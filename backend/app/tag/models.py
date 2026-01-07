from __future__ import annotations

from sqlalchemy import Column, String

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class Tag(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tag"

    name = Column(String(128), nullable=False, unique=True)
    color = Column(String(32), nullable=True)
    description = Column(String(512), nullable=True)
