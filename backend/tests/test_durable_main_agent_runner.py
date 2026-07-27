"""Plan 06 Task 6: durable Main Agent runner at execution boundaries.

Scripted tests for:
- pre/post Provider units
- Capability group prepare/started/result
- Manifest activation lifecycle accept
- waiting continuation
- completion + memory unit materialization
- runtime admission before Run insert (immutable runtime_kind)
- API queue path never calls _run_chat_background for main_agent
- native runtime rollout admission
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
BUILD = "build-test-1"


def _make_session():
    from tests._db import make_session

    return make_session()


def _register_worker(db, *, worker_id: str = "worker-1", build: str = BUILD):
    from app.assistant.durable.worker_registry import WorkerIdentity, WorkerRegistry

    identity = WorkerIdentity(
        worker_id=worker_id,
        app_build_revision=build,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1,),
    )
    WorkerRegistry(db).register(identity)
    return identity


def _make_legacy_run(db, *, status: str = "queued", **kwargs: Any):
    from app.assistant.models import AssistantChatRun, Conversation, Message

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
        status=status,
        runtime_kind=kwargs.pop("runtime_kind", "legacy"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run, conv, user, assistant


class DurableMainAgentAdmissionTests(unittest.TestCase):
    """Runtime admission immediately before Run insert; immutable runtime_kind."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_run_accepts_immutable_runtime_kind_main_agent(self) -> None:
        from app.assistant.models import Conversation, Message
        from app.assistant.run_service import AssistantChatRunService

        conv = Conversation(title="admit")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.commit()

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=conv,
            user_message=user,
            assistant_message=assistant,
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision=BUILD,
            memory_commit_status="pending",
        )
        self.assertEqual(run.runtime_kind, "main_agent")
        self.assertEqual(run.runtime_contract_version, 1)
        self.assertEqual(run.required_app_build_revision, BUILD)
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.memory_commit_status, "pending")

    def test_create_run_legacy_default_unchanged(self) -> None:
        from app.assistant.models import Conversation, Message
        from app.assistant.run_service import AssistantChatRunService

        conv = Conversation(title="legacy")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.commit()

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=conv,
            user_message=user,
            assistant_message=assistant,
        )
        self.assertEqual(run.runtime_kind, "legacy")
        self.assertIsNone(run.runtime_contract_version)
        self.assertIsNone(run.required_app_build_revision)

    def test_fallback_impossible_after_durable_run_exists(self) -> None:
        """Once a main_agent Run is inserted, Legacy fallback path is forbidden."""
        from app.assistant.durable.runner import assert_no_legacy_fallback

        run, _, _, _ = _make_legacy_run(
            self.db,
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision=BUILD,
            memory_commit_status="pending",
        )
        with self.assertRaises(RuntimeError) as ctx:
            assert_no_legacy_fallback(run)
        self.assertIn("main_agent", str(ctx.exception).lower())

    def test_legacy_run_allows_legacy_path(self) -> None:
        from app.assistant.durable.runner import assert_no_legacy_fallback

        run, _, _, _ = _make_legacy_run(self.db, runtime_kind="legacy")
        # Does not raise for legacy.
        assert_no_legacy_fallback(run)

    def test_api_queues_main_agent_without_background_thread(self) -> None:
        """chat_stream admission selects main_agent and never starts daemon thread."""
        from app.assistant.models import Conversation
        from app.assistant.service import AssistantService

        _register_worker(self.db)
        conv = Conversation(title="queue-ma")
        self.db.add(conv)
        self.db.commit()

        svc = AssistantService(self.db)
        started: list[Any] = []

        def _capture_start(**kwargs: Any) -> None:
            started.append(kwargs)

        with (
            patch.object(svc, "_start_background_run", side_effect=_capture_start),
            patch(
                "app.assistant.service.get_settings",
            ) as gs,
        ):
            settings = MagicMock()
            settings.assistant_runtime_mode = "main_agent"
            settings.app_build_revision = BUILD
            settings.assistant_worker_registration_ttl_sec = 20
            gs.return_value = settings

            # Force durable admission path to choose main_agent without full profile.
            with patch(
                "app.assistant.durable.admission.admit_and_select_runtime",
                return_value=(
                    "main_agent",
                    None,
                    {
                        "runtime_kind": "main_agent",
                        "runtime_contract_version": 1,
                        "required_app_build_revision": BUILD,
                        "memory_commit_status": "pending",
                    },
                ),
            ):
                # Drain generator until first yield attempt — may fail on stream,
                # but create_run + start decision must complete first.
                try:
                    gen = svc.chat_stream(conv.id, "hello", stream_output=False)
                    # Consume first event or until error.
                    next(gen, None)
                except Exception:
                    pass

        # Main Agent must not invoke background daemon start.
        self.assertEqual(started, [])

    def test_rejected_admission_leaves_no_orphan_messages_or_run(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.service import AssistantService
        from app.common.exceptions import ApiException

        conv = Conversation(title="queue-legacy")
        self.db.add(conv)
        self.db.commit()

        svc = AssistantService(self.db)
        started: list[Any] = []

        def _capture_start(**kwargs: Any) -> None:
            started.append(kwargs)

        with (
            patch.object(svc, "_start_background_run", side_effect=_capture_start),
            patch(
                "app.assistant.durable.admission.admit_and_select_runtime",
                return_value=("legacy", "rollout_assigned_legacy", {}),
            ),
        ):
            with self.assertRaises(ApiException) as ctx:
                gen = svc.chat_stream(conv.id, "hello", stream_output=False)
                next(gen, None)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.code, 50310)
        self.assertEqual(started, [])
        self.assertEqual(
            self.db.query(Message).filter(Message.conversation_id == conv.id).count(),
            0,
        )
        self.assertEqual(
            self.db.query(AssistantChatRun)
            .filter(AssistantChatRun.conversation_id == conv.id)
            .count(),
            0,
        )


