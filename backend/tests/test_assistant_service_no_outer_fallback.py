from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()



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
