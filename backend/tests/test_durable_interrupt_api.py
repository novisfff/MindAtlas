"""Conversation-scoped durable Interrupt token/decision APIs (Plan 07 Task 6).

Covers pending/detail/token/resolve under the conversation boundary, Run-lock-first
idempotency, lost-response retry, safe public shapes, queue-only HTTP resolution,
and the expiry scanner. No Workflow/Provider/Gateway construction on resolve.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.router import router as assistant_router  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402


DIGEST_A = "a" * 64
PEPPER = "task6-interrupt-api-pepper-not-for-prod-32bxx"
STATE_REVISION = 3


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def _seed_revisions(db, run_id, *, parent_ledger=None):
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    ledger = parent_ledger or _parent_ledger()
    ledger_payload = ledger.model_dump(mode="json", by_alias=True)

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
        budget_digest=str(ledger.ledger_digest),
        payload=ledger_payload,
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
        expected_state_revision=STATE_REVISION,
        committed_state_revision=STATE_REVISION,
        schema_version=2,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_A,
        phase="waiting",
        state_payload={"waiting": True, "schemaVersion": 2},
        state_digest=DIGEST_A,
    )
    db.add(ck)
    db.flush()
    return manifest, policy, budget, obligation, ck, ledger


def _make_waiting_run(db, *, status: str = "waiting_approval", state_revision: int = STATE_REVISION):
    from app.assistant.models import AssistantChatRun, Conversation, Message

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    msg = Message(conversation_id=conv.id, role="assistant", content="")
    db.add(msg)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        assistant_message_id=msg.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-test-1",
        state_revision=state_revision,
        last_event_seq=0,
        memory_commit_status="pending",
        deadline_at=None,
    )
    db.add(run)
    db.flush()
    return conv, msg, run


def _create_pending_interrupt(
    db,
    *,
    run,
    kind: str = "approval",
    schema: dict | None = None,
    expires_at: datetime | None = None,
    state_revision: int = STATE_REVISION,
):
    from app.assistant.workflow.durable.contracts import derive_interrupt_id
    from app.assistant.workflow.durable.interrupts import (
        DurableInterruptRepository,
        derive_interrupt_key,
    )

    parent = _parent_ledger()
    manifest, policy, budget, obligation, ck, ledger = _seed_revisions(
        db, run.id, parent_ledger=parent
    )
    run.current_manifest_revision_id = manifest.id
    run.current_policy_revision_id = policy.id
    run.current_budget_revision_id = budget.id
    run.current_obligation_revision_id = obligation.id
    run.current_checkpoint_id = ck.id
    run.state_revision = state_revision
    db.flush()

    frame_id = uuid.uuid4()
    visit = "visit-1"
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
        kind=kind,
        checkpoint_id=ck.id,
        manifest_revision_id=manifest.id,
        budget_revision_id=budget.id,
        workflow_frame_id=frame_id,
        node_id="n1",
        node_visit_id=visit,
        request_run_revision=state_revision,
        request_payload={"title": "Approve?", "body": "Please decide"},
        field_schema=schema,
        initial_values={},
        parent_ledger=ledger,
        parent_budget_revision_id=budget.id,
        expires_at=expires_at,
    )
    db.commit()
    db.refresh(run)
    db.refresh(created.interrupt)
    return created.interrupt, repo, ledger, budget, ck


class DurableInterruptApiTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(assistant_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.app = app
        self.client = TestClient(app)

        # Force durable interrupt settings for the service layer.
        self._settings_patch = patch(
            "app.assistant.workflow.durable.interrupt_api.get_settings",
            return_value=MagicMock(
                assistant_durable_interrupts_enabled=True,
                assistant_interrupt_token_pepper=PEPPER,
                assistant_interrupt_comment_max_chars=4000,
                assistant_interrupt_default_ttl_sec=86400,
                assistant_interrupt_max_ttl_sec=604800,
            ),
        )
        self._settings_patch.start()

    def tearDown(self) -> None:
        self._settings_patch.stop()
        self.db.close()

    def _base(self, conversation_id, run_id) -> str:
        return f"/api/assistant/conversations/{conversation_id}/runs/{run_id}/interrupts"

    def _reason(self, resp) -> str | None:
        body = resp.json()
        data = body.get("data") or {}
        if isinstance(data, dict):
            return data.get("reasonCode") or data.get("reason_code")
        return None

    def _seed_approval(self, **kwargs):
        conv, msg, run = _make_waiting_run(self.db)
        interrupt, _repo, ledger, budget, ck = _create_pending_interrupt(
            self.db, run=run, kind="approval", **kwargs
        )
        return conv, msg, run, interrupt, ledger, budget, ck

    def _seed_input(self, **kwargs):
        conv, msg, run = _make_waiting_run(self.db, status="waiting_input")
        schema = {
            "type": "object",
            "properties": {"note": {"type": "string", "title": "Note"}},
            "required": ["note"],
            "additionalProperties": False,
        }
        interrupt, _repo, ledger, budget, ck = _create_pending_interrupt(
            self.db, run=run, kind="input", schema=schema, **kwargs
        )
        return conv, msg, run, interrupt, ledger, budget, ck

    def _issue_token(self, conv, run, interrupt) -> dict:
        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/token",
            json={
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertIn("token", data)
        self.assertIn("tokenRevision", data)
        return data

    # ------------------------------------------------------------------
    # Routes / OpenAPI
    # ------------------------------------------------------------------

    def test_openapi_exposes_conversation_scoped_interrupt_paths(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        expected = {
            "/api/assistant/conversations/{id}/runs/{run_id}/interrupts/pending",
            "/api/assistant/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}",
            "/api/assistant/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}/token",
            "/api/assistant/conversations/{id}/runs/{run_id}/interrupts/{interrupt_id}/resolve",
        }
        for path in expected:
            self.assertIn(path, paths, msg=f"missing {path}")
        # No unscoped /api/assistant/runs/... interrupt routes
        for path in paths:
            if "interrupt" in path and path.startswith("/api/assistant/runs"):
                self.fail(f"unscoped interrupt route present: {path}")

    def test_legacy_approval_routes_still_present(self) -> None:
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        self.assertIn("/api/assistant/conversations/{id}/approvals/pending", paths)
        self.assertIn(
            "/api/assistant/conversations/{id}/approvals/{approval_id}/decision",
            paths,
        )

    # ------------------------------------------------------------------
    # Pending list / detail
    # ------------------------------------------------------------------

    def test_list_pending_and_detail_safe_shape(self) -> None:
        conv, msg, run, interrupt, *_ = self._seed_approval()
        resp = self.client.get(f"{self._base(conv.id, run.id)}/pending")
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()["data"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["interruptId"], str(interrupt.id))
        self.assertEqual(item["runId"], str(run.id))
        self.assertEqual(item["messageId"], str(msg.id))
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["kind"], "approval")
        self.assertIn("requestRevision", item)
        self.assertIn("runRevision", item)
        self.assertIn("tokenRevision", item)
        self.assertIn("expiresAt", item)
        self.assertIn("allowedActions", item)
        self.assertIn("fields", item)
        self.assertIn("requestPayload", item)
        # Never expose secrets / internal digests / values / comments.
        blob = resp.text
        for forbidden in (
            "resolutionDigest",
            "resolution_digest",
            "resumeTokenDigest",
            "resume_token_digest",
            "budgetSuspensionDigest",
            "budget_suspension_digest",
            "suspensionDigest",
            "tokenDigest",
        ):
            self.assertNotIn(forbidden, blob)
        self.assertNotIn("submittedValues", item)
        self.assertNotIn("comment", item)
        self.assertNotIn("resolutionRequestId", item)  # pending only

        detail = self.client.get(f"{self._base(conv.id, run.id)}/{interrupt.id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        d = detail.json()["data"]
        self.assertEqual(d["interruptId"], str(interrupt.id))
        self.assertNotIn("resolutionDigest", detail.text)

    def test_detail_not_found_and_conversation_mismatch(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        resp = self.client.get(f"{self._base(conv.id, run.id)}/{uuid.uuid4()}")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self._reason(resp), "durable_interrupt_not_found")

        from app.assistant.models import Conversation

        other_conv = Conversation(title="other")
        self.db.add(other_conv)
        self.db.commit()
        resp2 = self.client.get(f"{self._base(other_conv.id, run.id)}/{interrupt.id}")
        self.assertIn(resp2.status_code, (403, 404))
        self.assertEqual(self._reason(resp2), "durable_interrupt_conversation_mismatch")

    # ------------------------------------------------------------------
    # Token
    # ------------------------------------------------------------------

    def test_token_rotate_returns_raw_once_and_increments_revision(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        t1 = self._issue_token(conv, run, interrupt)
        self.assertEqual(t1["tokenRevision"], 1)
        t2 = self._issue_token(conv, run, interrupt)
        self.assertEqual(t2["tokenRevision"], 2)
        self.assertNotEqual(t1["token"], t2["token"])
        # Token endpoint response only exposes token + revision.
        self.assertEqual(set(t1.keys()), {"token", "tokenRevision"})

    def test_token_revision_mismatch_and_expired(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        bad = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/token",
            json={"expectedRequestRevision": 99, "expectedRunRevision": STATE_REVISION},
        )
        self.assertEqual(bad.status_code, 409)
        self.assertEqual(self._reason(bad), "interrupt_request_revision_mismatch")

        # Expired
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        conv2, _m2, run2, interrupt2, *_ = self._seed_approval(expires_at=past)
        # Force expires_at past after create (create may clamp).
        interrupt2.expires_at = past
        self.db.commit()
        exp = self.client.post(
            f"{self._base(conv2.id, run2.id)}/{interrupt2.id}/token",
            json={
                "expectedRequestRevision": interrupt2.request_revision,
                "expectedRunRevision": interrupt2.request_run_revision,
            },
        )
        self.assertEqual(exp.status_code, 409)
        self.assertEqual(self._reason(exp), "interrupt_expired")

    # ------------------------------------------------------------------
    # Resolve happy path + queue-only
    # ------------------------------------------------------------------

    def test_resolve_approval_queues_run_without_workflow_construction(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = str(uuid.uuid4())

        workflow_ctor = MagicMock(side_effect=AssertionError("Workflow constructed"))
        provider_ctor = MagicMock(side_effect=AssertionError("Provider constructed"))
        gateway_ctor = MagicMock(side_effect=AssertionError("Gateway constructed"))

        with (
            patch(
                "app.assistant.workflow.durable.interrupt_api.WorkflowEngine",
                workflow_ctor,
                create=True,
            ),
            patch(
                "app.assistant.workflow.durable.interrupt_api.ProviderClient",
                provider_ctor,
                create=True,
            ),
            patch(
                "app.assistant.workflow.durable.interrupt_api.CapabilityGateway",
                gateway_ctor,
                create=True,
            ),
            patch(
                "app.assistant.workflow.engine.WorkflowEngine",
                workflow_ctor,
                create=True,
            ),
        ):
            resp = self.client.post(
                f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
                json={
                    "token": tok["token"],
                    "resolutionRequestId": req_id,
                    "expectedTokenRevision": tok["tokenRevision"],
                    "expectedRequestRevision": interrupt.request_revision,
                    "expectedRunRevision": interrupt.request_run_revision,
                    "outcome": "approved",
                    "values": {},
                    "comment": None,
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual(data["status"], "approved")
        self.assertEqual(data["resolutionRequestId"], req_id)
        self.assertNotIn("resolutionDigest", resp.text)
        self.assertNotIn("submittedValues", data)
        self.assertNotIn("comment", data)

        self.db.refresh(run)
        self.assertEqual(run.status, "queued")
        self.assertIsNotNone(run.deadline_at)
        self.assertIsNotNone(run.current_checkpoint_id)

        from app.assistant.durable.models import AssistantRunInterrupt

        row = self.db.get(AssistantRunInterrupt, interrupt.id)
        self.assertEqual(row.status, "approved")
        self.assertEqual(str(row.resolution_request_id), req_id)
        self.assertIsNotNone(row.resolution_checkpoint_id)
        self.assertIsNotNone(row.resolution_budget_revision_id)
        self.assertIsNone(row.resume_token_digest)

        workflow_ctor.assert_not_called()
        provider_ctor.assert_not_called()
        gateway_ctor.assert_not_called()

        # Terminal detail exposes winning request id only.
        detail = self.client.get(f"{self._base(conv.id, run.id)}/{interrupt.id}")
        self.assertEqual(detail.status_code, 200)
        d = detail.json()["data"]
        self.assertEqual(d["resolutionRequestId"], req_id)
        self.assertNotIn("resolutionDigest", detail.text)
        self.assertNotIn("comment", d)
        self.assertNotIn("submittedValues", d)

    def test_resolve_input_submitted(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_input()
        tok = self._issue_token(conv, run, interrupt)
        req_id = str(uuid.uuid4())
        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": req_id,
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "submitted",
                "values": {"note": "hello"},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["data"]["status"], "submitted")
        self.db.refresh(run)
        self.assertEqual(run.status, "queued")

    # ------------------------------------------------------------------
    # Idempotency / lost response
    # ------------------------------------------------------------------

    def test_lost_response_retry_is_idempotent_no_second_queue(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = str(uuid.uuid4())
        body = {
            "token": tok["token"],
            "resolutionRequestId": req_id,
            "expectedTokenRevision": tok["tokenRevision"],
            "expectedRequestRevision": interrupt.request_revision,
            "expectedRunRevision": interrupt.request_run_revision,
            "outcome": "approved",
            "values": {},
        }
        first = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.db.refresh(run)
        ck_id = run.current_checkpoint_id
        budget_id = run.current_budget_revision_id
        rev = run.state_revision
        status = run.status

        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
        )
        from app.assistant.models import AssistantChatRunEvent

        ck_count = (
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.run_id == run.id)
            .count()
        )
        budget_count = (
            self.db.query(AssistantRunBudgetRevision)
            .filter(AssistantRunBudgetRevision.run_id == run.id)
            .count()
        )
        event_count = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .count()
        )

        # Lost-response retry: token already consumed; garbage token still OK.
        body2 = dict(body)
        body2["token"] = "garbage-after-consume-should-not-matter"
        second = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body2
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["data"]["resolutionRequestId"], req_id)
        self.assertEqual(second.json()["data"]["status"], "approved")

        self.db.refresh(run)
        self.assertEqual(run.status, status)
        self.assertEqual(run.state_revision, rev)
        self.assertEqual(run.current_checkpoint_id, ck_id)
        self.assertEqual(run.current_budget_revision_id, budget_id)

        ck_count2 = (
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.run_id == run.id)
            .count()
        )
        budget_count2 = (
            self.db.query(AssistantRunBudgetRevision)
            .filter(AssistantRunBudgetRevision.run_id == run.id)
            .count()
        )
        event_count2 = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .count()
        )
        self.assertEqual(ck_count2, ck_count)
        self.assertEqual(budget_count2, budget_count)
        self.assertEqual(event_count2, event_count)

    def test_altered_reuse_and_other_interrupt_conflict(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = str(uuid.uuid4())
        body = {
            "token": tok["token"],
            "resolutionRequestId": req_id,
            "expectedTokenRevision": tok["tokenRevision"],
            "expectedRequestRevision": interrupt.request_revision,
            "expectedRunRevision": interrupt.request_run_revision,
            "outcome": "approved",
            "values": {},
        }
        self.assertEqual(
            self.client.post(
                f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body
            ).status_code,
            200,
        )
        # Altered digest (different outcome) with same request id.
        body_bad = dict(body)
        body_bad["outcome"] = "rejected"
        body_bad["token"] = "x"
        conflict = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body_bad
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self._reason(conflict), "resolution_idempotency_conflict")

        # Different request after terminal → already resolved / not pending.
        body_new = dict(body)
        body_new["resolutionRequestId"] = str(uuid.uuid4())
        body_new["token"] = "x"
        already = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body_new
        )
        self.assertEqual(already.status_code, 409)
        self.assertIn(
            self._reason(already),
            {"interrupt_already_resolved", "interrupt_not_pending"},
        )

    def test_two_tabs_identify_winning_request_id(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        win = str(uuid.uuid4())
        lose = str(uuid.uuid4())
        body_win = {
            "token": tok["token"],
            "resolutionRequestId": win,
            "expectedTokenRevision": tok["tokenRevision"],
            "expectedRequestRevision": interrupt.request_revision,
            "expectedRunRevision": interrupt.request_run_revision,
            "outcome": "approved",
            "values": {},
        }
        r1 = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body_win
        )
        self.assertEqual(r1.status_code, 200)
        body_lose = dict(body_win)
        body_lose["resolutionRequestId"] = lose
        body_lose["token"] = "stale"
        r2 = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve", json=body_lose
        )
        self.assertEqual(r2.status_code, 409)
        detail = self.client.get(f"{self._base(conv.id, run.id)}/{interrupt.id}")
        self.assertEqual(detail.json()["data"]["resolutionRequestId"], win)

    # ------------------------------------------------------------------
    # Error codes
    # ------------------------------------------------------------------

    def test_invalid_token_and_stale_token_revision(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        bad = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": "not-the-token",
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(self._reason(bad), "interrupt_token_invalid")

        stale = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": 0,
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self._reason(stale), "interrupt_token_stale")

    def test_values_invalid_and_comment_bound(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_input()
        tok = self._issue_token(conv, run, interrupt)
        missing = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "submitted",
                "values": {},
            },
        )
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(self._reason(missing), "interrupt_values_invalid")

        # Re-issue token after failed attempt (token not consumed).
        tok2 = self._issue_token(conv, run, interrupt)
        huge = "x" * 5000
        too_long = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok2["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok2["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "submitted",
                "values": {"note": "ok"},
                "comment": huge,
            },
        )
        # Pydantic max_length may 422 first; service also rejects.
        self.assertIn(too_long.status_code, (409, 422))

    def test_run_cancelled_rejects_new_resolution(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        run.status = "cancelled"
        self.db.commit()
        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._reason(resp), "interrupt_run_cancelled")

    def test_auth_mode_unavailable_when_pepper_missing(self) -> None:
        self._settings_patch.stop()
        with patch(
            "app.assistant.workflow.durable.interrupt_api.get_settings",
            return_value=MagicMock(
                assistant_durable_interrupts_enabled=True,
                assistant_interrupt_token_pepper="",
                assistant_interrupt_comment_max_chars=4000,
                assistant_interrupt_default_ttl_sec=86400,
                assistant_interrupt_max_ttl_sec=604800,
            ),
        ):
            conv, _msg, run, interrupt, *_ = self._seed_approval()
            token_resp = self.client.post(
                f"{self._base(conv.id, run.id)}/{interrupt.id}/token",
                json={
                    "expectedRequestRevision": interrupt.request_revision,
                    "expectedRunRevision": interrupt.request_run_revision,
                },
            )
            self.assertEqual(token_resp.status_code, 503)
            self.assertEqual(
                self._reason(token_resp), "durable_interrupt_auth_mode_unavailable"
            )
            resolve_resp = self.client.post(
                f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
                json={
                    "token": "x",
                    "resolutionRequestId": str(uuid.uuid4()),
                    "expectedTokenRevision": 1,
                    "expectedRequestRevision": interrupt.request_revision,
                    "expectedRunRevision": interrupt.request_run_revision,
                    "outcome": "approved",
                },
            )
            self.assertEqual(resolve_resp.status_code, 503)
            self.assertEqual(
                self._reason(resolve_resp), "durable_interrupt_auth_mode_unavailable"
            )
        # Re-enable for remaining tests
        self._settings_patch = patch(
            "app.assistant.workflow.durable.interrupt_api.get_settings",
            return_value=MagicMock(
                assistant_durable_interrupts_enabled=True,
                assistant_interrupt_token_pepper=PEPPER,
                assistant_interrupt_comment_max_chars=4000,
                assistant_interrupt_default_ttl_sec=86400,
                assistant_interrupt_max_ttl_sec=604800,
            ),
        )
        self._settings_patch.start()

    def test_resolve_run_revision_mismatch(self) -> None:
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": STATE_REVISION + 99,
                "outcome": "approved",
            },
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._reason(resp), "interrupt_run_revision_mismatch")

    def test_resolve_expired_interrupt(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        # Mint token while still valid, then expire before resolve.
        tok = self._issue_token(conv, run, interrupt)
        interrupt.expires_at = past
        self.db.commit()
        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": str(uuid.uuid4()),
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._reason(resp), "interrupt_expired")

    def test_resolve_request_id_owned_by_other_interrupt(self) -> None:
        """Same resolutionRequestId already stored on another Interrupt → conflict."""
        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = str(uuid.uuid4())
        first = self.client.post(
            f"{self._base(conv.id, run.id)}/{interrupt.id}/resolve",
            json={
                "token": tok["token"],
                "resolutionRequestId": req_id,
                "expectedTokenRevision": tok["tokenRevision"],
                "expectedRequestRevision": interrupt.request_revision,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)

        # After first resolve, interrupt is terminal (not pending), so a second pending
        # row on the same run is allowed. Resolve it with the winning request id →
        # ownership conflict (request id belongs to the first interrupt).
        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.workflow.durable.contracts import derive_interrupt_id
        from app.assistant.workflow.durable.interrupts import derive_interrupt_key

        self.db.refresh(interrupt)
        frame_id = uuid.uuid4()
        visit = "visit-conflict-2"
        iid2 = derive_interrupt_id(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=2,
        )
        key2 = derive_interrupt_key(
            run_id=run.id,
            root_invocation_digest=DIGEST_A,
            frame_id=frame_id,
            node_visit_id=visit,
            logical_interrupt_ordinal=2,
        )
        row = AssistantRunInterrupt(
            id=iid2,
            run_id=run.id,
            interrupt_key=key2,
            kind="approval",
            status="pending",
            checkpoint_id=interrupt.checkpoint_id,
            manifest_revision_id=interrupt.manifest_revision_id,
            budget_revision_id=interrupt.budget_revision_id,
            workflow_frame_id=frame_id,
            node_id="n2",
            node_visit_id=visit,
            request_revision=1,
            request_run_revision=int(interrupt.request_run_revision),
            budget_suspension_state=dict(interrupt.budget_suspension_state or {}),
            budget_suspension_digest=str(interrupt.budget_suspension_digest),
            request_payload={"title": "other"},
            request_digest=DIGEST_A,
            initial_values={},
            token_revision=1,
            resume_token_digest="b" * 64,
            expires_at=interrupt.expires_at,
        )
        self.db.add(row)
        self.db.commit()

        resp = self.client.post(
            f"{self._base(conv.id, run.id)}/{iid2}/resolve",
            json={
                "token": "unused",
                "resolutionRequestId": req_id,
                "expectedTokenRevision": 1,
                "expectedRequestRevision": 1,
                "expectedRunRevision": interrupt.request_run_revision,
                "outcome": "approved",
            },
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(self._reason(resp), "resolution_idempotency_conflict")

    # ------------------------------------------------------------------
    # Expiry scanner
    # ------------------------------------------------------------------

    def test_expiry_scanner_expires_pending_through_cas(self) -> None:
        from app.assistant.workflow.durable.interrupt_api import scan_expired_interrupts

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        conv, _msg, run, interrupt, *_ = self._seed_approval(expires_at=past)
        interrupt.expires_at = past
        self.db.commit()

        result = scan_expired_interrupts(self.db, limit=10)
        self.assertGreaterEqual(result.expired_count, 1)
        self.db.refresh(interrupt)
        self.assertEqual(interrupt.status, "expired")
        self.assertIsNone(interrupt.resolution_budget_revision_id)
        self.assertIsNone(interrupt.resolution_checkpoint_id)
        self.db.refresh(run)
        # Terminal expiry cancels waiting run (no resume-ready child).
        self.assertEqual(run.status, "cancelled")

    def test_expiry_scanner_idempotent_on_already_terminal(self) -> None:
        from app.assistant.workflow.durable.interrupt_api import scan_expired_interrupts

        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        conv, _msg, run, interrupt, *_ = self._seed_approval(expires_at=past)
        interrupt.expires_at = past
        self.db.commit()
        first = scan_expired_interrupts(self.db, limit=10)
        second = scan_expired_interrupts(self.db, limit=10)
        self.assertGreaterEqual(first.expired_count, 1)
        # Second pass finds no pending expired (or no-ops).
        self.assertEqual(second.expired_count, 0)

    # ------------------------------------------------------------------
    # IntegrityError re-entry (created_resolution finish path)
    # ------------------------------------------------------------------

    def test_integrityerror_reentry_created_resolution_queues(self) -> None:
        """IntegrityError after interrupt mutation: re-entry with prepare finishes CAS.

        Simulates event/pointer unique race: first resolve mutates interrupt then
        CAS raises IntegrityError; rollback; re-enter takes first-resolution path
        again (with prepare_queued_children) and continues queue CAS successfully.
        """
        from sqlalchemy.exc import IntegrityError

        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunInterrupt,
        )
        from app.assistant.workflow.durable.interrupt_api import resolve_interrupt_http

        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = uuid.uuid4()

        real_commit = None
        calls = {"n": 0}

        from app.assistant.durable.repository import DurableRunRepository

        real_commit = DurableRunRepository.commit_resume_queued

        def _flaky_commit(self, *args, **kwargs):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError(
                    "simulated event/pointer unique race",
                    params=None,
                    orig=Exception("unique violation"),
                )
            return real_commit(self, *args, **kwargs)

        with patch.object(DurableRunRepository, "commit_resume_queued", _flaky_commit):
            payload = resolve_interrupt_http(
                self.db,
                conversation_id=conv.id,
                run_id=run.id,
                interrupt_id=interrupt.id,
                token=tok["token"],
                resolution_request_id=req_id,
                expected_token_revision=int(tok["tokenRevision"]),
                expected_request_revision=int(interrupt.request_revision),
                expected_run_revision=int(interrupt.request_run_revision),
                outcome="approved",
                values={},
            )

        self.assertEqual(payload["status"], "approved")
        self.assertEqual(payload["resolutionRequestId"], str(req_id))
        self.assertGreaterEqual(calls["n"], 2)

        self.db.refresh(run)
        self.assertEqual(run.status, "queued")
        self.assertIsNotNone(run.current_checkpoint_id)
        self.assertIsNotNone(run.current_budget_revision_id)
        self.assertIsNotNone(run.deadline_at)

        row = self.db.get(AssistantRunInterrupt, interrupt.id)
        self.assertEqual(row.status, "approved")
        self.assertEqual(row.resolution_request_id, req_id)
        self.assertIsNotNone(row.resolution_checkpoint_id)
        self.assertIsNotNone(row.resolution_budget_revision_id)
        self.assertIsNone(row.resume_token_digest)

        # Resume children exist (prepared on re-entry first-resolution path).
        self.assertGreaterEqual(
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.run_id == run.id)
            .count(),
            2,
        )
        self.assertGreaterEqual(
            self.db.query(AssistantRunBudgetRevision)
            .filter(AssistantRunBudgetRevision.run_id == run.id)
            .count(),
            2,
        )

    def test_integrityerror_reentry_created_resolution_cancelled(self) -> None:
        """IntegrityError on cancel CAS: re-entry created_resolution finishes cancel."""
        from sqlalchemy.exc import IntegrityError

        from app.assistant.durable.models import AssistantRunInterrupt
        from app.assistant.durable.repository import DurableRunRepository
        from app.assistant.workflow.durable.interrupt_api import resolve_interrupt_http

        conv, _msg, run, interrupt, *_ = self._seed_approval()
        tok = self._issue_token(conv, run, interrupt)
        req_id = uuid.uuid4()
        calls = {"n": 0}
        real_commit = DurableRunRepository.commit_waiting_terminal_cancel

        def _flaky_cancel(self, *args, **kwargs):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                raise IntegrityError(
                    "simulated cancel event unique race",
                    params=None,
                    orig=Exception("unique violation"),
                )
            return real_commit(self, *args, **kwargs)

        with patch.object(
            DurableRunRepository, "commit_waiting_terminal_cancel", _flaky_cancel
        ):
            payload = resolve_interrupt_http(
                self.db,
                conversation_id=conv.id,
                run_id=run.id,
                interrupt_id=interrupt.id,
                token=tok["token"],
                resolution_request_id=req_id,
                expected_token_revision=int(tok["tokenRevision"]),
                expected_request_revision=int(interrupt.request_revision),
                expected_run_revision=int(interrupt.request_run_revision),
                outcome="cancelled",
                values=None,
            )

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["resolutionRequestId"], str(req_id))
        self.assertGreaterEqual(calls["n"], 2)

        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        row = self.db.get(AssistantRunInterrupt, interrupt.id)
        self.assertEqual(row.status, "cancelled")
        self.assertEqual(row.resolution_request_id, req_id)
        self.assertIsNone(row.resolution_checkpoint_id)
        self.assertIsNone(row.resolution_budget_revision_id)


if __name__ == "__main__":
    unittest.main()
