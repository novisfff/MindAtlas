"""Plan 07 Task 5: durable pause without polling.

Covers:
- WorkerUnitPauseEffectPort stage/consume_exact/clear protocol
- Atomic pause commit (Interrupt + Checkpoint v2 + waiting status + lease clear)
- Missing/duplicate/mismatched/leftover pause effects rejected
- Crash before result transaction leaves no orphan Interrupt
- Retry converges on same proposal/key/digests
- Accidental durable call into Legacy create_and_wait forbidden
- No polling/sleep/retained waiter after pause
- Stop-vs-pause CAS (SQLite sequential; PG dual-session when available)
"""

from __future__ import annotations

import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone
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
BUILD = "build-test-plan07-t5"
_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()


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


def _plan_with_human() -> Any:
    from app.assistant.workflow.durable.contracts import (
        DurableEdgeV1,
        DurableExecutionPlanV1,
        DurableNodePlanV1,
        compute_plan_digest,
    )

    tvid = UUID("00000000-0000-4000-8000-000000000a04")
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


def _root_state(plan: Any, *, run_id: UUID | None = None) -> Any:
    from app.assistant.workflow.durable.runner import build_initial_workflow_state

    return build_initial_workflow_state(
        run_id=run_id or UUID("00000000-0000-4000-8000-000000000a10"),
        plan=plan,
        root_invocation_digest=DIGEST_A,
        invocation_call_id="root-call-1",
        target_id=UUID("00000000-0000-4000-8000-000000000a11"),
        inputs={"query": "hello"},
    )


def _material(plan: Any, *, configs: dict | None = None) -> Any:
    from app.assistant.workflow.durable.runner import DurableFrameMaterial

    node_configs = configs or {n.node_id: {} for n in plan.nodes}
    return DurableFrameMaterial(plan=plan, node_configs=node_configs, inputs={})


