"""Plan 03 Task 4: stream assembly vectors and soft-finalization policy."""

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
    CapabilityMetrics,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    completed_result,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
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
    ProviderRoundTerminal,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolChoice,
    ProviderUsage,
    ProviderUsageSnapshot,
    ToolSurfaceResolution,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import (  # noqa: E402
    is_finalization_round,
    run_provider_agent_loop,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderRuntimeInstructionMessage,
    ProviderToolMessage,
    ProviderUserMessage,
)
from app.assistant.provider_loop.scripted_provider import (  # noqa: E402
    ScriptedProvider,
    ScriptedRoundScript,
    text_then_terminal,
    tool_call_then_terminal,
)
from app.assistant.provider_loop.streaming import (  # noqa: E402
    ARGUMENTS_BYTE_LIMIT,
    IDENTITY_BYTE_LIMIT,
    DefaultFinalizationInstructionProvider,
    ProviderRoundAssembler,
    assemble_provider_round,
    is_safe_request_id,
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

RUN_ID = UUID("00000000-0000-4000-8000-000000000401")
CONV_ID = UUID("00000000-0000-4000-8000-000000000402")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000410")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000411")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000450")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000451")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000440")
TARGET_A = UUID("00000000-0000-4000-8000-000000000510")
TARGET_B = UUID("00000000-0000-4000-8000-000000000511")

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


def _manifest(run_id: UUID = RUN_ID):
    return create_base_run_manifest(
        run_id=run_id,
        main_agent=_main_agent(),
        provider=_provider(),
        model=_model(),
        effective_policy_digest=None,
    )


def _scope(*, run_id: UUID = RUN_ID):
    return create_execution_scope(
        run_id=run_id,
        conversation_id=CONV_ID,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-stream",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )


def _resolved_binding(*, capability_key: str, target_id: UUID | None = None):
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
            "configDigest": DIGEST_B,
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
        config_digest=DIGEST_B,
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
        config_digest=DIGEST_B,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )


def _frozen(*, capability_key: str, target_id: UUID | None = None):
    resolved = _resolved_binding(capability_key=capability_key, target_id=target_id)
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _descriptor(binding, *, description: str = "tool description"):
    resolved = binding.resolved if hasattr(binding, "resolved") else binding
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        side_effect="read",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=DIGEST_B,
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
        descriptor_digest=DIGEST_C,
        executable_revision=resolved.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status="available",
            reason_code=None,
            compatibility_only=False,
        ),
        completion=resolved.completion,
    )


def _pair(capability_key: str, *, target_id: UUID | None = None):
    binding = _frozen(capability_key=capability_key, target_id=target_id)
    return binding, _descriptor(binding)


def _surface(*keys: str) -> ToolSurfaceResolution:
    pairs = [
        _pair(key, target_id=TARGET_A if i == 0 else TARGET_B) for i, key in enumerate(keys)
    ]
    return build_provider_tool_surface(
        manifest=_manifest(),
        provider_protocol=P,
        visible=pairs,
        scope=_scope(),
    )


def _empty_surface(manifest=None) -> ToolSurfaceResolution:
    return build_provider_tool_surface(
        manifest=manifest or _manifest(),
        provider_protocol=P,
        visible=[],
        scope=_scope(),
    )


def _alias_for(surface_resolution: ToolSurfaceResolution, domain_key: str) -> str:
    for tool in surface_resolution.surface.tools:
        if tool.domain_key == domain_key:
            return tool.provider_alias
    raise AssertionError(f"missing alias for {domain_key}")


def _finish(events, surface_resolution, *, round_index: int = 0):
    return assemble_provider_round(
        events=events,
        surface=surface_resolution.surface,
        round_index=round_index,
    )


# ---------------------------------------------------------------------------
# Step 1: stream assembly vectors
# ---------------------------------------------------------------------------


def test_sequence_must_start_at_zero() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=1, delta="hi"),
        ProviderRoundTerminal(sequence=2, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="contiguous"):
        _finish(events, surface)


def test_sequence_gap_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="a"),
        ProviderTextDelta(sequence=2, delta="b"),
        ProviderRoundTerminal(sequence=3, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="contiguous"):
        _finish(events, surface)


def test_duplicate_sequence_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="a"),
        ProviderTextDelta(sequence=0, delta="b"),
        ProviderRoundTerminal(sequence=1, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="contiguous"):
        _finish(events, surface)


