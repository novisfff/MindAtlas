"""Plan 08 Task 1: CapabilityCall ledger model / contract / constraint tests.

CAS repository methods land in Task 2. This file pins storage contracts,
ORM check constraints, unique keys, interrupt origin XOR, and vocabulary.
PostgreSQL trigger/migration cycle lives in CI-gated companion tests when
MINDATLAS_TEST_POSTGRES_URL is set (see test_capability_call_migration_postgres.py
if added later); local SQLite cannot exercise PL/pgSQL triggers.
"""

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


def _make_main_agent_run(db, *, status: str = "queued", **kwargs):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-test-1",
        capability_ledger_mode=kwargs.pop("capability_ledger_mode", "legacy_read_only"),
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _manifest(db, run_id, *, revision: int = 1):
    from app.assistant.durable.models import AssistantRunManifestRevision

    row = AssistantRunManifestRevision(
        run_id=run_id,
        revision=revision,
        manifest_digest=DIGEST_A if revision == 1 else DIGEST_B,
        schema_version=1,
        payload={"k": revision},
    )
    db.add(row)
    db.flush()
    return row


def _artifact(db, run_id, *, kind: str = "call_input"):
    from app.assistant.durable.models import AssistantRunArtifact
    import hashlib
    import os

    payload = (kind + ":" + os.urandom(8).hex()).encode()
    digest = hashlib.sha256(payload).hexdigest()
    row = AssistantRunArtifact(
        run_id=run_id,
        kind=kind,
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(payload),
        content_sha256=digest,
        inline_bytes=payload,
        metadata_json={},
    )
    db.add(row)
    db.flush()
    return row


