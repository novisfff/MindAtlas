"""Real Main Agent orchestration for evaluation dataset cases (Plan 09 Task 6).

Runs Provider Loop with a deterministic ScriptedProvider under
RuntimeIsolationContext. Actual outcomes are observed from runtime state:

- active skills: only from loop result manifest / isolation-recorded activations
  after Provider tool-call dispatch (never pre-loop append_skill_activation, never
  case.acceptable_skill_keys)
- production_delta / safety_counters: only from installed isolation probes
  (missing counters stay None; fixtures never declare observed zeros)

Does not import production repositories. Candidate closure / Profile snapshots
are injected by callers (worker/router) when available.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityMetrics,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    completed_result,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedSkillRef,
    append_skill_activation,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
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
    DEFAULT_PRODUCTION_DELTA_KEYS,
    REQUIRED_SAFETY_COUNTER_KEYS,
    ObservedEvalCaseOutcome,
    build_scope_observation_probes,
    fold_observed_outcome,
    install_isolated_eval_observation_probes,
    observe_production_delta_from_scope,
    observe_safety_counters_from_scope,
)
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (
    ProviderDispatchRequest,
    ProviderDispatchResult,
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
    eval_tool_call_round_script,
    text_then_terminal,
)
from app.assistant.skills.resolution import build_binding_snapshot

logger = logging.getLogger(__name__)

ORCHESTRATOR_CONTRACT_VERSION = 1
EVAL_PROVIDER_PROTOCOL = OPENAI_CHAT_PROVIDER_PROTOCOL
EVAL_ADAPTER_KEY = "eval-scripted"
EVAL_ADAPTER_REVISION = "eval-v1"
EVAL_POLICY_DIGEST = "e" * 64
EVAL_MODEL_CONFIG_DIGEST = "f" * 64
EVAL_SKILL_INJECT_DOMAIN = "skill.inject"
EVAL_SKILL_SEARCH_DOMAIN = "skill.search"

# Fixed digests for eval-owned synthetic bindings (stable across cases).
_EVAL_DIGEST_A = "a" * 64
_EVAL_DIGEST_B = "b" * 64
_EVAL_DIGEST_C = "c" * 64
_EVAL_DIGEST_D = "d" * 64
_EVAL_DIGEST_E = "e" * 64
_EVAL_DIGEST_F = "f" * 64


@dataclass(frozen=True, slots=True)
class ProviderFixtureScript:
    """Versioned Provider script bundle — never embeds assertion fields.

    ``activates_skills`` describes which skills the *scripted Provider events*
    will request via the skill.inject tool surface. Activation is applied only
    when the loop dispatches that tool call — never via pre-loop
    ``append_skill_activation``.

    Fixtures must NOT declare observed safety/production maps. Those come only
    from isolation probes installed by the worker/orchestrator.
    """

    script_key: str
    revision: str
    rounds: tuple[ScriptedRoundScript, ...] = ()
    # Skills the scripted Provider will request via skill.inject tool calls.
    activates_skills: tuple[str, ...] = ()
    capability_path: tuple[str, ...] = ()
    completes: bool = True
    stop_reason: str = "natural_completion"
    final_text: str = "ok"


# Built-in versioned fixture registry for real_orchestration runs.
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
    raise KeyError(
        f"provider fixture not found: script_key={script_key!r} revision={revision!r}"
    )


def _bootstrap_builtin_fixtures() -> None:
    """Register decisive eval fixtures used by Task 6 negative tests.

    Builtin fixtures declare only scripted Provider behavior (which skills the
    scripted events will request). They never hardcode observed safety/production
    zero maps — those require installed isolation probes.
    """
    if "provider-selects-skill-b" in _FIXTURE_REGISTRY:
        return
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-selects-skill-b",
            revision="eval-v1",
            rounds=(),
            activates_skills=("skill-b",),
            capability_path=(EVAL_SKILL_SEARCH_DOMAIN, EVAL_SKILL_INJECT_DOMAIN),
            completes=True,
            stop_reason="natural_completion",
            final_text="activated skill-b",
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-selects-skill-a",
            revision="eval-v1",
            rounds=(),
            activates_skills=("skill-a",),
            capability_path=(EVAL_SKILL_SEARCH_DOMAIN, EVAL_SKILL_INJECT_DOMAIN),
            completes=True,
            final_text="activated skill-a",
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-direct-answer",
            revision="eval-v1",
            rounds=(),
            activates_skills=(),
            capability_path=(),
            completes=True,
            final_text="direct answer",
        )
    )
    register_provider_fixture(
        ProviderFixtureScript(
            script_key="provider-missing-secret-counter",
            revision="eval-v1",
            rounds=(),
            activates_skills=(),
            capability_path=(),
            completes=True,
            final_text="ok",
        )
    )


_bootstrap_builtin_fixtures()


def zero_safety_counter_probe() -> dict[str, int | None]:
    """Isolation probe returning proven-zero hard-safety counters."""
    return {k: 0 for k in REQUIRED_SAFETY_COUNTER_KEYS}


def zero_production_delta_probe() -> dict[str, int | None]:
    """Isolation probe returning proven-zero production mutation deltas."""
    return {k: 0 for k in DEFAULT_PRODUCTION_DELTA_KEYS}


def missing_safety_counter_probe(
    *,
    omit: str | Sequence[str] = "secret_exposure",
) -> Callable[[], Mapping[str, int | None]]:
    """Probe that deliberately leaves named counters unobserved (None)."""
    omit_set = {omit} if isinstance(omit, str) else set(str(x) for x in omit)

    def _probe() -> dict[str, int | None]:
        return {
            k: (None if k in omit_set else 0) for k in REQUIRED_SAFETY_COUNTER_KEYS
        }

    return _probe


def install_default_isolation_probes(
    *,
    safety: Mapping[str, int | None] | None = None,
    production_delta: Mapping[str, int | None] | None = None,
) -> tuple[
    Callable[[], Mapping[str, int | None]],
    Callable[[], Mapping[str, int | None]],
]:
    """Build static probe callables (test helper / explicit partial maps).

    Default is honest-missing Nones for unobserved keys — never manufacture
    proven zeros. Callers may supply partial maps; unspecified required keys
    stay None. Use ``zero_safety_counter_probe`` /
    ``zero_production_delta_probe`` only when a test explicitly needs zeros.

    Production worker path must use ``install_isolated_eval_observation_probes``
    (scope-backed) instead of these static all-None defaults.
    """
    safety_base: dict[str, int | None] = {
        k: None for k in REQUIRED_SAFETY_COUNTER_KEYS
    }
    if safety is not None:
        for key, value in safety.items():
            safety_base[str(key)] = None if value is None else int(value)
    delta_base: dict[str, int | None] = {
        k: None for k in DEFAULT_PRODUCTION_DELTA_KEYS
    }
    if production_delta is not None:
        for key, value in production_delta.items():
            delta_base[str(key)] = None if value is None else int(value)

    def safety_probe() -> dict[str, int | None]:
        return dict(safety_base)

    def delta_probe() -> dict[str, int | None]:
        return dict(delta_base)

    return safety_probe, delta_probe


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

    Full ``compose_main_agent_policy_runtime`` requires a live DB session and
    production control handlers; this orchestrator implements the closest real
    path that still routes skill selection through the Provider Loop tool
    surface + isolation event recording. Gate eligibility is refused unless
    safety/production observations come from installed probes.
    """

    config: EvaluationOrchestratorConfig = field(
        default_factory=EvaluationOrchestratorConfig
    )
    fixture_registry: dict[str, ProviderFixtureScript] = field(default_factory=dict)
    # Optional pre-resolved candidate closure (from Task 4 resolver, injected).
    candidate_closure: Any | None = None
    # Optional Profile snapshot fields (digest/key/version) injected by worker.
    profile_snapshot: Mapping[str, Any] | None = None
    # Required for gate-eligible observations. When missing, counters stay None.
    production_delta_probe: Callable[[], Mapping[str, int | None]] | None = None
    safety_counter_probe: Callable[[], Mapping[str, int | None]] | None = None
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
                "safety_probe_installed": self.safety_counter_probe is not None,
                "production_delta_probe_installed": self.production_delta_probe is not None,
            },
        )

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
        # Base manifest only — skills must arrive via loop tool dispatch.
        manifest = create_base_run_manifest(
            run_id=run_id,
            main_agent=main_agent,
            provider=provider_ref,
            model=model_ref,
            effective_policy_digest=EVAL_POLICY_DIGEST,
        )

        user_text = self._user_text(case)
        user_msg = ProviderUserMessage(content=user_text)

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

        scripted_skills = tuple(str(s) for s in fixture.activates_skills if str(s).strip())
        inject_binding, inject_descriptor = _eval_skill_inject_binding()
        tools = _EvalSkillToolsProvider(
            provider_protocol=EVAL_PROVIDER_PROTOCOL,
            scope=scope_exec,
            inject_binding=inject_binding,
            inject_descriptor=inject_descriptor,
            expose_inject=bool(scripted_skills),
        )
        dispatcher = _EvalSkillInjectDispatcher(
            inject_binding=inject_binding,
            inject_descriptor=inject_descriptor,
            scope=scope,
        )
        auth = _EvalAuthFactory()
        verifier = _EvalDescriptorVerifier(
            inject_binding=inject_binding,
            inject_descriptor=inject_descriptor,
        )
        events = _EvalEventSink(scope=scope, sink=self.event_sink)

        provider = ScriptedProvider(
            provider_protocol=EVAL_PROVIDER_PROTOCOL,
            adapter_key=EVAL_ADAPTER_KEY,
            adapter_revision=EVAL_ADAPTER_REVISION,
            model_config_digest=EVAL_MODEL_CONFIG_DIGEST,
            expected_model_ref=model_ref,
            relax_model_ref=True,
        )
        rounds = self._build_rounds(fixture=fixture, scripted_skills=scripted_skills)
        provider.enqueue(*rounds)

        ports = ProviderLoopPorts(
            provider=provider,
            tools_provider=tools,
            current_descriptors=verifier,
            authorization_evidence=auth,
            tool_dispatcher=dispatcher,
            sibling_executor=SequentialSiblingExecutor(),
            cancellation=_EvalCancellation(),
            events=events,
        )
        # Tools enabled when skill activation is scripted so the Provider may
        # emit skill.inject; otherwise direct-answer path (tools still resolved
        # but empty surface is fine with auto choice + no calls).
        generation = ProviderGenerationOptions(
            tool_choice=ProviderToolChoice(
                mode="auto" if scripted_skills else "none"
            ),
        )
        # When skill.inject is scripted, reserve exactly two Provider rounds so the
        # second is the tools-disabled finalization slot after the tool call.
        max_rounds = 2 if scripted_skills else max(2, int(self.config.max_rounds))
        request = ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(user_msg,),
            model_ref=model_ref,
            execution_scope=scope_exec,
            max_rounds=max_rounds,
            locale=str(
                getattr(case, "locale", None) or self.config.locale or "en"
            ),
            generation=generation,
        )

        stop_reason = fixture.stop_reason
        completed = bool(fixture.completes)
        # Skills start empty; only loop-observed activations count.
        active_skill_names: tuple[str, ...] = ()
        observed_capability_path: tuple[str, ...] = ()
        try:
            result = run_provider_agent_loop(request, ports)
            stop_reason = str(result.stop_reason or stop_reason)
            completed = result.status == "completed" and bool(fixture.completes)
            if result.manifest is not None and result.manifest.active_skills:
                active_skill_names = tuple(
                    s.canonical_name for s in result.manifest.active_skills
                )
            # Prefer dispatcher-recorded path when inject ran.
            if dispatcher.activated_skills:
                # Merge order-preserving: dispatcher first, then residual manifest.
                ordered: list[str] = []
                seen: set[str] = set()
                for name in list(dispatcher.activated_skills) + list(active_skill_names):
                    if name and name not in seen:
                        seen.add(name)
                        ordered.append(name)
                active_skill_names = tuple(ordered)
                observed_capability_path = (
                    EVAL_SKILL_SEARCH_DOMAIN,
                    EVAL_SKILL_INJECT_DOMAIN,
                )
            elif fixture.capability_path and active_skill_names:
                # Only accept fixture capability_path when skills were actually
                # observed on the resulting manifest (never invent path alone).
                observed_capability_path = tuple(fixture.capability_path)
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

        scope.record_event(
            "eval.skill_activation",
            {
                "actual_active_skills": list(active_skill_names),
                "capability_path": list(observed_capability_path),
                "completed": completed,
                "stop_reason": stop_reason,
                "dispatch_count": len(dispatcher.dispatch_requests),
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

        production_delta = self._observe_production_delta()
        safety_counters = self._observe_safety_counters()

        return fold_observed_outcome(
            eval_case_id=self._case_id(case),
            events=list(scope.events),
            active_skills=active_skill_names,
            capability_path=observed_capability_path,
            completed=completed,
            stop_reason=stop_reason,
            obligations_pending=0,
            production_delta=production_delta,
            safety_counters=safety_counters,
        )

    def _build_rounds(
        self,
        *,
        fixture: ProviderFixtureScript,
        scripted_skills: tuple[str, ...],
    ) -> list[ScriptedRoundScript]:
        """Build Provider rounds. Prefer fixture.rounds when supplied; otherwise
        synthesize tool-call (skill.inject) + finalization text from activates_skills.
        """
        if fixture.rounds:
            relaxed: list[ScriptedRoundScript] = []
            for idx, script in enumerate(fixture.rounds):
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
            return relaxed

        if scripted_skills:
            args = json.dumps(
                {"skills": list(scripted_skills)},
                separators=(",", ":"),
                sort_keys=True,
            )
            return [
                eval_tool_call_round_script(
                    call_id=f"eval-inject-{fixture.script_key}",
                    provider_alias="skill_inject",
                    arguments_json=args,
                    round_index=0,
                    tools_enabled=True,
                    provisional_text=None,
                ),
                # Round 1 is typically finalization (tools disabled) after inject.
                eval_text_round_script(
                    fixture.final_text or "ok",
                    round_index=1,
                    tools_enabled=False,
                    finalization_round=True,
                ),
            ]

        return [
            eval_text_round_script(
                fixture.final_text or "ok",
                round_index=0,
                tools_enabled=True,
            )
        ]

    def _observe_production_delta(self) -> dict[str, int | None]:
        """Only probe-derived production deltas count. No fixture maps."""
        if self.production_delta_probe is not None:
            raw = dict(self.production_delta_probe())
            out: dict[str, int | None] = {
                k: None for k in DEFAULT_PRODUCTION_DELTA_KEYS
            }
            for key, value in raw.items():
                out[str(key)] = None if value is None else int(value)
            return out
        # Missing probe → all None (never invent zeros from fixtures).
        return {k: None for k in DEFAULT_PRODUCTION_DELTA_KEYS}

    def _observe_safety_counters(self) -> dict[str, int | None]:
        """Only probe-derived safety counters count. No fixture maps."""
        counters: dict[str, int | None] = {
            k: None for k in REQUIRED_SAFETY_COUNTER_KEYS
        }
        if self.safety_counter_probe is not None:
            for key, value in dict(self.safety_counter_probe()).items():
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
            return ProviderFixtureScript(
                script_key=script_key,
                revision=revision or "unknown",
                rounds=(),
                activates_skills=(),
                completes=True,
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
# Eval-owned skill.inject tool surface + dispatcher (no production repositories)
# ---------------------------------------------------------------------------


def _eval_skill_inject_binding() -> tuple[Any, CapabilityDescriptor]:
    """Build a stable eval-owned skill.inject FrozenCapabilityBinding + descriptor."""
    input_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["skills"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    output_schema = normalize_binding_schema(
        {"type": "object", "additionalProperties": True},
        require_object_root=True,
    )
    completion = CapabilityCompletionContract()
    target = UUID("00000000-0000-4000-8000-00000000e1ec")
    target_identity = f"eval-control:{EVAL_SKILL_INJECT_DOMAIN}"
    executable_revision = "eval-v1"
    config_digest = _EVAL_DIGEST_B
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target),
            "targetVersionId": None,
            "targetRevision": 1,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=EVAL_SKILL_INJECT_DOMAIN,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )
    binding = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=_EVAL_DIGEST_D,
        ),
    )
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="eval-v1",
            ruleset_digest=_EVAL_DIGEST_A,
        ),
        side_effect="none",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=_EVAL_DIGEST_B,
    )
    descriptor = CapabilityDescriptor(
        capability_key=EVAL_SKILL_INJECT_DOMAIN,
        capability_type="tool",
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        resolution_digest=resolution_digest,
        binding_contract_digest=contract_digest,
        dependency_closure_digest=closure_digest,
        display_name="Skill Inject (eval)",
        description="Eval-owned skill.inject control surface",
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        descriptor_digest=_EVAL_DIGEST_C,
        executable_revision=executable_revision,
        behavior=behavior,
        availability=CapabilityAvailability(
            status="available",
            reason_code=None,
            compatibility_only=False,
        ),
        completion=completion,
    )
    return binding, descriptor


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

    def __call__(
        self, event_type: str, payload: Mapping[str, Any] | None = None
    ) -> None:
        self.emit(event_type, payload)


