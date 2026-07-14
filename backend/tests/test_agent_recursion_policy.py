"""Plan 05 Task 7: capability/agent call frames, depth/cycle guards, shared accounting."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
OWNER_VERSION = UUID("00000000-0000-4000-8000-000000000001")
AGENT_VERSION_A = UUID("00000000-0000-4000-8000-0000000000aa")
AGENT_VERSION_B = UUID("00000000-0000-4000-8000-0000000000bb")
RUN_ID = UUID("00000000-0000-4000-8000-0000000000f1")


# ---------------------------------------------------------------------------
# Pure frame / digest / depth / cycle unit tests
# ---------------------------------------------------------------------------


def test_frame_digest_is_exact_and_stable() -> None:
    from app.assistant.policy.recursion import (
        build_capability_call_frame,
        compute_frame_digest,
    )

    kwargs = dict(
        call_id="call-1",
        capability_type="tool",
        domain_key="skill.search",
        target_identity="system-tool:search",
        target_version_id=None,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION,
        capability_depth=1,
        agent_depth=1,
    )
    digest = compute_frame_digest(**kwargs)
    frame = build_capability_call_frame(**kwargs)
    assert frame.frame_digest == digest
    assert compute_frame_digest(**kwargs) == digest
    # body mutation changes digest
    other = compute_frame_digest(**{**kwargs, "call_id": "call-2"})
    assert other != digest


def test_frame_rejects_mismatched_digest() -> None:
    from app.assistant.policy.recursion import CapabilityCallFrame

    with pytest.raises(ValueError, match="frame_digest"):
        CapabilityCallFrame(
            call_id="c1",
            capability_type="tool",
            domain_key="k",
            target_identity="t",
            target_version_id=None,
            binding_contract_digest=DIGEST_B,
            owner_kind="main_agent",
            owner_version_id=OWNER_VERSION,
            capability_depth=1,
            agent_depth=1,
            frame_digest=DIGEST_A,
        )


def test_compute_next_depths_capability_and_agent() -> None:
    from app.assistant.policy.recursion import (
        build_capability_call_frame,
        compute_next_depths,
    )

    empty: tuple = ()
    assert compute_next_depths(empty, capability_type="tool") == (1, 1)
    assert compute_next_depths(empty, capability_type="agent") == (1, 1)

    tool = build_capability_call_frame(
        call_id="t1",
        capability_type="tool",
        domain_key="tool.a",
        target_identity="tool:a",
        target_version_id=None,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION,
        capability_depth=1,
        agent_depth=1,
    )
    assert compute_next_depths((tool,), capability_type="tool") == (2, 1)
    assert compute_next_depths((tool,), capability_type="agent") == (2, 1)

    agent = build_capability_call_frame(
        call_id="a1",
        capability_type="agent",
        domain_key="agent.a",
        target_identity="agent:a",
        target_version_id=AGENT_VERSION_A,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION,
        capability_depth=2,
        agent_depth=1,
    )
    stack = (tool, agent)
    # next tool under one agent frame: agent_depth stays at enclosing agent count
    assert compute_next_depths(stack, capability_type="tool") == (3, 1)
    # next agent under one agent frame: agent_depth = 2
    assert compute_next_depths(stack, capability_type="agent") == (3, 2)


def test_evaluate_recursion_guard_depth_cycle_and_main_agent_restart() -> None:
    from app.assistant.policy.recursion import (
        REASON_AGENT_CYCLE,
        REASON_AGENT_DEPTH,
        REASON_CAPABILITY_DEPTH,
        REASON_MAIN_AGENT_RESTART,
        build_capability_call_frame,
        evaluate_recursion_guard,
    )

    # empty stack always admits depth 1
    assert (
        evaluate_recursion_guard(
            (),
            capability_type="tool",
            target_identity="tool:x",
            target_version_id=None,
            max_capability_depth=4,
            max_agent_depth=2,
        )
        is None
    )

    frames = []
    for i in range(4):
        frames.append(
            build_capability_call_frame(
                call_id=f"c{i}",
                capability_type="tool",
                domain_key=f"k{i}",
                target_identity=f"tool:{i}",
                target_version_id=None,
                binding_contract_digest=DIGEST_B,
                owner_kind="main_agent",
                owner_version_id=OWNER_VERSION,
                capability_depth=i + 1,
                agent_depth=1,
            )
        )
    assert (
        evaluate_recursion_guard(
            tuple(frames),
            capability_type="tool",
            target_identity="tool:x",
            target_version_id=None,
            max_capability_depth=4,
            max_agent_depth=2,
        )
        == REASON_CAPABILITY_DEPTH
    )

    agent_frame = build_capability_call_frame(
        call_id="a1",
        capability_type="agent",
        domain_key="agent.a",
        target_identity="agent:a",
        target_version_id=AGENT_VERSION_A,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION,
        capability_depth=1,
        agent_depth=1,
    )
    # second agent with max_agent_depth=1 denied
    assert (
        evaluate_recursion_guard(
            (agent_frame,),
            capability_type="agent",
            target_identity="agent:b",
            target_version_id=AGENT_VERSION_B,
            max_capability_depth=4,
            max_agent_depth=1,
        )
        == REASON_AGENT_DEPTH
    )
    # exact agent version cycle
    assert (
        evaluate_recursion_guard(
            (agent_frame,),
            capability_type="agent",
            target_identity="agent:a",
            target_version_id=AGENT_VERSION_A,
            max_capability_depth=4,
            max_agent_depth=2,
        )
        == REASON_AGENT_CYCLE
    )
    # different version allowed at agent_depth 2
    assert (
        evaluate_recursion_guard(
            (agent_frame,),
            capability_type="agent",
            target_identity="agent:b",
            target_version_id=AGENT_VERSION_B,
            max_capability_depth=4,
            max_agent_depth=2,
        )
        is None
    )
    # main agent restart from agent frame
    assert (
        evaluate_recursion_guard(
            (agent_frame,),
            capability_type="tool",
            target_identity="main-agent-control:restart",
            target_version_id=None,
            domain_key="main_agent.restart",
            max_capability_depth=4,
            max_agent_depth=2,
        )
        == REASON_MAIN_AGENT_RESTART
    )


def test_process_local_frame_port_push_pop_and_exception_safe() -> None:
    from app.assistant.policy.recursion import (
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
    )

    port = ProcessLocalCapabilityCallFramePort()
    assert port.current() == ()
    frame = build_capability_call_frame(
        call_id="c1",
        capability_type="tool",
        domain_key="k",
        target_identity="t",
        target_version_id=None,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION,
        capability_depth=1,
        agent_depth=1,
    )
    with port.push(frame):
        assert len(port.current()) == 1
        assert port.current()[0].call_id == "c1"
        try:
            with port.push(
                build_capability_call_frame(
                    call_id="c2",
                    capability_type="agent",
                    domain_key="a",
                    target_identity="agent:x",
                    target_version_id=AGENT_VERSION_A,
                    binding_contract_digest=DIGEST_B,
                    owner_kind="skill_version",
                    owner_version_id=OWNER_VERSION,
                    capability_depth=2,
                    agent_depth=1,
                )
            ):
                assert len(port.current()) == 2
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # inner popped on exception
        assert len(port.current()) == 1
        assert port.current()[0].call_id == "c1"
    assert port.current() == ()


def test_noop_frame_port_default_on_runtime_ports() -> None:
    from app.assistant.capabilities.ports import (
        CapabilityRuntimePorts,
        NoOpCapabilityCallFramePort,
        NoOpCapabilityDispatchGuard,
    )

    cancel = SimpleNamespace(is_cancelled=lambda: False, raise_if_cancelled=lambda: None)
    events = SimpleNamespace(emit=lambda e: None)
    ports = CapabilityRuntimePorts(cancellation=cancel, events=events)  # type: ignore[arg-type]
    assert isinstance(ports.call_frames, NoOpCapabilityCallFramePort)
    assert isinstance(ports.dispatch_guard, NoOpCapabilityDispatchGuard)
    assert ports.call_frames.current() == ()
    with ports.call_frames.push(object()):
        assert ports.call_frames.current() == ()


# ---------------------------------------------------------------------------
# Gateway helpers (mirrors test_capability_gateway patterns)
# ---------------------------------------------------------------------------


@dataclass
class _FakeCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")


@dataclass
class _RecordingEventSink:
    events: list[Any] = field(default_factory=list)

    def emit(self, event: Any) -> None:
        self.events.append(event)


def _timeout_policy(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityTimeoutPolicy

    payload = {
        "mode": "cooperative",
        "timeout_seconds": None,
        "cancellation_supported": True,
    }
    payload.update(overrides)
    return CapabilityTimeoutPolicy(**payload)


def _behavior(**overrides: Any):
    from app.assistant.capabilities.contracts import (
        CapabilityBehavior,
        ClassificationContractRef,
    )

    payload = {
        "classification": ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        "side_effect": "read",
        "parallel_safe": True,
        "interrupt_mode": "none",
        "timeout_policy": _timeout_policy(),
        "behavior_digest": DIGEST_B,
    }
    payload.update(overrides)
    return CapabilityBehavior(**payload)


def _availability(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityAvailability

    payload = {
        "status": "available",
        "reason_code": None,
        "compatibility_only": False,
    }
    payload.update(overrides)
    return CapabilityAvailability(**payload)


def _completion(**overrides: Any):
    from app.assistant.domain.contracts import CapabilityCompletionContract

    payload = {
        "terminal_output": True,
        "needs_followup": False,
        "followup_hint": None,
    }
    payload.update(overrides)
    return CapabilityCompletionContract(**payload)


def _schemas():
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    in_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    out_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    return (
        in_schema,
        out_schema,
        binding_schema_digest(in_schema),
        binding_schema_digest(out_schema),
    )


def _descriptor(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityDescriptor

    in_schema, out_schema, in_digest, out_digest = _schemas()
    payload = {
        "capability_key": "search.query",
        "capability_type": "tool",
        "target_identity": "system-tool:search_entries",
        "target_id": None,
        "target_version_id": None,
        "target_revision": None,
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "display_name": "Search",
        "description": "search",
        "input_schema": in_schema,
        "output_schema": out_schema,
        "input_schema_digest": in_digest,
        "output_schema_digest": out_digest,
        "descriptor_digest": DIGEST_D,
        "executable_revision": "build-1",
        "behavior": _behavior(),
        "availability": _availability(),
        "completion": _completion(),
    }
    payload.update(overrides)
    return CapabilityDescriptor(**payload)


def _owner(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityOwnerRef

    payload = {
        "owner_kind": "main_agent",
        "owner_id": "owner-1",
        "owner_version_id": OWNER_VERSION,
    }
    payload.update(overrides)
    return CapabilityOwnerRef(**payload)


def _principal(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityPrincipal

    payload = {
        "principal_type": "test",
        "principal_id": "principal-1",
        "authenticated": True,
    }
    payload.update(overrides)
    return CapabilityPrincipal(**payload)


def _evidence(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityAuthorizationEvidence

    payload = {
        "issuer": "test",
        "call_id": "call-1",
        "principal": _principal(),
        "entrypoint": "test",
        "owner": _owner(),
        "capability_key": "search.query",
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "allowed_side_effects": ("none", "compute", "read"),
        "grant_source_digest": DIGEST_E,
        "evidence_digest": DIGEST_F,
    }
    payload.update(overrides)
    return CapabilityAuthorizationEvidence(**payload)


def _context(call_id: str = "call-1", **overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext

    payload = {
        "call_id": call_id,
        "run_id": RUN_ID,
        "nesting_depth": 0,
        "request_tool": "search.query",
    }
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _binding(
    *,
    capability_type: str = "tool",
    capability_key: str = "search.query",
    target_identity: str = "system-tool:search_entries",
    target_version_id: UUID | None = None,
):
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.domain.contracts import (
        CapabilityCompletionContract,
        ResolvedCapabilityBinding,
    )
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant.skills.resolution import build_binding_snapshot

    in_schema, out_schema, in_digest, out_digest = _schemas()
    completion = CapabilityCompletionContract(
        terminal_output=True, needs_followup=False, followup_hint=None
    )
    target_id = uuid4()
    config_digest = DIGEST_B
    executable_revision = "build-1"
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": capability_type,
            "targetIdentity": target_identity,
            "targetId": str(target_id),
            "targetVersionId": str(target_version_id) if target_version_id else None,
            "targetRevision": None,
            "inputSchemaDigest": in_digest,
            "outputSchemaDigest": out_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type=capability_type,  # type: ignore[arg-type]
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=target_version_id,
        target_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=target_version_id,
        resolved_tool_id=target_id if capability_type == "tool" else None,
        resolved_workflow_version_id=(
            target_version_id if capability_type == "workflow" else None
        ),
        resolved_agent_version_id=target_version_id if capability_type == "agent" else None,
        resolved_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        input_schema_digest=in_digest,
        output_schema_digest=out_digest,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=OWNER_VERSION,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _request(
    *,
    input_data: dict | None = None,
    evidence=None,
    context=None,
    binding=None,
    call_id: str = "call-1",
):
    from app.assistant.capabilities.contracts import CapabilityExecutionRequest

    b = binding or _binding()
    auth = evidence or _evidence(
        call_id=call_id,
        capability_key=b.resolved.capability_key,
        resolution_digest=b.resolved.resolution_digest,
        binding_contract_digest=b.resolved.binding_contract_digest,
        dependency_closure_digest=b.resolved.dependency_closure_digest,
    )
    return CapabilityExecutionRequest(
        binding=b,
        input=input_data if input_data is not None else {"query": "hello"},
        context=context or _context(call_id=call_id),
        authorization=auth,
    )


class _SpyRegistry:
    def __init__(self, target: Any, *, fail: BaseException | None = None) -> None:
        self.target = target
        self.fail = fail
        self.calls: list[str] = []

    def resolve(self, binding):
        self.calls.append("resolve")
        if self.fail is not None:
            raise self.fail
        return self.target

    def describe(self, binding):
        return self.target.descriptor


class _SpyPolicy:
    def __init__(self, decision: Any) -> None:
        self.decision = decision
        self.calls: list[str] = []

    def authorize(self, **kwargs):
        self.calls.append("authorize")
        return self.decision


class _SpyAdapter:
    def __init__(
        self,
        *,
        capability_type: str = "tool",
        structured_output: Any | None = None,
        on_execute=None,
        fail: BaseException | None = None,
    ) -> None:
        self.capability_type = capability_type
        self.structured_output = structured_output if structured_output is not None else {"text": "ok"}
        self.on_execute = on_execute
        self.fail = fail
        self.calls: list[Any] = []
        self.ports_seen: list[Any] = []

    def execute(self, request, *, ports):
        self.calls.append(request)
        self.ports_seen.append(ports)
        if self.on_execute is not None:
            self.on_execute(request, ports)
        if self.fail is not None:
            raise self.fail
        from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result

        return completed_result(
            structured_output=self.structured_output,
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
            terminal_output=True,
        )


def _target_from_binding(binding, *, capability_type: str = "tool", descriptor=None):
    from app.assistant.capabilities.ports import (
        ExecutableAgentVersionTarget,
        ExecutableToolTarget,
        ResolvedCapabilityTarget,
    )

    desc = descriptor or _descriptor(
        capability_key=binding.resolved.capability_key,
        capability_type=capability_type,
        target_identity=binding.resolved.target_identity,
        target_version_id=binding.resolved.target_version_id,
        resolution_digest=binding.resolved.resolution_digest,
        binding_contract_digest=binding.resolved.binding_contract_digest,
        dependency_closure_digest=binding.resolved.dependency_closure_digest,
        input_schema=binding.resolved.input_schema,
        output_schema=binding.resolved.output_schema,
        input_schema_digest=binding.resolved.input_schema_digest,
        output_schema_digest=binding.resolved.output_schema_digest,
    )
    if capability_type == "agent":
        executable: Any = ExecutableAgentVersionTarget(
            agent_profile_id=binding.resolved.target_id or uuid4(),
            version_id=binding.resolved.target_version_id or AGENT_VERSION_A,
            snapshot_digest=DIGEST_A,
            parsed_snapshot={},
        )
    else:
        executable = ExecutableToolTarget(
            target_identity=binding.resolved.target_identity,
            tool_id=binding.resolved.target_id,
            config_revision=None,
            config_digest=DIGEST_A,
            is_system=True,
            tool_object_or_record=object(),
        )
    closure = SimpleNamespace(
        binding_contract_digest=binding.resolved.binding_contract_digest,
        dependency_closure_digest=binding.resolved.dependency_closure_digest,
        bind_authorized=lambda **kw: SimpleNamespace(),
    )
    return ResolvedCapabilityTarget(
        descriptor=desc,
        binding=binding,
        executable=executable,
        execution_closure=closure,
    )


def _allow_decision(*, descriptor=None, permit=None, call_id: str = "call-1"):
    from app.assistant.capabilities.contracts import CapabilityPolicyDecision
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    desc = descriptor or _descriptor()
    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="allow",
        call_id=call_id,
        descriptor_digest=desc.descriptor_digest,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_F,
        owner=_owner(),
        granted_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_E,
        decision_digest=DIGEST_B,
        dispatch_permit=permit or AtomicSingleUseDispatchPermit(),
    )


def _adapters(**overrides: Any) -> dict[str, _SpyAdapter]:
    base = {
        "tool": _SpyAdapter(capability_type="tool"),
        "workflow": _SpyAdapter(capability_type="workflow"),
        "agent": _SpyAdapter(capability_type="agent"),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Gateway integration: depth deny, push/pop, nested shared ports
# ---------------------------------------------------------------------------


def test_gateway_denies_capability_depth_before_mark_started() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import (
        REASON_CAPABILITY_DEPTH,
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
    )

    binding = _binding()
    target = _target_from_binding(binding)
    started: list[str] = []

    class Guard:
        def mark_started(self, *, call_id: str, validated_arguments_digest: str) -> None:
            started.append(call_id)

        def finish(self, *, call_id: str, status: str) -> None:
            del call_id, status

        def release_unstarted(self, *, call_id: str, reason_code: str) -> None:
            del call_id, reason_code

    adapter = _SpyAdapter(capability_type="tool")
    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters=_adapters(tool=adapter),
    )
    frames = ProcessLocalCapabilityCallFramePort()
    # Pre-fill stack to max depth so next call is denied
    for i in range(4):
        with frames.push(
            build_capability_call_frame(
                call_id=f"pre-{i}",
                capability_type="tool",
                domain_key=f"pre{i}",
                target_identity=f"tool:pre{i}",
                target_version_id=None,
                binding_contract_digest=DIGEST_B,
                owner_kind="main_agent",
                owner_version_id=OWNER_VERSION,
                capability_depth=i + 1,
                agent_depth=1,
            )
        ):
            # Keep them on the stack by nesting — actually with exits pop.
            # Build stack without context manager:
            pass
    # Manually push 4 frames and leave them (use private stack via nested holds)
    cms = []
    for i in range(4):
        cm = frames.push(
            build_capability_call_frame(
                call_id=f"pre-{i}",
                capability_type="tool",
                domain_key=f"pre{i}",
                target_identity=f"tool:pre{i}",
                target_version_id=None,
                binding_contract_digest=DIGEST_B,
                owner_kind="main_agent",
                owner_version_id=OWNER_VERSION,
                capability_depth=i + 1,
                agent_depth=1,
            )
        )
        cm.__enter__()
        cms.append(cm)
    assert len(frames.current()) == 4

    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        dispatch_guard=Guard(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    # attach limits via SimpleNamespace wrapper is not needed; defaults max_cap=4
    result = gw.execute(_request(binding=binding), ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == REASON_CAPABILITY_DEPTH
    assert started == []  # mark_started never called
    assert adapter.calls == []
    for cm in reversed(cms):
        cm.__exit__(None, None, None)


def test_gateway_pushes_frame_during_adapter_and_pops_after() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import ProcessLocalCapabilityCallFramePort

    binding = _binding()
    target = _target_from_binding(binding)
    frames = ProcessLocalCapabilityCallFramePort()
    seen_depth: list[int] = []

    def on_execute(request, ports):
        stack = ports.call_frames.current()
        seen_depth.append(len(stack))
        assert stack[-1].call_id == request.context.call_id
        assert stack[-1].capability_type == "tool"

    adapter = _SpyAdapter(capability_type="tool", on_execute=on_execute)
    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters=_adapters(tool=adapter),
    )
    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    assert frames.current() == ()
    result = gw.execute(_request(binding=binding), ports=ports)
    assert result.status == "completed"
    assert seen_depth == [1]
    assert frames.current() == ()  # popped after success


def test_gateway_pops_frame_on_adapter_exception() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import ProcessLocalCapabilityCallFramePort

    binding = _binding()
    target = _target_from_binding(binding)
    frames = ProcessLocalCapabilityCallFramePort()
    adapter = _SpyAdapter(
        capability_type="tool",
        fail=RuntimeError("adapter-SECRET-xyz"),
    )
    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters=_adapters(tool=adapter),
    )
    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    result = gw.execute(_request(binding=binding), ports=ports)
    assert result.status == "failed"
    assert frames.current() == ()


def test_nested_gateway_shares_run_ports_and_sees_parent_frame() -> None:
    """Fake nested Gateway adapter proves shared accounting + frame stack."""
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import ProcessLocalCapabilityCallFramePort

    parent_binding = _binding(capability_key="parent.tool", target_identity="tool:parent")
    child_binding = _binding(capability_key="child.tool", target_identity="tool:child")
    parent_target = _target_from_binding(parent_binding)
    child_target = _target_from_binding(child_binding)

    frames = ProcessLocalCapabilityCallFramePort()
    cancel = _FakeCancellation()
    events = _RecordingEventSink()
    child_seen: dict[str, Any] = {}

    def child_on_execute(request, ports):
        child_seen["child_frames_during"] = len(ports.call_frames.current())
        child_seen["child_top_call_id"] = ports.call_frames.current()[-1].call_id
        child_seen["child_same_ports"] = ports is ports_holder[0]

    child_adapter = _SpyAdapter(capability_type="tool", on_execute=child_on_execute)
    child_gw = CapabilityGateway(
        registry=_SpyRegistry(child_target),
        policy=_SpyPolicy(
            _allow_decision(descriptor=child_target.descriptor, call_id="child-1")
        ),
        adapters=_adapters(tool=child_adapter),
    )

    def parent_on_execute(request, ports):
        # Nested call reuses exact same ports object (Run, cancel, events, frames).
        child_seen["same_ports"] = ports is ports_holder[0]
        child_seen["run_id"] = request.context.run_id
        child_seen["parent_frames"] = len(ports.call_frames.current())
        child_req = _request(
            binding=child_binding,
            call_id="child-1",
            context=_context(call_id="child-1", run_id=request.context.run_id),
            evidence=_evidence(
                call_id="child-1",
                capability_key=child_binding.resolved.capability_key,
                resolution_digest=child_binding.resolved.resolution_digest,
                binding_contract_digest=child_binding.resolved.binding_contract_digest,
                dependency_closure_digest=child_binding.resolved.dependency_closure_digest,
            ),
        )
        child_result = child_gw.execute(child_req, ports=ports)
        child_seen["child_status"] = child_result.status
        # After child returns, only parent frame remains
        child_seen["frames_after_child"] = len(ports.call_frames.current())

    parent_adapter = _SpyAdapter(capability_type="tool", on_execute=parent_on_execute)
    parent_gw = CapabilityGateway(
        registry=_SpyRegistry(parent_target),
        policy=_SpyPolicy(
            _allow_decision(descriptor=parent_target.descriptor, call_id="parent-1")
        ),
        adapters=_adapters(tool=parent_adapter),
    )
    ports_holder: list[Any] = []
    ports = CapabilityRuntimePorts(
        cancellation=cancel,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
        call_frames=frames,
    )
    ports_holder.append(ports)

    result = parent_gw.execute(
        _request(
            binding=parent_binding,
            call_id="parent-1",
            evidence=_evidence(
                call_id="parent-1",
                capability_key=parent_binding.resolved.capability_key,
                resolution_digest=parent_binding.resolved.resolution_digest,
                binding_contract_digest=parent_binding.resolved.binding_contract_digest,
                dependency_closure_digest=parent_binding.resolved.dependency_closure_digest,
            ),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert child_seen["same_ports"] is True
    assert child_seen["run_id"] == RUN_ID
    assert child_seen["parent_frames"] == 1  # parent frame visible to child path
    assert child_seen["child_status"] == "completed"
    assert child_seen["child_frames_during"] == 2  # parent + child
    assert child_seen["child_top_call_id"] == "child-1"
    assert child_seen["child_same_ports"] is True
    assert child_seen["frames_after_child"] == 1
    assert frames.current() == ()
    # Shared event sink received events from both
    types = [e.event_type for e in events.events]
    assert "capability.completed" in types


def test_sibling_frames_do_not_leak_across_parallel_calls() -> None:
    """Sibling calls share the parent stack but not each other's frames after return."""
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import ProcessLocalCapabilityCallFramePort

    frames = ProcessLocalCapabilityCallFramePort()
    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    depths: list[int] = []

    def on_execute(request, ports_arg):
        depths.append(len(ports_arg.call_frames.current()))

    results = []
    for i in range(2):
        binding = _binding(capability_key=f"sib.{i}", target_identity=f"tool:sib{i}")
        target = _target_from_binding(binding)
        adapter = _SpyAdapter(capability_type="tool", on_execute=on_execute)
        gw = CapabilityGateway(
            registry=_SpyRegistry(target),
            policy=_SpyPolicy(
                _allow_decision(descriptor=target.descriptor, call_id=f"sib-{i}")
            ),
            adapters=_adapters(tool=adapter),
        )
        results.append(
            gw.execute(
                _request(
                    binding=binding,
                    call_id=f"sib-{i}",
                    evidence=_evidence(
                        call_id=f"sib-{i}",
                        capability_key=binding.resolved.capability_key,
                        resolution_digest=binding.resolved.resolution_digest,
                        binding_contract_digest=binding.resolved.binding_contract_digest,
                        dependency_closure_digest=binding.resolved.dependency_closure_digest,
                    ),
                ),
                ports=ports,
            )
        )
    assert all(r.status == "completed" for r in results)
    # Each sibling sees only its own frame (depth 1), not the other sibling
    assert depths == [1, 1]
    assert frames.current() == ()


