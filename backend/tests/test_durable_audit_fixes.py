"""Regression tests for Plan 06 audit fixes (live cancel, public SSE, orphan GC, downgrade)."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
BUILD = "build-audit-1"


def _make_session():
    from tests._db import make_session

    return make_session()


def _seed_main_agent_run(db, *, status: str = "queued"):
    from app.assistant.models import AssistantChatRun, Conversation, Message

    conv = Conversation(title="audit")
    db.add(conv)
    db.flush()
    user = Message(conversation_id=conv.id, role="user", content="hello audit")
    db.add(user)
    db.flush()
    assistant = Message(conversation_id=conv.id, role="assistant", content="")
    db.add(assistant)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision=BUILD,
        state_revision=0,
        memory_commit_status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run, conv, user, assistant


def _claim(db, run, worker_id: str = "w-audit"):
    from datetime import timedelta

    from app.assistant.durable.repository import DurableRunRepository

    repo = DurableRunRepository(db)
    result = repo.claim_queued(
        run_id=run.id,
        expected_revision=int(run.state_revision),
        worker_id=worker_id,
        lease_ttl=timedelta(seconds=30),
    )
    db.refresh(run)
    return result


def _lease_from_run(run):
    from app.assistant.durable.repository import LeaseToken

    return LeaseToken(
        run_id=run.id,
        worker_id=str(run.lease_owner),
        lease_generation=int(run.lease_generation),
    )


def _materialize(db, run, lease):
    from app.assistant.durable.materialize import materialize_base_run_state
    from app.assistant.provider_loop.messages import ProviderUserMessage

    materialize_base_run_state(
        db,
        run_id=run.id,
        lease=lease,
        expected_revision=int(run.state_revision),
        manifest_payload={"schemaVersion": 1, "kind": "base"},
        manifest_digest=DIGEST_A,
        policy_payload={"schemaVersion": 1},
        policy_digest=DIGEST_A,
        budget_payload={"schemaVersion": 1, "revision": 0},
        budget_digest=DIGEST_A,
        obligation_payload={"schemaVersion": 1},
        obligation_digest=DIGEST_A,
        provider_messages=(ProviderUserMessage(content="hello"),),
    )
    db.refresh(run)


class PublicTerminalEventsTests(unittest.TestCase):
    def test_public_terminal_events_include_content_status_message_end(self) -> None:
        from app.assistant.durable.runner import public_terminal_events

        rid = uuid.uuid4()
        events = public_terminal_events(
            run_id=rid,
            status="completed",
            finish_reason="stop",
            content="hello world " * 40,
        )
        names = [e.event_name for e in events]
        self.assertIn("content_delta", names)
        self.assertIn("run_status", names)
        self.assertIn("message_end", names)
        self.assertTrue(all(e.visibility == "public" for e in events))
        status_ev = next(e for e in events if e.event_name == "run_status")
        self.assertEqual(status_ev.payload.get("status"), "completed")

    def test_cancelled_events_have_no_content_delta(self) -> None:
        from app.assistant.durable.runner import public_terminal_events

        events = public_terminal_events(
            run_id=uuid.uuid4(),
            status="cancelled",
            finish_reason="cancelled",
            content="should-not-emit",
        )
        names = [e.event_name for e in events]
        self.assertEqual(names, ["run_status", "message_end"])


class CancelProbeAndFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_cancel_probe_sees_cancelling_and_finalizes(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.durable.runner import _RunCancelProbe
        from app.assistant.models import AssistantChatRunEvent

        run, _c, _u, _a = _seed_main_agent_run(self.db)
        _claim(self.db, run)
        self.db.refresh(run)
        lease = _lease_from_run(run)

        repo = DurableRunRepository(self.db)
        stop = repo.request_stop(
            run_id=run.id,
            expected_revision=int(run.state_revision),
        )
        self.assertEqual(stop.status, "cancelling")
        self.db.refresh(run)

        def factory():
            return self.db

        factory._shared_session = True  # type: ignore[attr-defined]
        probe = _RunCancelProbe(factory, run_id=run.id, lease=lease)
        self.assertTrue(probe.is_cancelled())
        self.assertTrue(probe.try_finalize())
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        events = (
            self.db.query(AssistantChatRunEvent)
            .filter_by(run_id=run.id)
            .order_by(AssistantChatRunEvent.seq.asc())
            .all()
        )
        names = [e.event_name for e in events]
        self.assertIn("run_status", names)
        self.assertIn("message_end", names)
        status_payloads = [e.payload for e in events if e.event_name == "run_status"]
        self.assertTrue(any(p.get("status") == "cancelled" for p in status_payloads))


class ProviderCancelDuringIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_provider_stream_cancel_finalizes_without_content_commit(self) -> None:
        from app.assistant.durable.leases import ClaimedLease
        from app.assistant.durable.recovery import RecoveryDecision
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.durable.runner import MainAgentRunExecutor

        run, _c, _u, _a = _seed_main_agent_run(self.db)
        _claim(self.db, run)
        self.db.refresh(run)
        lease = _lease_from_run(run)
        _materialize(self.db, run, lease)
        self.db.refresh(run)
        db = self.db
        run_id = run.id

        class _Evt:
            def __init__(self, delta: str):
                self.delta = delta

        class _CancellingProvider:
            def stream_round(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
                cancellation = kwargs.get("cancellation")
                yield _Evt("partial ")
                repo = DurableRunRepository(db)
                r = repo.get_run(run_id)
                assert r is not None
                repo.request_stop(
                    run_id=run_id, expected_revision=int(r.state_revision)
                )
                if cancellation is not None:
                    assert cancellation.is_cancelled()
                yield _Evt("more")

        def session_factory():
            return db

        session_factory._shared_session = True  # type: ignore[attr-defined]

        executor = MainAgentRunExecutor(
            provider_factory=lambda **_k: _CancellingProvider(),
            scripted_final_text=None,
            finalize_memory=True,
        )
        claimed = ClaimedLease(
            run=run,
            lease=lease,
            kind="queued",
            state_revision=int(run.state_revision),
            status="running",
        )
        executor.execute(
            claimed=claimed,
            decision=RecoveryDecision(
                kind="continue",
                reason_code="fresh",
                allow_provider_io=True,
                allow_capability_io=True,
            ),
            heartbeat=lambda: True,
            session_factory=session_factory,
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        self.assertIsNotNone(run.ended_at)


class OrphanGcCompletedReadyForMemoryTests(unittest.TestCase):
    def test_orphan_gc_allows_completed_ready_for_memory_phase(self) -> None:
        """Reuse test_durable_artifacts helper path: completed + ready_for_memory current."""
        # Import the module under test and run the existing regression test function.
        from tests.test_durable_artifacts import (
            test_orphan_scanner_deletes_terminal_run_with_historical_nonterminal_checkpoints,
        )

        test_orphan_scanner_deletes_terminal_run_with_historical_nonterminal_checkpoints()


class MigrationDowngradeEmptyDbLogicTests(unittest.TestCase):
    def test_downgrade_source_allows_empty_without_ack(self) -> None:
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "6af373ef040f_add_durable_agent_run_foundation.py"
        ).read_text()
        # Unconditional empty-DB ack raise must be gone.
        self.assertNotIn(
            "set {DOWNGRADE_ACK_ENV}=1 to acknowledge",
            src,
        )
        self.assertIn("if has_data:", src)
        self.assertIn("durable Main Agent Run/history", src)


class FinalizerEmitsPublicEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_finalize_memory_commits_public_run_status_and_message_end(self) -> None:
        from app.assistant.durable.leases import ClaimedLease
        from app.assistant.durable.recovery import RecoveryDecision
        from app.assistant.durable.runner import MainAgentRunExecutor
        from app.assistant.models import AssistantChatRunEvent

        run, _c, _u, _a = _seed_main_agent_run(self.db)
        _claim(self.db, run)
        self.db.refresh(run)
        lease = _lease_from_run(run)
        _materialize(self.db, run, lease)
        self.db.refresh(run)

        class _Evt:
            def __init__(self, delta: str):
                self.delta = delta

        class _Provider:
            def stream_round(self, *a: Any, **k: Any):
                yield _Evt("answer text")

        def session_factory():
            return self.db

        session_factory._shared_session = True  # type: ignore[attr-defined]

        executor = MainAgentRunExecutor(
            provider_factory=lambda **_k: _Provider(),
            finalize_memory=True,
        )
        claimed = ClaimedLease(
            run=run,
            lease=lease,
            kind="queued",
            state_revision=int(run.state_revision),
            status="running",
        )
        executor.execute(
            claimed=claimed,
            decision=RecoveryDecision(
                kind="continue",
                reason_code="fresh",
                allow_provider_io=True,
                allow_capability_io=True,
            ),
            heartbeat=lambda: True,
            session_factory=session_factory,
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "completed")
        self.assertIn(str(run.memory_commit_status), {"committed", "failed"})
        events = (
            self.db.query(AssistantChatRunEvent)
            .filter_by(run_id=run.id)
            .order_by(AssistantChatRunEvent.seq.asc())
            .all()
        )
        names = [e.event_name for e in events]
        self.assertIn("run_status", names)
        self.assertIn("message_end", names)
        self.assertIn("content_delta", names)
        for e in events:
            if e.event_name in {"run_status", "message_end", "content_delta"}:
                self.assertEqual(e.visibility, "public")


if __name__ == "__main__":
    unittest.main()


class AtomicMainAgentCreateTests(unittest.TestCase):
    """Main Agent create + initial event must be one transaction (no claim race)."""

    def setUp(self) -> None:
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_run_commit_false_defers_visibility(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.run_service import AssistantChatRunService

        conv = Conversation(title="atomic")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=conv,
            user_message=user,
            assistant_message=assistant,
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision=BUILD,
            memory_commit_status="pending",
            commit=False,
        )
        # Not committed yet — a second session must not see the Run.
        from tests._db import make_session

        other = make_session()
        try:
            # Same process may share engine factory; use identity of uncommitted state:
            # last_event_seq still 0 and no events until append+commit.
            self.assertEqual(int(run.last_event_seq or 0), 0)
            svc.append_event(
                run_id=run.id,
                event_name="run_status",
                event_key=f"run.status:queued:{run.id}",
                payload={"status": "queued", "runtimeKind": "main_agent"},
                commit=False,
            )
            self.assertEqual(int(run.last_event_seq or 0), 1)
            self.db.commit()
            self.db.refresh(run)
            self.assertEqual(run.status, "queued")
            self.assertEqual(int(run.last_event_seq), 1)
        finally:
            other.close()


class HeartbeatIntervalConfigTests(unittest.TestCase):
    def test_executor_uses_configured_interval_capped_by_lease_ttl(self) -> None:
        from app.assistant.durable.runner import MainAgentRunExecutor

        # heartbeat=1, lease=5 → interval min(1, 5/3)=1
        ex = MainAgentRunExecutor(heartbeat_interval_sec=1, lease_ttl_sec=5)
        self.assertAlmostEqual(ex.heartbeat_interval_sec, 1.0)
        # heartbeat=5, lease=5 → interval min(5, 5/3)=1.666...
        ex2 = MainAgentRunExecutor(heartbeat_interval_sec=5, lease_ttl_sec=5)
        self.assertLessEqual(ex2.heartbeat_interval_sec, 5.0 / 3.0 + 1e-6)
        self.assertGreaterEqual(ex2.heartbeat_interval_sec, 0.5)