class _EvalSkillToolsProvider:
    """Exposes skill.inject when the fixture scripts skill activation."""

    def __init__(
        self,
        *,
        provider_protocol: str,
        scope: Any,
        inject_binding: Any,
        inject_descriptor: CapabilityDescriptor,
        expose_inject: bool,
    ) -> None:
        self._provider_protocol = provider_protocol
        self._scope = scope
        self._inject_binding = inject_binding
        self._inject_descriptor = inject_descriptor
        self._expose_inject = expose_inject

    def resolve(self, manifest: Any, *, scope: Any, locale: str) -> ToolSurfaceResolution:
        del locale
        visible: list[tuple[Any, CapabilityDescriptor]] = []
        if self._expose_inject:
            # Only expose inject while not already activated (append-only once).
            already = {
                s.canonical_name for s in (manifest.active_skills or ())
            }
            # Keep tool available even after activation so alias lineage is stable;
            # scripted provider only calls once.
            del already
            visible.append((self._inject_binding, self._inject_descriptor))
        return build_provider_tool_surface(
            manifest=manifest,
            provider_protocol=self._provider_protocol,
            visible=visible,
            scope=scope or self._scope,
        )


class _EvalDescriptorVerifier:
    def __init__(
        self,
        *,
        inject_binding: Any,
        inject_descriptor: CapabilityDescriptor,
    ) -> None:
        self._by_digest = {
            inject_binding.ref.binding_contract_digest: inject_descriptor,
        }

    def require_current(self, *, binding: Any, exposed_descriptor: Any, scope: Any) -> Any:
        del scope
        current = self._by_digest.get(binding.ref.binding_contract_digest)
        if current is None:
            return exposed_descriptor
        return current


