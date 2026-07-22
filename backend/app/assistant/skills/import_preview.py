"""Safe skill package import preview / apply (Plan 09 Task 2 / remediation Task 3).

Two-step flow:
  1. ``preview`` — stream ZIP into bounded memory, parse via Plan 01 package_io,
     persist an opaque preview row (actor/mode/target revision/digests/expiry +
     bounded archive bytes) in PostgreSQL.
  2. ``apply`` — lock the preview row, recheck token/bytes/revision, append one
     unpublished catalog-disabled draft via Plan 01 services, consume
     requestId/preview once and null archive bytes in the same transaction.

Modes:
  * ``create`` — namespace free; creates aggregate + draft
  * ``append_to_existing`` — complete snapshot replace (no file merge)
  * ``fork_as_new`` — rewrite only standard frontmatter name, full revalidation

This module never reimplements path normalization, ZIP security, MIME sniffing,
or digest factories — those live exclusively in ``package_io`` / Plan 01 services.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_bytes
from app.assistant.skills.contracts import ParsedSkillPackage
from app.assistant.skills.models import (
    AssistantSkillImportPreview,
    AssistantSkillPackage,
    AssistantSkillVersion,
)
from app.assistant.skills.package_io import (
    MAX_ZIP_UPLOAD_BYTES,
    parse_skill_directory_files,
    parse_skill_zip,
    rewrite_skill_md_frontmatter_name,
)
from app.assistant.skills.principal import OperatorPrincipal
from app.assistant.skills.schemas import (
    ImportApplyResult,
    ImportPreviewResult,
    ImportPreviewToken,
    SkillPackageDetail,
)
from app.assistant.skills.service import AgentSkillService, _display_name_for
from app.common.exceptions import ApiException
from app.common.time import utcnow

# Stable Plan 09 import codes (aligned with admin service where shared).
_CODE_UNAUTHORIZED = 40190
_CODE_FORBIDDEN = 40390
_CODE_NOT_OPERATOR = 40391
_CODE_NOT_FOUND = 40492
_CODE_CONFLICT_REVISION = 40994
_CODE_CONFLICT_REQUEST = 40997
_CODE_CONFLICT_STALE = 40993
_CODE_VALIDATION = 42291
_CODE_ARCHIVE = 42292
_CODE_EXPIRED = 41090

PREVIEW_TTL = timedelta(minutes=15)
MAX_STRUCTURAL_DIFF_ENTRIES = 100

ImportModeLiteral = Literal["create", "append_to_existing", "fork_as_new"]
_VALID_MODES = frozenset({"create", "append_to_existing", "fork_as_new"})


def _as_aware(value: datetime) -> datetime:
    """Normalize SQLite-naive datetimes to UTC-aware for comparisons."""
    if value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)
    return value


def _actor_scope_digest(principal: OperatorPrincipal) -> str:
    body = json.dumps(
        {"principal_id": principal.principal_id, "role": principal.role},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _request_digest(operation: str, payload: dict[str, Any]) -> str:
    body = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _preview_digest_for(
    *,
    preview_id: UUID,
    actor_scope_digest: str,
    mode: str,
    target_package_id: UUID | None,
    expected_aggregate_revision: int | None,
    upload_digest: str,
    candidate_content_digest: str,
    candidate_canonical_name: str,
    fork_canonical_name: str | None,
    expires_at: datetime,
) -> str:
    body = json.dumps(
        {
            "preview_id": str(preview_id),
            "actor_scope_digest": actor_scope_digest,
            "mode": mode,
            "target_package_id": str(target_package_id) if target_package_id else None,
            "expected_aggregate_revision": expected_aggregate_revision,
            "upload_digest": upload_digest,
            "candidate_content_digest": candidate_content_digest,
            "candidate_canonical_name": candidate_canonical_name,
            "fork_canonical_name": fork_canonical_name,
            "expires_at": expires_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _map_package_io_error(exc: Exception) -> ApiException:
    """Map pure parser/IO ValueErrors onto reserved Plan 01 code blocks."""
    if isinstance(exc, ApiException):
        return exc
    msg = str(exc)
    lower = msg.lower()

    if any(
        token in lower
        for token in (
            "compressed size limit",
            "zip upload exceeds",
            "request body exceeds",
        )
    ):
        return ApiException(status_code=413, code=41390, message=msg)

    if "total uncompressed" in lower or "total decoded" in lower:
        return ApiException(status_code=413, code=41391, message=msg)

    if any(
        token in lower
        for token in (
            "exceeds size limit",
            "entry count",
            "size exceeds limit",
        )
    ):
        return ApiException(status_code=413, code=41392, message=msg)

    if any(
        token in lower
        for token in (
            "mindatlas.yaml",
            "manifest",
            "legacy alias",
            "capability",
            "provider_aliases",
            "routing",
            "policy",
        )
    ):
        return ApiException(status_code=422, code=42291, message=msg)

    if any(
        token in lower
        for token in (
            "skill.md",
            "frontmatter",
            "canonical skill name",
            "description",
            "allowed-tools",
            "license",
            "compatibility",
            "metadata",
            "fork name",
        )
    ):
        return ApiException(status_code=422, code=42290, message=msg)

    return ApiException(status_code=422, code=42292, message=msg)


def _resource_index_dicts(parsed: ParsedSkillPackage) -> list[dict[str, Any]]:
    """Bounded metadata only — never resource body bytes."""
    out: list[dict[str, Any]] = []
    for entry in parsed.resource_index:
        out.append(
            {
                "path": entry.path,
                "kind": entry.resource_kind,
                "mediaType": entry.media_type,
                "size": entry.byte_size,
                "sha256": entry.sha256,
                "executable": False,
            }
        )
    return out


def _capability_keys(parsed: ParsedSkillPackage) -> list[str]:
    if parsed.manifest is None:
        return []
    return [f"{c.type}:{c.key}" for c in parsed.manifest.capabilities]


def _bounded_structural_diff(
    *,
    left_index: list[dict[str, Any]] | None,
    right_index: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Path/digest-level structural diff; never includes file bodies."""
    left_map = {
        item["path"]: item for item in (left_index or []) if isinstance(item, dict)
    }
    right_map = {item["path"]: item for item in right_index}
    paths = sorted(set(left_map) | set(right_map))
    hunks: list[dict[str, Any]] = []
    for path in paths:
        if len(hunks) >= MAX_STRUCTURAL_DIFF_ENTRIES:
            hunks.append(
                {
                    "path": "...",
                    "kind": "truncated",
                    "leftDigest": None,
                    "rightDigest": None,
                }
            )
            break
        left = left_map.get(path)
        right = right_map.get(path)
        if left is None and right is not None:
            hunks.append(
                {
                    "path": path,
                    "kind": "added",
                    "leftDigest": None,
                    "rightDigest": right.get("sha256"),
                }
            )
        elif left is not None and right is None:
            hunks.append(
                {
                    "path": path,
                    "kind": "removed",
                    "leftDigest": left.get("sha256"),
                    "rightDigest": None,
                }
            )
        elif left is not None and right is not None:
            if left.get("sha256") != right.get("sha256"):
                hunks.append(
                    {
                        "path": path,
                        "kind": "changed",
                        "leftDigest": left.get("sha256"),
                        "rightDigest": right.get("sha256"),
                    }
                )
    return hunks


