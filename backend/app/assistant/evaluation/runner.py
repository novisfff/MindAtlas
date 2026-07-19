"""EvaluationRunner — interactive_scripted execution under isolation (Plan 09 Task 4).

Composes real planner/policy/orchestration *contracts* under RuntimeIsolationContext.
Never imports production business adapters, EntryService, production Run/ledger/
memory/event/Artifact writers, or production write adapters.

Side-effect adapters and data/memory/event/Artifact/call namespaces are replaced
with evaluation-owned simulation. Planner and policy decision shapes remain
byte-contract compatible with Plans 05/08.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal, Mapping, Sequence
from uuid import UUID, uuid4

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.assertions import (
    InteractiveAssertionSummary,
    evaluate_interactive_safety,
)
from app.assistant.evaluation.contracts import (
    EVAL_OWNER_KIND,
    EvalExecutionIdentity,
    EvalSubjectKind,
    RuntimeIsolationContext,
)
from app.assistant.evaluation.isolation import (
    ISOLATION_BREACH,
    EvalCallRecord,
    EvalDataProvider,
    EvalExecutionScope,
    EvalMemoryProvider,
    IsolationBreach,
    IsolationError,
    IsolationWrappedGateway,
    build_isolation_context,
    eval_execution_scope,
    isolation_digest,
    require_active_eval_scope,
    tripwire_production_writer,
)
from app.assistant.evaluation.snapshots import (
    assert_evidence_safe,
    assert_isolation_snapshot_fields,
)

logger = logging.getLogger(__name__)

RUNNER_CONTRACT_VERSION = 1

EvalRunTerminal = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class InteractiveScriptStep:
    """One scripted capability intent for interactive_scripted mode.

    No live Provider. Steps drive isolation-wrapped Gateway dispatch so
    nested Workflow/Agent children re-enter the same isolation path.
    """

    capability_key: str
    side_effect: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    logical_call_key: str | None = None
    is_nested_child: bool = False
    parent_ordinal: int | None = None
    # Force a production tripwire site (test harness).
    force_tripwire_site: str | None = None
    # Force raw invoke attempt.
    force_raw_invoke: bool = False


@dataclass(frozen=True, slots=True)
class InteractiveScript:
    """Ordered interactive script for one eval case."""

    steps: tuple[InteractiveScriptStep, ...]
    input_messages: tuple[Mapping[str, Any], ...] = ()
    cancel_after_step: int | None = None
    crash_after_step: int | None = None


@dataclass
class EvaluationRunnerConfig:
    """Runner configuration. Production write mode is ignored."""

    runner_contract_version: int = RUNNER_CONTRACT_VERSION
    runtime_contract_version: int = 1
    build_revision: str = "development"
    # Injected for tests: ASSISTANT_MAIN_AGENT_WRITE_MODE value. Must not change results.
    production_write_mode: Literal["off", "golden"] = "off"


@dataclass
class EvaluationCaseOutcome:
    eval_run_id: UUID
    eval_case_id: UUID
    terminal: EvalRunTerminal
    failure_code: str | None
    gate_eligible: bool
    call_records: list[EvalCallRecord]
    events: list[dict[str, Any]]
    assertion_summary: InteractiveAssertionSummary
    aggregate_metrics: dict[str, Any]
    isolation_digest: str
    scope_namespace_id: UUID


class EvaluationRunner:
    """Execute interactive_scripted cases under mandatory isolation.

    Architecture ban: this module must not import EntryService, production
    CapabilityCall repository writers, production Run repositories, production
    event/Artifact/memory writers, or production write adapters.

    Composes real Gateway dispatch contracts under isolation via an optional
    inner CapabilityGateway (or thin adapter). Full Main Agent/Provider loop
    composition is deferred to Task 5.
    """

    def __init__(
        self,
        *,
        config: EvaluationRunnerConfig | None = None,
        fixture_store: Mapping[str, Any] | None = None,
        snapshot_store: Mapping[str, Any] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        inner_gateway: Any | None = None,
        package_port: Any | None = None,
        memory_provider: EvalMemoryProvider | None = None,
        data_provider: EvalDataProvider | None = None,
    ) -> None:
        self.config = config or EvaluationRunnerConfig()
        self.fixture_store = dict(fixture_store or {})
        self.snapshot_store = dict(snapshot_store or {})
        self.event_sink = event_sink
        self.inner_gateway = inner_gateway
        self.package_port = package_port
        self.memory_provider = memory_provider
        self.data_provider = data_provider
        # production_write_mode is recorded but MUST NOT affect simulation.
        self._write_mode_seen = self.config.production_write_mode

    def build_identity(
        self,
        *,
        eval_run_id: UUID,
        eval_case_id: UUID,
        namespace_id: UUID,
        subject_kind: EvalSubjectKind,
        subject_aggregate_id: UUID,
        subject_version_id: UUID,
    ) -> EvalExecutionIdentity:
        return EvalExecutionIdentity(
            eval_run_id=eval_run_id,
            eval_case_id=eval_case_id,
            namespace_id=namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
        )

    def resolve_candidate_draft(
        self,
        *,
        subject_kind: EvalSubjectKind,
        subject_aggregate_id: UUID,
        subject_version_id: UUID,
        content_digest: str,
        binding_digest: str,
        draft_files: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Resolve a candidate draft explicitly without Catalog mutation.

        When ``package_port`` is injected and package/version IDs are present,
        resolves via the real package/version read surface (get_package /
        get_version). Catalog is never mutated.
        """
        view: dict[str, Any] = {
            "subject_kind": subject_kind,
            "aggregate_id": str(subject_aggregate_id),
            "version_id": str(subject_version_id),
            "content_digest": content_digest,
            "binding_digest": binding_digest,
            "draft_files": list(draft_files or ()),
            "catalog_mutated": False,
            "resolved_by": "evaluation_runner",
            "package_resolved": False,
        }
        port = self.package_port
        if port is None:
            return view
        # Prefer real package/version resolution when IDs are present.
        try:
            package = None
            version = None
            if hasattr(port, "get_package"):
                try:
                    package = port.get_package(subject_aggregate_id)
                except Exception:
                    package = None
            if hasattr(port, "get_version"):
                try:
                    # AgentSkillService signature: get_version(package_id, version_id)
                    version = port.get_version(subject_aggregate_id, subject_version_id)
                except TypeError:
                    try:
                        version = port.get_version(subject_version_id)
                    except Exception:
                        version = None
                except Exception:
                    version = None
            if package is not None or version is not None:
                view["package_resolved"] = True
                view["resolved_by"] = "package_port"
                if package is not None:
                    view["package"] = _safe_package_view(package)
                if version is not None:
                    view["version"] = _safe_version_view(version)
                    # Prefer authoritative digests from the version when present.
                    for attr, key in (
                        ("content_digest", "content_digest"),
                        ("contentDigest", "content_digest"),
                        ("binding_digest", "binding_digest"),
                        ("bindingDigest", "binding_digest"),
                    ):
                        val = getattr(version, attr, None)
                        if val is None and isinstance(version, Mapping):
                            val = version.get(attr)
                        if val is not None and key in view:
                            view[key] = str(val)
        except Exception:
            # Fail soft: keep evaluation-owned draft view; do not mutate catalog.
            view["package_resolved"] = False
            view["resolved_by"] = "evaluation_runner"
        view["catalog_mutated"] = False
        return view

    def run_interactive_scripted(
        self,
        *,
        isolation: RuntimeIsolationContext,
        identity: EvalExecutionIdentity,
        script: InteractiveScript,
        production_delta_probe: Callable[[], Mapping[str, int]] | None = None,
        step_boundary_hook: Callable[[], None] | None = None,
    ) -> EvaluationCaseOutcome:
        """Run one interactive_scripted case under isolation.

        Requires RuntimeIsolationContext + EvalExecutionIdentity before any
        Gateway construction. Missing/mismatched identity fails closed.

        ``step_boundary_hook`` is invoked at loop boundaries (per step) so the
        worker can heartbeat the lease during long executes.
        """
        assert_isolation_snapshot_fields(isolation)
        iso_digest = isolation_digest(isolation)

        # Fail before Gateway if identity invalid (validate via scope enter).
        try:
            with eval_execution_scope(
                isolation=isolation,
                identity=identity,
                fixture_store=self.fixture_store,
                snapshot_store=self.snapshot_store,
            ) as scope:
                return self._run_in_scope(
                    scope=scope,
                    script=script,
                    iso_digest=iso_digest,
                    production_delta_probe=production_delta_probe,
                    step_boundary_hook=step_boundary_hook,
                )
        except IsolationError as exc:
            # Scope failed to install — no Gateway was constructed.
            from app.assistant.evaluation.assertions import AssertionResult

            summary = evaluate_interactive_safety(
                isolation_breached=(exc.code == ISOLATION_BREACH),
            )
            if exc.code != ISOLATION_BREACH:
                summary.results.append(
                    AssertionResult(
                        code=exc.code,
                        outcome="fail",
                        detail=exc.message,
                        hard_safety=False,
                    )
                )
            return EvaluationCaseOutcome(
                eval_run_id=identity.eval_run_id,
                eval_case_id=identity.eval_case_id,
                terminal="failed",
                failure_code=exc.code,
                gate_eligible=False,
                call_records=[],
                events=[],
                assertion_summary=summary,
                aggregate_metrics={"runner_contract_version": RUNNER_CONTRACT_VERSION},
                isolation_digest=iso_digest,
                scope_namespace_id=isolation.namespace_id,
            )

    def _run_in_scope(
        self,
        *,
        scope: EvalExecutionScope,
        script: InteractiveScript,
        iso_digest: str,
        production_delta_probe: Callable[[], Mapping[str, int]] | None,
        step_boundary_hook: Callable[[], None] | None = None,
    ) -> EvaluationCaseOutcome:
        # Gateway construction only after scope is active.
        require_active_eval_scope()
        memory = self.memory_provider or EvalMemoryProvider(
            mode=scope.isolation.memory_mode,  # type: ignore[arg-type]
            fixture_store=self.fixture_store,
        )
        data = self.data_provider or EvalDataProvider(
            mode=scope.isolation.data_mode,  # type: ignore[arg-type]
            fixture_store=self.fixture_store,
            snapshot_store=self.snapshot_store,
        )
        gateway = IsolationWrappedGateway(
            scope=scope,
            inner=self.inner_gateway,
            memory_provider=memory,
            data_provider=data,
        )
        scope.record_event(
            "eval.run_started",
            {
                "mode": "interactive_scripted",
                "runner_contract_version": RUNNER_CONTRACT_VERSION,
                "production_write_mode_ignored": True,
                "production_write_mode_seen": self._write_mode_seen,
                "inner_gateway": self.inner_gateway is not None,
            },
        )
        self._emit(scope)

        terminal: EvalRunTerminal = "completed"
        failure_code: str | None = None

        for idx, step in enumerate(script.steps):
            if step_boundary_hook is not None:
                try:
                    step_boundary_hook()
                except Exception:
                    logger.exception("step_boundary_hook failed")
            if scope.cancelled:
                terminal = "cancelled"
                break
            if script.cancel_after_step is not None and idx >= script.cancel_after_step:
                scope.cancelled = True
                scope.record_event("eval.cancel_requested", {"after_step": idx})
                terminal = "cancelled"
                break
            if script.crash_after_step is not None and idx >= script.crash_after_step:
                # Simulated worker crash boundary — leave non-terminal for recovery.
                scope.record_event("eval.crash_boundary", {"after_step": idx})
                failure_code = "worker_crash"
                terminal = "failed"
                break

            try:
                if step.force_tripwire_site:
                    tripwire_production_writer(step.force_tripwire_site)
                if step.force_raw_invoke:
                    from app.assistant.evaluation.isolation import reject_raw_tool_invoke

                    reject_raw_tool_invoke()

                if step.is_nested_child:
                    result = gateway.execute_nested_child(
                        side_effect=step.side_effect,
                        capability_key=step.capability_key,
                        arguments=dict(step.arguments),
                        parent_ordinal=step.parent_ordinal,
                        logical_call_key=step.logical_call_key,
                        descriptor_digest=sha256_canonical_json(
                            {"capability_key": step.capability_key}
                        ),
                        binding_digest=sha256_canonical_json(
                            {"binding": step.capability_key}
                        ),
                        policy_digest=sha256_canonical_json({"policy": "eval"}),
                    )
                else:
                    result = gateway.execute(
                        None,
                        side_effect=step.side_effect,
                        capability_key=step.capability_key,
                        arguments=dict(step.arguments),
                        logical_call_key=step.logical_call_key,
                        descriptor_digest=sha256_canonical_json(
                            {"capability_key": step.capability_key}
                        ),
                        binding_digest=sha256_canonical_json(
                            {"binding": step.capability_key}
                        ),
                        policy_digest=sha256_canonical_json({"policy": "eval"}),
                    )
                # Redact and sink safe result evidence.
                safe_result = {
                    "status": result.get("status"),
                    "logical_call_key": result.get("logical_call_key"),
                    "side_effect": result.get("side_effect"),
                }
                assert_evidence_safe(safe_result, context="eval.step_result")
                scope.record_event("eval.step_completed", safe_result)
                self._emit(scope)
            except IsolationBreach as exc:
                terminal = "failed"
                failure_code = ISOLATION_BREACH
                scope.record_event(
                    "eval.isolation_breach",
                    {"site": exc.site, "code": ISOLATION_BREACH},
                )
                self._emit(scope)
                break
            except IsolationError as exc:
                terminal = "failed"
                failure_code = exc.code
                scope.record_event(
                    "eval.error",
                    {"code": exc.code, "safe_message": "evaluation_error"},
                )
                self._emit(scope)
                break
            except Exception as exc:  # noqa: BLE001 — convert to safe failure
                logger.exception("evaluation step failed")
                terminal = "failed"
                failure_code = "eval_internal_error"
                scope.record_event(
                    "eval.error",
                    {
                        "code": "eval_internal_error",
                        "safe_message": type(exc).__name__,
                    },
                )
                self._emit(scope)
                break

        production_delta = dict(production_delta_probe() if production_delta_probe else {})
        call_outcomes = [r.outcome for r in scope.call_records]
        logical_keys = [(r.logical_call_key, r.attempt) for r in scope.call_records]
        evidence = list(scope.events) + [dict(r.decision) for r in scope.call_records]

        summary = evaluate_interactive_safety(
            isolation_breached=scope.breached or failure_code == ISOLATION_BREACH,
            production_delta=production_delta,
            simulated_writes=scope.simulated_writes,
            call_outcomes=call_outcomes,
            logical_keys_attempts=logical_keys,
            evidence_payloads=evidence,
        )
        # isolation_breach is permanently gate-ineligible — not a metric.
        gate_eligible = bool(summary.gate_eligible) and not scope.breached
        if failure_code == ISOLATION_BREACH:
            gate_eligible = False

        if terminal == "completed" and not scope.breached:
            scope.record_event("eval.run_completed", {"gate_eligible": gate_eligible})
        elif terminal == "cancelled":
            scope.record_event("eval.run_cancelled", {})
        else:
            scope.record_event(
                "eval.run_failed",
                {"failure_code": failure_code, "gate_eligible": gate_eligible},
            )
        self._emit(scope)

        metrics = {
            "runner_contract_version": RUNNER_CONTRACT_VERSION,
            "steps": len(script.steps),
            "calls": len(scope.call_records),
            "simulated_writes": len(scope.simulated_writes),
            "nested_max_depth": max(
                (1 for r in scope.call_records if r.parent_ordinal is not None),
                default=0,
            ),
            "production_write_mode_seen": self._write_mode_seen,
            "production_write_mode_affects_result": False,
        }
        return EvaluationCaseOutcome(
            eval_run_id=scope.identity.eval_run_id,
            eval_case_id=scope.identity.eval_case_id,
            terminal=terminal,
            failure_code=failure_code,
            gate_eligible=gate_eligible,
            call_records=list(scope.call_records),
            events=list(scope.events),
            assertion_summary=summary,
            aggregate_metrics=metrics,
            isolation_digest=iso_digest,
            scope_namespace_id=scope.isolation.namespace_id,
        )

    def _emit(self, scope: EvalExecutionScope) -> None:
        if self.event_sink is None or not scope.events:
            return
        # Emit only the latest event to avoid re-sending full history.
        self.event_sink(dict(scope.events[-1]))


