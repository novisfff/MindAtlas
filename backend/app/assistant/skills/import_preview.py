"""Safe skill package import preview / apply (Plan 09 Task 2).

Two-step flow:
  1. ``preview`` — stream ZIP into bounded memory, parse via Plan 01 package_io,
     bind an opaque preview token (actor/mode/target revision/digests/expiry).
  2. ``apply`` — recheck token/bytes/revision, append one unpublished
     catalog-disabled draft via Plan 01 services, consume requestId/preview once.

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
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_bytes
from app.assistant.skills.contracts import ParsedSkillPackage
from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
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
from app.assistant.skills.service import AgentSkillService
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


@dataclass
class _PreviewRecord:
    token: ImportPreviewToken
    raw_zip: bytes
    parsed_content_digest: str
    candidate_canonical_name: str
    fork_canonical_name: str | None
    resource_index: list[dict[str, Any]]
    capability_keys: list[str]
    findings: list[dict[str, Any]]
    structural_diff: list[dict[str, Any]]
    consumed: bool = False
    applied_package_id: UUID | None = None
    applied_request_id: str | None = None
    applied_request_digest: str | None = None


# Process-local preview store (dev/test). Tokens bind exact upload digests and
# never leave the server; raw bytes are dropped on consume/expiry.
_STORE_LOCK = threading.RLock()
_PREVIEW_STORE: dict[UUID, _PreviewRecord] = {}
# Global requestId index for create/fork (no package row yet at first apply).
_REQUEST_INDEX: dict[str, dict[str, Any]] = {}


def _purge_expired_locked(now: datetime | None = None) -> None:
    current = now or utcnow()
    expired = [
        pid
        for pid, rec in _PREVIEW_STORE.items()
        if rec.token.expires_at <= current
    ]
    for pid in expired:
        _PREVIEW_STORE.pop(pid, None)


class ImportPreviewService:
    """Owns temporary upload/preview tokens; apply calls Plan 01 package service."""

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
    # Internal record access (tests may call _get_record)
    # ------------------------------------------------------------------

    def _get_record(self, preview_id: UUID) -> _PreviewRecord:
        with _STORE_LOCK:
            record = _PREVIEW_STORE.get(preview_id)
            if record is None:
                # Opportunistic purge of other expired tokens; this id is unknown.
                _purge_expired_locked()
                raise ApiException(
                    status_code=404,
                    code=_CODE_NOT_FOUND,
                    message=f"import preview not found: {preview_id}",
                )
            if record.token.expires_at <= utcnow():
                _PREVIEW_STORE.pop(preview_id, None)
                raise ApiException(
                    status_code=410,
                    code=_CODE_EXPIRED,
                    message="import preview has expired",
                    details={"previewId": str(preview_id)},
                )
            return record

    def _store_record(self, record: _PreviewRecord) -> None:
        with _STORE_LOCK:
            _purge_expired_locked()
            _PREVIEW_STORE[record.token.preview_id] = record

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
        token = ImportPreviewToken(
            preview_id=preview_id,
            actor_scope_digest=_actor_scope_digest(principal),
            mode=mode,  # type: ignore[arg-type]
            target_package_id=target_package_id if mode == "append_to_existing" else None,
            expected_aggregate_revision=target_revision
            if mode == "append_to_existing"
            else None,
            upload_digest=upload_digest,
            candidate_content_digest=candidate.content_digest,
            expires_at=expires_at,
        )
        record = _PreviewRecord(
            token=token,
            raw_zip=raw,
            parsed_content_digest=candidate.content_digest,
            candidate_canonical_name=candidate.canonical_name,
            fork_canonical_name=fork_canonical_name if mode == "fork_as_new" else None,
            resource_index=resource_index,
            capability_keys=caps,
            findings=findings,
            structural_diff=structural_diff,
        )
        self._store_record(record)

        return ImportPreviewResult(
            preview_id=preview_id,
            mode=mode,  # type: ignore[arg-type]
            upload_digest=upload_digest,
            candidate_content_digest=candidate.content_digest,
            candidate_canonical_name=candidate.canonical_name,
            target_package_id=token.target_package_id,
            expected_aggregate_revision=token.expected_aggregate_revision,
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

        record = self._get_record(preview_id)
        token = record.token
        now = utcnow()
        if token.expires_at <= now:
            with _STORE_LOCK:
                _PREVIEW_STORE.pop(preview_id, None)
            raise ApiException(
                status_code=410,
                code=_CODE_EXPIRED,
                message="import preview has expired",
                details={"previewId": str(preview_id)},
            )

        actor = _actor_scope_digest(principal)
        if actor != token.actor_scope_digest:
            raise ApiException(
                status_code=403,
                code=_CODE_FORBIDDEN,
                message="import preview actor scope mismatch",
            )

        apply_payload = {
            "preview_id": str(preview_id),
            "mode": token.mode,
            "upload_digest": token.upload_digest,
            "candidate_content_digest": token.candidate_content_digest,
            "target_package_id": str(token.target_package_id)
            if token.target_package_id
            else None,
            "expected_aggregate_revision": token.expected_aggregate_revision,
            "candidate_canonical_name": record.candidate_canonical_name,
        }
        digest = _request_digest("import_apply", apply_payload)

        # Idempotent retry: same requestId + same payload → prior result.
        if record.consumed and record.applied_request_id == request_id:
            if record.applied_request_digest == digest and record.applied_package_id:
                detail = self._packages.get_package(record.applied_package_id)
                return ImportApplyResult(
                    mode=token.mode,  # type: ignore[arg-type]
                    preview_id=preview_id,
                    request_id=request_id,
                    package=detail,
                )
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_REQUEST,
                message="requestId was reused with a different payload",
                details={"requestId": request_id},
            )

        # Global requestId CAS for create/fork (and first-time apply).
        with _STORE_LOCK:
            prior = _REQUEST_INDEX.get(request_id)
            if prior is not None:
                if prior.get("digest") == digest and prior.get("package_id"):
                    detail = self._packages.get_package(UUID(str(prior["package_id"])))
                    return ImportApplyResult(
                        mode=token.mode,  # type: ignore[arg-type]
                        preview_id=preview_id,
                        request_id=request_id,
                        package=detail,
                    )
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_REQUEST,
                    message="requestId was reused with a different payload",
                    details={"requestId": request_id},
                )

        # Re-hash exact stored bytes and re-parse via Plan 01.
        if sha256_bytes(record.raw_zip) != token.upload_digest:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="upload bytes changed since preview",
            )
        try:
            reparsed = parse_skill_zip(
                io.BytesIO(record.raw_zip), compressed_size=len(record.raw_zip)
            )
        except ValueError as exc:
            raise _map_package_io_error(exc) from exc

        candidate = reparsed
        if token.mode == "fork_as_new":
            if not record.fork_canonical_name:
                raise ApiException(
                    status_code=409,
                    code=_CODE_CONFLICT_STALE,
                    message="fork preview missing fork name",
                )
            try:
                rewritten_md = rewrite_skill_md_frontmatter_name(
                    reparsed.skill_md_bytes, new_name=record.fork_canonical_name
                )
                files: dict[str, bytes] = {"SKILL.md": rewritten_md}
                if reparsed.mindatlas_yaml_bytes is not None:
                    files["mindatlas.yaml"] = reparsed.mindatlas_yaml_bytes
                for resource in reparsed.resources:
                    files[resource.path] = resource.content
                candidate = parse_skill_directory_files(
                    files, expected_root_name=record.fork_canonical_name
                )
            except ValueError as exc:
                raise _map_package_io_error(exc) from exc

        if candidate.content_digest != token.candidate_content_digest:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="candidate content digest changed since preview",
            )
        if candidate.canonical_name != record.candidate_canonical_name:
            raise ApiException(
                status_code=409,
                code=_CODE_CONFLICT_STALE,
                message="candidate canonical name changed since preview",
            )

        try:
            if token.mode == "create":
                detail = self._apply_create(
                    candidate, request_id=request_id, digest=digest
                )
            elif token.mode == "fork_as_new":
                detail = self._apply_create(
                    candidate, request_id=request_id, digest=digest
                )
            elif token.mode == "append_to_existing":
                detail = self._apply_append(
                    candidate,
                    package_id=token.target_package_id,  # type: ignore[arg-type]
                    expected_revision=token.expected_aggregate_revision,  # type: ignore[arg-type]
                    request_id=request_id,
                    digest=digest,
                )
            else:
                raise ApiException(
                    status_code=422,
                    code=_CODE_VALIDATION,
                    message=f"invalid import mode: {token.mode!r}",
                )
        except ApiException:
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._as_import_conflict(
                self._packages._translate_integrity_error(exc)
            ) from exc

        with _STORE_LOCK:
            record.consumed = True
            record.applied_package_id = detail.id
            record.applied_request_id = request_id
            record.applied_request_digest = digest
            # Drop raw archive bytes after successful apply (safety).
            record.raw_zip = b""
            _REQUEST_INDEX[request_id] = {
                "digest": digest,
                "package_id": str(detail.id),
                "preview_id": str(preview_id),
                "mode": token.mode,
            }

        return ImportApplyResult(
            mode=token.mode,  # type: ignore[arg-type]
            preview_id=preview_id,
            request_id=request_id,
            package=detail,
        )

    def _apply_create(
        self,
        parsed: ParsedSkillPackage,
        *,
        request_id: str,
        digest: str,
    ) -> SkillPackageDetail:
        # Plan 01 create-only import: unpublished + catalog disabled.
        detail = self._packages.import_package(
            parsed, actor_id=None, origin="import"
        )
        # Stamp requestId on the new aggregate for future CAS continuity.
        package = self.db.get(AssistantSkillPackage, detail.id)
        if package is not None:
            package.last_admin_request_id = request_id
            package.last_admin_request_digest = digest
            # Ensure draft-only invariants (import_package already does this).
            package.catalog_enabled = False
            package.published_version_id = None
            self.db.commit()
            detail = self._packages.get_package(detail.id)
        return detail

    def _apply_append(
        self,
        parsed: ParsedSkillPackage,
        *,
        package_id: UUID,
        expected_revision: int,
        request_id: str,
        digest: str,
    ) -> SkillPackageDetail:
        try:
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
            from app.assistant.skills.service import _display_name_for

            package.display_name = _display_name_for(parsed)
            package.description = parsed.frontmatter.description
            # Never auto-publish or catalog-enable on import append.
            package.catalog_enabled = False
            package.aggregate_revision = current_rev + 1
            package.last_admin_request_id = request_id
            package.last_admin_request_digest = digest
            self.db.commit()
            return self._packages.get_package(package.id)
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise self._packages._translate_integrity_error(exc) from exc


def clear_import_preview_store_for_tests() -> None:
    """Test helper — drop all in-process preview tokens."""
    with _STORE_LOCK:
        _PREVIEW_STORE.clear()
        _REQUEST_INDEX.clear()


__all__ = [
    "ImportPreviewService",
    "PREVIEW_TTL",
    "clear_import_preview_store_for_tests",
]