class ImportPreviewService:
    """Owns durable upload/preview rows; apply calls Plan 01 package service."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._packages = AgentSkillService(db)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _require_principal(
        self, principal: OperatorPrincipal | None, *, operator: bool = False
    ) -> OperatorPrincipal:
        if principal is None or not isinstance(principal, OperatorPrincipal):
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

    # ------------------------------------------------------------------
    # Durable row access
    # ------------------------------------------------------------------

    def _lock_preview(self, preview_id: UUID) -> AssistantSkillImportPreview:
        try:
            return (
                self.db.query(AssistantSkillImportPreview)
                .filter(AssistantSkillImportPreview.id == preview_id)
                .with_for_update()
                .one()
            )
        except NoResultFound as exc:
            raise ApiException(
                status_code=404,
                code=_CODE_NOT_FOUND,
                message=f"import preview not found: {preview_id}",
            ) from exc

    def _get_preview_or_404(self, preview_id: UUID) -> AssistantSkillImportPreview:
        row = (
            self.db.query(AssistantSkillImportPreview)
            .filter(AssistantSkillImportPreview.id == preview_id)
            .one_or_none()
        )
        if row is None:
            raise ApiException(
                status_code=404,
                code=_CODE_NOT_FOUND,
                message=f"import preview not found: {preview_id}",
            )
        return row

    def _get_record(self, preview_id: UUID) -> AssistantSkillImportPreview:
        """Compatibility accessor used by existing unit tests."""
        row = self._get_preview_or_404(preview_id)
        if (
            not row.consumed
            and _as_aware(row.expires_at) <= utcnow()
            and row.archive_bytes is None
        ):
            raise ApiException(
                status_code=410,
                code=_CODE_EXPIRED,
                message="import preview has expired",
                details={"previewId": str(preview_id)},
            )
        return row

    def expire(self, *, now: datetime | None = None) -> int:
        """Drop archive bytes from expired, still-unconsumed previews.

        Retains request/upload audit fields so request-id replay still works
        after TTL. Returns the number of rows whose archive payload was cleared.
        """
        current = _as_aware(now or utcnow())
        try:
            rows = list(
                self.db.scalars(
                    select(AssistantSkillImportPreview)
                    .where(
                        AssistantSkillImportPreview.expires_at <= current,
                        AssistantSkillImportPreview.archive_bytes.is_not(None),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
        except TypeError:
            # SQLite dialect may not accept skip_locked in older/test paths.
            rows = list(
                self.db.scalars(
                    select(AssistantSkillImportPreview)
                    .where(
                        AssistantSkillImportPreview.expires_at <= current,
                        AssistantSkillImportPreview.archive_bytes.is_not(None),
                    )
                    .with_for_update()
                )
            )
        # SQLite may store naive timestamps; filter again in Python.
        rows = [row for row in rows if _as_aware(row.expires_at) <= current]
        for row in rows:
            row.archive_bytes = None
        self.db.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(
        self,
        *,
        raw_zip: bytes,
        mode: ImportModeLiteral | str,
        principal: OperatorPrincipal | None,
        target_package_id: UUID | None = None,
        expected_aggregate_revision: int | None = None,
        fork_canonical_name: str | None = None,
    ) -> ImportPreviewResult:
        principal = self._require_principal(principal, operator=False)

        if mode not in _VALID_MODES:
            raise ApiException(
                status_code=422,
                code=_CODE_VALIDATION,
                message=f"invalid import mode: {mode!r}",
            )
        if not isinstance(raw_zip, (bytes, bytearray)):
            raise ApiException(
                status_code=422,
                code=_CODE_ARCHIVE,
                message="ZIP upload must be bytes",
            )
        raw = bytes(raw_zip)
        if len(raw) > MAX_ZIP_UPLOAD_BYTES:
            raise ApiException(
                status_code=413,
                code=41390,
                message=f"ZIP upload exceeds {MAX_ZIP_UPLOAD_BYTES} bytes",
            )
        if not raw:
            raise ApiException(
                status_code=422,
                code=_CODE_ARCHIVE,
                message="ZIP upload is empty",
            )

        upload_digest = sha256_bytes(raw)

        try:
            parsed = parse_skill_zip(io.BytesIO(raw), compressed_size=len(raw))
        except ValueError as exc:
            raise _map_package_io_error(exc) from exc

        findings: list[dict[str, Any]] = []
        structural_diff: list[dict[str, Any]] = []
        candidate = parsed
        target_revision: int | None = None
        bound_target_package_id: UUID | None = None
        bound_fork_name: str | None = None

        if mode == "create":
            self._assert_create_namespace(parsed)
            candidate = parsed

        elif mode == "append_to_existing":
            if target_package_id is None:
                raise ApiException(
                    status_code=422,
                    code=_CODE_VALIDATION,
                    message="append_to_existing requires targetPackageId",
                )
            if expected_aggregate_revision is None:
                raise ApiException(
                    status_code=422,
                    code=_CODE_VALIDATION,
                    message="append_to_existing requires expectedAggregateRevision",
                )
            package = self._packages._get_package_or_404(target_package_id)
            if package.canonical_name != parsed.canonical_name:
                raise ApiException(
                    status_code=409,
                    code=40990,
                    message=(
                        f"append package name mismatch: aggregate has "
                        f"{package.canonical_name!r}, archive has "
                        f"{parsed.canonical_name!r}"
                    ),
                )
            current_rev = int(package.aggregate_revision or 0)
            if current_rev != int(expected_aggregate_revision):
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REVISION,
                    message=(
                        f"aggregate revision conflict: expected "
                        f"{expected_aggregate_revision}, current {current_rev}"
                    ),
                    details={
                        "expectedAggregateRevision": expected_aggregate_revision,
                        "currentAggregateRevision": current_rev,
                        "packageId": str(package.id),
                    },
                )
            target_revision = current_rev
            bound_target_package_id = package.id
            # Complete snapshot replace — structural diff vs current draft only.
            left_index: list[dict[str, Any]] | None = None
            if package.draft_version_id is not None:
                try:
                    draft_detail = self._packages.get_version(
                        package.id, package.draft_version_id
                    )
                    left_index = list(draft_detail.resource_index or [])
                except ApiException:
                    left_index = None
            right_index = _resource_index_dicts(parsed)
            structural_diff = _bounded_structural_diff(
                left_index=left_index, right_index=right_index
            )
            candidate = parsed

        elif mode == "fork_as_new":
            if not fork_canonical_name:
                raise ApiException(
                    status_code=422,
                    code=_CODE_VALIDATION,
                    message="fork_as_new requires forkCanonicalName",
                )
            try:
                rewritten_md = rewrite_skill_md_frontmatter_name(
                    parsed.skill_md_bytes, new_name=fork_canonical_name
                )
            except ValueError as exc:
                raise _map_package_io_error(exc) from exc

            files: dict[str, bytes] = {"SKILL.md": rewritten_md}
            if parsed.mindatlas_yaml_bytes is not None:
                files["mindatlas.yaml"] = parsed.mindatlas_yaml_bytes
            for resource in parsed.resources:
                files[resource.path] = resource.content
            try:
                candidate = parse_skill_directory_files(
                    files, expected_root_name=fork_canonical_name
                )
            except ValueError as exc:
                raise _map_package_io_error(exc) from exc
            self._assert_create_namespace(candidate)
            bound_fork_name = fork_canonical_name
            findings.append(
                {
                    "code": "fork_name_rewritten",
                    "message": (
                        f"frontmatter name rewritten from "
                        f"{parsed.canonical_name!r} to {candidate.canonical_name!r}"
                    ),
                }
            )

        resource_index = _resource_index_dicts(candidate)
        caps = _capability_keys(candidate)
        preview_id = uuid4()
        expires_at = utcnow() + PREVIEW_TTL
        actor = _actor_scope_digest(principal)
        preview_digest = _preview_digest_for(
            preview_id=preview_id,
            actor_scope_digest=actor,
            mode=mode,
            target_package_id=bound_target_package_id,
            expected_aggregate_revision=target_revision,
            upload_digest=upload_digest,
            candidate_content_digest=candidate.content_digest,
            candidate_canonical_name=candidate.canonical_name,
            fork_canonical_name=bound_fork_name,
            expires_at=expires_at,
        )

        row = AssistantSkillImportPreview(
            id=preview_id,
            principal_id=principal.principal_id,
            principal_role=principal.role,
            actor_scope_digest=actor,
            mode=mode,
            target_package_id=bound_target_package_id,
            expected_aggregate_revision=target_revision,
            candidate_canonical_name=candidate.canonical_name,
            fork_canonical_name=bound_fork_name,
            upload_digest=upload_digest,
            candidate_content_digest=candidate.content_digest,
            preview_digest=preview_digest,
            findings=list(findings),
            structural_diff=list(structural_diff),
            resource_index=list(resource_index),
            capability_keys=list(caps),
            archive_bytes=raw,
            expires_at=expires_at,
            consumed=False,
        )
        self.db.add(row)
        self.db.commit()

        return ImportPreviewResult(
            preview_id=preview_id,
            mode=mode,  # type: ignore[arg-type]
            upload_digest=upload_digest,
            candidate_content_digest=candidate.content_digest,
            preview_digest=preview_digest,
            candidate_canonical_name=candidate.canonical_name,
            target_package_id=bound_target_package_id,
            expected_aggregate_revision=target_revision,
            expires_at=expires_at,
            resource_index=resource_index,
            capability_keys=caps,
            findings=findings,
            structural_diff=structural_diff,
            resource_bytes_excluded=True,
            raw_archive_excluded=True,
        )

    def _assert_create_namespace(self, parsed: ParsedSkillPackage) -> None:
        legacy = (
            list(parsed.manifest.legacy_aliases) if parsed.manifest is not None else []
        )
        self._packages._assert_import_namespace_free(
            canonical_name=parsed.canonical_name,
            legacy_aliases=legacy,
        )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(
        self,
        *,
        preview_id: UUID,
        request_id: str,
        principal: OperatorPrincipal | None,
        preview_digest: str | None = None,
    ) -> ImportApplyResult:
        principal = self._require_principal(principal, operator=False)
        if not request_id or not isinstance(request_id, str):
            raise ApiException(
                status_code=422,
                code=_CODE_VALIDATION,
                message="requestId is required",
            )
        request_id = request_id.strip()
        if not request_id or len(request_id) > 128:
            raise ApiException(
                status_code=422,
                code=_CODE_VALIDATION,
                message="requestId must be 1..128 characters",
            )

        row = self._lock_preview(preview_id)
        now = utcnow()

        if preview_digest is not None and preview_digest != row.preview_digest:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="import preview digest mismatch",
                details={"previewId": str(preview_id)},
            )

        actor = _actor_scope_digest(principal)
        if actor != row.actor_scope_digest:
            raise ApiException(
                status_code=403,
                code=_CODE_FORBIDDEN,
                message="import preview actor scope mismatch",
            )

        apply_payload = {
            "preview_id": str(preview_id),
            "mode": row.mode,
            "upload_digest": row.upload_digest,
            "candidate_content_digest": row.candidate_content_digest,
            "preview_digest": row.preview_digest,
            "target_package_id": str(row.target_package_id)
            if row.target_package_id
            else None,
            "expected_aggregate_revision": row.expected_aggregate_revision,
            "candidate_canonical_name": row.candidate_canonical_name,
        }
        digest = _request_digest("import_apply", apply_payload)

        # Consumed preview: same requestId + same payload → prior result;
        # any other requestId (or mismatched payload) → clean conflict.
        if row.consumed:
            if (
                row.applied_request_id == request_id
                and row.applied_request_digest == digest
                and row.applied_package_id
            ):
                detail = self._packages.get_package(row.applied_package_id)
                return ImportApplyResult(
                    mode=row.mode,  # type: ignore[arg-type]
                    preview_id=preview_id,
                    request_id=request_id,
                    package=detail,
                )
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REQUEST,
                message=(
                    "import preview already consumed"
                    if row.applied_request_id != request_id
                    else "requestId was reused with a different payload"
                ),
                details={
                    "requestId": request_id,
                    "previewId": str(preview_id),
                },
            )

        if _as_aware(row.expires_at) <= now:
            # Drop raw archive if still present, keep audit fields.
            if row.archive_bytes is not None:
                row.archive_bytes = None
                self.db.commit()
            raise ApiException(
                status_code=410,
                code=_CODE_EXPIRED,
                message="import preview has expired",
                details={"previewId": str(preview_id)},
            )

        # Global requestId CAS via unique package.last_admin_request_id (+ preview applied_request_id).
        durable = self._lookup_by_admin_request(request_id, digest)
        if durable is not None:
            row.consumed = True
            row.applied_package_id = durable.id
            row.applied_request_id = request_id
            row.applied_request_digest = digest
            row.applied_at = now
            row.archive_bytes = None
            self.db.commit()
            return ImportApplyResult(
                mode=row.mode,  # type: ignore[arg-type]
                preview_id=preview_id,
                request_id=request_id,
                package=durable,
            )

        # Re-hash exact stored bytes and re-parse via Plan 01.
        archive = row.archive_bytes
        if not archive or sha256_bytes(bytes(archive)) != row.upload_digest:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="upload bytes changed since preview",
            )
        try:
            reparsed = parse_skill_zip(
                io.BytesIO(bytes(archive)), compressed_size=len(archive)
            )
        except ValueError as exc:
            raise _map_package_io_error(exc) from exc

        candidate = reparsed
        if row.mode == "fork_as_new":
            if not row.fork_canonical_name:
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_STALE,
                    message="fork preview missing fork name",
                )
            try:
                rewritten_md = rewrite_skill_md_frontmatter_name(
                    reparsed.skill_md_bytes, new_name=row.fork_canonical_name
                )
                files: dict[str, bytes] = {"SKILL.md": rewritten_md}
                if reparsed.mindatlas_yaml_bytes is not None:
                    files["mindatlas.yaml"] = reparsed.mindatlas_yaml_bytes
                for resource in reparsed.resources:
                    files[resource.path] = resource.content
                candidate = parse_skill_directory_files(
                    files, expected_root_name=row.fork_canonical_name
                )
            except ValueError as exc:
                raise _map_package_io_error(exc) from exc

        if candidate.content_digest != row.candidate_content_digest:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="candidate content digest changed since preview",
            )
        if candidate.canonical_name != row.candidate_canonical_name:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="candidate canonical name changed since preview",
            )

        # Single transaction: package mutate (flush-only) + preview consume stamp.
        # Durable requestId reservation is the package unique last_admin_request_id
        # stamp, flushed before/with create/append and committed with consume.
        try:
            if row.mode in {"create", "fork_as_new"}:
                detail = self._apply_create(
                    candidate, request_id=request_id, digest=digest
                )
            elif row.mode == "append_to_existing":
                detail = self._apply_append(
                    candidate,
                    package_id=row.target_package_id,  # type: ignore[arg-type]
                    expected_revision=row.expected_aggregate_revision,  # type: ignore[arg-type]
                    request_id=request_id,
                    digest=digest,
                )
            else:
                raise ApiException(
                    status_code=422,
                    code=_CODE_VALIDATION,
                    message=f"invalid import mode: {row.mode!r}",
                )

            row.consumed = True
            row.applied_package_id = detail.id
            row.applied_request_id = request_id
            row.applied_request_digest = digest
            row.applied_at = utcnow()
            row.archive_bytes = None
            self.db.commit()
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            # Unique last_admin_request_id / applied_request_id collision.
            durable = self._lookup_by_admin_request(request_id, digest)
            if durable is not None:
                # Identical concurrent winner — stamp this preview if still open.
                try:
                    row = self._lock_preview(preview_id)
                    if not row.consumed:
                        row.consumed = True
                        row.applied_package_id = durable.id
                        row.applied_request_id = request_id
                        row.applied_request_digest = digest
                        row.applied_at = utcnow()
                        row.archive_bytes = None
                        self.db.commit()
                    elif (
                        row.applied_request_id != request_id
                        or row.applied_request_digest != digest
                    ):
                        raise ApiException(
                            status_code=409,
                            code=_CODE_CONFLICT_REQUEST,
                            message="import preview already consumed",
                            details={
                                "requestId": request_id,
                                "previewId": str(preview_id),
                            },
                        ) from exc
                except ApiException:
                    raise
                except IntegrityError as stamp_exc:
                    self.db.rollback()
                    raise ApiException(
                        status_code=409,
                        code=_CODE_CONFLICT_REQUEST,
                        message="requestId was reused with a different payload",
                        details={"requestId": request_id},
                    ) from stamp_exc
                return ImportApplyResult(
                    mode=row.mode,  # type: ignore[arg-type]
                    preview_id=preview_id,
                    request_id=request_id,
                    package=durable,
                )
            # Different payload already owns this requestId (or other uniqueness).
            mapped = self._packages._translate_integrity_error(exc)
            if mapped.code == _CODE_CONFLICT_REQUEST:
                raise mapped from exc
            other = (
                self.db.query(AssistantSkillImportPreview)
                .filter(AssistantSkillImportPreview.applied_request_id == request_id)
                .one_or_none()
            )
            if other is not None and other.id != preview_id:
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                ) from exc
            raise self._packages._as_import_conflict(mapped) from exc

        return ImportApplyResult(
            mode=row.mode,  # type: ignore[arg-type]
            preview_id=preview_id,
            request_id=request_id,
            package=detail,
        )

    def _lookup_by_admin_request(
        self, request_id: str, digest: str
    ) -> SkillPackageDetail | None:
        """Return package stamped with this requestId, or raise on digest mismatch."""
        package = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.last_admin_request_id == request_id)
            .one_or_none()
        )
        if package is None:
            return None
        last_digest = getattr(package, "last_admin_request_digest", None)
        if last_digest == digest:
            return self._packages.get_package(package.id)
        raise ApiException(
            status_code=409,
            code=_CODE_CONFLICT_REQUEST,
            message="requestId was reused with a different payload",
            details={"requestId": request_id},
        )

    def _apply_create(
        self,
        parsed: ParsedSkillPackage,
        *,
        request_id: str,
        digest: str,
    ) -> SkillPackageDetail:
        # Plan 01 create-only import: unpublished + catalog disabled.
        # Flush-only so preview consume stamps share one commit with package insert.
        # Unique last_admin_request_id is the durable global requestId reservation.
        try:
            return self._packages.import_package(
                parsed,
                actor_id=None,
                origin="import",
                admin_request_id=request_id,
                admin_request_digest=digest,
                commit=False,
            )
        except ApiException as exc:
            # Race / cold-index: package may already exist with this requestId.
            if exc.code in {40995, _CODE_CONFLICT_REQUEST}:
                durable = self._lookup_by_admin_request(request_id, digest)
                if durable is not None:
                    return durable
            raise

    def _apply_append(
        self,
        parsed: ParsedSkillPackage,
        *,
        package_id: UUID,
        expected_revision: int,
        request_id: str,
        digest: str,
    ) -> SkillPackageDetail:
        # Flush-only: outer apply() stamps preview consume and commits once.
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

        # Package-level requestId CAS (append targets an existing aggregate).
        last_id = getattr(package, "last_admin_request_id", None)
        last_digest = getattr(package, "last_admin_request_digest", None)
        if last_id == request_id:
            if last_digest == digest:
                return self._packages.get_package(package.id)
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REQUEST,
                message="requestId was reused with a different payload",
                details={"requestId": request_id},
            )

        # Global uniqueness: another package may already own this requestId.
        other = (
            self.db.query(AssistantSkillPackage)
            .filter(
                AssistantSkillPackage.last_admin_request_id == request_id,
                AssistantSkillPackage.id != package.id,
            )
            .one_or_none()
        )
        if other is not None:
            other_digest = getattr(other, "last_admin_request_digest", None)
            if other_digest == digest:
                return self._packages.get_package(other.id)
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REQUEST,
                message="requestId was reused with a different payload",
                details={"requestId": request_id},
            )

        current_rev = int(package.aggregate_revision or 0)
        if current_rev != int(expected_revision):
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REVISION,
                message=(
                    f"aggregate revision conflict: expected {expected_revision}, "
                    f"current {current_rev}"
                ),
                details={
                    "expectedAggregateRevision": expected_revision,
                    "currentAggregateRevision": current_rev,
                    "packageId": str(package.id),
                },
            )

        if package.canonical_name != parsed.canonical_name:
            raise ApiException(
                status_code=409,
                code=40990,
                message=(
                    f"canonical name is immutable: package has "
                    f"{package.canonical_name!r}, payload has "
                    f"{parsed.canonical_name!r}"
                ),
            )

        # Complete snapshot replace as new draft (Plan 01 content-digest reuse).
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
            draft = existing
        else:
            next_seq = self._packages._next_sequence(package.id)
            draft = self._packages._insert_draft_version(
                package=package,
                parsed=parsed,
                version_name=f"import-append-{next_seq}",
                origin="import",
                sequence_no=next_seq,
            )

        # Append any new legacy aliases (append-only; never rewrite).
        if parsed.manifest and parsed.manifest.legacy_aliases:
            self._packages._append_legacy_aliases(
                package_id=package.id,
                legacy_aliases=list(parsed.manifest.legacy_aliases),
            )

        package.draft_version_id = draft.id
        package.display_name = _display_name_for(parsed)
        package.description = parsed.frontmatter.description
        # Append only advances draft + revision. Leave published_version_id,
        # catalog_enabled, and catalog evidence fields untouched (no
        # auto-publish / auto-disable / auto-enable).
        package.aggregate_revision = current_rev + 1
        package.last_admin_request_id = request_id
        package.last_admin_request_digest = digest
        self.db.flush()
        return self._packages.get_package(package.id)


def clear_import_preview_store_for_tests() -> None:
    """Test helper — process-local no-op.

    Preview state is durable in the database. Tests call this after preview to
    prove process memory is irrelevant to apply.
    """
    return None


def expire_import_previews(db: Session, *, now: datetime | None = None) -> int:
    """Module-level expiry helper used by tests / maintenance jobs."""
    return ImportPreviewService(db).expire(now=now)


__all__ = [
    "ImportPreviewService",
    "PREVIEW_TTL",
    "clear_import_preview_store_for_tests",
    "expire_import_previews",
]