def _safe_package_view(package: Any) -> dict[str, Any]:
    """Project package detail into an evaluation-safe, non-mutating view."""
    if isinstance(package, Mapping):
        keys = ("id", "name", "canonical_name", "display_name", "status")
        return {k: package.get(k) for k in keys if k in package or package.get(k) is not None}
    out: dict[str, Any] = {}
    for attr in ("id", "name", "canonical_name", "display_name", "status"):
        val = getattr(package, attr, None)
        if val is not None:
            out[attr] = str(val) if not isinstance(val, (str, int, bool)) else val
    return out


def _safe_version_view(version: Any) -> dict[str, Any]:
    """Project version detail into an evaluation-safe, non-mutating view."""
    if isinstance(version, Mapping):
        keys = (
            "id",
            "content_digest",
            "contentDigest",
            "binding_digest",
            "bindingDigest",
            "sequence_no",
            "status",
        )
        return {k: version.get(k) for k in keys if version.get(k) is not None}
    out: dict[str, Any] = {}
    for attr in (
        "id",
        "content_digest",
        "contentDigest",
        "binding_digest",
        "bindingDigest",
        "sequence_no",
        "status",
    ):
        val = getattr(version, attr, None)
        if val is not None:
            out[attr] = str(val) if not isinstance(val, (str, int, bool)) else val
    return out


