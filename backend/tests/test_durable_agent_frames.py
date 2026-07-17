"""Plan 07 Task 3: nested Agent frame / agent_round boundary.

Covers reviewed Agent target frames using Plan 03 ProviderLoopContinuation
shape, no Main Agent Skill injection inside nested Agent, and one agent-round
boundary per prepared unit.
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
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def _agent_plan(*, target_version_id: UUID | None = None) -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = target_version_id or UUID("00000000-0000-4000-8000-000000000b01")
    nodes = (
        DurableNodePlanV1(
            node_id="agent_root",
            node_type="agent",
            config_digest=DIGEST_A,
            outgoing_edges=(),
            adapter_key="agent.v1",
            business_side_effect="compute",
            may_interrupt=False,
        ),
    )
    plan_digest = compute_plan_digest(
        target_kind="agent",
        target_version_id=tvid,
        target_digest=DIGEST_B,
        entry_node_id="agent_root",
        nodes=nodes,
    )
    return DurableExecutionPlanV1(
        target_kind="agent",
        target_version_id=tvid,
        target_digest=DIGEST_B,
        entry_node_id="agent_root",
        nodes=nodes,
        plan_digest=plan_digest,
    )


def _parent_workflow_with_agent(
    *,
    agent_version_id: UUID,
    call_node_id: str = "nested_agent_invoke",
    after_call_node_id: str = "parent_agent_out",
) -> Any:
    """Parent plan with nested agent call. Node ids are intentionally non-fixture
    names so parent advance cannot rely on agent_call→output maps.
    """
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        FrozenExecutionDependencyRef,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000b02")
    dep = FrozenExecutionDependencyRef(
        dependency_path=f"nodes.{call_node_id}.agent",
        dependency_type="agent",
        target_identity=f"agent:{agent_version_id}",
        target_version_id=agent_version_id,
        resolution_digest=DIGEST_A,
        dependency_digest=DIGEST_C,
    )
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="e0",
                    source_node_id="start",
                    target_node_id=call_node_id,
                ),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id=call_node_id,
            node_type="agent",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="e1",
                    source_node_id=call_node_id,
                    target_node_id=after_call_node_id,
                ),
            ),
            adapter_key="agent.v1",
            business_side_effect="compute",
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
        entry_node_id="start",
        nodes=nodes,
    )
    return DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_A,
        entry_node_id="start",
        nodes=nodes,
        plan_digest=plan_digest,
    )


def _material(plan: Any, *, configs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    return DurableFrameMaterial(
        plan=plan,
        node_configs=configs or {n.node_id: {} for n in plan.nodes},
    )


def _minimal_agent_loop_continuation() -> Any:
    """Build a minimal valid ProviderLoopContinuation for nested Agent frames."""
    from app.assistant.capabilities.contracts import CapabilityPrincipal, ContinuationRef
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

    run_id = UUID("00000000-0000-4000-8000-000000000b10")
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=UUID("00000000-0000-4000-8000-000000000b91"),
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_A,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    model = create_model_ref(
        model_id=UUID("00000000-0000-4000-8000-000000000b92"),
        model_name="gpt-agent",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=UUID("00000000-0000-4000-8000-000000000b93"),
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
        run_id=run_id,
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


class TestDurableAgentFrames:
    def test_agent_root_frame_one_agent_round_boundary(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        plan = _agent_plan()
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000b10"),
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="agent-root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000b11"),
            inputs={"prompt": "summarize"},
        )
        material = _material(plan)
        # Scripted agent round: complete after one round
        runner = DurableWorkflowRunner(
            agent_round_executor=lambda **kwargs: {
                "status": "completed",
                "output": {"text": "done"},
                "agent_loop_continuation": None,
            }
        )

        prepared = runner.prepare_boundary(state=state, material=material)
        assert prepared.unit.kind == "agent_round"
        assert prepared.node_id == "agent_root"
        result = runner.execute_boundary(prepared=prepared, material=material)
        assert result.kind == BoundaryKind.ROOT_COMPLETED
        state2 = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
        assert state2.frame_stack[-1].phase == "completed"
        assert state2.frame_stack[-1].target_kind == "agent"

    def test_agent_round_may_continue_with_provider_loop_continuation(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        plan = _agent_plan()
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000b10"),
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="agent-root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000b11"),
            inputs={},
        )
        material = _material(plan)
        cont = _minimal_agent_loop_continuation()
        calls = {"n": 0}

        def _exec(**kwargs: Any) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "status": "continue",
                    "output": {"text": "thinking"},
                    "agent_loop_continuation": cont,
                }
            return {
                "status": "completed",
                "output": {"text": "final"},
                "agent_loop_continuation": None,
            }

        runner = DurableWorkflowRunner(agent_round_executor=_exec)

        p1 = runner.prepare_boundary(state=state, material=material)
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.NODE_COMPLETED
        assert r1.agent_loop_continuation is not None
        state = runner.apply_boundary_result(state=p1.workflow_state, result=r1)
        frame = state.frame_stack[-1]
        assert frame.agent_loop_continuation is not None
        assert frame.phase == "ready"
        assert frame.current_node_id == "agent_root"

        p2 = runner.prepare_boundary(state=state, material=material)
        assert p2.unit.kind == "agent_round"
        # Same node, new visit ordinal
        assert p2.node_visit_id != p1.node_visit_id
        r2 = runner.execute_boundary(prepared=p2, material=material)
        assert r2.kind == BoundaryKind.ROOT_COMPLETED

    def test_parent_workflow_pushes_agent_child_frame(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        agent_vid = UUID("00000000-0000-4000-8000-000000000b01")
        agent_plan = _agent_plan(target_version_id=agent_vid)
        parent_plan = _parent_workflow_with_agent(agent_version_id=agent_vid)
        run_id = UUID("00000000-0000-4000-8000-000000000b10")

        state = build_initial_workflow_state(
            run_id=run_id,
            plan=parent_plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000b12"),
            inputs={},
        )
        parent_material = _material(parent_plan)
        agent_material = _material(agent_plan)
        runner = DurableWorkflowRunner(
            agent_round_executor=lambda **kwargs: {
                "status": "completed",
                "output": {"text": "agent-done"},
                "agent_loop_continuation": None,
            }
        )

        # start
        p0 = runner.prepare_boundary(state=state, material=parent_material)
        r0 = runner.execute_boundary(prepared=p0, material=parent_material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        # nested agent invoke pushes child agent frame
        p1 = runner.prepare_boundary(state=state, material=parent_material)
        assert p1.node_id == "nested_agent_invoke"
        r1 = runner.execute_boundary(
            prepared=p1,
            material=parent_material,
            child_materials={str(agent_vid): agent_material},
        )
        assert r1.kind == BoundaryKind.CHILD_PUSHED
        assert r1.child_frame is not None
        assert r1.child_frame.target_kind == "agent"
        assert r1.next_node_id == "parent_agent_out"
        assert r1.branch_decision is not None
        assert r1.branch_decision.chosen_target_node_id == "parent_agent_out"
        state = runner.apply_boundary_result(state=p1.workflow_state, result=r1)
        assert len(state.frame_stack) == 2
        assert state.frame_stack[0].phase == "child_active"
        assert state.frame_stack[1].target_kind == "agent"

        # one agent round completes child
        p2 = runner.prepare_boundary(state=state, material=agent_material)
        assert p2.unit.kind == "agent_round"
        r2 = runner.execute_boundary(prepared=p2, material=agent_material)
        assert r2.kind == BoundaryKind.CHILD_COMPLETED
        state = runner.apply_boundary_result(state=p2.workflow_state, result=r2)
        assert len(state.frame_stack) == 1
        assert state.frame_stack[0].current_node_id == "parent_agent_out"

    def test_nested_agent_has_no_main_agent_skill_injection(self) -> None:
        """Nested agent_round executor receives no Main Agent skill surface."""
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        plan = _agent_plan()
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000b10"),
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="agent-root-call",
            target_id=UUID("00000000-0000-4000-8000-000000000b11"),
            inputs={},
        )
        material = _material(plan)
        seen: dict[str, Any] = {}

        def _exec(**kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {
                "status": "completed",
                "output": {"text": "ok"},
                "agent_loop_continuation": None,
            }

        runner = DurableWorkflowRunner(agent_round_executor=_exec)
        prepared = runner.prepare_boundary(state=state, material=material)
        runner.execute_boundary(prepared=prepared, material=material)
        assert "main_agent_skills" not in seen
        assert seen.get("allow_main_agent_skills") is False or (
            "allow_main_agent_skills" not in seen
        )
        # Exact frozen tools only — no ambient skill injection key
        assert seen.get("skill_injection") in (None, False, ())
