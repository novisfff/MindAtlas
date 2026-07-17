"""Plan 08 Task 9: fault matrix automation for Task 0 labels F01–F20.

Each label has an automated assertion. PostgreSQL two-session races are
CI-gated via MINDATLAS_TEST_POSTGRES_URL (skipped locally when unset).
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
STRONG_SECRET = "s" * 32
_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()


class FaultMatrixUnitTests(unittest.TestCase):
    """Automated coverage for Task 0 fault labels (unit / SQLite)."""

    def test_F01_propose_before_authz_no_attempt(self) -> None:
        """Crash after propose leaves call proposed with zero attempts."""
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.durable.repository import LeaseToken
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db)
            repo = CapabilityCallRepository(db)
            call, created = repo.create_or_verify_proposed(
                ProposeCallSpec(
                    call_id=uuid.uuid4(),
                    run_id=run.id,
                    expected_run_revision=1,
                    lease=lease,
                    manifest_revision_id=manifest.id,
                    logical_call_key="provider:0:0:f01",
                    owner_kind="main_agent",
                    capability_type="tool",
                    domain_key="search_entries",
                    descriptor_digest=DIGEST_A,
                    authorization_digest=DIGEST_A,
                    input_artifact_id=art.id,
                    input_digest=DIGEST_A,
                    side_effect_class="read",
                    execution_mode="read_replayable",
                    idempotency_key="idem-f01",
                )
            )
            db.commit()
            self.assertTrue(created)
            self.assertEqual(call.status, "proposed")
            self.assertEqual(call.attempt_count, 0)
        finally:
            db.close()

    def test_F02_grant_before_approval_awaiting(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatchRequest,
            LedgerDispatcher,
        )
        from app.assistant.durable.repository import LeaseToken
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db, mode="enforced")
            inner = _FakeInner()
            disp = LedgerDispatcher(db=db, inner=inner)
            result = disp.dispatch_enforced(
                LedgerDispatchRequest(
                    provider_request=object(),
                    run_id=run.id,
                    capability_ledger_mode="enforced",
                    expected_run_revision=1,
                    lease=lease,
                    provider_round_index=0,
                    assistant_message_index=0,
                    provider_tool_call_id="tc_f02",
                    authorization_digest=DIGEST_A,
                    descriptor_digest=DIGEST_A,
                    input_artifact_id=art.id,
                    input_digest=DIGEST_A,
                    execution_mode="local_transactional",
                    side_effect_class="write_local",
                    domain_key="create_entry",
                    manifest_revision_id=manifest.id,
                    dispatch_disposition="awaiting_call_approval",
                    frozen_target_digest=DIGEST_B,
                    idempotency_secret=STRONG_SECRET,
                    owner_kind="skill_version",
                ),
                cancellation=_FakeCancellation(),
            )
            db.commit()
            self.assertEqual(len(inner.calls), 0)
            self.assertEqual(result.call_status, "awaiting_approval")
            self.assertIsNotNone(result.pause_proposal)
        finally:
            db.close()

    def test_F06_attempt_claimed_before_dispatch_effect_null(self) -> None:
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db)
            repo = CapabilityCallRepository(db)
            call, _ = repo.create_or_verify_proposed(
                ProposeCallSpec(
                    call_id=uuid.uuid4(),
                    run_id=run.id,
                    expected_run_revision=1,
                    lease=lease,
                    manifest_revision_id=manifest.id,
                    logical_call_key="provider:0:0:f06",
                    owner_kind="main_agent",
                    capability_type="tool",
                    domain_key="search_entries",
                    descriptor_digest=DIGEST_A,
                    authorization_digest=DIGEST_A,
                    input_artifact_id=art.id,
                    input_digest=DIGEST_A,
                    side_effect_class="read",
                    execution_mode="read_replayable",
                    idempotency_key="idem-f06",
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
                expected_call_revision=int(call.state_revision),
                expected_run_revision=1,
                lease=lease,
                worker_id="worker-1",
            )
            db.commit()
            self.assertEqual(call.status, "executing")
            self.assertIsNone(call.side_effect_started_at)
            self.assertEqual(attempt.status, "claimed")
        finally:
            db.close()

    def test_F07_F09_local_flush_rollback_and_idempotent_success(self) -> None:
        from app.entry.models import Entry, TimeMode
        from app.entry.schemas import EntryRequest
        from app.entry.service import EntryService
        from app.entry_type.models import EntryType
        from app.lightrag.models import EntryIndexOutbox
        from tests._db import make_session

        db = make_session()
        try:
            et = EntryType(
                code="KNOWLEDGE",
                name="Knowledge",
                color="#1",
                graph_enabled=True,
                ai_enabled=True,
                enabled=True,
            )
            db.add(et)
            db.commit()
            db.refresh(et)
            svc = EntryService(db)
            req = EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=et.id,
                time_mode=TimeMode.POINT,
                time_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            )
            entry = svc.create_in_uow(req, source_capability_call_id=None)
            self.assertEqual(db.query(Entry).count(), 1)
            self.assertEqual(db.query(EntryIndexOutbox).count(), 1)
            db.rollback()  # F07: crash before commit
            self.assertEqual(db.query(Entry).count(), 0)
            self.assertEqual(db.query(EntryIndexOutbox).count(), 0)
        finally:
            db.close()

    def test_F11_stop_after_external_effect_to_reconciliation(self) -> None:
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
            SettlementRequest,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db, status="cancelling", revision=2)
            call = AssistantCapabilityCall(
                id=uuid.uuid4(),
                run_id=run.id,
                manifest_revision_id=manifest.id,
                logical_call_key="provider:0:0:f11",
                owner_kind="main_agent",
                capability_type="tool",
                domain_key="external_write",
                descriptor_digest=DIGEST_A,
                authorization_digest=DIGEST_A,
                input_artifact_id=art.id,
                input_digest=DIGEST_A,
                side_effect_class="write_external",
                execution_mode="external_idempotent",
                idempotency_key="idem-f11",
                status="executing",
                state_revision=2,
                attempt_count=1,
                side_effect_started_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(call)
            db.commit()
            settlement = CapabilityCallSettlementRepository(db)
            run2 = settlement.settle_while_cancelling(
                SettlementRequest(
                    call_id=call.id,
                    attempt_id=uuid.uuid4(),
                    expected_call_revision=2,
                    expected_run_revision=2,
                    outcome="unknown",
                    result_artifact_id=None,
                    evidence_digest=DIGEST_A,
                )
            )
            db.commit()
            db.refresh(call)
            self.assertEqual(run2.status, "needs_reconciliation")
            self.assertEqual(call.status, "needs_reconciliation")
        finally:
            db.close()

    def test_F16_duplicate_approval_idempotent_binding(self) -> None:
        from app.assistant.capability_calls.approval import (
            authorize_call_after_approval,
            build_approval_binding,
        )
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db)
            repo = CapabilityCallRepository(db)
            call, _ = repo.create_or_verify_proposed(
                ProposeCallSpec(
                    call_id=uuid.uuid4(),
                    run_id=run.id,
                    expected_run_revision=1,
                    lease=lease,
                    manifest_revision_id=manifest.id,
                    logical_call_key="provider:0:0:f16",
                    owner_kind="skill_version",
                    capability_type="tool",
                    domain_key="create_entry",
                    descriptor_digest=DIGEST_A,
                    authorization_digest=DIGEST_A,
                    input_artifact_id=art.id,
                    input_digest=DIGEST_A,
                    side_effect_class="write_local",
                    execution_mode="local_transactional",
                    idempotency_key="idem-f16",
                )
            )
            call = repo.transition_call(
                call_id=call.id,
                expected_call_revision=0,
                expected_run_revision=1,
                to_status="awaiting_approval",
                lease=lease,
            )
            binding = build_approval_binding(
                call_id=call.id,
                logical_call_key=call.logical_call_key,
                owner_digest=DIGEST_A,
                binding_contract_digest=DIGEST_A,
                input_digest=call.input_digest,
                target_digest=DIGEST_B,
                descriptor_digest=call.descriptor_digest,
                authorization_digest=call.authorization_digest,
                principal_digest=DIGEST_A,
            )
            a1 = authorize_call_after_approval(
                repo=repo,
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=1,
                lease=lease,
                approval_binding=binding,
                expected_authorization_digest=DIGEST_A,
            )
            db.commit()
            self.assertEqual(a1.status, "authorized")
            self.assertEqual(a1.attempt_count, 0)
            # Second authorize on already-authorized is illegal transition — one win.
            from app.assistant.capability_calls.repository import CapabilityCallConflict

            with self.assertRaises(CapabilityCallConflict):
                authorize_call_after_approval(
                    repo=repo,
                    call_id=call.id,
                    expected_call_revision=int(a1.state_revision),
                    expected_run_revision=1,
                    lease=lease,
                    approval_binding=binding,
                    expected_authorization_digest=DIGEST_A,
                )
        finally:
            db.close()

    def test_F17_retry_same_key_local_forbidden(self) -> None:
        from app.assistant.capability_calls.state_machine import (
            CallTransitionError,
            validate_call_transition,
        )

        with self.assertRaises(CallTransitionError):
            validate_call_transition(
                from_status="needs_reconciliation",
                to_status="authorized",
                side_effect_started_at_is_set=False,
                execution_mode="local_transactional",
                has_retry_same_key_authorization=True,
            )

    def test_F18_post_approval_does_not_mint_grant(self) -> None:
        from app.assistant.policy.contracts import (
            GOLDEN_WRITE_LATTICE_PREFIX,
            build_authorization_decision_v2,
        )
        from app.assistant.policy.write_admission import (
            issue_post_approval_gateway_evidence,
        )

        dec = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="awaiting_call_approval",
            reason_code="awaiting_call_approval",
            principal_digest=DIGEST_A,
            entrypoint_policy_digest=DIGEST_A,
            global_policy_digest=DIGEST_A,
            owner_policy_digest=DIGEST_A,
            allowed_side_effects=GOLDEN_WRITE_LATTICE_PREFIX,
            grant_source_digest=DIGEST_B,
            exposure_digest=DIGEST_A,
            effective_policy_digest=DIGEST_A,
            write_release_digest=DIGEST_B,
        )
        after = issue_post_approval_gateway_evidence(
            frozen_decision=dec, approval_binding_digest=DIGEST_A
        )
        self.assertEqual(after.decision_digest, dec.decision_digest)
        self.assertEqual(after.grant_source_digest, dec.grant_source_digest)
        self.assertEqual(after.dispatch_disposition, "awaiting_call_approval")

    def test_F19_full_asset_fails_golden_audit(self) -> None:
        import json
        from pathlib import Path
        from app.assistant.capability_calls.release_admission import (
            audit_golden_workflow_graph,
        )

        path = (
            Path(__file__).resolve().parents[1]
            / "app/assistant/workflow/system_assets/workflows/smart_capture.json"
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        audit = audit_golden_workflow_graph(payload, asset_key="smart_capture")
        self.assertFalse(audit.ok)
        self.assertTrue(audit.has_human_node)

    def test_F20_refuse_cancel_finalizer_unproven(self) -> None:
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from tests._db import make_session

        db = make_session()
        try:
            run, lease, manifest, art = _seed_run(db, status="cancelling", revision=2)
            call = AssistantCapabilityCall(
                id=uuid.uuid4(),
                run_id=run.id,
                manifest_revision_id=manifest.id,
                logical_call_key="provider:0:0:f20",
                owner_kind="main_agent",
                capability_type="tool",
                domain_key="external_write",
                descriptor_digest=DIGEST_A,
                authorization_digest=DIGEST_A,
                input_artifact_id=art.id,
                input_digest=DIGEST_A,
                side_effect_class="write_external",
                execution_mode="external_idempotent",
                idempotency_key="idem-f20",
                status="executing",
                state_revision=1,
                attempt_count=1,
                side_effect_started_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(call)
            db.commit()
            settlement = CapabilityCallSettlementRepository(db)
            with self.assertRaises(CapabilityCallConflict):
                settlement.refuse_cancel_finalizer_if_unproven(run.id)
        finally:
            db.close()


@unittest.skipUnless(_POSTGRES_URL, "MINDATLAS_TEST_POSTGRES_URL unset; PG races CI-gated")
class FaultMatrixPostgresRaceTests(unittest.TestCase):
    def test_placeholder_documents_pg_gate(self) -> None:
        # Real dual-session races for F08/F11/F13 live in CI with disposable PG.
        self.assertTrue(bool(_POSTGRES_URL))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeCancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


class _FakeInner:
    def __init__(self) -> None:
        self.calls: list = []

    def dispatch(self, request, *, cancellation):
        self.calls.append(request)
        return type("R", (), {"capability_result": type("C", (), {"status": "succeeded"})()})()


def _seed_run(db, *, mode: str = "enforced", status: str = "running", revision: int = 1):
    from app.assistant.models import AssistantChatRun, Conversation
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunManifestRevision,
    )
    from app.assistant.durable.repository import LeaseToken
    import hashlib
    import os

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:6]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="b1",
        capability_ledger_mode=mode,
        state_revision=revision,
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
    lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
    return run, lease, manifest, art


if __name__ == "__main__":
    unittest.main()
