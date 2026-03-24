from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db
from app.system_settings.schemas import SystemLocaleResponse, SystemLocaleUpdateRequest
from app.system_settings.service import SystemSettingsService

router = APIRouter(prefix="/api/system-settings", tags=["system-settings"])


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
