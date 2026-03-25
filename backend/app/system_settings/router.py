from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db
from app.system_settings.initialization_service import SystemInitializationService
from app.system_settings.runtime_config_service import SystemRuntimeConfigService
from app.system_settings.schemas import (
    InitializeSystemRequest,
    InitializationCompletionResponse,
    InitializationDefaultsResponse,
    InitializationStatusResponse,
    RuntimeConfigResponse,
    RuntimeConfigValidationResponse,
    SystemLocaleResponse,
    SystemLocaleUpdateRequest,
)
from app.system_settings.service import SystemSettingsService

router = APIRouter(prefix="/api/system-settings", tags=["system-settings"])


@router.get("/initialization-status", response_model=ApiResponse)
def get_initialization_status(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemInitializationService(db)
    payload = service.get_initialization_status()
    return ApiResponse.ok(InitializationStatusResponse.model_validate(payload).model_dump(by_alias=True))


@router.get("/initialization-defaults", response_model=ApiResponse)
def get_initialization_defaults(locale: str, db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemInitializationService(db)
    payload = service.get_initialization_defaults(locale=locale)
    return ApiResponse.ok(InitializationDefaultsResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/initialize", response_model=ApiResponse)
def initialize_system(
    request: InitializeSystemRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemInitializationService(db)
    payload = service.initialize_system(request)
    return ApiResponse.ok(InitializationCompletionResponse.model_validate(payload).model_dump(by_alias=True))


@router.get("/runtime-config", response_model=ApiResponse)
def get_runtime_config(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.get_runtime_config_response()
    return ApiResponse.ok(RuntimeConfigResponse.model_validate(payload).model_dump(by_alias=True))


@router.put("/runtime-config/{group_key}", response_model=ApiResponse)
def update_runtime_config(
    group_key: str,
    request: dict[str, Any],
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.update_group(group_key, request)
    return ApiResponse.ok(RuntimeConfigResponse.model_validate(payload).model_dump(by_alias=True))


@router.post("/runtime-config/{group_key}/validate", response_model=ApiResponse)
def validate_runtime_config(
    group_key: str,
    request: dict[str, Any],
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemRuntimeConfigService(db)
    payload = service.validate_group(group_key, request)
    return ApiResponse.ok(RuntimeConfigValidationResponse.model_validate(payload).model_dump(by_alias=True))


@router.get("/locale", response_model=ApiResponse)
def get_system_locale(db: Session = Depends(get_db)) -> ApiResponse:
    service = SystemSettingsService(db)
    locale, persisted = service.resolve_locale_response()
    payload = SystemLocaleResponse(locale=locale, persisted=persisted)
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@router.put("/locale", response_model=ApiResponse)
def update_system_locale(
    request: SystemLocaleUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = SystemSettingsService(db)
    try:
        locale = service.set_locale(request.locale)
    except ValueError as exc:
        raise ApiException(status_code=400, code=40040, message=str(exc)) from exc
    payload = SystemLocaleResponse(locale=locale, persisted=True)
    return ApiResponse.ok(payload.model_dump(by_alias=True))
