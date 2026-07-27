from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

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


@unittest.skip("legacy Supervisor background-run summary path removed (Plan 10 B2)")
class AssistantServiceL1SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        _install_fastapi_stubs()
        self.db = make_session()
        self.session_factory = sessionmaker(bind=self.db.get_bind(), future=True)

    def tearDown(self) -> None:
        self.db.close()

    def _create_run(self):
        from app.assistant.models import Conversation, Message  # noqa: E402
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        conversation = Conversation(title="l1-run")
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        user_msg = Message(conversation_id=conversation.id, role="user", content="用户消息")
        assistant_msg = Message(conversation_id=conversation.id, role="assistant", content="")
        self.db.add(user_msg)
        self.db.add(assistant_msg)
        self.db.commit()
        self.db.refresh(user_msg)
        self.db.refresh(assistant_msg)

        run = AssistantChatRunService(self.db).create_run(
            conversation=conversation,
            user_message=user_msg,
            assistant_message=assistant_msg,
        )
        return conversation, user_msg, assistant_msg, run

    def test_background_run_success_updates_l1_summary(self) -> None:
        from app.assistant.models import AssistantConversationL1Memory  # noqa: E402
        from app.assistant.orchestration.openai_fallback_client import OpenAiFallbackConfig  # noqa: E402
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, _user_msg, _assistant_msg, run = self._create_run()
        service = AssistantService(self.db)
        cfg = OpenAiFallbackConfig(api_key="k", base_url="https://x", model="m")

        with patch("app.assistant.service.SessionLocal", self.session_factory), patch.object(
            service,
            "_generate_response",
            return_value=iter(["回答片段"]),
        ), patch.object(service, "_generate_title", return_value=None), patch.object(
            service,
            "_get_openai_config",
            return_value=cfg,
        ), patch.object(
            service._memory_computation_service,
            "compute_next_l1_summary",
            return_value=("会话摘要v1", "updated"),
        ):
            service._run_chat_background(run_id=run.id, stream_output=True)

        refreshed_run = AssistantChatRunService(self.db).get_run(conversation_id=conversation.id, run_id=run.id)
        self.assertIsNotNone(refreshed_run)
        assert refreshed_run is not None
        self.assertEqual(refreshed_run.status, "completed")

        l1_row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation.id)
            .first()
        )
        self.assertIsNotNone(l1_row)
        assert l1_row is not None
        self.assertEqual(l1_row.summary_text, "会话摘要v1")

        events = AssistantChatRunService(self.db).list_events_after(run_id=run.id, after_seq=0, limit=200)
        self.assertTrue(any(item.event_name == "message_end" for item in events))

    def test_background_run_summary_failure_is_fail_open(self) -> None:
        from app.assistant.models import AssistantConversationL1Memory  # noqa: E402
        from app.assistant.orchestration.openai_fallback_client import OpenAiFallbackConfig  # noqa: E402
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, _user_msg, _assistant_msg, run = self._create_run()
        service = AssistantService(self.db)
        cfg = OpenAiFallbackConfig(api_key="k", base_url="https://x", model="m")

        with patch("app.assistant.service.SessionLocal", self.session_factory), patch.object(
            service,
            "_generate_response",
            return_value=iter(["回答片段"]),
        ), patch.object(service, "_generate_title", return_value=None), patch.object(
            service,
            "_get_openai_config",
            return_value=cfg,
        ), patch.object(
            service._memory_computation_service,
            "compute_next_l1_summary",
            side_effect=RuntimeError("summary llm failed"),
        ):
            service._run_chat_background(run_id=run.id, stream_output=True)

        refreshed_run = AssistantChatRunService(self.db).get_run(conversation_id=conversation.id, run_id=run.id)
        self.assertIsNotNone(refreshed_run)
        assert refreshed_run is not None
        self.assertEqual(refreshed_run.status, "completed")

        l1_row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation.id)
            .first()
        )
        self.assertIsNone(l1_row)

    def test_cancelled_and_failed_paths_do_not_update_l1_summary(self) -> None:
        from app.assistant.models import AssistantConversationL1Memory  # noqa: E402
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        service = AssistantService(self.db)

        # cancelled path
        conversation_1, _user_msg_1, _assistant_msg_1, run_1 = self._create_run()
        AssistantChatRunService(self.db).request_stop(conversation_id=conversation_1.id, run_id=run_1.id)
        with patch("app.assistant.service.SessionLocal", self.session_factory), patch.object(
            service,
            "_generate_title",
            return_value=None,
        ):
            service._run_chat_background(run_id=run_1.id, stream_output=True)
        run_1_refreshed = AssistantChatRunService(self.db).get_run(conversation_id=conversation_1.id, run_id=run_1.id)
        self.assertIsNotNone(run_1_refreshed)
        assert run_1_refreshed is not None
        self.assertEqual(run_1_refreshed.status, "cancelled")

        l1_row_1 = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation_1.id)
            .first()
        )
        self.assertIsNone(l1_row_1)

        # failed path
        conversation_2, _user_msg_2, _assistant_msg_2, run_2 = self._create_run()
        with patch("app.assistant.service.SessionLocal", self.session_factory), patch.object(
            service,
            "_generate_response",
            side_effect=RuntimeError("runtime failed"),
        ), patch.object(
            service,
            "_generate_title",
            return_value=None,
        ):
            service._run_chat_background(run_id=run_2.id, stream_output=True)
        run_2_refreshed = AssistantChatRunService(self.db).get_run(conversation_id=conversation_2.id, run_id=run_2.id)
        self.assertIsNotNone(run_2_refreshed)
        assert run_2_refreshed is not None
        self.assertEqual(run_2_refreshed.status, "failed")

        l1_row_2 = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == conversation_2.id)
            .first()
        )
        self.assertIsNone(l1_row_2)
