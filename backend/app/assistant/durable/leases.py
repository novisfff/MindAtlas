"""Run claim / lease / heartbeat orchestration (Plan 06 Task 5 + Plan 2 Task 7).

One PostgreSQL transaction using database ``now()``:

1. select the earliest eligible Main Agent Run by ``next_attempt_at, created_at``
   with ``FOR UPDATE SKIP LOCKED``;
2. allow ``queued``, or expired ``running/recovering``, or expired ``cancelling``
   for cancellation finalization;
3. recheck compatibility on the locked row via
   ``WorkerCompatibility.from_run(run).matches(identity)`` — never rewrite Run
   requirements to match a Worker;
4. increment ``lease_generation``, set owner/heartbeat/expiry, and increment
   ``state_revision``;
5. use ``running`` for a normal queued claim and ``recovering`` for an expired
   execution claim;
6. commit and return the lease token.

Heartbeat is the only lease write that does not bump semantic revision. Zero
rows means lease lost — stop before any additional adapter call or semantic
commit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.assistant.models import AssistantChatRun
from app.assistant.durable.repository import (
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
    RUNTIME_KIND_MAIN_AGENT,
    STATUS_CANCELLING,
    STATUS_QUEUED,
    STATUS_RECOVERING,
    STATUS_RUNNING,
)
from app.assistant.durable.worker_registry import (
    WorkerCompatibility,
    WorkerIdentity,
)
from app.common.time import utcnow
from app.config import get_settings

logger = logging.getLogger(__name__)

ClaimKind = Literal["queued", "takeover_running", "reclaim_recovering", "reclaim_cancelling"]


class RuntimeInvariantViolation(RuntimeError):
    """Live-schema invariant broken (non-main-agent Run, etc.)."""


@dataclass(frozen=True)
class ClaimedLease:
    """Result of a successful claim / takeover / reclaim."""

    run: AssistantChatRun
    lease: LeaseToken
    kind: ClaimKind
    state_revision: int
    status: str
    events: tuple[Any, ...] = ()

    @property
    def run_id(self) -> UUID:
        return self.run.id

    @property
    def requires_recovery_classification(self) -> bool:
        """True when Checkpoint/ref validation must run before adapter I/O."""
        return self.kind in {"takeover_running", "reclaim_recovering"}

    @property
    def cancellation_only(self) -> bool:
        """True when only cancellation sealing/finalization is allowed."""
        return self.kind == "reclaim_cancelling"


def compute_retry_backoff(
    *,
    attempt: int,
    retry_base_ms: int,
    retry_max_ms: int,
) -> timedelta:
    """Bounded exponential backoff: min(retry_base * 2^attempt, retry_max)."""
    attempt = max(0, int(attempt))
    base = max(1, int(retry_base_ms))
    cap = max(base, int(retry_max_ms))
    delay_ms = min(base * (2**attempt), cap)
    return timedelta(milliseconds=delay_ms)


class RunLeaseService:
    """Claim compatible Runs and maintain leases for one worker identity."""

    # Max compatible candidates locked per claim poll. The SQL predicate below
    # filters by the worker's supported checkpoint codecs before this limit, so
    # incompatible codec heads cannot starve a later compatible Run.
    _CLAIM_CANDIDATE_BATCH = 16

    def __init__(
        self,
        db: Session,
        *,
        identity: WorkerIdentity,
        lease_ttl: timedelta | None = None,
        retry_base_ms: int | None = None,
        retry_max_ms: int | None = None,
        schema_compatibility: Any | None = None,
    ) -> None:
        self.db = db
        self.identity = identity
        self.repo = DurableRunRepository(db)
        self.schema_compatibility = schema_compatibility
        s = get_settings()
        self.lease_ttl = lease_ttl or timedelta(
            seconds=int(s.assistant_worker_lease_ttl_sec)
        )
        self.retry_base_ms = (
            int(retry_base_ms)
            if retry_base_ms is not None
            else int(s.assistant_worker_retry_base_ms)
        )
        self.retry_max_ms = (
            int(retry_max_ms)
            if retry_max_ms is not None
            else int(s.assistant_worker_retry_max_ms)
        )

    # ------------------------------------------------------------------
    # Claim
    # ------------------------------------------------------------------

    def claim_next(self, *, draining: bool = False) -> ClaimedLease | None:
        """Claim one eligible compatible Run, or return None.

        When ``draining`` is True, no new claims are taken.
        """
        if draining:
            return None

        if not self._schema_is_compatible():
            self.db.rollback()
            return None

        now = self._db_now()
        candidate = self._select_eligible_run(now=now)
        if candidate is None:
            return None
        if not self._schema_is_compatible():
            self.db.rollback()
            return None
        return self._claim_selected(candidate, now=now)

    def claim_run(self, run_id: UUID) -> ClaimedLease | None:
        """Claim a specific Run if eligible and compatible (test/helper path)."""
        if not self._schema_is_compatible():
            self.db.rollback()
            return None
        now = self._db_now()
        run = self.repo.get_run(run_id, for_update=True)
        if run is None:
            self.db.rollback()
            return None
        if not self._is_eligible(run, now=now):
            self.db.rollback()
            return None
        if not self._is_compatible(run):
            self.db.rollback()
            return None
        if not self._schema_is_compatible():
            self.db.rollback()
            return None
        return self._claim_selected(run, now=now)

    def _schema_is_compatible(self) -> bool:
        """Recheck family-bound schema before and after candidate locking."""
        bind = getattr(self.db, "bind", None)
        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            return True
        try:
            if self.schema_compatibility is not None:
                return bool(self.schema_compatibility.is_compatible(self.db))
            from app.schema.compatibility import runtime_schema_compatibility

            return runtime_schema_compatibility().is_compatible(self.db)
        except Exception:
            return False

    def _select_eligible_run(self, *, now: datetime) -> AssistantChatRun | None:
        """Select earliest eligible compatible Main Agent Run with SKIP LOCKED.

        Locks a small ordered batch under ``FOR UPDATE SKIP LOCKED``, then walks
        candidates and claims the first fully compatible row. A codec-incompatible
        head must not abandon the poll — later compatible Runs stay claimable.
        Falls back to plain FOR UPDATE when the dialect does not support
        skip_locked (SQLite). Callers must not claim concurrency guarantees
        from SQLite tests.

        Compatibility is rechecked on each locked row via
        ``WorkerCompatibility.from_run`` — never by rewriting Run requirements
        to match this Worker.
        """
        # Eligibility prefilter (status/due/build/contract/feature/codec). Full
        # compatibility is still rechecked on every locked candidate via
        # WorkerCompatibility.from_run before claiming it.
        try:
            supported_codec_versions = tuple(
                sorted(
                    {
                        int(version)
                        for version in self.identity.supported_checkpoint_codec_versions
                    }
                )
            )
        except (TypeError, ValueError):
            self.db.rollback()
            return None
        if not supported_codec_versions:
            self.db.rollback()
            return None

        expired_lease = or_(
            AssistantChatRun.lease_expires_at.is_(None),
            AssistantChatRun.lease_expires_at <= now,
        )
        status_predicate = or_(
            AssistantChatRun.status == STATUS_QUEUED,
            and_(
                AssistantChatRun.status.in_(
                    (STATUS_RUNNING, STATUS_RECOVERING, STATUS_CANCELLING)
                ),
                expired_lease,
            ),
        )
        due_predicate = or_(
            AssistantChatRun.next_attempt_at.is_(None),
            AssistantChatRun.next_attempt_at <= now,
        )

        base_where = (
            AssistantChatRun.runtime_kind == RUNTIME_KIND_MAIN_AGENT,
            AssistantChatRun.required_app_build_revision
            == self.identity.app_build_revision,
            AssistantChatRun.runtime_contract_version
            == int(self.identity.runtime_contract_version),
            AssistantChatRun.required_capability_feature_digest
            == str(self.identity.capability_feature_digest),
            AssistantChatRun.required_checkpoint_codec_version.in_(
                supported_codec_versions
            ),
            status_predicate,
            due_predicate,
        )
        order = (
            # Nulls first for next_attempt_at (treat as due-now priority).
            AssistantChatRun.next_attempt_at.asc().nullsfirst(),
            AssistantChatRun.created_at.asc(),
        )

        # Prefer SKIP LOCKED on PostgreSQL; SQLite falls back gracefully.
        # The codec membership predicate is deliberately in SQL, before LIMIT:
        # a long prefix of codec-incompatible rows must never hide a compatible
        # Run beyond this candidate batch.
        try:
            stmt = (
                select(AssistantChatRun)
                .where(*base_where)
                .order_by(*order)
                .limit(self._CLAIM_CANDIDATE_BATCH)
                .with_for_update(skip_locked=True)
            )
            candidates = list(self.db.scalars(stmt).all())
        except Exception:
            # Dialect may not support skip_locked; retry without it.
            self.db.rollback()
            stmt = (
                select(AssistantChatRun)
                .where(*base_where)
                .order_by(*order)
                .limit(self._CLAIM_CANDIDATE_BATCH)
                .with_for_update()
            )
            candidates = list(self.db.scalars(stmt).all())

        if not candidates:
            self.db.rollback()
            return None

        # Recheck full compatibility (codec membership + feature digest + kind)
        # on each locked row via the canonical from_run path. Do not claim an
        # incompatible row; advance to the next candidate instead of aborting.
        for run in candidates:
            if self._is_compatible(run):
                return run

        self.db.rollback()
        return None

    def _is_compatible(self, run: AssistantChatRun) -> bool:
        """True when this worker identity satisfies the Run's frozen requirements.

        Consumes the Run itself via ``WorkerCompatibility.from_run`` — never
        rewrites Run requirements to match the Worker.
        """
        if str(run.runtime_kind) != RUNTIME_KIND_MAIN_AGENT:
            # Live schema must only contain main_agent; surface as invariant.
            raise RuntimeInvariantViolation(
                "non-main-agent Run in live schema"
            )
        try:
            compatibility = WorkerCompatibility.from_run(run)
        except ValueError:
            return False
        return compatibility.matches(self.identity)

    def _is_eligible(self, run: AssistantChatRun, *, now: datetime) -> bool:
        if str(run.runtime_kind) != RUNTIME_KIND_MAIN_AGENT:
            return False
        next_at = run.next_attempt_at
        if next_at is not None:
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            if next_at > now:
                return False
        status = str(run.status)
        if status == STATUS_QUEUED:
            return True
        if status in {STATUS_RUNNING, STATUS_RECOVERING, STATUS_CANCELLING}:
            expires = run.lease_expires_at
            if expires is None:
                return True
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            return expires <= now
        return False

    def _claim_selected(
        self, run: AssistantChatRun, *, now: datetime
    ) -> ClaimedLease | None:
        """Apply the status-appropriate claim/takeover transition.

        The row is already locked via FOR UPDATE. We release the lock by
        rolling back the selection transaction and re-running the CAS path
        through DurableRunRepository so state_revision/event rules stay
        centralized — but only after capturing the snapshot needed for CAS.
        """
        run_id = run.id
        expected_revision = int(run.state_revision)
        status = str(run.status)
        worker_id = self.identity.worker_id

        # Drop the SELECT FOR UPDATE lock; repository claim re-acquires it.
        self.db.rollback()

        try:
            if status == STATUS_QUEUED:
                result = self.repo.claim_queued(
                    run_id=run_id,
                    expected_revision=expected_revision,
                    worker_id=worker_id,
                    lease_ttl=self.lease_ttl,
                    events=(
                        EventSpec(
                            event_key=f"lease.claim.queued:{run_id}:{expected_revision}",
                            event_name="run.lease.claimed",
                            payload={
                                "kind": "queued",
                                "workerId": worker_id,
                            },
                            visibility="internal",
                        ),
                    ),
                )
                kind: ClaimKind = "queued"
            elif status == STATUS_RUNNING:
                result = self.repo.takeover_expired_running(
                    run_id=run_id,
                    expected_revision=expected_revision,
                    worker_id=worker_id,
                    lease_ttl=self.lease_ttl,
                    events=(
                        EventSpec(
                            event_key=(
                                f"lease.takeover.running:{run_id}:{expected_revision}"
                            ),
                            event_name="run.lease.takeover",
                            payload={
                                "kind": "takeover_running",
                                "workerId": worker_id,
                                "fromStatus": STATUS_RUNNING,
                            },
                            visibility="internal",
                        ),
                    ),
                )
                kind = "takeover_running"
            elif status == STATUS_RECOVERING:
                result = self.repo.reclaim_expired_recovering(
                    run_id=run_id,
                    expected_revision=expected_revision,
                    worker_id=worker_id,
                    lease_ttl=self.lease_ttl,
                    events=(
                        EventSpec(
                            event_key=(
                                f"lease.reclaim.recovering:{run_id}:{expected_revision}"
                            ),
                            event_name="run.lease.takeover",
                            payload={
                                "kind": "reclaim_recovering",
                                "workerId": worker_id,
                                "fromStatus": STATUS_RECOVERING,
                            },
                            visibility="internal",
                        ),
                    ),
                )
                kind = "reclaim_recovering"
            elif status == STATUS_CANCELLING:
                result = self.repo.reclaim_expired_cancelling(
                    run_id=run_id,
                    expected_revision=expected_revision,
                    worker_id=worker_id,
                    lease_ttl=self.lease_ttl,
                    events=(
                        EventSpec(
                            event_key=(
                                f"lease.reclaim.cancelling:{run_id}:{expected_revision}"
                            ),
                            event_name="run.lease.takeover",
                            payload={
                                "kind": "reclaim_cancelling",
                                "workerId": worker_id,
                                "fromStatus": STATUS_CANCELLING,
                            },
                            visibility="internal",
                        ),
                    ),
                )
                kind = "reclaim_cancelling"
            else:
                return None
        except DurableRunConflict as exc:
            # Concurrent claim/stop won; treat as no work this cycle.
            logger.info(
                "claim skipped conflict code=%s run_id=%s",
                exc.code,
                run_id,
            )
            return None

        lease = LeaseToken(
            run_id=result.run.id,
            worker_id=worker_id,
            lease_generation=int(result.run.lease_generation),
        )
        return ClaimedLease(
            run=result.run,
            lease=lease,
            kind=kind,
            state_revision=int(result.state_revision),
            status=str(result.status),
            events=result.events,
        )

    # ------------------------------------------------------------------
    # Heartbeat / lost-lease
    # ------------------------------------------------------------------

    def heartbeat(self, lease: LeaseToken) -> bool:
        """Extend lease without bumping state_revision.

        Returns False on zero-row lost-lease (caller must stop before any
        further adapter I/O or semantic commit).
        """
        return self.repo.heartbeat(
            run_id=lease.run_id,
            lease=lease,
            lease_ttl=self.lease_ttl,
        )

    def schedule_backoff(
        self,
        *,
        lease: LeaseToken,
        expected_revision: int,
        attempt: int,
        reason_code: str = "transient_error",
    ) -> DurableCommitResult:
        """Release lease and set next_attempt_at with bounded exponential backoff."""
        delay = compute_retry_backoff(
            attempt=attempt,
            retry_base_ms=self.retry_base_ms,
            retry_max_ms=self.retry_max_ms,
        )
        next_at = self._db_now() + delay
        return self.repo.schedule_backoff(
            run_id=lease.run_id,
            expected_revision=expected_revision,
            lease=lease,
            next_attempt_at=next_at,
            events=(
                EventSpec(
                    event_key=(
                        f"lease.backoff:{lease.run_id}:{expected_revision}:{attempt}"
                    ),
                    event_name="run.lease.backoff",
                    payload={
                        "attempt": int(attempt),
                        "delayMs": int(delay.total_seconds() * 1000),
                        "nextAttemptAt": next_at.isoformat(),
                        "reasonCode": reason_code,
                        "workerId": lease.worker_id,
                    },
                    visibility="internal",
                ),
            ),
        )

    # ------------------------------------------------------------------
    # Time
    # ------------------------------------------------------------------

    def _db_now(self) -> datetime:
        """Database-time clock for lease eligibility/expiry calculations."""
        try:
            value = self.db.scalar(select(func.now()))
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    return value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc)
        except Exception:
            pass
        return utcnow()


__all__ = [
    "ClaimKind",
    "ClaimedLease",
    "RunLeaseService",
    "RuntimeInvariantViolation",
    "compute_retry_backoff",
]
