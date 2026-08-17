"""Plan 09 Task 4 — EvaluationRunner interactive_scripted paths.

Mandatory isolation + identity, fixture resolution, nested isolation-wrapped
Gateway, off|create_entry write-mode parity, cancel/crash, and safe evidence.
"""

from __future__ import annotations

import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64


class EvaluationRunnerIdentityTests(unittest.TestCase):
    def test_make_interactive_identity_pairs_namespace(self) -> None:
        from app.assistant.evaluation.runner import make_interactive_identity

        isolation, identity = make_interactive_identity()
        self.assertEqual(isolation.namespace_id, identity.namespace_id)
        self.assertEqual(identity.owner_kind, "test")
        self.assertEqual(isolation.owner_kind, "test")
        self.assertEqual(isolation.side_effect_mode, "simulate_only")
        self.assertEqual(isolation.data_mode, "fixture")

    def test_subject_run_id_conflation_rejected(self) -> None:
        from app.assistant.evaluation.contracts import EvalExecutionIdentity
        from app.assistant.evaluation.isolation import (
            CODE_OWNERSHIP_CONFLATED,
            IsolationError,
            build_isolation_context,
            eval_execution_scope,
        )

        run_id = uuid.uuid4()
        ns = uuid.uuid4()
        isolation = build_isolation_context(
            namespace_id=ns,
            subject_digest=DIGEST_A,
            dataset_version_ids=(uuid.uuid4(),),
        )
        identity = EvalExecutionIdentity(
            eval_run_id=run_id,
            eval_case_id=uuid.uuid4(),
            namespace_id=ns,
            owner_kind="test",
            subject_kind="skill_draft",
            subject_aggregate_id=run_id,  # conflated
            subject_version_id=uuid.uuid4(),
        )
        with self.assertRaises(IsolationError) as ctx:
            with eval_execution_scope(isolation=isolation, identity=identity):
                pass
        self.assertEqual(ctx.exception.code, CODE_OWNERSHIP_CONFLATED)

    def test_resolve_candidate_draft_does_not_mutate_catalog(self) -> None:
        from app.assistant.evaluation.runner import EvaluationRunner

        runner = EvaluationRunner()
        view = runner.resolve_candidate_draft(
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
            content_digest=DIGEST_A,
            binding_digest=DIGEST_A,
            draft_files=[{"path": "SKILL.md", "content": "x"}],
        )
        self.assertFalse(view["catalog_mutated"])
        self.assertEqual(view["resolved_by"], "evaluation_runner")

    def test_resolve_candidate_draft_uses_package_port_when_available(self) -> None:
        """When package/version IDs are present, resolve via real package port."""
        from app.assistant.evaluation.runner import EvaluationRunner

        package_id = uuid.uuid4()
        version_id = uuid.uuid4()
        dig_b = "b" * 64

        class FakePackagePort:
            def __init__(self) -> None:
                self.get_package_calls: list = []
                self.get_version_calls: list = []
                self.mutated = False

            def get_package(self, pid):  # type: ignore[no-untyped-def]
                self.get_package_calls.append(pid)
                return {
                    "id": str(pid),
                    "name": "demo-skill",
                    "canonical_name": "demo-skill",
                    "status": "draft",
                }

            def get_version(self, pid, vid):  # type: ignore[no-untyped-def]
                self.get_version_calls.append((pid, vid))
                return {
                    "id": str(vid),
                    "content_digest": dig_b,
                    "binding_digest": dig_b,
                    "status": "draft",
                    "sequence_no": 1,
                }

            def mutate(self) -> None:
                self.mutated = True

        port = FakePackagePort()
        runner = EvaluationRunner(package_port=port)
        view = runner.resolve_candidate_draft(
            subject_kind="skill_draft",
            subject_aggregate_id=package_id,
            subject_version_id=version_id,
            content_digest=DIGEST_A,
            binding_digest=DIGEST_A,
        )
        self.assertTrue(view["package_resolved"])
        self.assertEqual(view["resolved_by"], "package_port")
        self.assertFalse(view["catalog_mutated"])
        self.assertFalse(port.mutated)
        self.assertEqual(port.get_package_calls, [package_id])
        self.assertEqual(port.get_version_calls, [(package_id, version_id)])
        # Authoritative digests from version preferred when present.
        self.assertEqual(view["content_digest"], dig_b)
        self.assertEqual(view["binding_digest"], dig_b)
        self.assertIn("package", view)
        self.assertIn("version", view)

    def test_step_boundary_hook_invoked_per_step(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        hits: list[int] = []

        def hook() -> None:
            hits.append(1)

        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="a", side_effect="none", logical_call_key="a1"
                    ),
                    InteractiveScriptStep(
                        capability_key="b", side_effect="none", logical_call_key="b1"
                    ),
                )
            ),
            step_boundary_hook=hook,
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertEqual(len(hits), 2)

    def test_runner_delegates_allowlisted_to_inner_gateway(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        class FakeInner:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def execute(self, request, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append(
                    {
                        "capability_key": kwargs.get("capability_key"),
                        "side_effect": kwargs.get("side_effect"),
                    }
                )
                return {"status": "completed", "via": "inner"}

        inner = FakeInner()
        isolation, identity = make_interactive_identity()
        outcome = EvaluationRunner(inner_gateway=inner).run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="n", side_effect="none", logical_call_key="n1"
                    ),
                    InteractiveScriptStep(
                        capability_key="w",
                        side_effect="write_local",
                        logical_call_key="w1",
                    ),
                )
            ),
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertEqual(
            [r.outcome for r in outcome.call_records],
            ["succeeded_isolated", "simulated"],
        )
        # Only allowlisted none/compute/read should hit inner.
        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(inner.calls[0]["side_effect"], "none")


