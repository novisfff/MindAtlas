"""Operator auth configuration, key-ring, token, and same-origin policy tests."""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.common.exceptions import ApiException
from app.config import Settings
from app.operator_auth.origin import require_json_same_origin
from app.operator_auth.tokens import (
    SessionMacKeyRing,
    digest_csrf,
    digest_session,
    digests_equal,
    issue_raw_csrf,
    issue_raw_session_cookie,
    parse_session_cookie,
)


def _key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def test_key_ring_accepts_active_plus_one_previous() -> None:
    ring = SessionMacKeyRing.parse(
        active_key_id="k2",
        encoded_json=json.dumps({"k1": _key(1), "k2": _key(2)}),
    )
    assert ring.active_key_id == "k2"
    assert tuple(ring.keys) == ("k1", "k2")


@pytest.mark.parametrize(
    ("active", "payload"),
    [
        ("missing", {"k1": _key(1)}),
        ("k1", {"k1": base64.b64encode(b"x" * 31).decode("ascii")}),
        ("k1", {"k1": _key(1), "k2": _key(2), "k3": _key(3)}),
    ],
)
def test_key_ring_rejects_invalid_shape(active: str, payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        SessionMacKeyRing.parse(active_key_id=active, encoded_json=json.dumps(payload))


def test_key_ring_repr_hides_secret_material() -> None:
    ring = SessionMacKeyRing.parse(
        active_key_id="k1",
        encoded_json=json.dumps({"k1": _key(7)}),
    )
    text = repr(ring)
    assert _key(7) not in text
    assert base64.b64decode(_key(7)).hex() not in text
    assert "k1" in text


def test_production_rejects_wildcard_or_origin_mismatch() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            MINDATLAS_CANONICAL_ORIGIN="https://atlas.example",
            CORS_ORIGINS="*",
        )


def test_production_rejects_http_canonical_origin() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            MINDATLAS_CANONICAL_ORIGIN="http://atlas.example",
            CORS_ORIGINS="http://atlas.example",
        )


def test_production_accepts_exact_https_origin_in_cors() -> None:
    settings = Settings(
        APP_ENV="production",
        MINDATLAS_CANONICAL_ORIGIN="https://atlas.example",
        CORS_ORIGINS="https://atlas.example",
    )
    assert settings.canonical_origin == "https://atlas.example"


def test_canonical_origin_whitespace_is_normalized() -> None:
    settings = Settings(
        APP_ENV="production",
        MINDATLAS_CANONICAL_ORIGIN="  https://atlas.example  ",
        CORS_ORIGINS="https://atlas.example",
    )
    assert settings.canonical_origin == "https://atlas.example"
    # Stripped stored value must match a real browser Origin header.
    require_json_same_origin(
        _make_request(origin="https://atlas.example"),
        canonical_origin=settings.canonical_origin,
    )


def test_setup_token_min_length_enforced_when_present() -> None:
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="development",
            MINDATLAS_INITIAL_SETUP_TOKEN="too-short",
        )


def test_blank_secret_str_coerced_to_none() -> None:
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_INITIAL_SETUP_TOKEN="",
        MINDATLAS_SESSION_HMAC_KEYS="",
    )
    assert settings.initial_setup_token is None
    assert settings.session_hmac_keys is None


def test_settings_constructible_without_auth_secrets() -> None:
    settings = Settings(APP_ENV="development")
    assert settings.initial_setup_token is None
    assert settings.session_hmac_keys is None
    assert settings.canonical_origin == ""


def test_session_cookie_round_trip_and_digest_domain_separation() -> None:
    session_id = uuid.uuid4()
    cookie, raw = issue_raw_session_cookie(session_id)
    parsed_id, parsed_raw = parse_session_cookie(cookie)
    assert parsed_id == session_id
    assert parsed_raw == raw

    key = bytes([9]) * 32
    session_digest = digest_session(key=key, session_id=session_id, raw=raw)
    csrf_raw = secrets.token_bytes(32)
    csrf_digest = digest_csrf(key=key, session_id=session_id, raw=csrf_raw)
    assert session_digest != csrf_digest
    assert digests_equal(session_digest, session_digest) is True
    assert digests_equal(session_digest, csrf_digest) is False