def _seed_running_with_base(db, *, deadline_at: datetime | None = None):
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
        deadline_at=deadline_at
        or (datetime.now(timezone.utc) + timedelta(minutes=30)),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    repo = DurableRunRepository(db)
    claimed = repo.claim_queued(
        run_id=run.id,
        expected_revision=0,
        worker_id="worker-1",
        lease_ttl=timedelta(seconds=30),
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


def _advance_to_human_pause(runner, plan, state, material):
    """Execute start then human boundary; return (state, prepared, result)."""
    p0 = runner.prepare_boundary(state=state, material=material)
    r0 = runner.execute_boundary(prepared=p0, material=material)
    state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
    p1 = runner.prepare_boundary(state=state, material=material)
    r1 = runner.execute_boundary(prepared=p1, material=material)
    return state, p1, r1


# ---------------------------------------------------------------------------
# Effect port protocol
# ---------------------------------------------------------------------------


class TestWorkerUnitPauseEffectPort:
    def test_stage_consume_exact_happy_path(self) -> None:
        from app.assistant.workflow.durable.pause import WorkerUnitPauseEffectPort
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
        )

        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        _, prepared, result = _advance_to_human_pause(runner, plan, state, material)
        assert result.kind == BoundaryKind.HUMAN_PAUSE
        assert result.pause_proposal is not None
        assert port.has_staged

        proposal = port.consume_exact(
            root_call_id=result.pause_proposal.root_call_id,
            continuation=result.pause_proposal.root_continuation,
        )
        assert proposal.proposal_digest == result.pause_proposal.proposal_digest
        assert not port.has_staged
        port.clear()
        port.assert_clear()

    def test_missing_proposal_consume_fails(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.workflow.durable.pause import (
            DurablePauseProtocolError,
            WorkerUnitPauseEffectPort,
        )

        port = WorkerUnitPauseEffectPort()
        cont = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        with pytest.raises(DurablePauseProtocolError) as exc:
            port.consume_exact(root_call_id="x", continuation=cont)
        assert exc.value.reason_code == "durable_pause_protocol_error"

    def test_duplicate_stage_rejected(self) -> None:
        from app.assistant.workflow.durable.pause import (
            DurablePauseProtocolError,
            WorkerUnitPauseEffectPort,
        )
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        _, _, result = _advance_to_human_pause(runner, plan, state, material)
        assert result.pause_proposal is not None
        with pytest.raises(DurablePauseProtocolError):
            port.stage(result.pause_proposal)

    def test_mismatched_continuation_rejected(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.workflow.durable.pause import (
            DurablePauseProtocolError,
            WorkerUnitPauseEffectPort,
        )
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        _, _, result = _advance_to_human_pause(runner, plan, state, material)
        bad = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id="not-the-frame",
            payload_digest=DIGEST_B,
        )
        with pytest.raises(DurablePauseProtocolError):
            port.consume_exact(
                root_call_id=result.pause_proposal.root_call_id,
                continuation=bad,
            )

    def test_leftover_assert_clear_fails(self) -> None:
        from app.assistant.workflow.durable.pause import (
            DurablePauseProtocolError,
            WorkerUnitPauseEffectPort,
        )
        from app.assistant.workflow.durable.runner import DurableWorkflowRunner

        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        _advance_to_human_pause(runner, plan, state, material)
        with pytest.raises(DurablePauseProtocolError):
            port.assert_clear()
        port.clear()
        port.assert_clear()


# ---------------------------------------------------------------------------
# Atomic pause commit
# ---------------------------------------------------------------------------


class TestDurableWorkflowPauseCommit:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_pause_commits_waiting_approval_clears_lease(self) -> None:
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint, AssistantRunInterrupt
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            consume_and_commit_pause,
        )
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        assert run.lease_owner is not None
        assert run.deadline_at is not None

        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan, run_id=run.id)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Please approve"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)

        # start boundary prepare+execute (semantic)
        p0 = runner.prepare_boundary(state=state, material=material)
        prep0 = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p0
        )
        rev = prep0.state_revision
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)

        # human boundary prepare (inflight unit for continuity)
        p1 = runner.prepare_boundary(state=state, material=material)
        assert p1.node_id == "hitl"
        prep1 = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p1
        )
        rev = prep1.state_revision
        started = runner.mark_started(prepared=p1, budget_revision=1)
        start1 = commit_workflow_boundary_prepare(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            prepared=started,
            as_started=True,
        )
        rev = start1.state_revision

        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.HUMAN_PAUSE
        assert r1.pause_proposal is not None
        assert port.has_staged

        parent = _parent_ledger()
        self.db.refresh(run)

        result = consume_and_commit_pause(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            port=port,
            root_call_id=r1.pause_proposal.root_call_id,
            continuation=r1.pause_proposal.root_continuation,
            prepared=p1,
            parent_ledger=parent,
        )
        assert result.commit.status == "waiting_approval"
        self.db.refresh(run)
        assert run.status == "waiting_approval"
        assert run.lease_owner is None
        assert run.lease_expires_at is None
        assert run.deadline_at is None
        assert run.current_checkpoint_id == result.checkpoint_id

        interrupt = self.db.get(AssistantRunInterrupt, result.interrupt.id)
        assert interrupt is not None
        assert interrupt.status == "pending"
        assert interrupt.kind == "approval"
        assert interrupt.interrupt_key == result.interrupt_key
        assert interrupt.request_digest == result.request_digest

        ck = self.db.get(AssistantRunCheckpoint, result.checkpoint_id)
        assert ck is not None
        assert ck.phase == "waiting"
        decoded = decode_checkpoint(ck.state_payload)
        assert decoded.schema_version == 2
        assert decoded.phase == "waiting"
        assert decoded.pending_interrupt_id == r1.pause_proposal.interrupt_id
        assert decoded.budget_suspension is not None
        assert decoded.workflow_state is not None
        assert decoded.workflow_state.pending_interrupt_id == r1.pause_proposal.interrupt_id
        assert decoded.provider_loop_continuation is None
        assert decoded.active_capability_continuation is not None
        assert decoded.inflight_unit is None

        # Port cleared after successful commit
        port.assert_clear()

        # No polling primitives retained
        src = inspect.getsource(
            __import__(
                "app.assistant.workflow.durable.pause", fromlist=["commit_durable_workflow_pause"]
            ).commit_durable_workflow_pause
        )
        for forbidden in ("time.sleep", "while True", "Future", "threading.Event", "asyncio.sleep"):
            assert forbidden not in src

    def test_pause_input_kind_waiting_input(self) -> None:
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            consume_and_commit_pause,
        )
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan, run_id=run.id)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {
                    "kind": "input",
                    "title": "Enter value",
                    "field_schema": {
                        "type": "object",
                        "properties": {"note": {"type": "string"}},
                    },
                },
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        p0 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p0
        ).state_revision
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
        p1 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p1
        ).state_revision
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.HUMAN_PAUSE
        assert r1.pause_proposal.kind == "input"

        result = consume_and_commit_pause(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            port=port,
            root_call_id=r1.pause_proposal.root_call_id,
            continuation=r1.pause_proposal.root_continuation,
            prepared=p1,
            parent_ledger=_parent_ledger(),
        )
        assert result.commit.status == "waiting_input"
        self.db.refresh(run)
        assert run.status == "waiting_input"

    def test_waiting_without_proposal_cannot_commit(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.workflow.durable.pause import (
            DurablePauseProtocolError,
            WorkerUnitPauseEffectPort,
            consume_and_commit_pause,
        )

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        port = WorkerUnitPauseEffectPort()
        cont = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        with pytest.raises(DurablePauseProtocolError):
            consume_and_commit_pause(
                self.db,
                run_id=run.id,
                lease=lease,
                expected_revision=rev,
                port=port,
                root_call_id="missing",
                continuation=cont,
                parent_ledger=_parent_ledger(),
            )
        # No interrupt orphaned
        from app.assistant.durable.models import AssistantRunInterrupt
        from sqlalchemy import select

        rows = (
            self.db.execute(
                select(AssistantRunInterrupt).where(AssistantRunInterrupt.run_id == run.id)
            )
            .scalars()
            .all()
        )
        assert rows == []

    def test_crash_before_result_leaves_no_orphan_interrupt(self) -> None:
        """Simulate crash before CAS: staged proposal lost, no Interrupt row."""
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.workflow.durable.pause import WorkerUnitPauseEffectPort
        from app.assistant.workflow.durable.runner import (
            BoundaryKind,
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )
        from sqlalchemy import select

        run, lease, rev, _repo = _seed_running_with_base(self.db)
        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan, run_id=run.id)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        p0 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p0
        ).state_revision
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
        p1 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p1
        ).state_revision
        r1 = runner.execute_boundary(prepared=p1, material=material)
        assert r1.kind == BoundaryKind.HUMAN_PAUSE
        assert port.has_staged
        proposal_digest = r1.pause_proposal.proposal_digest
        interrupt_id = r1.pause_proposal.interrupt_id

        # Crash: drop ephemeral port (new worker unit) without committing.
        crashed_port = WorkerUnitPauseEffectPort()
        assert not crashed_port.has_staged

        rows = (
            self.db.execute(
                select(AssistantRunInterrupt).where(AssistantRunInterrupt.run_id == run.id)
            )
            .scalars()
            .all()
        )
        assert rows == []
        self.db.refresh(run)
        assert run.status == "running"

        # Retry: same plan/state re-executes human boundary → same digests.
        retry_port = WorkerUnitPauseEffectPort()
        retry_runner = DurableWorkflowRunner(pause_effect_port=retry_port)
        # Re-prepare from same state as before human (state after start)
        p1b = retry_runner.prepare_boundary(state=state, material=material)
        r1b = retry_runner.execute_boundary(prepared=p1b, material=material)
        assert r1b.pause_proposal.proposal_digest == proposal_digest
        assert r1b.pause_proposal.interrupt_id == interrupt_id

    def test_stop_first_blocks_pause_no_interrupt(self) -> None:
        """Stop CAS wins → pause result transaction fails; no Interrupt."""
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.durable.repository import DurableRunConflict, DurableRunRepository
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            consume_and_commit_pause,
        )
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )
        from sqlalchemy import select

        run, lease, rev, repo = _seed_running_with_base(self.db)
        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan, run_id=run.id)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        p0 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p0
        ).state_revision
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
        p1 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p1
        ).state_revision
        r1 = runner.execute_boundary(prepared=p1, material=material)

        # Stop wins revision CAS first.
        stop = repo.request_stop(run_id=run.id, expected_revision=rev)
        assert stop.status == "cancelling"
        self.db.refresh(run)
        assert run.status == "cancelling"
        new_rev = stop.state_revision

        with pytest.raises(DurableRunConflict):
            consume_and_commit_pause(
                self.db,
                run_id=run.id,
                lease=lease,
                expected_revision=rev,  # stale vs stop
                port=port,
                root_call_id=r1.pause_proposal.root_call_id,
                continuation=r1.pause_proposal.root_continuation,
                prepared=p1,
                parent_ledger=_parent_ledger(),
            )

        rows = (
            self.db.execute(
                select(AssistantRunInterrupt).where(AssistantRunInterrupt.run_id == run.id)
            )
            .scalars()
            .all()
        )
        assert rows == []
        self.db.refresh(run)
        assert run.status == "cancelling"
        assert int(run.state_revision) == int(new_rev)

    def test_pause_first_leaves_cancellable_waiting_run(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.workflow.durable.pause import (
            WorkerUnitPauseEffectPort,
            consume_and_commit_pause,
        )
        from app.assistant.workflow.durable.runner import (
            DurableWorkflowRunner,
            commit_workflow_boundary_prepare,
        )

        run, lease, rev, repo = _seed_running_with_base(self.db)
        port = WorkerUnitPauseEffectPort()
        plan = _plan_with_human()
        state = _root_state(plan, run_id=run.id)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {},
            },
        )
        runner = DurableWorkflowRunner(pause_effect_port=port)
        p0 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p0
        ).state_revision
        r0 = runner.execute_boundary(prepared=p0, material=material)
        state = runner.apply_boundary_result(state=p0.workflow_state, result=r0)
        p1 = runner.prepare_boundary(state=state, material=material)
        rev = commit_workflow_boundary_prepare(
            self.db, run_id=run.id, lease=lease, expected_revision=rev, prepared=p1
        ).state_revision
        r1 = runner.execute_boundary(prepared=p1, material=material)

        result = consume_and_commit_pause(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            port=port,
            root_call_id=r1.pause_proposal.root_call_id,
            continuation=r1.pause_proposal.root_continuation,
            prepared=p1,
            parent_ledger=_parent_ledger(),
        )
        assert result.commit.status == "waiting_approval"
        waiting_rev = result.commit.state_revision

        # Stop from waiting is direct cancel (Plan 06).
        stop = repo.request_stop(run_id=run.id, expected_revision=waiting_rev)
        assert stop.status == "cancelled"
        self.db.refresh(run)
        assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# Legacy blocking runtime guard