def test_agent_cycle_denied_on_gateway_before_adapter() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.policy.recursion import (
        REASON_AGENT_CYCLE,
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
    )

    binding = _binding(
        capability_type="agent",
        capability_key="agent.a",
        target_identity="agent:a",
        target_version_id=AGENT_VERSION_A,
    )
    target = _target_from_binding(binding, capability_type="agent")
    # Align descriptor digests with binding
    desc = _descriptor(
        capability_key=binding.resolved.capability_key,
        capability_type="agent",
        target_identity=binding.resolved.target_identity,
        target_version_id=AGENT_VERSION_A,
        resolution_digest=binding.resolved.resolution_digest,
        binding_contract_digest=binding.resolved.binding_contract_digest,
        dependency_closure_digest=binding.resolved.dependency_closure_digest,
        input_schema=binding.resolved.input_schema,
        output_schema=binding.resolved.output_schema,
        input_schema_digest=binding.resolved.input_schema_digest,
        output_schema_digest=binding.resolved.output_schema_digest,
    )
    target = _target_from_binding(binding, capability_type="agent", descriptor=desc)

    frames = ProcessLocalCapabilityCallFramePort()
    cm = frames.push(
        build_capability_call_frame(
            call_id="parent-agent",
            capability_type="agent",
            domain_key="agent.a",
            target_identity="agent:a",
            target_version_id=AGENT_VERSION_A,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            owner_kind="main_agent",
            owner_version_id=OWNER_VERSION,
            capability_depth=1,
            agent_depth=1,
        )
    )
    cm.__enter__()

    adapter = _SpyAdapter(capability_type="agent")
    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(
            _allow_decision(descriptor=target.descriptor, call_id="child-agent")
        ),
        adapters=_adapters(agent=adapter),
    )
    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    result = gw.execute(
        _request(
            binding=binding,
            call_id="child-agent",
            evidence=_evidence(
                call_id="child-agent",
                capability_key=binding.resolved.capability_key,
                resolution_digest=binding.resolved.resolution_digest,
                binding_contract_digest=binding.resolved.binding_contract_digest,
                dependency_closure_digest=binding.resolved.dependency_closure_digest,
            ),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == REASON_AGENT_CYCLE
    assert adapter.calls == []
    cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Reservation depths from frame port
# ---------------------------------------------------------------------------


def test_reservation_item_uses_real_frame_depths() -> None:
    from app.assistant.policy.recursion import (
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
    )
    from app.assistant.provider_loop.contracts import ProviderLoopPorts
    from app.assistant.provider_loop.loop import _build_reservation_item
    from app.assistant.provider_loop.scheduler import SequentialSiblingExecutor

    frames = ProcessLocalCapabilityCallFramePort()
    cm = frames.push(
        build_capability_call_frame(
            call_id="parent",
            capability_type="agent",
            domain_key="agent.a",
            target_identity="agent:a",
            target_version_id=AGENT_VERSION_A,
            binding_contract_digest=DIGEST_B,
            owner_kind="main_agent",
            owner_version_id=OWNER_VERSION,
            capability_depth=1,
            agent_depth=1,
        )
    )
    cm.__enter__()

    ports = ProviderLoopPorts(
        provider=SimpleNamespace(),  # type: ignore[arg-type]
        tools_provider=SimpleNamespace(),  # type: ignore[arg-type]
        current_descriptors=SimpleNamespace(),  # type: ignore[arg-type]
        authorization_evidence=SimpleNamespace(),  # type: ignore[arg-type]
        tool_dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),  # type: ignore[arg-type]
        events=SimpleNamespace(emit=lambda *a, **k: None),  # type: ignore[arg-type]
        call_frames=frames,
    )
    call = SimpleNamespace(
        call_id="child",
        domain_key="tool.x",
        arguments_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
    )
    descriptor = SimpleNamespace(
        capability_type="tool",
        behavior=SimpleNamespace(side_effect="read"),
    )
    item = _build_reservation_item(ports=ports, call=call, descriptor=descriptor)
    assert item.capability_depth == 2
    assert item.agent_depth == 1  # enclosing agent frames only

    agent_desc = SimpleNamespace(
        capability_type="agent",
        behavior=SimpleNamespace(side_effect="read"),
    )
    item2 = _build_reservation_item(ports=ports, call=call, descriptor=agent_desc)
    assert item2.capability_depth == 2
    assert item2.agent_depth == 2
    cm.__exit__(None, None, None)


def test_reservation_defaults_to_depth_one_without_frame_port() -> None:
    from app.assistant.provider_loop.contracts import ProviderLoopPorts
    from app.assistant.provider_loop.loop import _build_reservation_item
    from app.assistant.provider_loop.scheduler import SequentialSiblingExecutor

    ports = ProviderLoopPorts(
        provider=SimpleNamespace(),  # type: ignore[arg-type]
        tools_provider=SimpleNamespace(),  # type: ignore[arg-type]
        current_descriptors=SimpleNamespace(),  # type: ignore[arg-type]
        authorization_evidence=SimpleNamespace(),  # type: ignore[arg-type]
        tool_dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),  # type: ignore[arg-type]
        events=SimpleNamespace(emit=lambda *a, **k: None),  # type: ignore[arg-type]
    )
    call = SimpleNamespace(
        call_id="c1",
        domain_key="tool.x",
        arguments_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
    )
    descriptor = SimpleNamespace(
        capability_type="tool",
        behavior=SimpleNamespace(side_effect="read"),
    )
    item = _build_reservation_item(ports=ports, call=call, descriptor=descriptor)
    assert item.capability_depth == 1
    assert item.agent_depth == 1


