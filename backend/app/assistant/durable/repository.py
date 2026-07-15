"""CAS repository for durable Main Agent Run state (Plan 06 Task 3).

One transaction API owns status mutations, lease-owned writes, pointer updates,
immutable child appends, and idempotent event sequence allocation.

Callers MUST NOT append durable child rows or advance Main Agent status/events
outside this repository. Legacy ``AssistantChatRunService`` methods remain for
``runtime_kind=legacy`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
    AssistantRunProviderMessage,
)
from app.assistant.models import AssistantChatRun, AssistantChatRunEvent
from app.common.time import utcnow

# ---------------------------------------------------------------------------
# Stable conflict / failure codes
# ---------------------------------------------------------------------------

CODE_STALE_REVISION = "stale_revision"
CODE_INVALID_SOURCE_STATUS = "invalid_source_status"
CODE_LEASE_MISMATCH = "lease_mismatch"
CODE_EVENT_KEY_CONFLICT = "event_key_conflict"
CODE_EVENT_KEY_REQUIRED = "event_key_required"
CODE_RUN_FINALIZING = "run_finalizing"
CODE_TERMINAL_IMMUTABLE = "terminal_immutable"
CODE_RUN_NOT_FOUND = "run_not_found"
CODE_NOT_MAIN_AGENT = "not_main_agent"
CODE_POINTER_MISMATCH = "pointer_mismatch"
CODE_PROTOCOL_ERROR = "protocol_error"
CODE_FORBIDDEN_TRANSITION = "forbidden_transition"
CODE_CHILD_APPEND_REJECTED = "child_append_rejected"

RUNTIME_KIND_MAIN_AGENT = "main_agent"
RUNTIME_KIND_LEGACY = "legacy"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_RECOVERING = "recovering"
STATUS_WAITING_APPROVAL = "waiting_approval"
STATUS_WAITING_INPUT = "waiting_input"
STATUS_CANCELLING = "cancelling"
STATUS_NEEDS_RECONCILIATION = "needs_reconciliation"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
ACTIVE_STATUSES = frozenset(
    {
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_RECOVERING,
        STATUS_WAITING_APPROVAL,
        STATUS_WAITING_INPUT,
        STATUS_CANCELLING,
        STATUS_NEEDS_RECONCILIATION,
    }
)
WAITING_STATUSES = frozenset({STATUS_WAITING_APPROVAL, STATUS_WAITING_INPUT})

PHASE_READY_FOR_MEMORY = "ready_for_memory"

# Exact §4 transition table: (from_status, to_status) -> rule name
# Special flags are enforced by high-level methods (lease claim vs verify, expiry).
ALLOWED_TRANSITIONS: dict[tuple[str, str], str] = {
    (STATUS_QUEUED, STATUS_RUNNING): "claim",
    (STATUS_RUNNING, STATUS_RECOVERING): "takeover_expired",
    (STATUS_RECOVERING, STATUS_RUNNING): "recovery_complete",
    (STATUS_RUNNING, STATUS_WAITING_APPROVAL): "wait_approval",
    (STATUS_RUNNING, STATUS_WAITING_INPUT): "wait_input",
    (STATUS_WAITING_APPROVAL, STATUS_QUEUED): "resume_queued",
    (STATUS_WAITING_INPUT, STATUS_QUEUED): "resume_queued",
    (STATUS_QUEUED, STATUS_CANCELLED): "direct_cancel",
    (STATUS_WAITING_APPROVAL, STATUS_CANCELLED): "direct_cancel",
    (STATUS_WAITING_INPUT, STATUS_CANCELLED): "direct_cancel",
    (STATUS_RUNNING, STATUS_CANCELLING): "stop",
    (STATUS_RECOVERING, STATUS_CANCELLING): "stop",
    (STATUS_CANCELLING, STATUS_CANCELLED): "cancel_finalizer",
    (STATUS_RUNNING, STATUS_COMPLETED): "complete",
    (STATUS_RUNNING, STATUS_FAILED): "fail",
    (STATUS_RECOVERING, STATUS_FAILED): "fail",
    (STATUS_RUNNING, STATUS_NEEDS_RECONCILIATION): "reconcile",
    (STATUS_RECOVERING, STATUS_NEEDS_RECONCILIATION): "reconcile",
    (STATUS_NEEDS_RECONCILIATION, STATUS_CANCELLED): "abandon",
}

# Transitions that verify an existing lease token (not claim/takeover which set it).
LEASE_VERIFIED_TRANSITIONS = frozenset(
    {
        "recovery_complete",
        "wait_approval",
        "wait_input",
        "cancel_finalizer",
        "complete",
        "fail",
        "reconcile",
        # semantic result / prepare / started stay in running without status change
        "semantic_running",
        "ready_for_memory",
        "memory_finalizer",
    }
)

DURABLE_CHILD_TYPES = (
    AssistantRunManifestRevision,
    AssistantRunPolicyRevision,
    AssistantRunBudgetRevision,
    AssistantRunObligationRevision,
    AssistantRunProviderMessage,
    AssistantRunCheckpoint,
    AssistantRunArtifact,
)


class DurableRunConflict(Exception):
    """Stable CAS / protocol conflict for durable Main Agent Runs."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        run: AssistantChatRun | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message or "")
        self.run = run
        self.details = dict(details or {})
        super().__init__(self.code if not self.message else f"{self.code}: {self.message}")