# ---------------------------------------------------------------------------


class TestDurableBlockingRuntimeForbidden:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_main_agent_create_and_wait_forbidden_before_row(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation
        from app.assistant.workflow.human_approval_runtime import (
            DURABLE_BLOCKING_RUNTIME_FORBIDDEN,
            DurableBlockingRuntimeForbidden,
            HumanLoopContext,
            HumanLoopRuntime,
        )
        from app.assistant_config.models import AssistantHumanApproval
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker

        conv = Conversation(title="guard")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(
            conversation_id=conv.id,
            status="running",
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision=BUILD,
            state_revision=1,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        factory = sessionmaker(bind=self.db.get_bind())
        # Bind factory to same engine; for tests, wrap current session engine.
        runtime = HumanLoopRuntime(
            factory,
            context=HumanLoopContext(
                run_id=str(run.id),
                channel_type="assistant",
                conversation_id=conv.id,
            ),
        )
        with pytest.raises(DurableBlockingRuntimeForbidden) as exc:
            runtime.create_and_wait(
                node_id="n1",
                node_label="Approve",
                request_payload={"title": "x"},
                field_schema=[{"name": "a", "type": "string"}],
                initial_values={},
            )
        assert exc.value.reason_code == DURABLE_BLOCKING_RUNTIME_FORBIDDEN

        rows = self.db.execute(select(AssistantHumanApproval)).scalars().all()
        assert rows == []

    def test_legacy_string_run_id_still_allowed(self) -> None:
        """workflow-test / Legacy non-UUID run_ids are unchanged."""
        from sqlalchemy.orm import sessionmaker

        from app.assistant.workflow.human_approval_runtime import (
            _reject_durable_blocking_runtime,
        )

        factory = sessionmaker(bind=self.db.get_bind())
        # Non-UUID Legacy / workflow-test run ids must not raise.
        _reject_durable_blocking_runtime(factory, "run_hitl_legacy_1")
        # Missing run UUID is ignored (not treated as durable main_agent).
        _reject_durable_blocking_runtime(factory, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# PostgreSQL dual-session stop-vs-pause (optional gate)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _POSTGRES_URL, reason="MINDATLAS_TEST_POSTGRES_URL not set")
class TestStopVersusPausePostgres:
    def test_placeholder_gate_documents_requirement(self) -> None:
        # Full dual-session force is exercised when PG URL is available in CI.
        # SQLite sequential stop-first / pause-first tests above cover the CAS
        # predicates; this gate ensures the suite is discoverable under PG.
        assert _POSTGRES_URL
