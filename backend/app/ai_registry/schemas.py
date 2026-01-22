from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel

AiModelType = Literal["llm", "embedding"]
AiComponent = Literal["assistant", "lightrag"]


# ==================== Credential ====================

class AiCredentialBase(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    base_url: str = Field(..., min_length=1, max_length=2048)


class AiCredentialCreateRequest(AiCredentialBase):
    api_key: str = Field(..., min_length=1, max_length=4096)


class AiCredentialUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)


class AiCredentialResponse(OrmModel):
    id: UUID
    name: str
    base_url: str
    api_key_hint: str
    created_at: datetime
    updated_at: datetime


class AiCredentialTestConnectionResponse(CamelModel):
    ok: bool
    status_code: int | None = None
    message: str | None = None


# ==================== Model Discovery ====================

class DiscoverModelsByKeyRequest(CamelModel):
    base_url: str = Field(..., min_length=1, max_length=2048)
    api_key: str = Field(..., min_length=1, max_length=4096)


class DiscoveredModel(CamelModel):
    name: str
    suggested_type: AiModelType = "llm"


class DiscoverModelsResponse(CamelModel):
    ok: bool
    models: list[DiscoveredModel] = Field(default_factory=list)
    message: str | None = None


# ==================== Model ====================

class AiModelBase(CamelModel):
    credential_id: UUID
    name: str = Field(..., min_length=1, max_length=255)
    model_type: AiModelType


class AiModelCreateRequest(AiModelBase):
    pass


class AiModelUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    model_type: AiModelType | None = None


class AiModelResponse(OrmModel):
    id: UUID
    credential_id: UUID
    name: str
    model_type: AiModelType
    created_at: datetime
    updated_at: datetime


# ==================== Binding ====================

class ComponentBinding(CamelModel):
    llm_model_id: UUID | None = None
    embedding_model_id: UUID | None = None
    llm_model: AiModelResponse | None = None
    embedding_model: AiModelResponse | None = None


class ModelBindingsResponse(CamelModel):
    assistant: ComponentBinding
    lightrag: ComponentBinding


class UpdateComponentBindingRequest(CamelModel):
    llm_model_id: UUID | None = None
    embedding_model_id: UUID | None = None


class UpdateModelBindingsRequest(CamelModel):
    assistant: UpdateComponentBindingRequest | None = None
    lightrag: UpdateComponentBindingRequest | None = None
