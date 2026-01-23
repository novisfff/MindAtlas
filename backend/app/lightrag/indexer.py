"""LightRAG indexer for Entry upsert/delete.

Phase 5: refactored to use shared manager for LightRAG initialization.

Contract:
- Worker builds DocumentPayload (text + metadata) and passes via IndexRequest.payload.
- Indexer is DB-free and only talks to LightRAG + external storages.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.lightrag.errors import LightRagConfigError, LightRagDependencyError, LightRagNotEnabledError
from app.lightrag.manager import get_rag
from app.lightrag.types import IndexRequest, IndexResult

logger = logging.getLogger(__name__)


# Keep legacy error classes for backward compatibility with existing code
IndexerConfigError = LightRagConfigError
IndexerDependencyError = LightRagDependencyError


class Indexer:
    """LightRAG-backed indexer (sync entry point, async internally)."""

    def handle(self, req: IndexRequest) -> IndexResult:
        """Handle an outbox event (upsert/delete).

        Behavior:
        - Invalid op: non-retryable failure (dead immediately).
        - LIGHTRAG_ENABLED=false: fast skip (ok=True), to prevent outbox backlog.
        - upsert requires req.payload (built by worker).
        """
        if req.op not in ("upsert", "delete"):
            return IndexResult(
                ok=False,
                retryable=False,
                error_kind="payload",
                detail=f"invalid op: {req.op}",
            )

        settings = get_settings()
        if not settings.lightrag_enabled:
            return IndexResult(ok=True, retryable=False, detail="skipped: LIGHTRAG_ENABLED=false")

        try:
            rag = get_rag()
        except LightRagNotEnabledError as e:
            return IndexResult(ok=True, retryable=False, detail=str(e))
        except LightRagDependencyError as e:
            return IndexResult(ok=False, retryable=False, error_kind="dependency", detail=str(e))
        except LightRagConfigError as e:
            return IndexResult(ok=False, retryable=False, error_kind="config", detail=str(e))
        except Exception as e:
            return IndexResult(ok=False, retryable=True, error_kind="unknown", detail=f"init failed: {e}")

        if req.op == "delete":
            return self._delete_by_entry_id(rag, entry_id=str(req.entry_id))

        # upsert
        if req.payload is None:
            return IndexResult(
                ok=False,
                retryable=False,
                error_kind="payload",
                detail="missing payload for upsert",
            )

        try:
            entry_id = str(req.entry_id)
            # Best-effort: ensure both doc_id and file_path are the Entry UUID, so query_llm chunks
            # can be mapped back to Entries (some upstream responses only include file_path).
            try:
                track_id = rag.insert(req.payload.text, ids=[entry_id], file_paths=[entry_id])
            except TypeError:
                try:
                    track_id = rag.insert(req.payload.text, ids=[entry_id], file_path=entry_id)
                except TypeError:
                    track_id = rag.insert(req.payload.text, ids=[entry_id])
            return IndexResult(ok=True, detail=f"indexed: track_id={track_id}")
        except Exception as e:
            return IndexResult(ok=False, retryable=True, error_kind="transient", detail=str(e))

    def _delete_by_entry_id(self, rag, *, entry_id: str) -> IndexResult:
        doc_id = entry_id
        try:
            from app.lightrag.runtime import get_lightrag_runtime

            runtime = get_lightrag_runtime()

            def _do() -> None:
                runtime.loop.run_until_complete(rag.adelete_by_doc_id(doc_id))

            runtime.call(_do, timeout_sec=60.0)
            return IndexResult(ok=True, detail=f"deleted: doc_id={doc_id}")
        except Exception as e:
            # Deletion should be idempotent; treat failures as retryable by default.
            return IndexResult(ok=False, retryable=True, error_kind="transient", detail=str(e))
