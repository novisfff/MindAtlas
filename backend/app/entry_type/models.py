from __future__ import annotations

from sqlalchemy import Boolean, Column, String

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class EntryType(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "entry_type"

    code = Column(String(64), nullable=False, unique=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    color = Column(String(32), nullable=True)
    icon = Column(String(64), nullable=True)
    graph_enabled = Column(Boolean, nullable=False, default=True)
    ai_enabled = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
