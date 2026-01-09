from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai_provider.schemas import (
    AiProviderCreateRequest,
    AiProviderResponse,
    AiProviderTestConnectionResponse,
    AiProviderUpdateRequest,
    FetchModelsRequest,
    FetchModelsResponse,
)
from app.ai_provider.service import AiProviderService
from app.common.responses import ApiResponse
from app.database import get_db

router = APIRouter(prefix="/api/ai-providers", tags=["ai-providers"])


@router.get("", response_model=ApiResponse)
def list_ai_providers(db: Session = Depends(get_db)) -> ApiResponse:
    service = AiProviderService(db)
    providers = service.find_all()
    return ApiResponse.ok([
        AiProviderResponse.model_validate(p).model_dump(by_alias=True)
        for p in providers
    ])


@router.get("/{id}", response_model=ApiResponse)
def get_ai_provider(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AiProviderService(db)
    provider = service.find_by_id(id)
    return ApiResponse.ok(
        AiProviderResponse.model_validate(provider).model_dump(by_alias=True)
    )


@router.post("", response_model=ApiResponse)
def create_ai_provider(
    request: AiProviderCreateRequest, db: Session = Depends(get_db)
) -> ApiResponse:
    service = AiProviderService(db)
    provider = service.create(request)
    return ApiResponse.ok(
        AiProviderResponse.model_validate(provider).model_dump(by_alias=True)
    )


@router.put("/{id}", response_model=ApiResponse)
def update_ai_provider(
    id: UUID, request: AiProviderUpdateRequest, db: Session = Depends(get_db)
) -> ApiResponse:
    service = AiProviderService(db)
    provider = service.update(id, request)
    return ApiResponse.ok(
        AiProviderResponse.model_validate(provider).model_dump(by_alias=True)
    )


@router.delete("/{id}", response_model=ApiResponse)
def delete_ai_provider(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AiProviderService(db)
    service.delete(id)
    return ApiResponse.ok(None)


@router.post("/{id}/activate", response_model=ApiResponse)
def activate_ai_provider(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AiProviderService(db)
    provider = service.activate(id)
    return ApiResponse.ok(
        AiProviderResponse.model_validate(provider).model_dump(by_alias=True)
    )


@router.post("/{id}/test-connection", response_model=ApiResponse)
def test_ai_provider_connection(
    id: UUID, db: Session = Depends(get_db)
) -> ApiResponse:
    service = AiProviderService(db)
    result: AiProviderTestConnectionResponse = service.test_connection(id)
    return ApiResponse.ok(result.model_dump(by_alias=True))


@router.post("/fetch-models", response_model=ApiResponse)
def fetch_models(request: FetchModelsRequest) -> ApiResponse:
    result: FetchModelsResponse = AiProviderService.fetch_models(request)
    return ApiResponse.ok(result.model_dump(by_alias=True))


@router.post("/{id}/fetch-models", response_model=ApiResponse)
def fetch_models_by_id(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AiProviderService(db)
    result: FetchModelsResponse = service.fetch_models_by_id(id)
    return ApiResponse.ok(result.model_dump(by_alias=True))