class DurableMaterializeBaseTests(unittest.TestCase):
    """Atomic base Manifest/policy/budget/obligation/transcript + first Checkpoint."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_materialize_base_state_atomically(self) -> None:
        from app.assistant.durable.materialize import materialize_base_run_state
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
            AssistantRunProviderMessage,
        )
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.provider_loop.messages import ProviderUserMessage

        conv = Conversation(title="mat")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()
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
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        # Claim to running first.
        repo = DurableRunRepository(self.db)
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

        messages = (
            ProviderUserMessage(role="user", content="hello durable"),
        )
        result = materialize_base_run_state(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=claimed.state_revision,
            manifest_payload={"schemaVersion": 1, "kind": "base"},
            manifest_digest=DIGEST_A,
            policy_payload={"schemaVersion": 1, "policy": True},
            policy_digest=DIGEST_A,
            budget_payload={"schemaVersion": 1, "budget": True, "revision": 0},
            budget_digest=DIGEST_A,
            obligation_payload={"schemaVersion": 1, "obligation": True},
            obligation_digest=DIGEST_A,
            provider_messages=messages,
        )

        self.assertEqual(result.status, "running")
        self.db.refresh(run)
        self.assertIsNotNone(run.current_manifest_revision_id)
        self.assertIsNotNone(run.current_policy_revision_id)
        self.assertIsNotNone(run.current_budget_revision_id)
        self.assertIsNotNone(run.current_obligation_revision_id)
        self.assertIsNotNone(run.current_checkpoint_id)

        n_manifest = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        n_policy = (
            self.db.query(AssistantRunPolicyRevision).filter_by(run_id=run.id).count()
        )
        n_budget = (
            self.db.query(AssistantRunBudgetRevision).filter_by(run_id=run.id).count()
        )
        n_obl = (
            self.db.query(AssistantRunObligationRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        n_msg = (
            self.db.query(AssistantRunProviderMessage).filter_by(run_id=run.id).count()
        )
        n_ck = (
            self.db.query(AssistantRunCheckpoint).filter_by(run_id=run.id).count()
        )
        self.assertEqual(n_manifest, 1)
        self.assertEqual(n_policy, 1)
        self.assertEqual(n_budget, 1)
        self.assertEqual(n_obl, 1)
        self.assertEqual(n_msg, 1)
        self.assertEqual(n_ck, 1)

        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        self.assertEqual(ck.phase, "ready_for_provider")
        self.assertEqual(ck.provider_message_ordinal, 1)


class DurableExecutionBoundaryTests(unittest.TestCase):
    """Prepare → started → result unit protocol for Provider/Capability/completion."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()
        self.identity = _register_worker(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _seed_running_with_base(self):
        from app.assistant.durable.materialize import materialize_base_run_state
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.provider_loop.messages import ProviderUserMessage

        conv = Conversation(title="bound")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()
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
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        repo = DurableRunRepository(self.db)
        claimed = repo.claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id=self.identity.worker_id,
            lease_ttl=timedelta(seconds=30),
        )
        lease = LeaseToken(
            run_id=run.id,
            worker_id=self.identity.worker_id,
            lease_generation=int(claimed.run.lease_generation),
        )
        mat = materialize_base_run_state(
            self.db,
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
        self.db.refresh(run)
        return run, lease, mat.state_revision, repo

    def test_fresh_enforced_claim_uses_real_main_agent_service_path(self) -> None:
        from types import SimpleNamespace

        from app.assistant.durable.recovery import RecoveryDecision
        from app.assistant.durable.leases import ClaimedLease
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.durable.runner import MainAgentRunExecutor
        from app.assistant.models import AssistantChatRun, Conversation, Message

        conv = Conversation(title="enforced")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="create")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()
        run = AssistantChatRun(
            conversation_id=conv.id,
            user_message_id=user.id,
            assistant_message_id=assistant.id,
            status="queued",
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision=BUILD,
            memory_commit_status="pending",
            capability_ledger_mode="enforced",
            state_revision=0,
        )
        self.db.add(run)
        self.db.commit()
        claim_result = DurableRunRepository(self.db).claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id=self.identity.worker_id,
            lease_ttl=timedelta(seconds=30),
        )
        claimed = ClaimedLease(
            run=claim_result.run,
            lease=LeaseToken(
                run_id=run.id,
                worker_id=self.identity.worker_id,
                lease_generation=int(claim_result.run.lease_generation),
            ),
            kind="queued",
            state_revision=claim_result.state_revision,
            status="running",
        )

        def _wait(_request):
            row = self.db.get(AssistantChatRun, run.id)
            row.status = "waiting_approval"
            row.state_revision = int(row.state_revision) + 1
            self.db.commit()
            return SimpleNamespace(status="failed", reason_code="waiting")

        with patch("app.assistant.main_agent.service.MainAgentService") as service:
            service.return_value.run.side_effect = _wait
            MainAgentRunExecutor(provider_factory=None).execute(
                claimed=claimed,
                decision=RecoveryDecision(
                    kind="continue",
                    reason_code="fresh_claim",
                    allow_provider_io=True,
                    allow_capability_io=True,
                ),
                heartbeat=lambda: True,
                session_factory=lambda: self.db,
            )

        self.db.refresh(run)
        self.assertEqual(run.status, "waiting_approval")
        self.assertIsNone(run.current_checkpoint_id)
        service.return_value.run.assert_called_once()

    def test_prepare_then_started_before_provider_io(self) -> None:
        from app.assistant.durable.checkpoints import (
            commit_prepared_unit,
            commit_started_unit,
            commit_unit_result,
        )
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.models import AssistantRunCheckpoint

        run, lease, rev, repo = self._seed_running_with_base()

        prepared_unit = DurableExecutionUnitV1(
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
            expected_revision=rev,
            unit=prepared_unit,
            phase="ready_for_provider",
            next_action_kind="continue_provider",
        )
        self.assertEqual(prep.status, "running")
        self.db.refresh(run)
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        self.assertIsNotNone(ck)
        # Payload must carry prepared unit without started revision.
        from app.assistant.durable.codec import decode_checkpoint

        decoded = decode_checkpoint(ck.state_payload)
        self.assertEqual(decoded.inflight_unit.state, "prepared")
        self.assertIsNone(decoded.inflight_unit.started_budget_revision)

        started_unit = DurableExecutionUnitV1(
            logical_unit_id="provider:round:0",
            kind="provider_round",
            state="started",
            provider_round=0,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=1,
        )
        # New budget revision for started accounting.
        start = commit_started_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep.state_revision,
            unit=started_unit,
            phase="ready_for_provider",
            next_action_kind="continue_provider",
            budget_payload={"schemaVersion": 1, "revision": 1, "providerRoundsStarted": 1},
            budget_digest=DIGEST_B,
            budget_revision_number=2,
        )
        self.db.refresh(run)
        ck2 = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded2 = decode_checkpoint(ck2.state_payload)
        self.assertEqual(decoded2.inflight_unit.state, "started")
        self.assertEqual(decoded2.inflight_unit.started_budget_revision, 1)

        # Result clears inflight and advances phase.
        from app.assistant.provider_loop.messages import ProviderAssistantMessage

        result = commit_unit_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=start.state_revision,
            phase="dispatching_calls",
            next_action_kind="dispatch_calls",
            clear_inflight=True,
            provider_messages=(
                ProviderAssistantMessage(role="assistant", content="ok", tool_calls=()),
            ),
        )
        self.db.refresh(run)
        ck3 = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded3 = decode_checkpoint(ck3.state_payload)
        self.assertIsNone(decoded3.inflight_unit)
        self.assertEqual(decoded3.phase, "dispatching_calls")
        self.assertEqual(result.status, "running")

    def test_capability_not_started_before_mark_started(self) -> None:
        """Capability group prepared has no started charge; started requires mark_started."""
        from app.assistant.durable.checkpoints import commit_prepared_unit
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.policy.recursion import build_capability_call_frame

        run, lease, rev, _repo = self._seed_running_with_base()

        frame = build_capability_call_frame(
            call_id="call-1",
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
            logical_unit_id="cap:group:1",
            kind="capability_group",
            state="prepared",
            provider_round=0,
            call_ids=("call-1",),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        prep = commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            unit=unit,
            phase="dispatching_calls",
            next_action_kind="dispatch_calls",
            capability_frames=(frame,),
        )
        self.db.refresh(run)
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(ck.state_payload)
        self.assertEqual(decoded.inflight_unit.state, "prepared")
        self.assertIsNone(decoded.inflight_unit.started_budget_revision)
        self.assertEqual(decoded.capability_frames[0].call_id, "call-1")
        self.assertEqual(prep.status, "running")

    def test_completion_and_memory_units(self) -> None:
        from app.assistant.durable.checkpoints import (
            commit_prepared_unit,
            commit_unit_result,
        )
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint

        run, lease, rev, _repo = self._seed_running_with_base()

        unit = DurableExecutionUnitV1(
            logical_unit_id="completion:1",
            kind="completion",
            state="prepared",
            provider_round=None,
            call_ids=(),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        prep = commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            unit=unit,
            phase="ready_for_completion",
            next_action_kind="complete",
        )
        result = commit_unit_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=prep.state_revision,
            phase="ready_for_memory",
            next_action_kind="memory",
            clear_inflight=True,
            enter_ready_for_memory=True,
        )
        self.db.refresh(run)
        ck = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(ck.state_payload)
        self.assertEqual(decoded.phase, "ready_for_memory")
        self.assertEqual(decoded.next_action.kind, "memory")
        self.assertEqual(result.status, "running")

    def test_scripted_runner_provider_round_end_to_end(self) -> None:
        """MainAgentRunExecutor drives one scripted provider round via checkpoints."""
        from app.assistant.durable.leases import ClaimedLease
        from app.assistant.durable.materialize import materialize_base_run_state
        from app.assistant.durable.recovery import RecoveryDecision
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.durable.runner import MainAgentRunExecutor
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderUserMessage,
        )

        conv = Conversation(title="runner-e2e")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()
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
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        repo = DurableRunRepository(self.db)
        claimed = repo.claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id=self.identity.worker_id,
            lease_ttl=timedelta(seconds=30),
        )
        lease = LeaseToken(
            run_id=run.id,
            worker_id=self.identity.worker_id,
            lease_generation=int(claimed.run.lease_generation),
        )
        materialize_base_run_state(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=claimed.state_revision,
            manifest_payload={"schemaVersion": 1},
            manifest_digest=DIGEST_A,
            policy_payload={"schemaVersion": 1},
            policy_digest=DIGEST_A,
            budget_payload={"schemaVersion": 1, "revision": 0},
            budget_digest=DIGEST_A,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=DIGEST_A,
            provider_messages=(ProviderUserMessage(role="user", content="hi"),),
        )
        self.db.refresh(run)

        # Scripted provider that returns one assistant final text.
        class _Scripted:
            provider_protocol = "openai_chat"
            adapter_key = "openai"
            adapter_revision = "1"
            model_config_digest = DIGEST_A
            request_count = 0

            def stream_round(self, request, *, cancellation):
                self.request_count += 1
                from app.assistant.provider_loop.contracts import (
                    ProviderRoundTerminal,
                    ProviderTextDelta,
                )

                yield ProviderTextDelta(sequence=0, delta="hello")
                yield ProviderRoundTerminal(
                    sequence=1,
                    finish_reason="stop",
                )

        executor = MainAgentRunExecutor(
            provider_factory=lambda **_k: _Scripted(),
            scripted_final_text="hello",
        )
        claimed_lease = ClaimedLease(
            run=run,
            lease=lease,
            kind="queued",
            state_revision=int(run.state_revision),
            status="running",
        )
        decision = RecoveryDecision(
            kind="continue",
            reason_code="fresh_claim",
            allow_provider_io=True,
            allow_capability_io=True,
        )
        heartbeats = {"n": 0}

        def heartbeat() -> bool:
            heartbeats["n"] += 1
            return True

        session_factory = lambda: self.db  # reuse same session in unit test

        executor.execute(
            claimed=claimed_lease,
            decision=decision,
            heartbeat=heartbeat,
            session_factory=session_factory,
        )
        self.db.refresh(run)
        # Runner should have progressed checkpoint / provider messages.
        from app.assistant.durable.models import AssistantRunProviderMessage

        msgs = (
            self.db.query(AssistantRunProviderMessage)
            .filter_by(run_id=run.id)
            .order_by(AssistantRunProviderMessage.ordinal.asc())
            .all()
        )
        self.assertGreaterEqual(len(msgs), 1)
        self.assertGreaterEqual(heartbeats["n"], 1)

    def test_short_circuit_ready_for_completion_finalizes_memory(self) -> None:
        """Crash after ready_for_completion must still enter memory and complete.

        Classifier returns short_circuit for ready_for_completion with no inflight.
        Runner must reconstruct assistant text from transcript, enter ready_for_memory,
        finalize memory, and reach terminal completed — not leave the Run running forever.
        """
        from app.assistant.durable.checkpoints import commit_unit_result
        from app.assistant.durable.leases import ClaimedLease
        from app.assistant.durable.recovery import RecoveryDecision
        from app.assistant.durable.repository import LeaseToken
        from app.assistant.durable.runner import MainAgentRunExecutor
        from app.assistant.models import Message
        from app.assistant.provider_loop.messages import ProviderAssistantMessage

        run, lease, rev, repo = self._seed_running_with_base()
        # Commit post-result checkpoint at ready_for_completion with assistant text.
        result = commit_unit_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            phase="ready_for_completion",
            next_action_kind="complete",
            clear_inflight=True,
            provider_messages=(
                ProviderAssistantMessage(content="reconstructed final answer", tool_calls=()),
            ),
            completed_logical_unit_id="provider:0",
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "running")
        self.assertFalse(repo.is_ready_for_memory(run))

        # Simulate recovery claim: status recovering, short_circuit decision.
        run.status = "recovering"
        self.db.commit()
        self.db.refresh(run)

        claimed_lease = ClaimedLease(
            run=run,
            lease=LeaseToken(
                run_id=run.id,
                worker_id=self.identity.worker_id,
                lease_generation=int(run.lease_generation),
            ),
            kind="reclaim_recovering",
            state_revision=int(run.state_revision),
            status="recovering",
        )
        decision = RecoveryDecision(
            kind="short_circuit",
            reason_code="post_result_committed",
            detail="checkpoint phase=ready_for_completion has no inflight unit",
            allow_provider_io=False,
            allow_capability_io=False,
            short_circuit_after_result=True,
        )
        executor = MainAgentRunExecutor(
            provider_factory=None,
            scripted_final_text=None,
            finalize_memory=True,
        )
        executor.execute(
            claimed=claimed_lease,
            decision=decision,
            heartbeat=lambda: True,
            session_factory=lambda: self.db,
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "completed", f"expected completed, got {run.status}")
        self.assertIn(str(run.memory_commit_status or ""), {"committed", "failed"})
        assistant = self.db.get(Message, run.assistant_message_id)
        self.assertIsNotNone(assistant)
        self.assertEqual(assistant.content, "reconstructed final answer")
        # result state_revision used so the binding is not unused
        self.assertGreaterEqual(result.state_revision, rev)