def test_out_of_order_sequence_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="a"),
        ProviderTextDelta(sequence=2, delta="c"),
        ProviderTextDelta(sequence=1, delta="b"),
        ProviderRoundTerminal(sequence=3, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="contiguous"):
        _finish(events, surface)


def test_one_text_chunk() -> None:
    surface = _surface("search.lookup")
    result = _finish(text_then_terminal("hello"), surface)
    assert result.assistant_message.content == "hello"
    assert result.assistant_message.tool_calls == ()
    assert result.finish_reason == "stop"


def test_many_text_chunks() -> None:
    surface = _surface("search.lookup")
    result = _finish(text_then_terminal("he", "llo", " ", "world"), surface)
    assert result.assistant_message.content == "hello world"


def test_text_plus_one_tool_call() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = tool_call_then_terminal(
        call_id="c1",
        provider_alias=alias,
        arguments_json='{"query":"q"}',
        provisional_text="thinking...",
    )
    result = _finish(events, surface)
    assert result.assistant_message.content == "thinking..."
    assert len(result.assistant_message.tool_calls) == 1
    call = result.assistant_message.tool_calls[0]
    assert call.domain_key == "search.lookup"
    assert call.arguments == {"query": "q"}
    assert call.surface_digest == surface.surface.surface_digest
    assert (
        call.binding_contract_digest
        == surface.surface.tools[0].binding.ref.binding_contract_digest
    )


def test_interleaved_text_and_tool_fragments() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="pre-"),
        ProviderToolCallDelta(
            sequence=1,
            call_index=0,
            call_id="c1",
            provider_alias_delta=alias[:3],
            arguments_delta='{"que',
        ),
        ProviderTextDelta(sequence=2, delta="mid-"),
        ProviderToolCallDelta(
            sequence=3,
            call_index=0,
            provider_alias_delta=alias[3:],
            arguments_delta='ry":"x"}',
        ),
        ProviderTextDelta(sequence=4, delta="post"),
        ProviderRoundTerminal(sequence=5, finish_reason="tool_calls"),
    )
    result = _finish(events, surface)
    assert result.assistant_message.content == "pre-mid-post"
    assert result.assistant_message.tool_calls[0].arguments == {"query": "x"}
    assert result.assistant_message.tool_calls[0].provider_alias == alias


def test_two_tool_calls_fragmented_out_of_order_by_call_index() -> None:
    surface = _surface("search.lookup", "search.write")
    alias0 = _alias_for(surface, "search.lookup")
    alias1 = _alias_for(surface, "search.write")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=1,
            call_id="c1",
            provider_alias_delta=alias1,
            arguments_delta='{"query":"b"}',
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=0,
            call_id="c0",
            provider_alias_delta=alias0,
            arguments_delta='{"query":"a"}',
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="tool_calls"),
    )
    result = _finish(events, surface)
    calls = result.assistant_message.tool_calls
    assert [c.call_index for c in calls] == [0, 1]
    assert [c.call_id for c in calls] == ["c0", "c1"]
    assert calls[0].domain_key == "search.lookup"
    assert calls[1].domain_key == "search.write"


def test_arguments_split_across_chunks() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="c1",
            provider_alias_delta=alias,
            arguments_delta='{"que',
        ),
        ProviderToolCallDelta(sequence=1, call_index=0, arguments_delta='ry":'),
        ProviderToolCallDelta(sequence=2, call_index=0, arguments_delta='"z"}'),
        ProviderRoundTerminal(sequence=3, finish_reason="tool_calls"),
    )
    result = _finish(events, surface)
    assert result.assistant_message.tool_calls[0].arguments == {"query": "z"}


def test_terminal_usage_captured() -> None:
    surface = _surface("search.lookup")
    usage = ProviderUsage(input_tokens=10, output_tokens=4, total_tokens=14)
    result = _finish(text_then_terminal("ok", usage=usage), surface)
    assert result.usage == usage


def test_decreasing_usage_snapshot_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderUsageSnapshot(
            sequence=0,
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        ),
        ProviderUsageSnapshot(
            sequence=1,
            usage=ProviderUsage(input_tokens=3, output_tokens=1, total_tokens=4),
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="usage"):
        _finish(events, surface)


def test_inconsistent_usage_snapshot_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderUsageSnapshot(
            sequence=0,
            usage=ProviderUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        ),
        ProviderUsageSnapshot(
            sequence=1,
            usage=ProviderUsage(input_tokens=10, output_tokens=2, total_tokens=12),
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="usage"):
        _finish(events, surface)


