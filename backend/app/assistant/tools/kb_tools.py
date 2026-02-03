"""知识库（LightRAG）相关工具。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, TypeVar
from uuid import UUID

from langchain_core.tools import tool

from app.assistant.tools._context import get_current_db
from app.common.exceptions import ApiException
from app.entry.models import Entry

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

    timeout_sec = float(getattr(get_settings(), "lightrag_query_timeout_sec", 30.0) or 30.0) + 10.0

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
    graph_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build numbered reference list for citation.

    Order: entries (1..N) -> entities (N+1..) -> relationships (...)
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
        })
        idx += 1

    # 2. Entities
    for ent in graph_context.get("entities") or []:
        references.append({
            "index": idx,
            "type": "entity",
            "name": ent.get("name", ""),
            "entityType": ent.get("type"),
            "description": ent.get("description"),
        })
        idx += 1

    # 3. Relationships
    for rel in graph_context.get("relationships") or []:
        references.append({
            "index": idx,
            "type": "rel",
            "source": rel.get("source", ""),
            "target": rel.get("target", ""),
            "description": rel.get("description"),
            "keywords": rel.get("keywords"),
        })
        idx += 1

    return references


@tool
def kb_search(
    query: str,
) -> str:
    """使用 LightRAG 进行知识库检索，返回证据片段与图谱上下文。

    该工具仅负责检索，助手应基于返回内容自行总结与组织输出。
    返回结果包含：按 Entry 聚合的证据片段，以及相关的知识图谱实体与关系。

    注意:
        检索参数通过 Settings（env/.env）配置，不作为工具入参：
        - ASSISTANT_KB_GRAPH_RECALL_MODE
        - ASSISTANT_KB_GRAPH_RECALL_TOP_K
        - ASSISTANT_KB_GRAPH_RECALL_CHUNK_TOP_K
        - ASSISTANT_KB_GRAPH_RECALL_MAX_ENTRIES
        - ASSISTANT_KB_GRAPH_RECALL_CHUNKS_PER_ENTRY
        - ASSISTANT_KB_GRAPH_RECALL_MAX_CHUNK_CHARS
        - ASSISTANT_KB_GRAPH_RECALL_MIN_SCORE
        - ASSISTANT_KB_GRAPH_RECALL_MAX_TOKENS

    Args:
        query: 用户查询文本。

    Returns:
        JSON 字符串（对象）：
          {
            "items": [
              {
                "entryId": "...",
                "title": "...",
                "summary": "...",
                "content": "..."
              }
            ],
            "graphContext": {
              "entities": [{"name": "...", "type": "...", "description": "...", "entryId": "..."}],
              "relationships": [{"source": "...", "target": "...", "description": "...", "keywords": "...", "entryId": "..."}]
            }
          }
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")

    from app.config import get_settings

    settings = get_settings()
    m = _normalize_mode(getattr(settings, "assistant_kb_graph_recall_mode", "mix"))
    k = _clamp_int(getattr(settings, "assistant_kb_graph_recall_top_k", 10), default=10, min_value=1, max_value=50)
    ck = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_chunk_top_k", 20),
        default=max(k, 1),
        min_value=1,
        max_value=50,
    )
    me = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_entries", 10),
        default=10,
        min_value=1,
        max_value=50,
    )
    cpe = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_chunks_per_entry", 3),
        default=3,
        min_value=1,
        max_value=10,
    )
    mcc = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_chunk_chars", 600),
        default=600,
        min_value=50,
        max_value=2000,
    )
    threshold = _clamp_float(
        getattr(settings, "assistant_kb_graph_recall_min_score", 0.0),
        default=0.0,
        min_value=0.0,
        max_value=1.0,
    )
    mt = _clamp_int(
        getattr(settings, "assistant_kb_graph_recall_max_tokens", 8),
        default=8,
        min_value=1,
        max_value=64,
    )

    from app.lightrag.service import LightRagService

    try:
        graph_data = _run_async(
            lambda: LightRagService().graph_recall_with_context(
                query=q,
                mode=m,
                top_k=k,
                chunk_top_k=ck,
                max_tokens=mt,
            )
        )
    except ApiException as exc:
        if exc.code == 40410:
            raise ValueError("LightRAG is not enabled") from exc
        raise

    # Extract chunks from graph_data
    chunks = graph_data.get("chunks") or []

    title_cache: dict[str, UUID] = {}
    title_db = None

    # Normalize chunks and group by entry_id, keeping attachment chunks.
    attachment_ids: set[UUID] = set()
    chunks_by_entry: dict[UUID, list[dict[str, Any]]] = {}
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

        entry_uuid: UUID | None = None
        if doc_id:
            try:
                entry_uuid = UUID(doc_id)
            except Exception:
                entry_uuid = None
        if entry_uuid is None and file_path:
            try:
                entry_uuid = UUID(file_path)
            except Exception:
                entry_uuid = None

        # Backward compat: try to map to Entry by parsing the rendered "Title:" line.
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

        if entry_uuid is None:
            continue

        if score is not None and score < threshold:
            continue

        if kind == "attachment" and attachment_id:
            try:
                attachment_ids.add(UUID(attachment_id))
            except Exception:
                pass

        chunks_by_entry.setdefault(entry_uuid, []).append(
            {
                "kind": kind,
                "docId": doc_id or None,
                "filePath": file_path or None,
                "entryId": str(entry_uuid),
                "attachmentId": attachment_id,
                "score": score,
                "content": _truncate(raw_chunk, mcc),
            }
        )

    # Collect unique Entry UUIDs from chunks
    entry_ids: set[UUID] = set()
    for eid in chunks_by_entry.keys():
        entry_ids.add(eid)

    # Build graphContext from entities and relationships
    graph_context: dict[str, Any] = {
        "entities": [],
        "relationships": [],
    }
    for ent in graph_data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        graph_context["entities"].append({
            "name": (ent.get("name") or "").strip(),
            "type": ent.get("type"),
            "description": ent.get("description"),
            "entryId": ent.get("entry_id"),
        })
    for rel in graph_data.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        graph_context["relationships"].append({
            "source": (rel.get("source") or "").strip(),
            "target": (rel.get("target") or "").strip(),
            "description": rel.get("description"),
            "keywords": rel.get("keywords"),
            "entryId": rel.get("entry_id"),
        })

    if not entry_ids:
        # 即使没有 entry，也可能有 graphContext，生成 references
        references = _build_references([], graph_context)
        return json.dumps(
            {
                "mode": m,
                "query": q,
                "items": [],
                "graphContext": graph_context,
                "references": references,
            },
            ensure_ascii=False,
            indent=2,
        )

    db = get_current_db()
    entries = db.query(Entry).filter(Entry.id.in_(list(entry_ids))).limit(me).all()

    attachment_name_by_id: dict[str, str] = {}
    if attachment_ids:
        try:
            from app.attachment.models import Attachment

            rows = db.query(Attachment.id, Attachment.original_filename).filter(Attachment.id.in_(list(attachment_ids))).all()
            for aid, name in rows:
                attachment_name_by_id[str(aid)] = name or ""
        except Exception:
            attachment_name_by_id = {}

    items: list[dict[str, Any]] = []
    for e in entries:
        evidences = chunks_by_entry.get(e.id, [])
        evidences.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
        evidences = evidences[:cpe]
        for ev in evidences:
            if ev.get("kind") == "attachment" and ev.get("attachmentId"):
                ev["filename"] = attachment_name_by_id.get(ev["attachmentId"], "")
        items.append({
            "entryId": str(e.id),
            "title": e.title or "",
            "summary": e.summary or "",
            "content": e.content or "",
            "evidences": evidences,
        })

    # Build numbered references for citation
    references = _build_references(items, graph_context)

    return json.dumps(
        {
            "mode": m,
            "query": q,
            "items": items,
            "chunks": [ev for item in items for ev in (item.get("evidences") or [])],
            "graphContext": graph_context,
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