# ---------------------------------------------------------------------------
# Child obligations cannot complete parent Run
# ---------------------------------------------------------------------------


def test_child_result_cannot_satisfy_parent_main_agent_terminal() -> None:
    from app.assistant.policy.obligations import ObligationLedger

    # create() already seeds the Main Agent terminal obligation.
    ledger = ObligationLedger.create(run_id=RUN_ID, create_main_agent_terminal=True)
    pending_before = ledger.pending_blocking()
    assert len(pending_before) >= 1
    parent_obl = next(
        o for o in pending_before if o.owner_kind == "main_agent" and o.obligation_type == "terminal_output"
    )
    assert parent_obl.status == "pending"

    # Child skill_version result evidence must not satisfy main_agent terminal
    ledger.apply_capability_result(
        call_id="child-call",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=DIGEST_A,
        owner_kind="skill_version",
        owner_id="skill-child",
        owner_version_id=OWNER_VERSION,
    )
    pending = ledger.pending_blocking()
    assert any(o.obligation_id == parent_obl.obligation_id for o in pending)

    state = ledger.snapshot()
    terminals = [o for o in state.obligations if o.obligation_id == parent_obl.obligation_id]
    assert len(terminals) == 1
    assert terminals[0].status == "pending"


def test_provider_loop_modules_still_do_not_import_policy() -> None:
    import app.assistant.provider_loop.contracts as c
    import app.assistant.provider_loop.loop as loop
    import app.assistant.provider_loop.scheduler as sched

    for mod in (c, loop, sched):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "app.assistant.policy" not in src