def test_missing_finish_reason_allowed() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="ok"),
        ProviderRoundTerminal(sequence=1, finish_reason=None),
    )
    result = _finish(events, surface)
    assert result.finish_reason is None
    assert result.assistant_message.content == "ok"


def test_unsafe_and_oversized_request_id_discarded() -> None:
    assert is_safe_request_id("req_abc-123")
    assert not is_safe_request_id("sk-secret")
    assert not is_safe_request_id("a" * 200)
    assert not is_safe_request_id("bad id with space")
    assert not is_safe_request_id("https://evil.example/path")

    surface = _surface("search.lookup")
    assembler = ProviderRoundAssembler(surface=surface.surface, round_index=0)
    assembler.accept(ProviderTextDelta(sequence=0, delta="x"))
    assembler.accept(
        ProviderRoundTerminal(
            sequence=1,
            finish_reason="stop",
            safe_request_id="not a safe id!!!",
        )
    )
    result = assembler.finish()
    assert result.finish_reason == "stop"


def test_duplicate_terminal_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="x"),
        ProviderRoundTerminal(sequence=1, finish_reason="stop"),
        ProviderRoundTerminal(sequence=2, finish_reason="stop"),
    )
    with pytest.raises(ValueError, match="terminal"):
        _finish(events, surface)


def test_chunk_after_terminal_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderTextDelta(sequence=0, delta="x"),
        ProviderRoundTerminal(sequence=1, finish_reason="stop"),
        ProviderTextDelta(sequence=2, delta="late"),
    )
    with pytest.raises(ValueError, match="terminal"):
        _finish(events, surface)


def test_empty_stream_rejected() -> None:
    surface = _surface("search.lookup")
    with pytest.raises(ValueError, match="empty"):
        _finish((), surface)


def test_assembler_does_not_retain_events_after_finish() -> None:
    surface = _surface("search.lookup")
    assembler = ProviderRoundAssembler(surface=surface.surface, round_index=0)
    assembler.accept(ProviderTextDelta(sequence=0, delta="done"))
    assembler.accept(ProviderRoundTerminal(sequence=1, finish_reason="stop"))
    result = assembler.finish()
    assert result.assistant_message.content == "done"
    with pytest.raises(ValueError, match="finished|complete"):
        assembler.accept(ProviderTextDelta(sequence=2, delta="nope"))
    with pytest.raises(ValueError, match="finished|complete"):
        assembler.finish()


# ---------------------------------------------------------------------------
# Step 2: malformed tool call assembly
# ---------------------------------------------------------------------------


def test_changing_call_id_rejected() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="c1",
            provider_alias_delta=alias,
            arguments_delta="{",
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=0,
            call_id="c2",
            arguments_delta="}",
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="call id|call_id"):
        _finish(events, surface)


def test_changing_name_after_complete_alias_rejected() -> None:
    surface = _surface("search.lookup", "search.write")
    alias0 = _alias_for(surface, "search.lookup")
    alias1 = _alias_for(surface, "search.write")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="c1",
            provider_alias_delta=alias0,
            arguments_delta='{"query":"a"}',
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=0,
            provider_alias_delta=alias1,
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="name|alias"):
        _finish(events, surface)


def test_duplicate_call_id_rejected() -> None:
    surface = _surface("search.lookup", "search.write")
    alias0 = _alias_for(surface, "search.lookup")
    alias1 = _alias_for(surface, "search.write")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="same",
            provider_alias_delta=alias0,
            arguments_delta='{"query":"a"}',
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=1,
            call_id="same",
            provider_alias_delta=alias1,
            arguments_delta='{"query":"b"}',
        ),
        ProviderRoundTerminal(sequence=2, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="duplicate"):
        _finish(events, surface)


def test_gapped_call_index_rejected() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=1,
            call_id="c1",
            provider_alias_delta=alias,
            arguments_delta='{"query":"a"}',
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="index"):
        _finish(events, surface)


def test_non_function_type_rejected() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    event = ProviderToolCallDelta.model_construct(
        event_type="tool_call.delta",
        sequence=0,
        call_index=0,
        call_id="c1",
        function_type="custom",  # type: ignore[arg-type]
        provider_alias_delta=alias,
        arguments_delta='{"query":"a"}',
    )
    events = (
        event,
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="function"):
        _finish(events, surface)


