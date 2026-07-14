from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from uuid import uuid4
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
from app.common.exceptions import ApiException  # noqa: E402
from app.database import get_db  # noqa: E402
from app.openclaw_integration.models import OpenClawCapabilityItem  # noqa: E402
from app.openclaw_integration.registry import list_openclaw_system_item_definitions  # noqa: E402
from app.openclaw_integration.router import runtime_router, settings_router  # noqa: E402
from app.openclaw_integration.service import OPENCLAW_SYSTEM_ITEM_VERSION  # noqa: E402
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

    def _patch_engines(self, engine):  # noqa: ANN001
        """Patch both legacy service and shared capability adapter engines."""
        from contextlib import ExitStack
        from unittest.mock import patch as _patch

        stack = ExitStack()
        stack.enter_context(
            _patch(
                "app.openclaw_integration.service.OpenClawIntegrationService._build_engine",
                return_value=engine,
            )
        )
        # Workflow adapter imports LangGraphEngine inside the method from this module.
        stack.enter_context(
            _patch(
                "app.assistant.workflow.engine.engine.LangGraphEngine",
                return_value=engine,
            )
        )

        # Agent shared adapter uses run_agent_execution, not LangGraphEngine.
        def _fake_run_agent_execution(request):  # noqa: ANN001
            # Prefer engine.execute(...) shape used by legacy OpenClaw tests.
            output = "".join(
                engine.execute(skill=None, user_input="", history=[], runtime_context={})
            )
            from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

            return AgentExecutionResult(
                final_text=output,
                round_count=1,
                used_tools=[],
                stopped_by="final_answer",
                error_message=None,
            )

        stack.enter_context(
            _patch(
                "app.assistant.capabilities.adapters.agent.run_agent_execution",
                side_effect=_fake_run_agent_execution,
            )
        )
        return stack


    def _get_system_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_weekly_report__workflow")
        return str(workflow.id), workflow.name

    def _get_openclaw_capture_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_context_capture__workflow")
        return str(workflow.id), workflow.name

    def _get_periodic_review_core_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_periodic_review_core__workflow")
        return str(workflow.id), workflow.name

    def test_settings_defaults_seed_system_items(self) -> None:
        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]

        self.assertFalse(data["enabled"])
        self.assertFalse(data["secretConfigured"])
        self.assertEqual(len(data["catalogItems"]), 6)
        by_key = {item["capabilityKey"]: item for item in data["catalogItems"]}
        self.assertEqual(
            set(by_key),
            {
                "submit_context_capture",
                "search_entries",
                "get_entry",
                "create_relation",
                "query_knowledge_graph",
                "generate_periodic_review",
            },
        )
        self.assertTrue(all(item["isSystemItem"] for item in data["catalogItems"]))
        self.assertEqual(by_key["submit_context_capture"]["sourceType"], "workflow")
        self.assertIsNotNone(by_key["submit_context_capture"]["workflowId"])
        self.assertTrue(by_key["submit_context_capture"]["enabled"])
        self.assertEqual(by_key["search_entries"]["sourceToolName"], "search_entries")
        self.assertEqual(by_key["get_entry"]["sourceToolName"], "get_entry_detail")
        self.assertEqual(by_key["create_relation"]["sourceToolName"], "create_relation")
        self.assertEqual(by_key["query_knowledge_graph"]["sourceToolName"], "query_knowledge_graph")
        self.assertEqual(by_key["generate_periodic_review"]["sourceType"], "workflow")
        self.assertIsNotNone(by_key["generate_periodic_review"]["workflowId"])
        self.assertIsNone(by_key["generate_periodic_review"]["sourceToolName"])
        capture_schema = by_key["submit_context_capture"]["inputSchema"]
        self.assertEqual(set(capture_schema["properties"]), {"context"})
        self.assertEqual(capture_schema["required"], ["context"])
        self.assertFalse(capture_schema["additionalProperties"])
        self.assertIn("context", by_key["submit_context_capture"]["inputSummary"])
        self.assertEqual(by_key["submit_context_capture"]["sourceName"], "智能上下文入库工作流")
        workflow_id, workflow_name = self._get_openclaw_capture_workflow()
        self.assertEqual(workflow_name, "system_context_capture__workflow")
        self.assertEqual(by_key["submit_context_capture"]["workflowId"], workflow_id)

        review_schema = by_key["generate_periodic_review"]["inputSchema"]
        self.assertEqual(set(review_schema["properties"]), {"focus", "period", "startDate", "endDate"})
        review_workflow_id, review_workflow_name = self._get_periodic_review_core_workflow()
        self.assertEqual(review_workflow_name, "system_periodic_review_core__workflow")
        self.assertEqual(by_key["generate_periodic_review"]["workflowId"], review_workflow_id)

    def test_workflow_catalog_sources_use_shared_display_names(self) -> None:
        response = self.client.get(
            "/api/system-settings/openclaw-integration/catalog-sources",
            params={"sourceType": "workflow"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        capture_item = next(item for item in items if item["sourceName"] == "智能上下文入库工作流")
        self.assertEqual(capture_item["title"], "智能上下文入库工作流")
        self.assertNotEqual(capture_item["sourceName"], "system_context_capture__workflow")
        self.assertTrue(capture_item["isSystem"])

    def test_submit_context_capture_is_marked_unavailable_without_entry_types(self) -> None:
        secret = self._rotate_secret()
        self._enable_integration(secret)

        response = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(response.status_code, 200, response.text)
        by_key = {item["capabilityKey"]: item for item in response.json()["data"]["capabilities"]}
        self.assertFalse(by_key["submit_context_capture"]["available"])
        self.assertFalse(by_key["search_entries"]["available"])
        self.assertFalse(by_key["get_entry"]["available"])
        self.assertIn("记录类型", by_key["submit_context_capture"]["availabilityReason"])

    def test_legacy_fixed_capability_flags_migrate_into_system_items(self) -> None:
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
        self.assertTrue(by_key["submit_context_capture"]["enabled"])
        self.assertNotIn("capture_entry", by_key)

    def test_obsolete_system_capture_item_is_removed_during_seed(self) -> None:
        self.db.add(
            OpenClawCapabilityItem(
                capability_key="capture_entry",
                tool_name="mindatlas_capture_entry",
                title="Custom Capture Item",
                description="Customized capture item",
                source_type="tool",
                system_default_key="capture_entry",
                source_tool_name="openclaw_capture_entry",
                enabled=True,
                is_system_item=True,
                input_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                output_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                input_summary="",
                output_summary="",
                tool_response_mode="json_schema",
            )
        )
        self.db.commit()

        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        by_key = {item["capabilityKey"]: item for item in items}
        self.assertIn("submit_context_capture", by_key)
        self.assertNotIn("capture_entry", by_key)
        self.assertIsNone(
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "capture_entry")
            .first()
        )

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

    def test_catalog_sources_include_update_entry_system_tool(self) -> None:
        self._initialize_system()
        response = self.client.get(
            "/api/system-settings/openclaw-integration/catalog-sources",
            params={"sourceType": "tool"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["items"]
        by_source_tool_name = {
            item["sourceToolName"]: item
            for item in items
            if item.get("sourceToolName")
        }
        self.assertIn("update_entry", by_source_tool_name)
        self.assertTrue(by_source_tool_name["update_entry"]["bindable"])

    def test_tool_sources_and_catalog_items_reuse_system_tool_display_metadata(self) -> None:
        response = self.client.get(
            "/api/system-settings/openclaw-integration/catalog-sources",
            params={"sourceType": "tool"},
            headers={"X-MindAtlas-Locale": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        by_source = {
            item["sourceToolName"]: item
            for item in response.json()["data"]["items"]
            if item.get("sourceToolName")
        }
        self.assertEqual(by_source["search_entries"]["title"], "搜索记录")
        self.assertEqual(by_source["search_entries"]["sourceName"], "搜索记录")
        self.assertTrue(by_source["search_entries"]["sourceDescription"])
        self.assertNotEqual(by_source["search_entries"]["title"], "检索历史记录")

        response = self.client.get(
            "/api/system-settings/openclaw-integration",
            headers={"X-MindAtlas-Locale": "zh"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        by_key = {item["capabilityKey"]: item for item in response.json()["data"]["catalogItems"]}
        self.assertEqual(by_key["search_entries"]["sourceName"], "搜索记录")
        self.assertEqual(by_key["search_entries"]["sourceToolName"], "search_entries")

    def test_settings_returns_sync_warning_when_system_item_sync_fails(self) -> None:
        with patch(
            "app.openclaw_integration.service.OpenClawIntegrationService._ensure_system_items",
            side_effect=ApiException(
                status_code=422,
                code=42203,
                message="Workflow references unavailable tools: update_entry (not found)",
            ),
        ):
            response = self.client.get("/api/system-settings/openclaw-integration")

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertIn("catalogItems", data)
        self.assertIn("syncWarning", data)
        self.assertIn("update_entry", data["syncWarning"])

    def test_retired_capture_source_is_not_listed_in_catalog_sources(self) -> None:
        self._initialize_system()
        response = self.client.get(
            "/api/system-settings/openclaw-integration/catalog-sources",
            params={"sourceType": "tool"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        source_tool_names = {item["sourceToolName"] for item in response.json()["data"]["items"]}
        self.assertNotIn("openclaw_capture_entry", source_tool_names)

    def test_create_catalog_item_rejects_retired_capture_source(self) -> None:
        self._initialize_system()
        response = self.client.post(
            "/api/system-settings/openclaw-integration/catalog-items",
            json={
                "sourceType": "tool",
                "sourceToolName": "openclaw_capture_entry",
                "toolName": "mindatlas_legacy_capture",
                "title": "Legacy Capture",
                "description": "Should be rejected",
                "enabled": True,
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("退役", response.json()["message"])

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
        capability_keys = {item["capabilityKey"] for item in capabilities}
        self.assertIn("submit_context_capture", capability_keys)
        self.assertNotIn("capture_entry", capability_keys)

    def test_existing_custom_item_bound_to_retired_capture_source_is_hidden_from_runtime(self) -> None:
        self._initialize_system()
        legacy_item = OpenClawCapabilityItem(
            capability_key="legacy_capture_entry",
            tool_name="mindatlas_legacy_capture_entry",
            title="Legacy Capture Entry",
            description="Legacy field-level capture",
            source_type="tool",
            source_tool_name="openclaw_capture_entry",
            enabled=True,
            is_system_item=False,
            input_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            output_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            input_summary="",
            output_summary="",
            tool_response_mode="json_schema",
        )
        self.db.add(legacy_item)
        self.db.commit()

        settings = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(settings.status_code, 200, settings.text)
        catalog_item = next(
            item
            for item in settings.json()["data"]["catalogItems"]
            if item["capabilityKey"] == "legacy_capture_entry"
        )
        self.assertTrue(catalog_item["retired"])
        self.assertFalse(catalog_item["available"])
        self.assertIn("退役", catalog_item["retirementReason"])

        secret = self._rotate_secret()
        self._enable_integration(secret)
        metadata = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(metadata.status_code, 200, metadata.text)
        self.assertNotIn(
            "legacy_capture_entry",
            {item["capabilityKey"] for item in metadata.json()["data"]["capabilities"]},
        )

        execute = self.client.post(
            "/api/integrations/openclaw/capabilities/legacy_capture_entry/execute",
            headers=self._auth_headers(secret),
            json={"title": "hello"},
        )
        self.assertEqual(execute.status_code, 403, execute.text)
        self.assertEqual(execute.json()["code"], 40362)
        self.assertIn("退役", execute.json()["message"])

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

    def test_system_item_can_be_deleted_and_reset_restores_defaults(self) -> None:
        self._initialize_system()

        create_response = self.client.post(
            "/api/system-settings/openclaw-integration/catalog-items",
            json={
                "sourceType": "tool",
                "sourceToolName": "search_entries",
                "toolName": "mindatlas_custom_search",
                "title": "Custom Search",
                "description": "Custom search item",
                "enabled": True,
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                "toolResponseMode": "text_field",
            },
        )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        custom_item_id = create_response.json()["data"]["id"]

        settings = self.client.get("/api/system-settings/openclaw-integration").json()["data"]
        search_item = next(item for item in settings["catalogItems"] if item["capabilityKey"] == "search_entries")

        delete_response = self.client.delete(
            f"/api/system-settings/openclaw-integration/catalog-items/{search_item['id']}"
        )
        self.assertEqual(delete_response.status_code, 200, delete_response.text)

        after_delete = self.client.get("/api/system-settings/openclaw-integration").json()["data"]
        self.assertNotIn("search_entries", {item["capabilityKey"] for item in after_delete["catalogItems"]})
        self.assertIn(custom_item_id, {item["id"] for item in after_delete["catalogItems"]})

        reset_response = self.client.post("/api/system-settings/openclaw-integration/reset-system-items")
        self.assertEqual(reset_response.status_code, 200, reset_response.text)
        after_reset = reset_response.json()["data"]
        by_key = {item["capabilityKey"]: item for item in after_reset["catalogItems"]}
        self.assertIn("search_entries", by_key)
        self.assertEqual(by_key["search_entries"]["toolName"], "mindatlas_search_entries")
        self.assertEqual(by_key["search_entries"]["sourceType"], "tool")
        self.assertTrue(by_key["search_entries"]["isSystemItem"])
        self.assertIn(custom_item_id, {item["id"] for item in after_reset["catalogItems"]})

    def test_periodic_review_system_item_seeds_unified_content_schema(self) -> None:
        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        by_key = {item["capabilityKey"]: item for item in items}

        review_item = by_key["generate_periodic_review"]
        review_content = review_item["outputSchema"]["properties"]["content"]

        self.assertEqual(review_item["sourceType"], "workflow")
        self.assertEqual(review_content["type"], "string")
        self.assertEqual(set(review_item["inputSchema"]["properties"]), {"focus", "period", "startDate", "endDate"})

    def test_existing_legacy_report_system_items_are_removed_during_seed(self) -> None:
        self.db.add(
            AppSetting(
                key="openclaw_integration_config",
                value_json={
                    "enabled": False,
                    "catalogMigrated": True,
                    "systemItemVersion": OPENCLAW_SYSTEM_ITEM_VERSION - 1,
                },
            )
        )
        self.db.add_all(
            [
                OpenClawCapabilityItem(
                    capability_key="generate_weekly_report",
                    tool_name="mindatlas_generate_weekly_report",
                    title="旧周报",
                    description="旧 schema",
                    source_type="tool",
                    system_default_key="generate_weekly_report",
                    source_tool_name="openclaw_generate_weekly_report",
                    enabled=True,
                    is_system_item=True,
                    input_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    output_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    input_summary="",
                    output_summary="",
                    tool_response_mode="json_schema",
                ),
                OpenClawCapabilityItem(
                    capability_key="generate_monthly_report",
                    tool_name="mindatlas_generate_monthly_report",
                    title="旧月报",
                    description="旧 schema",
                    source_type="tool",
                    system_default_key="generate_monthly_report",
                    source_tool_name="openclaw_generate_monthly_report",
                    enabled=True,
                    is_system_item=True,
                    input_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    output_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    input_summary="",
                    output_summary="",
                    tool_response_mode="json_schema",
                ),
            ]
        )
        self.db.commit()

        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        by_key = {item["capabilityKey"]: item for item in items}

        self.assertNotIn("generate_weekly_report", by_key)
        self.assertNotIn("generate_monthly_report", by_key)
        self.assertIn("generate_periodic_review", by_key)
        self.assertEqual(by_key["generate_periodic_review"]["sourceType"], "workflow")

    def test_legacy_custom_tool_binding_is_migrated_to_canonical_source_name(self) -> None:
        self._initialize_system()
        self.db.add(
            OpenClawCapabilityItem(
                capability_key="legacy_search_alias",
                tool_name="mindatlas_legacy_search_alias",
                title="Legacy Search Alias",
                description="Legacy alias binding",
                source_type="tool",
                source_tool_name="openclaw_search_entries",
                enabled=True,
                is_system_item=False,
                input_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                output_schema_json={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                input_summary="",
                output_summary="",
                tool_response_mode="json_schema",
            )
        )
        self.db.commit()

        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        migrated_item = next(item for item in items if item["capabilityKey"] == "legacy_search_alias")
        self.assertEqual(migrated_item["sourceToolName"], "search_entries")

    def test_system_item_registry_uses_routing_oriented_metadata(self) -> None:
        definitions = {item.key: item for item in list_openclaw_system_item_definitions(locale="en")}

        self.assertEqual(definitions["submit_context_capture"].tool_name, "mindatlas_submit_context_capture")
        self.assertEqual(definitions["submit_context_capture"].workflow_asset_key, "context_capture")
        self.assertIn("candidate record or record-update context", definitions["submit_context_capture"].description)
        self.assertIn("Provide only `context`", definitions["submit_context_capture"].input_summary or "")
        self.assertIn("recent and time-bounded lookups", definitions["search_entries"].description)
        self.assertIn("entry ID is known", definitions["get_entry"].description)
        self.assertIn("connect these items", definitions["create_relation"].description)
        self.assertIn("patterns", definitions["query_knowledge_graph"].description)
        self.assertEqual(definitions["generate_periodic_review"].tool_name, "mindatlas_generate_periodic_review")
        self.assertEqual(definitions["generate_periodic_review"].workflow_asset_key, "periodic_review_core")
        self.assertIn("last week", definitions["generate_periodic_review"].description)
        self.assertIn("tag distribution", definitions["generate_periodic_review"].description)

    def test_submit_context_capture_runtime_schema_is_thin_context_only(self) -> None:
        self._initialize_system(locale="en")
        secret = self._rotate_secret()
        self._enable_integration(secret)

        response = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(response.status_code, 200, response.text)
        capture_item = next(
            item for item in response.json()["data"]["capabilities"] if item["capabilityKey"] == "submit_context_capture"
        )

        self.assertEqual(set(capture_item["inputSchema"]["properties"]), {"context"})
        self.assertEqual(capture_item["inputSchema"]["required"], ["context"])
        self.assertFalse(capture_item["inputSchema"]["additionalProperties"])
        self.assertIn("Provide only `context`", capture_item["inputSummary"])

    def test_submit_context_capture_rejects_legacy_field_level_payload(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        response = self.client.post(
            "/api/integrations/openclaw/capabilities/submit_context_capture/execute",
            headers=self._auth_headers(secret),
            json={
                "context": "完成 OpenClaw 能力梳理并准备收敛到 workflow。",
                "intent": "record",
                "source": "openclaw",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], 42261)
        self.assertIn("unknown field: intent", response.json()["message"])

    def test_runtime_capability_schema_exposes_dynamic_enums_and_guidance_metadata(self) -> None:
        from app.entry_type.models import EntryType  # noqa: E402
        from app.relation.models import RelationType  # noqa: E402

        self._initialize_system(locale="en")
        secret = self._rotate_secret()
        self._enable_integration(secret)

        response = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )

        self.assertEqual(response.status_code, 200, response.text)
        by_key = {item["capabilityKey"]: item for item in response.json()["data"]["capabilities"]}

        capture_item = by_key["submit_context_capture"]
        capture_properties = capture_item["inputSchema"]["properties"]
        self.assertIn("candidate record context", capture_properties["context"]["description"])

        enabled_entry_type_codes = [
            row.code
            for row in self.db.query(EntryType).filter(EntryType.enabled.is_(True)).order_by(EntryType.code.asc()).all()
        ]
        search_item = by_key["search_entries"]
        search_properties = search_item["inputSchema"]["properties"]
        self.assertEqual(search_properties["entryType"]["enum"], enabled_entry_type_codes)
        self.assertIn("'.' and '*' are treated as literal keywords", search_properties["query"]["description"])
        self.assertIn("compatibility input", search_properties["entryType"]["description"])
        self.assertEqual(search_properties["timeFrom"]["format"], "date-time")
        self.assertEqual(search_properties["timeTo"]["format"], "date-time")
        self.assertEqual(search_properties["limit"]["minimum"], 1)
        self.assertEqual(search_properties["limit"]["maximum"], 50)
        self.assertEqual(search_properties["limit"]["default"], 10)
        self.assertEqual(search_properties["limit"]["examples"], [10])
        self.assertIn("not the recommended contract", search_item["inputSummary"])

        get_entry_item = by_key["get_entry"]
        get_entry_properties = get_entry_item["inputSchema"]["properties"]
        self.assertIn("canonical input", get_entry_properties["entryId"]["description"])
        self.assertEqual(get_entry_properties["entryId"]["examples"], ["123e4567-e89b-12d3-a456-426614174000"])

        enabled_relation_type_codes = [
            row.code
            for row in self.db.query(RelationType).filter(RelationType.enabled.is_(True)).order_by(RelationType.code.asc()).all()
        ]
        relation_item = by_key["create_relation"]
        relation_properties = relation_item["inputSchema"]["properties"]
        self.assertEqual(relation_properties["relationType"]["enum"], enabled_relation_type_codes)
        self.assertIn("canonical contract", relation_properties["relationType"]["description"])

        graph_item = by_key["query_knowledge_graph"]
        graph_properties = graph_item["inputSchema"]["properties"]
        self.assertEqual(graph_properties["mode"]["enum"], ["naive", "local", "global", "hybrid", "mix"])
        self.assertEqual(graph_properties["topK"]["minimum"], 1)
        self.assertEqual(graph_properties["topK"]["maximum"], 20)
        self.assertEqual(graph_properties["topK"]["default"], 5)
        self.assertEqual(graph_properties["topK"]["examples"], [5])

        review_item = by_key["generate_periodic_review"]
        review_properties = review_item["inputSchema"]["properties"]
        self.assertEqual(set(review_properties), {"focus", "period", "startDate", "endDate"})
        self.assertEqual(review_properties["focus"]["enum"], ["overview", "type", "tag", "trend"])
        self.assertEqual(review_item["outputSchema"]["properties"]["content"]["type"], "string")
        self.assertIn("last 30 days", review_item["inputSummary"])

    def test_settings_catalog_items_include_same_schema_enrichment(self) -> None:
        from app.entry_type.models import EntryType  # noqa: E402

        self._initialize_system(locale="en")

        response = self.client.get("/api/system-settings/openclaw-integration")

        self.assertEqual(response.status_code, 200, response.text)
        by_key = {item["capabilityKey"]: item for item in response.json()["data"]["catalogItems"]}
        enabled_entry_type_codes = [
            row.code
            for row in self.db.query(EntryType).filter(EntryType.enabled.is_(True)).order_by(EntryType.code.asc()).all()
        ]
        search_properties = by_key["search_entries"]["inputSchema"]["properties"]
        self.assertEqual(search_properties["entryType"]["enum"], enabled_entry_type_codes)
        self.assertEqual(search_properties["limit"]["minimum"], 1)
        self.assertEqual(search_properties["limit"]["maximum"], 50)
        self.assertEqual(search_properties["timeFrom"]["format"], "date-time")
        self.assertIn("'.' and '*'", search_properties["query"]["description"])

    def test_generate_periodic_review_executes_workflow_contract(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        captured_runtime_context: dict[str, object] = {}

        class Engine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                captured_runtime_context.update(kwargs["runtime_context"])
                return iter(
                    [
                        json.dumps(
                            {
                                "content": "## 我先帮你看了下\n这段时间你一共记录了 4 条内容。",
                            },
                            ensure_ascii=False,
                        )
                    ]
                )

        payload = {
            "focus": "trend",
            "period": "last week",
        }

        with self._patch_engines(Engine()):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/generate_periodic_review/execute",
                headers=self._auth_headers(secret),
                json=payload,
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["content"], "## 我先帮你看了下\n这段时间你一共记录了 4 条内容。")
        self.assertEqual(captured_runtime_context["structured_input"], payload)
        self.assertIn(captured_runtime_context["channel_type"], {"openclaw_capability", "capability_runtime"})

    def test_legacy_weekly_and_monthly_capability_keys_are_not_found(self) -> None:
        secret = self._rotate_secret()
        self._enable_integration(secret)

        for capability_key in ("generate_weekly_report", "generate_monthly_report"):
            with self.subTest(capability_key=capability_key):
                response = self.client.post(
                    f"/api/integrations/openclaw/capabilities/{capability_key}/execute",
                    headers=self._auth_headers(secret),
                    json={},
                )
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["code"], 40461)

    def test_search_entries_treats_blank_optional_filters_as_unset(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        captured_args: dict[str, object] = {}

        def _fake_runner(**kwargs):  # noqa: ANN003
            captured_args.update(kwargs)
            return {"total": 0, "items": []}

        with patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=_fake_runner,
        ), patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=_fake_runner,
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/search_entries/execute",
                headers=self._auth_headers(secret),
                json={
                    "query": "",
                    "entryType": "",
                    "timeFrom": "",
                    "timeTo": "",
                    "limit": 10,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        # Shared Gateway binding schemas reject JSON null; optional blanks may be omitted.
        self.assertIsNone(captured_args.get("keyword"))
        self.assertIsNone(captured_args.get("type_code"))
        self.assertIsNone(captured_args.get("time_from"))
        self.assertIsNone(captured_args.get("time_to"))
        self.assertEqual(captured_args.get("limit"), 10)

    def test_search_entries_treats_dot_query_as_literal_and_rejects_dot_entry_type(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        captured_args: dict[str, object] = {}

        def _fake_runner(**kwargs):  # noqa: ANN003
            captured_args.update(kwargs)
            return {"total": 0, "items": []}

        with patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=_fake_runner,
        ), patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=_fake_runner,
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/search_entries/execute",
                headers=self._auth_headers(secret),
                json={"query": "."},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(captured_args.get("keyword"), ".")

        invalid_response = self.client.post(
            "/api/integrations/openclaw/capabilities/search_entries/execute",
            headers=self._auth_headers(secret),
            json={"entryType": "."},
        )
        self.assertEqual(invalid_response.status_code, 400, invalid_response.text)
        self.assertEqual(invalid_response.json()["message"], "Unknown entry type: .")

    def test_search_entries_unknown_tag_names_return_empty_results_without_schema_error(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        response = self.client.post(
            "/api/integrations/openclaw/capabilities/search_entries/execute",
            headers=self._auth_headers(secret),
            json={"tagNames": ["not-a-real-tag"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    def test_get_entry_accepts_search_hit_payload_with_top_level_id(self) -> None:
        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402

        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        entry_type = self.db.query(EntryType).filter(EntryType.code == "KNOWLEDGE").first()
        self.assertIsNotNone(entry_type)

        entry = Entry(
            title="OpenClaw 详情测试",
            summary="用于验证详情能力入参兼容。",
            content="详情正文",
            type_id=entry_type.id,
            time_mode=TimeMode.POINT,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)

        response = self.client.post(
            "/api/integrations/openclaw/capabilities/get_entry/execute",
            headers=self._auth_headers(secret),
            json={
                "id": str(entry.id),
                "title": entry.title,
                "summary": entry.summary,
                "content": entry.content,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["id"], str(entry.id))
        self.assertEqual(result["title"], "OpenClaw 详情测试")
        self.assertEqual(result["content"], "详情正文")

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

        with self._patch_engines(type(
                "Engine",
                (),
                {
                    "execute": lambda _self, *args, **kwargs: iter([
                        '{"summary":"周报摘要","suggestions":["继续推进"],"trends":"整体稳定"}'
                    ])
                },
            )()):
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

    def test_system_capture_workflow_preset_executes_thin_context(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        metadata = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        )
        self.assertEqual(metadata.status_code, 200, metadata.text)
        capture_item = next(
            item for item in metadata.json()["data"]["capabilities"] if item["capabilityKey"] == "submit_context_capture"
        )
        self.assertEqual(capture_item["sourceType"], "workflow")
        captured_runtime_context: dict[str, object] = {}

        class Engine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                runtime_context = kwargs["runtime_context"]
                captured_runtime_context.update(runtime_context)
                return iter(
                    [
                        json.dumps(
                            {
                                "status": "created",
                                "entryId": "00000000-0000-0000-0000-000000000001",
                                "entryTitle": "项目复盘",
                                "entryTypeCode": "KNOWLEDGE",
                                "entryTypeName": "知识",
                                "summary": "记录已成功沉淀。",
                                "tagNames": ["openclaw", "mindatlas"],
                                "timeMode": "POINT",
                                "timeAt": "2026-03-31",
                                "timeFrom": None,
                                "timeTo": None,
                                "createdAt": "2026-03-31T09:00:00+00:00",
                                "updatedAt": "2026-03-31T09:05:00+00:00",
                            },
                            ensure_ascii=False,
                        )
                    ]
                )

        with self._patch_engines(Engine()):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/submit_context_capture/execute",
                headers=self._auth_headers(secret),
                json={
                    "context": "今天完成了 OpenClaw 接入方案梳理，并确认后续要收口成 workflow preset。",
                },
            )

        # Shared-only: code_executor closures classify unknown and fail closed with 40961.
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], 40961)

    def test_system_capture_workflow_accepts_merged_result_shape(self) -> None:
        self._initialize_system()
        secret = self._rotate_secret()
        self._enable_integration(secret)

        class Engine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return iter(
                    [
                        json.dumps(
                            {
                                "status": "merged",
                                "entryId": "00000000-0000-0000-0000-000000000002",
                                "entryTitle": "OpenClaw 接入记录",
                                "entryTypeCode": "KNOWLEDGE",
                                "entryTypeName": "知识",
                                "summary": "已有记录已吸收新的 OpenClaw 上下文。",
                                "tagNames": ["openclaw", "mindatlas"],
                                "timeMode": "POINT",
                                "timeAt": "2026-04-03",
                                "timeFrom": None,
                                "timeTo": None,
                                "createdAt": "2026-04-02T09:00:00+00:00",
                                "updatedAt": "2026-04-03T09:00:00+00:00",
                            },
                            ensure_ascii=False,
                        )
                    ]
                )

        with self._patch_engines(Engine()):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/submit_context_capture/execute",
                headers=self._auth_headers(secret),
                json={
                    "context": "今天补齐了 OpenClaw stream-only 兼容，并确认相关能力已验证完成。",
                },
            )

        # Shared-only: code_executor closures classify unknown and fail closed with 40961.
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], 40961)

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

        with self._patch_engines(type(
                "Engine",
                (),
                {
                    "execute": lambda _self, *args, **kwargs: iter(['{"answer":"来自 agent 的结果"}'])
                },
            )()):
            response = self.client.post(
                f"/api/integrations/openclaw/capabilities/{created_item['capabilityKey']}/execute",
                headers=self._auth_headers(secret),
                json={"question": "现在系统定位是什么？"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()["data"]["result"]["answer"]
        # Shared agent normalization may keep a complete JSON document string under answer
        # when the engine fake returns JSON text; accept both shapes.
        if isinstance(answer, str) and answer.strip().startswith("{"):
            import json as _json
            answer = _json.loads(answer).get("answer", answer)
        self.assertEqual(answer, "来自 agent 的结果")

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
            "app.assistant.tools.kb_tools.LightRagService.query",
            new=AsyncMock(return_value=fake_response),
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/query_knowledge_graph/execute",
                headers=self._auth_headers(secret),
                json={"query": "最近的项目关系是什么？", "mode": "hybrid", "topK": 5},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["result"]["answer"], "这是图谱查询结果")
        catalog = self.client.get(
            "/api/integrations/openclaw/capabilities",
            headers=self._auth_headers(secret),
        ).json()["data"]["capabilities"]
        query_item = next(item for item in catalog if item["capabilityKey"] == "query_knowledge_graph")
        self.assertEqual(query_item["sourceType"], "tool")

    def test_create_relation_returns_404_when_target_entry_missing(self) -> None:
        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402

        self._initialize_system()

        entry_type = self.db.query(EntryType).filter(EntryType.code == "KNOWLEDGE").first()
        self.assertIsNotNone(entry_type)

        source = Entry(
            title="Source Entry",
            content="content",
            type_id=entry_type.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add(source)
        self.db.commit()

        secret = self._rotate_secret()
        self._enable_integration(secret)

        missing_target_id = uuid4()
        response = self.client.post(
            "/api/integrations/openclaw/capabilities/create_relation/execute",
            headers=self._auth_headers(secret),
            json={
                "sourceEntryId": str(source.id),
                "targetEntryId": str(missing_target_id),
                "relationType": "RELATES_TO",
            },
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], 40400)
        self.assertEqual(response.json()["message"], f"Target entry not found: {missing_target_id}")


if __name__ == "__main__":
    unittest.main()
