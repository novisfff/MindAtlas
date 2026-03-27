from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.openclaw_integration.schemas import (
    OpenClawCapabilityItemCreateRequest,
    OpenClawCapabilityItemUpdateRequest,
    OpenClawCatalogSourceType,
    OpenClawIntegrationUpdateRequest,
)
from app.openclaw_integration.service import OpenClawIntegrationService

settings_router = APIRouter(prefix="/api/system-settings/openclaw-integration", tags=["openclaw-integration"])
runtime_router = APIRouter(prefix="/api/integrations/openclaw", tags=["openclaw-integration-runtime"])


def _preferred_locale_from_request(request: Request) -> str | None:
    return request.headers.get("x-mindatlas-locale")


@settings_router.get("", response_model=ApiResponse)
def get_openclaw_integration_settings(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.get_settings_response(preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.put("", response_model=ApiResponse)
def update_openclaw_integration_settings(
    request: Request,
    body: OpenClawIntegrationUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.update_settings(body, preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.post("/rotate-secret", response_model=ApiResponse)
def rotate_openclaw_integration_secret(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.rotate_secret(preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.get("/catalog-sources", response_model=ApiResponse)
def list_openclaw_catalog_sources(
    request: Request,
    source_type: OpenClawCatalogSourceType = Query(alias="sourceType"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.list_catalog_sources(source_type, preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.post("/catalog-items", response_model=ApiResponse)
def create_openclaw_catalog_item(
    request: Request,
    body: OpenClawCapabilityItemCreateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.create_catalog_item(body, preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.put("/catalog-items/{item_id}", response_model=ApiResponse)
def update_openclaw_catalog_item(
    item_id: UUID,
    request: Request,
    body: OpenClawCapabilityItemUpdateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.update_catalog_item(item_id, body, preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@settings_router.delete("/catalog-items/{item_id}", response_model=ApiResponse)
def delete_openclaw_catalog_item(
    item_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    service.delete_catalog_item(item_id)
    return ApiResponse.ok({"deleted": True})


@settings_router.post("/reset-system-presets", response_model=ApiResponse)
def reset_openclaw_system_presets(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    payload = service.reset_system_presets(preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@runtime_router.get("/capabilities", response_model=ApiResponse)
def list_openclaw_capabilities(
    request: Request,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    service.authorize_runtime_request(request)
    payload = service.get_runtime_catalog(preferred_locale=_preferred_locale_from_request(request))
    return ApiResponse.ok(payload.model_dump(by_alias=True))


@runtime_router.post("/capabilities/{capability_key}/execute", response_model=ApiResponse)
async def execute_openclaw_capability(
    capability_key: str,
    request: Request,
    body: dict[str, Any],
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = OpenClawIntegrationService(db)
    audit_context = service.authorize_runtime_request(request)
    payload = await service.execute_capability(
        capability_key=capability_key,
        raw_payload=body,
        audit_context=audit_context,
        preferred_locale=_preferred_locale_from_request(request),
    )
    return ApiResponse.ok(payload.model_dump(by_alias=True))