def test_invalid_json_arguments_rejected() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = tool_call_then_terminal(
        call_id="c1",
        provider_alias=alias,
        arguments_json='{"query":',
    )
    with pytest.raises(ValueError, match="JSON"):
        _finish(events, surface)


@pytest.mark.parametrize("payload", ['"scalar"', "[1,2]", "null", "12"])
def test_non_object_json_arguments_rejected(payload: str) -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = tool_call_then_terminal(
        call_id="c1",
        provider_alias=alias,
        arguments_json=payload,
    )
    with pytest.raises(ValueError, match="object"):
        _finish(events, surface)


def test_unknown_alias_rejected() -> None:
    surface = _surface("search.lookup")
    events = tool_call_then_terminal(
        call_id="c1",
        provider_alias="not_on_surface",
        arguments_json='{"query":"a"}',
    )
    with pytest.raises(ValueError, match="unknown"):
        _finish(events, surface)


def test_arguments_exceeding_byte_limit_rejected() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    huge = "x" * (ARGUMENTS_BYTE_LIMIT + 1)
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="c1",
            provider_alias_delta=alias,
            arguments_delta=huge,
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="limit|arguments"):
        _finish(events, surface)


def test_missing_alias_rejected() -> None:
    surface = _surface("search.lookup")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="c1",
            provider_alias_delta="",
            arguments_delta='{"query":"a"}',
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="alias|name"):
        _finish(events, surface)


def test_identity_byte_limit_exceeded() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    oversized_id = "id_" + ("x" * IDENTITY_BYTE_LIMIT)
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id=oversized_id,
            provider_alias_delta=alias,
            arguments_delta='{"query":"a"}',
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    with pytest.raises(ValueError, match="limit|identity|call_id"):
        _finish(events, surface)


def test_missing_call_id_is_synthesized_with_warning() -> None:
    surface = _surface("search.lookup")
    alias = _alias_for(surface, "search.lookup")
    events = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id=None,
            provider_alias_delta=alias,
            arguments_delta='{"query":"a"}',
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls"),
    )
    result = _finish(events, surface, round_index=3)
    call = result.assistant_message.tool_calls[0]
    assert call.call_id.startswith("call_r3_i0_")
    assert len(call.call_id.split("_")[-1]) == 8
    assert "synthesized_call_id" in result.compatibility_warnings


# ---------------------------------------------------------------------------
# Soft finalization pure policy
# ---------------------------------------------------------------------------


def test_is_finalization_round_policy() -> None:
    assert is_finalization_round(round_index=0, max_rounds=4, prior_tool_call_count=0) is False
    assert is_finalization_round(round_index=3, max_rounds=4, prior_tool_call_count=0) is False
    assert is_finalization_round(round_index=3, max_rounds=4, prior_tool_call_count=1) is True
    assert is_finalization_round(round_index=2, max_rounds=4, prior_tool_call_count=2) is False
    assert is_finalization_round(round_index=1, max_rounds=2, prior_tool_call_count=1) is True
    assert is_finalization_round(round_index=3, max_rounds=4, prior_tool_call_count=5) is True


def test_finalization_instruction_provider_zh_en() -> None:
    provider = DefaultFinalizationInstructionProvider()
    en = provider.build(locale="en")
    zh = provider.build(locale="zh")
    assert isinstance(en, ProviderRuntimeInstructionMessage)
    assert en.instruction_type == "soft_finalization"
    assert zh.instruction_type == "soft_finalization"
    assert en.locale == "en"
    assert zh.locale == "zh"
    assert "tool" in en.content.lower() or "summar" in en.content.lower()
    assert "工具" in zh.content or "总结" in zh.content
    assert en.content != zh.content
    assert en.role == "runtime_instruction"


# ---------------------------------------------------------------------------
# Loop fakes
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
    fail_on: set[str] = field(default_factory=set)
    fail_count: int = 0

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type in self.fail_on:
            self.fail_count += 1
            raise RuntimeError(f"sink-fail-{event_type}")
        self.events.append((event_type, payload))

    def types(self) -> list[str]:
        return [item[0] for item in self.events]

    def payloads(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for et, payload in self.events if et == event_type]


