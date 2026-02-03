"""Outbox repository for attachment parse operations."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.attachment.models import AttachmentParseOutbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimResult:
    claimed: list[AttachmentParseOutbox]


def compute_backoff(
    attempts: int,
    base_sec: float = 5.0,
    cap_sec: float = 300.0,
) -> timedelta:
    delay = min(cap_sec, base_sec * (2 ** min(attempts - 1, 10)))
    jitter = random.uniform(0, delay * 0.1)
    return timedelta(seconds=delay + jitter)


class AttachmentParseOutboxRepo:
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
            self.db.query(AttachmentParseOutbox)
            .filter(
                AttachmentParseOutbox.attempts < max_attempts,
                AttachmentParseOutbox.available_at <= now,
                or_(
                    AttachmentParseOutbox.status == "pending",
                    and_(
                        AttachmentParseOutbox.status == "processing",
                        or_(
                            AttachmentParseOutbox.locked_at.is_(None),
                            AttachmentParseOutbox.locked_at <= lock_deadline,
                        ),
                    ),
                ),
            )
            .order_by(
                AttachmentParseOutbox.available_at.asc(),
                AttachmentParseOutbox.created_at.asc(),
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
        filters = [
            AttachmentParseOutbox.id == outbox_id,
            AttachmentParseOutbox.status == "processing",
        ]
        if self.worker_id:
            filters.append(AttachmentParseOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentParseOutbox).filter(*filters).first()
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
        filters = [
            AttachmentParseOutbox.id == outbox_id,
            AttachmentParseOutbox.status == "processing",
        ]
        if self.worker_id:
            filters.append(AttachmentParseOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentParseOutbox).filter(*filters).first()
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
        filters = [
            AttachmentParseOutbox.id == outbox_id,
            AttachmentParseOutbox.status == "processing",
        ]
        if self.worker_id:
            filters.append(AttachmentParseOutbox.locked_by == self.worker_id)

        row = self.db.query(AttachmentParseOutbox).filter(*filters).first()
        if row:
            row.status = "dead"
            row.locked_at = None
            row.locked_by = None
            row.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning("mark_dead failed", extra={"outbox_id": str(outbox_id)})
        return False
