from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.models import AssistantChatRun, AssistantChatRunEvent, Conversation, Message
from app.common.time import utcnow


RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_RECOVERING = "recovering"
RUN_STATUS_WAITING_APPROVAL = "waiting_approval"
RUN_STATUS_WAITING_INPUT = "waiting_input"
RUN_STATUS_CANCELLING = "cancelling"
RUN_STATUS_NEEDS_RECONCILIATION = "needs_reconciliation"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"

# Align with durable active unique index (Plan 06 §4 / models).
RUN_ACTIVE_STATUSES = {
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_RECOVERING,
    RUN_STATUS_WAITING_APPROVAL,
    RUN_STATUS_WAITING_INPUT,
    RUN_STATUS_CANCELLING,
    RUN_STATUS_NEEDS_RECONCILIATION,
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
        runtime_kind: str = "legacy",
        runtime_contract_version: int | None = None,
        required_app_build_revision: str | None = None,
        memory_commit_status: str | None = None,
        deadline_at=None,
        commit: bool = True,
    ) -> AssistantChatRun:
        """Create a Run with immutable ``runtime_kind``.

        Plan 06 Task 6: admission selects ``runtime_kind`` immediately before
        insertion. Once a ``main_agent`` row exists, Legacy fallback is forbidden.

        For ``runtime_kind=main_agent``, callers should pass ``commit=False``,
        append the initial public event on the same Session, then commit once so
        workers never claim a Run that is still missing its initialization event
        (Plan 06 §9 / Task 3 atomic write path).
        """
        active = self.get_active_run(conversation_id=conversation.id)
        if active is not None:
            raise ValueError("conversation already has an active run")

        kind = str(runtime_kind or "legacy").strip().lower()
        if kind not in {"legacy", "main_agent"}:
            raise ValueError(f"invalid runtime_kind: {runtime_kind!r}")

        if kind == "main_agent":
            if runtime_contract_version is None:
                runtime_contract_version = 1
            if not required_app_build_revision:
                raise ValueError(
                    "required_app_build_revision is required for runtime_kind=main_agent"
                )
            if memory_commit_status is None:
                memory_commit_status = "pending"
            from app.assistant.capability_calls.release_admission import (
                freeze_capability_ledger_mode_for_run,
            )

            capability_ledger_mode = freeze_capability_ledger_mode_for_run(
                runtime_kind=kind
            )
        else:
            # Legacy shape: contract version + build must be null.
            runtime_contract_version = None
            required_app_build_revision = None
            if memory_commit_status is None:
                memory_commit_status = "not_applicable"
            capability_ledger_mode = None

        run = AssistantChatRun(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            status=RUN_STATUS_QUEUED,
            last_event_seq=0,
            checkpoint_seq=0,
            runtime_kind=kind,
            runtime_contract_version=runtime_contract_version,
            required_app_build_revision=required_app_build_revision,
            memory_commit_status=memory_commit_status,
            capability_ledger_mode=capability_ledger_mode,
            deadline_at=deadline_at,
        )
        self.db.add(run)
        if commit:
            self.db.commit()
            self.db.refresh(run)
        else:
            self.db.flush()
        return run

    def append_event(
        self,
        *,
        run_id: UUID,
        event_name: str,
        payload: dict,
        event_key: str | None = None,
        commit: bool = True,
    ) -> int:
        """Append one event.

        Pass ``commit=False`` when composing Main Agent create + initial event in
        a single transaction (must not interleave with worker claim).
        """
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        next_seq = int(run.last_event_seq or 0) + 1
        name = str(event_name or "").strip()
        key = str(event_key).strip() if event_key is not None else ""
        # Main Agent public events require a deterministic event_key so stream
        # consumers can dedupe at-least-once transport (Plan 06 §9).
        if not key and str(getattr(run, "runtime_kind", None) or "") == "main_agent":
            key = f"{name}:{run.id}:{next_seq}"
        event = AssistantChatRunEvent(
            run_id=run.id,
            seq=next_seq,
            event_name=name,
            payload=payload if isinstance(payload, dict) else {},
            event_key=key or None,
        )
        self.db.add(event)
        run.last_event_seq = next_seq
        run.updated_at = utcnow()
        if commit:
            self.db.commit()
        else:
            self.db.flush()
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
