"""Canonical FastAPI dependencies for operator browser sessions.

The only production HTTP constructor of ``OperatorPrincipal`` is successful
validation of a durable password session cookie. Caller-supplied identity or
role headers are never read.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, Response
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.common.exceptions import ApiException
from app.config import Settings, get_settings
from app.database import get_db
from app.operator_auth.constants import (
    CONTEXT_HMAC_LABEL,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    SETUP_AUTH_SCHEME,
)
from app.operator_auth.contracts import (
    OperatorPrincipal,
    RequestSecurityContext,
    SetupAuthorization,
)
from app.operator_auth.service import (
    CsrfRejected,
    OperatorAuthService,
    SessionResolution,
)
from app.operator_auth.tokens import SessionMacKeyRing, digest_context


# Stable API error codes for the HTTP boundary.
CODE_INVALID_SESSION = 40110
CODE_INVALID_CREDENTIALS = 40111
CODE_INVALID_SETUP = 40112
CODE_OPERATOR_ROLE_REQUIRED = 40311
CODE_SAME_ORIGIN_REQUIRED = 40312
CODE_CSRF_REJECTED = 40313
CODE_LOGIN_LOCKED = 42910
CODE_AUTH_UNAVAILABLE = 50310

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class CookiePolicy:
    """Host-only cookie attribute policy derived from settings + origin scheme."""

    secure: bool


def load_session_mac_key_ring(settings: Settings) -> SessionMacKeyRing | None:
    """Parse the configured session-MAC key ring, or ``None`` when unavailable."""
    if settings.session_hmac_keys is None:
        return None
    active_id = (settings.session_hmac_active_key_id or "").strip()
    encoded = (settings.session_hmac_keys.get_secret_value() or "").strip()
    if not active_id or not encoded:
        return None
    try:
        return SessionMacKeyRing.parse(active_key_id=active_id, encoded_json=encoded)
    except ValueError:
        return None


def build_operator_auth_service(
    db: Session,
    settings: Settings,
) -> OperatorAuthService:
    """Construct the auth service bound to this request's DB session + key ring."""
    return OperatorAuthService(db, key_ring=load_session_mac_key_ring(settings))


def resolve_cookie_policy(settings: Settings) -> CookiePolicy:
    """Secure cookies unless explicitly validated local-development HTTP mode."""
    origin = (settings.canonical_origin or "").strip()
    if origin.startswith("https://"):
        return CookiePolicy(secure=True)
    if settings.app_env in {"production", "staging"}:
        # Production/staging must not emit non-Secure auth cookies.
        return CookiePolicy(secure=True)
    if origin.startswith("http://") and settings.app_env in {
        "development",
        "test",
        "testing",
        "local",
    }:
        host = (urlparse(origin).hostname or "").lower()
        if host in _LOCAL_HTTP_HOSTS:
            return CookiePolicy(secure=False)
    # Prefer Secure when the deployment posture is ambiguous.
    return CookiePolicy(secure=True)


def set_session_cookies(
    response: Response,
    *,
    session_cookie_value: str,
    csrf_cookie_value: str,
    settings: Settings,
) -> None:
    """Write the host-only session (HttpOnly) and CSRF (readable) cookies."""
    policy = resolve_cookie_policy(settings)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_cookie_value,
        httponly=True,
        secure=policy.secure,
        samesite="strict",
        path="/",
        max_age=None,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_cookie_value,
        httponly=False,
        secure=policy.secure,
        samesite="strict",
        path="/",
        max_age=None,
    )


def clear_session_cookies(response: Response, *, settings: Settings) -> None:
    """Expire both auth cookies with attributes matching the write path."""
    policy = resolve_cookie_policy(settings)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        httponly=True,
        secure=policy.secure,
        samesite="strict",
        path="/",
        max_age=0,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        "",
        httponly=False,
        secure=policy.secure,
        samesite="strict",
        path="/",
        max_age=0,
    )


def _context_material_digest(*, key: bytes | None, material: bytes) -> str:
    if key is not None:
        return digest_context(key=key, material=material)
    # No deployment key: still never persist raw IP/UA — domain-separated SHA-256.
    return hashlib.sha256(CONTEXT_HMAC_LABEL + material).hexdigest()


