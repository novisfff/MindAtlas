from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class RelationType(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "relation_type"

    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    inverse_name = Column(String(128), nullable=True)
    description = Column(String(512), nullable=True)
    color = Column(String(32), nullable=True)
    directed = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)


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