@dataclass
class RecordingToolsProvider:
    resolutions: list[ToolSurfaceResolution]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def resolve(self, manifest, *, scope, locale):
        self.calls.append(
            {
                "manifest_digest": manifest.manifest_digest,
                "manifest_revision": manifest.revision,
                "scope_digest": scope.scope_digest,
                "locale": locale,
            }
        )
        if not self.resolutions:
            raise AssertionError("tools provider has no remaining resolutions")
        return self.resolutions.pop(0)


@dataclass
class RecordingDescriptorVerifier:
    current_by_binding: dict[str, CapabilityDescriptor] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def require_current(self, *, binding, exposed_descriptor, scope):
        self.calls.append(
            {
                "binding_digest": binding.ref.binding_contract_digest,
                "exposed_descriptor_digest": exposed_descriptor.descriptor_digest,
                "scope_digest": scope.scope_digest,
            }
        )
        current = self.current_by_binding.get(
            binding.ref.binding_contract_digest,
            exposed_descriptor,
        )
        if (
            current.descriptor_digest != exposed_descriptor.descriptor_digest
            or current.behavior.behavior_digest != exposed_descriptor.behavior.behavior_digest
            or current.behavior.classification.revision
            != exposed_descriptor.behavior.classification.revision
            or current.behavior.classification.ruleset_digest
            != exposed_descriptor.behavior.classification.ruleset_digest
            or current.availability.status != exposed_descriptor.availability.status
        ):
            raise RuntimeError("classification_changed")
        return current


@dataclass
class RecordingAuthFactory:
    issued: list[str] = field(default_factory=list)

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
            allowed_side_effects=("none", "compute", "read"),
            grant_source_digest=DIGEST_E,
            evidence_digest=DIGEST_F,
        )
        self.issued.append(call.call_id)
        return evidence


@dataclass
class RecordingDispatcher:
    results: list[ProviderDispatchResult]
    verifier: RecordingDescriptorVerifier
    requests: list[ProviderDispatchRequest] = field(default_factory=list)

    def dispatch(self, request: ProviderDispatchRequest, *, cancellation):
        del cancellation
        self.requests.append(request)
        self.verifier.require_current(
            binding=request.binding,
            exposed_descriptor=request.descriptor,
            scope=request.execution_scope,
        )
        if not self.results:
            raise AssertionError("dispatcher has no remaining results")
        return self.results.pop(0)


@dataclass
class SequentialSiblingExecutor:
    def map_parallel(self, items, worker, *, max_workers: int):
        del max_workers
        return [worker(item) for item in items]


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
    verifier: RecordingDescriptorVerifier | None = None,
    auth: RecordingAuthFactory | None = None,
    dispatcher: RecordingDispatcher | None = None,
    cancellation: RecordingCancellation | None = None,
    events: RecordingEventSink | None = None,
) -> ProviderLoopPorts:
    verifier = verifier or RecordingDescriptorVerifier()
    auth = auth or RecordingAuthFactory()
    dispatcher = dispatcher or RecordingDispatcher(results=[], verifier=verifier)
    return ProviderLoopPorts(
        provider=provider,
        tools_provider=tools,
        current_descriptors=verifier,
        authorization_evidence=auth,
        tool_dispatcher=dispatcher,
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=cancellation or RecordingCancellation(),
        events=events or RecordingEventSink(),
    )


def _completed_cap():
    return completed_result(
        user_text="tool ok",
        structured_output={"ok": True},
        metrics=_metrics(),
    )


# ---------------------------------------------------------------------------
# Step 3: buffered visibility + event sink
# ---------------------------------------------------------------------------


def test_natural_answer_replays_final_text_after_assembly_only() -> None:
    model = _model()
    manifest = _manifest()
    surface = _surface("search.lookup")
    provider = _scripted(model)
    final = "final answer text"
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(ProviderUserMessage(content="hi"),),
            expected_surface_digest=surface.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=text_then_terminal("final ", "answer ", "text"),
        )
    )
    events = RecordingEventSink()
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[surface]),
            events=events,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == final
    assert result.stop_reason == "natural_completion"
    types = events.types()
    completed_at = types.index("round.completed")
    first_delta = types.index("final_text.delta")
    assert first_delta > completed_at
    assert "tool_call.requested" not in types


