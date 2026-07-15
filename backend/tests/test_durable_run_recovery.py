"""Plan 06 Task 5: recovery classification tests.

Covers:
- bounded recovery classification
- cancellation-only takeover for expired cancelling
- credential rotation/revision drift → no Provider I/O → needs_reconciliation
- same-logical-unit recovery reuses reservation/started; increments only attempt
- short-circuit after committed post-result Checkpoint
- recovery-count exhaustion
- build/codec mismatch → needs_reconciliation
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _make_main_agent_run(db, *, status: str = "queued", **kwargs: Any):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision=kwargs.pop("required_app_build_revision", "build-test-1"),
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _seed_revisions(db, run_id):
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={
            "model": {
                "credentialId": str(uuid.UUID(int=1)),
                "credentialRuntimeRevision": 3,
                "credentialConfigDigest": DIGEST_B,
            }
        },
    )
    policy = AssistantRunPolicyRevision(
        run_id=run_id,
        revision=1,
        policy_digest=DIGEST_A,
        payload={"policy": True},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run_id,
        revision=1,
        budget_digest=DIGEST_A,
        payload={"budget": True},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run_id,
        revision=1,
        obligation_digest=DIGEST_A,
        payload={"obligation": True},
    )
    db.add_all([manifest, policy, budget, obligation])
    db.flush()
    return manifest, policy, budget, obligation


def _make_checkpoint_payload(
    *,
    run_id: UUID,
    phase: str = "ready_for_provider",
    manifest_id: UUID,
    policy_id: UUID,
    budget_id: UUID,
    obligation_id: UUID,
    inflight: dict | None = None,
):
    """Minimal valid DurableAgentCheckpointV1 wire payload."""
    unit = inflight
    if unit is None and phase in {"ready_for_provider", "dispatching_calls"}:
        unit = {
            "logicalUnitId": "unit-1",
            "kind": "provider_round",
            "state": "prepared",
            "providerRound": 0,
            "callIds": [],
            "attempt": 1,
            "reservedBudgetRevision": 0,
            "startedBudgetRevision": None,
        }
    payload = {
        "schemaVersion": 1,
        "runId": str(run_id),
        "phase": phase,
        "manifestRevisionId": str(manifest_id),
        "policyRevisionId": str(policy_id),
        "budgetRevisionId": str(budget_id),
        "obligationRevisionId": str(obligation_id),
        "providerMessageOrdinal": 0,
        "providerTranscriptDigest": DIGEST_A,
        "providerLoopContinuation": None,
        "inflightUnit": unit,
        "capabilityFrames": [],
        "artifactIds": [],
        "visibleTextArtifactId": None,
        "nextAction": {"kind": "continue_provider", "reasonCode": None, "detail": None},
    }
    return payload


def _attach_checkpoint(db, run, *, phase: str = "ready_for_provider", inflight=None):
    from app.assistant.durable.codec import checkpoint_state_digest, decode_checkpoint
    from app.assistant.durable.models import AssistantRunCheckpoint

    manifest, policy, budget, obligation = _seed_revisions(db, run.id)
    payload = _make_checkpoint_payload(
        run_id=run.id,
        phase=phase,
        manifest_id=manifest.id,
        policy_id=policy.id,
        budget_id=budget.id,
        obligation_id=obligation.id,
        inflight=inflight,
    )
    decoded = decode_checkpoint(payload)
    digest = checkpoint_state_digest(decoded)
    ck = AssistantRunCheckpoint(
        run_id=run.id,
        sequence=1,
        expected_state_revision=int(run.state_revision),
        committed_state_revision=int(run.state_revision),
        schema_version=1,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_A,
        phase=phase,
        logical_unit_id=(inflight or {}).get("logicalUnitId") if inflight else None,
        reason=None,
        state_payload=payload,
        state_digest=digest,
    )
    db.add(ck)
    db.flush()
    run.current_checkpoint_id = ck.id
    run.current_manifest_revision_id = manifest.id
    run.current_policy_revision_id = policy.id
    run.current_budget_revision_id = budget.id
    run.current_obligation_revision_id = obligation.id
    db.commit()
    db.refresh(run)
    return ck, decoded


class FixedCredentialResolver:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls: list[UUID] = []

    def resolve_credential_snapshot(self, *, credential_id: UUID):
        self.calls.append(credential_id)
        return self.snapshot


class TrackingProviderIO:
    """Sentinel: recovery must never call this."""

    def __init__(self) -> None:
        self.provider_calls = 0
        self.gateway_calls = 0

    def call_provider(self) -> None:
        self.provider_calls += 1
        raise AssertionError("Provider I/O must not run during recovery classification")

    def call_gateway(self) -> None:
        self.gateway_calls += 1
        raise AssertionError("Gateway I/O must not run during recovery classification")


class RecoveryClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()
        self.io = TrackingProviderIO()

    def tearDown(self) -> None:
        self.db.close()

    def test_cancel_only_for_cancelling_takeover(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(self.db, status="cancelling", state_revision=2)
        clf = RecoveryClassifier(self.db)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_cancelling",
            worker_app_build_revision="build-test-1",
        )
        self.assertEqual(decision.kind, "cancel_only")
        self.assertFalse(decision.allow_provider_io)
        self.assertFalse(decision.allow_capability_io)
        self.assertEqual(self.io.provider_calls, 0)

    def test_build_mismatch_needs_reconciliation(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=1,
            required_app_build_revision="build-old",
        )
        clf = RecoveryClassifier(self.db)
        decision = clf.classify(
            run=run,
            claim_kind="takeover_running",
            worker_app_build_revision="build-new",
        )
        self.assertEqual(decision.kind, "needs_reconciliation")
        self.assertEqual(decision.reason_code, "build_revision_mismatch")
        self.assertFalse(decision.allow_provider_io)

    def test_credential_revision_drift_no_provider_io(self) -> None:
        from app.assistant.durable.recovery import (
            CredentialSnapshot,
            RecoveryClassifier,
        )

        run = _make_main_agent_run(self.db, status="recovering", state_revision=1)
        _attach_checkpoint(self.db, run, phase="ready_for_provider")
        self.db.refresh(run)

        # Live credential has a *newer* runtime revision (key-slot rotation).
        resolver = FixedCredentialResolver(
            CredentialSnapshot(
                credential_id=uuid.UUID(int=1),
                credential_runtime_revision=99,  # frozen was 3
                credential_config_digest=DIGEST_B,
            )
        )
        clf = RecoveryClassifier(self.db, credential_resolver=resolver)
        decision = clf.classify(
            run=run,
            claim_kind="takeover_running",
            worker_app_build_revision="build-test-1",
            worker_supported_codec_versions=(1,),
        )
        self.assertEqual(decision.kind, "needs_reconciliation")
        self.assertEqual(decision.reason_code, "credential_revision_drift")
        self.assertFalse(decision.allow_provider_io)
        self.assertFalse(decision.allow_capability_io)
        self.assertEqual(self.io.provider_calls, 0)
        self.assertEqual(self.io.gateway_calls, 0)
        # Resolver was consulted (classification path), but no Provider I/O.
        self.assertEqual(len(resolver.calls), 1)

    def test_same_logical_unit_reuses_reservation_increments_attempt(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(self.db, status="recovering", state_revision=2)
        inflight = {
            "logicalUnitId": "lu-provider-7",
            "kind": "provider_round",
            "state": "started",
            "providerRound": 2,
            "callIds": [],
            "attempt": 1,
            "reservedBudgetRevision": 4,
            "startedBudgetRevision": 4,
        }
        _attach_checkpoint(
            self.db, run, phase="ready_for_provider", inflight=inflight
        )
        self.db.refresh(run)

        # Credential matches frozen revision so classification continues.
        resolver = FixedCredentialResolver(
            __import__(
                "app.assistant.durable.recovery", fromlist=["CredentialSnapshot"]
            ).CredentialSnapshot(
                credential_id=uuid.UUID(int=1),
                credential_runtime_revision=3,
                credential_config_digest=DIGEST_B,
            )
        )
        clf = RecoveryClassifier(self.db, credential_resolver=resolver)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_recovering",
            worker_app_build_revision="build-test-1",
            worker_supported_codec_versions=(1,),
        )
        self.assertEqual(decision.kind, "reuse_unit")
        self.assertEqual(decision.reason_code, "same_logical_unit")
        assert decision.inflight_unit is not None
        assert decision.recovered_unit is not None
        # Reservation / started accounting preserved.
        self.assertEqual(
            decision.recovered_unit.reserved_budget_revision,
            decision.inflight_unit.reserved_budget_revision,
        )
        self.assertEqual(
            decision.recovered_unit.started_budget_revision,
            decision.inflight_unit.started_budget_revision,
        )
        self.assertEqual(
            decision.recovered_unit.logical_unit_id,
            decision.inflight_unit.logical_unit_id,
        )
        # Only attempt increments.
        self.assertEqual(decision.inflight_unit.attempt, 1)
        self.assertEqual(decision.recovered_unit.attempt, 2)
        self.assertTrue(decision.allow_provider_io)

    def test_short_circuit_after_post_result_checkpoint(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier, CredentialSnapshot

        run = _make_main_agent_run(self.db, status="recovering", state_revision=3)
        # ready_for_memory with no inflight = post-result already committed.
        _attach_checkpoint(
            self.db,
            run,
            phase="ready_for_memory",
            inflight=None,
        )
        self.db.refresh(run)

        resolver = FixedCredentialResolver(
            CredentialSnapshot(
                credential_id=uuid.UUID(int=1),
                credential_runtime_revision=3,
                credential_config_digest=DIGEST_B,
            )
        )
        clf = RecoveryClassifier(self.db, credential_resolver=resolver)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_recovering",
            worker_app_build_revision="build-test-1",
            worker_supported_codec_versions=(1,),
        )
        self.assertEqual(decision.kind, "short_circuit")
        self.assertEqual(decision.reason_code, "post_result_committed")
        self.assertTrue(decision.short_circuit_after_result)
        self.assertFalse(decision.allow_provider_io)
        self.assertFalse(decision.allow_capability_io)

    def test_unsupported_codec_needs_reconciliation(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(self.db, status="recovering", state_revision=1)
        ck, _ = _attach_checkpoint(self.db, run)
        # Force unsupported schema version on the row.
        ck.schema_version = 99
        self.db.commit()
        self.db.refresh(run)

        clf = RecoveryClassifier(self.db)
        decision = clf.classify(
            run=run,
            claim_kind="takeover_running",
            worker_app_build_revision="build-test-1",
            worker_supported_codec_versions=(1,),
        )
        self.assertEqual(decision.kind, "needs_reconciliation")
        self.assertEqual(decision.reason_code, "unsupported_checkpoint_codec")

    def test_recovery_exhausted_with_checkpoint_stays_uncertain(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=5,
            recovery_count=10,
        )
        _attach_checkpoint(self.db, run)
        self.db.refresh(run)

        clf = RecoveryClassifier(self.db, max_recovery_attempts=5)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_recovering",
            worker_app_build_revision="build-test-1",
        )
        self.assertEqual(decision.kind, "exhausted")
        self.assertEqual(decision.reason_code, "recovery_exhausted_uncertain")
        self.assertFalse(decision.allow_provider_io)

    def test_recovery_exhausted_without_checkpoint_fails(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=5,
            recovery_count=10,
        )
        clf = RecoveryClassifier(self.db, max_recovery_attempts=5)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_recovering",
            worker_app_build_revision="build-test-1",
        )
        self.assertEqual(decision.kind, "exhausted")
        self.assertEqual(decision.reason_code, "recovery_exhausted")

    def test_apply_cancel_only_finalizes(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier
        from app.assistant.durable.repository import (
            DurableRunRepository,
            LeaseToken,
        )
        from app.common.time import utcnow

        now = utcnow()
        run = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=2,
            lease_owner="w1",
            lease_generation=2,
            lease_expires_at=now + timedelta(seconds=30),
            heartbeat_at=now,
        )
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=2)
        clf = RecoveryClassifier(self.db)
        decision = clf.classify(
            run=run,
            claim_kind="reclaim_cancelling",
            worker_app_build_revision="build-test-1",
        )
        result = clf.apply_decision(
            run=run, lease=lease, decision=decision, expected_revision=2
        )
        assert result is not None
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.run.lease_owner)

    def test_apply_credential_drift_to_needs_reconciliation(self) -> None:
        from app.assistant.durable.recovery import (
            CredentialSnapshot,
            RecoveryClassifier,
        )
        from app.assistant.durable.repository import LeaseToken
        from app.common.time import utcnow

        now = utcnow()
        run = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=now + timedelta(seconds=30),
            heartbeat_at=now,
        )
        _attach_checkpoint(self.db, run)
        self.db.refresh(run)

        resolver = FixedCredentialResolver(
            CredentialSnapshot(
                credential_id=uuid.UUID(int=1),
                credential_runtime_revision=50,
                credential_config_digest=DIGEST_B,
            )
        )
        clf = RecoveryClassifier(self.db, credential_resolver=resolver)
        decision = clf.classify(
            run=run,
            claim_kind="takeover_running",
            worker_app_build_revision="build-test-1",
            worker_supported_codec_versions=(1,),
        )
        self.assertEqual(decision.kind, "needs_reconciliation")
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        result = clf.apply_decision(
            run=run, lease=lease, decision=decision, expected_revision=1
        )
        assert result is not None
        self.assertEqual(result.status, "needs_reconciliation")
        self.assertEqual(result.run.failure_code, "credential_revision_drift")

    def test_no_checkpoint_continue_for_queued_claim(self) -> None:
        from app.assistant.durable.recovery import RecoveryClassifier

        run = _make_main_agent_run(self.db, status="running", state_revision=1)
        clf = RecoveryClassifier(self.db)
        decision = clf.classify(
            run=run,
            claim_kind="queued",
            worker_app_build_revision="build-test-1",
        )
        self.assertEqual(decision.kind, "continue")
        self.assertEqual(decision.reason_code, "no_checkpoint")
        self.assertTrue(decision.allow_provider_io)

    def test_compute_retry_backoff_bounds(self) -> None:
        from app.assistant.durable.leases import compute_retry_backoff

        d0 = compute_retry_backoff(attempt=0, retry_base_ms=500, retry_max_ms=30000)
        d3 = compute_retry_backoff(attempt=3, retry_base_ms=500, retry_max_ms=30000)
        d20 = compute_retry_backoff(attempt=20, retry_base_ms=500, retry_max_ms=30000)
        self.assertEqual(d0.total_seconds() * 1000, 500)
        self.assertEqual(d3.total_seconds() * 1000, 4000)
        self.assertEqual(d20.total_seconds() * 1000, 30000)



class LeaseServiceSqliteUnitTests(unittest.TestCase):
    """Single-session SQLite coverage for claim/heartbeat/backoff/draining.

    Does NOT claim concurrency / SKIP LOCKED guarantees (see postgres suite).
    """

    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _identity(self, worker_id: str = "sqlite-lease-w"):
        from app.assistant.durable.worker_registry import WorkerIdentity

        return WorkerIdentity(
            worker_id=worker_id,
            app_build_revision="build-test-1",
            runtime_contract_version=1,
            supported_checkpoint_codec_versions=(1,),
            capability_feature_digest=DIGEST_A,
        )

    def _svc(self, worker_id: str = "sqlite-lease-w"):
        from app.assistant.durable.leases import RunLeaseService
        from datetime import timedelta

        return RunLeaseService(
            self.db,
            identity=self._identity(worker_id),
            lease_ttl=timedelta(seconds=30),
            retry_base_ms=500,
            retry_max_ms=30000,
        )

    def test_draining_skips_claim(self) -> None:
        _make_main_agent_run(self.db, status="queued")
        self.assertIsNone(self._svc().claim_next(draining=True))

    def test_claim_queued_and_heartbeat_no_revision_bump(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository, LeaseToken

        run = _make_main_agent_run(self.db, status="queued")
        svc = self._svc()
        claimed = svc.claim_next()
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.kind, "queued")
        self.assertEqual(claimed.status, "running")
        self.assertEqual(claimed.run_id, run.id)
        rev = claimed.state_revision
        self.assertTrue(svc.heartbeat(claimed.lease))
        run2 = DurableRunRepository(self.db).get_run(run.id)
        assert run2 is not None
        self.assertEqual(int(run2.state_revision), rev)
        # Zero-row lost lease
        lost = svc.heartbeat(
            LeaseToken(
                run_id=run.id,
                worker_id="sqlite-lease-w",
                lease_generation=999,
            )
        )
        self.assertFalse(lost)

    def test_build_mismatch_not_claimed_sqlite(self) -> None:
        _make_main_agent_run(
            self.db,
            status="queued",
            required_app_build_revision="other-build",
        )
        self.assertIsNone(self._svc().claim_next())

    def test_backoff_clears_lease_and_defers_claim(self) -> None:
        from app.assistant.durable.repository import DurableRunRepository
        from app.common.time import utcnow
        from datetime import timedelta

        run = _make_main_agent_run(self.db, status="queued")
        svc = self._svc()
        claimed = svc.claim_next()
        assert claimed is not None
        result = svc.schedule_backoff(
            lease=claimed.lease,
            expected_revision=claimed.state_revision,
            attempt=1,
        )
        self.assertIsNone(result.run.lease_owner)
        self.assertIsNotNone(result.run.next_attempt_at)
        self.assertIsNone(svc.claim_next())

        # Force due + expired recovering for reclaim
        run2 = DurableRunRepository(self.db).get_run(run.id)
        assert run2 is not None
        past = utcnow() - timedelta(seconds=5)
        run2.status = "recovering"
        run2.next_attempt_at = past
        run2.lease_expires_at = past
        self.db.commit()
        claimed2 = svc.claim_next()
        self.assertIsNotNone(claimed2)
        assert claimed2 is not None
        self.assertEqual(claimed2.kind, "reclaim_recovering")



if __name__ == "__main__":
    unittest.main()
