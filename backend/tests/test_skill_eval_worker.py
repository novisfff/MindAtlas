"""Plan 09 Task 4 — evaluation worker claim/lease/CAS, recovery, unavailable.

Separate eval worker; never claims production Runs; SKIP LOCKED pattern;
stale lease recovery; cancel; double-count prevention; admission fail-closed.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64


class EvalWorkerUnavailableTests(unittest.TestCase):
    def test_unavailable_fails_closed_no_production_fallback(self) -> None:
        from app.assistant.evaluation.isolation import CODE_WORKER_UNAVAILABLE
        from app.assistant.evaluation.worker import (
            EvalWorkerUnavailable,
            assert_eval_worker_available,
        )

        with self.assertRaises(EvalWorkerUnavailable) as ctx:
            assert_eval_worker_available(compatible_worker_present=False)
        self.assertEqual(ctx.exception.code, CODE_WORKER_UNAVAILABLE)

        with self.assertRaises(EvalWorkerUnavailable):
            assert_eval_worker_available(
                compatible_worker_present=True,
                allow_fallback_to_production=True,
            )

        # Compatible worker present + no fallback → ok.
        assert_eval_worker_available(compatible_worker_present=True)


class EvalWorkerClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _seed_run(
        self,
        *,
        build: str = "development",
        mode: str = "interactive_scripted",
        script_steps: list | None = None,
        status: str = "queued",
    ):
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="worker",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "golden_skill",
                "case_digest": DIGEST_A,
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
        )
        cases = self.repo.list_cases(published.version_id)
        case_id = cases[0].id
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode=mode,  # type: ignore[arg-type]
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision=build,
            isolation_digest=DIGEST_A,
            runner_contract_version=1,
        )
        # Stash script + real case id for worker materialization (FK target).
        run.aggregate_metrics = {
            "eval_case_id": str(case_id),
            "script_steps": script_steps
            or [
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "noop-1",
                }
            ],
        }
        if status != "queued":
            run.status = status
        self.db.commit()
        return run, case_id

    def test_claim_next_compatible_only(self) -> None:
        run_ok, _ = self._seed_run(build="development")
        self._seed_run(build="other-build")
        claimed = self.repo.claim_next_run(
            worker_id="eval-w1",
            required_build_revision="development",
            runtime_contract_version=1,
            runner_contract_version=1,
        )
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, run_ok.id)
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.lease_owner, "eval-w1")
        self.assertEqual(claimed.attempt_count, 1)
        self.db.commit()

        # Incompatible build already left; no more compatible queued.
        claimed2 = self.repo.claim_next_run(
            worker_id="eval-w1",
            required_build_revision="development",
            runtime_contract_version=1,
            runner_contract_version=1,
        )
        self.assertIsNone(claimed2)

    def test_claim_never_touches_production_runs(self) -> None:
        """claim_next_run only queries assistant_skill_eval_run."""
        import ast
        from pathlib import Path

        from app.assistant.evaluation import repository as repo_mod

        path = Path(repo_mod.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        method = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "EvaluationRepository":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "claim_next_run":
                        method = item
                        break
        self.assertIsNotNone(method, "claim_next_run not found")
        assert method is not None
        names: set[str] = set()
        for node in ast.walk(method):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        self.assertIn("AssistantSkillEvalRun", names)
        self.assertNotIn("AssistantChatRun", names)
        self.assertNotIn("assistant_chat_run", names)

    def test_stale_lease_recovery_requeues_then_reclaims(self) -> None:
        from app.common.time import utcnow

        run, _ = self._seed_run()
        claimed = self.repo.claim_run(
            run_id=run.id,
            worker_id="old-worker",
            required_build_revision="development",
            runtime_contract_version=1,
            lease_ttl=timedelta(seconds=1),
        )
        self.assertIsNotNone(claimed)
        assert claimed is not None
        # Expire lease.
        claimed.lease_expires_at = utcnow() - timedelta(seconds=5)
        self.db.commit()

        reclaimed = self.repo.claim_next_run(
            worker_id="new-worker",
            required_build_revision="development",
            runtime_contract_version=1,
            lease_ttl=timedelta(seconds=30),
        )
        self.assertIsNotNone(reclaimed)
        assert reclaimed is not None
        self.assertEqual(reclaimed.id, run.id)
        self.assertEqual(reclaimed.lease_owner, "new-worker")
        self.assertGreaterEqual(reclaimed.attempt_count, 2)

    def test_heartbeat_requires_owner_and_cas(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError
        from app.common.time import utcnow

        run, _ = self._seed_run()
        claimed = self.repo.claim_run(
            run_id=run.id,
            worker_id="w1",
            required_build_revision="development",
            runtime_contract_version=1,
        )
        assert claimed is not None
        rev = int(claimed.state_revision)
        hb = self.repo.heartbeat_run(
            run_id=claimed.id,
            expected_revision=rev,
            lease_owner="w1",
            lease_expires_at=utcnow() + timedelta(seconds=30),
        )
        self.assertEqual(int(hb.state_revision), rev + 1)
        with self.assertRaises(EvaluationRepositoryError):
            self.repo.heartbeat_run(
                run_id=claimed.id,
                expected_revision=int(hb.state_revision),
                lease_owner="other",
                lease_expires_at=utcnow() + timedelta(seconds=30),
            )

    def test_request_cancel_and_worker_finalizes(self) -> None:
        run, _ = self._seed_run()
        claimed = self.repo.claim_run(
            run_id=run.id,
            worker_id="w1",
            required_build_revision="development",
            runtime_contract_version=1,
        )
        assert claimed is not None
        cancelled = self.repo.request_cancel_run(run_id=claimed.id)
        self.assertEqual(cancelled.status, "cancelling")
        self.assertIsNotNone(cancelled.requested_cancel_at)
        final = self.repo.transition_run(
            run_id=cancelled.id,
            expected_revision=int(cancelled.state_revision),
            to_status="cancelled",
            gate_eligible=False,
        )
        self.assertEqual(final.status, "cancelled")


class EvalWorkerExecuteTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository
        from app.assistant.evaluation.worker import (
            EvalWorkerConfig,
            EvalWorkerIdentity,
            EvaluationWorker,
        )

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)
        self.session_factory = lambda: self._session()
        self._sessions: list = []

        identity = EvalWorkerIdentity(
            worker_id="eval-test-worker",
            app_build_revision="development",
            runtime_contract_version=1,
            runner_contract_version=1,
        )
        self.worker = EvaluationWorker(
            EvalWorkerConfig(identity=identity),
            session_factory=self.session_factory,
        )

    def _session(self):
        from tests._db import make_session

        # Share the same in-memory DB by using the same engine from self.db.
        # make_session() creates a fresh SQLite memory DB each call, so reuse
        # connection bind from existing session.
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=self.db.get_bind())
        s = Session()
        self._sessions.append(s)
        return s

    def tearDown(self) -> None:
        for s in self._sessions:
            try:
                s.close()
            except Exception:
                pass
        self.db.close()

    def _seed_run(self, script_steps: list | None = None):
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="worker-exec",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "golden_skill",
                "case_digest": DIGEST_A,
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
        )
        cases = self.repo.list_cases(published.version_id)
        case_id = cases[0].id
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="interactive_scripted",
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_A,
            runner_contract_version=1,
        )
        run.aggregate_metrics = {
            "eval_case_id": str(case_id),
            "script_steps": script_steps
            or [
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "noop-1",
                },
                {
                    "capability_key": "eval.write",
                    "side_effect": "write_local",
                    "logical_call_key": "write-1",
                },
            ],
        }
        self.db.commit()
        return run, case_id

    def test_execute_run_completes_and_persists_evidence(self) -> None:
        run, case_id = self._seed_run()
        outcome = self.worker.execute_run(run.id)
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(outcome.terminal, "completed")
        self.assertTrue(outcome.gate_eligible)

        # Fresh session read-back.
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.status, "completed")
            events = repo.list_events_after(eval_run_id=run.id, after_sequence=0)
            self.assertGreaterEqual(len(events), 1)
            calls = repo.list_capability_calls(eval_run_id=run.id, eval_case_id=case_id)
            self.assertEqual(len(calls), 2)
            outcomes = {c.outcome for c in calls}
            self.assertEqual(outcomes, {"succeeded_isolated", "simulated"})
        finally:
            s.close()

    def test_recovery_does_not_double_count_logical_calls(self) -> None:
        run, case_id = self._seed_run(
            script_steps=[
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "only-once",
                }
            ]
        )
        # First execution.
        outcome1 = self.worker.execute_run(run.id)
        self.assertIsNotNone(outcome1)

        # Manually re-queue and re-execute to simulate recovery after crash
        # post-persist of capability call.
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            # Force back to queued for reclaim (test-only).
            stored.status = "queued"
            stored.lease_owner = None
            stored.lease_expires_at = None
            stored.ended_at = None
            s.commit()
        finally:
            s.close()

        # Second claim/execute: append_capability_call should skip duplicate.
        outcome2 = self.worker.execute_run(run.id)
        # May fail transition if already completed path differs; either way
        # call count must remain 1.
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            calls = repo.list_capability_calls(eval_run_id=run.id, eval_case_id=case_id)
            keys = [(c.logical_call_key, c.attempt) for c in calls]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(
                sum(1 for k, _ in keys if k == "only-once"),
                1,
            )
        finally:
            s.close()

    def test_isolation_breach_run_gate_ineligible(self) -> None:
        run, _ = self._seed_run(
            script_steps=[
                {
                    "capability_key": "bad",
                    "side_effect": "none",
                    "logical_call_key": "b1",
                    "force_tripwire_site": "EntryService.create",
                }
            ]
        )
        outcome = self.worker.execute_run(run.id)
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(outcome.terminal, "failed")
        self.assertEqual(outcome.failure_code, "isolation_breach")
        self.assertFalse(outcome.gate_eligible)

        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            self.assertEqual(stored.status, "failed")
            self.assertFalse(stored.gate_eligible)
            self.assertEqual(stored.failure_code, "isolation_breach")
        finally:
            s.close()

    def test_dataset_scripted_runs_from_published_cases(self) -> None:
        """Worker admits dataset_scripted and materializes cases (not MA loop)."""
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="scripted",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "direct_answer",
                "case_digest": DIGEST_A,
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
        )
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="dataset_scripted",
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_A,
        )
        self.db.commit()
        outcome = self.worker.execute_run(run.id)
        # Dataset path returns None from execute_run after persist (not InteractiveOutcome).
        self.assertIsNone(outcome)
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            self.assertIn(stored.status, {"completed", "failed"})
            self.assertNotEqual(stored.failure_code, "mode_not_supported")
            metrics = dict(stored.aggregate_metrics or {})
            self.assertEqual(metrics.get("mode"), "dataset_scripted")
            self.assertEqual(int(metrics.get("case_count") or 0), 1)
        finally:
            s.close()

    def test_dataset_scripted_empty_cases_fails_missing(self) -> None:
        """Empty materialization must fail as dataset_cases_missing (not mode_not_supported)."""
        from unittest.mock import patch

        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="empty",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "direct_answer",
                "case_digest": DIGEST_A,
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
        )
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="dataset_scripted",
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_A,
        )
        self.db.commit()
        with patch.object(
            self.worker, "_materialize_structural_test_outcomes", return_value=[]
        ):
            outcome = self.worker.execute_run(run.id)
        self.assertIsNone(outcome)
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.failure_code, "dataset_cases_missing")
            self.assertFalse(stored.gate_eligible)
        finally:
            s.close()

    def test_non_interactive_mode_fails_without_production_fallback(self) -> None:
        # dataset_live remains unsupported on worker; fail closed, not production fallback.
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="live",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "golden_skill",
                "case_digest": DIGEST_A,
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
        )
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="dataset_live",
            isolation_namespace_id=uuid.uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_A,
        )
        self.db.commit()
        outcome = self.worker.execute_run(run.id)
        self.assertIsNone(outcome)
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.failure_code, "mode_not_supported")
        finally:
            s.close()

    def test_event_replay_after_sequence(self) -> None:
        run, _ = self._seed_run()
        self.worker.execute_run(run.id)
        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            all_events = repo.list_events_after(eval_run_id=run.id, after_sequence=0)
            self.assertGreaterEqual(len(all_events), 1)
            mid = all_events[0].sequence
            rest = repo.list_events_after(eval_run_id=run.id, after_sequence=mid)
            self.assertEqual(
                [e.sequence for e in rest],
                [e.sequence for e in all_events if e.sequence > mid],
            )
            # Production endpoints must not find eval IDs — helper documents contract.
            from app.assistant.evaluation.contracts import assert_not_evaluation_id

            with self.assertRaises(ValueError):
                assert_not_evaluation_id(entity="run", value=run.id)
        finally:
            s.close()

    def test_heartbeat_called_during_execute(self) -> None:
        """In-run heartbeat must run from _execute_claimed / step boundaries."""
        from unittest.mock import patch

        run, _ = self._seed_run(
            script_steps=[
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "hb1",
                },
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "hb2",
                },
            ]
        )
        # Force heartbeat interval to 0 so every step boundary fires.
        self.worker.cfg.heartbeat_interval_sec = 0
        with patch.object(
            self.worker, "_heartbeat", wraps=self.worker._heartbeat
        ) as hb_spy:
            outcome = self.worker.execute_run(run.id)
        self.assertIsNotNone(outcome)
        self.assertGreaterEqual(hb_spy.call_count, 1)

        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            stored = repo.get_run(run.id)
            assert stored is not None
            self.assertIsNotNone(stored.heartbeat_at)
        finally:
            s.close()

    def test_recovery_does_not_duplicate_events(self) -> None:
        """Idempotent event recovery: re-execute must not re-append same sequences."""
        run, case_id = self._seed_run(
            script_steps=[
                {
                    "capability_key": "eval.noop",
                    "side_effect": "none",
                    "logical_call_key": "evt-once",
                }
            ]
        )
        outcome1 = self.worker.execute_run(run.id)
        self.assertIsNotNone(outcome1)

        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            events_first = repo.list_events_after(eval_run_id=run.id, after_sequence=0)
            first_count = len(events_first)
            self.assertGreaterEqual(first_count, 1)
            first_seqs = [e.sequence for e in events_first]

            # Force re-queue for recovery.
            stored = repo.get_run(run.id)
            assert stored is not None
            stored.status = "queued"
            stored.lease_owner = None
            stored.lease_expires_at = None
            stored.ended_at = None
            s.commit()
        finally:
            s.close()

        self.worker.execute_run(run.id)

        s = self._session()
        try:
            from app.assistant.evaluation.repository import EvaluationRepository

            repo = EvaluationRepository(s)
            events_second = repo.list_events_after(eval_run_id=run.id, after_sequence=0)
            second_seqs = [e.sequence for e in events_second]
            # Sequences remain unique; no duplicate (run_id, sequence) rows.
            self.assertEqual(len(second_seqs), len(set(second_seqs)))
            # Existing sequences from first run must still be present exactly once.
            for seq in first_seqs:
                self.assertEqual(second_seqs.count(seq), 1)
            # Capability call still unique.
            calls = repo.list_capability_calls(eval_run_id=run.id, eval_case_id=case_id)
            keys = [(c.logical_call_key, c.attempt) for c in calls]
            self.assertEqual(len(keys), len(set(keys)))
        finally:
            s.close()

    def test_production_get_run_rejects_eval_ids(self) -> None:
        """Real production Run lookup helpers 404/reject evaluation-namespace IDs."""
        run, _ = self._seed_run()
        self.db.commit()

        from app.assistant.evaluation.contracts import reject_if_evaluation_id
        from app.assistant.run_service import AssistantChatRunService

        # Shared reject helper detects membership via eval tables.
        with self.assertRaises(ValueError) as ctx:
            reject_if_evaluation_id(self.db, entity="run", value=run.id)
        self.assertIn("evaluation identifiers", str(ctx.exception))

        # Real production service get_run path.
        run_svc = AssistantChatRunService(self.db)
        with self.assertRaises(ValueError) as ctx2:
            run_svc.get_run(conversation_id=uuid.uuid4(), run_id=run.id)
        self.assertIn("evaluation identifiers", str(ctx2.exception))

        # Durable repository get_run path.
        from app.assistant.durable.repository import DurableRunRepository

        durable = DurableRunRepository(self.db)
        with self.assertRaises(ValueError) as ctx3:
            durable.get_run(run.id)
        self.assertIn("evaluation identifiers", str(ctx3.exception))

        # list_events_after also rejects eval run ids.
        with self.assertRaises(ValueError):
            run_svc.list_events_after(run_id=run.id, after_seq=0)

        # Unknown (non-eval) UUID is not rejected by membership probe.
        unknown = uuid.uuid4()
        reject_if_evaluation_id(self.db, entity="run", value=unknown)  # no raise
        self.assertIsNone(run_svc.get_run(conversation_id=uuid.uuid4(), run_id=unknown))


if __name__ == "__main__":
    unittest.main()
