from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class AssistantServiceUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.assistant.models import Conversation  # noqa: E402

        self.conv = Conversation(title=None)
        self.db.add(self.conv)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_chunk_text(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        self.assertEqual(list(svc._chunk_text("", chunk_size=2)), [])
        self.assertEqual(list(svc._chunk_text("abcd", chunk_size=2)), ["ab", "cd"])

    def test_sse_serializes_uuid_and_datetime(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        payload = {"id": UUID("00000000-0000-0000-0000-000000000001"), "t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        raw = svc._sse("evt", payload)
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("event: evt\n"))
        self.assertIn("data: ", text)
        data_line = text.splitlines()[1]
        data_json = data_line[len("data: ") :]
        parsed = json.loads(data_json)
        self.assertEqual(parsed["id"], "00000000-0000-0000-0000-000000000001")
        self.assertIn("2026-01-01", parsed["t"])

    def test_build_llm_messages_filters_and_keeps_last_20(self) -> None:
        from app.assistant.models import Message  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        # 25 messages, including invalid role and empty assistant message
        for i in range(12):
            self.db.add(Message(conversation_id=self.conv.id, role="user", content=f"u{i}"))
            self.db.add(Message(conversation_id=self.conv.id, role="assistant", content=f"a{i}"))
        self.db.add(Message(conversation_id=self.conv.id, role="tool", content="ignored"))
        self.db.add(Message(conversation_id=self.conv.id, role="assistant", content=""))  # should be skipped
        self.db.commit()

        svc = AssistantService(self.db)
        msgs = svc._build_llm_messages(self.conv.id)
        self.assertGreaterEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "system")

        # system + (last 20 messages filtered for invalid role/empty assistant)
        self.assertEqual(len(msgs), 19)
        roles = {m["role"] for m in msgs}
        self.assertTrue(roles.issubset({"system", "user", "assistant"}))

    def test_parse_openai_content(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        raw = {"choices": [{"message": {"content": "hi"}}]}
        self.assertEqual(svc._parse_openai_content(json.dumps(raw)), "hi")
        self.assertEqual(svc._parse_openai_content(None), "")

    def test_build_api_url(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        self.assertEqual(
            svc._build_api_url("https://api.example.com", "/chat/completions"),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(
            svc._build_api_url("https://api.example.com/v1", "/chat/completions"),
            "https://api.example.com/v1/chat/completions",
        )
