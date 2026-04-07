"""知识库（LightRAG）相关工具。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar
from uuid import UUID

from langchain_core.tools import tool

from app.assistant.tools._context import get_current_db
from app.common.exceptions import ApiException
from app.entry.models import Entry
from app.lightrag.schemas import LightRagQueryResponse
from app.lightrag.service import LightRagService

T = TypeVar("T")

_ALLOWED_MODES = {"naive", "local", "global", "hybrid", "mix"}
_TITLE_PREFIX = "Title:"


def _run_async(factory: Callable[[], Awaitable[T]]) -> T:
    """Run an async coroutine from a sync tool function with a hard timeout.

    Note: We intentionally avoid trying to reuse/own the LightRAG runtime loop here.
    LightRAG internals are executed via `app.lightrag.service` which routes loop-bound
    operations into its dedicated runtime thread.
    """
    import asyncio
    from app.config import get_settings

    timeout_sec = float(getattr(get_settings(), "lightrag_query_timeout_sec", 60.0) or 60.0) + 10.0

    async def _runner() -> T:
        return await asyncio.wait_for(factory(), timeout=timeout_sec)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_runner())

    # If we're already in an event loop, run the coroutine in a separate thread.
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(_runner()))
        return fut.result(timeout=timeout_sec + 5.0)


def _normalize_mode(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    return m if m in _ALLOWED_MODES else "hybrid"


def _clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        v = int(value)
    except Exception:
        v = default
    return max(min_value, min(max_value, v))


def _clamp_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = default
    return max(min_value, min(max_value, v))


def _truncate(s: str | None, max_len: int) -> str:
    t = (s or "").strip()
    return t[:max_len] if len(t) > max_len else t


def _normalize_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except Exception:
        return None
    if v != v:  # NaN
        return None
    if v == float("inf") or v == float("-inf"):
        return None
    return v


def _parse_entry_title_from_chunk(text: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    if not first_line.lower().startswith(_TITLE_PREFIX.lower()):
        return None
    title = first_line[len(_TITLE_PREFIX) :].strip()
    return title or None


def _build_references(
    items: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    graph_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build numbered reference list for citation.

    Order: entries (1..N) -> attachments -> entities -> relationships
    """
    references: list[dict[str, Any]] = []
    idx = 1

    # 1. Entries
    for item in items:
        references.append({
            "index": idx,
            "type": "entry",
            "entryId": item.get("entryId"),
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "content": item.get("content", ""),
        })
        idx += 1

    # 2. Attachments
    for att in attachments:
        references.append({
            "index": idx,
            "type": "attachment",
            "attachmentId": att.get("attachmentId"),
            "entryId": att.get("entryId"),
            # Frontend citation registry expects `filename`
            "filename": att.get("title") or "",
            "content": att.get("content") or "",
        })
        idx += 1

    # 3. Entities
    for ent in graph_context.get("entities") or []:
        references.append({
            "index": idx,
            "type": "entity",
            "name": ent.get("name", ""),
            "entityType": ent.get("type"),
            "description": ent.get("description"),
            "entryId": ent.get("entryId"),
            "attachmentId": ent.get("attachmentId"),
        })
        idx += 1

    # 4. Relationships
    for rel in graph_context.get("relationships") or []:
        references.append({
            "index": idx,
            "type": "rel",
            "source": rel.get("source", ""),
            "target": rel.get("target", ""),
            "description": rel.get("description"),
            "keywords": rel.get("keywords"),
            "entryId": rel.get("entryId"),
            "attachmentId": rel.get("attachmentId"),
        })
        idx += 1

    return references


@dataclass(frozen=True)
class _KbSearchConfig:
    mode: str
    top_k: int
    chunk_top_k: int
    max_entries: int
    max_chunk_chars: int
    min_score: float
    max_tokens: int


