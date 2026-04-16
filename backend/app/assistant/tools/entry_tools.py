"""Entry 相关工具函数"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from langchain_core.tools import tool
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from app.common.exceptions import ApiException
from app.common.color_utils import pick_material_600_color
from app.entry.models import Entry, TimeMode
from app.entry.schemas import EntryRequest, EntrySearchRequest
from app.entry.service import EntryService
from app.entry_type.models import EntryType
from app.lightrag.schemas import LightRagSource
from app.lightrag.service import LightRagService
from app.tag.models import Tag

logger = logging.getLogger(__name__)


def _get_db():
    """获取数据库会话 - 由 agent 注入"""
    from app.assistant.tools._context import get_current_db
    return get_current_db()


def _format_entry_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _run_async(factory):
    """Run an async coroutine from a sync tool function with a hard timeout."""
    from app.config import get_settings

    timeout_sec = float(getattr(get_settings(), "lightrag_query_timeout_sec", 60.0) or 60.0) + 10.0

    async def _runner():
        return await asyncio.wait_for(factory(), timeout=timeout_sec)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_runner())

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(_runner()))
        return fut.result(timeout=timeout_sec + 5.0)


def _serialize_entry_search_item(entry: Entry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "title": entry.title,
        "content": entry.content or "",
        "type": entry.type.name if entry.type else "未知",
        "type_code": entry.type.code if entry.type else "",
        "summary": entry.summary or "",
        "tags": [t.name for t in entry.tags],
        "time_mode": entry.time_mode.value if entry.time_mode else "NONE",
        "time_at": _format_entry_datetime(entry.time_at),
        "time_from": _format_entry_datetime(entry.time_from),
        "time_to": _format_entry_datetime(entry.time_to),
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }


def _truncate_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _normalize_similar_limit(limit: int) -> int:
    try:
        value = int(limit)
    except Exception:
        value = 10
    return max(1, min(value, 20))


def _normalize_similar_query(value: Any) -> str:
    return str(value or "").strip()


def _recall_similar_sources(*, query: str, top_k: int) -> list[LightRagSource]:
    return _run_async(
        lambda: LightRagService().recall_sources(
            query=query,
            mode="hybrid",
            top_k=top_k,
        )
    )


def build_search_similar_entries_payload(*, query: str, limit: int = 10) -> dict[str, Any]:
    db = _get_db()
    cleaned_query = _normalize_similar_query(query)
    resolved_limit = _normalize_similar_limit(limit)
    if not cleaned_query:
        return {
            "status": "ok",
            "message": "查询为空，未执行相似记录召回。",
            "total": 0,
            "items": [],
        }

    top_k = min(max(resolved_limit * 6, 20), 50)
    try:
        sources = _recall_similar_sources(query=cleaned_query, top_k=top_k)
    except ApiException as exc:
        if exc.code == 40410:
            return {
                "status": "unavailable",
                "message": "LightRAG is not enabled.",
                "total": 0,
                "items": [],
            }
        logger.warning("search_similar_entries failed with LightRAG API exception", exc_info=True)
        return {
            "status": "unavailable",
            "message": "LightRAG similarity recall failed.",
            "total": 0,
            "items": [],
        }
    except Exception:
        logger.warning("search_similar_entries failed unexpectedly", exc_info=True)
        return {
            "status": "unavailable",
            "message": "LightRAG similarity recall failed.",
            "total": 0,
            "items": [],
        }

    grouped: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(sources or []):
        if not isinstance(source, LightRagSource):
            continue
        entry_id = str(source.entry_id or "").strip()
        if not entry_id:
            continue
        group = grouped.setdefault(
            entry_id,
            {
                "entry_id": entry_id,
                "first_index": index,
                "entry_hit_count": 0,
                "attachment_hit_count": 0,
                "matched_source_kinds": [],
                "matched_snippets": [],
            },
        )
        kind = str(source.kind or ("attachment" if source.attachment_id else "entry")).strip().lower() or "entry"
        if kind not in group["matched_source_kinds"]:
            group["matched_source_kinds"].append(kind)
        if kind == "attachment":
            group["attachment_hit_count"] += 1
        else:
            group["entry_hit_count"] += 1

        snippet = _truncate_text(source.content, 160)
        if snippet and snippet not in group["matched_snippets"] and len(group["matched_snippets"]) < 2:
            group["matched_snippets"].append(snippet)

    if not grouped:
        return {
            "status": "ok",
            "message": "未召回到相似记录候选。",
            "total": 0,
            "items": [],
        }

    entry_ids: list[UUID] = []
    for entry_id in grouped:
        try:
            entry_ids.append(UUID(entry_id))
        except ValueError:
            continue
    if not entry_ids:
        return {
            "status": "ok",
            "message": "未召回到可解析的记录候选。",
            "total": 0,
            "items": [],
        }

    entries = (
        db.query(Entry)
        .options(joinedload(Entry.type), selectinload(Entry.tags))
        .filter(Entry.id.in_(entry_ids))
        .all()
    )
    entry_by_id = {str(entry.id): entry for entry in entries}

    ordered_groups = sorted(
        (group for group in grouped.values() if group["entry_id"] in entry_by_id),
        key=lambda item: (
            0 if item["entry_hit_count"] > 0 else 1,
            item["first_index"],
            -item["attachment_hit_count"],
        ),
    )

    items: list[dict[str, Any]] = []
    for retrieval_rank, group in enumerate(ordered_groups[:resolved_limit], start=1):
        entry = entry_by_id[group["entry_id"]]
        item = _serialize_entry_search_item(entry)
        item["retrieval_rank"] = retrieval_rank
        item["matched_source_kinds"] = list(group["matched_source_kinds"])
        item["matched_snippets"] = list(group["matched_snippets"])
        items.append(item)

    return {
        "status": "ok",
        "message": "已返回相似记录候选。",
        "total": len(items),
        "items": items,
    }


def _resolve_search_type_id(db, type_code: Optional[str]) -> UUID | None:
    normalized = str(type_code or "").strip()
    if not normalized:
        return None
    row = (
        db.query(EntryType)
        .filter(func.lower(EntryType.code) == normalized.lower())
        .first()
    )
    return row.id if row is not None else None


def _resolve_search_tag_ids(db, tag_names: Optional[list[str]]) -> list[UUID] | None:
    cleaned = [str(tag).strip() for tag in (tag_names or []) if str(tag).strip()]
    if not cleaned:
        return None
    tags = (
        db.query(Tag)
        .filter(func.lower(Tag.name).in_([name.lower() for name in cleaned]))
        .all()
    )
    if not tags:
        return []
    return [tag.id for tag in tags]


def build_search_entries_payload(
    *,
    keyword: Optional[str] = None,
    type_code: Optional[str] = None,
    tag_names: Optional[list[str]] = None,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
    limit: int = 10,
) -> dict[str, Any]:
    db = _get_db()

    if limit < 1:
        limit = 10
    elif limit > 100:
        limit = 100

    tag_ids = _resolve_search_tag_ids(db, tag_names)
    if tag_ids == []:
        return {"total": 0, "items": []}
    type_id = _resolve_search_type_id(db, type_code)
    if type_code and type_id is None:
        return {"total": 0, "items": []}

    result = EntryService(db).search(
        EntrySearchRequest(
            keyword=(keyword or "").strip() or None,
            type_id=type_id,
            tag_ids=tag_ids,
            time_from=_parse_date_yyyy_mm_dd(time_from),
            time_to=_parse_date_yyyy_mm_dd(time_to),
            page=0,
            size=limit,
        )
    )

    return {
        "total": int(result.get("total") or 0),
        "items": [_serialize_entry_search_item(entry) for entry in result.get("content", [])],
    }


def build_entry_detail_payload(entry_id: str) -> dict[str, Any]:
    db = _get_db()
    try:
        uid = UUID(entry_id)
    except ValueError as exc:
        raise ValueError(f"无效的记录ID: {entry_id}") from exc

    entry = db.query(Entry).filter(Entry.id == uid).first()
    if not entry:
        raise ValueError(f"未找到记录: {entry_id}")
    return _serialize_entry_search_item(entry)


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
        匹配结果对象（JSON格式），包含 total 与 items 字段
    """
    result = build_search_entries_payload(
        keyword=keyword,
        type_code=type_code,
        tag_names=tag_names,
        time_from=time_from,
        time_to=time_to,
        limit=limit,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@tool
def search_similar_entries(
    query: str,
    limit: int = 10,
) -> str:
    """通过 LightRAG 向量召回相似记录候选。

    Args:
        query: 用于语义召回的查询短语
        limit: 返回候选记录数量限制，默认10条

    Returns:
        相似记录候选对象（JSON格式），包含 status、message、total 与 items 字段
    """
    result = build_search_similar_entries_payload(
        query=query,
        limit=limit,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


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


def _parse_date_yyyy_mm_dd(date_str: str | None):
    """Parse YYYY-MM-DD text into datetime, returning None on invalid input."""
    if not date_str:
        return None
    from datetime import datetime

    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date_yyyy_mm_dd_strict(date_str: Any, *, field_name: str) -> datetime | None:
    normalized = _normalize_optional_text(date_str)
    if normalized is None:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD 格式的有效日期") from exc


def _serialize_entry_tool_result(entry: Entry) -> str:
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
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _resolve_entry_time_fields(
    *,
    time_mode: Optional[str],
    time_at: Optional[str],
    time_from: Optional[str],
    time_to: Optional[str],
) -> tuple[TimeMode, datetime | None, datetime | None, datetime | None]:
    mode_text = _normalize_optional_text(time_mode)
    has_explicit_mode = mode_text is not None
    if has_explicit_mode:
        normalized_mode = mode_text.upper()
        if normalized_mode not in {"POINT", "RANGE"}:
            raise ValueError(f"无效的 time_mode: {mode_text}")
    else:
        normalized_mode = None

    time_at_text = _normalize_optional_text(time_at)
    time_from_text = _normalize_optional_text(time_from)
    time_to_text = _normalize_optional_text(time_to)

    parsed_time_at = _parse_date_yyyy_mm_dd_strict(time_at_text, field_name="time_at")
    parsed_time_from = _parse_date_yyyy_mm_dd_strict(time_from_text, field_name="time_from")
    parsed_time_to = _parse_date_yyyy_mm_dd_strict(time_to_text, field_name="time_to")

    has_time_at = parsed_time_at is not None
    has_time_from = parsed_time_from is not None
    has_time_to = parsed_time_to is not None

    if has_explicit_mode:
        if normalized_mode == "POINT":
            if not has_time_at:
                raise ValueError("time_mode=POINT 时必须提供合法的 time_at")
            if has_time_from or has_time_to:
                raise ValueError("time_mode=POINT 时不能同时提供 time_from/time_to")
            return TimeMode.POINT, parsed_time_at, None, None

        if has_time_at:
            raise ValueError("time_mode=RANGE 时不能同时提供 time_at")
        if not has_time_from or not has_time_to:
            raise ValueError("time_mode=RANGE 时必须同时提供合法的 time_from 和 time_to")
        if parsed_time_from > parsed_time_to:
            raise ValueError("time_from 必须早于或等于 time_to")
        return TimeMode.RANGE, None, parsed_time_from, parsed_time_to

    if not has_time_at and not has_time_from and not has_time_to:
        today_parsed = _parse_date_yyyy_mm_dd(date.today().isoformat())
        assert today_parsed is not None
        return TimeMode.POINT, today_parsed, None, None

    if has_time_at and not has_time_from and not has_time_to:
        return TimeMode.POINT, parsed_time_at, None, None

    if not has_time_at and has_time_from and has_time_to:
        if parsed_time_from > parsed_time_to:
            raise ValueError("time_from 必须早于或等于 time_to")
        return TimeMode.RANGE, None, parsed_time_from, parsed_time_to

    raise ValueError("时间字段组合无效：请提供单个 time_at，或同时提供 time_from 和 time_to")


def _build_entry_request(
    db,
    *,
    title: Optional[str],
    summary: Optional[str],
    content: Optional[str],
    type_code: Optional[str],
    tags: Optional[list[str]],
    time_mode: Optional[str],
    time_at: Optional[str],
    time_from: Optional[str],
    time_to: Optional[str],
) -> EntryRequest:
    raw = (content or "").strip()
    if not raw:
        raise ValueError("content 不能为空")

    enabled_types = db.query(EntryType).filter(EntryType.enabled.is_(True)).all()
    if not enabled_types:
        raise ValueError("没有可用的记录类型")

    def _pick_default_type(types: list[EntryType]) -> EntryType:
        preferred = ["knowledge", "project", "competition"]
        for code in preferred:
            for item in types:
                if (item.code or "").strip().lower() == code:
                    return item
        return types[0]

    def _clean_title(text: str) -> str:
        value = (text or "").strip()
        value = re.sub(r"^[#>\-\*\s]+", "", value).strip()
        if len(value) > 255:
            value = value[:255].rstrip()
        return value

    def _first_non_empty_line(text: str) -> str:
        for line in (text or "").splitlines():
            value = line.strip()
            if value:
                return value
        return ""

    def _infer_title_from_markdown(md: str) -> str:
        for line in (md or "").splitlines():
            match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
            if match:
                return _clean_title(match.group(1))
        return ""

    default_type = _pick_default_type(enabled_types)
    final_content = _remove_level1_headings(raw).strip()

    title_candidate = _clean_title(title or "") if isinstance(title, str) else ""
    provisional_title = _clean_title(_first_non_empty_line(final_content)) or "未命名记录"
    title_from_md = _infer_title_from_markdown(final_content)
    final_title = title_candidate or title_from_md or provisional_title or "未命名记录"

    summary_candidate = (summary or "").strip() if isinstance(summary, str) else ""
    final_summary = summary_candidate or (final_content or "")[:200] or None

    all_existing_tags = db.query(Tag).all()
    tag_objects: list[Tag] = []
    suggested: list[str] = []
    if isinstance(tags, list):
        suggested = [str(tag).strip() for tag in tags if str(tag).strip()]
    elif isinstance(tags, str) and tags.strip():
        parts = re.split(r"[,\n;，；]+", tags)
        suggested = [part.strip() for part in parts if part and part.strip()]
    if suggested:
        tag_objects = _normalize_tags(db, suggested, all_existing_tags)
        db.flush()

    enabled_type_by_code = {
        ((item.code or "").strip().lower()): item
        for item in enabled_types
        if (item.code or "").strip()
    }
    requested_code = _normalize_optional_text(type_code)
    if requested_code is None:
        chosen_type = default_type
    else:
        chosen_type = enabled_type_by_code.get(requested_code.lower())
        if chosen_type is None:
            raise ValueError(f"无效的 type_code: {requested_code}")

    final_time_mode, final_time_at, final_time_from, final_time_to = _resolve_entry_time_fields(
        time_mode=time_mode,
        time_at=time_at,
        time_from=time_from,
        time_to=time_to,
    )

    return EntryRequest.model_validate(
        {
            "title": final_title,
            "summary": final_summary,
            "content": final_content,
            "typeId": chosen_type.id,
            "tagIds": [tag.id for tag in tag_objects],
            "timeMode": final_time_mode.value,
            "timeAt": final_time_at,
            "timeFrom": final_time_from,
            "timeTo": final_time_to,
        }
    )


@tool
def get_entry_detail(entry_id: str) -> str:
    """获取记录的详细信息。

    Args:
        entry_id: 记录的 UUID

    Returns:
        记录的完整信息，包含内容、标签、关联等
    """
    result = build_entry_detail_payload(entry_id)
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
) -> str:
    """创建新的记录（写入数据库）。

    Args:
        title: 记录标题（可选；为空时会从内容中推断）
        summary: 摘要（可选；为空时会从内容中截取）
        content: 正文内容（必填）
        type_code: 记录类型编码（可选；为空时使用默认类型，非空但无效时报错）
        tags: 标签名称列表（可选；大小写不敏感复用，最多新建 5 个）
        time_mode: 时间模式，"POINT" 或 "RANGE"（可选；为空时按提供的时间字段推断，否则默认今天）
        time_at: 当 time_mode="POINT" 时的日期 (YYYY-MM-DD)
        time_from: 当 time_mode="RANGE" 时的起始日期 (YYYY-MM-DD)
        time_to: 当 time_mode="RANGE" 时的结束日期 (YYYY-MM-DD)

    Returns:
        创建成功的记录信息（JSON格式，包含id、标题、类型等）
    """
    db = _get_db()
    request = _build_entry_request(
        db,
        title=title,
        summary=summary,
        content=content,
        type_code=type_code,
        tags=tags,
        time_mode=time_mode,
        time_at=time_at,
        time_from=time_from,
        time_to=time_to,
    )
    entry = EntryService(db).create(request)
    return _serialize_entry_tool_result(entry)


@tool
def update_entry(
    entry_id: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    content: Optional[str] = None,
    type_code: Optional[str] = None,
    tags: Optional[list[str]] = None,
    time_mode: Optional[str] = None,
    time_at: Optional[str] = None,
    time_from: Optional[str] = None,
    time_to: Optional[str] = None,
) -> str:
    """更新已有记录（写入数据库）。

    Args:
        entry_id: 目标记录 UUID
        title: 记录标题
        summary: 摘要
        content: 正文内容
        type_code: 记录类型编码（为空时使用默认类型，非空但无效时报错）
        tags: 标签名称列表
        time_mode: 时间模式，"POINT" 或 "RANGE"（为空时按提供的时间字段推断，否则默认今天）
        time_at: 当 time_mode="POINT" 时的日期 (YYYY-MM-DD)
        time_from: 当 time_mode="RANGE" 时的起始日期 (YYYY-MM-DD)
        time_to: 当 time_mode="RANGE" 时的结束日期 (YYYY-MM-DD)

    Returns:
        更新后的记录信息（JSON格式，字段结构与 create_entry 保持一致）
    """
    db = _get_db()
    try:
        target_id = UUID(str(entry_id).strip())
    except ValueError as exc:
        raise ValueError(f"无效的记录ID: {entry_id}") from exc

    request = _build_entry_request(
        db,
        title=title,
        summary=summary,
        content=content,
        type_code=type_code,
        tags=tags,
        time_mode=time_mode,
        time_at=time_at,
        time_from=time_from,
        time_to=time_to,
    )
    entry = EntryService(db).update(target_id, request)
    return _serialize_entry_tool_result(entry)
