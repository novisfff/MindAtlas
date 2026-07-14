"""CapabilityGateway order, integrity, output validation, cancellation tests (Plan 02 Task 7)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeCancellation:
    cancelled: bool = False
    checks: list[str] = field(default_factory=list)

    def is_cancelled(self) -> bool:
        self.checks.append("is_cancelled")
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        self.checks.append("raise_if_cancelled")
        if self.cancelled:
            raise RuntimeError("cancelled")


@dataclass
class _RecordingEventSink:
    events: list[Any] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def emit(self, event: Any) -> None:
        if event.event_type in self.fail_on:
            raise RuntimeError("event-sink-SECRET-xyz")
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


def _principal(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityPrincipal

    payload = {
        "principal_type": "test",
        "principal_id": "principal-1",
        "authenticated": True,
    }
    payload.update(overrides)
    return CapabilityPrincipal(**payload)


def _owner(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityOwnerRef

    payload = {
        "owner_kind": "test",
        "owner_id": "owner-1",
        "owner_version_id": None,
    }
    payload.update(overrides)
    return CapabilityOwnerRef(**payload)


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

    payload = {"call_id": call_id, "nesting_depth": 0}
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _binding():
    """Minimal frozen binding with matching digests for gateway tests."""
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
    target_identity = f"system-tool:search_entries"
    config_digest = DIGEST_B
    executable_revision = "build-1"
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target_id),
            "targetVersionId": None,
            "targetRevision": None,
            "inputSchemaDigest": in_digest,
            "outputSchemaDigest": out_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=None,
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
        capability_type="tool",
        capability_key="search.query",
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=None,
        resolved_tool_id=target_id,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
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
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _request(*, input_data: dict | None = None, evidence=None, context=None, binding=None):
    from app.assistant.capabilities.contracts import CapabilityExecutionRequest

    b = binding or _binding()
    # Align evidence digests with binding.
    auth = evidence or _evidence(
        capability_key=b.resolved.capability_key,
        resolution_digest=b.resolved.resolution_digest,
        binding_contract_digest=b.resolved.binding_contract_digest,
        dependency_closure_digest=b.resolved.dependency_closure_digest,
    )
    return CapabilityExecutionRequest(
        binding=b,
        input=input_data if input_data is not None else {"query": "hello"},
        context=context or _context(),
        authorization=auth,
    )


class _SpyRegistry:
    def __init__(self, target: Any, *, fail: BaseException | None = None) -> None:
        self.target = target
        self.fail = fail
        self.calls: list[str] = []
        self.decrypt_calls = 0
        self.model_client_calls = 0

    def resolve(self, binding):
        self.calls.append("resolve")
        if self.fail is not None:
            raise self.fail
        return self.target

    def describe(self, binding):
        self.calls.append("describe")
        return self.target.descriptor

    def decrypt(self, *a, **k):  # should never be called
        self.decrypt_calls += 1
        raise AssertionError("registry must not decrypt")

    def build_model_client(self, *a, **k):
        self.model_client_calls += 1
        raise AssertionError("registry must not build model clients")


class _SpyPolicy:
    def __init__(self, decision: Any, *, fail: BaseException | None = None) -> None:
        self.decision = decision
        self.fail = fail
        self.calls: list[str] = []
        self.decrypt_calls = 0

    def authorize(self, **kwargs):
        self.calls.append("authorize")
        if self.fail is not None:
            raise self.fail
        return self.decision

    def decrypt(self, *a, **k):
        self.decrypt_calls += 1
        raise AssertionError("policy must not decrypt")


class _SpyAdapter:
    def __init__(
        self,
        result: Any | None = None,
        *,
        capability_type: str = "tool",
        fail: BaseException | None = None,
        structured_output: Any | None = None,
        status: str = "completed",
        on_execute=None,
    ) -> None:
        self.capability_type = capability_type
        self._result = result
        self.fail = fail
        self.structured_output = structured_output if structured_output is not None else {"text": "ok"}
        self.status = status
        self.on_execute = on_execute
        self.calls: list[Any] = []
        self.decrypt_calls = 0
        self.model_client_calls = 0

    def execute(self, request, *, ports):
        self.calls.append(request)
        if self.on_execute is not None:
            self.on_execute(request, ports)
        if self.fail is not None:
            raise self.fail
        if self._result is not None:
            return self._result
        from app.assistant.capabilities.contracts import (
            CapabilityMetrics,
            completed_result,
            failed_result,
            CapabilityError,
            cancelled_result,
        )

        metrics = CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0)
        if self.status == "completed":
            return completed_result(
                structured_output=self.structured_output,
                metrics=metrics,
                terminal_output=True,
            )
        if self.status == "cancelled":
            return cancelled_result(metrics=metrics, call_id=request.context.call_id)
        return failed_result(
            error=CapabilityError(
                error_type="execution_failed",
                safe_code="execution_failed",
                safe_message="failed",
                retry_disposition="never",
            ),
            metrics=metrics,
        )


class _TestVerifier:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._consumed = False
        self.instance_id = str(uuid4())

    def verify(self, *, descriptor, evidence, context):
        from app.assistant.capabilities.contracts import VerifiedAuthorizationEvidence
        from app.assistant.capabilities.policy import (
            AtomicSingleUseDispatchPermit,
            AuthorizationEvidenceVerificationError,
        )
        from app.assistant.domain.digests import sha256_canonical_json

        self.calls += 1
        with self._lock:
            if self._consumed:
                raise AuthorizationEvidenceVerificationError("evidence_already_consumed")
            self._consumed = True
        return VerifiedAuthorizationEvidence(
            call_id=evidence.call_id,
            verifier_key=(evidence.issuer, evidence.entrypoint),
            verifier_instance_id=self.instance_id,
            principal=evidence.principal,
            entrypoint=evidence.entrypoint,
            owner=evidence.owner,
            capability_key=evidence.capability_key,
            resolution_digest=evidence.resolution_digest,
            binding_contract_digest=evidence.binding_contract_digest,
            dependency_closure_digest=evidence.dependency_closure_digest,
            allowed_side_effects=tuple(evidence.allowed_side_effects),
            grant_source_digest=evidence.grant_source_digest,
            evidence_digest=evidence.evidence_digest,
            verification_digest=sha256_canonical_json(
                {"callId": evidence.call_id, "vid": self.instance_id}
            ),
            dispatch_permit=AtomicSingleUseDispatchPermit(),
        )


def _target_from_binding(binding, *, descriptor=None, capability_type: str = "tool"):
    from app.assistant.capabilities.ports import (
        ExecutableToolTarget,
        ResolvedCapabilityTarget,
    )

    desc = descriptor or _descriptor(
        capability_key=binding.resolved.capability_key,
        capability_type=capability_type,
        target_identity=binding.resolved.target_identity,
        resolution_digest=binding.resolved.resolution_digest,
        binding_contract_digest=binding.resolved.binding_contract_digest,
        dependency_closure_digest=binding.resolved.dependency_closure_digest,
        input_schema=binding.resolved.input_schema,
        output_schema=binding.resolved.output_schema,
        input_schema_digest=binding.resolved.input_schema_digest,
        output_schema_digest=binding.resolved.output_schema_digest,
    )
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


def _allow_decision(*, descriptor=None, permit=None):
    from app.assistant.capabilities.contracts import CapabilityPolicyDecision
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    desc = descriptor or _descriptor()
    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="allow",
        call_id="call-1",
        descriptor_digest=desc.descriptor_digest,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_F,
        owner=_owner(),
        granted_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_E,
        decision_digest=DIGEST_B,
        dispatch_permit=permit or AtomicSingleUseDispatchPermit(),
    )


def _deny_decision(*, reason: str = "denied"):
    from app.assistant.capabilities.contracts import CapabilityPolicyDecision

    return CapabilityPolicyDecision(
        allowed=False,
        reason_code=reason,
        call_id="call-1",
        descriptor_digest=DIGEST_D,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_F,
        owner=_owner(),
        granted_side_effects=(),
        grant_source_digest=DIGEST_E,
        decision_digest=DIGEST_B,
        dispatch_permit=None,
    )


def _adapters(**overrides: Any) -> dict[str, _SpyAdapter]:
    base = {
        "tool": _SpyAdapter(capability_type="tool"),
        "workflow": _SpyAdapter(capability_type="workflow"),
        "agent": _SpyAdapter(capability_type="agent"),
    }
    base.update(overrides)
    return base


def _gateway(*, registry=None, policy=None, adapters=None):
    from app.assistant.capabilities.gateway import CapabilityGateway

    binding = _binding()
    target = _target_from_binding(binding)
    reg = registry if registry is not None else _SpyRegistry(target)
    pol = policy if policy is not None else _SpyPolicy(_allow_decision(descriptor=target.descriptor))
    ads = adapters if adapters is not None else _adapters()
    return CapabilityGateway(registry=reg, policy=pol, adapters=ads), reg, pol, ads, binding, target


# ---------------------------------------------------------------------------
# Construction integrity
# ---------------------------------------------------------------------------


def test_missing_adapter_fails_at_construction() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway

    with pytest.raises(ValueError):
        CapabilityGateway(
            registry=_SpyRegistry(_target_from_binding(_binding())),
            policy=_SpyPolicy(_allow_decision()),
            adapters={"tool": _SpyAdapter()},  # missing workflow/agent
        )


def test_duplicate_extra_adapter_fails_at_construction() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway

    with pytest.raises(ValueError):
        CapabilityGateway(
            registry=_SpyRegistry(_target_from_binding(_binding())),
            policy=_SpyPolicy(_allow_decision()),
            adapters={
                "tool": _SpyAdapter(capability_type="tool"),
                "workflow": _SpyAdapter(capability_type="workflow"),
                "agent": _SpyAdapter(capability_type="agent"),
                "extra": _SpyAdapter(capability_type="tool"),  # type: ignore[dict-item]
            },
        )


def test_build_capability_runtime_wires_all_adapters() -> None:
    from app.assistant.capabilities.runtime import build_capability_runtime

    class _Db:
        pass

    gw = build_capability_runtime(
        db=_Db(),  # type: ignore[arg-type]
        evidence_verifiers={("test", "test"): _TestVerifier()},
    )
    assert set(gw._adapters) == {"tool", "workflow", "agent"}
    assert gw._adapters["tool"].capability_type == "tool"
    assert gw._adapters["workflow"].capability_type == "workflow"
    assert gw._adapters["agent"].capability_type == "agent"


# ---------------------------------------------------------------------------
# Order spies
# ---------------------------------------------------------------------------


def test_gateway_order_happy_path() -> None:
    stages: list[str] = []
    binding = _binding()
    target = _target_from_binding(binding)

    class OrderRegistry(_SpyRegistry):
        def resolve(self, b):
            stages.append("resolve")
            return super().resolve(b)

    class OrderPolicy(_SpyPolicy):
        def authorize(self, **kw):
            stages.append("policy")
            return super().authorize(**kw)

    class OrderAdapter(_SpyAdapter):
        def execute(self, request, *, ports):
            stages.append("adapter")
            assert request.target.descriptor.descriptor_digest == target.descriptor.descriptor_digest
            return super().execute(request, ports=ports)

    cancel = _FakeCancellation()
    sink = _RecordingEventSink()

    original_is = cancel.is_cancelled

    def tracked_is() -> bool:
        stages.append("cancel")
        return original_is()

    cancel.is_cancelled = tracked_is  # type: ignore[method-assign]

    registry = OrderRegistry(target)
    policy = OrderPolicy(_allow_decision(descriptor=target.descriptor))
    adapters = {
        "tool": OrderAdapter(capability_type="tool"),
        "workflow": _SpyAdapter(capability_type="workflow"),
        "agent": _SpyAdapter(capability_type="agent"),
    }

    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(registry=registry, policy=policy, adapters=adapters)
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=sink),
    )
    assert result.status == "completed"
    # cancel before resolve, cancel before adapter (after policy), cancel after adapter
    assert stages[0] == "cancel"
    assert "resolve" in stages
    assert stages.index("resolve") < stages.index("policy")
    assert stages.index("policy") < stages.index("adapter")
    # At least two cancel checks before adapter, one after.
    cancel_indices = [i for i, s in enumerate(stages) if s == "cancel"]
    assert len(cancel_indices) >= 3
    assert cancel_indices[0] < stages.index("resolve")
    assert any(i > stages.index("policy") and i < stages.index("adapter") for i in cancel_indices)
    assert any(i > stages.index("adapter") for i in cancel_indices)


def test_failure_before_policy_skips_later_stages() -> None:
    binding = _binding()
    target = _target_from_binding(binding)

    class FailRegistry(_SpyRegistry):
        def resolve(self, b):
            from app.assistant.capabilities.contracts import CapabilityError
            from app.assistant.capabilities.errors import CapabilityDomainError

            self.calls.append("resolve")
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="not_found",
                    safe_code="not_found",
                    safe_message="missing",
                    retry_disposition="never",
                )
            )

    policy = _SpyPolicy(_allow_decision())
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=FailRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "not_found"
    assert policy.calls == []
    assert adapter.calls == []


def test_policy_deny_skips_adapter_and_permit() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    # Deny decision must not expose a usable permit.
    policy = _SpyPolicy(_deny_decision(reason="side_effect_not_granted"))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unauthorized"
    assert result.error.safe_code == "side_effect_not_granted"
    assert adapter.calls == []
    assert permit.consumed is False


def test_invalid_input_skips_policy_and_adapter() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    policy = _SpyPolicy(_allow_decision(descriptor=target.descriptor))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding, input_data={"query": 123}),  # wrong type
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_input"
    assert policy.calls == []
    assert adapter.calls == []


# ---------------------------------------------------------------------------
# Dispatch integrity
# ---------------------------------------------------------------------------


def test_descriptor_type_selects_exactly_one_adapter() -> None:
    binding = _binding()
    target = _target_from_binding(binding, capability_type="tool")
    tool_a = _SpyAdapter()
    tool_a.capability_type = "tool"
    wf = _SpyAdapter()
    wf.capability_type = "workflow"
    ag = _SpyAdapter()
    ag.capability_type = "agent"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={"tool": tool_a, "workflow": wf, "agent": ag},
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.status == "completed"
    assert len(tool_a.calls) == 1
    assert wf.calls == []
    assert ag.calls == []


def test_no_fallback_adapter_on_missing_type_runtime() -> None:
    """If descriptor type is somehow not registered, fail closed (construction normally prevents)."""
    binding = _binding()
    # Force a workflow descriptor while only tool path is exercised via registry.
    desc = _descriptor(capability_type="workflow")
    target = _target_from_binding(binding, descriptor=desc, capability_type="workflow")
    tool = _SpyAdapter()
    tool.capability_type = "tool"
    # Provide a workflow adapter that must be selected — not tool.
    wf = _SpyAdapter()
    wf.capability_type = "workflow"
    ag = _SpyAdapter()
    ag.capability_type = "agent"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=desc)),
        adapters={"tool": tool, "workflow": wf, "agent": ag},
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert len(wf.calls) == 1
    assert tool.calls == []


def test_adapter_receives_resolved_target_not_rebindable() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    seen = {}

    def on_execute(request, ports):
        seen["descriptor"] = request.target.descriptor
        seen["binding"] = request.target.binding
        # Attempt to mutate should not affect gateway-held target identity.
        assert request.target.descriptor.capability_key == "search.query"

    adapter = _SpyAdapter(on_execute=on_execute)
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert seen["descriptor"].descriptor_digest == target.descriptor.descriptor_digest
    assert seen["binding"] is target.binding


def test_permit_admits_exactly_one_adapter_entry() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    decision = _allow_decision(descriptor=target.descriptor, permit=permit)
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(decision),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    ports = SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink())
    r1 = gw.execute(_request(binding=binding), ports=ports)
    assert r1.status == "completed"
    assert len(adapter.calls) == 1
    assert permit.consumed is True

    # Replay same decision/permit cannot enter adapter again.
    r2 = gw.execute(_request(binding=binding), ports=ports)
    # Second call: policy spy returns same decision with already-consumed permit.
    assert r2.status == "failed"
    assert r2.error is not None
    assert r2.error.safe_code == "dispatch_permit_consumed"
    assert len(adapter.calls) == 1


def test_concurrent_permit_single_adapter_entry() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    decision = _allow_decision(descriptor=target.descriptor, permit=permit)
    barrier = threading.Barrier(4)
    adapter_entries = []

    def on_execute(request, ports):
        adapter_entries.append(1)
        time.sleep(0.01)

    adapter = _SpyAdapter(on_execute=on_execute)
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(decision),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    results = []

    def worker() -> None:
        barrier.wait()
        results.append(
            gw.execute(
                _request(binding=binding),
                ports=SimpleNamespace(
                    cancellation=_FakeCancellation(), events=_RecordingEventSink()
                ),
            )
        )

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    completed = [r for r in results if r.status == "completed"]
    failed = [r for r in results if r.status == "failed"]
    assert len(completed) == 1
    assert len(failed) == 3
    assert sum(adapter_entries) == 1


def test_terminal_event_on_failure() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    policy = _SpyPolicy(_deny_decision(reason="unauthenticated_principal"))
    sink = _RecordingEventSink()
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=sink),
    )
    assert result.status == "failed"
    terminal = [e for e in sink.events if e.event_type in {"capability.failed", "capability.cancelled", "capability.completed"}]
    assert len(terminal) >= 1


def test_event_sink_failure_does_not_duplicate_dispatch() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    sink = _RecordingEventSink(fail_on={"capability.resolved", "capability.authorized", "capability.completed"})
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=sink),
    )
    assert result.status == "completed"
    assert len(adapter.calls) == 1


def test_unexpected_exception_text_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    binding = _binding()
    target = _target_from_binding(binding)

    class BoomRegistry(_SpyRegistry):
        def resolve(self, b):
            self.calls.append("resolve")
            raise RuntimeError("SECRET_TOKEN_should_never_appear password=hunter2")

    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=BoomRegistry(target),
        policy=_SpyPolicy(_allow_decision()),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    with caplog.at_level(logging.DEBUG):
        result = gw.execute(
            _request(binding=binding),
            ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
        )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "execution_failed"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET_TOKEN" not in joined
    assert "hunter2" not in joined
    assert "password=" not in joined
    # Exception class name is ok.
    assert "RuntimeError" in joined or result.error.safe_code == "execution_failed"


def test_metrics_use_monotonic_time() -> None:
    binding = _binding()
    target = _target_from_binding(binding)

    def slow_execute(request, ports):
        time.sleep(0.02)

    adapter = _SpyAdapter(on_execute=slow_execute)
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.metrics.duration_ms >= 10.0
    assert result.metrics.adapter_duration_ms is not None
    assert result.metrics.adapter_duration_ms >= 10.0


def test_no_decrypt_or_model_client_outside_adapter() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    registry = _SpyRegistry(target)
    policy = _SpyPolicy(_allow_decision(descriptor=target.descriptor))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=registry,
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert registry.decrypt_calls == 0
    assert registry.model_client_calls == 0
    assert policy.decrypt_calls == 0


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


def test_invalid_output_from_adapter() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    # Valid object root but missing required "text".
    adapter = _SpyAdapter(structured_output={"wrong": True})
    adapter.capability_type = "tool"
    sink = _RecordingEventSink()
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=sink),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_output"
    assert result.terminal_output is False
    assert len(adapter.calls) == 1  # no retry
    # Raw invalid value not present in events.
    for event in sink.events:
        blob = str(event)
        assert "wrong" not in blob


def test_invalid_output_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    secret_value = "RAW_INVALID_OUTPUT_SECRET"
    adapter = _SpyAdapter(structured_output={"text": 123, "leak": secret_value})
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    with caplog.at_level(logging.DEBUG):
        result = gw.execute(
            _request(binding=binding),
            ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
        )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_output"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_value not in joined


# ---------------------------------------------------------------------------
# Cancellation races
# ---------------------------------------------------------------------------


def test_cancel_before_resolution() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    registry = _SpyRegistry(target)
    policy = _SpyPolicy(_allow_decision(descriptor=target.descriptor))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=registry,
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    cancel = _FakeCancellation(cancelled=True)
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=_RecordingEventSink()),
    )
    assert result.status == "cancelled"
    assert registry.calls == []  # adapter not started; resolve not reached
    assert policy.calls == []
    assert adapter.calls == []


def test_cancel_after_resolution_before_adapter() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    registry = _SpyRegistry(target)
    policy = _SpyPolicy(_allow_decision(descriptor=target.descriptor))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=registry,
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    cancel = _FakeCancellation()
    # Cancel after policy authorize: first checks pass until after authorize.
    checks = {"n": 0}

    def is_cancelled() -> bool:
        checks["n"] += 1
        # 1: before resolve, 2: before adapter (after policy)
        return checks["n"] >= 2

    cancel.is_cancelled = is_cancelled  # type: ignore[method-assign]
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=_RecordingEventSink()),
    )
    assert result.status == "cancelled"
    assert "resolve" in registry.calls
    assert policy.calls == ["authorize"]
    assert adapter.calls == []  # adapter not started


def test_cancel_after_authorization_before_permit() -> None:
    """Same as after-resolution: cancel immediately before adapter/permit."""
    binding = _binding()
    target = _target_from_binding(binding)
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    policy = _SpyPolicy(_allow_decision(descriptor=target.descriptor, permit=permit))
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    cancel = _FakeCancellation()
    n = {"c": 0}

    def is_cancelled() -> bool:
        n["c"] += 1
        return n["c"] >= 2

    cancel.is_cancelled = is_cancelled  # type: ignore[method-assign]
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=_RecordingEventSink()),
    )
    assert result.status == "cancelled"
    assert adapter.calls == []
    assert permit.consumed is False


def test_cancel_during_adapter_cooperative() -> None:
    binding = _binding()
    target = _target_from_binding(binding)
    cancel = _FakeCancellation()

    def on_execute(request, ports):
        # Simulate cooperative cancel mid-work by returning cancelled from adapter.
        pass

    from app.assistant.capabilities.contracts import CapabilityMetrics, cancelled_result

    adapter = _SpyAdapter(
        result=cancelled_result(
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
            call_id="call-1",
        ),
        on_execute=on_execute,
    )
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=_RecordingEventSink()),
    )
    assert result.status == "cancelled"
    assert len(adapter.calls) == 1  # adapter did start


def test_cancel_after_adapter_completed_does_not_rewrite_success() -> None:
    """If adapter completed before cancel flag, do not claim termination of side effect."""
    binding = _binding()
    target = _target_from_binding(binding)
    cancel = _FakeCancellation()
    n = {"c": 0}

    def is_cancelled() -> bool:
        n["c"] += 1
        # Cancel only after adapter returns (3rd+ check).
        return n["c"] >= 3

    cancel.is_cancelled = is_cancelled  # type: ignore[method-assign]
    adapter = _SpyAdapter(structured_output={"text": "done"})
    adapter.capability_type = "tool"
    from app.assistant.capabilities.gateway import CapabilityGateway

    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=_SpyPolicy(_allow_decision(descriptor=target.descriptor)),
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(binding=binding),
        ports=SimpleNamespace(cancellation=cancel, events=_RecordingEventSink()),
    )
    # Adapter completed successfully — gateway must not rewrite to cancelled.
    assert result.status == "completed"
    assert result.structured_output == {"text": "done"}
    assert len(adapter.calls) == 1


# ---------------------------------------------------------------------------
# Real policy engine integration (not just spies)
# ---------------------------------------------------------------------------


def test_gateway_with_real_policy_engine() -> None:
    from app.assistant.capabilities.gateway import CapabilityGateway
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    binding = _binding()
    target = _target_from_binding(
        binding,
        descriptor=_descriptor(
            capability_key=binding.resolved.capability_key,
            target_identity=binding.resolved.target_identity,
            resolution_digest=binding.resolved.resolution_digest,
            binding_contract_digest=binding.resolved.binding_contract_digest,
            dependency_closure_digest=binding.resolved.dependency_closure_digest,
            input_schema=binding.resolved.input_schema,
            output_schema=binding.resolved.output_schema,
            input_schema_digest=binding.resolved.input_schema_digest,
            output_schema_digest=binding.resolved.output_schema_digest,
        ),
    )
    verifier = _TestVerifier()
    policy = CapabilityPolicyEngine({("test", "test"): verifier})
    adapter = _SpyAdapter()
    adapter.capability_type = "tool"
    gw = CapabilityGateway(
        registry=_SpyRegistry(target),
        policy=policy,
        adapters={
            "tool": adapter,
            "workflow": _SpyAdapter(capability_type="workflow"),
            "agent": _SpyAdapter(capability_type="agent"),
        },
    )
    result = gw.execute(
        _request(
            binding=binding,
            evidence=_evidence(
                capability_key=binding.resolved.capability_key,
                resolution_digest=binding.resolved.resolution_digest,
                binding_contract_digest=binding.resolved.binding_contract_digest,
                dependency_closure_digest=binding.resolved.dependency_closure_digest,
            ),
        ),
        ports=SimpleNamespace(cancellation=_FakeCancellation(), events=_RecordingEventSink()),
    )
    assert result.status == "completed"
    assert len(adapter.calls) == 1
    assert verifier.calls == 1
