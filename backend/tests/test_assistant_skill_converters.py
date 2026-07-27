from __future__ import annotations
import pytest
pytestmark = pytest.mark.skip(reason="db skill converters removed with assistant_skill (Plan 10 B2)")


import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantSkillConvertersTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_db_skill_to_definition_maps_kb_config(self) -> None:
        from app.assistant.skill_catalog.converters import db_skill_to_definition  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "langgraph"
        skill.langgraph_pattern = "agent_loop"
        skill.system_prompt = "x"
        skill.kb_config = {"enabled": True}
        skill.nodes = []
        skill.edges = []

        definition = db_skill_to_definition(skill)
        self.assertIsNotNone(definition.kb)
        self.assertTrue(definition.kb.enabled)

    def test_db_skill_to_definition_light_maps_kb_config(self) -> None:
        from app.assistant.skill_catalog.converters import db_skill_to_definition_light  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "langgraph"
        skill.langgraph_pattern = "workflow_dag"
        skill.system_prompt = None
        skill.kb_config = {"enabled": True}

        definition = db_skill_to_definition_light(skill)
        self.assertIsNotNone(definition.kb)
        self.assertTrue(definition.kb.enabled)

    def test_db_skill_to_definition_ignores_invalid_kb_config(self) -> None:
        from app.assistant.skill_catalog.converters import db_skill_to_definition  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "general_chat"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "langgraph"
        skill.langgraph_pattern = "agent_loop"
        skill.system_prompt = "x"
        skill.kb_config = "not-a-dict"
        skill.nodes = []
        skill.edges = []

        definition = db_skill_to_definition(skill)
        self.assertIsNone(definition.kb)

    def test_db_skill_to_definition_rejects_legacy_mode(self) -> None:
        from app.assistant.skill_catalog.converters import db_skill_to_definition  # noqa: E402

        skill = type("Skill", (), {})()
        skill.name = "legacy"
        skill.description = "d"
        skill.intent_examples = []
        skill.tools = []
        skill.mode = "agent"
        skill.langgraph_pattern = "agent_loop"
        skill.system_prompt = "x"
        skill.kb_config = None
        skill.nodes = []
        skill.edges = []

        with self.assertRaises(ValueError):
            db_skill_to_definition(skill)


if __name__ == "__main__":
    unittest.main()
