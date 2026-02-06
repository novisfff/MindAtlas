"""Business service for LightRAG queries (Phase 5)."""
from __future__ import annotations

import json
import hashlib
import logging
import math
import numbers
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from functools import partial
from typing import Any, AsyncIterator
from uuid import UUID

import anyio
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.config import get_settings
from app.lightrag.errors import LightRagConfigError, LightRagDependencyError, LightRagNotEnabledError
from app.lightrag.manager import get_rag
from app.lightrag.schemas import (
    LightRagEntryRelationRecommendationItem,
    LightRagEntryRelationRecommendationsResponse,
    LightRagQueryMetadata,
    LightRagQueryMode,
    LightRagQueryResponse,
    LightRagSource,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CacheEntry:
    value: LightRagQueryResponse
    expires_at: float


class _TtlCache:
    def __init__(self) -> None:
        self._data: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, *, now: float) -> LightRagQueryResponse | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return entry.value

    def set(self, key: str, value: LightRagQueryResponse, *, now: float, ttl_sec: int, maxsize: int) -> None:
        if ttl_sec <= 0 or maxsize <= 0:
            return
        with self._lock:
            self._data[key] = _CacheEntry(value=value, expires_at=now + float(ttl_sec))
            self._data.move_to_end(key)
            while len(self._data) > maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_QUERY_CACHE = _TtlCache()

_QUERY_SEMAPHORES: dict[int, threading.BoundedSemaphore] = {}
_QUERY_SEMAPHORES_LOCK = threading.Lock()


def _get_query_semaphore(max_concurrency: int) -> threading.BoundedSemaphore:
    n = max(1, int(max_concurrency or 1))
    with _QUERY_SEMAPHORES_LOCK:
        sem = _QUERY_SEMAPHORES.get(n)
        if sem is None:
            sem = threading.BoundedSemaphore(n)
            _QUERY_SEMAPHORES[n] = sem
        return sem


async def _acquire_query_semaphore(sem: threading.BoundedSemaphore, *, timeout_sec: float) -> bool:
    t = float(timeout_sec or 0.0)
    if t <= 0:
        return await anyio.to_thread.run_sync(lambda: sem.acquire(blocking=False), abandon_on_cancel=True)
    return await anyio.to_thread.run_sync(lambda: sem.acquire(timeout=t), abandon_on_cancel=True)


def reset_lightrag_query_state_for_tests() -> None:
    """Best-effort test hook to clear in-process caches."""
    try:
        with _QUERY_SEMAPHORES_LOCK:
            _QUERY_SEMAPHORES.clear()
    except Exception:
        pass
    try:
        _QUERY_CACHE.clear()
    except Exception:
        pass


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\\n\\n"


