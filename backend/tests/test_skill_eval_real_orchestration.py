"""Decisive real-orchestration negative tests (Plan 09 Task 6).

Proves expected Skill keys never rewrite actual active skills, missing safety
counters stay None (never manufactured zeros), and gate eligibility requires
probe-derived observations (not fixture-declared zeros).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.evaluation.assertions import evaluate_dataset_assertions  # noqa: E402
from app.assistant.evaluation.isolation import (  # noqa: E402
    build_isolation_context,
)
from app.assistant.evaluation.observations import (  # noqa: E402
    ObservedEvalCaseOutcome,
    fold_observed_outcome,
    observed_to_case_outcome_mapping,
)
from app.assistant.evaluation.orchestration import (  # noqa: E402
    EvaluationOrchestrator,
    EvaluationOrchestratorConfig,
    ProviderFixtureScript,
    install_default_isolation_probes,
    missing_safety_counter_probe,
    register_provider_fixture,
    resolve_provider_fixture,
    zero_production_delta_probe,
    zero_safety_counter_probe,
)
from app.assistant.evaluation.contracts import (  # noqa: E402
    EVAL_OWNER_KIND,
    EvalExecutionIdentity,
)
from app.assistant.provider_loop.scripted_provider import (  # noqa: E402
    eval_text_round_script,
)


DIGEST_A = "a" * 64


@dataclass
class _HarnessCase:
    id: UUID = field(default_factory=uuid4)
    case_key: str = "case-1"
    locale: str = "en"
    input_messages: list[dict[str, Any]] = field(
        default_factory=lambda: [{"role": "user", "content": "hi"}]
    )
    fixture_refs: list[dict[str, Any]] = field(default_factory=list)
    expected_mode: str = "direct_answer"
    acceptable_skill_keys: list[str] = field(default_factory=list)
    forbidden_skill_keys: list[str] = field(default_factory=list)
    acceptable_capability_paths: list[list[str]] = field(default_factory=list)
    expect_completion: bool = True
    assertion_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class _CaseAssertions:
    skill_recall: bool
    gate_eligible: bool
    summary: Any


@dataclass
class _HarnessOutcome:
    actual_active_skills: tuple[str, ...]
    safety_counters: dict[str, int | None]
    production_delta: dict[str, int | None]
    completed: bool
    execution_kind: str
    gate_eligible: bool
    assertions: _CaseAssertions
    observed: ObservedEvalCaseOutcome


class RealEvalHarness:
    """Minimal real-orchestration harness for decisive negative tests."""

    def __init__(
        self,
        *,
        install_probes: bool = True,
        safety_probe=None,
        production_delta_probe=None,
    ) -> None:
        self.namespace_id = uuid4()
        self.isolation = build_isolation_context(
            namespace_id=self.namespace_id,
            subject_digest=DIGEST_A,
            dataset_version_ids=(uuid4(),),
            memory_mode="empty",
            data_mode="fixture",
        )
        if install_probes and safety_probe is None and production_delta_probe is None:
            safety_probe, production_delta_probe = install_default_isolation_probes()
        self.orchestrator = EvaluationOrchestrator(
            config=EvaluationOrchestratorConfig(app_build_revision="test"),
            safety_counter_probe=safety_probe,
            production_delta_probe=production_delta_probe,
        )

    def case(
        self,
        *,
        expected_mode: str = "golden_skill",
        acceptable_skill_keys: Sequence[str] | None = None,
        fixture_key: str = "provider-selects-skill-b",
        forbidden_skill_keys: Sequence[str] | None = None,
        expect_completion: bool = True,
    ) -> _HarnessCase:
        return _HarnessCase(
            expected_mode=expected_mode,
            acceptable_skill_keys=list(acceptable_skill_keys or []),
            forbidden_skill_keys=list(forbidden_skill_keys or []),
            fixture_refs=[
                {
                    "kind": "provider_script",
                    "script_key": fixture_key,
                    "revision": "eval-v1",
                }
            ],
            expect_completion=expect_completion,
            input_messages=[{"role": "user", "content": f"prompt for {fixture_key}"}],
        )

    def execute(self, case: _HarnessCase) -> _HarnessOutcome:
        identity = EvalExecutionIdentity(
            eval_run_id=uuid4(),
            eval_case_id=case.id,
            namespace_id=self.namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            subject_kind="skill_draft",
            subject_aggregate_id=uuid4(),
            subject_version_id=uuid4(),
        )
        observed = self.orchestrator.execute_case(
            self.isolation,
            case,
            None,
            identity=identity,
        )
        mapping = observed_to_case_outcome_mapping(observed, case=case)
        summary = evaluate_dataset_assertions(
            case_outcomes=[mapping],
            safety_counters=observed.safety_counters,
            production_delta=observed.production_delta,
            isolation_breached=False,
        )
        acceptable = set(case.acceptable_skill_keys)
        actual = set(observed.actual_active_skills)
        if acceptable:
            skill_recall = bool(acceptable & actual)
        else:
            skill_recall = not actual
        missing_counters = any(
            v is None for v in (observed.safety_counters or {}).values()
        )
        missing_delta = any(
            v is None for v in (observed.production_delta or {}).values()
        )
        gate_eligible = (
            bool(summary.gate_eligible)
            and skill_recall
            and not missing_counters
            and not missing_delta
            and bool(observed.completed)
        )
        return _HarnessOutcome(
            actual_active_skills=observed.actual_active_skills,
            safety_counters=dict(observed.safety_counters),
            production_delta=dict(observed.production_delta),
            completed=observed.completed,
            execution_kind=observed.execution_kind,
            gate_eligible=gate_eligible,
            assertions=_CaseAssertions(
                skill_recall=skill_recall,
                gate_eligible=gate_eligible,
                summary=summary,
            ),
            observed=observed,
        )

    def execute_without_counter(self, counter_name: str) -> _HarnessOutcome:
        """Run with a probe that omits the named safety counter (stays None)."""
        harness = RealEvalHarness(
            install_probes=False,
            safety_probe=missing_safety_counter_probe(omit=counter_name),
            production_delta_probe=zero_production_delta_probe,
        )
        case = harness.case(
            expected_mode="direct_answer",
            acceptable_skill_keys=[],
            fixture_key="provider-direct-answer",
        )
        return harness.execute(case)


@pytest.fixture
def real_eval_harness() -> RealEvalHarness:
    # Explicit zero probes — defaults are honest-missing Nones, not proven zeros.
    return RealEvalHarness(
        install_probes=False,
        safety_probe=zero_safety_counter_probe,
        production_delta_probe=zero_production_delta_probe,
    )


def test_default_isolation_probes_return_none_for_unobserved_keys() -> None:
    """Default probes must not manufacture proven zeros for missing observations."""
    safety_probe, delta_probe = install_default_isolation_probes()
    safety = safety_probe()
    delta = delta_probe()
    assert safety
    assert delta
    assert all(v is None for v in safety.values())
    assert all(v is None for v in delta.values())
    # Explicit zero helpers still return proven zeros for tests that need them.
    assert all(v == 0 for v in zero_safety_counter_probe().values())
    assert all(v == 0 for v in zero_production_delta_probe().values())


def test_default_probes_make_matching_skills_not_gate_eligible() -> None:
    """install_default_isolation_probes alone cannot invent hard-safety pass."""
    harness = RealEvalHarness(install_probes=True)
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is True
    assert all(v is None for v in outcome.safety_counters.values())
    assert all(v is None for v in outcome.production_delta.values())
    assert outcome.gate_eligible is False


def test_expected_skill_never_rewrites_actual_skill(
    real_eval_harness: RealEvalHarness,
) -> None:
    case = real_eval_harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-a"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = real_eval_harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is False
    assert outcome.gate_eligible is False


def test_missing_safety_observation_is_not_zero(
    real_eval_harness: RealEvalHarness,
) -> None:
    outcome = real_eval_harness.execute_without_counter("secret_exposure")
    assert outcome.safety_counters["secret_exposure"] is None
    assert outcome.gate_eligible is False


def test_matching_skills_without_safety_probe_gate_false() -> None:
    """Matching expected/fixture skills with missing safety probe → gate false."""
    harness = RealEvalHarness(
        install_probes=False,
        safety_probe=None,
        production_delta_probe=zero_production_delta_probe,
    )
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is True
    assert all(v is None for v in outcome.safety_counters.values())
    assert outcome.gate_eligible is False


def test_installed_probes_with_matching_skills_can_be_gate_eligible() -> None:
    """Installed probes returning zeros + matching skills → gate may be true."""
    harness = RealEvalHarness(
        install_probes=False,
        safety_probe=zero_safety_counter_probe,
        production_delta_probe=zero_production_delta_probe,
    )
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is True
    assert all(v is not None for v in outcome.safety_counters.values())
    assert all(v is not None for v in outcome.production_delta.values())
    assert outcome.gate_eligible is True


def test_fixture_cannot_force_gate_eligible_without_probes() -> None:
    """Builtin fixtures no longer declare observed zeros; missing probes → gate false."""
    harness = RealEvalHarness(install_probes=False)
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    # No probes → counters/delta None even when skills match.
    assert all(v is None for v in outcome.safety_counters.values())
    assert all(v is None for v in outcome.production_delta.values())
    assert outcome.gate_eligible is False
    # Builtin fixture has no observed_* maps.
    fixture = resolve_provider_fixture(script_key="provider-selects-skill-b")
    assert not hasattr(fixture, "observed_safety_counters") or not getattr(
        fixture, "observed_safety_counters", None
    )


def test_observed_outcome_rejects_expected_as_actual_fields() -> None:
    """ObservedEvalCaseOutcome constructor has no acceptable_skill_keys field."""
    fields = set(ObservedEvalCaseOutcome.model_fields)
    assert "acceptable_skill_keys" not in fields
    assert "expect_completion" not in fields
    assert "expected_mode" not in fields
    with pytest.raises(Exception):
        ObservedEvalCaseOutcome(  # type: ignore[call-arg]
            eval_case_id=uuid4(),
            execution_kind="golden_skill",
            actual_active_skills=("skill-b",),
            capability_path=(),
            completed=True,
            stop_reason="ok",
            obligations_pending=0,
            production_delta={},
            safety_counters={},
            acceptable_skill_keys=["skill-a"],  # banned
        )


def test_fold_observed_outcome_uses_runtime_skills_only() -> None:
    observed = fold_observed_outcome(
        eval_case_id=uuid4(),
        active_skills=("skill-b",),
        completed=True,
        stop_reason="natural_completion",
        safety_counters={"secret_exposure": None},
        production_delta={"assistant_chat_run": 0},
    )
    assert observed.actual_active_skills == ("skill-b",)
    assert observed.safety_counters["secret_exposure"] is None
    assert "duplicate_write" in observed.safety_counters
    assert observed.safety_counters["duplicate_write"] is None


def test_provider_fixture_registry_resolves_skill_b() -> None:
    fixture = resolve_provider_fixture(script_key="provider-selects-skill-b")
    assert fixture.activates_skills == ("skill-b",)
    assert "acceptable" not in fixture.script_key


def test_structural_materializer_never_gate_eligible_for_mismatch() -> None:
    """Structural path copies expected→actual (legacy); gate path must not use it."""
    harness = RealEvalHarness(
        install_probes=False,
        safety_probe=zero_safety_counter_probe,
        production_delta_probe=zero_production_delta_probe,
    )
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-a"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    assert outcome.actual_active_skills != tuple(case.acceptable_skill_keys)
    assert outcome.actual_active_skills == ("skill-b",)


def test_matching_fixture_and_expected_can_be_gate_eligible(
    real_eval_harness: RealEvalHarness,
) -> None:
    case = real_eval_harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = real_eval_harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is True
    # Explicit zero probes + matching skills → gate-eligible.
    assert outcome.gate_eligible is True
    assert all(v is not None for v in outcome.safety_counters.values())


def test_skill_activation_goes_through_loop_dispatch() -> None:
    """Skill activations are applied only after Provider tool-call dispatch."""
    harness = RealEvalHarness(
        install_probes=False,
        safety_probe=zero_safety_counter_probe,
        production_delta_probe=zero_production_delta_probe,
    )
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-b"],
        fixture_key="provider-selects-skill-b",
    )
    identity = EvalExecutionIdentity(
        eval_run_id=uuid4(),
        eval_case_id=case.id,
        namespace_id=harness.namespace_id,
        owner_kind=EVAL_OWNER_KIND,
        subject_kind="skill_draft",
        subject_aggregate_id=uuid4(),
        subject_version_id=uuid4(),
    )
    # Capture scope events via a custom orchestrator run under isolation.
    from app.assistant.evaluation.isolation import eval_execution_scope

    with eval_execution_scope(
        isolation=harness.isolation,
        identity=identity,
        fixture_store={},
    ) as scope:
        observed = harness.orchestrator.execute_case(
            harness.isolation,
            case,
            None,
            identity=identity,
            scope=scope,
        )
        event_types = [e.get("event_type") for e in scope.events]
        assert "eval.skill_inject_dispatched" in event_types
        assert observed.actual_active_skills == ("skill-b",)
        assert "skill.inject" in observed.capability_path