def test_session_cookie_rejects_unknown_version_and_short_token() -> None:
    session_id = uuid.uuid4()
    with pytest.raises(ValueError):
        parse_session_cookie(f"v0.{session_id.hex}.{'a' * 43}")
    cookie, _ = issue_raw_session_cookie(session_id)
    version, sid, _token = cookie.split(".", 2)
    short = base64.urlsafe_b64encode(b"x" * 16).rstrip(b"=").decode("ascii")
    with pytest.raises(ValueError):
        parse_session_cookie(f"{version}.{sid}.{short}")


def test_issue_raw_csrf_is_independent_32_byte_token() -> None:
    value, raw = issue_raw_csrf()
    assert len(raw) == 32
    assert value  # non-empty urlsafe encoding


def _make_request(
    *,
    content_type: str | None = "application/json",
    origin: str | None = "https://atlas.example",
    sec_fetch_site: str | None = "same-origin",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if content_type is not None:
        headers.append((b"content-type", content_type.encode("ascii")))
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if sec_fetch_site is not None:
        headers.append((b"sec-fetch-site", sec_fetch_site.encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/operator-auth/login",
        "raw_path": b"/api/operator-auth/login",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("atlas.example", 443),
    }
    return Request(scope)


def test_require_json_same_origin_accepts_valid_request() -> None:
    require_json_same_origin(
        _make_request(),
        canonical_origin="https://atlas.example",
    )


def _gate_then_verify(
    request: Request,
    *,
    canonical_origin: str,
    password_verifier: MagicMock,
) -> None:
    """Call the origin gate, then the spy — spy only runs if the gate returns."""
    require_json_same_origin(request, canonical_origin=canonical_origin)
    password_verifier("would-run-if-gate-bypassed")


@pytest.mark.parametrize(
    ("headers", "status", "code", "message"),
    [
        (
            {"content_type": "text/plain", "origin": "https://atlas.example", "sec_fetch_site": "same-origin"},
            415,
            41510,
            "json_content_type_required",
        ),
        (
            {"content_type": "application/json", "origin": None, "sec_fetch_site": "same-origin"},
            403,
            40312,
            "same_origin_required",
        ),
        (
            {
                "content_type": "application/json",
                "origin": "https://atlas.example",
                "sec_fetch_site": "cross-site",
            },
            403,
            40312,
            "same_origin_required",
        ),
        (
            {
                "content_type": "application/json",
                "origin": "https://evil.atlas.example",
                "sec_fetch_site": "same-origin",
            },
            403,
            40312,
            "same_origin_required",
        ),
        (
            {
                "content_type": "application/json",
                "origin": "https://atlas.example:8443",
                "sec_fetch_site": "same-origin",
            },
            403,
            40312,
            "same_origin_required",
        ),
    ],
)
def test_require_json_same_origin_rejects_before_secret_check(
    headers: dict[str, str | None],
    status: int,
    code: int,
    message: str,
) -> None:
    password_verifier = MagicMock()
    with pytest.raises(ApiException) as exc_info:
        _gate_then_verify(
            _make_request(**headers),
            canonical_origin="https://atlas.example",
            password_verifier=password_verifier,
        )
    assert exc_info.value.status_code == status
    assert exc_info.value.code == code
    assert exc_info.value.message == message
    password_verifier.assert_not_called()


def test_gate_then_verify_invokes_spy_when_gate_passes() -> None:
    """Sanity: the helper would call the spy if origin checks succeeded."""
    password_verifier = MagicMock()
    _gate_then_verify(
        _make_request(),
        canonical_origin="https://atlas.example",
        password_verifier=password_verifier,
    )
    password_verifier.assert_called_once_with("would-run-if-gate-bypassed")
