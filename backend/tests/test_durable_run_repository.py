"""SQLite unit tests for Plan 06 durable Run CAS repository (Task 3).

These tests exercise transition table correctness, event-key idempotency,
revision CAS, lease verification, and child-append atomicity on a single
session. They do NOT claim concurrency guarantees — PostgreSQL two-session
proof lives in ``test_durable_run_events_postgres.py``.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _make_main_agent_run(db, *, status: str = "queued", **kwargs: Any):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-test-1",
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _lease_for(run, worker_id: str = "worker-1") -> Any:
    from app.assistant.durable.repository import LeaseToken

    return LeaseToken(
        run_id=run.id,
        worker_id=worker_id,
        lease_generation=int(run.lease_generation),
    )


def _checkpoint_row(run_id, *, phase: str = "ready_for_provider", sequence: int = 1):
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    # Caller must flush parent revisions first; this helper builds all four
    # revisions + one checkpoint for pointer tests.
    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=sequence,
        manifest_digest=DIGEST_A if sequence == 1 else DIGEST_B,
        schema_version=1,
        payload={"k": sequence},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run_id,
        revision=sequence,
        policy_digest=DIGEST_A if sequence == 1 else DIGEST_B,
        payload={"p": sequence},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run_id,
        revision=sequence,
        budget_digest=DIGEST_A if sequence == 1 else DIGEST_B,
        payload={"b": sequence},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run_id,
        revision=sequence,
        obligation_digest=DIGEST_A if sequence == 1 else DIGEST_B,
        payload={"o": sequence},
    )
    return manifest, policy, budget, obligation, phase


class DurableRunRepositoryUnitTests(unittest.TestCase):
    """Single-session SQLite unit coverage. No concurrency claims."""

    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _repo(self):
        from app.assistant.durable.repository import DurableRunRepository

        return DurableRunRepository(self.db)

    # ------------------------------------------------------------------
    # Transition table / CAS
    # ------------------------------------------------------------------

    def test_claim_queued_to_running_increments_revision_and_lease(self) -> None:
        from app.assistant.durable.repository import EventSpec

        run = _make_main_agent_run(self.db, status="queued")
        repo = self._repo()
        result = repo.claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id="worker-a",
            lease_ttl=timedelta(seconds=30),
            events=[
                EventSpec(
                    event_key="run.claimed:1",
                    event_name="run.claimed",
                    payload={"worker": "worker-a"},
                )
            ],
        )
        self.assertEqual(result.status, "running")
        self.assertEqual(result.state_revision, 1)
        self.assertEqual(result.run.lease_owner, "worker-a")
        self.assertEqual(result.run.lease_generation, 1)
        self.assertEqual(result.run.last_event_seq, 1)
        self.assertEqual(result.inserted_event_keys, ("run.claimed:1",))
        self.assertIsNotNone(result.run.started_at)

    def test_stale_revision_rejected(self) -> None:
        from app.assistant.durable.repository import (
            CODE_STALE_REVISION,
            DurableRunConflict,
        )

        run = _make_main_agent_run(self.db, status="queued")
        repo = self._repo()
        repo.claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id="w1",
            lease_ttl=timedelta(seconds=30),
        )
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.claim_queued(
                run_id=run.id,
                expected_revision=0,
                worker_id="w2",
                lease_ttl=timedelta(seconds=30),
            )
        self.assertEqual(ctx.exception.code, CODE_STALE_REVISION)
        self.db.refresh(run)
        self.assertEqual(run.state_revision, 1)
        self.assertEqual(run.lease_owner, "w1")

    def test_invalid_source_status_rejected(self) -> None:
        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            DurableRunConflict,
        )

        run = _make_main_agent_run(self.db, status="completed", state_revision=3)
        repo = self._repo()
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.claim_queued(
                run_id=run.id,
                expected_revision=3,
                worker_id="w1",
                lease_ttl=timedelta(seconds=30),
            )
        self.assertIn(
            ctx.exception.code,
            {CODE_INVALID_SOURCE_STATUS, "terminal_immutable"},
        )

    def test_stop_queued_direct_cancel(self) -> None:
        from app.assistant.durable.repository import EventSpec

        run = _make_main_agent_run(self.db, status="queued")
        repo = self._repo()
        result = repo.request_stop(
            run_id=run.id,
            expected_revision=0,
            events=[
                EventSpec(
                    event_key="run.stop:1",
                    event_name="run.stop",
                    payload={"reason": "user"},
                )
            ],
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.state_revision, 1)
        self.assertIsNotNone(result.run.cancel_requested_at)
        self.assertIsNotNone(result.run.ended_at)

    def test_stop_running_to_cancelling(self) -> None:
        from datetime import datetime, timezone

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=2,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        result = repo.request_stop(run_id=run.id, expected_revision=2)
        self.assertEqual(result.status, "cancelling")
        self.assertEqual(result.state_revision, 3)
        # Lease remains responsible
        self.assertEqual(result.run.lease_owner, "w1")

    def test_result_never_overwrites_cancelling(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            DurableRunConflict,
            LeaseToken,
        )

        run = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=4,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_running_result(
                run_id=run.id,
                expected_revision=4,
                lease=lease,
                target_status="completed",
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_SOURCE_STATUS)
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")
        self.assertEqual(run.state_revision, 4)

    def test_cancel_finalizer_only_from_cancelling(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            DurableRunConflict,
            LeaseToken,
        )

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.finalize_cancellation(
                run_id=run.id,
                expected_revision=1,
                lease=lease,
                require_lease=True,
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_SOURCE_STATUS)

        # stop then finalize
        repo.request_stop(run_id=run.id, expected_revision=1)
        self.db.refresh(run)
        result = repo.finalize_cancellation(
            run_id=run.id,
            expected_revision=2,
            lease=lease,
            require_lease=True,
        )
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.state_revision, 3)

    def test_lease_mismatch_rejected(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import (
            CODE_LEASE_MISMATCH,
            DurableRunConflict,
            EventSpec,
            LeaseToken,
        )

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=2,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        bad = LeaseToken(run_id=run.id, worker_id="other", lease_generation=2)
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=1,
                lease=bad,
                events=[
                    EventSpec(
                        event_key="unit.prepared:1",
                        event_name="unit.prepared",
                        payload={},
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, CODE_LEASE_MISMATCH)

        bad_gen = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        with self.assertRaises(DurableRunConflict) as ctx2:
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=1,
                lease=bad_gen,
                events=[
                    EventSpec(
                        event_key="unit.prepared:1",
                        event_name="unit.prepared",
                        payload={},
                    )
                ],
            )
        self.assertEqual(ctx2.exception.code, CODE_LEASE_MISMATCH)

    def test_terminal_immutable(self) -> None:
        from app.assistant.durable.repository import (
            CODE_TERMINAL_IMMUTABLE,
            DurableRunConflict,
        )

        run = _make_main_agent_run(self.db, status="completed", state_revision=5)
        repo = self._repo()
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.request_stop(run_id=run.id, expected_revision=5)
        self.assertEqual(ctx.exception.code, CODE_TERMINAL_IMMUTABLE)

    def test_recovering_to_running(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import EventSpec, LeaseToken

        run = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=3,
            lease_owner="w2",
            lease_generation=2,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            recovery_count=1,
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w2", lease_generation=2)
        result = repo.complete_recovery(
            run_id=run.id,
            expected_revision=3,
            lease=lease,
            events=[
                EventSpec(
                    event_key="run.recovered:1",
                    event_name="run.recovered",
                    payload={},
                )
            ],
        )
        self.assertEqual(result.status, "running")
        self.assertEqual(result.state_revision, 4)

    def test_takeover_requires_expired_lease(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import (
            CODE_LEASE_MISMATCH,
            DurableRunConflict,
        )

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="old",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.takeover_expired_running(
                run_id=run.id,
                expected_revision=1,
                worker_id="new",
                lease_ttl=timedelta(seconds=30),
            )
        self.assertEqual(ctx.exception.code, CODE_LEASE_MISMATCH)

        # Expire and succeed
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.db.commit()
        result = repo.takeover_expired_running(
            run_id=run.id,
            expected_revision=1,
            worker_id="new",
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(result.status, "recovering")
        self.assertEqual(result.run.lease_owner, "new")
        self.assertEqual(result.run.lease_generation, 2)
        self.assertEqual(result.run.recovery_count, 1)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def test_event_key_required_for_main_agent(self) -> None:
        from app.assistant.durable.repository import EventSpec

        with self.assertRaises(ValueError):
            EventSpec(event_key="", event_name="x", payload={})
        with self.assertRaises(ValueError):
            EventSpec(event_key="   ", event_name="x", payload={})

    def test_identical_event_key_replay_reuses_without_advancing_seq(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import EventSpec, LeaseToken

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        spec = EventSpec(
            event_key="unit.prepared:lu-1",
            event_name="unit.prepared",
            payload={"logical_unit_id": "lu-1"},
        )
        r1 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            events=[spec],
        )
        self.assertEqual(r1.run.last_event_seq, 1)
        self.assertEqual(r1.inserted_event_keys, ("unit.prepared:lu-1",))
        self.assertEqual(r1.reused_event_keys, ())

        r2 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=2,
            lease=lease,
            events=[spec],
        )
        # Sequence does not advance on identical replay; revision still bumps
        # because the semantic CAS transition committed.
        self.assertEqual(r2.run.last_event_seq, 1)
        self.assertEqual(r2.reused_event_keys, ("unit.prepared:lu-1",))
        self.assertEqual(r2.inserted_event_keys, ())
        self.assertEqual(r2.state_revision, 3)

    def test_conflicting_event_key_advances_nothing(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import (
            CODE_EVENT_KEY_CONFLICT,
            DurableRunConflict,
            EventSpec,
            LeaseToken,
        )
        from app.assistant.models import AssistantChatRunEvent

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            events=[
                EventSpec(
                    event_key="unit.prepared:lu-1",
                    event_name="unit.prepared",
                    payload={"v": 1},
                )
            ],
        )
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=2,
                lease=lease,
                events=[
                    EventSpec(
                        event_key="unit.prepared:lu-1",
                        event_name="unit.prepared",
                        payload={"v": 2},  # different payload
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, CODE_EVENT_KEY_CONFLICT)
        self.db.refresh(run)
        self.assertEqual(run.state_revision, 2)
        self.assertEqual(run.last_event_seq, 1)
        count = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .count()
        )
        self.assertEqual(count, 1)

    def test_different_event_keys_allocate_monotonic_seq(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import EventSpec, LeaseToken

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        r1 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            events=[
                EventSpec(event_key="e:1", event_name="a", payload={}),
                EventSpec(event_key="e:2", event_name="b", payload={}),
            ],
        )
        self.assertEqual(r1.run.last_event_seq, 2)
        self.assertEqual(r1.events[0].seq, 1)
        self.assertEqual(r1.events[1].seq, 2)

    def test_failed_transition_rolls_back_event_and_seq(self) -> None:
        """Child append failure rolls back event sequence with the transition.

        SQLite unit proof of atomicity on a single session (not a concurrency claim).
        """
        from datetime import datetime, timezone

        from app.assistant.durable.models import AssistantRunManifestRevision
        from app.assistant.durable.repository import (
            DurableChildBundle,
            DurableRunConflict,
            EventSpec,
            LeaseToken,
        )
        from app.assistant.models import AssistantChatRunEvent

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)

        # First seed a valid manifest rev=1 so a conflicting digest/rev fails.
        good = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={"ok": True},
        )
        r1 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            events=[EventSpec(event_key="seed:1", event_name="seed", payload={})],
            children=DurableChildBundle(
                rows=[good],
                current_manifest_revision_id=None,  # set after flush via auto? need id
            ),
        )
        # Point manually after first commit
        self.db.refresh(run)
        # Force pointer with a second commit that only sets pointer
        self.db.refresh(good)
        run.current_manifest_revision_id = good.id
        self.db.commit()
        self.db.refresh(run)
        rev_before = int(run.state_revision)
        seq_before = int(run.last_event_seq)

        # Duplicate revision number should fail integrity and roll back event.
        bad = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,  # conflict with existing
            manifest_digest=DIGEST_B,
            schema_version=1,
            payload={"bad": True},
        )
        with self.assertRaises((IntegrityError, DurableRunConflict, Exception)):
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=rev_before,
                lease=lease,
                events=[
                    EventSpec(
                        event_key="should-not-commit",
                        event_name="ghost",
                        payload={"x": 1},
                    )
                ],
                children=DurableChildBundle(rows=[bad]),
            )

        self.db.refresh(run)
        self.assertEqual(run.state_revision, rev_before)
        self.assertEqual(run.last_event_seq, seq_before)
        ghost = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.event_key == "should-not-commit")
            .one_or_none()
        )
        self.assertIsNone(ghost)

    # ------------------------------------------------------------------
    # ready_for_memory fence
    # ------------------------------------------------------------------

    def test_ready_for_memory_blocks_stop(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.durable.repository import (
            CODE_RUN_FINALIZING,
            DurableChildBundle,
            DurableRunConflict,
            EventSpec,
            LeaseToken,
        )

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)

        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=DIGEST_A,
            payload={},
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=DIGEST_A,
            payload={},
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=DIGEST_A,
            payload={},
        )
        # Need IDs for checkpoint FKs — append revisions first via semantic,
        # then enter ready_for_memory with checkpoint.
        r0 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            children=DurableChildBundle(
                rows=[manifest, policy, budget, obligation],
            ),
        )
        self.db.refresh(manifest)
        self.db.refresh(policy)
        self.db.refresh(budget)
        self.db.refresh(obligation)

        ck = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=r0.state_revision,
            committed_state_revision=r0.state_revision + 1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_memory",
            state_payload={"phase": "ready_for_memory"},
            state_digest=DIGEST_C,
        )
        r1 = repo.enter_ready_for_memory(
            run_id=run.id,
            expected_revision=r0.state_revision,
            lease=lease,
            events=[
                EventSpec(
                    event_key="memory.ready:1",
                    event_name="memory.ready",
                    payload={},
                    visibility="internal",
                )
            ],
            children=DurableChildBundle(rows=[ck]),
        )
        self.assertEqual(r1.status, "running")
        self.assertTrue(repo.is_ready_for_memory(r1.run))

        with self.assertRaises(DurableRunConflict) as ctx:
            repo.request_stop(run_id=run.id, expected_revision=r1.state_revision)
        self.assertEqual(ctx.exception.code, CODE_RUN_FINALIZING)
        self.db.refresh(run)
        self.assertEqual(run.status, "running")

    def test_memory_finalizer_to_completed(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.durable.repository import (
            DurableChildBundle,
            EventSpec,
            LeaseToken,
        )

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)

        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=DIGEST_A,
            payload={},
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=DIGEST_A,
            payload={},
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=DIGEST_A,
            payload={},
        )
        r0 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            children=DurableChildBundle(rows=[manifest, policy, budget, obligation]),
        )
        self.db.refresh(manifest)
        self.db.refresh(policy)
        self.db.refresh(budget)
        self.db.refresh(obligation)
        ck = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=r0.state_revision,
            committed_state_revision=r0.state_revision + 1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_memory",
            state_payload={},
            state_digest=DIGEST_C,
        )
        r1 = repo.enter_ready_for_memory(
            run_id=run.id,
            expected_revision=r0.state_revision,
            lease=lease,
            children=DurableChildBundle(rows=[ck]),
        )
        r2 = repo.finalize_memory(
            run_id=run.id,
            expected_revision=r1.state_revision,
            lease=lease,
            memory_commit_status="committed",
            events=[
                EventSpec(
                    event_key="run.completed:1",
                    event_name="run.completed",
                    payload={"memory": "committed"},
                )
            ],
        )
        self.assertEqual(r2.status, "completed")
        self.assertEqual(r2.run.memory_commit_status, "committed")
        self.assertIsNotNone(r2.run.memory_committed_at)
        self.assertIsNone(r2.run.lease_owner)

    def test_heartbeat_does_not_bump_revision(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.durable.repository import LeaseToken

        run = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=7,
            lease_owner="w1",
            lease_generation=3,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        repo = self._repo()
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=3)
        ok = repo.heartbeat(
            run_id=run.id,
            lease=lease,
            lease_ttl=timedelta(seconds=30),
        )
        self.assertTrue(ok)
        self.db.refresh(run)
        self.assertEqual(run.state_revision, 7)

        lost = repo.heartbeat(
            run_id=run.id,
            lease=LeaseToken(run_id=run.id, worker_id="other", lease_generation=3),
            lease_ttl=timedelta(seconds=30),
        )
        self.assertFalse(lost)

    def test_legacy_run_rejected(self) -> None:
        from app.assistant.durable.repository import (
            CODE_NOT_MAIN_AGENT,
            DurableRunConflict,
        )
        from app.assistant.models import AssistantChatRun, Conversation

        conv = Conversation(title="legacy")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(
            conversation_id=conv.id,
            status="queued",
            runtime_kind="legacy",
        )
        self.db.add(run)
        self.db.commit()
        repo = self._repo()
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.request_stop(run_id=run.id, expected_revision=0)
        self.assertEqual(ctx.exception.code, CODE_NOT_MAIN_AGENT)

    def test_transition_table_covers_section4(self) -> None:
        from app.assistant.durable.repository import ALLOWED_TRANSITIONS, is_transition_allowed

        required = {
            ("queued", "running"),
            ("running", "recovering"),
            ("recovering", "running"),
            ("running", "waiting_approval"),
            ("running", "waiting_input"),
            ("waiting_approval", "queued"),
            ("waiting_input", "queued"),
            ("queued", "cancelled"),
            ("waiting_approval", "cancelled"),
            ("waiting_input", "cancelled"),
            ("running", "cancelling"),
            ("recovering", "cancelling"),
            ("cancelling", "cancelled"),
            ("running", "completed"),
            ("running", "failed"),
            ("recovering", "failed"),
            ("running", "needs_reconciliation"),
            ("recovering", "needs_reconciliation"),
            ("needs_reconciliation", "cancelled"),
        }
        self.assertTrue(required.issubset(set(ALLOWED_TRANSITIONS.keys())))
        self.assertFalse(is_transition_allowed("cancelling", "completed"))
        self.assertFalse(is_transition_allowed("completed", "running"))
        self.assertFalse(is_transition_allowed("failed", "queued"))

    def test_sqlite_does_not_claim_concurrency_guarantees(self) -> None:
        """Documentation assertion: this module is single-session only."""
        # Explicit marker so reviewers/CI greps can find the non-claim.
        self.assertIn("No concurrency claims", self.__class__.__doc__ or "")
        self.assertIn("do NOT claim concurrency", __doc__ or "")


if __name__ == "__main__":
    unittest.main()
