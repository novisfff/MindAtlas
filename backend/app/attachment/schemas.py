from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.common.schemas import OrmModel


ParseStatus = Literal["pending", "processing", "completed", "failed"]
IndexStatus = Literal["pending", "processing", "succeeded", "dead"]
MarkdownState = Literal["ready", "processing", "failed", "unsupported"]
MarkdownSource = Literal["parsed_text", "file"]


class AttachmentResponse(OrmModel):
    id: UUID
    entry_id: UUID
    filename: str
    original_filename: str
    content_type: str
    size: int
    created_at: datetime
    index_to_knowledge_graph: bool | None = None
    parse_status: ParseStatus | None = None
    parsed_at: datetime | None = None
    parse_last_error: str | None = None
    kg_index_status: IndexStatus | None = None
    kg_index_attempts: int | None = None
    kg_index_last_error: str | None = None
    kg_index_updated_at: datetime | None = None


class AttachmentMarkdownResponse(OrmModel):
    attachment_id: UUID
    state: MarkdownState
    source: MarkdownSource | None = None
    markdown: str | None = None
    content_type: str
    original_filename: str
    parse_status: ParseStatus | None = None
    parse_last_error: str | None = None
