"""Outbox repository for LightRAG attachment index operations."""
from __future__ import annotations

from datetime import timedelta

from app.lightrag.base_outbox_repo import (
    BaseOutboxRepo,
    ClaimResult,
    compute_backoff as base_compute_backoff,
)
from app.lightrag.models import AttachmentIndexOutbox


def compute_backoff(
    attempts: int,
    base_sec: float = 5.0,
    cap_sec: float = 300.0,
) -> timedelta:
    """Compute exponential backoff with jitter."""
    return base_compute_backoff(attempts, base_sec, cap_sec)


class AttachmentOutboxRepo(BaseOutboxRepo[AttachmentIndexOutbox]):
    """Repository for attachment index outbox operations."""

    model = AttachmentIndexOutbox
