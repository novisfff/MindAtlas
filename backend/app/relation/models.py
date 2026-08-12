from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class RelationType(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "relation_type"

    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    inverse_name = Column(String(128), nullable=True)
    description = Column(String(512), nullable=True)
    color = Column(String(32), nullable=True)
    directed = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_relation_type_id", "id"),)


class Relation(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "relation"

    source_entry_id = Column(UUID(as_uuid=True), ForeignKey("entry.id", ondelete="CASCADE"), nullable=False)
    target_entry_id = Column(UUID(as_uuid=True), ForeignKey("entry.id", ondelete="CASCADE"), nullable=False)
    relation_type_id = Column(UUID(as_uuid=True), ForeignKey("relation_type.id"), nullable=False)
    description = Column(String(512), nullable=True)

    # Relationships
    source_entry = relationship("Entry", foreign_keys=[source_entry_id], lazy="select")
    target_entry = relationship("Entry", foreign_keys=[target_entry_id], lazy="select")
    relation_type = relationship("RelationType", lazy="select")

    __table_args__ = (
        Index("ix_relation_id", "id"),
        Index("ix_relation_relation_type_id", "relation_type_id"),
        Index("ix_relation_source_entry_id", "source_entry_id"),
        Index("ix_relation_target_entry_id", "target_entry_id"),
    )
