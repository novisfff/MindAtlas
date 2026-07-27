from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


@unittest.skip('legacy SkillRouter removed (Plan 10 B2)')
class SkillRouterPromptFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_router_prompt_format_does_not_raise(self) -> None:
        from app.assistant.orchestration.intent_router import ROUTER_PROMPT  # noqa: E402
        from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME  # noqa: E402

        rendered = ROUTER_PROMPT.format(
            current_date="2026-01-01",
            skills_list="",
            default_skill_name=DEFAULT_SKILL_NAME,
            last_skill_hint="",
        )
        self.assertIn(DEFAULT_SKILL_NAME, rendered)
        self.assertIn('"skill"', rendered)
