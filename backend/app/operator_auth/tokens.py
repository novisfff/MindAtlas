"""Session MAC key ring, raw token issuance, and domain-separated HMAC digests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from app.operator_auth.constants import (
    CONTEXT_HMAC_LABEL,
    CSRF_HMAC_LABEL,
    RAW_TOKEN_BYTES,
    SESSION_COOKIE_VERSION,
    SESSION_HMAC_LABEL,
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode_exact(encoded: str, *, expected_len: int) -> bytes:
    """Decode URL-safe Base64 without padding; reject non-canonical encodings."""
    if not encoded or any(c in encoded for c in " \t\r\n"):
        raise ValueError("invalid token encoding")
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    try:
        raw = base64.b64decode(
            (encoded + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:  # noqa: BLE001 - normalize to ValueError
        raise ValueError("invalid token encoding") from exc
    # Reject non-canonical forms by re-encoding.
    if _b64url_encode(raw) != encoded:
        raise ValueError("non-canonical token encoding")
    if len(raw) != expected_len:
        raise ValueError("token must be exactly 32 bytes")
    return raw


@dataclass(frozen=True)
class SessionMacKeyRing:
    """Active session-MAC key plus at most one previous key for rotation."""

    active_key_id: str
    keys: Mapping[str, bytes]

    @property
    def active_key(self) -> bytes:
        return self.keys[self.active_key_id]

    def get(self, key_id: str) -> bytes | None:
        return self.keys.get(key_id)

    def __repr__(self) -> str:
        return (
            f"SessionMacKeyRing(active_key_id={self.active_key_id!r}, "
            f"key_ids={tuple(self.keys.keys())})"
        )

    @classmethod
    def parse(cls, *, active_key_id: str, encoded_json: str) -> SessionMacKeyRing:
        if not active_key_id:
            raise ValueError("active session HMAC key id is required")
        try:
            payload = json.loads(encoded_json)
        except json.JSONDecodeError as exc:
            raise ValueError("session HMAC keys must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("session HMAC keys must be a JSON object")
        if len(payload) not in {1, 2}:
            raise ValueError(
                "session HMAC key ring must contain active plus at most one previous key"
            )
        decoded: dict[str, bytes] = {}
        for key_id, encoded in payload.items():
            if not isinstance(key_id, str) or not key_id:
                raise ValueError("session HMAC key ids must be nonempty strings")
            if not isinstance(encoded, str):
                raise ValueError("session HMAC key values must be Base64 strings")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("session HMAC key is not valid Base64") from exc
            if len(raw) < 32:
                raise ValueError(
                    "each session HMAC key must decode to at least 32 bytes"
                )
            decoded[key_id] = raw
        if active_key_id not in decoded:
            raise ValueError("active session HMAC key id is not present")
        # Preserve insertion order from JSON object for stable key-id tuples.
        return cls(active_key_id=active_key_id, keys=decoded)


def issue_raw_session_cookie(session_id: UUID) -> tuple[str, bytes]:
    raw = secrets.token_bytes(RAW_TOKEN_BYTES)
    encoded = _b64url_encode(raw)
    return f"{SESSION_COOKIE_VERSION}.{session_id.hex}.{encoded}", raw


def issue_raw_csrf() -> tuple[str, bytes]:
    raw = secrets.token_bytes(RAW_TOKEN_BYTES)
    return _b64url_encode(raw), raw


def parse_session_cookie(value: str) -> tuple[UUID, bytes]:
    if not value or value.count(".") != 2:
        raise ValueError("invalid session cookie")
    version, session_hex, token_encoded = value.split(".", 2)
    if version != SESSION_COOKIE_VERSION:
        raise ValueError("unknown session cookie version")
    try:
        session_id = UUID(hex=session_hex)
    except ValueError as exc:
        raise ValueError("invalid session id") from exc
    raw = _b64url_decode_exact(token_encoded, expected_len=RAW_TOKEN_BYTES)
    return session_id, raw


def parse_csrf_cookie(value: str) -> bytes:
    return _b64url_decode_exact(value, expected_len=RAW_TOKEN_BYTES)


def digest_session(*, key: bytes, session_id: UUID, raw: bytes) -> str:
    message = SESSION_HMAC_LABEL + session_id.bytes + raw
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def digest_csrf(*, key: bytes, session_id: UUID, raw: bytes) -> str:
    message = CSRF_HMAC_LABEL + session_id.bytes + raw
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def digest_context(*, key: bytes, material: bytes) -> str:
    message = CONTEXT_HMAC_LABEL + material
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def digests_equal(left: str, right: str) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left, right)
