"""Operator browser auth routes: login, session probe, logout, password, revoke-all."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.config import Settings, get_settings
from app.database import get_db
from app.operator_auth.constants import SESSION_COOKIE_NAME
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.dependencies import (
    CODE_AUTH_UNAVAILABLE,
    CODE_INVALID_CREDENTIALS,
    CODE_LOGIN_LOCKED,
    build_operator_auth_service,
    clear_session_cookies,
    load_session_mac_key_ring,
    request_security_context,
    require_csrf,
    require_operator_principal,
    require_viewer_principal,
    set_session_cookies,
)
from app.operator_auth.origin import require_json_same_origin
from app.operator_auth.password import PasswordPolicyError
from app.operator_auth.schemas import (
    OperatorLoginRequest,
    OperatorPasswordChangeRequest,
    OperatorRevokeAllRequest,
    OperatorSessionResponse,
)
from app.operator_auth.service import AuthRejected, LoginLocked

router = APIRouter(prefix="/api/operator-auth", tags=["operator-auth"])


def _session_payload(
    *,
    authenticated: bool,
    role: str | None = None,
    idle_expires_at=None,
    absolute_expires_at=None,
) -> dict:
    return OperatorSessionResponse(
        authenticated=authenticated,
        role=role,  # type: ignore[arg-type]
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    ).model_dump(by_alias=True)


@router.post("/login", response_model=ApiResponse)
async def login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse | JSONResponse:
    """Exchange the exact Operator password for host-only session cookies.

    Origin / content-type gates run before any secret inspection and before
    Pydantic body parsing so non-JSON clients receive 415, not 422.
    """
    require_json_same_origin(request, canonical_origin=settings.canonical_origin)

    try:
        raw_body = await request.json()
    except Exception as exc:  # noqa: BLE001 - normalize malformed JSON
        raise ApiException(
            status_code=400,
            code=40010,
            message="invalid_json_body",
        ) from exc
    body = OperatorLoginRequest.model_validate(raw_body)

    if load_session_mac_key_ring(settings) is None:
        raise ApiException(
            status_code=503,
            code=CODE_AUTH_UNAVAILABLE,
            message="operator_auth_unavailable",
        )

    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    try:
        issued = service.login(body.password, context)
    except LoginLocked as exc:
        return JSONResponse(
            status_code=429,
            content=ApiResponse.fail(
                code=CODE_LOGIN_LOCKED,
                message="login_locked",
                data={"retryAfterSeconds": exc.retry_after_seconds},
            ).model_dump(),
            headers={
                "Retry-After": str(exc.retry_after_seconds),
                "Cache-Control": "no-store",
            },
        )
    except AuthRejected as exc:
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_CREDENTIALS,
            message="invalid_credentials",
        ) from exc
    except PasswordPolicyError as exc:
        # Login body only carries the existing secret; policy errors are generic.
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_CREDENTIALS,
            message="invalid_credentials",
        ) from exc

    set_session_cookies(
        response,
        session_cookie_value=issued.session_cookie_value,
        csrf_cookie_value=issued.csrf_cookie_value,
        settings=settings,
    )
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(
        _session_payload(
            authenticated=True,
            role=issued.principal.role,
            idle_expires_at=issued.idle_expires_at,
            absolute_expires_at=issued.absolute_expires_at,
        )
    )


@router.get("/session", response_model=ApiResponse)
def get_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    """Optional session probe — never raises 401; clears invalid cookies."""
    value = request.cookies.get(SESSION_COOKIE_NAME)
    if not value:
        response.headers["Cache-Control"] = "no-store"
        return ApiResponse.ok(_session_payload(authenticated=False))

    if load_session_mac_key_ring(settings) is None:
        clear_session_cookies(response, settings=settings)
        response.headers["Cache-Control"] = "no-store"
        return ApiResponse.ok(_session_payload(authenticated=False))

    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    # GET probe: session only — no CSRF rotation on this request.
    resolved = service.resolve_session(value, context)
    if resolved is None:
        clear_session_cookies(response, settings=settings)
        response.headers["Cache-Control"] = "no-store"
        return ApiResponse.ok(_session_payload(authenticated=False))

    if resolved.rotated_cookie is not None:
        session_value, csrf_value = resolved.rotated_cookie
        set_session_cookies(
            response,
            session_cookie_value=session_value,
            csrf_cookie_value=csrf_value,
            settings=settings,
        )

    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(
        _session_payload(
            authenticated=True,
            role=resolved.principal.role,
            idle_expires_at=resolved.idle_expires_at,
            absolute_expires_at=resolved.absolute_expires_at,
        )
    )


@router.post("/logout", response_model=ApiResponse)
def logout(
    request: Request,
    response: Response,
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    """Revoke the current session and expire browser cookies."""
    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    service.revoke_current(principal=principal, context=context)
    clear_session_cookies(response, settings=settings)
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(_session_payload(authenticated=False))


@router.post("/password", response_model=ApiResponse)
def change_password(
    body: OperatorPasswordChangeRequest,
    request: Request,
    response: Response,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    """Change the Operator password, revoke every session, and clear cookies."""
    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    try:
        service.change_password(
            principal=principal,
            current_password=body.current_password,
            new_password=body.new_password,
            context=context,
        )
    except AuthRejected as exc:
        raise ApiException(
            status_code=401,
            code=CODE_INVALID_CREDENTIALS,
            message="invalid_credentials",
        ) from exc
    except PasswordPolicyError as exc:
        raise ApiException(
            status_code=400,
            code=40010,
            message="password_policy_rejected",
        ) from exc

    clear_session_cookies(response, settings=settings)
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(_session_payload(authenticated=False))


@router.post("/sessions/revoke-all", response_model=ApiResponse)
def revoke_all_sessions(
    body: OperatorRevokeAllRequest,
    request: Request,
    response: Response,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    """Revoke every active session for the Operator and clear cookies."""
    service = build_operator_auth_service(db, settings)
    context = request_security_context(request, settings)
    service.revoke_all(
        principal=principal,
        context=context,
        reason=body.reason,
    )
    clear_session_cookies(response, settings=settings)
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(_session_payload(authenticated=False))
