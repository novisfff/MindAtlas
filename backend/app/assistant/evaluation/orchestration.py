"""Real Main Agent orchestration for evaluation dataset cases (Plan 09 Task 6).

Runs Provider Loop with a deterministic ScriptedProvider under
RuntimeIsolationContext. Actual outcomes are observed from runtime state
(manifest active skills, loop result, isolation events/calls) — never from
dataset expected assertion fields.

Does not import production repositories. Candidate closure / Profile snapshots
are injected by callers (worker/router) when available; the orchestrator only
consumes already-resolved closure digests and fixture scripts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from app.assistant.capabilities.contracts import CapabilityPrincipal
from app.assistant.domain.contracts import (
    ResolvedMainAgentRef,
    ResolvedSkillRef,
    append_skill_activation,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.contracts import (
    EVAL_OWNER_KIND,
    EvalExecutionIdentity,
    ProviderFixtureRef,
    RuntimeIsolationContext,
    normalize_provider_fixture_refs,
)
from app.assistant.evaluation.isolation import (
    EvalExecutionScope,
    IsolationError,
    assert_not_production_scope_for_eval,
    eval_execution_scope,
    require_active_eval_scope,
)
from app.assistant.evaluation.observations import (
    ObservedEvalCaseOutcome,
    fold_observed_outcome,
)
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (
    ProviderGenerationOptions,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderToolChoice,
    ToolSurfaceResolution,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import run_provider_agent_loop
from app.assistant.provider_loop.messages import ProviderUserMessage
from app.assistant.provider_loop.scheduler import SequentialSiblingExecutor
from app.assistant.provider_loop.scripted_provider import (
    ScriptedProvider,
    ScriptedRoundScript,
    eval_text_round_script,
    text_then_terminal,
)

logger = logging.getLogger(__name__)

ORCHESTRATOR_CONTRACT_VERSION = 1
EVAL_PROVIDER_PROTOCOL = OPENAI_CHAT_PROVIDER_PROTOCOL
EVAL_ADAPTER_KEY = "eval-scripted"
EVAL_ADAPTER_REVISION = "eval-v1"
EVAL_POLICY_DIGEST = "e" * 64
EVAL_MODEL_CONFIG_DIGEST = "f" * 64


@dataclass(frozen=True, slots=True)
class ProviderFixtureScript:
    """Versioned Provider script bundle — never embeds assertion fields."""

    script_key: str
    revision: str
    rounds: tuple[ScriptedRoundScript, ...]
    # Skills the scripted Provider *activation path* materializes via dispatcher
    # / pre-activation for this fixture. Observed as actual runtime skills —
    # independent of case.acceptable_skill_keys.
    activates_skills: tuple[str, ...] = ()
    capability_path: tuple[str, ...] = ()
    completes: bool = True
    stop_reason: str = "natural_completion"
    final_text: str = "ok"
    # Optional safety counter observations produced by this fixture path.
    # Keys not listed remain None (missing), never auto-zeroed.
    observed_safety_counters: Mapping[str, int | None] = field(default_factory=dict)
    observed_production_delta: Mapping[str, int | None] = field(default_factory=dict)


# Built-in versioned fixture registry for real_orchestration runs.
# Keys: (script_key, revision) and bare script_key fallback.
_FIXTURE_REGISTRY: dict[str, ProviderFixtureScript] = {}


def register_provider_fixture(fixture: ProviderFixtureScript) -> ProviderFixtureScript:
    """Register a versioned fixture script. Overwrites same script_key+revision."""
    key = f"{fixture.script_key}@{fixture.revision}"
    _FIXTURE_REGISTRY[key] = fixture
    _FIXTURE_REGISTRY[fixture.script_key] = fixture
    return fixture


def resolve_provider_fixture(
    *,
    script_key: str,
    revision: str | None = None,
    registry: Mapping[str, ProviderFixtureScript] | None = None,
) -> ProviderFixtureScript:
    """Resolve a versioned fixture. Fail closed when missing."""
    store = dict(_FIXTURE_REGISTRY)
    if registry:
        store.update(registry)
    if revision:
        keyed = f"{script_key}@{revision}"
        if keyed in store:
            return store[keyed]
    if script_key in store:
        return store[script_key]
    raise KeyError(f"provider fixture not found: script_key={script_key!r} revision={revision!r}")


def _bootstrap_builtin_fixtures() -> None:
    """Register decisive eval fixtures used by Task 6 negative tests."""
    if "provider-selects-skill-b" in _FIXTURE_REGISTRY:
        return
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-selects-skill-b",
            revision="eval-v1",
            rounds=(eval_text_round_script("activated skill-b"),),
            activates_skills=("skill-b",),
            capability_path=("skill.search", "skill.inject"),
            completes=True,
            stop_reason="natural_completion",
            final_text="activated skill-b",
            # Proven zero production mutation + full safety counters for gate path.
            observed_safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            },
            observed_production_delta={
                "assistant_chat_run": 0,
                "capability_call": 0,
                "assistant_memory": 0,
                "artifact": 0,
            },
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-selects-skill-a",
            revision="eval-v1",
            rounds=(eval_text_round_script("activated skill-a"),),
            activates_skills=("skill-a",),
            capability_path=("skill.search", "skill.inject"),
            completes=True,
            observed_safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            },
            observed_production_delta={
                "assistant_chat_run": 0,
                "capability_call": 0,
                "assistant_memory": 0,
                "artifact": 0,
            },
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-direct-answer",
            revision="eval-v1",
            rounds=(eval_text_round_script("direct answer"),),
            activates_skills=(),
            capability_path=(),
            completes=True,
            observed_safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            },
            observed_production_delta={
                "assistant_chat_run": 0,
                "capability_call": 0,
                "assistant_memory": 0,
                "artifact": 0,
            },
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-missing-secret-counter",
            revision="eval-v1",
            rounds=(eval_text_round_script("ok"),),
            activates_skills=(),
            capability_path=(),
            completes=True,
            # Deliberately omit secret_exposure so observation stays None.
            observed_safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                # secret_exposure intentionally absent
                "duplicate_write": 0,
            },
            observed_production_delta={
                "assistant_chat_run": 0,
                "capability_call": 0,
                "assistant_memory": 0,
                "artifact": 0,
            },
        )
    )


_bootstrap_builtin_fixtures()


@dataclass
class EvaluationOrchestratorConfig:
    """Orchestrator knobs. Never toggles production write adapters."""

    contract_version: int = ORCHESTRATOR_CONTRACT_VERSION
    app_build_revision: str = "development"
    max_rounds: int = 4
    locale: str = "en"
    profile_key: str = "eval-default"
    profile_content_digest: str = "c" * 64


@dataclass
class EvaluationOrchestrator:
    """Execute one dataset case through real Provider Loop + isolation ports.

    Architecture ban: do not import production EntryService, production Run
    repositories, production CapabilityCall writers, or production memory /
    Artifact / event writers. Isolation ports are injected.
    """

    config: EvaluationOrchestratorConfig = field(
        default_factory=EvaluationOrchestratorConfig
    )
    fixture_registry: dict[str, ProviderFixtureScript] = field(default_factory=dict)
    # Optional pre-resolved candidate closure (from Task 4 resolver, injected).
    candidate_closure: Any | None = None
    # Optional Profile snapshot fields (digest/key/version) injected by worker.
    profile_snapshot: Mapping[str, Any] | None = None
    # Test-owned ports / probes.
    production_delta_probe: Callable[[], Mapping[str, int | None]] | None = None
    safety_counter_probe: Callable[[], Mapping[str, int | None]] | None = None
    # When set, overrides fixture safety counters (for missing-counter tests).
    safety_counter_override: Mapping[str, int | None] | None = None
    # Optional event sink (owner-qualified eval events).
    event_sink: Callable[[dict[str, Any]], None] | None = None

    def execute_case(
        self,
        context: RuntimeIsolationContext,
        case: Any,
        fixture: ProviderFixtureScript | ProviderFixtureRef | Mapping[str, Any] | str | None,
        *,
        identity: EvalExecutionIdentity | None = None,
        scope: EvalExecutionScope | None = None,
    ) -> ObservedEvalCaseOutcome:
        """Run one case under isolation; return observed actuals only.

        ``fixture`` may be a resolved ProviderFixtureScript, a ProviderFixtureRef,
        a raw fixture_refs entry, or a script_key string. Dataset assertion fields
        on ``case`` are never copied into actual outcome fields.
        """
        resolved = self._resolve_fixture(fixture, case=case)
        case_id = self._case_id(case)
        identity = identity or self._identity_for(context, case_id=case_id)

        if scope is not None:
            return self._execute_in_scope(
                scope=scope,
                case=case,
                fixture=resolved,
            )

        with eval_execution_scope(
            isolation=context,
            identity=identity,
            fixture_store={"provider_fixture": resolved.script_key},
        ) as active:
            return self._execute_in_scope(
                scope=active,
                case=case,
                fixture=resolved,
            )

    def _execute_in_scope(
        self,
        *,
        scope: EvalExecutionScope,
        case: Any,
        fixture: ProviderFixtureScript,
    ) -> ObservedEvalCaseOutcome:
        require_active_eval_scope()
        assert_not_production_scope_for_eval()

        scope.record_event(
            "eval.case_started",
            {
                "script_key": fixture.script_key,
                "fixture_revision": fixture.revision,
                "orchestrator_contract_version": self.config.contract_version,
                "candidate_closure_present": self.candidate_closure is not None,
            },
        )

        # Build minimal MA-compatible Provider Loop request under isolation.
        run_id = scope.identity.eval_run_id
        conversation_id = uuid4()
        model_ref = self._eval_model_ref()
        provider_ref = create_provider_ref(
            provider_protocol=EVAL_PROVIDER_PROTOCOL,
            provider_config_id=uuid4(),
            provider_runtime_revision=1,
            provider_config_digest=EVAL_MODEL_CONFIG_DIGEST,
            adapter_key=EVAL_ADAPTER_KEY,
            adapter_revision=EVAL_ADAPTER_REVISION,
            protocol_revision="1",
            app_build_revision=self.config.app_build_revision,
        )
        profile = dict(self.profile_snapshot or {})
        profile_key = str(profile.get("profile_key") or self.config.profile_key)
        profile_version_id = profile.get("profile_version_id") or uuid4()
        if not isinstance(profile_version_id, UUID):
            profile_version_id = UUID(str(profile_version_id))
        profile_digest = str(
            profile.get("content_digest") or self.config.profile_content_digest
        )
        main_agent = ResolvedMainAgentRef(
            profile_id=uuid4(),
            version_id=profile_version_id,
            profile_key=profile_key,
            sequence=1,
            content_digest=profile_digest,
        )
        manifest = create_base_run_manifest(
            run_id=run_id,
            main_agent=main_agent,
            provider=provider_ref,
            model=model_ref,
            effective_policy_digest=EVAL_POLICY_DIGEST,
        )

        # Materialize fixture-declared skill activations onto the runtime
        # manifest BEFORE observation. This is the scripted Provider activation
        # path — skills come from the fixture script, not case.acceptable_*.
        active_skill_names = tuple(str(s) for s in fixture.activates_skills)
        for skill_name in active_skill_names:
            skill = ResolvedSkillRef(
                package_id=uuid4(),
                version_id=uuid4(),
                canonical_name=skill_name,
                sequence=1,
                content_digest=sha256_canonical_json({"skill": skill_name}),
                version_digest=sha256_canonical_json({"skill": skill_name, "v": 1}),
                requested_name_normalized=skill_name,
                resolved_via_alias_id=None,
            )
            manifest = append_skill_activation(manifest, skill=skill, capabilities=())

        user_text = self._user_text(case)
        user_msg = ProviderUserMessage(content=user_text)
        rounds = list(fixture.rounds) if fixture.rounds else [
            eval_text_round_script(fixture.final_text or "ok")
        ]
        provider = ScriptedProvider(
            provider_protocol=EVAL_PROVIDER_PROTOCOL,
            adapter_key=EVAL_ADAPTER_KEY,
            adapter_revision=EVAL_ADAPTER_REVISION,
            model_config_digest=EVAL_MODEL_CONFIG_DIGEST,
            expected_model_ref=model_ref,
            relax_model_ref=True,
        )
        # Ensure relaxed assertion flags on supplied scripts.
        relaxed: list[ScriptedRoundScript] = []
        for idx, script in enumerate(rounds):
            if isinstance(script, ScriptedRoundScript):
                relaxed.append(
                    ScriptedRoundScript(
                        expected_round_index=script.expected_round_index
                        if script.expected_round_index is not None
                        else idx,
                        expected_messages=script.expected_messages,
                        expected_surface_digest=script.expected_surface_digest or "",
                        expected_tools_enabled=script.expected_tools_enabled,
                        expected_finalization_round=script.expected_finalization_round,
                        events=script.events or text_then_terminal("ok"),
                        expected_generation=script.expected_generation,
                        expected_tool_aliases=script.expected_tool_aliases,
                        raise_error=script.raise_error,
                        assert_messages=False,
                        assert_surface_digest=False,
                    )
                )
            else:
                relaxed.append(eval_text_round_script("ok", round_index=idx))
        provider.enqueue(*relaxed)

        principal = CapabilityPrincipal(
            principal_type="test",
            principal_id=f"eval-{scope.identity.namespace_id}",
            authenticated=True,
        )
        scope_exec = create_execution_scope(
            run_id=run_id,
            conversation_id=conversation_id,
            principal=principal,
            tenant_scope_id=None,
        )
        # Empty tool surface — fixtures declare activations via scripted path,
        # not live tool dispatch. tools_provider still required by the loop.
        tools = _EvalToolsProvider(
            provider_protocol=EVAL_PROVIDER_PROTOCOL,
            scope=scope_exec,
        )
        ports = ProviderLoopPorts(
            provider=provider,
            tools_provider=tools,
            current_descriptors=_EvalDescriptorVerifier(),
            authorization_evidence=_EvalAuthFactory(),
            tool_dispatcher=_EvalToolDispatcher(),
            sibling_executor=SequentialSiblingExecutor(),
            cancellation=_EvalCancellation(),
            events=_EvalEventSink(scope=scope, sink=self.event_sink),
        )
        request = ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(user_msg,),
            model_ref=model_ref,
            execution_scope=scope_exec,
            max_rounds=max(2, int(self.config.max_rounds)),
            locale=str(
                getattr(case, "locale", None) or self.config.locale or "en"
            ),
            generation=ProviderGenerationOptions(
                tool_choice=ProviderToolChoice(mode="none"),
            ),
        )

        stop_reason = fixture.stop_reason
        completed = bool(fixture.completes)
        try:
            result = run_provider_agent_loop(request, ports)
            stop_reason = str(result.stop_reason or stop_reason)
            completed = result.status == "completed" and bool(fixture.completes)
            # Prefer skills from resulting manifest when present.
            if result.manifest is not None and result.manifest.active_skills:
                active_skill_names = tuple(
                    s.canonical_name for s in result.manifest.active_skills
                )
        except IsolationError:
            raise
        except Exception as exc:  # noqa: BLE001 — convert to observed failure
            logger.exception("eval orchestrator provider loop failed")
            stop_reason = f"orchestrator_error:{type(exc).__name__}"
            completed = False
            scope.record_event(
                "eval.case_failed",
                {"stop_reason": stop_reason, "safe_message": type(exc).__name__},
            )

        # Record observed skill activation as owner-qualified eval event.
        scope.record_event(
            "eval.skill_activation",
            {
                "actual_active_skills": list(active_skill_names),
                "capability_path": list(fixture.capability_path),
                "completed": completed,
                "stop_reason": stop_reason,
            },
        )
        if completed:
            scope.record_event(
                "eval.case_completed",
                {
                    "actual_active_skills": list(active_skill_names),
                    "stop_reason": stop_reason,
                },
            )
        else:
            scope.record_event(
                "eval.case_failed",
                {
                    "actual_active_skills": list(active_skill_names),
                    "stop_reason": stop_reason,
                },
            )

        production_delta = self._observe_production_delta(fixture)
        safety_counters = self._observe_safety_counters(fixture)

        return fold_observed_outcome(
            eval_case_id=self._case_id(case),
            events=list(scope.events),
            active_skills=active_skill_names,
            capability_path=fixture.capability_path,
            completed=completed,
            stop_reason=stop_reason,
            obligations_pending=0,
            production_delta=production_delta,
            safety_counters=safety_counters,
        )

    def _observe_production_delta(
        self, fixture: ProviderFixtureScript
    ) -> dict[str, int | None]:
        if self.production_delta_probe is not None:
            return dict(self.production_delta_probe())
        if fixture.observed_production_delta:
            return dict(fixture.observed_production_delta)
        # Missing probe → all None (never invent zeros).
        return {
            "assistant_chat_run": None,
            "capability_call": None,
            "assistant_memory": None,
            "artifact": None,
        }

    def _observe_safety_counters(
        self, fixture: ProviderFixtureScript
    ) -> dict[str, int | None]:
        if self.safety_counter_override is not None:
            return dict(self.safety_counter_override)
        if self.safety_counter_probe is not None:
            return dict(self.safety_counter_probe())
        # Start from None for all required keys, then layer fixture observations.
        from app.assistant.evaluation.observations import REQUIRED_SAFETY_COUNTER_KEYS

        counters: dict[str, int | None] = {
            k: None for k in REQUIRED_SAFETY_COUNTER_KEYS
        }
        for key, value in (fixture.observed_safety_counters or {}).items():
            counters[str(key)] = None if value is None else int(value)
        return counters

    def _resolve_fixture(
        self,
        fixture: ProviderFixtureScript | ProviderFixtureRef | Mapping[str, Any] | str | None,
        *,
        case: Any,
    ) -> ProviderFixtureScript:
        if isinstance(fixture, ProviderFixtureScript):
            return fixture
        script_key: str | None = None
        revision: str | None = None
        if isinstance(fixture, ProviderFixtureRef):
            script_key = fixture.script_key
            revision = fixture.revision
        elif isinstance(fixture, str):
            script_key = fixture.strip() or None
        elif isinstance(fixture, Mapping):
            refs = normalize_provider_fixture_refs([fixture])
            if refs:
                script_key = refs[0].script_key
                revision = refs[0].revision
        if not script_key:
            # Fall back to first case fixture_ref.
            raw_refs = list(getattr(case, "fixture_refs", None) or ())
            refs = normalize_provider_fixture_refs(raw_refs)
            if refs:
                script_key = refs[0].script_key
                revision = refs[0].revision
        if not script_key:
            script_key = "provider-direct-answer"
        try:
            return resolve_provider_fixture(
                script_key=script_key,
                revision=revision,
                registry=self.fixture_registry,
            )
        except KeyError:
            # Unknown fixture → empty activation (still runs Provider Loop text).
            return ProviderFixtureScript(
                script_key=script_key,
                revision=revision or "unknown",
                rounds=(eval_text_round_script("unregistered fixture"),),
                activates_skills=(),
                completes=True,
                # Missing counters → None
                observed_safety_counters={},
                observed_production_delta={},
            )

    def _case_id(self, case: Any) -> UUID:
        raw = getattr(case, "id", None) or getattr(case, "eval_case_id", None)
        if isinstance(raw, UUID):
            return raw
        if raw is not None:
            try:
                return UUID(str(raw))
            except Exception:
                pass
        return uuid4()

    def _identity_for(
        self, context: RuntimeIsolationContext, *, case_id: UUID
    ) -> EvalExecutionIdentity:
        return EvalExecutionIdentity(
            eval_run_id=uuid4(),
            eval_case_id=case_id,
            namespace_id=context.namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            subject_kind="skill_draft",
            subject_aggregate_id=uuid4(),
            subject_version_id=uuid4(),
        )

    def _user_text(self, case: Any) -> str:
        messages = list(getattr(case, "input_messages", None) or ())
        for msg in messages:
            if isinstance(msg, Mapping):
                content = msg.get("content")
                if content:
                    return str(content)
            elif isinstance(msg, str) and msg.strip():
                return msg
        return str(getattr(case, "case_key", None) or "eval")

    def _eval_model_ref(self):
        return create_model_ref(
            model_id=uuid4(),
            model_name="eval-scripted",
            model_type="llm",
            model_runtime_revision=1,
            credential_id=uuid4(),
            credential_runtime_revision=1,
            credential_config_digest=EVAL_MODEL_CONFIG_DIGEST,
            model_config_digest=EVAL_MODEL_CONFIG_DIGEST,
            provider_ref_digest=sha256_canonical_json(
                {"protocol": EVAL_PROVIDER_PROTOCOL, "adapter": EVAL_ADAPTER_KEY}
            ),
            capability_probe_id=None,
            capability_probe_digest=None,
        )


# ---------------------------------------------------------------------------
# Minimal eval-owned Provider Loop ports (no production repositories)
# ---------------------------------------------------------------------------


class _EvalCancellation:
    def is_cancelled(self) -> bool:
        return False


class _EvalEventSink:
    def __init__(
        self,
        *,
        scope: EvalExecutionScope,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._scope = scope
        self._sink = sink

    def emit(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        event = {
            "event_type": f"provider.{event_type}",
            "payload": dict(payload or {}),
        }
        self._scope.record_event(event["event_type"], event["payload"])
        if self._sink is not None:
            self._sink(event)

    # ProviderLoopEventSink protocol may use __call__
    def __call__(self, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        self.emit(event_type, payload)


class _EvalToolsProvider:
    """Returns an empty Provider tool surface for each round (no live tools)."""

    def __init__(self, *, provider_protocol: str, scope: Any) -> None:
        self._provider_protocol = provider_protocol
        self._scope = scope

    def resolve(self, manifest: Any, *, scope: Any, locale: str) -> ToolSurfaceResolution:
        del locale
        return build_provider_tool_surface(
            manifest=manifest,
            provider_protocol=self._provider_protocol,
            visible=[],
            scope=scope or self._scope,
        )


class _EvalDescriptorVerifier:
    def require_current(self, *, binding: Any, exposed_descriptor: Any, scope: Any) -> Any:
        del binding, scope
        return exposed_descriptor


class _EvalAuthFactory:
    def issue(self, *, call: Any, binding: Any, descriptor: Any, scope: Any) -> Any:
        del call, binding, descriptor, scope
        raise RuntimeError("eval auth factory unused for tools-disabled fixtures")


class _EvalToolDispatcher:
    def dispatch(self, request: Any, *, cancellation: Any) -> Any:
        del request, cancellation
        raise RuntimeError("eval tool dispatcher has no bindings for this fixture")


__all__ = [
    "ORCHESTRATOR_CONTRACT_VERSION",
    "EvaluationOrchestrator",
    "EvaluationOrchestratorConfig",
    "ProviderFixtureScript",
    "register_provider_fixture",
    "resolve_provider_fixture",
]
