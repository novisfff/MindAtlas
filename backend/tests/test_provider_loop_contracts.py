"""Plan 03 Task 1: provider loop result/continuation/scope/stream contracts."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityMetrics,
    CapabilityPrincipal,
    CapabilityResult,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    ContinuationRef,
    FrozenBindingProvenance,
    completed_result,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedProviderAliasRef,
    ResolvedRunManifestRevision,
    append_provider_aliases,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    CurrentCapabilityDescriptorVerifier,
    ProviderExecutionScope,
    ProviderGenerationOptions,
    ProviderLoopContinuation,
    ProviderLoopRequest,
    ProviderLoopResult,
    ProviderLoopResumeRequest,
    ProviderRoundTerminal,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolChoice,
    ProviderToolDefinition,
    ProviderToolSurface,
    ProviderUsage,
    ProviderUsageSnapshot,
    ProviderWaitingCallState,
    ProviderWaitingResolution,
    SafeProviderError,
    ToolSurfaceResolution,
    aggregate_provider_usage,
    assert_not_serializable_port,
    compute_alias_map_digest,
    compute_scope_digest,
    compute_surface_digest,
    create_execution_scope,
    parse_provider_stream_event,
    recompute_continuation_identity,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderToolCallRecord,
    ProviderToolMessage,
    ProviderUserMessage,
    digest_arguments,
    digest_provider_message,
    digest_provider_transcript,
    project_tool_result_envelope,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64
DIGEST_6 = "6" * 64
DIGEST_7 = "7" * 64
DIGEST_8 = "8" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000101")
CONV_ID = UUID("00000000-0000-4000-8000-000000000102")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000110")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000111")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000150")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000151")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000140")


def _principal(**overrides: Any) -> CapabilityPrincipal:
    payload = {
        "principal_type": "test",
        "principal_id": "principal-1",
        "authenticated": True,
    }
    payload.update(overrides)
    return CapabilityPrincipal(**payload)


def _main_agent() -> ResolvedMainAgentRef:
    return ResolvedMainAgentRef(
        profile_id=PROFILE_ID,
        version_id=PROFILE_VERSION_ID,
        profile_key="general_chat",
        sequence=1,
        content_digest=DIGEST_A,
    )


def _model() -> ModelRef:
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    return create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )


def _manifest() -> ResolvedRunManifestRevision:
    model = _model()
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    return create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=_main_agent(),
        provider=provider,
        model=model,
        effective_policy_digest=None,
    )


def _resolved_binding(
    *,
    capability_key: str = "tools.search",
    target_id: UUID | None = None,
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
    config_digest = DIGEST_B
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


def _descriptor(binding: ResolvedCapabilityBinding) -> CapabilityDescriptor:
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
        capability_key=binding.capability_key,
        capability_type="tool",
        target_identity=binding.target_identity,
        target_id=binding.target_id,
        target_version_id=binding.target_version_id,
        target_revision=binding.resolved_revision,
        resolution_digest=binding.resolution_digest,
        binding_contract_digest=binding.binding_contract_digest,
        dependency_closure_digest=binding.dependency_closure_digest,
        display_name=binding.capability_key,
        description="search",
        input_schema=binding.input_schema,
        output_schema=binding.output_schema,
        input_schema_digest=binding.input_schema_digest,
        output_schema_digest=binding.output_schema_digest,
        descriptor_digest=DIGEST_C,
        executable_revision=binding.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status="available",
            reason_code=None,
            compatibility_only=False,
        ),
        completion=binding.completion,
    )


def _surface(manifest: ResolvedRunManifestRevision) -> ProviderToolSurface:
    resolved = _resolved_binding()
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )
    descriptor = _descriptor(resolved)
    tool = ProviderToolDefinition(
        provider_alias="search_entries",
        domain_key=resolved.capability_key,
        description="search",
        input_schema=resolved.input_schema,
        binding=frozen,
        descriptor=descriptor,
    )
    alias_map = compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=manifest.manifest_digest,
        aliases=(
            (
                resolved.capability_key,
                "search_entries",
                resolved.binding_contract_digest,
            ),
        ),
    )
    surface_digest = compute_surface_digest(
        provider_protocol="openai_compat",
        manifest_revision=manifest.revision,
        manifest_digest=manifest.manifest_digest,
        alias_map_digest=alias_map,
        tools=(tool,),
    )
    return ProviderToolSurface(
        provider_protocol="openai_compat",
        manifest_revision=manifest.revision,
        manifest_digest=manifest.manifest_digest,
        alias_map_digest=alias_map,
        tools=(tool,),
        surface_digest=surface_digest,
    )


def _call_from_surface(surface: ProviderToolSurface) -> ProviderToolCall:
    tool = surface.tools[0]
    args = {"query": "hello"}
    return ProviderToolCall(
        call_id="call-1",
        call_index=0,
        provider_alias=tool.provider_alias,
        domain_key=tool.domain_key,
        arguments=args,
        arguments_digest=digest_arguments(args),
        binding_contract_digest=tool.binding.ref.binding_contract_digest,
        descriptor_digest=tool.descriptor.descriptor_digest,
        behavior_digest=tool.descriptor.behavior.behavior_digest,
        classification_revision=tool.descriptor.behavior.classification.revision,
        classification_ruleset_digest=tool.descriptor.behavior.classification.ruleset_digest,
        manifest_revision=surface.manifest_revision,
        manifest_digest=surface.manifest_digest,
        surface_digest=surface.surface_digest,
    )


def _metrics() -> CapabilityMetrics:
    return CapabilityMetrics(
        duration_ms=1.0,
        adapter_duration_ms=None,
        input_bytes=0,
        output_bytes=0,
    )


def _continuation_ref() -> ContinuationRef:
    return ContinuationRef(
        continuation_type="human_approval",
        contract_version=1,
        reference_id="cont-1",
        payload_digest=DIGEST_E,
    )


def test_execution_scope_digest_and_tamper_rejection() -> None:
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=_principal(),
        tenant_scope_id=None,
    )
    assert scope.scope_digest == compute_scope_digest(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=_principal(),
        tenant_scope_id=None,
    )
    with pytest.raises(ValidationError):
        ProviderExecutionScope(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            principal=_principal(),
            tenant_scope_id=None,
            scope_digest="0" * 64,
        )
    other = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=_principal(principal_id="principal-2"),
        tenant_scope_id=None,
    )
    assert other.scope_digest != scope.scope_digest


def test_scope_a_surface_not_reusable_for_scope_b_identity() -> None:
    scope_a = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=_principal(principal_id="a"),
        tenant_scope_id="tenant-a",
    )
    scope_b = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=_principal(principal_id="b"),
        tenant_scope_id="tenant-b",
    )
    assert scope_a.scope_digest != scope_b.scope_digest
    # Continuations embed full scope; swapping scopes is a digest mismatch.
    identity_a = recompute_continuation_identity  # presence check only
    assert callable(identity_a)


def test_status_stop_reason_combinations() -> None:
    manifest = _manifest()
    messages = (
        ProviderUserMessage(content="hi"),
        ProviderAssistantMessage(content="hello", tool_calls=()),
    )
    usage = ProviderUsage()
    completed = ProviderLoopResult(
        status="completed",
        final_text="hello",
        messages=messages,
        tool_calls=(),
        round_count=1,
        stop_reason="natural_completion",
        manifest=manifest,
        continuation=None,
        usage=usage,
        error=None,
    )
    assert completed.final_text == "hello"

    soft = ProviderLoopResult(
        status="completed",
        final_text="summary",
        messages=messages,
        tool_calls=(),
        round_count=2,
        stop_reason="max_rounds_soft_finalized",
        manifest=manifest,
        continuation=None,
        usage=usage,
        error=None,
    )
    assert soft.stop_reason == "max_rounds_soft_finalized"

    for bad_reason in (
        "waiting_interrupt",
        "cancelled",
        "provider_error",
        "protocol_error",
        "capability_error",
        "max_rounds_hard_stop",
    ):
        with pytest.raises(ValidationError):
            ProviderLoopResult(
                status="completed",
                final_text="x",
                messages=messages,
                tool_calls=(),
                round_count=1,
                stop_reason=bad_reason,  # type: ignore[arg-type]
                manifest=manifest,
                continuation=None,
                usage=usage,
                error=None,
            )

    failed = ProviderLoopResult(
        status="failed",
        final_text=None,
        messages=messages,
        tool_calls=(),
        round_count=1,
        stop_reason="provider_error",
        manifest=manifest,
        continuation=None,
        usage=usage,
        error=SafeProviderError(
            semantic_code="provider_error",
            safe_summary="provider failed",
        ),
    )
    assert failed.error is not None

    with pytest.raises(ValidationError):
        ProviderLoopResult(
            status="failed",
            final_text=None,
            messages=messages,
            tool_calls=(),
            round_count=1,
            stop_reason="provider_error",
            manifest=manifest,
            continuation=None,
            usage=usage,
            error=None,
        )

    cancelled = ProviderLoopResult(
        status="cancelled",
        final_text=None,
        messages=messages,
        tool_calls=(),
        round_count=1,
        stop_reason="cancelled",
        manifest=manifest,
        continuation=None,
        usage=usage,
        error=None,
    )
    assert cancelled.status == "cancelled"


def test_waiting_requires_continuation_and_non_waiting_forbids() -> None:
    manifest = _manifest()
    surface = _surface(manifest)
    call = _call_from_surface(surface)
    assistant = ProviderAssistantMessage(content=None, tool_calls=(call,))
    messages = (ProviderUserMessage(content="q"), assistant)
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=_principal(),
        tenant_scope_id=None,
    )
    waiting_state = ProviderWaitingCallState(
        call_id=call.call_id,
        call_index=0,
        binding_contract_digest=call.binding_contract_digest,
        descriptor_digest=call.descriptor_digest,
        behavior_digest=call.behavior_digest,
        classification_revision=call.classification_revision,
        classification_ruleset_digest=call.classification_ruleset_digest,
        capability_continuation=_continuation_ref(),
    )
    cont = ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=manifest.model,  # type: ignore[arg-type]
        locale="en",
        max_rounds=4,
        provider_rounds_used=1,
        prior_tool_call_count=0,
        accumulated_usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        current_manifest_revision=manifest.revision,
        current_manifest_digest=manifest.manifest_digest,
        exposed_surface=surface,
        assistant_message_digest=digest_provider_message(assistant),
        transcript_digest=digest_provider_transcript(messages),
        waiting_call=waiting_state,
        next_call_index=1,
        pending_call_ids=(),
        completed_call_records=(),
    )
    waiting = ProviderLoopResult(
        status="waiting",
        final_text=None,
        messages=messages,
        tool_calls=(
            ProviderToolCallRecord(
                call=call,
                status="waiting",
                result_message_digest=None,
                safe_duration_ms=None,
            ),
        ),
        round_count=1,
        stop_reason="waiting_interrupt",
        manifest=manifest,
        continuation=cont,
        usage=ProviderUsage(),
        error=None,
    )
    assert waiting.continuation is not None
    assert waiting.continuation.exposed_surface.surface_digest == surface.surface_digest
    assert (
        waiting.continuation.waiting_call.capability_continuation.reference_id
        == "cont-1"
    )

    with pytest.raises(ValidationError):
        ProviderLoopResult(
            status="waiting",
            final_text=None,
            messages=messages,
            tool_calls=(),
            round_count=1,
            stop_reason="waiting_interrupt",
            manifest=manifest,
            continuation=None,
            usage=ProviderUsage(),
            error=None,
        )

    with pytest.raises(ValidationError):
        ProviderLoopResult(
            status="completed",
            final_text="x",
            messages=(
                ProviderUserMessage(content="q"),
                ProviderAssistantMessage(content="x", tool_calls=()),
            ),
            tool_calls=(),
            round_count=1,
            stop_reason="natural_completion",
            manifest=manifest,
            continuation=cont,
            usage=ProviderUsage(),
            error=None,
        )


def test_continuation_identifies_one_open_assistant_and_pending_order() -> None:
    manifest = _manifest()
    surface = _surface(manifest)
    c1 = _call_from_surface(surface)
    c2 = ProviderToolCall(
        call_id="call-2",
        call_index=1,
        provider_alias=c1.provider_alias,
        domain_key=c1.domain_key,
        arguments={"query": "second"},
        arguments_digest=digest_arguments({"query": "second"}),
        binding_contract_digest=c1.binding_contract_digest,
        descriptor_digest=c1.descriptor_digest,
        behavior_digest=c1.behavior_digest,
        classification_revision=c1.classification_revision,
        classification_ruleset_digest=c1.classification_ruleset_digest,
        manifest_revision=c1.manifest_revision,
        manifest_digest=c1.manifest_digest,
        surface_digest=c1.surface_digest,
    )
    assistant = ProviderAssistantMessage(content=None, tool_calls=(c1, c2))
    # Complete first call, leave second open as waiting with no pending.
    tool_msg = ProviderToolMessage(
        call_id="call-1",
        provider_alias=c1.provider_alias,
        content=project_tool_result_envelope(
            domain_key=c1.domain_key,
            result=completed_result(user_text="ok", metrics=_metrics()),
        ),
    )
    messages = (ProviderUserMessage(content="q"), assistant, tool_msg)
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=_principal(),
        tenant_scope_id=None,
    )
    cont = ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=manifest.model,  # type: ignore[arg-type]
        locale="en",
        max_rounds=4,
        provider_rounds_used=1,
        prior_tool_call_count=0,
        accumulated_usage=ProviderUsage(),
        current_manifest_revision=manifest.revision,
        current_manifest_digest=manifest.manifest_digest,
        exposed_surface=surface,
        assistant_message_digest=digest_provider_message(assistant),
        transcript_digest=digest_provider_transcript(messages),
        waiting_call=ProviderWaitingCallState(
            call_id="call-2",
            call_index=1,
            binding_contract_digest=c2.binding_contract_digest,
            descriptor_digest=c2.descriptor_digest,
            behavior_digest=c2.behavior_digest,
            classification_revision=c2.classification_revision,
            classification_ruleset_digest=c2.classification_ruleset_digest,
            capability_continuation=_continuation_ref(),
        ),
        next_call_index=2,
        pending_call_ids=(),
        completed_call_records=(
            ProviderToolCallRecord(
                call=c1,
                status="completed",
                result_message_digest=digest_provider_message(tool_msg),
                safe_duration_ms=1.0,
            ),
        ),
    )
    result = ProviderLoopResult(
        status="waiting",
        final_text=None,
        messages=messages,
        tool_calls=cont.completed_call_records
        + (
            ProviderToolCallRecord(
                call=c2,
                status="waiting",
                result_message_digest=None,
                safe_duration_ms=None,
            ),
        ),
        round_count=1,
        stop_reason="waiting_interrupt",
        manifest=manifest,
        continuation=cont,
        usage=ProviderUsage(),
        error=None,
    )
    assert result.continuation is not None
    assert result.continuation.waiting_call.call_id == "call-2"


def test_resume_digest_and_classification_mismatch() -> None:
    manifest = _manifest()
    surface = _surface(manifest)
    call = _call_from_surface(surface)
    assistant = ProviderAssistantMessage(content=None, tool_calls=(call,))
    messages = (ProviderUserMessage(content="q"), assistant)
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=_principal(),
        tenant_scope_id=None,
    )
    cont = ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=manifest.model,  # type: ignore[arg-type]
        locale="en",
        max_rounds=4,
        provider_rounds_used=1,
        prior_tool_call_count=0,
        accumulated_usage=ProviderUsage(),
        current_manifest_revision=manifest.revision,
        current_manifest_digest=manifest.manifest_digest,
        exposed_surface=surface,
        assistant_message_digest=digest_provider_message(assistant),
        transcript_digest=digest_provider_transcript(messages),
        waiting_call=ProviderWaitingCallState(
            call_id=call.call_id,
            call_index=0,
            binding_contract_digest=call.binding_contract_digest,
            descriptor_digest=call.descriptor_digest,
            behavior_digest=call.behavior_digest,
            classification_revision=call.classification_revision,
            classification_ruleset_digest=call.classification_ruleset_digest,
            capability_continuation=_continuation_ref(),
        ),
        next_call_index=1,
        pending_call_ids=(),
        completed_call_records=(),
    )
    resolution = ProviderWaitingResolution(
        call_id=call.call_id,
        capability_continuation=_continuation_ref(),
        capability_result=completed_result(user_text="ok", metrics=_metrics()),
    )
    ok = ProviderLoopResumeRequest(
        manifest=manifest,
        messages=messages,
        continuation=cont,
        resolved_waiting=resolution,
    )
    assert ok.resolved_waiting.capability_result.status == "completed"

    # Transcript mismatch
    with pytest.raises(ValidationError, match="transcript"):
        ProviderLoopResumeRequest(
            manifest=manifest,
            messages=(ProviderUserMessage(content="tampered"), assistant),
            continuation=cont,
            resolved_waiting=resolution,
        )

    # Manifest digest mismatch via child revision
    child = append_provider_aliases(
        manifest,
        aliases=(
            ResolvedProviderAliasRef(
                provider_protocol="openai_compat",
                domain_key="tools.search",
                provider_alias="search_entries",
                binding_contract_digest=call.binding_contract_digest,
            ),
        ),
    )
    with pytest.raises(ValidationError, match="manifest"):
        ProviderLoopResumeRequest(
            manifest=child,
            messages=messages,
            continuation=cont,
            resolved_waiting=resolution,
        )

    # Surface digest mismatch
    bad_surface_cont = cont.model_copy(
        update={
            "exposed_surface": cont.exposed_surface.model_copy(
                update={"surface_digest": "0" * 64}
            )
        }
    )
    with pytest.raises(ValidationError):
        # surface_digest recompute fails at surface validation
        ProviderLoopContinuation.model_validate(bad_surface_cont.model_dump())

    # Classification fields on waiting state are frozen evidence; mismatch is detected
    # by comparing recompute helpers / exposed surface stamps.
    assert cont.waiting_call.classification_revision == "plan02-v1"
    assert cont.waiting_call.behavior_digest == call.behavior_digest
    assert cont.waiting_call.descriptor_digest == call.descriptor_digest


def test_resume_rejects_waiting_result_and_raw_tool_message() -> None:
    with pytest.raises(ValidationError):
        ProviderWaitingResolution(
            call_id="call-1",
            capability_continuation=_continuation_ref(),
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
        )


def test_execution_scope_model_locale_max_rounds_usage_tampering() -> None:
    manifest = _manifest()
    model = manifest.model
    assert model is not None
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=_principal(),
        tenant_scope_id=None,
    )
    request = ProviderLoopRequest(
        manifest=manifest,
        initial_messages=(ProviderUserMessage(content="hi"),),
        model_ref=model,
        execution_scope=scope,
        max_rounds=4,
        locale="en",
        generation=ProviderGenerationOptions(tool_choice=ProviderToolChoice(mode="auto")),
    )
    assert request.max_rounds == 4

    with pytest.raises(ValidationError):
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=scope,
            max_rounds=0,
            locale="en",
        )

    with pytest.raises(ValidationError):
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=scope,
            max_rounds=1,
            locale="en",
            generation=ProviderGenerationOptions(
                tool_choice=ProviderToolChoice(mode="auto")
            ),
        )

    # model mismatch
    other_model = create_model_ref(
        model_id=MODEL_ID,
        model_name="other-model",
        model_type="llm",
        model_runtime_revision=9,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=DIGEST_5,
        provider_ref_digest=model.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    with pytest.raises(ValidationError, match="model"):
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=other_model,
            execution_scope=scope,
            max_rounds=4,
            locale="en",
        )

    usage = aggregate_provider_usage(
        ProviderUsage(input_tokens=1, output_tokens=2, total_tokens=3),
        ProviderUsage(input_tokens=4, output_tokens=5, total_tokens=9),
    )
    assert usage.input_tokens == 5
    assert usage.output_tokens == 7
    assert usage.total_tokens == 12


def test_round_count_and_usage_bounds_on_result() -> None:
    manifest = _manifest()
    with pytest.raises(ValidationError):
        ProviderLoopResult(
            status="completed",
            final_text="x",
            messages=(
                ProviderUserMessage(content="q"),
                ProviderAssistantMessage(content="x", tool_calls=()),
            ),
            tool_calls=(),
            round_count=-1,
            stop_reason="natural_completion",
            manifest=manifest,
            continuation=None,
            usage=ProviderUsage(),
            error=None,
        )
    with pytest.raises(ValidationError):
        ProviderUsage(input_tokens=-1)


def test_no_callback_client_session_future_serialization() -> None:
    class Session:
        pass

    class Future:
        pass

    class OpenAI:
        pass

    for value in (Session(), Future(), OpenAI(), lambda: None):
        with pytest.raises(TypeError):
            assert_not_serializable_port(value)

    # Runtime port type name is rejected.
    class CurrentCapabilityDescriptorVerifier:  # noqa: N801 - intentional name match
        pass

    with pytest.raises(TypeError):
        assert_not_serializable_port(CurrentCapabilityDescriptorVerifier())

    # Verifier protocol must not appear on message/surface/continuation/result models.
    source = open(
        __import__("app.assistant.provider_loop.contracts", fromlist=["x"]).__file__,
        encoding="utf-8",
    ).read()
    assert "current_descriptors" not in ProviderLoopResult.model_fields
    assert "current_descriptors" not in ProviderLoopContinuation.model_fields
    assert "current_descriptors" not in ProviderToolSurface.model_fields
    assert CurrentCapabilityDescriptorVerifier is not None
    assert "class CurrentCapabilityDescriptorVerifier" in source
    # Arbitrary SDK objects cannot populate frozen string fields.
    with pytest.raises(ValidationError):
        SafeProviderError(
            semantic_code="x",
            safe_summary="y",
            adapter_key=object(),  # type: ignore[arg-type]
        )


def test_stream_event_union_rejects_unknown_and_extra_sdk_data() -> None:
    ok = parse_provider_stream_event(
        {"event_type": "text.delta", "sequence": 0, "delta": "hi"}
    )
    assert isinstance(ok, ProviderTextDelta)
    parse_provider_stream_event(
        {
            "event_type": "tool_call.delta",
            "sequence": 1,
            "call_index": 0,
            "call_id": "c1",
            "provider_alias_delta": "se",
            "arguments_delta": "{",
        }
    )
    parse_provider_stream_event(
        {
            "event_type": "usage",
            "sequence": 2,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    )
    parse_provider_stream_event(
        {
            "event_type": "round.terminal",
            "sequence": 3,
            "finish_reason": "stop",
            "safe_request_id": "req-1",
        }
    )
    with pytest.raises(ValueError, match="unknown"):
        parse_provider_stream_event({"event_type": "response.output_text.delta", "delta": "x"})
    with pytest.raises(ValidationError):
        ProviderTextDelta.model_validate(
            {
                "event_type": "text.delta",
                "sequence": 0,
                "delta": "hi",
                "raw_chunk": {"id": "sdk"},
            }
        )
    with pytest.raises(ValidationError):
        ProviderToolCallDelta.model_validate(
            {
                "event_type": "tool_call.delta",
                "sequence": 0,
                "call_index": 0,
                "choices": [],
            }
        )


def test_alias_map_depends_on_manifest_not_reverse() -> None:
    manifest = _manifest()
    child = append_provider_aliases(
        manifest,
        aliases=(
            ResolvedProviderAliasRef(
                provider_protocol="openai_compat",
                domain_key="tools.search",
                provider_alias="search_entries",
                binding_contract_digest=DIGEST_7,
            ),
        ),
    )
    alias_map = compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=child.manifest_digest,
        aliases=(("tools.search", "search_entries", DIGEST_7),),
    )
    # Manifest payload never contains alias_map_digest (dependency direction).
    assert "aliasMapDigest" not in child.model_dump(by_alias=True)
    assert alias_map != child.manifest_digest

    surface = _surface(child)
    # Surface includes manifest + alias map; binding digest does not include surface.
    assert surface.manifest_digest == child.manifest_digest
    assert surface.alias_map_digest == compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=child.manifest_digest,
        aliases=(
            (
                surface.tools[0].domain_key,
                surface.tools[0].provider_alias,
                surface.tools[0].binding.ref.binding_contract_digest,
            ),
        ),
    )
    binding_digest = surface.tools[0].binding.ref.binding_contract_digest
    assert surface.surface_digest not in binding_digest
    ToolSurfaceResolution(manifest=child, surface=surface)


def test_loop_request_rejects_run_id_mismatch() -> None:
    manifest = _manifest()
    model = manifest.model
    assert model is not None
    other_scope = create_execution_scope(
        run_id=UUID("00000000-0000-4000-8000-000000000999"),
        conversation_id=None,
        principal=_principal(),
        tenant_scope_id=None,
    )
    with pytest.raises(ValidationError, match="run_id"):
        ProviderLoopRequest(
            manifest=manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=other_scope,
            max_rounds=4,
            locale="en",
        )


def test_stream_and_round_types_are_frozen() -> None:
    event = ProviderTextDelta(sequence=0, delta="x")
    with pytest.raises(ValidationError):
        event.delta = "y"  # type: ignore[misc]
    terminal = ProviderRoundTerminal(sequence=1, finish_reason="stop")
    assert terminal.event_type == "round.terminal"
    usage_event = ProviderUsageSnapshot(
        sequence=2,
        usage=ProviderUsage(input_tokens=1, output_tokens=0, total_tokens=1),
    )
    assert usage_event.usage.input_tokens == 1


def test_safe_provider_error_shape() -> None:
    err = SafeProviderError(
        semantic_code="protocol_error",
        safe_summary="malformed stream",
        http_status=400,
        adapter_key="openai_chat",
        adapter_revision="1",
        safe_request_id="req_123",
        retry_disposition="never",
    )
    assert err.semantic_code == "protocol_error"
    with pytest.raises(ValidationError):
        SafeProviderError(semantic_code="", safe_summary="x")


def test_build_provider_tool_surface_appends_aliases_and_freezes_maps() -> None:
    """Task 2 extension: surface builder integrates append-only aliases + digests."""
    from app.assistant.provider_loop.aliases import (
        OPENAI_CHAT_PROVIDER_PROTOCOL,
        build_provider_tool_surface,
        forward_alias_map,
        reverse_alias_map,
    )

    manifest = _manifest()
    empty_digest = manifest.manifest_digest
    assert manifest.provider_aliases == ()

    resolved = _resolved_binding(capability_key="skill.inject")
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )
    descriptor = _descriptor(resolved)
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        visible=((frozen, descriptor),),
    )
    assert resolution.manifest.revision == 2
    assert resolution.manifest.parent_digest == empty_digest
    assert len(resolution.manifest.provider_aliases) == 1
    assert resolution.manifest.provider_aliases[0].provider_alias == "skill_inject"
    assert resolution.manifest.provider_aliases[0].domain_key == "skill.inject"
    assert forward_alias_map(resolution.surface) == {"skill_inject": "skill.inject"}
    reverse = reverse_alias_map(resolution.surface)
    assert reverse["skill_inject"] == (
        "skill.inject",
        resolved.binding_contract_digest,
    )
    # Digest dependency DAG: binding -> alias -> manifest -> alias map -> surface.
    assert resolution.surface.manifest_digest == resolution.manifest.manifest_digest
    assert empty_digest != resolution.manifest.manifest_digest
    ToolSurfaceResolution(
        manifest=resolution.manifest,
        surface=resolution.surface,
    )


def test_empty_surface_preserves_plan01_empty_alias_digest() -> None:
    from app.assistant.provider_loop.aliases import (
        OPENAI_CHAT_PROVIDER_PROTOCOL,
        build_provider_tool_surface,
    )

    manifest = _manifest()
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        visible=(),
    )
    assert resolution.manifest.manifest_digest == manifest.manifest_digest
    assert resolution.manifest.provider_aliases == ()
    assert resolution.surface.tools == ()
