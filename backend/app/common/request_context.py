from __future__ import annotations

from contextvars import ContextVar, Token

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_request_locale_var: ContextVar[str | None] = ContextVar("request_locale", default=None)


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
