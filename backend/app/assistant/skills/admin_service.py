"""Skill package aggregate admin service (Plan 09 Task 1).

Owns revision-CAS metadata/archive/catalog/alias mutations and restore-as-new-draft.
Content version appends reuse Plan 01 ``AgentSkillService``. Every privileged
transition requires a verified ``OperatorPrincipal`` (not an ``isAdmin`` boolean).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.skills.models import (
    AssistantSkillPackage,
    AssistantSkillPackageAlias,
    AssistantSkillVersion,
)
from app.assistant.skills.package_io import parse_skill_directory_files
from app.assistant.skills.principal import OperatorPrincipal
from app.assistant.skills.schemas import (
    AddSkillPackageAliasCommand,
    AggregateRevisionCommand,
    DisableSkillPackageAliasCommand,
    RestoreSkillVersionAsDraftCommand,
    SkillPackageDetail,
    SkillPackageSummary,
    SkillVersionSummary,
    UpdateSkillPackageMetadataCommand,
)
from app.assistant.skills.service import AgentSkillService
from app.assistant.skills.contracts import (
    is_reserved_skill_lookup_name,
    normalize_skill_lookup_name,
)
from app.common.exceptions import ApiException
from app.common.time import utcnow

logger = logging.getLogger(__name__)

# Error codes for Plan 09 admin (stable).
_CODE_UNAUTHORIZED = 40190
_CODE_FORBIDDEN = 40390
_CODE_NOT_OPERATOR = 40391
_CODE_CONFLICT_REVISION = 40994
_CODE_CONFLICT_REQUEST = 40997
_CODE_ARCHIVED = 40996
_CODE_ALIAS_PROTECTED = 42296
_CODE_VALIDATION = 42291


def _request_digest(operation: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class SkillAdminService:
    """Aggregate admin mutations with principal + revision CAS."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._packages = AgentSkillService(db)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _require_principal(
        self, principal: OperatorPrincipal | None, *, operator: bool = False
    ) -> OperatorPrincipal:
        if principal is None:
            raise ApiException(
                status_code=401,
                code=_CODE_UNAUTHORIZED,
                message="verified OperatorPrincipal is required",
            )
        if not isinstance(principal, OperatorPrincipal):
            raise ApiException(
                status_code=401,
                code=_CODE_UNAUTHORIZED,
                message="verified OperatorPrincipal is required",
            )
        if operator and not principal.is_operator:
            raise ApiException(
                status_code=403,
                code=_CODE_NOT_OPERATOR,
                message="operator role is required for this transition",
            )
        return principal

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

    def _assert_revision(
        self, package: AssistantSkillPackage, expected: int
    ) -> None:
        current = int(package.aggregate_revision or 0)
        if current != int(expected):
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REVISION,
                message=(
                    f"aggregate revision conflict: expected {expected}, "
                    f"current {current}"
                ),
                details={
                    "expectedAggregateRevision": expected,
                    "currentAggregateRevision": current,
                    "packageId": str(package.id),
                },
            )

    def _begin_idempotent(
        self,
        package: AssistantSkillPackage,
        *,
        request_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> SkillPackageDetail | None:
        """Return prior result when identical requestId is retried; conflict on reuse."""
        digest = _request_digest(operation, payload)
        last_id = getattr(package, "last_admin_request_id", None)
        last_digest = getattr(package, "last_admin_request_digest", None)
        if last_id is None:
            package.last_admin_request_id = request_id
            package.last_admin_request_digest = digest
            return None
        if last_id == request_id:
            if last_digest == digest:
                # Identical retry → return current aggregate snapshot.
                return self._packages.get_package(package.id)
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REQUEST,
                message="requestId was reused with a different payload",
                details={"requestId": request_id},
            )
        package.last_admin_request_id = request_id
        package.last_admin_request_digest = digest
        return None

    def _bump_revision(self, package: AssistantSkillPackage) -> int:
        package.aggregate_revision = int(package.aggregate_revision or 0) + 1
        return int(package.aggregate_revision)

    def _summary(self, package: AssistantSkillPackage) -> SkillPackageSummary:
        return self._packages._package_summary(package)

    def _detail(self, package_id: UUID) -> SkillPackageDetail:
        return self._packages.get_package(package_id)

    # ------------------------------------------------------------------
    # Metadata CAS
    # ------------------------------------------------------------------

    def update_metadata(
        self,
        package_id: UUID,
        command: UpdateSkillPackageMetadataCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "display_name": command.display_name,
                "description": command.description,
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="update_metadata",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            if command.display_name is not None:
                name = command.display_name.strip()
                if not name:
                    raise ApiException(
                        status_code=422,
                        code=_CODE_VALIDATION,
                        message="display_name must not be empty",
                    )
                package.display_name = name[:128]
            if command.description is not None:
                package.description = command.description[:1024]
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Archive / unarchive
    # ------------------------------------------------------------------

    def archive(
        self,
        package_id: UUID,
        command: AggregateRevisionCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="archive",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            if package.archived_at is not None:
                # Already archived under a *new* requestId: intentional audit bump.
                # Identical requestId retry is handled above as a pure no-op (no
                # revision bump). Re-archive with a distinct requestId still CAS-
                # advances aggregate_revision so concurrent observers see the
                # admin action, even though archive state is unchanged.
                self._bump_revision(package)
                self.db.commit()
                return self._detail(package.id)

            now = utcnow()
            package.archived_at = now
            package.archived_by = principal.audit_actor()
            # Archive atomically disables new Catalog recall.
            package.catalog_enabled = False
            package.catalog_enabled_at = None
            package.catalog_enabled_by = None
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    def unarchive(
        self,
        package_id: UUID,
        command: AggregateRevisionCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="unarchive",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            package.archived_at = None
            package.archived_by = None
            # Unarchive never re-enables catalog automatically.
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Catalog enable / disable (operator-privileged)
    # ------------------------------------------------------------------

    def enable_catalog(
        self,
        package_id: UUID,
        command: AggregateRevisionCommand,
        *,
        principal: OperatorPrincipal | None,
        expected_published_version_id: UUID | None = None,
        gate_id: UUID | None = None,
    ) -> SkillPackageDetail:
        """Enable catalog visibility for the exact current published version.

        Always requires a fresh matching non-expired promotion gate (observe and
        enforce). Gate-use is appended in the same transaction as the enable.
        """
        from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            build_publish_gate_subject,
        )
        from app.assistant.domain.digests import sha256_canonical_json

        principal = self._require_principal(principal, operator=True)
        resolved_gate_id = gate_id if gate_id is not None else getattr(command, "gate_id", None)
        try:
            # Serialize enable with Plan 01 mutex.
            self._packages._acquire_catalog_enable_lock()
            package = self._lock_package(package_id)
            payload = {
                "expected_aggregate_revision": command.expected_aggregate_revision,
                "expected_published_version_id": (
                    str(expected_published_version_id)
                    if expected_published_version_id
                    else None
                ),
                "gate_id": str(resolved_gate_id) if resolved_gate_id else None,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="enable_catalog",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            if package.archived_at is not None:
                raise ApiException(
                    status_code=409,
                    code=_CODE_ARCHIVED,
                    message="cannot enable catalog for an archived skill package",
                )
            if package.published_version_id is None:
                raise ApiException(
                    status_code=422,
                    code=42291,
                    message="cannot enable catalog without a published skill version",
                )
            if (
                expected_published_version_id is not None
                and package.published_version_id != expected_published_version_id
            ):
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published skill version drifted during enable",
                )
            version = self.db.get(AssistantSkillVersion, package.published_version_id)
            if version is None or version.skill_package_id != package.id:
                raise ApiException(
                    status_code=422,
                    code=42291,
                    message="published skill version missing or unowned",
                )
            if str(version.version_source) != "publish":
                raise ApiException(
                    status_code=422,
                    code=42291,
                    message="catalog enable requires version_source=publish",
                )

            # Server-derived subject for the exact current published version.
            gate_svc = PublishGateService(self.db)
            existing_gate = (
                gate_svc.get_gate(resolved_gate_id) if resolved_gate_id is not None else None
            )
            placeholder = "0" * 64
            content_digest = str(version.content_digest or placeholder)
            binding_digest = str(version.binding_set_digest or placeholder)
            if existing_gate is not None:
                profile_digest = str(existing_gate.profile_digest)
                catalog_digest = str(existing_gate.catalog_digest)
                dataset_ids = tuple(
                    UUID(str(x)) for x in (existing_gate.dataset_version_ids or [])
                )
                runtime_cv = int(existing_gate.runtime_contract_version)
                policy_version = str(existing_gate.policy_version)
                threshold_version = str(existing_gate.threshold_version)
                build_revision = str(existing_gate.build_revision)
                subject_kind = str(existing_gate.subject_kind)
                # Enable must target exact current published version id.
                subject_version_id = package.published_version_id
            else:
                profile_digest = placeholder
                catalog_digest = sha256_canonical_json(
                    {
                        "package_id": str(package.id),
                        "canonical_name": package.canonical_name,
                        "published_version_id": str(package.published_version_id),
                        "content_digest": content_digest,
                    }
                )
                from uuid import uuid5, NAMESPACE_URL

                dataset_ids = (
                    uuid5(NAMESPACE_URL, "mindatlas:plan09:gate-placeholder-dataset"),
                )
                runtime_cv = 1
                policy_version = "plan09-policy-v1"
                threshold_version = THRESHOLD_POLICY_VERSION
                build_revision = "development"
                subject_kind = "skill_version"
                subject_version_id = package.published_version_id

            subject = build_publish_gate_subject(
                kind=subject_kind,
                aggregate_id=package.id,
                version_id=subject_version_id,
                content_digest=content_digest,
                binding_digest=binding_digest,
                profile_digest=profile_digest,
                catalog_digest=catalog_digest,
                dataset_version_ids=dataset_ids,
                runtime_contract_version=runtime_cv,
                policy_version=policy_version,
                threshold_version=threshold_version,
                build_revision=build_revision,
            )
            # If gate pins content/binding, recompute must match version bytes.
            if existing_gate is not None:
                if str(existing_gate.subject_content_digest).lower() != content_digest.lower():
                    raise ApiException(
                        status_code=409,
                        code=40982,
                        message="enable gate content_digest drifted from published version",
                        details={
                            "type": "gate_subject_drift",
                            "details": {"drifts": ["subject_content_digest"]},
                        },
                    )
                if (
                    existing_gate.subject_binding_digest
                    and str(existing_gate.subject_binding_digest).lower()
                    != binding_digest.lower()
                ):
                    raise ApiException(
                        status_code=409,
                        code=40982,
                        message="enable gate binding_digest drifted from published version",
                        details={
                            "type": "gate_subject_drift",
                            "details": {"drifts": ["subject_binding_digest"]},
                        },
                    )
                # Gate subject version must be the published version (or its draft source).
                if str(existing_gate.subject_version_id) not in {
                    str(package.published_version_id),
                    str(getattr(version, "source_draft_version_id", None) or ""),
                }:
                    # Rebuild subject using gate's version_id only when it matches
                    # content; otherwise drift.
                    if str(existing_gate.subject_content_digest).lower() == content_digest.lower():
                        subject = build_publish_gate_subject(
                            kind=str(existing_gate.subject_kind),
                            aggregate_id=package.id,
                            version_id=existing_gate.subject_version_id,
                            content_digest=content_digest,
                            binding_digest=binding_digest,
                            profile_digest=profile_digest,
                            catalog_digest=catalog_digest,
                            dataset_version_ids=dataset_ids,
                            runtime_contract_version=runtime_cv,
                            policy_version=policy_version,
                            threshold_version=threshold_version,
                            build_revision=build_revision,
                        )

            try:
                gate_svc.enforce_enable(
                    gate_id=resolved_gate_id,
                    subject=subject,
                    action="skill_catalog_enable",
                    aggregate_id=package.id,
                    resulting_version_id=package.published_version_id,
                    actor_principal=principal.audit_actor(),
                    request_id=str(command.request_id),
                    aggregate_revision=int(package.aggregate_revision or 0),
                )
            except PublishGateError as exc:
                raise exc.to_api_exception() from exc

            now = utcnow()
            package.catalog_enabled = True
            package.catalog_enabled_at = now
            package.catalog_enabled_by = principal.audit_actor()
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    def disable_catalog(
        self,
        package_id: UUID,
        command: AggregateRevisionCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=True)
        try:
            package = self._lock_package(package_id)
            payload = {
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="disable_catalog",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            package.catalog_enabled = False
            package.catalog_enabled_at = None
            package.catalog_enabled_by = None
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Custom aliases (append-only; disable only)
    # ------------------------------------------------------------------

    def add_alias(
        self,
        package_id: UUID,
        command: AddSkillPackageAliasCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "alias": command.alias,
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="add_alias",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            if package.archived_at is not None:
                raise ApiException(
                    status_code=409,
                    code=_CODE_ARCHIVED,
                    message="cannot add aliases to an archived skill package",
                )
            try:
                normalized = normalize_skill_lookup_name(command.alias)
            except (TypeError, ValueError) as exc:
                raise ApiException(
                    status_code=422, code=_CODE_VALIDATION, message=str(exc)
                ) from exc
            if is_reserved_skill_lookup_name(command.alias):
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {command.alias!r} is reserved",
                )
            existing = (
                self.db.query(AssistantSkillPackageAlias)
                .filter(AssistantSkillPackageAlias.normalized_alias == normalized)
                .one_or_none()
            )
            if existing is not None:
                # Disabled custom names remain reserved — never reassign.
                raise ApiException(
                    status_code=409,
                    code=40991,
                    message=f"alias {command.alias!r} is already reserved",
                )
            self.db.add(
                AssistantSkillPackageAlias(
                    skill_package_id=package.id,
                    alias=command.alias,
                    normalized_alias=normalized,
                    alias_type="custom",
                )
            )
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    def disable_alias(
        self,
        package_id: UUID,
        alias_id: UUID,
        command: DisableSkillPackageAliasCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillPackageDetail:
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "alias_id": str(alias_id),
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            reused = self._begin_idempotent(
                package,
                request_id=command.request_id,
                operation="disable_alias",
                payload=payload,
            )
            if reused is not None:
                return reused
            self._assert_revision(package, command.expected_aggregate_revision)
            alias = (
                self.db.query(AssistantSkillPackageAlias)
                .filter(
                    AssistantSkillPackageAlias.id == alias_id,
                    AssistantSkillPackageAlias.skill_package_id == package.id,
                )
                .one_or_none()
            )
            if alias is None:
                raise ApiException(
                    status_code=404,
                    code=40492,
                    message=f"alias not found: {alias_id}",
                )
            if alias.alias_type in {"canonical", "legacy"}:
                raise ApiException(
                    status_code=422,
                    code=_CODE_ALIAS_PROTECTED,
                    message=(
                        f"cannot disable {alias.alias_type} alias through ordinary admin"
                    ),
                )
            if alias.disabled_at is None:
                alias.disabled_at = utcnow()
                alias.disabled_by = principal.audit_actor()
            self._bump_revision(package)
            self.db.commit()
            return self._detail(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc

    # ------------------------------------------------------------------
    # Restore-as-new-draft
    # ------------------------------------------------------------------

    def restore_as_new_draft(
        self,
        package_id: UUID,
        version_id: UUID,
        command: RestoreSkillVersionAsDraftCommand,
        *,
        principal: OperatorPrincipal | None,
    ) -> SkillVersionSummary:
        """Copy an owned immutable version into a new draft; leave published pointer.

        Plan 01 content-digest uniqueness may re-point ``draft_version_id`` to an
        existing ``version_source=save`` row. Version rows are immutable (Plan 01
        PG triggers reject UPDATE), so restore NEVER mutates historical version
        data — including ``extension_manifest``. Provenance
        ``restoredFromVersionId`` is recorded on the package aggregate
        (``last_restored_from_version_id``). When a brand-new save row is
        inserted, provenance is also stamped into that row's
        ``extension_manifest`` at INSERT time only.
        """
        principal = self._require_principal(principal, operator=False)
        try:
            package = self._lock_package(package_id)
            payload = {
                "version_id": str(version_id),
                "expected_aggregate_revision": command.expected_aggregate_revision,
            }
            last_id = getattr(package, "last_admin_request_id", None)
            last_digest = getattr(package, "last_admin_request_digest", None)
            digest = _request_digest("restore_as_new_draft", payload)
            if last_id == command.request_id:
                if last_digest == digest:
                    if package.draft_version_id is not None:
                        draft = self.db.get(
                            AssistantSkillVersion, package.draft_version_id
                        )
                        if draft is not None:
                            return self._packages._version_summary(draft)
                    return self._packages.get_version(package_id, version_id)
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                )
            self._assert_revision(package, command.expected_aggregate_revision)

            version = (
                self.db.query(AssistantSkillVersion)
                .filter(
                    AssistantSkillVersion.id == version_id,
                    AssistantSkillVersion.skill_package_id == package.id,
                )
                .one_or_none()
            )
            if version is None:
                raise ApiException(
                    status_code=404,
                    code=40491,
                    message=f"Skill version not found: {version_id}",
                )

            published_before = package.published_version_id

            # Reconstruct portable files and re-validate via Plan 01 parser.
            files: dict[str, bytes] = {
                "SKILL.md": (version.skill_md or "").encode("utf-8"),
            }
            if version.mindatlas_yaml:
                files["mindatlas.yaml"] = version.mindatlas_yaml.encode("utf-8")
            resource_rows = self._packages._load_stored_resources(version_id=version.id)
            for resource in resource_rows:
                files[resource.path] = resource.content

            parsed = parse_skill_directory_files(
                files,
                expected_root_name=package.canonical_name,
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
                # Re-point only — never mutate the immutable historical save row.
                draft = existing
            else:
                next_seq = self._packages._next_sequence(package.id)
                draft = self._packages._insert_draft_version(
                    package=package,
                    parsed=parsed,
                    version_name=f"restore-from-{version.sequence_no}",
                    origin="api",
                    sequence_no=next_seq,
                    # Stamp provenance at INSERT time only (never UPDATE later).
                    extension_manifest_extra={
                        "restoredFromVersionId": str(version.id),
                    },
                )

            # Aggregate-level provenance (always; survives content-digest reuse).
            package.last_restored_from_version_id = version.id
            package.draft_version_id = draft.id
            package.display_name = draft.frontmatter.get("name") or package.display_name
            if isinstance(draft.frontmatter, dict):
                desc = draft.frontmatter.get("description")
                if isinstance(desc, str):
                    package.description = desc
            if package.published_version_id != published_before:
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="published pointer drifted during restore",
                )
            self._bump_revision(package)
            package.last_admin_request_id = command.request_id
            package.last_admin_request_digest = digest
            self.db.commit()
            return self._packages._version_summary(draft)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc
