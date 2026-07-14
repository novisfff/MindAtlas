"""Bounded skill resource reads for active published Skills (Plan 04 Task 7)."""

from __future__ import annotations

import base64
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    completed_result,
    failed_result,
)
from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.domain.digests import JsonValue, sha256_bytes

RESOURCE_NOT_ACTIVE = "resource_not_active"
RESOURCE_NOT_FOUND = "resource_not_found"
RESOURCE_RANGE_INVALID = "resource_range_invalid"
DEFAULT_CHUNK = 16_384
DEFAULT_MAX_PER_CALL = 65_536


class ResourceBytesPort(Protocol):
    def get_resource_bytes(
        self, package_id: UUID, version_id: UUID, path: str
    ) -> bytes: ...

    def get_resource_meta(
        self, package_id: UUID, version_id: UUID, path: str
    ) -> Mapping[str, Any] | None: ...


def is_skill_version_active(
    manifest: ResolvedRunManifestRevision,
    skill_version_id: UUID,
) -> bool:
    return any(item.version_id == skill_version_id for item in manifest.active_skills)


def package_id_for_active_version(
    manifest: ResolvedRunManifestRevision,
    skill_version_id: UUID,
) -> UUID | None:
    for item in manifest.active_skills:
        if item.version_id == skill_version_id:
            return item.package_id
    return None


def read_skill_resource_chunk(
    *,
    call_id: str,
    validated_input: dict[str, JsonValue],
    manifest: ResolvedRunManifestRevision,
    resource_port: ResourceBytesPort,
    default_chunk: int = DEFAULT_CHUNK,
    max_per_call: int = DEFAULT_MAX_PER_CALL,
) -> CapabilityResult:
    """Bounded read of an immutable published resource for an active Skill version."""
    version_raw = validated_input.get("skillVersionId") or validated_input.get(
        "skill_version_id"
    )
    path = validated_input.get("path")
    if not isinstance(version_raw, str) or not isinstance(path, str) or not path:
        return _fail(call_id, RESOURCE_NOT_FOUND, "skill resource not found")
    try:
        version_id = UUID(version_raw)
    except (TypeError, ValueError):
        return _fail(call_id, RESOURCE_NOT_FOUND, "skill resource not found")

    if not is_skill_version_active(manifest, version_id):
        return _fail(call_id, RESOURCE_NOT_ACTIVE, "skill version is not active")

    package_id = package_id_for_active_version(manifest, version_id)
    if package_id is None:
        return _fail(call_id, RESOURCE_NOT_ACTIVE, "skill version is not active")

    offset_raw = validated_input.get("offset", 0)
    limit_raw = validated_input.get("limit", default_chunk)
    offset = (
        int(offset_raw)
        if isinstance(offset_raw, int) and not isinstance(offset_raw, bool)
        else 0
    )
    limit = (
        int(limit_raw)
        if isinstance(limit_raw, int) and not isinstance(limit_raw, bool)
        else default_chunk
    )
    if offset < 0 or limit < 1:
        return _fail(call_id, RESOURCE_RANGE_INVALID, "resource range invalid")
    if limit > max_per_call:
        return _fail(call_id, RESOURCE_RANGE_INVALID, "resource range invalid")

    # Normalize path lightly: reject traversal / absolute.
    if path.startswith("/") or ".." in path.split("/"):
        return _fail(call_id, RESOURCE_NOT_FOUND, "skill resource not found")

    try:
        content = resource_port.get_resource_bytes(package_id, version_id, path)
    except Exception:
        return _fail(call_id, RESOURCE_NOT_FOUND, "skill resource not found")

    total = len(content)
    if offset > total:
        return _fail(call_id, RESOURCE_RANGE_INVALID, "resource range invalid")
    chunk = content[offset : offset + limit]
    digest = sha256_bytes(content)
    meta = None
    try:
        meta = resource_port.get_resource_meta(package_id, version_id, path)
    except Exception:
        meta = None
    media_type = "application/octet-stream"
    if isinstance(meta, Mapping):
        media_type = str(meta.get("media_type") or meta.get("mediaType") or media_type)

    encoding: str
    try:
        text = chunk.decode("utf-8")
        encoding = "utf-8"
        body: str = text
    except UnicodeDecodeError:
        encoding = "base64"
        body = base64.b64encode(chunk).decode("ascii")

    payload: dict[str, JsonValue] = {
        "path": path,
        "mediaType": media_type,
        "totalSize": total,
        "offset": offset,
        "returnedBytes": len(chunk),
        "eof": offset + len(chunk) >= total,
        "contentDigest": digest,
        "encoding": encoding,
        "content": body,
    }
    # scripts/ are returned as inert bytes/text only — no execute path exists.
    return completed_result(
        user_text=None,
        structured_output=payload,
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
        terminal_output=False,
        needs_followup=True,
    )


def _fail(call_id: str, code: str, message: str) -> CapabilityResult:
    return failed_result(
        error=CapabilityError(
            error_type="execution_failed"
            if code != RESOURCE_NOT_FOUND
            else "not_found",
            safe_code=code[:64],
            safe_message=message[:256],
            retry_disposition="never",
            call_id=call_id,
        ),
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
    )


__all__ = [
    "DEFAULT_CHUNK",
    "DEFAULT_MAX_PER_CALL",
    "RESOURCE_NOT_ACTIVE",
    "RESOURCE_NOT_FOUND",
    "RESOURCE_RANGE_INVALID",
    "ResourceBytesPort",
    "is_skill_version_active",
    "package_id_for_active_version",
    "read_skill_resource_chunk",
]
