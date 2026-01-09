from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class AiProviderBase(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    base_url: str = Field(..., min_length=1, max_length=2048)
    model: str = Field(..., min_length=1, max_length=255)


class AiProviderCreateRequest(AiProviderBase):
    api_key: str = Field(..., min_length=1, max_length=4096)


class AiProviderUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderResponse(OrmModel):
    id: UUID
    name: str
    base_url: str
    model: str
    api_key_hint: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AiProviderTestConnectionResponse(CamelModel):
    ok: bool
    status_code: int | None = None
    message: str | None = None


class FetchModelsRequest(CamelModel):
    base_url: str = Field(..., min_length=1, max_length=2048)
    api_key: str = Field(..., min_length=1, max_length=4096)


class FetchModelsResponse(CamelModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    message: str | None = None
