"""Plan 07 Task 3: nested Workflow frame push/pop.

Covers workflow_call child frame push, parent freeze (child_active),
child completion pop, and result application on parent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _child_plan(*, target_version_id: UUID | None = None) -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = target_version_id or UUID("00000000-0000-4000-8000-000000000a01")
    nodes = (
        DurableNodePlanV1(
            node_id="c_start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="ce1",
                    source_node_id="c_start",
                    target_node_id="c_output",
                ),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="c_output",
            node_type="output",
            config_digest=DIGEST_B,
            outgoing_edges=(),
            adapter_key="output.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
    )
    plan_digest = compute_plan_digest(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_C,
        entry_node_id="c_start",
        nodes=nodes,
    )
    return DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_C,
        entry_node_id="c_start",
        nodes=nodes,
        plan_digest=plan_digest,
    )


def _parent_plan_with_call(
    *,
    child_version_id: UUID,
    call_node_id: str = "nested_wf_invoke",
    after_call_node_id: str = "parent_final_out",
) -> Any:
    """Parent plan with a workflow_call. Node ids are intentionally non-fixture
    names so parent advance cannot rely on call→p_output / agent_call→output maps.
    """
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        FrozenExecutionDependencyRef,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000a02")
    dep = FrozenExecutionDependencyRef(
        dependency_path=f"nodes.{call_node_id}.workflow",
        dependency_type="workflow",
        target_identity=f"workflow:{child_version_id}",
        target_version_id=child_version_id,
        resolution_digest=DIGEST_A,
        dependency_digest=DIGEST_B,
    )
    nodes = (
        DurableNodePlanV1(
            node_id="p_start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="pe0",
                    source_node_id="p_start",
                    target_node_id=call_node_id,
                ),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id=call_node_id,
            node_type="workflow_call",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="pe1",
                    source_node_id=call_node_id,
                    target_node_id=after_call_node_id,
                ),
            ),
            adapter_key="workflow_call.v1",
            business_side_effect="none",
            may_interrupt=False,
            dependency_refs=(dep,),
        ),
        DurableNodePlanV1(
            node_id=after_call_node_id,
            node_type="output",
            config_digest=DIGEST_C,
            outgoing_edges=(),
            adapter_key="output.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
    )
    plan_digest = compute_plan_digest(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_A,
        entry_node_id="p_start",
        nodes=nodes,
    )
    return DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_A,
        entry_node_id="p_start",
        nodes=nodes,
        plan_digest=plan_digest,
    )


def _material(plan: Any, *, configs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    return DurableFrameMaterial(
        plan=plan,
        node_configs=configs or {n.node_id: {} for n in plan.nodes},
    )


class TestNestedWorkflowFrames:
    def test_workflow_call_pushes_child_frame_and_freezes_parent(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        child_vid = UUID("00000000-0000-4000-8000-000000000a01")
        child_plan = _child_plan(target_version_id=child_vid)
        parent_plan = _parent_plan_with_call(child_version_id=child_vid)
        run_id = UUID("00000000-0000-4000-8000-000000000a10")

        state = build_initial_workflow_state(
            run_id=run_id,
            plan=parent_plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000a11"),
            inputs={"x": 1},
        )
        parent_material = _material(parent_plan)
        child_material = _material(child_plan)
        runner = DurableWorkflowRunner()

        # Parent start
        p0 = runner.prepare_boundary(state=state, material=parent_material)
        r0 = runner.execute_boundary(prepared=p0, material=parent_material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
        assert state.frame_stack[-1].current_node_id == "nested_wf_invoke"

        # workflow_call → child_pushed
        p1 = runner.prepare_boundary(state=state, material=parent_material)
        assert p1.node_id == "nested_wf_invoke"
        r1 = runner.execute_boundary(
            prepared=p1,
            material=parent_material,
            child_materials={str(child_vid): child_material},
        )
        assert r1.kind == BoundaryKind.CHILD_PUSHED
        assert r1.child_frame is not None
        assert r1.child_frame.target_kind == "workflow"
        assert r1.child_frame.target_version_id == child_vid
        assert r1.child_frame.phase == "ready"
        assert r1.child_frame.current_node_id == "c_start"
        assert r1.child_frame.parent_frame_id == state.frame_stack[-1].frame_id

        state = runner.apply_boundary_result(state=p1.workflow_state, result=r1)
        assert len(state.frame_stack) == 2
        parent = state.frame_stack[0]
        child = state.frame_stack[1]
        assert parent.phase == "child_active"
        assert child.frame_id in parent.child_frame_ids
        assert child.phase == "ready"

        # Child start
        p2 = runner.prepare_boundary(state=state, material=child_material)
        assert p2.node_id == "c_start"
        assert p2.frame.frame_id == child.frame_id
        r2 = runner.execute_boundary(prepared=p2, material=child_material)
        state = runner.apply_boundary_result(state=p2.workflow_state, result=r2)

        # Child output → completes child, pops
        p3 = runner.prepare_boundary(state=state, material=child_material)
        assert p3.node_id == "c_output"
        r3 = runner.execute_boundary(prepared=p3, material=child_material)
        assert r3.kind == BoundaryKind.CHILD_COMPLETED
        state = runner.apply_boundary_result(state=p3.workflow_state, result=r3)
        assert len(state.frame_stack) == 1
        parent = state.frame_stack[0]
        assert parent.phase == "ready"
        assert parent.current_node_id == "parent_final_out"
        # Successor was recorded as a branch decision at CHILD_PUSHED time
        assert any(
            d.node_id == "nested_wf_invoke" and d.chosen_target_node_id == "parent_final_out"
            for d in parent.branch_decisions
        )

        # Parent output → root completed
        p4 = runner.prepare_boundary(state=state, material=parent_material)
        r4 = runner.execute_boundary(prepared=p4, material=parent_material)
        assert r4.kind == BoundaryKind.ROOT_COMPLETED
        state = runner.apply_boundary_result(state=p4.workflow_state, result=r4)
        assert state.frame_stack[0].phase == "completed"

    def test_child_frame_id_is_deterministic_on_retry(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        child_vid = UUID("00000000-0000-4000-8000-000000000a01")
        child_plan = _child_plan(target_version_id=child_vid)
        parent_plan = _parent_plan_with_call(child_version_id=child_vid)
        run_id = UUID("00000000-0000-4000-8000-000000000a10")

        state = build_initial_workflow_state(
            run_id=run_id,
            plan=parent_plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000a11"),
            inputs={},
        )
        parent_material = _material(parent_plan)
        child_material = _material(child_plan)
        runner = DurableWorkflowRunner()

        p0 = runner.prepare_boundary(state=state, material=parent_material)
        r0 = runner.execute_boundary(prepared=p0, material=parent_material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        p1 = runner.prepare_boundary(state=state, material=parent_material)
        r1a = runner.execute_boundary(
            prepared=p1,
            material=parent_material,
            child_materials={str(child_vid): child_material},
        )
        r1b = runner.execute_boundary(
            prepared=p1,
            material=parent_material,
            child_materials={str(child_vid): child_material},
        )
        assert r1a.kind == BoundaryKind.CHILD_PUSHED
        assert r1b.kind == BoundaryKind.CHILD_PUSHED
        assert r1a.child_frame is not None and r1b.child_frame is not None
        assert r1a.child_frame.frame_id == r1b.child_frame.frame_id

    def test_missing_child_material_is_denied(self) -> None:
        from app.assistant.workflow.durable.adapters import DurableAdapterError
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        child_vid = UUID("00000000-0000-4000-8000-000000000a01")
        parent_plan = _parent_plan_with_call(child_version_id=child_vid)
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000a10"),
            plan=parent_plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000a11"),
            inputs={},
        )
        parent_material = _material(parent_plan)
        runner = DurableWorkflowRunner()

        p0 = runner.prepare_boundary(state=state, material=parent_material)
        r0 = runner.execute_boundary(prepared=p0, material=parent_material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        p1 = runner.prepare_boundary(state=state, material=parent_material)
        with pytest.raises(DurableAdapterError) as exc:
            runner.execute_boundary(prepared=p1, material=parent_material)
        assert exc.value.reason_code in {
            "missing_child_plan",
            "nested_workflow_call_unsupported",
        }
