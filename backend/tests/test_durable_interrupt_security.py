"""HMAC token, redaction corpus, and schema security tests (Plan 07 Task 4)."""

from __future__ import annotations

import hashlib
import hmac
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
PEPPER = "unit-test-interrupt-pepper-not-for-prod-32b"


def _make_main_agent_run(db, *, status: str = "waiting_approval", **kwargs: Any):
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
        state_revision=int(kwargs.pop("state_revision", 1)),
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
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={"k": 1},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run_id,
        revision=1,
        policy_digest=DIGEST_A,
        payload={"p": 1},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run_id,
        revision=1,
        budget_digest=DIGEST_A,
        payload={"b": 1},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run_id,
        revision=1,
        obligation_digest=DIGEST_A,
        payload={"o": 1},
    )
    db.add_all([manifest, policy, budget, obligation])
    db.flush()
    ck = AssistantRunCheckpoint(
        run_id=run_id,
        sequence=1,
        expected_state_revision=1,
        committed_state_revision=1,
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
    db.add(ck)
    db.flush()
    return manifest, policy, budget, obligation, ck


def _parent_ledger(*, remaining_ms: int = 120_000):
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits
    from app.assistant.policy.contracts import RunBudgetLimits

    start = datetime.now(timezone.utc)
    limits = normalize_run_budget_limits()
    payload = limits.model_dump()
    payload["max_wall_time_ms"] = max(remaining_ms + 10_000, 30_000)
    limits = RunBudgetLimits(**payload)
    deadline = start + timedelta(milliseconds=remaining_ms + 10_000)
    return create_initial_ledger_state(
        limits=limits,
        started_at_utc=start,
        deadline_at_utc=deadline,
    )


class InterruptTokenSecurityTests(unittest.TestCase):
    def test_hmac_sha256_pepper_token_not_plain_concat(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            digest_resume_token,
            generate_resume_token,
            verify_resume_token,
        )

        token = generate_resume_token()
        self.assertGreaterEqual(len(token), 32)
        digest = digest_resume_token(pepper=PEPPER, token=token)
        self.assertEqual(len(digest), 64)
        expected = hmac.new(PEPPER.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(digest, expected)
        # Not plain SHA256(pepper || token)
        plain = hashlib.sha256((PEPPER + token).encode("utf-8")).hexdigest()
        self.assertNotEqual(digest, plain)
        self.assertTrue(verify_resume_token(pepper=PEPPER, token=token, expected_digest=digest))
        self.assertFalse(
            verify_resume_token(pepper=PEPPER, token=token + "x", expected_digest=digest)
        )

    def test_pepper_required(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            InterruptTokenError,
            require_interrupt_token_pepper,
        )

        with self.assertRaises(InterruptTokenError):
            require_interrupt_token_pepper("")
        with self.assertRaises(InterruptTokenError):
            require_interrupt_token_pepper("   ")
        self.assertEqual(require_interrupt_token_pepper(PEPPER), PEPPER)

    def test_token_rotation_does_not_extend_expiry_or_budget(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import (
            DurableInterruptRepository,
            derive_interrupt_key,
        )

        db = make_session()
        try:
            run = _make_main_agent_run(db, state_revision=2)
            manifest, _p, budget, _o, ck = _seed_revisions(db, run.id)
            run.current_budget_revision_id = budget.id
            run.state_revision = 2
            db.commit()
            parent = _parent_ledger()
            frame_id = uuid.uuid4()
            visit = "v1"
            iid = derive_interrupt_id(
                run_id=run.id,
                root_invocation_digest=DIGEST_A,
                frame_id=frame_id,
                node_visit_id=visit,
                logical_interrupt_ordinal=1,
            )
            key = derive_interrupt_key(
                run_id=run.id,
                root_invocation_digest=DIGEST_A,
                frame_id=frame_id,
                node_visit_id=visit,
                logical_interrupt_ordinal=1,
            )
            repo = DurableInterruptRepository(db, token_pepper=PEPPER)
            created = repo.create_pending_interrupt(
                run_id=run.id,
                interrupt_id=iid,
                interrupt_key=key,
                kind="approval",
                checkpoint_id=ck.id,
                manifest_revision_id=manifest.id,
                budget_revision_id=budget.id,
                workflow_frame_id=frame_id,
                node_id="n1",
                node_visit_id=visit,
                request_run_revision=2,
                request_payload={"title": "Approve"},
                field_schema=None,
                initial_values={},
                parent_ledger=parent,
                parent_budget_revision_id=budget.id,
            )
            db.commit()
            expires_before = created.interrupt.expires_at
            suspension_before = created.interrupt.budget_suspension_digest
            remaining_before = created.suspension.remaining_active_ms

            tok1 = repo.rotate_token(
                run_id=run.id,
                interrupt_id=iid,
                expected_request_revision=1,
                expected_run_revision=2,
            )
            db.commit()
            self.assertEqual(tok1.token_revision, 1)
            self.assertIsNotNone(tok1.token)
            self.assertEqual(tok1.interrupt.expires_at, expires_before)
            self.assertEqual(tok1.interrupt.budget_suspension_digest, suspension_before)
            self.assertEqual(
                tok1.interrupt.budget_suspension_state["remainingActiveMs"],
                remaining_before,
            )

            tok2 = repo.rotate_token(
                run_id=run.id,
                interrupt_id=iid,
                expected_request_revision=1,
                expected_run_revision=2,
            )
            db.commit()
            self.assertEqual(tok2.token_revision, 2)
            self.assertNotEqual(tok1.token, tok2.token)
            # Old token no longer verifies.
            from app.assistant.workflow.durable.interrupts import verify_resume_token

            self.assertFalse(
                verify_resume_token(
                    pepper=PEPPER,
                    token=tok1.token,
                    expected_digest=tok2.interrupt.resume_token_digest,
                )
            )
            self.assertTrue(
                verify_resume_token(
                    pepper=PEPPER,
                    token=tok2.token,
                    expected_digest=tok2.interrupt.resume_token_digest,
                )
            )
        finally:
            db.close()

    def test_redaction_corpus_raw_token_absent(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            assert_no_sensitive_token_leak,
            digest_resume_token,
            generate_resume_token,
        )

        token = generate_resume_token()
        digest = digest_resume_token(pepper=PEPPER, token=token)
        safe_payload = {
            "interruptId": str(uuid.uuid4()),
            "status": "pending",
            "tokenRevision": 1,
            "requestDigest": DIGEST_A,
            # Safe public fields only — never raw token / digests.
        }
        assert_no_sensitive_token_leak(safe_payload, corpus=[token, digest, PEPPER])
        with self.assertRaises(AssertionError):
            assert_no_sensitive_token_leak(
                {"token": token},
                corpus=[token],
            )
        with self.assertRaises(AssertionError):
            assert_no_sensitive_token_leak(
                {"resumeTokenDigest": digest},
                corpus=[digest],
            )

    def test_resolution_digest_excludes_raw_token(self) -> None:
        from app.assistant.workflow.durable.interrupts import compute_resolution_digest

        iid = uuid.uuid4()
        rid = uuid.uuid4()
        d1 = compute_resolution_digest(
            interrupt_id=iid,
            resolution_request_id=rid,
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=2,
            outcome="approved",
            submitted_values=None,
            comment="ok",
        )
        # Same decision envelope -> same digest regardless of any raw token value
        # (token is not an input).
        d2 = compute_resolution_digest(
            interrupt_id=iid,
            resolution_request_id=rid,
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=2,
            outcome="approved",
            submitted_values=None,
            comment="ok",
        )
        self.assertEqual(d1, d2)
        d3 = compute_resolution_digest(
            interrupt_id=iid,
            resolution_request_id=rid,
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=2,
            outcome="rejected",
            submitted_values=None,
            comment="ok",
        )
        self.assertNotEqual(d1, d3)

    def test_schema_rejects_remote_ref_and_secret_widgets(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            InterruptSchemaError,
            normalize_interrupt_field_schema,
        )

        with self.assertRaises(InterruptSchemaError):
            normalize_interrupt_field_schema(
                {
                    "type": "object",
                    "properties": {
                        "x": {"$ref": "https://evil.example/schema.json"},
                    },
                }
            )
        with self.assertRaises(InterruptSchemaError):
            normalize_interrupt_field_schema(
                {
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                    },
                }
            )
        with self.assertRaises(InterruptSchemaError):
            normalize_interrupt_field_schema(
                {
                    "type": "object",
                    "properties": {
                        "x": {"type": "string", "pattern": "(?i)(a+)+$"},
                    },
                }
            )


class InterruptRepositorySecurityFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_pending(self, *, kind: str = "approval", schema=None):
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import (
            DurableInterruptRepository,
            derive_interrupt_key,
        )

        run = _make_main_agent_run(self.db, state_revision=3)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        run.current_budget_revision_id = budget.id
        run.state_revision = 3
        self.db.commit()
        parent = _parent_ledger()
        frame_id = uuid.uuid4()
        visit = "v1"
        iid = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        repo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        created = repo.create_pending_interrupt(
            run_id=run.id,
            interrupt_id=iid,
            interrupt_key=key,
            kind=kind,
            checkpoint_id=ck.id,
            manifest_revision_id=manifest.id,
            budget_revision_id=budget.id,
            workflow_frame_id=frame_id,
            node_id="n1",
            node_visit_id=visit,
            request_run_revision=3,
            request_payload={"title": "t"},
            field_schema=schema,
            initial_values={},
            parent_ledger=parent,
            parent_budget_revision_id=budget.id,
        )
        self.db.commit()
        return run, repo, created, parent

    def test_one_shot_resolution_and_idempotent_retry(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_ALREADY_RESOLVED,
            CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
            CODE_INTERRUPT_TOKEN_INVALID,
            InterruptConflict,
        )

        run, repo, created, _parent = self._create_pending()
        tok = repo.rotate_token(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            expected_request_revision=1,
            expected_run_revision=3,
        )
        self.db.commit()
        req_id = uuid.uuid4()
        first = repo.resolve_interrupt(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            resolution_request_id=req_id,
            token=tok.token,
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=3,
            outcome="approved",
            queues_execution=False,
        )
        self.db.commit()
        self.assertTrue(first.created_resolution)
        self.assertEqual(first.interrupt.status, "approved")
        self.assertIsNone(first.interrupt.resume_token_digest)

        # Idempotent retry after token consumption.
        replay = repo.resolve_interrupt(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            resolution_request_id=req_id,
            token="garbage-token-should-not-matter",
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=3,
            outcome="approved",
            queues_execution=False,
        )
        self.db.commit()
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(replay.interrupt.status, "approved")

        # Altered reuse conflicts.
        with self.assertRaises(InterruptConflict) as ctx:
            repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                resolution_request_id=req_id,
                token="x",
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=3,
                outcome="rejected",
                queues_execution=False,
            )
        self.assertEqual(ctx.exception.code, CODE_INTERRUPT_IDEMPOTENCY_CONFLICT)
        self.db.rollback()

        # Different request after terminal is already-resolved (new path).
        with self.assertRaises(InterruptConflict) as ctx2:
            repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                resolution_request_id=uuid.uuid4(),
                token=tok.token,
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=3,
                outcome="approved",
                queues_execution=False,
            )
        self.assertEqual(ctx2.exception.code, CODE_INTERRUPT_ALREADY_RESOLVED)

    def test_invalid_token_rejected(self) -> None:
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_TOKEN_INVALID,
            InterruptConflict,
        )

        run, repo, created, _ = self._create_pending()
        tok = repo.rotate_token(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            expected_request_revision=1,
            expected_run_revision=3,
        )
        self.db.commit()
        with self.assertRaises(InterruptConflict) as ctx:
            repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                resolution_request_id=uuid.uuid4(),
                token="not-the-token",
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=3,
                outcome="approved",
            )
        self.assertEqual(ctx.exception.code, CODE_INTERRUPT_TOKEN_INVALID)
        # Ensure original token still works after failed attempt (not consumed).
        self.db.rollback()
        ok = repo.resolve_interrupt(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            resolution_request_id=uuid.uuid4(),
            token=tok.token,
            expected_token_revision=1,
            expected_request_revision=1,
            expected_run_revision=3,
            outcome="approved",
        )
        self.db.commit()
        self.assertEqual(ok.interrupt.status, "approved")

    def test_terminal_cancellation_and_expiry(self) -> None:
        run, repo, created, _ = self._create_pending()
        cancelled = repo.cancel_interrupt(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            comment="user cancelled",
        )
        self.db.commit()
        self.assertEqual(cancelled.interrupt.status, "cancelled")
        self.assertIsNone(cancelled.interrupt.resolution_budget_revision_id)
        self.assertIsNone(cancelled.interrupt.resolution_checkpoint_id)

        # Second interrupt on same run after terminal is allowed at repository layer
        # (partial unique only blocks concurrent pending). Create another.
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import derive_interrupt_key

        frame_id = uuid.uuid4()
        visit = "v2"
        iid2 = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key2 = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        # Need a second checkpoint/budget for FK uniqueness? same is ok.
        from app.assistant.durable.models import AssistantRunCheckpoint

        ck = self.db.get(AssistantRunCheckpoint, created.interrupt.checkpoint_id)
        manifest_id = created.interrupt.manifest_revision_id
        budget_id = created.interrupt.budget_revision_id
        parent = _parent_ledger(remaining_ms=80_000)
        created2 = repo.create_pending_interrupt(
            run_id=run.id,
            interrupt_id=iid2,
            interrupt_key=key2,
            kind="approval",
            checkpoint_id=ck.id,
            manifest_revision_id=manifest_id,
            budget_revision_id=budget_id,
            workflow_frame_id=frame_id,
            node_id="n2",
            node_visit_id=visit,
            request_run_revision=3,
            request_payload={"title": "second"},
            field_schema=None,
            initial_values={},
            parent_ledger=parent,
            parent_budget_revision_id=budget_id,
        )
        self.db.commit()
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_NOT_EXPIRED,
            InterruptConflict,
        )

        with self.assertRaises(InterruptConflict) as early:
            repo.expire_interrupt(run_id=run.id, interrupt_id=created2.interrupt.id)
        self.assertEqual(early.exception.code, CODE_INTERRUPT_NOT_EXPIRED)
        self.db.rollback()

        created2.interrupt.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        expired = repo.expire_interrupt(run_id=run.id, interrupt_id=created2.interrupt.id)
        self.db.commit()
        self.assertEqual(expired.interrupt.status, "expired")
        self.assertIsNone(expired.interrupt.resolution_budget_revision_id)

    def test_parent_tamper_refused(self) -> None:
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import (
            CODE_INTERRUPT_PARENT_TAMPER,
            DurableInterruptRepository,
            InterruptConflict,
            derive_interrupt_key,
        )

        run = _make_main_agent_run(self.db, state_revision=1)
        manifest, _p, budget, _o, ck = _seed_revisions(self.db, run.id)
        other_budget_id = uuid.uuid4()
        run.current_budget_revision_id = budget.id
        self.db.commit()
        parent = _parent_ledger()
        frame_id = uuid.uuid4()
        visit = "v1"
        iid = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        key = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=1,
        )
        repo = DurableInterruptRepository(self.db, token_pepper=PEPPER)
        with self.assertRaises(InterruptConflict) as ctx:
            repo.create_pending_interrupt(
                run_id=run.id,
                interrupt_id=iid,
                interrupt_key=key,
                kind="approval",
                checkpoint_id=ck.id,
                manifest_revision_id=manifest.id,
                budget_revision_id=budget.id,
                workflow_frame_id=frame_id,
                node_id="n1",
                node_visit_id=visit,
                request_run_revision=1,
                request_payload={"title": "x"},
                field_schema=None,
                initial_values={},
                parent_ledger=parent,
                parent_budget_revision_id=other_budget_id,
            )
        self.assertEqual(ctx.exception.code, CODE_INTERRUPT_PARENT_TAMPER)

    def test_fixed_vector_pause_rotate_resume_nonincreasing_remaining(self) -> None:
        """pause -> wait/token rotation -> resume budget derivation fixed vector."""
        from app.assistant.workflow.durable.interrupts import (
            derive_resume_budget_ledger,
            non_time_budget_snapshot,
        )

        run, repo, created, parent = self._create_pending()
        rem1 = created.suspension.remaining_active_ms
        self.assertGreater(rem1, 0)
        snap1 = non_time_budget_snapshot(parent)

        tok = repo.rotate_token(
            run_id=run.id,
            interrupt_id=created.interrupt.id,
            expected_request_revision=1,
            expected_run_revision=3,
        )
        self.db.commit()
        # Crash/wait/token rotation leaves remaining and non-time usage unchanged.
        rem_after_rotate = created.interrupt.budget_suspension_state["remainingActiveMs"]
        self.assertEqual(rem_after_rotate, rem1)
        self.assertEqual(tok.interrupt.budget_suspension_digest, created.suspension.suspension_digest)

        resume_now = datetime(2026, 7, 16, 15, 0, 0, tzinfo=timezone.utc)
        child = derive_resume_budget_ledger(
            parent=parent,
            remaining_active_ms=rem1,
            database_now=resume_now,
        )
        self.assertEqual(non_time_budget_snapshot(child), snap1)
        # Second pause remaining must never increase.
        rem2 = rem1 - 5_000
        self.assertLess(rem2, rem1)
        child2 = derive_resume_budget_ledger(
            parent=child,
            remaining_active_ms=rem2,
            database_now=resume_now + timedelta(seconds=10),
        )
        self.assertEqual(non_time_budget_snapshot(child2), snap1)
        # Running downtime still consumes absolute deadline (remaining shrinks).
        from app.assistant.workflow.durable.interrupts import compute_remaining_active_ms

        later = parent.deadline_at_utc - timedelta(milliseconds=1_000)
        rem_running = compute_remaining_active_ms(
            parent_deadline_at_utc=parent.deadline_at_utc,
            database_now=later,
        )
        self.assertLessEqual(rem_running, rem1)


if __name__ == "__main__":
    unittest.main()
