"""辅助工具函数"""
from __future__ import annotations

import json

from langchain_core.tools import tool

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
        "code": t.code,
        "name": t.name,
        "color": t.color,
    } for t in types]

    return json.dumps(results, ensure_ascii=False)


@tool
def list_tags() -> str:
    """列出所有标签。

    Returns:
        标签列表，包含名称、颜色
    """
    db = _get_db()
    tags = db.query(Tag).all()

    results = [{
        "name": t.name,
        "color": t.color,
    } for t in tags]

    return json.dumps(results, ensure_ascii=False)
