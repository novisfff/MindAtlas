from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantKnowledgeBaseToolsTests(unittest.TestCase):
    def test_tools_exported_and_registered(self) -> None:
        from app.assistant import tools as assistant_tools  # noqa: E402
        from app.assistant_config.registry import ToolRegistry  # noqa: E402

        self.assertIn("kb_search", getattr(assistant_tools, "__all__", []))
        self.assertIn("kb_relation_recommendations", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("kb_graph_recall", getattr(assistant_tools, "__all__", []))

        # Tool objects created by @tool should expose `name`.
        self.assertEqual(getattr(assistant_tools.kb_search, "name", None), "kb_search")
        self.assertEqual(
            getattr(assistant_tools.kb_relation_recommendations, "name", None),
            "kb_relation_recommendations",
        )
        self.assertIsNone(getattr(assistant_tools, "kb_graph_recall", None))

        names = {t.name for t in ToolRegistry.list_system_tools()}
        self.assertIn("kb_search", names)
        self.assertIn("kb_relation_recommendations", names)
        self.assertNotIn("kb_graph_recall", names)

    def test_general_chat_includes_kb_tools(self) -> None:
        from app.assistant.skills.definitions import GENERAL_CHAT  # noqa: E402

        self.assertIn("kb_search", GENERAL_CHAT.tools)
        self.assertIn("kb_relation_recommendations", GENERAL_CHAT.tools)
        self.assertNotIn("kb_graph_recall", GENERAL_CHAT.tools)
