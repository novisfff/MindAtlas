"""Entry 相关工具函数"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional
from uuid import UUID

from langchain_core.tools import tool

from app.common.color_utils import pick_material_600_color
from app.entry.models import Entry, TimeMode
from app.entry_type.models import EntryType
from app.lightrag.models import EntryIndexOutbox
from app.tag.models import Tag

logger = logging.getLogger(__name__)


def _get_db():
    """获取数据库会话 - 由 agent 注入"""
    from app.assistant.tools._context import get_current_db
    return get_current_db()


@tool
def search_entries(
    keyword: Optional[str] = None,
    type_code: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    limit: int = 10
) -> str:
    """搜索用户的记录（Entry）。

    Args:
        keyword: 搜索关键词，匹配标题和内容
        type_code: 记录类型编码，如 knowledge, project, competition
        tag_names: 标签名称列表（匹配任意一个标签）
        time_from: 查询起始日期 (YYYY-MM-DD)，与记录时间做交集判断
        time_to: 查询结束日期 (YYYY-MM-DD)，与记录时间做交集判断
        limit: 返回结果数量限制，默认10条

    Returns:
        匹配的记录列表（JSON数组），包含id、标题、类型、摘要等信息
    """
    db = _get_db()
    query = db.query(Entry)

    # limit 边界校验
    if limit < 1:
        limit = 10
    elif limit > 100:
        limit = 100

    if keyword:
        kw = f"%{keyword}%"
        query = query.filter(
            Entry.title.ilike(kw) | Entry.content.ilike(kw)
        )

    if type_code:
        query = query.join(EntryType).filter(EntryType.code == type_code)

    if tag_names:
        cleaned = [t.strip() for t in tag_names if isinstance(t, str) and t.strip()]
        if cleaned:
            query = query.join(Entry.tags).filter(Tag.name.in_(cleaned)).distinct()

    # Time intersection filter
    def _parse_date(value: Optional[str]):
        if not value or not isinstance(value, str):
            return None
        v = value.strip()
        if not v:
            return None
        from datetime import datetime
        try:
            return datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return None

    q_from = _parse_date(time_from)
    q_to = _parse_date(time_to)
    if q_from or q_to:
        point_clause = (Entry.time_mode == TimeMode.POINT) & Entry.time_at.isnot(None)
        if q_from:
            point_clause = point_clause & (Entry.time_at >= q_from)
        if q_to:
            point_clause = point_clause & (Entry.time_at <= q_to)

        range_clause = (Entry.time_mode == TimeMode.RANGE) & Entry.time_from.isnot(None) & Entry.time_to.isnot(None)
        if q_to:
            range_clause = range_clause & (Entry.time_from <= q_to)
        if q_from:
            range_clause = range_clause & (Entry.time_to >= q_from)

        query = query.filter(point_clause | range_clause)

    entries = query.order_by(Entry.updated_at.desc()).limit(limit).all()

    if not entries:
        return json.dumps([], ensure_ascii=False, indent=2)

    results = []
    for e in entries:
        results.append({
            "id": str(e.id),
            "title": e.title,
            "type": e.type.name if e.type else "未知",
            "summary": e.summary or "",
            "tags": [t.name for t in e.tags],
        })

    return json.dumps(results, ensure_ascii=False, indent=2)


def _remove_level1_headings(md: str) -> str:
    """移除 Markdown 中的一级标题，转换为二级标题"""
    lines = (md or "").splitlines()
    if not lines:
        return md or ""
    out: list[str] = []
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if not in_code:
            line = re.sub(r"^(\s*)#\s+", r"\1## ", line)
        out.append(line)
    return "\n".join(out)


def _normalize_tags(db, tag_names: list[str], existing_tags: list[Tag] | None = None) -> list[Tag]:
    """标签归一化与复用（大小写不敏感匹配）

    Args:
        db: 数据库会话
        tag_names: AI 建议的标签名列表
        existing_tags: 已有标签列表（可选，避免重复查询）
    """
    if existing_tags is None:
        existing_tags = db.query(Tag).all()
    existing_by_lower: dict[str, Tag] = {}
    for t in existing_tags:
        n = (t.name or "").strip()
        if n:
            existing_by_lower[n.lower()] = t

    out: list[Tag] = []
    used_lower: set[str] = set()
    new_created = 0

    for raw in (tag_names or [])[:50]:
        name = (str(raw) if raw is not None else "").strip()
        if not name:
            continue
        name = re.sub(r"^[#]+", "", name).strip()
        if not name:
            continue

        key = name.lower()
        if key in used_lower:
            continue
        used_lower.add(key)

        hit = existing_by_lower.get(key)
        if hit:
            out.append(hit)
            continue

        if new_created >= 5:
            continue

        if len(name) > 128:
            name = name[:128].rstrip()
        if not name:
            continue

        tag = Tag(name=name, color=pick_material_600_color(name))
        db.add(tag)
        existing_by_lower[key] = tag
        out.append(tag)
        new_created += 1

    return out


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
    title: Optional[str] = None,
    summary: Optional[str] = None,
    content: Optional[str] = None,
    type_code: Optional[str] = None,
    tags: Optional[list[str]] = None,
    time_mode: Optional[str] = None,
    time_at: Optional[str] = None,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    raw_content: Optional[str] = None,
) -> str:
    """创建新的记录（写入数据库）。

    Args:
        title: 记录标题（可选；为空时会从内容中推断）
        summary: 摘要（可选；为空时会从内容中截取）
        content: 正文内容（可选；为空时会使用 raw_content）
        type_code: 记录类型编码（可选；为空或无效时使用默认类型）
        tags: 标签名称列表（可选；大小写不敏感复用，最多新建 5 个）
        time_mode: 时间模式，"POINT" 或 "RANGE"（可选；默认 "POINT"）
        time_at: 当 time_mode="POINT" 时的日期 (YYYY-MM-DD)
        time_from: 当 time_mode="RANGE" 时的起始日期 (YYYY-MM-DD)
        time_to: 当 time_mode="RANGE" 时的结束日期 (YYYY-MM-DD)
        raw_content: 原始输入内容（兼容字段；当 content 为空时使用）

    Returns:
        创建成功的记录信息（JSON格式，包含id、标题、类型等）
    """
    db = _get_db()

    raw = ((content or "").strip() or (raw_content or "").strip())
    if not raw:
        raise ValueError("content/raw_content 不能为空")

    # 获取可用类型
    enabled_types = (
        db.query(EntryType)
        .filter(EntryType.enabled.is_(True))
        .all()
    )
    if not enabled_types:
        raise ValueError("没有可用的记录类型")

    # 选择默认类型
    def _pick_default_type(types: list[EntryType]) -> EntryType:
        preferred = ["knowledge", "project", "competition"]
        for code in preferred:
            for t in types:
                if (t.code or "").strip().lower() == code:
                    return t
        return types[0]

    # 清理标题
    def _clean_title(text: str) -> str:
        v = (text or "").strip()
        v = re.sub(r"^[#>\-\*\s]+", "", v).strip()
        if len(v) > 255:
            v = v[:255].rstrip()
        return v

    # 从文本提取首行作为临时标题
    def _first_non_empty_line(text: str) -> str:
        for line in (text or "").splitlines():
            v = line.strip()
            if v:
                return v
        return ""

    # 从 Markdown 提取标题
    def _infer_title_from_markdown(md: str) -> str:
        for line in (md or "").splitlines():
            m = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
            if m:
                return _clean_title(m.group(1))
        return ""

    default_type = _pick_default_type(enabled_types)
    final_content = _remove_level1_headings(raw).strip()

    title_candidate = _clean_title(title or "") if isinstance(title, str) else ""
    provisional_title = _clean_title(_first_non_empty_line(final_content)) or "未命名记录"
    title_from_md = _infer_title_from_markdown(final_content)
    final_title = title_candidate or title_from_md or provisional_title or "未命名记录"

    summary_candidate = (summary or "").strip() if isinstance(summary, str) else ""
    final_summary = summary_candidate or (final_content or "")[:200] or None

    # 预先查询所有标签（复用于 Prompt 和归一化）
    all_existing_tags = db.query(Tag).all()

    enabled_type_by_code = {
        ((t.code or "").strip().lower()): t
        for t in enabled_types
        if (t.code or "").strip()
    }
    req_code = (str(type_code).strip() if type_code is not None else "").strip()
    chosen_type = enabled_type_by_code.get(req_code.lower()) if req_code else None
    if not chosen_type:
        chosen_type = default_type

    # 处理标签（使用归一化函数，复用已查询的标签列表）
    tag_objects: list[Tag] = []
    suggested: list[str] = []
    if isinstance(tags, list):
        suggested = [str(t).strip() for t in tags if str(t).strip()]
    elif isinstance(tags, str) and tags.strip():
        parts = re.split(r"[,\n;，；]+", tags)
        suggested = [p.strip() for p in parts if p and p.strip()]
    if suggested:
        tag_objects = _normalize_tags(db, suggested, all_existing_tags)

    # 处理时间字段
    def _parse_date(date_str: str | None):
        """解析日期字符串为 datetime"""
        if not date_str:
            return None
        from datetime import datetime
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            return None

    today_parsed = _parse_date(date.today().isoformat())
    final_time_mode = TimeMode.POINT
    final_time_at = today_parsed
    final_time_from = None
    final_time_to = None

    mode = (str(time_mode).strip().upper() if isinstance(time_mode, str) and time_mode.strip() else "POINT")
    if mode not in ("POINT", "RANGE"):
        mode = "POINT"

    if mode == "POINT":
        parsed = _parse_date(time_at)
        if parsed:
            final_time_at = parsed
    elif mode == "RANGE":
        from_parsed = _parse_date(time_from)
        to_parsed = _parse_date(time_to)
        if from_parsed and to_parsed and from_parsed <= to_parsed:
            final_time_mode = TimeMode.RANGE
            final_time_at = None
            final_time_from = from_parsed
            final_time_to = to_parsed

    # 创建记录
    entry = Entry(
        title=final_title,
        content=final_content,
        summary=final_summary,
        type_id=chosen_type.id,
        time_mode=final_time_mode,
        time_at=final_time_at,
        time_from=final_time_from,
        time_to=final_time_to,
    )
    entry.tags = tag_objects
    db.add(entry)
    db.flush()  # Ensure entry.id / updated_at are available in the same transaction

    # Add to outbox for LightRAG indexing (same as manual creation)
    db.add(EntryIndexOutbox(
        entry_id=entry.id,
        op="upsert",
        entry_updated_at=entry.updated_at,
        status="pending",
    ))

    db.commit()
    db.refresh(entry)

    result = {
        "id": str(entry.id),
        "title": entry.title,
        "type": entry.type.name if entry.type else "",
        "type_code": entry.type.code if entry.type else "",
        "tags": [t.name for t in entry.tags],
        "time_mode": entry.time_mode.value if entry.time_mode else "NONE",
        "time_at": entry.time_at.strftime("%Y-%m-%d") if entry.time_at else None,
        "time_from": entry.time_from.strftime("%Y-%m-%d") if entry.time_from else None,
        "time_to": entry.time_to.strftime("%Y-%m-%d") if entry.time_to else None,
        "summary": entry.summary or "",
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
