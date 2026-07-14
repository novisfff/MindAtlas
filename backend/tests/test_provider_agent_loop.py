"""Plan 03 Task 3: scripted provider + core direct/single-call agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAvailability,
    CapabilityAuthorizationEvidence,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityMetrics,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    completed_result,
    failed_result,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
    append_skill_activation,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.provider_loop.aliases import (  # noqa: E402
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    NoOpRoundContextProvider,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderGenerationOptions,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderUsage,
    RoundContextResolution,
    ToolSurfaceResolution,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import run_provider_agent_loop  # noqa: E402
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderContextUpdateMessage,
    ProviderToolMessage,
    ProviderUserMessage,
)
from app.assistant.provider_loop.scripted_provider import (  # noqa: E402
    ScriptedProvider,
    ScriptedRoundScript,
    text_then_terminal,
    tool_call_then_terminal,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000301")
CONV_ID = UUID("00000000-0000-4000-8000-000000000302")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000310")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000311")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000350")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000351")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000340")
TARGET_A = UUID("00000000-0000-4000-8000-000000000410")
TARGET_B = UUID("00000000-0000-4000-8000-000000000411")
SKILL_PKG = UUID("00000000-0000-4000-8000-000000000510")
SKILL_VER = UUID("00000000-0000-4000-8000-000000000511")

P = OPENAI_CHAT_PROVIDER_PROTOCOL
ADAPTER_KEY = "openai"
ADAPTER_REVISION = "a1"
MODEL_CONFIG = DIGEST_5


def _metrics() -> CapabilityMetrics:
    return CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0)


def _main_agent() -> ResolvedMainAgentRef:
    return ResolvedMainAgentRef(
        profile_id=PROFILE_ID,
        version_id=PROFILE_VERSION_ID,
        profile_key="general_chat",
        sequence=1,
        content_digest=DIGEST_A,
    )


def _provider():
    return create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision="build-1",
    )


def _model() -> ModelRef:
    provider = _provider()
    return create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=MODEL_CONFIG,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )


def _manifest(run_id: UUID = RUN_ID) -> ResolvedRunManifestRevision:
    return create_base_run_manifest(
        run_id=run_id,
        main_agent=_main_agent(),
        provider=_provider(),
        model=_model(),
        effective_policy_digest=None,
    )


def _scope(*, run_id: UUID = RUN_ID, tenant_scope_id: str | None = None):
    return create_execution_scope(
        run_id=run_id,
        conversation_id=CONV_ID,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-loop",
            authenticated=True,
        ),
        tenant_scope_id=tenant_scope_id,
    )


def _resolved_binding(
    *,
    capability_key: str,
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
) -> ResolvedCapabilityBinding:
    input_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    output_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    completion = CapabilityCompletionContract()
    target = target_id or uuid4()
    target_identity = f"remote-tool:{target}"
    executable_revision = "1"
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
    return ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=capability_key,
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


def _frozen(
    *,
    capability_key: str,
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
):
    resolved = _resolved_binding(
        capability_key=capability_key,
        target_id=target_id,
        config_digest=config_digest,
    )
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _descriptor(
    binding,
    *,
    description: str = "tool description",
    classification_revision: str = "plan02-v1",
    ruleset_digest: str = DIGEST_A,
    behavior_digest: str = DIGEST_B,
    descriptor_digest: str = DIGEST_C,
    parallel_safe: bool = True,
    availability_status: str = "available",
    side_effect: str = "read",
):
    resolved = binding.resolved if hasattr(binding, "resolved") else binding
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision=classification_revision,
            ruleset_digest=ruleset_digest,
        ),
        side_effect=side_effect,  # type: ignore[arg-type]
        parallel_safe=parallel_safe,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=behavior_digest,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type="tool",
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description=description,
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=descriptor_digest,
        executable_revision=resolved.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status=availability_status,  # type: ignore[arg-type]
            reason_code=None if availability_status == "available" else "test_unavailable",
            compatibility_only=False,
        ),
        completion=resolved.completion,
    )


def _pair(capability_key: str, *, target_id: UUID | None = None, **kwargs):
    binding = _frozen(capability_key=capability_key, target_id=target_id)
    return binding, _descriptor(binding, **kwargs)


# ---------------------------------------------------------------------------
# Fake ports
# ---------------------------------------------------------------------------


@dataclass
class RecordingCancellation:
    cancelled: bool = False
    check_count: int = 0

    def is_cancelled(self) -> bool:
        self.check_count += 1
        return self.cancelled


@dataclass
class RecordingEventSink:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))

    def types(self) -> list[str]:
        return [item[0] for item in self.events]


@dataclass
class RecordingToolsProvider:
    resolutions: list[ToolSurfaceResolution]
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None

    def resolve(self, manifest, *, scope, locale):
        self.calls.append(
            {
                "manifest_digest": manifest.manifest_digest,
                "manifest_revision": manifest.revision,
                "scope_digest": scope.scope_digest,
                "locale": locale,
            }
        )
        if self.fail_with is not None:
            raise self.fail_with
        if not self.resolutions:
            raise AssertionError("tools provider has no remaining resolutions")
        return self.resolutions.pop(0)


@dataclass
class RecordingDescriptorVerifier:
    """Returns current descriptor or raises on configured drift."""

    current_by_binding: dict[str, CapabilityDescriptor]
    calls: list[dict[str, Any]] = field(default_factory=list)
    force_error: Exception | None = None
    mutate_after: int | None = None
    mutate_to: CapabilityDescriptor | None = None
    _count: int = 0

    def require_current(self, *, binding, exposed_descriptor, scope):
        self._count += 1
        self.calls.append(
            {
                "binding_digest": binding.ref.binding_contract_digest,
                "exposed_descriptor_digest": exposed_descriptor.descriptor_digest,
                "scope_digest": scope.scope_digest,
            }
        )
        if self.force_error is not None:
            raise self.force_error
        if self.mutate_after is not None and self._count > self.mutate_after:
            if self.mutate_to is not None:
                current = self.mutate_to
            else:
                raise RuntimeError("classification changed")
        else:
            current = self.current_by_binding.get(binding.ref.binding_contract_digest)
            if current is None:
                current = exposed_descriptor
        # Equality of classification/behavior/descriptor digests + availability.
        if (
            current.descriptor_digest != exposed_descriptor.descriptor_digest
            or current.behavior.behavior_digest != exposed_descriptor.behavior.behavior_digest
            or current.behavior.classification.revision
            != exposed_descriptor.behavior.classification.revision
            or current.behavior.classification.ruleset_digest
            != exposed_descriptor.behavior.classification.ruleset_digest
            or current.availability.status != exposed_descriptor.availability.status
            or current.behavior.parallel_safe != exposed_descriptor.behavior.parallel_safe
        ):
            raise RuntimeError("classification_changed")
        return current


@dataclass
class RecordingAuthFactory:
    issued: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None
    wrong_scope: bool = False

    def issue(self, *, call, binding, descriptor, scope):
        if self.fail_with is not None:
            raise self.fail_with
        principal = scope.principal
        if self.wrong_scope:
            principal = CapabilityPrincipal(
                principal_type="test",
                principal_id="other-principal",
                authenticated=True,
            )
        evidence = CapabilityAuthorizationEvidence(
            issuer="test",
            call_id=call.call_id,
            principal=principal,
            entrypoint="test",
            owner=CapabilityOwnerRef(
                owner_kind="test",
                owner_id="owner-1",
                owner_version_id=None,
            ),
            capability_key=call.domain_key,
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
            allowed_side_effects=("none", "compute", "read"),
            grant_source_digest=DIGEST_E,
            evidence_digest=DIGEST_F,
        )
        self.issued.append(
            {
                "call_id": call.call_id,
                "domain_key": call.domain_key,
                "binding_digest": binding.ref.binding_contract_digest,
                "descriptor_digest": descriptor.descriptor_digest,
                "scope_digest": scope.scope_digest,
                "evidence": evidence,
            }
        )
        return evidence


@dataclass
class RecordingDispatcher:
    """Fake dispatcher that re-verifies descriptor equality before evidence use."""

    results: list[ProviderDispatchResult]
    verifier: RecordingDescriptorVerifier
    requests: list[ProviderDispatchRequest] = field(default_factory=list)
    evidence_verified: list[str] = field(default_factory=list)
    fail_with: Exception | None = None

    def dispatch(self, request: ProviderDispatchRequest, *, cancellation):
        del cancellation
        self.requests.append(request)
        # Independently repeat equality before "issuing"/accepting evidence.
        self.verifier.require_current(
            binding=request.binding,
            exposed_descriptor=request.descriptor,
            scope=request.execution_scope,
        )
        # Fake gateway verifies evidence once.
        if request.authorization.call_id != request.call.call_id:
            raise RuntimeError("evidence call mismatch")
        if request.authorization.principal.principal_id != request.execution_scope.principal.principal_id:
            raise RuntimeError("evidence principal mismatch")
        self.evidence_verified.append(request.authorization.evidence_digest)
        if self.fail_with is not None:
            raise self.fail_with
        if not self.results:
            raise AssertionError("dispatcher has no remaining results")
        return self.results.pop(0)


@dataclass
class SequentialSiblingExecutor:
    def map_parallel(self, items, worker, *, max_workers: int):
        del max_workers
        return [worker(item) for item in items]


def _usage(inp: int = 3, out: int = 5) -> ProviderUsage:
    return ProviderUsage(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


def _build_surface(manifest, pairs):
    return build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=P,
        visible=pairs,
        scope=_scope(),
    )


def _scripted(model: ModelRef) -> ScriptedProvider:
    return ScriptedProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )


def _ports(
    *,
    provider: ScriptedProvider,
    tools: RecordingToolsProvider,
    verifier: RecordingDescriptorVerifier,
    auth: RecordingAuthFactory,
    dispatcher: RecordingDispatcher,
    cancellation: RecordingCancellation | None = None,
    events: RecordingEventSink | None = None,
    round_context_provider=None,
) -> ProviderLoopPorts:
    kwargs: dict[str, Any] = {
        "provider": provider,
        "tools_provider": tools,
        "current_descriptors": verifier,
        "authorization_evidence": auth,
        "tool_dispatcher": dispatcher,
        "sibling_executor": SequentialSiblingExecutor(),
        "cancellation": cancellation or RecordingCancellation(),
        "events": events or RecordingEventSink(),
    }
    if round_context_provider is not None:
        kwargs["round_context_provider"] = round_context_provider
    return ProviderLoopPorts(**kwargs)


# ---------------------------------------------------------------------------
# Step 2: direct answer
# ---------------------------------------------------------------------------


def test_direct_answer_buffers_text_and_skips_dispatcher() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    resolution = _build_surface(base, [(binding, descriptor)])
    model = _model()
    scope = _scope()
    user = ProviderUserMessage(content="hello")
    final = "Hello from the model."

    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=resolution.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            expected_generation=ProviderGenerationOptions(),
            expected_tool_aliases=tuple(t.provider_alias for t in resolution.surface.tools),
            events=text_then_terminal("Hello ", "from the model.", usage=_usage()),
        )
    )

    tools = RecordingToolsProvider(resolutions=[resolution])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    events = RecordingEventSink()
    cancellation = RecordingCancellation()

    request = ProviderLoopRequest(
        manifest=base,
        initial_messages=(user,),
        model_ref=model,
        execution_scope=scope,
        max_rounds=4,
        locale="en",
        generation=ProviderGenerationOptions(),
    )
    ports = _ports(
        provider=provider,
        tools=tools,
        verifier=verifier,
        auth=auth,
        dispatcher=dispatcher,
        cancellation=cancellation,
        events=events,
    )

    result = run_provider_agent_loop(request, ports)

    assert result.status == "completed"
    assert result.stop_reason == "natural_completion"
    assert result.final_text == final
    assert result.round_count == 1
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 5
    assert len(result.messages) == 2
    assert isinstance(result.messages[1], ProviderAssistantMessage)
    assert result.messages[1].content == final
    assert result.tool_calls == ()
    assert provider.request_count == 1
    assert len(tools.calls) == 1
    assert tools.calls[0]["scope_digest"] == scope.scope_digest
    assert tools.calls[0]["locale"] == "en"
    assert dispatcher.requests == []
    assert auth.issued == []
    assert cancellation.check_count >= 1
    assert events.types()[0] == "loop.started"
    assert "round.started" in events.types()
    assert "final_text.delta" in events.types()
    assert events.types()[-1] == "loop.completed"
    # Alias revision from tools_provider becomes current manifest.
    assert result.manifest.manifest_digest == resolution.manifest.manifest_digest


# ---------------------------------------------------------------------------
# Step 3: one tool then answer
# ---------------------------------------------------------------------------


def test_one_tool_then_answer_pairs_result_and_rechecks_tools() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res1 = _build_surface(base, [(binding, descriptor)])
    # Round 2 tools_provider returns same surface lineage (no new tools).
    res2 = ToolSurfaceResolution(manifest=res1.manifest, surface=res1.surface)
    model = _model()
    scope = _scope()
    user = ProviderUserMessage(content="search something")
    alias = res1.surface.tools[0].provider_alias
    call_id = "call_r0_i0"
    args_json = '{"query":"atlas"}'
    provisional = "I will search now."
    final = "Found three entries."

    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json=args_json,
                provisional_text=provisional,
                usage=_usage(2, 4),
            ),
        )
    )

    tools = RecordingToolsProvider(resolutions=[res1, res2])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()

    cap_result = completed_result(
        user_text="3 hits",
        structured_output={"count": 3},
        metrics=_metrics(),
    )
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=cap_result,
                next_manifest=res1.manifest,
            )
        ],
        verifier=verifier,
    )
    events = RecordingEventSink()

    # Round 2 script is enqueued after we know the tool message; use a deferred approach:
    # first run prep for expected messages by constructing expected tool message after.
    # We'll enqueue round 2 after building expected assistant+tool via a two-phase script
    # that ignores exact message digests for round 2 by using a custom provider subclass.
    # Simpler: use ScriptedRoundScript with expected messages filled after dry assembly.
    # Instead, relax by subclassing to only check surface/round for round 2.

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not hasattr(self, "_round0_done"):
                self._round0_done = False
            if not self._round0_done:
                self._round0_done = True
                # Must yield-from: a bare return of another generator is ignored
                # because this method itself is a generator function.
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.round_index == 1
            assert request.tool_surface.surface_digest == res2.surface.surface_digest
            assert request.tools_enabled is True
            assert len(request.messages) == 3
            assert isinstance(request.messages[1], ProviderAssistantMessage)
            assert request.messages[1].tool_calls
            assert request.messages[1].content == provisional
            assert isinstance(request.messages[2], ProviderToolMessage)
            assert request.messages[2].call_id == call_id
            assert request.messages[2].content.status == "completed"
            yield from text_then_terminal(final, usage=_usage(1, 2))

    provider = FlexibleProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json=args_json,
                provisional_text=provisional,
                usage=_usage(2, 4),
            ),
        )
    )

    request = ProviderLoopRequest(
        manifest=base,
        initial_messages=(user,),
        model_ref=model,
        execution_scope=scope,
        max_rounds=4,
        locale="en",
    )
    ports = _ports(
        provider=provider,
        tools=tools,
        verifier=verifier,
        auth=auth,
        dispatcher=dispatcher,
        events=events,
    )
    result = run_provider_agent_loop(request, ports)

    assert result.status == "completed"
    assert result.final_text == final
    assert provisional not in (result.final_text or "")
    assert result.round_count == 2
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].status == "completed"
    assert result.tool_calls[0].call.call_id == call_id
    assert result.tool_calls[0].call.domain_key == "tools.search"
    assert result.tool_calls[0].call.surface_digest == res1.surface.surface_digest

    # Same assistant call message retained.
    assistant_msg = result.messages[1]
    assert isinstance(assistant_msg, ProviderAssistantMessage)
    assert assistant_msg.content == provisional
    assert len(assistant_msg.tool_calls) == 1

    # One tool result before round 2.
    tool_msg = result.messages[2]
    assert isinstance(tool_msg, ProviderToolMessage)
    assert tool_msg.call_id == call_id
    assert tool_msg.content.status == "completed"
    assert tool_msg.content.structured_output == {"count": 3}

    # Dispatcher saw domain key, binding, call id, original manifest/surface.
    assert len(dispatcher.requests) == 1
    dreq = dispatcher.requests[0]
    assert dreq.call.domain_key == "tools.search"
    assert dreq.call.call_id == call_id
    assert dreq.binding.ref.binding_contract_digest == binding.ref.binding_contract_digest
    assert dreq.call.manifest_digest == res1.surface.manifest_digest
    assert dreq.call.surface_digest == res1.surface.surface_digest

    # Fresh evidence issued for exact scope/call/descriptor; verified once by fake gateway.
    assert len(auth.issued) == 1
    assert auth.issued[0]["call_id"] == call_id
    assert auth.issued[0]["scope_digest"] == scope.scope_digest
    assert auth.issued[0]["descriptor_digest"] == descriptor.descriptor_digest
    assert dispatcher.evidence_verified == [DIGEST_F]

    # Pre-plan verifier + dispatcher pre-dispatch verifier both called.
    assert len(verifier.calls) >= 2

    # tools_provider called again before round 2.
    assert len(tools.calls) == 2
    assert tools.calls[0]["locale"] == "en"
    assert tools.calls[1]["locale"] == "en"

    assert "tool_call.requested" in events.types()
    assert "tool_call.started" in events.types()
    assert "tool_call.completed" in events.types()
    assert "final_text.delta" in events.types()


# ---------------------------------------------------------------------------
# Step 4: dynamic next-manifest
# ---------------------------------------------------------------------------


def test_dynamic_next_manifest_exposes_new_tool_on_round_two() -> None:
    base = _manifest()
    binding_a, desc_a = _pair("tools.search", target_id=TARGET_A)
    binding_b, desc_b = _pair("tools.detail", target_id=TARGET_B)

    res1 = _build_surface(base, [(binding_a, desc_a)])
    # Dispatcher appends a new capability via skill activation (append-only).
    child = append_skill_activation(
        res1.manifest,
        skill=ResolvedSkillRef(
            package_id=SKILL_PKG,
            version_id=SKILL_VER,
            canonical_name="demo.skill",
            sequence=1,
            content_digest=DIGEST_A,
            version_digest=DIGEST_B,
            requested_name_normalized=None,
            resolved_via_alias_id=None,
        ),
        capabilities=(binding_b.ref,),
    )
    # Round 2 surface includes both tools; aliases append-only.
    res2 = build_provider_tool_surface(
        manifest=child,
        provider_protocol=P,
        visible=[(binding_a, desc_a), (binding_b, desc_b)],
        scope=_scope(),
    )

    model = _model()
    user = ProviderUserMessage(content="use tool then grow")
    alias_a = res1.surface.tools[0].provider_alias
    call_id = "call_dyn_1"

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if request.round_index == 0:
                assert request.tool_surface.surface_digest == res1.surface.surface_digest
                assert [t.domain_key for t in request.tool_surface.tools] == ["tools.search"]
                yield from tool_call_then_terminal(
                    call_id=call_id,
                    provider_alias=alias_a,
                    arguments_json='{"query":"x"}',
                )
                return
            assert request.round_index == 1
            # Round 2 sees the new tool; existing alias unchanged.
            aliases = {t.domain_key: t.provider_alias for t in request.tool_surface.tools}
            assert "tools.detail" in aliases
            assert aliases["tools.search"] == alias_a
            # No skill-specific control name hardcoded in loop — surface comes from provider.
            assert request.tool_surface.surface_digest == res2.surface.surface_digest
            yield from text_then_terminal("done with new surface")

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res1, res2])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            binding_a.ref.binding_contract_digest: desc_a,
            binding_b.ref.binding_contract_digest: desc_b,
        }
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=child,
            )
        ],
        verifier=verifier,
    )

    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="zh",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == "done with new surface"
    assert result.manifest.manifest_digest == res2.manifest.manifest_digest
    # Round-1 call remains bound to old surface.
    assert result.tool_calls[0].call.surface_digest == res1.surface.surface_digest
    old_alias_refs = {
        (a.domain_key, a.provider_alias, a.binding_contract_digest)
        for a in res1.manifest.provider_aliases
    }
    new_alias_refs = {
        (a.domain_key, a.provider_alias, a.binding_contract_digest)
        for a in result.manifest.provider_aliases
    }
    assert old_alias_refs.issubset(new_alias_refs)


# ---------------------------------------------------------------------------
# Step 5: normal tool failure continues
# ---------------------------------------------------------------------------


def test_normal_tool_failure_reaches_round_two() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res1 = _build_surface(base, [(binding, descriptor)])
    res2 = ToolSurfaceResolution(manifest=res1.manifest, surface=res1.surface)
    model = _model()
    user = ProviderUserMessage(content="fail softly")
    alias = res1.surface.tools[0].provider_alias
    call_id = "call_fail_1"
    secret = "Traceback: secret SQL password=hunter2"

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if request.round_index == 0:
                yield from tool_call_then_terminal(
                    call_id=call_id,
                    provider_alias=alias,
                    arguments_json='{"query":"x"}',
                )
                return
            tool_msg = request.messages[2]
            assert isinstance(tool_msg, ProviderToolMessage)
            assert tool_msg.content.status == "failed"
            # No exception string / raw output leak.
            dumped = str(tool_msg.content.model_dump())
            assert secret not in dumped
            assert "hunter2" not in dumped
            yield from text_then_terminal("handled failure")

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res1, res2])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=failed_result(
                    error=CapabilityError(
                        error_type="execution_failed",
                        safe_code="execution_failed",
                        safe_message="tool execution failed",
                        retry_disposition="model_may_continue",
                    ),
                    metrics=_metrics(),
                ),
                next_manifest=res1.manifest,
            )
        ],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == "handled failure"
    assert result.tool_calls[0].status == "failed"
    assert secret not in str(result.model_dump())


# ---------------------------------------------------------------------------
# Step 6: early failures
# ---------------------------------------------------------------------------


def test_cancellation_before_round() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    provider = _scripted(model)
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    cancellation = RecordingCancellation(cancelled=True)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="x"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
            cancellation=cancellation,
        ),
    )
    assert result.status == "cancelled"
    assert result.stop_reason == "cancelled"
    assert provider.request_count == 0
    assert tools.calls == []


def test_tools_provider_failure() -> None:
    base = _manifest()
    model = _model()
    provider = _scripted(model)
    tools = RecordingToolsProvider(resolutions=[], fail_with=RuntimeError("db down"))
    verifier = RecordingDescriptorVerifier(current_by_binding={})
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="x"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "protocol_error"
    assert result.error is not None
    assert result.error.semantic_code == "tools_provider_failed"
    assert provider.request_count == 0


def test_invalid_surface_from_tools_provider() -> None:
    base = _manifest()
    model = _model()
    provider = _scripted(model)

    class BadTools:
        def resolve(self, manifest, *, scope, locale):
            return {"not": "a surface"}

    verifier = RecordingDescriptorVerifier(current_by_binding={})
    ports = ProviderLoopPorts(
        provider=provider,
        tools_provider=BadTools(),  # type: ignore[arg-type]
        current_descriptors=verifier,
        authorization_evidence=RecordingAuthFactory(),
        tool_dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=RecordingCancellation(),
        events=RecordingEventSink(),
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="x"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "invalid_surface"


def test_adapter_config_mismatch_before_provider() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    provider = ScriptedProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest="0" * 64,  # wrong
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="x"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "adapter_config_mismatch"
    assert provider.request_count == 0
    assert tools.calls == []


def test_adapter_key_mismatch_before_provider() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    provider = ScriptedProvider(
        provider_protocol=P,
        adapter_key="other_adapter",
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="x"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "adapter_key_mismatch"
    assert provider.request_count == 0


def test_provider_error_maps_safely() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=text_then_terminal("unused"),
            raise_error=RuntimeError("OpenAIError api_key=sk-secret body=..."),
        )
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "provider_error"
    assert result.error is not None
    assert result.error.semantic_code == "provider_error"
    assert "sk-secret" not in result.error.safe_summary
    assert "OpenAIError" not in result.error.safe_summary


def test_empty_response_is_protocol_error() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=text_then_terminal("   "),
        )
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "empty_response"


def test_unknown_alias_fails_without_dispatch() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id="c1",
                provider_alias="not_a_real_alias",
                arguments_json='{"query":"x"}',
            ),
        )
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "protocol_error"
    assert dispatcher.requests == []
    assert auth.issued == []


def test_invalid_call_arguments_fail_without_dispatch() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id="c1",
                provider_alias=alias,
                arguments_json='["not","object"]',
            ),
        )
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "protocol_error"
    assert dispatcher.requests == []


def test_duplicate_call_id_fails_without_dispatch() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias
    from app.assistant.provider_loop.contracts import (
        ProviderRoundTerminal,
        ProviderToolCallDelta,
    )

    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="dup",
            provider_alias_delta=alias,
            arguments_delta='{"query":"a"}',
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=1,
            call_id="dup",
            provider_alias_delta=alias,
            arguments_delta='{"query":"b"}',
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="tool_calls"),
    )
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=res.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=events,
        )
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "protocol_error"
    assert dispatcher.requests == []


def test_dispatcher_fatal_capability_error_seals_call() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias
    call_id = "call_fatal"

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            yield from tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=failed_result(
                    error=CapabilityError(
                        error_type="unauthorized",
                        safe_code="unauthorized",
                        safe_message="not authorized",
                        retry_disposition="never",
                    ),
                    metrics=_metrics(),
                ),
                next_manifest=res.manifest,
            )
        ],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "capability_error"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].status == "blocked"
    # Paired: assistant + tool message present.
    assert isinstance(result.messages[-1], ProviderToolMessage)
    assert result.messages[-1].call_id == call_id
    # No second provider round.
    assert provider.request_count == 1


@pytest.mark.parametrize(
    "mutate_kwargs",
    [
        {"classification_revision": "plan02-v2"},
        {"ruleset_digest": DIGEST_E},
        {"behavior_digest": DIGEST_F},
        {"descriptor_digest": DIGEST_D},
        {"availability_status": "disabled"},
        {"parallel_safe": False},
    ],
)
def test_classification_drift_dispatches_nothing(mutate_kwargs: dict[str, Any]) -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias
    call_id = "call_drift"

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    drifted = _descriptor(binding, **mutate_kwargs)
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: drifted}
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "capability_error"
    assert result.error is not None
    assert result.error.semantic_code == "classification_changed"
    assert dispatcher.requests == []
    assert auth.issued == []
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].status == "blocked"
    assert result.messages[-1].content.status == "blocked"  # type: ignore[union-attr]


def test_authorization_factory_failure_blocks_without_dispatch() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from tool_call_then_terminal(
                call_id="c_auth",
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory(fail_with=RuntimeError("factory boom"))
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "capability_error"
    assert result.error is not None
    assert result.error.semantic_code == "authorization_evidence_failed"
    assert dispatcher.requests == []
    assert result.tool_calls[0].status == "blocked"


def test_authorization_wrong_scope_blocks() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res.surface.tools[0].provider_alias

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from tool_call_then_terminal(
                call_id="c_scope",
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory(wrong_scope=True)
    dispatcher = RecordingDispatcher(results=[], verifier=verifier)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "authorization_scope_mismatch"
    assert dispatcher.requests == []


def test_returned_manifest_ancestor_is_rejected() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res1 = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res1.surface.tools[0].provider_alias

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from tool_call_then_terminal(
                call_id="c_anc",
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res1])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    auth = RecordingAuthFactory()
    # Return the parent base (ancestor of res1.manifest after alias append).
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=base if base.revision < res1.manifest.revision else base,
            )
        ],
        verifier=verifier,
    )
    # Ensure base is actually an ancestor when aliases were appended.
    if res1.manifest.revision == base.revision:
        # Force a child then ask for ancestor by building a second alias append and returning res1.
        binding_b, desc_b = _pair("tools.detail", target_id=TARGET_B)
        child = append_skill_activation(
            res1.manifest,
            skill=__import__(
                "app.assistant.domain.contracts", fromlist=["ResolvedSkillRef"]
            ).ResolvedSkillRef(
                package_id=SKILL_PKG,
                version_id=SKILL_VER,
                canonical_name="demo.skill",
                sequence=1,
                content_digest=DIGEST_A,
                version_digest=DIGEST_B,
            ),
            capabilities=(binding_b.ref,),
        )
        # Re-run with tools surface on child as current after tools_provider... simpler path:
        # current_manifest after tools is res1; return base only if revision lower.
        pass

    # If tools_provider appended aliases, res1.manifest.revision > base.revision.
    if res1.manifest.revision > base.revision:
        dispatcher.results = [
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=base,
            )
        ]
        result = run_provider_agent_loop(
            ProviderLoopRequest(
                manifest=base,
                initial_messages=(user,),
                model_ref=model,
                execution_scope=_scope(),
                max_rounds=4,
                locale="en",
            ),
            _ports(
                provider=provider,
                tools=tools,
                verifier=verifier,
                auth=auth,
                dispatcher=dispatcher,
            ),
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.semantic_code == "manifest_lineage_error"
        # Call already paired as completed before lineage rejection.
        assert result.tool_calls[0].status == "completed"
    else:
        # Fallback: return unrelated run manifest.
        other = _manifest(run_id=UUID("00000000-0000-4000-8000-000000000999"))
        dispatcher.results = [
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=other,
            )
        ]
        result = run_provider_agent_loop(
            ProviderLoopRequest(
                manifest=base,
                initial_messages=(user,),
                model_ref=model,
                execution_scope=_scope(),
                max_rounds=4,
                locale="en",
            ),
            _ports(
                provider=provider,
                tools=tools,
                verifier=verifier,
                auth=auth,
                dispatcher=dispatcher,
            ),
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.semantic_code == "manifest_lineage_error"


def test_returned_manifest_unrelated_run_rejected() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    res1 = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="x")
    alias = res1.surface.tools[0].provider_alias

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from tool_call_then_terminal(
                call_id="c_run",
                provider_alias=alias,
                arguments_json='{"query":"x"}',
            )

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res1])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    other = _manifest(run_id=UUID("00000000-0000-4000-8000-000000000998"))
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=other,
            )
        ],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "manifest_lineage_error"


def test_scope_digest_tamper_fails_before_ports() -> None:
    base = _manifest()
    model = _model()
    scope = _scope()
    # Bypass model validation by model_construct.
    bad_scope = scope.model_construct(
        run_id=scope.run_id,
        conversation_id=scope.conversation_id,
        principal=scope.principal,
        tenant_scope_id=scope.tenant_scope_id,
        scope_digest="f" * 64,
    )
    request = ProviderLoopRequest.model_construct(
        manifest=base,
        initial_messages=(ProviderUserMessage(content="x"),),
        model_ref=model,
        execution_scope=bad_scope,
        max_rounds=4,
        locale="en",
        generation=ProviderGenerationOptions(),
    )
    provider = _scripted(model)
    tools = RecordingToolsProvider(resolutions=[])
    verifier = RecordingDescriptorVerifier(current_by_binding={})
    result = run_provider_agent_loop(
        request,  # type: ignore[arg-type]
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier),
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "scope_digest_mismatch"
    assert provider.request_count == 0
    assert tools.calls == []


# ---------------------------------------------------------------------------
# Plan 04 Task 2: protected round context
# ---------------------------------------------------------------------------


@dataclass
class RecordingRoundContextProvider:
    """Scripted context provider that injects once per new skill version id."""

    content_by_skill_version: dict[UUID, str]
    calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_build_digest: str = DIGEST_3

    def resolve(
        self,
        *,
        manifest,
        already_applied_skill_version_ids,
        execution_scope,
        locale,
    ):
        self.calls.append(
            {
                "manifest_revision": manifest.revision,
                "manifest_digest": manifest.manifest_digest,
                "already_applied": already_applied_skill_version_ids,
                "scope_digest": execution_scope.scope_digest,
                "locale": locale,
                "active_skill_version_ids": tuple(
                    skill.version_id for skill in manifest.active_skills
                ),
            }
        )
        applied = set(already_applied_skill_version_ids)
        new_ids: list[UUID] = []
        messages: list[ProviderContextUpdateMessage] = []
        for skill in manifest.active_skills:
            if skill.version_id in applied:
                continue
            content = self.content_by_skill_version.get(skill.version_id)
            if content is None:
                continue
            new_ids.append(skill.version_id)
            messages.append(
                ProviderContextUpdateMessage(
                    locale=locale,
                    manifest_revision=manifest.revision,
                    manifest_digest=manifest.manifest_digest,
                    prompt_build_digest=self.prompt_build_digest,
                    content=content,
                )
            )
        return RoundContextResolution(
            manifest_revision=manifest.revision,
            manifest_digest=manifest.manifest_digest,
            applied_skill_version_ids=tuple(new_ids),
            messages=tuple(messages),
        )


def test_noop_round_context_is_default_and_byte_identical() -> None:
    base = _manifest()
    binding, descriptor = _pair("tools.search", target_id=TARGET_A)
    resolution = _build_surface(base, [(binding, descriptor)])
    model = _model()
    user = ProviderUserMessage(content="hello")
    final = "Hello from the model."

    # Default ports use NoOpRoundContextProvider.
    default_ports = ProviderLoopPorts(
        provider=_scripted(model),
        tools_provider=RecordingToolsProvider(resolutions=[]),
        current_descriptors=RecordingDescriptorVerifier(current_by_binding={}),
        authorization_evidence=RecordingAuthFactory(),
        tool_dispatcher=RecordingDispatcher(
            results=[], verifier=RecordingDescriptorVerifier(current_by_binding={})
        ),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=RecordingCancellation(),
        events=RecordingEventSink(),
    )
    assert isinstance(default_ports.round_context_provider, NoOpRoundContextProvider)

    # Explicit no-op vs default: same result shape for direct answer.
    provider_a = _scripted(model)
    provider_a.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=resolution.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            expected_generation=ProviderGenerationOptions(),
            expected_tool_aliases=tuple(t.provider_alias for t in resolution.surface.tools),
            events=text_then_terminal("Hello ", "from the model.", usage=_usage()),
        )
    )
    tools_a = RecordingToolsProvider(
        resolutions=[
            ToolSurfaceResolution(manifest=resolution.manifest, surface=resolution.surface)
        ]
    )
    verifier_a = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result_default = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider_a,
            tools=tools_a,
            verifier=verifier_a,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier_a),
        ),
    )
    provider_b = _scripted(model)
    provider_b.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(user,),
            expected_surface_digest=resolution.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            expected_generation=ProviderGenerationOptions(),
            expected_tool_aliases=tuple(t.provider_alias for t in resolution.surface.tools),
            events=text_then_terminal("Hello ", "from the model.", usage=_usage()),
        )
    )
    tools_b = RecordingToolsProvider(
        resolutions=[
            ToolSurfaceResolution(manifest=resolution.manifest, surface=resolution.surface)
        ]
    )
    verifier_b = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    result_noop = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider_b,
            tools=tools_b,
            verifier=verifier_b,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results=[], verifier=verifier_b),
            round_context_provider=NoOpRoundContextProvider(),
        ),
    )
    assert result_default.status == "completed"
    assert result_default.final_text == final
    assert result_noop.model_dump() == result_default.model_dump()
    # No runtime_context messages under no-op.
    assert not any(isinstance(m, ProviderContextUpdateMessage) for m in result_default.messages)


def test_context_injected_once_after_manifest_change_before_round_two() -> None:
    base = _manifest()
    binding_a, desc_a = _pair("tools.search", target_id=TARGET_A)
    binding_b, desc_b = _pair("tools.detail", target_id=TARGET_B)

    res1 = _build_surface(base, [(binding_a, desc_a)])
    child = append_skill_activation(
        res1.manifest,
        skill=ResolvedSkillRef(
            package_id=SKILL_PKG,
            version_id=SKILL_VER,
            canonical_name="demo.skill",
            sequence=1,
            content_digest=DIGEST_A,
            version_digest=DIGEST_B,
            requested_name_normalized=None,
            resolved_via_alias_id=None,
        ),
        capabilities=(binding_b.ref,),
    )
    res2 = build_provider_tool_surface(
        manifest=child,
        provider_protocol=P,
        visible=[(binding_a, desc_a), (binding_b, desc_b)],
        scope=_scope(),
    )
    model = _model()
    user = ProviderUserMessage(content="inject then answer")
    alias_a = res1.surface.tools[0].provider_alias
    call_id = "call_ctx_1"
    skill_body = "PROTECTED skill instructions for demo.skill"

    order: list[str] = []

    class OrderedTools(RecordingToolsProvider):
        def resolve(self, manifest, *, scope, locale):
            order.append("tools")
            return super().resolve(manifest, scope=scope, locale=locale)

    class OrderedCtx(RecordingRoundContextProvider):
        def resolve(self, *, manifest, already_applied_skill_version_ids, execution_scope, locale):
            order.append("context")
            return super().resolve(
                manifest=manifest,
                already_applied_skill_version_ids=already_applied_skill_version_ids,
                execution_scope=execution_scope,
                locale=locale,
            )

    ordered_ctx = OrderedCtx(content_by_skill_version={SKILL_VER: skill_body})

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if request.round_index == 0:
                # Round 0: base controls only — no skill context yet.
                assert not any(
                    isinstance(m, ProviderContextUpdateMessage) for m in request.messages
                )
                assert request.tool_surface.surface_digest == res1.surface.surface_digest
                yield from tool_call_then_terminal(
                    call_id=call_id,
                    provider_alias=alias_a,
                    arguments_json='{"query":"x"}',
                )
                return
            assert request.round_index == 1
            # Round 2 sees protected context exactly once, then tool result history.
            ctx_msgs = [
                m for m in request.messages if isinstance(m, ProviderContextUpdateMessage)
            ]
            assert len(ctx_msgs) == 1
            assert ctx_msgs[0].content == skill_body
            assert ctx_msgs[0].manifest_digest == res2.manifest.manifest_digest
            assert ctx_msgs[0].manifest_revision == res2.manifest.revision
            assert request.tool_surface.surface_digest == res2.surface.surface_digest
            yield from text_then_terminal("done with context")

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = OrderedTools(
        resolutions=[
            res1,
            res2,
        ]
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            binding_a.ref.binding_contract_digest: desc_a,
            binding_b.ref.binding_contract_digest: desc_b,
        }
    )
    auth = RecordingAuthFactory()
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=child,
            )
        ],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="zh",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=auth,
            dispatcher=dispatcher,
            round_context_provider=ordered_ctx,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == "done with context"
    # Tools before context on every non-finalization round.
    assert order == ["tools", "context", "tools", "context"]
    # Context appended once in final transcript.
    ctx_in_result = [
        m for m in result.messages if isinstance(m, ProviderContextUpdateMessage)
    ]
    assert len(ctx_in_result) == 1
    assert ctx_in_result[0].content == skill_body
    # Round 0: no skills yet. Round 1: skill newly active, already_applied still empty
    # at resolve-time; provider returns the skill id and loop records it after append.
    assert ordered_ctx.calls[0]["already_applied"] == ()
    assert ordered_ctx.calls[1]["already_applied"] == ()
    assert ordered_ctx.calls[1]["active_skill_version_ids"] == (SKILL_VER,)
    # Second resolve used exact accepted round-2 Manifest.
    assert ordered_ctx.calls[1]["manifest_digest"] == res2.manifest.manifest_digest
    # Pairing closed.
    from app.assistant.provider_loop.messages import validate_provider_transcript

    validate_provider_transcript(result.messages)


def test_alias_only_revision_does_not_duplicate_skill_context() -> None:
    base = _manifest()
    binding_a, desc_a = _pair("tools.search", target_id=TARGET_A)
    binding_b, desc_b = _pair("tools.detail", target_id=TARGET_B)
    res1 = _build_surface(base, [(binding_a, desc_a)])
    child = append_skill_activation(
        res1.manifest,
        skill=ResolvedSkillRef(
            package_id=SKILL_PKG,
            version_id=SKILL_VER,
            canonical_name="demo.skill",
            sequence=1,
            content_digest=DIGEST_A,
            version_digest=DIGEST_B,
            requested_name_normalized=None,
            resolved_via_alias_id=None,
        ),
        capabilities=(binding_b.ref,),
    )
    res2 = build_provider_tool_surface(
        manifest=child,
        provider_protocol=P,
        visible=[(binding_a, desc_a), (binding_b, desc_b)],
        scope=_scope(),
    )
    # Simulate alias-only child: same skills, new revision via append of no new aliases
    # is identity; instead re-resolve same skill manifest for a third round surface.
    res3 = ToolSurfaceResolution(manifest=res2.manifest, surface=res2.surface)

    model = _model()
    user = ProviderUserMessage(content="no dup")
    alias_a = res1.surface.tools[0].provider_alias
    skill_body = "skill once only"

    ctx = RecordingRoundContextProvider(content_by_skill_version={SKILL_VER: skill_body})

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if request.round_index == 0:
                yield from tool_call_then_terminal(
                    call_id="call_alias_1",
                    provider_alias=alias_a,
                    arguments_json='{"query":"x"}',
                )
                return
            if request.round_index == 1:
                # Force another tool round that will re-resolve tools on same skill manifest.
                yield from tool_call_then_terminal(
                    call_id="call_alias_2",
                    provider_alias=alias_a,
                    arguments_json='{"query":"y"}',
                )
                return
            ctx_msgs = [
                m for m in request.messages if isinstance(m, ProviderContextUpdateMessage)
            ]
            assert len(ctx_msgs) == 1
            yield from text_then_terminal("final")

    provider = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    tools = RecordingToolsProvider(resolutions=[res1, res2, res3])
    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            binding_a.ref.binding_contract_digest: desc_a,
            binding_b.ref.binding_contract_digest: desc_b,
        }
    )
    dispatcher = RecordingDispatcher(
        results=[
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok1", metrics=_metrics()),
                next_manifest=child,
            ),
            ProviderDispatchResult(
                capability_result=completed_result(user_text="ok2", metrics=_metrics()),
                next_manifest=res2.manifest,
            ),
        ],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(user,),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=6,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=tools,
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
            round_context_provider=ctx,
        ),
    )
    assert result.status == "completed"
    ctx_msgs = [m for m in result.messages if isinstance(m, ProviderContextUpdateMessage)]
    assert len(ctx_msgs) == 1
    # Three context resolves (rounds 0,1,2 non-finalization); only round 1 injects.
    assert len(ctx.calls) == 3
    assert ctx.calls[0]["already_applied"] == ()
    assert ctx.calls[1]["already_applied"] == ()  # inject happens during this call
    assert ctx.calls[2]["already_applied"] == (SKILL_VER,)  # reinjection suppressed
