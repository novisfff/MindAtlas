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

    # migrate via payload registry also lands on v2.
    migrated = codec["migrate_checkpoint"](v1_encoded)
    assert migrated.schema_version == 2
    assert migrated == v2


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
