"""辅助工具函数"""
from __future__ import annotations

import json

from langchain_core.tools import tool
from sqlalchemy import func

from app.entry.models import entry_tag
from app.entry_type.models import EntryType
from app.tag.models import Tag


def _get_db():
    from app.assistant.tools._context import get_current_db
    return get_current_db()


@tool
def list_entry_types() -> str:
    """列出所有可用的记录类型。

    Returns:
        类型列表，包含编码、名称、颜色等
    """
    db = _get_db()
    types = db.query(EntryType).filter(
        EntryType.enabled.is_(True)
    ).all()

    results = [{
        "id": str(t.id),
        "code": t.code,
        "type": t.name,
        "name": t.name,
        "color": t.color,
    } for t in types]

    return json.dumps(results, ensure_ascii=False)


@tool
def list_tags() -> str:
    """列出所有标签。

    Returns:
        标签列表，包含名称、颜色、使用次数
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
        .order_by(Tag.name.asc())
        .all()
    )

    results = [{
        "id": str(tag_id),
        "name": name,
        "color": color,
        "entry_count": int(count or 0),
    } for tag_id, name, color, count in tag_stats]

    return json.dumps(results, ensure_ascii=False)
