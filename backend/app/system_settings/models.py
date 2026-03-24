from __future__ import annotations

from sqlalchemy import Column, JSON, String

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class AppSetting(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_setting"

    key = Column(String(128), nullable=False, unique=True, index=True)
    value_json = Column(JSON, nullable=False)