def test_tool_call_round_text_retained_but_not_emitted_as_final() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    res2 = ToolSurfaceResolution(manifest=res1.manifest, surface=res1.surface)
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    provisional = "I will call a tool"
    final = "here is the answer"
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.round_index == 1
            assert request.tools_enabled is True
            assert request.finalization_round is False
            assert len(request.messages) == 3
            assert isinstance(request.messages[1], ProviderAssistantMessage)
            assert request.messages[1].content == provisional
            assert isinstance(request.messages[2], ProviderToolMessage)
            assert request.messages[2].call_id == call_id
            yield from text_then_terminal(final)

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
            expected_messages=(ProviderUserMessage(content="use tool"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
                provisional_text=provisional,
            ),
        )
    )
    events = RecordingEventSink()
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="use tool"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1, res2]),
            verifier=verifier,
            dispatcher=dispatcher,
            events=events,
        ),
    )
    assert result.status == "completed"
    assert result.final_text == final
    assert provisional not in (result.final_text or "")
    assistant_msgs = [m for m in result.messages if getattr(m, "role", None) == "assistant"]
    assert any(getattr(m, "content", None) == provisional for m in assistant_msgs)
    joined = "".join(p["delta"] for p in events.payloads("final_text.delta"))
    assert provisional not in joined
    assert final in joined


def test_provider_error_discards_buffered_text() -> None:
    model = _model()
    manifest = _manifest()
    surface = _surface("search.lookup")
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(ProviderUserMessage(content="hi"),),
            expected_surface_digest=surface.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=(),
            raise_error=RuntimeError("provider boom"),
        )
    )
    events = RecordingEventSink()
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[surface]),
            events=events,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "provider_error"
    assert result.final_text is None
    assert "final_text.delta" not in events.types()


def test_event_sink_failure_never_duplicates_provider_or_tool_calls() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    res2 = ToolSurfaceResolution(manifest=res1.manifest, surface=res1.surface)
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            yield from text_then_terminal("done")

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
            expected_messages=(ProviderUserMessage(content="x"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
                provisional_text="tmp",
            ),
        )
    )
    events = RecordingEventSink(
        fail_on={"loop.started", "round.started", "tool_call.completed", "final_text.delta"}
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
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
            tools=RecordingToolsProvider(resolutions=[res1, res2]),
            verifier=verifier,
            dispatcher=dispatcher,
            events=events,
        ),
    )
    assert result.status == "completed"
    assert provider.request_count == 2
    assert len(dispatcher.requests) == 1
    assert events.fail_count >= 1


# ---------------------------------------------------------------------------
# Step 4: soft finalization loop scenarios
# ---------------------------------------------------------------------------


def test_soft_finalization_reserved_round_after_tools() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()
    instruction = DefaultFinalizationInstructionProvider().build(locale="en")

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.round_index == 1
            assert request.tools_enabled is False
            assert request.finalization_round is True
            assert request.tool_surface.tools == ()
            assert request.generation.tool_choice.mode == "none"
            assert any(isinstance(m, ProviderRuntimeInstructionMessage) for m in request.messages)
            assert sum(
                1 for m in request.messages if isinstance(m, ProviderRuntimeInstructionMessage)
            ) == 1
            runtime = next(
                m for m in request.messages if isinstance(m, ProviderRuntimeInstructionMessage)
            )
            assert runtime.instruction_type == "soft_finalization"
            assert runtime.content == instruction.content
            yield from text_then_terminal("soft final answer")

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    events = RecordingEventSink()
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=2,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1]),
            verifier=verifier,
            dispatcher=dispatcher,
            events=events,
        ),
    )
    assert result.status == "completed"
    assert result.stop_reason == "max_rounds_soft_finalized"
    assert result.final_text == "soft final answer"
    assert result.round_count == 2
    assert provider.request_count == 2
    instructions = [
        m for m in result.messages if isinstance(m, ProviderRuntimeInstructionMessage)
    ]
    assert len(instructions) == 1
    assert "finalization.started" in events.types()
    joined = "".join(p["delta"] for p in events.payloads("final_text.delta"))
    assert instructions[0].content not in joined
    assert "soft final answer" in joined


def test_soft_finalization_tool_calls_are_hard_stop() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.finalization_round is True
            assert request.tools_enabled is False
            # Provider illegally emits a tool call during finalization.
            yield from tool_call_then_terminal(
                call_id="c2",
                provider_alias=alias,
                arguments_json='{"query":"nope"}',
            )

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=2,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1]),
            verifier=verifier,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "max_rounds_hard_stop"
    assert len(dispatcher.requests) == 1
    assert provider.request_count == 2


