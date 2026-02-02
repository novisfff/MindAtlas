"""Outbox worker for consuming index events.

Usage:
    python -m app.lightrag.worker

The worker polls the outbox table for pending events and processes them
using the configured indexer. It supports graceful shutdown via SIGINT/SIGTERM.
"""
from __future__ import annotations

import logging
import signal
import socket
import os
from datetime import datetime
from threading import Event
from typing import Callable

from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal
from app.entry.models import Entry
import app.entry_type.models  # noqa: F401
import app.tag.models  # noqa: F401
from app.lightrag.documents import build_document_payload, should_index
from app.lightrag.indexer import Indexer
from app.lightrag.outbox_repo import OutboxRepo, compute_backoff
from app.lightrag.types import IndexRequest, IndexResult, WorkerConfig

logger = logging.getLogger(__name__)

def _index_signature(entry: Entry) -> tuple[str, str | None, str | None]:
    """Signature of fields that should trigger re-indexing.

    Product policy: ignore type/tags-only changes (metadata churn).
    """
    return (entry.title, entry.summary, entry.content)


def build_worker_config() -> WorkerConfig:
    """Build worker configuration from settings."""
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"

    return WorkerConfig(
        enabled=settings.lightrag_worker_enabled,
        poll_interval_ms=settings.lightrag_worker_poll_interval_ms,
        batch_size=settings.lightrag_worker_batch_size,
        max_attempts=settings.lightrag_worker_max_attempts,
        lock_ttl_sec=settings.lightrag_worker_lock_ttl_sec,
        worker_id=worker_id,
    )


