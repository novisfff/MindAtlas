"""Knowledge base (LightRAG) tools for the Assistant."""
from __future__ import annotations

import asyncio
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
    """Run an async coroutine from a sync tool function."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    # If we're already in an event loop, run the coroutine in a separate thread.
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(lambda: asyncio.run(factory()))
        return fut.result()


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


def _parse_entry_title_from_chunk(text: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    first_line = raw.splitlines()[0].strip()
    if not first_line.lower().startswith(_TITLE_PREFIX.lower()):
        return None
    title = first_line[len(_TITLE_PREFIX) :].strip()
    return title or None


@tool
def kb_search(
    query: str,
) -> str:
    """Search (recall) evidence chunks with graph context via LightRAG.

    This tool is designed for retrieval only. The assistant should generate the final response.
    Returns chunks grouped by Entry, plus knowledge graph entities and relationships for context.

    Note:
        Retrieval parameters are configured via Settings (env/.env), not tool arguments
        (the env var names are kept for backward compatibility):
        - ASSISTANT_KB_GRAPH_RECALL_MODE
        - ASSISTANT_KB_GRAPH_RECALL_TOP_K
        - ASSISTANT_KB_GRAPH_RECALL_CHUNK_TOP_K
        - ASSISTANT_KB_GRAPH_RECALL_MAX_ENTRIES
        - ASSISTANT_KB_GRAPH_RECALL_CHUNKS_PER_ENTRY
        - ASSISTANT_KB_GRAPH_RECALL_MAX_CHUNK_CHARS
        - ASSISTANT_KB_GRAPH_RECALL_MIN_SCORE
        - ASSISTANT_KB_GRAPH_RECALL_MAX_TOKENS

    Args:
        query: User query text.

    Returns:
        JSON object string:
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

    # Collect unique Entry UUIDs from chunks
    entry_ids: set[UUID] = set()
    title_cache: dict[str, UUID] = {}
    title_db = None
    for src in chunks:
        if not isinstance(src, dict):
            continue
        doc_id = (src.get("doc_id") or "").strip()
        entry_id: UUID | None = None
        if doc_id:
            try:
                entry_id = UUID(doc_id)
            except Exception:
                entry_id = None

        raw_chunk = src.get("content") or ""

        # Backward compat: try to map to Entry by parsing the rendered "Title:" line.
        if entry_id is None:
            title = _parse_entry_title_from_chunk(raw_chunk)
            if title:
                cached = title_cache.get(title)
                if cached is not None:
                    entry_id = cached
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
                        entry_id = hit.id

        if entry_id is not None:
            entry_ids.add(entry_id)

    # Build graphContext from entities and relationships
    graph_context: dict[str, Any] = {
        "entities": [],
        "relationships": [],
    }
    for ent in graph_data.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        graph_context["entities"].append({
            "name": ent.get("name") or "",
            "type": ent.get("type"),
            "description": ent.get("description"),
            "entryId": ent.get("entry_id"),
        })
    for rel in graph_data.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        graph_context["relationships"].append({
            "source": rel.get("source") or "",
            "target": rel.get("target") or "",
            "description": rel.get("description"),
            "keywords": rel.get("keywords"),
            "entryId": rel.get("entry_id"),
        })

    if not entry_ids:
        return json.dumps(
            {
                "mode": m,
                "query": q,
                "items": [],
                "graphContext": graph_context,
            },
            ensure_ascii=False,
            indent=2,
        )

    db = get_current_db()
    entries = db.query(Entry).filter(Entry.id.in_(list(entry_ids))).limit(me).all()

    items: list[dict[str, Any]] = []
    for e in entries:
        items.append({
            "entryId": str(e.id),
            "title": e.title or "",
            "summary": e.summary or "",
            "content": e.content or "",
        })

    return json.dumps(
        {
            "mode": m,
            "query": q,
            "items": items,
            "graphContext": graph_context,
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
    """Recommend relations for an Entry using LightRAG.

    Args:
        entry_id: Source Entry UUID.
        mode: LightRAG mode.
        limit: Max number of recommendations (1-100).
        min_score: Minimum similarity score (0.0-1.0).
        exclude_existing_relations: Filter out entries with existing relations.
        include_relation_type: Whether to predict relationType (slower).

    Returns:
        JSON object string:
          {"items": [{"targetEntryId": "...", "relationType": "USES", "score": 0.83}]}
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
