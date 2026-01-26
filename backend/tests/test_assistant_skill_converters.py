from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantSkillConvertersTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_db_skill_to_definition_maps_kb_config(self) -> None:
        from app.assistant.skills.converters import db_skill_to_definition  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "agent"
        skill.system_prompt = "x"
        skill.steps = []
        skill.kb_config = {"enabled": True, "useInAgent": True, "stepsSummaryDefault": True}

        definition = db_skill_to_definition(skill)
        self.assertIsNotNone(definition.kb)
        self.assertTrue(definition.kb.enabled)

    def test_db_skill_to_definition_light_maps_kb_config(self) -> None:
        from app.assistant.skills.converters import db_skill_to_definition_light  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "agent"
        skill.system_prompt = "x"
        skill.kb_config = {"enabled": True}

        definition = db_skill_to_definition_light(skill)
        self.assertIsNotNone(definition.kb)
        self.assertTrue(definition.kb.enabled)

    def test_db_skill_to_definition_ignores_invalid_kb_config(self) -> None:
        from app.assistant.skills.converters import db_skill_to_definition  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "agent"
        skill.system_prompt = "x"
        skill.steps = []
        skill.kb_config = "not-a-dict"

        definition = db_skill_to_definition(skill)
        self.assertIsNone(definition.kb)

