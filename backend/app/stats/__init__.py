from __future__ import annotations

from pydantic import BaseModel


class DashboardStats(BaseModel):
    total_entries: int
    total_tags: int
    total_entry_types: int
    total_relations: int
    recent_entries_count: int

    class Config:
        from_attributes = True
