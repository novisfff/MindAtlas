from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

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


class _FailingAgent:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def stream(self, *args, **kwargs):
        raise RuntimeError("supervisor failed")


class AssistantServiceNoOuterFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402

        self.conv = Conversation(title="t")
        self.db.add(self.conv)
        self.db.commit()

        self.db.add(Message(conversation_id=self.conv.id, role="user", content="hello"))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_generate_response_does_not_call_outer_openai_fallback(self) -> None:
        _install_fastapi_stubs()
        from app.assistant.orchestration.openai_fallback_client import OpenAiFallbackConfig  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        cfg = OpenAiFallbackConfig(api_key="k", base_url="https://x", model="m")

        # Legacy AssistantAgent path is removed; generate_response fail-closes and
        # must not call the outer OpenAI stream fallback.
        with patch.object(svc, "_get_openai_config", return_value=cfg), patch.object(
            svc, "_openai_stream", side_effect=AssertionError("_openai_stream should not be called")
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Legacy AssistantAgent/Supervisor runtime is removed",
            ):
                list(svc._generate_response(self.conv.id))


if __name__ == "__main__":
    unittest.main()
