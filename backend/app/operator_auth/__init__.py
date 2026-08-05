"""Single-operator production control-plane authentication primitives.

Public surface only — no secrets, raw tokens, or internal repositories.
"""

from __future__ import annotations

from app.operator_auth.constants import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_HMAC_LABEL,
    CONTEXT_HMAC_LABEL,
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCK_SECONDS,
    LOGIN_WINDOW_SECONDS,
    OPERATOR_AUTH_CONTRACT_VERSION,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_HMAC_LABEL,
    SESSION_IDLE_SECONDS,
    SETUP_AUTH_SCHEME,
)
from app.operator_auth.contracts import (
    IssuedSession,
    OperatorAuthAvailability,
    OperatorPrincipal,
    OperatorRole,
    PasswordVerification,
    RequestSecurityContext,
    SetupAuthorization,
)
from app.operator_auth.dependencies import (
    build_operator_auth_service,
    clear_session_cookies,
    require_csrf,
    require_operator_principal,
    require_setup_authorization,
    require_viewer_principal,
    request_security_context,
    set_session_cookies,
)
from app.operator_auth.origin import require_json_same_origin
from app.operator_auth.password import PasswordPolicyError, PasswordService
from app.operator_auth.service import (
    AuthRejected,
    CsrfRejected,
    LoginLocked,
    OperatorAuthService,
    SessionRejected,
    SessionResolution,
)
from app.operator_auth.tokens import (
    SessionMacKeyRing,
    digest_context,
    digest_csrf,
    digest_session,
    digests_equal,
    issue_raw_csrf,
    issue_raw_session_cookie,
    parse_csrf_cookie,
    parse_session_cookie,
)

__all__ = [
    "CONTEXT_HMAC_LABEL",
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "CSRF_HMAC_LABEL",
    "AuthRejected",
    "CsrfRejected",
    "IssuedSession",
    "LOGIN_FAILURE_LIMIT",
    "LOGIN_LOCK_SECONDS",
    "LOGIN_WINDOW_SECONDS",
    "LoginLocked",
    "OPERATOR_AUTH_CONTRACT_VERSION",
    "OperatorAuthAvailability",
    "OperatorAuthService",
    "OperatorPrincipal",
    "OperatorRole",
    "PasswordPolicyError",
    "PasswordService",
    "PasswordVerification",
    "RequestSecurityContext",
    "SESSION_ABSOLUTE_SECONDS",
    "SESSION_COOKIE_NAME",
    "SESSION_HMAC_LABEL",
    "SESSION_IDLE_SECONDS",
    "SETUP_AUTH_SCHEME",
    "SessionMacKeyRing",
    "SessionRejected",
    "SessionResolution",
    "SetupAuthorization",
    "build_operator_auth_service",
    "clear_session_cookies",
    "digest_context",
    "digest_csrf",
    "digest_session",
    "digests_equal",
    "issue_raw_csrf",
    "issue_raw_session_cookie",
    "parse_csrf_cookie",
    "parse_session_cookie",
    "require_csrf",
    "require_json_same_origin",
    "require_operator_principal",
    "require_setup_authorization",
    "require_viewer_principal",
    "request_security_context",
    "set_session_cookies",
]
