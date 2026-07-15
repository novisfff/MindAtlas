"""ORM contract tests for Plan 06 durable agent run foundation (Task 1).

SQLite-backed via tests._db. PostgreSQL partial indexes / triggers are proven
in test_durable_run_migration_postgres.py.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DURABLE_CHILD_TABLES = (
    "assistant_worker_registration",
    "assistant_run_manifest_revision",
    "assistant_run_provider_message",
    "assistant_run_policy_revision",
    "assistant_run_budget_revision",
    "assistant_run_obligation_revision",
    "assistant_run_checkpoint",
    "assistant_run_artifact",
    "assistant_run_artifact_gc",
)

ACTIVE_STATUSES = (
    "queued",
    "running",
    "recovering",
    "waiting_approval",
    "waiting_input",
    "cancelling",
    "needs_reconciliation",
)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")

PROVIDER_ROLES = (
    "system",
    "runtime_instruction",
    "runtime_context",
    "runtime_completion",
    "user",
    "assistant",
    "tool",
)

PROTECTED_ROLES = (
    "runtime_instruction",
    "runtime_context",
    "runtime_completion",
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


class DurableRunModelContractTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session  # noqa: E402

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    # Table registration
    # ------------------------------------------------------------------

    def test_durable_tables_registered(self) -> None:
        from app.database import Base  # noqa: E402
        import app.assistant.models  # noqa: F401,E402
        import app.assistant.durable.models  # noqa: F401,E402

        for name in DURABLE_CHILD_TABLES:
            self.assertIn(name, Base.metadata.tables, msg=f"missing table {name}")

    def test_assistant_chat_run_durable_columns_present(self) -> None:
        from app.assistant.models import AssistantChatRun  # noqa: E402

        cols = {c.name for c in AssistantChatRun.__table__.columns}
        expected = {
            "runtime_kind",
            "runtime_contract_version",
            "required_app_build_revision",
            "state_revision",
            "current_manifest_revision_id",
            "current_policy_revision_id",
            "current_checkpoint_id",
            "current_budget_revision_id",
            "current_obligation_revision_id",
            "lease_owner",
            "lease_generation",
            "lease_expires_at",
            "heartbeat_at",
            "next_attempt_at",
            "recovery_count",
            "deadline_at",
            "failure_code",
            "memory_commit_status",
            "memory_committed_at",
        }
        self.assertTrue(expected.issubset(cols), msg=f"missing columns: {expected - cols}")

    def test_assistant_chat_run_event_additive_columns(self) -> None:
        from app.assistant.models import AssistantChatRunEvent  # noqa: E402

        cols = {c.name for c in AssistantChatRunEvent.__table__.columns}
        for name in ("event_key", "payload_version", "visibility"):
            self.assertIn(name, cols)

    def test_l1_l2_memory_columns(self) -> None:
        from app.assistant.models import (  # noqa: E402
            AssistantConversationL1Memory,
            AssistantConversationSkillL2Memory,
        )

        l1_cols = {c.name for c in AssistantConversationL1Memory.__table__.columns}
        self.assertIn("last_applied_run_id", l1_cols)

        l2_cols = {c.name for c in AssistantConversationSkillL2Memory.__table__.columns}
        for name in (
            "skill_package_id",
            "memory_namespace",
            "facts_v2",
            "last_applied_run_id",
        ):
            self.assertIn(name, l2_cols)

    # ------------------------------------------------------------------
    # Checks / defaults on Run
    # ------------------------------------------------------------------

    def test_run_defaults_are_legacy_compatible(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()

        run = AssistantChatRun(conversation_id=conv.id, status="queued")
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        self.assertEqual(run.runtime_kind, "legacy")
        self.assertEqual(run.state_revision, 0)
        self.assertEqual(run.lease_generation, 0)
        self.assertEqual(run.recovery_count, 0)
        self.assertEqual(run.memory_commit_status, "not_applicable")
        self.assertIsNone(run.runtime_contract_version)
        self.assertIsNone(run.current_manifest_revision_id)
        self.assertIsNone(run.lease_owner)

    def test_run_status_accepts_main_agent_statuses(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        # One conversation per status so the active partial unique index is not hit.
        for status in (*ACTIVE_STATUSES, *TERMINAL_STATUSES):
            conv = Conversation(title=f"t-{status}")
            self.db.add(conv)
            self.db.flush()
            needs_main = status in (
                "recovering",
                "waiting_input",
                "needs_reconciliation",
            )
            run = AssistantChatRun(
                conversation_id=conv.id,
                status=status,
                runtime_kind="main_agent" if needs_main else "legacy",
                runtime_contract_version=1 if needs_main else None,
                required_app_build_revision="build-1" if needs_main else None,
            )
            self.db.add(run)
            self.db.flush()
        self.db.commit()

    def test_run_status_rejects_unknown(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        self.db.add(
            AssistantChatRun(conversation_id=conv.id, status="not_a_status")
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_run_runtime_kind_check(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        self.db.add(
            AssistantChatRun(
                conversation_id=conv.id,
                status="queued",
                runtime_kind="other",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_run_memory_commit_status_check(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        self.db.add(
            AssistantChatRun(
                conversation_id=conv.id,
                status="queued",
                memory_commit_status="bogus",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_run_state_revision_and_lease_generation_non_negative(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        self.db.add(
            AssistantChatRun(
                conversation_id=conv.id,
                status="queued",
                state_revision=-1,
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        self.db.add(
            AssistantChatRun(
                conversation_id=conv.id,
                status="queued",
                lease_generation=-1,
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_run_active_status_partial_unique_index_defined(self) -> None:
        from app.assistant.models import AssistantChatRun  # noqa: E402

        indexes = {idx.name: idx for idx in AssistantChatRun.__table__.indexes}
        self.assertIn("uq_assistant_chat_run_active_conversation", indexes)
        idx = indexes["uq_assistant_chat_run_active_conversation"]
        self.assertTrue(idx.unique)
        # SQLAlchemy stores postgresql_where / sqlite_where on dialect_options.
        # Accessing the clause object with `or`/`bool` raises TypeError; use `is not None`.
        pg_opts = idx.dialect_options.get("postgresql", {})
        sqlite_opts = idx.dialect_options.get("sqlite", {})
        pg_where = pg_opts.get("where")
        sqlite_where = sqlite_opts.get("where")
        self.assertTrue(
            pg_where is not None or sqlite_where is not None,
            msg="active uniqueness index must declare a partial WHERE clause",
        )

    # ------------------------------------------------------------------
    # Event additive columns
    # ------------------------------------------------------------------

    def test_event_defaults_and_key_partial_unique(self) -> None:
        from app.assistant.models import (  # noqa: E402
            AssistantChatRun,
            AssistantChatRunEvent,
            Conversation,
        )

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(conversation_id=conv.id, status="queued")
        self.db.add(run)
        self.db.flush()

        e1 = AssistantChatRunEvent(
            run_id=run.id,
            seq=1,
            event_name="run.started",
            payload={},
        )
        self.db.add(e1)
        self.db.commit()
        self.db.refresh(e1)
        self.assertEqual(e1.payload_version, 1)
        self.assertEqual(e1.visibility, "public")
        self.assertIsNone(e1.event_key)

        e2 = AssistantChatRunEvent(
            run_id=run.id,
            seq=2,
            event_name="unit.prepared",
            payload={"x": 1},
            event_key="unit.prepared:lu-1",
            visibility="internal",
        )
        self.db.add(e2)
        self.db.commit()

        e3 = AssistantChatRunEvent(
            run_id=run.id,
            seq=3,
            event_name="unit.prepared",
            payload={"x": 2},
            event_key="unit.prepared:lu-1",
        )
        self.db.add(e3)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Null event_key may repeat (legacy compatibility).
        e4 = AssistantChatRunEvent(
            run_id=run.id,
            seq=3,
            event_name="legacy.tick",
            payload={},
            event_key=None,
        )
        e5 = AssistantChatRunEvent(
            run_id=run.id,
            seq=4,
            event_name="legacy.tick2",
            payload={},
            event_key=None,
        )
        self.db.add_all([e4, e5])
        self.db.commit()

        indexes = {
            idx.name: idx for idx in AssistantChatRunEvent.__table__.indexes
        }
        self.assertIn("uq_assistant_chat_run_event_key", indexes)

    def test_event_visibility_check(self) -> None:
        from app.assistant.models import (  # noqa: E402
            AssistantChatRun,
            AssistantChatRunEvent,
            Conversation,
        )

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(conversation_id=conv.id, status="queued")
        self.db.add(run)
        self.db.flush()
        self.db.add(
            AssistantChatRunEvent(
                run_id=run.id,
                seq=1,
                event_name="x",
                payload={},
                visibility="secret",
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    # ------------------------------------------------------------------
    # Worker registration
    # ------------------------------------------------------------------

    def test_worker_registration_pk_and_checks(self) -> None:
        from app.assistant.durable.models import AssistantWorkerRegistration  # noqa: E402

        worker = AssistantWorkerRegistration(
            worker_id="worker-1",
            app_build_revision="build-abc",
            runtime_contract_version=1,
            supported_checkpoint_codec_versions=[1],
            capability_feature_digest=DIGEST_A,
            started_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc),
            hostname_label="api-1",
        )
        self.db.add(worker)
        self.db.commit()
        self.db.expunge_all()

        dup = AssistantWorkerRegistration(
            worker_id="worker-1",
            app_build_revision="build-xyz",
            runtime_contract_version=1,
            supported_checkpoint_codec_versions=[1],
            capability_feature_digest=DIGEST_B,
            started_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc),
        )
        self.db.add(dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.db.expunge_all()

        bad = AssistantWorkerRegistration(
            worker_id="worker-2",
            app_build_revision="build-abc",
            runtime_contract_version=0,
            supported_checkpoint_codec_versions=[1],
            capability_feature_digest=DIGEST_A,
            started_at=datetime.now(timezone.utc),
            heartbeat_at=datetime.now(timezone.utc),
        )
        self.db.add(bad)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    # ------------------------------------------------------------------
    # Immutable children
    # ------------------------------------------------------------------

    def _conversation_and_run(self, *, runtime_kind: str = "main_agent"):
        from app.assistant.models import AssistantChatRun, Conversation  # noqa: E402

        conv = Conversation(title="durable")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(
            conversation_id=conv.id,
            status="queued",
            runtime_kind=runtime_kind,
            runtime_contract_version=1 if runtime_kind == "main_agent" else None,
            required_app_build_revision="build-1" if runtime_kind == "main_agent" else None,
        )
        self.db.add(run)
        self.db.flush()
        return conv, run

    def test_manifest_revision_uniqueness(self) -> None:
        from app.assistant.durable.models import AssistantRunManifestRevision  # noqa: E402

        _, run = self._conversation_and_run()
        m1 = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={"revision": 1},
        )
        self.db.add(m1)
        self.db.commit()

        dup_rev = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_B,
            schema_version=1,
            payload={"revision": 1},
        )
        self.db.add(dup_rev)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        dup_digest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=2,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={"revision": 2},
        )
        self.db.add(dup_digest)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_provider_message_role_discriminator_revision_link_contract(self) -> None:
        from app.assistant.durable.models import (  # noqa: E402
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
            AssistantRunProviderMessage,
        )

        _, run = self._conversation_and_run()
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=DIGEST_B,
            payload={"grants": []},
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=DIGEST_C,
            payload={},
        )
        self.db.add_all([manifest, policy, obligation])
        self.db.flush()

        # Ordinary system message: manifest required, policy/obligation null.
        sys_msg = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=0,
            provider_round=0,
            role="system",
            payload_version=1,
            payload_discriminator=None,
            payload_body={"content": "hi"},
            protection_kind="public",
            content_digest=DIGEST_A,
            manifest_revision_id=manifest.id,
            policy_revision_id=None,
            obligation_revision_id=None,
        )
        self.db.add(sys_msg)
        self.db.commit()

        # Protected runtime_instruction requires policy revision.
        bad_instruction = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=1,
            provider_round=0,
            role="runtime_instruction",
            payload_version=1,
            payload_discriminator="soft_finalization",
            payload_body={"instruction_type": "soft_finalization"},
            protection_kind="protected",
            content_digest=DIGEST_B,
            manifest_revision_id=manifest.id,
            policy_revision_id=None,
            obligation_revision_id=None,
        )
        self.db.add(bad_instruction)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        good_instruction = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=1,
            provider_round=0,
            role="runtime_instruction",
            payload_version=1,
            payload_discriminator="soft_finalization",
            payload_body={"instruction_type": "soft_finalization"},
            protection_kind="protected",
            content_digest=DIGEST_B,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            obligation_revision_id=None,
        )
        self.db.add(good_instruction)
        self.db.commit()

        # runtime_completion requires policy + obligation.
        bad_completion = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=2,
            provider_round=1,
            role="runtime_completion",
            payload_version=1,
            payload_discriminator="completion_guard",
            payload_body={},
            protection_kind="protected",
            content_digest=DIGEST_C,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            obligation_revision_id=None,
        )
        self.db.add(bad_completion)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        good_completion = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=2,
            provider_round=1,
            role="runtime_completion",
            payload_version=1,
            payload_discriminator="completion_guard",
            payload_body={},
            protection_kind="protected",
            content_digest=DIGEST_C,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            obligation_revision_id=obligation.id,
        )
        self.db.add(good_completion)
        self.db.commit()

        # Protected roles cannot be stored as bare system.
        downcast = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=3,
            provider_round=1,
            role="system",
            payload_version=1,
            payload_discriminator="soft_finalization",
            payload_body={},
            protection_kind="protected",
            content_digest=DIGEST_A,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            obligation_revision_id=None,
        )
        self.db.add(downcast)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Unique ordinal within run.
        dup_ord = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=0,
            provider_round=0,
            role="user",
            payload_version=1,
            payload_body={"content": "x"},
            protection_kind="public",
            content_digest=DIGEST_A,
            manifest_revision_id=manifest.id,
        )
        self.db.add(dup_ord)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Role enum
        bad_role = AssistantRunProviderMessage(
            run_id=run.id,
            ordinal=9,
            provider_round=0,
            role="function",
            payload_version=1,
            payload_body={},
            protection_kind="public",
            content_digest=DIGEST_A,
            manifest_revision_id=manifest.id,
        )
        self.db.add(bad_role)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_policy_budget_obligation_checkpoint_uniqueness(self) -> None:
        from app.assistant.durable.models import (  # noqa: E402
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )

        _, run = self._conversation_and_run()
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=DIGEST_A,
            payload={"grants": []},
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=DIGEST_B,
            payload={},
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=DIGEST_C,
            payload={},
        )
        self.db.add_all([manifest, policy, budget, obligation])
        self.db.flush()

        ck = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=0,
            committed_state_revision=1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=-1,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_provider",
            state_payload={"phase": "ready_for_provider"},
            state_digest=DIGEST_A,
        )
        self.db.add(ck)
        self.db.commit()

        dup_seq = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=1,
            committed_state_revision=2,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=-1,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_provider",
            state_payload={},
            state_digest=DIGEST_B,
        )
        self.db.add(dup_seq)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        dup_committed = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=2,
            expected_state_revision=1,
            committed_state_revision=1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=-1,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_provider",
            state_payload={},
            state_digest=DIGEST_B,
        )
        self.db.add(dup_committed)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Policy revision uniqueness
        dup_policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=DIGEST_B,
            payload={},
        )
        self.db.add(dup_policy)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_artifact_storage_kind_xor_and_gc_independent(self) -> None:
        from app.assistant.durable.models import (  # noqa: E402
            AssistantRunArtifact,
            AssistantRunArtifactGc,
        )

        _, run = self._conversation_and_run()
        inline = AssistantRunArtifact(
            run_id=run.id,
            kind="text",
            media_type="text/plain",
            display_label="answer",
            storage_kind="inline",
            byte_size=5,
            content_sha256=DIGEST_A,
            inline_bytes=b"hello",
            object_key=None,
            metadata_json={},
        )
        self.db.add(inline)
        self.db.commit()

        both = AssistantRunArtifact(
            run_id=run.id,
            kind="text",
            media_type="text/plain",
            display_label="bad",
            storage_kind="inline",
            byte_size=1,
            content_sha256=DIGEST_B,
            inline_bytes=b"x",
            object_key="assistant-runs/x",
            metadata_json={},
        )
        self.db.add(both)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        neither = AssistantRunArtifact(
            run_id=run.id,
            kind="text",
            media_type="text/plain",
            display_label="bad2",
            storage_kind="object",
            byte_size=1,
            content_sha256=DIGEST_C,
            inline_bytes=None,
            object_key=None,
            metadata_json={},
        )
        self.db.add(neither)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        obj = AssistantRunArtifact(
            run_id=run.id,
            kind="blob",
            media_type="application/octet-stream",
            display_label="obj",
            storage_kind="object",
            byte_size=10,
            content_sha256=DIGEST_B,
            inline_bytes=None,
            object_key=f"assistant-runs/{run.id}/obj",
            metadata_json={},
        )
        self.db.add(obj)
        self.db.commit()

        # Content identity uniqueness within run
        dup_content = AssistantRunArtifact(
            run_id=run.id,
            kind="blob",
            media_type="application/octet-stream",
            display_label="obj2",
            storage_kind="object",
            byte_size=10,
            content_sha256=DIGEST_B,
            inline_bytes=None,
            object_key=f"assistant-runs/{run.id}/obj2",
            metadata_json={},
        )
        self.db.add(dup_content)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        gc = AssistantRunArtifactGc(
            bucket_name="mindatlas-assistant-artifacts",
            object_key=f"assistant-runs/{run.id}/obj",
            content_sha256=DIGEST_B,
            status="pending",
            attempts=0,
        )
        self.db.add(gc)
        self.db.commit()
        # GC has no run FK — independent outbox.
        mapper = sa_inspect(AssistantRunArtifactGc)
        fk_tables = {fk.column.table.name for rel in mapper.relationships for fk in []}
        # Explicit: no run_id column
        self.assertNotIn("run_id", {c.name for c in AssistantRunArtifactGc.__table__.columns})

    def test_run_pointer_fks_defined_set_null(self) -> None:
        from app.assistant.models import AssistantChatRun  # noqa: E402

        fks = {fk.parent.name: fk for fk in AssistantChatRun.__table__.foreign_keys}
        for col in (
            "current_manifest_revision_id",
            "current_policy_revision_id",
            "current_checkpoint_id",
            "current_budget_revision_id",
            "current_obligation_revision_id",
        ):
            self.assertIn(col, fks, msg=f"missing pointer FK column {col}")
            self.assertEqual(fks[col].ondelete, "SET NULL")

    # ------------------------------------------------------------------
    # L2 split uniqueness (Legacy vs native)
    # ------------------------------------------------------------------

    def test_l2_legacy_and_native_uniqueness_split(self) -> None:
        from app.assistant.models import (  # noqa: E402
            AssistantConversationSkillL2Memory,
            Conversation,
        )
        from app.assistant.skills.models import AssistantSkillPackage  # noqa: E402

        conv = Conversation(title="mem")
        self.db.add(conv)
        self.db.flush()

        legacy = AssistantConversationSkillL2Memory(
            conversation_id=conv.id,
            skill_name="legacy-skill",
            facts=["a"],
            skill_package_id=None,
            memory_namespace=None,
        )
        self.db.add(legacy)
        self.db.commit()

        legacy_dup = AssistantConversationSkillL2Memory(
            conversation_id=conv.id,
            skill_name="legacy-skill",
            facts=["b"],
            skill_package_id=None,
            memory_namespace=None,
        )
        self.db.add(legacy_dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        pkg = AssistantSkillPackage(
            canonical_name="native-skill",
            display_name="Native",
            description="d",
            migration_state="native",
            catalog_enabled=False,
            is_system=False,
        )
        self.db.add(pkg)
        self.db.flush()

        native = AssistantConversationSkillL2Memory(
            conversation_id=conv.id,
            skill_name="native-skill",  # display/compat only
            facts=[],
            skill_package_id=pkg.id,
            memory_namespace="default",
            facts_v2=[],
        )
        self.db.add(native)
        self.db.commit()

        # Same package+namespace collides even with different display skill_name.
        native_dup = AssistantConversationSkillL2Memory(
            conversation_id=conv.id,
            skill_name="other-display",
            facts=[],
            skill_package_id=pkg.id,
            memory_namespace="default",
            facts_v2=[],
        )
        self.db.add(native_dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

        # Different namespace is allowed for same package.
        other_ns = AssistantConversationSkillL2Memory(
            conversation_id=conv.id,
            skill_name="native-skill",
            facts=[],
            skill_package_id=pkg.id,
            memory_namespace="project",
            facts_v2=[],
        )
        self.db.add(other_ns)
        self.db.commit()

        # Legacy name index must be partial (null package only).
        indexes = {
            idx.name: idx
            for idx in AssistantConversationSkillL2Memory.__table__.indexes
        }
        self.assertIn("uq_assistant_l2_memory_legacy_conversation_skill", indexes)
        self.assertIn("uq_assistant_l2_memory_native_package_namespace", indexes)

    def test_l1_last_applied_run_fk(self) -> None:
        from app.assistant.models import (  # noqa: E402
            AssistantChatRun,
            AssistantConversationL1Memory,
            Conversation,
        )

        conv = Conversation(title="l1")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(conversation_id=conv.id, status="completed")
        self.db.add(run)
        self.db.flush()
        mem = AssistantConversationL1Memory(
            conversation_id=conv.id,
            summary_text="s",
            last_applied_run_id=run.id,
        )
        self.db.add(mem)
        self.db.commit()
        self.db.refresh(mem)
        self.assertEqual(mem.last_applied_run_id, run.id)


if __name__ == "__main__":
    unittest.main()
