from __future__ import annotations

import sys
import types
import unittest

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


class AssistantChatStopTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402

        self.conv = Conversation(title="stop")
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

    def test_stop_run_marks_cancelling_and_emits_status_event_once(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        _install_fastapi_stubs()
        from app.assistant.service import AssistantService  # noqa: E402

        run_svc = AssistantChatRunService(self.db)
        run = run_svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
        )
        svc = AssistantService(self.db)

        payload = svc.stop_run(conversation_id=self.conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelling")

        payload_2 = svc.stop_run(conversation_id=self.conv.id, run_id=run.id)
        self.assertEqual(payload_2["status"], "cancelling")

        events = run_svc.list_events_after(run_id=run.id, after_seq=0, limit=20)
        names = [item.event_name for item in events]
        self.assertEqual(names.count("run_status"), 1)


if __name__ == "__main__":
    unittest.main()
