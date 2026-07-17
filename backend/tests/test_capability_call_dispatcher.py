"""Plan 08 Task 4: LedgerDispatcher boundary tests."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@dataclass
class _FakeCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled


@dataclass
class _FakeProviderResult:
    capability_result: Any
    next_manifest: Any = None


@dataclass
class _FakeCapResult:
    status: str = "succeeded"


@dataclass
class _FakeInner:
    calls: list[Any] = field(default_factory=list)

    def dispatch(self, request: Any, *, cancellation: Any) -> Any:
        self.calls.append(request)
        return _FakeProviderResult(capability_result=_FakeCapResult(status="succeeded"))


def _make_run(db, *, mode: str = "enforced", revision: int = 1):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status="running",
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-test-1",
        capability_ledger_mode=mode,
        state_revision=revision,
        last_event_seq=0,
        memory_commit_status="pending",
        lease_owner="worker-1",
        lease_generation=1,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _manifest_and_artifact(db, run_id):
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunManifestRevision,
    )
    import hashlib
    import os

    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={"k": 1},
    )
    db.add(manifest)
    db.flush()
    payload = os.urandom(12)
    art = AssistantRunArtifact(
        run_id=run_id,
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
    return manifest, art


class SelectDispatcherTests(unittest.TestCase):
    def test_legacy_uses_compatibility(self) -> None:
        from app.assistant.capability_calls.dispatcher import select_dispatcher

        inner = _FakeInner()
        chosen = select_dispatcher(
            capability_ledger_mode="legacy_read_only",
            ledger_dispatcher=None,
            compatibility_dispatcher=inner,
        )
        self.assertIs(chosen, inner)

    def test_enforced_uses_ledger(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatcher,
            select_dispatcher,
        )
        from tests._db import make_session

        db = make_session()
        try:
            inner = _FakeInner()
            ledger = LedgerDispatcher(db=db, inner=inner)
            chosen = select_dispatcher(
                capability_ledger_mode="enforced",
                ledger_dispatcher=ledger,
                compatibility_dispatcher=inner,
            )
            self.assertIs(chosen, ledger)
        finally:
            db.close()


class LedgerDispatcherBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()
        self.inner = _FakeInner()

    def tearDown(self) -> None:
        self.db.close()

    def test_legacy_passthrough_zero_gateway_before_inner(self) -> None:
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher

        disp = LedgerDispatcher(db=self.db, inner=self.inner)
        result = disp.dispatch(object(), cancellation=_FakeCancellation())
        self.assertEqual(len(self.inner.calls), 1)
        self.assertIsNotNone(result)

    def test_enforced_read_proposes_claims_then_gateway(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatchRequest,
            LedgerDispatcher,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.durable.repository import LeaseToken

        run = _make_run(self.db, mode="enforced", revision=1)
        manifest, artifact = _manifest_and_artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        disp = LedgerDispatcher(db=self.db, inner=self.inner)
        ledger = LedgerDispatchRequest(
            provider_request=object(),
            run_id=run.id,
            capability_ledger_mode="enforced",
            expected_run_revision=1,
            lease=lease,
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="tc_read_1",
            authorization_digest=DIGEST_A,
            descriptor_digest=DIGEST_A,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            execution_mode="read_replayable",
            side_effect_class="read",
            domain_key="search_entries",
            manifest_revision_id=manifest.id,
            dispatch_disposition="dispatch",
            frozen_target_digest=DIGEST_B,
            idempotency_secret="s" * 32,
        )
        result = disp.dispatch_enforced(ledger, cancellation=_FakeCancellation())
        self.db.commit()
        self.assertEqual(len(self.inner.calls), 1)
        self.assertIsNotNone(result.call_id)
        row = (
            self.db.query(AssistantCapabilityCall)
            .filter(AssistantCapabilityCall.id == result.call_id)
            .one()
        )
        # succeeded after fake gateway success, or at least was executing
        self.assertIn(row.status, {"executing", "succeeded"})
        self.assertGreaterEqual(row.attempt_count, 1)

    def test_awaiting_approval_does_not_invoke_gateway(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatchRequest,
            LedgerDispatcher,
        )
        from app.assistant.durable.repository import LeaseToken

        run = _make_run(self.db, mode="enforced", revision=1)
        manifest, artifact = _manifest_and_artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        disp = LedgerDispatcher(db=self.db, inner=self.inner)
        ledger = LedgerDispatchRequest(
            provider_request=object(),
            run_id=run.id,
            capability_ledger_mode="enforced",
            expected_run_revision=1,
            lease=lease,
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="tc_write_1",
            authorization_digest=DIGEST_A,
            descriptor_digest=DIGEST_A,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            execution_mode="local_transactional",
            side_effect_class="write_local",
            domain_key="create_entry",
            manifest_revision_id=manifest.id,
            dispatch_disposition="awaiting_call_approval",
            frozen_target_digest=DIGEST_B,
            idempotency_secret="s" * 32,
            owner_kind="skill_version",
            owner_version_id=uuid.uuid4(),
        )
        result = disp.dispatch_enforced(ledger, cancellation=_FakeCancellation())
        self.db.commit()
        self.assertEqual(len(self.inner.calls), 0)
        self.assertEqual(result.call_status, "awaiting_approval")
        self.assertIsNotNone(result.pause_proposal)
        self.assertEqual(result.pause_proposal["contractVersion"], 1)

    def test_deny_disposition_no_gateway(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatchRequest,
            LedgerDispatcher,
        )
        from app.assistant.durable.repository import LeaseToken

        run = _make_run(self.db, mode="enforced", revision=1)
        manifest, artifact = _manifest_and_artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        disp = LedgerDispatcher(db=self.db, inner=self.inner)
        ledger = LedgerDispatchRequest(
            provider_request=object(),
            run_id=run.id,
            capability_ledger_mode="enforced",
            expected_run_revision=1,
            lease=lease,
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="tc_bad",
            authorization_digest=DIGEST_A,
            descriptor_digest=DIGEST_A,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            execution_mode="unsupported",
            side_effect_class="write_external",
            domain_key="update_entry",
            manifest_revision_id=manifest.id,
            dispatch_disposition="deny",
            frozen_target_digest=DIGEST_B,
            idempotency_secret="s" * 32,
        )
        result = disp.dispatch_enforced(ledger, cancellation=_FakeCancellation())
        self.db.commit()
        self.assertEqual(len(self.inner.calls), 0)
        self.assertTrue(result.denied)
        self.assertEqual(result.call_status, "denied")

    def test_cancel_before_gateway_no_side_effect(self) -> None:
        from app.assistant.capability_calls.dispatcher import (
            LedgerDispatchRequest,
            LedgerDispatcher,
        )
        from app.assistant.durable.repository import LeaseToken

        run = _make_run(self.db, mode="enforced", revision=1)
        manifest, artifact = _manifest_and_artifact(self.db, run.id)
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        disp = LedgerDispatcher(db=self.db, inner=self.inner)
        ledger = LedgerDispatchRequest(
            provider_request=object(),
            run_id=run.id,
            capability_ledger_mode="enforced",
            expected_run_revision=1,
            lease=lease,
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="tc_cancel",
            authorization_digest=DIGEST_A,
            descriptor_digest=DIGEST_A,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            execution_mode="read_replayable",
            side_effect_class="read",
            domain_key="search_entries",
            manifest_revision_id=manifest.id,
            dispatch_disposition="dispatch",
            frozen_target_digest=DIGEST_B,
            idempotency_secret="s" * 32,
        )
        result = disp.dispatch_enforced(
            ledger, cancellation=_FakeCancellation(cancelled=True)
        )
        self.assertEqual(len(self.inner.calls), 0)
        self.assertTrue(result.denied)


if __name__ == "__main__":
    unittest.main()
