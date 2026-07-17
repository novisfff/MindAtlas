from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import Field

from app.common.schemas import CamelModel, OrmModel


class ChatRequest(CamelModel):
    message: str = Field(..., min_length=1, max_length=8000)
    stream_output: bool = True


class HumanApprovalDecisionRequest(CamelModel):
    decision: Literal["approved", "rejected"]
    values: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)


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


class AssistantRunResponse(CamelModel):
    run_id: UUID
    conversation_id: UUID
    message_id: UUID | None = None
    status: str
    last_event_seq: int = 0
    checkpoint_seq: int = 0
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class DurableInterruptTokenRequest(CamelModel):
    """Token rotation requires exact request/Run revisions (Plan 07 §13)."""

    expected_request_revision: int = Field(..., ge=1)
    expected_run_revision: int = Field(..., ge=0)


class DurableInterruptResolveRequest(CamelModel):
    """Resolve requires token, resolutionRequestId, revisions, typed outcome."""

    token: str = Field(..., min_length=1, max_length=512)
    resolution_request_id: UUID
    expected_token_revision: int = Field(..., ge=0)
    expected_request_revision: int = Field(..., ge=1)
    expected_run_revision: int = Field(..., ge=0)
    outcome: Literal["approved", "rejected", "submitted", "cancelled"]
    values: dict[str, Any] = Field(default_factory=dict)
    # Hard ceiling 4000; settings may lower server-side validation further.
    comment: str | None = Field(default=None, max_length=4000)