class Worker:
    """Outbox worker that polls and processes index events."""

    def __init__(
        self,
        cfg: WorkerConfig,
        *,
        indexer: Indexer,
        session_factory: Callable[[], object] | None = None,
    ) -> None:
        self.cfg = cfg
        self.indexer = indexer
        self.session_factory = session_factory or SessionLocal

    def run_forever(self, stop_event: Event) -> int:
        """Run the worker loop until stop_event is set.

        Args:
            stop_event: Threading event to signal shutdown

        Returns:
            Exit code (0 for normal shutdown)
        """
        logger.info(
            "worker starting",
            extra={
                "worker_id": self.cfg.worker_id,
                "poll_interval_ms": self.cfg.poll_interval_ms,
                "batch_size": self.cfg.batch_size,
                "max_attempts": self.cfg.max_attempts,
                "lock_ttl_sec": self.cfg.lock_ttl_sec,
            },
        )

        while not stop_event.is_set():
            try:
                processed = self.run_once()
                if processed == 0:
                    # No work, wait before next poll
                    stop_event.wait(self.cfg.poll_interval_ms / 1000.0)
            except Exception:
                logger.exception("worker run_once error")
                # Wait before retrying to avoid tight error loop
                stop_event.wait(self.cfg.poll_interval_ms / 1000.0)

        logger.info("worker stopped", extra={"worker_id": self.cfg.worker_id})
        return 0

    def run_once(self) -> int:
        """Run one iteration of the worker loop.

        Returns:
            Number of messages processed in this iteration
        """
        now = utcnow()
        db = self.session_factory()

        try:
            repo = OutboxRepo(db, worker_id=self.cfg.worker_id)

            # Claim batch of messages
            result = repo.claim_batch(
                now=now,
                batch_size=self.cfg.batch_size,
                worker_id=self.cfg.worker_id,
                lock_ttl_sec=self.cfg.lock_ttl_sec,
                max_attempts=self.cfg.max_attempts,
            )

            if not result.claimed:
                return 0

            logger.info("claimed batch (worker_id=%s count=%s)", self.cfg.worker_id, len(result.claimed))

            # Process each message
            for outbox in result.claimed:
                self._process_one(db, repo, outbox, now)

            return len(result.claimed)

        finally:
            db.close()

    def _process_one(self, db, repo: OutboxRepo, outbox, now: datetime) -> None:
        """Process a single outbox message.

        Args:
            db: Database session
            repo: Outbox repository
            outbox: The outbox message to process
            now: Current timestamp
        """
        try:
            # For upsert, check if event is stale (防乱序护栏)
            payload = None
            effective_op = outbox.op
            sig_before: tuple[str, str | None, str | None] | None = None

            if outbox.op == "upsert":
                entry = db.query(Entry).filter(Entry.id == outbox.entry_id).first()

                if entry is None:
                    # Entry deleted, execute delete_document to clean up any index residue
                    logger.info(
                        "entry deleted, cleaning up index",
                        extra={
                            "outbox_id": str(outbox.id),
                            "entry_id": str(outbox.entry_id),
                        },
                    )
                    effective_op = "delete"
                else:
                    if (
                        outbox.entry_updated_at is not None
                        and entry.updated_at is not None
                        and entry.updated_at > outbox.entry_updated_at
                    ):
                        # If a newer outbox event exists for this entry, skip this stale one.
                        # Otherwise (coalesced mode), process it against current entry state.
                        from app.lightrag.models import EntryIndexOutbox  # local import to avoid cycles

                        newer_exists = (
                            db.query(EntryIndexOutbox)
                            .filter(
                                EntryIndexOutbox.entry_id == outbox.entry_id,
                                EntryIndexOutbox.id != outbox.id,
                                EntryIndexOutbox.op == "upsert",
                                EntryIndexOutbox.status.in_(["pending", "processing"]),
                                EntryIndexOutbox.created_at > outbox.created_at,
                            )
                            .first()
                            is not None
                        )

                        if newer_exists:
                            logger.info(
                                "skipping stale upsert (newer outbox exists)",
                                extra={
                                    "outbox_id": str(outbox.id),
                                    "entry_id": str(outbox.entry_id),
                                    "outbox_updated_at": outbox.entry_updated_at.isoformat(),
                                    "entry_updated_at": entry.updated_at.isoformat(),
                                },
                            )
                            repo.mark_succeeded(outbox_id=outbox.id)
                            return

                    sig_before = _index_signature(entry)
                    payload = build_document_payload(entry=entry, entry_updated_at=entry.updated_at)
                    if not should_index(payload):
                        # EntryType flags disable indexing: translate upsert -> delete (cleanup residue).
                        logger.info(
                            "entry type disables indexing, cleaning up index",
                            extra={
                                "outbox_id": str(outbox.id),
                                "entry_id": str(outbox.entry_id),
                                "type_id": str(payload.type_id),
                                "graph_enabled": payload.graph_enabled,
                                "ai_enabled": payload.ai_enabled,
                                "type_enabled": payload.type_enabled,
                            },
                        )
                        effective_op = "delete"
                        payload = None

            # Build index request
            req = IndexRequest(
                outbox_id=outbox.id,
                entry_id=outbox.entry_id,
                op=effective_op,
                entry_updated_at=outbox.entry_updated_at,
                attempts=outbox.attempts,
                payload=payload,
            )

            # Call indexer
            result = self.indexer.handle(req)

            if result.ok:
                # Coalesce rapid successive edits: if entry's index-signature changed while we were processing,
                # requeue the same outbox message instead of creating another row.
                if effective_op == "upsert" and sig_before is not None:
                    current = db.query(Entry).filter(Entry.id == outbox.entry_id).first()
                    if current is not None and _index_signature(current) != sig_before:
                        repo.mark_pending(outbox_id=outbox.id, next_available_at=utcnow())
                        logger.info(
                            "requeued after concurrent update",
                            extra={"outbox_id": str(outbox.id), "entry_id": str(outbox.entry_id)},
                        )
                        return

                repo.mark_succeeded(outbox_id=outbox.id)
                logger.info(
                    "index succeeded",
                    extra={
                        "outbox_id": str(outbox.id),
                        "entry_id": str(outbox.entry_id),
                        "op": effective_op,
                    },
                )
            else:
                self._handle_failure(
                    repo,
                    outbox,
                    result.detail or "indexer returned not ok",
                    retryable=bool(result.retryable),
                )

        except Exception as e:
            logger.exception(
                "index error",
                extra={
                    "outbox_id": str(outbox.id),
                    "entry_id": str(outbox.entry_id),
                },
            )
            self._handle_failure(repo, outbox, str(e), retryable=True)

    def _handle_failure(self, repo: OutboxRepo, outbox, error_message: str, *, retryable: bool) -> None:
        """Handle a failed indexing attempt.

        Args:
            repo: Outbox repository
            outbox: The failed outbox message
            error_message: Error description
            retryable: Whether retry makes sense (non-retryable => mark dead immediately)
        """
        if not retryable:
            repo.mark_dead(outbox_id=outbox.id, error_message=error_message)
            logger.warning(
                "index dead (non-retryable) outbox_id=%s entry_id=%s attempts=%s error=%s",
                str(outbox.id),
                str(outbox.entry_id),
                outbox.attempts,
                (error_message or "")[:200],
            )
            return

        if outbox.attempts >= self.cfg.max_attempts:
            # Exceeded max attempts, mark as dead
            repo.mark_dead(
                outbox_id=outbox.id,
                error_message=error_message,
            )
            logger.warning(
                "index dead (max attempts exceeded) outbox_id=%s entry_id=%s attempts=%s error=%s",
                str(outbox.id),
                str(outbox.entry_id),
                outbox.attempts,
                (error_message or "")[:200],
            )
        else:
            # Retry with backoff
            backoff = compute_backoff(outbox.attempts)
            next_available = utcnow() + backoff

            repo.mark_retry(
                outbox_id=outbox.id,
                next_available_at=next_available,
                error_message=error_message,
            )
            logger.info(
                "index retry scheduled outbox_id=%s entry_id=%s attempts=%s next_available_at=%s error=%s",
                str(outbox.id),
                str(outbox.entry_id),
                outbox.attempts,
                next_available.isoformat(),
                (error_message or "")[:200],
            )


def main() -> None:
    """Main entry point for the worker process."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = build_worker_config()

    if not cfg.enabled:
        logger.warning(
            "worker disabled (LIGHTRAG_WORKER_ENABLED=false), exiting"
        )
        raise SystemExit(0)

    # Validate configuration
    if cfg.poll_interval_ms < 200:
        logger.warning("poll_interval_ms < 200, using 200")
        cfg = WorkerConfig(
            enabled=cfg.enabled,
            poll_interval_ms=200,
            batch_size=cfg.batch_size,
            max_attempts=cfg.max_attempts,
            lock_ttl_sec=cfg.lock_ttl_sec,
            worker_id=cfg.worker_id,
        )

    indexer = Indexer()
    worker = Worker(cfg, indexer=indexer)

    stop_event = Event()

    def handle_signal(signum, _frame):
        logger.info("signal received, initiating shutdown", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    exit_code = worker.run_forever(stop_event)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
