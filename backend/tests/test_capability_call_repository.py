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


def _normalized_result_artifact(db, call, *, status: str = "completed"):
    from tests.test_agent_policy_runtime import _base_manifest
    from app.assistant.capabilities.contracts import (
        CapabilityError,
        CapabilityMetrics,
        completed_result,
        failed_result,
    )
    from app.assistant.capability_calls.result_codec import encode_capability_result
    from app.assistant.durable.models import AssistantRunArtifact
    from app.assistant.provider_loop.contracts import ProviderDispatchResult

    metrics = CapabilityMetrics(duration_ms=1.0, input_bytes=2, output_bytes=3)
    capability_result = (
        completed_result(structured_output={"value": 7}, metrics=metrics)
        if status == "completed"
        else failed_result(
            error=CapabilityError(
                error_type="execution_failed",
                safe_code="provider_failed",
                safe_message="provider failed",
                retry_disposition="never",
            ),
            metrics=metrics,
        )
    )
    encoded = encode_capability_result(
        call_id=str(call.provider_tool_call_id),
        binding_contract_digest=str(call.authorization_digest),
        descriptor_digest=str(call.descriptor_digest),
        result=ProviderDispatchResult(
            capability_result=capability_result,
            next_manifest=_base_manifest(run_id=call.run_id)[0],
        ),
    )
    artifact = AssistantRunArtifact(
        run_id=call.run_id,
        kind="capability_call_result",
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(encoded.payload),
        content_sha256=encoded.digest,
        inline_bytes=encoded.payload,
        metadata_json={"contractVersion": 1},
    )
    db.add(artifact)
    db.flush()
    return artifact


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

    def test_interrupt_repository_creates_capability_call_origin(self) -> None:
        """Call-owned approval rows use the XOR profile and keep call linkage."""
        from datetime import timedelta

        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.policy import (
            create_initial_ledger_state,
            normalize_run_budget_limits,
        )
        from app.assistant.workflow.durable.interrupts import DurableInterruptRepository

        run = _make_main_agent_run(
            self.db,
            status="running",
            capability_ledger_mode="enforced",
            state_revision=4,
        )
        manifest = _manifest(self.db, run.id)
        policy = AssistantRunPolicyRevision(
            run_id=run.id, revision=1, policy_digest=DIGEST_A, payload={}
        )
        start = datetime.now(timezone.utc)
        ledger = create_initial_ledger_state(
            limits=normalize_run_budget_limits(),
            started_at_utc=start,
            deadline_at_utc=start + timedelta(minutes=2),
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=ledger.ledger_digest,
            payload=ledger.model_dump(mode="json", by_alias=True),
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id, revision=1, obligation_digest=DIGEST_A, payload={}
        )
        self.db.add_all([policy, budget, obligation])
        self.db.flush()
        checkpoint = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=4,
            committed_state_revision=4,
            schema_version=2,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="waiting",
            state_payload={"schemaVersion": 2, "phase": "waiting"},
            state_digest=DIGEST_A,
        )
        self.db.add(checkpoint)
        self.db.flush()
        artifact = _artifact(self.db, run.id)
        call = AssistantCapabilityCall(
            **_call_kwargs(
                run.id,
                manifest.id,
                artifact.id,
                status="awaiting_approval",
            )
        )
        call.approval_binding_digest = DIGEST_B
        self.db.add(call)
        self.db.flush()
        run.current_manifest_revision_id = manifest.id
        run.current_policy_revision_id = policy.id
        run.current_budget_revision_id = budget.id
        run.current_obligation_revision_id = obligation.id
        run.current_checkpoint_id = checkpoint.id
        self.db.flush()

        interrupt_id = uuid.uuid4()
        created = DurableInterruptRepository(self.db).create_pending_interrupt(
            run_id=run.id,
            interrupt_id=interrupt_id,
            interrupt_key=f"capability:{call.id}",
            kind="approval",
            checkpoint_id=checkpoint.id,
            manifest_revision_id=manifest.id,
            budget_revision_id=budget.id,
            capability_call_id=call.id,
            interrupt_origin="capability_call",
            workflow_frame_id=None,
            node_id=None,
            node_visit_id=None,
            request_run_revision=4,
            request_payload={"approvalBindingDigest": DIGEST_B},
            field_schema=None,
            initial_values={},
            parent_ledger=ledger,
            parent_budget_revision_id=budget.id,
        )

        self.assertEqual(created.interrupt.interrupt_origin, "capability_call")
        self.assertEqual(created.interrupt.capability_call_id, call.id)
        self.assertIsNone(created.interrupt.workflow_frame_id)
        self.assertIsNone(created.interrupt.node_id)
        self.assertIsNone(created.interrupt.node_visit_id)


    def test_run_capability_ledger_mode_values(self) -> None:
        run = _make_main_agent_run(self.db, capability_ledger_mode="enforced")
        self.assertEqual(run.capability_ledger_mode, "enforced")
        legacy = _make_main_agent_run(self.db, capability_ledger_mode="legacy_read_only")
        self.assertEqual(legacy.capability_ledger_mode, "legacy_read_only")


