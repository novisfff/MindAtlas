from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.assistant.schemas import (
    AssistantRunResponse,
    ChatRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    DurableInterruptResolveRequest,
    DurableInterruptTokenRequest,
    HumanApprovalDecisionRequest,
)
from app.assistant.service import AssistantService
from app.assistant.workflow.durable import interrupt_api as durable_interrupt_api
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
        service.chat_stream(id, request.message, stream_output=request.stream_output),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations/{id}/runs/active", response_model=ApiResponse)
def get_active_run(
    id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    payload = service.get_active_run_payload(id)
    data = AssistantRunResponse.model_validate(payload).model_dump(by_alias=True) if payload else None
    return ApiResponse.ok(data)


@router.get("/conversations/{id}/runs/{run_id}/stream")
def stream_run(
    id: UUID,
    run_id: UUID,
    after_seq: int = Query(0, ge=0, alias="afterSeq"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)
    return StreamingResponse(
        service.stream_run(id, run_id=run_id, after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{id}/runs/{run_id}/stop", response_model=ApiResponse)
def stop_run(
    id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    payload = service.stop_run(conversation_id=id, run_id=run_id)
    data = AssistantRunResponse.model_validate(payload).model_dump(by_alias=True)
    return ApiResponse.ok(data)


@router.get("/conversations/{id}/approvals/pending", response_model=ApiResponse)
def list_pending_approvals(
    id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    items = service.list_pending_approvals(id)
    return ApiResponse.ok(items)


@router.post("/conversations/{id}/approvals/{approval_id}/decision", response_model=ApiResponse)
def submit_approval_decision(
    id: UUID,
    approval_id: UUID,
    request: HumanApprovalDecisionRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    payload = service.submit_approval_decision(
        conversation_id=id,
        approval_id=approval_id,
        decision=request.decision,
        values=request.values,
        comment=request.comment,
    )
    return ApiResponse.ok(payload)


# ---------------------------------------------------------------------------
# Durable Interrupt APIs (Plan 07 Task 6) — conversation-scoped only
# ---------------------------------------------------------------------------


@router.get(
    "/conversations/{id}/runs/{run_id}/interrupts/pending",
    response_model=ApiResponse,
)
def list_pending_durable_interrupts(
    id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)
    items = durable_interrupt_api.service_list_pending(db, id, run_id)
    return ApiResponse.ok(items)


@router.get(
    "/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}",
    response_model=ApiResponse,
)
def get_durable_interrupt(
    id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)
    payload = durable_interrupt_api.service_get_detail(db, id, run_id, interrupt_id)
    return ApiResponse.ok(payload)


@router.post(
    "/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}/token",
    response_model=ApiResponse,
)
def rotate_durable_interrupt_token(
    id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    request: DurableInterruptTokenRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)
    payload = durable_interrupt_api.service_rotate_token(
        db,
        conversation_id=id,
        run_id=run_id,
        interrupt_id=interrupt_id,
        expected_request_revision=request.expected_request_revision,
        expected_run_revision=request.expected_run_revision,
    )
    return ApiResponse.ok(payload)


@router.post(
    "/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}/resolve",
    response_model=ApiResponse,
)
def resolve_durable_interrupt(
    id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    request: DurableInterruptResolveRequest,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = AssistantService(db)
    service.get_conversation_basic(id)
    payload = durable_interrupt_api.service_resolve(
        db,
        conversation_id=id,
        run_id=run_id,
        interrupt_id=interrupt_id,
        token=request.token,
        resolution_request_id=request.resolution_request_id,
        expected_token_revision=request.expected_token_revision,
        expected_request_revision=request.expected_request_revision,
        expected_run_revision=request.expected_run_revision,
        outcome=request.outcome,
        values=request.values,
        comment=request.comment,
    )
    return ApiResponse.ok(payload)
