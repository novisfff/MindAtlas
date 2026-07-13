"""HTTP surface for Agent Skill packages and Main Agent profiles (Plan 01 Task 9).

Two child routers own their full prefixes; ``app.main`` registers them without an
extra prefix. Legacy ``/api/assistant-config/skills`` is intentionally untouched.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.assistant.skills.package_io import (
    MAX_ZIP_UPLOAD_BYTES,
    normalize_package_path,
    parse_skill_directory_files,
    parse_skill_zip,
)
from app.assistant.skills.schemas import (
    CreateSkillPackageCommand,
    MainAgentProfileSnapshotV1,
    PublishMainAgentProfileCommand,
    PublishSkillVersionCommand,
    SaveMainAgentProfileDraftCommand,
    SaveSkillDraftCommand,
    SkillPackageJsonCreateRequest,
    SkillPackageJsonSaveRequest,
)
from app.assistant.skills.service import AgentSkillService, MainAgentProfileService
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.common.schemas import CamelModel
from app.database import get_db

skill_package_router = APIRouter(
    prefix="/api/assistant-config/skill-packages",
    tags=["assistant-skill-packages"],
)

main_agent_profile_router = APIRouter(
    prefix="/api/assistant-config/main-agent-profiles",
    tags=["assistant-main-agent-profiles"],
)

MAX_JSON_BODY_BYTES = 36 * 1024 * 1024
STREAM_CHUNK_SIZE = 64 * 1024
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

PublicationState = Literal["unpublished", "published"]


class PublishDraftRequest(CamelModel):
    """Body-selected draft publish; never resolves latest implicitly."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    draft_version_id: UUID = Field(alias="draftVersionId")


class MainAgentDraftSaveRequest(CamelModel):
    """Router-facing Main Agent draft body."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    snapshot: MainAgentProfileSnapshotV1
    version_name: str | None = Field(default=None, alias="versionName")


def _dto(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, mode="json")
    return model


def _json_safe(value: Any) -> Any:
    """Recursively coerce values so ApiException details are JSON-serializable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _validation_details(exc: ValidationError) -> dict[str, Any]:
    return {
        "type": "validation_error",
        "details": _json_safe(exc.errors()),
    }


def _page(items: list[Any], *, total: int, limit: int, offset: int) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_LIST_LIMIT
    return min(limit, MAX_LIST_LIMIT)


