from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.models import AssistantChatRun, AssistantChatRunEvent, Conversation, Message
from app.common.time import utcnow


RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
RUN_STATUS_CANCELLING = "cancelling"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"

RUN_ACTIVE_STATUSES = {
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_WAITING_APPROVAL,
    RUN_STATUS_CANCELLING,
}
RUN_TERMINAL_STATUSES = {
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
}


class AssistantChatRunService:
    def __init__(self, db: Session):
        self.db = db

    def get_run(self, *, conversation_id: UUID, run_id: UUID) -> AssistantChatRun | None:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            return None
        if run.conversation_id != conversation_id:
            return None
        return run

    def get_active_run(self, *, conversation_id: UUID) -> AssistantChatRun | None:
        return (
            self.db.query(AssistantChatRun)
            .filter(
                AssistantChatRun.conversation_id == conversation_id,
                AssistantChatRun.status.in_(tuple(RUN_ACTIVE_STATUSES)),
            )
            .order_by(AssistantChatRun.created_at.desc())
            .first()
        )

    def create_run(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
    ) -> AssistantChatRun:
        active = self.get_active_run(conversation_id=conversation.id)
        if active is not None:
            raise ValueError("conversation already has an active run")

        run = AssistantChatRun(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            status=RUN_STATUS_QUEUED,
            last_event_seq=0,
            checkpoint_seq=0,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def append_event(
        self,
        *,
        run_id: UUID,
        event_name: str,
        payload: dict,
    ) -> int:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        next_seq = int(run.last_event_seq or 0) + 1
        event = AssistantChatRunEvent(
            run_id=run.id,
            seq=next_seq,
            event_name=str(event_name or "").strip(),
            payload=payload if isinstance(payload, dict) else {},
        )
        self.db.add(event)
        run.last_event_seq = next_seq
        run.updated_at = utcnow()
        self.db.commit()
        return next_seq

    def list_events_after(
        self,
        *,
        run_id: UUID,
        after_seq: int,
        limit: int = 200,
    ) -> list[AssistantChatRunEvent]:
        return (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run_id,
                AssistantChatRunEvent.seq > int(after_seq),
            )
            .order_by(AssistantChatRunEvent.seq.asc())
            .limit(max(1, int(limit)))
            .all()
        )

    def update_checkpoint(self, *, run_id: UUID, checkpoint_seq: int) -> None:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            return
        target = max(int(run.checkpoint_seq or 0), int(checkpoint_seq or 0))
        if target == int(run.checkpoint_seq or 0):
            return
        run.checkpoint_seq = target
        run.updated_at = utcnow()
        self.db.commit()

    def update_run_status(
        self,
        *,
        run_id: UUID,
        status: str,
        error_message: str | None = None,
    ) -> AssistantChatRun:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        now = utcnow()
        new_status = str(status or "").strip().lower()
        run.status = new_status
        if new_status == RUN_STATUS_RUNNING and run.started_at is None:
            run.started_at = now
        if new_status in RUN_TERMINAL_STATUSES:
            if run.started_at is None:
                run.started_at = now
            run.ended_at = now
        if error_message:
            run.error_message = str(error_message)
        run.updated_at = now
        self.db.commit()
        self.db.refresh(run)
        return run

    def request_stop(self, *, conversation_id: UUID, run_id: UUID) -> AssistantChatRun | None:
        run = self.get_run(conversation_id=conversation_id, run_id=run_id)
        if run is None:
            return None
        if run.status in RUN_TERMINAL_STATUSES:
            return run
        now = utcnow()
        if run.cancel_requested_at is None:
            run.cancel_requested_at = now
        if run.status != RUN_STATUS_CANCELLING:
            run.status = RUN_STATUS_CANCELLING
        run.updated_at = now
        self.db.commit()
        self.db.refresh(run)
        return run

    def is_cancel_requested(self, *, run_id: UUID) -> bool:
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            return True
        return bool(run.cancel_requested_at is not None or run.status in {RUN_STATUS_CANCELLING, RUN_STATUS_CANCELLED})

    def active_statuses(self) -> Iterable[str]:
        return RUN_ACTIVE_STATUSES

    def terminal_statuses(self) -> Iterable[str]:
        return RUN_TERMINAL_STATUSES
