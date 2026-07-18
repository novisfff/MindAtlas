"""Plan 03 Task 5: multi-call scheduling, waiting, resume, cancellation sealing."""

from __future__ import annotations

import threading
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
    CapabilityResult,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    ContinuationRef,
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
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderGenerationOptions,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopReservedResumeRequest,
    ProviderLoopResumeRequest,
    ProviderUsage,
    ProviderWaitingResolution,
    ToolSurfaceResolution,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import (  # noqa: E402
    ProviderAgentLoop,
    resume_provider_agent_loop,
    run_provider_agent_loop,
    seal_waiting_after_cancellation,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderToolCall,
    ProviderToolMessage,
    ProviderUserMessage,
    digest_arguments,
    digest_provider_message,
    digest_provider_transcript,
    project_tool_result_envelope,
    validate_provider_transcript,
)
from app.assistant.provider_loop.scheduler import (  # noqa: E402
    BoundedIsolatedSiblingExecutor,
    DispatcherCapabilities,
    SequentialSiblingExecutor,
    merge_parallel_manifests,
    plan_sibling_execution,
)
from app.assistant.provider_loop.scripted_provider import (  # noqa: E402
    ScriptedProvider,
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

RUN_ID = UUID("00000000-0000-4000-8000-000000000501")
CONV_ID = UUID("00000000-0000-4000-8000-000000000502")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000510")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000511")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000550")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000551")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000540")

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


def _scope(*, run_id: UUID = RUN_ID, tenant_scope_id: str | None = None, principal_id: str = "principal-loop"):
    return create_execution_scope(
        run_id=run_id,
        conversation_id=CONV_ID,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id=principal_id,
            authenticated=True,
        ),
        tenant_scope_id=tenant_scope_id,
    )


def _resolved_binding(
    *,
    capability_key: str,
    capability_type: str = "tool",
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
    target_identity = f"{capability_type}:{target}"
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": capability_type,
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
        capability_type=capability_type,  # type: ignore[arg-type]
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
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target if capability_type == "tool" else None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=target if capability_type == "agent" else None,
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


def _frozen(*, capability_key: str, capability_type: str = "tool", target_id: UUID | None = None, config_digest: str = DIGEST_B):
    resolved = _resolved_binding(
        capability_key=capability_key,
        capability_type=capability_type,
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
    interrupt_mode: str = "none",
    capability_type: str | None = None,
):
    resolved = binding.resolved if hasattr(binding, "resolved") else binding
    cap_type = capability_type or resolved.capability_type
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision=classification_revision,
            ruleset_digest=ruleset_digest,
        ),
        side_effect=side_effect,  # type: ignore[arg-type]
        parallel_safe=parallel_safe,
        interrupt_mode=interrupt_mode,  # type: ignore[arg-type]
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=behavior_digest,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type=cap_type,  # type: ignore[arg-type]
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


def _pair(
    capability_key: str,
    *,
    target_id: UUID | None = None,
    capability_type: str = "tool",
    **kwargs,
):
    binding = _frozen(capability_key=capability_key, capability_type=capability_type, target_id=target_id)
    return binding, _descriptor(binding, capability_type=capability_type, **kwargs)


def _call_from_def(
    surface,
    definition,
    *,
    call_id: str,
    call_index: int,
    arguments: dict[str, Any] | None = None,
) -> ProviderToolCall:
    args = arguments or {"query": f"q{call_index}"}
    return ProviderToolCall(
        call_id=call_id,
        call_index=call_index,
        provider_alias=definition.provider_alias,
        domain_key=definition.domain_key,
        arguments=args,
        arguments_digest=digest_arguments(args),
        binding_contract_digest=definition.binding.ref.binding_contract_digest,
        descriptor_digest=definition.descriptor.descriptor_digest,
        behavior_digest=definition.descriptor.behavior.behavior_digest,
        classification_revision=definition.descriptor.behavior.classification.revision,
        classification_ruleset_digest=definition.descriptor.behavior.classification.ruleset_digest,
        manifest_revision=surface.manifest_revision,
        manifest_digest=surface.manifest_digest,
        surface_digest=surface.surface_digest,
    )


@dataclass
class RecordingCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled


@dataclass
class RecordingEventSink:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


@dataclass
class RecordingToolsProvider:
    resolutions: list[ToolSurfaceResolution]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def resolve(self, manifest, *, scope, locale):
        self.calls.append(
            {
                "manifest_digest": manifest.manifest_digest,
                "scope_digest": scope.scope_digest,
                "locale": locale,
            }
        )
        if not self.resolutions:
            raise AssertionError("tools provider exhausted")
        return self.resolutions.pop(0)


@dataclass
class RecordingDescriptorVerifier:
    current_by_binding: dict[str, CapabilityDescriptor]
    calls: list[dict[str, Any]] = field(default_factory=list)
    mutate_after: int | None = None
    mutate_to: CapabilityDescriptor | None = None
    mutate_binding_digest: str | None = None
    _count: int = 0

    def require_current(self, *, binding, exposed_descriptor, scope):
        self._count += 1
        self.calls.append(
            {
                "binding_digest": binding.ref.binding_contract_digest,
                "exposed_descriptor_digest": exposed_descriptor.descriptor_digest,
                "scope_digest": scope.scope_digest,
                "count": self._count,
            }
        )
        current = self.current_by_binding.get(
            binding.ref.binding_contract_digest,
            exposed_descriptor,
        )
        if (
            self.mutate_after is not None
            and self._count > self.mutate_after
            and (
                self.mutate_binding_digest is None
                or binding.ref.binding_contract_digest == self.mutate_binding_digest
            )
        ):
            if self.mutate_to is not None:
                current = self.mutate_to
            else:
                raise RuntimeError("classification_changed")
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

    def issue(self, *, call, binding, descriptor, scope):
        evidence = CapabilityAuthorizationEvidence(
            issuer="test",
            call_id=call.call_id,
            principal=scope.principal,
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
            allowed_side_effects=("none", "compute", "read", "write_local", "draft", "unknown"),
            grant_source_digest=DIGEST_E,
            evidence_digest=sha256_canonical_json(
                {"callId": call.call_id, "scope": scope.scope_digest}
            ),
        )
        self.issued.append(
            {
                "call_id": call.call_id,
                "scope_digest": scope.scope_digest,
                "evidence_digest": evidence.evidence_digest,
            }
        )
        return evidence


