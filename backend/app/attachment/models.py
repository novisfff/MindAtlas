from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


class Attachment(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attachment"

    entry_id = Column(UUID(as_uuid=True), ForeignKey("entry.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    size = Column("file_size", BigInteger, nullable=False)
    content_type = Column("mime_type", String(128), nullable=False)

    # Knowledge graph indexing fields
    index_to_knowledge_graph = Column(Boolean, nullable=True, default=False)
    parse_status = Column(String(20), nullable=True)
    parsed_text = Column(Text, nullable=True)
    parsed_at = Column(DateTime(timezone=True), nullable=True)
    parse_last_error = Column(Text, nullable=True)

    # Relationships
    entry = relationship("Entry", lazy="joined")


class AttachmentParseOutbox(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attachment_parse_outbox"

    attachment_id = Column(UUID(as_uuid=True), ForeignKey("attachment.id", ondelete="CASCADE"), nullable=False)
    entry_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_attachment_parse_outbox_pending", "status", "available_at"),
        Index("idx_attachment_parse_outbox_attachment_id", "attachment_id"),
    )