def _hash_for_cache(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _make_cache_key(*, query: str, mode: LightRagQueryMode, top_k: int) -> str:
    # Keep key stable and safe: do not include secrets.
    q = (query or "").strip()
    return f"m={mode}|k={top_k}|ql={len(q)}|qh={_hash_for_cache(q)}"


def _normalize_score(value: Any) -> float | None:
    if value is None:
        return None
    # bool is a subclass of int; treat it as invalid for score.
    if isinstance(value, bool):
        return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    if isinstance(value, numbers.Real):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _normalize_sources(value: Any) -> list[LightRagSource]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[LightRagSource] = []
        for item in value:
            if isinstance(item, str):
                out.append(LightRagSource(content=item))
                continue
            if isinstance(item, dict):
                out.append(
                    LightRagSource(
                        doc_id=str(item.get("doc_id") or item.get("docId") or item.get("id") or "") or None,
                        file_path=str(item.get("file_path") or item.get("filePath") or "") or None,
                        content=(item.get("content") or item.get("text") or item.get("chunk") or None),
                        score=_normalize_score(item.get("score")),
                        metadata=(item.get("metadata") if isinstance(item.get("metadata"), dict) else None),
                    )
                )
                continue
            out.append(LightRagSource(content=str(item)))
        return out
    if isinstance(value, dict):
        return _normalize_sources([value])
    return [LightRagSource(content=str(value))]


def _decorate_sources(sources: list[LightRagSource]) -> list[LightRagSource]:
    """Derive entry/attachment linkage from doc_id/file_path.

    Convention:
    - Entry doc: doc_id == <entry_uuid>, file_path may also be <entry_uuid>
    - Attachment doc: doc_id == "attachment:<attachment_uuid>", file_path == "{entry_uuid}/attachments/{attachment_uuid}" (new) or <entry_uuid> (old)
    """
    from app.lightrag.source_ids import (
        is_attachment_doc_id,
        parse_attachment_id_from_attachment_file_path,
        parse_attachment_id_from_doc_id,
        parse_entry_id_from_attachment_file_path,
    )

    for src in sources or []:
        doc_id = (getattr(src, "doc_id", None) or "").strip()
        file_path = (getattr(src, "file_path", None) or "").strip()

        # Some upstream recall paths may lose attachment doc_id prefix; recover from file_path when possible.
        if "/attachments/" in file_path:
            attachment_id = parse_attachment_id_from_attachment_file_path(file_path)
            entry_id = parse_entry_id_from_attachment_file_path(file_path)
            if attachment_id and entry_id:
                src.kind = "attachment"
                src.attachment_id = attachment_id
                src.entry_id = entry_id
                continue

        if is_attachment_doc_id(doc_id):
            attachment_id = parse_attachment_id_from_doc_id(doc_id)
            src.kind = "attachment"
            src.attachment_id = attachment_id or None
            # Support both old (UUID-only) and new (composite) file_path formats
            src.entry_id = parse_entry_id_from_attachment_file_path(file_path)
            continue

        # Default: try interpret doc_id as entry UUID.
        try:
            src.entry_id = str(UUID(doc_id)) if doc_id else None
            if src.entry_id:
                src.kind = "entry"
        except Exception:
            pass

    return sources


def _normalize_answer(raw: Any) -> tuple[str, list[LightRagSource]]:
    if raw is None:
        return "", []

    if isinstance(raw, str):
        # Try parse as JSON (for include_references=True responses)
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    answer = (
                        parsed.get("answer")
                        or parsed.get("response")
                        or parsed.get("result")
                        or parsed.get("text")
                        or ""
                    )
                    sources = (
                        parsed.get("sources")
                        or parsed.get("source")
                        or parsed.get("contexts")
                        or parsed.get("chunks")
                        or parsed.get("documents")
                        or parsed.get("references")
                        or []
                    )
                    return str(answer), _normalize_sources(sources)
            except (json.JSONDecodeError, ValueError):
                pass
        return raw, []
    if isinstance(raw, dict):
        answer = (
            raw.get("answer")
            or raw.get("response")
            or raw.get("result")
            or raw.get("text")
            or ""
        )
        sources = (
            raw.get("sources")
            or raw.get("source")
            or raw.get("contexts")
            or raw.get("chunks")
            or raw.get("documents")
            or raw.get("references")
            or []
        )
        return str(answer), _normalize_sources(sources)
    # Fallback: stringify object
    return str(raw), []


def _call_rag_query_sync(*, query: str, mode: LightRagQueryMode, top_k: int, timeout_sec: float = 30.0) -> Any:
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _do() -> Any:
        rag = get_rag()
        q = (query or "").strip()
        enable_rerank = bool(getattr(rag, "rerank_model_func", None))

        # Try new QueryParam + query_llm API (lightrag-hku >= 1.4.x)
        # query_llm returns structured data with llm_response and chunks
        try:
            from lightrag import QueryParam

            try:
                param = QueryParam(mode=mode, top_k=top_k, chunk_top_k=top_k, stream=False, enable_rerank=enable_rerank)
                raw = rag.query_llm(q, param=param)
            except Exception:
                if str(mode) != "mix":
                    raise
                # Backward compat: some LightRAG versions don't accept "mix"; fall back to "hybrid".
                param = QueryParam(
                    mode="hybrid", top_k=top_k, chunk_top_k=top_k, stream=False, enable_rerank=enable_rerank
                )
                raw = rag.query_llm(q, param=param)

            # Extract answer from llm_response
            answer = ""
            if isinstance(raw, dict):
                llm_resp = raw.get("llm_response") or {}
                answer = llm_resp.get("content") or ""

            # Get sources with doc_id from chunks_vdb
            sources = []
            try:
                loop = runtime.loop
                vector_hits = loop.run_until_complete(rag.chunks_vdb.query(q, top_k=top_k))
                for hit in vector_hits or []:
                    if isinstance(hit, dict):
                        score = hit.get("score")
                        if score is None:
                            score = hit.get("distance")
                        file_path = hit.get("file_path") or hit.get("filePath") or ""
                        doc_id = (
                            hit.get("full_doc_id")
                            or hit.get("fullDocId")
                            or hit.get("doc_id")
                            or hit.get("docId")
                            or hit.get("id")
                            or file_path
                            or ""
                        )
                        sources.append({
                            "doc_id": doc_id,
                            "file_path": file_path,
                            "content": hit.get("content") or "",
                            "score": score if score is not None else 0.0,
                        })
            except Exception:
                # Fallback: try to get chunks from query_llm response
                if isinstance(raw, dict):
                    data = raw.get("data") or {}
                    chunks = data.get("chunks") or []
                    for chunk in chunks:
                        if isinstance(chunk, dict):
                            file_path = chunk.get("file_path") or chunk.get("filePath") or ""
                            doc_id = (
                                chunk.get("full_doc_id")
                                or chunk.get("fullDocId")
                                or chunk.get("doc_id")
                                or chunk.get("docId")
                                or chunk.get("id")
                                or file_path
                                or ""
                            )
                            sources.append({
                                "doc_id": doc_id,
                                "file_path": file_path,
                                "content": chunk.get("content") or chunk.get("text") or chunk.get("chunk") or "",
                                "score": 0.5,  # Default score when not available
                            })

            return {"answer": answer, "sources": sources}
        except (ImportError, TypeError, AttributeError):
            pass

        # Fallback to old API conventions
        try:
            return rag.query(q, mode=mode, top_k=top_k)
        except TypeError:
            pass
        try:
            return rag.query(q, query_mode=mode, top_k=top_k)
        except TypeError:
            pass
        try:
            return rag.query(q, mode=mode, top_k=top_k, topK=top_k)
        except TypeError:
            pass
        return rag.query(q)

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _call_rag_recall_sync(*, query: str, top_k: int, timeout_sec: float = 30.0) -> Any:
    """Lightweight recall path: vector retrieval only, no LLM call.

    Returns a payload compatible with _normalize_sources().
    """
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _do() -> Any:
        rag = get_rag()
        q = (query or "").strip()
        if not q or top_k <= 0:
            return []

        vector_hits = runtime.loop.run_until_complete(rag.chunks_vdb.query(q, top_k=top_k))
        sources: list[dict[str, Any]] = []
        for hit in vector_hits or []:
            if not isinstance(hit, dict):
                continue
            score = hit.get("score")
            file_path = hit.get("file_path") or hit.get("filePath") or ""
            doc_id = (
                hit.get("full_doc_id")
                or hit.get("fullDocId")
                or hit.get("doc_id")
                or hit.get("docId")
                or hit.get("id")
                or file_path
                or ""
            )
            sources.append(
                {
                    "doc_id": doc_id,
                    "file_path": file_path,
                    "content": hit.get("content") or "",
                    "score": score if score is not None else 0.0,
                }
            )
        return sources

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _extract_query_llm_chunks(raw: Any) -> list[dict[str, Any]]:
    """Best-effort extraction of chunks from LightRAG `query_llm` raw response."""
    if raw is None:
        return []

    # Sometimes upstream returns JSON text.
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                raw = json.loads(s)
            except Exception:
                return []
        else:
            return []

    if not isinstance(raw, dict):
        return []

    candidates: list[Any] = []

    def pick(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key in ("chunks", "chunk", "contexts", "context", "sources", "documents", "references"):
            v = container.get(key)
            if isinstance(v, list):
                candidates.extend(v)
            elif isinstance(v, dict):
                candidates.append(v)

    pick(raw)
    pick(raw.get("data"))
    pick(raw.get("result"))

    out: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"doc_id": "", "content": s, "score": None})
            continue
        if not isinstance(item, dict):
            continue

        file_path = item.get("file_path") or item.get("filePath") or ""
        doc_id = (
            item.get("full_doc_id")
            or item.get("fullDocId")
            or item.get("doc_id")
            or item.get("docId")
            or item.get("file_path")
            or item.get("filePath")
            or item.get("id")
            or ""
        )
        content = item.get("content") or item.get("text") or item.get("chunk") or ""
        score = item.get("score")
        if score is None:
            score = item.get("distance")
        if score is None:
            score = item.get("similarity")

        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else None

        # Recover doc_id/file_path from metadata if upstream uses nested fields.
        if metadata:
            if not doc_id:
                doc_id = (
                    metadata.get("full_doc_id")
                    or metadata.get("fullDocId")
                    or metadata.get("doc_id")
                    or metadata.get("docId")
                    or metadata.get("id")
                    or doc_id
                )
            if not file_path:
                file_path = metadata.get("file_path") or metadata.get("filePath") or file_path

        doc_id_s = str(doc_id).strip() if doc_id is not None else ""
        file_path_s = str(file_path).strip() if file_path is not None else ""
        if not doc_id_s and file_path_s:
            doc_id_s = file_path_s

        # If attachment chunks lost "attachment:" doc_id, recover from file_path composite.
        if "/attachments/" in file_path_s and not doc_id_s.startswith("attachment:"):
            try:
                from app.lightrag.source_ids import build_attachment_doc_id, parse_attachment_id_from_attachment_file_path

                attachment_id = parse_attachment_id_from_attachment_file_path(file_path_s)
                if attachment_id:
                    doc_id_s = build_attachment_doc_id(attachment_id)
            except Exception:
                pass

        out.append(
            {
                "doc_id": doc_id_s,
                "file_path": file_path_s,
                "content": str(content) if content is not None else "",
                "score": score,
                "metadata": metadata,
            }
        )

    return out


def _extract_graph_context(raw: Any) -> dict[str, Any]:
    """Extract full graph context from LightRAG `query_llm` response with only_need_context=True.

    Returns:
        dict with keys: chunks, entities, relationships
    """
    result: dict[str, Any] = {"chunks": [], "entities": [], "relationships": []}

    if raw is None:
        return result

    # Parse JSON string if needed
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                raw = json.loads(s)
            except Exception:
                return result
        else:
            return result

    if not isinstance(raw, dict):
        return result

    # Get data container
    data = raw.get("data") or raw

    # Extract chunks
    result["chunks"] = _extract_query_llm_chunks(raw)

    # Extract entities
    entities_raw = data.get("entities") or []
    for item in entities_raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("entity_name") or item.get("name") or "").strip()
        if not name:
            continue
        entry_id = (item.get("file_path") or item.get("source_id") or "").strip()
        # Normalize entry_id: skip if it's "unknown_source" or not a valid UUID pattern
        if entry_id and entry_id.lower() == "unknown_source":
            entry_id = None
        result["entities"].append({
            "name": name,
            "type": (item.get("entity_type") or item.get("type") or "").strip() or None,
            "description": (item.get("description") or "").strip() or None,
            "entry_id": entry_id or None,
        })

    # Extract relationships
    relationships_raw = data.get("relationships") or []
    for item in relationships_raw:
        if not isinstance(item, dict):
            continue
        source = (item.get("src_id") or item.get("source") or "").strip()
        target = (item.get("tgt_id") or item.get("target") or "").strip()
        if not source or not target:
            continue
        entry_id = (item.get("file_path") or item.get("source_id") or "").strip()
        if entry_id and entry_id.lower() == "unknown_source":
            entry_id = None
        result["relationships"].append({
            "source": source,
            "target": target,
            "description": (item.get("description") or "").strip() or None,
            "keywords": (item.get("keywords") or "").strip() or None,
            "entry_id": entry_id or None,
        })

    return result


