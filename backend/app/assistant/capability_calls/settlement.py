"""Call-aware settlement while a Run is cancelling (Plan 08 Task 2).

No new I/O: only already-captured Attempt evidence may be committed. Adds the
Run edge ``cancelling -> needs_reconciliation`` when an already-started call
cannot be proven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capability_calls.repository import (
    CODE_CALL_NOT_FOUND,
    CODE_INVALID_TRANSITION,
    CODE_STALE_CALL_REVISION,
    CODE_STALE_RUN_REVISION,
    CapabilityCallConflict,
    CapabilityCallRepository,
)
from app.assistant.models import AssistantChatRun
from app.assistant.durable.repository import (
    ALLOWED_TRANSITIONS,
    STATUS_CANCELLING,
    STATUS_NEEDS_RECONCILIATION,
)
from app.assistant.capability_calls.state_machine import PLAN08_RUN_TRANSITION_DELTA
from app.assistant.capability_calls.state_machine import (
    CallTransitionError,
    validate_call_transition,
)
from app.common.time import utcnow


# Install Plan 08 Run delta once at import (idempotent).
for _edge, _rule in PLAN08_RUN_TRANSITION_DELTA.items():
    ALLOWED_TRANSITIONS.setdefault(_edge, _rule)


Outcome = Literal["succeeded", "failed", "unknown"]


@dataclass(slots=True)
class SettlementRequest:
    call_id: UUID
    attempt_id: UUID
    expected_call_revision: int
    expected_run_revision: int
    outcome: Outcome
    result_artifact_id: UUID | None
    evidence_digest: str


class CapabilityCallSettlementRepository:
    """Settle already-started call evidence under Run ``cancelling``."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.calls = CapabilityCallRepository(db)

    def settle_while_cancelling(
        self,
        request: SettlementRequest,
        *,
        now: datetime | None = None,
    ) -> AssistantChatRun:
        """Apply trusted captured outcome without adapter/I/O.

        - succeeded/failed: call becomes terminal; Run remains cancelling.
        - unknown: call -> unknown -> needs_reconciliation; Run cancelling ->
          needs_reconciliation.
        """
        ts = now or utcnow()
        probe = self.calls.get_call(request.call_id)
        if probe is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        # Global lock order: Run before call/Attempt/Interrupt rows.
        run = self.calls.get_run(probe.run_id, for_update=True)
        call = self.calls.get_call(request.call_id, for_update=True)
        if call is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        if int(run.state_revision) != int(request.expected_run_revision):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                f"expected run revision {request.expected_run_revision}, got {run.state_revision}",
                run=run,
                call=call,
            )
        if int(call.state_revision) != int(request.expected_call_revision):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                f"expected call revision {request.expected_call_revision}, got {call.state_revision}",
                call=call,
                run=run,
            )
        if str(run.status) != STATUS_CANCELLING:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"settlement requires run status cancelling, got {run.status!r}",
                call=call,
                run=run,
            )
        if call.side_effect_started_at is None and request.outcome != "unknown":
            # Unstarted calls should use ordinary cancel, not settlement.
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "settlement requires side_effect_started_at or unknown classification",
                call=call,
                run=run,
            )

        if request.outcome in {"succeeded", "failed"}:
            to_status = request.outcome
            self._validate_transition(call, to_status)
            call.status = to_status
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            call.terminal_at = ts
            if request.result_artifact_id is not None:
                call.output_artifact_id = request.result_artifact_id
            self.db.flush()
            return run

        # unknown -> needs_reconciliation on call; Run cancelling -> needs_reconciliation
        if str(call.status) == "executing":
            self._validate_transition(call, "unknown")
            call.status = "unknown"
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            self.db.flush()
        if str(call.status) == "unknown":
            self._validate_transition(call, "needs_reconciliation")
            call.status = "needs_reconciliation"
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            self.db.flush()

        # Advance Run via Plan 08 delta.
        edge = (STATUS_CANCELLING, STATUS_NEEDS_RECONCILIATION)
        if edge not in ALLOWED_TRANSITIONS:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "cancelling run lacks Plan 08 reconciliation transition",
                call=call,
                run=run,
            )
        run.status = STATUS_NEEDS_RECONCILIATION
        run.state_revision = int(run.state_revision) + 1
        self.db.flush()
        return run

    @staticmethod
    def _validate_transition(call: object, to_status: str) -> None:
        try:
            validate_call_transition(
                from_status=str(getattr(call, "status")),
                to_status=to_status,
                side_effect_started_at_is_set=bool(
                    getattr(call, "side_effect_started_at", None) is not None
                ),
                execution_mode=str(getattr(call, "execution_mode")),
            )
        except CallTransitionError as exc:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION, exc.message, call=call  # type: ignore[arg-type]
            ) from exc

    def refuse_cancel_finalizer_if_unproven(self, run_id: UUID) -> None:
        """Raise if cancelling -> cancelled would lie about started calls."""
        if self.calls.has_unproven_started_calls(run_id):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "cannot finalize cancelled while started calls are unproven",
            )


__all__ = [
    "CapabilityCallSettlementRepository",
    "SettlementRequest",
]
