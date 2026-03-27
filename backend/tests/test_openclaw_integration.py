from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant_config.schemas import AssistantAgentProfileCreateRequest  # noqa: E402
from app.assistant_config.service import AssistantConfigService  # noqa: E402
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from app.openclaw_integration.router import runtime_router, settings_router  # noqa: E402
from app.system_settings.initialization_service import SystemInitializationService  # noqa: E402
from app.system_settings.models import AppSetting  # noqa: E402
from app.system_settings.schemas import InitializeSystemRequest  # noqa: E402


class OpenClawIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(settings_router)
        app.include_router(runtime_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.db.close()

    def _make_initialization_request(self, *, locale: str = "zh") -> InitializeSystemRequest:
        return InitializeSystemRequest.model_validate(
            {
                "locale": locale,
                "aiCredential": {
                    "name": "OpenAI",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-test-1234567890",
                },
                "llmModel": {
                    "name": "gpt-4.1-mini",
                },
                "entryTypes": [
                    {
                        "code": "KNOWLEDGE",
                        "name": "知识" if locale == "zh" else "Knowledge",
                        "description": "知识点" if locale == "zh" else "Concepts",
                        "color": "#3B82F6",
                        "icon": "book",
                        "graphEnabled": True,
                        "aiEnabled": True,
                        "enabled": True,
                        "origin": "default",
                    }
                ],
            }
        )

    def _initialize_system(self, *, locale: str = "zh") -> None:
        SystemInitializationService(self.db).initialize_system(
            self._make_initialization_request(locale=locale)
        )

    def _rotate_secret(self) -> str:
        response = self.client.post("/api/system-settings/openclaw-integration/rotate-secret")
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["secret"]

    def _enable_integration(self, secret: str) -> None:
        response = self.client.put(
            "/api/system-settings/openclaw-integration",
            json={"enabled": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["data"]["enabled"])
        self.assertTrue(secret)

    def _auth_headers(self, secret: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {secret}",
            "X-OpenClaw-Source": "unit-test",
            "X-OpenClaw-Channel": "cli",
            "X-OpenClaw-Session": "session-1",
            "X-OpenClaw-Tool": "tool-1",
        }

    def _get_system_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_weekly_report__workflow")
        return str(workflow.id), workflow.name

    def test_settings_defaults_seed_system_presets(self) -> None:
        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]

        self.assertFalse(data["enabled"])
        self.assertFalse(data["secretConfigured"])
        self.assertEqual(len(data["catalogItems"]), 7)
        self.assertEqual(
            {item["capabilityKey"] for item in data["catalogItems"]},
            {
                "capture_entry",
                "search_entries",
                "get_entry",
                "create_relation",
                "query_knowledge_graph",
                "generate_weekly_report",
                "generate_monthly_report",
            },
        )
        self.assertTrue(all(item["isSystemPreset"] for item in data["catalogItems"]))

    def test_legacy_fixed_capability_flags_migrate_into_system_presets(self) -> None:
        self.db.add(
            AppSetting(
                key="openclaw_integration_config",
                value_json={
                    "enabled": False,
                    "capabilities": {
                        "search_entries": False,
                        "capture_entry": True,
                    },
                },
            )
        )
        self.db.commit()

        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        by_key = {item["capabilityKey"]: item for item in items}
        self.assertFalse(by_key["search_entries"]["enabled"])
        self.assertTrue(by_key["capture_entry"]["enabled"])

    def test_update_requires_secret_before_enabling(self) -> None:
        response = self.client.put(
            "/api/system-settings/openclaw-integration",
            json={"enabled": True},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], 40061)

    def test_can_create_catalog_item_from_system_tool_source(self) -> None:
        self._initialize_system()
        sources_response = self.client.get(
            "/api/system-settings/openclaw-integration/catalog-sources",
            params={"sourceType": "tool"},
        )
        self.assertEqual(sources_response.status_code, 200, sources_response.text)
        sources = sources_response.json()["data"]["items"]
        source = next(item for item in sources if item["sourceToolName"] == "search_entries")
        self.assertTrue(source["bindable"])

        create_response = self.client.post(
            "/api/system-settings/openclaw-integration/catalog-items",
            json={
                "sourceType": "tool",
                "sourceToolName": "search_entries",
                "toolName": "mindatlas_search_entries_catalog",
                "title": "Search Entries Catalog",
                "description": "Expose system tool search_entries",
                "enabled": True,
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        item = create_response.json()["data"]
        self.assertEqual(item["sourceType"], "tool")
        self.assertEqual(item["sourceToolName"], "search_entries")
        self.assertTrue(item["schemaEditable"])

    def test_runtime_capabilities_require_valid_bearer_secret(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        unauthorized = self.client.get("/api/integrations/openclaw/capabilities")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json()["code"], 40161)

        authorized = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(authorized.status_code, 200)
        capabilities = authorized.json()["data"]["capabilities"]
        self.assertTrue(any(item["capabilityKey"] == "capture_entry" for item in capabilities))

    def test_disabled_catalog_item_is_hidden_from_runtime_metadata(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        settings = self.client.get("/api/system-settings/openclaw-integration").json()["data"]
        search_item = next(item for item in settings["catalogItems"] if item["capabilityKey"] == "search_entries")
        update_response = self.client.put(
            f"/api/system-settings/openclaw-integration/catalog-items/{search_item['id']}",
            json={"enabled": False},
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)

        metadata = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(metadata.status_code, 200)
        self.assertNotIn(
            "search_entries",
            {item["capabilityKey"] for item in metadata.json()["data"]["capabilities"]},
        )

    def test_workflow_catalog_item_executes_published_workflow(self) -> None:
        self._initialize_system()
        workflow_id, workflow_name = self._get_system_workflow()
        create_response = self.client.post(
            "/api/system-settings/openclaw-integration/catalog-items",
            json={
                "sourceType": "workflow",
                "workflowId": workflow_id,
                "toolName": "mindatlas_weekly_report_workflow",
                "title": "Weekly Report Workflow",
                "description": workflow_name,
                "enabled": True,
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        created_item = create_response.json()["data"]

        secret = self._rotate_secret()
        self._enable_integration(secret)

        with patch(
            "app.openclaw_integration.service.LangGraphEngine.execute",
            new=lambda _self, *args, **kwargs: iter([
                '{"summary":"周报摘要","suggestions":["继续推进"],"trends":"整体稳定"}'
            ]),
        ):
            response = self.client.post(
                f"/api/integrations/openclaw/capabilities/{created_item['capabilityKey']}/execute",
                headers=self._auth_headers(secret),
                json={
                    "periodType": "weekly",
                    "periodStart": "2026-03-16",
                    "periodEnd": "2026-03-22",
                    "entryCount": 3,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["summary"], "周报摘要")
        self.assertEqual(result["suggestions"], ["继续推进"])

    def test_agent_catalog_item_executes_published_agent(self) -> None:
        self._initialize_system()
        agent = AssistantConfigService(self.db).create_agent_profile(
            AssistantAgentProfileCreateRequest.model_validate(
                {
                    "name": "openclaw_test_agent",
                    "description": "OpenClaw test agent",
                    "systemPrompt": "Return helpful structured answers.",
                    "tools": [],
                    "enabled": True,
                    "modelSource": "default",
                    "kbConfig": {"enabled": False},
                }
            )
        )

        create_response = self.client.post(
            "/api/system-settings/openclaw-integration/catalog-items",
            json={
                "sourceType": "agent",
                "agentProfileId": str(agent.id),
                "toolName": "mindatlas_test_agent_tool",
                "title": "Agent Tool",
                "description": "Expose a published agent",
                "enabled": True,
                "inputSummary": "question (string)",
                "outputSummary": "answer (string)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"}
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"}
                    },
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        created_item = create_response.json()["data"]

        secret = self._rotate_secret()
        self._enable_integration(secret)

        with patch(
            "app.openclaw_integration.service.LangGraphEngine.execute",
            new=lambda _self, *args, **kwargs: iter(['{"answer":"来自 agent 的结果"}']),
        ):
            response = self.client.post(
                f"/api/integrations/openclaw/capabilities/{created_item['capabilityKey']}/execute",
                headers=self._auth_headers(secret),
                json={"question": "现在系统定位是什么？"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["result"]["answer"], "来自 agent 的结果")

    def test_query_knowledge_graph_system_preset_uses_lightrag_service(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        fake_response = {
            "answer": "这是图谱查询结果",
            "sources": [],
            "metadata": {
                "mode": "hybrid",
                "topK": 5,
                "latencyMs": 12,
                "cacheHit": False,
            },
        }

        with patch(
            "app.openclaw_integration.service.resolve_runtime_knowledge_graph_config",
            return_value=type("Cfg", (), {"enabled": True, "configured": True})(),
        ), patch(
            "app.openclaw_integration.service.LightRagService.query",
            new=AsyncMock(return_value=fake_response),
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/query_knowledge_graph/execute",
                headers=self._auth_headers(secret),
                json={"query": "最近的项目关系是什么？", "mode": "hybrid", "topK": 5},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["result"]["answer"], "这是图谱查询结果")


if __name__ == "__main__":
    unittest.main()
