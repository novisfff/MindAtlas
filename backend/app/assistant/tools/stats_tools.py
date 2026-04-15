"""统计分析工具函数"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from langchain_core.tools import tool
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.entry.models import Entry, TimeMode, entry_tag
from app.entry_type.models import EntryType
from app.relation.models import Relation
from app.stats.service import StatsService
from app.tag.models import Tag

_DEFAULT_TOP_TAGS = 5
_DEFAULT_TOP_TYPES = 5
_PERIOD_DAY_ALIASES: dict[str, tuple[str, int]] = {
    "week": ("7d", 7),
    "7d": ("7d", 7),
    "month": ("30d", 30),
    "30d": ("30d", 30),
    "quarter": ("90d", 90),
    "90d": ("90d", 90),
    "year": ("365d", 365),
    "365d": ("365d", 365),
}


def _get_db() -> Session:
    from app.assistant.tools._context import get_current_db

    return get_current_db()


def _normalize_positive_int(value: int | None, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _normalize_optional_date_arg(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() in {"null", "none"}:
        return None
    return normalized


def _parse_date_window(
    start_date: str | None,
    end_date: str | None,
) -> tuple[date, date, datetime, datetime] | None:
    normalized_start = _normalize_optional_date_arg(start_date)
    normalized_end = _normalize_optional_date_arg(end_date)

    if normalized_start is None and normalized_end is None:
        return None
    if normalized_start is None or normalized_end is None:
        raise ValueError("start_date 和 end_date 必须同时提供")

    try:
        start_d = datetime.strptime(normalized_start, "%Y-%m-%d").date()
        end_d = datetime.strptime(normalized_end, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("日期格式错误，请使用 YYYY-MM-DD")

    if start_d > end_d:
        raise ValueError("start_date 必须小于等于 end_date")

    start_dt = datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_d, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1) - timedelta(
        microseconds=1
    )
    return start_d, end_d, start_dt, end_dt


def _build_business_time_query(
    db: Session,
    *,
    start_dt: datetime,
    end_dt: datetime,
    type_code: str | None = None,
):
    point_clause = (
        (Entry.time_mode == TimeMode.POINT)
        & Entry.time_at.isnot(None)
        & (Entry.time_at >= start_dt)
        & (Entry.time_at <= end_dt)
    )
    range_clause = (
        (Entry.time_mode == TimeMode.RANGE)
        & Entry.time_from.isnot(None)
        & Entry.time_to.isnot(None)
        & (Entry.time_from <= end_dt)
        & (Entry.time_to >= start_dt)
    )

    query = db.query(Entry).filter(or_(point_clause, range_clause))
    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)
    return query


def _serialize_top_types(stats: Any, *, limit: int = _DEFAULT_TOP_TYPES) -> list[dict[str, Any]]:
    items = [
        {
            "type_id": item.type_id,
            "type_name": item.type_name,
            "type_color": item.type_color,
            "count": int(item.count),
        }
        for item in (stats.entries_by_type or [])
        if int(item.count or 0) > 0
    ]
    items.sort(key=lambda item: (-int(item["count"]), str(item["type_name"] or "")))
    return items[:limit]


def _sort_top_types(items: list[dict[str, Any]], *, limit: int = _DEFAULT_TOP_TYPES) -> list[dict[str, Any]]:
    items.sort(key=lambda item: (-int(item["count"]), str(item["type_name"] or "")))
    return items[:limit]


def _load_tag_rows(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    window = _parse_date_window(start_date, end_date)
    query = db.query(
        Tag.id,
        Tag.name,
        Tag.color,
        func.count(entry_tag.c.entry_id),
    )

    window_meta = {
        "window_scope": "all_time",
        "window_start": None,
        "window_end": None,
        "time_basis": "business_time",
    }
    if window is not None:
        start_d, end_d, start_dt, end_dt = window
        business_query = _build_business_time_query(db, start_dt=start_dt, end_dt=end_dt)
        entry_ids_subquery = business_query.with_entities(Entry.id).subquery()
        entry_ids_select = select(entry_ids_subquery.c.id)
        query = query.join(entry_tag, Tag.id == entry_tag.c.tag_id).filter(entry_tag.c.entry_id.in_(entry_ids_select))
        window_meta = {
            "window_scope": "custom_range",
            "window_start": start_d.isoformat(),
            "window_end": end_d.isoformat(),
            "time_basis": "business_time",
        }
    else:
        query = query.outerjoin(entry_tag, Tag.id == entry_tag.c.tag_id)

    tag_stats = (
        query.group_by(Tag.id, Tag.name, Tag.color)
        .order_by(func.count(entry_tag.c.entry_id).desc(), Tag.name.asc())
        .all()
    )

    return (
        [
            {
                "id": str(tag_id),
                "name": name,
                "color": color,
                "entry_count": int(count or 0),
            }
            for tag_id, name, color, count in tag_stats
        ],
        window_meta,
    )


def _build_tag_statistics_payload(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = _DEFAULT_TOP_TAGS,
    include_zero_usage: bool = False,
) -> dict[str, Any]:
    normalized_top_n = _normalize_positive_int(top_n, default=_DEFAULT_TOP_TAGS)
    all_rows, window_meta = _load_tag_rows(db, start_date=start_date, end_date=end_date)
    visible_rows = all_rows if include_zero_usage else [item for item in all_rows if int(item["entry_count"]) > 0]
    top_rows = visible_rows[:normalized_top_n]
    total_tags = len(all_rows) if window_meta["window_scope"] == "custom_range" else (db.query(func.count(Tag.id)).scalar() or 0)

    return {
        "total_tags": total_tags,
        "window_scope": window_meta["window_scope"],
        "window_start": window_meta["window_start"],
        "window_end": window_meta["window_end"],
        "time_basis": window_meta["time_basis"],
        "top_n": normalized_top_n,
        "others_count": max(0, len(visible_rows) - len(top_rows)),
        "tags": top_rows,
    }


def _resolve_period_days(period: str) -> tuple[str, int]:
    normalized = str(period or "month").strip().lower()
    return _PERIOD_DAY_ALIASES.get(normalized, _PERIOD_DAY_ALIASES["month"])


@tool
def get_statistics(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """获取用户数据的整体统计信息。

    Args:
        start_date: 可选的开始日期（YYYY-MM-DD），用于按业务时间限定统计窗口。
        end_date: 可选的结束日期（YYYY-MM-DD），用于按业务时间限定统计窗口。

    Returns:
        统计数据，包含记录总数、关系数、各类型数量和高频标签等
    """
    db = _get_db()
    window = _parse_date_window(start_date, end_date)
    if window is None:
        stats = StatsService(db).get_dashboard_stats()
        tag_rows, _ = _load_tag_rows(db)
        top_types = _serialize_top_types(stats)
        top_tags = [item for item in tag_rows if int(item["entry_count"]) > 0][:_DEFAULT_TOP_TAGS]

        result = {
            "total_entries": int(stats.total_entries or 0),
            "total_tags": int(stats.total_tags or 0),
            "total_relations": int(stats.total_relations or 0),
            "total_types": len(stats.entries_by_type or []),
            "window_scope": "all_time",
            "window_start": None,
            "window_end": None,
            "time_basis": "business_time",
            "entries_by_type": {item.type_name: int(item.count or 0) for item in (stats.entries_by_type or [])},
            "entries_by_tag": {item["name"]: int(item["entry_count"]) for item in tag_rows},
            "top_types": top_types,
            "top_tags": top_tags,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    start_d, end_d, start_dt, end_dt = window
    query = _build_business_time_query(db, start_dt=start_dt, end_dt=end_dt)
    entry_ids_subquery = query.with_entities(Entry.id).subquery()
    entry_ids_select = select(entry_ids_subquery.c.id)

    total_entries = query.count()
    type_rows = (
        query.join(EntryType, Entry.type_id == EntryType.id)
        .with_entities(
            EntryType.id,
            EntryType.name,
            EntryType.color,
            func.count(Entry.id),
        )
        .group_by(EntryType.id, EntryType.name, EntryType.color)
        .all()
    )
    tag_rows, _ = _load_tag_rows(db, start_date=start_d.isoformat(), end_date=end_d.isoformat())
    total_relations = db.query(func.count(Relation.id)).filter(
        or_(
            Relation.source_entry_id.in_(entry_ids_select),
            Relation.target_entry_id.in_(entry_ids_select),
        )
    ).scalar() or 0

    entries_by_type = {name: int(count or 0) for _, name, _, count in type_rows}
    top_types = _sort_top_types(
        [
            {
                "type_id": str(type_id),
                "type_name": name,
                "type_color": color,
                "count": int(count or 0),
            }
            for type_id, name, color, count in type_rows
        ]
    )
    non_zero_tags = [item for item in tag_rows if int(item["entry_count"]) > 0]

    result = {
        "total_entries": int(total_entries or 0),
        "total_tags": len(non_zero_tags),
        "total_relations": int(total_relations or 0),
        "total_types": len(type_rows),
        "window_scope": "custom_range",
        "window_start": start_d.isoformat(),
        "window_end": end_d.isoformat(),
        "time_basis": "business_time",
        "entries_by_type": entries_by_type,
        "entries_by_tag": {item["name"]: int(item["entry_count"]) for item in tag_rows},
        "top_types": top_types,
        "top_tags": non_zero_tags[:_DEFAULT_TOP_TAGS],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def get_entries_by_time_range(
    start_date: str,
    end_date: str,
    type_code: Optional[str] = None
) -> str:
    """获取指定时间范围内的记录（按业务时间，非创建时间）。

    Args:
        start_date: 开始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)
        type_code: 可选的类型筛选

    Returns:
        时间范围内的记录列表
    """
    db = _get_db()

    window = _parse_date_window(start_date, end_date)
    if window is None:
        raise ValueError("start_date 和 end_date 必须同时提供")
    _, _, start_dt, end_dt = window

    query = _build_business_time_query(
        db,
        start_dt=start_dt,
        end_dt=end_dt,
        type_code=type_code,
    )

    entries = query.order_by(
        func.coalesce(Entry.time_at, Entry.time_from).desc(),
        Entry.created_at.desc(),
    ).all()

    results = [{
        "id": str(e.id),
        "title": e.title,
        "type": e.type.name if e.type else "",
        "summary": e.summary or "",
        "time_mode": e.time_mode.value if e.time_mode else "",
        "time_at": e.time_at.strftime("%Y-%m-%d") if e.time_at else None,
        "time_from": e.time_from.strftime("%Y-%m-%d") if e.time_from else None,
        "time_to": e.time_to.strftime("%Y-%m-%d") if e.time_to else None,
    } for e in entries]

    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def analyze_activity(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: str = "month",
) -> str:
    """分析用户在指定时间范围内的记录创建活动（按 created_at）。

    Args:
        start_date: 开始日期 (YYYY-MM-DD)。若为空则按 period 自动取最近一段时间。
        end_date: 结束日期 (YYYY-MM-DD)。若为空则按 period 自动取最近一段时间。
        period: 兼容字段，start_date/end_date 为空时用于决定默认范围（支持 week/month/year/7d/30d/90d）。

    Returns:
        活动分析报告（包含趋势数据）
    """
    db = _get_db()

    now = utcnow()

    # 解析时间范围
    window = _parse_date_window(start_date, end_date)
    if window is not None:
        start_d, end_d, start_dt, end_dt = window
        resolved_period = "custom"
    else:
        resolved_period, days = _resolve_period_days(period)
        end_d = now.date()
        start_d = end_d - timedelta(days=days - 1)
        start_dt = datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end_d, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1) - timedelta(
            microseconds=1
        )
    days = (end_d - start_d).days + 1

    count = db.query(func.count(Entry.id)).filter(
        Entry.created_at >= start_dt,
        Entry.created_at <= end_dt,
    ).scalar() or 0

    # 获取时间范围内的记录创建时间
    created_rows = (
        db.query(Entry.created_at)
        .filter(Entry.created_at >= start_dt, Entry.created_at <= end_dt)
        .all()
    )
    created_ats = [r[0] for r in created_rows if r and r[0]]

    # 生成趋势数据
    trend_unit = "day"
    trend: list[dict] = []

    if days > 120:
        trend_unit = "month"
        month_counts = Counter(dt.strftime("%Y-%m") for dt in created_ats)
        cursor = date(start_d.year, start_d.month, 1)
        end_month = date(end_d.year, end_d.month, 1)
        while cursor <= end_month:
            k = f"{cursor.year:04d}-{cursor.month:02d}"
            trend.append({"date": k, "count": int(month_counts.get(k, 0))})
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
    else:
        day_counts = Counter(dt.astimezone(timezone.utc).date().isoformat() for dt in created_ats)
        for i in range(days):
            d = (start_d + timedelta(days=i)).isoformat()
            trend.append({"date": d, "count": int(day_counts.get(d, 0))})

    peak_bucket = max(trend, key=lambda item: int(item["count"])) if trend else None
    if peak_bucket and int(peak_bucket.get("count", 0)) <= 0:
        peak_bucket = None
    latest_bucket = dict(trend[-1]) if trend else None
    previous_bucket = dict(trend[-2]) if len(trend) > 1 else None
    active_buckets = sum(1 for item in trend if int(item["count"]) > 0)

    result = {
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "days": days,
        "period": period,
        "resolved_period": resolved_period,
        "window_scope": "custom_range" if resolved_period == "custom" else "recent_window",
        "entries_created": count,
        "avg_per_day": round(count / days, 2),
        "trend_unit": trend_unit,
        "trend": trend,
        "metric_scope": "created_at",
        "active_buckets": active_buckets,
        "inactive_buckets": max(0, len(trend) - active_buckets),
        "peak_bucket": peak_bucket,
        "latest_bucket": latest_bucket,
        "previous_bucket": previous_bucket,
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def get_tag_statistics(
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = _DEFAULT_TOP_TAGS,
    include_zero_usage: bool = False,
) -> str:
    """获取标签使用统计。

    Args:
        start_date: 可选的开始日期（YYYY-MM-DD），用于按业务时间限定统计窗口。
        end_date: 可选的结束日期（YYYY-MM-DD），用于按业务时间限定统计窗口。
        top_n: 返回的标签数量上限。
        include_zero_usage: 是否包含未被使用的标签。

    Returns:
        标签使用统计（默认仅返回有使用记录的高频标签）
    """
    db = _get_db()
    result = _build_tag_statistics_payload(
        db,
        start_date=start_date,
        end_date=end_date,
        top_n=top_n,
        include_zero_usage=include_zero_usage,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)