def _extract_candidate_entry_ids(graph_context: dict[str, Any]) -> set[UUID]:
    """Extract candidate entry IDs from graph context (chunks/entities/relationships).

    Args:
        graph_context: dict with keys chunks, entities, relationships

    Returns:
        Set of valid UUIDs extracted from file_path/doc_id/entry_id fields
    """
    from app.lightrag.source_ids import is_attachment_doc_id, parse_entry_id_from_attachment_file_path

    entry_ids: set[UUID] = set()

    # From chunks: use doc_id or file_path
    for chunk in graph_context.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        doc_id = (chunk.get("doc_id") or chunk.get("full_doc_id") or "").strip()
        file_path = (chunk.get("file_path") or "").strip()

        # For attachment chunks, parse entry_id from composite file_path
        if is_attachment_doc_id(doc_id):
            parsed_entry_id = parse_entry_id_from_attachment_file_path(file_path)
            if parsed_entry_id:
                try:
                    entry_ids.add(UUID(parsed_entry_id))
                except (ValueError, TypeError):
                    pass
            continue

        # For entry chunks, try doc_id first, then file_path
        if doc_id:
            try:
                entry_ids.add(UUID(doc_id))
                continue
            except (ValueError, TypeError):
                pass
        if file_path:
            # Try as UUID first, then as composite format
            try:
                entry_ids.add(UUID(file_path))
            except (ValueError, TypeError):
                parsed = parse_entry_id_from_attachment_file_path(file_path)
                if parsed:
                    try:
                        entry_ids.add(UUID(parsed))
                    except (ValueError, TypeError):
                        pass

    # From entities: use entry_id
    for ent in graph_context.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        entry_id = (ent.get("entry_id") or "").strip()
        if entry_id:
            try:
                entry_ids.add(UUID(entry_id))
            except (ValueError, TypeError):
                pass

    # From relationships: use entry_id
    for rel in graph_context.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        entry_id = (rel.get("entry_id") or "").strip()
        if entry_id:
            try:
                entry_ids.add(UUID(entry_id))
            except (ValueError, TypeError):
                pass

    return entry_ids


