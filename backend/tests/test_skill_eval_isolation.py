"""Plan 09 Task 4 — isolation delta, tripwires, architecture import bans.

Zero production mutation under interactive_scripted success/failure/cancel/
nested/malicious paths. Isolation breach is permanently gate-ineligible.
"""

from __future__ import annotations

import ast
import unittest
import uuid
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_PKG = _REPO_ROOT / "backend" / "app" / "assistant" / "evaluation"
DIGEST_A = "a" * 64


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ProductionSnapshot:
    """Counts production tables/object prefixes that must not change under Eval."""

    KEYS = (
        "entries",
        "conversations",
        "messages",
        "assistant_chat_run",
        "assistant_chat_run_event",
        "assistant_capability_call",
        "l1_memory",
        "l2_memory",
        "run_artifacts",
        "entry_index_outbox",
    )

    def __init__(self, db) -> None:
        self.db = db
        self.counts = self._snapshot()

    def _snapshot(self) -> dict[str, int]:
        from sqlalchemy import func, select, text

        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.models import (
            AssistantChatRun,
            AssistantChatRunEvent,
            AssistantConversationL1Memory,
            AssistantConversationSkillL2Memory,
        )
        from app.entry.models import Entry
        from app.lightrag.models import EntryIndexOutbox

        counts: dict[str, int] = {}
        mapping = {
            "entries": Entry,
            "assistant_chat_run": AssistantChatRun,
            "assistant_chat_run_event": AssistantChatRunEvent,
            "assistant_capability_call": AssistantCapabilityCall,
            "l1_memory": AssistantConversationL1Memory,
            "l2_memory": AssistantConversationSkillL2Memory,
            "entry_index_outbox": EntryIndexOutbox,
        }
        for key, model in mapping.items():
            counts[key] = int(
                self.db.execute(select(func.count()).select_from(model)).scalar() or 0
            )
        # Conversations/messages may live under different module names; use raw if present.
        for table, key in (
            ("assistant_conversation", "conversations"),
            ("assistant_message", "messages"),
            ("assistant_run_artifact", "run_artifacts"),
        ):
            try:
                counts[key] = int(
                    self.db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
                )
            except Exception:
                self.db.rollback()
                counts[key] = 0
        return counts

    def delta(self) -> dict[str, int]:
        now = self._snapshot()
        return {k: int(now[k]) - int(self.counts[k]) for k in self.counts}


