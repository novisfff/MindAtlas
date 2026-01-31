from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class WeeklyReportContent(CamelModel):
    summary: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    trends: str | None = None


class WeeklyReportResponse(OrmModel):
    id: UUID
    week_start: date
    week_end: date
    entry_count: int
    content: WeeklyReportContent | None = None
    status: str
    attempts: int
    last_error: str | None = None
    generated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WeeklyReportListResponse(CamelModel):
    items: list[WeeklyReportResponse]
    total: int
    page: int
    size: int


class MonthlyReportContent(CamelModel):
    summary: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    trends: str | None = None


class MonthlyReportResponse(OrmModel):
    id: UUID
    month_start: date
    month_end: date
    entry_count: int
    content: MonthlyReportContent | None = None
    status: str
    attempts: int
    last_error: str | None = None
    generated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MonthlyReportListResponse(CamelModel):
    items: list[MonthlyReportResponse]
    total: int
    page: int
    size: int
