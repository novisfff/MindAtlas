"""Plan 07 Task 7: multiple sequential interrupts with stable outer ContinuationRef.

Covers:
- Root Workflow pauses twice; outer ContinuationRef unchanged
- Second pause does not call resume_provider_loop / does not replace ContinuationRef
- Only after root terminal is one ProviderWaitingResolution built
- Decision vs cancel / expiry race vectors at resume claim time
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

# Reuse helpers from interrupt resume suite
from tests.test_durable_interrupt_resume import (  # noqa: E402
    BUILD,
    DIGEST_A,
    DIGEST_B,
    DIGEST_C,
    DIGEST_D,
    PEPPER,
    _advance_to_human_pause,
    _make_session,
    _material,
    _parent_ledger,
    _pause_and_resolve,
    _plan_with_human,
    _register_worker,
    _seed_running_with_base,
)


class TestMultipleSequentialInterrupts:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_two_sequential_pauses_stable_outer_continuation(self) -> None:
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        plan = _plan_with_human(second_hitl=True)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl1": {"kind": "approval", "title": "First?"},
                "hitl2": {"kind": "approval", "title": "Second?"},
                "output": {"text": "done-twice"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt1, proposal1, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
            second_hitl=True,
            outcome="approved",
        )
        outer1 = proposal1.root_continuation

        # Resume first interrupt → second pause
        result1 = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert result1.kind == "second_pause", (
            result1.kind,
            result1.reason_code,
            result1.detail,
        )
        assert result1.pause_commit is not None
        assert result1.root_continuation is not None
        assert result1.root_continuation.reference_id == outer1.reference_id
        assert result1.root_continuation.payload_digest == outer1.payload_digest
        assert (
            result1.pause_commit.proposal.root_continuation.reference_id
            == outer1.reference_id
        )
        assert (
            result1.pause_commit.proposal.root_continuation.payload_digest
            == outer1.payload_digest
        )
        # Outer ContinuationRef deliberately does not identify the interrupt row
        assert result1.pause_commit.proposal.interrupt_id != interrupt1.id

        self.db.refresh(run)
        assert run.status in {"waiting_approval", "waiting_input"}
        interrupt2 = result1.pause_commit.interrupt
        waiting_rev = int(result1.pause_commit.commit.state_revision)

        # Resolve second interrupt and queue again
        from app.assistant.durable.repository import DurableChildBundle, DurableRunRepository
        from app.assistant.workflow.durable.interrupt_api import _build_resume_children
        from app.assistant.workflow.durable.interrupts import DurableInterruptRepository

        irepo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        tok = irepo.rotate_token(
            run_id=run.id,
            interrupt_id=interrupt2.id,
            expected_request_revision=int(interrupt2.request_revision),
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
            interrupt_id=interrupt2.id,
            resolution_request_id=uuid.uuid4(),
            token=tok.token,
            expected_token_revision=tok.token_revision,
            expected_request_revision=int(interrupt2.request_revision),
            expected_run_revision=waiting_rev,
            outcome="approved",
            submitted_values={},
            queues_execution=True,
            prepare_queued_children=prepare_queued,
        )
        assert res.created_resolution
        interrupt2 = irepo.get_interrupt(interrupt2.id)
        run_repo = DurableRunRepository(self.db)
        q = run_repo.commit_resume_queued(
            run_id=run.id,
            expected_revision=int(prepared_hold["expected_revision"]),
            children=DurableChildBundle(
                rows=[],
                current_checkpoint_id=interrupt2.resolution_checkpoint_id,
                current_budget_revision_id=interrupt2.resolution_budget_revision_id,
            ),
            set_deadline_at=prepared_hold["deadline"],
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

        result2 = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert result2.kind == "root_terminal", (
            result2.kind,
            result2.reason_code,
            result2.detail,
        )
        assert result2.root_continuation is not None
        assert result2.root_continuation.reference_id == outer1.reference_id
        assert result2.root_continuation.payload_digest == outer1.payload_digest
        assert result2.capability_result is not None
        assert result2.capability_result.status == "completed"

    def test_decision_vs_stop_before_second_resume(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_STOP_WON,
            execute_interrupt_resume,
        )

        plan = _plan_with_human(second_hitl=True)
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl1": {"kind": "approval", "title": "First?"},
                "hitl2": {"kind": "approval", "title": "Second?"},
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

        run, lease, rev, _i, _p, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
            second_hitl=True,
        )
        # Stop before worker applies human result
        repo = DurableRunRepository(self.db)
        stop = repo.request_stop(run_id=run.id, expected_revision=rev)
        self.db.refresh(run)
        assert run.status == "cancelling"
        out = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
        )
        assert out.kind == "stop_won"
        assert out.reason_code == CODE_RESUME_STOP_WON
        self.db.refresh(run)
        assert run.status == "cancelling"
        assert int(run.state_revision) == int(stop.state_revision)