async def _read_bounded_body(request: Request, *, max_bytes: int) -> bytes:
    """Stream the request body and reject payloads above ``max_bytes`` early.

    Declared ``Content-Length`` is only an early rejection hint; streamed bytes
    are always counted because the header is optional/untrusted.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_n = int(declared)
        except ValueError as exc:
            raise ApiException(
                status_code=422,
                code=42292,
                message="invalid Content-Length header",
            ) from exc
        if declared_n < 0:
            raise ApiException(
                status_code=422,
                code=42292,
                message="invalid Content-Length header",
            )
        if declared_n > max_bytes:
            raise ApiException(
                status_code=413,
                code=41390,
                message=f"request body exceeds {max_bytes} bytes",
                details={"type": "payload_too_large", "details": {"maxBytes": max_bytes}},
            )

    buf = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        remaining = max_bytes - len(buf)
        if len(chunk) > remaining:
            # Consume one extra byte past the bound, then stop.
            buf.extend(chunk[: remaining + 1])
            raise ApiException(
                status_code=413,
                code=41390,
                message=f"request body exceeds {max_bytes} bytes",
                details={"type": "payload_too_large", "details": {"maxBytes": max_bytes}},
            )
        buf.extend(chunk)
    return bytes(buf)


def _parse_json_object(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiException(
            status_code=422,
            code=42292,
            message="request body must be UTF-8 JSON",
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApiException(
            status_code=422,
            code=42292,
            message="request body must be valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise ApiException(
            status_code=422,
            code=42292,
            message="request body must be a JSON object",
        )
    return payload


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

    # Size / entry-count limits only. Do not match bare "file " — messages like
    # "package file map must be…", "ZIP archive has no file entries", or
    # "file path must be…" are archive/path validation (42292).
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
        )
    ):
        return ApiException(status_code=422, code=42290, message=msg)

    # Archive / path / link safety (default for package_io ValueError).
    return ApiException(status_code=422, code=42292, message=msg)


def _files_from_json_request(
    body: SkillPackageJsonCreateRequest | SkillPackageJsonSaveRequest,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "SKILL.md": body.skill_md.encode("utf-8"),
    }
    if body.mindatlas_yaml is not None:
        files["mindatlas.yaml"] = body.mindatlas_yaml.encode("utf-8")

    for resource in body.resources:
        try:
            path = normalize_package_path(resource.path, field="resource path")
        except ValueError as exc:
            raise _map_package_io_error(exc) from exc
        if path in {"SKILL.md", "mindatlas.yaml"}:
            raise ApiException(
                status_code=422,
                code=42292,
                message=f"resource path collides with package root file: {path}",
            )
        try:
            content = base64.b64decode(resource.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ApiException(
                status_code=422,
                code=42292,
                message=f"invalid base64 content for resource {path!r}",
            ) from exc
        files[path] = content
    return files


def _parse_package_from_json(
    body: SkillPackageJsonCreateRequest | SkillPackageJsonSaveRequest,
):
    files = _files_from_json_request(body)
    try:
        return parse_skill_directory_files(files, expected_root_name=None)
    except ValueError as exc:
        raise _map_package_io_error(exc) from exc


def _attachment_disposition(filename: str) -> str:
    """Sanitized ASCII fallback + UTF-8 filename* for Content-Disposition."""
    base = filename.rsplit("/", 1)[-1] or "download"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "download"
    # Neutralize header injection / path separators.
    ascii_name = ascii_name.replace('"', "").replace("\\", "").replace("\n", "")
    encoded = quote(base, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _sort_key_created_desc(item: Any) -> tuple[Any, tuple[int, ...]]:
    """Stable list ordering: created_at DESC, then id DESC."""
    created = getattr(item, "created_at", None)
    # None created_at sorts last among DESC (treat as minimal).
    ts = created.timestamp() if created is not None else float("-inf")
    # Ascending sort on negated ordinals yields reverse lexicographic id order.
    id_str = str(getattr(item, "id", ""))
    return (-ts, tuple(-ord(c) for c in id_str))


# ---------------------------------------------------------------------------
# Skill packages
# ---------------------------------------------------------------------------


@skill_package_router.get("")
def list_skill_packages(
    migration_state: str | None = Query(None, alias="migrationState"),
    publication_state: str | None = Query(None, alias="publicationState"),
    catalog_enabled: bool | None = Query(None, alias="catalogEnabled"),
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if publication_state is not None and publication_state not in {
        "unpublished",
        "published",
    }:
        raise ApiException(
            status_code=422,
            code=42292,
            message="publicationState must be 'unpublished' or 'published'",
        )
    if migration_state is not None and migration_state not in {
        "shadow",
        "native",
        "cutover",
    }:
        raise ApiException(
            status_code=422,
            code=42292,
            message="migrationState must be one of: shadow, native, cutover",
        )

    service = AgentSkillService(db)
    items = service.list_packages()

    filtered = []
    for item in items:
        if migration_state is not None and item.migration_state != migration_state:
            continue
        if catalog_enabled is not None and bool(item.catalog_enabled) is not catalog_enabled:
            continue
        if publication_state == "published" and item.published_version is None:
            continue
        if publication_state == "unpublished" and item.published_version is not None:
            continue
        filtered.append(item)

    filtered.sort(key=_sort_key_created_desc)
    total = len(filtered)
    page_items = filtered[offset : offset + limit]
    return ApiResponse.ok(
        _page(
            [_dto(item) for item in page_items],
            total=total,
            limit=limit,
            offset=offset,
        )
    )


@skill_package_router.post("")
async def create_skill_package(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    raw = await _read_bounded_body(request, max_bytes=MAX_JSON_BODY_BYTES)
    payload = _parse_json_object(raw)
    try:
        body = SkillPackageJsonCreateRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiException(
            status_code=422,
            code=42292,
            message="invalid skill package create body",
            details=_validation_details(exc),
        ) from exc

    parsed = _parse_package_from_json(body)
    service = AgentSkillService(db)
    detail = service.create_native_package(
        CreateSkillPackageCommand(
            parsed=parsed,
            version_name=body.version_name,
            origin="api",
        )
    )
    return ApiResponse.ok(_dto(detail))


@skill_package_router.post("/import")
async def import_skill_package(
    request: Request,
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Create-only ZIP import. Streaming compressed-size bound is 32 MiB."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_n = int(declared)
        except ValueError as exc:
            raise ApiException(
                status_code=422,
                code=42292,
                message="invalid Content-Length header",
            ) from exc
        if declared_n > MAX_ZIP_UPLOAD_BYTES:
            raise ApiException(
                status_code=413,
                code=41390,
                message=f"ZIP upload exceeds {MAX_ZIP_UPLOAD_BYTES} bytes",
                details={
                    "type": "payload_too_large",
                    "details": {"maxBytes": MAX_ZIP_UPLOAD_BYTES},
                },
            )

    # Prefer multipart file when present; otherwise treat raw body as ZIP bytes.
    raw: bytes
    if file is not None:
        buf = bytearray()
        while True:
            chunk = await file.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            remaining = MAX_ZIP_UPLOAD_BYTES - len(buf)
            if len(chunk) > remaining:
                buf.extend(chunk[: remaining + 1])
                raise ApiException(
                    status_code=413,
                    code=41390,
                    message=f"ZIP upload exceeds {MAX_ZIP_UPLOAD_BYTES} bytes",
                    details={
                        "type": "payload_too_large",
                        "details": {"maxBytes": MAX_ZIP_UPLOAD_BYTES},
                    },
                )
            buf.extend(chunk)
        raw = bytes(buf)
    else:
        raw = await _read_bounded_body(request, max_bytes=MAX_ZIP_UPLOAD_BYTES)

    try:
        parsed = parse_skill_zip(io.BytesIO(raw), compressed_size=len(raw))
    except ValueError as exc:
        raise _map_package_io_error(exc) from exc

    service = AgentSkillService(db)
    detail = service.import_package(parsed, actor_id=None, origin="import")
    return ApiResponse.ok(_dto(detail))


@skill_package_router.get("/{package_id}")
def get_skill_package(package_id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AgentSkillService(db)
    return ApiResponse.ok(_dto(service.get_package(package_id)))


@skill_package_router.put("/{package_id}/draft")
async def save_skill_package_draft(
    package_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    raw = await _read_bounded_body(request, max_bytes=MAX_JSON_BODY_BYTES)
    payload = _parse_json_object(raw)
    try:
        body = SkillPackageJsonSaveRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiException(
            status_code=422,
            code=42292,
            message="invalid skill package draft body",
            details=_validation_details(exc),
        ) from exc

    parsed = _parse_package_from_json(body)
    service = AgentSkillService(db)
    version = service.save_draft(
        SaveSkillDraftCommand(
            package_id=package_id,
            parsed=parsed,
            version_name=body.version_name,
            origin="api",
        )
    )
    return ApiResponse.ok(_dto(version))


@skill_package_router.get("/{package_id}/versions")
def list_skill_package_versions(
    package_id: UUID,
    version_source: str | None = Query(None, alias="versionSource"),
    origin: str | None = Query(None),
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if version_source is not None and version_source not in {"save", "publish"}:
        raise ApiException(
            status_code=422,
            code=42292,
            message="versionSource must be 'save' or 'publish'",
        )

    service = AgentSkillService(db)
    items = service.list_versions(package_id)
    filtered = []
    for item in items:
        if version_source is not None and item.version_source != version_source:
            continue
        if origin is not None and item.origin != origin:
            continue
        filtered.append(item)

    filtered.sort(key=_sort_key_created_desc)
    total = len(filtered)
    page_items = filtered[offset : offset + limit]
    return ApiResponse.ok(
        _page(
            [_dto(item) for item in page_items],
            total=total,
            limit=limit,
            offset=offset,
        )
    )


@skill_package_router.get("/{package_id}/versions/{version_id}")
def get_skill_package_version(
    package_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AgentSkillService(db)
    detail = service.get_version(package_id, version_id)
    payload = _dto(detail)
    # Never include resource bytes; metadata-only list is already on the DTO.
    return ApiResponse.ok(payload)


@skill_package_router.post("/{package_id}/publish")
def publish_skill_package(
    package_id: UUID,
    body: PublishDraftRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AgentSkillService(db)
    version = service.publish(
        package_id,
        PublishSkillVersionCommand(draft_version_id=body.draft_version_id),
    )
    return ApiResponse.ok(_dto(version))


@skill_package_router.get("/{package_id}/versions/{version_id}/resources/{path:path}")
def get_skill_package_resource(
    package_id: UUID,
    version_id: UUID,
    path: str,
    db: Session = Depends(get_db),
) -> Response:
    try:
        normalized = normalize_package_path(path, field="resource path")
    except ValueError as exc:
        raise _map_package_io_error(exc) from exc

    service = AgentSkillService(db)
    # Ownership + metadata first (media type is server-stored, never client-supplied).
    version = service.get_version(package_id, version_id)
    media_type = "application/octet-stream"
    for resource in version.resources:
        if resource.path == normalized:
            media_type = resource.media_type or media_type
            break
    else:
        raise ApiException(
            status_code=404,
            code=40492,
            message=f"Skill resource not found: {normalized}",
        )

    content = service.get_resource_bytes(package_id, version_id, normalized)
    filename = normalized.rsplit("/", 1)[-1]
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": _attachment_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(len(content)),
        },
    )


@skill_package_router.get("/{package_id}/versions/{version_id}/export")
def export_skill_package_version(
    package_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    service = AgentSkillService(db)
    package = service.get_package(package_id)
    # Prove version ownership before export.
    version = service.get_version(package_id, version_id)
    raw = service.export_version(package_id=package_id, version_id=version_id)
    filename = f"{package.canonical_name}-{version.sequence_no}.zip"
    return Response(
        content=raw,
        media_type="application/zip",
        headers={
            "Content-Disposition": _attachment_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(len(raw)),
        },
    )


# ---------------------------------------------------------------------------
# Main Agent profiles
# ---------------------------------------------------------------------------


@main_agent_profile_router.get("/default")
def get_default_main_agent_profile(db: Session = Depends(get_db)) -> ApiResponse:
    service = MainAgentProfileService(db)
    # Bootstrap is idempotent; ensures the default row exists for first access.
    try:
        summary = service.get_default()
    except ApiException as exc:
        if exc.code != 40493:
            raise
        summary = service.ensure_default()
    return ApiResponse.ok(_dto(summary))


@main_agent_profile_router.put("/default/draft")
async def save_default_main_agent_draft(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    raw = await _read_bounded_body(request, max_bytes=MAX_JSON_BODY_BYTES)
    payload = _parse_json_object(raw)
    try:
        body = MainAgentDraftSaveRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiException(
            status_code=422,
            code=42294,
            message="invalid main agent profile snapshot",
            details=_validation_details(exc),
        ) from exc

    service = MainAgentProfileService(db)
    try:
        profile = service.get_default()
    except ApiException as exc:
        if exc.code != 40493:
            raise
        profile = service.ensure_default()

    version = service.save_draft(
        profile.id,
        SaveMainAgentProfileDraftCommand(
            snapshot=body.snapshot,
            version_name=body.version_name,
            origin="api",
        ),
    )
    return ApiResponse.ok(_dto(version))


@main_agent_profile_router.get("/default/versions")
def list_default_main_agent_versions(
    version_source: str | None = Query(None, alias="versionSource"),
    origin: str | None = Query(None),
    limit: int = Query(DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if version_source is not None and version_source not in {"save", "publish"}:
        raise ApiException(
            status_code=422,
            code=42292,
            message="versionSource must be 'save' or 'publish'",
        )

    service = MainAgentProfileService(db)
    try:
        profile = service.get_default()
    except ApiException as exc:
        if exc.code != 40493:
            raise
        profile = service.ensure_default()

    items = service.list_versions(profile.id)
    filtered = []
    for item in items:
        if version_source is not None and item.version_source != version_source:
            continue
        if origin is not None and item.origin != origin:
            continue
        filtered.append(item)

    filtered.sort(key=_sort_key_created_desc)
    total = len(filtered)
    page_items = filtered[offset : offset + limit]
    return ApiResponse.ok(
        _page(
            [_dto(item) for item in page_items],
            total=total,
            limit=limit,
            offset=offset,
        )
    )


@main_agent_profile_router.post("/default/publish")
def publish_default_main_agent_profile(
    body: PublishDraftRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = MainAgentProfileService(db)
    try:
        profile = service.get_default()
    except ApiException as exc:
        if exc.code != 40493:
            raise
        profile = service.ensure_default()

    version = service.publish(
        profile.id,
        PublishMainAgentProfileCommand(draft_version_id=body.draft_version_id),
    )
    return ApiResponse.ok(_dto(version))


__all__ = [
    "MAX_JSON_BODY_BYTES",
    "main_agent_profile_router",
    "skill_package_router",
]
