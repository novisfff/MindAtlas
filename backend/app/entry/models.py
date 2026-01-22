from __future__ import annotations

import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base
from app.entry_type.models import EntryType
from app.tag.models import Tag


class TimeMode(str, enum.Enum):
    NONE = "NONE"
    POINT = "POINT"
    RANGE = "RANGE"


# Association table for Entry-Tag many-to-many relationship
entry_tag = Table(
    "entry_tag",
    Base.metadata,
    Column("entry_id", UUID(as_uuid=True), ForeignKey("entry.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)


class Entry(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "entry"

    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    type_id = Column(UUID(as_uuid=True), ForeignKey("entry_type.id"), nullable=False)
    time_mode = Column(Enum(TimeMode), nullable=False, default=TimeMode.NONE)
    time_at = Column(DateTime(timezone=True), nullable=True)
    time_from = Column(DateTime(timezone=True), nullable=True)
    time_to = Column(DateTime(timezone=True), nullable=True)
    summary = Column(Text, nullable=True)

    # Relationships
    type = relationship(EntryType, lazy="joined")
    tags = relationship(Tag, secondary=entry_tag, lazy="joined")