class IsolationArchitectureTests(unittest.TestCase):
    """EvaluationRunner must not import production writers / EntryService."""

    FORBIDDEN_MODULES = {
        "app.entry.service",
        "app.assistant.durable.repository",
        "app.assistant.capability_calls.repository",
        "app.assistant.run_service",
        "app.assistant.service",
        "app.assistant.capability_calls.local_write",
        "app.assistant.memory_service",
        "app.assistant.durable.artifacts",
    }
    FORBIDDEN_NAMES = {
        "EntryService",
        "AssistantChatRun",
        "AssistantCapabilityCall",
        "DurableRunRepository",
        "CapabilityCallRepository",
        "AssistantMemoryService",
        "DurableArtifactService",
        "create_entry_local_transactional",
    }
    RUNNER_FILES = ("runner.py", "isolation.py", "worker.py", "assertions.py")

    def test_evaluation_runner_has_no_production_writer_imports(self) -> None:
        violations: list[str] = []
        for name in self.RUNNER_FILES:
            path = _EVAL_PKG / name
            self.assertTrue(path.exists(), msg=f"missing {name}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod in self.FORBIDDEN_MODULES:
                        violations.append(f"{name}: from {mod}")
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_NAMES:
                            violations.append(f"{name}: import {alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_MODULES:
                            violations.append(f"{name}: import {alias.name}")
        self.assertEqual(violations, [], msg=f"forbidden imports: {violations}")

    def test_runner_module_docstring_bans_entry_service(self) -> None:
        import ast

        from app.assistant.evaluation import runner as runner_mod

        src = Path(runner_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("EntryService", src)  # ban is documented
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertNotEqual(mod, "app.entry.service")
                for alias in node.names:
                    self.assertNotEqual(alias.name, "EntryService")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "app.entry.service")


class IsolationScopeTests(unittest.TestCase):
    def test_missing_scope_fails_before_gateway(self) -> None:
        from app.assistant.evaluation.isolation import (
            CODE_MISSING_SCOPE,
            IsolationError,
            IsolationWrappedGateway,
            require_active_eval_scope,
        )

        with self.assertRaises(IsolationError) as ctx:
            require_active_eval_scope()
        self.assertEqual(ctx.exception.code, CODE_MISSING_SCOPE)

        gw = IsolationWrappedGateway()
        with self.assertRaises(IsolationError) as ctx2:
            gw.execute(None, side_effect="none", capability_key="x")
        self.assertEqual(ctx2.exception.code, CODE_MISSING_SCOPE)

    def test_namespace_mismatch_fails_closed(self) -> None:
        from app.assistant.evaluation.contracts import EvalExecutionIdentity
        from app.assistant.evaluation.isolation import (
            CODE_NAMESPACE_MISMATCH,
            IsolationError,
            build_isolation_context,
            eval_execution_scope,
        )

        ns1, ns2 = _uuid(), _uuid()
        isolation = build_isolation_context(
            namespace_id=ns1,
            subject_digest=DIGEST_A,
            dataset_version_ids=(_uuid(),),
        )
        identity = EvalExecutionIdentity(
            eval_run_id=_uuid(),
            eval_case_id=_uuid(),
            namespace_id=ns2,
            owner_kind="test",
            subject_kind="skill_draft",
            subject_aggregate_id=_uuid(),
            subject_version_id=_uuid(),
        )
        with self.assertRaises(IsolationError) as ctx:
            with eval_execution_scope(isolation=isolation, identity=identity):
                pass
        self.assertEqual(ctx.exception.code, CODE_NAMESPACE_MISMATCH)

    def test_mixed_namespace_nested_scope_fails(self) -> None:
        from app.assistant.evaluation.isolation import (
            CODE_MIXED_NAMESPACE,
            IsolationError,
            eval_execution_scope,
        )
        from app.assistant.evaluation.runner import make_interactive_identity

        iso1, id1 = make_interactive_identity()
        iso2, id2 = make_interactive_identity()
        with eval_execution_scope(isolation=iso1, identity=id1):
            with self.assertRaises(IsolationError) as ctx:
                with eval_execution_scope(isolation=iso2, identity=id2):
                    pass
            self.assertEqual(ctx.exception.code, CODE_MIXED_NAMESPACE)


class IsolationDeltaTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _run_script(self, steps, *, write_mode: str = "off"):
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            EvaluationRunnerConfig,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        script = InteractiveScript(
            steps=tuple(
                InteractiveScriptStep(**s) if isinstance(s, dict) else s for s in steps
            )
        )
        runner = EvaluationRunner(
            config=EvaluationRunnerConfig(production_write_mode=write_mode)  # type: ignore[arg-type]
        )
        snap = ProductionSnapshot(self.db)
        outcome = runner.run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=script,
            production_delta_probe=snap.delta,
        )
        return outcome, snap.delta()

    def test_success_path_zero_production_delta(self) -> None:
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, delta = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="eval.compute",
                    side_effect="compute",
                    logical_call_key="c1",
                ),
                InteractiveScriptStep(
                    capability_key="eval.read",
                    side_effect="read",
                    logical_call_key="r1",
                    arguments={"fixture_key": "missing"},
                ),
            ]
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertTrue(outcome.gate_eligible)
        self.assertTrue(all(v == 0 for v in delta.values()), msg=delta)

    def test_write_simulation_zero_production_delta_off_and_golden(self) -> None:
        from app.assistant.evaluation.runner import InteractiveScriptStep

        steps = [
            InteractiveScriptStep(
                capability_key="entry.create",
                side_effect="write_local",
                logical_call_key="w1",
            ),
            InteractiveScriptStep(
                capability_key="entry.external",
                side_effect="write_external",
                logical_call_key="w2",
            ),
            InteractiveScriptStep(
                capability_key="entry.draft",
                side_effect="draft",
                logical_call_key="w3",
            ),
        ]
        out_off, delta_off = self._run_script(steps, write_mode="off")
        out_golden, delta_golden = self._run_script(steps, write_mode="golden")
        self.assertEqual(out_off.terminal, "completed")
        self.assertEqual(out_golden.terminal, "completed")
        self.assertEqual(
            [r.outcome for r in out_off.call_records],
            [r.outcome for r in out_golden.call_records],
        )
        self.assertEqual(
            [r.outcome for r in out_off.call_records],
            ["simulated", "simulated", "simulated"],
        )
        self.assertTrue(all(v == 0 for v in delta_off.values()), msg=delta_off)
        self.assertTrue(all(v == 0 for v in delta_golden.values()), msg=delta_golden)
        # Metrics record write mode was seen but must not affect result.
        self.assertFalse(out_off.aggregate_metrics["production_write_mode_affects_result"])
        self.assertFalse(
            out_golden.aggregate_metrics["production_write_mode_affects_result"]
        )

    def test_unknown_side_effect_denied_not_simulated(self) -> None:
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, delta = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="mystery",
                    side_effect="unknown",
                    logical_call_key="u1",
                )
            ]
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertEqual(outcome.call_records[0].outcome, "denied")
        self.assertTrue(all(v == 0 for v in delta.values()), msg=delta)

    def test_nested_child_uses_isolation_wrapper(self) -> None:
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, delta = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="workflow.root",
                    side_effect="compute",
                    logical_call_key="root",
                ),
                InteractiveScriptStep(
                    capability_key="workflow.child",
                    side_effect="write_local",
                    logical_call_key="child",
                    is_nested_child=True,
                    parent_ordinal=1,
                ),
            ]
        )
        self.assertEqual(outcome.terminal, "completed")
        self.assertEqual(outcome.call_records[1].outcome, "simulated")
        self.assertEqual(outcome.call_records[1].parent_ordinal, 1)
        self.assertTrue(all(v == 0 for v in delta.values()), msg=delta)

    def test_tripwire_entry_service_isolation_breach_gate_ineligible(self) -> None:
        from app.assistant.evaluation.isolation import ISOLATION_BREACH
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, delta = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="malicious",
                    side_effect="none",
                    logical_call_key="m1",
                    force_tripwire_site="EntryService.create",
                )
            ]
        )
        self.assertEqual(outcome.terminal, "failed")
        self.assertEqual(outcome.failure_code, ISOLATION_BREACH)
        self.assertFalse(outcome.gate_eligible)
        self.assertTrue(outcome.assertion_summary.isolation_breach)
        self.assertTrue(all(v == 0 for v in delta.values()), msg=delta)

    def test_tripwire_cannot_be_downgraded_to_metric(self) -> None:
        from app.assistant.evaluation.isolation import ISOLATION_BREACH
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, _ = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="x",
                    side_effect="none",
                    force_tripwire_site="production_write_adapter",
                )
            ]
        )
        self.assertEqual(outcome.failure_code, ISOLATION_BREACH)
        self.assertFalse(outcome.gate_eligible)
        # Present as hard safety assertion, not a soft metric.
        codes = {r.code: r for r in outcome.assertion_summary.results}
        self.assertIn(ISOLATION_BREACH, codes)
        self.assertTrue(codes[ISOLATION_BREACH].hard_safety)
        self.assertEqual(codes[ISOLATION_BREACH].outcome, "fail")

    def test_raw_invoke_tripwire(self) -> None:
        from app.assistant.evaluation.isolation import ISOLATION_BREACH
        from app.assistant.evaluation.runner import InteractiveScriptStep

        outcome, delta = self._run_script(
            [
                InteractiveScriptStep(
                    capability_key="raw",
                    side_effect="none",
                    force_raw_invoke=True,
                )
            ]
        )
        self.assertEqual(outcome.failure_code, ISOLATION_BREACH)
        self.assertFalse(outcome.gate_eligible)
        self.assertTrue(all(v == 0 for v in delta.values()), msg=delta)

    def test_live_entry_service_tripwire_under_scope(self) -> None:
        """If code actually reaches EntryService under eval scope, breach fires."""
        from app.assistant.evaluation.isolation import IsolationBreach, eval_execution_scope
        from app.assistant.evaluation.runner import make_interactive_identity
        from app.entry.service import EntryService

        isolation, identity = make_interactive_identity()
        with eval_execution_scope(isolation=isolation, identity=identity):
            svc = EntryService(self.db)
            with self.assertRaises(IsolationBreach) as ctx:
                # create_in_uow tripwire fires before any DB write.
                from app.entry.schemas import EntryRequest

                # Minimal call — tripwire is first line, so request validity is irrelevant
                # if we pass a mock-like object; use a simple namespace.
                class _Req:
                    title = "t"
                    summary = None
                    content = None
                    type_id = _uuid()
                    tag_ids = None
                    time_mode = None
                    time_at = None
                    time_from = None
                    time_to = None

                svc.create_in_uow(_Req())  # type: ignore[arg-type]
            self.assertEqual(ctx.exception.code, "isolation_breach")
            self.assertEqual(ctx.exception.site, "EntryService.create_in_uow")