@dataclass(frozen=True)
class LeaseToken:
    """One lease identity: (run_id, worker_id, lease_generation)."""

    run_id: UUID
    worker_id: str
    lease_generation: int

    def __post_init__(self) -> None:
        if not str(self.worker_id or "").strip():
            raise ValueError("lease worker_id must be nonempty")
        if int(self.lease_generation) < 0:
            raise ValueError("lease_generation must be non-negative")


@dataclass(frozen=True)
class EventSpec:
    """Idempotent durable event specification (Main Agent requires event_key)."""

    event_key: str
    event_name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    visibility: str = "public"
    payload_version: int = 1

    def __post_init__(self) -> None:
        key = str(self.event_key or "").strip()
        name = str(self.event_name or "").strip()
        if not key:
            raise ValueError("event_key must be nonempty for Main Agent events")
        if not name:
            raise ValueError("event_name must be nonempty")
        if self.visibility not in ("public", "internal"):
            raise ValueError("visibility must be public|internal")
        if int(self.payload_version) <= 0:
            raise ValueError("payload_version must be positive")
        object.__setattr__(self, "event_key", key)
        object.__setattr__(self, "event_name", name)
        object.__setattr__(self, "payload", dict(self.payload or {}))


@dataclass
class DurableChildBundle:
    """Immutable child rows appended only inside a repository transaction.

    Construct ORM instances outside if convenient; the repository is the sole
    writer that ``add``s and commits them together with CAS state.
    """

    rows: list[Any] = field(default_factory=list)
    current_manifest_revision_id: UUID | None = None
    current_policy_revision_id: UUID | None = None
    current_checkpoint_id: UUID | None = None
    current_budget_revision_id: UUID | None = None
    current_obligation_revision_id: UUID | None = None


@dataclass(frozen=True)
class DurableCommitResult:
    """Outcome of a successful repository transaction."""

    run: AssistantChatRun
    state_revision: int
    status: str
    events: tuple[AssistantChatRunEvent, ...]
    reused_event_keys: tuple[str, ...]
    inserted_event_keys: tuple[str, ...]


@dataclass
class _TransitionPlan:
    run_id: UUID
    expected_revision: int
    target_status: str | None
    allowed_from: frozenset[str]
    rule_name: str
    lease: LeaseToken | None = None
    require_lease_verify: bool = False
    set_lease: bool = False
    lease_owner: str | None = None
    lease_ttl: timedelta | None = None
    require_expired_lease: bool = False
    clear_lease: bool = False
    events: tuple[EventSpec, ...] = ()
    children: DurableChildBundle | None = None
    set_cancel_requested: bool = False
    set_started_at_if_missing: bool = False
    set_ended_at: bool = False
    failure_code: str | None = None
    error_message: str | None = None
    memory_commit_status: str | None = None
    set_memory_committed_at: bool = False
    enter_ready_for_memory: bool = False
    reject_if_ready_for_memory: bool = False
    # When True, status may stay running for semantic child/event writes.
    allow_same_status: bool = False
    bump_recovery_count: bool = False
    next_attempt_at: datetime | None = None


def event_payload_digest(payload: Mapping[str, Any] | None) -> str:
    """Canonical SHA-256 digest of an event payload for idempotent key reuse."""
    return sha256_canonical_json(dict(payload or {}))


def is_transition_allowed(from_status: str, to_status: str) -> bool:
    if from_status == to_status:
        return True
    return (from_status, to_status) in ALLOWED_TRANSITIONS


