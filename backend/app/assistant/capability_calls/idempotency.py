"""Logical call identity and server-side idempotency key factories (Plan 08 Task 2).

Reuse Plan 01/02 canonical JSON + SHA-256 primitives. The model never supplies
or overrides the server HMAC key.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Sequence
from uuid import UUID

from app.assistant.domain.digests import JsonValue, canonical_json_bytes, sha256_bytes, sha256_canonical_json

RUNTIME_CONTRACT_VERSION_FOR_KEY = 1

# Minimum secret length for enforced admission (bytes after UTF-8 encode).
MIN_IDEM_SECRET_BYTES = 32


def _length_prefixed_parts(parts: Sequence[str]) -> str:
    """Length-prefixed canonical tuple for stable hashing of structural keys."""
    encoded: list[str] = []
    for part in parts:
        s = str(part)
        encoded.append(f"{len(s)}:{s}")
    return "|".join(encoded)


def make_provider_logical_call_key(
    *,
    provider_round_index: int,
    assistant_message_index: int,
    provider_tool_call_id: str,
) -> str:
    """Provider Tool Call identity within a Run."""
    if provider_round_index < 0 or assistant_message_index < 0:
        raise ValueError("provider indices must be non-negative")
    if not provider_tool_call_id or not str(provider_tool_call_id).strip():
        raise ValueError("provider_tool_call_id is required")
    material = _length_prefixed_parts(
        (
            "provider",
            str(int(provider_round_index)),
            str(int(assistant_message_index)),
            str(provider_tool_call_id).strip(),
        )
    )
    return sha256_bytes(material.encode("utf-8"))


def make_workflow_logical_call_key(
    *,
    root_continuation_id: str,
    frame_id: UUID | str,
    node_visit_id: str,
    invocation_ordinal: int,
) -> str:
    if invocation_ordinal < 0:
        raise ValueError("invocation_ordinal must be non-negative")
    if not root_continuation_id or not node_visit_id:
        raise ValueError("root_continuation_id and node_visit_id are required")
    material = _length_prefixed_parts(
        (
            "workflow",
            str(root_continuation_id),
            str(frame_id),
            str(node_visit_id),
            str(int(invocation_ordinal)),
        )
    )
    return sha256_bytes(material.encode("utf-8"))


def make_nested_agent_logical_call_key(
    *,
    parent_call_id: UUID | str,
    agent_round_index: int,
    provider_tool_call_id: str,
) -> str:
    if agent_round_index < 0:
        raise ValueError("agent_round_index must be non-negative")
    if not provider_tool_call_id:
        raise ValueError("provider_tool_call_id is required")
    material = _length_prefixed_parts(
        (
            "nested_agent",
            str(parent_call_id),
            str(int(agent_round_index)),
            str(provider_tool_call_id).strip(),
        )
    )
    return sha256_bytes(material.encode("utf-8"))


def require_idempotency_secret(secret: str | bytes | None) -> bytes:
    """Require a strong secret when enforced ledger admission is active."""
    if secret is None:
        raise ValueError("capability_call_idempotency_secret is required")
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if len(raw) < MIN_IDEM_SECRET_BYTES:
        raise ValueError(
            f"capability_call_idempotency_secret must be at least "
            f"{MIN_IDEM_SECRET_BYTES} bytes"
        )
    return raw


def make_server_idempotency_key(
    *,
    secret: str | bytes,
    run_id: UUID | str,
    logical_call_key: str,
    frozen_target_digest: str,
    canonical_input_digest: str,
    runtime_contract_version: int = RUNTIME_CONTRACT_VERSION_FOR_KEY,
) -> str:
    """HMAC server key: never accepted from model input.

    Returns a hex digest (safe to store). Logs must use
    :func:`idempotency_key_fingerprint` only.
    """
    key = require_idempotency_secret(secret)
    for name, digest in (
        ("frozen_target_digest", frozen_target_digest),
        ("canonical_input_digest", canonical_input_digest),
        ("logical_call_key", logical_call_key),
    ):
        if not digest or not isinstance(digest, str):
            raise ValueError(f"{name} is required")
    msg = "|".join(
        (
            str(int(runtime_contract_version)),
            str(run_id),
            logical_call_key,
            frozen_target_digest,
            canonical_input_digest,
        )
    ).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def idempotency_key_fingerprint(idempotency_key: str, *, prefix_len: int = 12) -> str:
    """Non-secret short diagnostic fingerprint for logs/events."""
    if not idempotency_key:
        return ""
    # Hash again so even partial key material is not the raw key.
    return sha256_bytes(idempotency_key.encode("utf-8"))[:prefix_len]


def digest_input_payload(payload: Mapping[str, Any] | JsonValue) -> str:
    """Canonical input digest for identity / idempotency binding."""
    return sha256_canonical_json(payload)  # type: ignore[arg-type]


__all__ = [
    "MIN_IDEM_SECRET_BYTES",
    "RUNTIME_CONTRACT_VERSION_FOR_KEY",
    "digest_input_payload",
    "idempotency_key_fingerprint",
    "make_nested_agent_logical_call_key",
    "make_provider_logical_call_key",
    "make_server_idempotency_key",
    "make_workflow_logical_call_key",
    "require_idempotency_secret",
]
