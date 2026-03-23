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

    def test_update_system_behavior_binding_accepts_agent(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        agent = self._create_agent_profile(f"report_agent_{uuid4().hex[:8]}")
        svc = AssistantConfigService(self.db)
        payload = svc.update_system_behavior_binding(
            behavior_key="monthly_report_generation",
            target_type="agent",
            agent_profile_id=agent.id,
        )

        self.assertEqual(payload["current_binding"]["target_type"], "agent")
        self.assertEqual(payload["current_binding"]["agent_profile_id"], agent.id)

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

    def test_weekly_report_service_uses_system_behavior_runner(self) -> None:
        from app.report.service import WeeklyReportService  # noqa: E402

        _FakeLangGraphEngine.seen_runtime_contexts = []

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


if __name__ == "__main__":
    unittest.main()
