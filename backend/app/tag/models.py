from __future__ import annotations

from sqlalchemy import Column, Index, String

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class Tag(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tag"

    name = Column(String(128), nullable=False, unique=True, index=True)
    color = Column(String(32), nullable=True)
    description = Column(String(512), nullable=True)

    __table_args__ = (Index("ix_tag_id", "id"),)
