from __future__ import annotations

from sqlalchemy import Column, String, Text

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class AiProvider(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_provider"

    name = Column(String(128), nullable=False, unique=True)
    base_url = Column(String(2048), nullable=False)
    model = Column(String(255), nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    api_key_hint = Column(String(64), nullable=False)
