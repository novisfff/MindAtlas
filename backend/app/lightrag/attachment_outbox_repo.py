"""Outbox repository for LightRAG attachment index operations."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.lightrag.models import AttachmentIndexOutbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimResult:
    claimed: list[AttachmentIndexOutbox]


def compute_backoff(
    attempts: int,
    base_sec: float = 5.0,
    cap_sec: float = 300.0,
) -> timedelta:
    delay = min(cap_sec, base_sec * (2 ** min(attempts - 1, 10)))
    jitter = random.uniform(0, delay * 0.1)
    return timedelta(seconds=delay + jitter)


class AttachmentOutboxRepo:
    def __init__(self, db: Session, *, worker_id: str | None = None) -> None:
        self.db = db
        self.worker_id = worker_id

    def claim_batch(
        self,
        *,
        now: datetime,
        batch_size: int,
        worker_id: str,
        lock_ttl_sec: int,
        max_attempts: int,
    ) -> ClaimResult:
        lock_deadline = now - timedelta(seconds=lock_ttl_sec)

        query = (
            self.db.query(AttachmentIndexOutbox)
            .filter(
                AttachmentIndexOutbox.attempts < max_attempts,
                AttachmentIndexOutbox.available_at <= now,
                or_(
                    AttachmentIndexOutbox.status == "pending",
                    and_(
                        AttachmentIndexOutbox.status == "processing",
                        or_(
                            AttachmentIndexOutbox.locked_at.is_(None),
                            AttachmentIndexOutbox.locked_at <= lock_deadline,
                        ),
                    ),
                ),
            )
            .order_by(
                AttachmentIndexOutbox.available_at.asc(),
                AttachmentIndexOutbox.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )

        rows = query.all()
        for row in rows:
            row.status = "processing"
            row.locked_at = now
            row.locked_by = worker_id
            row.attempts = (row.attempts or 0) + 1

        self.db.commit()
        return ClaimResult(claimed=rows)

    def mark_succeeded(self, *, outbox_id: UUID) -> bool:
        filters = [AttachmentIndexOutbox.id == outbox_id, AttachmentIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(AttachmentIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentIndexOutbox).filter(*filters).first()
        if row:
            row.status = "succeeded"
            row.locked_at = None
            row.locked_by = None
            row.last_error = None
            self.db.commit()
            return True

        logger.warning("mark_succeeded failed", extra={"outbox_id": str(outbox_id)})
        return False

    def mark_retry(
        self,
        *,
        outbox_id: UUID,
        next_available_at: datetime,
        error_message: str,
    ) -> bool:
        filters = [AttachmentIndexOutbox.id == outbox_id, AttachmentIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(AttachmentIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentIndexOutbox).filter(*filters).first()
        if row:
            row.status = "pending"
            row.locked_at = None
            row.locked_by = None
            row.available_at = next_available_at
            row.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning("mark_retry failed", extra={"outbox_id": str(outbox_id)})
        return False

    def mark_dead(self, *, outbox_id: UUID, error_message: str) -> bool:
        filters = [AttachmentIndexOutbox.id == outbox_id, AttachmentIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(AttachmentIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentIndexOutbox).filter(*filters).first()
        if row:
            row.status = "dead"
            row.locked_at = None
            row.locked_by = None
            row.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning("mark_dead failed", extra={"outbox_id": str(outbox_id)})
        return False

