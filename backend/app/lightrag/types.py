"""Type definitions for LightRAG worker and indexer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

# Status constants for outbox events
OutboxStatus = Literal["pending", "processing", "succeeded", "dead"]
OutboxOp = Literal["upsert", "delete"]

IndexErrorKind = Literal["transient", "config", "dependency", "payload", "unknown"]


@dataclass(frozen=True)
class DocumentPayload:
    """Normalized document payload passed from Worker -> Indexer (契约升级).

    Worker is responsible for reading Entry/EntryType/Tags from DB and
    building this payload. Indexer is DB-free and only uses this payload.
    """

    entry_id: UUID
    entry_updated_at: datetime | None

    # EntryType metadata (used for audit/debugging; decision is made in worker)
    type_id: UUID
    type_code: str | None
    type_name: str | None
    type_enabled: bool
    graph_enabled: bool
    ai_enabled: bool

    # Entry content
    title: str
    summary: str | None
    content: str | None
    tags: list[str] = field(default_factory=list)
    tag_ids: list[UUID] = field(default_factory=list)

    # Final rendered text for LightRAG ingestion
    text: str = ""


@dataclass(frozen=True)
class IndexResult:
    """Result of an indexing operation."""

    ok: bool
    detail: str | None = None
    retryable: bool = True
    error_kind: IndexErrorKind | None = None


@dataclass(frozen=True)
class IndexRequest:
    """Request payload for indexer."""

    outbox_id: UUID
    entry_id: UUID
    op: str  # "upsert" | "delete"
    entry_updated_at: datetime | None
    attempts: int
    payload: DocumentPayload | None = None


@dataclass(frozen=True)
class WorkerConfig:
    """Configuration for the outbox worker."""

    enabled: bool
    poll_interval_ms: int
    batch_size: int
    max_attempts: int
    lock_ttl_sec: int
    worker_id: str
