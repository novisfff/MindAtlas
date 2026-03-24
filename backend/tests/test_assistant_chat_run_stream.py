from __future__ import annotations

import json
import sys
import threading
import time
import types
import unittest

from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


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


class AssistantChatRunStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402

        self.conv = Conversation(title="stream")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)

        self.user_msg = Message(conversation_id=self.conv.id, role="user", content="hello")
        self.assistant_msg = Message(conversation_id=self.conv.id, role="assistant", content="")
        self.db.add(self.user_msg)
        self.db.add(self.assistant_msg)
        self.db.commit()
        self.db.refresh(self.user_msg)
        self.db.refresh(self.assistant_msg)

    def tearDown(self) -> None:
        self.db.close()

    def test_stream_run_replays_events_and_exits_on_terminal(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        _install_fastapi_stubs()
        from app.assistant.service import AssistantService  # noqa: E402

        run_svc = AssistantChatRunService(self.db)
        run = run_svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
        )
        run_svc.append_event(run_id=run.id, event_name="message_start", payload={"messageId": str(self.assistant_msg.id), "runId": str(run.id)})
        run_svc.append_event(run_id=run.id, event_name="content_delta", payload={"delta": "A"})
        run_svc.append_event(run_id=run.id, event_name="message_end", payload={"finishReason": "stop"})
        run_svc.update_run_status(run_id=run.id, status="completed")

        svc = AssistantService(self.db)
        chunks = list(svc.stream_run(self.conv.id, run_id=run.id, after_seq=0))
        self.assertEqual(len(chunks), 3)
        events = [_decode_sse(chunk) for chunk in chunks]
        self.assertEqual([name for name, _ in events], ["message_start", "content_delta", "message_end"])
        self.assertEqual(events[0][1]["seq"], 1)
        self.assertEqual(events[1][1]["seq"], 2)
        self.assertEqual(events[2][1]["seq"], 3)

    def test_stream_run_after_seq_skips_history(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        _install_fastapi_stubs()
        from app.assistant.service import AssistantService  # noqa: E402

        run_svc = AssistantChatRunService(self.db)
        run = run_svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
        )
        run_svc.append_event(run_id=run.id, event_name="message_start", payload={"messageId": str(self.assistant_msg.id), "runId": str(run.id)})
        run_svc.append_event(run_id=run.id, event_name="content_delta", payload={"delta": "A"})
        run_svc.append_event(run_id=run.id, event_name="message_end", payload={"finishReason": "stop"})
        run_svc.update_run_status(run_id=run.id, status="completed")

        svc = AssistantService(self.db)
        chunks = list(svc.stream_run(self.conv.id, run_id=run.id, after_seq=1))
        self.assertEqual(len(chunks), 2)
        events = [_decode_sse(chunk) for chunk in chunks]
        self.assertEqual([name for name, _ in events], ["content_delta", "message_end"])
        self.assertEqual(events[0][1]["seq"], 2)

    def test_stream_run_observes_late_terminal_updates_from_other_session(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        _install_fastapi_stubs()
        from app.assistant.service import AssistantService  # noqa: E402

        run_svc = AssistantChatRunService(self.db)
        run = run_svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
        )
        run_svc.update_run_status(run_id=run.id, status="running")

        svc = AssistantService(self.db)
        chunks: list[bytes] = []
        errors: list[Exception] = []

        def _consume() -> None:
            try:
                chunks.extend(list(svc.stream_run(self.conv.id, run_id=run.id, after_seq=0)))
            except Exception as exc:  # pragma: no cover - assertion will fail
                errors.append(exc)

        thread = threading.Thread(target=_consume, daemon=True)
        thread.start()
        time.sleep(0.2)

        LateSession = sessionmaker(bind=self.db.get_bind(), future=True)
        with LateSession() as late_db:
            late_svc = AssistantChatRunService(late_db)
            late_svc.append_event(run_id=run.id, event_name="message_end", payload={"finishReason": "stop"})
            late_svc.update_run_status(run_id=run.id, status="completed")

        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "stream_run should exit after terminal update becomes visible")
        self.assertEqual(errors, [])
        events = [_decode_sse(chunk) for chunk in chunks]
        self.assertEqual([name for name, _ in events], ["message_end"])


if __name__ == "__main__":
    unittest.main()
