"""CapabilityCall CAS repository (Plan 08 Task 2).

The only module allowed to transition call/attempt/reconciliation state.
Gateway is never invoked here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capability_calls.models import (
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
    AssistantCapabilityReconciliation,
)
from app.assistant.capability_calls.state_machine import (
    CallTransitionError,
    is_terminal_call_status,
    validate_call_transition,
)
from app.assistant.models import AssistantChatRun
from app.assistant.durable.repository import LeaseToken
from app.common.time import utcnow


CODE_STALE_CALL_REVISION = "stale_call_revision"
CODE_STALE_RUN_REVISION = "stale_run_revision"
CODE_LEASE_MISMATCH = "lease_mismatch"
CODE_IDENTITY_MISMATCH = "call_identity_mismatch"
CODE_CALL_NOT_FOUND = "call_not_found"
CODE_INVALID_TRANSITION = "invalid_call_transition"
CODE_RUN_CANCELLING = "run_cancelling_blocks_ordinary_dispatch"

_ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "claimed": frozenset({"dispatched", "failed", "abandoned"}),
    "dispatched": frozenset({"response_received", "failed", "uncertain"}),
    "response_received": frozenset({"committed", "failed", "uncertain"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "uncertain": frozenset(),
    "abandoned": frozenset(),
}

_EXTERNAL_EFFECT_EXECUTION_MODES = frozenset(
    {"external_idempotent", "external_reconcilable", "non_retriable"}
)


class CapabilityCallConflict(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        call: AssistantCapabilityCall | None = None,
        run: AssistantChatRun | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.call = call
        self.run = run


# Immutable identity fields compared on create-or-verify.
_IDENTITY_FIELDS = (
    "manifest_revision_id",
    "logical_call_key",
    "owner_kind",
    "owner_id",
    "owner_version_id",
    "capability_type",
    "domain_key",
    "target_id",
    "target_version_id",
    "descriptor_digest",
    "authorization_digest",
    "input_artifact_id",
    "input_digest",
    "side_effect_class",
    "execution_mode",
    "idempotency_key",
    "parent_call_id",
    "provider_tool_call_id",
)


@dataclass(slots=True)
class ProposeCallSpec:
    """Inputs for create-or-verify of a proposed call."""

    call_id: UUID
    run_id: UUID
    expected_run_revision: int
    lease: LeaseToken | None
    manifest_revision_id: UUID
    logical_call_key: str
    owner_kind: str
    capability_type: str
    domain_key: str
    descriptor_digest: str
    authorization_digest: str
    input_artifact_id: UUID
    input_digest: str
    side_effect_class: str
    execution_mode: str
    idempotency_key: str
    owner_id: UUID | None = None
    owner_version_id: UUID | None = None
    target_id: UUID | None = None
    target_version_id: UUID | None = None
    parent_call_id: UUID | None = None
    provider_tool_call_id: str | None = None
    approval_binding_digest: str | None = None


class CapabilityCallRepository:
    """CAS repository for capability call ledger rows."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Locks / loads
    # ------------------------------------------------------------------

    def get_run(self, run_id: UUID, *, for_update: bool = False) -> AssistantChatRun:
        stmt = self.db.query(AssistantChatRun).filter(AssistantChatRun.id == run_id)
        if for_update:
            stmt = stmt.populate_existing().with_for_update()
        run = stmt.one_or_none()
        if run is None:
            raise CapabilityCallConflict(CODE_CALL_NOT_FOUND, f"run {run_id} not found")
        return run

    def get_call(
        self,
        call_id: UUID,
        *,
        for_update: bool = False,
    ) -> AssistantCapabilityCall | None:
        stmt = self.db.query(AssistantCapabilityCall).filter(
            AssistantCapabilityCall.id == call_id
        )
        if for_update:
            stmt = stmt.populate_existing().with_for_update()
        return stmt.one_or_none()

    def get_call_by_logical_key(
        self,
        *,
        run_id: UUID,
        logical_call_key: str,
        for_update: bool = False,
    ) -> AssistantCapabilityCall | None:
        stmt = self.db.query(AssistantCapabilityCall).filter(
            AssistantCapabilityCall.run_id == run_id,
            AssistantCapabilityCall.logical_call_key == logical_call_key,
        )
        if for_update:
            stmt = stmt.populate_existing().with_for_update()
        return stmt.one_or_none()

    def _verify_lease(self, run: AssistantChatRun, lease: LeaseToken | None) -> None:
        if lease is None:
            return
        if str(run.id) != str(lease.run_id):
            raise CapabilityCallConflict(CODE_LEASE_MISMATCH, "lease run_id mismatch", run=run)
        if (run.lease_owner or "") != lease.worker_id:
            raise CapabilityCallConflict(
                CODE_LEASE_MISMATCH,
                "lease worker mismatch",
                run=run,
            )
        if int(run.lease_generation) != int(lease.lease_generation):
            raise CapabilityCallConflict(
                CODE_LEASE_MISMATCH,
                "lease generation mismatch",
                run=run,
            )

    def _require_run_revision(self, run: AssistantChatRun, expected: int) -> None:
        if int(run.state_revision) != int(expected):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                f"expected run revision {expected}, got {run.state_revision}",
                run=run,
            )

    def _require_call_revision(
        self, call: AssistantCapabilityCall, expected: int
    ) -> None:
        if int(call.state_revision) != int(expected):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                f"expected call revision {expected}, got {call.state_revision}",
                call=call,
            )

    def _identity_matches(
        self, existing: AssistantCapabilityCall, spec: ProposeCallSpec
    ) -> bool:
        for field in _IDENTITY_FIELDS:
            if getattr(existing, field) != getattr(spec, field):
                return False
        return True

    # ------------------------------------------------------------------
    # Create-or-verify
    # ------------------------------------------------------------------

    def create_or_verify_proposed(
        self,
        spec: ProposeCallSpec,
        *,
        now: datetime | None = None,
    ) -> tuple[AssistantCapabilityCall, bool]:
        """Insert proposed call or verify immutable identity on replay.

        Returns ``(call, created)``.
        """
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production ledger.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer(
            "CapabilityCallRepository.create_or_verify_proposed"
        )
        ts = now or utcnow()
        run = self.get_run(spec.run_id, for_update=True)
        self._verify_lease(run, spec.lease)
        self._require_run_revision(run, spec.expected_run_revision)

        existing = self.get_call_by_logical_key(
            run_id=spec.run_id,
            logical_call_key=spec.logical_call_key,
            for_update=True,
        )
        if existing is not None:
            if not self._identity_matches(existing, spec):
                raise CapabilityCallConflict(
                    CODE_IDENTITY_MISMATCH,
                    "replayed logical_call_key with mismatched immutable identity",
                    call=existing,
                    run=run,
                )
            # A deterministic caller-owned id is part of replay identity.  A
            # different stored id must never be adopted merely because the
            # remaining logical-key fields happen to match.
            if existing.id != spec.call_id and spec.call_id is not None:
                raise CapabilityCallConflict(
                    CODE_IDENTITY_MISMATCH,
                    "replayed logical_call_key with mismatched call_id",
                    call=existing,
                    run=run,
                )
            return existing, False

        call = AssistantCapabilityCall(
            id=spec.call_id,
            run_id=spec.run_id,
            manifest_revision_id=spec.manifest_revision_id,
            provider_tool_call_id=spec.provider_tool_call_id,
            parent_call_id=spec.parent_call_id,
            logical_call_key=spec.logical_call_key,
            owner_kind=spec.owner_kind,
            owner_id=spec.owner_id,
            owner_version_id=spec.owner_version_id,
            capability_type=spec.capability_type,
            domain_key=spec.domain_key,
            target_id=spec.target_id,
            target_version_id=spec.target_version_id,
            descriptor_digest=spec.descriptor_digest,
            authorization_digest=spec.authorization_digest,
            approval_binding_digest=spec.approval_binding_digest,
            input_artifact_id=spec.input_artifact_id,
            input_digest=spec.input_digest,
            side_effect_class=spec.side_effect_class,
            execution_mode=spec.execution_mode,
            idempotency_key=spec.idempotency_key,
            status="proposed",
            state_revision=0,
            attempt_count=0,
            created_at=ts,
            updated_at=ts,
        )
        self.db.add(call)
        self.db.flush()
        return call, True

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition_call(
        self,
        *,
        call_id: UUID,
        expected_call_revision: int,
        expected_run_revision: int,
        to_status: str,
        lease: LeaseToken | None,
        output_artifact_id: UUID | None = None,
        failure_code: str | None = None,
        approval_binding_digest: str | None = None,
        side_effect_started_at: datetime | None = None,
        interrupt_id: UUID | None = None,
        has_retry_same_key_authorization: bool = False,
        allow_while_cancelling: bool = False,
        now: datetime | None = None,
    ) -> AssistantCapabilityCall:
        """CAS transition with Run + call revision checks."""
        ts = now or utcnow()
        call_hint = self.get_call(call_id)
        if call_hint is None:
            raise CapabilityCallConflict(CODE_CALL_NOT_FOUND, f"call {call_id} not found")
        # Global lock order is Run -> Interrupt/CapabilityCall. Resolve the
        # parent id without a row lock, serialize on Run, then refresh+lock the
        # call so a blocked Session cannot act on identity-map stale state.
        run = self.get_run(call_hint.run_id, for_update=True)
        call = self.get_call(call_id, for_update=True)
        if call is None:
            raise CapabilityCallConflict(CODE_CALL_NOT_FOUND, f"call {call_id} not found")
        self._verify_lease(run, lease)
        self._require_run_revision(run, expected_run_revision)
        self._require_call_revision(call, expected_call_revision)

        if (
            str(run.status) == "cancelling"
            and not allow_while_cancelling
            and to_status not in {"cancelled", "unknown", "needs_reconciliation", "succeeded", "failed"}
        ):
            raise CapabilityCallConflict(
                CODE_RUN_CANCELLING,
                "ordinary call transition blocked while run is cancelling",
                call=call,
                run=run,
            )

        effect_set = call.side_effect_started_at is not None or side_effect_started_at is not None
        try:
            validate_call_transition(
                from_status=str(call.status),
                to_status=to_status,
                side_effect_started_at_is_set=bool(
                    call.side_effect_started_at is not None
                ),
                execution_mode=str(call.execution_mode),
                has_retry_same_key_authorization=has_retry_same_key_authorization,
            )
        except CallTransitionError as exc:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION, exc.message, call=call, run=run
            ) from exc

        # local_transactional: may only set side_effect_started_at when succeeding.
        if side_effect_started_at is not None:
            if call.side_effect_started_at is not None:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "side_effect_started_at is irreversible",
                    call=call,
                    run=run,
                )
            if (
                str(call.execution_mode) == "local_transactional"
                and to_status != "succeeded"
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "local_transactional may set side_effect_started_at only on succeeded",
                    call=call,
                    run=run,
                )
            call.side_effect_started_at = side_effect_started_at

        if approval_binding_digest is not None:
            if (
                call.approval_binding_digest is not None
                and call.approval_binding_digest != approval_binding_digest
            ):
                raise CapabilityCallConflict(
                    CODE_IDENTITY_MISMATCH,
                    "approval_binding_digest is immutable once set",
                    call=call,
                    run=run,
                )
            call.approval_binding_digest = approval_binding_digest

        if interrupt_id is not None:
            call.interrupt_id = interrupt_id

        call.status = to_status
        call.state_revision = int(call.state_revision) + 1
        call.updated_at = ts
        if failure_code is not None:
            call.failure_code = failure_code
        if output_artifact_id is not None:
            call.output_artifact_id = output_artifact_id
        if is_terminal_call_status(to_status):
            call.terminal_at = ts
        self.db.flush()
        return call

    def claim_attempt(
        self,
        *,
        call_id: UUID,
        expected_call_revision: int,
        expected_run_revision: int,
        lease: LeaseToken,
        worker_id: str,
        dispatch_deadline_at: datetime | None = None,
        mark_side_effect_started: bool = False,
        now: datetime | None = None,
    ) -> tuple[AssistantCapabilityCall, AssistantCapabilityCallAttempt]:
        """Transition authorized -> executing and append a claimed Attempt."""
        ts = now or utcnow()
        side_effect_started_at = None
        if mark_side_effect_started:
            call_hint = self.get_call(call_id)
            if call_hint is None:
                raise CapabilityCallConflict(
                    CODE_CALL_NOT_FOUND, f"call {call_id} not found"
                )
            if str(call_hint.execution_mode) not in _EXTERNAL_EFFECT_EXECUTION_MODES:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "effect-start claim requires an external execution mode",
                    call=call_hint,
                )
            side_effect_started_at = ts
        call = self.transition_call(
            call_id=call_id,
            expected_call_revision=expected_call_revision,
            expected_run_revision=expected_run_revision,
            to_status="executing",
            lease=lease,
            side_effect_started_at=side_effect_started_at,
            now=ts,
        )
        attempt_number = int(call.attempt_count) + 1
        call.attempt_count = attempt_number
        # transition_call already advanced state_revision once; claiming an attempt
        # is part of the same logical mutation — bump again for attempt evidence.
        call.state_revision = int(call.state_revision) + 1
        call.updated_at = ts
        attempt = AssistantCapabilityCallAttempt(
            id=uuid4(),
            call_id=call.id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            lease_generation=int(lease.lease_generation),
            status="claimed",
            started_at=ts,
            dispatch_deadline_at=dispatch_deadline_at,
            side_effect_started=mark_side_effect_started,
            side_effect_started_at=side_effect_started_at,
            created_at=ts,
        )
        self.db.add(attempt)
        self.db.flush()
        return call, attempt

    def transition_attempt(
        self,
        *,
        attempt_id: UUID,
        expected_status: str,
        to_status: str,
        request_digest: str | None = None,
        response_digest: str | None = None,
        error_code: str | None = None,
        ended_at: datetime | None = None,
        now: datetime | None = None,
    ) -> AssistantCapabilityCallAttempt:
        """Advance one Attempt through its append-only evidence lifecycle."""
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.id == attempt_id)
            .with_for_update()
            .one_or_none()
        )
        if attempt is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"attempt {attempt_id} not found"
            )
        current = str(attempt.status)
        if current != expected_status:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"expected attempt status {expected_status!r}, got {current!r}",
            )
        if to_status not in _ATTEMPT_TRANSITIONS.get(current, frozenset()):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"invalid attempt transition {current!r} -> {to_status!r}",
            )
        for field, value in (
            ("request_digest", request_digest),
            ("response_digest", response_digest),
        ):
            if value is None:
                continue
            existing = getattr(attempt, field)
            if existing is not None and existing != value:
                raise CapabilityCallConflict(
                    CODE_IDENTITY_MISMATCH, f"{field} is immutable once set"
                )
            setattr(attempt, field, value)
        attempt.status = to_status
        if error_code is not None:
            attempt.error_code = error_code
        if to_status in {"committed", "failed", "uncertain", "abandoned"}:
            attempt.ended_at = ended_at or now or utcnow()
        self.db.flush()
        return attempt

    def list_calls_for_run(self, run_id: UUID) -> list[AssistantCapabilityCall]:
        return (
            self.db.query(AssistantCapabilityCall)
            .filter(AssistantCapabilityCall.run_id == run_id)
            .order_by(AssistantCapabilityCall.created_at.asc())
            .all()
        )

    def has_unproven_started_calls(self, run_id: UUID) -> bool:
        """True if any call has side_effect_started_at and nonterminal/unproven status."""
        rows = self.list_calls_for_run(run_id)
        for call in rows:
            if call.side_effect_started_at is None:
                continue
            if str(call.status) in {
                "succeeded",
                "failed",
                "compensated",
            }:
                continue
            return True
        return False


__all__ = [
    "CODE_IDENTITY_MISMATCH",
    "CODE_INVALID_TRANSITION",
    "CODE_LEASE_MISMATCH",
    "CODE_CALL_NOT_FOUND",
    "CODE_RUN_CANCELLING",
    "CODE_STALE_CALL_REVISION",
    "CODE_STALE_RUN_REVISION",
    "CapabilityCallConflict",
    "CapabilityCallRepository",
    "ProposeCallSpec",
]
