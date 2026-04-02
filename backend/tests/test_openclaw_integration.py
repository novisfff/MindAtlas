from __future__ import annotations

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

    def _get_system_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_weekly_report__workflow")
        return str(workflow.id), workflow.name

    def _get_openclaw_capture_workflow(self) -> tuple[str, str]:
        workflows = AssistantConfigService(self.db).list_workflows(include_disabled=True)
        workflow = next(item for item in workflows if item.name == "system_openclaw_context_capture__workflow")
        return str(workflow.id), workflow.name

    def test_settings_defaults_seed_system_items(self) -> None:
        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]

        self.assertFalse(data["enabled"])
        self.assertFalse(data["secretConfigured"])
        self.assertEqual(len(data["catalogItems"]), 7)
        by_key = {item["capabilityKey"]: item for item in data["catalogItems"]}
        self.assertEqual(
            set(by_key),
            {
                "submit_context_capture",
                "search_entries",
                "get_entry",
                "create_relation",
                "query_knowledge_graph",
                "generate_weekly_report",
                "generate_monthly_report",
            },
        )
        self.assertTrue(all(item["isSystemItem"] for item in data["catalogItems"]))
        self.assertEqual(by_key["submit_context_capture"]["sourceType"], "workflow")
        self.assertIsNotNone(by_key["submit_context_capture"]["workflowId"])
        self.assertTrue(by_key["submit_context_capture"]["enabled"])
        workflow_id, workflow_name = self._get_openclaw_capture_workflow()
        self.assertEqual(workflow_name, "system_openclaw_context_capture__workflow")
        self.assertEqual(by_key["submit_context_capture"]["workflowId"], workflow_id)

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

    def test_report_system_items_seed_structured_content_schema(self) -> None:
        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        by_key = {item["capabilityKey"]: item for item in items}

        weekly_content = by_key["generate_weekly_report"]["outputSchema"]["properties"]["content"]
        monthly_content = by_key["generate_monthly_report"]["outputSchema"]["properties"]["content"]

        self.assertEqual(weekly_content["type"], "object")
        self.assertTrue(weekly_content["nullable"])
        self.assertEqual(set(weekly_content["properties"]), {"summary", "suggestions", "trends"})
        self.assertEqual(monthly_content["type"], "object")
        self.assertTrue(monthly_content["nullable"])
        self.assertEqual(set(monthly_content["properties"]), {"summary", "suggestions", "trends"})

    def test_existing_report_system_item_schema_is_resynced(self) -> None:
        self.db.add(
            AppSetting(
                key="openclaw_integration_config",
                value_json={
                    "enabled": False,
                    "catalogMigrated": True,
                    "systemItemVersion": OPENCLAW_SYSTEM_ITEM_VERSION,
                },
            )
        )
        self.db.add(
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
                output_schema_json={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                input_summary="",
                output_summary="",
                tool_response_mode="json_schema",
            )
        )
        self.db.commit()

        response = self.client.get("/api/system-settings/openclaw-integration")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()["data"]["catalogItems"]
        weekly_item = next(item for item in items if item["capabilityKey"] == "generate_weekly_report")
        weekly_content = weekly_item["outputSchema"]["properties"]["content"]

        self.assertEqual(weekly_content["type"], "object")
        self.assertTrue(weekly_content["nullable"])
        self.assertEqual(set(weekly_content["properties"]), {"summary", "suggestions", "trends"})

    def test_system_item_registry_uses_routing_oriented_metadata(self) -> None:
        definitions = {item.key: item for item in list_openclaw_system_item_definitions(locale="en")}

        self.assertEqual(definitions["submit_context_capture"].tool_name, "mindatlas_submit_context_capture")
        self.assertIn("remember, save, record, or store", definitions["submit_context_capture"].description)
        self.assertIn("recent and time-bounded lookups", definitions["search_entries"].description)
        self.assertIn("entry ID is known", definitions["get_entry"].description)
        self.assertIn("connect these items", definitions["create_relation"].description)
        self.assertIn("patterns", definitions["query_knowledge_graph"].description)
        self.assertIn("what did I do this week", definitions["generate_weekly_report"].description)
        self.assertIn("what did I do this month", definitions["generate_monthly_report"].description)

    def test_generate_weekly_report_accepts_structured_content_object(self) -> None:
        secret = self._rotate_secret()
        self._enable_integration(secret)

        with patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=lambda **kwargs: {
                "id": str(uuid4()),
                "weekStart": "2026-03-23",
                "weekEnd": "2026-03-29",
                "entryCount": 4,
                "content": {
                    "summary": "本周推进顺利",
                    "suggestions": ["继续跟进自动化"],
                    "trends": "活跃度上升",
                },
                "contentLocale": "zh",
                "status": "ready",
                "attempts": 1,
                "lastError": None,
                "generatedAt": "2026-03-31T09:00:00+00:00",
                "createdAt": "2026-03-31T09:00:00+00:00",
                "updatedAt": "2026-03-31T09:00:00+00:00",
            },
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/generate_weekly_report/execute",
                headers=self._auth_headers(secret),
                json={"weekStart": "2026-03-23", "forceRegenerate": False},
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["content"]["summary"], "本周推进顺利")
        self.assertEqual(result["content"]["suggestions"], ["继续跟进自动化"])
        self.assertEqual(result["content"]["trends"], "活跃度上升")

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

        with patch(
            "app.openclaw_integration.service.OpenClawIntegrationService._build_engine",
            return_value=type(
                "Engine",
                (),
                {
                    "execute": lambda _self, *args, **kwargs: iter([
                        '{"summary":"周报摘要","suggestions":["继续推进"],"trends":"整体稳定"}'
                    ])
                },
            )(),
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

        with patch(
            "app.openclaw_integration.service.OpenClawIntegrationService._build_engine",
            return_value=type(
                "Engine",
                (),
                {
                    "execute": lambda _self, *args, **kwargs: iter([
                        '{"status":"created","entryId":"00000000-0000-0000-0000-000000000001","entryTitle":"项目复盘","entryTypeCode":"KNOWLEDGE","entryTypeName":"知识","summary":"记录已成功沉淀。","tagNames":["openclaw","mindatlas"],"timeMode":"POINT","timeAt":"2026-03-31","timeFrom":null,"timeTo":null,"createdAt":"2026-03-31T09:00:00+00:00"}'
                    ])
                },
            )(),
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/submit_context_capture/execute",
                headers=self._auth_headers(secret),
                json={
                    "intent": "record",
                    "context": "今天完成了 OpenClaw 接入方案梳理，并确认后续要收口成 workflow preset。",
                    "source": "openclaw",
                    "session": "session-1",
                    "channel": "cli",
                    "taskHint": "phase-1 capture",
                    "timeHint": "today",
                    "tagHints": ["openclaw", "mindatlas"],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]["result"]
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["entryId"], "00000000-0000-0000-0000-000000000001")
        self.assertEqual(result["entryTitle"], "项目复盘")
        self.assertEqual(result["tagNames"], ["openclaw", "mindatlas"])
        self.assertEqual(result["timeMode"], "POINT")
        self.assertEqual(result["timeAt"], "2026-03-31")
        self.assertIsNone(result["timeFrom"])
        self.assertIsNone(result["timeTo"])
        self.assertEqual(result["createdAt"], "2026-03-31T09:00:00+00:00")

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
            "app.openclaw_integration.service.OpenClawIntegrationService._build_engine",
            return_value=type(
                "Engine",
                (),
                {
                    "execute": lambda _self, *args, **kwargs: iter(['{"answer":"来自 agent 的结果"}'])
                },
            )(),
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
            "app.assistant.tools.openclaw_tools.LightRagService.query",
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