class EvaluationRunnerDispatchTests(unittest.TestCase):
    def test_none_compute_read_succeeded_isolated(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        outcome = EvaluationRunner(
            fixture_store={"k": {"title": "ok"}}
        ).run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="n", side_effect="none", logical_call_key="n1"
                    ),
                    InteractiveScriptStep(
                        capability_key="c", side_effect="compute", logical_call_key="c1"
                    ),
                    InteractiveScriptStep(
                        capability_key="r",
                        side_effect="read",
                        logical_call_key="r1",
                        arguments={"fixture_key": "k"},
                    ),
                )
            ),
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertEqual(
            [r.outcome for r in outcome.call_records],
            ["succeeded_isolated", "succeeded_isolated", "succeeded_isolated"],
        )
        self.assertTrue(outcome.gate_eligible)

    def test_write_modes_off_and_create_entry_identical(self) -> None:
        from typing import get_args, get_type_hints

        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            EvaluationRunnerConfig,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        self.assertEqual(
            get_args(get_type_hints(EvaluationRunnerConfig)["production_write_mode"]),
            ("off", "create_entry"),
        )

        steps = InteractiveScript(
            steps=(
                InteractiveScriptStep(
                    capability_key="w", side_effect="write_local", logical_call_key="w1"
                ),
                InteractiveScriptStep(
                    capability_key="d", side_effect="draft", logical_call_key="d1"
                ),
            )
        )
        isolation_a, identity_a = make_interactive_identity()
        isolation_b, identity_b = make_interactive_identity()
        out_off = EvaluationRunner(
            config=EvaluationRunnerConfig(production_write_mode="off")
        ).run_interactive_scripted(
            isolation=isolation_a, identity=identity_a, script=steps
        )
        out_create_entry = EvaluationRunner(
            config=EvaluationRunnerConfig(production_write_mode="create_entry")
        ).run_interactive_scripted(
            isolation=isolation_b, identity=identity_b, script=steps
        )
        self.assertEqual(
            [r.outcome for r in out_off.call_records],
            [r.outcome for r in out_create_entry.call_records],
        )
        self.assertEqual(
            [r.side_effect for r in out_off.call_records],
            [r.side_effect for r in out_create_entry.call_records],
        )
        self.assertEqual(out_off.terminal, out_create_entry.terminal)
        self.assertEqual(out_off.gate_eligible, out_create_entry.gate_eligible)

    def test_events_are_monotonic_and_safe(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )
        from app.assistant.evaluation.snapshots import payload_contains_hard_denied_keys

        isolation, identity = make_interactive_identity()
        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="x", side_effect="none", logical_call_key="x1"
                    ),
                )
            ),
        )
        seqs = [e["seq"] for e in outcome.events]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        for event in outcome.events:
            self.assertEqual(
                payload_contains_hard_denied_keys(event.get("payload") or {}),
                [],
            )

    def test_cancel_after_step(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="a", side_effect="none", logical_call_key="a1"
                    ),
                    InteractiveScriptStep(
                        capability_key="b", side_effect="none", logical_call_key="b1"
                    ),
                ),
                cancel_after_step=1,
            ),
        )
        self.assertEqual(outcome.terminal, "cancelled")
        self.assertEqual(len(outcome.call_records), 1)

    def test_isolation_breach_not_gate_eligible(self) -> None:
        from app.assistant.evaluation.isolation import ISOLATION_BREACH
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="bad",
                        side_effect="none",
                        force_tripwire_site="DurableRunRepository.commit",
                    ),
                )
            ),
        )
        self.assertEqual(outcome.failure_code, ISOLATION_BREACH)
        self.assertFalse(outcome.gate_eligible)
        self.assertFalse(outcome.assertion_summary.gate_eligible)


class EvaluationRunnerPersistenceShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_persist_synthetic_calls_and_events_via_repository(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        # Create dataset + run for persistence.
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="runner",
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
        self.assertGreaterEqual(len(cases), 1)
        case_id = cases[0].id
        # Rebuild identity with a real case FK target.
        from app.assistant.evaluation.contracts import EvalExecutionIdentity

        identity = EvalExecutionIdentity(
            eval_run_id=identity.eval_run_id,
            eval_case_id=case_id,
            namespace_id=isolation.namespace_id,
            owner_kind="test",
            subject_kind=identity.subject_kind,
            subject_aggregate_id=identity.subject_aggregate_id,
            subject_version_id=identity.subject_version_id,
        )
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=identity.subject_aggregate_id,
            subject_version_id=identity.subject_version_id,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_A,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="interactive_scripted",
            isolation_namespace_id=isolation.namespace_id,
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest=DIGEST_A,
            run_id=identity.eval_run_id,
        )
        self.db.commit()

        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=InteractiveScript(
                steps=(
                    InteractiveScriptStep(
                        capability_key="eval.noop",
                        side_effect="none",
                        logical_call_key="persist-1",
                    ),
                    InteractiveScriptStep(
                        capability_key="eval.write",
                        side_effect="write_local",
                        logical_call_key="persist-2",
                    ),
                )
            ),
        )
        # Persist via repository the way the worker would.
        rev = int(run.state_revision)
        run = self.repo.transition_run(
            run_id=run.id,
            expected_revision=rev,
            to_status="running",
            lease_owner="test-worker",
        )
        rev = int(run.state_revision)
        run_id = run.id
        for event in outcome.events:
            self.repo.append_event(
                eval_run_id=run_id,
                expected_run_revision=rev,
                event_type=str(event["event_type"]),
                payload=dict(event.get("payload") or {}),
            )
            current = self.repo.get_run(run_id)
            assert current is not None
            rev = int(current.state_revision)
        for record in outcome.call_records:
            self.repo.append_capability_call(
                eval_run_id=run_id,
                eval_case_id=identity.eval_case_id,
                expected_run_revision=rev,
                logical_call_key=record.logical_call_key,
                attempt=record.attempt,
                subject_kind=identity.subject_kind,
                subject_aggregate_id=identity.subject_aggregate_id,
                subject_version_id=identity.subject_version_id,
                subject_owner_digest=DIGEST_A,
                binding_digest=DIGEST_A,
                input_digest=DIGEST_A,
                descriptor_digest=DIGEST_A,
                policy_digest=DIGEST_A,
                outcome=record.outcome,
                decision_json=dict(record.decision),
                parent_ordinal=record.parent_ordinal,
                child_ordinal=record.child_ordinal,
                eval_call_id=record.eval_call_id,
            )
            current = self.repo.get_run(run_id)
            assert current is not None
            rev = int(current.state_revision)
        run = self.repo.transition_run(
            run_id=run_id,
            expected_revision=rev,
            to_status="completed",
            # Task 5: structural_synthetic default cannot become gate-eligible.
            gate_eligible=False,
            aggregate_metrics=outcome.aggregate_metrics,
        )
        self.db.commit()

        events = self.repo.list_events_after(eval_run_id=run_id, after_sequence=0)
        self.assertGreaterEqual(len(events), 1)
        seqs = [e.sequence for e in events]
        self.assertEqual(seqs, sorted(seqs))
        calls = self.repo.list_capability_calls(
            eval_run_id=run.id, eval_case_id=identity.eval_case_id
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual({c.outcome for c in calls}, {"succeeded_isolated", "simulated"})
        # Replay after sequence dedup semantics: second fetch from last seq is empty.
        last = events[-1].sequence
        more = self.repo.list_events_after(eval_run_id=run.id, after_sequence=last)
        self.assertEqual(more, [])


if __name__ == "__main__":
    unittest.main()
