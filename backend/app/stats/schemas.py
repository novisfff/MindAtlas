from __future__ import annotations

from pydantic import Field

from app.common.schemas import CamelModel


class TypeCount(CamelModel):
    type_id: str
    type_name: str
    type_color: str | None = None
    count: int


class DashboardStats(CamelModel):
    total_entries: int
    total_tags: int
    total_relations: int
    entries_by_type: list[TypeCount] = Field(default_factory=list)
