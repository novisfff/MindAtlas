from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummaryResponse,
)
from app.assistant.service import AssistantService
from app.common.responses import ApiResponse
from app.database import get_db


router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.get("/conversations", response_model=ApiResponse)
def list_conversations(
    archived: bool | None = Query(None),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    conversations = service.list_conversations(archived=archived)
    data = ConversationListResponse(
        items=[ConversationSummaryResponse.model_validate(c) for c in conversations],
        total=len(conversations),
    )
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.post("/conversations", response_model=ApiResponse)
def create_conversation(
    request: ConversationCreateRequest | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    title = request.title if request else None
    conversation = service.create_conversation(title=title)
    data = ConversationResponse.model_validate(conversation)
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.get("/conversations/{id}", response_model=ApiResponse)
def get_conversation(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantService(db)
    conversation = service.get_conversation(id)
    data = ConversationResponse.model_validate(conversation)
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.delete("/conversations/{id}", response_model=ApiResponse)
def delete_conversation(id: UUID, db: Session = Depends(get_db)) -> ApiResponse:
    service = AssistantService(db)
    service.delete_conversation(id)
    return ApiResponse.ok(None, "Conversation deleted successfully")


@router.post("/conversations/{id}/chat")
def chat(
    id: UUID,
    request: ChatRequest,
    db: Session = Depends(get_db)
) -> StreamingResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)  # 验证存在
    return StreamingResponse(
        service.chat_stream(id, request.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
