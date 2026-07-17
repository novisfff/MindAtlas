"""Plan 07 Task 3: one-boundary durable Workflow/Agent runner.

Deterministic interpreter over frozen DurableExecutionPlanV1. Executes at most
one node/agent-round boundary per prepared unit. Never calls graph.invoke() or
HumanLoopRuntime.create_and_wait. Reuses Plan 06 prepare/result Checkpoint ports
when committing V2 workflow state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.durable.contracts import DurableExecutionUnitV2
from app.assistant.durable.repository import DurableCommitResult, LeaseToken
from app.assistant.workflow.durable.adapters import (
    AdapterExecutionContext,
    DefaultDurableNodeAdapterRegistry,
    DurableAdapterError,
    PortableNodeBag,
    build_default_registry,
)
from app.assistant.workflow.durable.contracts import (
    DurableBranchDecisionV1,
    DurableCallFrameV1,
    DurableExecutionPlanV1,
    DurableLoopCursorV1,
    DurableNodePlanV1,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
    derive_frame_id,
    derive_node_visit_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class BoundaryKind(str, Enum):
    NODE_COMPLETED = "node_completed"
    CHILD_PUSHED = "child_pushed"
    CHILD_COMPLETED = "child_completed"
    HUMAN_PAUSE = "human_pause"
    ROOT_COMPLETED = "root_completed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"
    FAILED = "failed"


@dataclass(slots=True)
class DurableFrameMaterial:
    """Exact plan + node configs for one frame target version."""

    plan: DurableExecutionPlanV1
    node_configs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # Optional portable bag seed (inputs) for this frame
    inputs: Mapping[str, Any] = field(default_factory=dict)
    # Portable bag snapshot for cross-process rehydrate (Task 3 minimal durability).
    # Serialized into Checkpoint/workflow material between boundaries; never Legacy
    # WorkflowState. prepare_boundary rehydrates process-local bag when missing.
    bag_snapshot: Mapping[str, Any] | None = None


@dataclass(slots=True)
class PreparedBoundary:
    """Result of prepare: pre-execution frame + unit ready for adapter I/O."""

    workflow_state: DurableWorkflowStateV1
    frame: DurableCallFrameV1
    node: DurableNodePlanV1
    node_id: str
    node_visit_id: str
    unit: DurableExecutionUnitV2
    bag: PortableNodeBag
    input_digest: str
    root_call_id: str


@dataclass(slots=True)
class BoundaryResult:
    """Post-adapter one-boundary result (pure; apply separately)."""

    kind: BoundaryKind
    node_id: str
    node_visit_id: str
    next_node_id: str | None = None
    branch_decision: DurableBranchDecisionV1 | None = None
    loop_cursor: DurableLoopCursorV1 | None = None
    child_frame: DurableCallFrameV1 | None = None
    child_inputs: Mapping[str, Any] | None = None
    pause_proposal: DurablePauseProposalV1 | None = None
    agent_loop_continuation: Any = None
    output_artifact_id: UUID | None = None
    bag: PortableNodeBag | None = None
    # Portable bag projection after successful pure/node completion (for material).
    bag_snapshot: Mapping[str, Any] | None = None
    reason_code: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# State builders
# ---------------------------------------------------------------------------


def build_initial_workflow_state(
    *,
    run_id: UUID,
    plan: DurableExecutionPlanV1,
    root_invocation_digest: str,
    invocation_call_id: str,
    target_id: UUID,
    inputs: Mapping[str, Any] | None = None,
    owner_skill_package_id: UUID | None = None,
    owner_skill_version_id: UUID | None = None,
) -> DurableWorkflowStateV1:
    """Create root frame ready at plan entry node (not yet executing)."""
    frame_id = derive_frame_id(
        root_invocation_digest=root_invocation_digest,
        parent_path="root",
        target_version_id=plan.target_version_id,
        invocation_call_id=invocation_call_id,
    )
    frame = DurableCallFrameV1(
        frame_id=frame_id,
        parent_frame_id=None,
        invocation_call_id=invocation_call_id,
        owner_skill_package_id=owner_skill_package_id,
        owner_skill_version_id=owner_skill_version_id,
        target_kind=plan.target_kind,
        target_id=target_id,
        target_version_id=plan.target_version_id,
        target_digest=plan.target_digest,
        execution_plan_digest=plan.plan_digest,
        current_node_id=plan.entry_node_id,
        node_visit_id=None,
        node_visit_ordinal=0,
        execution_attempt=1,
        phase="ready",
    )
    return DurableWorkflowStateV1(
        run_id=run_id,
        root_frame_id=frame_id,
        root_invocation_digest=root_invocation_digest,
        frame_stack=(frame,),
        pending_interrupt_id=None,
        terminal_output_artifact_id=None,
    )


def _plan_nodes_by_id(plan: DurableExecutionPlanV1) -> dict[str, DurableNodePlanV1]:
    return {n.node_id: n for n in plan.nodes}


def _logical_unit_id(*, frame_id: UUID, node_visit_id: str) -> str:
    return f"workflow_node:{frame_id}:{node_visit_id}"


def _agent_logical_unit_id(*, frame_id: UUID, node_visit_id: str) -> str:
    return f"agent_round:{frame_id}:{node_visit_id}"


def _input_digest(
    *,
    frame: DurableCallFrameV1,
    node_id: str,
    node_visit_id: str,
    bag: PortableNodeBag,
) -> str:
    from app.assistant.domain.digests import sha256_canonical_json

    return sha256_canonical_json(
        {
            "frameId": str(frame.frame_id),
            "nodeId": node_id,
            "nodeVisitId": node_visit_id,
            "inputs": bag.inputs,
            "nodeOutputs": bag.node_outputs,
            "variables": bag.variables,
        }
    )


def _replace_top_frame(
    state: DurableWorkflowStateV1, frame: DurableCallFrameV1
) -> DurableWorkflowStateV1:
    stack = tuple(state.frame_stack[:-1]) + (frame,)
    return DurableWorkflowStateV1(
        run_id=state.run_id,
        root_frame_id=state.root_frame_id,
        root_invocation_digest=state.root_invocation_digest,
        frame_stack=stack,
        pending_interrupt_id=state.pending_interrupt_id,
        terminal_output_artifact_id=state.terminal_output_artifact_id,
    )


def _with_stack(
    state: DurableWorkflowStateV1,
    stack: Sequence[DurableCallFrameV1],
    *,
    terminal_output_artifact_id: UUID | None = None,
    pending_interrupt_id: UUID | None = None,
) -> DurableWorkflowStateV1:
    return DurableWorkflowStateV1(
        run_id=state.run_id,
        root_frame_id=state.root_frame_id,
        root_invocation_digest=state.root_invocation_digest,
        frame_stack=tuple(stack),
        pending_interrupt_id=(
            pending_interrupt_id
            if pending_interrupt_id is not None
            else state.pending_interrupt_id
        ),
        terminal_output_artifact_id=(
            terminal_output_artifact_id
            if terminal_output_artifact_id is not None
            else state.terminal_output_artifact_id
        ),
    )


def _copy_frame(
    src: DurableCallFrameV1,
    **overrides: Any,
) -> DurableCallFrameV1:
    """Build a DurableCallFrameV1 from src with field overrides."""
    base = {
        "frame_id": src.frame_id,
        "parent_frame_id": src.parent_frame_id,
        "invocation_call_id": src.invocation_call_id,
        "owner_skill_package_id": src.owner_skill_package_id,
        "owner_skill_version_id": src.owner_skill_version_id,
        "target_kind": src.target_kind,
        "target_id": src.target_id,
        "target_version_id": src.target_version_id,
        "target_digest": src.target_digest,
        "execution_plan_digest": src.execution_plan_digest,
        "current_node_id": src.current_node_id,
        "node_visit_id": src.node_visit_id,
        "node_visit_ordinal": src.node_visit_ordinal,
        "execution_attempt": src.execution_attempt,
        "phase": src.phase,
        "node_state_artifact_id": src.node_state_artifact_id,
        "node_output_artifact_ids": src.node_output_artifact_ids,
        "branch_decisions": src.branch_decisions,
        "loop_cursors": src.loop_cursors,
        "child_frame_ids": src.child_frame_ids,
        "agent_loop_continuation": src.agent_loop_continuation,
    }
    base.update(overrides)
    return DurableCallFrameV1(**base)


def _project_bag_snapshot(bag: PortableNodeBag | None) -> dict[str, Any] | None:
    if bag is None:
        return None
    return bag.to_snapshot()


def _build_boundary_bag_artifact(
    *,
    run_id: UUID,
    frame_id: UUID,
    node_visit_id: str,
    bag_snapshot: Mapping[str, Any],
) -> Any:
    """Build a deterministic inline Artifact for one committed portable bag."""
    import json

    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.models import AssistantRunArtifact

    body = json.dumps(
        {
            "contractVersion": 1,
            "kind": "node_bag_snapshot",
            "runId": str(run_id),
            "frameId": str(frame_id),
            "nodeVisitId": str(node_visit_id),
            "bagSnapshot": dict(bag_snapshot),
        },
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    digest = sha256_bytes(body)
    return AssistantRunArtifact(
        id=UUID(hex=digest[:32]),
        run_id=run_id,
        kind="node_bag_snapshot",
        media_type="application/json",
        display_label=f"bag:{frame_id}",
        storage_kind="inline",
        byte_size=len(body),
        content_sha256=digest,
        inline_bytes=body,
        object_key=None,
        metadata_json={
            "frameId": str(frame_id),
            "nodeVisitId": str(node_visit_id),
        },
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class DurableWorkflowRunner:
    """One-boundary durable Workflow/Agent interpreter."""

    def __init__(
        self,
        *,
        registry: DefaultDurableNodeAdapterRegistry | None = None,
        cancellation_probe: Any = None,
        lease_heartbeat: Callable[[], bool] | None = None,
        agent_round_executor: Callable[..., Mapping[str, Any]] | None = None,
        capability_gateway: Any = None,
        exact_dependency_resolver: Any = None,
        bags: dict[UUID, PortableNodeBag] | None = None,
        pause_effect_port: Any = None,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.cancellation_probe = cancellation_probe
        self.lease_heartbeat = lease_heartbeat
        self.agent_round_executor = agent_round_executor
        self.capability_gateway = capability_gateway
        self.exact_dependency_resolver = exact_dependency_resolver
        # Worker-unit ephemeral pause staging port (Plan 07 §5.3). Never serialized.
        self.pause_effect_port = pause_effect_port
        # Process-local bags keyed by frame_id (never durable by themselves)
        self._bags: dict[UUID, PortableNodeBag] = dict(bags or {})

    def get_bag(self, frame_id: UUID, *, inputs: Mapping[str, Any] | None = None) -> PortableNodeBag:
        bag = self._bags.get(frame_id)
        if bag is None:
            bag = PortableNodeBag(inputs=dict(inputs or {}))
            self._bags[frame_id] = bag
        return bag

    def export_bag_snapshot(self, frame_id: UUID) -> dict[str, Any] | None:
        """Export process-local bag as a portable snapshot for material/checkpoint."""
        bag = self._bags.get(frame_id)
        if bag is None:
            return None
        return bag.to_snapshot()

    def load_bag_snapshot(
        self,
        frame_id: UUID,
        snapshot: Mapping[str, Any] | None,
        *,
        inputs: Mapping[str, Any] | None = None,
    ) -> PortableNodeBag:
        """Install a portable bag snapshot into process-local storage (new-process recovery)."""
        if snapshot:
            bag = PortableNodeBag.from_snapshot(snapshot)
            if inputs and not bag.inputs:
                bag.inputs = dict(inputs)
        else:
            bag = PortableNodeBag(inputs=dict(inputs or {}))
        self._bags[frame_id] = bag
        return bag

    def prepare_boundary(
        self,
        *,
        state: DurableWorkflowStateV1,
        material: DurableFrameMaterial,
        reserved_budget_revision: int = 0,
        root_call_id: str | None = None,
    ) -> PreparedBoundary:
        """Select top frame + next node; enter phase=executing; build prepared unit.

        On recovery retry (frame already executing with same node), keep node_visit_id
        and increment execution_attempt only.

        When process-local bag is missing, rehydrate from material.bag_snapshot so a
        simulated new process with the same workflow_state + bag_snapshot can continue.
        """
        if not state.frame_stack:
            raise DurableAdapterError(
                "workflow state has empty frame_stack",
                reason_code="empty_frame_stack",
            )
        frame = state.frame_stack[-1]
        plan = material.plan
        if plan.plan_digest != frame.execution_plan_digest:
            raise DurableAdapterError(
                "frame execution_plan_digest does not match material plan",
                reason_code="plan_digest_mismatch",
            )
        nodes = _plan_nodes_by_id(plan)
        node_id = frame.current_node_id or plan.entry_node_id
        if node_id not in nodes:
            raise DurableAdapterError(
                f"current node {node_id!r} not in plan",
                reason_code="unknown_node",
            )
        node = nodes[node_id]

        # Rehydrate process-local bag from portable snapshot when missing (new process).
        if frame.frame_id not in self._bags and material.bag_snapshot:
            bag = self.load_bag_snapshot(
                frame.frame_id,
                material.bag_snapshot,
                inputs=material.inputs or {},
            )
        else:
            bag = self.get_bag(frame.frame_id, inputs=material.inputs or {})
        # Seed bag from material inputs on first prepare of frame
        if material.inputs and not bag.inputs:
            bag.inputs = dict(material.inputs)

        is_retry = (
            frame.phase == "executing"
            and frame.current_node_id == node_id
            and frame.node_visit_id is not None
        )
        if is_retry:
            # Crash recovery: same logical visit, attempt only increments.
            node_visit_id = str(frame.node_visit_id)
            ordinal = int(frame.node_visit_ordinal)
            attempt = int(frame.execution_attempt) + 1
        else:
            # Ready frames store the *next* visit ordinal (0 on initial frame).
            # Executing frames without a visit id are treated as fresh starts.
            if frame.phase == "ready" and frame.node_visit_id is None:
                ordinal = int(frame.node_visit_ordinal)
            else:
                ordinal = int(frame.node_visit_ordinal) + 1
            node_visit_id = derive_node_visit_id(
                frame_id=frame.frame_id,
                node_id=node_id,
                node_visit_ordinal=ordinal,
            )
            attempt = 1

        executing_frame = _copy_frame(
            frame,
            current_node_id=node_id,
            node_visit_id=node_visit_id,
            node_visit_ordinal=ordinal,
            execution_attempt=attempt,
            phase="executing",
        )
        new_state = _replace_top_frame(state, executing_frame)

        # Nested agent frames (target_kind=agent) use agent_round units.
        if frame.target_kind == "agent":
            unit_kind = "agent_round"
            logical = _agent_logical_unit_id(
                frame_id=frame.frame_id, node_visit_id=node_visit_id
            )
        else:
            unit_kind = "workflow_node"
            logical = _logical_unit_id(
                frame_id=frame.frame_id, node_visit_id=node_visit_id
            )

        unit = DurableExecutionUnitV2(
            logical_unit_id=logical,
            kind=unit_kind,  # type: ignore[arg-type]
            state="prepared",
            provider_round=None,
            call_ids=(),
            attempt=attempt,
            reserved_budget_revision=int(reserved_budget_revision),
            started_budget_revision=None,
        )
        digest = _input_digest(
            frame=executing_frame,
            node_id=node_id,
            node_visit_id=node_visit_id,
            bag=bag,
        )
        return PreparedBoundary(
            workflow_state=new_state,
            frame=executing_frame,
            node=node,
            node_id=node_id,
            node_visit_id=node_visit_id,
            unit=unit,
            bag=bag,
            input_digest=digest,
            root_call_id=root_call_id or state.frame_stack[0].invocation_call_id,
        )

    def mark_started(
        self,
        *,
        prepared: PreparedBoundary,
        budget_revision: int,
    ) -> PreparedBoundary:
        """Transition prepared unit → started immediately before adapter I/O."""
        started_unit = DurableExecutionUnitV2(
            logical_unit_id=prepared.unit.logical_unit_id,
            kind=prepared.unit.kind,
            state="started",
            provider_round=prepared.unit.provider_round,
            call_ids=prepared.unit.call_ids,
            attempt=prepared.unit.attempt,
            reserved_budget_revision=prepared.unit.reserved_budget_revision,
            started_budget_revision=int(budget_revision),
        )
        return PreparedBoundary(
            workflow_state=prepared.workflow_state,
            frame=prepared.frame,
            node=prepared.node,
            node_id=prepared.node_id,
            node_visit_id=prepared.node_visit_id,
            unit=started_unit,
            bag=prepared.bag,
            input_digest=prepared.input_digest,
            root_call_id=prepared.root_call_id,
        )

    def execute_boundary(
        self,
        *,
        prepared: PreparedBoundary,
        material: DurableFrameMaterial,
        child_materials: Mapping[str, DurableFrameMaterial] | None = None,
    ) -> BoundaryResult:
        """Run one ephemeral adapter until a deterministic boundary."""
        # Lease / cancellation probes immediately before invocation
        if self.lease_heartbeat is not None:
            try:
                alive = bool(self.lease_heartbeat())
            except Exception:  # noqa: BLE001
                alive = False
            if not alive:
                return BoundaryResult(
                    kind=BoundaryKind.LEASE_LOST,
                    node_id=prepared.node_id,
                    node_visit_id=prepared.node_visit_id,
                    reason_code="lease_lost",
                )
        if self.cancellation_probe is not None:
            cancelled = False
            try:
                if hasattr(self.cancellation_probe, "is_cancelled"):
                    cancelled = bool(self.cancellation_probe.is_cancelled())
                elif callable(self.cancellation_probe):
                    cancelled = bool(self.cancellation_probe())
            except Exception:  # noqa: BLE001
                cancelled = False
            if cancelled:
                return BoundaryResult(
                    kind=BoundaryKind.CANCELLED,
                    node_id=prepared.node_id,
                    node_visit_id=prepared.node_visit_id,
                    reason_code="cancelled",
                )

        adapter = self.registry.require(prepared.node)
        cfg = dict(material.node_configs.get(prepared.node_id) or {})
        ctx = AdapterExecutionContext(
            run_id=prepared.workflow_state.run_id,
            root_invocation_digest=prepared.workflow_state.root_invocation_digest,
            root_call_id=prepared.root_call_id,
            plan=material.plan,
            node=prepared.node,
            frame=prepared.frame,
            workflow_state=prepared.workflow_state,
            node_visit_id=prepared.node_visit_id,
            node_config=cfg,
            bag=prepared.bag,
            child_materials=dict(child_materials or {}),
            capability_gateway=self.capability_gateway,
            exact_dependency_resolver=self.exact_dependency_resolver,
            agent_round_executor=self.agent_round_executor,
            pause_effect_port=self.pause_effect_port,
        )
        try:
            raw = adapter.execute(ctx)
        except DurableAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DurableAdapterError(
                f"adapter {prepared.node.adapter_key!r} failed: {exc}",
                reason_code="adapter_execution_failed",
            ) from exc

        kind = BoundaryKind(raw.kind) if raw.kind in BoundaryKind._value2member_map_ else BoundaryKind.FAILED
        result_bag = raw.bag or prepared.bag
        # Project portable bag on successful pure/node boundaries for material.
        bag_snapshot = None
        if kind in {
            BoundaryKind.NODE_COMPLETED,
            BoundaryKind.ROOT_COMPLETED,
            BoundaryKind.CHILD_COMPLETED,
            BoundaryKind.CHILD_PUSHED,
            BoundaryKind.HUMAN_PAUSE,
        }:
            bag_snapshot = _project_bag_snapshot(result_bag)
        return BoundaryResult(
            kind=kind,
            node_id=prepared.node_id,
            node_visit_id=prepared.node_visit_id,
            next_node_id=raw.next_node_id,
            branch_decision=raw.branch_decision,
            loop_cursor=raw.loop_cursor,
            child_frame=raw.child_frame,
            child_inputs=raw.child_inputs,
            pause_proposal=raw.pause_proposal,
            agent_loop_continuation=raw.agent_loop_continuation,
            output_artifact_id=raw.output_artifact_id,
            bag=result_bag,
            bag_snapshot=bag_snapshot,
            reason_code=raw.reason_code,
            detail=raw.detail,
        )

    def apply_boundary_result(
        self,
        *,
        state: DurableWorkflowStateV1,
        result: BoundaryResult,
    ) -> DurableWorkflowStateV1:
        """Persist branch/loop/child/agent updates into portable workflow state.

        Branch/loop/child state is applied here *before* the next boundary follows
        the chosen path (caller must apply before next prepare).
        """
        if not state.frame_stack:
            raise DurableAdapterError(
                "cannot apply result to empty frame stack",
                reason_code="empty_frame_stack",
            )
        top = state.frame_stack[-1]

        # Keep process-local bag in sync with result bag when present.
        if result.bag is not None:
            self._bags[top.frame_id] = result.bag

        if result.kind in {BoundaryKind.CANCELLED, BoundaryKind.LEASE_LOST}:
            cancelled = _copy_frame(top, phase="cancelled")
            return _replace_top_frame(state, cancelled)

        if result.kind == BoundaryKind.HUMAN_PAUSE:
            if result.pause_proposal is None:
                raise DurableAdapterError(
                    "human_pause requires pause_proposal",
                    reason_code="durable_pause_protocol_error",
                )
            return result.pause_proposal.proposed_workflow_state

        if result.kind == BoundaryKind.CHILD_PUSHED:
            if result.child_frame is None:
                raise DurableAdapterError(
                    "child_pushed requires child_frame",
                    reason_code="missing_child_frame",
                )
            # Record the call-node successor at push time so CHILD_COMPLETED can
            # advance the parent without fixture-hardcoded node-id maps.
            branch_decisions = top.branch_decisions
            if result.branch_decision is not None:
                branch_decisions = tuple(
                    d for d in branch_decisions if d.node_id != result.branch_decision.node_id
                ) + (result.branch_decision,)
            parent = _copy_frame(
                top,
                phase="child_active",
                branch_decisions=branch_decisions,
                child_frame_ids=top.child_frame_ids + (result.child_frame.frame_id,),
            )
            self.get_bag(
                result.child_frame.frame_id,
                inputs=dict(result.child_inputs or {}),
            )
            stack = tuple(state.frame_stack[:-1]) + (parent, result.child_frame)
            return _with_stack(state, stack)

        if result.kind == BoundaryKind.CHILD_COMPLETED:
            if top.parent_frame_id is None:
                # Treat as root completion if no parent
                return self.apply_boundary_result(
                    state=state,
                    result=BoundaryResult(
                        kind=BoundaryKind.ROOT_COMPLETED,
                        node_id=result.node_id,
                        node_visit_id=result.node_visit_id,
                        output_artifact_id=result.output_artifact_id,
                        bag=result.bag,
                        bag_snapshot=result.bag_snapshot,
                    ),
                )
            # Pop child; advance parent past call node using successor recorded at push.
            parent = state.frame_stack[-2]
            parent_bag = self.get_bag(parent.frame_id)
            child_payload: dict[str, Any] = {}
            if result.bag is not None:
                child_payload = result.bag.output_of(result.node_id)
            if parent.current_node_id:
                parent_bag.set_output(parent.current_node_id, child_payload)
            next_node = _advance_parent_after_child(parent, result)
            arts = parent.node_output_artifact_ids
            if result.output_artifact_id is not None:
                arts = arts + (result.output_artifact_id,)
            parent_ready = _copy_frame(
                parent,
                current_node_id=next_node,
                node_visit_id=None,  # next prepare will mint
                node_visit_ordinal=int(parent.node_visit_ordinal) + 1,
                execution_attempt=1,
                phase="ready",
                node_output_artifact_ids=arts,
                agent_loop_continuation=None,
            )
            stack = tuple(state.frame_stack[:-2]) + (parent_ready,)
            # Drop child bag
            self._bags.pop(top.frame_id, None)
            result.bag = parent_bag
            result.bag_snapshot = _project_bag_snapshot(parent_bag)
            return _with_stack(state, stack)

        if result.kind == BoundaryKind.ROOT_COMPLETED:
            arts = top.node_output_artifact_ids
            if result.output_artifact_id is not None:
                arts = arts + (result.output_artifact_id,)
            completed = _copy_frame(
                top,
                phase="completed",
                node_output_artifact_ids=arts,
                agent_loop_continuation=None,
            )
            return DurableWorkflowStateV1(
                run_id=state.run_id,
                root_frame_id=state.root_frame_id,
                root_invocation_digest=state.root_invocation_digest,
                frame_stack=tuple(state.frame_stack[:-1]) + (completed,),
                pending_interrupt_id=None,
                terminal_output_artifact_id=result.output_artifact_id
                or state.terminal_output_artifact_id,
            )

        if result.kind == BoundaryKind.NODE_COMPLETED:
            branch_decisions = top.branch_decisions
            if result.branch_decision is not None:
                # Replace any prior decision for same node_id, then append
                branch_decisions = tuple(
                    d for d in branch_decisions if d.node_id != result.branch_decision.node_id
                ) + (result.branch_decision,)

            loop_cursors = top.loop_cursors
            if result.loop_cursor is not None:
                loop_cursors = tuple(
                    c
                    for c in loop_cursors
                    if c.loop_node_id != result.loop_cursor.loop_node_id
                ) + (result.loop_cursor,)

            arts = top.node_output_artifact_ids
            if result.output_artifact_id is not None:
                arts = arts + (result.output_artifact_id,)

            next_node = result.next_node_id
            # Ready frames store the *next* visit ordinal so re-entry (agent rounds,
            # loop iterations, next node) mints a new stable node_visit_id.
            next_ordinal = int(top.node_visit_ordinal) + 1
            ready = _copy_frame(
                top,
                current_node_id=next_node,
                node_visit_id=None,
                node_visit_ordinal=next_ordinal,
                execution_attempt=1,
                phase="ready",
                node_output_artifact_ids=arts,
                branch_decisions=branch_decisions,
                loop_cursors=loop_cursors,
                agent_loop_continuation=result.agent_loop_continuation,
            )
            return _replace_top_frame(state, ready)

        if result.kind == BoundaryKind.FAILED:
            failed = _copy_frame(top, phase="failed")
            return _replace_top_frame(state, failed)

        raise DurableAdapterError(
            f"unhandled boundary kind: {result.kind}",
            reason_code="unhandled_boundary_kind",
        )


def _advance_parent_after_child(
    parent: DurableCallFrameV1,
    result: BoundaryResult,
) -> str | None:
    """After child completes, parent advances to the successor of the call node.

    Successor is the branch decision recorded at CHILD_PUSHED for the call node
    (parent.current_node_id). No fixture name maps. Child result.next_node_id is
    intentionally ignored — it belongs to the child graph, not the parent.
    """
    del result  # child result does not own parent successor
    call = parent.current_node_id
    if call:
        for decision in reversed(parent.branch_decisions):
            if decision.node_id == call and decision.chosen_target_node_id:
                return decision.chosen_target_node_id
    # Unknown successor: stay on call node (fail-visible; no name-based fixture maps)
    return call


# ---------------------------------------------------------------------------
# Plan 06 Checkpoint V2 commit helpers
# ---------------------------------------------------------------------------


def commit_workflow_boundary_prepare(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    prepared: PreparedBoundary,
    as_started: bool = False,
    phase: str = "dispatching_calls",
    next_action_kind: str = "continue_child",
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
) -> DurableCommitResult:
    """Append a Checkpoint v2 with workflow_state + inflight workflow/agent unit."""
    from app.assistant.durable.checkpoints import commit_checkpoint_v2

    unit = prepared.unit
    if as_started and unit.state != "started":
        raise ValueError("as_started requires unit.state=started")
    if not as_started and unit.state != "prepared":
        raise ValueError("prepare commit requires unit.state=prepared")

    # Embed input_digest so result commit can re-verify visit/digest continuity.
    reason_tag = "started" if as_started else "prepared"
    reason = f"{reason_tag}|input_digest:{prepared.input_digest}|node_visit:{prepared.node_visit_id}"

    return commit_checkpoint_v2(
        db,
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        phase=phase,
        next_action_kind=next_action_kind,
        unit=unit,
        workflow_state=prepared.workflow_state,
        budget_payload=budget_payload,
        budget_digest=budget_digest,
        budget_revision_number=budget_revision_number,
        reason=reason,
    )


def commit_workflow_boundary_result(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    workflow_state: DurableWorkflowStateV1,
    boundary_result: BoundaryResult,
    completed_logical_unit_id: str | None = None,
    prepared: PreparedBoundary | None = None,
    expected_logical_unit_id: str | None = None,
    expected_node_visit_id: str | None = None,
    expected_input_digest: str | None = None,
    phase: str = "dispatching_calls",
    next_action_kind: str = "continue_child",
    pending_interrupt_id: UUID | None = None,
    budget_suspension: Any = None,
    reason: str = "result",
) -> DurableCommitResult:
    """Append a post-boundary Checkpoint v2 with updated workflow_state, no inflight.

    Re-verifies prepared unit identity (logical_unit_id / node_visit_id / input_digest)
    against the last Checkpoint's inflight unit (and optional prepared/expected args)
    before dropping inflight. Mismatch fails with stable reason_code.
    """
    from app.assistant.durable.checkpoints import commit_checkpoint_v2
    from app.assistant.durable.codec import decode_checkpoint
    from app.assistant.durable.models import AssistantRunCheckpoint
    from app.assistant.models import AssistantChatRun

    # Resolve expected identity from prepared or explicit args.
    exp_logical = expected_logical_unit_id
    exp_visit = expected_node_visit_id
    exp_digest = expected_input_digest
    if prepared is not None:
        exp_logical = exp_logical or prepared.unit.logical_unit_id
        exp_visit = exp_visit or prepared.node_visit_id
        exp_digest = exp_digest or prepared.input_digest
    if completed_logical_unit_id is not None and exp_logical is None:
        exp_logical = completed_logical_unit_id

    # Load last Checkpoint's inflight unit and verify continuity.
    run = db.get(AssistantChatRun, run_id)
    if run is None:
        raise DurableAdapterError(
            f"run {run_id} not found for result commit",
            reason_code="run_not_found",
        )
    if run.current_checkpoint_id is None:
        raise DurableAdapterError(
            "result commit requires a prior prepared/started Checkpoint",
            reason_code="missing_prepared_checkpoint",
        )
    ck = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
    if ck is None:
        raise DurableAdapterError(
            "current checkpoint row missing for result commit",
            reason_code="missing_prepared_checkpoint",
        )
    try:
        decoded = decode_checkpoint(ck.state_payload)
    except Exception as exc:  # noqa: BLE001
        raise DurableAdapterError(
            f"failed to decode prior checkpoint: {exc}",
            reason_code="checkpoint_decode_failed",
        ) from exc

    inflight = getattr(decoded, "inflight_unit", None)
    if inflight is None:
        raise DurableAdapterError(
            "result commit requires inflight unit on last Checkpoint",
            reason_code="missing_inflight_unit",
        )

    inflight_logical = getattr(inflight, "logical_unit_id", None)
    if exp_logical is not None and inflight_logical != exp_logical:
        raise DurableAdapterError(
            f"result logical_unit_id mismatch: expected {exp_logical!r}, "
            f"inflight {inflight_logical!r}",
            reason_code="unit_identity_mismatch",
        )

    # node_visit_id is embedded in logical_unit_id (workflow_node|agent_round:frame:visit)
    # and also carried on workflow_state top frame. Verify both when available.
    if exp_visit is not None:
        inflight_ws = getattr(decoded, "workflow_state", None)
        if inflight_ws is not None and getattr(inflight_ws, "frame_stack", None):
            top = inflight_ws.frame_stack[-1]
            prior_visit = getattr(top, "node_visit_id", None)
            if prior_visit is not None and prior_visit != exp_visit:
                raise DurableAdapterError(
                    f"result node_visit_id mismatch: expected {exp_visit!r}, "
                    f"prior {prior_visit!r}",
                    reason_code="node_visit_mismatch",
                )
        # Also require visit substring in logical unit id when present.
        if inflight_logical and exp_visit not in str(inflight_logical):
            raise DurableAdapterError(
                f"result node_visit_id {exp_visit!r} not present in "
                f"inflight logical_unit_id {inflight_logical!r}",
                reason_code="node_visit_mismatch",
            )

    prior_reason = getattr(ck, "reason", None) or ""
    prior_digest_from_ck: str | None = None
    prior_visit_from_ck: str | None = None
    if isinstance(prior_reason, str) and prior_reason:
        # Format: prepared|input_digest:<hex>|node_visit:<id>
        for part in prior_reason.split("|"):
            if part.startswith("input_digest:"):
                prior_digest_from_ck = part.split(":", 1)[1] or None
            elif part.startswith("node_visit:"):
                prior_visit_from_ck = part.split(":", 1)[1] or None

    if exp_visit is not None and prior_visit_from_ck is not None:
        if prior_visit_from_ck != exp_visit:
            raise DurableAdapterError(
                f"result node_visit_id mismatch vs prepared checkpoint: "
                f"expected {exp_visit!r}, prior {prior_visit_from_ck!r}",
                reason_code="node_visit_mismatch",
            )

    if exp_digest is not None:
        if not exp_digest:
            raise DurableAdapterError(
                "result commit expected_input_digest is empty",
                reason_code="input_digest_mismatch",
            )
        if prior_digest_from_ck is not None and prior_digest_from_ck != exp_digest:
            raise DurableAdapterError(
                f"result input_digest mismatch: expected {exp_digest!r}, "
                f"prior {prior_digest_from_ck!r}",
                reason_code="input_digest_mismatch",
            )
        # When prepared is supplied but prior checkpoint has no digest marker
        # (older path), require unit identity already matched above — still fail
        # closed if caller explicitly expected a digest that cannot be checked
        # against an empty prior marker when completed_logical differs.
        if prior_digest_from_ck is None and prepared is not None:
            # Continuity is established via logical_unit_id + node_visit checks.
            pass

    completed_id = completed_logical_unit_id or exp_logical or inflight_logical

    workflow_state_for_commit = workflow_state
    artifact_ids: tuple[UUID, ...] = ()
    extra_child_rows: tuple[Any, ...] = ()
    successful_with_bag = boundary_result.kind in {
        BoundaryKind.NODE_COMPLETED,
        BoundaryKind.ROOT_COMPLETED,
        BoundaryKind.CHILD_PUSHED,
        BoundaryKind.CHILD_COMPLETED,
    }
    if successful_with_bag:
        if boundary_result.bag_snapshot is None:
            raise DurableAdapterError(
                "successful boundary result missing bag_snapshot",
                reason_code="missing_bag_snapshot",
            )
        prior_ws = getattr(decoded, "workflow_state", None)
        prior_top = (
            prior_ws.frame_stack[-1]
            if prior_ws is not None and getattr(prior_ws, "frame_stack", None)
            else None
        )
        owning_frame_id = getattr(prior_top, "frame_id", None)
        current_ids = {frame.frame_id for frame in workflow_state.frame_stack}
        if owning_frame_id not in current_ids:
            owning_frame_id = (
                workflow_state.frame_stack[-1].frame_id
                if workflow_state.frame_stack
                else None
            )
        if owning_frame_id is None:
            raise DurableAdapterError(
                "successful boundary has no owning frame",
                reason_code="missing_boundary_frame",
            )
        bag_artifact = _build_boundary_bag_artifact(
            run_id=run_id,
            frame_id=owning_frame_id,
            node_visit_id=boundary_result.node_visit_id,
            bag_snapshot=boundary_result.bag_snapshot,
        )
        stamped_stack = tuple(
            _copy_frame(frame, node_state_artifact_id=bag_artifact.id)
            if frame.frame_id == owning_frame_id
            else frame
            for frame in workflow_state.frame_stack
        )
        workflow_state_for_commit = DurableWorkflowStateV1(
            run_id=workflow_state.run_id,
            root_frame_id=workflow_state.root_frame_id,
            root_invocation_digest=workflow_state.root_invocation_digest,
            frame_stack=stamped_stack,
            pending_interrupt_id=workflow_state.pending_interrupt_id,
            terminal_output_artifact_id=workflow_state.terminal_output_artifact_id,
        )
        artifact_ids = (bag_artifact.id,)
        extra_child_rows = (bag_artifact,)

    return commit_checkpoint_v2(
        db,
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        phase=phase,
        next_action_kind=next_action_kind,
        unit=None,
        workflow_state=workflow_state_for_commit,
        completed_logical_unit_id=completed_id,
        pending_interrupt_id=pending_interrupt_id,
        budget_suspension=budget_suspension,
        reason=reason,
        artifact_ids=artifact_ids,
        extra_child_rows=extra_child_rows,
    )


__all__ = [
    "BoundaryKind",
    "BoundaryResult",
    "DurableFrameMaterial",
    "DurableWorkflowRunner",
    "PreparedBoundary",
    "build_initial_workflow_state",
    "commit_workflow_boundary_prepare",
    "commit_workflow_boundary_result",
]
