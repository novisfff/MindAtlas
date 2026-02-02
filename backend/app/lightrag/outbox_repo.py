"""Outbox repository for claim/ack/retry/dead operations."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.lightrag.models import EntryIndexOutbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimResult:
    """Result of claiming outbox messages."""

    claimed: list[EntryIndexOutbox]


def compute_backoff(
    attempts: int,
    base_sec: float = 2.0,
    cap_sec: float = 60.0,
) -> timedelta:
    """Compute exponential backoff with jitter.

    Args:
        attempts: Current attempt count (already incremented)
        base_sec: Base delay in seconds
        cap_sec: Maximum delay cap in seconds

    Returns:
        timedelta for the next available_at
    """
    delay = min(cap_sec, base_sec * (2 ** min(attempts - 1, 10)))
    jitter = random.uniform(0, delay * 0.1)
    return timedelta(seconds=delay + jitter)


class OutboxRepo:
    """Repository for outbox operations with concurrency-safe claiming."""

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
        """Claim a batch of pending outbox messages for processing.

        Uses FOR UPDATE SKIP LOCKED to safely claim messages in a concurrent environment.
        Messages are transitioned from pending to processing within the same transaction.

        Args:
            now: Current timestamp
            batch_size: Maximum number of messages to claim
            worker_id: Identifier for this worker instance
            lock_ttl_sec: Lock TTL in seconds for expired lock detection
            max_attempts: Maximum attempts before a message is considered dead

        Returns:
            ClaimResult containing the list of claimed messages
        """
        lock_deadline = now - timedelta(seconds=lock_ttl_sec)

        # Query for claimable messages:
        # 1. pending and available
        # 2. OR processing but lock expired (crash recovery)
        query = (
            self.db.query(EntryIndexOutbox)
            .filter(
                EntryIndexOutbox.attempts < max_attempts,
                EntryIndexOutbox.available_at <= now,
                or_(
                    # Normal pending messages
                    EntryIndexOutbox.status == "pending",
                    # Processing with expired lock (worker crashed)
                    and_(
                        EntryIndexOutbox.status == "processing",
                        or_(
                            EntryIndexOutbox.locked_at.is_(None),
                            EntryIndexOutbox.locked_at <= lock_deadline,
                        ),
                    ),
                ),
            )
            .order_by(
                EntryIndexOutbox.available_at.asc(),
                EntryIndexOutbox.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )

        rows = query.all()

        # Update claimed rows to processing status
        for row in rows:
            row.status = "processing"
            row.locked_at = now
            row.locked_by = worker_id
            row.attempts = (row.attempts or 0) + 1

        self.db.commit()

        return ClaimResult(claimed=rows)

    def mark_succeeded(self, *, outbox_id: UUID) -> bool:
        """Mark an outbox message as successfully processed.

        Only updates if this worker still owns the lock (防止 late-ack 竞态).

        Args:
            outbox_id: ID of the outbox message

        Returns:
            True if the message was updated, False if lock was lost
        """
        # Only update if we still own the lock
        filters = [EntryIndexOutbox.id == outbox_id, EntryIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(EntryIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(EntryIndexOutbox).filter(*filters).first()
        if row:
            row.status = "succeeded"
            row.locked_at = None
            row.locked_by = None
            row.last_error = None
            self.db.commit()
            return True

        logger.warning(
            "mark_succeeded failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False

    def mark_retry(
        self,
        *,
        outbox_id: UUID,
        next_available_at: datetime,
        error_message: str,
    ) -> bool:
        """Mark an outbox message for retry with backoff.

        Only updates if this worker still owns the lock (防止 late-ack 竞态).

        Args:
            outbox_id: ID of the outbox message
            next_available_at: When the message should become available again
            error_message: Error message to store (will be truncated)

        Returns:
            True if the message was updated, False if lock was lost
        """
        filters = [EntryIndexOutbox.id == outbox_id, EntryIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(EntryIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(EntryIndexOutbox).filter(*filters).first()
        if row:
            row.status = "pending"
            row.locked_at = None
            row.locked_by = None
            row.available_at = next_available_at
            # Truncate error message to prevent DB bloat
            row.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning(
            "mark_retry failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False

    def mark_pending(self, *, outbox_id: UUID, next_available_at: datetime) -> bool:
        """Requeue an outbox message without recording an error.

        Only updates if this worker still owns the lock (防止 late-ack 竞态).
        Useful for coalescing rapid successive updates into a single message.
        """
        filters = [EntryIndexOutbox.id == outbox_id, EntryIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(EntryIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(EntryIndexOutbox).filter(*filters).first()
        if row:
            row.status = "pending"
            row.locked_at = None
            row.locked_by = None
            row.available_at = next_available_at
            # This is not a failure; reset attempts so it won't be throttled by max_attempts.
            row.attempts = 0
            row.last_error = None
            self.db.commit()
            return True

        logger.warning(
            "mark_pending failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False

    def mark_dead(self, *, outbox_id: UUID, error_message: str) -> bool:
        """Mark an outbox message as dead (exceeded max attempts).

        Only updates if this worker still owns the lock (防止 late-ack 竞态).

        Args:
            outbox_id: ID of the outbox message
            error_message: Final error message

        Returns:
            True if the message was updated, False if lock was lost
        """
        filters = [EntryIndexOutbox.id == outbox_id, EntryIndexOutbox.status == "processing"]
        if self.worker_id:
            filters.append(EntryIndexOutbox.locked_by == self.worker_id)

        row = self.db.query(EntryIndexOutbox).filter(*filters).first()
        if row:
            row.status = "dead"
            row.locked_at = None
            row.locked_by = None
            row.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning(
            "mark_dead failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False
