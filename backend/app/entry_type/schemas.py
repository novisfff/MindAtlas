from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class EntryTypeBase(CamelModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=512)
    color: Optional[str] = Field(None, max_length=32)
    icon: Optional[str] = Field(None, max_length=64)
    graph_enabled: bool = True
    ai_enabled: bool = True
    enabled: bool = True


class EntryTypeRequest(EntryTypeBase):
    pass


class EntryTypeUpdateRequest(CamelModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)
    graph_enabled: bool | None = None
    ai_enabled: bool | None = None
    enabled: bool | None = None


class EntryTypeResponse(OrmModel, EntryTypeBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
