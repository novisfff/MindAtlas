"""Plan 08 Task 7: external uncertainty matrix + reconciliation service."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64


def _seed_external_call(db, *, mode: str = "external_idempotent", status: str = "needs_reconciliation"):
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunManifestRevision,
    )
    from app.assistant.models import AssistantChatRun, Conversation
    import hashlib
    import os

    conv = Conversation(title="t")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status="needs_reconciliation",
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="b1",
        capability_ledger_mode="enforced",
        state_revision=2,
        lease_owner="worker-1",
        lease_generation=1,
        memory_commit_status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={},
    )
    db.add(manifest)
    db.flush()
    payload = os.urandom(8)
    art = AssistantRunArtifact(
        run_id=run.id,
        kind="call_input",
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        inline_bytes=payload,
        metadata_json={},
    )
    db.add(art)
    db.flush()
    call = AssistantCapabilityCall(
        id=uuid.uuid4(),
        run_id=run.id,
        manifest_revision_id=manifest.id,
        logical_call_key=f"provider:0:0:{uuid.uuid4().hex[:8]}",
        owner_kind="main_agent",
        capability_type="tool",
        domain_key="external_write",
        descriptor_digest=DIGEST_A,
        authorization_digest=DIGEST_A,
        input_artifact_id=art.id,
        input_digest=DIGEST_A,
        side_effect_class="write_external",
        execution_mode=mode,
        idempotency_key="idem-" + uuid.uuid4().hex,
        status=status,
        state_revision=3,
        attempt_count=1,
        side_effect_started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return run, call, art


class ScriptedAdapterTests(unittest.TestCase):
    def test_scripted_outcomes_classify(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            ScriptedExternalAdapter,
            ScriptedExternalOutcome,
        )

        adapter = ScriptedExternalAdapter(
            [
                ScriptedExternalOutcome(kind="before_send_refusal"),
                ScriptedExternalOutcome(kind="accepted_then_timeout"),
                ScriptedExternalOutcome(kind="key_echo_success", echo_key="k"),
                ScriptedExternalOutcome(kind="ambiguous_5xx", status_code=503),
                ScriptedExternalOutcome(kind="non_retriable_uncertain"),
            ]
        )
        o1 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o1), "failed")
        o2 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o2), "unknown")
        o3 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o3), "succeeded")
        o4 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o4), "unknown")
        o5 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o5), "unknown")


class ReconciliationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_mark_failed_and_idempotent_resolution_request(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )

        run, call, art = _seed_external_call(self.db)
        svc = CapabilityReconciliationService(self.db)
        rid = uuid.uuid4()
        req = ReconciliationDecisionRequest(
            call_id=call.id,
            expected_call_revision=3,
            expected_run_revision=2,
            decision="mark_failed",
            reason="status lookup not_found",
            evidence_artifact_ids=(art.id,),
            resolution_request_id=rid,
            actor_admin_id=uuid.uuid4(),
        )
        r1 = svc.apply(req)
        self.db.commit()
        self.assertTrue(r1.created)
        self.assertEqual(r1.resulting_call_status, "failed")
        r2 = svc.apply(req)
        self.assertFalse(r2.created)
        self.assertEqual(r2.reconciliation_id, r1.reconciliation_id)

    def test_retry_same_key_forbidden_for_local_transactional(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        # Seed as external first (allows side_effect_started_at), then rewrite
        # mode/status to local_transactional without effect-start for the matrix.
        run, call, art = _seed_external_call(
            self.db, mode="external_idempotent", status="needs_reconciliation"
        )
        call.execution_mode = "local_transactional"
        call.side_effect_class = "write_local"
        call.side_effect_started_at = None
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(run)
        svc = CapabilityReconciliationService(self.db)
        with self.assertRaises(CapabilityCallConflict) as ctx:
            svc.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    decision="retry_same_key",
                    reason="should fail",
                    evidence_artifact_ids=(art.id,),
                    resolution_request_id=uuid.uuid4(),
                    actor_admin_id=uuid.uuid4(),
                )
            )
        self.assertIn("forbidden", ctx.exception.message)

    def test_external_idempotent_retry_same_key(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )

        run, call, art = _seed_external_call(
            self.db, mode="external_idempotent", status="needs_reconciliation"
        )
        svc = CapabilityReconciliationService(self.db)
        result = svc.apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="retry_same_key",
                reason="operator approved same-key retry",
                evidence_artifact_ids=(art.id,),
                resolution_request_id=uuid.uuid4(),
                actor_admin_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.assertEqual(result.resulting_call_status, "authorized")

    def test_external_reconcilable_requires_lookup_proof(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, art = _seed_external_call(
            self.db, mode="external_reconcilable", status="needs_reconciliation"
        )
        svc = CapabilityReconciliationService(self.db)
        with self.assertRaises(CapabilityCallConflict):
            svc.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="retry_same_key",
                    reason="missing lookup",
                    evidence_artifact_ids=(art.id,),
                    resolution_request_id=uuid.uuid4(),
                    actor_admin_id=uuid.uuid4(),
                    status_lookup_proved_not_accepted=False,
                )
            )
        result = svc.apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="retry_same_key",
                reason="lookup proved not_accepted",
                evidence_artifact_ids=(art.id,),
                resolution_request_id=uuid.uuid4(),
                actor_admin_id=uuid.uuid4(),
                status_lookup_proved_not_accepted=True,
            )
        )
        self.db.commit()
        self.assertEqual(result.resulting_call_status, "authorized")

    def test_cli_contract_surface(self) -> None:
        from app.assistant.capability_calls.cli import main

        code = main(["inspect", "--call-id", str(uuid.uuid4())])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
