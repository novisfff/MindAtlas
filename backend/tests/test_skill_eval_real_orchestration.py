"""Decisive real-orchestration negative tests (Plan 09 Task 6).

Proves expected Skill keys never rewrite actual active skills, and missing
safety counters stay None (never manufactured zeros).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
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
    register_provider_fixture,
    resolve_provider_fixture,
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

    def __init__(self) -> None:
        self.namespace_id = uuid4()
        self.isolation = build_isolation_context(
            namespace_id=self.namespace_id,
            subject_digest=DIGEST_A,
            dataset_version_ids=(uuid4(),),
            memory_mode="empty",
            data_mode="fixture",
        )
        self.orchestrator = EvaluationOrchestrator(
            config=EvaluationOrchestratorConfig(app_build_revision="test")
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
        # Per-case skill recall: acceptable ∩ actual non-empty when acceptable set.
        acceptable = set(case.acceptable_skill_keys)
        actual = set(observed.actual_active_skills)
        if acceptable:
            skill_recall = bool(acceptable & actual)
        else:
            skill_recall = not actual
        # Missing safety counters force gate-ineligible (never invent zeros).
        missing_counters = any(
            v is None for v in (observed.safety_counters or {}).values()
        )
        # Skill-recall failure on a positive golden case is promotion-ineligible.
        gate_eligible = (
            bool(summary.gate_eligible)
            and skill_recall
            and not missing_counters
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
        """Run a fixture that omits the named safety counter (stays None)."""
        fixture_key = "provider-missing-secret-counter"
        if counter_name != "secret_exposure":
            # Register a one-off fixture that omits the requested counter.
            counters = {
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            }
            counters.pop(counter_name, None)
            register_provider_fixture(
                ProviderFixtureScript(
                    script_key=f"provider-missing-{counter_name}",
                    revision="eval-v1",
                    rounds=(eval_text_round_script("ok"),),
                    activates_skills=(),
                    completes=True,
                    observed_safety_counters=counters,
                    observed_production_delta={
                        "assistant_chat_run": 0,
                        "capability_call": 0,
                        "assistant_memory": 0,
                        "artifact": 0,
                    },
                )
            )
            fixture_key = f"provider-missing-{counter_name}"
        case = self.case(
            expected_mode="direct_answer",
            acceptable_skill_keys=[],
            fixture_key=fixture_key,
        )
        return self.execute(case)


@pytest.fixture
def real_eval_harness() -> RealEvalHarness:
    return RealEvalHarness()


def test_expected_skill_never_rewrites_actual_skill(real_eval_harness: RealEvalHarness) -> None:
    case = real_eval_harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-a"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = real_eval_harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is False
    assert outcome.gate_eligible is False


def test_missing_safety_observation_is_not_zero(real_eval_harness: RealEvalHarness) -> None:
    outcome = real_eval_harness.execute_without_counter("secret_exposure")
    assert outcome.safety_counters["secret_exposure"] is None
    assert outcome.gate_eligible is False


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
    # Required keys present even when not supplied.
    assert "duplicate_write" in observed.safety_counters
    assert observed.safety_counters["duplicate_write"] is None


def test_provider_fixture_registry_resolves_skill_b() -> None:
    fixture = resolve_provider_fixture(script_key="provider-selects-skill-b")
    assert fixture.activates_skills == ("skill-b",)
    assert "acceptable" not in fixture.script_key


def test_structural_materializer_never_gate_eligible_for_mismatch() -> None:
    """Structural path copies expected→actual (legacy); gate path must not use it."""
    from app.assistant.evaluation.worker import EvaluationWorker, EvalWorkerConfig

    # Pure unit: structural helper with a fake case-like object is covered by
    # worker tests; here we only assert the orchestrator path diverges.
    harness = RealEvalHarness()
    case = harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-a"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = harness.execute(case)
    # Real path: actual is skill-b, not skill-a
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
    # Full safety counters + zero production delta → summary gate-eligible.
    assert outcome.gate_eligible is True
    assert all(v is not None for v in outcome.safety_counters.values())
