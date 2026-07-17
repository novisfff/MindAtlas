"""Plan 08 Task 6: local transactional create_entry + UoW boundary."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

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
        from app.assistant.models import AssistantChatRun, Conversation
        from app.assistant.durable.models import (
            AssistantRunArtifact,
            AssistantRunManifestRevision,
        )
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.durable.repository import LeaseToken
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
        self.run = AssistantChatRun(
            conversation_id=conv.id,
            status="running",
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision="b1",
            capability_ledger_mode="enforced",
            state_revision=1,
            lease_owner="worker-1",
            lease_generation=1,
            memory_commit_status="pending",
        )
        self.db.add(self.run)
        self.db.commit()
        self.db.refresh(self.run)
        self.manifest = AssistantRunManifestRevision(
            run_id=self.run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        self.db.add(self.manifest)
        self.db.flush()
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


if __name__ == "__main__":
    unittest.main()
