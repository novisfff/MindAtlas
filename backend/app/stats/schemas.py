from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import Field

from app.common.schemas import CamelModel
from app.entry.models import TimeMode


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


class HeatmapEntry(CamelModel):
    id: str
    title: str


class HeatmapDay(CamelModel):
    date: date
    count: int
    point_count: int = 0
    range_start_count: int = 0
    range_active_count: int = 0
    entries: list[HeatmapEntry] = Field(default_factory=list)


class HeatmapResponse(CamelModel):
    start_date: date
    end_date: date
    data: list[HeatmapDay] = Field(default_factory=list)


class WeeklyMetrics(CamelModel):
    week_entry_count: int
    active_days: int
    total_entries: int
    total_relations: int
    week_start: date
    week_end: date


class TypeHotness(CamelModel):
    type_id: str
    type_name: str
    type_color: str | None = None
    count: int


class TagHotness(CamelModel):
    tag_id: str
    tag_name: str
    tag_color: str | None = None
    count: int


class HotnessResponse(CamelModel):
    top_types: list[TypeHotness] = Field(default_factory=list)
    top_tags: list[TagHotness] = Field(default_factory=list)
    window_start: date
    window_end: date


class CoverKind(str, Enum):
    POINT = "POINT"
    RANGE_START = "RANGE_START"
    RANGE_SPAN = "RANGE_SPAN"


class DayEntry(CamelModel):
    id: str
    title: str
    time_mode: TimeMode
    time_at: datetime | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    cover_kind: CoverKind
    type_color: str | None = None


class DayEntriesResponse(CamelModel):
    date: date
    entries: list[DayEntry] = Field(default_factory=list)
