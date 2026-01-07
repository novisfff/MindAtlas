from __future__ import annotations

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class Attachment(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attachment"

    entry_id = Column(UUID(as_uuid=True), ForeignKey("entry.id", ondelete="CASCADE"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    size = Column("file_size", BigInteger, nullable=False)
    content_type = Column("mime_type", String(128), nullable=False)

    # Relationships
    entry = relationship("Entry", lazy="joined")
