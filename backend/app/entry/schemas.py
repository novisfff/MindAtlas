from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.entry.models import TimeMode
from app.entry_type.schemas import EntryTypeResponse
from app.common.schemas import CamelModel, OrmModel
from app.tag.schemas import TagResponse


class EntryBase(CamelModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: Optional[str] = None
    type_id: UUID
    time_mode: TimeMode = Field(default=TimeMode.NONE)
    time_at: Optional[datetime] = Field(default=None)
    time_from: Optional[datetime] = Field(default=None)
    time_to: Optional[datetime] = Field(default=None)


class EntryRequest(EntryBase):
    tag_ids: Optional[List[UUID]] = Field(default_factory=list, max_length=50)


class EntryResponse(OrmModel, EntryBase):
    id: UUID
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    type: EntryTypeResponse
    tags: List[TagResponse] = Field(default_factory=list)


class EntrySearchRequest(CamelModel):
    keyword: Optional[str] = None
    type_id: Optional[UUID] = None
    tag_ids: Optional[List[UUID]] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None
    page: int = Field(default=0, ge=0)
    size: int = Field(default=20, ge=1, le=100)
