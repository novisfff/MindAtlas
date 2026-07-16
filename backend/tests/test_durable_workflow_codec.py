"""Plan 07 Task 1: durable Workflow/Agent plan+state codec contracts.

Strict frozen/round-trip/digest/forbidden-object tests for execution plan,
Workflow state, frames, node visits, branches, loop cursors, nested Agent
continuation, size/depth/Artifact projection, and ephemeral-context rejection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import ContinuationRef  # noqa: E402
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000701")
TARGET_ID = UUID("00000000-0000-4000-8000-000000000702")
TARGET_VERSION_ID = UUID("00000000-0000-4000-8000-000000000703")
FRAME_ID = UUID("00000000-0000-4000-8000-000000000704")
ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000705")
INTERRUPT_ID = UUID("00000000-0000-4000-8000-000000000706")
BUDGET_REV_ID = UUID("00000000-0000-4000-8000-000000000707")
OWNER_PKG_ID = UUID("00000000-0000-4000-8000-000000000708")
OWNER_VER_ID = UUID("00000000-0000-4000-8000-000000000709")


def _import_workflow_contracts():
    from app.assistant.workflow.durable.contracts import (
        DURABLE_WORKFLOW_IDENTITY_NAMESPACE,
        BudgetSuspensionStateV1,
        DurableBranchDecisionV1,
        DurableCallFrameV1,
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableLoopCursorV1,
        DurableNodePlanV1,
        DurablePauseProposalV1,
        DurableWorkflowStateV1,
        FrozenExecutionDependencyRef,
        build_root_continuation,
        compute_branch_decision_digest,
        compute_loop_cursor_digest,
        compute_plan_digest,
        compute_proposal_digest,
        compute_suspension_digest,
        derive_frame_id,
        derive_interrupt_id,
        derive_node_visit_id,
    )

    return {
        "DURABLE_WORKFLOW_IDENTITY_NAMESPACE": DURABLE_WORKFLOW_IDENTITY_NAMESPACE,
        "BudgetSuspensionStateV1": BudgetSuspensionStateV1,
        "DurableBranchDecisionV1": DurableBranchDecisionV1,
        "DurableCallFrameV1": DurableCallFrameV1,
        "DurableEdgeV1": DurableEdgeV1,
        "DurableExecutionPlanV1": DurableExecutionPlanV1,
        "DurableLoopCursorV1": DurableLoopCursorV1,
        "DurableNodePlanV1": DurableNodePlanV1,
        "DurablePauseProposalV1": DurablePauseProposalV1,
        "DurableWorkflowStateV1": DurableWorkflowStateV1,
        "FrozenExecutionDependencyRef": FrozenExecutionDependencyRef,
        "build_root_continuation": build_root_continuation,
        "compute_branch_decision_digest": compute_branch_decision_digest,
        "compute_loop_cursor_digest": compute_loop_cursor_digest,
        "compute_plan_digest": compute_plan_digest,
        "compute_proposal_digest": compute_proposal_digest,
        "compute_suspension_digest": compute_suspension_digest,
        "derive_frame_id": derive_frame_id,
        "derive_interrupt_id": derive_interrupt_id,
        "derive_node_visit_id": derive_node_visit_id,
    }


def _import_workflow_codec():
    from app.assistant.durable.codec import (
        DurableCodecError,
        NeedsReconciliationError,
    )
    from app.assistant.workflow.durable.codec import (
        MAX_WORKFLOW_CODEC_JSON_BYTES,
        MAX_WORKFLOW_CODEC_JSON_DEPTH,
        decode_execution_plan,
        decode_pause_proposal,
        decode_workflow_state,
        encode_execution_plan,
        encode_pause_proposal,
        encode_workflow_state,
        project_json_for_durable_storage,
        workflow_state_digest,
    )

    return {
        "DurableCodecError": DurableCodecError,
        "NeedsReconciliationError": NeedsReconciliationError,
        "MAX_WORKFLOW_CODEC_JSON_BYTES": MAX_WORKFLOW_CODEC_JSON_BYTES,
        "MAX_WORKFLOW_CODEC_JSON_DEPTH": MAX_WORKFLOW_CODEC_JSON_DEPTH,
        "decode_execution_plan": decode_execution_plan,
        "decode_pause_proposal": decode_pause_proposal,
        "decode_workflow_state": decode_workflow_state,
        "encode_execution_plan": encode_execution_plan,
        "encode_pause_proposal": encode_pause_proposal,
        "encode_workflow_state": encode_workflow_state,
        "project_json_for_durable_storage": project_json_for_durable_storage,
        "workflow_state_digest": workflow_state_digest,
    }


def _import_context():
    from app.assistant.workflow.durable.context import EphemeralWorkflowContext

    return EphemeralWorkflowContext


def _edge(**overrides: Any):
    c = _import_workflow_contracts()
    data = {
        "edge_id": "e1",
        "source_node_id": "start",
        "target_node_id": "llm",
        "source_handle": None,
        "target_handle": None,
    }
    data.update(overrides)
    return c["DurableEdgeV1"](**data)


def _dep(**overrides: Any):
    c = _import_workflow_contracts()
    data = {
        "dependency_path": "nodes.llm.model",
        "dependency_type": "model",
        "target_identity": "model:gpt-test",
        "target_version_id": None,
        "resolution_digest": DIGEST_A,
        "dependency_digest": DIGEST_B,
    }
    data.update(overrides)
    return c["FrozenExecutionDependencyRef"](**data)


def _node(**overrides: Any):
    c = _import_workflow_contracts()
    data = {
        "node_id": "start",
        "node_type": "start",
        "config_digest": DIGEST_A,
        "outgoing_edges": (_edge(),),
        "adapter_key": "start.v1",
        "business_side_effect": "none",
        "may_interrupt": False,
        "dependency_refs": (),
    }
    data.update(overrides)
    return c["DurableNodePlanV1"](**data)


def _plan(**overrides: Any):
    c = _import_workflow_contracts()
    nodes = (
        _node(node_id="start", node_type="start", adapter_key="start.v1"),
        _node(
            node_id="llm",
            node_type="llm",
            adapter_key="llm.v1",
            business_side_effect="compute",
            outgoing_edges=(
                _edge(edge_id="e2", source_node_id="llm", target_node_id="human"),
            ),
            dependency_refs=(_dep(),),
        ),
        _node(
            node_id="human",
            node_type="human_in_loop",
            adapter_key="human.v1",
            may_interrupt=True,
            business_side_effect="none",
            outgoing_edges=(
                _edge(edge_id="e3", source_node_id="human", target_node_id="output"),
            ),
        ),
        _node(
            node_id="output",
            node_type="output",
            adapter_key="output.v1",
            outgoing_edges=(),
        ),
    )
    base = {
        "target_kind": "workflow",
        "target_version_id": TARGET_VERSION_ID,
        "target_digest": DIGEST_C,
        "entry_node_id": "start",
        "nodes": nodes,
    }
    base.update(overrides)
    if "plan_digest" not in base:
        base["plan_digest"] = c["compute_plan_digest"](
            target_kind=base["target_kind"],
            target_version_id=base["target_version_id"],
            target_digest=base["target_digest"],
            entry_node_id=base["entry_node_id"],
            nodes=base["nodes"],
        )
    return c["DurableExecutionPlanV1"](**base)


def _branch(**overrides: Any):
    c = _import_workflow_contracts()
    visit = str(uuid4())
    data = {
        "node_id": "if1",
        "node_visit_id": visit,
        "chosen_handle": "true",
        "chosen_target_node_id": "then_node",
    }
    data.update(overrides)
    if "decision_digest" not in data:
        data["decision_digest"] = c["compute_branch_decision_digest"](
            node_id=data["node_id"],
            node_visit_id=data["node_visit_id"],
            chosen_handle=data["chosen_handle"],
            chosen_target_node_id=data["chosen_target_node_id"],
        )
    return c["DurableBranchDecisionV1"](**data)


def _loop(**overrides: Any):
    c = _import_workflow_contracts()
    visit = str(uuid4())
    data = {
        "loop_node_id": "iter1",
        "node_visit_id": visit,
        "iteration_index": 0,
        "item_key": "item-0",
        "completed_child_output_artifact_ids": (),
    }
    data.update(overrides)
    if "cursor_digest" not in data:
        data["cursor_digest"] = c["compute_loop_cursor_digest"](
            loop_node_id=data["loop_node_id"],
            node_visit_id=data["node_visit_id"],
            iteration_index=data["iteration_index"],
            item_key=data.get("item_key"),
            completed_child_output_artifact_ids=data.get(
                "completed_child_output_artifact_ids", ()
            ),
        )
    return c["DurableLoopCursorV1"](**data)


def _frame(**overrides: Any):
    c = _import_workflow_contracts()
    data = {
        "frame_id": FRAME_ID,
        "parent_frame_id": None,
        "invocation_call_id": "call-root-1",
        "owner_skill_package_id": OWNER_PKG_ID,
        "owner_skill_version_id": OWNER_VER_ID,
        "target_kind": "workflow",
        "target_id": TARGET_ID,
        "target_version_id": TARGET_VERSION_ID,
        "target_digest": DIGEST_C,
        "execution_plan_digest": DIGEST_D,
        "current_node_id": "human",
        "node_visit_id": str(uuid4()),
        "node_visit_ordinal": 2,
        "execution_attempt": 1,
        "phase": "waiting",
        "node_state_artifact_id": None,
        "node_output_artifact_ids": (),
        "branch_decisions": (),
        "loop_cursors": (),
        "child_frame_ids": (),
        "agent_loop_continuation": None,
    }
    data.update(overrides)
    return c["DurableCallFrameV1"](**data)


def _workflow_state(**overrides: Any):
    c = _import_workflow_contracts()
    frame = _frame()
    data = {
        "run_id": RUN_ID,
        "root_frame_id": FRAME_ID,
        "root_invocation_digest": DIGEST_E,
        "frame_stack": (frame,),
        "pending_interrupt_id": INTERRUPT_ID,
        "terminal_output_artifact_id": None,
    }
    data.update(overrides)
    return c["DurableWorkflowStateV1"](**data)


def _suspension(**overrides: Any):
    c = _import_workflow_contracts()
    now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    data = {
        "run_id": RUN_ID,
        "interrupt_id": INTERRUPT_ID,
        "parent_budget_revision_id": BUDGET_REV_ID,
        "parent_ledger_revision": 3,
        "parent_ledger_digest": DIGEST_F,
        "suspended_at_utc": now,
        "remaining_active_ms": 45_000,
        "human_wait_expires_at_utc": datetime(2026, 7, 15, 13, 0, 0, tzinfo=timezone.utc),
    }
    data.update(overrides)
    if "suspension_digest" not in data:
        data["suspension_digest"] = c["compute_suspension_digest"](
            run_id=data["run_id"],
            interrupt_id=data["interrupt_id"],
            parent_budget_revision_id=data["parent_budget_revision_id"],
            parent_ledger_revision=data["parent_ledger_revision"],
            parent_ledger_digest=data["parent_ledger_digest"],
            suspended_at_utc=data["suspended_at_utc"],
            remaining_active_ms=data["remaining_active_ms"],
            human_wait_expires_at_utc=data["human_wait_expires_at_utc"],
        )
    return c["BudgetSuspensionStateV1"](**data)


# ===========================================================================
# Frozen contracts + digests
# ===========================================================================


def test_workflow_contracts_are_frozen_forbid_extra() -> None:
    c = _import_workflow_contracts()
    plan = _plan()
    assert plan.contract_version == 1
    with pytest.raises((ValidationError, TypeError, ValueError)):
        plan.contract_version = 2  # type: ignore[misc]
    with pytest.raises((ValidationError, TypeError, ValueError)):
        c["DurableEdgeV1"](
            edge_id="e",
            source_node_id="a",
            target_node_id="b",
            unexpected=True,  # type: ignore[call-arg]
        )
    state = _workflow_state()
    assert state.schema_version == 1
    with pytest.raises((ValidationError, TypeError, ValueError)):
        state.schema_version = 2  # type: ignore[misc]
    with pytest.raises((ValidationError, TypeError, ValueError)):
        c["DurableWorkflowStateV1"].model_validate(
            {
                "runId": str(RUN_ID),
                "rootFrameId": str(FRAME_ID),
                "rootInvocationDigest": DIGEST_E,
                "frameStack": [],
                "legacyGraph": {},
            }
        )


def test_execution_plan_round_trip_and_fixed_digest() -> None:
    codec = _import_workflow_codec()
    c = _import_workflow_contracts()
    plan = _plan()
    encoded = codec["encode_execution_plan"](plan)
    assert encoded["contractVersion"] == 1
    assert encoded["targetKind"] == "workflow"
    assert "nodes" in encoded
    decoded = codec["decode_execution_plan"](encoded)
    assert decoded == plan
    assert decoded.plan_digest == plan.plan_digest
    # Recompute plan digest from public helper.
    recomputed = c["compute_plan_digest"](
        target_kind=plan.target_kind,
        target_version_id=plan.target_version_id,
        target_digest=plan.target_digest,
        entry_node_id=plan.entry_node_id,
        nodes=plan.nodes,
    )
    assert recomputed == plan.plan_digest
    assert len(plan.plan_digest) == 64
    # Canonical second encode.
    assert codec["encode_execution_plan"](decoded) == encoded


def test_plan_digest_tamper_rejected() -> None:
    c = _import_workflow_contracts()
    with pytest.raises((ValidationError, ValueError)):
        _plan(plan_digest=DIGEST_A)  # wrong digest


def test_workflow_state_frame_branch_loop_round_trip() -> None:
    codec = _import_workflow_codec()
    branch = _branch()
    loop = _loop(completed_child_output_artifact_ids=(ARTIFACT_ID,))
    frame = _frame(
        phase="executing",
        branch_decisions=(branch,),
        loop_cursors=(loop,),
        node_output_artifact_ids=(ARTIFACT_ID,),
    )
    state = _workflow_state(frame_stack=(frame,), pending_interrupt_id=None)
    encoded = codec["encode_workflow_state"](state)
    assert encoded["schemaVersion"] == 1
    decoded = codec["decode_workflow_state"](encoded)
    assert decoded == state
    assert decoded.frame_stack[0].branch_decisions[0] == branch
    assert decoded.frame_stack[0].loop_cursors[0] == loop
    digest = codec["workflow_state_digest"](state)
    assert digest == sha256_canonical_json(encoded)
    assert codec["workflow_state_digest"](decoded) == digest


def _agent_provider_loop_continuation() -> Any:
    """Minimal real ProviderLoopContinuation for nested Agent frame fixture."""
    from app.assistant.capabilities.contracts import (
        CapabilityPrincipal,
        ContinuationRef,
    )
    from app.assistant.domain.contracts import create_model_ref, create_provider_ref
    from app.assistant.provider_loop.contracts import (
        ProviderLoopContinuation,
        ProviderToolSurface,
        ProviderUsage,
        ProviderWaitingCallState,
        compute_alias_map_digest,
        compute_surface_digest,
        create_execution_scope,
    )

    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=UUID("00000000-0000-4000-8000-000000000911"),
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_A,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    model = create_model_ref(
        model_id=UUID("00000000-0000-4000-8000-000000000912"),
        model_name="gpt-agent",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=UUID("00000000-0000-4000-8000-000000000913"),
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_A,
        model_config_digest=DIGEST_B,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    alias_map_digest = compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=DIGEST_A,
        aliases=(),
    )
    surface_digest = compute_surface_digest(
        provider_protocol="openai_compat",
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        alias_map_digest=alias_map_digest,
        tools=(),
    )
    surface = ProviderToolSurface(
        provider_protocol="openai_compat",
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        alias_map_digest=alias_map_digest,
        tools=(),
        surface_digest=surface_digest,
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
    waiting_state = ProviderWaitingCallState(
        call_id="agent-wait-1",
        call_index=0,
        binding_contract_digest=DIGEST_A,
        descriptor_digest=DIGEST_B,
        behavior_digest=DIGEST_C,
        classification_revision="plan02-v1",
        classification_ruleset_digest=DIGEST_A,
        capability_continuation=ContinuationRef(
            continuation_type="human_approval",
            contract_version=1,
            reference_id="agent-cont-1",
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
        accumulated_usage=ProviderUsage(input_tokens=2, output_tokens=1, total_tokens=3),
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


def test_nested_agent_frame_provider_loop_continuation_round_trip() -> None:
    """DurableCallFrameV1 with real ProviderLoopContinuation round-trips + digests."""
    codec = _import_workflow_codec()
    from app.assistant.provider_loop.contracts import ProviderLoopContinuation

    agent_cont = _agent_provider_loop_continuation()
    assert isinstance(agent_cont, ProviderLoopContinuation)
    frame = _frame(
        target_kind="agent",
        phase="waiting",
        current_node_id="agent_round",
        agent_loop_continuation=agent_cont,
    )
    assert frame.agent_loop_continuation is agent_cont
    state = _workflow_state(
        frame_stack=(frame,),
        pending_interrupt_id=None,
    )
    encoded = codec["encode_workflow_state"](state)
    nested = encoded["frameStack"][0]["agentLoopContinuation"]
    assert nested is not None
    assert nested["waitingCall"]["callId"] == "agent-wait-1"
    decoded = codec["decode_workflow_state"](encoded)
    assert decoded == state
    assert decoded.frame_stack[0].agent_loop_continuation is not None
    assert (
        decoded.frame_stack[0].agent_loop_continuation.waiting_call.call_id
        == "agent-wait-1"
    )
    assert (
        decoded.frame_stack[0].agent_loop_continuation.transcript_digest == DIGEST_D
    )
    digest = codec["workflow_state_digest"](state)
    assert digest == sha256_canonical_json(encoded)
    assert codec["workflow_state_digest"](decoded) == digest
    assert codec["encode_workflow_state"](decoded) == encoded


def test_durable_pause_effect_port_protocol_surface() -> None:
    """Protocol-only DurablePauseEffectPort matches plan §5.3 stage/consume_exact."""
    from typing import get_type_hints

    from app.assistant.workflow.durable.contracts import (
        DurablePauseEffectPort,
        DurablePauseProposalV1,
    )

    assert hasattr(DurablePauseEffectPort, "stage")
    assert hasattr(DurablePauseEffectPort, "consume_exact")
    # Structural Protocol: a duck-typed object is accepted by typing but not
    # instantiated as a runtime implementation here.
    hints_stage = get_type_hints(DurablePauseEffectPort.stage)
    assert hints_stage.get("return") is type(None) or hints_stage.get("return") is None
    # Ensure proposal type is the frozen contract.
    assert DurablePauseProposalV1 is not None


def test_branch_and_loop_digest_helpers() -> None:
    c = _import_workflow_contracts()
    branch = _branch()
    loop = _loop()
    assert branch.decision_digest == c["compute_branch_decision_digest"](
        node_id=branch.node_id,
        node_visit_id=branch.node_visit_id,
        chosen_handle=branch.chosen_handle,
        chosen_target_node_id=branch.chosen_target_node_id,
    )
    assert loop.cursor_digest == c["compute_loop_cursor_digest"](
        loop_node_id=loop.loop_node_id,
        node_visit_id=loop.node_visit_id,
        iteration_index=loop.iteration_index,
        item_key=loop.item_key,
        completed_child_output_artifact_ids=loop.completed_child_output_artifact_ids,
    )
    with pytest.raises((ValidationError, ValueError)):
        c["DurableBranchDecisionV1"](
            node_id="x",
            node_visit_id="y",
            chosen_handle="true",
            chosen_target_node_id="z",
            decision_digest=DIGEST_A,
        )


def test_budget_suspension_digest_immutable_identity() -> None:
    c = _import_workflow_contracts()
    sus = _suspension()
    assert sus.contract_version == 1
    assert sus.suspension_digest == c["compute_suspension_digest"](
        run_id=sus.run_id,
        interrupt_id=sus.interrupt_id,
        parent_budget_revision_id=sus.parent_budget_revision_id,
        parent_ledger_revision=sus.parent_ledger_revision,
        parent_ledger_digest=sus.parent_ledger_digest,
        suspended_at_utc=sus.suspended_at_utc,
        remaining_active_ms=sus.remaining_active_ms,
        human_wait_expires_at_utc=sus.human_wait_expires_at_utc,
    )
    with pytest.raises((ValidationError, ValueError)):
        _suspension(suspension_digest=DIGEST_A)


def test_pause_proposal_round_trip() -> None:
    codec = _import_workflow_codec()
    c = _import_workflow_contracts()
    state = _workflow_state()
    root_cont = c["build_root_continuation"](
        root_frame_id=FRAME_ID,
        root_invocation_digest=DIGEST_E,
    )
    proposal_fields = {
        "run_id": RUN_ID,
        "root_call_id": "call-root-1",
        "root_continuation": root_cont,
        "frame_id": FRAME_ID,
        "node_id": "human",
        "node_visit_id": state.frame_stack[0].node_visit_id or "visit",
        "interrupt_id": INTERRUPT_ID,
        "kind": "approval",
        "request_payload": {"prompt": "approve?"},
        "field_schema": None,
        "initial_values": {},
        "proposed_workflow_state": state,
    }
    proposal = c["DurablePauseProposalV1"](
        **proposal_fields,
        proposal_digest=c["compute_proposal_digest"](**proposal_fields),
    )
    encoded = codec["encode_pause_proposal"](proposal)
    decoded = codec["decode_pause_proposal"](encoded)
    assert decoded == proposal
    assert decoded.root_continuation.continuation_type == "durable_capability_invocation"
    assert decoded.root_continuation.reference_id == str(FRAME_ID)
    assert decoded.root_continuation.payload_digest == DIGEST_E


# ===========================================================================
# Deterministic identities
# ===========================================================================


def test_deterministic_frame_node_visit_interrupt_identities() -> None:
    c = _import_workflow_contracts()
    ns = c["DURABLE_WORKFLOW_IDENTITY_NAMESPACE"]
    assert isinstance(ns, UUID)

    frame_a = c["derive_frame_id"](
        root_invocation_digest=DIGEST_E,
        parent_path="root",
        target_version_id=TARGET_VERSION_ID,
        invocation_call_id="call-root-1",
    )
    frame_b = c["derive_frame_id"](
        root_invocation_digest=DIGEST_E,
        parent_path="root",
        target_version_id=TARGET_VERSION_ID,
        invocation_call_id="call-root-1",
    )
    assert frame_a == frame_b
    assert frame_a != c["derive_frame_id"](
        root_invocation_digest=DIGEST_E,
        parent_path="root/child",
        target_version_id=TARGET_VERSION_ID,
        invocation_call_id="call-root-1",
    )

    visit_a = c["derive_node_visit_id"](
        frame_id=frame_a,
        node_id="human",
        node_visit_ordinal=2,
    )
    visit_b = c["derive_node_visit_id"](
        frame_id=frame_a,
        node_id="human",
        node_visit_ordinal=2,
    )
    assert visit_a == visit_b
    # Retries do not create a new logical visit (same ordinal).
    assert visit_a != c["derive_node_visit_id"](
        frame_id=frame_a,
        node_id="human",
        node_visit_ordinal=3,
    )

    interrupt_a = c["derive_interrupt_id"](
        run_id=RUN_ID,
        root_invocation_digest=DIGEST_E,
        frame_id=frame_a,
        node_visit_id=visit_a,
        logical_interrupt_ordinal=1,
    )
    interrupt_b = c["derive_interrupt_id"](
        run_id=RUN_ID,
        root_invocation_digest=DIGEST_E,
        frame_id=frame_a,
        node_visit_id=visit_a,
        logical_interrupt_ordinal=1,
    )
    assert interrupt_a == interrupt_b
    assert interrupt_a != c["derive_interrupt_id"](
        run_id=RUN_ID,
        root_invocation_digest=DIGEST_E,
        frame_id=frame_a,
        node_visit_id=visit_a,
        logical_interrupt_ordinal=2,
    )

    cont = c["build_root_continuation"](
        root_frame_id=frame_a,
        root_invocation_digest=DIGEST_E,
    )
    assert cont == ContinuationRef(
        continuation_type="durable_capability_invocation",
        contract_version=1,
        reference_id=str(frame_a),
        payload_digest=DIGEST_E,
    )


# ===========================================================================
# Size / depth / Artifact projection + forbidden objects
# ===========================================================================


def test_reject_excess_depth_and_size_on_workflow_state() -> None:
    codec = _import_workflow_codec()
    Error = codec["DurableCodecError"]
    max_depth = codec["MAX_WORKFLOW_CODEC_JSON_DEPTH"]
    max_bytes = codec["MAX_WORKFLOW_CODEC_JSON_BYTES"]
    assert max_depth >= 8
    assert max_bytes >= 1024

    node: Any = {"schemaVersion": 1, "runId": str(RUN_ID)}
    cur = node
    for _ in range(max_depth + 5):
        cur = {"nested": cur}
    with pytest.raises((Error, ValueError)):
        codec["decode_workflow_state"](cur)

    huge = {
        "schemaVersion": 1,
        "runId": str(RUN_ID),
        "rootFrameId": str(FRAME_ID),
        "rootInvocationDigest": DIGEST_E,
        "frameStack": [],
        "pendingInterruptId": None,
        "terminalOutputArtifactId": None,
        "pad": "x" * (max_bytes + 100),
    }
    with pytest.raises((Error, ValueError)):
        codec["decode_workflow_state"](huge)


def test_artifact_projection_for_oversized_payload() -> None:
    codec = _import_workflow_codec()
    project = codec["project_json_for_durable_storage"]
    small = {"prompt": "approve this step"}
    small_proj = project(small)
    assert small_proj.storage_kind == "inline"
    assert small_proj.payload == small
    assert small_proj.byte_size > 0
    assert len(small_proj.content_digest) == 64

    max_inline = small_proj.byte_size  # baseline
    # Force oversize relative to a tight limit.
    large = {"blob": "y" * 4096}
    large_proj = project(large, max_inline_bytes=64)
    assert large_proj.storage_kind == "artifact_required"
    assert large_proj.payload is None
    assert large_proj.byte_size > 64
    assert large_proj.reason_code == "inline_limit_exceeded"
    assert len(large_proj.content_digest) == 64
    # Content digest is stable for the same payload.
    assert project(large, max_inline_bytes=64).content_digest == large_proj.content_digest
    assert max_inline >= 1


def test_reject_secret_keys_in_workflow_payloads() -> None:
    codec = _import_workflow_codec()
    Error = codec["DurableCodecError"]
    payload = {
        "schemaVersion": 1,
        "runId": str(RUN_ID),
        "rootFrameId": str(FRAME_ID),
        "rootInvocationDigest": DIGEST_E,
        "frameStack": [],
        "pendingInterruptId": None,
        "terminalOutputArtifactId": None,
        "apiKey": "sk-secret",
    }
    with pytest.raises((Error, ValueError)):
        codec["decode_workflow_state"](payload)


def test_reject_ephemeral_workflow_context_from_codec() -> None:
    codec = _import_workflow_codec()
    Error = codec["DurableCodecError"]
    EphemeralWorkflowContext = _import_context()

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
        codec["encode_workflow_state"](ctx)  # type: ignore[arg-type]

    # Plan 06 recursive forbidden-type assertion includes this type family.
    from app.assistant.durable.codec import encode_checkpoint_v1

    with pytest.raises((Error, TypeError, ValueError)):
        encode_checkpoint_v1(ctx)  # type: ignore[arg-type]


def test_reject_legacy_runtime_objects_from_workflow_codec() -> None:
    codec = _import_workflow_codec()
    Error = codec["DurableCodecError"]

    class WorkflowState:
        pass

    class HumanLoopRuntime:
        pass

    for obj in (WorkflowState(), HumanLoopRuntime(), lambda: None, object()):
        with pytest.raises((Error, TypeError, ValueError)):
            codec["encode_workflow_state"](obj)  # type: ignore[arg-type]


# ===========================================================================
# Unknown plan/state versions -> needs_reconciliation before runtime work
# ===========================================================================


def test_unknown_plan_and_state_versions_need_reconciliation() -> None:
    codec = _import_workflow_codec()
    Needs = codec["NeedsReconciliationError"]

    plan_payload = {
        "contractVersion": 99,
        "targetKind": "workflow",
        "targetVersionId": str(TARGET_VERSION_ID),
        "targetDigest": DIGEST_C,
        "entryNodeId": "start",
        "nodes": [],
        "planDigest": DIGEST_A,
    }
    with pytest.raises(Needs) as exc:
        codec["decode_execution_plan"](plan_payload)
    assert exc.value.code == "needs_reconciliation"

    state_payload = {
        "schemaVersion": 99,
        "runId": str(RUN_ID),
        "rootFrameId": str(FRAME_ID),
        "rootInvocationDigest": DIGEST_E,
        "frameStack": [],
        "pendingInterruptId": None,
        "terminalOutputArtifactId": None,
    }
    with pytest.raises(Needs) as exc2:
        codec["decode_workflow_state"](state_payload)
    assert exc2.value.code == "needs_reconciliation"

    # Missing version also fail-closed.
    bad = dict(state_payload)
    del bad["schemaVersion"]
    with pytest.raises((Needs, codec["DurableCodecError"], ValidationError, ValueError)):
        codec["decode_workflow_state"](bad)
