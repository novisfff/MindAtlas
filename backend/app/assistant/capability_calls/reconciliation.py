"""Capability call reconciliation operations (Plan 08 Task 7).

No production external write is enabled. This module provides the complete
backend contract for operator decisions with mode-matrix enforcement.
HTTP mutation routes remain unmounted; CLI is the guarded transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capability_calls.models import (
    AssistantCapabilityCall,
    AssistantCapabilityReconciliation,
)
from app.assistant.capability_calls.repository import (
    CODE_CALL_NOT_FOUND,
    CODE_INVALID_TRANSITION,
    CODE_STALE_CALL_REVISION,
    CODE_STALE_RUN_REVISION,
    CapabilityCallConflict,
    CapabilityCallRepository,
)
from app.common.time import utcnow

ReconciliationDecision = Literal[
    "mark_succeeded",
    "mark_failed",
    "mark_compensated",
    "retry_same_key",
]

MODES_FORBIDDING_RETRY = frozenset(
    {
        "local_transactional",
        "non_retriable",
        "unsupported",
        "pure_replayable",
        "read_replayable",
    }
)


@dataclass(frozen=True, slots=True)
class ReconciliationDecisionRequest:
    call_id: UUID
    expected_call_revision: int
    expected_run_revision: int
    decision: ReconciliationDecision
    reason: str
    evidence_artifact_ids: tuple[UUID, ...]
    resolution_request_id: UUID
    actor_user_id: UUID | None = None
    actor_admin_id: UUID | None = None
    # For external_reconcilable retry_same_key: operator asserts status lookup done.
    status_lookup_proved_not_accepted: bool = False


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    call_id: UUID
    decision: str
    resulting_call_status: str
    resulting_call_revision: int
    resulting_run_revision: int
    reconciliation_id: UUID
    created: bool


class CapabilityReconciliationService:
    """Append-only reconciliation decisions with mode matrix enforcement."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.calls = CapabilityCallRepository(db)

    def get_call(self, call_id: UUID) -> AssistantCapabilityCall | None:
        return self.calls.get_call(call_id)

    def list_for_run(self, run_id: UUID) -> list[AssistantCapabilityReconciliation]:
        return (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(AssistantCapabilityReconciliation.run_id == run_id)
            .order_by(AssistantCapabilityReconciliation.revision.asc())
            .all()
        )

    def apply(
        self,
        request: ReconciliationDecisionRequest,
        *,
        now: datetime | None = None,
    ) -> ReconciliationResult:
        ts = now or utcnow()
        if not (request.reason or "").strip():
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION, "reconciliation reason is required"
            )
        if not request.evidence_artifact_ids:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires at least one evidence artifact id",
            )
        if request.actor_user_id is None and request.actor_admin_id is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires actor_user_id or actor_admin_id",
            )

        # Idempotency by resolution_request_id
        existing = (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(
                AssistantCapabilityReconciliation.resolution_request_id
                == request.resolution_request_id
            )
            .one_or_none()
        )
        if existing is not None:
            call = self.calls.get_call(existing.call_id)
            return ReconciliationResult(
                call_id=existing.call_id,
                decision=str(existing.decision),
                resulting_call_status=str(call.status) if call else "unknown",
                resulting_call_revision=int(existing.resulting_call_revision or 0),
                resulting_run_revision=int(existing.resulting_run_revision or 0),
                reconciliation_id=existing.id,
                created=False,
            )

        call = self.calls.get_call(request.call_id, for_update=True)
        if call is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        run = self.calls.get_run(call.run_id, for_update=True)
        if int(call.state_revision) != int(request.expected_call_revision):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                f"expected call revision {request.expected_call_revision}, got {call.state_revision}",
                call=call,
                run=run,
            )
        if int(run.state_revision) != int(request.expected_run_revision):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                f"expected run revision {request.expected_run_revision}, got {run.state_revision}",
                call=call,
                run=run,
            )
        if str(call.status) not in {"needs_reconciliation", "unknown"}:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"call status {call.status!r} is not reconcilable",
                call=call,
                run=run,
            )

        mode = str(call.execution_mode)
        decision = request.decision

        if decision == "retry_same_key":
            if mode in MODES_FORBIDDING_RETRY:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    f"retry_same_key forbidden for execution_mode={mode!r}",
                    call=call,
                    run=run,
                )
            if mode == "external_reconcilable" and not request.status_lookup_proved_not_accepted:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "external_reconcilable requires authoritative not_accepted "
                    "status lookup before retry_same_key",
                    call=call,
                    run=run,
                )
            if mode == "external_idempotent" or (
                mode == "external_reconcilable"
                and request.status_lookup_proved_not_accepted
            ):
                if str(call.status) == "unknown":
                    call.status = "needs_reconciliation"
                    call.state_revision = int(call.state_revision) + 1
                    call.updated_at = ts
                    self.db.flush()
                call = self.calls.transition_call(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    to_status="authorized",
                    lease=None,
                    has_retry_same_key_authorization=True,
                    allow_while_cancelling=True,
                    now=ts,
                )
            else:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    f"retry_same_key not permitted for mode={mode!r}",
                    call=call,
                    run=run,
                )
        else:
            to_status = {
                "mark_succeeded": "succeeded",
                "mark_failed": "failed",
                "mark_compensated": "compensated",
            }[decision]
            if str(call.status) == "unknown":
                call.status = "needs_reconciliation"
                call.state_revision = int(call.state_revision) + 1
                call.updated_at = ts
                self.db.flush()
            call = self.calls.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=int(run.state_revision),
                to_status=to_status,
                lease=None,
                allow_while_cancelling=True,
                now=ts,
            )

        run.state_revision = int(run.state_revision) + 1
        self.db.flush()

        next_rev = (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(AssistantCapabilityReconciliation.call_id == call.id)
            .count()
            + 1
        )
        row = AssistantCapabilityReconciliation(
            id=uuid4(),
            call_id=call.id,
            run_id=run.id,
            revision=next_rev,
            decision=decision,
            actor_user_id=request.actor_user_id,
            actor_admin_id=request.actor_admin_id,
            authorization_evidence={
                "mode": mode,
                "statusLookupProvedNotAccepted": request.status_lookup_proved_not_accepted,
            },
            reason=request.reason.strip(),
            evidence_artifact_ids=[str(x) for x in request.evidence_artifact_ids],
            expected_call_revision=request.expected_call_revision,
            expected_run_revision=request.expected_run_revision,
            resulting_call_revision=int(call.state_revision),
            resulting_run_revision=int(run.state_revision),
            resolution_request_id=request.resolution_request_id,
            created_at=ts,
        )
        self.db.add(row)
        self.db.flush()
        return ReconciliationResult(
            call_id=call.id,
            decision=decision,
            resulting_call_status=str(call.status),
            resulting_call_revision=int(call.state_revision),
            resulting_run_revision=int(run.state_revision),
            reconciliation_id=row.id,
            created=True,
        )


