from __future__ import annotations

import copy
import math
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def _metrics(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityMetrics

    payload = {
        "duration_ms": 1.0,
        "adapter_duration_ms": None,
        "input_bytes": 0,
        "output_bytes": 0,
    }
    payload.update(overrides)
    return CapabilityMetrics(**payload)


def _timeout_policy(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityTimeoutPolicy

    payload = {
        "mode": "none",
        "timeout_seconds": None,
        "cancellation_supported": False,
    }
    payload.update(overrides)
    return CapabilityTimeoutPolicy(**payload)


def _classification(**overrides: Any):
    from app.assistant.capabilities.contracts import ClassificationContractRef

    payload = {
        "schema_version": 1,
        "revision": "plan02-v1",
        "ruleset_digest": DIGEST_A,
    }
    payload.update(overrides)
    return ClassificationContractRef(**payload)


def _behavior(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityBehavior

    payload = {
        "classification": _classification(),
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
        "terminal_output": False,
        "needs_followup": True,
        "followup_hint": None,
    }
    payload.update(overrides)
    return CapabilityCompletionContract(**payload)


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


def _continuation(**overrides: Any):
    from app.assistant.capabilities.contracts import ContinuationRef

    payload = {
        "continuation_type": "human_approval",
        "contract_version": 1,
        "reference_id": "cont-1",
        "payload_digest": DIGEST_C,
    }
    payload.update(overrides)
    return ContinuationRef(**payload)


def _error(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityError

    payload = {
        "error_type": "execution_failed",
        "safe_code": "execution_failed",
        "safe_message": "execution failed",
        "retry_disposition": "never",
        "target_identity": None,
        "call_id": None,
        "validation_issues": (),
    }
    payload.update(overrides)
    return CapabilityError(**payload)


def _resolved_binding() -> Any:
    from app.assistant.domain.contracts import (
        CapabilityCompletionContract,
        ResolvedCapabilityBinding,
    )
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant.skills.resolution import build_binding_snapshot

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
    target_id = uuid4()
    target_identity = f"remote-tool:{target_id}"
    config_digest = DIGEST_B
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target_id),
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
        target_id=target_id,
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
        capability_key="search.query",
        target_identity=target_identity,
        target_id=target_id,
        target_version_id=None,
        resolved_tool_id=target_id,
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


def _provenance(**overrides: Any):
    from app.assistant.capabilities.contracts import FrozenBindingProvenance

    payload = {
        "origin": "test",
        "binding_row_id": None,
        "owner_version_id": None,
        "source_snapshot_digest": DIGEST_D,
    }
    payload.update(overrides)
    return FrozenBindingProvenance(**payload)


def test_waiting_result_requires_portable_continuation() -> None:
    from app.assistant.capabilities.contracts import CapabilityResult

    with pytest.raises(ValidationError):
        CapabilityResult(
            status="waiting",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=None,
            terminal_output=False,
            needs_followup=True,
            error=None,
            metrics=_metrics(),
        )


def test_result_status_error_continuation_invariants() -> None:
    from app.assistant.capabilities.contracts import CapabilityResult

    completed = CapabilityResult(
        status="completed",
        user_text="ok",
        structured_output={"ok": True},
        artifact_refs=(),
        continuation=None,
        terminal_output=True,
        needs_followup=False,
        error=None,
        metrics=_metrics(),
    )
    assert completed.error is None
    assert completed.continuation is None

    failed = CapabilityResult(
        status="failed",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=None,
        terminal_output=False,
        needs_followup=False,
        error=_error(),
        metrics=_metrics(),
    )
    assert failed.error is not None

    cancelled = CapabilityResult(
        status="cancelled",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=None,
        terminal_output=False,
        needs_followup=False,
        error=_error(error_type="cancelled", safe_code="cancelled", safe_message="cancelled"),
        metrics=_metrics(),
    )
    assert cancelled.error is not None and cancelled.error.error_type == "cancelled"

    waiting = CapabilityResult(
        status="waiting",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=_continuation(),
        terminal_output=False,
        needs_followup=True,
        error=None,
        metrics=_metrics(),
    )
    assert waiting.continuation is not None

    with pytest.raises(ValidationError):
        CapabilityResult(
            status="completed",
            user_text="ok",
            structured_output=None,
            artifact_refs=(),
            continuation=_continuation(),
            terminal_output=True,
            needs_followup=False,
            error=None,
            metrics=_metrics(),
        )
    with pytest.raises(ValidationError):
        CapabilityResult(
            status="completed",
            user_text="ok",
            structured_output=None,
            artifact_refs=(),
            continuation=None,
            terminal_output=True,
            needs_followup=False,
            error=_error(),
            metrics=_metrics(),
        )
    with pytest.raises(ValidationError):
        CapabilityResult(
            status="failed",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=None,
            terminal_output=False,
            needs_followup=False,
            error=None,
            metrics=_metrics(),
        )
    with pytest.raises(ValidationError):
        CapabilityResult(
            status="cancelled",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=None,
            terminal_output=False,
            needs_followup=False,
            error=_error(error_type="execution_failed"),
            metrics=_metrics(),
        )
    with pytest.raises(ValidationError):
        CapabilityResult(
            status="failed",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=_continuation(),
            terminal_output=False,
            needs_followup=False,
            error=_error(),
            metrics=_metrics(),
        )


def test_contracts_are_frozen_and_reject_extra_keys() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityExecutionContext,
        CapabilityPrincipal,
    )

    principal = CapabilityPrincipal(
        principal_type="test",
        principal_id="p1",
        authenticated=True,
    )
    with pytest.raises(ValidationError):
        principal.principal_id = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CapabilityPrincipal(
            principal_type="test",
            principal_id="p1",
            authenticated=True,
            unexpected="nope",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        CapabilityExecutionContext(call_id="c1", extra_field=True)  # type: ignore[call-arg]


def test_digest_format_is_lowercase_sha256() -> None:
    from app.assistant.capabilities.contracts import ClassificationContractRef

    ClassificationContractRef(revision="plan02-v1", ruleset_digest=DIGEST_A)
    with pytest.raises(ValidationError):
        ClassificationContractRef(revision="plan02-v1", ruleset_digest="A" * 64)
    with pytest.raises(ValidationError):
        ClassificationContractRef(revision="plan02-v1", ruleset_digest="not-a-digest")
    with pytest.raises(ValidationError):
        ClassificationContractRef(revision="plan02-v1", ruleset_digest="a" * 63)


def test_execution_input_is_object_only_json() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityAuthorizationEvidence,
        CapabilityExecutionContext,
        CapabilityExecutionRequest,
        project_frozen_capability_binding,
    )

    binding = project_frozen_capability_binding(
        resolved=_resolved_binding(),
        provenance=_provenance(),
    )
    auth = CapabilityAuthorizationEvidence(
        issuer="test",
        call_id="call-1",
        principal=_principal(),
        entrypoint="test",
        owner=_owner(),
        capability_key="search.query",
        resolution_digest=binding.resolved.resolution_digest,
        binding_contract_digest=binding.resolved.binding_contract_digest,
        dependency_closure_digest=binding.resolved.dependency_closure_digest,
        allowed_side_effects=("read",),
        grant_source_digest=DIGEST_E,
        evidence_digest=DIGEST_A,
    )
    context = CapabilityExecutionContext(call_id="call-1")
    CapabilityExecutionRequest(
        binding=binding,
        input={"query": "hello"},
        context=context,
        authorization=auth,
    )
    with pytest.raises(ValidationError):
        CapabilityExecutionRequest(
            binding=binding,
            input=["not", "object"],  # type: ignore[arg-type]
            context=context,
            authorization=auth,
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityExecutionRequest(
            binding=binding,
            input={"bad": float("nan")},
            context=context,
            authorization=auth,
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityExecutionRequest(
            binding=binding,
            input={"bad": float("inf")},
            context=context,
            authorization=auth,
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityExecutionRequest(
            binding=binding,
            input={"bad": {1: "x"}},  # type: ignore[dict-item]
            context=context,
            authorization=auth,
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityExecutionRequest(
            binding=binding,
            input={"cb": lambda: None},  # type: ignore[dict-item]
            context=context,
            authorization=auth,
        )


def test_negative_numeric_bounds_fail() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityExecutionContext,
        CapabilityMetrics,
        CapabilityTimeoutPolicy,
    )

    with pytest.raises(ValidationError):
        CapabilityExecutionContext(call_id="c1", nesting_depth=-1)
    with pytest.raises(ValidationError):
        CapabilityTimeoutPolicy(mode="native", timeout_seconds=-1.0, cancellation_supported=True)
    with pytest.raises(ValidationError):
        CapabilityMetrics(duration_ms=-0.1, input_bytes=0, output_bytes=0)
    with pytest.raises(ValidationError):
        CapabilityMetrics(duration_ms=1.0, input_bytes=-1, output_bytes=0)
    with pytest.raises(ValidationError):
        CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=-2)


def test_empty_identity_fields_fail() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityExecutionContext,
        CapabilityOwnerRef,
        CapabilityPrincipal,
        CapabilityRuntimeEvent,
        CapabilityEventMetadata,
    )

    with pytest.raises(ValidationError):
        CapabilityExecutionContext(call_id="")
    with pytest.raises(ValidationError):
        CapabilityExecutionContext(call_id="   ")
    with pytest.raises(ValidationError):
        CapabilityPrincipal(principal_type="test", principal_id="", authenticated=True)
    with pytest.raises(ValidationError):
        CapabilityOwnerRef(owner_kind="test", owner_id="", owner_version_id=None)
    with pytest.raises(ValidationError):
        CapabilityRuntimeEvent(
            event_type="capability.started",
            call_id="",
            capability_key="k",
            target_identity="t",
            capability_type="tool",
            metadata=CapabilityEventMetadata(),
        )
    with pytest.raises(ValidationError):
        CapabilityRuntimeEvent(
            event_type="capability.started",
            call_id="c1",
            capability_key="",
            target_identity="t",
            capability_type="tool",
            metadata=CapabilityEventMetadata(),
        )
    with pytest.raises(ValidationError):
        CapabilityRuntimeEvent(
            event_type="capability.started",
            call_id="c1",
            capability_key="k",
            target_identity="",
            capability_type="tool",
            metadata=CapabilityEventMetadata(),
        )


def test_unknown_cannot_be_parallel_safe() -> None:
    with pytest.raises(ValidationError):
        _behavior(side_effect="unknown", parallel_safe=True)
    ok = _behavior(side_effect="unknown", parallel_safe=False)
    assert ok.parallel_safe is False


def test_safe_error_messages_reject_controls_and_are_length_bounded() -> None:
    from app.assistant.capabilities.contracts import CapabilityError, MAX_SAFE_MESSAGE_LEN

    with pytest.raises(ValidationError):
        CapabilityError(
            error_type="execution_failed",
            safe_code="execution_failed",
            safe_message="bad\nmessage",
            retry_disposition="never",
        )
    with pytest.raises(ValidationError):
        CapabilityError(
            error_type="execution_failed",
            safe_code="execution_failed",
            safe_message="bad\x00message",
            retry_disposition="never",
        )
    with pytest.raises(ValidationError):
        CapabilityError(
            error_type="execution_failed",
            safe_code="execution_failed",
            safe_message="x" * (MAX_SAFE_MESSAGE_LEN + 1),
            retry_disposition="never",
        )
    ok = CapabilityError(
        error_type="execution_failed",
        safe_code="execution_failed",
        safe_message="x" * MAX_SAFE_MESSAGE_LEN,
        retry_disposition="never",
    )
    assert len(ok.safe_message) == MAX_SAFE_MESSAGE_LEN


def test_event_metadata_rejects_unknown_and_raw_payload_fields() -> None:
    from app.assistant.capabilities.contracts import CapabilityEventMetadata, CapabilityRuntimeEvent

    with pytest.raises(ValidationError):
        CapabilityEventMetadata(input={"secret": "x"})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CapabilityEventMetadata(output={"secret": "x"})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CapabilityEventMetadata(error={"secret": "x"})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CapabilityEventMetadata(raw_body="nope")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CapabilityRuntimeEvent(
            event_type="capability.completed",
            call_id="c1",
            capability_key="k",
            target_identity="t",
            capability_type="tool",
            metadata=CapabilityEventMetadata(),
            input={"x": 1},  # type: ignore[call-arg]
        )


def test_serialization_round_trips_deterministically() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityDescriptor,
        CapabilityRuntimeEvent,
        CapabilityEventMetadata,
    )

    descriptor = CapabilityDescriptor(
        capability_key="search.query",
        capability_type="tool",
        target_identity="system-tool:search_entries",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        display_name="Search",
        description="Search entries",
        input_schema={"type": "object"},
        output_schema={"type": "string"},
        input_schema_digest=DIGEST_D,
        output_schema_digest=DIGEST_E,
        descriptor_digest=DIGEST_A,
        executable_revision="build-1",
        behavior=_behavior(),
        availability=_availability(),
        completion=_completion(),
    )
    dumped = descriptor.model_dump(by_alias=True, mode="json")
    restored = CapabilityDescriptor.model_validate(dumped)
    assert restored.model_dump(by_alias=True, mode="json") == dumped

    event = CapabilityRuntimeEvent(
        event_type="capability.resolved",
        call_id="call-1",
        capability_key="search.query",
        target_identity="system-tool:search_entries",
        capability_type="tool",
        safe_status="available",
        metadata=CapabilityEventMetadata(
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
        ),
    )
    event_dump = event.model_dump(by_alias=True, mode="json")
    assert CapabilityRuntimeEvent.model_validate(event_dump).model_dump(
        by_alias=True, mode="json"
    ) == event_dump


def test_plan01_binding_projects_without_field_substitution() -> None:
    from app.assistant.capabilities.contracts import project_frozen_capability_binding
    from app.assistant.domain.digests import canonical_json_bytes

    resolved = _resolved_binding()
    source_snapshot = copy.deepcopy(resolved.resolution_snapshot)
    source_input = copy.deepcopy(resolved.input_schema)
    frozen = project_frozen_capability_binding(resolved=resolved, provenance=_provenance())

    assert frozen.resolved.binding_contract_digest == resolved.binding_contract_digest
    assert frozen.resolved.dependency_closure_digest == resolved.dependency_closure_digest
    assert frozen.ref.binding_contract_digest == resolved.binding_contract_digest
    assert frozen.ref.resolution_digest == resolved.resolution_digest
    assert frozen.ref.dependency_closure_digest == resolved.dependency_closure_digest
    assert frozen.ref.capability_key == resolved.capability_key
    assert frozen.ref.target_identity == resolved.target_identity
    assert canonical_json_bytes(frozen.resolved.resolution_snapshot) == canonical_json_bytes(
        source_snapshot
    )

    # Mutating the original ORM/source JSON cannot change the frozen projection.
    resolved.resolution_snapshot["inputSchema"] = {"type": "string"}  # type: ignore[index]
    if isinstance(resolved.input_schema, dict):
        resolved.input_schema["properties"] = {}  # type: ignore[index]
    source_input["hijacked"] = True
    assert frozen.resolved.binding_contract_digest == source_snapshot["bindingContractDigest"]
    assert frozen.resolved.input_schema.get("hijacked") is None
    assert frozen.resolved.resolution_snapshot["inputSchema"] == source_snapshot["inputSchema"]

    # Mutating a materialized schema copy cannot change later consumers.
    material = dict(frozen.resolved.input_schema)
    material["mutated"] = True
    assert "mutated" not in frozen.resolved.input_schema
    assert frozen.resolved.input_schema_digest == source_snapshot["inputSchemaDigest"]


def test_direct_frozen_binding_rejects_tampered_snapshot_with_stale_digests() -> None:
    """Direct construction must recompute the canonical digest, not just compare fields.

    A tampered resolution_snapshot body with a still-matching stored digest pair
    (resolved.binding_contract_digest == snapshot.bindingContractDigest) must be rejected.
    """
    from app.assistant.capabilities.contracts import (
        FrozenCapabilityBinding,
        ResolvedCapabilityBinding,
        ResolvedCapabilityRef,
    )
    from app.assistant.domain.contracts import CapabilityCompletionContract

    honest = _resolved_binding()
    stale_digest = honest.binding_contract_digest
    assert honest.resolution_snapshot["bindingContractDigest"] == stale_digest

    tampered_snapshot = copy.deepcopy(honest.resolution_snapshot)
    # Body change that would yield a different canonical digest if recomputed.
    tampered_snapshot["executableRevision"] = "tampered-revision"
    # Keep the stale digest pair mutually consistent so field-equality alone would pass.
    tampered_snapshot["bindingContractDigest"] = stale_digest

    tampered = ResolvedCapabilityBinding(
        capability_type=honest.capability_type,
        capability_key=honest.capability_key,
        target_identity=honest.target_identity,
        target_id=honest.target_id,
        target_version_id=honest.target_version_id,
        resolved_tool_id=honest.resolved_tool_id,
        resolved_workflow_version_id=honest.resolved_workflow_version_id,
        resolved_agent_version_id=honest.resolved_agent_version_id,
        resolved_revision=honest.resolved_revision,
        input_schema=copy.deepcopy(honest.input_schema),
        output_schema=copy.deepcopy(honest.output_schema),
        input_schema_digest=honest.input_schema_digest,
        output_schema_digest=honest.output_schema_digest,
        completion=CapabilityCompletionContract(
            terminal_output=honest.completion.terminal_output,
            needs_followup=honest.completion.needs_followup,
            followup_hint=honest.completion.followup_hint,
        ),
        config_digest=honest.config_digest,
        executable_revision=honest.executable_revision,
        resolution_digest=honest.resolution_digest,
        resolution_snapshot=tampered_snapshot,
        dependencies=(),
        dependency_closure_digest=honest.dependency_closure_digest,
        binding_contract_digest=stale_digest,
    )
    ref = ResolvedCapabilityRef(
        capability_type=tampered.capability_type,
        capability_key=tampered.capability_key,
        target_identity=tampered.target_identity,
        target_id=tampered.target_id,
        target_version_id=tampered.target_version_id,
        target_revision=tampered.resolved_revision,
        input_schema_digest=tampered.input_schema_digest,
        output_schema_digest=tampered.output_schema_digest,
        resolution_digest=tampered.resolution_digest,
        dependency_closure_digest=tampered.dependency_closure_digest,
        binding_contract_digest=tampered.binding_contract_digest,
    )

    with pytest.raises(ValidationError):
        FrozenCapabilityBinding(
            provenance=_provenance(),
            ref=ref,
            resolved=tampered,
        )


def test_non_json_and_runtime_objects_rejected_from_frozen_values() -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityDescriptor,
        ArtifactRef,
    )
    from pydantic import BaseModel

    class ArbitraryModel(BaseModel):
        value: str

    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityDescriptor(
            capability_key="k",
            capability_type="tool",
            target_identity="t",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            resolution_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
            display_name="n",
            description="d",
            input_schema={"cb": lambda: None},  # type: ignore[dict-item]
            output_schema={"type": "string"},
            input_schema_digest=DIGEST_D,
            output_schema_digest=DIGEST_E,
            descriptor_digest=DIGEST_A,
            executable_revision="1",
            behavior=_behavior(),
            availability=_availability(),
            completion=_completion(),
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        ArtifactRef(
            artifact_id=SimpleNamespace(session="db"),  # type: ignore[arg-type]
            media_type="text/plain",
            content_digest=DIGEST_A,
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityDescriptor(
            capability_key="k",
            capability_type="tool",
            target_identity="t",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            resolution_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
            display_name="n",
            description="d",
            input_schema={"model": ArbitraryModel(value="x")},  # type: ignore[dict-item]
            output_schema={"type": "string"},
            input_schema_digest=DIGEST_D,
            output_schema_digest=DIGEST_E,
            descriptor_digest=DIGEST_A,
            executable_revision="1",
            behavior=_behavior(),
            availability=_availability(),
            completion=_completion(),
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        CapabilityDescriptor(
            capability_key="k",
            capability_type="tool",
            target_identity="t",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            resolution_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
            display_name="n",
            description="d",
            input_schema={"exc": ValueError("secret-token-xyz")},  # type: ignore[dict-item]
            output_schema={"type": "string"},
            input_schema_digest=DIGEST_D,
            output_schema_digest=DIGEST_E,
            descriptor_digest=DIGEST_A,
            executable_revision="1",
            behavior=_behavior(),
            availability=_availability(),
            completion=_completion(),
        )


def test_result_factories_are_pure_and_consistent() -> None:
    from app.assistant.capabilities.contracts import (
        completed_result,
        failed_result,
        cancelled_result,
    )

    done = completed_result(
        user_text="done",
        structured_output={"ok": True},
        metrics=_metrics(duration_ms=12.5, input_bytes=3, output_bytes=4),
        terminal_output=True,
        needs_followup=False,
    )
    assert done.status == "completed"
    assert done.error is None
    assert done.continuation is None

    fail = failed_result(error=_error(), metrics=_metrics())
    assert fail.status == "failed"
    assert fail.error is not None
    assert fail.continuation is None

    cancel = cancelled_result(metrics=_metrics(), call_id="call-9", target_identity="t")
    assert cancel.status == "cancelled"
    assert cancel.error is not None
    assert cancel.error.error_type == "cancelled"
    assert cancel.error.call_id == "call-9"


def test_ephemeral_policy_decision_excludes_permit_from_compare() -> None:
    from app.assistant.capabilities.contracts import CapabilityPolicyDecision
    from app.assistant.capabilities.ports import SingleUseDispatchPermit

    class _Permit:
        permit_id = "p1"

        def consume(self, *, call_id: str, descriptor_digest: str) -> None:
            return None

    permit: SingleUseDispatchPermit = _Permit()  # type: ignore[assignment]
    left = CapabilityPolicyDecision(
        allowed=True,
        reason_code="allowed",
        call_id="c1",
        descriptor_digest=DIGEST_A,
        classification_ruleset_digest=DIGEST_B,
        evidence_digest=DIGEST_C,
        owner=_owner(),
        granted_side_effects=("read",),
        grant_source_digest=DIGEST_D,
        decision_digest=DIGEST_E,
        dispatch_permit=permit,
    )
    right = CapabilityPolicyDecision(
        allowed=True,
        reason_code="allowed",
        call_id="c1",
        descriptor_digest=DIGEST_A,
        classification_ruleset_digest=DIGEST_B,
        evidence_digest=DIGEST_C,
        owner=_owner(),
        granted_side_effects=("read",),
        grant_source_digest=DIGEST_D,
        decision_digest=DIGEST_E,
        dispatch_permit=None,
    )
    assert left == right
    assert "dispatch_permit" not in repr(left)


def test_nan_rejected_in_metrics() -> None:
    from app.assistant.capabilities.contracts import CapabilityMetrics

    with pytest.raises(ValidationError):
        CapabilityMetrics(duration_ms=math.nan, input_bytes=0, output_bytes=0)
    with pytest.raises(ValidationError):
        CapabilityMetrics(duration_ms=math.inf, input_bytes=0, output_bytes=0)