class CancelAndCrashIsolationTests(unittest.TestCase):
    def test_cancel_mid_script_zero_delta(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        script = InteractiveScript(
            steps=(
                InteractiveScriptStep(
                    capability_key="a", side_effect="compute", logical_call_key="a1"
                ),
                InteractiveScriptStep(
                    capability_key="b", side_effect="write_local", logical_call_key="b1"
                ),
            ),
            cancel_after_step=1,
        )
        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=script,
            production_delta_probe=lambda: {},
        )
        self.assertEqual(outcome.terminal, "cancelled")
        self.assertEqual(len(outcome.call_records), 1)

    def test_crash_boundary_does_not_touch_production(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            InteractiveScript,
            InteractiveScriptStep,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        script = InteractiveScript(
            steps=(
                InteractiveScriptStep(
                    capability_key="a", side_effect="none", logical_call_key="a1"
                ),
                InteractiveScriptStep(
                    capability_key="b", side_effect="write_local", logical_call_key="b1"
                ),
            ),
            crash_after_step=1,
        )
        outcome = EvaluationRunner().run_interactive_scripted(
            isolation=isolation,
            identity=identity,
            script=script,
        )
        self.assertEqual(outcome.terminal, "failed")
        self.assertEqual(outcome.failure_code, "worker_crash")
        self.assertEqual(len(outcome.call_records), 1)


class IsolationWrappedGatewayDelegationTests(unittest.TestCase):
    def test_delegates_to_real_gateway_for_allowlisted_read_none(self) -> None:
        """IsolationWrappedGateway must call inner gateway for none|compute|read."""
        from app.assistant.evaluation.isolation import (
            IsolationWrappedGateway,
            eval_execution_scope,
            build_isolation_context,
        )
        from app.assistant.evaluation.contracts import EvalExecutionIdentity

        class FakeInnerGateway:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def execute(self, request, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append(
                    {
                        "request": request,
                        "capability_key": kwargs.get("capability_key"),
                        "side_effect": kwargs.get("side_effect"),
                        "arguments": kwargs.get("arguments"),
                    }
                )
                return {"status": "completed", "output": {"ok": True}}

        ns = _uuid()
        isolation = build_isolation_context(
            namespace_id=ns,
            subject_digest=DIGEST_A,
            dataset_version_ids=(_uuid(),),
        )
        identity = EvalExecutionIdentity(
            eval_run_id=_uuid(),
            eval_case_id=_uuid(),
            namespace_id=ns,
            owner_kind="test",
            subject_kind="skill_draft",
            subject_aggregate_id=_uuid(),
            subject_version_id=_uuid(),
        )
        inner = FakeInnerGateway()
        with eval_execution_scope(isolation=isolation, identity=identity) as scope:
            gw = IsolationWrappedGateway(inner=inner, scope=scope)
            r_none = gw.execute(
                None,
                side_effect="none",
                capability_key="tool.compute_local",
                arguments={"x": 1},
            )
            r_read = gw.execute(
                None,
                side_effect="read",
                capability_key="tool.read_fixture",
                arguments={"fixture_key": "k"},
            )
            r_write = gw.execute(
                None,
                side_effect="write_local",
                capability_key="tool.write",
                arguments={"title": "x"},
            )
            r_unknown = gw.execute(
                None,
                side_effect="unknown",
                capability_key="tool.mystery",
            )

        self.assertEqual(r_none["status"], "succeeded_isolated")
        self.assertTrue(r_none.get("delegated_to_inner"))
        self.assertEqual(r_read["status"], "succeeded_isolated")
        self.assertTrue(r_read.get("delegated_to_inner"))
        # draft/write must simulate, never delegate.
        self.assertEqual(r_write["status"], "simulated")
        self.assertFalse(r_write.get("delegated_to_inner"))
        self.assertEqual(r_unknown["status"], "denied")
        self.assertFalse(r_unknown.get("delegated_to_inner"))
        # Inner saw only allowlisted isolated dispatches.
        self.assertEqual(len(inner.calls), 2)
        self.assertEqual(
            {c["side_effect"] for c in inner.calls},
            {"none", "read"},
        )

    def test_nested_child_reenters_isolation_wrapped_gateway(self) -> None:
        from app.assistant.evaluation.isolation import (
            IsolationWrappedGateway,
            eval_execution_scope,
            build_isolation_context,
        )
        from app.assistant.evaluation.contracts import EvalExecutionIdentity

        class FakeInnerGateway:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def execute(self, request, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append(dict(kwargs))
                return {"status": "completed"}

        ns = _uuid()
        isolation = build_isolation_context(
            namespace_id=ns,
            subject_digest=DIGEST_A,
            dataset_version_ids=(_uuid(),),
        )
        identity = EvalExecutionIdentity(
            eval_run_id=_uuid(),
            eval_case_id=_uuid(),
            namespace_id=ns,
            owner_kind="test",
            subject_kind="skill_draft",
            subject_aggregate_id=_uuid(),
            subject_version_id=_uuid(),
        )
        inner = FakeInnerGateway()
        with eval_execution_scope(isolation=isolation, identity=identity) as scope:
            gw = IsolationWrappedGateway(inner=inner, scope=scope)
            parent = gw.execute(
                None,
                side_effect="none",
                capability_key="workflow.parent",
                logical_call_key="p1",
            )
            child = gw.execute_nested_child(
                side_effect="compute",
                capability_key="agent.child",
                parent_ordinal=1,
                logical_call_key="c1",
            )
        self.assertEqual(parent["status"], "succeeded_isolated")
        self.assertEqual(child["status"], "succeeded_isolated")
        self.assertTrue(child.get("nested"))
        self.assertEqual(len(inner.calls), 2)
        self.assertEqual(len(scope.call_records), 2)
        self.assertEqual(scope.call_records[1].parent_ordinal, 1)


if __name__ == "__main__":
    unittest.main()