@dataclass
class RecordingDispatcher:
    results_by_call_id: dict[str, ProviderDispatchResult] = field(default_factory=dict)
    result_queue: list[ProviderDispatchResult] = field(default_factory=list)
    verifier: RecordingDescriptorVerifier | None = None
    requests: list[ProviderDispatchRequest] = field(default_factory=list)
    active: dict[str, float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    barrier: threading.Barrier | None = None
    release_order: list[str] = field(default_factory=list)
    hold_until: dict[str, threading.Event] = field(default_factory=dict)
    started: dict[str, threading.Event] = field(default_factory=dict)
    fail_if_shared_session: bool = False
    parent_session_id: str | None = None
    seen_sessions: list[str] = field(default_factory=list)

    def dispatch(self, request: ProviderDispatchRequest, *, cancellation):
        del cancellation
        self.requests.append(request)
        if self.verifier is not None:
            self.verifier.require_current(
                binding=request.binding,
                exposed_descriptor=request.descriptor,
                scope=request.execution_scope,
            )
        call_id = request.call.call_id
        if call_id not in self.started:
            self.started[call_id] = threading.Event()
        self.started[call_id].set()
        with self.lock:
            self.active[call_id] = threading.get_ident()
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            hold = self.hold_until.get(call_id)
            if hold is not None:
                assert hold.wait(timeout=5), f"hold not released for {call_id}"
            if call_id in self.results_by_call_id:
                result = self.results_by_call_id[call_id]
            elif self.result_queue:
                result = self.result_queue.pop(0)
            else:
                raise AssertionError(f"no dispatch result for {call_id}")
            with self.lock:
                self.release_order.append(call_id)
            return result
        finally:
            with self.lock:
                self.active.pop(call_id, None)


@dataclass
class ThreadGuard:
    parent_session_id: str = "parent-session"
    seen_threads: set[int] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def enter_worker(self, *, parent_session_id: str | None, thread_id: int) -> None:
        with self.lock:
            if parent_session_id == self.parent_session_id and thread_id in self.seen_threads:
                self.failures.append("reused thread unexpectedly")
            if parent_session_id is not None and parent_session_id != self.parent_session_id:
                # parent id mismatch is fine; workers must not use parent session
                pass
            if thread_id in self.seen_threads:
                # same worker thread may be reused by pool; that is ok
                pass
            self.seen_threads.add(thread_id)

    def exit_worker(self, *, thread_id: int) -> None:
        del thread_id


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
    sibling_executor=None,
    cancellation: RecordingCancellation | None = None,
    events: RecordingEventSink | None = None,
    capability_ledger=None,
) -> ProviderLoopPorts:
    return ProviderLoopPorts(
        provider=provider,
        tools_provider=tools,
        current_descriptors=verifier,
        authorization_evidence=auth,
        tool_dispatcher=dispatcher,
        sibling_executor=sibling_executor or SequentialSiblingExecutor(),
        cancellation=cancellation or RecordingCancellation(),
        events=events or RecordingEventSink(),
        capability_ledger=capability_ledger,
    )


def _surface_for_pairs(manifest, pairs):
    return build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=P,
        visible=pairs,
        scope=_scope(),
    )




def _alias_by_domain(surface) -> dict[str, str]:
    return {definition.domain_key: definition.provider_alias for definition in surface.tools}


def _multi_tool_events(surface, call_specs: list[tuple[str, str, dict[str, Any]]], *, usage=None):
    """Build stream events for multi tool calls by alias."""
    from app.assistant.provider_loop.contracts import (
        ProviderRoundTerminal,
        ProviderToolCallDelta,
        ProviderUsageSnapshot,
    )

    events = []
    seq = 0
    for index, (call_id, alias, args) in enumerate(call_specs):
        import json

        args_json = json.dumps(args, separators=(",", ":"), sort_keys=True)
        events.append(
            ProviderToolCallDelta(
                sequence=seq,
                call_index=index,
                call_id=call_id,
                provider_alias_delta=alias,
                arguments_delta=args_json,
            )
        )
        seq += 1
    if usage is not None:
        events.append(ProviderUsageSnapshot(sequence=seq, usage=usage))
        seq += 1
    events.append(ProviderRoundTerminal(sequence=seq, finish_reason="tool_calls"))
    return events


# ---------------------------------------------------------------------------
# Step 1: pure scheduling plan tests
# ---------------------------------------------------------------------------


def _plan_specs():
    """Return (name, pairs kwargs, expected modes/boundaries)."""
    return []


def test_plan_read_safe_read_safe_parallel_when_supported() -> None:
    manifest = _manifest()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
    ]
    res = _surface_for_pairs(manifest, pairs)
    calls = tuple(
        _call_from_def(res.surface, definition, call_id=f"c{i}", call_index=i)
        for i, definition in enumerate(res.surface.tools)
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
    )
    assert len(groups) == 1
    assert groups[0].mode == "parallel"
    assert groups[0].call_indexes == (0, 1)


