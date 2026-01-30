from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.entry.models import TimeMode
from app.entry_type.schemas import EntryTypeResponse
from app.common.schemas import CamelModel, OrmModel
from app.tag.schemas import TagResponse


class EntryBase(CamelModel):
    title: str = Field(..., min_length=1, max_length=255)
    summary: Optional[str] = None
    content: Optional[str] = None
    type_id: UUID
    time_mode: TimeMode = Field(default=TimeMode.POINT)
    time_at: Optional[datetime] = Field(default=None)
    time_from: Optional[datetime] = Field(default=None)
    time_to: Optional[datetime] = Field(default=None)


class EntryRequest(EntryBase):
    tag_ids: Optional[List[UUID]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _validate_time_fields(self) -> "EntryRequest":
        if self.time_mode == TimeMode.NONE:
            raise ValueError("time_mode cannot be NONE")

        if self.time_mode == TimeMode.POINT:
            if self.time_at is None:
                raise ValueError("time_at is required when time_mode=POINT")

        if self.time_mode == TimeMode.RANGE:
            if self.time_from is None or self.time_to is None:
                raise ValueError("time_from and time_to are required when time_mode=RANGE")
            if self.time_from > self.time_to:
                raise ValueError("time_from must be <= time_to")

        return self


class EntryResponse(OrmModel, EntryBase):
    id: UUID
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


class EntryTimePatch(CamelModel):
    time_mode: Optional[TimeMode] = None
    time_at: Optional[datetime] = None
    time_from: Optional[datetime] = None
    time_to: Optional[datetime] = None

    @model_validator(mode='after')
    def validate_time_fields(self) -> 'EntryTimePatch':
        has_any = any([
            self.time_mode is not None,
            self.time_at is not None,
            self.time_from is not None,
            self.time_to is not None,
        ])
        if not has_any:
            raise ValueError('At least one time field must be provided')

        if self.time_mode == TimeMode.POINT and self.time_at is None:
            raise ValueError('time_at required when time_mode is POINT')

        if self.time_mode == TimeMode.RANGE:
            if self.time_from is None or self.time_to is None:
                raise ValueError('time_from and time_to required when time_mode is RANGE')
            if self.time_from > self.time_to:
                raise ValueError('time_from must be <= time_to')

        return self