class _EvalAuthFactory:
    def issue(self, *, call: Any, binding: Any, descriptor: Any, scope: Any) -> Any:
        del descriptor
        return CapabilityAuthorizationEvidence(
            issuer="test",
            call_id=call.call_id,
            principal=scope.principal,
            entrypoint="test",
            owner=CapabilityOwnerRef(
                owner_kind="test",
                owner_id="eval-orchestrator",
                owner_version_id=None,
            ),
            capability_key=call.domain_key,
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
            allowed_side_effects=("none", "compute", "read"),
            grant_source_digest=_EVAL_DIGEST_E,
            evidence_digest=_EVAL_DIGEST_F,
        )


class _EvalSkillInjectDispatcher:
    """Applies skill activations via next_manifest when skill.inject is dispatched.

    This is the only path that materializes fixture-declared skills onto the
    runtime manifest — driven by Provider tool-call events through the loop.
    """

    def __init__(
        self,
        *,
        inject_binding: Any,
        inject_descriptor: CapabilityDescriptor,
        scope: EvalExecutionScope,
    ) -> None:
        self._inject_binding = inject_binding
        self._inject_descriptor = inject_descriptor
        self._scope = scope
        self.activated_skills: list[str] = []
        self.dispatch_requests: list[ProviderDispatchRequest] = []
        self.capability_path: list[str] = []

    def dispatch(self, request: ProviderDispatchRequest, *, cancellation: Any) -> Any:
        del cancellation
        self.dispatch_requests.append(request)
        domain = str(request.call.domain_key or "")
        current = request.current_manifest
        if domain != EVAL_SKILL_INJECT_DOMAIN:
            # Unknown tool — return completed without manifest change.
            return ProviderDispatchResult(
                capability_result=completed_result(
                    user_text="eval noop",
                    structured_output={"status": "noop"},
                    metrics=CapabilityMetrics(
                        duration_ms=0.0, input_bytes=0, output_bytes=0
                    ),
                ),
                next_manifest=current,
            )

        args = dict(request.call.arguments or {})
        skills_raw = args.get("skills") or args.get("skill_keys") or ()
        if isinstance(skills_raw, str):
            skills_raw = [skills_raw]
        skill_names = [str(s).strip() for s in skills_raw if str(s).strip()]
        next_manifest = current
        for skill_name in skill_names:
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
            next_manifest = append_skill_activation(
                next_manifest, skill=skill, capabilities=()
            )
            self.activated_skills.append(skill_name)
            self._scope.record_event(
                "eval.skill_inject_dispatched",
                {
                    "skill_key": skill_name,
                    "call_id": request.call.call_id,
                    "domain_key": domain,
                },
            )
        if skill_names:
            self.capability_path = [
                EVAL_SKILL_SEARCH_DOMAIN,
                EVAL_SKILL_INJECT_DOMAIN,
            ]
        return ProviderDispatchResult(
            capability_result=completed_result(
                user_text=f"activated {','.join(skill_names) or 'none'}",
                structured_output={"activated": skill_names},
                metrics=CapabilityMetrics(
                    duration_ms=1.0, input_bytes=0, output_bytes=0
                ),
            ),
            next_manifest=next_manifest,
        )


__all__ = [
    "ORCHESTRATOR_CONTRACT_VERSION",
    "EvaluationOrchestrator",
    "EvaluationOrchestratorConfig",
    "ProviderFixtureScript",
    "build_scope_observation_probes",
    "install_default_isolation_probes",
    "install_isolated_eval_observation_probes",
    "missing_safety_counter_probe",
    "observe_production_delta_from_scope",
    "observe_safety_counters_from_scope",
    "register_provider_fixture",
    "resolve_provider_fixture",
    "zero_production_delta_probe",
    "zero_safety_counter_probe",
]