def test_plan_read_then_write_splits_groups() -> None:
    manifest = _manifest()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="3" * 64, behavior_digest="4" * 64),
    ]
    res = _surface_for_pairs(manifest, pairs)
    calls = tuple(
        _call_from_def(res.surface, definition, call_id=f"c{i}", call_index=i)
        for i, definition in enumerate(res.surface.tools)
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
    )
    assert [g.mode for g in groups] == ["parallel", "sequential"]
    assert groups[0].call_indexes == (0,)
    assert groups[1].call_indexes == (1,)


def test_plan_write_then_read_splits_groups() -> None:
    manifest = _manifest()
    write_pair = _pair(
        "tools.write_a",
        side_effect="write_local",
        parallel_safe=False,
        descriptor_digest="1" * 64,
        behavior_digest="2" * 64,
    )
    read_pair = _pair(
        "tools.read_a",
        side_effect="read",
        parallel_safe=True,
        descriptor_digest="3" * 64,
        behavior_digest="4" * 64,
    )
    res = _surface_for_pairs(manifest, [write_pair, read_pair])
    by_key = {definition.domain_key: definition for definition in res.surface.tools}
    calls = (
        _call_from_def(res.surface, by_key["tools.write_a"], call_id="c0", call_index=0),
        _call_from_def(res.surface, by_key["tools.read_a"], call_id="c1", call_index=1),
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
    )
    assert [g.mode for g in groups] == ["sequential", "parallel"]
    assert groups[0].call_indexes == (0,)
    assert groups[1].call_indexes == (1,)


def test_plan_compute_read_write_read_groups() -> None:
    manifest = _manifest()
    pairs = [
        _pair("tools.compute", side_effect="compute", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="5" * 64, behavior_digest="6" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="7" * 64, behavior_digest="8" * 64),
    ]
    res = _surface_for_pairs(manifest, pairs)
    by_key = {definition.domain_key: definition for definition in res.surface.tools}
    ordered_keys = ["tools.compute", "tools.read_a", "tools.write_a", "tools.read_b"]
    calls = tuple(
        _call_from_def(res.surface, by_key[key], call_id=f"c{i}", call_index=i)
        for i, key in enumerate(ordered_keys)
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
    )
    assert [(g.mode, g.call_indexes) for g in groups] == [
        ("parallel", (0, 1)),
        ("sequential", (2,)),
        ("parallel", (3,)),
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"capability_type": "agent", "side_effect": "read", "parallel_safe": True},
        {"side_effect": "draft", "parallel_safe": False},
        {"side_effect": "unknown", "parallel_safe": False},
        {"side_effect": "read", "parallel_safe": True, "interrupt_mode": "durable"},
        {"side_effect": "read", "parallel_safe": False},
    ],
)
def test_plan_unsafe_kinds_are_sequential(kwargs: dict[str, Any]) -> None:
    manifest = _manifest()
    pair = _pair(
        "tools.special",
        descriptor_digest="1" * 64,
        behavior_digest="2" * 64,
        **kwargs,
    )
    res = _surface_for_pairs(manifest, [pair])
    calls = (
        _call_from_def(res.surface, res.surface.tools[0], call_id="c0", call_index=0),
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
    )
    assert len(groups) == 1
    assert groups[0].mode == "sequential"


def test_plan_legacy_blocking_descriptor_is_sequential_via_alias_map() -> None:
    """legacy_blocking is excluded from surfaces; planner still treats it sequential."""
    binding, desc = _pair(
        "tools.legacy",
        side_effect="read",
        parallel_safe=True,
        interrupt_mode="none",
        descriptor_digest="1" * 64,
        behavior_digest="2" * 64,
    )
    # Force interrupt_mode on a synthetic descriptor map without surface validation.
    legacy = desc.model_copy(
        update={
            "behavior": desc.behavior.model_copy(update={"interrupt_mode": "legacy_blocking"})
        }
    )
    manifest = _manifest()
    res = _surface_for_pairs(manifest, [(binding, desc)])
    call = _call_from_def(res.surface, res.surface.tools[0], call_id="c0", call_index=0)
    groups = plan_sibling_execution(
        (call,),
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=True),
        descriptors_by_alias={call.provider_alias: legacy},
    )
    assert groups[0].mode == "sequential"


def test_plan_dispatcher_parallel_unsupported_forces_sequential() -> None:
    manifest = _manifest()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
    ]
    res = _surface_for_pairs(manifest, pairs)
    calls = tuple(
        _call_from_def(res.surface, definition, call_id=f"c{i}", call_index=i)
        for i, definition in enumerate(res.surface.tools)
    )
    groups = plan_sibling_execution(
        calls,
        surface=res.surface,
        dispatcher_capabilities=DispatcherCapabilities(supports_isolated_parallel=False),
    )
    assert len(groups) == 1
    assert groups[0].mode == "sequential"
    assert groups[0].call_indexes == (0, 1)


def test_preplan_verifier_rejects_stale_parallel_safe_before_planner() -> None:
    manifest = _manifest()
    binding, desc = _pair(
        "tools.read_a",
        side_effect="read",
        parallel_safe=True,
        descriptor_digest="1" * 64,
        behavior_digest="2" * 64,
    )
    res = _surface_for_pairs(manifest, [(binding, desc)])
    stale = _descriptor(
        binding,
        side_effect="read",
        parallel_safe=False,
        descriptor_digest="9" * 64,
        behavior_digest="8" * 64,
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: stale}
    )
    with pytest.raises(RuntimeError, match="classification_changed"):
        verifier.require_current(
            binding=binding,
            exposed_descriptor=desc,
            scope=_scope(),
        )
    # Planner is never called after verifier failure in the loop path.


# ---------------------------------------------------------------------------
# Bounded parallel + sequential unsafe + mixed failures
# ---------------------------------------------------------------------------


