"""Minimal snapshot projection stubs (Task 3 contracts only).

Task 4 owns snapshot builders and field allowlists. This module records the
hard-denied field classes so persistence writers can reject secret-shaped
payloads early.
"""

from __future__ import annotations

from typing import Any, Mapping

# Hard-denied regardless of allowlist (plan §Test Runtime Isolation Contract).
HARD_DENIED_FIELD_MARKERS: frozenset[str] = frozenset(
    {
        "password",
        "api_key",
        "apikey",
        "secret",
        "token",
        "authorization",
        "cookie",
        "credential",
        "encrypted",
        "private_key",
        "presigned",
        "signed_url",
    }
)


def payload_contains_hard_denied_keys(payload: Mapping[str, Any] | None) -> list[str]:
    """Return hard-denied key names found (case-insensitive substring match)."""
    if not payload:
        return []
    hits: list[str] = []
    for key in payload.keys():
        lower = str(key).lower()
        for marker in HARD_DENIED_FIELD_MARKERS:
            if marker in lower:
                hits.append(str(key))
                break
    return hits


def assert_payload_safe(payload: Mapping[str, Any] | None, *, context: str) -> None:
    hits = payload_contains_hard_denied_keys(payload)
    if hits:
        raise ValueError(f"{context} contains hard-denied fields: {hits}")
