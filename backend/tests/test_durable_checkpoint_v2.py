"""Plan 07 Task 1: Checkpoint v2 contracts + lossless v1→v2 migration.

Covers additive V2 fields, unit/action kind expansion, migrate_checkpoint_v1_to_v2
fixed vectors, v1 digest meaning unchanged, BudgetSuspensionStateV1 without altering
Plan 05 BudgetLedgerState fixed vectors, and unknown versions → needs_reconciliation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.policy.budgets import (  # noqa: E402
    compute_ledger_digest,
    create_initial_ledger_state,
)
from app.assistant.policy.contracts import normalize_run_budget_limits  # noqa: E402

DIGEST_1 = "1" * 64
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000201")
MANIFEST_REV_ID = UUID("00000000-0000-4000-8000-000000000211")
POLICY_REV_ID = UUID("00000000-0000-4000-8000-000000000212")
BUDGET_REV_ID = UUID("00000000-0000-4000-8000-000000000213")
OBLIGATION_REV_ID = UUID("00000000-0000-4000-8000-000000000214")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000221")
INTERRUPT_ID = UUID("00000000-0000-4000-8000-000000000706")
FRAME_ID = UUID("00000000-0000-4000-8000-000000000704")
TARGET_ID = UUID("00000000-0000-4000-8000-000000000702")
TARGET_VERSION_ID = UUID("00000000-0000-4000-8000-000000000703")


def _import_v1_contracts():
    from app.assistant.durable.contracts import (
        DurableAgentCheckpointV1,
        DurableExecutionUnitV1,
        DurableNextActionV1,
    )

    return DurableAgentCheckpointV1, DurableExecutionUnitV1, DurableNextActionV1


def _import_v2_contracts():
    from app.assistant.durable.contracts import (
        DurableAgentCheckpointV2,
        DurableExecutionUnitV2,
        DurableNextActionV2,
    )

    return DurableAgentCheckpointV2, DurableExecutionUnitV2, DurableNextActionV2


def _import_codec():
    from app.assistant.durable.codec import (
        SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
        DurableCodecError,
        NeedsReconciliationError,
        checkpoint_state_digest,
        decode_checkpoint,
        decode_checkpoint_v1,
        decode_checkpoint_v2,
        encode_checkpoint_v1,
        encode_checkpoint_v2,
        migrate_checkpoint,
        migrate_checkpoint_v1_to_v2,
    )

    return {
        "SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS": SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS,
        "DurableCodecError": DurableCodecError,
        "NeedsReconciliationError": NeedsReconciliationError,
        "checkpoint_state_digest": checkpoint_state_digest,
        "decode_checkpoint": decode_checkpoint,
        "decode_checkpoint_v1": decode_checkpoint_v1,
        "decode_checkpoint_v2": decode_checkpoint_v2,
        "encode_checkpoint_v1": encode_checkpoint_v1,
        "encode_checkpoint_v2": encode_checkpoint_v2,
        "migrate_checkpoint": migrate_checkpoint,
        "migrate_checkpoint_v1_to_v2": migrate_checkpoint_v1_to_v2,
    }


def _v1_unit(*, kind: str = "provider_round", state: str = "prepared") -> Any:
    _, DurableExecutionUnitV1, _ = _import_v1_contracts()
    return DurableExecutionUnitV1(
        logical_unit_id="unit-1",
        kind=kind,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        provider_round=1 if kind == "provider_round" else None,
        call_ids=(),
        attempt=1,
        reserved_budget_revision=0,
        started_budget_revision=None if state == "prepared" else 0,
    )


def _v1_checkpoint(**overrides: Any) -> Any:
    DurableAgentCheckpointV1, _, DurableNextActionV1 = _import_v1_contracts()
    data = {
        "run_id": RUN_ID,
        "phase": "ready_for_provider",
        "manifest_revision_id": MANIFEST_REV_ID,
        "policy_revision_id": POLICY_REV_ID,
        "budget_revision_id": BUDGET_REV_ID,
        "obligation_revision_id": OBLIGATION_REV_ID,
        "provider_message_ordinal": 0,
        "provider_transcript_digest": DIGEST_1,
        "provider_loop_continuation": None,
        "inflight_unit": _v1_unit(),
        "capability_frames": (),
        "artifact_ids": (),
        "visible_text_artifact_id": None,
        "next_action": DurableNextActionV1(kind="continue_provider"),
    }
    data.update(overrides)
    return DurableAgentCheckpointV1(**data)


def _workflow_state_fixture() -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableCallFrameV1,
        DurableWorkflowStateV1,
    )

    frame = DurableCallFrameV1(
        frame_id=FRAME_ID,
        parent_frame_id=None,
        invocation_call_id="call-root-1",
        owner_skill_package_id=None,
        owner_skill_version_id=None,
        target_kind="workflow",
        target_id=TARGET_ID,
        target_version_id=TARGET_VERSION_ID,
        target_digest=DIGEST_C,
        execution_plan_digest=DIGEST_D,
        current_node_id="human",
        node_visit_id="visit-1",
        node_visit_ordinal=1,
        execution_attempt=1,
        phase="waiting",
        node_state_artifact_id=None,
        node_output_artifact_ids=(),
        branch_decisions=(),
        loop_cursors=(),
        child_frame_ids=(),
        agent_loop_continuation=None,
    )
    return DurableWorkflowStateV1(
        run_id=RUN_ID,
        root_frame_id=FRAME_ID,
        root_invocation_digest=DIGEST_E,
        frame_stack=(frame,),
        pending_interrupt_id=INTERRUPT_ID,
        terminal_output_artifact_id=None,
    )


def _suspension_fixture() -> Any:
    from app.assistant.workflow.durable.contracts import (
        BudgetSuspensionStateV1,
        compute_suspension_digest,
    )

    suspended_at = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    expires = datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc)
    return BudgetSuspensionStateV1(
        run_id=RUN_ID,
        interrupt_id=INTERRUPT_ID,
        parent_budget_revision_id=BUDGET_REV_ID,
        parent_ledger_revision=1,
        parent_ledger_digest=DIGEST_F,
        suspended_at_utc=suspended_at,
        remaining_active_ms=12_000,
        human_wait_expires_at_utc=expires,
        suspension_digest=compute_suspension_digest(
            run_id=RUN_ID,
            interrupt_id=INTERRUPT_ID,
            parent_budget_revision_id=BUDGET_REV_ID,
            parent_ledger_revision=1,
            parent_ledger_digest=DIGEST_F,
            suspended_at_utc=suspended_at,
            remaining_active_ms=12_000,
            human_wait_expires_at_utc=expires,
        ),
    )


# ===========================================================================
# Supported versions + V2 contract surface
# ===========================================================================


def test_supported_checkpoint_schema_versions_include_v2() -> None:
    codec = _import_codec()
    assert 1 in codec["SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS"]
    assert 2 in codec["SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS"]


def test_v2_execution_unit_and_next_action_kinds() -> None:
    DurableAgentCheckpointV2, DurableExecutionUnitV2, DurableNextActionV2 = (
        _import_v2_contracts()
    )
    for kind in (
        "provider_round",
        "capability_group",
        "completion",
        "memory_commit",
        "workflow_node",
        "agent_round",
        "interrupt_resume",
    ):
        unit = DurableExecutionUnitV2(
            logical_unit_id=f"u-{kind}",
            kind=kind,  # type: ignore[arg-type]
            state="prepared",
            provider_round=None,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        assert unit.kind == kind

    for kind in (
        "continue_provider",
        "dispatch_calls",
        "wait",
        "complete",
        "memory",
        "terminal",
        "reconcile",
        "resume_child",
        "continue_child",
        "resume_provider_loop",
        "expire_or_cancel_child",
    ):
        action = DurableNextActionV2(kind=kind)  # type: ignore[arg-type]
        assert action.kind == kind

    # V1 still rejects new kinds.
    _, DurableExecutionUnitV1, DurableNextActionV1 = _import_v1_contracts()
    with pytest.raises((ValidationError, ValueError)):
        DurableExecutionUnitV1(
            logical_unit_id="u",
            kind="workflow_node",  # type: ignore[arg-type]
            state="prepared",
            provider_round=None,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
    with pytest.raises((ValidationError, ValueError)):
        DurableNextActionV1(kind="resume_child")  # type: ignore[arg-type]


def test_checkpoint_v2_round_trip_with_workflow_and_suspension() -> None:
    codec = _import_codec()
    DurableAgentCheckpointV2, DurableExecutionUnitV2, DurableNextActionV2 = (
        _import_v2_contracts()
    )
    from app.assistant.capabilities.contracts import ContinuationRef

    unit = DurableExecutionUnitV2(
        logical_unit_id="wf-node-1",
        kind="workflow_node",
        state="prepared",
        provider_round=None,
        call_ids=(),
        attempt=1,
        reserved_budget_revision=2,
        started_budget_revision=None,
    )
    cont = ContinuationRef(
        continuation_type="durable_capability_invocation",
        contract_version=1,
        reference_id=str(FRAME_ID),
        payload_digest=DIGEST_E,
    )
    cp = DurableAgentCheckpointV2(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=3,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(ARTIFACT_ID,),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV2(kind="wait"),
        workflow_state=_workflow_state_fixture(),
        active_capability_continuation=cont,
        pending_interrupt_id=INTERRUPT_ID,
        budget_suspension=_suspension_fixture(),
    )
    assert cp.schema_version == 2
    encoded = codec["encode_checkpoint_v2"](cp)
    assert encoded["schemaVersion"] == 2
    assert encoded["workflowState"]["schemaVersion"] == 1
    assert encoded["pendingInterruptId"] == str(INTERRUPT_ID)
    assert encoded["budgetSuspension"]["contractVersion"] == 1
    assert "adHocSuspension" not in encoded
    decoded = codec["decode_checkpoint_v2"](encoded)
    assert decoded == cp
    assert decoded.workflow_state is not None
    assert decoded.budget_suspension is not None
    assert decoded.active_capability_continuation == cont
    digest = codec["checkpoint_state_digest"](cp)
    assert digest == sha256_canonical_json(encoded)
    assert codec["encode_checkpoint_v2"](decoded) == encoded


def test_checkpoint_v2_forbids_extra_and_is_frozen() -> None:
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()
    cp = DurableAgentCheckpointV2(
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
        next_action=DurableNextActionV2(kind="terminal"),
    )
    # Frozen: attribute assignment rejected.
    with pytest.raises((ValidationError, TypeError, ValueError)):
        cp.schema_version = 1  # type: ignore[misc]
    # Extra fields forbidden on construction / validate.
    with pytest.raises((ValidationError, TypeError, ValueError)):
        DurableAgentCheckpointV2(
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
            next_action=DurableNextActionV2(kind="terminal"),
            suspension_json={"remaining": 1},  # type: ignore[call-arg]
        )
    with pytest.raises((ValidationError, TypeError, ValueError)):
        DurableAgentCheckpointV2.model_validate(
            {
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
                "nextAction": {"kind": "terminal"},
                "suspensionJson": {"remaining": 1},
            }
        )


# ===========================================================================
# Lossless v1 → v2 migration fixed vectors
# ===========================================================================


def test_migrate_checkpoint_v1_to_v2_lossless_fixed_vector() -> None:
    codec = _import_codec()
    v1 = _v1_checkpoint(
        phase="ready_for_provider",
        inflight_unit=_v1_unit(kind="provider_round", state="prepared"),
        artifact_ids=(ARTIFACT_ID,),
        visible_text_artifact_id=ARTIFACT_ID,
    )
    v1_encoded = codec["encode_checkpoint_v1"](v1)
    v1_digest = codec["checkpoint_state_digest"](v1)

    v2 = codec["migrate_checkpoint_v1_to_v2"](v1)
    assert v2.schema_version == 2
    # All v1 field meanings preserved.
    assert v2.run_id == v1.run_id
    assert v2.phase == v1.phase
    assert v2.manifest_revision_id == v1.manifest_revision_id
    assert v2.policy_revision_id == v1.policy_revision_id
    assert v2.budget_revision_id == v1.budget_revision_id
    assert v2.obligation_revision_id == v1.obligation_revision_id
    assert v2.provider_message_ordinal == v1.provider_message_ordinal
    assert v2.provider_transcript_digest == v1.provider_transcript_digest
    assert v2.provider_loop_continuation == v1.provider_loop_continuation
    assert v2.capability_frames == v1.capability_frames
    assert v2.artifact_ids == v1.artifact_ids
    assert v2.visible_text_artifact_id == v1.visible_text_artifact_id
    assert v2.next_action.kind == v1.next_action.kind
    assert v2.inflight_unit is not None
    assert v2.inflight_unit.kind == "provider_round"
    assert v2.inflight_unit.logical_unit_id == v1.inflight_unit.logical_unit_id
    assert v2.inflight_unit.state == v1.inflight_unit.state
    assert v2.inflight_unit.attempt == v1.inflight_unit.attempt
    # New fields null/empty.
    assert v2.workflow_state is None
    assert v2.active_capability_continuation is None
    assert v2.pending_interrupt_id is None
    assert v2.budget_suspension is None

    # v1 digest meaning unchanged after migration path (encode of original still same).
    assert codec["encode_checkpoint_v1"](v1) == v1_encoded
    assert codec["checkpoint_state_digest"](v1) == v1_digest

    # The generic registry advances legacy payloads to the current Plan 08 v3
    # while the explicit v1→v2 migration above remains byte/meaning stable.
    migrated = codec["migrate_checkpoint"](v1_encoded)
    assert migrated.schema_version == 3
    assert migrated.run_id == v2.run_id
    assert migrated.inflight_unit == v2.inflight_unit
    assert migrated.policy_contract_version == 1
    assert migrated.capability_calls == ()


def test_migrate_every_v1_unit_kind_losslessly() -> None:
    codec = _import_codec()
    kinds = ("provider_round", "capability_group", "completion", "memory_commit")
    for kind in kinds:
        state = "prepared"
        unit = _v1_unit(kind=kind, state=state)
        phase = "ready_for_provider"
        next_kind = "continue_provider"
        if kind == "capability_group":
            # capability_group prepared may have empty call_ids
            phase = "dispatching_calls"
            next_kind = "dispatch_calls"
        elif kind == "completion":
            phase = "ready_for_completion"
            next_kind = "complete"
        elif kind == "memory_commit":
            phase = "ready_for_memory"
            next_kind = "memory"
        DurableAgentCheckpointV1, _, DurableNextActionV1 = _import_v1_contracts()
        v1 = DurableAgentCheckpointV1(
            run_id=RUN_ID,
            phase=phase,  # type: ignore[arg-type]
            manifest_revision_id=MANIFEST_REV_ID,
            policy_revision_id=POLICY_REV_ID,
            budget_revision_id=BUDGET_REV_ID,
            obligation_revision_id=OBLIGATION_REV_ID,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_1,
            provider_loop_continuation=None,
            inflight_unit=unit,
            next_action=DurableNextActionV1(kind=next_kind),  # type: ignore[arg-type]
        )
        v2 = codec["migrate_checkpoint_v1_to_v2"](v1)
        assert v2.inflight_unit is not None
        assert v2.inflight_unit.kind == kind
        assert v2.inflight_unit.logical_unit_id == unit.logical_unit_id
        assert v2.inflight_unit.reserved_budget_revision == unit.reserved_budget_revision


def test_v1_decode_encode_still_works_after_v2_support() -> None:
    codec = _import_codec()
    DurableAgentCheckpointV1, _, DurableNextActionV1 = _import_v1_contracts()
    v1 = DurableAgentCheckpointV1(
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
        next_action=DurableNextActionV1(kind="terminal"),
    )
    encoded = codec["encode_checkpoint_v1"](v1)
    decoded = codec["decode_checkpoint_v1"](encoded)
    assert decoded == v1
    # decode_checkpoint routes by version without forcing migration.
    decoded_any = codec["decode_checkpoint"](encoded)
    assert decoded_any.schema_version == 1
    assert decoded_any == v1


def test_decode_checkpoint_routes_v2() -> None:
    codec = _import_codec()
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()
    cp = DurableAgentCheckpointV2(
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
        next_action=DurableNextActionV2(kind="terminal"),
    )
    encoded = codec["encode_checkpoint_v2"](cp)
    decoded = codec["decode_checkpoint"](encoded)
    assert decoded.schema_version == 2
    assert decoded == cp


# ===========================================================================
# Plan 05 BudgetLedgerState fixed vectors unchanged
# ===========================================================================


def test_budget_ledger_state_fixed_vector_unchanged_with_suspension_sibling() -> None:
    """BudgetSuspensionStateV1 is a sibling; Plan 05 ledger digests must not drift."""
    from app.assistant.workflow.durable.contracts import BudgetSuspensionStateV1

    limits = normalize_run_budget_limits()
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    deadline = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
    state = create_initial_ledger_state(
        limits=limits,
        owner_limits=(),
        started_at_utc=started,
        deadline_at_utc=deadline,
    )
    # Fixed recomputation path must remain stable.
    recomputed = compute_ledger_digest(
        revision=state.revision,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    assert recomputed == state.ledger_digest
    # Suspension type exists and is independent.
    assert BudgetSuspensionStateV1 is not None
    assert "remaining_active_ms" not in type(state).model_fields
    assert "suspension_digest" not in type(state).model_fields


# ===========================================================================
# Unknown versions
# ===========================================================================


def test_unknown_checkpoint_schema_version_needs_reconciliation() -> None:
    codec = _import_codec()
    Needs = codec["NeedsReconciliationError"]
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
    with pytest.raises(Needs):
        codec["migrate_checkpoint"](payload)


def _empty_provider_surface(*, manifest_digest: str = DIGEST_1, manifest_revision: int = 1):
    from app.assistant.provider_loop.contracts import (
        ProviderToolSurface,
        compute_alias_map_digest,
        compute_surface_digest,
    )

    alias_map_digest = compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=manifest_digest,
        aliases=(),
    )
    surface_digest = compute_surface_digest(
        provider_protocol="openai_compat",
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        alias_map_digest=alias_map_digest,
        tools=(),
    )
    return ProviderToolSurface(
        provider_protocol="openai_compat",
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        alias_map_digest=alias_map_digest,
        tools=(),
        surface_digest=surface_digest,
    )


def _provider_loop_continuation() -> Any:
    from app.assistant.capabilities.contracts import ContinuationRef
    from app.assistant.domain.contracts import create_model_ref, create_provider_ref
    from app.assistant.provider_loop.contracts import (
        ProviderLoopContinuation,
        ProviderUsage,
        ProviderWaitingCallState,
        create_execution_scope,
    )
    from app.assistant.capabilities.contracts import CapabilityPrincipal

    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=UUID("00000000-0000-4000-8000-000000000901"),
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_A,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    model = create_model_ref(
        model_id=UUID("00000000-0000-4000-8000-000000000902"),
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=UUID("00000000-0000-4000-8000-000000000903"),
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_A,
        model_config_digest=DIGEST_B,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=CapabilityPrincipal(
            principal_type="service",
            principal_id="local-assistant",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )
    surface = _empty_provider_surface()
    waiting_state = ProviderWaitingCallState(
        call_id="wait-1",
        call_index=0,
        binding_contract_digest=DIGEST_A,
        descriptor_digest=DIGEST_B,
        behavior_digest=DIGEST_C,
        classification_revision="plan02-v1",
        classification_ruleset_digest=DIGEST_A,
        capability_continuation=ContinuationRef(
            continuation_type="human_approval",
            contract_version=1,
            reference_id="cont-1",
            payload_digest=DIGEST_B,
        ),
    )
    return ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=model,
        locale="en",
        max_rounds=4,
        provider_rounds_used=1,
        prior_tool_call_count=0,
        accumulated_usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        current_manifest_revision=1,
        current_manifest_digest=DIGEST_A,
        exposed_surface=surface,
        assistant_message_digest=DIGEST_C,
        transcript_digest=DIGEST_D,
        waiting_call=waiting_state,
        next_call_index=1,
        pending_call_ids=(),
        completed_call_records=(),
    )


def test_checkpoint_v2_waiting_provider_path_valid() -> None:
    """Valid waiting shape (a): provider_loop_continuation without pause bundle."""
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()
    cont = _provider_loop_continuation()
    cp = DurableAgentCheckpointV2(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=1,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=cont,
        inflight_unit=None,
        next_action=DurableNextActionV2(kind="wait"),
        workflow_state=None,
        active_capability_continuation=None,
        pending_interrupt_id=None,
        budget_suspension=None,
    )
    assert cp.provider_loop_continuation is cont
    assert cp.pending_interrupt_id is None


def test_checkpoint_v2_waiting_workflow_pause_path_valid() -> None:
    """Valid waiting shape (b): complete workflow pause bundle without provider cont."""
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()
    from app.assistant.capabilities.contracts import ContinuationRef

    cont = ContinuationRef(
        continuation_type="durable_capability_invocation",
        contract_version=1,
        reference_id=str(FRAME_ID),
        payload_digest=DIGEST_E,
    )
    cp = DurableAgentCheckpointV2(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=3,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        next_action=DurableNextActionV2(kind="wait"),
        workflow_state=_workflow_state_fixture(),
        active_capability_continuation=cont,
        pending_interrupt_id=INTERRUPT_ID,
        budget_suspension=_suspension_fixture(),
    )
    assert cp.workflow_state is not None
    assert cp.budget_suspension is not None
    assert cp.pending_interrupt_id == INTERRUPT_ID


def test_checkpoint_v2_waiting_rejects_empty_and_partial_pause() -> None:
    """Empty waiting and partial pause bundles must fail closed."""
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()

    base = dict(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        next_action=DurableNextActionV2(kind="wait"),
    )

    # Empty waiting: no provider continuation, no pause bundle.
    with pytest.raises((ValidationError, ValueError)) as empty_exc:
        DurableAgentCheckpointV2(**base)
    assert "waiting phase" in str(empty_exc.value)

    # Partial: only pending_interrupt_id.
    with pytest.raises((ValidationError, ValueError)):
        DurableAgentCheckpointV2(
            **base,
            pending_interrupt_id=INTERRUPT_ID,
        )

    # Partial: interrupt + suspension, missing workflow_state.
    with pytest.raises((ValidationError, ValueError)):
        DurableAgentCheckpointV2(
            **base,
            pending_interrupt_id=INTERRUPT_ID,
            budget_suspension=_suspension_fixture(),
        )

    # Partial: interrupt + workflow_state, missing suspension.
    with pytest.raises((ValidationError, ValueError)):
        DurableAgentCheckpointV2(
            **base,
            pending_interrupt_id=INTERRUPT_ID,
            workflow_state=_workflow_state_fixture(),
        )

    # Complete pause fields but mismatched workflow_state.pending_interrupt_id.
    from app.assistant.workflow.durable.contracts import DurableWorkflowStateV1

    mismatched_ws = _workflow_state_fixture()
    mismatched_ws = DurableWorkflowStateV1(
        run_id=mismatched_ws.run_id,
        root_frame_id=mismatched_ws.root_frame_id,
        root_invocation_digest=mismatched_ws.root_invocation_digest,
        frame_stack=mismatched_ws.frame_stack,
        pending_interrupt_id=UUID("00000000-0000-4000-8000-000000000799"),
        terminal_output_artifact_id=None,
    )
    with pytest.raises((ValidationError, ValueError)) as mismatch_exc:
        DurableAgentCheckpointV2(
            **base,
            pending_interrupt_id=INTERRUPT_ID,
            budget_suspension=_suspension_fixture(),
            workflow_state=mismatched_ws,
        )
    assert "pending_interrupt_id" in str(mismatch_exc.value)

    # Both provider continuation AND complete pause bundle: rejected.
    both_kwargs = dict(base)
    both_kwargs["provider_loop_continuation"] = _provider_loop_continuation()
    both_kwargs["pending_interrupt_id"] = INTERRUPT_ID
    both_kwargs["budget_suspension"] = _suspension_fixture()
    both_kwargs["workflow_state"] = _workflow_state_fixture()
    with pytest.raises((ValidationError, ValueError)) as both_exc:
        DurableAgentCheckpointV2(**both_kwargs)
    assert "provider_loop_continuation" in str(both_exc.value) or "pause" in str(
        both_exc.value
    )


def test_nested_unknown_workflow_state_version_needs_reconciliation() -> None:
    """Nested workflowState schemaVersion=99 must raise NeedsReconciliationError.

    Top-level decode_workflow_state already routes unknown versions to
    needs_reconciliation; Checkpoint V2 nested validation must do the same
    rather than collapsing into DurableCodecError("checkpoint_invalid").
    """
    codec = _import_codec()
    Needs = codec["NeedsReconciliationError"]
    Error = codec["DurableCodecError"]

    # Encode a valid v2 waiting pause checkpoint, then mutate nested version.
    DurableAgentCheckpointV2, _, DurableNextActionV2 = _import_v2_contracts()
    from app.assistant.capabilities.contracts import ContinuationRef

    cp = DurableAgentCheckpointV2(
        run_id=RUN_ID,
        phase="waiting",
        manifest_revision_id=MANIFEST_REV_ID,
        policy_revision_id=POLICY_REV_ID,
        budget_revision_id=BUDGET_REV_ID,
        obligation_revision_id=OBLIGATION_REV_ID,
        provider_message_ordinal=3,
        provider_transcript_digest=DIGEST_1,
        provider_loop_continuation=None,
        inflight_unit=None,
        next_action=DurableNextActionV2(kind="wait"),
        workflow_state=_workflow_state_fixture(),
        active_capability_continuation=ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(FRAME_ID),
            payload_digest=DIGEST_E,
        ),
        pending_interrupt_id=INTERRUPT_ID,
        budget_suspension=_suspension_fixture(),
    )
    encoded = codec["encode_checkpoint_v2"](cp)
    encoded["workflowState"] = dict(encoded["workflowState"])
    encoded["workflowState"]["schemaVersion"] = 99

    with pytest.raises(Needs) as exc:
        codec["decode_checkpoint_v2"](encoded)
    assert exc.value.code == "needs_reconciliation"
    assert exc.value.schema_version == 99

    with pytest.raises(Needs) as exc2:
        codec["decode_checkpoint"](encoded)
    assert exc2.value.code == "needs_reconciliation"

    # Must not only surface as generic checkpoint_invalid without Needs.
    try:
        codec["decode_checkpoint_v2"](encoded)
        raise AssertionError("expected NeedsReconciliationError")
    except Needs:
        pass
    except Error as err:
        raise AssertionError(
            f"nested unknown workflow version raised DurableCodecError only: {err}"
        ) from err


def test_checkpoint_v2_public_types_are_exact_not_any() -> None:
    """Public V2 annotations must be exact plan types, not typing.Any."""
    import typing

    DurableAgentCheckpointV2, _, _ = _import_v2_contracts()
    hints = typing.get_type_hints(
        DurableAgentCheckpointV2,
        include_extras=True,
    )
    for field_name in (
        "workflow_state",
        "active_capability_continuation",
        "budget_suspension",
    ):
        ann = hints[field_name]
        # Resolve Optional/Union to concrete args.
        origin = typing.get_origin(ann)
        args = typing.get_args(ann) if origin is not None else (ann,)
        # At least one concrete non-None, non-Any type must be present.
        concrete = [a for a in args if a is not type(None) and a is not typing.Any]
        assert concrete, f"{field_name} annotation must not be bare Any: {ann!r}"
        assert typing.Any not in args, f"{field_name} must not include Any: {ann!r}"


def test_ephemeral_context_rejected_by_checkpoint_v2_codec() -> None:
    codec = _import_codec()
    Error = codec["DurableCodecError"]
    from app.assistant.workflow.durable.context import EphemeralWorkflowContext

    class Session:
        pass

    class CapabilityGateway:
        pass

    class ArtifactStore:
        pass

    class EventSink:
        pass

    class CancellationProbe:
        pass

    class Clock:
        pass

    class ProviderResolver:
        pass

    class DurableNodeAdapterRegistry:
        pass

    class ExactRuntimeDependencyResolver:
        def require_tool(self, **kwargs: Any) -> object:
            return object()

        def require_workflow_version(self, **kwargs: Any) -> object:
            return object()

        def require_model(self, **kwargs: Any) -> object:
            return object()

    ctx = EphemeralWorkflowContext(
        session_factory=lambda: Session(),
        provider_resolver=ProviderResolver(),
        capability_gateway=CapabilityGateway(),
        artifact_store=ArtifactStore(),
        event_sink=EventSink(),
        cancellation_probe=CancellationProbe(),
        clock=Clock(),
        exact_dependency_resolver=ExactRuntimeDependencyResolver(),
        node_adapters=DurableNodeAdapterRegistry(),
    )
    with pytest.raises((Error, TypeError, ValueError)):
        codec["encode_checkpoint_v2"](ctx)  # type: ignore[arg-type]
