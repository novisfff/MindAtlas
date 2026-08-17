from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.models import AssistantChatRun, AssistantChatRunEvent, Conversation, Message
from app.assistant.runtime.contracts import require_sha256
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
        # Plan 09 Task 4: production Run lookup rejects evaluation-namespace IDs.
        from app.assistant.evaluation.contracts import reject_if_evaluation_id

        reject_if_evaluation_id(self.db, entity="run", value=run_id)
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
        main_agent_rollout_revision_id: UUID,
        main_agent_profile_version_id: UUID,
        resolved_model_id: UUID,
        runtime_closure_digest: str,
        runtime_contract_version: int,
        required_checkpoint_codec_version: int,
        required_capability_feature_digest: str,
        required_create_entry_contract_digest: str,
        required_write_policy_digest: str,
        required_write_cohort_digest: str,
        required_reconciliation_contract_version: int,
        required_app_build_revision: str,
        capability_ledger_mode: str,
        deadline_at: datetime | None = None,
        commit: bool = False,
    ) -> AssistantChatRun:
        """Create a Main-Agent Run with the frozen runtime closure.

        Plan 2: every newly admitted Chat Run is ``runtime_kind=main_agent``. No
        call site may pass ``runtime_kind``; Legacy is not selectable. Callers
        should pass ``commit=False``, append the initial public event on the same
        Session, then commit once so workers never claim a Run that is still
        missing its initialization event.
        """
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production Run writer.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("run_service.create_run")
        active = self.get_active_run(conversation_id=conversation.id)
        if active is not None:
            raise ValueError("conversation already has an active run")

        if int(runtime_contract_version) <= 0:
            raise ValueError("runtime_contract_version must be > 0")
        if int(required_checkpoint_codec_version) <= 0:
            raise ValueError("required_checkpoint_codec_version must be > 0")
        if int(required_reconciliation_contract_version) <= 0:
            raise ValueError("required_reconciliation_contract_version must be > 0")
        build = str(required_app_build_revision or "").strip()
        if not build:
            raise ValueError("required_app_build_revision is required")
        ledger = str(capability_ledger_mode or "").strip()
        if ledger not in {"legacy_read_only", "enforced"}:
            raise ValueError(f"invalid capability_ledger_mode: {capability_ledger_mode!r}")

        run = AssistantChatRun(
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            status=RUN_STATUS_QUEUED,
            last_event_seq=0,
            checkpoint_seq=0,
            runtime_kind="main_agent",
            main_agent_rollout_revision_id=main_agent_rollout_revision_id,
            main_agent_profile_version_id=main_agent_profile_version_id,
            resolved_model_id=resolved_model_id,
            runtime_closure_digest=require_sha256(
                runtime_closure_digest, field_name="runtime_closure_digest"
            ),
            runtime_contract_version=int(runtime_contract_version),
            required_checkpoint_codec_version=int(required_checkpoint_codec_version),
            required_capability_feature_digest=require_sha256(
                required_capability_feature_digest,
                field_name="required_capability_feature_digest",
            ),
            required_create_entry_contract_digest=require_sha256(
                required_create_entry_contract_digest,
                field_name="required_create_entry_contract_digest",
            ),
            required_write_policy_digest=require_sha256(
                required_write_policy_digest,
                field_name="required_write_policy_digest",
            ),
            required_write_cohort_digest=require_sha256(
                required_write_cohort_digest,
                field_name="required_write_cohort_digest",
            ),
            required_reconciliation_contract_version=int(
                required_reconciliation_contract_version
            ),
            required_app_build_revision=build,
            capability_ledger_mode=ledger,
            memory_commit_status="pending",
            deadline_at=deadline_at,
        )
        self.db.add(run)
        self.db.flush()
        if commit:
            self.db.commit()
            self.db.refresh(run)
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
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production event writer.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("run_service.append_event")
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
        # Plan 09 Task 4: production event lookup rejects evaluation-namespace IDs.
        from app.assistant.evaluation.contracts import reject_if_evaluation_id

        reject_if_evaluation_id(self.db, entity="run", value=run_id)
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
