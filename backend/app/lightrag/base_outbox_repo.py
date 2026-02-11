"""Generic outbox repository primitives for LightRAG outbox tables."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Generic, Type, TypeVar, cast
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class ClaimResult(Generic[ModelT]):
    """Result of claiming outbox messages."""
    claimed: list[ModelT]


def compute_backoff(
    attempts: int,
    base_sec: float = 2.0,
    cap_sec: float = 60.0,
) -> timedelta:
    """Compute exponential backoff with jitter."""
    delay = min(cap_sec, base_sec * (2 ** min(attempts - 1, 10)))
    jitter = random.uniform(0, delay * 0.1)
    return timedelta(seconds=delay + jitter)


class BaseOutboxRepo(Generic[ModelT]):
    """Generic repository for outbox operations with concurrency-safe claiming."""

    model: Type[ModelT]

    def __init__(self, db: Session, *, worker_id: str | None = None) -> None:
        self.db = db
        self.worker_id = worker_id

    def _processing_filters(self, *, outbox_id: UUID) -> list[Any]:
        model = cast(Any, self.model)
        filters = [model.id == outbox_id, model.status == "processing"]
        if self.worker_id:
            filters.append(model.locked_by == self.worker_id)
        return filters

    def claim_batch(
        self,
        *,
        now: datetime,
        batch_size: int,
        worker_id: str,
        lock_ttl_sec: int,
        max_attempts: int,
    ) -> ClaimResult[ModelT]:
        lock_deadline = now - timedelta(seconds=lock_ttl_sec)
        model = cast(Any, self.model)

        query = (
            self.db.query(self.model)
            .filter(
                model.attempts < max_attempts,
                model.available_at <= now,
                or_(
                    model.status == "pending",
                    and_(
                        model.status == "processing",
                        or_(
                            model.locked_at.is_(None),
                            model.locked_at <= lock_deadline,
                        ),
                    ),
                ),
            )
            .order_by(model.available_at.asc(), model.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )

        rows = cast(list[ModelT], query.all())
        for row in rows:
            row_ref = cast(Any, row)
            row_ref.status = "processing"
            row_ref.locked_at = now
            row_ref.locked_by = worker_id
            row_ref.attempts = (row_ref.attempts or 0) + 1

        self.db.commit()
        return ClaimResult(claimed=rows)

    def mark_succeeded(self, *, outbox_id: UUID) -> bool:
        row = self.db.query(self.model).filter(
            *self._processing_filters(outbox_id=outbox_id)
        ).first()
        if row:
            row_ref = cast(Any, row)
            row_ref.status = "succeeded"
            row_ref.locked_at = None
            row_ref.locked_by = None
            row_ref.last_error = None
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
        row = self.db.query(self.model).filter(
            *self._processing_filters(outbox_id=outbox_id)
        ).first()
        if row:
            row_ref = cast(Any, row)
            row_ref.status = "pending"
            row_ref.locked_at = None
            row_ref.locked_by = None
            row_ref.available_at = next_available_at
            row_ref.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning(
            "mark_retry failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False

    def mark_dead(self, *, outbox_id: UUID, error_message: str) -> bool:
        row = self.db.query(self.model).filter(
            *self._processing_filters(outbox_id=outbox_id)
        ).first()
        if row:
            row_ref = cast(Any, row)
            row_ref.status = "dead"
            row_ref.locked_at = None
            row_ref.locked_by = None
            row_ref.last_error = error_message[:4000] if error_message else None
            self.db.commit()
            return True

        logger.warning(
            "mark_dead failed: lock lost or message not found",
            extra={"outbox_id": str(outbox_id), "worker_id": self.worker_id},
        )
        return False
