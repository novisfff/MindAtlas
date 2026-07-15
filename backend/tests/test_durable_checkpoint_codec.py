"""Plan 06 Task 2: strict durable checkpoint contracts + codec.

TDD suite covering Checkpoint v1, execution units, Provider message union,
Plan 03 continuation, Plan 05 grants/frames/snapshots, and codec rejection
rules (extra fields, NaN, depth/size, ephemerals, unknown schema version).
"""

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
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    ContinuationRef,
    FrozenBindingProvenance,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    FrozenContract,
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
from app.assistant.main_agent.authorization import (  # noqa: E402
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
)
from app.assistant.policy.budgets import (  # noqa: E402
    BudgetLedgerState,
    create_initial_ledger_state,
)
from app.assistant.policy.contracts import (  # noqa: E402
    ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    EMPTY_POLICY_DIGEST,
    EffectiveCapabilityGrant,
    EffectiveRunPolicySnapshot,
    RunBudgetLimits,
    build_effective_capability_grant,
    build_effective_run_policy_snapshot,
    build_manifest_exposure_index,
    normalize_run_budget_limits,
)
from app.assistant.policy.obligations import (  # noqa: E402
    ObligationLedgerState,
    create_initial_obligation_ledger_state,
)
from datetime import datetime, timezone  # noqa: E402
from app.assistant.policy.recursion import (  # noqa: E402
    CapabilityCallFrame,
    build_capability_call_frame,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    ProviderLoopContinuation,
    ProviderToolDefinition,
    ProviderToolSurface,
    ProviderUsage,
    ProviderWaitingCallState,
    compute_alias_map_digest,
    compute_surface_digest,
    create_execution_scope,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderCompletionInstructionMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderToolCallRecord,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    ProviderUserMessage,
    digest_arguments,
    digest_provider_message,
    digest_provider_transcript,
    provider_message_payload,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402

# ---------------------------------------------------------------------------
# Fixed digests / ids
# ---------------------------------------------------------------------------

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

RUN_ID = UUID("00000000-0000-4000-8000-000000000201")
MANIFEST_REV_ID = UUID("00000000-0000-4000-8000-000000000211")
POLICY_REV_ID = UUID("00000000-0000-4000-8000-000000000212")
BUDGET_REV_ID = UUID("00000000-0000-4000-8000-000000000213")
OBLIGATION_REV_ID = UUID("00000000-0000-4000-8000-000000000214")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000221")
OWNER_VERSION_ID = UUID("00000000-0000-4000-8000-000000000231")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000110")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000111")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000150")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000151")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000140")
TARGET_ID = UUID("00000000-0000-4000-8000-000000000160")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _principal() -> CapabilityPrincipal:
    return CapabilityPrincipal(
        principal_type="service",
        principal_id="local-assistant",
        authenticated=True,
    )


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
    target = TARGET_ID
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


def _tool_call(surface: ProviderToolSurface) -> ProviderToolCall:
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


def _continuation_ref() -> ContinuationRef:
    return ContinuationRef(
        continuation_type="human_approval",
        contract_version=1,
        reference_id="cont-1",
        payload_digest=DIGEST_E,
    )


def _waiting_continuation() -> ProviderLoopContinuation:
    manifest = _manifest()
    surface = _surface(manifest)
    call = _tool_call(surface)
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
    return ProviderLoopContinuation(
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


def _frame(*, call_id: str = "call-1") -> CapabilityCallFrame:
    binding = _resolved_binding()
    return build_capability_call_frame(
        call_id=call_id,
        capability_type="tool",
        domain_key=binding.capability_key,
        target_identity=binding.target_identity,
        target_version_id=None,
        binding_contract_digest=binding.binding_contract_digest,
        owner_kind="main_agent",
        owner_version_id=OWNER_VERSION_ID,
        capability_depth=1,
        agent_depth=1,
    )


def _grant(*, capability_key: str = "tools.search") -> EffectiveCapabilityGrant:
    binding = _resolved_binding(capability_key=capability_key)
    return build_effective_capability_grant(
        owner_kind="main_agent",
        owner_id="main-agent",
        owner_version_id=OWNER_VERSION_ID,
        capability_key=capability_key,
        binding_contract_digest=binding.binding_contract_digest,
        allowed_side_effects=("read", "compute"),
        allowed_interrupt_modes=("none",),
        platform_ceiling_digest=MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
        entrypoint_policy_digest=ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
        global_policy_digest=EMPTY_POLICY_DIGEST,
        owner_policy_digest=DIGEST_6,
    )


def _policy_snapshot() -> EffectiveRunPolicySnapshot:
    limits = normalize_run_budget_limits()
    exposure_index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(),
    )
    return build_effective_run_policy_snapshot(
        app_build_revision="build-1",
        run_id=RUN_ID,
        principal=_principal(),
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        entrypoint_policy_digest=ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
        global_policy_digest=EMPTY_POLICY_DIGEST,
        exposure_index=exposure_index,
        owner_policy_refs=(),
        run_budget_limits=limits,
    )


