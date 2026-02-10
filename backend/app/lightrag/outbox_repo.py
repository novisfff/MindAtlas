"""Outbox repository for claim/ack/retry/dead operations."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from app.lightrag.base_outbox_repo import (
    BaseOutboxRepo,
    ClaimResult,
    compute_backoff as base_compute_backoff,
)
from app.lightrag.models import EntryIndexOutbox

logger = logging.getLogger(__name__)


def compute_backoff(
    attempts: int,
    base_sec: float = 2.0,
    cap_sec: float = 60.0,
) -> timedelta:
    """Compute exponential backoff with jitter."""
    return base_compute_backoff(attempts, base_sec, cap_sec)


class OutboxRepo(BaseOutboxRepo[EntryIndexOutbox]):
    """Repository for entry index outbox operations."""

    model = EntryIndexOutbox

    def mark_pending(self, *, outbox_id: UUID, next_available_at: datetime) -> bool:
        """Requeue an outbox message without recording an error.

        Useful for coalescing rapid successive updates into a single message.
        """
        row = self.db.query(self.model).filter(
            *self._processing_filters(outbox_id=outbox_id)
        ).first()
        if row:
            row.status = "pending"
            row.locked_at = None
            row.locked_by = None
            row.available_at = next_available_at
            row.attempts = 0
            row.last_error = None
            self.db.commit()
            return True

        logger.warning(
            "mark_pending failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False
