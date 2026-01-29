from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class SkillRouterPromptFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_router_prompt_format_does_not_raise(self) -> None:
        from app.assistant.skills.router import ROUTER_PROMPT  # noqa: E402
        from app.assistant.skills.base import DEFAULT_SKILL_NAME  # noqa: E402

        rendered = ROUTER_PROMPT.format(
            current_date="2026-01-01",
            skills_list="",
            default_skill_name=DEFAULT_SKILL_NAME,
        )
        self.assertIn(DEFAULT_SKILL_NAME, rendered)

