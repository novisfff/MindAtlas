"""Plan 08 Task 6: local transactional create_entry + UoW boundary."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class LocalTransactionStoragePlaceholderTests(unittest.TestCase):
    def test_local_transactional_mode_is_declared(self) -> None:
        from app.assistant.capability_calls.contracts import CAPABILITY_EXECUTION_MODES

        self.assertIn("local_transactional", CAPABILITY_EXECUTION_MODES)

    def test_entry_model_exposes_source_capability_call_id(self) -> None:
        from app.entry.models import Entry

        self.assertTrue(hasattr(Entry, "source_capability_call_id"))

    def test_architecture_forbids_committing_create_import(self) -> None:
        from app.assistant.capability_calls.local_write import (
            assert_no_committing_create_import,
        )

        assert_no_committing_create_import()


class EntryCreateInUowTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session
        from app.entry_type.models import EntryType

        self.db = make_session()
        self.etype = EntryType(
            code="KNOWLEDGE",
            name="Knowledge",
            color="#1",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add(self.etype)
        self.db.commit()
        self.db.refresh(self.etype)

    def tearDown(self) -> None:
        self.db.close()

    def test_create_in_uow_rollback_leaves_zero(self) -> None:
        from app.entry.models import Entry
        from app.entry.schemas import EntryRequest
        from app.entry.service import EntryService
        from app.entry.models import TimeMode
        from app.lightrag.models import EntryIndexOutbox

        svc = EntryService(self.db)
        req = EntryRequest(
            title="t",
            summary="s",
            content="c",
            type_id=self.etype.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        entry = svc.create_in_uow(req, source_capability_call_id=None)
        self.assertIsNotNone(entry.id)
        # visible in session
        self.assertEqual(self.db.query(Entry).count(), 1)
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 1)
        self.db.rollback()
        self.assertEqual(self.db.query(Entry).count(), 0)
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 0)

    def test_create_wrapper_still_commits(self) -> None:
        from app.entry.models import Entry, TimeMode
        from app.entry.schemas import EntryRequest
        from app.entry.service import EntryService

        svc = EntryService(self.db)
        req = EntryRequest(
            title="t2",
            summary="s",
            content="c",
            type_id=self.etype.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        entry = svc.create(req)
        self.assertIsNotNone(entry.id)
        # new session-like check: same session after commit still sees row
        self.assertEqual(self.db.query(Entry).filter(Entry.id == entry.id).count(), 1)

    def test_uow_commit_spy(self) -> None:
        from app.assistant.capability_calls.uow import (
            UnitOfWorkBoundaryError,
            install_commit_spy,
        )

        restore = install_commit_spy(self.db)
        try:
            with self.assertRaises(UnitOfWorkBoundaryError):
                self.db.commit()
        finally:
            restore()


class LocalTransactionalGoldenPathTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session
        from app.entry_type.models import EntryType
        from app.assistant.models import Conversation
        from app.assistant.durable.models import (
            AssistantRunArtifact,
            AssistantRunBudgetRevision,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.durable.repository import LeaseToken
        from tests.assistant_runtime_support import make_main_agent_run
        from app.assistant.policy import (
            create_initial_ledger_state,
            normalize_run_budget_limits,
        )
        import hashlib
        import os

        self.db = make_session()
        self.etype = EntryType(
            code="KNOWLEDGE",
            name="Knowledge",
            color="#1",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add(self.etype)
        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        self.run = make_main_agent_run(
            self.db,
            conversation=conv,
            status="running",
            build_revision="b1",
            runtime_contract_version=1,
            required_app_build_revision="b1",
            capability_ledger_mode="enforced",
            state_revision=1,
            lease_owner="worker-1",
            lease_generation=1,
            memory_commit_status="pending",
        )
        self.manifest = AssistantRunManifestRevision(
            run_id=self.run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        self.db.add(self.manifest)
        self.db.flush()
        self.policy = AssistantRunPolicyRevision(
            run_id=self.run.id,
            revision=1,
            policy_digest=DIGEST_B,
            payload={},
        )
        started = datetime.now(timezone.utc)
        budget_ledger = create_initial_ledger_state(
            limits=normalize_run_budget_limits(),
            started_at_utc=started,
            deadline_at_utc=started + timedelta(minutes=2),
        )
        self.budget = AssistantRunBudgetRevision(
            run_id=self.run.id,
            revision=1,
            budget_digest=budget_ledger.ledger_digest,
            payload=budget_ledger.model_dump(mode="json", by_alias=True),
        )
        self.obligation = AssistantRunObligationRevision(
            run_id=self.run.id,
            revision=1,
            obligation_digest="d" * 64,
            payload={},
        )
        self.db.add_all([self.policy, self.budget, self.obligation])
        self.db.flush()
        self.run.current_manifest_revision_id = self.manifest.id
        self.run.current_policy_revision_id = self.policy.id
        self.run.current_budget_revision_id = self.budget.id
        self.run.current_obligation_revision_id = self.obligation.id
        self.run.deadline_at = budget_ledger.deadline_at_utc
        payload = os.urandom(8)
        self.art = AssistantRunArtifact(
            run_id=self.run.id,
            kind="call_input",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            inline_bytes=payload,
            metadata_json={},
        )
        self.db.add(self.art)
        self.db.flush()
        self.lease = LeaseToken(
            run_id=self.run.id, worker_id="worker-1", lease_generation=1
        )
        self.repo = CapabilityCallRepository(self.db)
        self.call_id = uuid.uuid4()
        call, _ = self.repo.create_or_verify_proposed(
            ProposeCallSpec(
                call_id=self.call_id,
                run_id=self.run.id,
                expected_run_revision=1,
                lease=self.lease,
                manifest_revision_id=self.manifest.id,
                logical_call_key="provider:0:0:create1",
                owner_kind="skill_version",
                capability_type="tool",
                domain_key="create_entry",
                descriptor_digest=DIGEST_A,
                authorization_digest=DIGEST_A,
                input_artifact_id=self.art.id,
                input_digest=DIGEST_A,
                side_effect_class="write_local",
                execution_mode="local_transactional",
                idempotency_key="idem-" + uuid.uuid4().hex,
            )
        )
        self.repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=self.lease,
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_atomic_create_and_call_success(self) -> None:
        from app.assistant.capability_calls.local_write import (
            create_entry_local_transactional,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.entry.models import Entry, TimeMode
        from app.entry.schemas import EntryRequest
        from app.lightrag.models import EntryIndexOutbox

        req = EntryRequest(
            title="golden",
            summary="s",
            content="body",
            type_id=self.etype.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        result = create_entry_local_transactional(
            session=self.db,
            request=req,
            call_id=self.call_id,
            expected_call_revision=1,
            expected_run_revision=1,
            lease=self.lease,
        )
        entry = self.db.query(Entry).filter(Entry.id == result.entry_id).one()
        self.assertEqual(entry.source_capability_call_id, self.call_id)
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 1)
        call = (
            self.db.query(AssistantCapabilityCall)
            .filter(AssistantCapabilityCall.id == self.call_id)
            .one()
        )
        self.assertEqual(call.status, "succeeded")
        self.assertIsNotNone(call.side_effect_started_at)
        # Idempotent second call does not create a second entry.
        result2 = create_entry_local_transactional(
            session=self.db,
            request=req,
            call_id=self.call_id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=1,
            lease=self.lease,
        )
        self.assertEqual(result2.entry_id, result.entry_id)
        self.assertEqual(self.db.query(Entry).count(), 1)

    def test_post_approval_guard_denial_is_durable_without_attempt_or_entry(self) -> None:
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import (
            DurableCapabilityLedgerAggregate,
        )
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.policy.budgets import BudgetLedger, DeterministicBudgetClock
        from app.assistant.policy.contracts import (
            normalize_owner_budget_limits,
            normalize_run_budget_limits,
        )
        from app.assistant.policy.runtime import (
            BudgetLedgerDispatchGuard,
            BudgetLedgerReservationPort,
        )
        from app.assistant.provider_loop.contracts import CapabilityCallReservationItem
        from app.assistant.provider_loop.contracts import LedgerPrepareOutcome
        from app.entry.models import Entry
        from app.lightrag.models import EntryIndexOutbox
        from app.tag.models import Tag
        from tests.test_agent_policy_runtime import _base_manifest
        from tests._db import allowing_test_write_guard

        guard = allowing_test_write_guard(self.db)
        guard.evaluate_post_approval_locked = lambda **_kwargs: SimpleNamespace(
            allowed=False,
            reason_code="pre_ga_launch_unapproved",
        )
        arguments = {
            "title": "must not exist",
            "content": "blocked",
            "type_code": "KNOWLEDGE",
            "tags": [f"blocked-post-approval-{uuid.uuid4().hex}"],
            "time_mode": "POINT",
            "time_at": "2026-07-18",
        }
        limits = normalize_run_budget_limits(
            operator_limits={
                "max_total_capability_calls": 2,
                "max_parallel_calls": 1,
                "max_same_read_signature": 2,
            }
        )
        owner_version_id = uuid.uuid4()
        budget_ledger = BudgetLedger.create(
            limits=limits,
            owner_limits=(
                normalize_owner_budget_limits(
                    owner_kind="main_agent",
                    owner_version_id=owner_version_id,
                    run_limits=limits,
                ),
            ),
            clock=DeterministicBudgetClock(),
        )
        assert BudgetLedgerReservationPort(ledger=budget_ledger).reserve_one(
            CapabilityCallReservationItem(
                call_id="post-approval-denied",
                owner_kind="main_agent",
                owner_version_id=owner_version_id,
                domain_key="create_entry",
                side_effect="write_local",
                arguments_digest=sha256_canonical_json(arguments),
                binding_contract_digest=DIGEST_A,
                capability_depth=1,
                agent_depth=1,
            )
        ).allowed
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=guard,
            lease=self.lease,
            dispatch_guard=BudgetLedgerDispatchGuard(ledger=budget_ledger),
        )
        seeded_call = self.db.get(AssistantCapabilityCall, self.call_id)
        seeded_call.provider_tool_call_id = "post-approval-denied"
        self.db.commit()
        outcome = LedgerPrepareOutcome(
            kind="dispatch_local",
            call_id=self.call_id,
            call_revision=1,
        )
        current_manifest, _resolved_surface = _base_manifest(run_id=self.run.id)
        blocked_tag_name = arguments["tags"][0]
        request = SimpleNamespace(
            call=SimpleNamespace(
                call_id="post-approval-denied",
                arguments=arguments,
            ),
            binding=SimpleNamespace(),
            current_manifest=current_manifest,
        )

        result = aggregate.execute_local(outcome, request)
        self.assertEqual(result.capability_result.status, "failed")
        self.assertEqual(
            result.capability_result.error.safe_code,
            "pre_ga_launch_unapproved",
        )
        self.assertEqual(result.capability_result.error.retry_disposition, "never")
        aggregate.commit_result(outcome, result)
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            ProviderUserMessage,
            project_tool_result_envelope,
        )
        from tests.test_durable_checkpoint_codec import _manifest, _surface, _tool_call

        tool_call = _tool_call(_surface(_manifest())).model_copy(
            update={"call_id": "post-approval-denied"}
        )
        tool_message = ProviderToolMessage(
            call_id="post-approval-denied",
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        aggregate.commit_progress(
            (
                ProviderUserMessage(content="create it"),
                ProviderAssistantMessage(content=None, tool_calls=(tool_call,)),
                tool_message,
            )
        )

        call = self.db.get(AssistantCapabilityCall, self.call_id)
        self.assertIsNotNone(call)
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.failure_code, "pre_ga_launch_unapproved")
        self.assertIsNone(call.side_effect_started_at)
        self.assertIsNotNone(call.output_artifact_id)
        self.assertEqual(
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=self.call_id)
            .count(),
            0,
        )
        self.assertEqual(
            self.db.query(Entry)
            .filter_by(source_capability_call_id=self.call_id)
            .count(),
            0,
        )
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 0)
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["released"],
        )
        self.assertEqual(
            self.db.query(Tag).filter(Tag.name == blocked_tag_name).count(),
            0,
        )
        from app.assistant.durable.models import AssistantRunCheckpoint

        self.db.refresh(self.run)
        checkpoint = self.db.get(AssistantRunCheckpoint, self.run.current_checkpoint_id)
        self.assertEqual(checkpoint.state_payload["capabilityCalls"][0]["status"], "failed")
        with self.assertRaisesRegex(Exception, "local write call identity"):
            aggregate.execute_local(outcome, request)

    def test_local_recovery_finishes_restored_call_reservation(self) -> None:
        """A resumed local write owns the preserved scheduler reservation."""
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import (
            DurableCapabilityLedgerAggregate,
        )
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.policy.budgets import BudgetLedger, DeterministicBudgetClock
        from app.assistant.policy.contracts import (
            normalize_owner_budget_limits,
            normalize_run_budget_limits,
        )
        from app.assistant.policy.runtime import (
            BudgetLedgerDispatchGuard,
            BudgetLedgerReservationPort,
        )
        from app.assistant.provider_loop.contracts import (
            CapabilityCallReservationItem,
            LedgerPrepareOutcome,
        )
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            ProviderUserMessage,
            project_tool_result_envelope,
        )
        from app.entry.models import Entry
        from tests._db import allowing_test_write_guard
        from tests.test_agent_policy_runtime import _base_manifest
        from tests.test_durable_checkpoint_codec import _manifest, _surface, _tool_call

        provider_call_id = "local-budget-recovery"
        arguments = {
            "title": "restored reservation",
            "content": "body",
            "type_code": "KNOWLEDGE",
            "tags": [],
            "time_mode": "POINT",
            "time_at": "2026-07-18",
        }
        limits = normalize_run_budget_limits(
            operator_limits={
                "max_total_capability_calls": 2,
                "max_parallel_calls": 1,
                "max_same_read_signature": 2,
            }
        )
        owner_version_id = uuid.uuid4()
        clock = DeterministicBudgetClock()
        initial_ledger = BudgetLedger.create(
            limits=limits,
            owner_limits=(
                normalize_owner_budget_limits(
                    owner_kind="main_agent",
                    owner_version_id=owner_version_id,
                    run_limits=limits,
                ),
            ),
            clock=clock,
        )
        reservation = BudgetLedgerReservationPort(ledger=initial_ledger)
        assert reservation.reserve_one(
            CapabilityCallReservationItem(
                call_id=provider_call_id,
                owner_kind="main_agent",
                owner_version_id=owner_version_id,
                domain_key="create_entry",
                side_effect="write_local",
                arguments_digest=sha256_canonical_json(arguments),
                binding_contract_digest=DIGEST_A,
                capability_depth=1,
                agent_depth=1,
            )
        ).allowed
        paused_payload = initial_ledger.serialize()
        restarted_ledger = BudgetLedger.deserialize(paused_payload, clock=clock)
        self.assertEqual(
            [item.state for item in restarted_ledger.snapshot().reservations],
            ["reserved"],
        )

        current_manifest, _resolved_surface = _base_manifest(run_id=self.run.id)
        current_manifest = current_manifest.model_copy(
            update={"manifest_digest": DIGEST_A}
        )
        seeded_call = self.db.get(AssistantCapabilityCall, self.call_id)
        seeded_call.provider_tool_call_id = provider_call_id
        self.db.commit()
        request = SimpleNamespace(
            call=SimpleNamespace(call_id=provider_call_id, arguments=arguments),
            binding=SimpleNamespace(),
            current_manifest=current_manifest,
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
            dispatch_guard=BudgetLedgerDispatchGuard(ledger=restarted_ledger),
        )
        outcome = LedgerPrepareOutcome(
            kind="dispatch_local",
            call_id=self.call_id,
            call_revision=1,
        )
        result = aggregate.execute_local(outcome, request)
        aggregate.commit_result(outcome, result)
        tool_call = _tool_call(_surface(_manifest())).model_copy(
            update={
                "call_id": provider_call_id,
                "domain_key": "create_entry",
                "arguments": arguments,
                "arguments_digest": sha256_canonical_json(arguments),
                "binding_contract_digest": DIGEST_A,
            }
        )
        tool_message = ProviderToolMessage(
            call_id=provider_call_id,
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        aggregate.commit_progress(
            (
                ProviderUserMessage(content="create it"),
                ProviderAssistantMessage(content=None, tool_calls=(tool_call,)),
                tool_message,
            )
        )

        call = self.db.get(AssistantCapabilityCall, self.call_id)
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=self.call_id, status="committed")
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(Entry)
            .filter_by(source_capability_call_id=self.call_id)
            .count(),
            1,
        )
        snapshot = restarted_ledger.snapshot()
        self.assertEqual(snapshot.capability_calls_started, 1)
        self.assertEqual([item.state for item in snapshot.reservations], ["finished"])

    def test_aggregate_local_dispatch_commits_entry_attempt_and_result_together(self) -> None:
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.durable.models import AssistantRunArtifact
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.policy.budgets import BudgetLedger, DeterministicBudgetClock
        from app.assistant.policy.contracts import build_authorization_decision_v2
        from app.assistant.policy.contracts import (
            normalize_owner_budget_limits,
            normalize_run_budget_limits,
        )
        from app.assistant.policy.runtime import (
            BudgetLedgerDispatchGuard,
            BudgetLedgerReservationPort,
        )
        from app.assistant.provider_loop.contracts import CapabilityCallReservationItem
        from tests.test_agent_policy_runtime import _base_manifest
        from app.entry.models import Entry
        from app.lightrag.models import EntryIndexOutbox

        decision = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="awaiting_call_approval",
            reason_code="approval_required",
            principal_digest=DIGEST_A,
            entrypoint_policy_digest=DIGEST_A,
            global_policy_digest=DIGEST_A,
            owner_policy_digest=DIGEST_A,
            allowed_side_effects=("none", "compute", "read", "draft", "write_local"),
            grant_source_digest=DIGEST_B,
            exposure_digest=DIGEST_A,
            effective_policy_digest=DIGEST_A,
            write_release_digest=DIGEST_B,
        )
        resolved_manifest, _surface = _base_manifest()
        resolved_manifest = resolved_manifest.model_copy(
            update={"run_id": self.run.id, "manifest_digest": DIGEST_A}
        )
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=self.run.id),
            call=SimpleNamespace(
                call_id="golden-aggregate-1",
                domain_key="create_entry",
                arguments={
                    "title": "aggregate golden",
                    "content": "body",
                    "type_code": "KNOWLEDGE",
                    "tags": [],
                    "time_mode": "POINT",
                    "time_at": "2026-07-18",
                },
            ),
            current_manifest=resolved_manifest,
            binding=SimpleNamespace(
                ref=SimpleNamespace(
                    binding_contract_digest=DIGEST_A,
                    resolution_digest=DIGEST_B,
                )
            ),
            descriptor=SimpleNamespace(
                behavior=SimpleNamespace(side_effect="write_local"),
                capability_type="tool",
                target_id=None,
                target_version_id=None,
                descriptor_digest=DIGEST_A,
            ),
            authorization=SimpleNamespace(
                owner=SimpleNamespace(
                    owner_kind="main_agent",
                    owner_id=None,
                    owner_version_id=None,
                )
            ),
        )
        budget_limits = normalize_run_budget_limits(
            operator_limits={
                "max_total_capability_calls": 2,
                "max_parallel_calls": 1,
                "max_same_read_signature": 2,
            }
        )
        budget_owner_id = uuid.uuid4()
        budget_ledger = BudgetLedger.create(
            limits=budget_limits,
            owner_limits=(
                normalize_owner_budget_limits(
                    owner_kind="main_agent",
                    owner_version_id=budget_owner_id,
                    run_limits=budget_limits,
                ),
            ),
            clock=DeterministicBudgetClock(),
        )
        assert BudgetLedgerReservationPort(ledger=budget_ledger).reserve_one(
            CapabilityCallReservationItem(
                call_id="golden-aggregate-1",
                owner_kind="main_agent",
                owner_version_id=budget_owner_id,
                domain_key="create_entry",
                side_effect="write_local",
                arguments_digest=sha256_canonical_json(request.call.arguments),
                binding_contract_digest=DIGEST_A,
                capability_depth=1,
                agent_depth=1,
            )
        ).allowed
        budget_guard = BudgetLedgerDispatchGuard(ledger=budget_ledger)
        from tests._db import allowing_test_write_guard

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
            dispatch_guard=budget_guard,
        )

        outcome = aggregate.prepare(request)
        self.assertEqual(outcome.kind, "pause")

        from tests.test_durable_checkpoint_codec import (
            _manifest,
            _surface,
            _tool_call,
            _waiting_continuation,
        )
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            ProviderUserMessage,
            digest_provider_transcript,
            project_tool_result_envelope,
        )

        surface = _surface(_manifest())
        tool_call = _tool_call(surface).model_copy(
            update={"call_id": "golden-aggregate-1"}
        )
        base_messages = (
            ProviderUserMessage(content="create it"),
            ProviderAssistantMessage(content=None, tool_calls=(tool_call,)),
        )
        root_continuation = ContinuationRef(
            continuation_type="capability_call",
            contract_version=1,
            reference_id=outcome.pause_proposal["interruptId"],
            payload_digest=outcome.pause_proposal["proposalDigest"],
        )
        continuation = _waiting_continuation()
        continuation = continuation.model_copy(
            update={
                "execution_scope": continuation.execution_scope.model_copy(
                    update={"run_id": self.run.id}
                ),
                "waiting_call": continuation.waiting_call.model_copy(
                    update={
                        "call_id": "golden-aggregate-1",
                        "capability_continuation": root_continuation,
                    }
                ),
                "transcript_digest": digest_provider_transcript(base_messages),
            }
        )
        aggregate.commit_pause(continuation, base_messages)

        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.durable.models import AssistantRunInterrupt

        call = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id="golden-aggregate-1")
            .one()
        )
        interrupt = self.db.query(AssistantRunInterrupt).filter_by(id=call.interrupt_id).one()
        interrupt.status = "approved"
        call.status = "authorized"
        call.state_revision = int(call.state_revision) + 1
        self.run.status = "running"
        self.run.state_revision = int(self.run.state_revision) + 1
        self.run.lease_owner = "worker-1"
        self.run.lease_generation = 1
        self.db.commit()

        from tests._db import allowing_test_write_guard

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
            dispatch_guard=budget_guard,
        )
        dispatcher = LedgerDispatcher(
            inner=SimpleNamespace(dispatch=lambda *_args, **_kwargs: None),
            aggregate=aggregate,
        )
        result = dispatcher.dispatch(
            request,
            cancellation=SimpleNamespace(is_cancelled=lambda: False),
        )

        tool_message = ProviderToolMessage(
            call_id="golden-aggregate-1",
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        from app.assistant.durable.crash import (
            CrashPoint,
            TransactionRollbackInject,
            armed_crash,
        )

        with armed_crash(CrashPoint.AFTER_CHECKPOINT_INSERT_BEFORE_POINTER_ADVANCE):
            with self.assertRaises(TransactionRollbackInject):
                aggregate.commit_progress(
                    (
                        *base_messages,
                        tool_message,
                    )
                )

        # The local business mutation and every ledger/result child share the
        # Run CAS transaction; a crash at the final pointer gap leaves zero.
        rolled_back_call = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id="golden-aggregate-1")
            .one()
        )
        self.assertEqual(rolled_back_call.status, "authorized")
        self.assertEqual(rolled_back_call.attempt_count, 0)
        self.assertEqual(
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=rolled_back_call.id)
            .count(),
            0,
        )
        self.assertEqual(
            self.db.query(Entry).filter_by(source_capability_call_id=self.call_id).count(),
            0,
        )
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 0)
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["reserved"],
        )

        # Replay the whole logical invocation after rollback; deterministic
        # call identity converges and the complete set commits once.
        from tests._db import allowing_test_write_guard

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
            dispatch_guard=budget_guard,
        )
        dispatcher = LedgerDispatcher(
            inner=SimpleNamespace(dispatch=lambda *_args, **_kwargs: None),
            aggregate=aggregate,
        )
        result = dispatcher.dispatch(
            request,
            cancellation=SimpleNamespace(is_cancelled=lambda: False),
        )
        tool_message = ProviderToolMessage(
            call_id="golden-aggregate-1",
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        aggregate.commit_progress(
            (
                *base_messages,
                tool_message,
            )
        )

        call = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id="golden-aggregate-1")
            .one()
        )
        self.assertEqual(call.status, "succeeded")
        self.assertIsNotNone(call.side_effect_started_at)
        self.assertEqual(
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=call.id, status="committed")
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(AssistantRunArtifact)
            .filter_by(id=call.output_artifact_id, kind="capability_call_result")
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(Entry).filter_by(source_capability_call_id=call.id).count(),
            1,
        )
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 1)
        self.assertEqual(budget_ledger.snapshot().capability_calls_started, 1)
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["finished"],
        )
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunProviderMessage,
        )

        self.db.refresh(self.run)
        checkpoint = (
            self.db.query(AssistantRunCheckpoint)
            .filter_by(id=self.run.current_checkpoint_id, schema_version=3)
            .one()
        )
        self.assertEqual(checkpoint.schema_version, 3)
        self.assertEqual(checkpoint.state_payload["capabilityCalls"][0]["status"], "succeeded")
        self.assertEqual(self.db.query(AssistantRunProviderMessage).count(), 3)
        self.assertEqual(self.run.state_revision, 4)


if __name__ == "__main__":
    unittest.main()