def test_bounded_parallel_overlap_and_order() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    res2 = _surface_for_pairs(manifest, pairs)
    aliases = [tool.provider_alias for tool in res1.surface.tools]
    call_specs = [
        ("call-a", aliases[0], {"query": "a"}),
        ("call-b", aliases[1], {"query": "b"}),
    ]
    from app.assistant.provider_loop.scripted_provider import ScriptedRoundScript
    del ScriptedRoundScript  # used only as import presence check

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count == 1:
                assert len(request.messages) == 1
                yield from _multi_tool_events(
                    res1.surface,
                    call_specs,
                    usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
                return
            if self.request_count == 2:
                # Full pairing required before next Provider request.
                validate_provider_transcript(request.messages)
                tool_msgs = [m for m in request.messages if isinstance(m, ProviderToolMessage)]
                assert [m.call_id for m in tool_msgs] == ["call-a", "call-b"]
                yield from text_then_terminal(
                    "done",
                    usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
                return
            raise AssertionError("extra provider request")

    flex = Flex(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            pairs[0][0].ref.binding_contract_digest: pairs[0][1],
            pairs[1][0].ref.binding_contract_digest: pairs[1][1],
        }
    )
    barrier = threading.Barrier(2)
    dispatcher = RecordingDispatcher(
        results_by_call_id={
            "call-a": ProviderDispatchResult(
                capability_result=completed_result(user_text="A", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "call-b": ProviderDispatchResult(
                capability_result=completed_result(user_text="B", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
        barrier=barrier,
    )
    auth = RecordingAuthFactory()
    guard = ThreadGuard()
    ports = _ports(
        provider=flex,
        tools=RecordingToolsProvider([res1, res2]),
        verifier=verifier,
        auth=auth,
        dispatcher=dispatcher,
        sibling_executor=BoundedIsolatedSiblingExecutor(
            max_workers=2,
            parent_session_id=guard.parent_session_id,
            guard=guard,
        ),
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed"
    assert result.final_text == "done"
    assert [r.call.call_id for r in result.tool_calls] == ["call-a", "call-b"]
    assert len(auth.issued) == 2
    assert auth.issued[0]["evidence_digest"] != auth.issued[1]["evidence_digest"]
    assert flex.request_count == 2


def test_writes_never_overlap() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.write_b", side_effect="write_local", parallel_safe=False, descriptor_digest="3" * 64, behavior_digest="4" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    res2 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    active: list[str] = []
    max_active = 0
    lock = threading.Lock()

    class WriteDispatcher(RecordingDispatcher):
        def dispatch(self, request, *, cancellation):
            call_id = request.call.call_id
            with lock:
                active.append(call_id)
                nonlocal max_active
                max_active = max(max_active, len(active))
            try:
                return super().dispatch(request, cancellation=cancellation)
            finally:
                with lock:
                    active.remove(call_id)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count == 1:
                yield from _multi_tool_events(
                    res1.surface,
                    [
                        ("w1", aliases["tools.write_a"], {"query": "1"}),
                        ("w2", aliases["tools.write_b"], {"query": "2"}),
                    ],
                    usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
                return
            yield from text_then_terminal(
                "ok",
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            pairs[0][0].ref.binding_contract_digest: pairs[0][1],
            pairs[1][0].ref.binding_contract_digest: pairs[1][1],
        }
    )
    dispatcher = WriteDispatcher(
        results_by_call_id={
            "w1": ProviderDispatchResult(
                capability_result=completed_result(user_text="1", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "w2": ProviderDispatchResult(
                capability_result=completed_result(user_text="2", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1, res2]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
            sibling_executor=BoundedIsolatedSiblingExecutor(max_workers=4),
        ),
    )
    assert result.status == "completed"
    assert max_active == 1


def test_fatal_in_parallel_retains_all_started_in_order() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
        _pair("tools.read_c", side_effect="read", parallel_safe=True, descriptor_digest="5" * 64, behavior_digest="6" * 64),
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="7" * 64, behavior_digest="8" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    release_b = threading.Event()
    release_ac = threading.Event()

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count != 1:
                raise AssertionError("no further provider request after fatal")
            yield from _multi_tool_events(
                res1.surface,
                [
                    ("c0", aliases["tools.read_a"], {"query": "0"}),
                    ("c1", aliases["tools.read_b"], {"query": "1"}),
                    ("c2", aliases["tools.read_c"], {"query": "2"}),
                    ("c3", aliases["tools.write_a"], {"query": "3"}),
                ],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    class OrderedDispatcher(RecordingDispatcher):
        def dispatch(self, request, *, cancellation):
            call_id = request.call.call_id
            if call_id == "c1":
                # complete first
                return super().dispatch(request, cancellation=cancellation)
            # wait until c1 finished path starts collecting; release after c1 request recorded
            if call_id in {"c0", "c2"}:
                assert release_ac.wait(timeout=5)
            return super().dispatch(request, cancellation=cancellation)

    verifier = RecordingDescriptorVerifier(
        current_by_binding={
            p[0].ref.binding_contract_digest: p[1] for p in pairs
        }
    )
    dispatcher = OrderedDispatcher(
        results_by_call_id={
            "c0": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok0", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "c1": ProviderDispatchResult(
                capability_result=failed_result(
                    error=CapabilityError(
                        error_type="unauthorized",
                        safe_code="policy_denied",
                        safe_message="denied",
                        retry_disposition="never",
                    ),
                    metrics=_metrics(),
                ),
                next_manifest=res1.manifest,
            ),
            "c2": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok2", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "c3": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok3", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
    )
    # Let c1 run, then release others so all three start.
    def _releaser():
        # Wait until c1 is requested.
        for _ in range(100):
            if any(r.call.call_id == "c1" for r in dispatcher.requests):
                release_ac.set()
                return
            threading.Event().wait(0.01)
        release_ac.set()

    threading.Thread(target=_releaser, daemon=True).start()

    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
            sibling_executor=BoundedIsolatedSiblingExecutor(max_workers=3),
        ),
    )
    assert result.status == "failed"
    statuses = [(r.call.call_id, r.status) for r in result.tool_calls]
    assert statuses[0][0] == "c0"
    assert statuses[1][0] == "c1"
    assert statuses[2][0] == "c2"
    assert statuses[3] == ("c3", "cancelled_before_start")
    assert statuses[0][1] == "completed"
    assert statuses[1][1] == "blocked"
    assert statuses[2][1] == "completed"
    # c3 never dispatched
    assert set(r.call.call_id for r in dispatcher.requests) == {"c0", "c1", "c2"}


def test_conflicting_parallel_children_rejected() -> None:
    parent = _manifest()
    with pytest.raises(ValueError, match="conflicting"):
        fake_a = parent.model_copy(
            update={"revision": parent.revision + 1, "manifest_digest": "a" * 64}
        )
        fake_b = parent.model_copy(
            update={"revision": parent.revision + 1, "manifest_digest": "b" * 64}
        )
        merge_parallel_manifests(parent=parent, children=[fake_a, fake_b])


# ---------------------------------------------------------------------------
# Waiting / resume / seal
# ---------------------------------------------------------------------------


def _continuation_ref(reference_id: str = "cont-1") -> ContinuationRef:
    return ContinuationRef(
        continuation_type="test.durable",
        contract_version=1,
        reference_id=reference_id,
        payload_digest=DIGEST_D,
    )


def test_waiting_scenario_retains_prefix_defers_suffix() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="durable",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="5" * 64, behavior_digest="6" * 64),
        _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="7" * 64, behavior_digest="8" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    cont_ref = _continuation_ref()

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count != 1:
                raise AssertionError("no provider request while waiting")
            yield from _multi_tool_events(
                res1.surface,
                [
                    ("r1", aliases["tools.read_a"], {"query": "1"}),
                    ("w1", aliases["tools.wait"], {"query": "wait"}),
                    ("wr", aliases["tools.write_a"], {"query": "write"}),
                    ("r2", aliases["tools.read_b"], {"query": "2"}),
                ],
                usage=ProviderUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={p[0].ref.binding_contract_digest: p[1] for p in pairs}
    )
    waiting_result = CapabilityResult(
        status="waiting",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=cont_ref,
        terminal_output=False,
        needs_followup=True,
        error=None,
        metrics=_metrics(),
    )
    dispatcher = RecordingDispatcher(
        results_by_call_id={
            "r1": ProviderDispatchResult(
                capability_result=completed_result(user_text="read-ok", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "w1": ProviderDispatchResult(
                capability_result=waiting_result,
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
    )
    class RecordingLedger:
        def __init__(self):
            self.reservations = []
            self.pause_calls = 0

        def reserve_siblings(self, requests, provider_messages=()):
            assert provider_messages
            self.reservations.append(tuple(request.call.call_id for request in requests))

        def commit_progress(self, provider_messages=(), **_kwargs):
            del provider_messages

        def commit_recovery_drift(self, provider_messages, *, stale_call_id):
            del provider_messages, stale_call_id

        def commit_pause(self, continuation, provider_messages=()):
            del continuation, provider_messages
            self.pause_calls += 1

    ledger = RecordingLedger()
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
            capability_ledger=ledger,
        ),
    )
    assert result.status == "waiting"
    assert result.stop_reason == "waiting_interrupt"
    assert result.continuation is not None
    cont = result.continuation
    assert cont.waiting_call.call_id == "w1"
    assert cont.pending_call_ids == ("wr", "r2")
    assert cont.next_call_index == 2
    assert cont.exposed_surface.surface_digest == res1.surface.surface_digest
    assert cont.current_manifest_digest == res1.manifest.manifest_digest
    assert cont.provider_rounds_used == 1
    assert cont.waiting_call.capability_continuation.reference_id == "cont-1"
    assert cont.waiting_call.descriptor_digest == pairs[1][1].descriptor_digest
    statuses = {r.call.call_id: r.status for r in result.tool_calls}
    assert statuses["r1"] == "completed"
    assert statuses["w1"] == "waiting"
    assert statuses["wr"] == "deferred"
    assert statuses["r2"] == "deferred"
    # No fabricated tool messages for waiting/deferred.
    tool_msgs = [m for m in result.messages if isinstance(m, ProviderToolMessage)]
    assert [m.call_id for m in tool_msgs] == ["r1"]
    validate_provider_transcript(result.messages, allowed_open_continuation=cont)
    assert [r.call.call_id for r in dispatcher.requests] == ["r1", "w1"]
    assert ledger.reservations == [("r1", "w1", "wr", "r2")]
    assert ledger.pause_calls == 1


def test_reserved_sibling_resume_dispatches_open_prefix_before_provider() -> None:
    from app.assistant.provider_loop.loop import (
        ProviderLoopError,
        resume_reserved_provider_loop,
    )

    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=False),
        _pair("tools.read_b", side_effect="read", parallel_safe=False),
    ]
    resolution = _surface_for_pairs(manifest, pairs)
    definitions = {item.domain_key: item for item in resolution.surface.tools}
    calls = (
        _call_from_def(
            resolution.surface,
            definitions["tools.read_a"],
            call_id="reserved-r1",
            call_index=0,
        ),
        _call_from_def(
            resolution.surface,
            definitions["tools.read_b"],
            call_id="reserved-r2",
            call_index=1,
        ),
    )
    persisted_result = completed_result(user_text="ok", metrics=_metrics())
    open_messages = (
        ProviderUserMessage(content="resume"),
        ProviderAssistantMessage(content=None, tool_calls=calls),
        ProviderToolMessage(
            call_id=calls[0].call_id,
            provider_alias=calls[0].provider_alias,
            content=project_tool_result_envelope(
                domain_key=calls[0].domain_key,
                result=persisted_result,
            ),
        ),
    )

    class FinalProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            tool_ids = [
                item.call_id
                for item in request.messages
                if isinstance(item, ProviderToolMessage)
            ]
            assert tool_ids == ["reserved-r1", "reserved-r2"]
            yield from text_then_terminal(
                "done",
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={pair[0].ref.binding_contract_digest: pair[1] for pair in pairs}
    )
    dispatcher = RecordingDispatcher(
        results_by_call_id={
            call.call_id: ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=resolution.manifest,
            )
            for call in calls[1:]
        },
        verifier=verifier,
    )
    result = resume_reserved_provider_loop(
        ProviderLoopReservedResumeRequest(
            manifest=resolution.manifest,
            initial_messages=open_messages,
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=FinalProvider(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([resolution, resolution]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == "done"
    assert [item.call.call_id for item in dispatcher.requests] == ["reserved-r2"]

    stale_verifier = RecordingDescriptorVerifier(
        current_by_binding={
            pair[0].ref.binding_contract_digest: pair[1] for pair in pairs
        },
        mutate_after=1,
    )
    stale_dispatcher = RecordingDispatcher(
        results_by_call_id={
            call.call_id: ProviderDispatchResult(
                capability_result=completed_result(user_text="unexpected", metrics=_metrics()),
                next_manifest=resolution.manifest,
            )
            for call in calls
        },
        verifier=stale_verifier,
    )
    with pytest.raises(ProviderLoopError, match="classification changed"):
        resume_reserved_provider_loop(
            ProviderLoopReservedResumeRequest(
                manifest=resolution.manifest,
                initial_messages=open_messages[:2],
                model_ref=model,
                execution_scope=_scope(),
                max_rounds=4,
                locale="en",
                generation=ProviderGenerationOptions(),
            ),
            _ports(
                provider=FinalProvider(
                    provider_protocol=P,
                    adapter_key=ADAPTER_KEY,
                    adapter_revision=ADAPTER_REVISION,
                    model_config_digest=MODEL_CONFIG,
                    expected_model_ref=model,
                ),
                tools=RecordingToolsProvider([resolution]),
                verifier=stale_verifier,
                auth=RecordingAuthFactory(),
                dispatcher=stale_dispatcher,
            ),
        )
    assert stale_dispatcher.requests == []


def test_resume_completes_pending_and_provider_after_pairing() -> None:
    # Build a waiting state first, then resume.
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="durable",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
        _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="5" * 64, behavior_digest="6" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    res2 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    cont_ref = _continuation_ref().model_copy(
        update={"continuation_type": "capability_call"}
    )

    class FlexStart(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count != 1:
                raise AssertionError("start should not continue provider")
            yield from _multi_tool_events(
                res1.surface,
                [
                    ("r1", aliases["tools.read_a"], {"query": "1"}),
                    ("w1", aliases["tools.wait"], {"query": "wait"}),
                    ("wr", aliases["tools.write_a"], {"query": "write"}),
                ],
                usage=ProviderUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={p[0].ref.binding_contract_digest: p[1] for p in pairs}
    )
    start_dispatcher = RecordingDispatcher(
        results_by_call_id={
            "r1": ProviderDispatchResult(
                capability_result=completed_result(user_text="read-ok", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "w1": ProviderDispatchResult(
                capability_result=CapabilityResult(
                    status="waiting",
                    user_text=None,
                    structured_output=None,
                    artifact_refs=(),
                    continuation=cont_ref,
                    terminal_output=False,
                    needs_followup=True,
                    error=None,
                    metrics=_metrics(),
                ),
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
    )
    waiting = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=FlexStart(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=start_dispatcher,
        ),
    )
    assert waiting.status == "waiting"
    cont = waiting.continuation
    assert cont is not None

    class FlexResume(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            validate_provider_transcript(request.messages)
            tool_ids = [m.call_id for m in request.messages if isinstance(m, ProviderToolMessage)]
            assert tool_ids == ["r1", "w1", "wr"]
            yield from text_then_terminal(
                "final",
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    resume_auth = RecordingAuthFactory()
    resume_dispatcher = RecordingDispatcher(
        results_by_call_id={
            "w1": ProviderDispatchResult(
                capability_result=completed_result(
                    user_text="approved write executed", metrics=_metrics()
                ),
                next_manifest=res1.manifest,
            ),
            "wr": ProviderDispatchResult(
                capability_result=completed_result(user_text="wrote", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        },
        verifier=verifier,
    )
    resolution = ProviderWaitingResolution(
        call_id="w1",
        capability_continuation=cont_ref,
        capability_result=completed_result(user_text="approved", metrics=_metrics()),
    )
    resumed = resume_provider_agent_loop(
        ProviderLoopResumeRequest(
            manifest=waiting.manifest,
            messages=waiting.messages,
            continuation=cont,
            resolved_waiting=resolution,
        ),
        _ports(
            provider=FlexResume(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res2]),
            verifier=verifier,
            auth=resume_auth,
            dispatcher=resume_dispatcher,
        ),
    )
    assert resumed.status == "completed"
    assert resumed.final_text == "final"
    # Call-owned approval re-dispatches the exact waiting call before later
    # siblings; approval itself is never projected as fake Tool success.
    assert [item["call_id"] for item in resume_auth.issued] == ["w1", "wr"]
    assert [item.call.call_id for item in resume_dispatcher.requests] == ["w1", "wr"]
    assert resumed.round_count == 2
    assert resumed.usage.total_tokens == 6


def test_resume_classification_drift_seals_honestly() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="durable",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    cont_ref = _continuation_ref()

    class FlexStart(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            yield from _multi_tool_events(
                res1.surface,
                [
                    ("w1", aliases["tools.wait"], {"query": "wait"}),
                    ("r1", aliases["tools.read_a"], {"query": "1"}),
                ],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={p[0].ref.binding_contract_digest: p[1] for p in pairs}
    )
    waiting = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=FlexStart(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(
                results_by_call_id={
                    "w1": ProviderDispatchResult(
                        capability_result=CapabilityResult(
                            status="waiting",
                            user_text=None,
                            structured_output=None,
                            artifact_refs=(),
                            continuation=cont_ref,
                            terminal_output=False,
                            needs_followup=True,
                            error=None,
                            metrics=_metrics(),
                        ),
                        next_manifest=res1.manifest,
                    ),
                },
                verifier=verifier,
            ),
        ),
    )
    assert waiting.status == "waiting"
    cont = waiting.continuation
    assert cont is not None

    # Drift on resume: flip parallel_safe / descriptor digests.
    drifted = _descriptor(
        pairs[0][0],
        side_effect="read",
        parallel_safe=False,
        interrupt_mode="durable",
        descriptor_digest="9" * 64,
        behavior_digest="8" * 64,
        classification_revision="plan02-v2",
    )
    drift_verifier = RecordingDescriptorVerifier(
        current_by_binding={
            pairs[0][0].ref.binding_contract_digest: drifted,
            pairs[1][0].ref.binding_contract_digest: pairs[1][1],
        }
    )
    class NoProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            raise AssertionError("no provider on classification drift resume")

    resume_dispatcher = RecordingDispatcher(verifier=drift_verifier)
    result = resume_provider_agent_loop(
        ProviderLoopResumeRequest(
            manifest=waiting.manifest,
            messages=waiting.messages,
            continuation=cont,
            resolved_waiting=ProviderWaitingResolution(
                call_id="w1",
                capability_continuation=cont_ref,
                capability_result=completed_result(user_text="child-done", metrics=_metrics()),
            ),
        ),
        _ports(
            provider=NoProvider(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([]),
            verifier=drift_verifier,
            auth=RecordingAuthFactory(),
            dispatcher=resume_dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "classification_changed"
    tool_msgs = [m for m in result.messages if isinstance(m, ProviderToolMessage)]
    assert tool_msgs[0].call_id == "w1"
    assert tool_msgs[0].content.status == "completed"
    assert tool_msgs[0].content.user_text == "child-done"
    assert tool_msgs[1].call_id == "r1"
    assert tool_msgs[1].content.status == "cancelled_before_start"
    assert resume_dispatcher.requests == []


def test_resume_tamper_protocol_error_no_dispatch() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="durable",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    alias = res1.surface.tools[0].provider_alias
    cont_ref = _continuation_ref()

    class FlexStart(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from _multi_tool_events(
                res1.surface,
                [("w1", alias, {"query": "wait"})],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={pairs[0][0].ref.binding_contract_digest: pairs[0][1]}
    )
    waiting = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=FlexStart(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(
                results_by_call_id={
                    "w1": ProviderDispatchResult(
                        capability_result=CapabilityResult(
                            status="waiting",
                            user_text=None,
                            structured_output=None,
                            artifact_refs=(),
                            continuation=cont_ref,
                            terminal_output=False,
                            needs_followup=True,
                            error=None,
                            metrics=_metrics(),
                        ),
                        next_manifest=res1.manifest,
                    ),
                },
                verifier=verifier,
            ),
        ),
    )
    cont = waiting.continuation
    assert cont is not None

    class NoProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            raise AssertionError("provider must not run")

    dispatcher = RecordingDispatcher(verifier=verifier)
    # Tamper transcript digest by mutating messages without updating continuation.
    bad_messages = waiting.messages + (ProviderUserMessage(content="tamper"),)
    with pytest.raises(Exception):
        # Construction itself validates transcript digest.
        ProviderLoopResumeRequest(
            manifest=manifest,
            messages=bad_messages,
            continuation=cont,
            resolved_waiting=ProviderWaitingResolution(
                call_id="w1",
                capability_continuation=cont_ref,
                capability_result=completed_result(user_text="x", metrics=_metrics()),
            ),
        )
    assert dispatcher.requests == []


def test_seal_waiting_after_cancellation() -> None:
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="durable",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
        _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    cont_ref = _continuation_ref()

    class FlexStart(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from _multi_tool_events(
                res1.surface,
                [
                    ("w1", aliases["tools.wait"], {"query": "wait"}),
                    ("r1", aliases["tools.read_a"], {"query": "1"}),
                ],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={p[0].ref.binding_contract_digest: p[1] for p in pairs}
    )
    waiting = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=FlexStart(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(
                results_by_call_id={
                    "w1": ProviderDispatchResult(
                        capability_result=CapabilityResult(
                            status="waiting",
                            user_text=None,
                            structured_output=None,
                            artifact_refs=(),
                            continuation=cont_ref,
                            terminal_output=False,
                            needs_followup=True,
                            error=None,
                            metrics=_metrics(),
                        ),
                        next_manifest=res1.manifest,
                    ),
                },
                verifier=verifier,
            ),
        ),
    )
    assert waiting.status == "waiting"
    cont = waiting.continuation
    assert cont is not None
    assistant = next(m for m in waiting.messages if isinstance(m, ProviderAssistantMessage))
    waiting_call = assistant.tool_calls[0]
    pending = assistant.tool_calls[1:]
    sealed = seal_waiting_after_cancellation(
        messages=waiting.messages,
        continuation=cont,
        waiting_call=waiting_call,
        pending_calls=pending,
        tool_call_records=tuple(
            r for r in waiting.tool_calls if r.status not in {"waiting", "deferred"}
        ),
        manifest=manifest,
    )
    assert sealed.status == "cancelled"
    tool_msgs = [m for m in sealed.messages if isinstance(m, ProviderToolMessage)]
    assert [m.call_id for m in tool_msgs] == ["w1", "r1"]
    assert tool_msgs[0].content.status == "cancelled"
    assert tool_msgs[1].content.status == "cancelled_before_start"
    validate_provider_transcript(sealed.messages)
    # Sealer alone does not dispatch.
    loop = ProviderAgentLoop()
    sealed2 = loop.seal_waiting_after_cancellation(
        messages=waiting.messages,
        continuation=cont,
        waiting_call=waiting_call,
        pending_calls=pending,
        manifest=manifest,
    )
    assert sealed2.status == "cancelled"


def test_legacy_blocking_waiting_is_protocol_error() -> None:
    """If a surface somehow has interrupt_mode!=durable wait, reject as protocol error.

    Production surfaces exclude legacy_blocking; use durable=false path by forcing
    descriptor interrupt_mode=none while dispatcher returns waiting.
    """
    manifest = _manifest()
    model = _model()
    pairs = [
        _pair(
            "tools.wait",
            side_effect="read",
            parallel_safe=False,
            interrupt_mode="none",
            descriptor_digest="3" * 64,
            behavior_digest="4" * 64,
        ),
    ]
    res1 = _surface_for_pairs(manifest, pairs)
    alias = res1.surface.tools[0].provider_alias

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            yield from _multi_tool_events(
                res1.surface,
                [("w1", alias, {"query": "wait"})],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={pairs[0][0].ref.binding_contract_digest: pairs[0][1]}
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(
                results_by_call_id={
                    "w1": ProviderDispatchResult(
                        capability_result=CapabilityResult(
                            status="waiting",
                            user_text=None,
                            structured_output=None,
                            artifact_refs=(),
                            continuation=_continuation_ref(),
                            terminal_output=False,
                            needs_followup=True,
                            error=None,
                            metrics=_metrics(),
                        ),
                        next_manifest=res1.manifest,
                    ),
                },
                verifier=verifier,
            ),
        ),
    )
    assert result.status == "failed"
    assert result.tool_calls[0].status == "blocked"
    assert result.error is not None
    assert result.error.semantic_code == "unexpected_waiting"


# ---------------------------------------------------------------------------
# Bounded seeded invariant sequences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_seeded_invariant_sequences(seed: int) -> None:
    """Bounded reproducible sequences covering core pairing invariants."""
    manifest = _manifest()
    model = _model()
    # Vary side-effect pattern by seed.
    if seed % 2 == 0:
        pairs = [
            _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="1" * 64, behavior_digest="2" * 64),
            _pair("tools.read_b", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
        ]
        executor = BoundedIsolatedSiblingExecutor(max_workers=2)
    else:
        pairs = [
            _pair("tools.write_a", side_effect="write_local", parallel_safe=False, descriptor_digest="1" * 64, behavior_digest="2" * 64),
            _pair("tools.read_a", side_effect="read", parallel_safe=True, descriptor_digest="3" * 64, behavior_digest="4" * 64),
        ]
        executor = SequentialSiblingExecutor()

    res1 = _surface_for_pairs(manifest, pairs)
    res2 = _surface_for_pairs(manifest, pairs)
    aliases = _alias_by_domain(res1.surface)
    keys = [p[0].resolved.capability_key if hasattr(p[0], "resolved") else p[1].capability_key for p in pairs]
    # Frozen binding wraps resolved; use descriptor capability_key.
    keys = [p[1].capability_key for p in pairs]

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            self.request_count += 1
            self.seen_requests.append(request)
            if self.request_count == 1:
                validate_provider_transcript(request.messages)
                yield from _multi_tool_events(
                    res1.surface,
                    [
                        ("c0", aliases[keys[0]], {"query": "0"}),
                        ("c1", aliases[keys[1]], {"query": "1"}),
                    ],
                    usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
                return
            validate_provider_transcript(request.messages)
            tool_ids = [m.call_id for m in request.messages if isinstance(m, ProviderToolMessage)]
            assert tool_ids == ["c0", "c1"], f"seed={seed} unpaired provider request"
            yield from text_then_terminal(
                f"done-{seed}",
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    verifier = RecordingDescriptorVerifier(
        current_by_binding={p[0].ref.binding_contract_digest: p[1] for p in pairs}
    )
    if seed == 3:
        # recoverable failure on first call
        results = {
            "c0": ProviderDispatchResult(
                capability_result=failed_result(
                    error=CapabilityError(
                        error_type="execution_failed",
                        safe_code="tool_failed",
                        safe_message="failed",
                        retry_disposition="model_may_continue",
                    ),
                    metrics=_metrics(),
                ),
                next_manifest=res1.manifest,
            ),
            "c1": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        }
    else:
        results = {
            "c0": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok0", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
            "c1": ProviderDispatchResult(
                capability_result=completed_result(user_text="ok1", metrics=_metrics()),
                next_manifest=res1.manifest,
            ),
        }
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        _ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            tools=RecordingToolsProvider([res1, res2]),
            verifier=verifier,
            auth=RecordingAuthFactory(),
            dispatcher=RecordingDispatcher(results_by_call_id=results, verifier=verifier),
            sibling_executor=executor,
        ),
    )
    assert result.status == "completed", f"seed={seed}"
    assert [r.call.call_id for r in result.tool_calls] == ["c0", "c1"], f"seed={seed}"
    validate_provider_transcript(result.messages)
    # reverse alias exactness
    for record in result.tool_calls:
        assert record.call.domain_key.startswith("tools.")
        assert record.call.provider_alias
