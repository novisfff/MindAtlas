from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantAgentHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_agent_and_skills_pass_default_headers(self) -> None:
        captured: list[dict] = []

        class FakeChatOpenAI:
            def __init__(self, *args, **kwargs):
                captured.append(kwargs)

        with (
            patch("app.assistant.agent.ChatOpenAI", new=FakeChatOpenAI),
            patch("app.assistant.skills.executor.ChatOpenAI", new=FakeChatOpenAI),
            patch("langchain_openai.ChatOpenAI", new=FakeChatOpenAI),
        ):
            from app.assistant.agent import AssistantAgent  # noqa: E402

            AssistantAgent(
                api_key=" sk-test-key ",
                base_url=" https://api.example.com/v1 ",
                model="gpt-4o-mini",
                db=None,
            )

        self.assertEqual(len(captured), 4)
        for kwargs in captured:
            self.assertEqual(kwargs["api_key"], "sk-test-key")
            self.assertEqual(kwargs["base_url"], "https://api.example.com/v1")
            self.assertEqual(kwargs["default_headers"]["Accept"], "application/json")
            self.assertEqual(kwargs["default_headers"]["User-Agent"], "MindAtlas/1.0")