def test_soft_finalization_empty_is_hard_stop() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.finalization_round is True
            yield from (ProviderRoundTerminal(sequence=0, finish_reason="stop"),)

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=2,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1]),
            verifier=verifier,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "max_rounds_hard_stop"


def test_soft_finalization_provider_error_is_hard_stop() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.finalization_round is True
            raise RuntimeError("finalization provider boom")

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=2,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1]),
            verifier=verifier,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "failed"
    # Finalization provider error maps to hard stop when tools already used at budget.
    assert result.stop_reason in {"max_rounds_hard_stop", "provider_error"}
    assert provider.request_count == 2
    assert len(dispatcher.requests) == 1


def test_max_rounds_one_nonempty_surface_rejected_before_provider() -> None:
    model = _model()
    manifest = _manifest()
    surface = _surface("search.lookup")
    provider = _scripted(model)
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=1,
            locale="en",
            generation=ProviderGenerationOptions(tool_choice=ProviderToolChoice(mode="none")),
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[surface]),
        ),
    )
    assert result.status == "failed"
    assert result.stop_reason == "protocol_error"
    assert provider.request_count == 0


def test_direct_answer_first_round_natural_completion() -> None:
    model = _model()
    manifest = _manifest()
    surface = _surface("search.lookup")
    provider = _scripted(model)
    provider.enqueue(
        ScriptedRoundScript(
            expected_round_index=0,
            expected_messages=(ProviderUserMessage(content="hi"),),
            expected_surface_digest=surface.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=text_then_terminal("just answer"),
        )
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[surface]),
        ),
    )
    assert result.status == "completed"
    assert result.stop_reason == "natural_completion"
    assert result.round_count == 1
    assert provider.request_count == 1


def test_natural_final_before_max_after_tools() -> None:
    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    res2 = ToolSurfaceResolution(manifest=res1.manifest, surface=res1.surface)
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            assert request.round_index == 1
            assert request.finalization_round is False
            assert request.tools_enabled is True
            yield from text_then_terminal("early natural")

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1, res2]),
            verifier=verifier,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "completed"
    assert result.stop_reason == "natural_completion"
    assert result.final_text == "early natural"
    assert result.round_count == 2
    assert provider.request_count == 2


def test_finalization_instruction_appended_once_and_digested() -> None:
    from app.assistant.provider_loop.messages import digest_provider_message

    base = _manifest()
    binding, descriptor = _pair("search.lookup", target_id=TARGET_A)
    res1 = build_provider_tool_surface(
        manifest=base, provider_protocol=P, visible=[(binding, descriptor)], scope=_scope()
    )
    model = _model()
    alias = res1.surface.tools[0].provider_alias
    call_id = "c1"
    cap = _completed_cap()
    digests: list[str] = []

    class FlexibleProvider(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            if not getattr(self, "_done0", False):
                self._done0 = True
                yield from super().stream_round(request, cancellation=cancellation)
                return
            self.request_count += 1
            self.seen_requests.append(request)
            runtime_msgs = [
                m for m in request.messages if isinstance(m, ProviderRuntimeInstructionMessage)
            ]
            assert len(runtime_msgs) == 1
            digests.append(digest_provider_message(runtime_msgs[0]))
            yield from text_then_terminal("ok")

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
            expected_messages=(ProviderUserMessage(content="go"),),
            expected_surface_digest=res1.surface.surface_digest,
            expected_tools_enabled=True,
            expected_finalization_round=False,
            events=tool_call_then_terminal(
                call_id=call_id,
                provider_alias=alias,
                arguments_json='{"query":"q"}',
            ),
        )
    )
    verifier = RecordingDescriptorVerifier(
        current_by_binding={binding.ref.binding_contract_digest: descriptor}
    )
    dispatcher = RecordingDispatcher(
        results=[ProviderDispatchResult(capability_result=cap, next_manifest=res1.manifest)],
        verifier=verifier,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            manifest=base,
            initial_messages=(ProviderUserMessage(content="go"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=2,
            locale="en",
        ),
        _ports(
            provider=provider,
            tools=RecordingToolsProvider(resolutions=[res1]),
            verifier=verifier,
            dispatcher=dispatcher,
        ),
    )
    assert result.status == "completed"
    assert len(digests) == 1
    assert digests[0]
    runtime_in_result = [
        m for m in result.messages if isinstance(m, ProviderRuntimeInstructionMessage)
    ]
    assert len(runtime_in_result) == 1
    assert digest_provider_message(runtime_in_result[0]) == digests[0]