# ---------------------------------------------------------------------------
# Production fail-closed: nested agent bindings remain unavailable
# ---------------------------------------------------------------------------


def test_nested_agent_flag_still_unavailable_in_adapter() -> None:
    """Plan 02 fail-closed: snapshot nested_agent/restart stays unavailable."""
    from app.assistant.capabilities.adapters.agent import _snapshot_forbids_nesting

    assert _snapshot_forbids_nesting({"nested_agent": True}) is True
    assert _snapshot_forbids_nesting({"main_agent_restart": True}) is True
    assert _snapshot_forbids_nesting({"restart": 1}) is True
    assert _snapshot_forbids_nesting({}) is False
    assert _snapshot_forbids_nesting(None) is False


def test_depths_from_ports_helper() -> None:
    from app.assistant.policy.recursion import (
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
        depths_from_ports,
    )
    from app.assistant.capabilities.ports import CapabilityRuntimePorts

    frames = ProcessLocalCapabilityCallFramePort()
    ports = CapabilityRuntimePorts(
        cancellation=_FakeCancellation(),  # type: ignore[arg-type]
        events=_RecordingEventSink(),  # type: ignore[arg-type]
        call_frames=frames,
    )
    assert depths_from_ports(ports, capability_type="tool") == (1, 1)
    cm = frames.push(
        build_capability_call_frame(
            call_id="a",
            capability_type="agent",
            domain_key="a",
            target_identity="agent:a",
            target_version_id=AGENT_VERSION_A,
            binding_contract_digest=DIGEST_B,
            owner_kind="main_agent",
            owner_version_id=OWNER_VERSION,
            capability_depth=1,
            agent_depth=1,
        )
    )
    cm.__enter__()
    assert depths_from_ports(ports, capability_type="tool") == (2, 1)
    assert depths_from_ports(ports, capability_type="agent") == (2, 2)
    cm.__exit__(None, None, None)