def request_security_context(
    request: Request,
    settings: Settings,
) -> RequestSecurityContext:
    """Build safe request digests; never trust X-Forwarded-For or log raw IP/UA.

    Uses the server-generated request id (middleware), raw User-Agent, and
    ``request.client.host`` only as HMAC input material.
    """
    request_id = getattr(request.state, "request_id", None)
    if not request_id:
        request_id = request.headers.get("x-request-id") or ""
    if not isinstance(request_id, str):
        request_id = str(request_id)

    user_agent = request.headers.get("user-agent") or ""
    network = ""
    if request.client is not None and request.client.host:
        network = request.client.host

    ring = load_session_mac_key_ring(settings)
    key = ring.active_key if ring is not None else None

    return RequestSecurityContext(
        request_id=request_id,
        request_digest=_context_material_digest(
            key=key, material=request_id.encode("utf-8")
        ),
        user_agent_digest=_context_material_digest(
            key=key, material=user_agent.encode("utf-8", errors="replace")
        ),
        network_digest=_context_material_digest(
            key=key, material=network.encode("utf-8")
        ),
    )


def require_viewer_principal(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OperatorPrincipal:
    """Resolve the browser session cookie into an ``OperatorPrincipal``.

    On unsafe methods the CSRF cookie is supplied so previous-key sessions can
    rotate; when rotation occurs both cookies are re-emitted with the same raws.
    """
    value = request.cookies.get(SESSION_COOKIE_NAME)
    csrf_cookie_value: str | None = None
    if request.method.upper() in _UNSAFE_METHODS:
        csrf_cookie_value = request.cookies.get(CSRF_COOKIE_NAME)

    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    resolved = service.resolve_session(
        value,
        context,
        csrf_cookie_value=csrf_cookie_value,
    )
    if resolved is None:
        # Best-effort cleanup of a present-but-invalid browser cookie pair.
        # Flag request.state so api_exception_handler can re-apply clears onto the
        # fresh JSONResponse (dependency Response Set-Cookie headers are dropped).
        if value:
            request.state.operator_auth_clear_cookies = True
            clear_session_cookies(response, settings=settings)
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_SESSION,
            message="invalid_session",
        )

    request.state.operator_session_resolution = resolved
    if resolved.rotated_cookie is not None:
        session_value, csrf_value = resolved.rotated_cookie
        set_session_cookies(
            response,
            session_cookie_value=session_value,
            csrf_cookie_value=csrf_value,
            settings=settings,
        )
    return resolved.principal


def require_operator_principal(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
) -> OperatorPrincipal:
    """Require the resolved principal to carry the ``operator`` role."""
    if principal.role != "operator":
        raise ApiException(
            status_code=403,
            code=CODE_OPERATOR_ROLE_REQUIRED,
            message="operator_role_required",
        )
    return principal


def require_csrf(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    principal: OperatorPrincipal = Depends(require_viewer_principal),
) -> None:
    """Validate Origin / Sec-Fetch-Site and the double-submit CSRF pair.

    Consumes the session resolution cached by ``require_viewer_principal``.
    Does not read ``X-MindAtlas-Operator-Id`` or ``X-MindAtlas-Operator-Role``.
    """
    del principal  # presence ensures session resolution ran first
    origin = request.headers.get("origin", "")
    canonical = settings.canonical_origin or ""
    if not canonical or not secrets.compare_digest(origin, canonical):
        raise ApiException(
            status_code=403,
            code=CODE_SAME_ORIGIN_REQUIRED,
            message="same_origin_required",
        )
    if request.headers.get("sec-fetch-site", "").lower() != "same-origin":
        raise ApiException(
            status_code=403,
            code=CODE_SAME_ORIGIN_REQUIRED,
            message="same_origin_required",
        )

    resolution = getattr(request.state, "operator_session_resolution", None)
    if not isinstance(resolution, SessionResolution):
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_SESSION,
            message="invalid_session",
        )

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    try:
        build_operator_auth_service(db, settings).verify_csrf(
            resolution=resolution,
            csrf_cookie_value=csrf_cookie,
            csrf_header_value=csrf_header,
        )
    except CsrfRejected as exc:
        raise ApiException(
            status_code=403,
            code=CODE_CSRF_REJECTED,
            message="csrf_rejected",
        ) from exc


def require_setup_authorization(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SetupAuthorization:
    """Validate ``Authorization: Setup <token>`` without echoing the secret.

    The Setup Token is never a Principal and never appears in responses.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, remainder = header.partition(" ")
    provided = remainder.strip() if scheme.lower() == SETUP_AUTH_SCHEME.lower() else ""
    expected = ""
    if settings.initial_setup_token is not None:
        expected = settings.initial_setup_token.get_secret_value() or ""

    # Constant-time compare only when both sides are non-empty and equal length
    # is not guaranteed — digests avoid length oracle on the raw token.
    if not expected or not provided:
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_SETUP,
            message="invalid_setup_authorization",
        )
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    provided_digest = hashlib.sha256(provided.encode("utf-8")).digest()
    if not secrets.compare_digest(expected_digest, provided_digest):
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_SETUP,
            message="invalid_setup_authorization",
        )
    return SetupAuthorization(validated=True)
