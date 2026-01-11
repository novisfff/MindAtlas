"""Entry 相关工具函数"""
from __future__ import annotations

import json
from typing import Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy.orm import Session

from app.entry.models import Entry
from app.entry_type.models import EntryType
from app.tag.models import Tag


def _get_db():
    """获取数据库会话 - 由 agent 注入"""
    from app.assistant.tools._context import get_current_db
    return get_current_db()


@tool
def search_entries(
    keyword: Optional[str] = None,
    type_code: Optional[str] = None,
    limit: int = 10
) -> str:
    """搜索用户的记录（Entry）。

    Args:
        keyword: 搜索关键词，匹配标题和内容
        type_code: 记录类型编码，如 knowledge, project, competition
        limit: 返回结果数量限制，默认10条

    Returns:
        匹配的记录列表，包含标题、类型、摘要等信息
    """
    db = _get_db()
    query = db.query(Entry)

    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            Entry.title.ilike(kw) | Entry.content.ilike(kw)
        )

    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)

    entries = query.order_by(Entry.updated_at.desc()).limit(limit).all()

    if not entries:
        return "没有找到匹配的记录。"

    results = []
    for e in entries:
        results.append({
            "id": str(e.id),
            "title": e.title,
            "type": e.type.name if e.type else "未知",
            "summary": (e.summary or e.content or "")[:100],
            "tags": [t.name for t in e.tags],
        })

    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def get_entry_detail(entry_id: str) -> str:
    """获取记录的详细信息。

    Args:
        entry_id: 记录的 UUID

    Returns:
        记录的完整信息，包含内容、标签、关联等
    """
    db = _get_db()
    try:
        uid = UUID(entry_id)
    except ValueError:
        raise ValueError(f"无效的记录ID: {entry_id}")

    entry = db.query(Entry).filter(Entry.id == uid).first()
    if not entry:
        raise ValueError(f"未找到记录: {entry_id}")

    result = {
        "id": str(entry.id),
        "title": entry.title,
        "content": entry.content or "",
        "type": entry.type.name if entry.type else "未知",
        "type_code": entry.type.code if entry.type else "",
        "summary": entry.summary or "",
        "tags": [t.name for t in entry.tags],
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def create_entry(
    title: str,
    content: str,
    type_code: str,
) -> str:
    """创建新的记录。

    Args:
        title: 记录标题
        content: 记录内容（Markdown 格式）
        type_code: 记录类型编码，如 knowledge, project

    Returns:
        创建成功的记录信息
    """
    db = _get_db()

    # 查找类型
    entry_type = db.query(EntryType).filter(
        EntryType.code == type_code
    ).first()
    if not entry_type:
        raise ValueError(f"未找到类型: {type_code}")

    # 创建记录
    entry = Entry(
        title=title,
        content=content,
        type_id=entry_type.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return f"创建成功！记录ID: {entry.id}, 标题: {entry.title}"
