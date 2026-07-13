"""Append-only Agent Skill package aggregate service (Plan 01 Task 4).

Does not publish executable catalog entries. Resource bytes are never included
in ordinary list/detail serialization.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.domain.contracts import ResolvedSkillRef
from app.assistant.skills.contracts import (
    ParsedSkillPackage,
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
    validate_canonical_skill_name,
)
from app.assistant.skills.models import (
    AssistantSkillCapabilityBinding,
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillResourceBlob,
    AssistantSkillVersion,
    AssistantSkillVersionResource,
)
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    SaveSkillDraftCommand,
    SkillPackageAliasSummary,
    SkillPackageDetail,
    SkillPackageSummary,
    SkillResourceMetadata,
    SkillVersionDetail,
    SkillVersionSummary,
)
from app.common.exceptions import ApiException

logger = logging.getLogger(__name__)

# Aggregate-locked distinct referenced blob budget (in addition to per-version 25 MiB).
MAX_PACKAGE_DISTINCT_BLOB_BYTES = 256 * 1024 * 1024


def _frontmatter_json(parsed: ParsedSkillPackage) -> dict[str, Any]:
    return parsed.frontmatter.model_dump(by_alias=True, exclude_none=False)


def _manifest_json(parsed: ParsedSkillPackage) -> dict[str, Any] | None:
    if parsed.manifest is None:
        return None
    return parsed.manifest.model_dump(by_alias=True, exclude_none=False)


def _resource_index_json(parsed: ParsedSkillPackage) -> list[dict[str, Any]]:
    return [
        {
            "path": entry.path,
            "kind": entry.resource_kind,
            "mediaType": entry.media_type,
            "size": entry.byte_size,
            "sha256": entry.sha256,
        }
        for entry in parsed.resource_index
    ]


def _decode_text(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8")


def _display_name_for(parsed: ParsedSkillPackage) -> str:
    if parsed.manifest and parsed.manifest.display_name:
        return parsed.manifest.display_name
    return parsed.canonical_name


class AgentSkillService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Create / save
    # ------------------------------------------------------------------

    def create_native_package(
        self, command: CreateSkillPackageCommand
    ) -> SkillPackageDetail:
        parsed = command.parsed
        try:
            canonical = validate_canonical_skill_name(parsed.canonical_name)
        except ValueError as exc:
            raise ApiException(status_code=422, code=42290, message=str(exc)) from exc

        if is_reserved_skill_lookup_name(canonical):
            raise ApiException(
                status_code=409,
                code=40990,
                message=f"canonical skill name {canonical!r} is reserved",
            )

        version_name = command.version_name or "draft-1"
        origin = command.origin

        try:
            package = AssistantSkillPackage(
                canonical_name=canonical,
                display_name=_display_name_for(parsed),
                description=parsed.frontmatter.description,
                migration_state="native",
                catalog_enabled=False,
                is_system=False,
            )
            self.db.add(package)
            self.db.flush()

            self._reserve_aliases(
                package_id=package.id,
                canonical_name=canonical,
                legacy_aliases=list(parsed.manifest.legacy_aliases)
                if parsed.manifest
                else [],
            )
            self.db.flush()

            version = self._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name=version_name,
                origin=origin,
                sequence_no=1,
            )
            package.draft_version_id = version.id
            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

        return self.get_package(package.id)

    def save_draft(self, command: SaveSkillDraftCommand) -> SkillVersionSummary:
        parsed = command.parsed
        try:
            canonical = validate_canonical_skill_name(parsed.canonical_name)
        except ValueError as exc:
            raise ApiException(status_code=422, code=42290, message=str(exc)) from exc

        version_name = command.version_name or "draft"
        origin = command.origin

        try:
            package = self._lock_package(command.package_id)

            if package.canonical_name != canonical:
                raise ApiException(
                    status_code=409,
                    code=40990,
                    message=(
                        f"canonical name is immutable: package has "
                        f"{package.canonical_name!r}, payload has {canonical!r}"
                    ),
                )

            # shadow -> native on first administrator/native edit; never reverse.
            if package.migration_state == "shadow":
                package.migration_state = "native"

            # Append any new legacy aliases (append-only; never rewrite).
            if parsed.manifest and parsed.manifest.legacy_aliases:
                self._append_legacy_aliases(
                    package_id=package.id,
                    legacy_aliases=list(parsed.manifest.legacy_aliases),
                )

            existing = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.skill_package_id == package.id,
                    AssistantSkillVersion.version_source == "save",
                    AssistantSkillVersion.content_digest == parsed.content_digest,
                )
                .one_or_none()
            )
            if existing is not None:
                package.draft_version_id = existing.id
                package.display_name = _display_name_for(parsed)
                package.description = parsed.frontmatter.description
                self.db.commit()
                return self._version_summary(existing)

            next_seq = self._next_sequence(package.id)
            version = self._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name=version_name,
                origin=origin,
                sequence_no=next_seq,
            )
            package.draft_version_id = version.id
            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            self.db.commit()
            return self._version_summary(version)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_packages(self) -> list[SkillPackageSummary]:
        packages = (
            self.db.query(AssistantSkillPackage)
            .order_by(AssistantSkillPackage.canonical_name.asc())
            .all()
        )
        return [self._package_summary(pkg) for pkg in packages]

    def get_package(self, package_id: UUID) -> SkillPackageDetail:
        package = self._get_package_or_404(package_id)
        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == package.id)
            .order_by(AssistantSkillPackageAlias.created_at.asc())
            .all()
        )
        summary = self._package_summary(package)
        return SkillPackageDetail(
            **summary.model_dump(),
            aliases=[
                SkillPackageAliasSummary(
                    id=a.id,
                    alias=a.alias,
                    normalized_alias=a.normalized_alias,
                    alias_type=a.alias_type,  # type: ignore[arg-type]
                    created_at=a.created_at,
                )
                for a in aliases
            ],
            legacy_skill_id=package.legacy_skill_id,
            legacy_source_digest=package.legacy_source_digest,
        )

    def list_versions(self, package_id: UUID) -> list[SkillVersionSummary]:
        self._get_package_or_404(package_id)
        versions = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .order_by(AssistantSkillVersion.sequence_no.asc())
            .all()
        )
        return [self._version_summary(v) for v in versions]

    def get_version(self, package_id: UUID, version_id: UUID) -> SkillVersionDetail:
        self._get_package_or_404(package_id)
        version = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == version_id,
                AssistantSkillVersion.skill_package_id == package_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {version_id}",
            )
        resources = (
            self.db.query(AssistantSkillVersionResource)
            .filter(AssistantSkillVersionResource.skill_version_id == version.id)
            .order_by(AssistantSkillVersionResource.path.asc())
            .all()
        )
        summary = self._version_summary(version)
        return SkillVersionDetail(
            **summary.model_dump(),
            frontmatter=version.frontmatter or {},
            extension_manifest=version.extension_manifest,
            resource_index=list(version.resource_index or []),
            resources=[
                SkillResourceMetadata(
                    path=r.path,
                    resource_kind=r.resource_kind,  # type: ignore[arg-type]
                    media_type=r.media_type,
                    byte_size=r.byte_size,
                    sha256=r.sha256,
                    executable=bool(r.executable),
                )
                for r in resources
            ],
            skill_md=version.skill_md,
            mindatlas_yaml=version.mindatlas_yaml,
        )

    def get_resource_bytes(
        self, package_id: UUID, version_id: UUID, path: str
    ) -> bytes:
        self._get_package_or_404(package_id)
        version = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == version_id,
                AssistantSkillVersion.skill_package_id == package_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"Skill version not found: {version_id}",
            )
        resource = (
            self.db.query(AssistantSkillVersionResource)
            .filter(
                AssistantSkillVersionResource.skill_version_id == version.id,
                AssistantSkillVersionResource.path == path,
            )
            .one_or_none()
        )
        if resource is None:
            raise ApiException(
                status_code=404,
                code=40492,
                message=f"Skill resource not found: {path}",
            )
        blob = self.db.get(AssistantSkillResourceBlob, resource.blob_id)
        if blob is None:
            raise ApiException(
                status_code=404,
                code=40492,
                message=f"Skill resource blob missing for: {path}",
            )
        # Verify exact bytes match stored digest/size.
        if blob.byte_size != resource.byte_size or blob.sha256 != resource.sha256:
            raise ApiException(
                status_code=409,
                code=40993,
                message="resource blob metadata mismatch",
            )
        if len(blob.content) != blob.byte_size:
            raise ApiException(
                status_code=409,
                code=40993,
                message="resource blob size mismatch",
            )
        return bytes(blob.content)

    def resolve_published_alias(self, name: str) -> ResolvedSkillRef:
        try:
            normalized = normalize_skill_lookup_name(name)
        except (TypeError, ValueError) as exc:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found for name: {name!r}",
            ) from exc

        try:
            alias = (
                self.db.query(AssistantSkillPackageAlias)
                .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
                .one_or_none()
            )
            if alias is None:
                raise ApiException(
                    status_code=404,
                    code=40490,
                    message=f"Skill package not found for name: {name!r}",
                )

            package = (
                self.db.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.id == alias.skill_package_id)
                .with_for_update()
                .one_or_none()
            )
            if package is None:
                raise ApiException(
                    status_code=404,
                    code=40490,
                    message=f"Skill package not found for name: {name!r}",
                )
            if package.published_version_id is None:
                raise ApiException(
                    status_code=404,
                    code=40491,
                    message=(
                        f"Skill package {package.canonical_name!r} has no published version"
                    ),
                )

            version = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.id == package.published_version_id,
                    AssistantSkillVersion.skill_package_id == package.id,
                    AssistantSkillVersion.version_source == "publish",
                )
                .one_or_none()
            )
            if version is None:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published_version_id does not reference an owned publish version",
                )
            if version.version_digest is None:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published version missing version_digest",
                )

            # Build frozen ref inside the transaction; do not re-follow pointer later.
            return ResolvedSkillRef(
                package_id=package.id,
                version_id=version.id,
                canonical_name=package.canonical_name,
                sequence=version.sequence_no,
                content_digest=version.content_digest,
                version_digest=version.version_digest,
                requested_name_normalized=normalized,
                resolved_via_alias_id=alias.id,
            )
        except ApiException:
            raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_package_or_404(self, package_id: UUID) -> AssistantSkillPackage:
        package = self.db.get(AssistantSkillPackage, package_id)
        if package is None:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found: {package_id}",
            )
        return package

    def _lock_package(self, package_id: UUID) -> AssistantSkillPackage:
        package = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.id == package_id)
            .with_for_update()
            .one_or_none()
        )
        if package is None:
            raise ApiException(
                status_code=404,
                code=40490,
                message=f"Skill package not found: {package_id}",
            )
        return package

    def _next_sequence(self, package_id: UUID) -> int:
        current = (
            self.db.query(func.max(AssistantSkillVersion.sequence_no))
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .scalar()
        )
        return int(current or 0) + 1

    def _reserve_aliases(
        self,
        *,
        package_id: UUID,
        canonical_name: str,
        legacy_aliases: list[str],
    ) -> None:
        # Canonical alias first.
        self._insert_alias_row(
            package_id=package_id,
            alias=canonical_name,
            alias_type="canonical",
        )
        seen_normalized = {normalize_skill_lookup_name(canonical_name)}
        for raw in legacy_aliases:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(raw):
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {raw!r} is reserved",
                )
            if normalized in seen_normalized:
                # Duplicate within the same package request is a no-op after first.
                continue
            seen_normalized.add(normalized)
            self._insert_alias_row(
                package_id=package_id,
                alias=raw,
                alias_type="legacy",
            )

    def _append_legacy_aliases(
        self, *, package_id: UUID, legacy_aliases: list[str]
    ) -> None:
        existing = {
            row.normalized_alias
            for row in self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == package_id)
            .all()
        }
        for raw in legacy_aliases:
            try:
                normalized = normalize_skill_lookup_name(raw)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=42291, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(raw):
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {raw!r} is reserved",
                )
            if normalized in existing:
                continue
            # Collision with another package's alias is caught by unique index.
            self._insert_alias_row(
                package_id=package_id,
                alias=raw,
                alias_type="legacy",
            )
            existing.add(normalized)

    def _insert_alias_row(
        self,
        *,
        package_id: UUID,
        alias: str,
        alias_type: str,
    ) -> AssistantSkillPackageAlias:
        try:
            normalized = normalize_skill_lookup_name(alias)
        except (TypeError, ValueError) as exc:
            raise ApiException(status_code=422, code=42291, message=str(exc)) from exc
        if is_reserved_skill_lookup_name(alias) and alias_type != "canonical":
            # Canonical reservation of reserved names is already rejected earlier.
            raise ApiException(
                status_code=409,
                code=40991,
                message=f"alias {alias!r} is reserved",
            )
        # Pre-check for clearer conflict codes (unique index remains authoritative).
        conflict = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
            .one_or_none()
        )
        if conflict is not None:
            if conflict.skill_package_id == package_id:
                return conflict
            if alias_type == "canonical":
                raise ApiException(
                    status_code=409,
                    code=40990,
                    message=f"canonical name {alias!r} already reserved",
                )
            raise ApiException(
                status_code=409,
                code=40991,
                message=f"alias {alias!r} already reserved",
            )
        row = AssistantSkillPackageAlias(
            skill_package_id=package_id,
            alias=alias,
            normalized_alias=normalized,
            alias_type=alias_type,
        )
        self.db.add(row)
        return row

    def _insert_draft_version(
        self,
        *,
        package: AssistantSkillPackage,
        parsed: ParsedSkillPackage,
        version_name: str,
        origin: str,
        sequence_no: int,
    ) -> AssistantSkillVersion:
        version = AssistantSkillVersion(
            skill_package_id=package.id,
            sequence_no=sequence_no,
            version_name=version_name,
            version_source="save",
            source_draft_version_id=None,
            origin=origin,
            skill_md=_decode_text(parsed.skill_md_bytes) or "",
            mindatlas_yaml=_decode_text(parsed.mindatlas_yaml_bytes),
            frontmatter=_frontmatter_json(parsed),
            extension_manifest=_manifest_json(parsed),
            resource_index=_resource_index_json(parsed),
            skill_md_digest=parsed.skill_md_digest,
            manifest_digest=parsed.manifest_digest,
            resource_index_digest=parsed.resource_index_digest,
            content_digest=parsed.content_digest,
            binding_set_digest=None,
            version_digest=None,
        )
        self.db.add(version)
        self.db.flush()

        self._insert_resources(package_id=package.id, version=version, parsed=parsed)
        self._insert_unresolved_bindings(version=version, parsed=parsed)
        self.db.flush()
        return version

    def _insert_resources(
        self,
        *,
        package_id: UUID,
        version: AssistantSkillVersion,
        parsed: ParsedSkillPackage,
    ) -> None:
        # Deterministic path order.
        resources = sorted(parsed.resources, key=lambda r: r.path)
        for item in resources:
            blob = self._get_or_create_blob(
                sha256=item.sha256,
                content=item.content,
                byte_size=item.byte_size,
            )
            self.db.add(
                AssistantSkillVersionResource(
                    skill_version_id=version.id,
                    path=item.path,
                    resource_kind=item.resource_kind,
                    media_type=item.media_type,
                    byte_size=item.byte_size,
                    sha256=item.sha256,
                    blob_id=blob.id,
                    executable=False,
                )
            )
        # Enforce aggregate distinct-blob budget after staging references.
        self._enforce_package_blob_quota(package_id)

    def _get_or_create_blob(
        self, *, sha256: str, content: bytes, byte_size: int
    ) -> AssistantSkillResourceBlob:
        if byte_size != len(content):
            raise ApiException(
                status_code=422,
                code=42292,
                message="resource byte_size does not match content length",
            )
        existing = (
            self.db.query(AssistantSkillResourceBlob)
            .filter(
                AssistantSkillResourceBlob.sha256 == sha256,
                AssistantSkillResourceBlob.byte_size == byte_size,
            )
            .one_or_none()
        )
        if existing is not None:
            if bytes(existing.content) != content:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob digest collision with different bytes",
                )
            return existing

        blob = AssistantSkillResourceBlob(
            sha256=sha256,
            byte_size=byte_size,
            content=content,
        )
        # Use a SAVEPOINT so a uniqueness race does not abort the outer unit of work.
        try:
            with self.db.begin_nested():
                self.db.add(blob)
                self.db.flush()
            return blob
        except IntegrityError:
            existing = (
                self.db.query(AssistantSkillResourceBlob)
                .filter(
                    AssistantSkillResourceBlob.sha256 == sha256,
                    AssistantSkillResourceBlob.byte_size == byte_size,
                )
                .one_or_none()
            )
            if existing is None:
                raise ApiException(
                    status_code=409,
                    code=40992,
                    message="resource blob insert conflict; retry",
                )
            if bytes(existing.content) != content:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="resource blob digest collision with different bytes",
                )
            return existing

    def _enforce_package_blob_quota(self, package_id: UUID) -> None:
        # Sum distinct blob sizes referenced by any version of this package.
        # Use subquery of distinct blob_ids then join for sizes.
        blob_ids = (
            self.db.query(AssistantSkillVersionResource.blob_id)
            .join(
                AssistantSkillVersion,
                AssistantSkillVersion.id
                == AssistantSkillVersionResource.skill_version_id,
            )
            .filter(AssistantSkillVersion.skill_package_id == package_id)
            .distinct()
            .subquery()
        )
        total = (
            self.db.query(func.coalesce(func.sum(AssistantSkillResourceBlob.byte_size), 0))
            .filter(AssistantSkillResourceBlob.id.in_(select(blob_ids.c.blob_id)))
            .scalar()
        )
        total_bytes = int(total or 0)
        if total_bytes > MAX_PACKAGE_DISTINCT_BLOB_BYTES:
            raise ApiException(
                status_code=413,
                code=41391,
                message=(
                    f"package distinct resource blob bytes {total_bytes} exceed "
                    f"limit {MAX_PACKAGE_DISTINCT_BLOB_BYTES}"
                ),
            )

    def _insert_unresolved_bindings(
        self, *, version: AssistantSkillVersion, parsed: ParsedSkillPackage
    ) -> None:
        if parsed.manifest is None:
            return
        for ordinal, cap in enumerate(parsed.manifest.capabilities):
            self.db.add(
                AssistantSkillCapabilityBinding(
                    skill_version_id=version.id,
                    ordinal=ordinal,
                    capability_type=cap.type,
                    capability_key=cap.key,
                    resolution_status="unresolved",
                )
            )

    def _version_summary(self, version: AssistantSkillVersion) -> SkillVersionSummary:
        return SkillVersionSummary(
            id=version.id,
            skill_package_id=version.skill_package_id,
            sequence_no=version.sequence_no,
            version_name=version.version_name,
            version_source=version.version_source,  # type: ignore[arg-type]
            origin=version.origin,
            content_digest=version.content_digest,
            skill_md_digest=version.skill_md_digest,
            manifest_digest=version.manifest_digest,
            resource_index_digest=version.resource_index_digest,
            binding_set_digest=version.binding_set_digest,
            version_digest=version.version_digest,
            source_draft_version_id=version.source_draft_version_id,
            created_at=version.created_at,
        )

    def _package_summary(self, package: AssistantSkillPackage) -> SkillPackageSummary:
        draft = None
        if package.draft_version_id is not None:
            draft_row = self.db.get(AssistantSkillVersion, package.draft_version_id)
            if draft_row is not None:
                draft = self._version_summary(draft_row)
        published = None
        if package.published_version_id is not None:
            pub_row = self.db.get(AssistantSkillVersion, package.published_version_id)
            if pub_row is not None:
                published = self._version_summary(pub_row)
        return SkillPackageSummary(
            id=package.id,
            canonical_name=package.canonical_name,
            display_name=package.display_name,
            description=package.description,
            migration_state=package.migration_state,  # type: ignore[arg-type]
            catalog_enabled=bool(package.catalog_enabled),
            is_system=bool(package.is_system),
            draft_version=draft,
            published_version=published,
            created_at=package.created_at,
            updated_at=package.updated_at,
        )

    def _translate_integrity_error(self, exc: IntegrityError) -> ApiException:
        msg = str(getattr(exc, "orig", exc)).lower()
        if "canonical_name" in msg or "assistant_skill_package" in msg and "unique" in msg:
            if "normalized_alias" in msg or "alias" in msg:
                return ApiException(
                    status_code=409,
                    code=40991,
                    message="skill alias namespace conflict",
                )
            return ApiException(
                status_code=409,
                code=40990,
                message="skill canonical name conflict",
            )
        if "normalized_alias" in msg or "assistant_skill_package_alias" in msg:
            return ApiException(
                status_code=409,
                code=40991,
                message="skill alias namespace conflict",
            )
        if "sequence" in msg or "uq_assistant_skill_version_seq" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="skill version sequence conflict; retry",
            )
        if "draft_content" in msg or "content_digest" in msg:
            return ApiException(
                status_code=409,
                code=40992,
                message="skill draft content conflict; retry",
            )
        return ApiException(
            status_code=409,
            code=40992,
            message="skill package constraint violation",
        )


__all__ = [
    "MAX_PACKAGE_DISTINCT_BLOB_BYTES",
    "AgentSkillService",
]
