"""Plan 06 Task 6: budget/obligation recovery and uncommitted retry semantics.

Covers:
- uncommitted read/compute retry uses same logical unit
- does not consume a second round/call budget
- started charge only after mark_started / started CAS
- prepared reservation reuses same logical_unit_id
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
BUILD = "build-test-1"


def _session():
    from tests._db import make_session

    return make_session()


def _seed(db):
    from app.assistant.durable.materialize import materialize_base_run_state
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken
    from app.assistant.models import AssistantChatRun, Conversation, Message
    from app.assistant.provider_loop.messages import ProviderUserMessage

    conv = Conversation(title=f"b-{uuid.uuid4().hex[:6]}")
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
        memory_commit_status="pending",
        state_revision=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    repo = DurableRunRepository(db)
    claimed = repo.claim_queued(
        run_id=run.id,
        expected_revision=0,
        worker_id="worker-budget",
        lease_ttl=timedelta(seconds=30),
    )
    lease = LeaseToken(
        run_id=run.id,
        worker_id="worker-budget",
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
        budget_payload={
            "schemaVersion": 1,
            "revision": 0,
            "providerRoundsStarted": 0,
            "totalCallsStarted": 0,
        },
        budget_digest=DIGEST_A,
        obligation_payload={"schemaVersion": 1, "revision": 0},
        obligation_digest=DIGEST_A,
        provider_messages=(ProviderUserMessage(role="user", content="hi"),),
    )
    db.refresh(run)
    return run, lease, mat


class BudgetObligationRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _session()

    def tearDown(self) -> None:
        self.db.close()

    def test_uncommitted_retry_reuses_same_logical_unit_and_attempt_increment(self) -> None:
        """Crash after prepare/started before result: recovery reuses unit, bumps attempt only."""
        from app.assistant.durable.checkpoints import (
            commit_prepared_unit,
            commit_started_unit,
            resolve_retry_unit,
        )
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.models import AssistantRunCheckpoint

        run, lease, mat = _seed(self.db)
        unit = DurableExecutionUnitV1(
            logical_unit_id="provider:round:0",
            kind="provider_round",
            state="prepared",
            provider_round=0,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        prep = commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=mat.state_revision,
            unit=unit,
            phase="ready_for_provider",
            next_action_kind="continue_provider",
        )
        started = DurableExecutionUnitV1(
            logical_unit_id="provider:round:0",
            kind="provider_round",
            state="started",
            provider_round=0,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=1,
        )
        start = commit_started_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep.state_revision,
            unit=started,
            phase="ready_for_provider",
            next_action_kind="continue_provider",
            budget_payload={
                "schemaVersion": 1,
                "revision": 1,
                "providerRoundsStarted": 1,
                "totalCallsStarted": 0,
            },
            budget_digest=DIGEST_B,
            budget_revision_number=2,
        )
        self.db.refresh(run)
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(ck.state_payload)

        # Simulate recovery: same logical unit, attempt + 1, preserve started budget.
        retry = resolve_retry_unit(decoded.inflight_unit)
        self.assertEqual(retry.logical_unit_id, "provider:round:0")
        self.assertEqual(retry.attempt, 2)
        self.assertEqual(retry.state, "started")
        self.assertEqual(retry.started_budget_revision, 1)
        self.assertEqual(retry.reserved_budget_revision, 0)

        # Committing the retry unit does not append a second started budget charge.
        retry_commit = commit_started_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=start.state_revision,
            unit=retry,
            phase="ready_for_provider",
            next_action_kind="continue_provider",
            # Same budget revision pointer — no new charge.
            budget_payload=None,
            budget_digest=None,
            budget_revision_number=None,
        )
        self.db.refresh(run)
        from app.assistant.durable.models import AssistantRunBudgetRevision

        n_budget = (
            self.db.query(AssistantRunBudgetRevision).filter_by(run_id=run.id).count()
        )
        # Base (1) + first started (1) = 2; retry must not add a third.
        self.assertEqual(n_budget, 2)
        self.assertEqual(retry_commit.status, "running")

    def test_prepared_only_retry_does_not_consume_started_budget(self) -> None:
        from app.assistant.durable.checkpoints import (
            commit_prepared_unit,
            resolve_retry_unit,
        )
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.models import AssistantRunBudgetRevision

        run, lease, mat = _seed(self.db)
        from app.assistant.policy.recursion import build_capability_call_frame

        frame = build_capability_call_frame(
            call_id="call-read-1",
            capability_type="tool",
            domain_key="tools.search",
            target_identity="remote-tool:search",
            target_version_id=None,
            binding_contract_digest=DIGEST_A,
            owner_kind="main_agent",
            owner_version_id=uuid.UUID(int=42),
            capability_depth=1,
            agent_depth=1,
        )
        unit = DurableExecutionUnitV1(
            logical_unit_id="cap:group:read-1",
            kind="capability_group",
            state="prepared",
            provider_round=0,
            call_ids=("call-read-1",),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        prep = commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=mat.state_revision,
            unit=unit,
            phase="dispatching_calls",
            next_action_kind="dispatch_calls",
            capability_frames=(frame,),
        )
        retry = resolve_retry_unit(
            DurableExecutionUnitV1(
                logical_unit_id="cap:group:read-1",
                kind="capability_group",
                state="prepared",
                provider_round=0,
                call_ids=("call-read-1",),
                attempt=1,
                reserved_budget_revision=0,
                started_budget_revision=None,
            )
        )
        self.assertEqual(retry.attempt, 2)
        self.assertIsNone(retry.started_budget_revision)
        n_budget = (
            self.db.query(AssistantRunBudgetRevision).filter_by(run_id=run.id).count()
        )
        self.assertEqual(n_budget, 1)
        self.assertEqual(prep.status, "running")

    def test_same_logical_unit_short_circuits_when_post_result_exists(self) -> None:
        from app.assistant.durable.checkpoints import (
            commit_prepared_unit,
            commit_started_unit,
            commit_unit_result,
            find_post_result_for_unit,
        )
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.provider_loop.messages import ProviderAssistantMessage

        run, lease, mat = _seed(self.db)
        unit_id = "provider:round:0"
        prep = commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=mat.state_revision,
            unit=DurableExecutionUnitV1(
                logical_unit_id=unit_id,
                kind="provider_round",
                state="prepared",
                provider_round=0,
                call_ids=(),
                attempt=1,
                reserved_budget_revision=0,
                started_budget_revision=None,
            ),
            phase="ready_for_provider",
            next_action_kind="continue_provider",
        )
        start = commit_started_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep.state_revision,
            unit=DurableExecutionUnitV1(
                logical_unit_id=unit_id,
                kind="provider_round",
                state="started",
                provider_round=0,
                call_ids=(),
                attempt=1,
                reserved_budget_revision=0,
                started_budget_revision=1,
            ),
            phase="ready_for_provider",
            next_action_kind="continue_provider",
            budget_payload={
                "schemaVersion": 1,
                "revision": 1,
                "providerRoundsStarted": 1,
            },
            budget_digest=DIGEST_B,
            budget_revision_number=2,
        )
        commit_unit_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=start.state_revision,
            phase="ready_for_completion",
            next_action_kind="complete",
            clear_inflight=True,
            provider_messages=(
                ProviderAssistantMessage(role="assistant", content="done", tool_calls=()),
            ),
            completed_logical_unit_id=unit_id,
        )
        found = find_post_result_for_unit(self.db, run_id=run.id, logical_unit_id=unit_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.phase, "ready_for_completion")


if __name__ == "__main__":
    unittest.main()
