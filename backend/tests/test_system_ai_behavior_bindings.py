from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class _FakeLangGraphEngine:
    seen_runtime_contexts: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002,ANN003
        self.args = args
        self.kwargs = kwargs

    def execute(self, **kwargs):  # noqa: ANN003
        runtime_context = kwargs.get("runtime_context") or {}
        self.__class__.seen_runtime_contexts.append(dict(runtime_context))
        yield '{"summary":"Generated summary","suggestions":["Keep going"],"trends":"Stable trend"}'


class SystemAiBehaviorBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        AssistantConfigService(self.db).ensure_system_catalog_synced()

    def tearDown(self) -> None:
        self.db.close()

    def _structured_report_workflow_input(self):
        from app.assistant_config.schemas import WorkflowInput  # noqa: E402

        return WorkflowInput.model_validate(
            {
                "nodes": [
                    {
                        "nodeId": "start",
                        "nodeType": "start",
                        "label": "Start",
                        "positionX": 120,
                        "positionY": 240,
                        "config": {
                            "inputMode": "structured",
                            "memoryMode": "off",
                            "structuredFields": [
                                {"name": "periodType", "type": "string", "required": True},
                                {"name": "periodStart", "type": "string", "required": True},
                                {"name": "periodEnd", "type": "string", "required": True},
                                {"name": "entryCount", "type": "integer", "required": True},
                            ],
                        },
                    },
                    {
                        "nodeId": "output_final",
                        "nodeType": "output",
                        "label": "Output",
                        "positionX": 520,
                        "positionY": 240,
                        "config": {
                            "outputMode": "structured",
                            "outputFields": [
                                {
                                    "name": "summary",
                                    "type": "string",
                                    "nullable": False,
                                    "value": "Summary for {{start.periodStart}}",
                                },
                                {
                                    "name": "suggestions",
                                    "type": "array",
                                    "itemsType": "string",
                                    "nullable": False,
                                    "value": '["Suggestion A", "Suggestion B"]',
                                },
                                {
                                    "name": "trends",
                                    "type": "string",
                                    "nullable": False,
                                    "value": "Trend for {{start.periodEnd}}",
                                },
                            ],
                        },
                    },
                ],
                "edges": [
                    {
                        "edgeId": "e_start_output",
                        "sourceNodeId": "start",
                        "targetNodeId": "output_final",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    }
                ],
            }
        )

    def _create_structured_report_workflow(self, name: str):
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        return svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name=name,
                description="Structured report workflow",
                enabled=True,
                workflow=self._structured_report_workflow_input(),
            )
        )

    def _create_agent_profile(self, name: str):
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        return svc.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name=name,
                description="Agent target for report behavior",
                system_prompt="Respond accurately.",
                tools=[],
                kb_config={"enabled": False},
                enabled=True,
                model_source="default",
            )
        )

    def _english_like_system_behavior_default_workflow_input(self, behavior_key: str):
        from app.assistant_config.schemas import WorkflowInput  # noqa: E402
        from app.assistant.workflow.system_assets import load_system_workflow_asset  # noqa: E402
        from app.assistant_config.system_behavior_registry import get_system_behavior_definition  # noqa: E402

        definition = get_system_behavior_definition(behavior_key, locale="zh")
        assert definition is not None
        assert definition.default_target.default_target_asset_key is not None
        preset = load_system_workflow_asset(definition.default_target.default_target_asset_key, locale="zh")
        payload = preset.model_dump(by_alias=True)
        nodes = payload.get("nodes", [])
        for node in nodes:
            node_id = node.get("nodeId")
            if node_id == "start":
                node["label"] = "Start"
            elif node_id == "tool_entries":
                node["label"] = "Load Entries"
            elif node_id == "tool_activity":
                node["label"] = "Analyze Activity"
            elif node_id == "llm_report":
                node["label"] = "Compose Weekly Report" if behavior_key == "weekly_report_generation" else "Compose Monthly Report"
                config = node.get("config") if isinstance(node.get("config"), dict) else {}
                config["systemPrompt"] = (
                    "Generate a concise weekly report for MindAtlas based on the provided period, entries, and activity analysis."
                    if behavior_key == "weekly_report_generation"
                    else "Generate a concise monthly report for MindAtlas based on the provided period, entries, and activity analysis."
                )
                config["userInput"] = (
                    "Report period: {{start.periodType}}\n"
                    "Start: {{start.periodStart}}\n"
                    "End: {{start.periodEnd}}\n"
                    "Entry count: {{start.entryCount}}\n\n"
                    "Entries:\n{{tool_entries.result}}\n\n"
                    "Activity analysis:\n{{tool_activity.result}}"
                )
                node["config"] = config
            elif node_id == "output_final":
                node["label"] = "Output"
        return WorkflowInput.model_validate(payload)

    @staticmethod
    def _node_by_id(workflow, node_id: str):
        return next(node for node in workflow.nodes if node.node_id == node_id)

    def test_list_system_behaviors_initializes_defaults(self) -> None:
        from app.assistant_config.models import AssistantSystemBehaviorBinding, AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        items = svc.list_system_behaviors()

        self.assertEqual(
            {item["behavior_key"] for item in items},
            {"weekly_report_generation", "monthly_report_generation"},
        )
        self.assertEqual(self.db.query(AssistantSystemBehaviorBinding).count(), 2)

        workflow_names = {
            name
            for name, in self.db.query(AssistantWorkflow.name)
            .filter(AssistantWorkflow.is_system.is_(True))
            .all()
        }
        self.assertIn("system_weekly_report__workflow", workflow_names)
        self.assertIn("system_monthly_report__workflow", workflow_names)

    def test_update_system_behavior_binding_accepts_structured_workflow(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        workflow = self._create_structured_report_workflow(f"report_wf_{uuid4().hex[:8]}")
        svc = AssistantConfigService(self.db)
        payload = svc.update_system_behavior_binding(
            behavior_key="weekly_report_generation",
            target_type="workflow",
            workflow_id=workflow.id,
        )

        self.assertEqual(payload["current_binding"]["target_type"], "workflow")
        self.assertEqual(payload["current_binding"]["workflow_id"], workflow.id)
        self.assertFalse(payload["current_binding"]["is_canonical_default"])

    def test_update_system_behavior_binding_rejects_unstructured_workflow(self) -> None:
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name=f"plain_wf_{uuid4().hex[:8]}",
                description="Plain workflow",
                enabled=True,
            )
        )

        with self.assertRaises(ApiException) as ctx:
            svc.update_system_behavior_binding(
                behavior_key="weekly_report_generation",
                target_type="workflow",
                workflow_id=workflow.id,
            )

        self.assertIn(ctx.exception.code, {42248, 42249, 42250})

    def test_update_system_behavior_binding_rejects_agent_without_explicit_contract(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        agent = self._create_agent_profile(f"report_agent_{uuid4().hex[:8]}")
        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.update_system_behavior_binding(
                behavior_key="monthly_report_generation",
                target_type="agent",
                agent_profile_id=agent.id,
            )

        self.assertEqual(ctx.exception.code, 42276)

    def test_create_example_workflow_creates_without_binding_by_default(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.assistant_config.system_behavior_registry import get_system_behavior_definition  # noqa: E402

        svc = AssistantConfigService(self.db)
        before = svc.list_system_behaviors()
        before_binding_id = next(
            item["current_binding"]["workflow_id"]
            for item in before
            if item["behavior_key"] == "weekly_report_generation"
        )
        payload = svc.create_system_behavior_example_workflow("weekly_report_generation")

        created = payload["created_workflow"]
        self.assertEqual(created["name"], "weekly_report_example__workflow")
        self.assertFalse(created["is_system"])
        self.assertTrue(created["enabled"])
        self.assertIsNotNone(created["published_version_id"])
        self.assertEqual(payload["system_behavior"]["current_binding"]["workflow_id"], before_binding_id)

        workflow = svc.get_workflow(created["id"])
        definition = get_system_behavior_definition("weekly_report_generation")
        self.assertIsNotNone(definition)
        svc._validate_system_behavior_workflow_target(definition=definition, workflow=workflow)  # noqa: SLF001

    def test_create_example_workflow_can_bind_when_requested(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        payload = svc.create_system_behavior_example_workflow(
            "weekly_report_generation",
            bind_to_behavior=True,
        )

        self.assertEqual(
            payload["system_behavior"]["current_binding"]["workflow_id"],
            payload["created_workflow"]["id"],
        )

    def test_create_example_workflow_names_increment(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        first = svc.create_system_behavior_example_workflow("weekly_report_generation")
        second = svc.create_system_behavior_example_workflow("weekly_report_generation")

        self.assertEqual(first["created_workflow"]["name"], "weekly_report_example__workflow")
        self.assertEqual(second["created_workflow"]["name"], "weekly_report_example__workflow__2")

    def test_list_system_behaviors_reconciles_mutated_default_workflow(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        defaults = svc.list_system_behaviors()
        weekly_default_id = next(
            item["canonical_default_target"]["id"]
            for item in defaults
            if item["behavior_key"] == "weekly_report_generation"
        )

        weekly_default = svc.get_workflow(weekly_default_id)
        english_input = self._english_like_system_behavior_default_workflow_input("weekly_report_generation")
        svc._apply_workflow_to_workflow_entity(weekly_default, english_input, persist=True)  # noqa: SLF001
        mutated_version = svc._create_workflow_version(  # noqa: SLF001
            workflow=weekly_default,
            workflow_input=english_input,
            version_source="publish",
            version_name="Mutated English",
        )
        weekly_default.draft_version_id = mutated_version.id
        weekly_default.published_version_id = mutated_version.id
        self.db.commit()

        items = svc.list_system_behaviors()
        weekly = next(item for item in items if item["behavior_key"] == "weekly_report_generation")
        self.assertTrue(weekly["current_binding"]["is_canonical_default"])

        restored = svc.get_workflow(weekly_default_id)
        self.db.refresh(restored)
        self.assertEqual(self._node_by_id(restored, "tool_entries").label, "加载记录")
        self.assertEqual(self._node_by_id(restored, "llm_report").label, "生成周报")
        self.assertIn("请为 MindAtlas 生成一份简洁、扎实的中文周报", self._node_by_id(restored, "llm_report").config["systemPrompt"])
        version_count = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == weekly_default_id)
            .count()
        )
        self.assertEqual(version_count, 1)

    def test_reset_system_behavior_binding_restores_latest_chinese_default_workflow(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        defaults = svc.list_system_behaviors()
        weekly_default_id = next(
            item["canonical_default_target"]["id"]
            for item in defaults
            if item["behavior_key"] == "weekly_report_generation"
        )

        weekly_default = svc.get_workflow(weekly_default_id)
        english_input = self._english_like_system_behavior_default_workflow_input("weekly_report_generation")
        svc._apply_workflow_to_workflow_entity(weekly_default, english_input, persist=True)  # noqa: SLF001
        mutated_version = svc._create_workflow_version(  # noqa: SLF001
            workflow=weekly_default,
            workflow_input=english_input,
            version_source="publish",
            version_name="Mutated English",
        )
        weekly_default.draft_version_id = mutated_version.id
        weekly_default.published_version_id = mutated_version.id
        weekly_default.description = "English weekly default"
        self.db.commit()

        self.assertEqual(weekly_default.description, "English weekly default")
        self.assertEqual(self._node_by_id(weekly_default, "tool_entries").label, "Load Entries")

        svc.reset_system_behavior_binding("weekly_report_generation")

        reset_workflow = svc.get_workflow(weekly_default_id)
        self.assertEqual(self._node_by_id(reset_workflow, "start").label, "开始")
        self.assertEqual(self._node_by_id(reset_workflow, "tool_entries").label, "加载记录")
        self.assertEqual(self._node_by_id(reset_workflow, "tool_activity").label, "分析活动")
        self.assertEqual(self._node_by_id(reset_workflow, "llm_report").label, "生成周报")
        self.assertEqual(self._node_by_id(reset_workflow, "output_final").label, "输出")
        self.assertIn("请为 MindAtlas 生成一份简洁、扎实的中文周报", self._node_by_id(reset_workflow, "llm_report").config["systemPrompt"])
        self.assertIn("报告周期：{{start.periodType}}", self._node_by_id(reset_workflow, "llm_report").config["userInput"])
        self.assertEqual(reset_workflow.description, "通过可复用的 Workflow 或 Agent 生成系统周报。")

        version_count = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == weekly_default_id)
            .count()
        )
        self.assertEqual(version_count, 1)

    def test_reset_all_system_behaviors_rebinds_everything_to_defaults(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        defaults = svc.list_system_behaviors()
        monthly_default_id = next(
            item["canonical_default_target"]["id"]
            for item in defaults
            if item["behavior_key"] == "monthly_report_generation"
        )
        monthly_default = svc.get_workflow(monthly_default_id)
        english_input = self._english_like_system_behavior_default_workflow_input("monthly_report_generation")
        svc._apply_workflow_to_workflow_entity(monthly_default, english_input, persist=True)  # noqa: SLF001
        mutated_version = svc._create_workflow_version(  # noqa: SLF001
            workflow=monthly_default,
            workflow_input=english_input,
            version_source="publish",
            version_name="Mutated English",
        )
        monthly_default.draft_version_id = mutated_version.id
        monthly_default.published_version_id = mutated_version.id
        monthly_default.description = "English monthly default"
        self.db.commit()

        custom_workflow = self._create_structured_report_workflow(f"reset_all_wf_{uuid4().hex[:8]}")
        custom_monthly_workflow = self._create_structured_report_workflow(f"reset_all_monthly_wf_{uuid4().hex[:8]}")

        svc.update_system_behavior_binding(
            behavior_key="weekly_report_generation",
            target_type="workflow",
            workflow_id=custom_workflow.id,
        )
        svc.update_system_behavior_binding(
            behavior_key="monthly_report_generation",
            target_type="workflow",
            workflow_id=custom_monthly_workflow.id,
        )

        payload = svc.reset_all_system_behaviors(confirm=True)

        self.assertEqual(payload["reset_count"], 2)
        self.assertEqual(
            {item["behavior_key"] for item in payload["affected"]},
            {"weekly_report_generation", "monthly_report_generation"},
        )

        refreshed = svc.list_system_behaviors()
        self.assertTrue(all(item["current_binding"]["is_canonical_default"] for item in refreshed))
        self.assertTrue(all(item["current_binding"]["target_type"] == "workflow" for item in refreshed))
        self.assertEqual(
            {item["current_binding"]["name"] for item in refreshed},
            {"周报生成工作流", "月报生成工作流"},
        )

        monthly_reset = svc.get_workflow(monthly_default_id)
        self.assertEqual(self._node_by_id(monthly_reset, "tool_entries").label, "加载记录")
        self.assertEqual(self._node_by_id(monthly_reset, "llm_report").label, "生成月报")
        self.assertIn("请为 MindAtlas 生成一份简洁、扎实的中文月报", self._node_by_id(monthly_reset, "llm_report").config["systemPrompt"])
        self.assertIn("报告周期：{{start.periodType}}", self._node_by_id(monthly_reset, "llm_report").config["userInput"])
        version_count = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == monthly_default_id)
            .count()
        )
        self.assertEqual(version_count, 1)

    def test_delete_workflow_requires_confirm_then_rebinds_to_default(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = self._create_structured_report_workflow(f"delete_wf_{uuid4().hex[:8]}")
        svc.update_system_behavior_binding(
            behavior_key="weekly_report_generation",
            target_type="workflow",
            workflow_id=workflow.id,
        )

        with self.assertRaises(ApiException) as ctx:
            svc.delete_workflow(workflow.id)
        self.assertEqual(ctx.exception.code, 40961)

        svc.delete_workflow(workflow.id, confirm_rebind_system_behaviors=True)

        rebound = svc.list_system_behaviors()
        weekly = next(item for item in rebound if item["behavior_key"] == "weekly_report_generation")
        self.assertTrue(weekly["current_binding"]["is_canonical_default"])
        deleted = self.db.query(AssistantWorkflow).filter(AssistantWorkflow.id == workflow.id).first()
        self.assertIsNone(deleted)

    def test_runner_falls_back_to_canonical_default_when_bound_workflow_disabled(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.assistant_config.system_behavior_runner import SystemAiBehaviorRunInput, SystemAiBehaviorRunner  # noqa: E402

        _FakeLangGraphEngine.seen_runtime_contexts = []

        svc = AssistantConfigService(self.db)
        defaults = svc.list_system_behaviors()
        weekly_default_id = next(
            item["canonical_default_target"]["id"]
            for item in defaults
            if item["behavior_key"] == "weekly_report_generation"
        )
        workflow = self._create_structured_report_workflow(f"fallback_wf_{uuid4().hex[:8]}")
        svc.update_system_behavior_binding(
            behavior_key="weekly_report_generation",
            target_type="workflow",
            workflow_id=workflow.id,
        )
        workflow.enabled = False
        self.db.commit()

        runner = SystemAiBehaviorRunner(self.db)
        with patch(
            "app.assistant_config.system_behavior_runner.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.system_behavior_runner.LangGraphEngine",
            _FakeLangGraphEngine,
        ):
            payload = runner.run_report_behavior(
                behavior_key="weekly_report_generation",
                payload=SystemAiBehaviorRunInput(
                    period_type="weekly",
                    period_start=date(2026, 3, 16),
                    period_end=date(2026, 3, 22),
                    entry_count=0,
                ),
            )

        self.assertEqual(payload["summary"], "Generated summary")
        self.assertTrue(_FakeLangGraphEngine.seen_runtime_contexts)
        self.assertEqual(
            _FakeLangGraphEngine.seen_runtime_contexts[-1].get("workflow_id"),
            str(weekly_default_id),
        )
        self.assertEqual(_FakeLangGraphEngine.seen_runtime_contexts[-1].get("locale"), "zh")

    def test_weekly_report_service_uses_system_behavior_runner(self) -> None:
        from app.report.service import WeeklyReportService  # noqa: E402
        from app.system_settings.service import SystemSettingsService  # noqa: E402

        _FakeLangGraphEngine.seen_runtime_contexts = []
        SystemSettingsService(self.db).set_locale("en")

        service = WeeklyReportService(self.db)
        report = service.get_or_create_for_week(date(2026, 3, 16))

        with patch(
            "app.assistant_config.system_behavior_runner.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.system_behavior_runner.LangGraphEngine",
            _FakeLangGraphEngine,
        ):
            report = service.generate_report(report)

        self.assertEqual(report.status, "completed")
        self.assertEqual(report.content["summary"], "Generated summary")
        self.assertEqual(report.content["suggestions"], ["Keep going"])
        self.assertEqual(report.content["trends"], "Stable trend")
        self.assertIsNotNone(report.generated_at)
        self.assertEqual(report.content_locale, "en")
        self.assertEqual(_FakeLangGraphEngine.seen_runtime_contexts[-1].get("locale"), "en")
        SystemSettingsService(self.db).set_locale("zh")
        self.assertTrue(service.should_generate_report(report))


if __name__ == "__main__":
    unittest.main()