class DurableRunRepository:
    """Single-writer CAS repository for ``runtime_kind=main_agent`` Runs."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
        nowait: bool = False,
    ) -> AssistantChatRun | None:
        stmt = select(AssistantChatRun).where(AssistantChatRun.id == run_id)
        if for_update:
            stmt = stmt.with_for_update(nowait=nowait)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_current_checkpoint_phase(self, run: AssistantChatRun) -> str | None:
        if run.current_checkpoint_id is None:
            return None
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        if ck is None:
            return None
        if ck.run_id != run.id:
            raise DurableRunConflict(
                CODE_POINTER_MISMATCH,
                "current_checkpoint_id does not belong to run",
                run=run,
            )
        return str(ck.phase)

    def is_ready_for_memory(self, run: AssistantChatRun) -> bool:
        return (
            str(run.status) == STATUS_RUNNING
            and self.get_current_checkpoint_phase(run) == PHASE_READY_FOR_MEMORY
        )

    # ------------------------------------------------------------------
    # High-level transitions (§4 table)
    # ------------------------------------------------------------------

    def claim_queued(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        worker_id: str,
        lease_ttl: timedelta,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """queued -> running with a new lease (compatible worker claim)."""
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RUNNING,
                allowed_from=frozenset({STATUS_QUEUED}),
                rule_name="claim",
                set_lease=True,
                lease_owner=str(worker_id).strip(),
                lease_ttl=lease_ttl,
                events=tuple(events),
                set_started_at_if_missing=True,
            )
        )

    def takeover_expired_running(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        worker_id: str,
        lease_ttl: timedelta,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """expired running -> recovering (takeover; no adapter call yet)."""
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RECOVERING,
                allowed_from=frozenset({STATUS_RUNNING}),
                rule_name="takeover_expired",
                set_lease=True,
                lease_owner=str(worker_id).strip(),
                lease_ttl=lease_ttl,
                require_expired_lease=True,
                events=tuple(events),
                bump_recovery_count=True,
            )
        )

    def reclaim_expired_recovering(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        worker_id: str,
        lease_ttl: timedelta,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """expired recovering stays recovering under a new lease (takeover reclaim).

        Status remains ``recovering`` so the worker must re-validate Checkpoint/refs
        and only then commit ``recovering -> running``.
        """
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RECOVERING,
                allowed_from=frozenset({STATUS_RECOVERING}),
                rule_name="reclaim_recovering",
                set_lease=True,
                lease_owner=str(worker_id).strip(),
                lease_ttl=lease_ttl,
                require_expired_lease=True,
                allow_same_status=True,
                events=tuple(events),
                bump_recovery_count=True,
            )
        )

    def reclaim_expired_cancelling(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        worker_id: str,
        lease_ttl: timedelta,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """expired cancelling -> take lease for cancellation-only finalization.

        Does not resume Provider/Capability work. Caller must finalize to cancelled.
        """
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_CANCELLING,
                allowed_from=frozenset({STATUS_CANCELLING}),
                rule_name="reclaim_cancelling",
                set_lease=True,
                lease_owner=str(worker_id).strip(),
                lease_ttl=lease_ttl,
                require_expired_lease=True,
                allow_same_status=True,
                events=tuple(events),
            )
        )

    def schedule_backoff(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        next_attempt_at: datetime,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """Release lease and schedule the next claim after bounded backoff.

        Status is preserved (running/recovering). The Run becomes claimable again
        only when ``next_attempt_at`` has elapsed and the lease is absent/expired.
        """
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=None,
                allowed_from=frozenset({STATUS_RUNNING, STATUS_RECOVERING}),
                rule_name="schedule_backoff",
                lease=lease,
                require_lease_verify=True,
                clear_lease=True,
                allow_same_status=True,
                next_attempt_at=next_attempt_at,
                events=tuple(events),
            )
        )

    def complete_recovery(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """recovering -> running after Checkpoint/ref validation."""
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RUNNING,
                allowed_from=frozenset({STATUS_RECOVERING}),
                rule_name="recovery_complete",
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
            )
        )

    def request_stop(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """API stop: CAS by revision/status only (no lease required).

        - queued|waiting_* -> cancelled (direct)
        - running|recovering -> cancelling
        - after ready_for_memory while running -> CODE_RUN_FINALIZING
        - terminal -> CODE_TERMINAL_IMMUTABLE
        - already cancelling with matching revision -> idempotent no-op
        """
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=None,  # resolved inside _validate/_apply for stop
                allowed_from=frozenset(
                    {
                        STATUS_QUEUED,
                        STATUS_RUNNING,
                        STATUS_RECOVERING,
                        STATUS_WAITING_APPROVAL,
                        STATUS_WAITING_INPUT,
                        STATUS_CANCELLING,
                        STATUS_NEEDS_RECONCILIATION,
                    }
                ),
                rule_name="stop_request",
                events=tuple(events),
                set_cancel_requested=True,
            )
        )

    def finalize_cancellation(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken | None = None,
        require_lease: bool = False,
        events: Sequence[EventSpec] = (),
    ) -> DurableCommitResult:
        """cancelling -> cancelled (cancellation finalizer only)."""
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_CANCELLED,
                allowed_from=frozenset({STATUS_CANCELLING}),
                rule_name="cancel_finalizer",
                lease=lease,
                require_lease_verify=bool(require_lease and lease is not None),
                events=tuple(events),
                set_cancel_requested=True,
                set_ended_at=True,
                set_started_at_if_missing=True,
                clear_lease=True,
            )
        )

    def commit_running_result(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        target_status: str,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
        failure_code: str | None = None,
        error_message: str | None = None,
    ) -> DurableCommitResult:
        """Lease-owned result: running -> completed|failed|needs_reconciliation.

        Never accepts ``cancelling`` as a source. Recovery workers must first
        commit recovering -> running.
        """
        if target_status not in {
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_NEEDS_RECONCILIATION,
        }:
            raise DurableRunConflict(
                CODE_PROTOCOL_ERROR,
                f"commit_running_result target_status invalid: {target_status}",
            )
        rule = {
            STATUS_COMPLETED: "complete",
            STATUS_FAILED: "fail",
            STATUS_NEEDS_RECONCILIATION: "reconcile",
        }[target_status]
        # completed via this path is only for non-memory terminals (e.g. empty);
        # normal happy path uses enter_ready_for_memory + finalize_memory.
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=target_status,
                allowed_from=frozenset({STATUS_RUNNING}),
                rule_name=rule,
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
                failure_code=failure_code,
                error_message=error_message,
                set_ended_at=target_status in TERMINAL_STATUSES,
                set_started_at_if_missing=True,
                # needs_reconciliation is quiescent (never auto-claimed); clear lease.
                clear_lease=target_status in TERMINAL_STATUSES
                or target_status == STATUS_NEEDS_RECONCILIATION,
                reject_if_ready_for_memory=False,
            )
        )

    def commit_recovery_terminal(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        target_status: str,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
        failure_code: str | None = None,
        error_message: str | None = None,
    ) -> DurableCommitResult:
        """recovering -> failed|needs_reconciliation|cancelling (via stop separately)."""
        if target_status not in {STATUS_FAILED, STATUS_NEEDS_RECONCILIATION}:
            raise DurableRunConflict(
                CODE_PROTOCOL_ERROR,
                f"commit_recovery_terminal target invalid: {target_status}",
            )
        rule = "fail" if target_status == STATUS_FAILED else "reconcile"
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=target_status,
                allowed_from=frozenset({STATUS_RECOVERING}),
                rule_name=rule,
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
                failure_code=failure_code,
                error_message=error_message,
                set_ended_at=True,
                set_started_at_if_missing=True,
                clear_lease=True,
            )
        )

    def commit_semantic(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """Lease-owned semantic write while status remains ``running``.

        Used by prepare/started/result unit boundaries that do not change public
        status (Checkpoint/events/children + revision bump).
        """
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RUNNING,
                allowed_from=frozenset({STATUS_RUNNING}),
                rule_name="semantic_running",
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
                allow_same_status=True,
            )
        )

    def enter_ready_for_memory(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """Persist accepted final content and enter internal ready_for_memory phase.

        Public status remains ``running``. This is the last cancellation fence:
        a later stop returns ``run_finalizing``.
        """
        if children is None or not any(
            isinstance(r, AssistantRunCheckpoint) and r.phase == PHASE_READY_FOR_MEMORY
            for r in children.rows
        ):
            raise DurableRunConflict(
                CODE_PROTOCOL_ERROR,
                "enter_ready_for_memory requires a Checkpoint child with phase=ready_for_memory",
            )
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_RUNNING,
                allowed_from=frozenset({STATUS_RUNNING}),
                rule_name="ready_for_memory",
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
                allow_same_status=True,
                enter_ready_for_memory=True,
            )
        )

    def finalize_memory(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease: LeaseToken,
        memory_commit_status: str,
        events: Sequence[EventSpec] = (),
        children: DurableChildBundle | None = None,
    ) -> DurableCommitResult:
        """ready_for_memory (phase) + running -> completed with memory outcome."""
        if memory_commit_status not in {"committed", "failed"}:
            raise DurableRunConflict(
                CODE_PROTOCOL_ERROR,
                "memory_commit_status must be committed|failed",
            )
        return self._commit(
            _TransitionPlan(
                run_id=run_id,
                expected_revision=expected_revision,
                target_status=STATUS_COMPLETED,
                allowed_from=frozenset({STATUS_RUNNING}),
                rule_name="memory_finalizer",
                lease=lease,
                require_lease_verify=True,
                events=tuple(events),
                children=children,
                memory_commit_status=memory_commit_status,
                set_memory_committed_at=True,
                set_ended_at=True,
                set_started_at_if_missing=True,
                clear_lease=True,
            )
        )

    def heartbeat(
        self,
        *,
        run_id: UUID,
        lease: LeaseToken,
        lease_ttl: timedelta,
    ) -> bool:
        """Extend lease without bumping ``state_revision``.

        Uses database ``now()`` for expiry calculations. Returns False when the
        lease is lost (zero rows updated) — callers must stop before any further
        adapter call or semantic commit.
        """
        now = self._db_now()
        expires = now + lease_ttl
        result = self.db.execute(
            update(AssistantChatRun)
            .where(
                AssistantChatRun.id == run_id,
                AssistantChatRun.lease_owner == lease.worker_id,
                AssistantChatRun.lease_generation == int(lease.lease_generation),
                AssistantChatRun.status.in_(
                    (
                        STATUS_RUNNING,
                        STATUS_RECOVERING,
                        STATUS_CANCELLING,
                        STATUS_WAITING_APPROVAL,
                        STATUS_WAITING_INPUT,
                    )
                ),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=expires,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            self.db.rollback()
            return False
        self.db.commit()
        return True

    def _db_now(self) -> datetime:
        """Database-time clock for lease calculations; falls back to process UTC."""
        try:
            value = self.db.scalar(select(func.now()))
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
        except Exception:
            pass
        return utcnow()

    # ------------------------------------------------------------------
    # Core transaction
    # ------------------------------------------------------------------

    def _commit(self, plan: _TransitionPlan) -> DurableCommitResult:
        now = self._db_now()
        run = self.get_run(plan.run_id, for_update=True)
        if run is None:
            raise DurableRunConflict(CODE_RUN_NOT_FOUND, f"run not found: {plan.run_id}")

        try:
            self._require_main_agent(run)
            self._resolve_stop_request(run, plan)
            self._validate_cas(run, plan, now=now)

            # Idempotent stop while already cancelling: no revision bump.
            if plan.rule_name == "stop_idempotent":
                # Release the FOR UPDATE lock without mutating state.
                self.db.rollback()
                # Re-load outside the aborted transaction for a clean view.
                run = self.get_run(plan.run_id) or run
                return DurableCommitResult(
                    run=run,
                    state_revision=int(run.state_revision),
                    status=str(run.status),
                    events=(),
                    reused_event_keys=(),
                    inserted_event_keys=(),
                )

            reused_keys: list[str] = []
            inserted_keys: list[str] = []
            event_rows: list[AssistantChatRunEvent] = []

            for spec in plan.events:
                event, reused = self._upsert_event(run, spec)
                event_rows.append(event)
                if reused:
                    reused_keys.append(spec.event_key)
                else:
                    inserted_keys.append(spec.event_key)

            # Plan §9: pure identical package replay (all events reused, no child
            # mutations, no status/side-effect change) advances neither status,
            # revision, nor sequence — same shape as stop_idempotent.
            if (
                not inserted_keys
                and not self._has_child_mutations(plan)
                and not self._would_mutate_run_fields(run, plan)
            ):
                self.db.rollback()
                run = self.get_run(plan.run_id) or run
                return DurableCommitResult(
                    run=run,
                    state_revision=int(run.state_revision),
                    status=str(run.status),
                    # Event ORM instances expire on rollback; keys are the contract.
                    events=(),
                    reused_event_keys=tuple(reused_keys),
                    inserted_event_keys=(),
                )

            if plan.children is not None:
                self._append_children(run, plan.children)

            new_revision = int(run.state_revision) + 1
            run.state_revision = new_revision
            if plan.target_status is not None:
                run.status = plan.target_status

            if plan.set_cancel_requested and run.cancel_requested_at is None:
                run.cancel_requested_at = now
            if plan.set_started_at_if_missing and run.started_at is None:
                run.started_at = now
            if plan.set_ended_at:
                run.ended_at = now
            if plan.failure_code is not None:
                run.failure_code = plan.failure_code
            if plan.error_message is not None:
                run.error_message = plan.error_message
            if plan.memory_commit_status is not None:
                run.memory_commit_status = plan.memory_commit_status
            if plan.set_memory_committed_at:
                run.memory_committed_at = now
            if plan.bump_recovery_count:
                run.recovery_count = int(run.recovery_count or 0) + 1
            if plan.next_attempt_at is not None:
                run.next_attempt_at = plan.next_attempt_at

            if plan.set_lease:
                owner = str(plan.lease_owner or "").strip()
                if not owner:
                    raise DurableRunConflict(CODE_PROTOCOL_ERROR, "lease_owner required for claim")
                if plan.lease_ttl is None:
                    raise DurableRunConflict(CODE_PROTOCOL_ERROR, "lease_ttl required for claim")
                run.lease_owner = owner
                run.lease_generation = int(run.lease_generation or 0) + 1
                run.heartbeat_at = now
                run.lease_expires_at = now + plan.lease_ttl
            elif plan.clear_lease:
                run.lease_owner = None
                run.lease_expires_at = None
                # keep generation monotonic; do not reset
            elif plan.require_lease_verify and plan.lease is not None and plan.lease_ttl is not None:
                # optional heartbeat refresh on semantic commit
                run.heartbeat_at = now
                run.lease_expires_at = now + plan.lease_ttl

            run.updated_at = now

            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            for ev in event_rows:
                try:
                    self.db.refresh(ev)
                except Exception:
                    pass

            return DurableCommitResult(
                run=run,
                state_revision=int(run.state_revision),
                status=str(run.status),
                events=tuple(event_rows),
                reused_event_keys=tuple(reused_keys),
                inserted_event_keys=tuple(inserted_keys),
            )
        except DurableRunConflict:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise

    def _resolve_stop_request(self, run: AssistantChatRun, plan: _TransitionPlan) -> None:
        """Map API stop to the exact §4 target and side effects."""
        if plan.rule_name != "stop_request":
            return

        status = str(run.status)
        if status in TERMINAL_STATUSES:
            raise DurableRunConflict(
                CODE_TERMINAL_IMMUTABLE,
                f"run already terminal: {status}",
                run=run,
            )
        if status == STATUS_RUNNING and self.is_ready_for_memory(run):
            raise DurableRunConflict(
                CODE_RUN_FINALIZING,
                "run is finalizing memory; stop cannot cancel accepted content",
                run=run,
            )
        if status == STATUS_CANCELLING:
            plan.rule_name = "stop_idempotent"
            plan.target_status = STATUS_CANCELLING
            plan.allow_same_status = True
            return
        if status in {STATUS_QUEUED, *WAITING_STATUSES}:
            plan.target_status = STATUS_CANCELLED
            plan.allowed_from = frozenset({STATUS_QUEUED, *WAITING_STATUSES})
            plan.rule_name = "direct_cancel"
            plan.set_ended_at = True
            plan.set_started_at_if_missing = True
            return
        if status in {STATUS_RUNNING, STATUS_RECOVERING}:
            plan.target_status = STATUS_CANCELLING
            plan.allowed_from = frozenset({STATUS_RUNNING, STATUS_RECOVERING})
            plan.rule_name = "stop"
            plan.set_ended_at = False
            return
        if status == STATUS_NEEDS_RECONCILIATION:
            plan.target_status = STATUS_CANCELLED
            plan.allowed_from = frozenset({STATUS_NEEDS_RECONCILIATION})
            plan.rule_name = "abandon"
            plan.set_ended_at = True
            plan.set_started_at_if_missing = True
            return
        raise DurableRunConflict(
            CODE_INVALID_SOURCE_STATUS,
            f"stop not allowed from status={status}",
            run=run,
        )

    def _require_main_agent(self, run: AssistantChatRun) -> None:
        if str(run.runtime_kind) != RUNTIME_KIND_MAIN_AGENT:
            raise DurableRunConflict(
                CODE_NOT_MAIN_AGENT,
                f"runtime_kind={run.runtime_kind} is not main_agent",
                run=run,
            )

    def _validate_cas(
        self,
        run: AssistantChatRun,
        plan: _TransitionPlan,
        *,
        now: datetime,
    ) -> None:
        if int(run.state_revision) != int(plan.expected_revision):
            raise DurableRunConflict(
                CODE_STALE_REVISION,
                f"expected revision {plan.expected_revision}, got {run.state_revision}",
                run=run,
            )

        status = str(run.status)
        if status in TERMINAL_STATUSES and (
            plan.target_status is not None and plan.target_status != status
        ):
            raise DurableRunConflict(
                CODE_TERMINAL_IMMUTABLE,
                f"terminal status {status} cannot transition",
                run=run,
            )

        if status not in plan.allowed_from:
            raise DurableRunConflict(
                CODE_INVALID_SOURCE_STATUS,
                f"status {status} not in allowed_from={sorted(plan.allowed_from)}",
                run=run,
            )

        target = plan.target_status
        if target is not None and target != status:
            if not is_transition_allowed(status, target):
                raise DurableRunConflict(
                    CODE_FORBIDDEN_TRANSITION,
                    f"transition {status} -> {target} not in §4 table",
                    run=run,
                )
            rule = ALLOWED_TRANSITIONS.get((status, target))
            if rule is not None and plan.rule_name not in {
                rule,
                "semantic_running",
                "ready_for_memory",
                "memory_finalizer",
                "direct_cancel",
                "stop",
                "abandon",
            }:
                # High-level methods set rule_name consistently with the table.
                pass
        elif target is not None and target == status and not plan.allow_same_status:
            raise DurableRunConflict(
                CODE_FORBIDDEN_TRANSITION,
                f"no-op status transition not allowed for rule={plan.rule_name}",
                run=run,
            )

        if plan.require_lease_verify:
            if plan.lease is None:
                raise DurableRunConflict(CODE_LEASE_MISMATCH, "lease token required", run=run)
            self._verify_lease(run, plan.lease)

        if plan.require_expired_lease:
            expires = run.lease_expires_at
            if expires is not None:
                # SQLite may return naive datetimes; normalize for comparison.
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires > now:
                    raise DurableRunConflict(
                        CODE_LEASE_MISMATCH,
                        "lease not expired; takeover denied",
                        run=run,
                    )

        if plan.rule_name == "memory_finalizer":
            if not self.is_ready_for_memory(run):
                raise DurableRunConflict(
                    CODE_INVALID_SOURCE_STATUS,
                    "memory finalizer requires running + ready_for_memory phase",
                    run=run,
                )

        if plan.reject_if_ready_for_memory and self.is_ready_for_memory(run):
            raise DurableRunConflict(
                CODE_RUN_FINALIZING,
                "run is finalizing memory",
                run=run,
            )

        # Ordinary results must not overwrite cancelling — enforced by allowed_from.

    def _verify_lease(self, run: AssistantChatRun, lease: LeaseToken) -> None:
        if lease.run_id != run.id:
            raise DurableRunConflict(
                CODE_LEASE_MISMATCH,
                "lease run_id mismatch",
                run=run,
            )
        if str(run.lease_owner or "") != str(lease.worker_id):
            raise DurableRunConflict(
                CODE_LEASE_MISMATCH,
                f"lease_owner mismatch: have={run.lease_owner!r} want={lease.worker_id!r}",
                run=run,
            )
        if int(run.lease_generation or 0) != int(lease.lease_generation):
            raise DurableRunConflict(
                CODE_LEASE_MISMATCH,
                f"lease_generation mismatch: have={run.lease_generation} want={lease.lease_generation}",
                run=run,
            )

    def _upsert_event(
        self,
        run: AssistantChatRun,
        spec: EventSpec,
    ) -> tuple[AssistantChatRunEvent, bool]:
        key = str(spec.event_key or "").strip()
        if not key:
            raise DurableRunConflict(
                CODE_EVENT_KEY_REQUIRED,
                "main_agent events require nonempty event_key",
                run=run,
            )

        existing = (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run.id,
                AssistantChatRunEvent.event_key == key,
            )
            .one_or_none()
        )
        if existing is not None:
            want_digest = event_payload_digest(spec.payload)
            have_digest = event_payload_digest(existing.payload if isinstance(existing.payload, dict) else {})
            if (
                str(existing.event_name) != str(spec.event_name)
                or want_digest != have_digest
            ):
                raise DurableRunConflict(
                    CODE_EVENT_KEY_CONFLICT,
                    f"event_key={key!r} exists with different name/payload",
                    run=run,
                    details={
                        "event_key": key,
                        "existing_name": existing.event_name,
                        "requested_name": spec.event_name,
                    },
                )
            # Identical replay: reuse without advancing sequence.
            return existing, True

        # Allocate sequence atomically with the Run row (already locked).
        next_seq = int(run.last_event_seq or 0) + 1
        run.last_event_seq = next_seq
        event = AssistantChatRunEvent(
            run_id=run.id,
            seq=next_seq,
            event_name=spec.event_name,
            payload=dict(spec.payload),
            event_key=key,
            payload_version=int(spec.payload_version),
            visibility=str(spec.visibility),
        )
        self.db.add(event)
        self.db.flush()
        return event, False

    @staticmethod
    def _has_child_mutations(plan: _TransitionPlan) -> bool:
        """True when the plan would append children or move aggregate pointers."""
        bundle = plan.children
        if bundle is None:
            return False
        if bundle.rows:
            return True
        return any(
            value is not None
            for value in (
                bundle.current_manifest_revision_id,
                bundle.current_policy_revision_id,
                bundle.current_checkpoint_id,
                bundle.current_budget_revision_id,
                bundle.current_obligation_revision_id,
            )
        )

    @staticmethod
    def _would_mutate_run_fields(run: AssistantChatRun, plan: _TransitionPlan) -> bool:
        """True when the plan would change Run status or other aggregate fields.

        Pure identical event-package replay must leave status, revision, sequence,
        and side-effect columns untouched (Plan §9).
        """
        if plan.target_status is not None and str(plan.target_status) != str(run.status):
            return True
        if plan.set_cancel_requested and run.cancel_requested_at is None:
            return True
        if plan.set_started_at_if_missing and run.started_at is None:
            return True
        if plan.set_ended_at:
            return True
        if plan.failure_code is not None and plan.failure_code != run.failure_code:
            return True
        if plan.error_message is not None and plan.error_message != run.error_message:
            return True
        if (
            plan.memory_commit_status is not None
            and plan.memory_commit_status != run.memory_commit_status
        ):
            return True
        if plan.set_memory_committed_at:
            return True
        if plan.bump_recovery_count:
            return True
        if plan.next_attempt_at is not None:
            return True
        if plan.set_lease:
            return True
        if plan.clear_lease and (
            run.lease_owner is not None or run.lease_expires_at is not None
        ):
            return True
        # Optional lease refresh on semantic commit is a real write.
        if (
            plan.require_lease_verify
            and plan.lease is not None
            and plan.lease_ttl is not None
        ):
            return True
        return False

    def _append_children(self, run: AssistantChatRun, bundle: DurableChildBundle) -> None:
        for row in bundle.rows:
            if not isinstance(row, DURABLE_CHILD_TYPES):
                raise DurableRunConflict(
                    CODE_CHILD_APPEND_REJECTED,
                    f"unsupported child type: {type(row)!r}",
                    run=run,
                )
            row_run_id = getattr(row, "run_id", None)
            if row_run_id is not None and row_run_id != run.id:
                raise DurableRunConflict(
                    CODE_POINTER_MISMATCH,
                    "child run_id does not match aggregate",
                    run=run,
                )
            if getattr(row, "run_id", None) is None and hasattr(row, "run_id"):
                row.run_id = run.id
            self.db.add(row)

        # Flush so generated IDs are available for pointer updates.
        if bundle.rows:
            self.db.flush()

        def _set_pointer(attr: str, value: UUID | None, model_type: type) -> None:
            if value is None:
                return
            obj = self.db.get(model_type, value)
            if obj is None:
                raise DurableRunConflict(
                    CODE_POINTER_MISMATCH,
                    f"{attr} target not found: {value}",
                    run=run,
                )
            if getattr(obj, "run_id", None) != run.id:
                raise DurableRunConflict(
                    CODE_POINTER_MISMATCH,
                    f"{attr} target belongs to another run",
                    run=run,
                )
            setattr(run, attr, value)

        _set_pointer(
            "current_manifest_revision_id",
            bundle.current_manifest_revision_id,
            AssistantRunManifestRevision,
        )
        _set_pointer(
            "current_policy_revision_id",
            bundle.current_policy_revision_id,
            AssistantRunPolicyRevision,
        )
        _set_pointer(
            "current_checkpoint_id",
            bundle.current_checkpoint_id,
            AssistantRunCheckpoint,
        )
        _set_pointer(
            "current_budget_revision_id",
            bundle.current_budget_revision_id,
            AssistantRunBudgetRevision,
        )
        _set_pointer(
            "current_obligation_revision_id",
            bundle.current_obligation_revision_id,
            AssistantRunObligationRevision,
        )

        # Auto-point current_checkpoint_id when a single new checkpoint is appended
        # and the caller did not set the pointer explicitly.
        if bundle.current_checkpoint_id is None:
            new_cks = [r for r in bundle.rows if isinstance(r, AssistantRunCheckpoint)]
            if len(new_cks) == 1 and new_cks[0].id is not None:
                run.current_checkpoint_id = new_cks[0].id


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CODE_CHILD_APPEND_REJECTED",
    "CODE_EVENT_KEY_CONFLICT",
    "CODE_EVENT_KEY_REQUIRED",
    "CODE_FORBIDDEN_TRANSITION",
    "CODE_INVALID_SOURCE_STATUS",
    "CODE_LEASE_MISMATCH",
    "CODE_NOT_MAIN_AGENT",
    "CODE_POINTER_MISMATCH",
    "CODE_PROTOCOL_ERROR",
    "CODE_RUN_FINALIZING",
    "CODE_RUN_NOT_FOUND",
    "CODE_STALE_REVISION",
    "CODE_TERMINAL_IMMUTABLE",
    "DurableChildBundle",
    "DurableCommitResult",
    "DurableRunConflict",
    "DurableRunRepository",
    "EventSpec",
    "LeaseToken",
    "PHASE_READY_FOR_MEMORY",
    "STATUS_CANCELLED",
    "STATUS_CANCELLING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_NEEDS_RECONCILIATION",
    "STATUS_QUEUED",
    "STATUS_RECOVERING",
    "STATUS_RUNNING",
    "STATUS_WAITING_APPROVAL",
    "STATUS_WAITING_INPUT",
    "TERMINAL_STATUSES",
    "event_payload_digest",
    "is_transition_allowed",
]
