"""Attachment parse worker for document text extraction.

Usage:
    python -m app.attachment.worker
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    enabled: bool
    poll_interval_ms: int
    batch_size: int
    max_attempts: int
    lock_ttl_sec: int
    worker_id: str


def build_worker_config() -> WorkerConfig:
    settings = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    return WorkerConfig(
        enabled=settings.docling_worker_enabled,
        poll_interval_ms=settings.docling_worker_poll_interval_ms,
        batch_size=settings.docling_worker_batch_size,
        max_attempts=settings.docling_worker_max_attempts,
        lock_ttl_sec=settings.docling_worker_lock_ttl_sec,
        worker_id=worker_id,
    )


class Worker:
    def __init__(
        self,
        cfg: WorkerConfig,
        *,
        session_factory: Callable[[], object] | None = None,
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory or SessionLocal

    def run_forever(self, stop_event: Event) -> int:
        logger.info("parse worker starting", extra={"worker_id": self.cfg.worker_id})

        while not stop_event.is_set():
            try:
                processed = self.run_once()
                if processed == 0:
                    stop_event.wait(self.cfg.poll_interval_ms / 1000.0)
            except Exception:
                logger.exception("worker run_once error")
                stop_event.wait(self.cfg.poll_interval_ms / 1000.0)

        logger.info("parse worker stopped", extra={"worker_id": self.cfg.worker_id})
        return 0

    def run_once(self) -> int:
        from app.attachment.models import Attachment, AttachmentParseOutbox
        from app.attachment.outbox_repo import AttachmentParseOutboxRepo, compute_backoff

        now = utcnow()
        db = self.session_factory()

        try:
            repo = AttachmentParseOutboxRepo(db, worker_id=self.cfg.worker_id)
            result = repo.claim_batch(
                now=now,
                batch_size=self.cfg.batch_size,
                worker_id=self.cfg.worker_id,
                lock_ttl_sec=self.cfg.lock_ttl_sec,
                max_attempts=self.cfg.max_attempts,
            )

            if not result.claimed:
                return 0

            for outbox in result.claimed:
                self._process_one(db, repo, outbox, now)

            return len(result.claimed)
        finally:
            db.close()

    def _process_one(self, db, repo, outbox, now) -> None:
        from app.attachment.models import Attachment
        from app.attachment.outbox_repo import compute_backoff
        from app.attachment.parser import parse_document, ParseError
        from app.common.storage import get_minio_client

        attachment = db.query(Attachment).filter(
            Attachment.id == outbox.attachment_id
        ).first()

        if not attachment:
            repo.mark_succeeded(outbox_id=outbox.id)
            return

        # Set status to 'processing' for UI visibility
        attachment.parse_status = "processing"
        db.commit()

        try:
            client, bucket = get_minio_client()
            file_ext = Path(attachment.original_filename).suffix
            with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as tmp:
                tmp_path = tmp.name
                response = client.get_object(bucket, attachment.file_path)
                for chunk in response.stream(32 * 1024):
                    tmp.write(chunk)
                response.close()
                response.release_conn()
        except Exception as e:
            self._handle_error(db, repo, outbox, attachment, str(e), now)
            return

        try:
            text = parse_document(tmp_path, attachment.content_type)
            attachment.parsed_text = text
            attachment.parse_status = "completed"
            attachment.parsed_at = now
            attachment.parse_last_error = None
            db.commit()

            repo.mark_succeeded(outbox_id=outbox.id)
            self._enqueue_attachment_index(db, attachment_id=outbox.attachment_id, entry_id=outbox.entry_id)
            logger.info("parse succeeded", extra={"attachment_id": str(outbox.attachment_id)})
        except ParseError as e:
            self._handle_error(db, repo, outbox, attachment, str(e), now, retryable=e.retryable)
        except Exception as e:
            self._handle_error(db, repo, outbox, attachment, str(e), now)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _handle_error(self, db, repo, outbox, attachment, error_msg: str, now, *, retryable: bool = True) -> None:
        from app.attachment.outbox_repo import compute_backoff

        attachment.parse_last_error = error_msg[:4000] if error_msg else None

        if not retryable or outbox.attempts >= self.cfg.max_attempts:
            attachment.parse_status = "failed"
            db.commit()
            repo.mark_dead(outbox_id=outbox.id, error_message=error_msg)
            logger.warning("parse failed permanently", extra={"attachment_id": str(outbox.attachment_id)})
        else:
            attachment.parse_status = "pending"
            db.commit()
            backoff = compute_backoff(outbox.attempts)
            repo.mark_retry(outbox_id=outbox.id, next_available_at=now + backoff, error_message=error_msg)
            logger.info("parse retry scheduled", extra={"attachment_id": str(outbox.attachment_id)})

    def _enqueue_attachment_index(self, db, *, attachment_id, entry_id) -> None:
        from app.lightrag.models import AttachmentIndexOutbox

        outbox = AttachmentIndexOutbox(
            attachment_id=attachment_id,
            entry_id=entry_id,
            op="upsert",
            status="pending",
        )
        db.add(outbox)
        db.commit()


def main() -> None:
    # Import all models to ensure SQLAlchemy relationships are resolved
    import app.entry.models  # noqa: F401
    import app.attachment.models  # noqa: F401

    logging.basicConfig(level=logging.INFO)

    cfg = build_worker_config()
    if not cfg.enabled:
        logger.info("parse worker disabled")
        return

    worker = Worker(cfg)
    stop_event = Event()

    def handle_signal(signum, _frame):
        logger.info("signal received", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    exit_code = worker.run_forever(stop_event)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