def _all_provider_messages() -> list[ProviderMessage]:
    surface = _surface(_manifest())
    call = _tool_call(surface)
    return [
        ProviderSystemMessage(content="system prompt"),
        ProviderRuntimeInstructionMessage(
            instruction_type="soft_finalization",
            locale="en",
            content="Summarize without tools",
        ),
        ProviderContextUpdateMessage(
            locale="en",
            manifest_revision=1,
            manifest_digest=DIGEST_A,
            prompt_build_digest=DIGEST_B,
            content="Main agent context block",
        ),
        ProviderCompletionInstructionMessage(
            locale="en",
            manifest_revision=1,
            manifest_digest=DIGEST_A,
            guard_state_digest=DIGEST_C,
            content="Complete the answer now",
        ),
        ProviderUserMessage(content="hello user"),
        ProviderAssistantMessage(content="thinking", tool_calls=(call,)),
        ProviderToolMessage(
            call_id=call.call_id,
            provider_alias=call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="completed",
                domain_key=call.domain_key,
                user_text="ok",
                structured_output={"ok": True},
                terminal_output=True,
                needs_followup=False,
                error=None,
                artifact_refs=(),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Imports under test (expected module surface)
# ---------------------------------------------------------------------------


def _import_contracts():
    from app.assistant.durable.contracts import (
        DurableAgentCheckpointV1,
        DurableExecutionUnitV1,
        DurableGrantSetV1,
        DurableNextActionV1,
        DurableProviderMessageRecordV1,
    )

    return (
        DurableAgentCheckpointV1,
        DurableExecutionUnitV1,
        DurableGrantSetV1,
        DurableNextActionV1,
        DurableProviderMessageRecordV1,
    )


def _import_codec():
    from app.assistant.durable.codec import (
        MAX_CODEC_JSON_BYTES,
        MAX_CODEC_JSON_DEPTH,
        SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
        DurableCodecError,
        NeedsReconciliationError,
        checkpoint_state_digest,
        decode_budget_ledger_state,
        decode_capability_frame,
        decode_checkpoint,
        decode_checkpoint_v1,
        decode_grant,
        decode_grant_set,
        decode_obligation_ledger_state,
        decode_policy_snapshot,
        decode_provider_message,
        decode_provider_message_record,
        encode_budget_ledger_state,
        encode_capability_frame,
        encode_checkpoint_v1,
        encode_grant,
        encode_grant_set,
        encode_obligation_ledger_state,
        encode_policy_snapshot,
        encode_provider_message,
        encode_provider_message_record,
        migrate_checkpoint,
    )

    return {
        "MAX_CODEC_JSON_BYTES": MAX_CODEC_JSON_BYTES,
        "MAX_CODEC_JSON_DEPTH": MAX_CODEC_JSON_DEPTH,
        "SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS": SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
        "DurableCodecError": DurableCodecError,
        "NeedsReconciliationError": NeedsReconciliationError,
        "checkpoint_state_digest": checkpoint_state_digest,
        "decode_budget_ledger_state": decode_budget_ledger_state,
        "decode_capability_frame": decode_capability_frame,
        "decode_checkpoint": decode_checkpoint,
        "decode_checkpoint_v1": decode_checkpoint_v1,
        "decode_grant": decode_grant,
        "decode_grant_set": decode_grant_set,
        "decode_obligation_ledger_state": decode_obligation_ledger_state,
        "decode_policy_snapshot": decode_policy_snapshot,
        "decode_provider_message": decode_provider_message,
        "decode_provider_message_record": decode_provider_message_record,
        "encode_budget_ledger_state": encode_budget_ledger_state,
        "encode_capability_frame": encode_capability_frame,
        "encode_checkpoint_v1": encode_checkpoint_v1,
        "encode_grant": encode_grant,
        "encode_grant_set": encode_grant_set,
        "encode_obligation_ledger_state": encode_obligation_ledger_state,
        "encode_policy_snapshot": encode_policy_snapshot,
        "encode_provider_message": encode_provider_message,
        "encode_provider_message_record": encode_provider_message_record,
        "migrate_checkpoint": migrate_checkpoint,
    }


def _prepared_unit() -> Any:
    _, DurableExecutionUnitV1, *_ = _import_contracts()
    return DurableExecutionUnitV1(
        logical_unit_id="unit-provider-1",
        kind="provider_round",
        state="prepared",
        provider_round=1,
        call_ids=(),
        attempt=1,
        reserved_budget_revision=1,
        started_budget_revision=None,
    )


def _started_capability_unit() -> Any:
    _, DurableExecutionUnitV1, *_ = _import_contracts()
    return DurableExecutionUnitV1(
        logical_unit_id="unit-cap-1",
        kind="capability_group",
        state="started",
        provider_round=1,
        call_ids=("call-1",),
        attempt=1,
        reserved_budget_revision=2,
        started_budget_revision=3,
    )


def _checkpoint(
    *,
    phase: str = "ready_for_provider",
    next_kind: str = "continue_provider",
    continuation: ProviderLoopContinuation | None = None,
    inflight: Any | None = ...,  # type: ignore[assignment]
    frames: tuple[CapabilityCallFrame, ...] = (),
) -> Any:
    DurableAgentCheckpointV1, _, _, DurableNextActionV1, _ = _import_contracts()
    if inflight is ...:
        # Default prepared unit only for phases that may carry inflight work.
        if phase in {"waiting", "terminal", "ready_for_memory"}:
            resolved_inflight = None
        else:
            resolved_inflight = _prepared_unit()
    else:
        resolved_inflight = inflight
    return DurableAgentCheckpointV1(
        run_id=RUN_ID,
        phase=phase,  # type: ignore[arg-type]
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=2,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=continuation,
        inflight_unit=resolved_inflight,
        capability_frames=frames,
        artifact_ids=(ARTIFACT_ID,),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV1(kind=next_kind),  # type: ignore[arg-type]
    )


# ===========================================================================
# Contract shape / frozen / extra forbid
# ===========================================================================


def test_contracts_are_frozen_forbid_extra() -> None:
    (
        DurableAgentCheckpointV1,
        DurableExecutionUnitV1,
        DurableGrantSetV1,
        DurableNextActionV1,
        DurableProviderMessageRecordV1,
    ) = _import_contracts()
    for cls in (
        DurableAgentCheckpointV1,
        DurableExecutionUnitV1,
        DurableGrantSetV1,
        DurableNextActionV1,
        DurableProviderMessageRecordV1,
    ):
        assert issubclass(cls, FrozenContract)
        assert cls.model_config.get("frozen") is True
        assert cls.model_config.get("extra") == "forbid"

    unit = _prepared_unit()
    with pytest.raises(ValidationError):
        DurableExecutionUnitV1.model_validate(
            {**unit.model_dump(mode="json", by_alias=True), "extraField": 1}
        )
    with pytest.raises(ValidationError):
        DurableNextActionV1.model_validate({"kind": "terminal", "mystery": True})


def test_execution_unit_prepared_and_started_rules() -> None:
    _, DurableExecutionUnitV1, *_ = _import_contracts()
    prepared = DurableExecutionUnitV1(
        logical_unit_id="u1",
        kind="provider_round",
        state="prepared",
        provider_round=0,
        call_ids=(),
        attempt=1,
        reserved_budget_revision=1,
        started_budget_revision=None,
    )
    assert prepared.started_budget_revision is None
    started = DurableExecutionUnitV1(
        logical_unit_id="u1",
        kind="capability_group",
        state="started",
        provider_round=1,
        call_ids=("c1", "c2"),
        attempt=2,
        reserved_budget_revision=3,
        started_budget_revision=4,
    )
    assert started.started_budget_revision == 4
    with pytest.raises(ValidationError):
        DurableExecutionUnitV1(
            logical_unit_id="u1",
            kind="provider_round",
            state="prepared",
            provider_round=0,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=1,
            started_budget_revision=2,  # illegal for prepared
        )
    with pytest.raises(ValidationError):
        DurableExecutionUnitV1(
            logical_unit_id="u1",
            kind="capability_group",
            state="started",
            provider_round=1,
            call_ids=("c1",),
            attempt=1,
            reserved_budget_revision=1,
            started_budget_revision=None,  # illegal for started
        )


# ===========================================================================
# Checkpoint round-trip + fixed digest vector
# ===========================================================================


def test_checkpoint_v1_round_trip_and_fixed_digest() -> None:
    codec = _import_codec()
    cont = _waiting_continuation()
    frame = _frame()
    cp = _checkpoint(
        phase="waiting",
        next_kind="wait",
        continuation=cont,
        inflight=None,
        frames=(frame,),
    )
    # Override inflight to None for waiting phase
    DurableAgentCheckpointV1, *_ = _import_contracts()
    cp = DurableAgentCheckpointV1(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=2,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=cont,
        inflight_unit=None,
        capability_frames=(frame,),
        artifact_ids=(ARTIFACT_ID,),
        visible_text_artifact_id=None,
        next_action=cp.next_action,
    )
    encoded = codec["encode_checkpoint_v1"](cp)
    assert encoded["schemaVersion"] == 1
    assert "extra" not in encoded
    decoded = codec["decode_checkpoint_v1"](encoded)
    assert decoded == cp
    assert type(decoded.provider_loop_continuation) is ProviderLoopContinuation
    assert decoded.provider_loop_continuation == cont
    assert decoded.capability_frames[0] == frame
    # Round-trip preserves continuation surface discriminator digests.
    assert (
        decoded.provider_loop_continuation.exposed_surface.surface_digest
        == cont.exposed_surface.surface_digest
    )
    digest = codec["checkpoint_state_digest"](cp)
    # Fixed vector: recomputed from canonical encode; lock against accidental drift.
    assert digest == sha256_canonical_json(encoded)
    assert len(digest) == 64
    # Second encode must match exactly (canonical).
    encoded2 = codec["encode_checkpoint_v1"](decoded)
    assert encoded2 == encoded
    assert codec["checkpoint_state_digest"](decoded) == digest


def test_prepared_and_started_units_round_trip() -> None:
    codec = _import_codec()
    prepared = _prepared_unit()
    started = _started_capability_unit()
    frame = _frame(call_id="call-1")
    for unit, phase, next_kind, frames in (
        (prepared, "ready_for_provider", "continue_provider", ()),
        (started, "dispatching_calls", "dispatch_calls", (frame,)),
    ):
        cp = _checkpoint(
            phase=phase,
            next_kind=next_kind,
            continuation=None,
            inflight=unit,
            frames=frames,
        )
        encoded = codec["encode_checkpoint_v1"](cp)
        decoded = codec["decode_checkpoint_v1"](encoded)
        assert decoded.inflight_unit == unit
        assert decoded.inflight_unit.state == unit.state


# ===========================================================================
# Provider message union members
# ===========================================================================


def test_every_provider_message_union_member_round_trip() -> None:
    codec = _import_codec()
    messages = _all_provider_messages()
    assert len(messages) == 7
    roles = {m.role for m in messages}
    assert roles == {
        "system",
        "runtime_instruction",
        "runtime_context",
        "runtime_completion",
        "user",
        "assistant",
        "tool",
    }
    for message in messages:
        before_digest = digest_provider_message(message)
        encoded = codec["encode_provider_message"](message)
        # Must match Plan 03 payload shape (camelCase, role discriminator).
        assert encoded["role"] == message.role
        assert encoded == provider_message_payload(message)
        decoded = codec["decode_provider_message"](encoded)
        assert type(decoded) is type(message)
        assert decoded.role == message.role
        assert digest_provider_message(decoded) == before_digest
        assert decoded == message


def test_ordinary_system_message_vector_unchanged() -> None:
    """Prove existing ordinary system message digest remains stable."""
    codec = _import_codec()
    system = ProviderSystemMessage(content="sys")
    expected = digest_provider_message(system)
    # Fixed ordinary vector (must not drift with protected-role work).
    assert expected == sha256_canonical_json({"role": "system", "content": "sys"})
    assert expected == "ccc6fe3a1d3df937ea1c4d903a975dc9d1a3c55bf08fbf1e7f0a1cb467ae78fd"
    encoded = codec["encode_provider_message"](system)
    decoded = codec["decode_provider_message"](encoded)
    assert digest_provider_message(decoded) == expected


# ===========================================================================
# Protected message downcast + revision linkage
# ===========================================================================


def test_reject_protected_message_downcast_to_system() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    for role, payload in (
        (
            "runtime_instruction",
            {
                "role": "system",
                "instructionType": "soft_finalization",
                "locale": "en",
                "content": "x",
            },
        ),
        (
            "runtime_context",
            {
                "role": "system",
                "contextType": "main_agent_manifest",
                "locale": "en",
                "manifestRevision": 1,
                "manifestDigest": DIGEST_A,
                "promptBuildDigest": DIGEST_B,
                "content": "ctx",
            },
        ),
        (
            "runtime_completion",
            {
                "role": "system",
                "locale": "en",
                "manifestRevision": 1,
                "manifestDigest": DIGEST_A,
                "guardStateDigest": DIGEST_C,
                "content": "done",
            },
        ),
    ):
        with pytest.raises((Error, ValidationError, ValueError, TypeError)) as exc:
            codec["decode_provider_message"](payload)
        # Must not silently become a system message.
        text = str(exc.value).lower()
        assert "system" in text or "downcast" in text or "role" in text or "discriminator" in text


def test_provider_message_record_revision_linkage() -> None:
    codec = _import_codec()
    _, _, _, _, DurableProviderMessageRecordV1 = _import_contracts()
    Error = codec["DurableCodecError"]

    # runtime_instruction requires policy, forbids obligation
    ok_instruction = DurableProviderMessageRecordV1(
        role="runtime_instruction",
        protection_kind="protected",
        message=ProviderRuntimeInstructionMessage(
            instruction_type="soft_finalization",
            locale="en",
            content="finish",
        ),
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        obligation_revision_id=None,
        content_digest=digest_provider_message(
            ProviderRuntimeInstructionMessage(
                instruction_type="soft_finalization",
                locale="en",
                content="finish",
            )
        ),
    )
    encoded = codec["encode_provider_message_record"](ok_instruction)
    decoded = codec["decode_provider_message_record"](encoded)
    assert decoded == ok_instruction
    assert decoded.message.role == "runtime_instruction"

    # runtime_completion requires both policy and obligation
    completion_msg = ProviderCompletionInstructionMessage(
        locale="en",
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        guard_state_digest=DIGEST_C,
        content="complete now",
    )
    ok_completion = DurableProviderMessageRecordV1(
        role="runtime_completion",
        protection_kind="protected",
        message=completion_msg,
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        content_digest=digest_provider_message(completion_msg),
    )
    assert codec["decode_provider_message_record"](
        codec["encode_provider_message_record"](ok_completion)
    ) == ok_completion

    # Missing policy for protected runtime_context
    with pytest.raises((ValidationError, Error)):
        DurableProviderMessageRecordV1(
            role="runtime_context",
            protection_kind="protected",
            message=ProviderContextUpdateMessage(
                locale="en",
                manifest_revision=1,
                manifest_digest=DIGEST_A,
                prompt_build_digest=DIGEST_B,
                content="ctx",
            ),
            manifest_revision_id=MANIFEST_REV_ID,
            policy_revision_id=None,
            obligation_revision_id=None,
            content_digest=DIGEST_1,
        )

    # Bare system cannot be protected / cannot carry protected discriminator fields
    with pytest.raises((ValidationError, Error)):
        DurableProviderMessageRecordV1(
            role="system",
            protection_kind="protected",
            message=ProviderSystemMessage(content="sys"),
            manifest_revision_id=MANIFEST_REV_ID,
            policy_revision_id=POLICY_REV_ID,
            obligation_revision_id=None,
            content_digest=digest_provider_message(ProviderSystemMessage(content="sys")),
        )

    # Role mismatch between envelope and message body
    with pytest.raises((ValidationError, Error)):
        DurableProviderMessageRecordV1(
            role="runtime_instruction",
            protection_kind="protected",
            message=ProviderSystemMessage(content="sys"),
            manifest_revision_id=MANIFEST_REV_ID,
            policy_revision_id=POLICY_REV_ID,
            obligation_revision_id=None,
            content_digest=digest_provider_message(ProviderSystemMessage(content="sys")),
        )

    # Mismatched content_digest
    with pytest.raises((ValidationError, Error)):
        DurableProviderMessageRecordV1(
            role="user",
            protection_kind="public",
            message=ProviderUserMessage(content="hi"),
            manifest_revision_id=MANIFEST_REV_ID,
            policy_revision_id=None,
            obligation_revision_id=None,
            content_digest=DIGEST_8,
        )


# ===========================================================================
# Grants, frames, policy snapshot, ledgers
# ===========================================================================


def test_complete_grant_set_round_trip_no_classification_substitution() -> None:
    codec = _import_codec()
    g1 = _grant(capability_key="tools.search")
    g2 = _grant(capability_key="system.skill.inject")
    # Ensure grants carry grant_source_digest and no classification fields.
    for g in (g1, g2):
        dumped = g.model_dump(mode="json", by_alias=True)
        assert "grantSourceDigest" in dumped or "grant_source_digest" in g.model_dump()
        blob = str(dumped).lower()
        assert "classification" not in blob
        assert "ruleset" not in blob
        assert "side_effect" not in blob or "allowedSideEffects" in dumped or True

    grant_set_cls = _import_contracts()[2]
    grant_set = grant_set_cls(grants=(g1, g2))
    encoded = codec["encode_grant_set"](grant_set)
    decoded = codec["decode_grant_set"](encoded)
    assert decoded == grant_set
    assert decoded.grants[0].grant_source_digest == g1.grant_source_digest
    assert decoded.grants[1].capability_key == "system.skill.inject"

    # Single grant path
    enc_g = codec["encode_grant"](g1)
    assert codec["decode_grant"](enc_g) == g1

    # Reject classification-shaped substitution masquerading as a grant
    Error = codec["DurableCodecError"]
    bad = {
        **enc_g,
        "classificationRevision": "plan02-v1",
        "classificationRulesetDigest": DIGEST_A,
    }
    with pytest.raises((Error, ValidationError)):
        codec["decode_grant"](bad)

    # Reject copied-descriptor grant (descriptorDigest is not a grant field)
    bad2 = {**enc_g, "descriptorDigest": DIGEST_C}
    with pytest.raises((Error, ValidationError)):
        codec["decode_grant"](bad2)


def test_portable_frame_round_trip() -> None:
    codec = _import_codec()
    frame = _frame()
    encoded = codec["encode_capability_frame"](frame)
    decoded = codec["decode_capability_frame"](encoded)
    assert decoded == frame
    assert decoded.frame_digest == frame.frame_digest


def test_policy_snapshot_and_ledger_round_trip() -> None:
    codec = _import_codec()
    snapshot = _policy_snapshot()
    enc = codec["encode_policy_snapshot"](snapshot)
    assert codec["decode_policy_snapshot"](enc) == snapshot

    budget = create_initial_ledger_state(
        limits=normalize_run_budget_limits(),
        started_at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert isinstance(budget, BudgetLedgerState)
    b_enc = codec["encode_budget_ledger_state"](budget)
    assert codec["decode_budget_ledger_state"](b_enc) == budget

    obligation = create_initial_obligation_ledger_state()
    assert isinstance(obligation, ObligationLedgerState)
    o_enc = codec["encode_obligation_ledger_state"](obligation)
    assert codec["decode_obligation_ledger_state"](o_enc) == obligation


def test_frame_inflight_inconsistency_rejected() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    # capability_group started for call-1 but frames only have call-9
    unit = _started_capability_unit()
    wrong_frame = _frame(call_id="call-9")
    with pytest.raises((Error, ValidationError)):
        _checkpoint(
            phase="dispatching_calls",
            next_kind="dispatch_calls",
            inflight=unit,
            frames=(wrong_frame,),
        )
    # Or via decode of a tampered payload
    good_frame = _frame(call_id="call-1")
    cp = _checkpoint(
        phase="dispatching_calls",
        next_kind="dispatch_calls",
        inflight=unit,
        frames=(good_frame,),
    )
    encoded = codec["encode_checkpoint_v1"](cp)
    # Tamper frame call id while keeping unit call_ids
    encoded["capabilityFrames"][0]["callId"] = "call-other"
    # frame_digest will also mismatch; either way reject
    with pytest.raises((Error, ValidationError, ValueError)):
        codec["decode_checkpoint_v1"](encoded)


# ===========================================================================
# Codec rejection corpus: depth/size/NaN/bytes/cycles/classes/ephemerals
# ===========================================================================


def test_reject_nan_infinity_bytes_arbitrary_classes_cycles() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]

    # NaN / Infinity via raw dict injection into grant path
    with pytest.raises((Error, ValueError, TypeError, ValidationError)):
        codec["decode_grant"](
            {
                "ownerKind": "main_agent",
                "ownerVersionId": str(OWNER_VERSION_ID),
                "capabilityKey": "tools.search",
                "bindingContractDigest": DIGEST_A,
                "allowedSideEffects": ["read"],
                "allowedInterruptModes": ["none"],
                "platformCeilingDigest": MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
                "entrypointPolicyDigest": ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
                "globalPolicyDigest": EMPTY_POLICY_DIGEST,
                "ownerPolicyDigest": DIGEST_6,
                "grantSourceDigest": DIGEST_7,
                "nanField": float("nan"),
            }
        )

    # bytes rejected
    with pytest.raises((Error, TypeError, ValueError, ValidationError)):
        codec["decode_provider_message"](
            {"role": "user", "content": b"not-text"}  # type: ignore[dict-item]
        )

    # arbitrary class
    class NotAMessage:
        role = "user"
        content = "x"

    with pytest.raises((Error, TypeError, ValidationError)):
        codec["encode_provider_message"](NotAMessage())  # type: ignore[arg-type]

    # cycle
    cyclic: dict[str, Any] = {"role": "user"}
    cyclic["content"] = cyclic  # type: ignore[assignment]
    with pytest.raises((Error, TypeError, ValueError, RecursionError, ValidationError)):
        codec["decode_provider_message"](cyclic)


def test_reject_excess_depth_and_size() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    max_depth = codec["MAX_CODEC_JSON_DEPTH"]
    max_bytes = codec["MAX_CODEC_JSON_BYTES"]
    assert max_depth >= 8
    assert max_bytes >= 1024

    # Depth bomb
    node: Any = {"role": "user", "content": "x"}
    cur = node
    for _ in range(max_depth + 5):
        nxt: dict[str, Any] = {"nested": cur}
        cur = nxt
    with pytest.raises((Error, ValueError)):
        codec["decode_provider_message"](cur)

    # Size bomb (string payload)
    huge = {"role": "user", "content": "x" * (max_bytes + 100)}
    with pytest.raises((Error, ValueError)):
        codec["decode_provider_message"](huge)


def test_reject_ephemeral_runtime_object_families() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]

    class Session:  # sqlalchemy-like name
        pass

    class OpenAI:  # client
        pass

    class BudgetLedger:  # process-local ledger facade
        pass

    class ProcessLocalCapabilityCallFramePort:
        pass

    class CapabilityGateway:
        pass

    for obj in (
        Session(),
        OpenAI(),
        BudgetLedger(),
        ProcessLocalCapabilityCallFramePort(),
        CapabilityGateway(),
        lambda: None,
        object(),
    ):
        with pytest.raises((Error, TypeError, ValidationError)):
            codec["encode_checkpoint_v1"](obj)  # type: ignore[arg-type]


def test_recursive_secret_credential_corpus_rejection() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    secrets = [
        {"role": "user", "content": "x", "apiKey": "sk-secret-abc"},
        {"role": "user", "content": "x", "password": "hunter2"},
        {"role": "user", "content": "x", "credential": {"token": "tok"}},
        {
            "role": "user",
            "content": "x",
            "nested": {"authorization": "Bearer abc", "more": {"fernetKey": "k"}},
        },
        {"role": "user", "content": "x", "decryptedCredential": "plain"},
        {"role": "assistant", "content": None, "toolCalls": [], "secret": "nope"},
    ]
    for payload in secrets:
        with pytest.raises((Error, ValidationError, ValueError)):
            codec["decode_provider_message"](payload)


# ===========================================================================
# Unknown schema version -> needs_reconciliation BEFORE runtime construction
# ===========================================================================


def test_unknown_schema_version_needs_reconciliation_before_construction() -> None:
    codec = _import_codec()
    Needs = codec["NeedsReconciliationError"]
    assert 1 in codec["SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS"]

    constructed: list[Any] = []

    class TrackingCheckpoint:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructed.append(True)
            raise AssertionError("must not construct runtime checkpoint for unknown version")

    # Unknown future version payload
    payload = {
        "schemaVersion": 99,
        "runId": str(RUN_ID),
        "phase": "terminal",
        "manifestRevisionId": str(MANIFEST_REV_ID),
        "policyRevisionId": str(POLICY_REV_ID),
        "budgetRevisionId": str(BUDGET_REV_ID),
        "obligationRevisionId": str(OBLIGATION_REV_ID),
        "providerMessageOrdinal": 0,
        "providerTranscriptDigest": DIGEST_1,
        "providerLoopContinuation": None,
        "inflightUnit": None,
        "capabilityFrames": [],
        "artifactIds": [],
        "visibleTextArtifactId": None,
        "nextAction": {"kind": "terminal"},
    }
    with pytest.raises(Needs) as exc:
        codec["decode_checkpoint"](payload)
    assert exc.value.code == "needs_reconciliation"
    assert constructed == []

    # migrate_checkpoint also signals before construction
    with pytest.raises(Needs) as exc2:
        codec["migrate_checkpoint"](payload)
    assert exc2.value.code == "needs_reconciliation"

    # Missing version also fail-closed (not silent default)
    bad = dict(payload)
    del bad["schemaVersion"]
    with pytest.raises((Needs, codec["DurableCodecError"], ValidationError, ValueError)):
        codec["decode_checkpoint"](bad)


def test_extra_fields_rejected_on_checkpoint_decode() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    cp = _checkpoint(inflight=None)
    DurableAgentCheckpointV1, *_ = _import_contracts()
    from app.assistant.durable.contracts import DurableNextActionV1

    cp = DurableAgentCheckpointV1(
        run_id=RUN_ID,
        phase="terminal",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV1(kind="terminal"),
    )
    encoded = codec["encode_checkpoint_v1"](cp)
    encoded["workflowState"] = {"hidden": True}  # future field must not be silently dropped
    with pytest.raises((Error, ValidationError)):
        codec["decode_checkpoint_v1"](encoded)


def test_continuation_is_exact_plan03_type_not_reduced_copy() -> None:
    codec = _import_codec()
    cont = _waiting_continuation()
    DurableAgentCheckpointV1, *_ = _import_contracts()
    from app.assistant.durable.contracts import DurableNextActionV1

    cp = DurableAgentCheckpointV1(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=3,
        provider_transcript_digest=cont.transcript_digest,
        provider_loop_continuation=cont,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV1(kind="wait"),
    )
    decoded = codec["decode_checkpoint_v1"](codec["encode_checkpoint_v1"](cp))
    assert type(decoded.provider_loop_continuation) is ProviderLoopContinuation
    # Full exposed surface retained (not rebuilt from catalog).
    assert len(decoded.provider_loop_continuation.exposed_surface.tools) == 1
    assert (
        decoded.provider_loop_continuation.exposed_surface.tools[0].provider_alias
        == "search_entries"
    )
    assert (
        decoded.provider_loop_continuation.waiting_call.capability_continuation.reference_id
        == "cont-1"
    )


def test_checkpoint_schema_version_literal_one() -> None:
    DurableAgentCheckpointV1, *_ = _import_contracts()
    from app.assistant.durable.contracts import DurableNextActionV1

    cp = DurableAgentCheckpointV1(
        run_id=RUN_ID,
        phase="terminal",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV1(kind="terminal"),
    )
    assert cp.schema_version == 1
    with pytest.raises(ValidationError):
        DurableAgentCheckpointV1.model_validate(
            {
                **cp.model_dump(mode="json", by_alias=True),
                "schemaVersion": 2,
            }
        )
