"""Plan 08 Task 6: local transactional create_entry + UoW boundary."""

from __future__ import annotations

import unittest
import uuid
import ast
import importlib
from pathlib import Path
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

    def test_local_write_ast_uses_only_create_in_uow_for_entry_service(self) -> None:
        from app.assistant.capability_calls import local_write

        tree = ast.parse(Path(local_write.__file__).read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        attribute_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        self.assertNotIn("app.assistant.tools.entry_tools", imported_modules)
        self.assertNotIn("create", attribute_calls)
        self.assertNotIn("commit", attribute_calls)
        self.assertIn("create_in_uow", attribute_calls)


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
            create_initial_obligation_ledger_state,
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
        obligation_ledger = create_initial_obligation_ledger_state()
        self.obligation = AssistantRunObligationRevision(
            run_id=self.run.id,
            revision=1,
            obligation_digest=obligation_ledger.ledger_digest,
            payload=obligation_ledger.model_dump(mode="json", by_alias=True),
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

    def _approval_boundary_fixture(self, provider_call_id: str):
        """Build a real aggregate pause request with one budget reservation."""
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import (
            DurableCapabilityLedgerAggregate,
        )
        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.policy.budgets import BudgetLedger, DeterministicBudgetClock
        from app.assistant.policy.contracts import (
            build_authorization_decision_v2,
            normalize_owner_budget_limits,
            normalize_run_budget_limits,
        )
        from app.assistant.policy.runtime import (
            BudgetLedgerDispatchGuard,
            BudgetLedgerReservationPort,
        )
        from app.assistant.provider_loop.contracts import CapabilityCallReservationItem
        from tests._db import allowing_test_write_guard
        from tests.test_agent_policy_runtime import _base_manifest

        arguments = {
            "title": "approval boundary",
            "content": "body",
            "type_code": "KNOWLEDGE",
            "tags": [],
            "time_mode": "POINT",
            "time_at": "2026-07-18",
        }
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
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=self.run.id),
            call=SimpleNamespace(
                call_id=provider_call_id,
                domain_key="create_entry",
                arguments=arguments,
            ),
            current_manifest=resolved_manifest.model_copy(
                update={"run_id": self.run.id, "manifest_digest": DIGEST_A}
            ),
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
        limits = normalize_run_budget_limits(
            operator_limits={
                "max_total_capability_calls": 2,
                "max_parallel_calls": 1,
                "max_same_read_signature": 2,
            }
        )
        owner_version_id = uuid.uuid4()
        ledger = BudgetLedger.create(
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
        self.assertTrue(
            BudgetLedgerReservationPort(ledger=ledger)
            .reserve_one(
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
            )
            .allowed
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
            dispatch_guard=BudgetLedgerDispatchGuard(ledger=ledger),
        )
        return aggregate, request

    def test_aggregate_proposal_faults_rollback_before_and_after_materialization(self) -> None:
        from app.assistant.capability_calls.faults import (
            CapabilityFaultPort,
            CapabilityInjectedFault,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt
        from app.entry.models import Entry

        for point in ("before_proposal", "after_proposal"):
            with self.subTest(point=point):
                provider_call_id = f"proposal-fault-{point}"
                aggregate, request = self._approval_boundary_fixture(provider_call_id)
                aggregate.fault_port = CapabilityFaultPort.once(point)
                if point == "before_proposal":
                    with self.assertRaises(CapabilityInjectedFault):
                        aggregate.prepare(request)
                else:
                    outcome = aggregate.prepare(request)
                    from app.assistant.capabilities.contracts import ContinuationRef
                    from app.assistant.provider_loop.messages import (
                        ProviderAssistantMessage,
                        ProviderUserMessage,
                        digest_provider_transcript,
                    )
                    from tests.test_durable_checkpoint_codec import (
                        _manifest,
                        _surface,
                        _tool_call,
                        _waiting_continuation,
                    )

                    tool_call = _tool_call(_surface(_manifest())).model_copy(
                        update={"call_id": provider_call_id}
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
                    waiting_continuation = _waiting_continuation()
                    continuation = waiting_continuation.model_copy(
                        update={
                            "execution_scope": waiting_continuation.execution_scope.model_copy(
                                update={"run_id": self.run.id}
                            ),
                            "waiting_call": waiting_continuation.waiting_call.model_copy(
                                update={
                                    "call_id": provider_call_id,
                                    "capability_continuation": root_continuation,
                                }
                            ),
                            "transcript_digest": digest_provider_transcript(base_messages),
                        }
                    )
                    with self.assertRaises(CapabilityInjectedFault):
                        aggregate.commit_pause(continuation, base_messages)
                self.assertEqual(
                    self.db.query(AssistantCapabilityCall)
                    .filter_by(provider_tool_call_id=provider_call_id)
                    .count(),
                    0,
                )
                self.assertEqual(
                    self.db.query(AssistantCapabilityCallAttempt).count(), 0
                )
                self.assertEqual(self.db.query(Entry).count(), 0)

    def test_after_checkpoint_observation_fault_recovers_committed_local_bundle(self) -> None:
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher
        from app.assistant.capability_calls.faults import (
            CapabilityFaultPort,
            CapabilityInjectedFault,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            ProviderUserMessage,
            digest_provider_transcript,
            project_tool_result_envelope,
        )
        from app.entry.models import Entry
        from tests.test_durable_checkpoint_codec import (
            _manifest,
            _surface,
            _tool_call,
            _waiting_continuation,
        )

        provider_call_id = "checkpoint-observation-fault"
        aggregate, request = self._approval_boundary_fixture(provider_call_id)
        outcome = aggregate.prepare(request)
        self.assertEqual(outcome.kind, "pause")
        tool_call = _tool_call(_surface(_manifest())).model_copy(
            update={"call_id": provider_call_id}
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
        waiting = _waiting_continuation()
        continuation = waiting.model_copy(
            update={
                "execution_scope": waiting.execution_scope.model_copy(
                    update={"run_id": self.run.id}
                ),
                "waiting_call": waiting.waiting_call.model_copy(
                    update={
                        "call_id": provider_call_id,
                        "capability_continuation": root_continuation,
                    }
                ),
                "transcript_digest": digest_provider_transcript(base_messages),
            }
        )
        aggregate.commit_pause(continuation, base_messages)
        call = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id=provider_call_id)
            .one()
        )
        interrupt = self.db.get(AssistantRunInterrupt, call.interrupt_id)
        self.assertIsNotNone(interrupt)
        interrupt.status = "approved"
        call.status = "authorized"
        call.state_revision = int(call.state_revision) + 1
        self.run.status = "running"
        self.run.state_revision = int(self.run.state_revision) + 1
        self.run.lease_owner = "worker-1"
        self.run.lease_generation = 1
        self.db.commit()

        aggregate.fault_port = CapabilityFaultPort.once(
            "after_commit_before_checkpoint_observation"
        )
        dispatcher = LedgerDispatcher(
            inner=type("Inner", (), {"dispatch": lambda *_args, **_kwargs: None})(),
            aggregate=aggregate,
        )
        result = dispatcher.dispatch(
            request,
            cancellation=type("Cancellation", (), {"is_cancelled": lambda _self: False})(),
        )
        tool_message = ProviderToolMessage(
            call_id=provider_call_id,
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        with self.assertRaises(CapabilityInjectedFault):
            aggregate.commit_progress((*base_messages, tool_message))

        self.db.refresh(call)
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=call.id, status="committed")
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(Entry).filter_by(source_capability_call_id=call.id).count(),
            1,
        )
        self.assertIsNotNone(aggregate.last_local_settlement)

    def test_mismatched_search_call_has_no_direct_local_settlement_entrypoint(self) -> None:
        """A mismatched call can only reach the aggregate-owned execution path."""
        import app.assistant.capability_calls as capability_calls
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.entry.models import Entry

        call = self.db.get(AssistantCapabilityCall, self.call_id)
        assert call is not None
        call.domain_key = "search_entries"
        call.input_digest = DIGEST_B
        self.db.flush()

        self.assertEqual(call.domain_key, "search_entries")
        self.assertNotEqual(call.input_digest, DIGEST_A)
        self.assertEqual(self.db.query(Entry).count(), 0)
        self.assertFalse(
            hasattr(capability_calls, "create_entry_local_transactional")
        )
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module(
                "app.assistant.capability_calls.local_settlement"
            )
        self.assertEqual(self.db.query(Entry).count(), 0)

    def test_recovery_requires_the_complete_durable_bundle(self) -> None:
        """A source Entry alone must not be treated as an acknowledged commit."""
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import (
            DurableCapabilityLedgerAggregate,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.capability_calls.local_write import (
            LocalCommitRecovery,
        )
        from app.entry.models import Entry
        from app.entry.schemas import EntryRequest
        from app.entry.service import EntryService
        from app.entry.models import TimeMode
        from tests._db import allowing_test_write_guard

        call = self.db.get(AssistantCapabilityCall, self.call_id)
        self.assertIsNotNone(call)
        entry = EntryService(self.db).create_in_uow(
            EntryRequest(
                title="partial durable entry",
                summary="partial",
                content="partial",
                type_id=self.etype.id,
                time_mode=TimeMode.POINT,
                time_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            source_capability_call_id=call.id,
        )
        # Simulate a boundary-visible Entry with the rest of the settlement
        # bundle still missing.  The fresh-session classifier must fail closed.
        call.status = "needs_reconciliation"
        call.output_artifact_id = None
        call.side_effect_started_at = None
        self.db.commit()

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(self.db),
            lease=self.lease,
        )
        recovery = aggregate.recover_local_commit(call.id)
        self.assertIsInstance(recovery, LocalCommitRecovery)
        self.assertEqual(recovery.kind, "unknown")
        self.assertEqual(recovery.failure_code, "local_commit_outcome_unknown")
        self.assertIsNone(recovery.attempt_id)
        self.assertIsNone(recovery.output_artifact_id)
        self.assertEqual(
            self.db.query(Entry).filter_by(source_capability_call_id=call.id).count(),
            1,
        )

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
        budget_guard = BudgetLedgerDispatchGuard(ledger=budget_ledger)
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=guard,
            lease=self.lease,
            dispatch_guard=budget_guard,
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
        provider_messages = (
            ProviderUserMessage(content="create it"),
            ProviderAssistantMessage(content=None, tool_calls=(tool_call,)),
            tool_message,
        )
        from app.assistant.durable.crash import (
            CrashPoint,
            TransactionRollbackInject,
            armed_crash,
        )

        with armed_crash(CrashPoint.AFTER_CHECKPOINT_INSERT_BEFORE_POINTER_ADVANCE):
            with self.assertRaises(TransactionRollbackInject):
                aggregate.commit_progress(provider_messages)

        rolled_back = self.db.get(AssistantCapabilityCall, self.call_id)
        self.assertEqual(rolled_back.status, "authorized")
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["reserved"],
        )

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=guard,
            lease=self.lease,
            dispatch_guard=budget_guard,
        )
        result = aggregate.execute_local(outcome, request)
        aggregate.commit_result(outcome, result)
        aggregate.commit_progress(provider_messages)

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

    def test_dispatcher_commit_result_failure_restores_denied_reservation(self) -> None:
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import (
            DurableCapabilityLedgerAggregate,
        )
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher
        from app.assistant.capability_calls.models import AssistantCapabilityCall
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
        from tests._db import allowing_test_write_guard
        from tests.test_agent_policy_runtime import _base_manifest
        from tests.test_durable_checkpoint_codec import _manifest, _surface, _tool_call

        provider_call_id = "post-approval-denied-dispatcher"
        arguments = {
            "title": "must not exist",
            "content": "blocked",
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
        guard = allowing_test_write_guard(self.db)
        guard.evaluate_post_approval_locked = lambda **_kwargs: SimpleNamespace(
            allowed=False,
            reason_code="pre_ga_launch_unapproved",
        )
        budget_guard = BudgetLedgerDispatchGuard(ledger=budget_ledger)
        seeded_call = self.db.get(AssistantCapabilityCall, self.call_id)
        seeded_call.provider_tool_call_id = provider_call_id
        self.db.commit()
        outcome = LedgerPrepareOutcome(
            kind="dispatch_local",
            call_id=self.call_id,
            call_revision=1,
        )
        current_manifest, _resolved_surface = _base_manifest(run_id=self.run.id)
        request = SimpleNamespace(
            call=SimpleNamespace(call_id=provider_call_id, arguments=arguments),
            binding=SimpleNamespace(),
            current_manifest=current_manifest,
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=guard,
            lease=self.lease,
            dispatch_guard=budget_guard,
        )
        aggregate.prepare = lambda _request: outcome
        aggregate.commit_result = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("commit result boundary failed")
        )
        dispatcher = LedgerDispatcher(
            inner=SimpleNamespace(dispatch=lambda *_args, **_kwargs: None),
            aggregate=aggregate,
        )

        with self.assertRaisesRegex(RuntimeError, "commit result boundary failed"):
            dispatcher.dispatch(
                request,
                cancellation=SimpleNamespace(is_cancelled=lambda: False),
            )

        rolled_back = self.db.get(AssistantCapabilityCall, self.call_id)
        self.assertEqual(rolled_back.status, "authorized")
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["reserved"],
        )

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(),
            idempotency_secret="s" * 32,
            write_guard=guard,
            lease=self.lease,
            dispatch_guard=budget_guard,
        )
        result = aggregate.execute_local(outcome, request)
        aggregate.commit_result(outcome, result)
        tool_call = _tool_call(_surface(_manifest())).model_copy(
            update={"call_id": provider_call_id}
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
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.failure_code, "pre_ga_launch_unapproved")
        self.assertEqual(
            [item.state for item in budget_ledger.snapshot().reservations],
            ["released"],
        )

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
        committed = aggregate.recover_local_commit(self.call_id)
        self.assertEqual(committed.kind, "committed")
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
        )
        from app.assistant.durable.codec import checkpoint_state_digest, decode_checkpoint
        from app.assistant.policy.obligations import (
            ObligationLedgerState,
            compute_obligation_ledger_digest,
        )

        checkpoint = self.db.get(AssistantRunCheckpoint, self.run.current_checkpoint_id)
        current_obligation = self.db.get(
            AssistantRunObligationRevision,
            self.run.current_obligation_revision_id,
        )
        self.assertIsNotNone(checkpoint)
        self.assertIsNotNone(current_obligation)
        current_ledger = ObligationLedgerState.model_validate(current_obligation.payload)
        drifted_revision = int(current_ledger.revision) + 1
        drifted_digest = compute_obligation_ledger_digest(
            revision=drifted_revision,
            obligations=current_ledger.obligations,
            evidence_edges=current_ledger.evidence_edges,
            followup_rounds_started=current_ledger.followup_rounds_started,
        )
        drifted_ledger = current_ledger.model_copy(
            update={"revision": drifted_revision, "ledger_digest": drifted_digest}
        )
        drifted_obligation = AssistantRunObligationRevision(
            run_id=self.run.id,
            revision=int(current_obligation.revision) + 1,
            parent_revision_id=current_obligation.id,
            parent_digest=current_obligation.obligation_digest,
            obligation_digest=drifted_ledger.ledger_digest,
            payload=drifted_ledger.model_dump(mode="json", by_alias=True),
        )
        self.db.add(drifted_obligation)
        self.db.flush()
        self.run.current_obligation_revision_id = drifted_obligation.id
        self.db.commit()
        drifted = aggregate.recover_local_commit(self.call_id)
        self.assertEqual(drifted.kind, "unknown")
        self.run.current_obligation_revision_id = checkpoint.obligation_revision_id
        self.db.commit()
        self.db.refresh(current_obligation)
        original_obligation_payload = dict(current_obligation.payload)
        tampered_obligation_payload = dict(original_obligation_payload)
        tampered_obligation_payload["followupRoundsStarted"] = 1
        current_obligation.payload = tampered_obligation_payload
        self.db.commit()
        tampered_obligation = aggregate.recover_local_commit(self.call_id)
        self.assertEqual(tampered_obligation.kind, "unknown")
        current_obligation.payload = original_obligation_payload
        self.db.commit()
        original_checkpoint_payload = dict(checkpoint.state_payload)
        tampered_checkpoint_payload = dict(original_checkpoint_payload)
        tampered_checkpoint_payload["obligationRevisionId"] = str(uuid.uuid4())
        checkpoint.state_payload = tampered_checkpoint_payload
        checkpoint.state_digest = checkpoint_state_digest(
            decode_checkpoint(tampered_checkpoint_payload)
        )
        self.db.commit()
        tampered_checkpoint = aggregate.recover_local_commit(self.call_id)
        self.assertEqual(tampered_checkpoint.kind, "unknown")
        checkpoint.state_payload = original_checkpoint_payload
        checkpoint.state_digest = checkpoint_state_digest(
            decode_checkpoint(original_checkpoint_payload)
        )
        self.db.commit()
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=self.call_id, status="committed")
            .one()
        )
        attempt.response_digest = DIGEST_B
        self.db.commit()
        corrupt = aggregate.recover_local_commit(self.call_id)
        self.assertEqual(corrupt.kind, "unknown")
        self.assertEqual(corrupt.failure_code, "local_commit_outcome_unknown")

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
        from app.assistant.capability_calls.faults import (
            CapabilityFaultPort,
            CapabilityInjectedFault,
        )

        aggregate.fault_port = CapabilityFaultPort.once(
            "after_entry_stage_before_commit"
        )
        with self.assertRaises(CapabilityInjectedFault):
            dispatcher.dispatch(
                request,
                cancellation=SimpleNamespace(is_cancelled=lambda: False),
            )
        self.assertEqual(
            self.db.query(Entry)
            .filter(Entry.source_capability_call_id == self.call_id)
            .count(),
            0,
        )
        self.db.refresh(call)
        self.assertEqual(call.status, "authorized")

        # Retry through a fresh aggregate after the proven rollback.
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
        self.assertNotIn("title", result.capability_result.structured_output or {})
        self.assertNotIn("content", result.capability_result.structured_output or {})
        self.assertNotIn("aggregate golden", result.capability_result.user_text or "")
        tool_message = ProviderToolMessage(
            call_id="golden-aggregate-1",
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="create_entry",
                result=result.capability_result,
            ),
        )
        # A local settlement is one ledger-owned transaction.  The reader
        # session must see the old approval-only state until the final Run CAS
        # commits, then observe the complete Call/Attempt/Entry/Artifact/
        # Checkpoint bundle together.
        from sqlalchemy import event
        from sqlalchemy.orm import sessionmaker

        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
        )

        settlement_call_id = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id="golden-aggregate-1")
            .one()
            .id
        )
        reader = sessionmaker(bind=self.db.get_bind(), future=True)()
        before_commit_snapshots = []
        commit_events = []

        def _snapshot():
            reader.rollback()
            reader.expire_all()
            visible_run = reader.get(type(self.run), self.run.id)
            visible_call = reader.get(AssistantCapabilityCall, settlement_call_id)
            visible_attempts = (
                reader.query(AssistantCapabilityCallAttempt)
                .filter(AssistantCapabilityCallAttempt.call_id == settlement_call_id)
                .all()
            )
            visible_entry_count = (
                reader.query(Entry)
                .filter(Entry.source_capability_call_id == settlement_call_id)
                .count()
            )
            visible_result_count = (
                reader.query(AssistantRunArtifact)
                .filter(
                    AssistantRunArtifact.run_id == self.run.id,
                    AssistantRunArtifact.kind == "capability_call_result",
                )
                .count()
            )
            visible_checkpoint = (
                reader.get(AssistantRunCheckpoint, visible_run.current_checkpoint_id)
                if visible_run is not None and visible_run.current_checkpoint_id is not None
                else None
            )
            visible_obligation = (
                reader.get(
                    AssistantRunObligationRevision,
                    visible_run.current_obligation_revision_id,
                )
                if visible_run is not None
                and visible_run.current_obligation_revision_id is not None
                else None
            )
            call_state = None
            if visible_checkpoint is not None:
                call_state = next(
                    (
                        item
                        for item in visible_checkpoint.state_payload.get("capabilityCalls", [])
                        if str(item.get("callId")) == str(settlement_call_id)
                    ),
                    None,
                )
            obligation_statuses = tuple(
                sorted(
                    str(item.get("status"))
                    for item in (visible_obligation.payload or {}).get("obligations", [])
                    if isinstance(item, dict)
                )
            ) if visible_obligation is not None else ()
            return {
                "call_status": None if visible_call is None else str(visible_call.status),
                "attempt_count": len(visible_attempts),
                "attempt_statuses": tuple(sorted(str(item.status) for item in visible_attempts)),
                "entry_count": visible_entry_count,
                "result_artifact_count": visible_result_count,
                "checkpoint_schema": None if visible_checkpoint is None else int(visible_checkpoint.schema_version),
                "checkpoint_call_status": None if call_state is None else str(call_state.get("status")),
                "obligation_statuses": obligation_statuses,
            }

        def _before_commit(_session):
            if _session is not self.db or _session.in_nested_transaction():
                return
            commit_events.append("before")
            before_commit_snapshots.append(_snapshot())

        def _after_commit(_session):
            if _session is not self.db or _session.in_nested_transaction():
                return
            commit_events.append("after")

        event.listen(self.db, "before_commit", _before_commit)
        event.listen(self.db, "after_commit", _after_commit)
        try:
            from app.assistant.capability_calls.faults import (
                CapabilityFaultPort,
                CapabilityInjectedFault,
            )

            # The fault is after the Run CAS commit.  The caller observes an
            # exception, but fresh-session recovery must classify the durable
            # Entry as committed and preserve the finished budget reservation.
            aggregate.fault_port = CapabilityFaultPort.once("after_commit_before_ack")
            with self.assertRaises(CapabilityInjectedFault):
                aggregate.commit_progress(
                    (
                        *base_messages,
                        tool_message,
                    )
                )
            after_commit_snapshot = _snapshot()
        finally:
            event.remove(self.db, "before_commit", _before_commit)
            event.remove(self.db, "after_commit", _after_commit)
            reader.close()

        self.assertEqual(commit_events, ["before", "after"])
        self.assertEqual(len(before_commit_snapshots), 1)
        self.assertEqual(before_commit_snapshots[0]["call_status"], "authorized")
        self.assertEqual(before_commit_snapshots[0]["attempt_count"], 0)
        self.assertEqual(before_commit_snapshots[0]["entry_count"], 0)
        self.assertEqual(before_commit_snapshots[0]["result_artifact_count"], 0)
        self.assertNotEqual(before_commit_snapshots[0]["checkpoint_call_status"], "succeeded")
        self.assertNotIn("satisfied", before_commit_snapshots[0]["obligation_statuses"])

        self.assertEqual(after_commit_snapshot["call_status"], "succeeded")
        self.assertEqual(after_commit_snapshot["attempt_count"], 1)
        self.assertEqual(after_commit_snapshot["attempt_statuses"], ("committed",))
        self.assertEqual(after_commit_snapshot["entry_count"], 1)
        self.assertEqual(after_commit_snapshot["result_artifact_count"], 1)
        self.assertEqual(after_commit_snapshot["checkpoint_schema"], 3)
        self.assertEqual(after_commit_snapshot["checkpoint_call_status"], "succeeded")
        call = (
            self.db.query(AssistantCapabilityCall)
            .filter_by(provider_tool_call_id="golden-aggregate-1")
            .one()
        )
        self.assertEqual(call.status, "succeeded")
        self.assertIsNotNone(call.side_effect_started_at)
        settlement = aggregate.last_local_settlement
        self.assertIsNotNone(settlement)
        self.assertEqual(settlement.call_id, call.id)
        self.assertEqual(settlement.output_artifact_id, call.output_artifact_id)
        self.assertEqual(settlement.resulting_run_revision, self.run.state_revision)
        committed_attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=call.id, status="committed")
            .one()
        )
        self.assertTrue(committed_attempt.side_effect_started)
        self.assertEqual(
            call.side_effect_started_at,
            committed_attempt.side_effect_started_at,
        )
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
        from app.assistant.durable.models import AssistantRunProviderMessage

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

        from app.assistant.capability_calls.repository import CapabilityCallConflict

        conflicting_request = SimpleNamespace(**vars(request))
        conflicting_request.call = SimpleNamespace(
            call_id=request.call.call_id,
            domain_key=request.call.domain_key,
            arguments={**request.call.arguments, "content": "different body"},
        )
        with self.assertRaises(CapabilityCallConflict) as conflict:
            aggregate.prepare(conflicting_request)
        self.assertEqual(conflict.exception.code, "idempotency_conflict")
        self.assertEqual(
            self.db.query(Entry)
            .filter(Entry.source_capability_call_id == call.id)
            .count(),
            1,
        )


if __name__ == "__main__":
    unittest.main()