def _call_kwargs(run_id, manifest_id, artifact_id, **overrides):
    base = dict(
        id=uuid.uuid4(),
        run_id=run_id,
        manifest_revision_id=manifest_id,
        logical_call_key=f"provider:0:0:{uuid.uuid4().hex[:8]}",
        owner_kind="main_agent",
        capability_type="tool",
        domain_key="create_entry",
        descriptor_digest=DIGEST_A,
        authorization_digest=DIGEST_B,
        input_artifact_id=artifact_id,
        input_digest=DIGEST_A,
        side_effect_class="write_local",
        execution_mode="local_transactional",
        idempotency_key=f"idem-{uuid.uuid4().hex}",
        status="proposed",
        state_revision=0,
        attempt_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return base


class CapabilityCallContractTests(unittest.TestCase):
    def test_status_and_mode_vocabularies(self) -> None:
        from app.assistant.capability_calls.contracts import (
            CAPABILITY_CALL_STATUSES,
            CAPABILITY_EXECUTION_MODES,
            CAPABILITY_LEDGER_MODES,
            CALL_ATTEMPT_STATUSES,
            INTERRUPT_ORIGINS,
            RECONCILIATION_DECISIONS,
        )

        self.assertIn("proposed", CAPABILITY_CALL_STATUSES)
        self.assertIn("needs_reconciliation", CAPABILITY_CALL_STATUSES)
        self.assertIn("local_transactional", CAPABILITY_EXECUTION_MODES)
        self.assertEqual(set(CAPABILITY_LEDGER_MODES), {"legacy_read_only", "enforced"})
        self.assertIn("claimed", CALL_ATTEMPT_STATUSES)
        self.assertEqual(set(INTERRUPT_ORIGINS), {"workflow_node", "capability_call"})
        self.assertIn("retry_same_key", RECONCILIATION_DECISIONS)

    def test_identity_contract_frozen(self) -> None:
        from app.assistant.capability_calls.contracts import CapabilityCallIdentity

        identity = CapabilityCallIdentity(
            run_id=uuid.uuid4(),
            logical_call_key="k",
            parent_call_id=None,
            manifest_revision_id=uuid.uuid4(),
            owner_kind="main_agent",
            owner_id=None,
            owner_version_id=None,
            capability_type="tool",
            domain_key="create_entry",
            target_id=None,
            target_version_id=None,
            descriptor_digest=DIGEST_A,
            input_digest=DIGEST_B,
            side_effect_class="write_local",
            execution_mode="local_transactional",
        )
        with self.assertRaises(Exception):
            identity.logical_call_key = "other"  # type: ignore[misc]


class CapabilityCallModelTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_proposed_call_row(self) -> None:
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        call = AssistantCapabilityCall(
            **_call_kwargs(run.id, manifest.id, artifact.id)
        )
        self.db.add(call)
        self.db.commit()
        self.db.refresh(call)
        self.assertEqual(call.status, "proposed")
        self.assertEqual(call.state_revision, 0)
        self.assertIsNone(call.side_effect_started_at)

    def test_unique_logical_call_key_per_run(self) -> None:
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        key = "provider:1:0:abc"
        self.db.add(
            AssistantCapabilityCall(
                **_call_kwargs(run.id, manifest.id, artifact.id, logical_call_key=key)
            )
        )
        self.db.commit()
        self.db.add(
            AssistantCapabilityCall(
                **_call_kwargs(
                    run.id,
                    manifest.id,
                    artifact.id,
                    logical_call_key=key,
                    idempotency_key=f"idem-{uuid.uuid4().hex}",
                )
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_local_transactional_forbids_effect_start_unless_succeeded(self) -> None:
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        call = AssistantCapabilityCall(
            **_call_kwargs(
                run.id,
                manifest.id,
                artifact.id,
                status="executing",
                side_effect_started_at=datetime.now(timezone.utc),
            )
        )
        self.db.add(call)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_attempt_number_unique_and_positive(self) -> None:
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        call = AssistantCapabilityCall(
            **_call_kwargs(run.id, manifest.id, artifact.id)
        )
        self.db.add(call)
        self.db.flush()
        now = datetime.now(timezone.utc)
        self.db.add(
            AssistantCapabilityCallAttempt(
                id=uuid.uuid4(),
                call_id=call.id,
                attempt_number=1,
                worker_id="w1",
                lease_generation=1,
                status="claimed",
                started_at=now,
                created_at=now,
            )
        )
        self.db.commit()
        self.db.add(
            AssistantCapabilityCallAttempt(
                id=uuid.uuid4(),
                call_id=call.id,
                attempt_number=1,
                worker_id="w2",
                lease_generation=1,
                status="claimed",
                started_at=now,
                created_at=now,
            )
        )
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_entry_source_capability_call_unique(self) -> None:
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.entry.models import Entry, TimeMode
        from app.entry_type.models import EntryType

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        call = AssistantCapabilityCall(
            **_call_kwargs(run.id, manifest.id, artifact.id)
        )
        self.db.add(call)
        self.db.flush()
        et = EntryType(
            code="KNOWLEDGE",
            name="Knowledge",
            color="#1",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add(et)
        self.db.flush()
        e1 = Entry(
            title="a",
            content="c",
            type_id=et.id,
            time_mode=TimeMode.NONE,
            source_capability_call_id=call.id,
        )
        self.db.add(e1)
        self.db.commit()
        e2 = Entry(
            title="b",
            content="c2",
            type_id=et.id,
            time_mode=TimeMode.NONE,
            source_capability_call_id=call.id,
        )
        self.db.add(e2)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_interrupt_origin_xor_workflow_node(self) -> None:
        """workflow_node requires frame/node/visit and null capability_call_id."""
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunInterrupt,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )

        run = _make_main_agent_run(self.db)
        manifest = _manifest(self.db, run.id)
        policy = AssistantRunPolicyRevision(
            run_id=run.id, revision=1, policy_digest=DIGEST_A, payload={}
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id, revision=1, budget_digest=DIGEST_A, payload={}
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id, revision=1, obligation_digest=DIGEST_A, payload={}
        )
        self.db.add_all([policy, budget, obligation])
        self.db.flush()
        ckpt = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=0,
            committed_state_revision=0,
            schema_version=2,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="waiting",
            state_payload={"waiting": True},
            state_digest=DIGEST_A,
        )
        self.db.add(ckpt)
        self.db.flush()
        now = datetime.now(timezone.utc)

        # Valid workflow_node profile.
        good = AssistantRunInterrupt(
            run_id=run.id,
            interrupt_key=f"k-{uuid.uuid4().hex[:8]}",
            kind="approval",
            status="pending",
            checkpoint_id=ckpt.id,
            manifest_revision_id=manifest.id,
            capability_call_id=None,
            interrupt_origin="workflow_node",
            workflow_frame_id=uuid.uuid4(),
            node_id="n1",
            node_visit_id="v1",
            request_revision=1,
            request_run_revision=0,
            budget_revision_id=budget.id,
            budget_suspension_state={"contractVersion": 1},
            budget_suspension_digest=DIGEST_A,
            request_payload={},
            request_digest=DIGEST_A,
            initial_values={},
            expires_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(good)
        self.db.commit()
        self.assertEqual(good.interrupt_origin, "workflow_node")
        self.assertIsNone(good.capability_call_id)

        # Invalid: capability_call origin without call id / with workflow identity.
        bad = AssistantRunInterrupt(
            run_id=run.id,
            interrupt_key=f"k-{uuid.uuid4().hex[:8]}",
            kind="approval",
            status="pending",
            checkpoint_id=ckpt.id,
            manifest_revision_id=manifest.id,
            capability_call_id=None,
            interrupt_origin="capability_call",
            workflow_frame_id=uuid.uuid4(),
            node_id="n1",
            node_visit_id="v1",
            request_revision=1,
            request_run_revision=0,
            budget_revision_id=budget.id,
            budget_suspension_state={"contractVersion": 1},
            budget_suspension_digest=DIGEST_A,
            request_payload={},
            request_digest=DIGEST_A,
            initial_values={},
            expires_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(bad)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()


    def test_run_capability_ledger_mode_values(self) -> None:
        run = _make_main_agent_run(self.db, capability_ledger_mode="enforced")
        self.assertEqual(run.capability_ledger_mode, "enforced")
        legacy = _make_main_agent_run(self.db, capability_ledger_mode="legacy_read_only")
        self.assertEqual(legacy.capability_ledger_mode, "legacy_read_only")


class CapabilityCallMigrationMetaTests(unittest.TestCase):
    def test_revision_parent_and_sole_head(self) -> None:
        from pathlib import Path
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        backend = Path(__file__).resolve().parents[1]
        cfg = Config(str(backend / "alembic.ini"))
        cfg.set_main_option("script_location", str(backend / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        self.assertEqual(len(heads), 1, heads)
        head = heads[0]
        rev = script.get_revision(head)
        self.assertEqual(rev.down_revision, "7a3dac0ac2a8")
        # Filename must mention capability call ledger.
        self.assertIn("capability_call_ledger", rev.path)


if __name__ == "__main__":
    unittest.main()


class CapabilityCallRepositoryCasTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _seed(self):
        from app.assistant.capability_calls.repository import ProposeCallSpec
        from app.assistant.durable.repository import LeaseToken

        run = _make_main_agent_run(self.db, status="running", state_revision=1)
        run.lease_owner = "worker-1"
        run.lease_generation = 1
        self.db.commit()
        self.db.refresh(run)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        spec = ProposeCallSpec(
            call_id=uuid.uuid4(),
            run_id=run.id,
            expected_run_revision=1,
            lease=lease,
            manifest_revision_id=manifest.id,
            logical_call_key=f"provider:0:0:{uuid.uuid4().hex[:8]}",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="search_entries",
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_B,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            side_effect_class="read",
            execution_mode="read_replayable",
            idempotency_key=f"idem-{uuid.uuid4().hex}",
        )
        return run, lease, spec

    def test_create_or_verify_idempotent(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        call1, created1 = repo.create_or_verify_proposed(spec)
        self.db.commit()
        self.assertTrue(created1)
        call2, created2 = repo.create_or_verify_proposed(spec)
        self.db.commit()
        self.assertFalse(created2)
        self.assertEqual(call1.id, call2.id)

    def test_create_or_verify_identity_mismatch(self) -> None:
        from app.assistant.capability_calls.repository import (
            CODE_IDENTITY_MISMATCH,
            CapabilityCallConflict,
            CapabilityCallRepository,
            ProposeCallSpec,
        )

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        repo.create_or_verify_proposed(spec)
        self.db.commit()
        bad = ProposeCallSpec(
            **{
                **{f: getattr(spec, f) for f in ProposeCallSpec.__dataclass_fields__},
                "input_digest": DIGEST_B,
            }
        )
        with self.assertRaises(CapabilityCallConflict) as ctx:
            repo.create_or_verify_proposed(bad)
        self.assertEqual(ctx.exception.code, CODE_IDENTITY_MISMATCH)

    def test_authorize_claim_succeed_read_path(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        self.db.commit()
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=lease,
        )
        self.db.commit()
        call, attempt = repo.claim_attempt(
            call_id=call.id,
            expected_call_revision=1,
            expected_run_revision=1,
            lease=lease,
            worker_id="worker-1",
        )
        self.db.commit()
        self.assertEqual(call.status, "executing")
        self.assertEqual(attempt.attempt_number, 1)
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=DIGEST_A,
        )
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=DIGEST_B,
        )
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="response_received",
            to_status="committed",
        )
        out = _artifact(self.db, run.id, kind="call_output")
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=1,
            to_status="succeeded",
            lease=lease,
            output_artifact_id=out.id,
        )
        self.db.commit()
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(attempt.status, "committed")
        self.assertEqual(attempt.request_digest, DIGEST_A)
        self.assertEqual(attempt.response_digest, DIGEST_B)
        self.assertIsNotNone(attempt.ended_at)
        self.assertIsNotNone(call.terminal_at)

    def test_stale_call_revision_rejected(self) -> None:
        from app.assistant.capability_calls.repository import (
            CODE_STALE_CALL_REVISION,
            CapabilityCallConflict,
            CapabilityCallRepository,
        )

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict) as ctx:
            repo.transition_call(
                call_id=call.id,
                expected_call_revision=99,
                expected_run_revision=1,
                to_status="authorized",
                lease=lease,
            )
        self.assertEqual(ctx.exception.code, CODE_STALE_CALL_REVISION)

    def test_settlement_unknown_moves_run_to_needs_reconciliation(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            SettlementRequest,
        )
        from app.assistant.durable.repository import LeaseToken

        run, lease, spec = self._seed()
        # external mode for effect-start while executing
        from dataclasses import replace

        # rebuild spec with external mode
        from app.assistant.capability_calls.repository import ProposeCallSpec

        run = _make_main_agent_run(self.db, status="running", state_revision=1)
        run.lease_owner = "worker-1"
        run.lease_generation = 1
        self.db.commit()
        self.db.refresh(run)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        spec = ProposeCallSpec(
            call_id=uuid.uuid4(),
            run_id=run.id,
            expected_run_revision=1,
            lease=lease,
            manifest_revision_id=manifest.id,
            logical_call_key=f"provider:0:0:{uuid.uuid4().hex[:8]}",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="external_write",
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_B,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            side_effect_class="write_external",
            execution_mode="external_idempotent",
            idempotency_key=f"idem-{uuid.uuid4().hex}",
        )
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        self.db.commit()
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=lease,
        )
        self.db.commit()
        call, attempt = repo.claim_attempt(
            call_id=call.id,
            expected_call_revision=1,
            expected_run_revision=1,
            lease=lease,
            worker_id="worker-1",
        )
        self.db.commit()
        # mark effect started while executing (external protocol)
        call.side_effect_started_at = datetime.now(timezone.utc)
        call.state_revision = int(call.state_revision) + 1
        self.db.commit()
        # Run enters cancelling
        run.status = "cancelling"
        run.state_revision = 2
        self.db.commit()
        settlement = CapabilityCallSettlementRepository(self.db)
        run2 = settlement.settle_while_cancelling(
            SettlementRequest(
                call_id=call.id,
                attempt_id=attempt.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=2,
                outcome="unknown",
                result_artifact_id=None,
                evidence_digest=DIGEST_A,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.assertEqual(run2.status, "needs_reconciliation")
        self.assertEqual(call.status, "needs_reconciliation")

    def test_refuse_cancel_finalizer_with_unproven_started(self) -> None:
        from app.assistant.capability_calls.repository import (
            CapabilityCallConflict,
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken

        run = _make_main_agent_run(self.db, status="cancelling", state_revision=2)
        run.lease_owner = "worker-1"
        run.lease_generation = 1
        self.db.commit()
        self.db.refresh(run)
        manifest = _manifest(self.db, run.id)
        artifact = _artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        # Directly insert an executing call with effect started.
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        call = AssistantCapabilityCall(
            **_call_kwargs(
                run.id,
                manifest.id,
                artifact.id,
                status="executing",
                execution_mode="external_idempotent",
                side_effect_class="write_external",
                side_effect_started_at=datetime.now(timezone.utc),
                state_revision=3,
            )
        )
        self.db.add(call)
        self.db.commit()
        durable = DurableRunRepository(self.db)
        with self.assertRaises(CapabilityCallConflict):
            durable.finalize_cancellation(
                run_id=run.id,
                expected_revision=2,
                lease=lease,
                require_lease=True,
            )
        settlement = CapabilityCallSettlementRepository(self.db)
        with self.assertRaises(CapabilityCallConflict):
            settlement.refuse_cancel_finalizer_if_unproven(run.id)
