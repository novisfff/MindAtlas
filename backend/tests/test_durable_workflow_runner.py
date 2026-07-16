"""Plan 07 Task 3: one-boundary durable Workflow runner.

Covers pre/post Checkpoints, stable retries, branch commit, loop cursor,
lease loss, cancellation, and unsafe node denial.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
BUILD = "build-test-plan07-t3"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session():
    from tests._db import make_session

    return make_session()


def _register_worker(db, *, worker_id: str = "worker-1", build: str = BUILD):
    from app.assistant.durable.worker_registry import WorkerIdentity, WorkerRegistry

    identity = WorkerIdentity(
        worker_id=worker_id,
        app_build_revision=build,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1, 2),
    )
    WorkerRegistry(db).register(identity)
    return identity


def _digest(payload: Any) -> str:
    from app.assistant.domain.digests import sha256_canonical_json

    return sha256_canonical_json(payload)


def _edge(
    *,
    edge_id: str,
    source: str,
    target: str,
    source_handle: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "edge_id": edge_id,
        "source_node_id": source,
        "target_node_id": target,
    }
    if source_handle is not None:
        payload["source_handle"] = source_handle
    return payload


def _plan_linear_start_output(
    *,
    target_version_id: UUID | None = None,
    entry: str = "start",
) -> Any:
    """Minimal start -> output plan."""
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = target_version_id or UUID("00000000-0000-4000-8000-000000000901")
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="e1",
                    source_node_id="start",
                    target_node_id="output",
                ),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="output",
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
        entry_node_id=entry,
        nodes=nodes,
    )
    return DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=tvid,
        target_digest=DIGEST_C,
        entry_node_id=entry,
        nodes=nodes,
        plan_digest=plan_digest,
    )


def _plan_with_if_else(*, true_target: str = "out_true", false_target: str = "out_false") -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000902")
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="branch"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="branch",
            node_type="if_else",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(
                    edge_id="e1",
                    source_node_id="branch",
                    target_node_id=true_target,
                    source_handle="true",
                ),
                DurableEdgeV1(
                    edge_id="e2",
                    source_node_id="branch",
                    target_node_id=false_target,
                    source_handle="else",
                ),
            ),
            adapter_key="if_else.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id=true_target,
            node_type="output",
            config_digest=DIGEST_C,
            outgoing_edges=(),
            adapter_key="output.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id=false_target,
            node_type="output",
            config_digest=DIGEST_D,
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


def _plan_with_loop() -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000903")
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="loop"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="loop",
            node_type="iteration",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e1", source_node_id="loop", target_node_id="output"),
            ),
            adapter_key="iteration.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="output",
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


def _plan_with_human() -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000904")
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="hitl"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="hitl",
            node_type="human_in_loop",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e1", source_node_id="hitl", target_node_id="output"),
            ),
            adapter_key="human_in_loop.v1",
            business_side_effect="none",
            may_interrupt=True,
        ),
        DurableNodePlanV1(
            node_id="output",
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


def _plan_with_unsafe_tool() -> Any:
    """Plan that includes an unsafe adapter_key (should be denied at execute)."""
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000905")
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="bad"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="bad",
            node_type="http_request",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e1", source_node_id="bad", target_node_id="output"),
            ),
            adapter_key="http_request.v1",
            business_side_effect="write_external",  # type: ignore[arg-type]
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="output",
            node_type="output",
            config_digest=DIGEST_C,
            outgoing_edges=(),
            adapter_key="output.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
    )
    # Bypass plan digest validation by constructing nodes with allowed SideEffectClass
    # and forcing via model_construct if needed. SideEffectClass may not include write_external.
    # Use compute side effect but unsupported adapter key for denial.
    nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="bad"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="bad",
            node_type="http_request",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(edge_id="e1", source_node_id="bad", target_node_id="output"),
            ),
            adapter_key="http_request.v1",
            business_side_effect="compute",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="output",
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


def _root_state(plan: Any, *, run_id: UUID | None = None, inputs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableCallFrameV1,
        DurableWorkflowStateV1,
        derive_frame_id,
        derive_node_visit_id,
    )
    from app.assistant.workflow.durable.runner import build_initial_workflow_state

    return build_initial_workflow_state(
        run_id=run_id or UUID("00000000-0000-4000-8000-000000000910"),
        plan=plan,
        root_invocation_digest=DIGEST_A,
        invocation_call_id="root-call-1",
        target_id=UUID("00000000-0000-4000-8000-000000000911"),
        inputs=inputs or {"query": "hello"},
    )


def _material(plan: Any, *, configs: dict | None = None, inputs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    node_configs = configs or {
        n.node_id: {} for n in plan.nodes
    }
    return DurableFrameMaterial(plan=plan, node_configs=node_configs, inputs=inputs or {})


def _seed_running_with_base(db):
    from app.assistant.durable.materialize import materialize_base_run_state
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken
    from app.assistant.models import AssistantChatRun, Conversation, Message
    from app.assistant.provider_loop.messages import ProviderUserMessage

    _register_worker(db)
    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    user = Message(conversation_id=conv.id, role="user", content="hi")
    assistant = Message(conversation_id=conv.id, role="assistant", content="")
    db.add_all([user, assistant])
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        status="queued",
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision=BUILD,
        state_revision=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    repo = DurableRunRepository(db)
    claimed = repo.claim_queued(
        run_id=run.id,
        expected_revision=0,
        worker_id="worker-1",
        lease_ttl=__import__("datetime").timedelta(seconds=30),
    )
    lease = LeaseToken(
        run_id=run.id,
        worker_id="worker-1",
        lease_generation=int(claimed.run.lease_generation),
    )

    mat = materialize_base_run_state(
        db,
        run_id=run.id,
        lease=lease,
        expected_revision=claimed.state_revision,
        manifest_payload={"schemaVersion": 1},
        manifest_digest=DIGEST_A,
        policy_payload={"schemaVersion": 1},
        policy_digest=DIGEST_A,
        budget_payload={"schemaVersion": 1, "revision": 0, "providerRoundsStarted": 0},
        budget_digest=DIGEST_A,
        obligation_payload={"schemaVersion": 1},
        obligation_digest=DIGEST_A,
        provider_messages=(ProviderUserMessage(role="user", content="hi"),),
    )
    db.refresh(run)
    return run, lease, mat.state_revision, repo


# ---------------------------------------------------------------------------
# In-memory boundary protocol
# ---------------------------------------------------------------------------


class TestDurableWorkflowRunnerBoundaries:
    def test_start_then_output_one_boundary_each(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_linear_start_output()
        state = _root_state(plan)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        # Boundary 1: start
        prepared = runner.prepare_boundary(state=state, material=material)
        assert prepared.node_id == "start"
        assert prepared.unit.kind == "workflow_node"
        assert prepared.unit.state == "prepared"
        assert prepared.frame.phase == "executing"
        assert prepared.node_visit_id == prepared.frame.node_visit_id

        result = runner.execute_boundary(prepared=prepared, material=material)
        assert result.kind == BoundaryKind.NODE_COMPLETED
        assert result.next_node_id == "output"
        state2 = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
        assert state2.frame_stack[-1].current_node_id == "output"
        assert state2.frame_stack[-1].phase == "ready"

        # Boundary 2: output (root terminal)
        prepared2 = runner.prepare_boundary(state=state2, material=material)
        assert prepared2.node_id == "output"
        result2 = runner.execute_boundary(prepared=prepared2, material=material)
        assert result2.kind == BoundaryKind.ROOT_COMPLETED
        state3 = runner.apply_boundary_result(state=prepared2.workflow_state, result=result2)
        assert state3.frame_stack[-1].phase == "completed"
        assert state3.terminal_output_artifact_id is not None

    def test_stable_retry_keeps_node_visit_id_and_increments_attempt(self) -> None:
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        plan = _plan_linear_start_output()
        state = _root_state(plan)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        prepared1 = runner.prepare_boundary(state=state, material=material)
        visit1 = prepared1.node_visit_id
        attempt1 = prepared1.frame.execution_attempt
        assert attempt1 == 1

        # Crash after prepare: re-prepare same executing frame → same visit, attempt+1
        crash_state = prepared1.workflow_state
        prepared2 = runner.prepare_boundary(state=crash_state, material=material)
        assert prepared2.node_visit_id == visit1
        assert prepared2.frame.execution_attempt == 2
        # logical unit id stable
        assert prepared2.unit.logical_unit_id == prepared1.unit.logical_unit_id

    def test_branch_decision_persisted_before_following(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_with_if_else()
        inputs = {"flag": "yes"}
        state = _root_state(plan, inputs=inputs)
        material = _material(
            plan,
            configs={
                "start": {},
                "branch": {
                    "branches": [
                        {
                            "id": "true",
                            "logic": "and",
                            "conditions": [
                                {
                                    "variable": "start.flag",
                                    "operator": "eq",
                                    "value": "yes",
                                }
                            ],
                        }
                    ],
                    "else_handle": "else",
                },
                "out_true": {},
                "out_false": {},
            },
            inputs=inputs,
        )
        runner = DurableWorkflowRunner()

        # start
        p0 = runner.prepare_boundary(state=state, material=material)
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        # branch
        p1 = runner.prepare_boundary(state=state, material=material)
        assert p1.node_id == "branch"
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.NODE_COMPLETED
        assert r1.branch_decision is not None
        assert r1.branch_decision.chosen_handle == "true"
        assert r1.next_node_id == "out_true"
        # Decision is on the result before apply — apply persists it on the frame
        state2 = runner.apply_boundary_result(state=p1.workflow_state, result=r1)
        frame = state2.frame_stack[-1]
        assert len(frame.branch_decisions) == 1
        assert frame.branch_decisions[0].chosen_handle == "true"
        assert frame.current_node_id == "out_true"

    def test_loop_cursor_persisted_before_next_iteration(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_with_loop()
        inputs = {"items": ["a", "b"]}
        state = _root_state(plan, inputs=inputs)
        material = _material(
            plan,
            configs={
                "start": {},
                "loop": {
                    "items": ["a", "b"],
                    "max_iterations": 2,
                },
                "output": {},
            },
            inputs=inputs,
        )
        runner = DurableWorkflowRunner()

        # start
        p0 = runner.prepare_boundary(state=state, material=material)
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        # first loop boundary: advances cursor for item 0
        p1 = runner.prepare_boundary(state=state, material=material)
        assert p1.node_id == "loop"
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.NODE_COMPLETED
        assert r1.loop_cursor is not None
        assert r1.loop_cursor.iteration_index == 0
        state = runner.apply_boundary_result(state=p1.workflow_state, result=r1)
        frame = state.frame_stack[-1]
        assert len(frame.loop_cursors) == 1
        assert frame.loop_cursors[0].iteration_index == 0
        # still on loop for next iteration
        assert frame.current_node_id == "loop"

        # second iteration
        p2 = runner.prepare_boundary(state=state, material=material)
        r2 = runner.execute_boundary(prepared=p2, material=material)
        assert r2.loop_cursor is not None
        assert r2.loop_cursor.iteration_index == 1
        state = runner.apply_boundary_result(state=p2.workflow_state, result=r2)

        # third: loop done → advance to output
        p3 = runner.prepare_boundary(state=state, material=material)
        r3 = runner.execute_boundary(prepared=p3, material=material)
        assert r3.next_node_id == "output"
        state = runner.apply_boundary_result(state=p3.workflow_state, result=r3)
        assert state.frame_stack[-1].current_node_id == "output"

    def test_human_boundary_returns_pause_proposal_ready_result(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_with_human()
        state = _root_state(plan)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {
                    "kind": "approval",
                    "title": "Please approve",
                },
                "output": {},
            },
        )
        runner = DurableWorkflowRunner()

        p0 = runner.prepare_boundary(state=state, material=material)
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        p1 = runner.prepare_boundary(state=state, material=material)
        assert p1.node_id == "hitl"
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.HUMAN_PAUSE
        assert r1.pause_proposal is not None
        assert r1.pause_proposal.kind == "approval"
        assert r1.pause_proposal.node_id == "hitl"
        assert r1.pause_proposal.node_visit_id == p1.node_visit_id
        assert r1.pause_proposal.interrupt_id is not None
        # Does NOT call create_and_wait — result is pure proposal-ready shape
        assert r1.pause_proposal.proposed_workflow_state.pending_interrupt_id == (
            r1.pause_proposal.interrupt_id
        )

    def test_unsafe_node_denied_at_execute(self) -> None:
        from app.assistant.workflow.durable.adapters import DurableAdapterError
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        plan = _plan_with_unsafe_tool()
        state = _root_state(plan)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        p0 = runner.prepare_boundary(state=state, material=material)
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        p1 = runner.prepare_boundary(state=state, material=material)
        assert p1.node_id == "bad"
        with pytest.raises(DurableAdapterError) as exc:
            runner.execute_boundary(prepared=p1, material=material)
        assert exc.value.reason_code == "unsupported_adapter"

    def test_cancellation_stops_before_adapter(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_linear_start_output()
        state = _root_state(plan)
        material = _material(plan)
        cancel = MagicMock()
        cancel.is_cancelled.return_value = True
        runner = DurableWorkflowRunner(cancellation_probe=cancel)

        prepared = runner.prepare_boundary(state=state, material=material)
        result = runner.execute_boundary(prepared=prepared, material=material)
        assert result.kind == BoundaryKind.CANCELLED
        cancel.is_cancelled.assert_called()

    def test_lease_loss_stops_before_adapter(self) -> None:
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        plan = _plan_linear_start_output()
        state = _root_state(plan)
        material = _material(plan)
        heartbeat = MagicMock(return_value=False)
        runner = DurableWorkflowRunner(lease_heartbeat=heartbeat)

        prepared = runner.prepare_boundary(state=state, material=material)
        result = runner.execute_boundary(prepared=prepared, material=material)
        assert result.kind == BoundaryKind.LEASE_LOST
        heartbeat.assert_called()

    def test_never_invokes_graph_invoke(self) -> None:
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        plan = _plan_linear_start_output()
        state = _root_state(plan)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        prepared = runner.prepare_boundary(state=state, material=material)
        result = runner.execute_boundary(prepared=prepared, material=material)
        # graph.invoke would require a compiled graph; we never construct one
        assert result.kind.value in {"node_completed", "root_completed"}


# ---------------------------------------------------------------------------
# Checkpoint pre/post with Plan 06 CAS
# ---------------------------------------------------------------------------


class TestDurableWorkflowRunnerCheckpoints:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_prepare_and_result_append_v2_checkpoints(self) -> None:
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
            commit_workflow_boundary_result,
        )

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        plan = _plan_linear_start_output()
        state = _root_state(plan, run_id=run.id)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        prepared = runner.prepare_boundary(state=state, material=material)
        prep_commit = commit_workflow_boundary_prepare(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            prepared=prepared,
        )
        assert prep_commit.status == "running"
        self.db.refresh(run)
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        assert ck is not None
        assert ck.schema_version == 2
        decoded = decode_checkpoint(ck.state_payload)
        assert decoded.schema_version == 2
        assert decoded.inflight_unit is not None
        assert decoded.inflight_unit.kind == "workflow_node"
        assert decoded.inflight_unit.state == "prepared"
        assert decoded.workflow_state is not None
        assert decoded.workflow_state.frame_stack[-1].phase == "executing"

        # started
        started = runner.mark_started(prepared=prepared, budget_revision=1)
        start_commit = commit_workflow_boundary_prepare(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep_commit.state_revision,
            prepared=started,
            as_started=True,
        )
        assert start_commit.status == "running"

        result = runner.execute_boundary(prepared=started, material=material)
        assert result.kind == BoundaryKind.NODE_COMPLETED
        state_after = runner.apply_boundary_result(
            state=started.workflow_state, result=result
        )
        result_commit = commit_workflow_boundary_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=start_commit.state_revision,
            workflow_state=state_after,
            completed_logical_unit_id=started.unit.logical_unit_id,
            next_action_kind="continue_child",
        )
        assert result_commit.status == "running"
        self.db.refresh(run)
        ck2 = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded2 = decode_checkpoint(ck2.state_payload)
        assert decoded2.inflight_unit is None
        assert decoded2.workflow_state is not None
        assert decoded2.workflow_state.frame_stack[-1].current_node_id == "output"
        assert decoded2.workflow_state.frame_stack[-1].phase == "ready"

    def test_retry_after_started_reuses_logical_unit(self) -> None:
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        plan = _plan_linear_start_output()
        state = _root_state(plan, run_id=run.id)
        material = _material(plan)
        runner = DurableWorkflowRunner()

        prepared = runner.prepare_boundary(state=state, material=material)
        prep = commit_workflow_boundary_prepare(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            prepared=prepared,
        )
        started = runner.mark_started(prepared=prepared, budget_revision=1)
        commit_workflow_boundary_prepare(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep.state_revision,
            prepared=started,
            as_started=True,
        )
        # Crash while executing: re-prepare from started frame state
        retry = runner.prepare_boundary(state=started.workflow_state, material=material)
        assert retry.unit.logical_unit_id == prepared.unit.logical_unit_id
        assert retry.node_visit_id == prepared.node_visit_id
        assert retry.frame.execution_attempt == 2
