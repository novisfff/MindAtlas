from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import UUID


from app.common.time import utcnow


class UuidPrimaryKeyMixin:
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
