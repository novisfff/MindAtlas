from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantKnowledgeBaseToolsTests(unittest.TestCase):
    @staticmethod
    def _tool_name(tool: object) -> str | None:
        return getattr(tool, "name", None) or getattr(tool, "__name__", None)

    def test_tools_exported_and_registered(self) -> None:
        from app.assistant import tools as assistant_tools  # noqa: E402
        from app.assistant_config.registry import ToolRegistry  # noqa: E402

        self.assertNotIn("kb_search", getattr(assistant_tools, "__all__", []))
        self.assertIn("search_similar_entries", getattr(assistant_tools, "__all__", []))
        self.assertIn("create_entry", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("update_entry", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("create_relation", getattr(assistant_tools, "__all__", []))
        self.assertIn("query_knowledge_graph", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("generate_weekly_report", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("generate_monthly_report", getattr(assistant_tools, "__all__", []))
        self.assertIn("kb_relation_recommendations", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("openclaw_search_entries", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("openclaw_get_entry", getattr(assistant_tools, "__all__", []))
        self.assertNotIn("kb_graph_recall", getattr(assistant_tools, "__all__", []))

        # Tool objects created by @tool should expose `name`.
        self.assertEqual(self._tool_name(assistant_tools.kb_search), "kb_search")
        self.assertEqual(self._tool_name(assistant_tools.search_similar_entries), "search_similar_entries")
        self.assertEqual(self._tool_name(assistant_tools.create_entry), "create_entry")
        self.assertEqual(self._tool_name(assistant_tools.query_knowledge_graph), "query_knowledge_graph")
        self.assertEqual(self._tool_name(assistant_tools.openclaw_search_entries), "openclaw_search_entries")
        self.assertEqual(
            self._tool_name(assistant_tools.kb_relation_recommendations),
            "kb_relation_recommendations",
        )
        self.assertIsNone(getattr(assistant_tools, "kb_graph_recall", None))

        names = {t.name for t in ToolRegistry.list_system_tools()}
        self.assertNotIn("kb_search", names)
        self.assertIn("search_similar_entries", names)
        self.assertIn("create_entry", names)
        self.assertNotIn("update_entry", names)
        self.assertNotIn("create_relation", names)
        self.assertIn("query_knowledge_graph", names)
        self.assertNotIn("generate_weekly_report", names)
        self.assertNotIn("generate_monthly_report", names)
        self.assertIn("kb_relation_recommendations", names)
        self.assertNotIn("openclaw_search_entries", names)
        self.assertNotIn("openclaw_get_entry", names)
        self.assertNotIn("kb_graph_recall", names)

    def test_hidden_openclaw_aliases_still_validate_as_system_tools(self) -> None:
        from app.assistant_config.registry import ToolRegistry  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from tests._db import make_session  # noqa: E402

        self.assertTrue(ToolRegistry.has_system_tool("openclaw_search_entries"))
        self.assertTrue(ToolRegistry.has_system_tool("openclaw_get_entry"))

        db = make_session()
        try:
            service = AssistantConfigService(db)
            service._validate_workflow_tool_names({"openclaw_search_entries", "openclaw_get_entry"})
            service._validate_agent_tool_names(["openclaw_search_entries"])
        finally:
            db.close()

    def test_general_chat_includes_kb_tools(self) -> None:
        from app.assistant.skill_catalog.definitions import GENERAL_CHAT  # noqa: E402

        # kb_search is internally prefetched when skill.kb.enabled=true, not exposed as a visible tool name.
        self.assertNotIn("kb_search", GENERAL_CHAT.tools)
        self.assertNotIn("kb_relation_recommendations", GENERAL_CHAT.tools)
        self.assertNotIn("kb_graph_recall", GENERAL_CHAT.tools)

    def test_system_tool_output_contracts_are_complete_and_parseable(self) -> None:
        from app.assistant_config.registry import ToolRegistry  # noqa: E402

        definitions = ToolRegistry.list_system_tool_definitions()
        self.assertGreater(len(definitions), 0)

        for tool in definitions:
            self.assertTrue(tool.output_params, f"{tool.name} output_params should not be empty")
            self.assertTrue(tool.returns, f"{tool.name} returns should not be empty")
            for param in tool.output_params:
                self.assertTrue(param.name, f"{tool.name} output param name should not be empty")
                self.assertTrue(param.param_type, f"{tool.name}.{param.name} param_type should not be empty")
                self.assertIn(
                    f"- {param.name} ({param.param_type})",
                    tool.returns,
                    f"{tool.name} returns should include list item for {param.name}",
                )

    def test_system_tool_definitions_expose_display_metadata_with_locale(self) -> None:
        from fastapi import FastAPI  # noqa: E402
        from fastapi.testclient import TestClient  # noqa: E402

        from app.assistant_config.router import router as assistant_config_router  # noqa: E402
        from app.common.exceptions import register_exception_handlers  # noqa: E402
        from app.database import get_db  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(assistant_config_router)

        def _override_get_db():  # noqa: ANN001
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        client = TestClient(app)

        try:
            response = client.get(
                "/api/assistant-config/system-tools/definitions",
                headers={"X-MindAtlas-Locale": "zh"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            by_name = {item["name"]: item for item in response.json()["data"]}
            self.assertEqual(by_name["search_entries"]["displayName"], "搜索记录")
            self.assertEqual(by_name["search_similar_entries"]["displayName"], "检索相似记录")
            self.assertEqual(by_name["create_entry"]["displayName"], "创建记录")
            self.assertNotIn("update_entry", by_name)
            self.assertNotIn("create_relation", by_name)
            self.assertEqual(by_name["kb_relation_recommendations"]["displayName"], "关系推荐")
            self.assertTrue(by_name["get_tag_statistics"]["displayDescription"])

            response = client.get(
                "/api/assistant-config/system-tools/definitions",
                headers={"X-MindAtlas-Locale": "en"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            by_name = {item["name"]: item for item in response.json()["data"]}
            self.assertEqual(by_name["search_entries"]["displayName"], "Search Entries")
            self.assertEqual(by_name["search_similar_entries"]["displayName"], "Search Similar Entries")
            self.assertEqual(by_name["create_entry"]["displayName"], "Create Entry")
            self.assertNotIn("update_entry", by_name)
            self.assertNotIn("create_relation", by_name)
            self.assertEqual(by_name["kb_relation_recommendations"]["displayName"], "Relation Recommendations")
        finally:
            db.close()
