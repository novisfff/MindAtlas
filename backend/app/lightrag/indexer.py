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

        logger.info(
            "indexer init start (outbox_id=%s entry_id=%s op=%s attempts=%s)",
            str(req.outbox_id),
            str(req.entry_id),
            req.op,
            req.attempts,
        )
        try:
            rag = get_rag()
        except LightRagNotEnabledError as e:
            return IndexResult(ok=True, retryable=False, detail=str(e))
        except LightRagDependencyError as e:
            return IndexResult(ok=False, retryable=False, error_kind="dependency", detail=str(e))
        except LightRagConfigError as e:
            return IndexResult(ok=False, retryable=False, error_kind="config", detail=str(e))
        except Exception as e:
            msg = (str(e) or "").strip()
            if not msg:
                # Many timeout-related exceptions have empty string representations.
                msg = repr(e)
            return IndexResult(
                ok=False,
                retryable=True,
                error_kind="unknown",
                detail=f"init failed: {type(e).__name__}: {msg}",
            )
        finally:
            logger.info(
                "indexer init done (outbox_id=%s entry_id=%s op=%s)",
                str(req.outbox_id),
                str(req.entry_id),
                req.op,
            )

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
            track_id = self._upsert_by_entry_id(rag, entry_id=entry_id, text=req.payload.text)
            return IndexResult(ok=True, detail=f"indexed: track_id={track_id}")
        except Exception as e:
            return IndexResult(ok=False, retryable=True, error_kind="transient", detail=str(e))

    def upsert_attachment(self, *, attachment_id: str, entry_id: str, text: str) -> IndexResult:
        """Upsert an attachment document into LightRAG.

        Attachment docs use unique file_path to avoid conflicts with Entry docs and other attachments.
        - doc_id: "attachment:{attachment_id}" (unchanged for backward compatibility)
        - file_path: "{entry_id}/attachments/{attachment_id}" (unique per attachment)
        """
        from app.lightrag.source_ids import build_attachment_doc_id, build_attachment_file_path

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
            msg = (str(e) or "").strip() or repr(e)
            return IndexResult(ok=False, retryable=True, error_kind="unknown", detail=f"init failed: {type(e).__name__}: {msg}")

        try:
            from app.lightrag.runtime import get_lightrag_runtime

            runtime = get_lightrag_runtime()
            doc_id = build_attachment_doc_id(attachment_id)
            file_path = build_attachment_file_path(entry_id, attachment_id)

            # IMPORTANT: run LightRAG async ops on the dedicated runtime thread/loop.
            def _do() -> str:
                return self._replace_doc(rag, doc_id=doc_id, file_path=file_path, text=text, runtime=runtime)

            track_id = runtime.call(_do, timeout_sec=None)
            return IndexResult(ok=True, detail=f"indexed: track_id={track_id}")
        except Exception as e:
            return IndexResult(ok=False, retryable=True, error_kind="transient", detail=str(e))

    def delete_attachment(self, *, attachment_id: str) -> IndexResult:
        from app.lightrag.source_ids import build_attachment_doc_id

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
            msg = (str(e) or "").strip() or repr(e)
            return IndexResult(ok=False, retryable=True, error_kind="unknown", detail=f"init failed: {type(e).__name__}: {msg}")

        doc_id = build_attachment_doc_id(attachment_id)
        return self._delete_by_doc_id(rag, doc_id=doc_id)

    def _upsert_by_entry_id(self, rag, *, entry_id: str, text: str) -> str:
        """Upsert a document into LightRAG on the dedicated runtime loop.

        LightRAG's sync `insert()` uses a thread-local event loop (via
        `always_get_an_event_loop()`), which can mismatch the loop used to
        initialize storages (notably Neo4j async driver). To avoid
        \"got Future attached to a different loop\", run `ainsert()` on our
        process-wide LightRAG runtime loop.
        """
        from app.lightrag.runtime import get_lightrag_runtime

        runtime = get_lightrag_runtime()

        # Best-effort: ensure both doc_id and file_path are the Entry UUID, so query_llm chunks
        # can be mapped back to Entries (some upstream responses only include file_path).
        def _do() -> str:
            return self._replace_doc(rag, doc_id=entry_id, file_path=entry_id, text=text, runtime=runtime)

        return runtime.call(_do, timeout_sec=None)

    def _delete_by_entry_id(self, rag, *, entry_id: str) -> IndexResult:
        return self._delete_by_doc_id(rag, doc_id=entry_id)

    def _delete_by_doc_id(self, rag, *, doc_id: str) -> IndexResult:
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

    def _replace_doc(self, rag, *, doc_id: str, file_path: str, text: str, runtime=None) -> str:
        """Idempotent replace (best-effort delete, then insert)."""
        from app.lightrag.runtime import get_lightrag_runtime

        rt = runtime or get_lightrag_runtime()

        # LightRAG insert is not a true upsert: if the same document ID already exists it may be ignored.
        try:
            rt.loop.run_until_complete(rag.adelete_by_doc_id(doc_id))
        except Exception:
            pass
        try:
            return rt.loop.run_until_complete(rag.ainsert(text, ids=[doc_id], file_paths=[file_path]))
        except TypeError:
            try:
                return rt.loop.run_until_complete(rag.ainsert(text, ids=[doc_id], file_path=file_path))
            except TypeError:
                return rt.loop.run_until_complete(rag.ainsert(text, ids=[doc_id]))