def _resolve_kb_search_config(mode: str | None, top_k: int | None) -> _KbSearchConfig:
    from app.config import get_settings

    settings = get_settings()
    mode_setting = getattr(settings, "assistant_kb_graph_recall_mode", "mix")
    resolved_mode = _normalize_mode(mode if mode is not None else mode_setting)
    resolved_top_k = _clamp_int(
        top_k if top_k is not None else getattr(settings, "assistant_kb_graph_recall_top_k", 10),
        default=10,
        min_value=1,
        max_value=50,
    )
    chunk_top_k = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_chunk_top_k", 20),
        default=max(resolved_top_k, 1),
        min_value=1,
        max_value=50,
    )
    max_entries = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_entries", 10),
        default=10,
        min_value=1,
        max_value=50,
    )
    max_chunk_chars = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_chunk_chars", 600),
        default=600,
        min_value=50,
        max_value=2000,
    )
    min_score = _clamp_float(
        getattr(settings, "assistant_kb_graph_recall_min_score", 0.0),
        default=0.0,
        min_value=0.0,
        max_value=1.0,
    )
    max_tokens = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_tokens", 8),
        default=8,
        min_value=1,
        max_value=64,
    )
    return _KbSearchConfig(
        mode=resolved_mode,
        top_k=resolved_top_k,
        chunk_top_k=chunk_top_k,
        max_entries=max_entries,
        max_chunk_chars=max_chunk_chars,
        min_score=min_score,
        max_tokens=max_tokens,
    )


def _recall_graph_data(query: str, cfg: _KbSearchConfig) -> dict[str, Any]:
    from app.lightrag.service import LightRagService

    try:
        return _run_async(
            lambda: LightRagService().graph_recall_with_context(
                query=query,
                mode=cfg.mode,
                top_k=cfg.top_k,
                chunk_top_k=cfg.chunk_top_k,
                max_tokens=cfg.max_tokens,
            )
        )
    except ApiException as exc:
        if exc.code == 40410:
            raise ValueError("LightRAG is not enabled") from exc
        raise


