"""Report-related assistant tools."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from langchain_core.tools import tool

from app.report.schemas import MonthlyReportResponse, WeeklyReportResponse
from app.report.service import MonthlyReportService, WeeklyReportService


def _get_db():
    from app.assistant.tools._context import get_current_db

    return get_current_db()


def _parse_optional_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid date: {value}") from exc


def build_weekly_report_payload(
    *,
    week_start: str | None = None,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    service = WeeklyReportService(_get_db())
    resolved_week_start = _parse_optional_date(week_start) or service.get_last_monday()
    report = service.get_or_create_for_week(resolved_week_start)
    if force_regenerate or service.should_generate_report(report):
        report = service.generate_report(report)
    return WeeklyReportResponse.model_validate(report).model_dump(mode="json")


def build_monthly_report_payload(
    *,
    month_start: str | None = None,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    service = MonthlyReportService(_get_db())
    resolved_month_start = _parse_optional_date(month_start) or service.get_last_month_start()
    report = service.get_or_create_for_month(resolved_month_start)
    if force_regenerate or service.should_generate_report(report):
        report = service.generate_report(report)
    return MonthlyReportResponse.model_validate(report).model_dump(mode="json")


@tool
def generate_weekly_report(
    week_start: str | None = None,
    force_regenerate: bool = False,
) -> str:
    """生成或返回指定周的周报。

    Args:
        week_start: 周起始日期（YYYY-MM-DD，可选）
        force_regenerate: 是否强制重新生成

    Returns:
        周报结构化结果（JSON格式）
    """

    payload = build_weekly_report_payload(
        week_start=week_start,
        force_regenerate=force_regenerate,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool
def generate_monthly_report(
    month_start: str | None = None,
    force_regenerate: bool = False,
) -> str:
    """生成或返回指定月份的月报。

    Args:
        month_start: 月起始日期（YYYY-MM-DD，可选）
        force_regenerate: 是否强制重新生成

    Returns:
        月报结构化结果（JSON格式）
    """

    payload = build_monthly_report_payload(
        month_start=month_start,
        force_regenerate=force_regenerate,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)
