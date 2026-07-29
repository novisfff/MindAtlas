"""Atomic Main-Agent-only Chat admission (Plan 2 Task 8).

Locks rollout control, revalidates readiness + closure + Worker compatibility,
then inserts user Message + empty assistant Message + one main_agent Run + the
initial public event in a single transaction. Pre-insert failures leave no
residue. Post-insert failures stay on that exact Run — never select legacy.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.capability_calls.release_admission import (
    freeze_capability_ledger_mode_for_run,
)
from app.assistant.durable.worker_registry import (
    WorkerCompatibility,
    WorkerRegistry,
)
from app.assistant.models import Conversation, Message
from app.assistant.run_service import AssistantChatRunService
from app.assistant.runtime.closure import (
    AssistantRuntimeClosureBuilder,
    RuntimeClosureDrift,
)
from app.assistant.runtime.contracts import (
    RUNTIME_READINESS_REASON_CODES,
    NewChatAdmission,
)
from app.assistant.runtime.readiness import AssistantReadinessService
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.assistant.skills.models import AssistantMainAgentProfileVersion
from app.assistant.skills.schemas import MainAgentProfileSnapshotV2
from app.common.time import utcnow
from app.config import Settings, get_settings


class AssistantAdmissionError(RuntimeError):
    """Stable pre-insert admission rejection (no payload leakage)."""

    def __init__(self, reason_code: str) -> None:
        code = str(reason_code or "").strip()
        if code not in RUNTIME_READINESS_REASON_CODES:
            code = "runtime_closure_drift"
        super().__init__(code)
        self.reason_code = code


class ConcurrentChatAdmission(RuntimeError):
    """Raised when another admission already owns the active Run slot."""

    def __init__(self, message: str = "conversation already has an active run") -> None:
        super().__init__(message)


ADMISSION_HTTP_REASON: dict[str, str] = {
    "rollout_inactive": "assistant_rollout_inactive",
    "runtime_closure_drift": "assistant_runtime_closure_drift",
    "worker_unavailable": "assistant_worker_unavailable",
    "new_runs_disabled": "assistant_new_runs_disabled",
    "schema_incompatible": "assistant_schema_incompatible",
    "system_not_initialized": "assistant_system_not_initialized",
    "operator_missing": "assistant_operator_missing",
    "operator_auth_unavailable": "assistant_operator_auth_unavailable",
    "system_seed_invalid": "assistant_system_seed_invalid",
    "profile_unpublished": "assistant_profile_unpublished",
    "model_unbound": "assistant_model_unbound",
}


class AssistantChatAdmissionService:
    """Admit a new Chat turn as one atomic Main-Agent Run unit."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | Any | None = None,
        readiness: AssistantReadinessService | None = None,
        runtime_repo: AssistantRuntimeRepository | None = None,
        closure_builder: AssistantRuntimeClosureBuilder | None = None,
        run_service: AssistantChatRunService | None = None,
        worker_registry: WorkerRegistry | None = None,
    ) -> None:
        self.db = db
        self.settings = settings if settings is not None else get_settings()
        self.runtime_repo = runtime_repo or AssistantRuntimeRepository(db)
        self.closure_builder = closure_builder or AssistantRuntimeClosureBuilder(db)
        self.readiness = readiness or AssistantReadinessService(
            db,
            settings=self.settings,
            closure_builder=self.closure_builder,
        )
        self.run_service = run_service or AssistantChatRunService(db)
        self.worker_registry = worker_registry or WorkerRegistry(db)

    def admit_and_create(
        self,
        *,
        conversation_id: UUID,
        user_message: str,
    ):
        """Lock → evaluate → insert Message/Run/event → single commit.

        Fixed lock order: rollout control → active rollout closure rows →
        compatible Worker snapshot (read) → conversation.
        """
        try:
            control = self.runtime_repo.get_control_for_update()
            if control is None:
                raise AssistantAdmissionError("rollout_inactive")

            readiness = self.readiness.evaluate_locked(control=control)
            if not readiness.ready:
                reason = (
                    readiness.reason_codes[0]
                    if readiness.reason_codes
                    else "runtime_closure_drift"
                )
                raise AssistantAdmissionError(reason)

            rollout_revision_id = readiness.active_rollout_revision_id
            if rollout_revision_id is None:
                raise AssistantAdmissionError("rollout_inactive")

            try:
                closure = self.closure_builder.build(
                    rollout_revision_id=rollout_revision_id,
                    lock=True,
                )
            except RuntimeClosureDrift:
                raise AssistantAdmissionError("runtime_closure_drift") from None
            except AssistantAdmissionError:
                raise
            except Exception:
                raise AssistantAdmissionError("runtime_closure_drift") from None

            workers = self.worker_registry.find_compatible_workers(
                WorkerCompatibility.from_closure(closure)
            )
            if not workers:
                raise AssistantAdmissionError("worker_unavailable")

            conversation = self._lock_conversation(conversation_id)
            self._assert_no_active_run(conversation.id)

            admission = NewChatAdmission(
                closure=closure,
                compatible_worker_ids=tuple(row.worker_id for row in workers),
                deadline_at=self._deadline_from_profile(closure.profile_version_id),
            )

            user = Message(
                conversation_id=conversation.id,
                role="user",
                content=user_message,
            )
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
            )
            self.db.add_all((user, assistant))
            conversation.last_message_at = utcnow()
            self.db.flush()

            try:
                run = self.run_service.create_run(
                    conversation=conversation,
                    user_message=user,
                    assistant_message=assistant,
                    main_agent_rollout_revision_id=closure.rollout_revision_id,
                    main_agent_profile_version_id=closure.profile_version_id,
                    resolved_model_id=closure.model_id,
                    runtime_closure_digest=closure.closure_digest,
                    runtime_contract_version=closure.runtime_contract_version,
                    required_checkpoint_codec_version=closure.checkpoint_codec_version,
                    required_capability_feature_digest=closure.capability_feature_digest,
                    required_app_build_revision=closure.build_revision,
                    capability_ledger_mode=self._frozen_ledger_mode(),
                    deadline_at=admission.deadline_at,
                    commit=False,
                )
            except ValueError as exc:
                # Active-run guard inside create_run → concurrent admission.
                self.db.rollback()
                raise ConcurrentChatAdmission(str(exc)) from exc

            self.run_service.append_event(
                run_id=run.id,
                event_name="run_status",
                event_key=f"run.status:queued:{run.id}",
                payload={"status": "queued", "runtimeKind": "main_agent"},
                commit=False,
            )
            self.db.commit()
            self.db.refresh(run)
            return run
        except AssistantAdmissionError:
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        except ConcurrentChatAdmission:
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        except IntegrityError as exc:
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise ConcurrentChatAdmission from exc
        except Exception:
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise

    def _lock_conversation(self, conversation_id: UUID) -> Conversation:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
        conversation = self.db.execute(stmt).scalar_one_or_none()
        if conversation is None:
            # Conversation missing is not an admission readiness reason; surface
            # as concurrent/not-found style conflict without leaking IDs beyond
            # the exception message used by ApiException mapping.
            raise ConcurrentChatAdmission(
                f"conversation not found: {conversation_id}"
            )
        return conversation

    def _assert_no_active_run(self, conversation_id: UUID) -> None:
        active = self.run_service.get_active_run(conversation_id=conversation_id)
        if active is not None:
            raise ConcurrentChatAdmission(
                "conversation already has an active run"
            )

    def _deadline_from_profile(self, profile_version_id: UUID) -> datetime | None:
        version = self.db.get(AssistantMainAgentProfileVersion, profile_version_id)
        if version is None:
            return None
        try:
            snapshot = MainAgentProfileSnapshotV2.model_validate(version.snapshot or {})
        except Exception:
            return None
        max_wall = getattr(snapshot.output_budget, "max_wall_time_ms", None)
        if max_wall is None:
            return None
        try:
            wall_ms = int(max_wall)
        except (TypeError, ValueError):
            return None
        if wall_ms <= 0:
            return None
        return utcnow() + timedelta(milliseconds=wall_ms)

    def _frozen_ledger_mode(self) -> str:
        mode = freeze_capability_ledger_mode_for_run(
            runtime_kind="main_agent",
            settings=self.settings if isinstance(self.settings, Settings) else None,
        )
        if mode is None:
            # Settings overlay (tests) may not be a Settings instance.
            raw = getattr(
                self.settings, "assistant_capability_ledger_mode", "legacy_read_only"
            )
            mode = str(raw or "legacy_read_only")
        ledger = str(mode or "legacy_read_only").strip().lower()
        if ledger not in {"legacy_read_only", "enforced"}:
            return "legacy_read_only"
        return ledger


__all__ = (
    "ADMISSION_HTTP_REASON",
    "AssistantAdmissionError",
    "AssistantChatAdmissionService",
    "ConcurrentChatAdmission",
    "NewChatAdmission",
)
