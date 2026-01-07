from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel
from app.entry.schemas import EntryResponse


class RelationTypeBase(CamelModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    inverse_name: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=512)
    color: Optional[str] = Field(None, max_length=32)
    directed: bool = True
    enabled: bool = True


class RelationTypeRequest(RelationTypeBase):
    pass


class RelationTypeResponse(OrmModel, RelationTypeBase):
    id: UUID
    created_at: datetime
    updated_at: datetime


class RelationBase(CamelModel):
    source_entry_id: UUID
    target_entry_id: UUID
    relation_type_id: UUID
    description: Optional[str] = Field(None, max_length=512)


class RelationRequest(RelationBase):
    pass


class RelationResponse(OrmModel):
    id: UUID
    source_entry: EntryResponse
    target_entry: EntryResponse
    relation_type: RelationTypeResponse
    description: Optional[str] = Field(None, max_length=512)
    created_at: datetime
    updated_at: datetime
