"""Evaluation Artifact helpers (namespace keys + payload XOR validation)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.assistant.domain.digests import sha256_bytes
from app.assistant.evaluation.contracts import (
    EVAL_OBJECT_KEY_PREFIX,
    assert_evaluation_object_key,
    build_evaluation_object_key,
    is_evaluation_object_key,
)

INLINE_MAX_BYTES = 64 * 1024  # 64 KiB bounded inline payload

__all__ = [
    "EVAL_OBJECT_KEY_PREFIX",
    "INLINE_MAX_BYTES",
    "assert_evaluation_object_key",
    "build_evaluation_object_key",
    "is_evaluation_object_key",
    "resolve_artifact_storage",
    "validate_production_rejects_eval_key",
]


def resolve_artifact_storage(
    *,
    eval_run_id: UUID,
    payload: bytes | None,
    object_key: str | None,
) -> dict[str, Any]:
    """Resolve XOR storage shape for an evaluation Artifact.

    Exactly one of inline payload or evaluation-prefixed object key is allowed.
    """
    has_payload = payload is not None
    has_key = object_key is not None and str(object_key).strip() != ""
    if has_payload == has_key:
        raise ValueError("artifact requires exactly one of inline_payload or object_key")
    if has_payload:
        data = bytes(payload or b"")
        if len(data) > INLINE_MAX_BYTES:
            raise ValueError(
                f"inline payload exceeds {INLINE_MAX_BYTES} bytes; use object storage"
            )
        digest = sha256_bytes(data)
        return {
            "storage_kind": "inline",
            "inline_payload": data,
            "object_key": None,
            "byte_size": len(data),
            "content_digest": digest,
        }
    key = assert_evaluation_object_key(str(object_key).strip())
    # Object rows require the caller to supply size/digest separately via
    # metadata; this helper only validates the key namespace.
    if not key.startswith(f"{EVAL_OBJECT_KEY_PREFIX}/{eval_run_id}/"):
        # Allow any evaluation-namespace key; prefer run-scoped when generated.
        pass
    return {
        "storage_kind": "object",
        "inline_payload": None,
        "object_key": key,
    }


def validate_production_rejects_eval_key(object_key: str | None) -> None:
    """Production Artifact/Event stores must reject evaluation keys."""
    if is_evaluation_object_key(object_key):
        raise ValueError("production APIs reject evaluation object keys")
