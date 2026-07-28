from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_request_locale_var: ContextVar[str | None] = ContextVar("request_locale", default=None)

# Matches operator_audit_event.request_id String(128) and similar durable columns.
REQUEST_ID_MAX_LENGTH = 128


def normalize_request_id(value: str | None) -> str:
    """Accept a client X-Request-ID only when safe and durable-column sized.

    Client-supplied ids that are non-empty, ASCII printable, and length ≤ 128 are
    preserved (audit correlation). Missing, blank, non-ASCII, non-printable, or
    oversized values are replaced with a server-generated uuid4 hex so flush into
    ``String(128)`` audit/lockout rows cannot fail and erase durability.
    """
    if not isinstance(value, str):
        return uuid.uuid4().hex
    candidate = value.strip()
    if (
        1 <= len(candidate) <= REQUEST_ID_MAX_LENGTH
        and candidate.isascii()
        and all(c.isprintable() for c in candidate)
    ):
        return candidate
    return uuid.uuid4().hex


def set_request_id(request_id: str | None) -> Token[str | None]:
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_locale(locale: str | None) -> Token[str | None]:
    return _request_locale_var.set(locale)


def reset_request_locale(token: Token[str | None]) -> None:
    _request_locale_var.reset(token)


def get_request_locale() -> str | None:
    return _request_locale_var.get()
