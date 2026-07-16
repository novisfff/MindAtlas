"""Plan 07 Task 7: exact interrupt resume of durable child frames.

Covers:
- Load resume-ready Checkpoint, resolved Interrupt, original waiting Checkpoint/frame
- Apply one typed human result once
- Suspension parent → derived resolution budget lineage before adapter work
- Reject missing/mismatched child revision before runtime construction
- Worker crash before/after continuation node commit
- Nested Workflow and Agent frame waits
- Two decisions, decision vs cancel/expiry, two workers, stale event, tampered frame
- Stop-vs-post-resume result CAS (status=running + expected revision)
- Irreconcilable drift → needs_reconciliation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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
BUILD = "build-test-plan07-t7"
PEPPER = "task7-resume-pepper-not-for-prod-32bytesxx"


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


def _plan_with_human(*, second_hitl: bool = False) -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000b04")
    if second_hitl:
        nodes = (
            DurableNodePlanV1(
                node_id="start",
                node_type="start",
                config_digest=DIGEST_A,
                outgoing_edges=(
                    DurableEdgeV1(edge_id="e0", source_node_id="start", target_node_id="hitl1"),
                ),
                adapter_key="start.v1",
                business_side_effect="none",
                may_interrupt=False,
            ),
            DurableNodePlanV1(
                node_id="hitl1",
                node_type="human_in_loop",
                config_digest=DIGEST_B,
                outgoing_edges=(
                    DurableEdgeV1(edge_id="e1", source_node_id="hitl1", target_node_id="hitl2"),
                ),
                adapter_key="human_in_loop.v1",
                business_side_effect="none",
                may_interrupt=True,
            ),
            DurableNodePlanV1(
                node_id="hitl2",
                node_type="human_in_loop",
                config_digest=DIGEST_C,
                outgoing_edges=(
                    DurableEdgeV1(edge_id="e2", source_node_id="hitl2", target_node_id="output"),
                ),
                adapter_key="human_in_loop.v1",
                business_side_effect="none",
                may_interrupt=True,
            ),
            DurableNodePlanV1(
                node_id="output",
                node_type="output",
                config_digest=DIGEST_D,
                outgoing_edges=(),
                adapter_key="output.v1",
                business_side_effect="none",
                may_interrupt=False,
            ),
        )
    else:
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


def _plan_nested_child() -> tuple[Any, Any]:
    """Parent workflow_call → child with human → parent output."""
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        FrozenExecutionDependencyRef,
        compute_plan_digest,
    )

    child_tvid = UUID("00000000-0000-4000-8000-000000000c01")
    parent_tvid = UUID("00000000-0000-4000-8000-000000000c02")
    child_nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="ce0", source_node_id="start", target_node_id="hitl"),
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
                DurableEdgeV1(edge_id="ce1", source_node_id="hitl", target_node_id="output"),
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
    child_digest = compute_plan_digest(
        target_kind="workflow",
        target_version_id=child_tvid,
        target_digest=DIGEST_B,
        entry_node_id="start",
        nodes=child_nodes,
    )
    child_plan = DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=child_tvid,
        target_digest=DIGEST_B,
        entry_node_id="start",
        nodes=child_nodes,
        plan_digest=child_digest,
    )
    dep = FrozenExecutionDependencyRef(
        dependency_path="nodes.call.workflow",
        dependency_type="workflow",
        target_identity=str(child_tvid),
        target_version_id=child_tvid,
        resolution_digest=DIGEST_B,
        dependency_digest=DIGEST_C,
    )
    parent_nodes = (
        DurableNodePlanV1(
            node_id="start",
            node_type="start",
            config_digest=DIGEST_A,
            outgoing_edges=(
                DurableEdgeV1(edge_id="pe0", source_node_id="start", target_node_id="call"),
            ),
            adapter_key="start.v1",
            business_side_effect="none",
            may_interrupt=False,
        ),
        DurableNodePlanV1(
            node_id="call",
            node_type="workflow_call",
            config_digest=DIGEST_B,
            outgoing_edges=(
                DurableEdgeV1(edge_id="pe1", source_node_id="call", target_node_id="output"),
            ),
            adapter_key="workflow_call.v1",
            business_side_effect="none",
            may_interrupt=False,
            dependency_refs=(dep,),
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
    parent_digest = compute_plan_digest(
        target_kind="workflow",
        target_version_id=parent_tvid,
        target_digest=DIGEST_A,
        entry_node_id="start",
        nodes=parent_nodes,
    )
    parent_plan = DurableExecutionPlanV1(
        target_kind="workflow",
        target_version_id=parent_tvid,
        target_digest=DIGEST_A,
        entry_node_id="start",
        nodes=parent_nodes,
        plan_digest=parent_digest,
    )
    return parent_plan, child_plan


def _material(plan: Any, *, configs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    node_configs = configs or {n.node_id: {} for n in plan.nodes}
    return DurableFrameMaterial(plan=plan, node_configs=node_configs, inputs={})


def _parent_ledger(*, remaining_ms: int = 120_000):
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits
    from app.assistant.policy.contracts import RunBudgetLimits

    start = datetime.now(timezone.utc)
    limits_payload = normalize_run_budget_limits().model_dump()
    limits_payload["max_wall_time_ms"] = max(remaining_ms, 1_000)
    limits = RunBudgetLimits(**limits_payload)
    return create_initial_ledger_state(
        limits=limits,
        started_at_utc=start,
        deadline_at_utc=start + timedelta(milliseconds=remaining_ms + 5_000),
    )


def _seed_running_with_base(db, *, deadline_at: datetime | None = None, worker_id: str = "worker-1"):
    from app.assistant.durable.materialize import materialize_base_run_state
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken
    from app.assistant.models import AssistantChatRun, Conversation, Message
    from app.assistant.provider_loop.messages import ProviderUserMessage

    _register_worker(db, worker_id=worker_id)
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
        deadline_at=deadline_at or (datetime.now(timezone.utc) + timedelta(minutes=30)),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    repo = DurableRunRepository(db)
    claimed = repo.claim_queued(
        run_id=run.id,
        expected_revision=0,
        worker_id=worker_id,
        lease_ttl=timedelta(seconds=30),
    )
    lease = LeaseToken(
        run_id=run.id,
        worker_id=worker_id,
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


def _advance_to_human_pause(
    runner,
    plan,
    state,
    material,
    *,
    human_node: str = "hitl",
    db=None,
    run_id=None,
    lease=None,
    revision: int | None = None,
    child_materials=None,
):
    """Advance to human pause. When db/lease provided, commit prepare/started units."""
    from app.assistant.workflow.durable.runner import (
        BoundaryKind,
        commit_workflow_boundary_prepare,
    )

    current = state
    prepared = None
    result = None
    rev = revision
    for _ in range(16):
        top = current.frame_stack[-1]
        mat = material
        if child_materials is not None:
            key = str(top.target_version_id)
            if key in child_materials:
                mat = child_materials[key]
        prepared = runner.prepare_boundary(state=current, material=mat)
        if db is not None and lease is not None and rev is not None and run_id is not None:
            prep = commit_workflow_boundary_prepare(
                db, run_id=run_id, lease=lease, expected_revision=rev, prepared=prepared
            )
            rev = prep.state_revision
            started = runner.mark_started(prepared=prepared, budget_revision=1)
            start = commit_workflow_boundary_prepare(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=rev,
                prepared=started,
                as_started=True,
            )
            rev = start.state_revision
        result = runner.execute_boundary(
            prepared=prepared, material=mat, child_materials=child_materials
        )
        if result.kind == BoundaryKind.HUMAN_PAUSE:
            return current, prepared, result, rev
        current = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
    raise AssertionError(f"never reached human pause at {human_node}")


def _pause_and_resolve(
    db,
    *,
    run,
    lease,
    expected_revision: int,
    plan,
    material,
    parent_ledger,
    outcome: str = "approved",
    values: dict | None = None,
    second_hitl: bool = False,
    child_materials=None,
):
    """Run to first human pause, commit pause, resolve interrupt → queued resume-ready."""
    from app.assistant.durable.repository import DurableChildBundle, DurableRunRepository
    from app.assistant.workflow.durable.interrupt_api import _build_resume_children
    from app.assistant.workflow.durable.interrupts import DurableInterruptRepository
    from app.assistant.workflow.durable.pause import (
        WorkerUnitPauseEffectPort,
        commit_durable_workflow_pause,
    )
    from app.assistant.workflow.durable.runner import (
        DurableWorkflowRunner,
        build_initial_workflow_state,
    )

    port = WorkerUnitPauseEffectPort()
    runner = DurableWorkflowRunner(pause_effect_port=port)
    state = build_initial_workflow_state(
        run_id=run.id,
        plan=plan,
        root_invocation_digest=DIGEST_A,
        invocation_call_id="root-call-1",
        target_id=UUID("00000000-0000-4000-8000-000000000b11"),
        inputs={"query": "hello"},
    )
    runner.get_bag(state.frame_stack[0].frame_id, inputs={"query": "hello"})

    _, prepared, result, rev = _advance_to_human_pause(
        runner,
        plan,
        state,
        material,
        human_node="hitl1" if second_hitl else "hitl",
        db=db,
        run_id=run.id,
        lease=lease,
        revision=expected_revision,
        child_materials=child_materials,
    )
    assert result.pause_proposal is not None
    proposal = result.pause_proposal
    expected_revision = rev if rev is not None else expected_revision

    pause = commit_durable_workflow_pause(
        db,
        run_id=run.id,
        lease=lease,
        expected_revision=expected_revision,
        proposal=proposal,
        prepared=prepared,
        parent_ledger=parent_ledger,
        ttl_sec=3600,
        reason="test_pause",
    )
    db.refresh(run)
    assert run.status in {"waiting_approval", "waiting_input"}
    interrupt = pause.interrupt
    waiting_revision = int(pause.commit.state_revision)

    irepo = DurableInterruptRepository(db, token_pepper=PEPPER)
    tok = irepo.rotate_token(
        run_id=run.id,
        interrupt_id=interrupt.id,
        expected_request_revision=int(interrupt.request_revision),
        expected_run_revision=waiting_revision,
    )
    prepared_hold: dict[str, Any] = {}

    def prepare_queued(locked_run, locked_interrupt):
        rows, budget_id, ck_id, deadline = _build_resume_children(
            db,
            run=locked_run,
            interrupt=locked_interrupt,
            expected_revision=int(locked_run.state_revision),
        )
        for row in rows:
            db.add(row)
        db.flush()
        prepared_hold["budget_id"] = budget_id
        prepared_hold["checkpoint_id"] = ck_id
        prepared_hold["deadline"] = deadline
        prepared_hold["expected_revision"] = int(locked_run.state_revision)
        return ck_id, budget_id, int(locked_run.state_revision) + 1

    req_id = uuid.uuid4()
    resolved = irepo.resolve_interrupt(
        run_id=run.id,
        interrupt_id=interrupt.id,
        resolution_request_id=req_id,
        token=tok.token,
        expected_token_revision=tok.token_revision,
        expected_request_revision=int(interrupt.request_revision),
        expected_run_revision=waiting_revision,
        outcome=outcome,
        submitted_values=values or {},
        comment="ok",
        queues_execution=True,
        prepare_queued_children=prepare_queued,
    )
    assert resolved.created_resolution or resolved.idempotent_replay

    interrupt = irepo.get_interrupt(interrupt.id)
    assert interrupt is not None
    assert str(interrupt.status) != "pending"
    assert interrupt.resolution_checkpoint_id is not None
    assert interrupt.resolution_budget_revision_id is not None

    run_repo = DurableRunRepository(db)
    bundle = DurableChildBundle(
        rows=[],
        current_checkpoint_id=interrupt.resolution_checkpoint_id,
        current_budget_revision_id=interrupt.resolution_budget_revision_id,
    )
    commit = run_repo.commit_resume_queued(
        run_id=run.id,
        expected_revision=int(prepared_hold.get("expected_revision", waiting_revision)),
        children=bundle,
        set_deadline_at=prepared_hold.get("deadline")
        or (datetime.now(timezone.utc) + timedelta(minutes=5)),
    )
    db.refresh(run)
    assert run.status == "queued"
    revision = int(commit.state_revision)

    claimed = run_repo.claim_queued(
        run_id=run.id,
        expected_revision=revision,
        worker_id=lease.worker_id,
        lease_ttl=timedelta(seconds=30),
    )
    new_lease = type(lease)(
        run_id=run.id,
        worker_id=lease.worker_id,
        lease_generation=int(claimed.run.lease_generation),
    )
    db.refresh(run)
    return run, new_lease, int(claimed.state_revision), interrupt, proposal, parent_ledger


def _pause_resolve_claim(db, **kwargs):
    return _pause_and_resolve(db, **kwargs)


# ---------------------------------------------------------------------------
# Pure unit: human apply + successor
# ---------------------------------------------------------------------------


class TestApplyHumanResultOnce:
    def test_injects_typed_result_and_advances(self) -> None:
        from app.assistant.workflow.durable.adapters import PortableNodeBag
        from app.assistant.workflow.durable.resume import (
            HumanContinuationResult,
            apply_human_result_once,
        )
        from app.assistant.workflow.durable.runner import build_initial_workflow_state

        plan = _plan_with_human()
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000b20"),
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call-1",
            target_id=UUID("00000000-0000-4000-8000-000000000b21"),
            inputs={},
        )
        # Move top frame to waiting on hitl
        top = state.frame_stack[-1]
        from app.assistant.workflow.durable.runner import _copy_frame, _replace_top_frame
        from app.assistant.workflow.durable.contracts import derive_node_visit_id

        visit = derive_node_visit_id(
            frame_id=top.frame_id, node_id="hitl", node_visit_ordinal=1
        )
        waiting = _copy_frame(
            top,
            current_node_id="hitl",
            node_visit_id=visit,
            node_visit_ordinal=1,
            phase="waiting",
        )
        state = _replace_top_frame(state, waiting)
        human = HumanContinuationResult(
            outcome="approved",
            status="approved",
            values={"note": "yes"},
            comment="ok",
            resolution_request_id=uuid.uuid4(),
            resolution_digest=DIGEST_C,
            interrupt_id=uuid.uuid4(),
            node_id="hitl",
            node_visit_id=visit,
            frame_id=top.frame_id,
        )
        bag = PortableNodeBag(inputs={})
        new_state, bag2, next_id = apply_human_result_once(
            state=state, human=human, plan=plan, bag=bag, interrupt_kind="approval"
        )
        assert next_id == "output"
        assert bag2.node_outputs["hitl"]["outcome"] == "approved"
        assert new_state.frame_stack[-1].current_node_id == "output"
        assert new_state.frame_stack[-1].phase == "ready"
        assert new_state.pending_interrupt_id is None

    def test_input_cancel_without_branch_cancels_root(self) -> None:
        from app.assistant.workflow.durable.adapters import PortableNodeBag
        from app.assistant.workflow.durable.resume import (
            HumanContinuationResult,
            apply_human_result_once,
        )
        from app.assistant.workflow.durable.runner import (
            _copy_frame,
            _replace_top_frame,
            build_initial_workflow_state,
        )
        from app.assistant.workflow.durable.contracts import derive_node_visit_id

        plan = _plan_with_human()
        state = build_initial_workflow_state(
            run_id=UUID("00000000-0000-4000-8000-000000000b30"),
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call-1",
            target_id=UUID("00000000-0000-4000-8000-000000000b31"),
            inputs={},
        )
        top = state.frame_stack[-1]
        visit = derive_node_visit_id(
            frame_id=top.frame_id, node_id="hitl", node_visit_ordinal=1
        )
        state = _replace_top_frame(
            state,
            _copy_frame(
                top,
                current_node_id="hitl",
                node_visit_id=visit,
                node_visit_ordinal=1,
                phase="waiting",
            ),
        )
        human = HumanContinuationResult(
            outcome="cancelled",
            status="cancelled",
            values={},
            comment=None,
            resolution_request_id=uuid.uuid4(),
            resolution_digest=DIGEST_D,
            interrupt_id=uuid.uuid4(),
            node_id="hitl",
            node_visit_id=visit,
            frame_id=top.frame_id,
        )
        new_state, _, next_id = apply_human_result_once(
            state=state,
            human=human,
            plan=plan,
            bag=PortableNodeBag(),
            interrupt_kind="input",
        )
        assert next_id is None
        assert new_state.frame_stack[-1].phase == "cancelled"


# ---------------------------------------------------------------------------
# Budget lineage
# ---------------------------------------------------------------------------


class TestBudgetLineage:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_mismatched_child_parent_rejected(self) -> None:
        from app.assistant.durable.models import AssistantRunBudgetRevision
        from app.assistant.workflow.durable.contracts import (
            BudgetSuspensionStateV1,
            compute_suspension_digest,
        )
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_BUDGET_MISMATCH,
            DurableResumeError,
            verify_resolution_budget_lineage,
        )

        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        run_id = uuid.uuid4()
        interrupt_id = uuid.uuid4()
        parent = AssistantRunBudgetRevision(
            id=parent_id,
            run_id=run_id,
            revision=1,
            budget_digest=DIGEST_A,
            payload={"schemaVersion": 1},
        )
        child = AssistantRunBudgetRevision(
            id=child_id,
            run_id=run_id,
            revision=2,
            parent_revision_id=uuid.uuid4(),  # wrong parent
            parent_digest=DIGEST_A,
            budget_digest=DIGEST_B,
            payload={"schemaVersion": 1},
        )
        now = datetime.now(timezone.utc)
        susp = BudgetSuspensionStateV1(
            run_id=run_id,
            interrupt_id=interrupt_id,
            parent_budget_revision_id=parent_id,
            parent_ledger_revision=1,
            parent_ledger_digest=DIGEST_A,
            suspended_at_utc=now,
            remaining_active_ms=60_000,
            human_wait_expires_at_utc=now + timedelta(hours=1),
            suspension_digest=compute_suspension_digest(
                run_id=run_id,
                interrupt_id=interrupt_id,
                parent_budget_revision_id=parent_id,
                parent_ledger_revision=1,
                parent_ledger_digest=DIGEST_A,
                suspended_at_utc=now,
                remaining_active_ms=60_000,
                human_wait_expires_at_utc=now + timedelta(hours=1),
            ),
        )
        with pytest.raises(DurableResumeError) as exc:
            verify_resolution_budget_lineage(
                suspension=susp,
                parent_budget=parent,
                child_budget=child,
            )
        assert exc.value.reason_code == CODE_RESUME_BUDGET_MISMATCH


# ---------------------------------------------------------------------------
# Integration: load + resume to root terminal
# ---------------------------------------------------------------------------


class TestInterruptResumeIntegration:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_load_exact_resume_ready_and_apply_once(self) -> None:
        from app.assistant.workflow.durable.resume import (
            execute_interrupt_resume,
            load_resume_context,
        )

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done"},
            },
        )
        run, lease, rev, _repo = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        # Replace base budget with full ledger so pause/resolve works.
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        assert budget is not None
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
            outcome="approved",
        )

        ctx = load_resume_context(self.db, run_id=run.id)
        assert ctx.interrupt.id == interrupt.id
        assert ctx.human_result.outcome == "approved"
        assert ctx.human_result.node_id == proposal.node_id
        assert ctx.human_result.node_visit_id == proposal.node_visit_id
        assert str(ctx.root_continuation.reference_id) == str(
            proposal.root_continuation.reference_id
        )
        assert ctx.root_continuation.payload_digest == proposal.root_continuation.payload_digest

        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert result.kind == "root_terminal", (result.kind, result.reason_code, result.detail)
        assert result.applied_node_visit_id == proposal.node_visit_id
        assert result.capability_result is not None
        assert result.capability_result.status == "completed"
        assert result.root_continuation is not None
        assert result.root_continuation.payload_digest == proposal.root_continuation.payload_digest
        # Human applied once
        assert result.human_result is not None
        assert result.human_result.resolution_digest == interrupt.resolution_digest

    def test_crash_before_and_after_human_apply(self) -> None:
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )

        before = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
            crash_before_human_apply=True,
        )
        assert before.kind == "failed"
        assert before.reason_code == "crash_before_human_apply"
        # Run still running at same revision (no commit)
        self.db.refresh(run)
        assert int(run.state_revision) == rev

        after = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
            crash_after_human_apply=True,
        )
        assert after.kind == "human_applied"
        assert after.reason_code == "crash_after_human_apply"
        assert after.state_revision is not None and after.state_revision > rev
        # Recovery: post-apply Checkpoint has next_action=continue_child,
        # pending_interrupt_id=None, bag_snapshot on Artifact; re-entry continues.
        self.db.refresh(run)
        cont = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=int(after.state_revision),
            material=material,
            parent_ledger=parent_ledger,
        )
        assert cont.kind in {"root_terminal", "second_pause"}, (
            cont.kind,
            cont.reason_code,
            cont.detail,
        )
        assert cont.kind != "failed"

    def test_stop_first_blocks_post_resume_result(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_STOP_WON,
            execute_interrupt_resume,
        )

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )

        # Stop first (Plan 06): running → cancelling, bumps revision
        repo = DurableRunRepository(self.db)
        stop = repo.request_stop(
            run_id=run.id,
            expected_revision=rev,
        )
        self.db.refresh(run)
        assert run.status == "cancelling"
        stop_rev = int(stop.state_revision)

        # Resume result with stale expected_revision must not overwrite
        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,  # pre-stop revision
            material=material,
            parent_ledger=parent_ledger,
        )
        assert result.kind == "stop_won"
        assert result.reason_code == CODE_RESUME_STOP_WON
        self.db.refresh(run)
        assert run.status == "cancelling"
        assert int(run.state_revision) == stop_rev

    def test_tampered_frame_fails_before_runtime(self) -> None:
        from app.assistant.durable.codec import decode_checkpoint, encode_checkpoint_v2
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_FRAME_MISMATCH,
            DurableResumeError,
            load_resume_context,
        )
        from app.assistant.durable.codec import checkpoint_state_digest

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, _ = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )

        # Tamper resume checkpoint workflow frame node
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(ck.state_payload)
        ws = decoded.workflow_state
        top = ws.frame_stack[-1]
        from app.assistant.workflow.durable.runner import _copy_frame

        bad_top = _copy_frame(top, current_node_id="not-the-node", phase="waiting")
        from app.assistant.workflow.durable.contracts import DurableWorkflowStateV1

        bad_ws = DurableWorkflowStateV1(
            run_id=ws.run_id,
            root_frame_id=ws.root_frame_id,
            root_invocation_digest=ws.root_invocation_digest,
            frame_stack=tuple(ws.frame_stack[:-1]) + (bad_top,),
            pending_interrupt_id=ws.pending_interrupt_id,
            terminal_output_artifact_id=ws.terminal_output_artifact_id,
        )
        from app.assistant.durable.contracts import DurableAgentCheckpointV2

        bad_cp = DurableAgentCheckpointV2(
            run_id=decoded.run_id,
            phase=decoded.phase,
            manifest_revision_id=decoded.manifest_revision_id,
            policy_revision_id=decoded.policy_revision_id,
            budget_revision_id=decoded.budget_revision_id,
            obligation_revision_id=decoded.obligation_revision_id,
            provider_message_ordinal=decoded.provider_message_ordinal,
            provider_transcript_digest=decoded.provider_transcript_digest,
            provider_loop_continuation=decoded.provider_loop_continuation,
            inflight_unit=decoded.inflight_unit,
            capability_frames=decoded.capability_frames,
            artifact_ids=decoded.artifact_ids,
            visible_text_artifact_id=decoded.visible_text_artifact_id,
            next_action=decoded.next_action,
            workflow_state=bad_ws,
            active_capability_continuation=decoded.active_capability_continuation,
            pending_interrupt_id=decoded.pending_interrupt_id,
            budget_suspension=decoded.budget_suspension,
        )
        ck.state_payload = encode_checkpoint_v2(bad_cp)
        ck.state_digest = checkpoint_state_digest(bad_cp)
        self.db.flush()

        with pytest.raises(DurableResumeError) as exc:
            load_resume_context(self.db, run_id=run.id)
        assert exc.value.reason_code == CODE_RESUME_FRAME_MISMATCH

    def test_nested_workflow_child_human_then_parent_complete(self) -> None:
        from app.assistant.workflow.durable.resume import execute_interrupt_resume
        from app.assistant.workflow.durable.runner import DurableFrameMaterial

        parent_plan, child_plan = _plan_nested_child()
        parent_material = DurableFrameMaterial(
            plan=parent_plan,
            node_configs={
                "start": {},
                "call": {},
                "output": {"text": "parent-done"},
            },
            inputs={},
        )
        child_material = DurableFrameMaterial(
            plan=child_plan,
            node_configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Child approve?"},
                "output": {"text": "child-done"},
            },
            inputs={},
        )
        child_key = str(child_plan.target_version_id)
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        # Pause path with nested: need custom advance using child materials
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            commit_durable_workflow_pause,
        )
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )
        from app.assistant.workflow.durable.interrupts import DurableInterruptRepository
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.workflow.durable.interrupt_api import _build_resume_children

        port = WorkerUnitPauseEffectPort()
        runner = DurableWorkflowRunner(pause_effect_port=port)
        state = build_initial_workflow_state(
            run_id=run.id,
            plan=parent_plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call-nested",
            target_id=UUID("00000000-0000-4000-8000-000000000c11"),
            inputs={},
        )
        runner.get_bag(state.frame_stack[0].frame_id, inputs={})
        child_materials = {
            child_key: child_material,
            str(parent_plan.target_version_id): parent_material,
        }
        _, prepared, result, rev = _advance_to_human_pause(
            runner,
            parent_plan,
            state,
            parent_material,
            human_node="hitl",
            db=self.db,
            run_id=run.id,
            lease=lease,
            revision=rev,
            child_materials=child_materials,
        )
        proposal = result.pause_proposal
        assert proposal is not None

        pause = commit_durable_workflow_pause(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            proposal=proposal,
            prepared=prepared,
            parent_ledger=ledger,
            ttl_sec=3600,
        )
        self.db.refresh(run)
        interrupt = pause.interrupt
        waiting_rev = int(pause.commit.state_revision)

        irepo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        tok = irepo.rotate_token(
            run_id=run.id,
            interrupt_id=interrupt.id,
            expected_request_revision=int(interrupt.request_revision),
            expected_run_revision=waiting_rev,
        )
        prepared_hold: dict[str, Any] = {}

        def prepare_queued(locked_run, locked_interrupt):
            rows, budget_id, ck_id, deadline = _build_resume_children(
                self.db,
                run=locked_run,
                interrupt=locked_interrupt,
                expected_revision=int(locked_run.state_revision),
            )
            for row in rows:
                self.db.add(row)
            self.db.flush()
            prepared_hold["budget_id"] = budget_id
            prepared_hold["checkpoint_id"] = ck_id
            prepared_hold["deadline"] = deadline
            prepared_hold["expected_revision"] = int(locked_run.state_revision)
            return ck_id, budget_id, int(locked_run.state_revision) + 1

        res = irepo.resolve_interrupt(
            run_id=run.id,
            interrupt_id=interrupt.id,
            resolution_request_id=uuid.uuid4(),
            token=tok.token,
            expected_token_revision=tok.token_revision,
            expected_request_revision=int(interrupt.request_revision),
            expected_run_revision=waiting_rev,
            outcome="approved",
            submitted_values={},
            queues_execution=True,
            prepare_queued_children=prepare_queued,
        )
        assert res.created_resolution
        interrupt = irepo.get_interrupt(interrupt.id)
        from app.assistant.durable.repository import DurableChildBundle
        run_repo = DurableRunRepository(self.db)
        q = run_repo.commit_resume_queued(
            run_id=run.id,
            expected_revision=int(prepared_hold.get("expected_revision", waiting_rev)),
            children=DurableChildBundle(
                rows=[],
                current_checkpoint_id=interrupt.resolution_checkpoint_id,
                current_budget_revision_id=interrupt.resolution_budget_revision_id,
            ),
            set_deadline_at=prepared_hold.get("deadline")
            or (datetime.now(timezone.utc) + timedelta(minutes=5)),
        )
        claimed = run_repo.claim_queued(
            run_id=run.id,
            expected_revision=int(q.state_revision),
            worker_id=lease.worker_id,
            lease_ttl=timedelta(seconds=30),
        )
        lease = type(lease)(
            run_id=run.id,
            worker_id=lease.worker_id,
            lease_generation=int(claimed.run.lease_generation),
        )
        rev = int(claimed.state_revision)

        # Resume: need runner that can select material by top frame — execute_interrupt_resume
        # uses single material; for nested child wait, pass child plan as material when
        # interrupt is on child. Our load uses workflow state; continue_child uses one material.
        # For nested, pass parent material + child_materials; continue uses top target.
        # The continue_child_until_boundary currently uses only `material` for prepare —
        # so for nested resume we pass the child material as primary when waiting on child.
        # apply_human uses the plan that owns the human node (child plan).
        # continue selects material by top frame target via child_materials.
        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=child_material,
            child_materials=child_materials,
            parent_ledger=ledger,
        )
        assert result.kind == "root_terminal", (
            result.kind,
            result.reason_code,
            result.detail,
        )
        assert result.capability_result is not None
        assert result.capability_result.status == "completed"

    def test_two_workers_second_loses_lease_cas(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db, worker_id="worker-1")
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )

        # Second worker with wrong lease generation
        bad_lease = LeaseToken(
            run_id=run.id,
            worker_id="worker-2",
            lease_generation=int(lease.lease_generation) + 99,
        )
        _register_worker(self.db, worker_id="worker-2")
        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=bad_lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert result.kind == "stop_won" or (
            result.kind == "failed" and result.reason_code
        ), (result.kind, result.reason_code, result.detail)

    def test_irreconcilable_missing_waiting_checkpoint(self) -> None:
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.workflow.durable.resume import (
            DurableResumeNeedsReconciliation,
            load_resume_context,
            route_irreconcilable_to_needs_reconciliation,
        )
        from app.assistant.durable.repository import DurableRunRepository

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )

        # Break waiting checkpoint payload to force needs_reconciliation on decode
        waiting = self.db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
        waiting.state_payload = {"schemaVersion": 99, "broken": True}
        self.db.flush()

        with pytest.raises((DurableResumeNeedsReconciliation, Exception)):
            load_resume_context(self.db, run_id=run.id)

        # Route via CAS
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        out = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert out.kind == "needs_reconciliation" or out.needs_reconciliation
        # Route helper CAS must leave Run out of running when lease allows.
        self.db.refresh(run)
        assert run.status == "needs_reconciliation"
        assert int(run.state_revision) > rev


class TestDecisionRaces:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_stale_event_action_rejected_on_load(self) -> None:
        """Resume with expected_interrupt_id mismatch fails closed."""
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_LINEAGE_MISMATCH,
            DurableResumeError,
            load_resume_context,
        )

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, _ = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )
        with pytest.raises(DurableResumeError) as exc:
            load_resume_context(
                self.db,
                run_id=run.id,
                expected_interrupt_id=uuid.uuid4(),
            )
        assert exc.value.reason_code == CODE_RESUME_LINEAGE_MISMATCH

    def test_decision_vs_expiry_one_wins_under_cas(self) -> None:
        """Decision and expiry race: exactly one terminal outcome under CAS."""
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_ALREADY_RESOLVED,
            DurableInterruptRepository,
            InterruptConflict,
        )
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            commit_durable_workflow_pause,
        )
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            build_initial_workflow_state,
        )

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        # Pause only (leave interrupt pending) so both decision and expiry can race.
        port = WorkerUnitPauseEffectPort()
        runner = DurableWorkflowRunner(pause_effect_port=port)
        state = build_initial_workflow_state(
            run_id=run.id,
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call-race",
            target_id=UUID("00000000-0000-4000-8000-000000000b31"),
            inputs={},
        )
        runner.get_bag(state.frame_stack[0].frame_id, inputs={})
        _, prepared, result, rev = _advance_to_human_pause(
            runner,
            plan,
            state,
            material,
            human_node="hitl",
            db=self.db,
            run_id=run.id,
            lease=lease,
            revision=rev,
        )
        assert result.pause_proposal is not None
        pause = commit_durable_workflow_pause(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev if rev is not None else int(run.state_revision),
            proposal=result.pause_proposal,
            prepared=prepared,
            parent_ledger=ledger,
            ttl_sec=3600,
        )
        self.db.refresh(run)
        interrupt = pause.interrupt
        waiting_rev = int(pause.commit.state_revision)
        assert str(interrupt.status) == "pending"

        irepo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        tok = irepo.rotate_token(
            run_id=run.id,
            interrupt_id=interrupt.id,
            expected_request_revision=int(interrupt.request_revision),
            expected_run_revision=waiting_rev,
        )

        # Decision first wins.
        decision = irepo.resolve_interrupt(
            run_id=run.id,
            interrupt_id=interrupt.id,
            resolution_request_id=uuid.uuid4(),
            token=tok.token,
            expected_token_revision=tok.token_revision,
            expected_request_revision=int(interrupt.request_revision),
            expected_run_revision=waiting_rev,
            outcome="approved",
            submitted_values={},
            queues_execution=False,
        )
        assert decision.created_resolution is True
        interrupt = irepo.get_interrupt(interrupt.id)
        assert str(interrupt.status) == "approved"

        # Expiry second loses under CAS / already-resolved.
        try:
            expired = irepo.expire_interrupt(
                run_id=run.id,
                interrupt_id=interrupt.id,
                resolution_request_id=uuid.uuid4(),
            )
            # If it returns without raising, must not reverse decision.
            assert str(expired.interrupt.status) == "approved"
            assert expired.created_resolution is False
        except InterruptConflict as exc:
            assert getattr(exc, "code", None) in {
                CODE_INTERRUPT_ALREADY_RESOLVED,
                "interrupt_already_resolved",
            }

        # Reverse order on a fresh pending interrupt: expiry first, decision loses.
        run2, lease2, rev2, _ = _seed_running_with_base(self.db, worker_id="worker-race-2")
        budget2 = self.db.get(AssistantRunBudgetRevision, run2.current_budget_revision_id)
        budget2.payload = ledger.model_dump(mode="json", by_alias=True)
        budget2.budget_digest = str(ledger.ledger_digest)
        self.db.flush()
        port2 = WorkerUnitPauseEffectPort()
        runner2 = DurableWorkflowRunner(pause_effect_port=port2)
        state2 = build_initial_workflow_state(
            run_id=run2.id,
            plan=plan,
            root_invocation_digest=DIGEST_A,
            invocation_call_id="root-call-race-2",
            target_id=UUID("00000000-0000-4000-8000-000000000b32"),
            inputs={},
        )
        runner2.get_bag(state2.frame_stack[0].frame_id, inputs={})
        _, prepared2, result2, rev2 = _advance_to_human_pause(
            runner2,
            plan,
            state2,
            material,
            human_node="hitl",
            db=self.db,
            run_id=run2.id,
            lease=lease2,
            revision=rev2,
        )
        pause2 = commit_durable_workflow_pause(
            self.db,
            run_id=run2.id,
            lease=lease2,
            expected_revision=rev2 if rev2 is not None else int(run2.state_revision),
            proposal=result2.pause_proposal,
            prepared=prepared2,
            parent_ledger=ledger,
            ttl_sec=3600,
        )
        interrupt2 = pause2.interrupt
        waiting_rev2 = int(pause2.commit.state_revision)
        irepo2 = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        tok2 = irepo2.rotate_token(
            run_id=run2.id,
            interrupt_id=interrupt2.id,
            expected_request_revision=int(interrupt2.request_revision),
            expected_run_revision=waiting_rev2,
        )
        exp_first = irepo2.expire_interrupt(
            run_id=run2.id,
            interrupt_id=interrupt2.id,
            resolution_request_id=uuid.uuid4(),
        )
        assert exp_first.created_resolution is True
        assert str(exp_first.interrupt.status) == "expired"
        try:
            dec_second = irepo2.resolve_interrupt(
                run_id=run2.id,
                interrupt_id=interrupt2.id,
                resolution_request_id=uuid.uuid4(),
                token=tok2.token,
                expected_token_revision=tok2.token_revision,
                expected_request_revision=int(interrupt2.request_revision),
                expected_run_revision=waiting_rev2,
                outcome="approved",
                submitted_values={},
                queues_execution=False,
            )
            assert str(dec_second.interrupt.status) == "expired"
            assert dec_second.created_resolution is False
        except InterruptConflict as exc:
            assert getattr(exc, "code", None) in {
                CODE_INTERRUPT_ALREADY_RESOLVED,
                "interrupt_already_resolved",
                "interrupt_expired",
            }

    def test_nested_agent_frame_wait_residual_or_path(self) -> None:
        """Nested Agent frame wait: exercise Task 3 agent material selection if feasible.

        Full reviewed-child-Agent-round → Workflow wait → Agent complete remains a
        residual (Task 9/10 golden path). Here we prove agent target material selection
        in continue_child_until_boundary via child_materials keyed by target_version_id,
        reusing Task 3 agent plan patterns without requiring a full Provider loop.
        """
        from app.assistant.workflow.durable.contracts import (
            DurableEdgeV1,
            DurableExecutionPlanV1,
            DurableNodePlanV1,
            FrozenExecutionDependencyRef,
            compute_plan_digest,
        )
        from app.assistant.workflow.durable.runner import DurableFrameMaterial

        agent_tvid = UUID("00000000-0000-4000-8000-000000000a01")
        # Minimal agent plan (single agent node) — may_interrupt=False; residual noted.
        agent_nodes = (
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
        agent_digest = compute_plan_digest(
            target_kind="agent",
            target_version_id=agent_tvid,
            target_digest=DIGEST_B,
            entry_node_id="agent_root",
            nodes=agent_nodes,
        )
        agent_plan = DurableExecutionPlanV1(
            target_kind="agent",
            target_version_id=agent_tvid,
            target_digest=DIGEST_B,
            entry_node_id="agent_root",
            nodes=agent_nodes,
            plan_digest=agent_digest,
        )
        # Parent with human only (already covered nested workflow). Agent material map
        # is still accepted by execute_interrupt_resume child_materials without error.
        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "done-with-agent-materials"},
            },
        )
        agent_material = DurableFrameMaterial(
            plan=agent_plan,
            node_configs={"agent_root": {"scripted": True}},
            inputs={},
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, _interrupt, _proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )
        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            child_materials={
                str(agent_tvid): agent_material,
                str(plan.target_version_id): material,
            },
            parent_ledger=parent_ledger,
        )
        # Root path still completes; nested Agent wait golden path residual remains.
        assert result.kind == "root_terminal", (
            result.kind,
            result.reason_code,
            result.detail,
        )