class CapabilityCallMigrationMetaTests(unittest.TestCase):
    def test_revision_parent_and_sole_head(self) -> None:
        """Plan 08 ledger chain remains ancestors of the sole Plan 09 head."""
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
        # Plan 09 eval workbench is the sole pre-merge head (alias soft-disable
        # folded into 09A lifecycle; residual 24f1e06fdd9e removed).
        self.assertEqual(head, "027869a00a47")

        plan09_eval = script.get_revision(head)
        self.assertEqual(plan09_eval.down_revision, "403414a62e55")
        self.assertIn("skill_evaluation_workbench", plan09_eval.path)

        plan09_lifecycle = script.get_revision(plan09_eval.down_revision)
        self.assertEqual(plan09_lifecycle.down_revision, "d7e8f9a0b1c3")
        self.assertIn("skill_package_admin_lifecycle", plan09_lifecycle.path)

        # Plan 08 tip remains the parent of Plan 09 Task 1.
        plan08_evidence = script.get_revision(plan09_lifecycle.down_revision)
        self.assertEqual(plan08_evidence.revision, "d7e8f9a0b1c3")
        self.assertEqual(plan08_evidence.down_revision, "f2c3a4b5d6e7")
        self.assertIn("reconciliation_evidence", plan08_evidence.path)
        lifecycle = script.get_revision(plan08_evidence.down_revision)
        self.assertEqual(lifecycle.down_revision, "984c07876856")
        self.assertIn("capability_attempt_lifecycle", lifecycle.path)
        ledger = script.get_revision(lifecycle.down_revision)
        self.assertEqual(ledger.down_revision, "7a3dac0ac2a8")
        self.assertIn("capability_call_ledger", ledger.path)


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

    def _seed_external_settlement(
        self,
        *,
        attempt_status: str = "dispatched",
        response_digest: str | None = None,
        error_code: str | None = None,
    ):
        """Create an executing external Call with one durable Attempt."""
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.durable.repository import LeaseToken

        run = _make_main_agent_run(self.db, status="running", state_revision=1)
        run.lease_owner = "worker-1"
        run.lease_generation = 1
        self.db.commit()
        manifest = _manifest(self.db, run.id)
        input_artifact = _artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(
            ProposeCallSpec(
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
                input_artifact_id=input_artifact.id,
                input_digest=DIGEST_A,
                side_effect_class="write_external",
                execution_mode="external_idempotent",
                idempotency_key=f"idem-{uuid.uuid4().hex}",
                provider_tool_call_id=f"tc-{uuid.uuid4().hex}",
            )
        )
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=lease,
        )
        call, attempt = repo.claim_attempt(
            call_id=call.id,
            expected_call_revision=1,
            expected_run_revision=1,
            lease=lease,
            worker_id="worker-1",
        )
        if attempt_status != "claimed":
            attempt = repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="claimed",
                to_status=("failed" if attempt_status == "failed" else "dispatched"),
                request_digest=DIGEST_A,
                error_code=(error_code if attempt_status == "failed" else None),
            )
        if attempt_status in {"response_received", "committed"}:
            attempt = repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="dispatched",
                to_status="response_received",
                response_digest=response_digest,
            )
        if attempt_status == "committed":
            attempt = repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="response_received",
                to_status="committed",
            )
        if attempt_status == "uncertain":
            attempt = repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="dispatched",
                to_status="uncertain",
                error_code=error_code,
            )
        from app.assistant.durable.checkpoints import _build_provider_message_rows
        from app.assistant.durable.codec import checkpoint_state_digest, encode_checkpoint_v3
        from app.assistant.durable.contracts import (
            DurableAgentCheckpointV3,
            DurableCapabilityCallStateV1,
            DurableNextActionV2,
        )
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.policy.obligations import (
            create_initial_obligation_ledger_state,
        )
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolCall,
            digest_arguments,
            digest_provider_transcript,
        )

        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest="c" * 64,
            payload={},
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest="d" * 64,
            payload={},
        )
        obligations = create_initial_obligation_ledger_state()
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=obligations.ledger_digest,
            payload=obligations.model_dump(mode="json", by_alias=True),
        )
        self.db.add_all([policy, budget, obligation])
        self.db.flush()
        provider_call = ProviderToolCall(
            call_id=str(call.provider_tool_call_id),
            call_index=0,
            provider_alias="external_write",
            domain_key=str(call.domain_key),
            arguments={},
            arguments_digest=digest_arguments({}),
            binding_contract_digest=str(call.authorization_digest),
            descriptor_digest=str(call.descriptor_digest),
            behavior_digest="e" * 64,
            classification_revision="plan08-settlement-test",
            classification_ruleset_digest="f" * 64,
            manifest_revision=1,
            manifest_digest=str(manifest.manifest_digest),
            surface_digest="1" * 64,
        )
        assistant = ProviderAssistantMessage(content=None, tool_calls=(provider_call,))
        provider_rows = _build_provider_message_rows(
            run_id=run.id,
            messages=(assistant,),
            start_ordinal=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            obligation_revision_id=obligation.id,
        )
        checkpoint = DurableAgentCheckpointV3(
            run_id=run.id,
            phase="dispatching_calls",
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=1,
            provider_transcript_digest=digest_provider_transcript((assistant,)),
            provider_loop_continuation=None,
            inflight_unit=None,
            capability_frames=(),
            artifact_ids=(input_artifact.id,),
            visible_text_artifact_id=None,
            next_action=DurableNextActionV2(kind="dispatch_calls"),
            policy_contract_version=2,
            capability_calls=(
                DurableCapabilityCallStateV1(
                    call_id=call.id,
                    logical_call_key=str(call.logical_call_key),
                    provider_tool_call_id=str(call.provider_tool_call_id),
                    provider_order=0,
                    status="executing",
                    attempt_id=attempt.id,
                ),
            ),
        )
        checkpoint_row = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=1,
            committed_state_revision=2,
            schema_version=3,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=1,
            provider_transcript_digest=checkpoint.provider_transcript_digest,
            phase=checkpoint.phase,
            logical_unit_id=str(call.logical_call_key),
            reason="test_external_attempt_started",
            state_payload=encode_checkpoint_v3(checkpoint),
            state_digest=checkpoint_state_digest(checkpoint),
        )
        self.db.add_all([*provider_rows, checkpoint_row])
        self.db.flush()
        run.current_manifest_revision_id = manifest.id
        run.current_policy_revision_id = policy.id
        run.current_budget_revision_id = budget.id
        run.current_obligation_revision_id = obligation.id
        run.current_checkpoint_id = checkpoint_row.id
        effect_started_at = datetime.now(timezone.utc)
        call.side_effect_started_at = effect_started_at
        attempt.side_effect_started = True
        attempt.side_effect_started_at = effect_started_at
        call.state_revision = int(call.state_revision) + 1
        run.status = "cancelling"
        run.state_revision = 2
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(attempt)
        return run, call, attempt

    def _settle_request(
        self,
        *,
        run,
        call,
        attempt_id,
        outcome: str,
        result_artifact_id=None,
        evidence_digest: str = DIGEST_A,
    ):
        from app.assistant.capability_calls.settlement import SettlementRequest

        return SettlementRequest(
            call_id=call.id,
            attempt_id=attempt_id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(run.state_revision),
            outcome=outcome,
            result_artifact_id=result_artifact_id,
            evidence_digest=evidence_digest,
        )

    def test_settlement_rejects_missing_attempt(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, _ = self._seed_external_settlement(attempt_status="uncertain")
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=uuid.uuid4(),
                    outcome="unknown",
                )
            )

    def test_settlement_rejects_attempt_owned_by_another_call(self) -> None:
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, _ = self._seed_external_settlement(attempt_status="uncertain")
        other = AssistantCapabilityCall(
            **_call_kwargs(
                run.id,
                call.manifest_revision_id,
                call.input_artifact_id,
                status="executing",
                execution_mode="external_idempotent",
                side_effect_class="write_external",
                attempt_count=1,
                side_effect_started_at=datetime.now(timezone.utc),
            )
        )
        self.db.add(other)
        self.db.flush()
        other_attempt = AssistantCapabilityCallAttempt(
            call_id=other.id,
            attempt_number=1,
            worker_id="worker-1",
            lease_generation=1,
            status="uncertain",
            request_digest=DIGEST_A,
            error_code="transport_outcome_unknown",
        )
        self.db.add(other_attempt)
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=other_attempt.id,
                    outcome="unknown",
                )
            )

    def test_settlement_rejects_mismatched_evidence_digest(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="unknown",
                    evidence_digest=DIGEST_B,
                )
            )

    def test_settlement_rejects_attempt_request_not_bound_to_call_input(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        attempt.request_digest = DIGEST_B
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="unknown",
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_rejects_stale_attempt_evidence(self) -> None:
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        call.attempt_count = 2
        newer_attempt = AssistantCapabilityCallAttempt(
            call_id=call.id,
            attempt_number=2,
            worker_id="worker-1",
            lease_generation=1,
            status="claimed",
        )
        self.db.add(newer_attempt)
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="unknown",
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_rejects_success_without_captured_response(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, attempt = self._seed_external_settlement(attempt_status="dispatched")
        result = _artifact(self.db, run.id, kind="capability_call_result")
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                )
            )

    def test_settlement_rejects_success_without_result_artifact(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="committed",
            response_digest=DIGEST_A,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                )
            )

    def test_settlement_rejects_missing_result_artifact_row(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="committed",
            response_digest=DIGEST_A,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=uuid.uuid4(),
                )
            )

    def test_settlement_rejects_result_digest_not_captured_by_attempt(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="committed",
            response_digest=DIGEST_A,
        )
        result = _artifact(self.db, run.id, kind="capability_call_result")
        self.db.commit()
        self.assertNotEqual(result.content_sha256, attempt.response_digest)
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                )
            )

    def test_settlement_rejects_failed_or_unknown_with_result_artifact(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        for outcome, status in (("failed", "failed"), ("unknown", "uncertain")):
            with self.subTest(outcome=outcome):
                run, call, attempt = self._seed_external_settlement(
                    attempt_status=status,
                    error_code="transport_error",
                )
                result = _artifact(self.db, run.id, kind="capability_call_result")
                self.db.commit()
                with self.assertRaises(CapabilityCallConflict):
                    CapabilityCallSettlementRepository(
                        self.db
                    ).settle_while_cancelling(
                        self._settle_request(
                            run=run,
                            call=call,
                            attempt_id=attempt.id,
                            outcome=outcome,
                            result_artifact_id=result.id,
                        )
                    )
                self.db.rollback()

    def test_settlement_rejects_outcome_not_proved_by_attempt_status(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        for outcome in ("failed", "unknown"):
            with self.subTest(outcome=outcome):
                run, call, attempt = self._seed_external_settlement(
                    attempt_status="dispatched"
                )
                with self.assertRaises(CapabilityCallConflict):
                    CapabilityCallSettlementRepository(
                        self.db
                    ).settle_while_cancelling(
                        self._settle_request(
                            run=run,
                            call=call,
                            attempt_id=attempt.id,
                            outcome=outcome,
                        )
                    )
                self.db.rollback()

    def test_settlement_rejects_cross_run_result_artifact(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )

        other_run = _make_main_agent_run(self.db)
        other_result = _artifact(self.db, other_run.id, kind="capability_call_result")
        run, call, attempt = self._seed_external_settlement(
            attempt_status="committed",
            response_digest=other_result.content_sha256,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=other_result.id,
                )
            )

    def test_settlement_rejects_random_result_bytes(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        result = _artifact(self.db, run.id, kind="capability_call_result")
        result.metadata_json = {"contractVersion": 1}
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_accepts_captured_success_and_commits_attempt(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        budget_pointer = run.current_budget_revision_id
        obligation_pointer = run.current_obligation_revision_id
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="succeeded",
                result_artifact_id=result.id,
                evidence_digest=evidence_digest,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(attempt)
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(call.output_artifact_id, result.id)
        self.assertEqual(attempt.status, "committed")
        self.db.refresh(run)
        self.assertEqual(run.state_revision, 3)
        self.assertEqual(run.status, "cancelling")
        self.assertEqual(run.current_budget_revision_id, budget_pointer)
        self.assertEqual(run.current_obligation_revision_id, obligation_pointer)
        from app.assistant.durable.codec import decode_checkpoint, decode_provider_message
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunProviderMessage,
        )
        from app.assistant.models import AssistantChatRunEvent

        messages = (
            self.db.query(AssistantRunProviderMessage)
            .filter(AssistantRunProviderMessage.run_id == run.id)
            .order_by(AssistantRunProviderMessage.ordinal.asc())
            .all()
        )
        self.assertEqual([row.role for row in messages], ["assistant", "tool"])
        tool = decode_provider_message(messages[-1].payload_body)
        self.assertEqual(tool.call_id, str(call.provider_tool_call_id))
        self.assertEqual(tool.content.status, "completed")
        checkpoint_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        self.assertEqual(checkpoint.phase, "terminal")
        state = next(item for item in checkpoint.capability_calls if item.call_id == call.id)
        self.assertEqual(state.output_artifact_id, result.id)
        self.assertEqual(state.result_message_digest, messages[-1].content_digest)
        event = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .one()
        )
        self.assertEqual(event.event_name, "capability_call.settled")

    def test_settlement_accepts_captured_failure_and_commits_attempt(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        result = _normalized_result_artifact(self.db, call, status="failed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="failed",
            result_artifact=result,
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="failed",
                result_artifact_id=result.id,
                evidence_digest=evidence_digest,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(attempt)
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.output_artifact_id, result.id)
        self.assertEqual(call.failure_code, "provider_failed")
        self.assertEqual(attempt.status, "committed")

    def test_settlement_closes_unstarted_pending_sibling_transcript(self) -> None:
        from tests.test_capability_call_reconciliation import (
            _add_pending_sibling_continuation,
        )
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.provider_loop.messages import (
            ProviderToolMessage,
            validate_provider_transcript,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call
        )
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="succeeded",
                result_artifact_id=result.id,
                evidence_digest=digest,
            )
        )
        self.db.commit()
        self.db.refresh(run)
        self.db.refresh(sibling)
        self.assertEqual(sibling.status, "cancelled")
        _ordinal, _digest, transcript = _current_transcript_digest(self.db, run.id)
        validate_provider_transcript(transcript)
        tools = [item for item in transcript if isinstance(item, ProviderToolMessage)]
        self.assertEqual([item.content.status for item in tools], [
            "completed",
            "cancelled_before_start",
        ])
        checkpoint_row = self.db.get(
            AssistantRunCheckpoint, run.current_checkpoint_id
        )
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        self.assertEqual(checkpoint.phase, "terminal")
        self.assertIsNone(checkpoint.provider_loop_continuation)

    def test_settlement_closes_reserved_sibling_without_continuation(self) -> None:
        from tests.test_capability_call_reconciliation import (
            _add_pending_sibling_continuation,
        )
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.provider_loop.messages import validate_provider_transcript

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call, with_continuation=False
        )
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        digest = compute_settlement_evidence_digest(
            attempt=attempt, outcome="succeeded", result_artifact=result
        )

        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="succeeded",
                result_artifact_id=result.id,
                evidence_digest=digest,
            )
        )
        self.db.commit()

        self.db.refresh(sibling)
        self.assertEqual(sibling.status, "cancelled")
        _ordinal, _digest, transcript = _current_transcript_digest(self.db, run.id)
        validate_provider_transcript(transcript)

    def test_settlement_rejects_stale_target_checkpoint_state(self) -> None:
        from app.assistant.capability_calls.repository import (
            CapabilityCallConflict,
            CapabilityCallRepository,
        )
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            decode_checkpoint,
            encode_checkpoint_v3,
        )
        from app.assistant.durable.models import AssistantRunCheckpoint

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        checkpoint = decode_checkpoint(row.state_payload)
        stale = checkpoint.model_copy(
            update={
                "capability_calls": tuple(
                    item.model_copy(update={"status": "authorized"})
                    if item.call_id == call.id
                    else item
                    for item in checkpoint.capability_calls
                )
            }
        )
        row.state_payload = encode_checkpoint_v3(stale)
        row.state_digest = checkpoint_state_digest(stale)
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        digest = compute_settlement_evidence_digest(
            attempt=attempt, outcome="succeeded", result_artifact=result
        )

        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                    evidence_digest=digest,
                )
            )

    def test_settlement_preserves_already_denied_pending_sibling(self) -> None:
        from tests.test_capability_call_reconciliation import (
            _add_pending_sibling_continuation,
        )
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.provider_loop.messages import ProviderToolMessage, validate_provider_transcript

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call, sibling_status="denied"
        )
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        digest = compute_settlement_evidence_digest(
            attempt=attempt, outcome="succeeded", result_artifact=result
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="succeeded",
                result_artifact_id=result.id,
                evidence_digest=digest,
            )
        )
        self.db.commit()
        self.db.refresh(sibling)
        self.assertEqual(sibling.status, "denied")
        _ordinal, _digest, transcript = _current_transcript_digest(self.db, run.id)
        validate_provider_transcript(transcript)
        tools = [item for item in transcript if isinstance(item, ProviderToolMessage)]
        self.assertEqual(tools[-1].content.status, "cancelled_before_start")
        self.assertEqual(tools[-1].content.error.safe_code, "policy_denied")

    def test_settlement_accepts_failed_attempt_evidence(self) -> None:
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="failed",
            error_code="provider_failed",
        )
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="failed",
            result_artifact=None,
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="failed",
                evidence_digest=evidence_digest,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.assertEqual(call.status, "failed")
        self.assertIsNotNone(call.output_artifact_id)
        from app.assistant.durable.models import AssistantRunArtifact

        artifact = self.db.get(AssistantRunArtifact, call.output_artifact_id)
        self.assertEqual(artifact.kind, "capability_call_settlement_result")

    def test_settlement_classifies_dispatched_attempt_unknown_atomically(self) -> None:
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        original_obligation_id = run.current_obligation_revision_id
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        settled_run = CapabilityCallSettlementRepository(
            self.db
        ).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="unknown",
                evidence_digest=evidence_digest,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(attempt)
        self.assertEqual(settled_run.status, "needs_reconciliation")
        self.assertEqual(call.status, "needs_reconciliation")
        self.assertEqual(attempt.status, "uncertain")
        self.assertEqual(attempt.error_code, "settlement_outcome_unknown")
        self.assertEqual(settled_run.state_revision, 3)
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
        )
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
        )
        from app.assistant.models import AssistantChatRunEvent
        from app.assistant.policy.obligations import ObligationLedgerState

        obligation_row = self.db.get(
            AssistantRunObligationRevision, settled_run.current_obligation_revision_id
        )
        self.assertNotEqual(
            settled_run.current_obligation_revision_id, original_obligation_id
        )
        obligation_state = ObligationLedgerState.model_validate(obligation_row.payload)
        pending = [item for item in obligation_state.obligations if item.status == "pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].source_call_id, str(call.id))
        checkpoint_row = self.db.get(
            AssistantRunCheckpoint, settled_run.current_checkpoint_id
        )
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        state = next(item for item in checkpoint.capability_calls if item.call_id == call.id)
        self.assertEqual(state.status, "needs_reconciliation")
        self.assertEqual(state.attempt_id, attempt.id)
        CapabilityReconciliationService(self.db)._require_pending_context(
            run=settled_run,
            call=call,
            decision="mark_failed",
        )
        events = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .all()
        )
        self.assertEqual([event.event_name for event in events], ["capability_call.settled"])

    def test_settlement_rejects_unknown_without_attempt_effect_evidence(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        attempt.side_effect_started = False
        attempt.side_effect_started_at = None
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="unknown",
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_accepts_precommitted_success_evidence(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallRepository
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        result = _normalized_result_artifact(self.db, call, status="completed")
        repo = CapabilityCallRepository(self.db)
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="response_received",
            to_status="committed",
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="succeeded",
                result_artifact_id=result.id,
                evidence_digest=evidence_digest,
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(attempt)
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(attempt.status, "committed")

    def test_settlement_rejects_object_backed_result_without_trusted_reader(self) -> None:
        from app.assistant.capability_calls.repository import (
            CapabilityCallConflict,
            CapabilityCallRepository,
        )
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        from app.assistant.durable.models import AssistantRunArtifact

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        inline_result = _normalized_result_artifact(
            self.db, call, status="completed"
        )
        result = AssistantRunArtifact(
            run_id=run.id,
            kind=inline_result.kind,
            media_type=inline_result.media_type,
            storage_kind="object",
            byte_size=inline_result.byte_size,
            content_sha256=inline_result.content_sha256,
            object_key=f"runs/{run.id}/result.json",
            metadata_json=dict(inline_result.metadata_json),
        )
        self.db.delete(inline_result)
        self.db.flush()
        self.db.add(result)
        self.db.flush()
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_digest_binds_all_attempt_evidence(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        attempt.dispatch_deadline_at = datetime.now(timezone.utc)
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="unknown",
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_digest_binds_full_result_artifact(self) -> None:
        from app.assistant.capability_calls.repository import (
            CapabilityCallConflict,
            CapabilityCallRepository,
        )
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="dispatched"
        )
        result = _normalized_result_artifact(self.db, call, status="completed")
        attempt = CapabilityCallRepository(self.db).transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=result.content_sha256,
        )
        self.db.commit()
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="succeeded",
            result_artifact=result,
        )
        result.display_label = "mutated after evidence capture"
        result.metadata_json = {"contractVersion": 1, "mutated": True}
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                self._settle_request(
                    run=run,
                    call=call,
                    attempt_id=attempt.id,
                    outcome="succeeded",
                    result_artifact_id=result.id,
                    evidence_digest=evidence_digest,
                )
            )

    def test_settlement_digest_canonicalizes_database_datetimes(self) -> None:
        from app.assistant.capability_calls.settlement import (
            compute_settlement_evidence_digest,
        )

        _, _, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        naive_started_at = attempt.started_at.replace(tzinfo=None)
        attempt.started_at = naive_started_at
        naive_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        attempt.started_at = naive_started_at.replace(tzinfo=timezone.utc)
        aware_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        self.assertEqual(naive_digest, aware_digest)

    def test_settlement_rejects_invalid_outcome_at_runtime(self) -> None:
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )

        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="bogus",  # type: ignore[arg-type]
            result_artifact=None,
        )
        request = self._settle_request(
            run=run,
            call=call,
            attempt_id=attempt.id,
            outcome="bogus",
            evidence_digest=evidence_digest,
        )
        with self.assertRaises(CapabilityCallConflict):
            CapabilityCallSettlementRepository(self.db).settle_while_cancelling(
                request
            )

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

    def test_denied_replay_rejects_fixed_identity_drift(self) -> None:
        from dataclasses import replace

        from app.assistant.capability_calls.repository import (
            CODE_IDENTITY_MISMATCH,
            CapabilityCallConflict,
            CapabilityCallRepository,
        )

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="denied",
            lease=lease,
            failure_code="policy_denied",
        )
        self.db.commit()

        drifts = {
            "call_id": replace(spec, call_id=uuid.uuid4()),
            "owner": replace(spec, owner_kind="skill_version"),
            "idempotency": replace(spec, idempotency_key="forged-idempotency-key"),
            "decision": replace(spec, authorization_digest=DIGEST_A),
        }
        for label, replay_spec in drifts.items():
            with self.subTest(label=label):
                with self.assertRaises(CapabilityCallConflict) as ctx:
                    repo.create_or_verify_proposed(replay_spec)
                self.assertEqual(ctx.exception.code, CODE_IDENTITY_MISMATCH)
                self.db.rollback()

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

    def test_external_claim_persists_one_effect_start_boundary(self) -> None:
        from dataclasses import replace

        from app.assistant.capability_calls.repository import CapabilityCallRepository

        run, lease, spec = self._seed()
        spec = replace(
            spec,
            side_effect_class="write_external",
            execution_mode="external_idempotent",
        )
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=lease,
        )

        call, attempt = repo.claim_attempt(
            call_id=call.id,
            expected_call_revision=1,
            expected_run_revision=1,
            lease=lease,
            worker_id="worker-1",
            mark_side_effect_started=True,
        )

        self.assertIsNotNone(call.side_effect_started_at)
        self.assertTrue(attempt.side_effect_started)
        self.assertEqual(attempt.side_effect_started_at, call.side_effect_started_at)

    def test_replayable_claim_rejects_effect_start_boundary(self) -> None:
        from app.assistant.capability_calls.repository import (
            CODE_INVALID_TRANSITION,
            CapabilityCallConflict,
            CapabilityCallRepository,
        )

        run, lease, spec = self._seed()
        repo = CapabilityCallRepository(self.db)
        call, _ = repo.create_or_verify_proposed(spec)
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="authorized",
            lease=lease,
        )

        with self.assertRaises(CapabilityCallConflict) as ctx:
            repo.claim_attempt(
                call_id=call.id,
                expected_call_revision=1,
                expected_run_revision=1,
                lease=lease,
                worker_id="worker-1",
                mark_side_effect_started=True,
            )

        self.assertEqual(ctx.exception.code, CODE_INVALID_TRANSITION)

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
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            compute_settlement_evidence_digest,
        )
        run, call, attempt = self._seed_external_settlement(
            attempt_status="uncertain",
            error_code="transport_outcome_unknown",
        )
        evidence_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome="unknown",
            result_artifact=None,
        )
        settlement = CapabilityCallSettlementRepository(self.db)
        run2 = settlement.settle_while_cancelling(
            self._settle_request(
                run=run,
                call=call,
                attempt_id=attempt.id,
                outcome="unknown",
                evidence_digest=evidence_digest,
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