@dataclass
class ScriptedExternalOutcome:
    """One scripted transport outcome for external uncertainty tests."""

    kind: Literal[
        "before_send_refusal",
        "accepted_then_timeout",
        "ambiguous_5xx",
        "key_echo_success",
        "status_lookup",
        "duplicate_key",
        "non_retriable_uncertain",
    ]
    status_code: int | None = None
    body: dict[str, Any] | None = None
    echo_key: str | None = None
    accepted: bool | None = None


class ScriptedExternalAdapter:
    """Network-free scripted external adapter for uncertainty matrix tests."""

    def __init__(self, outcomes: list[ScriptedExternalOutcome] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[dict[str, Any]] = []
        self._i = 0

    def send(self, *, idempotency_key: str, payload: dict[str, Any]) -> ScriptedExternalOutcome:
        if self._i >= len(self.outcomes):
            raise RuntimeError("scripted adapter exhausted")
        outcome = self.outcomes[self._i]
        self._i += 1
        self.calls.append(
            {"key": idempotency_key, "payload": payload, "outcome": outcome.kind}
        )
        return outcome

    def classify_for_ledger(
        self, outcome: ScriptedExternalOutcome
    ) -> Literal["succeeded", "failed", "unknown"]:
        if outcome.kind in {"key_echo_success", "duplicate_key"}:
            return "succeeded"
        if outcome.kind in {"before_send_refusal"}:
            return "failed"
        return "unknown"


__all__ = [
    "CapabilityReconciliationService",
    "MODES_FORBIDDING_RETRY",
    "ReconciliationDecisionRequest",
    "ReconciliationResult",
    "ScriptedExternalAdapter",
    "ScriptedExternalOutcome",
]
