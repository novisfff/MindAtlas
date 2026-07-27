"""Plan 06 Task 7 — database-driven SSE replay and stop.

Covers:
- Main Agent stream ignores attachment/background-thread bookkeeping for decisions
- Disconnect closes only the reader; the Run continues
- Cursor replay: older / equal / newer afterSeq, internal gaps, two readers,
  duplicate last event (uncertain client cursor)
- Cancellation while queued/running/recovering/cancelling and lease-expiry reclaim
- Force stop vs Provider/Capability/activation/ready_for_memory/memory/finalizer
  (unit CAS outcomes; PG two-session coverage lives in test_durable_run_events_postgres)
- Legacy event names/payloads when runtime_kind=legacy / Main Agent mode off
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _install_fastapi_stubs() -> None:
    if "fastapi" in sys.modules:
        return

    fastapi = types.ModuleType("fastapi")
    fastapi_exceptions = types.ModuleType("fastapi.exceptions")
    fastapi_responses = types.ModuleType("fastapi.responses")

    class FastAPI:  # pragma: no cover - test stub
        pass

    class RequestValidationError(Exception):  # pragma: no cover - test stub
        pass

    class JSONResponse:  # pragma: no cover - test stub
        def __init__(self, *args, **kwargs) -> None:
            pass

    fastapi.FastAPI = FastAPI
    fastapi_exceptions.RequestValidationError = RequestValidationError
    fastapi_responses.JSONResponse = JSONResponse
    sys.modules["fastapi"] = fastapi
    sys.modules["fastapi.exceptions"] = fastapi_exceptions
    sys.modules["fastapi.responses"] = fastapi_responses

    starlette_requests = types.ModuleType("starlette.requests")
    starlette_exceptions = types.ModuleType("starlette.exceptions")
    starlette_status = types.ModuleType("starlette.status")

    class Request:  # pragma: no cover - test stub
        pass

    class HTTPException(Exception):  # pragma: no cover - test stub
        def __init__(self, status_code: int = 500, detail: str | None = None) -> None:
            super().__init__(detail or "")
            self.status_code = status_code
            self.detail = detail

    starlette_requests.Request = Request
    starlette_exceptions.HTTPException = HTTPException
    starlette_status.HTTP_500_INTERNAL_SERVER_ERROR = 500
    sys.modules["starlette.requests"] = starlette_requests
    sys.modules["starlette.exceptions"] = starlette_exceptions
    sys.modules["starlette.status"] = starlette_status


def _decode_sse(raw: bytes) -> tuple[str, dict]:
    text = raw.decode("utf-8")
    lines = [line for line in text.splitlines() if line]
    event = lines[0].split("event: ", 1)[1]
    payload = json.loads(lines[1].split("data: ", 1)[1])
    return event, payload


def _make_main_agent_run(db, *, status: str = "queued", **kwargs: Any):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"stream-{uuid.uuid4().hex[:8]}")
    db.add(conv)
    db.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-stream-1",
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run, conv


def _append_event(
    db,
    *,
    run_id,
    seq: int,
    event_name: str,
    payload: dict | None = None,
    event_key: str | None = None,
    visibility: str = "public",
) -> None:
    from app.assistant.models import AssistantChatRun, AssistantChatRunEvent

    event = AssistantChatRunEvent(
        run_id=run_id,
        seq=seq,
        event_name=event_name,
        payload=payload if isinstance(payload, dict) else {},
        event_key=event_key,
        payload_version=1,
        visibility=visibility,
    )
    db.add(event)
    run = db.get(AssistantChatRun, run_id)
    assert run is not None
    run.last_event_seq = max(int(run.last_event_seq or 0), int(seq))
    db.commit()


class DurableRunStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()
        _install_fastapi_stubs()

    def tearDown(self) -> None:
        from app.assistant.service import AssistantService

        # Clear process-local Legacy bookkeeping between tests.
        with AssistantService._attached_run_stream_lock:
            AssistantService._attached_run_stream_ids.clear()
        with AssistantService._background_run_threads_lock:
            AssistantService._background_run_threads.clear()
        self.db.close()

    def _svc(self):
        from app.assistant.service import AssistantService

        return AssistantService(self.db)

    # ------------------------------------------------------------------
    # SSE replay by cursor
    # ------------------------------------------------------------------

    def test_stream_replays_public_events_with_event_key_and_skips_internal(self) -> None:
        from app.assistant.main_agent.events import mark_internal

        run, conv = _make_main_agent_run(self.db, status="running", state_revision=1)
        _append_event(
            self.db,
            run_id=run.id,
            seq=1,
            event_name="run_status",
            payload={"status": "running", "runId": str(run.id)},
            event_key=f"run.status:running:{run.id}",
        )
        _append_event(
            self.db,
            run_id=run.id,
            seq=2,
            event_name="main_agent_diagnostic",
            payload=mark_internal({"code": "diag"}),
            event_key=f"diag:1:{run.id}",
            visibility="internal",
        )
        _append_event(
            self.db,
            run_id=run.id,
            seq=3,
            event_name="content_delta",
            payload={"delta": "Hi", "runId": str(run.id)},
            event_key=f"content:1:{run.id}",
        )
        _append_event(
            self.db,
            run_id=run.id,
            seq=4,
            event_name="message_end",
            payload={"finishReason": "stop", "runId": str(run.id)},
            event_key=f"message.end:{run.id}",
        )
        run.status = "completed"
        self.db.commit()

        chunks = list(self._svc().stream_run(conv.id, run_id=run.id, after_seq=0))
        events = [_decode_sse(c) for c in chunks]
        names = [n for n, _ in events]
        self.assertEqual(names, ["run_status", "content_delta", "message_end"])
        self.assertEqual(events[0][1]["seq"], 1)
        self.assertEqual(events[0][1]["eventKey"], f"run.status:running:{run.id}")
        self.assertEqual(events[1][1]["seq"], 3)
        self.assertEqual(events[1][1]["eventKey"], f"content:1:{run.id}")
        self.assertNotIn("_visibility", events[0][1])
        # Internal row advanced cursor; no diagnostic yielded.
        self.assertTrue(all("diag" not in json.dumps(p) for _, p in events))

    def test_stream_cursor_older_equal_newer(self) -> None:
        run, conv = _make_main_agent_run(self.db, status="completed", state_revision=1)
        for seq, name, delta in (
            (1, "message_start", None),
            (2, "content_delta", "A"),
            (3, "content_delta", "B"),
            (4, "message_end", None),
        ):
            payload: dict[str, Any] = {"runId": str(run.id)}
            if delta is not None:
                payload["delta"] = delta
            if name == "message_end":
                payload["finishReason"] = "stop"
            _append_event(
                self.db,
                run_id=run.id,
                seq=seq,
                event_name=name,
                payload=payload,
                event_key=f"{name}:{seq}:{run.id}",
            )

        svc = self._svc()
        # older
        older = [_decode_sse(c) for c in svc.stream_run(conv.id, run_id=run.id, after_seq=1)]
        self.assertEqual([n for n, _ in older], ["content_delta", "content_delta", "message_end"])
        self.assertEqual([p["seq"] for _, p in older], [2, 3, 4])

        # equal — only seq > N
        equal = [_decode_sse(c) for c in svc.stream_run(conv.id, run_id=run.id, after_seq=4)]
        self.assertEqual(equal, [])

        # newer than last_event_seq — empty, clean exit
        newer = [_decode_sse(c) for c in svc.stream_run(conv.id, run_id=run.id, after_seq=99)]
        self.assertEqual(newer, [])

    def test_stream_internal_visibility_column_gap_advances_cursor(self) -> None:
        """Visibility column (not only payload marker) is filtered; cursor advances."""
        run, conv = _make_main_agent_run(self.db, status="completed", state_revision=1)
        _append_event(
            self.db,
            run_id=run.id,
            seq=1,
            event_name="run_status",
            payload={"status": "running"},
            event_key=f"s1:{run.id}",
        )
        # Internal by column only — no payload marker.
        _append_event(
            self.db,
            run_id=run.id,
            seq=2,
            event_name="authorization_decision",
            payload={"status": "allow"},
            event_key=f"auth:1:{run.id}",
            visibility="internal",
        )
        _append_event(
            self.db,
            run_id=run.id,
            seq=3,
            event_name="message_end",
            payload={"finishReason": "stop"},
            event_key=f"end:{run.id}",
        )

        events = [
            _decode_sse(c)
            for c in self._svc().stream_run(conv.id, run_id=run.id, after_seq=0)
        ]
        self.assertEqual([n for n, _ in events], ["run_status", "message_end"])
        self.assertEqual([p["seq"] for _, p in events], [1, 3])

    def test_two_concurrent_readers_see_same_committed_events(self) -> None:
        run, conv = _make_main_agent_run(self.db, status="running", state_revision=1)
        _append_event(
            self.db,
            run_id=run.id,
            seq=1,
            event_name="run_status",
            payload={"status": "running"},
            event_key=f"running:{run.id}",
        )

        results: list[list[tuple[str, dict]]] = [[], []]
        errors: list[Exception] = []
        barrier = threading.Barrier(2)
        run_id = run.id
        conversation_id = conv.id
        ReaderSession = sessionmaker(bind=self.db.get_bind(), future=True)

        def _reader(idx: int) -> None:
            from app.assistant.service import AssistantService

            try:
                barrier.wait(timeout=5)
                with ReaderSession() as reader_db:
                    chunks = list(
                        AssistantService(reader_db).stream_run(
                            conversation_id,
                            run_id=run_id,
                            after_seq=0,
                        )
                    )
                results[idx] = [_decode_sse(c) for c in chunks]
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t1 = threading.Thread(target=_reader, args=(0,), daemon=True)
        t2 = threading.Thread(target=_reader, args=(1,), daemon=True)
        t1.start()
        t2.start()
        time.sleep(0.15)

        LateSession = sessionmaker(bind=self.db.get_bind(), future=True)
        with LateSession() as late_db:
            _append_event(
                late_db,
                run_id=run.id,
                seq=2,
                event_name="content_delta",
                payload={"delta": "X"},
                event_key=f"delta:1:{run.id}",
            )
            _append_event(
                late_db,
                run_id=run.id,
                seq=3,
                event_name="message_end",
                payload={"finishReason": "stop"},
                event_key=f"end:{run.id}",
            )
            from app.assistant.models import AssistantChatRun

            r = late_db.get(AssistantChatRun, run.id)
            assert r is not None
            r.status = "completed"
            late_db.commit()

        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(errors, [])
        for events in results:
            self.assertEqual(
                [n for n, _ in events],
                ["run_status", "content_delta", "message_end"],
            )
            self.assertEqual([p["seq"] for _, p in events], [1, 2, 3])

    def test_disconnect_during_provider_work_does_not_cancel_run(self) -> None:
        """GeneratorExit on the reader must not change Run status (Run continues)."""
        run, conv = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=2,
            lease_owner="worker-1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        _append_event(
            self.db,
            run_id=run.id,
            seq=1,
            event_name="run_status",
            payload={"status": "running"},
            event_key=f"running:{run.id}",
        )

        gen = self._svc().stream_run(conv.id, run_id=run.id, after_seq=0)
        first = next(gen)
        name, payload = _decode_sse(first)
        self.assertEqual(name, "run_status")
        self.assertEqual(payload["seq"], 1)

        # Client disconnect
        gen.close()

        self.db.refresh(run)
        self.assertEqual(run.status, "running")
        self.assertIsNone(run.cancel_requested_at)
        self.assertEqual(run.lease_owner, "worker-1")

    def test_main_agent_approval_followup_ignores_stream_attachment(self) -> None:
        """Main Agent decisions must not consult stream-attachment/background-thread maps."""
        from app.assistant.service import AssistantService

        run, conv = _make_main_agent_run(self.db, status="running", state_revision=1)
        # Mark Legacy bookkeeping as if a stream were attached — Main Agent must ignore.
        AssistantService._mark_run_stream_attached(str(run.id))
        self.assertTrue(AssistantService._is_run_stream_attached(str(run.id)))

        # Active Main Agent Run: no disconnected followup message is written.
        svc = self._svc()
        before = self.db.query(
            __import__("app.assistant.models", fromlist=["Message"]).Message
        ).filter_by(conversation_id=conv.id).count()
        svc._ensure_disconnected_approval_followup(
            conversation_id=conv.id,
            approval_payload={
                "runId": str(run.id),
                "status": "approved",
                "messageId": None,
            },
        )
        after = self.db.query(
            __import__("app.assistant.models", fromlist=["Message"]).Message
        ).filter_by(conversation_id=conv.id).count()
        self.assertEqual(before, after)

        # Non-active Main Agent Run: legacy approval follow-up remains removed.
        run.status = "completed"
        self.db.commit()
        before = after
        svc._ensure_disconnected_approval_followup(
            conversation_id=conv.id,
            approval_payload={
                "runId": str(run.id),
                "status": "approved",
                "messageId": None,
            },
        )
        after = self.db.query(
            __import__("app.assistant.models", fromlist=["Message"]).Message
        ).filter_by(conversation_id=conv.id).count()
        self.assertEqual(after, before)

    def test_legacy_stream_preserves_event_names_without_forcing_event_key(self) -> None:
        from app.assistant.models import AssistantChatRun, Conversation, Message
        from app.assistant.run_service import AssistantChatRunService

        conv = Conversation(title="legacy-stream")
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add(user)
        self.db.add(assistant)
        self.db.commit()
        self.db.refresh(user)
        self.db.refresh(assistant)

        run_svc = AssistantChatRunService(self.db)
        run = run_svc.create_run(
            conversation=conv,
            user_message=user,
            assistant_message=assistant,
            runtime_kind="legacy",
        )
        run_svc.append_event(
            run_id=run.id,
            event_name="message_start",
            payload={"messageId": str(assistant.id), "runId": str(run.id)},
        )
        run_svc.append_event(
            run_id=run.id,
            event_name="content_delta",
            payload={"delta": "Hello"},
        )
        run_svc.append_event(
            run_id=run.id,
            event_name="message_end",
            payload={"finishReason": "stop"},
        )
        run_svc.update_run_status(run_id=run.id, status="completed")

        events = [
            _decode_sse(c)
            for c in self._svc().stream_run(conv.id, run_id=run.id, after_seq=0)
        ]
        self.assertEqual(
            [n for n, _ in events],
            ["message_start", "content_delta", "message_end"],
        )
        # Legacy rows have no event_key; payload must not invent one.
        for _, payload in events:
            self.assertNotIn("eventKey", payload)
            self.assertIn("seq", payload)

        refreshed = self.db.get(AssistantChatRun, run.id)
        assert refreshed is not None
        self.assertEqual(refreshed.runtime_kind, "legacy")

    # ------------------------------------------------------------------
    # Stop: service wiring + cancellation matrix
    # ------------------------------------------------------------------

    def test_stop_queued_main_agent_direct_cancel(self) -> None:
        run, conv = _make_main_agent_run(self.db, status="queued", state_revision=0)
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelled")
        self.assertIsNotNone(payload["cancelRequestedAt"])
        self.assertIsNotNone(payload["endedAt"])
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.state_revision, 1)

    def test_stop_running_main_agent_to_cancelling_with_event(self) -> None:
        run, conv = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=2,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelling")
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")
        self.assertEqual(run.state_revision, 3)
        self.assertEqual(run.lease_owner, "w1")  # lease remains responsible

        from app.assistant.models import AssistantChatRunEvent

        events = (
            self.db.query(AssistantChatRunEvent)
            .filter(AssistantChatRunEvent.run_id == run.id)
            .order_by(AssistantChatRunEvent.seq.asc())
            .all()
        )
        self.assertTrue(any(e.event_name == "run_status" for e in events))
        status_evt = next(e for e in events if e.event_name == "run_status")
        self.assertEqual(status_evt.payload.get("status"), "cancelling")
        self.assertTrue(str(status_evt.event_key or "").startswith("run.stop:"))

    def test_stop_recovering_to_cancelling(self) -> None:
        run, conv = _make_main_agent_run(
            self.db,
            status="recovering",
            state_revision=4,
            lease_owner="w2",
            lease_generation=2,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            recovery_count=1,
        )
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelling")
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")
        self.assertEqual(run.state_revision, 5)

    def test_stop_idempotent_while_cancelling(self) -> None:
        run, conv = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=5,
            cancel_requested_at=datetime.now(timezone.utc),
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        p1 = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        p2 = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(p1["status"], "cancelling")
        self.assertEqual(p2["status"], "cancelling")
        self.db.refresh(run)
        # Idempotent: no revision bump
        self.assertEqual(run.state_revision, 5)

    def test_stop_waiting_approval_direct_cancel(self) -> None:
        run, conv = _make_main_agent_run(
            self.db, status="waiting_approval", state_revision=3
        )
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelled")
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")

    def test_stop_waiting_approval_cancels_pending_durable_interrupt(self) -> None:
        from unittest.mock import MagicMock, patch

        run, conv = _make_main_agent_run(
            self.db, status="waiting_approval", state_revision=3
        )
        pending = MagicMock(id=uuid.uuid4())
        repo = MagicMock()
        repo.get_pending_for_run.return_value = pending

        with patch(
            "app.assistant.workflow.durable.interrupts.DurableInterruptRepository",
            return_value=repo,
        ):
            payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)

        self.assertEqual(payload["status"], "cancelled")
        repo.get_pending_for_run.assert_called_once_with(run.id, for_update=False)
        repo.cancel_interrupt.assert_called_once_with(
            run_id=run.id,
            interrupt_id=pending.id,
            comment="run stopped",
        )

    def test_stop_after_ready_for_memory_returns_run_finalizing(self) -> None:
        from app.assistant.durable.models import (
            AssistantRunBudgetRevision,
            AssistantRunCheckpoint,
            AssistantRunManifestRevision,
            AssistantRunObligationRevision,
            AssistantRunPolicyRevision,
        )
        from app.assistant.durable.repository import (
            DurableChildBundle,
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )
        from app.common.exceptions import ApiException

        run, conv = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=1,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id, revision=1, policy_digest=DIGEST_A, payload={}
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id, revision=1, budget_digest=DIGEST_A, payload={}
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id, revision=1, obligation_digest=DIGEST_A, payload={}
        )
        r0 = repo.commit_semantic(
            run_id=run.id,
            expected_revision=1,
            lease=lease,
            children=DurableChildBundle(rows=[manifest, policy, budget, obligation]),
        )
        self.db.refresh(manifest)
        self.db.refresh(policy)
        self.db.refresh(budget)
        self.db.refresh(obligation)
        ck = AssistantRunCheckpoint(
            run_id=run.id,
            sequence=1,
            expected_state_revision=r0.state_revision,
            committed_state_revision=r0.state_revision + 1,
            schema_version=1,
            manifest_revision_id=manifest.id,
            policy_revision_id=policy.id,
            budget_revision_id=budget.id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=0,
            provider_transcript_digest=DIGEST_A,
            phase="ready_for_memory",
            state_payload={"phase": "ready_for_memory"},
            state_digest=DIGEST_C,
        )
        r1 = repo.enter_ready_for_memory(
            run_id=run.id,
            expected_revision=r0.state_revision,
            lease=lease,
            events=[
                EventSpec(
                    event_key="memory.ready:1",
                    event_name="memory.ready",
                    payload={},
                    visibility="internal",
                )
            ],
            children=DurableChildBundle(rows=[ck]),
        )
        self.assertEqual(r1.status, "running")

        with self.assertRaises(ApiException) as ctx:
            self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("run_finalizing", str(ctx.exception.message))
        self.db.refresh(run)
        self.assertEqual(run.status, "running")

    def test_stop_terminal_is_idempotent_read(self) -> None:
        run, conv = _make_main_agent_run(
            self.db, status="completed", state_revision=7
        )
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "completed")
        self.db.refresh(run)
        self.assertEqual(run.state_revision, 7)

    def test_legacy_stop_still_marks_cancelling(self) -> None:
        """Preserve Legacy stop semantics (queued/running -> cancelling)."""
        from app.assistant.models import Conversation, Message
        from app.assistant.run_service import AssistantChatRunService

        conv = Conversation(title="legacy-stop")
        self.db.add(conv)
        self.db.commit()
        self.db.refresh(conv)
        user = Message(conversation_id=conv.id, role="user", content="hi")
        assistant = Message(conversation_id=conv.id, role="assistant", content="")
        self.db.add(user)
        self.db.add(assistant)
        self.db.commit()
        self.db.refresh(user)
        self.db.refresh(assistant)

        run = AssistantChatRunService(self.db).create_run(
            conversation=conv,
            user_message=user,
            assistant_message=assistant,
            runtime_kind="legacy",
        )
        payload = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelling")
        # Second stop still cancelling, single run_status event (Legacy path).
        payload2 = self._svc().stop_run(conversation_id=conv.id, run_id=run.id)
        self.assertEqual(payload2["status"], "cancelling")

    # ------------------------------------------------------------------
    # CAS unit outcomes: stop vs results / finalizers
    # ------------------------------------------------------------------

    def test_provider_result_never_overwrites_cancelling(self) -> None:
        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=4,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_running_result(
                run_id=run.id,
                expected_revision=4,
                lease=lease,
                target_status="completed",
                events=[
                    EventSpec(
                        event_key="provider.result:1",
                        event_name="run.completed",
                        payload={},
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_SOURCE_STATUS)
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")
        self.assertEqual(run.state_revision, 4)
        self.assertEqual(run.last_event_seq, 0)

    def test_capability_semantic_result_never_overwrites_cancelling(self) -> None:
        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=3,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=3,
                lease=lease,
                events=[
                    EventSpec(
                        event_key="capability.result:1",
                        event_name="tool_call_end",
                        payload={"status": "completed"},
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_SOURCE_STATUS)
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")

    def test_stop_then_activation_accept_rejected(self) -> None:
        """Activation accept is a semantic/running CAS; stop wins → accept fails."""
        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            CODE_STALE_REVISION,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=2,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        stop = repo.request_stop(
            run_id=run.id,
            expected_revision=2,
            events=[
                EventSpec(
                    event_key="run.stop:1",
                    event_name="run_status",
                    payload={"status": "cancelling"},
                )
            ],
        )
        self.assertEqual(stop.status, "cancelling")
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.commit_semantic(
                run_id=run.id,
                expected_revision=2,  # stale after stop
                lease=lease,
                events=[
                    EventSpec(
                        event_key="skill.accept:1",
                        event_name="skill_activation_end",
                        payload={"status": "success"},
                    )
                ],
            )
        self.assertIn(
            ctx.exception.code,
            {CODE_STALE_REVISION, CODE_INVALID_SOURCE_STATUS},
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")

    def test_duplicate_cancellation_finalizer_converges(self) -> None:
        from app.assistant.durable.repository import (
            CODE_STALE_REVISION,
            CODE_TERMINAL_IMMUTABLE,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=6,
            cancel_requested_at=datetime.now(timezone.utc),
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        first = repo.finalize_cancellation(
            run_id=run.id,
            expected_revision=6,
            lease=lease,
            require_lease=True,
            events=[
                EventSpec(
                    event_key="run.cancelled:1",
                    event_name="run_status",
                    payload={"status": "cancelled"},
                )
            ],
        )
        self.assertEqual(first.status, "cancelled")
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.finalize_cancellation(
                run_id=run.id,
                expected_revision=6,
                lease=lease,
                require_lease=True,
                events=[
                    EventSpec(
                        event_key="run.cancelled:2",
                        event_name="run_status",
                        payload={"status": "cancelled"},
                    )
                ],
            )
        self.assertIn(
            ctx.exception.code,
            {CODE_STALE_REVISION, CODE_TERMINAL_IMMUTABLE},
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.state_revision, 7)
        self.assertEqual(run.last_event_seq, 1)

    def test_lease_expiry_during_cancellation_reclaim_finalizes_only(self) -> None:
        from app.assistant.durable.repository import (
            DurableRunRepository,
            EventSpec,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="cancelling",
            state_revision=4,
            cancel_requested_at=datetime.now(timezone.utc),
            lease_owner="old-worker",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        repo = DurableRunRepository(self.db)
        reclaimed = repo.reclaim_expired_cancelling(
            run_id=run.id,
            expected_revision=4,
            worker_id="recovery-worker",
            lease_ttl=timedelta(seconds=30),
        )
        self.assertEqual(reclaimed.status, "cancelling")
        self.assertEqual(reclaimed.run.lease_owner, "recovery-worker")
        lease = LeaseToken(
            run_id=run.id,
            worker_id="recovery-worker",
            lease_generation=int(reclaimed.run.lease_generation),
        )
        final = repo.finalize_cancellation(
            run_id=run.id,
            expected_revision=reclaimed.state_revision,
            lease=lease,
            require_lease=True,
            events=[
                EventSpec(
                    event_key="run.cancelled:recovery",
                    event_name="run_status",
                    payload={"status": "cancelled"},
                )
            ],
        )
        self.assertEqual(final.status, "cancelled")
        self.db.refresh(run)
        self.assertIsNone(run.lease_owner)

    def test_memory_finalizer_after_stop_rejected(self) -> None:
        """Stop that wins before ready_for_memory prevents memory finalizer."""
        from app.assistant.durable.repository import (
            CODE_INVALID_SOURCE_STATUS,
            CODE_STALE_REVISION,
            DurableRunConflict,
            DurableRunRepository,
            LeaseToken,
        )

        run, _ = _make_main_agent_run(
            self.db,
            status="running",
            state_revision=2,
            lease_owner="w1",
            lease_generation=1,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = DurableRunRepository(self.db)
        lease = LeaseToken(run_id=run.id, worker_id="w1", lease_generation=1)
        stop = repo.request_stop(run_id=run.id, expected_revision=2)
        self.assertEqual(stop.status, "cancelling")
        with self.assertRaises(DurableRunConflict) as ctx:
            repo.finalize_memory(
                run_id=run.id,
                expected_revision=2,
                lease=lease,
                memory_commit_status="committed",
            )
        self.assertIn(
            ctx.exception.code,
            {CODE_STALE_REVISION, CODE_INVALID_SOURCE_STATUS, "run_finalizing"},
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "cancelling")


if __name__ == "__main__":
    unittest.main()
