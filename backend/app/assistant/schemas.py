from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class ChatRequest(CamelModel):
    message: str = Field(..., min_length=1, max_length=8000)


class ConversationCreateRequest(CamelModel):
    title: Optional[str] = Field(None, max_length=200)


class MessageResponse(OrmModel):
    id: UUID
    role: str
    content: str
    tool_calls: Optional[Any] = None
    tool_results: Optional[Any] = None
    skill_calls: Optional[Any] = None
    analysis: Optional[Any] = None
    created_at: datetime
    updated_at: datetime


class ConversationSummaryResponse(OrmModel):
    id: UUID
    title: Optional[str] = None
    summary: Optional[str] = None
    is_archived: bool
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ConversationResponse(ConversationSummaryResponse):
    messages: list[MessageResponse] = Field(default_factory=list)


class ConversationListResponse(CamelModel):
    items: list[ConversationSummaryResponse] = Field(default_factory=list)
    total: int
