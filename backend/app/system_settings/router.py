from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.config import Settings, get_settings
from app.database import get_db
from app.operator_auth.contracts import (
    OperatorPrincipal,
    RequestSecurityContext,
    SetupAuthorization,
)
from app.operator_auth.dependencies import (
    CODE_AUTH_UNAVAILABLE,
    build_operator_auth_service,
    request_security_context,
    require_csrf,
    require_operator_principal,
    set_session_cookies,
    verify_setup_header,
)
from app.operator_auth.origin import require_json_same_origin
from app.system_settings.initialization_coordinator import InitializationCoordinator
from app.system_settings.initialization_service import SystemInitializationService
from app.system_settings.runtime_config_service import SystemRuntimeConfigService
from app.system_settings.schemas import (
    InitializationCompletionResponse,
    InitializationDefaultsResponse,
    InitializationStatusResponse,
    InitializeSystemRequest,
    RuntimeConfigResponse,
    RuntimeConfigValidationResponse,
    SystemLocaleResponse,
    SystemLocaleUpdateRequest,
)
from app.system_settings.service import SystemSettingsService

_SETTINGS_PREFIX = "/api/system-settings"
_SETTINGS_TAGS = ["system-settings"]

# Split by policy for parent-router mounting in main.py.
public_system_settings_router = APIRouter(prefix=_SETTINGS_PREFIX, tags=_SETTINGS_TAGS)
setup_system_settings_router = APIRouter(prefix=_SETTINGS_PREFIX, tags=_SETTINGS_TAGS)
protected_system_settings_router = APIRouter(
    prefix=_SETTINGS_PREFIX, tags=_SETTINGS_TAGS
)

# Aggregate for tests that still import ``router``.
router = APIRouter(tags=_SETTINGS_TAGS)


@dataclass(frozen=True)
class AuthorizedInitializationRequest:
    """Setup-authorized, same-origin, already-validated initialize payload."""

    payload: InitializeSystemRequest
    setup: SetupAuthorization
    context: RequestSecurityContext


async def parse_authorized_initialization_request(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthorizedInitializationRequest:
    """Gate content-type / Origin / Setup header before body domain validation.

    Order is intentional so an invalid Setup token yields 401 without evaluating
    password policy or other body-domain rules.
    """
    require_json_same_origin(request, canonical_origin=settings.canonical_origin)

    expected = ""
    if settings.initial_setup_token is not None:
        expected = settings.initial_setup_token.get_secret_value() or ""
    setup = verify_setup_header(
        request.headers.get("authorization"),
        configured_token=expected or None,
    )

    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001 - normalize malformed JSON
        raise ApiException(
            status_code=400,
            code=40010,
            message="invalid_json_body",
        ) from exc

    try:
        payload = InitializeSystemRequest.model_validate(raw)
    except ValidationError as exc:
        # Surface as 422 without leaking field values in the top-level message.
        raise ApiException(
            status_code=422,
            code=42210,
            message="initialization_request_invalid",
            details=_safe_validation_details(exc),
        ) from exc

    return AuthorizedInitializationRequest(
        payload=payload,
        setup=setup,
        context=request_security_context(request, settings),
    )


def _safe_validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Strip input values from pydantic errors so secrets never echo."""
    cleaned: list[dict[str, Any]] = []
    for item in exc.errors():
        cleaned.append(
            {
                "type": item.get("type"),
                "loc": list(item.get("loc") or ()),
                "msg": item.get("msg"),
            }
        )
    return cleaned


@public_system_settings_router.get("/initialization-status", response_model=ApiResponse)
def get_initialization_status(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemInitializationService(db)
    payload = service.get_initialization_status()
    return ApiResponse.ok(
        InitializationStatusResponse.model_validate(payload).model_dump(by_alias=True)
    )


@public_system_settings_router.get(
    "/initialization-defaults", response_model=ApiResponse
)
def get_initialization_defaults(locale: str, db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemInitializationService(db)
    payload = service.get_initialization_defaults(locale=locale)
    return ApiResponse.ok(
        InitializationDefaultsResponse.model_validate(payload).model_dump(by_alias=True)
    )


@setup_system_settings_router.post("/initialize", response_model=ApiResponse)
def initialize_system(
    response: Response,
    authorized: AuthorizedInitializationRequest = Depends(
        parse_authorized_initialization_request
    ),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    """Atomic clean-only Operator initialization under Setup authorization.

    Session cookies are issued only after the outer init transaction commits.
    If session issuance fails, the system remains initialized and the Operator
    can use normal login (503 ``operator_auth_unavailable``).
    """
    result = InitializationCoordinator(db).initialize(
        authorized.payload,
        setup_authorization=authorized.setup,
        request_context=authorized.context,
    )

    service = build_operator_auth_service(db, settings)
    try:
        issued = service.issue_initial_session(
            result.operator_account_id, authorized.context
        )
    except Exception as exc:  # noqa: BLE001 - map any session failure to 503
        raise ApiException(
            status_code=503,
            code=CODE_AUTH_UNAVAILABLE,
            message="operator_auth_unavailable",
        ) from exc

    set_session_cookies(
        response,
        session_cookie_value=issued.session_cookie_value,
        csrf_cookie_value=issued.csrf_cookie_value,
        settings=settings,
    )
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse.ok(
        InitializationCompletionResponse.model_validate(
            result.to_response()
        ).model_dump(by_alias=True)
    )


@protected_system_settings_router.get("/runtime-config", response_model=ApiResponse)
def get_runtime_config(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.get_runtime_config_response()
    return ApiResponse.ok(
        RuntimeConfigResponse.model_validate(payload).model_dump(by_alias=True)
    )


@protected_system_settings_router.put(
    "/runtime-config/{group_key}", response_model=ApiResponse
)
def update_runtime_config(
    group_key: str,
    request: dict[str, Any],
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.update_group(group_key, request)
    return ApiResponse.ok(
        RuntimeConfigResponse.model_validate(payload).model_dump(by_alias=True)
    )


@protected_system_settings_router.post(
    "/runtime-config/{group_key}/validate", response_model=ApiResponse
)
def validate_runtime_config(
    group_key: str,
    request: dict[str, Any],
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.validate_group(group_key, request)
    return ApiResponse.ok(
        RuntimeConfigValidationResponse.model_validate(payload).model_dump(by_alias=True)
    )


@protected_system_settings_router.get("/locale", response_model=ApiResponse)
def get_system_locale(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemSettingsService(db)
    locale, persisted = service.resolve_locale_response()
    payload = SystemLocaleResponse(locale=locale, persisted=persisted)
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@protected_system_settings_router.put("/locale", response_model=ApiResponse)
def update_system_locale(
    request: SystemLocaleUpdateRequest,
    db: Session = Depends(get_db),
    # Parent protected_browser_router already enforces Operator + CSRF + audit.
    # Keep explicit deps as defense-in-depth for tests that mount this router alone.
    _principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
) -> ApiResponse:
    service = SystemSettingsService(db)
    try:
        locale = service.set_locale(request.locale)
    except ValueError as exc:
        raise ApiException(status_code=400, code=40040, message=str(exc)) from exc
    payload = SystemLocaleResponse(locale=locale, persisted=True)
    return ApiResponse.ok(payload.model_dump(by_alias=True))


router.include_router(public_system_settings_router)
router.include_router(setup_system_settings_router)
router.include_router(protected_system_settings_router)
