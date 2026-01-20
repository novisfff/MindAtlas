"""统计分析工具函数"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool
from sqlalchemy import func

from app.common.time import utcnow
from app.entry.models import Entry, TimeMode, entry_tag
from app.entry_type.models import EntryType
from app.tag.models import Tag


def _get_db():
    from app.assistant.tools._context import get_current_db
    return get_current_db()


@tool
def get_statistics() -> str:
    """获取用户数据的整体统计信息。

    Returns:
        统计数据，包含记录总数、各类型数量、标签使用情况等
    """
    db = _get_db()

    total_entries = db.query(func.count(Entry.id)).scalar() or 0
    total_tags = db.query(func.count(Tag.id)).scalar() or 0
    total_types = db.query(func.count(EntryType.id)).scalar() or 0

    # 按类型统计
    type_stats = db.query(
        EntryType.name,
        func.count(Entry.id)
    ).outerjoin(Entry).group_by(EntryType.id).all()

    # 按标签统计
    tag_stats = (
        db.query(
            Tag.name,
            func.count(entry_tag.c.entry_id),
        )
        .outerjoin(entry_tag, Tag.id == entry_tag.c.tag_id)
        .group_by(Tag.id, Tag.name)
        .all()
    )

    result = {
        "total_entries": total_entries,
        "total_tags": total_tags,
        "total_types": total_types,
        "entries_by_type": {name: count for name, count in type_stats},
        "entries_by_tag": {name: count for name, count in tag_stats},
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

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("日期格式错误，请使用 YYYY-MM-DD")

    # 让 end_date 覆盖到当日 23:59:59
    end = end + timedelta(days=1) - timedelta(microseconds=1)

    # 时间交集查询：POINT 在范围内，或 RANGE 与查询范围有交集
    point_clause = (
        (Entry.time_mode == TimeMode.POINT)
        & Entry.time_at.isnot(None)
        & (Entry.time_at >= start)
        & (Entry.time_at <= end)
    )
    range_clause = (
        (Entry.time_mode == TimeMode.RANGE)
        & Entry.time_from.isnot(None)
        & Entry.time_to.isnot(None)
        & (Entry.time_from <= end)
        & (Entry.time_to >= start)
    )

    query = db.query(Entry).filter(point_clause | range_clause)

    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)

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
        period: 兼容字段，start_date/end_date 为空时用于决定默认范围（week/month/year）。

    Returns:
        活动分析报告（包含趋势数据）
    """
    db = _get_db()

    now = utcnow()

    # 解析时间范围
    if start_date and end_date:
        try:
            start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("日期格式错误，请使用 YYYY-MM-DD")
        if start_d > end_d:
            raise ValueError("start_date 必须小于等于 end_date")
    else:
        days = {"week": 7, "month": 30, "year": 365}.get(period, 30)
        end_d = now.date()
        start_d = end_d - timedelta(days=days - 1)

    start_dt = datetime.combine(start_d, datetime.min.time(), tzinfo=timezone.utc)
    # 让 end_date 覆盖到当日 23:59:59.999999
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
        day_counts = Counter(dt.date().isoformat() for dt in created_ats)
        for i in range(days):
            d = (start_d + timedelta(days=i)).isoformat()
            trend.append({"date": d, "count": int(day_counts.get(d, 0))})

    result = {
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "days": days,
        "period": period,
        "entries_created": count,
        "avg_per_day": round(count / days, 2),
        "trend_unit": trend_unit,
        "trend": trend,
    }
    return json.dumps(result, ensure_ascii=False)


@tool
def get_tag_statistics() -> str:
    """获取标签使用统计。

    Returns:
        标签使用统计（包含每个标签的记录数量，按使用次数降序排列）
    """
    db = _get_db()

    tag_stats = (
        db.query(
            Tag.id,
            Tag.name,
            Tag.color,
            func.count(entry_tag.c.entry_id),
        )
        .outerjoin(entry_tag, Tag.id == entry_tag.c.tag_id)
        .group_by(Tag.id, Tag.name, Tag.color)
        .order_by(func.count(entry_tag.c.entry_id).desc(), Tag.name.asc())
        .all()
    )

    tags = [{
        "id": str(tag_id),
        "name": name,
        "color": color,
        "entry_count": int(count or 0),
    } for tag_id, name, color, count in tag_stats]

    result = {
        "total_tags": db.query(func.count(Tag.id)).scalar() or 0,
        "tags": tags,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
