from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class TagBase(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    color: Optional[str] = Field(None, max_length=32)
    description: Optional[str] = Field(None, max_length=512)


class TagRequest(TagBase):
    pass


class TagResponse(OrmModel, TagBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
