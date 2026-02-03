"""ORM models for LightRAG outbox and metadata."""
from __future__ import annotations

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


class EntryIndexOutbox(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "entry_index_outbox"

    entry_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    locked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_outbox_pending_available", "status", "available_at"),
        Index("idx_outbox_entry_id", "entry_id"),
    )


class AttachmentIndexOutbox(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attachment_index_outbox"

    # Intentionally no FK to Attachment: we want deletion indexing to survive even if the row is gone.
    attachment_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    locked_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_attachment_outbox_pending_available", "status", "available_at"),
        Index("idx_attachment_outbox_attachment_id", "attachment_id"),
        Index("idx_attachment_outbox_entry_id", "entry_id"),
    )