def make_interactive_identity(
    *,
    eval_run_id: UUID | None = None,
    eval_case_id: UUID | None = None,
    namespace_id: UUID | None = None,
    subject_kind: EvalSubjectKind = "skill_draft",
    subject_aggregate_id: UUID | None = None,
    subject_version_id: UUID | None = None,
) -> tuple[RuntimeIsolationContext, EvalExecutionIdentity]:
    """Test helper: paired isolation + identity with matching namespace."""
    ns = namespace_id or uuid4()
    subject_agg = subject_aggregate_id or uuid4()
    subject_ver = subject_version_id or uuid4()
    subject_digest = sha256_canonical_json(
        {
            "kind": subject_kind,
            "aggregate_id": str(subject_agg),
            "version_id": str(subject_ver),
        }
    )
    # dataset_version_ids must be non-empty for isolation context; interactive
    # runs may use a placeholder dataset version id when not dataset-bound.
    isolation = build_isolation_context(
        namespace_id=ns,
        subject_digest=subject_digest,
        dataset_version_ids=(uuid4(),),
        memory_mode="empty",
        data_mode="fixture",
    )
    identity = EvalExecutionIdentity(
        eval_run_id=eval_run_id or uuid4(),
        eval_case_id=eval_case_id or uuid4(),
        namespace_id=ns,
        owner_kind=EVAL_OWNER_KIND,
        subject_kind=subject_kind,
        subject_aggregate_id=subject_agg,
        subject_version_id=subject_ver,
    )
    return isolation, identity


__all__ = [
    "RUNNER_CONTRACT_VERSION",
    "EvaluationCaseOutcome",
    "EvaluationRunner",
    "EvaluationRunnerConfig",
    "InteractiveScript",
    "InteractiveScriptStep",
    "make_interactive_identity",
]
