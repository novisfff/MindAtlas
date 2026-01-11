"""统计分析工具函数"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from langchain_core.tools import tool
from sqlalchemy import func

from app.entry.models import Entry
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

    result = {
        "total_entries": total_entries,
        "total_tags": total_tags,
        "total_types": total_types,
        "entries_by_type": {name: count for name, count in type_stats},
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def get_entries_by_time_range(
    start_date: str,
    end_date: str,
    type_code: Optional[str] = None
) -> str:
    """获取指定时间范围内的记录。

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

    query = db.query(Entry).filter(
        Entry.created_at >= start,
        Entry.created_at <= end
    )

    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)

    entries = query.order_by(Entry.created_at.desc()).all()

    results = [{
        "id": str(e.id),
        "title": e.title,
        "type": e.type.name if e.type else "",
        "created_at": e.created_at.strftime("%Y-%m-%d"),
    } for e in entries]

    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def analyze_activity(period: str = "month") -> str:
    """分析用户的活动情况。

    Args:
        period: 分析周期，可选 week/month/year

    Returns:
        活动分析报告
    """
    db = _get_db()

    days = {"week": 7, "month": 30, "year": 365}.get(period, 30)
    since = datetime.now() - timedelta(days=days)

    count = db.query(func.count(Entry.id)).filter(
        Entry.created_at >= since
    ).scalar() or 0

    result = {
        "period": period,
        "entries_created": count,
        "avg_per_day": round(count / days, 2),
    }
    return json.dumps(result, ensure_ascii=False)
