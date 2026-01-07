from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.common.schemas import OrmModel


class AttachmentResponse(OrmModel):
    id: UUID
    entry_id: UUID
    filename: str
    original_filename: str
    content_type: str
    size: int
    created_at: datetime