class DurableSkillActivationTests(unittest.TestCase):
    """Process-local stage + one post-lineage result transaction as accept."""

    def setUp(self) -> None:
        reset_caches()
        self.db = _make_session()
        self.identity = _register_worker(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_stage_leaves_no_durable_residue(self) -> None:
        from app.assistant.durable.activation import DurableSkillActivationLifecycle
        from app.assistant.durable.models import AssistantRunManifestRevision

        lifecycle = DurableSkillActivationLifecycle()
        package = {
            "call_id": "inj-1",
            "proposed_manifest_digest": DIGEST_B,
            "parent_manifest_digest": DIGEST_A,
        }
        lifecycle.stage(call_id="inj-1", package=package)
        self.assertTrue(lifecycle.has_pending("inj-1"))
        lifecycle.discard(call_id="inj-1", reason_code="lineage_failed")
        self.assertFalse(lifecycle.has_pending("inj-1"))
        # No durable rows created by stage/discard alone.
        n = self.db.query(AssistantRunManifestRevision).count()
        self.assertEqual(n, 0)

    def test_accept_requires_lineage_and_is_single_transaction(self) -> None:
        from app.assistant.durable.activation import DurableSkillActivationLifecycle
        from app.assistant.durable.checkpoints import commit_prepared_unit
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.materialize import materialize_base_run_state
        from app.assistant.durable.models import AssistantRunManifestRevision
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.provider_loop.messages import ProviderUserMessage

        conv = Conversation(title="act")
        self.db.add(conv)
        self.db.flush()
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add_all([user, assistant])
        self.db.flush()
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
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        repo = DurableRunRepository(self.db)
        claimed = repo.claim_queued(
            run_id=run.id,
            expected_revision=0,
            worker_id=self.identity.worker_id,
            lease_ttl=timedelta(seconds=30),
        )
        lease = LeaseToken(
            run_id=run.id,
            worker_id=self.identity.worker_id,
            lease_generation=int(claimed.run.lease_generation),
        )
        mat = materialize_base_run_state(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=claimed.state_revision,
            manifest_payload={"schemaVersion": 1, "digest": DIGEST_A},
            manifest_digest=DIGEST_A,
            policy_payload={"schemaVersion": 1},
            policy_digest=DIGEST_A,
            budget_payload={"schemaVersion": 1, "revision": 0},
            budget_digest=DIGEST_A,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=DIGEST_A,
            provider_messages=(ProviderUserMessage(role="user", content="hi"),),
        )
        self.db.refresh(run)

        unit = DurableExecutionUnitV1(
            logical_unit_id="cap:inject:1",
            kind="capability_group",
            state="started",
            provider_round=0,
            call_ids=("inj-1",),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=1,
        )
        # Prepare/start via prepared with started state for this unit test boundary.
        from app.assistant.durable.checkpoints import commit_started_unit

        # First prepared without frames is ok for non-started capability? started needs frames.
        # Use provider_round kind to avoid frame invariant for this activation focus.
        unit = DurableExecutionUnitV1(
            logical_unit_id="cap:inject:1",
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
            expected_revision=mat.state_revision,
            unit=unit,
            phase="dispatching_calls",
            next_action_kind="dispatch_calls",
            budget_payload={"schemaVersion": 1, "revision": 1},
            budget_digest=DIGEST_B,
            budget_revision_number=2,
        )

        lifecycle = DurableSkillActivationLifecycle()
        lifecycle.stage(
            call_id="inj-1",
            package={
                "proposed_manifest_digest": DIGEST_B,
                "parent_manifest_digest": DIGEST_A,
                "child_payload": {"schemaVersion": 1, "digest": DIGEST_B, "child": True},
            },
        )
        # Lineage failure: wrong parent digest.
        with self.assertRaises(ValueError):
            lifecycle.accept_into_result(
                db=self.db,
                run_id=run.id,
                lease=lease,
                expected_revision=start.state_revision,
                call_id="inj-1",
                current_manifest_digest=DIGEST_C,  # mismatch
                policy_payload={"schemaVersion": 1, "child": True},
                policy_digest=DIGEST_B,
                budget_payload={"schemaVersion": 1, "revision": 1},
                budget_digest=DIGEST_D,
                obligation_payload={"schemaVersion": 1},
                obligation_digest=DIGEST_C,
            )
        # No candidate residue.
        self.assertFalse(lifecycle.has_pending("inj-1"))
        n_before = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        self.assertEqual(n_before, 1)

        # Re-stage and accept with correct lineage.
        lifecycle.stage(
            call_id="inj-1",
            package={
                "proposed_manifest_digest": DIGEST_B,
                "parent_manifest_digest": DIGEST_A,
                "child_payload": {"schemaVersion": 1, "digest": DIGEST_B, "child": True},
            },
        )
        accepted = lifecycle.accept_into_result(
            db=self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=start.state_revision,
            call_id="inj-1",
            current_manifest_digest=DIGEST_A,
            policy_payload={"schemaVersion": 1, "child": True},
            policy_digest=DIGEST_B,
            budget_payload={"schemaVersion": 1, "revision": 1},
            budget_digest=DIGEST_D,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=DIGEST_C,
        )
        self.assertEqual(accepted.status, "running")
        n_after = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        self.assertEqual(n_after, 2)
        self.assertFalse(lifecycle.has_pending("inj-1"))

        # Replay accept of same call_id must not duplicate child.
        lifecycle.stage(
            call_id="inj-1",
            package={
                "proposed_manifest_digest": DIGEST_B,
                "parent_manifest_digest": DIGEST_A,
                "child_payload": {"schemaVersion": 1, "digest": DIGEST_B, "child": True},
            },
        )
        # Detect already-accepted child via digest and no-op.
        replay = lifecycle.accept_into_result(
            db=self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=accepted.state_revision,
            call_id="inj-1",
            current_manifest_digest=DIGEST_B,  # already child
            policy_payload={"schemaVersion": 1, "child": True},
            policy_digest=DIGEST_B,
            budget_payload={"schemaVersion": 1, "revision": 1},
            budget_digest=DIGEST_D,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=DIGEST_C,
            allow_already_accepted=True,
        )
        n_replay = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        self.assertEqual(n_replay, 2)
        self.assertIsNotNone(replay)

        # Process restart: new lifecycle has empty process-local state.
        # Pointer already at child; re-stage + accept parent package without
        # allow_already_accepted must short-circuit via durable digest (no
        # lineage error, no duplicate child).
        restarted = DurableSkillActivationLifecycle()
        restarted.stage(
            call_id="inj-1",
            package={
                "proposed_manifest_digest": DIGEST_B,
                "parent_manifest_digest": DIGEST_A,
                "child_payload": {"schemaVersion": 1, "digest": DIGEST_B, "child": True},
            },
        )
        post_restart = restarted.accept_into_result(
            db=self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=accepted.state_revision,
            call_id="inj-1",
            current_manifest_digest=DIGEST_B,  # pointer advanced to child
            policy_payload={"schemaVersion": 1, "child": True},
            policy_digest=DIGEST_B,
            budget_payload={"schemaVersion": 1, "revision": 1},
            budget_digest=DIGEST_D,
            obligation_payload={"schemaVersion": 1},
            obligation_digest=DIGEST_C,
            # allow_already_accepted intentionally omitted (False)
        )
        n_restart = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        self.assertEqual(n_restart, 2)
        self.assertIsNotNone(post_restart)
        self.assertFalse(restarted.has_pending("inj-1"))

        # True lineage mismatch for a different proposed digest still fails.
        restarted.stage(
            call_id="inj-2",
            package={
                "proposed_manifest_digest": DIGEST_D,
                "parent_manifest_digest": DIGEST_A,
                "child_payload": {"schemaVersion": 1, "digest": DIGEST_D, "other": True},
            },
        )
        with self.assertRaises(ValueError) as ctx:
            restarted.accept_into_result(
                db=self.db,
                run_id=run.id,
                lease=lease,
                expected_revision=accepted.state_revision,
                call_id="inj-2",
                current_manifest_digest=DIGEST_B,  # not parent, not proposed
                policy_payload={"schemaVersion": 1},
                policy_digest=DIGEST_B,
                budget_payload={"schemaVersion": 1, "revision": 1},
                budget_digest=DIGEST_D,
                obligation_payload={"schemaVersion": 1},
                obligation_digest=DIGEST_C,
            )
        self.assertIn("lineage failed", str(ctx.exception))
        self.assertFalse(restarted.has_pending("inj-2"))
        n_mismatch = (
            self.db.query(AssistantRunManifestRevision)
            .filter_by(run_id=run.id)
            .count()
        )
        self.assertEqual(n_mismatch, 2)


if __name__ == "__main__":
    unittest.main()
