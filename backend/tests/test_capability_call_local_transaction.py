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

    def test_aggregate_local_dispatch_commits_entry_attempt_and_result_together(self) -> None:
        from types import SimpleNamespace

        from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.durable.models import AssistantRunArtifact
        from app.assistant.policy.contracts import build_authorization_decision_v2
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
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            lease=self.lease,
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

        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            lease=self.lease,
        )
        outcome = aggregate.prepare(request)
        self.assertEqual(outcome.kind, "dispatch_local")
        result = aggregate.execute_local(outcome, request)
        aggregate.commit_result(outcome, result)

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
            self.db.query(Entry).filter_by(source_capability_call_id=outcome.call_id).count(),
            0,
        )
        self.assertEqual(self.db.query(EntryIndexOutbox).count(), 0)

        # Replay the whole logical invocation after rollback; deterministic
        # call identity converges and the complete set commits once.
        aggregate = DurableCapabilityLedgerAggregate(
            db=self.db,
            authorization_factory=SimpleNamespace(
                decision_for_call=lambda **_kwargs: decision
            ),
            idempotency_secret="s" * 32,
            lease=self.lease,
        )
        outcome = aggregate.prepare(request)
        result = aggregate.execute_local(outcome, request)
        aggregate.commit_result(outcome, result)
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