def _call_rag_get_knowledge_graph_sync(
    *,
    node_label: str = "*",
    max_depth: int = 3,
    max_nodes: int = 1000,
    timeout_sec: float = 30.0,
) -> Any:
    """Get full knowledge graph from LightRAG.

    Returns:
        KnowledgeGraph object with nodes and edges
    """
    import inspect
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _do() -> Any:
        rag = get_rag()
        # Try async get_knowledge_graph method
        try:
            coro = rag.get_knowledge_graph(
                node_label=node_label,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
            # If it returns a coroutine, run it in the runtime loop
            if inspect.isawaitable(coro):
                return runtime.loop.run_until_complete(coro)
            return coro
        except TypeError:
            # Fallback: try without max_nodes
            try:
                coro = rag.get_knowledge_graph(
                    node_label=node_label,
                    max_depth=max_depth,
                )
                if inspect.isawaitable(coro):
                    return runtime.loop.run_until_complete(coro)
                return coro
            except Exception as e:
                logger.warning("lightrag get_knowledge_graph fallback failed", extra={"error": str(e)})
        except AttributeError:
            logger.debug("lightrag get_knowledge_graph method not available")
        # Return empty graph if method not available
        return {"nodes": [], "edges": []}

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _knowledge_graph_to_graph_data(raw_kg: Any, *, db: "Session | None" = None):
    """Convert LightRAG KnowledgeGraph to GraphData format.

    Args:
        raw_kg: KnowledgeGraph object or dict with nodes/edges
        db: Optional SQLAlchemy Session for resolving entry_id -> entry_title

    Returns:
        GraphData compatible with frontend
    """
    from app.graph.schemas import GraphData, GraphNode, GraphLink

    if raw_kg is None:
        return GraphData(nodes=[], links=[])

    # Handle both object and dict formats
    if hasattr(raw_kg, "nodes"):
        raw_nodes = raw_kg.nodes or []
        raw_edges = raw_kg.edges or []
    elif isinstance(raw_kg, dict):
        raw_nodes = raw_kg.get("nodes") or []
        raw_edges = raw_kg.get("edges") or []
    else:
        return GraphData(nodes=[], links=[])

    # Collect all entry_ids for batch title lookup
    node_items: list[dict[str, Any]] = []
    link_items: list[dict[str, Any]] = []
    entry_uuids: set[UUID] = set()

    for n in raw_nodes:
        node_id = _get_node_attr(n, "id", "")
        if not node_id:
            continue

        # Extract properties
        props = _get_node_attr(n, "properties", {}) or {}
        if not isinstance(props, dict):
            props = {}

        # Extract LightRAG-specific fields
        entity_id = _normalize_text(props.get("entity_id") or props.get("entityId"))
        entity_type = _normalize_text(props.get("entity_type") or props.get("entityType"))
        description = _normalize_text(props.get("description") or props.get("summary"))
        entry_id = _normalize_text(
            props.get("file_path")
            or props.get("filePath")
            or props.get("entry_id")
            or props.get("entryId")
        )
        # Handle multiple entry IDs separated by <SEP>
        if entry_id and "<SEP>" in entry_id:
            entry_id = entry_id.split("<SEP>")[0].strip()
        # Normalize entry_id and collect for batch lookup
        if entry_id:
            try:
                u = UUID(entry_id)
                entry_uuids.add(u)
                entry_id = str(u)
            except (ValueError, TypeError):
                pass
        created_at = _normalize_datetime(props.get("created_at") or props.get("createdAt"))

        # Extract label: prefer entity_id for display
        label = (
            entity_id
            or _normalize_text(props.get("name"))
            or _normalize_text(props.get("title"))
            or _normalize_text(props.get("entity"))
            or str(node_id)
        )

        # Extract type from labels array, prefer entity_type
        labels = _get_node_attr(n, "labels", []) or []
        if not isinstance(labels, list):
            labels = [str(labels)] if labels else []
        type_name = entity_type or (labels[0] if labels else "LightRAG")
        type_id = f"lightrag:{type_name}"

        # Generate stable color from type_name
        color = _hash_to_color(type_name)

        node_items.append({
            "id": str(node_id),
            "label": str(label),
            "type_id": type_id,
            "type_name": type_name,
            "color": color,
            "created_at": created_at,
            "summary": description,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "description": description,
            "entry_id": entry_id,
        })

    links = []
    for e in raw_edges:
        source = _get_node_attr(e, "source", "")
        target = _get_node_attr(e, "target", "")
        if not source or not target:
            continue

        # Extract edge properties
        edge_props = _get_node_attr(e, "properties", {}) or {}
        if not isinstance(edge_props, dict):
            edge_props = {}

        edge_description = _normalize_text(edge_props.get("description"))
        edge_keywords = _normalize_keywords(edge_props.get("keywords"))
        edge_entry_id = _normalize_text(
            edge_props.get("file_path")
            or edge_props.get("filePath")
            or edge_props.get("entry_id")
            or edge_props.get("entryId")
        )
        # Handle multiple entry IDs separated by <SEP>
        if edge_entry_id and "<SEP>" in edge_entry_id:
            edge_entry_id = edge_entry_id.split("<SEP>")[0].strip()
        # Normalize entry_id and collect for batch lookup
        if edge_entry_id:
            try:
                u = UUID(edge_entry_id)
                entry_uuids.add(u)
                edge_entry_id = str(u)
            except (ValueError, TypeError):
                pass
        edge_created_at = _normalize_datetime(edge_props.get("created_at") or edge_props.get("createdAt"))

        edge_id = _get_node_attr(e, "id", "")
        edge_type = _normalize_text(_get_node_attr(e, "type", None)) or "RELATED"
        if not edge_id:
            edge_id = f"{source}|{edge_type}|{target}"

        link_items.append({
            "id": str(edge_id),
            "source": str(source),
            "target": str(target),
            "label": str(edge_type),
            "description": edge_description,
            "keywords": edge_keywords,
            "entry_id": edge_entry_id,
            "created_at": edge_created_at,
        })

    # Batch lookup entry titles
    entry_title_by_id: dict[str, str] = {}
    if db is not None and entry_uuids:
        try:
            from app.entry.models import Entry
            rows = db.query(Entry.id, Entry.title).filter(Entry.id.in_(list(entry_uuids))).all()
            entry_title_by_id = {str(eid): title for eid, title in rows if title}
        except Exception as e:
            logger.warning("lightrag graph entry title resolve failed", extra={"error": str(e)})

    # Build final nodes with entry_title
    nodes: list[GraphNode] = []
    for item in node_items:
        eid = item.get("entry_id")
        nodes.append(GraphNode(
            id=item["id"],
            label=item["label"],
            type_id=item["type_id"],
            type_name=item["type_name"],
            color=item.get("color"),
            created_at=item.get("created_at"),
            summary=item.get("summary"),
            entity_id=item.get("entity_id"),
            entity_type=item.get("entity_type"),
            description=item.get("description"),
            entry_id=eid,
            entry_title=entry_title_by_id.get(eid) if eid else None,
        ))

    # Build final links with entry_title
    links: list[GraphLink] = []
    for item in link_items:
        eid = item.get("entry_id")
        links.append(GraphLink(
            id=item["id"],
            source=item["source"],
            target=item["target"],
            label=item["label"],
            description=item.get("description"),
            keywords=item.get("keywords"),
            entry_id=eid,
            entry_title=entry_title_by_id.get(eid) if eid else None,
            created_at=item.get("created_at"),
        ))

    return GraphData(nodes=nodes, links=links)


def _get_node_attr(obj: Any, attr: str, default: Any) -> Any:
    """Get attribute from object or dict."""
    if hasattr(obj, attr):
        return getattr(obj, attr, default)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _normalize_text(value: Any) -> str | None:
    """Normalize a value to a trimmed string or None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (bytes, bytearray)):
        try:
            s = value.decode("utf-8", errors="ignore").strip()
            return s or None
        except Exception:
            return None
    return str(value).strip() or None


def _normalize_keywords(value: Any) -> str | None:
    """Normalize keywords to a comma-separated string."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            s = _normalize_text(item)
            if s:
                parts.append(s)
        return ", ".join(parts) or None
    return _normalize_text(value)


def _normalize_datetime(value: Any):
    """Normalize a value to a datetime object or None."""
    from datetime import datetime, timezone

    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    # Unix timestamps (seconds or milliseconds)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            # Heuristic: treat large values as milliseconds
            if ts > 1e11:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric timestamps encoded as string
        try:
            ts = float(s)
            return _normalize_datetime(ts)
        except ValueError:
            pass
        # ISO 8601 format
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    return None


# Tableau 10 color palette - professional, high-contrast colors for data visualization
_TABLEAU10 = (
    "#4E79A7",  # Blue
    "#F28E2B",  # Orange
    "#E15759",  # Red
    "#76B7B2",  # Teal
    "#59A14F",  # Green
    "#EDC949",  # Yellow
    "#AF7AA1",  # Purple
    "#FF9DA7",  # Pink
    "#9C755F",  # Brown
    "#BAB0AC",  # Gray
)


def _hash_to_color(s: str) -> str:
    """Map string to a stable color from the Tableau 10 palette."""
    key = (s or "").strip()
    if not key:
        return _TABLEAU10[0]
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    idx = int.from_bytes(digest[:4], "big") % len(_TABLEAU10)
    return _TABLEAU10[idx]


def _call_rag_graph_recall_sync(
    *,
    query: str,
    mode: LightRagQueryMode,
    top_k: int,
    chunk_top_k: int,
    max_tokens: int,
    include_graph_context: bool = False,
    timeout_sec: float = 30.0,
) -> Any:
    """Graph/hybrid recall path via query_llm chunks, with minimal answer generation.

    Args:
        include_graph_context: If True, return full graph context dict; otherwise return chunks list.
    """
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _do() -> Any:
        rag = get_rag()
        q = (query or "").strip()
        enable_rerank = bool(getattr(rag, "rerank_model_func", None))
        empty_result = {"chunks": [], "entities": [], "relationships": []} if include_graph_context else []
        if not q or top_k <= 0 or chunk_top_k <= 0:
            return empty_result

        # Prefer QueryParam + query_llm API (lightrag-hku >= 1.4.x)
        try:
            from lightrag import QueryParam

            try:
                param = QueryParam(
                    mode=mode,
                    top_k=top_k,
                    chunk_top_k=chunk_top_k,
                    stream=False,
                    only_need_context=True,
                    enable_rerank=enable_rerank,
                )
                try:
                    raw = rag.query_llm(q, param=param, max_tokens=max_tokens, temperature=0)
                except TypeError:
                    raw = rag.query_llm(q, param=param)
            except Exception:
                if str(mode) != "mix":
                    raise
                # Backward compat: some LightRAG versions don't accept "mix"; fall back to "hybrid".
                param = QueryParam(
                    mode="hybrid",
                    top_k=top_k,
                    chunk_top_k=chunk_top_k,
                    stream=False,
                    only_need_context=True,
                    enable_rerank=enable_rerank,
                )
                try:
                    raw = rag.query_llm(q, param=param, max_tokens=max_tokens, temperature=0)
                except TypeError:
                    raw = rag.query_llm(q, param=param)
            if include_graph_context:
                return _extract_graph_context(raw)
            return _extract_query_llm_chunks(raw)
        except (ImportError, TypeError, AttributeError):
            pass

        # Fallback: no query_llm available; degrade to vector recall.
        fallback = _call_rag_recall_sync(query=q, top_k=chunk_top_k, timeout_sec=timeout_sec)
        if include_graph_context:
            return {"chunks": fallback, "entities": [], "relationships": []}
        return fallback

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _call_rag_relation_recommend_sync(
    *,
    prompt: str,
    mode: LightRagQueryMode,
    top_k: int,
    chunk_top_k: int,
    timeout_sec: float = 30.0,
) -> tuple[str, dict[str, Any]]:
    """Single-stage relation recommendation: recall + LLM JSON response.

    Returns:
        (llm_answer, graph_context) - LLM JSON string and graph context with chunks/entities/relationships
    """
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()
    empty_context: dict[str, Any] = {"chunks": [], "entities": [], "relationships": []}

    def _do() -> tuple[str, dict[str, Any]]:
        rag = get_rag()
        q = (prompt or "").strip()
        enable_rerank = bool(getattr(rag, "rerank_model_func", None))
        if not q or top_k <= 0 or chunk_top_k <= 0:
            return "", empty_context

        try:
            from lightrag import QueryParam

            try:
                param = QueryParam(
                    mode=mode,
                    top_k=top_k,
                    chunk_top_k=chunk_top_k,
                    stream=False,
                    enable_rerank=enable_rerank,
                )
                raw = rag.query_llm(q, param=param)
            except Exception:
                if str(mode) != "mix":
                    raise
                param = QueryParam(
                    mode="hybrid",
                    top_k=top_k,
                    chunk_top_k=chunk_top_k,
                    stream=False,
                    enable_rerank=enable_rerank,
                )
                raw = rag.query_llm(q, param=param)

            llm_answer = ""
            if isinstance(raw, dict):
                llm_resp = raw.get("llm_response") or {}
                llm_answer = llm_resp.get("content") or ""
            graph_context = _extract_graph_context(raw)
            return llm_answer, graph_context
        except (ImportError, TypeError, AttributeError):
            pass

        # Fallback: degrade to vector recall without LLM
        fallback = _call_rag_recall_sync(query=q, top_k=chunk_top_k, timeout_sec=timeout_sec)
        fallback_chunks = [{"doc_id": s.doc_id} for s in fallback if s.doc_id]
        return "", {"chunks": fallback_chunks, "entities": [], "relationships": []}

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _call_rag_llm_only_sync(*, query: str, mode: LightRagQueryMode, timeout_sec: float = 30.0) -> Any:
    """LLM-only path: generate an answer without fetching sources.

    Note: Depending on upstream LightRAG implementation, query_llm may still
    perform internal retrieval. This function avoids the extra chunks_vdb.query
    call that query() currently performs in this codebase.
    """
    from app.lightrag.runtime import get_lightrag_runtime

    runtime = get_lightrag_runtime()

    def _do() -> Any:
        rag = get_rag()
        q = (query or "").strip()
        if not q:
            return ""

        # Prefer QueryParam + query_llm API (lightrag-hku >= 1.4.x)
        try:
            from lightrag import QueryParam

            # Try to disable retrieval by setting k to 0; fall back if rejected.
            last_exc: Exception | None = None
            for top_k, chunk_top_k in ((0, 0), (1, 0), (1, 1)):
                try:
                    try:
                        param = QueryParam(mode=mode, top_k=top_k, chunk_top_k=chunk_top_k, stream=False)
                        raw = rag.query_llm(q, param=param)
                    except Exception:
                        if str(mode) != "mix":
                            raise
                        # Backward compat: some LightRAG versions don't accept "mix"; fall back to "hybrid".
                        param = QueryParam(mode="hybrid", top_k=top_k, chunk_top_k=chunk_top_k, stream=False)
                        raw = rag.query_llm(q, param=param)
                    if isinstance(raw, dict):
                        llm_resp = raw.get("llm_response") or {}
                        return llm_resp.get("content") or ""
                    return raw
                except Exception as e:
                    last_exc = e
                    continue
            raise RuntimeError("LightRAG query_llm failed") from last_exc
        except (ImportError, TypeError, AttributeError):
            pass

        # Fallback: best-effort legacy query (may involve retrieval in upstream).
        try:
            return rag.query(q, mode=mode, top_k=1)
        except TypeError:
            return rag.query(q)

    return runtime.call(_do, timeout_sec=float(timeout_sec or 0.0) + 5.0)


def _build_entry_recommendation_query_text(entry: Any) -> str:
    """Build query text from Entry for relation recommendations.

    Args:
        entry: Entry model instance

    Returns:
        Query text (max 4000 chars)
    """
    title = (getattr(entry, "title", None) or "").strip()
    summary = (getattr(entry, "summary", None) or "").strip()
    content = (getattr(entry, "content", None) or "").strip()
    tags = []
    for t in getattr(entry, "tags", None) or []:
        name = (getattr(t, "name", None) or "").strip()
        if name:
            tags.append(name)

    parts: list[str] = []
    if title:
        parts.append(title)
    if summary:
        parts.append(summary)
    if content:
        parts.append(content)
    if tags:
        parts.append(f"Tags: {', '.join(sorted(set(tags)))}")

    q = "\n\n".join(parts).strip()
    # Keep query bounded to reduce cost and avoid overly large prompts.
    max_chars = 4000
    return q[:max_chars].strip() if len(q) > max_chars else q


# Regex to extract JSON from markdown code fence
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", flags=re.IGNORECASE | re.DOTALL)

# Role definition for Entry relation recommendation prompts
_ENTRY_RELATION_RECOMMENDATION_ROLE = (
    "你是一个高精度的知识图谱关系推荐助手。\n\n"
    "你的任务是：\n"
    "1) 阅读用户提供的「当前记录内容」\n"
    "2) 分析系统检索到的三类数据：chunks（文档片段）、entities（实体）、relationships（关系）\n"
    "3) 从这些数据的 file_path 字段中识别候选 Entry ID（UUID 格式）\n"
    "4) 只推荐与「当前记录」在事件/内容层面真实相关的 Entry，并给出关系类型与 relevance 分数\n\n"
    "什么叫“真实相关”（必须满足其一，才允许输出）：\n"
    "- 同一具体项目/任务/客户需求/交付物/会议主题/工单等（能从文本中抽取到可唯一指代的对象，如项目名、客户名+项目代号、工单号、合同/里程碑名称等）\n"
    "- 明确的依赖/包含/跟进/复盘/因果链路（文本中有直接指向，如“为…准备/针对…整改/跟进…需求/…导致…”）\n"
    "- 跨类型但目的绑定（重点）：如出差/拜访/现场支持/培训/报销/采购等，必须能绑定到某个具体项目/任务/客户需求；若无法从文本中找到绑定对象，则不要推荐\n\n"
    "明确禁止（命中任一条必须不输出）：\n"
    "- 仅同类目/同标签/同领域词相似（例如同样是“比赛”、同样是“项目”、同样是“会议”）\n"
    "- 仅时间接近、仅地点相同、仅参与人部分重叠，但主题/对象不同且无直接指向\n"
    "- 需要靠常识猜测才能建立联系（不允许脑补）\n\n"
    "重要约束：\n"
    "- targetEntryId 必须是检索数据中 file_path 字段的值，不得编造\n"
    "- 只能推荐在检索结果中出现的 Entry\n"
    "- 允许返回空列表；不要为了凑满数量而输出"
)


def _truncate(s: str, max_len: int) -> str:
    """Truncate string to max_len characters."""
    s = (s or "").strip()
    return s[:max_len] if len(s) > max_len else s


def _build_entry_relation_recommendation_prompt(
    *, base_text: str, relation_type_codes: list[str], limit: int, max_chars: int = 5000
) -> str:
    """Build a prompt that asks LightRAG to output structured relation JSON with relevance."""
    base = (base_text or "").strip()
    if not base:
        return ""

    codes = [(c or "").strip() for c in (relation_type_codes or [])]
    codes = [c for c in codes if c]
    role = _ENTRY_RELATION_RECOMMENDATION_ROLE

    if not codes:
        prompt = f"{role}\n\n{base}".strip()
        if len(prompt) <= max_chars:
            return prompt
        keep = max(0, max_chars - len(role) - 2)
        return f"{role}\n\n{_truncate(base, keep)}".strip()

    relevance_rubric = (
        "relevance 评分标准（0.0-1.0，精确到小数点后两位）：\n"
        "- 0.95-1.00 近乎确定：同一具体项目/任务/工单/合同/里程碑等存在唯一标识重合，或文本明确说明依赖/包含/跟进/因果\n"
        "- 0.80-0.94 明确相关：存在多个强线索一致（例如同一客户+同一交付物/需求点+清晰时间链路+相互指向描述）\n"
        "- 0.65-0.79 可信相关：至少一个可具体指代的实体重合，并且能解释两者为什么有关联（而不是“同类目”）\n"
        "- 0.30-0.64 谨慎输出：只有在仍能找到“具体指代对象/目的绑定/依赖线索”时才可给到该区间；若只是标签/类别/泛主题相似，relevance 必须 ≤0.20 且不要输出\n"
        "- 低于 0.30 不推荐，不要输出"
    )

    instructions = (
        f"输出要求：\n"
        f"- 返回纯 JSON，无 markdown 包裹，无解释文字\n"
        f'- 格式: {{"relations":[{{"targetEntryId":"<UUID>","relationType":"<CODE>","relevance":<0.30-1.00>}}]}}\n'
        f'- 如无满足条件的结果，返回: {{"relations":[]}}\n'
        f"- relevance 必须是 0.0-1.0 的数字，保留两位小数；低于 0.30 的不要输出\n"
        f"- 最多 {int(limit)} 条推荐，按 relevance 从高到低排序\n"
        f"- relationType 必须是: {json.dumps(codes, ensure_ascii=False)}\n"
        f"- targetEntryId 必须来自检索数据的 file_path 字段\n\n"
        f"{relevance_rubric}"
    )
    sep = "\n\n---\n\n"

    body = f"{base}{sep}{instructions}".strip()
    prompt = f"{role}\n\n{body}".strip()
    if len(prompt) <= max_chars:
        return prompt

    overhead = len(role) + 2 + len(sep) + len(instructions)
    keep = max(0, max_chars - overhead)
    return f"{role}\n\n{_truncate(base, keep)}{sep}{instructions}".strip()


def _try_parse_json_from_answer(answer: str) -> Any | None:
    """Try to parse JSON from LightRAG answer, handling markdown fences."""
    s = (answer or "").strip()
    if not s:
        return None

    # Try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # Try extracting from markdown fence
    m = _JSON_FENCE_RE.search(s)
    if m:
        candidate = (m.group(1) or "").strip()
        if candidate:
            try:
                return json.loads(candidate)
            except Exception:
                pass

    # Best-effort: extract JSON object/array substring
    start_candidates = [pos for pos in (s.find("{"), s.find("[")) if pos != -1]
    if not start_candidates:
        return None
    start = min(start_candidates)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        return None

    candidate = s[start : end + 1].strip()
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _extract_relation_items(payload: Any) -> list[dict[str, Any]]:
    """Extract relation items from parsed JSON payload."""
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        rels = payload.get("relations") or payload.get("items") or payload.get("data") or payload.get("result")
        if isinstance(rels, list):
            return [i for i in rels if isinstance(i, dict)]
    return []


@dataclass
class RelationRecommendation:
    """Parsed relation recommendation from LLM output."""
    target_id: UUID
    relation_type: str | None
    relevance: float


def _parse_relation_recommendation_payload(
    answer: str,
    *,
    allowed_relation_type_codes: list[str],
    candidate_ids: set[UUID],
) -> list[RelationRecommendation]:
    """Parse LLM answer to extract relation recommendations with relevance.

    Args:
        answer: LLM JSON output
        allowed_relation_type_codes: Valid relation type codes
        candidate_ids: Whitelist of valid target entry IDs

    Returns:
        List of RelationRecommendation, deduplicated by target_id (max relevance wins)
    """
    if not answer or not candidate_ids:
        return []

    payload = _try_parse_json_from_answer(answer)
    if payload is None:
        return []

    items = _extract_relation_items(payload)
    if not items:
        return []

    # Build case-insensitive lookup for relation types
    allowed = {
        (c or "").strip().lower(): (c or "").strip()
        for c in allowed_relation_type_codes
        if (c or "").strip()
    }

    # Collect recommendations, tracking max relevance per target_id
    by_target: dict[UUID, RelationRecommendation] = {}

    for item in items:
        # Extract targetEntryId
        target_raw = (
            item.get("targetEntryId")
            or item.get("target_entry_id")
            or item.get("targetId")
            or item.get("doc_id")
        )
        if not target_raw:
            continue
        try:
            target_id = UUID(str(target_raw))
        except (ValueError, TypeError):
            continue
        if target_id not in candidate_ids:
            continue

        # Extract relevance (explicit None check to handle relevance=0.0 correctly)
        relevance_raw = item.get("relevance")
        if relevance_raw is None:
            relevance_raw = item.get("score")
        if relevance_raw is None:
            continue
        try:
            relevance = float(relevance_raw)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(relevance):
            continue
        relevance = max(0.0, min(1.0, relevance))

        # Extract relationType (optional)
        relation_raw = item.get("relationType") or item.get("relation_type")
        relation_type: str | None = None
        if relation_raw and allowed:
            rel_code = allowed.get(str(relation_raw).strip().lower())
            if rel_code:
                relation_type = rel_code

        # Keep max relevance per target_id
        existing = by_target.get(target_id)
        if existing is None or relevance > existing.relevance:
            by_target[target_id] = RelationRecommendation(
                target_id=target_id,
                relation_type=relation_type or (existing.relation_type if existing else None),
                relevance=relevance,
            )

    return list(by_target.values())


class LightRagService:
    async def query(self, *, query: str, mode: LightRagQueryMode, top_k: int) -> LightRagQueryResponse:
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        query_len = len((query or "").strip())
        cache_ttl = int(getattr(settings, "lightrag_query_cache_ttl_sec", 0) or 0)
        cache_maxsize = int(getattr(settings, "lightrag_query_cache_maxsize", 0) or 0)
        cache_key = _make_cache_key(query=query, mode=mode, top_k=top_k)
        now = time.monotonic()

        cache_hit = False
        if cache_ttl > 0 and cache_maxsize > 0:
            cached = _QUERY_CACHE.get(cache_key, now=now)
            if cached is not None:
                cache_hit = True
                return LightRagQueryResponse(
                    answer=cached.answer,
                    sources=cached.sources,
                    metadata=LightRagQueryMetadata(
                        mode=mode,
                        top_k=top_k,
                        latency_ms=0,
                        cache_hit=True,
                    ),
                )

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        queue_start = time.perf_counter()
        queue_wait_ms = 0
        run_ms = 0
        acquired = False
        try:
            acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
            acquired_at = time.perf_counter()
            queue_wait_ms = int((acquired_at - queue_start) * 1000)
            if not acquired:
                raise TimeoutError("LightRAG query concurrency slot timeout")

            with anyio.fail_after(timeout_sec):
                    raw = await anyio.to_thread.run_sync(
                        partial(_call_rag_query_sync, query=query, mode=mode, top_k=top_k, timeout_sec=timeout_sec),
                        abandon_on_cancel=True,
                    )
            run_ms = int((time.perf_counter() - acquired_at) * 1000)
        except TimeoutError as e:
            logger.warning(
                "lightrag query timeout",
                extra={
                    "mode": mode,
                    "top_k": top_k,
                    "timeout_sec": timeout_sec,
                    "queue_wait_ms": queue_wait_ms,
                    "query_len": query_len,
                },
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            logger.info("lightrag is not enabled", extra={"detail": str(e)})
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            logger.exception("lightrag dependency missing", extra={"detail": str(e)})
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            logger.exception("lightrag config error", extra={"detail": str(e)})
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            logger.exception("lightrag query failed", extra={"detail": str(e)})
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            if acquired:
                try:
                    sem.release()
                except Exception:
                    pass

        latency_ms = int((time.perf_counter() - queue_start) * 1000)
        answer, sources = _normalize_answer(raw)
        sources = _decorate_sources(sources)
        result = LightRagQueryResponse(
            answer=answer,
            sources=sources,
            metadata=LightRagQueryMetadata(
                mode=mode,
                top_k=top_k,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            ),
        )

        if cache_ttl > 0 and cache_maxsize > 0:
            _QUERY_CACHE.set(cache_key, result, now=time.monotonic(), ttl_sec=cache_ttl, maxsize=cache_maxsize)

        logger.info(
            "lightrag query done",
            extra={
                "mode": mode,
                "top_k": top_k,
                "latency_ms": latency_ms,
                "queue_wait_ms": queue_wait_ms,
                "run_ms": run_ms,
                "cache_hit": cache_hit,
                "cache_ttl_sec": cache_ttl,
                "cache_maxsize": cache_maxsize,
                "timeout_sec": timeout_sec,
                "max_concurrency": max_concurrency,
                "query_len": query_len,
            },
        )
        return result

    async def recall_sources(self, *, query: str, mode: LightRagQueryMode, top_k: int) -> list[LightRagSource]:
        """Recall sources via vector retrieval only (faster than full query()).

        Note: mode is accepted for cache key consistency and future extensibility.
        """
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        q = (query or "").strip()
        cache_ttl = int(getattr(settings, "lightrag_query_cache_ttl_sec", 0) or 0)
        cache_maxsize = int(getattr(settings, "lightrag_query_cache_maxsize", 0) or 0)
        cache_key = f"recall|{_make_cache_key(query=q, mode=mode, top_k=top_k)}"
        now = time.monotonic()

        if cache_ttl > 0 and cache_maxsize > 0:
            cached = _QUERY_CACHE.get(cache_key, now=now)
            if cached is not None:
                return list(cached.sources or [])

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            logger.warning(
                "lightrag query concurrency timeout",
                extra={"op": "recall_sources", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout")
        try:
            with anyio.fail_after(timeout_sec):
                raw_sources = await anyio.to_thread.run_sync(
                    partial(_call_rag_recall_sync, query=q, top_k=top_k, timeout_sec=timeout_sec),
                    abandon_on_cancel=True,
                )
        except TimeoutError as e:
            logger.warning(
                "lightrag query timeout",
                extra={"op": "recall_sources", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        sources = _decorate_sources(_normalize_sources(raw_sources))
        if cache_ttl > 0 and cache_maxsize > 0:
            _QUERY_CACHE.set(
                cache_key,
                LightRagQueryResponse(
                    answer="",
                    sources=sources,
                    metadata=LightRagQueryMetadata(mode=mode, top_k=top_k, latency_ms=0, cache_hit=False),
                ),
                now=time.monotonic(),
                ttl_sec=cache_ttl,
                maxsize=cache_maxsize,
            )
        return sources

    async def graph_recall_sources(
        self,
        *,
        query: str,
        mode: LightRagQueryMode,
        top_k: int,
        chunk_top_k: int | None = None,
        max_tokens: int = 8,
    ) -> list[LightRagSource]:
        """Recall sources via LightRAG query_llm chunks (graph/hybrid aware).

        This is intended to be used as a knowledge-base retrieval layer only.
        The assistant should generate the final answer.
        """
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        q = (query or "").strip()
        k = max(1, min(50, int(top_k or 1)))
        ck = max(1, min(50, int(chunk_top_k or k)))
        mt = max(1, min(64, int(max_tokens or 1)))

        cache_ttl = int(getattr(settings, "lightrag_query_cache_ttl_sec", 0) or 0)
        cache_maxsize = int(getattr(settings, "lightrag_query_cache_maxsize", 0) or 0)
        cache_key = f"graphrecall|m={mode}|k={k}|ck={ck}|mt={mt}|ql={len(q)}|qh={_hash_for_cache(q)}"
        now = time.monotonic()

        if cache_ttl > 0 and cache_maxsize > 0:
            cached = _QUERY_CACHE.get(cache_key, now=now)
            if cached is not None:
                return list(cached.sources or [])

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            logger.warning(
                "lightrag query concurrency timeout",
                extra={"op": "graph_recall_sources", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout")
        try:
            with anyio.fail_after(timeout_sec):
                    raw_sources = await anyio.to_thread.run_sync(
                        partial(
                            _call_rag_graph_recall_sync,
                            query=q,
                            mode=mode,
                            top_k=k,
                            chunk_top_k=ck,
                            max_tokens=mt,
                            timeout_sec=timeout_sec,
                        ),
                        abandon_on_cancel=True,
                    )
        except TimeoutError as e:
            logger.warning(
                "lightrag query timeout",
                extra={"op": "graph_recall_sources", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        sources = _decorate_sources(_normalize_sources(raw_sources))
        if cache_ttl > 0 and cache_maxsize > 0:
            _QUERY_CACHE.set(
                cache_key,
                LightRagQueryResponse(
                    answer="",
                    sources=sources,
                    metadata=LightRagQueryMetadata(mode=mode, top_k=k, latency_ms=0, cache_hit=False),
                ),
                now=time.monotonic(),
                ttl_sec=cache_ttl,
                maxsize=cache_maxsize,
            )
        return sources

    async def graph_recall_with_context(
        self,
        *,
        query: str,
        mode: LightRagQueryMode,
        top_k: int,
        chunk_top_k: int | None = None,
        max_tokens: int = 8,
    ) -> dict[str, Any]:
        """Recall sources with full graph context (entities + relationships + chunks).

        Returns:
            dict with keys: chunks, entities, relationships
        """
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        q = (query or "").strip()
        k = max(1, min(50, int(top_k or 1)))
        ck = max(1, min(50, int(chunk_top_k or k)))
        mt = max(1, min(64, int(max_tokens or 1)))

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            logger.warning(
                "lightrag query concurrency timeout",
                extra={"op": "graph_recall_with_context", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout")
        try:
            with anyio.fail_after(timeout_sec):
                result = await anyio.to_thread.run_sync(
                    partial(
                        _call_rag_graph_recall_sync,
                        query=q,
                        mode=mode,
                        top_k=k,
                        chunk_top_k=ck,
                        max_tokens=mt,
                        include_graph_context=True,
                        timeout_sec=timeout_sec,
                    ),
                    abandon_on_cancel=True,
                )
        except TimeoutError as e:
            logger.warning(
                "lightrag query timeout",
                extra={"op": "graph_recall_with_context", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        return result

    async def llm_only_answer(self, *, prompt: str, mode: LightRagQueryMode) -> str:
        """Run LLM generation only (no extra vector recall in this codepath)."""
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        q = (prompt or "").strip()
        cache_ttl = int(getattr(settings, "lightrag_query_cache_ttl_sec", 0) or 0)
        cache_maxsize = int(getattr(settings, "lightrag_query_cache_maxsize", 0) or 0)
        cache_key = f"llm|m={mode}|ql={len(q)}|qh={_hash_for_cache(q)}"
        now = time.monotonic()

        if cache_ttl > 0 and cache_maxsize > 0:
            cached = _QUERY_CACHE.get(cache_key, now=now)
            if cached is not None:
                return cached.answer or ""

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            logger.warning(
                "lightrag query concurrency timeout",
                extra={"op": "llm_only_answer", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout")
        try:
            with anyio.fail_after(timeout_sec):
                raw = await anyio.to_thread.run_sync(
                    partial(_call_rag_llm_only_sync, query=q, mode=mode, timeout_sec=timeout_sec),
                    abandon_on_cancel=True,
                )
        except TimeoutError as e:
            logger.warning(
                "lightrag query timeout",
                extra={"op": "llm_only_answer", "timeout_sec": timeout_sec, "max_concurrency": max_concurrency},
            )
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        answer, _ = _normalize_answer(raw)
        if cache_ttl > 0 and cache_maxsize > 0:
            _QUERY_CACHE.set(
                cache_key,
                LightRagQueryResponse(
                    answer=answer,
                    sources=[],
                    metadata=LightRagQueryMetadata(mode=mode, top_k=0, latency_ms=0, cache_hit=False),
                ),
                now=time.monotonic(),
                ttl_sec=cache_ttl,
                maxsize=cache_maxsize,
            )
        return answer

    async def query_sse(self, *, query: str, mode: LightRagQueryMode, top_k: int) -> AsyncIterator[str]:
        """SSE stream that emits ApiResponse JSON frames."""
        try:
            result = await self.query(query=query, mode=mode, top_k=top_k)
            payload = ApiResponse.ok(result.model_dump(by_alias=True)).model_dump()
            yield _sse_frame(payload)
        except ApiException as exc:
            payload = ApiResponse.fail(code=exc.code, message=exc.message, data=exc.details).model_dump()
            yield _sse_frame(payload)
        except Exception:
            payload = ApiResponse.fail(code=50000, message="Internal Server Error").model_dump()
            yield _sse_frame(payload)

    async def relation_recommend_with_llm(
        self,
        *,
        prompt: str,
        mode: LightRagQueryMode,
        top_k: int,
        chunk_top_k: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Single-stage relation recommendation with LLM JSON output.

        Returns:
            (llm_answer, graph_context) - graph_context has chunks/entities/relationships
        """
        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        q = (prompt or "").strip()
        k = max(1, min(50, int(top_k or 1)))
        ck = max(1, min(50, int(chunk_top_k or k)))

        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)
        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout")
        try:
            with anyio.fail_after(timeout_sec):
                result = await anyio.to_thread.run_sync(
                    partial(
                        _call_rag_relation_recommend_sync,
                        prompt=q,
                        mode=mode,
                        top_k=k,
                        chunk_top_k=ck,
                        timeout_sec=timeout_sec,
                    ),
                    abandon_on_cancel=True,
                )
        except TimeoutError as e:
            raise ApiException(status_code=504, code=50400, message="LightRAG query timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except LightRagConfigError as e:
            raise ApiException(status_code=500, code=50011, message="LightRAG config error") from e
        except Exception as e:
            raise ApiException(status_code=500, code=50012, message="LightRAG query failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        return result

    async def recommend_entry_relations(
        self,
        *,
        db: Session,
        entry_id: UUID,
        mode: str = "mix",
        limit: int = 20,
        min_score: float = 0.1,
        exclude_existing_relations: bool = False,
        include_relation_type: bool = True,
    ) -> LightRagEntryRelationRecommendationsResponse:
        """Recommend related Entries using LightRAG query recall (Phase 5.5).

        Args:
            db: Database session
            entry_id: Source Entry ID
            mode: Query mode (naive/local/global/hybrid/mix)
            limit: Max number of recommendations (1-100)
            min_score: Minimum similarity score threshold (0.0-1.0)
            exclude_existing_relations: Filter out entries with existing Relation records

        Returns:
            Recommendations response with items sorted by score desc

        Raises:
            ApiException: 404 if entry not found, 422 if params invalid
        """
        # Validate parameters
        if limit < 1 or limit > 100:
            raise ApiException(status_code=422, code=42200, message="Validation Error", details={"limit": limit})
        if min_score < 0.0 or min_score > 1.0:
            raise ApiException(
                status_code=422, code=42200, message="Validation Error", details={"min_score": min_score}
            )
        # Validate mode
        if mode not in ("naive", "local", "global", "hybrid", "mix"):
            raise ApiException(status_code=422, code=42200, message="Validation Error", details={"mode": mode})

        # HC-3: Enforce minimum relevance threshold of 0.30 (spec constraint)
        effective_min_score = max(min_score, 0.30)

        # Local imports to keep module imports light
        from app.entry.models import Entry
        from app.entry.service import EntryService
        from app.relation.models import RelationType
        from app.relation.service import RelationService

        # Find source entry (raises 404 if not exists)
        source_entry = EntryService(db).find_by_id(entry_id)

        # Build base query text from entry content
        base_text = _build_entry_recommendation_query_text(source_entry)
        if not base_text:
            return LightRagEntryRelationRecommendationsResponse(items=[])

        relation_type_codes: list[str] = []
        if include_relation_type:
            # Get enabled relation type codes for optional phase-2 structured output
            relation_type_codes = [
                (row[0] or "").strip()
                for row in db.query(RelationType.code)
                .filter(RelationType.enabled.is_(True))
                .order_by(RelationType.code.asc())
                .all()
            ]
            relation_type_codes = [c for c in relation_type_codes if c]

        # Single-stage: build prompt with relation recommendation instructions
        top_k = min(max(limit * 2, 1), 50)
        prompt = _build_entry_relation_recommendation_prompt(
            base_text=base_text,
            relation_type_codes=relation_type_codes,
            limit=limit,
        )

        # Call LLM with retrieval - get graph_context with chunks/entities/relationships
        llm_answer, graph_context = await self.relation_recommend_with_llm(
            prompt=prompt,
            mode=mode,
            top_k=top_k,
            chunk_top_k=top_k,
        )

        # Extract candidate entry IDs from all three data sources
        all_candidate_ids = _extract_candidate_entry_ids(graph_context)
        all_candidate_ids.discard(entry_id)  # Remove self-reference
        if not all_candidate_ids:
            return LightRagEntryRelationRecommendationsResponse(items=[])

        # Parse LLM output for relevance and relation types
        recommendations: list[RelationRecommendation] = []
        if llm_answer:
            try:
                recommendations = _parse_relation_recommendation_payload(
                    llm_answer,
                    allowed_relation_type_codes=relation_type_codes,
                    candidate_ids=all_candidate_ids,
                )
            except Exception as e:
                logger.warning("Failed to parse recommendations from LLM", extra={"error": str(e)})

        # Build relevance map from LLM output
        relevance_by_target: dict[UUID, float] = {r.target_id: r.relevance for r in recommendations}
        relation_type_by_target: dict[UUID, str | None] = {r.target_id: r.relation_type for r in recommendations}

        # Optional: filter out entries with explicit relations to source entry
        excluded: set[UUID] = set()
        if exclude_existing_relations:
            for rel in RelationService(db).find_by_entry(entry_id):
                other = rel.target_entry_id if rel.source_entry_id == entry_id else rel.source_entry_id
                excluded.add(other)

        # Apply effective_min_score (HC-3 enforced) and exclusion filters using relevance from LLM
        candidate_ids = [
            i for i in relevance_by_target.keys()
            if i not in excluded and relevance_by_target[i] >= effective_min_score
        ]
        if not candidate_ids:
            return LightRagEntryRelationRecommendationsResponse(items=[])

        # Filter out deleted/non-existent entries in SQL
        existing_ids = {row[0] for row in db.query(Entry.id).filter(Entry.id.in_(candidate_ids)).all()}
        final_ids = [i for i in candidate_ids if i in existing_ids]
        if not final_ids:
            return LightRagEntryRelationRecommendationsResponse(items=[])

        # Sort by relevance desc, then UUID for stability
        final_ids.sort(key=lambda i: (-relevance_by_target.get(i, 0.0), str(i)))
        final_ids = final_ids[:limit]

        items = [
            LightRagEntryRelationRecommendationItem(
                target_entry_id=i,
                relation_type=relation_type_by_target.get(i),
                score=relevance_by_target.get(i, 0.0),
            )
            for i in final_ids
        ]
        return LightRagEntryRelationRecommendationsResponse(items=items)

    async def get_graph_data(
        self,
        *,
        node_label: str = "*",
        max_depth: int = 3,
        max_nodes: int = 1000,
        db: "Session | None" = None,
    ):
        """Get LightRAG knowledge graph data converted to GraphData format.

        Args:
            node_label: Label filter for nodes, "*" means all nodes
            max_depth: Maximum depth of graph traversal
            max_nodes: Maximum number of nodes to return

        Returns:
            GraphData compatible with frontend KnowledgeGraph component
        """
        import re
        from app.graph.schemas import GraphData

        settings = get_settings()
        if not settings.lightrag_enabled:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled")

        # Input validation
        node_label = (node_label or "*").strip()[:256]
        if node_label != "*" and not re.match(r"^[A-Za-z0-9_:\-\s]+$", node_label):
            raise ApiException(status_code=422, code=42200, message="Invalid node label format")
        max_depth = max(1, min(10, max_depth))
        max_nodes = max(1, min(5000, max_nodes))

        timeout_sec = float(getattr(settings, "lightrag_query_timeout_sec", 30.0) or 30.0)
        max_concurrency = int(getattr(settings, "lightrag_query_max_concurrency", 1) or 1)
        sem = _get_query_semaphore(max_concurrency)

        acquired = await _acquire_query_semaphore(sem, timeout_sec=timeout_sec)
        if not acquired:
            raise ApiException(status_code=504, code=50400, message="LightRAG graph timeout (queue)")

        try:
            with anyio.fail_after(timeout_sec):
                raw_kg = await anyio.to_thread.run_sync(
                    partial(
                        _call_rag_get_knowledge_graph_sync,
                        node_label=node_label,
                        max_depth=max_depth,
                        max_nodes=max_nodes,
                        timeout_sec=timeout_sec,
                    ),
                    abandon_on_cancel=True,
                )
        except TimeoutError as e:
            logger.warning("lightrag graph timeout", extra={"timeout_sec": timeout_sec})
            raise ApiException(status_code=504, code=50400, message="LightRAG graph timeout") from e
        except LightRagNotEnabledError as e:
            raise ApiException(status_code=404, code=40410, message="LightRAG is not enabled") from e
        except LightRagDependencyError as e:
            logger.exception("lightrag dependency missing")
            raise ApiException(status_code=500, code=50010, message="LightRAG dependency missing") from e
        except Exception as e:
            logger.exception("lightrag graph failed", extra={"detail": str(e)})
            raise ApiException(status_code=500, code=50012, message="LightRAG graph failed") from e
        finally:
            try:
                sem.release()
            except Exception:
                pass

        return _knowledge_graph_to_graph_data(raw_kg, db=db)