def _collect_chunk_buckets(
    *,
    chunks: list[Any],
    threshold: float,
    max_chunk_chars: int,
    parse_attachment_id_from_attachment_file_path: Callable[[str], str | None],
    parse_entry_id_from_attachment_file_path: Callable[[str], str | None],
) -> tuple[set[UUID], dict[UUID, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    title_cache: dict[str, UUID] = {}
    title_db = None

    attachment_ids: set[UUID] = set()
    chunks_by_entry: dict[UUID, list[dict[str, Any]]] = {}
    chunks_by_attachment: dict[str, list[dict[str, Any]]] = {}
    for src in chunks:
        if not isinstance(src, dict):
            continue
        doc_id = (src.get("doc_id") or "").strip()
        file_path = (src.get("file_path") or "").strip()
        raw_chunk = src.get("content") or ""
        score = _normalize_score(src.get("score"))

        kind = "entry"
        attachment_id: str | None = None
        if doc_id.startswith("attachment:"):
            kind = "attachment"
            attachment_id = doc_id.split(":", 1)[1].strip() or None
        if kind != "attachment" and "/attachments/" in file_path:
            kind = "attachment"
            attachment_id = parse_attachment_id_from_attachment_file_path(file_path) or None

        entry_uuid: UUID | None = None
        if doc_id:
            try:
                entry_uuid = UUID(doc_id)
            except Exception:
                entry_uuid = None

        if entry_uuid is None and file_path:
            if kind == "attachment":
                parsed_entry_id = parse_entry_id_from_attachment_file_path(file_path)
                if parsed_entry_id:
                    try:
                        entry_uuid = UUID(parsed_entry_id)
                    except Exception:
                        pass
            else:
                try:
                    entry_uuid = UUID(file_path)
                except Exception:
                    entry_uuid = None

        if entry_uuid is None:
            title = _parse_entry_title_from_chunk(raw_chunk)
            if title:
                cached = title_cache.get(title)
                if cached is not None:
                    entry_uuid = cached
                else:
                    if title_db is None:
                        title_db = get_current_db()
                    hit = (
                        title_db.query(Entry)
                        .filter(Entry.title == title)
                        .order_by(Entry.updated_at.desc())
                        .first()
                    )
                    if hit is not None:
                        title_cache[title] = hit.id
                        entry_uuid = hit.id

        if entry_uuid is None and not (kind == "attachment" and attachment_id):
            continue

        effective_score = score if score is not None else 0.0
        if effective_score < threshold:
            continue

        if kind == "attachment" and attachment_id:
            try:
                attachment_ids.add(UUID(attachment_id))
            except Exception:
                pass

        chunk = {
            "kind": kind,
            "docId": doc_id or None,
            "filePath": file_path or None,
            "entryId": str(entry_uuid) if entry_uuid is not None else None,
            "attachmentId": attachment_id,
            "score": score,
            "content": _truncate(raw_chunk, max_chunk_chars),
        }
        if not (chunk.get("content") or "").strip():
            continue

        if entry_uuid is not None:
            chunks_by_entry.setdefault(entry_uuid, []).append(chunk)
        if kind == "attachment" and attachment_id:
            chunks_by_attachment.setdefault(attachment_id, []).append(chunk)

    return attachment_ids, chunks_by_entry, chunks_by_attachment


def _build_graph_context(
    *,
    chunks: list[Any],
    graph_data: dict[str, Any],
    parse_attachment_id_from_attachment_file_path: Callable[[str], str | None],
    parse_entry_id_from_attachment_file_path: Callable[[str], str | None],
) -> dict[str, Any]:
    graph_context: dict[str, Any] = {
        "entities": [],
        "relationships": [],
    }
    attachment_entry_by_file_path: dict[str, str] = {}
    for src in chunks:
        if not isinstance(src, dict):
            continue
        fp = (src.get("file_path") or "").strip()
        aid = (src.get("doc_id") or "").strip()
        if not fp or not aid.startswith("attachment:"):
            continue
        chunk_entry_id = ""
        try:
            parsed_entry_id = parse_entry_id_from_attachment_file_path(fp)
            if parsed_entry_id:
                chunk_entry_id = parsed_entry_id
        except Exception:
            chunk_entry_id = ""
        if chunk_entry_id:
            attachment_entry_by_file_path[fp] = chunk_entry_id

    for ent in graph_data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        entry_id = ent.get("entry_id")
        file_path = (ent.get("file_path") or "").strip()
        attachment_id = ""
        if file_path and "/attachments/" in file_path:
            attachment_id = parse_attachment_id_from_attachment_file_path(file_path) or ""
            if not entry_id:
                entry_id = attachment_entry_by_file_path.get(file_path) or parse_entry_id_from_attachment_file_path(file_path)
        graph_context["entities"].append({
            "name": (ent.get("name") or "").strip(),
            "type": ent.get("type"),
            "description": ent.get("description"),
            "entryId": entry_id,
            "attachmentId": attachment_id or None,
        })
    for rel in graph_data.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        entry_id = rel.get("entry_id")
        file_path = (rel.get("file_path") or "").strip()
        attachment_id = ""
        if file_path and "/attachments/" in file_path:
            attachment_id = parse_attachment_id_from_attachment_file_path(file_path) or ""
            if not entry_id:
                entry_id = attachment_entry_by_file_path.get(file_path) or parse_entry_id_from_attachment_file_path(file_path)
        graph_context["relationships"].append({
            "source": (rel.get("source") or "").strip(),
            "target": (rel.get("target") or "").strip(),
            "description": rel.get("description"),
            "keywords": rel.get("keywords"),
            "entryId": entry_id,
            "attachmentId": attachment_id or None,
        })
    return graph_context


def _rank_entry_ids(chunks_by_entry: dict[UUID, list[dict[str, Any]]]) -> list[UUID]:
    entry_best_score: dict[UUID, float] = {}
    for entry_id, entry_chunks in chunks_by_entry.items():
        best = 0.0
        for chunk in entry_chunks:
            score = chunk.get("score")
            if isinstance(score, (int, float)) and float(score) > best:
                best = float(score)
        entry_best_score[entry_id] = best
    return sorted(entry_best_score.keys(), key=lambda eid: entry_best_score.get(eid, 0.0), reverse=True)


def _load_attachment_metadata(db: Any, attachment_ids: set[UUID]) -> tuple[dict[str, str], dict[str, str]]:
    attachment_name_by_id: dict[str, str] = {}
    attachment_entry_by_id: dict[str, str] = {}
    if not attachment_ids:
        return attachment_name_by_id, attachment_entry_by_id
    try:
        from app.attachment.models import Attachment

        rows = (
            db.query(Attachment.id, Attachment.entry_id, Attachment.original_filename)
            .filter(Attachment.id.in_(list(attachment_ids)))
            .all()
        )
        for aid, entry_id, name in rows:
            attachment_name_by_id[str(aid)] = name or ""
            attachment_entry_by_id[str(aid)] = str(entry_id)
    except Exception:
        attachment_name_by_id = {}
        attachment_entry_by_id = {}
    return attachment_name_by_id, attachment_entry_by_id


def _build_entry_items(
    *,
    selected_entry_ids: list[UUID],
    entry_by_id: dict[UUID, Entry],
    chunks_by_entry: dict[UUID, list[dict[str, Any]]],
    max_chunk_chars: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry_id in selected_entry_ids:
        entry = entry_by_id.get(entry_id)
        if entry is None:
            continue
        entry_chunks = chunks_by_entry.get(entry_id, [])
        ranked_entry_chunks = sorted(
            [c for c in entry_chunks if isinstance(c, dict) and c.get("kind") == "entry"],
            key=lambda x: float(x.get("score") or 0.0),
            reverse=True,
        )
        for chunk in ranked_entry_chunks:
            content = (chunk.get("content") or "").strip()
            if not content:
                continue
            items.append(
                {
                    "entryId": str(entry.id),
                    "title": entry.title or "",
                    "summary": entry.summary or "",
                    "content": content,
                }
            )

        if not ranked_entry_chunks:
            fallback = _truncate(entry.content, max_chunk_chars)
            if fallback:
                items.append(
                    {
                        "entryId": str(entry.id),
                        "title": entry.title or "",
                        "summary": entry.summary or "",
                        "content": fallback,
                    }
                )
    return items


def _build_attachment_items(
    *,
    attachment_name_by_id: dict[str, str],
    attachment_entry_by_id: dict[str, str],
    chunks_by_attachment: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if not attachment_name_by_id:
        return attachments

    def _best_score(chunks: list[dict[str, Any]]) -> float:
        best = 0.0
        for chunk in chunks:
            score = chunk.get("score")
            if isinstance(score, (int, float)) and float(score) > best:
                best = float(score)
        return best

    ranked_attachment_ids = sorted(
        attachment_name_by_id.keys(),
        key=lambda aid: _best_score(chunks_by_attachment.get(aid, [])),
        reverse=True,
    )
    for attachment_id in ranked_attachment_ids:
        attachment_chunks = chunks_by_attachment.get(attachment_id, [])
        ranked_chunks = sorted(attachment_chunks, key=lambda x: float(x.get("score") or 0.0), reverse=True)
        for chunk in ranked_chunks:
            content = (chunk.get("content") or "").strip()
            if not content:
                continue
            attachments.append(
                {
                    "attachmentId": attachment_id,
                    "entryId": attachment_entry_by_id.get(attachment_id) or "",
                    "title": attachment_name_by_id.get(attachment_id) or "",
                    "content": content,
                }
            )
    return attachments


@tool
def kb_search(
    query: str,
    mode: str | None = None,
    top_k: int | None = None,
) -> str:
    """使用 LightRAG 进行知识库检索，返回证据片段与图谱上下文。

    该工具仅负责检索，助手应基于返回内容自行总结与组织输出。
    返回结果包含：按 Entry 聚合的证据片段，以及相关的知识图谱实体与关系。

    Args:
        query: 用户查询文本。
        mode: 可选检索模式（naive/local/global/hybrid/mix），为空时使用系统默认配置。
        top_k: 可选召回条数，为空时使用系统默认配置。

    Returns:
        JSON 字符串（对象）：
          {
            "references": [
              {"index": 1, "type": "entry", "entryId": "...", "title": "...", "summary": "...", "content": "..."},
              {"index": 2, "type": "entry", "entryId": "...", "title": "...", "summary": "...", "content": "..."},
              {"index": 3, "type": "attachment", "attachmentId": "...", "entryId": "...", "filename": "...", "content": "..."},
              {"index": 4, "type": "attachment", "attachmentId": "...", "entryId": "...", "filename": "...", "content": "..."},
              {"index": 5, "type": "entity", "name": "...", "entityType": "...", "description": "...", "entryId": "..."},
              {"index": 6, "type": "rel", "source": "...", "target": "...", "description": "...", "keywords": "...", "entryId": "..."}
            ]
          }
    """

    # 注意:
    # 检索参数通过 Settings（env/.env）配置，不作为工具入参：
    # - ASSISTANT_KB_GRAPH_RECALL_MODE
    # - ASSISTANT_KB_GRAPH_RECALL_TOP_K
    # - ASSISTANT_KB_GRAPH_RECALL_CHUNK_TOP_K
    # - ASSISTANT_KB_GRAPH_RECALL_MAX_ENTRIES
    # - ASSISTANT_KB_GRAPH_RECALL_MAX_CHUNK_CHARS
    # - ASSISTANT_KB_GRAPH_RECALL_MIN_SCORE
    # - ASSISTANT_KB_GRAPH_RECALL_MAX_TOKENS
    
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")

    cfg = _resolve_kb_search_config(mode, top_k)
    graph_data = _recall_graph_data(q, cfg)

    # Extract chunks from graph_data
    chunks = graph_data.get("chunks") or []

    # Import helper once outside the loop for performance
    from app.lightrag.source_ids import (
        parse_attachment_id_from_attachment_file_path,
        parse_entry_id_from_attachment_file_path,
    )

    attachment_ids, chunks_by_entry, chunks_by_attachment = _collect_chunk_buckets(
        chunks=chunks,
        threshold=cfg.min_score,
        max_chunk_chars=cfg.max_chunk_chars,
        parse_attachment_id_from_attachment_file_path=parse_attachment_id_from_attachment_file_path,
        parse_entry_id_from_attachment_file_path=parse_entry_id_from_attachment_file_path,
    )
    ranked_entry_ids = _rank_entry_ids(chunks_by_entry)
    graph_context = _build_graph_context(
        chunks=chunks,
        graph_data=graph_data,
        parse_attachment_id_from_attachment_file_path=parse_attachment_id_from_attachment_file_path,
        parse_entry_id_from_attachment_file_path=parse_entry_id_from_attachment_file_path,
    )

    db = get_current_db()
    selected_entry_ids = ranked_entry_ids[: cfg.max_entries]
    entries = db.query(Entry).filter(Entry.id.in_(list(selected_entry_ids))).all()
    entry_by_id: dict[UUID, Entry] = {e.id: e for e in entries}
    attachment_name_by_id, attachment_entry_by_id = _load_attachment_metadata(db, attachment_ids)
    items = _build_entry_items(
        selected_entry_ids=selected_entry_ids,
        entry_by_id=entry_by_id,
        chunks_by_entry=chunks_by_entry,
        max_chunk_chars=cfg.max_chunk_chars,
    )
    attachments = _build_attachment_items(
        attachment_name_by_id=attachment_name_by_id,
        attachment_entry_by_id=attachment_entry_by_id,
        chunks_by_attachment=chunks_by_attachment,
    )

    # Build numbered references for citation
    references = _build_references(items, attachments, graph_context)

    return json.dumps(
        {
            "mode": cfg.mode,
            "query": q,
            "references": references,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@tool
def kb_relation_recommendations(
    entry_id: str,
    mode: str = "hybrid",
    limit: int = 20,
    min_score: float = 0.1,
    exclude_existing_relations: bool = False,
    include_relation_type: bool = True,
) -> str:
    """使用 LightRAG 为指定记录推荐可能的关联关系。

    Args:
        entry_id: 源记录的 UUID。
        mode: LightRAG 查询模式（naive/local/global/hybrid/mix）。
        limit: 推荐数量上限（1-100）。
        min_score: 最小相似度阈值（0.0-1.0）。
        exclude_existing_relations: 是否过滤已存在关联的记录。
        include_relation_type: 是否通过 LLM 预测关联类型（更慢）。

    Returns:
        JSON 字符串：{"items": [{"targetEntryId": "...", "relationType": "USES", "score": 0.83}]}
    """
    try:
        src_id = UUID((entry_id or "").strip())
    except Exception:
        raise ValueError(f"invalid entry_id: {entry_id}")

    m = _normalize_mode(mode)
    lim = _clamp_int(limit, default=20, min_value=1, max_value=100)
    threshold = _clamp_float(min_score, default=0.1, min_value=0.0, max_value=1.0)

    from app.lightrag.service import LightRagService

    db = get_current_db()
    try:
        resp = _run_async(
            lambda: LightRagService().recommend_entry_relations(
                db=db,
                entry_id=src_id,
                mode=m,
                limit=lim,
                min_score=threshold,
                exclude_existing_relations=exclude_existing_relations,
                include_relation_type=include_relation_type,
            )
        )
    except ApiException as exc:
        if exc.code == 40410:
            raise ValueError("LightRAG is not enabled") from exc
        raise
    return json.dumps(resp.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False, indent=2, default=str)


@tool
def query_knowledge_graph(
    query: str,
    mode: str = "hybrid",
    top_k: int = 5,
) -> str:
    """查询知识图谱并返回综合回答、来源与元信息。

    Args:
        query: 查询问题文本
        mode: LightRAG 查询模式（naive/local/global/hybrid/mix）
        top_k: 召回条数上限（1-50）

    Returns:
        知识图谱结构化查询结果（JSON格式）
    """

    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("query is required")

    result = _run_async(
        lambda: LightRagService().query(
            query=normalized_query,
            mode=_normalize_mode(mode),
            top_k=_clamp_int(top_k, default=5, min_value=1, max_value=50),
        )
    )
    payload = LightRagQueryResponse.model_validate(result).model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
