from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()
import app.ai_registry.models  # noqa: F401,E402


EXPECTED_CONTEXT_CAPTURE_POSITIONS = {
    "start": (80, 320),
    "tool_types": (490, 245),
    "tool_tags": (490, 396),
    "llm_materialize": (900, 320),
    "llm_prepare_lookup": (1310, 320),
    "tool_search_similar": (1720, 320),
    "code_pick_top1": (2130, 320),
    "llm_decide": (2540, 320),
    "if_route": (2950, 320),
    "tool_get_existing": (3360, 245),
    "tool_create": (3360, 396),
    "llm_merge_rewrite": (3770, 245),
    "tool_update": (4180, 245),
    "output_created": (3770, 396),
    "output_merged": (4590, 245),
}


class AssistantConfigServiceMoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _system_workflow(self):
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.ensure_system_catalog_synced()
        return svc, next(item for item in svc.list_workflows(include_disabled=True) if item.is_system)

    def _system_agent(self):
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.ensure_system_catalog_synced()
        return svc, next(item for item in svc.list_agent_profiles(include_disabled=True) if item.is_system)

    def test_sync_system_skills_and_list_workflows_keep_system_workflow_edges_unique(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)

        for _ in range(3):
            svc.sync_system_skills()
            svc.list_workflows(include_disabled=True)

        workflows = [item for item in svc.list_workflows(include_disabled=True) if item.is_system]
        self.assertTrue(workflows)
        for workflow in workflows:
            edge_keys = {
                (edge.source_node_id, edge.source_handle, edge.target_node_id, edge.target_handle)
                for edge in workflow.edges
            }
            self.assertEqual(len(workflow.edges), len(edge_keys))

    def test_system_periodic_review_workflow_resolves_core_asset_to_pinned_target(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        wrapper = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "periodic_review__workflow")
            .first()
        )
        core = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_periodic_review_core__workflow")
            .first()
        )

        self.assertIsNotNone(wrapper)
        self.assertIsNotNone(core)
        assert wrapper is not None
        assert core is not None
        self.assertIsNotNone(core.published_version_id)

        call_node = next(node for node in (wrapper.nodes or []) if node.node_id == "call_core")
        cfg = dict(call_node.config or {})

        self.assertEqual(cfg.get("targetWorkflowId"), str(core.id))
        self.assertEqual(cfg.get("targetPublishedVersionId"), str(core.published_version_id))
        self.assertEqual(cfg.get("bindingMode"), "pinned")
        self.assertNotIn("targetSystemAssetKey", cfg)

    def test_system_smart_capture_workflow_resolves_relation_followup_asset_to_pinned_target(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        wrapper = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "smart_capture__workflow")
            .first()
        )
        followup = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_smart_capture_relation_followup__workflow")
            .first()
        )

        self.assertIsNotNone(wrapper)
        self.assertIsNotNone(followup)
        assert wrapper is not None
        assert followup is not None
        self.assertIsNotNone(followup.published_version_id)

        call_node = next(node for node in (wrapper.nodes or []) if node.node_id == "call_relation_followup")
        cfg = dict(call_node.config or {})

        self.assertEqual(cfg.get("targetWorkflowId"), str(followup.id))
        self.assertEqual(cfg.get("targetPublishedVersionId"), str(followup.published_version_id))
        self.assertEqual(cfg.get("bindingMode"), "pinned")
        self.assertNotIn("targetSystemAssetKey", cfg)

    def test_sync_standalone_system_targets_creates_context_capture_workflow_without_system_skill(self) -> None:
        from app.assistant_config.models import AssistantSkill, AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_context_capture__workflow")
            .first()
        )
        self.assertIsNotNone(workflow)
        self.assertTrue(bool(workflow.is_system))
        linked_system_skills = (
            self.db.query(AssistantSkill)
            .filter(
                AssistantSkill.is_system.is_(True),
                AssistantSkill.workflow_id == workflow.id,
            )
            .all()
        )
        self.assertEqual(linked_system_skills, [])

    def test_sync_standalone_system_targets_can_republish_existing_workflow_without_duplicate_edges(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_context_capture__workflow")
            .first()
        )
        self.assertIsNotNone(workflow)
        workflow_id = workflow.id
        workflow.published_version_id = None
        self.db.commit()
        self.db.expunge_all()

        other_svc = AssistantConfigService(self.db)
        other_svc.sync_standalone_system_targets()

        refreshed = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.id == workflow_id)
            .first()
        )
        self.assertIsNotNone(refreshed)
        self.assertIsNotNone(refreshed.published_version_id)

        edge_keys = {
            (edge.source_node_id, edge.source_handle, edge.target_node_id, edge.target_handle)
            for edge in refreshed.edges
        }
        self.assertEqual(len(refreshed.edges), len(edge_keys))

    def test_sync_standalone_system_targets_renames_legacy_capture_workflow_in_place(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        legacy = AssistantWorkflow(
            name="system_openclaw_context_capture__workflow",
            description="legacy",
            workflow_version=0,
            workflow_viewport=None,
            is_system=True,
            enabled=True,
        )
        self.db.add(legacy)
        self.db.commit()
        legacy_id = legacy.id

        svc = AssistantConfigService(self.db)
        svc.sync_standalone_system_targets()

        refreshed = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.id == legacy_id)
            .first()
        )
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.name, "system_context_capture__workflow")
        self.assertIsNotNone(refreshed.published_version_id)
        self.assertIsNone(
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_openclaw_context_capture__workflow")
            .first()
        )

    def test_sync_standalone_system_targets_rejects_custom_name_conflict(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        self.db.add(
            AssistantWorkflow(
                name="system_context_capture__workflow",
                description="custom conflict",
                workflow_version=0,
                workflow_viewport=None,
                is_system=False,
                enabled=True,
            )
        )
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.sync_standalone_system_targets()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40946)

    def test_standalone_system_workflow_uses_localized_display_name_and_callable_listing(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        serialized = svc.serialize_workflow(workflow)
        callable_items = svc.list_callable_workflows()
        callable_item = next(item for item in callable_items if item["id"] == workflow.id)

        self.assertEqual(serialized["name"], "智能上下文入库工作流")
        self.assertEqual(callable_item["name"], "智能上下文入库工作流")
        self.assertIn("先提取检索线索与最终字段", serialized["description"])

    def test_serialize_targets_include_openclaw_reference_count(self) -> None:
        from app.openclaw_integration.models import OpenClawCapabilityItem  # noqa: E402

        workflow_svc, workflow = self._system_workflow()
        agent_svc, agent = self._system_agent()

        self.db.add_all(
            [
                OpenClawCapabilityItem(
                    capability_key="test_workflow_capability",
                    tool_name="mindatlas_test_workflow_capability",
                    title="Workflow Capability",
                    description="workflow reference",
                    source_type="workflow",
                    workflow_id=workflow.id,
                    enabled=True,
                    is_system_item=False,
                    input_schema_json={},
                    output_schema_json={},
                    input_summary="",
                    output_summary="",
                    tool_response_mode="json_schema",
                ),
                OpenClawCapabilityItem(
                    capability_key="test_agent_capability",
                    tool_name="mindatlas_test_agent_capability",
                    title="Agent Capability",
                    description="agent reference",
                    source_type="agent",
                    agent_profile_id=agent.id,
                    enabled=True,
                    is_system_item=False,
                    input_schema_json={},
                    output_schema_json={},
                    input_summary="",
                    output_summary="",
                    tool_response_mode="json_schema",
                ),
            ]
        )
        self.db.commit()

        workflow_serialized = workflow_svc.serialize_workflow(workflow_svc.get_workflow(workflow.id))
        agent_serialized = agent_svc.serialize_agent_profile(agent_svc.get_agent_profile(agent.id))

        self.assertEqual(workflow_serialized["openclaw_reference_count"], 1)
        self.assertEqual(agent_serialized["openclaw_reference_count"], 1)

    def test_list_serializers_omit_heavy_workflow_graph_and_agent_draft_fields(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        workflow_summary = svc.serialize_workflow_summary(workflow)
        self.assertFalse(workflow_summary["details_loaded"])
        self.assertEqual(workflow_summary["nodes"], [])
        self.assertEqual(workflow_summary["edges"], [])
        self.assertIsNone(workflow_summary["workflow_viewport"])

        agent = next(item for item in svc.list_agent_profiles(include_disabled=True) if item.is_system)
        agent_summary = svc.serialize_agent_profile_summary(agent)
        self.assertFalse(agent_summary["details_loaded"])
        self.assertIsNone(agent_summary["system_prompt"])
        self.assertIsNone(agent_summary["tools"])
        self.assertIsNone(agent_summary["kb_config"])

        workflow_detail = svc.serialize_workflow(svc.get_workflow(workflow.id))
        agent_detail = svc.serialize_agent_profile(svc.get_agent_profile(agent.id))
        self.assertTrue(workflow_detail["details_loaded"])
        self.assertTrue(len(workflow_detail["nodes"]) > 0)
        self.assertTrue(agent_detail["details_loaded"])
        self.assertIsInstance(agent_detail["system_prompt"], str)

    def test_standalone_system_workflow_start_field_description_explains_create_vs_merge_context(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        start_node = next(node for node in workflow.nodes if node.node_id == "start")
        start_cfg = dict(start_node.config or {})
        structured_fields = start_cfg.get("structuredFields") or start_cfg.get("structured_fields") or []
        context_field = next(item for item in structured_fields if item.get("name") == "context")

        self.assertIn("新建记录还是修正、合并到已有记录", str(context_field.get("description") or ""))

    def test_standalone_system_workflow_uses_lookup_preparation_and_top1_merge_gate(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        node_by_id = {node.node_id: node for node in (workflow.nodes or [])}

        start_cfg = dict(node_by_id["start"].config or {})
        self.assertEqual(start_cfg.get("memoryMode"), "off")
        self.assertIn("llm_prepare_lookup", node_by_id)
        self.assertIn("tool_tags", node_by_id)
        self.assertIn("tool_search_similar", node_by_id)
        self.assertIn("code_pick_top1", node_by_id)

        materialize_sources = {
            edge.source_node_id
            for edge in (workflow.edges or [])
            if edge.target_node_id == "llm_materialize"
        }
        self.assertEqual(materialize_sources, {"tool_types", "tool_tags"})

        lookup_sources = {
            edge.source_node_id
            for edge in (workflow.edges or [])
            if edge.target_node_id == "llm_prepare_lookup"
        }
        self.assertEqual(lookup_sources, {"llm_materialize"})

        search_cfg = dict(node_by_id["tool_search_similar"].config or {})
        search_bindings = search_cfg.get("inputBindings") or search_cfg.get("input_bindings") or {}
        self.assertEqual(search_cfg.get("toolName") or search_cfg.get("tool_name"), "search_similar_entries")
        self.assertEqual(search_bindings.get("query"), "{{llm_prepare_lookup.lookup_query}}")
        self.assertEqual(search_bindings.get("limit"), "8")

        top1_sources = {
            edge.source_node_id
            for edge in (workflow.edges or [])
            if edge.target_node_id == "code_pick_top1"
        }
        self.assertEqual(top1_sources, {"tool_search_similar"})

        decide_sources = {
            edge.source_node_id
            for edge in (workflow.edges or [])
            if edge.target_node_id == "llm_decide"
        }
        self.assertEqual(decide_sources, {"code_pick_top1"})

        decide_cfg = dict(node_by_id["llm_decide"].config or {})
        output_fields = decide_cfg.get("outputFields") or decide_cfg.get("output_fields") or []
        output_names = {item.get("name") for item in output_fields if isinstance(item, dict)}
        self.assertEqual(output_names, {"action", "entry_id", "reason"})
        decide_user_input = str(decide_cfg.get("userInput") or decide_cfg.get("user_input") or "")
        self.assertIn("top1_candidate", decide_user_input)
        self.assertIn("candidate_found", decide_user_input)
        self.assertNotIn("primary_candidates", decide_user_input)
        self.assertNotIn("secondary_candidates", decide_user_input)

        route_cfg = dict(node_by_id["if_route"].config or {})
        branches = route_cfg.get("branches") or []
        merge_branch = next(item for item in branches if item.get("id") == "merge")
        merge_conditions = merge_branch.get("conditions") or []
        self.assertIn(
            {
                "id": "merge_action",
                "variable": "llm_decide.action",
                "operator": "is",
                "value": "merge",
            },
            merge_conditions,
        )
        self.assertIn(
            {
                "id": "merge_entry_id",
                "variable": "llm_decide.entry_id",
                "operator": "is_not_empty",
                "value": "",
            },
            merge_conditions,
        )

    def test_standalone_system_workflow_context_capture_uses_horizontal_parallel_layout(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        position_map = {
            node.node_id: (int(round(float(node.position_x))), int(round(float(node.position_y))))
            for node in (workflow.nodes or [])
        }
        for node_id, expected in EXPECTED_CONTEXT_CAPTURE_POSITIONS.items():
            self.assertEqual(position_map.get(node_id), expected, f"context_capture.{node_id} position mismatch")

        for edge in (workflow.edges or []):
            source = next(node for node in workflow.nodes if node.node_id == edge.source_node_id)
            target = next(node for node in workflow.nodes if node.node_id == edge.target_node_id)
            self.assertGreater(
                int(round(float(target.position_x))),
                int(round(float(source.position_x))),
                f"context_capture edge {edge.edge_id} should flow left-to-right",
            )

    def test_standalone_system_workflow_prompts_include_lookup_and_merge_guardrails(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()

        workflow = next(
            item for item in svc.list_workflows(include_disabled=True)
            if item.name == "system_context_capture__workflow"
        )
        node_by_id = {node.node_id: node for node in (workflow.nodes or [])}

        lookup_prompt = str(dict(node_by_id["llm_prepare_lookup"].config or {}).get("systemPrompt") or "")
        decide_prompt = str(dict(node_by_id["llm_decide"].config or {}).get("systemPrompt") or "")
        merge_prompt = str(dict(node_by_id["llm_merge_rewrite"].config or {}).get("systemPrompt") or "")

        self.assertIn("稳定主体/持久对象", lookup_prompt)
        self.assertIn("不要把整句原文照抄", lookup_prompt)
        self.assertIn("只负责找候选", decide_prompt)
        self.assertIn("宁可多建一条，也不要错并", decide_prompt)
        self.assertIn("兜底默认值", merge_prompt)
        self.assertIn("今天", merge_prompt)

    def test_system_target_audit_reports_only_expected_origins(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        svc.sync_standalone_system_targets()
        svc.ensure_system_behaviors()

        clean_report = svc._audit_system_target_origins()  # noqa: SLF001
        self.assertEqual(clean_report["unexpectedWorkflows"], [])
        self.assertEqual(clean_report["unexpectedAgents"], [])

        self.db.add(
            AssistantWorkflow(
                name="system_unclassified__workflow",
                description="orphan",
                workflow_version=0,
                workflow_viewport=None,
                is_system=True,
                enabled=True,
            )
        )
        self.db.commit()

        dirty_report = svc._audit_system_target_origins()  # noqa: SLF001
        names = {item["name"] for item in dirty_report["unexpectedWorkflows"]}
        self.assertIn("system_unclassified__workflow", names)

    def test_ensure_system_behaviors_reuses_resolved_default_workflow_for_binding_creation(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.assistant_config.system_behavior_registry import list_system_behavior_definitions  # noqa: E402

        svc = AssistantConfigService(self.db)
        expected_count = len(list_system_behavior_definitions(locale=svc._current_locale()))  # noqa: SLF001

        with patch.object(
            AssistantConfigService,
            "_ensure_system_behavior_default_workflow",
            wraps=svc._ensure_system_behavior_default_workflow,
        ) as mocked:
            svc.ensure_system_behaviors()

        self.assertEqual(mocked.call_count, expected_count)

    def test_ensure_system_catalog_synced_runs_component_syncs_without_nested_commits(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)

        with patch.object(svc, "_acquire_system_catalog_sync_lock") as lock_mock, patch.object(
            svc, "sync_system_tools"
        ) as tools_mock, patch.object(svc, "sync_system_skills") as skills_mock, patch.object(
            svc, "sync_standalone_system_targets"
        ) as standalone_mock, patch.object(svc, "ensure_system_behaviors") as behaviors_mock, patch.object(
            self.db, "commit"
        ) as commit_mock:
            svc.ensure_system_catalog_synced()

        lock_mock.assert_called_once_with()
        tools_mock.assert_called_once_with(commit=False)
        skills_mock.assert_called_once_with(commit=False)
        standalone_mock.assert_called_once_with(commit=False)
        behaviors_mock.assert_called_once_with(commit=False)
        commit_mock.assert_called_once_with()

    def test_read_endpoints_do_not_trigger_system_catalog_sync(self) -> None:
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest, AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name=f"read_only_wf_{uuid4().hex[:8]}",
                description="read only workflow",
                enabled=True,
            )
        )
        agent = svc.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name=f"read_only_agent_{uuid4().hex[:8]}",
                description="read only agent",
                system_prompt="Stay helpful.",
                tools=[],
                kb_config={"enabled": False},
                enabled=True,
                model_source="default",
            )
        )

        with patch.object(svc, "ensure_system_catalog_synced") as sync_mock, patch.object(
            svc, "sync_system_tools"
        ) as tools_sync_mock, patch.object(svc, "sync_system_skills") as skills_sync_mock:
            svc.list_tools(sync_system=True, include_disabled=True)
            svc.list_skills(sync_system=True, include_disabled=True)
            svc.list_workflows(include_disabled=True)
            svc.get_workflow(workflow.id)
            svc.list_agent_profiles(include_disabled=True)
            svc.get_agent_profile(agent.id)
            svc.list_callable_workflows()
            svc.list_system_behaviors()

        sync_mock.assert_not_called()
        tools_sync_mock.assert_not_called()
        skills_sync_mock.assert_not_called()

    def test_system_catalog_warm_skips_full_sync_when_signature_matches(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.ensure_system_catalog_synced()

        with patch.object(svc, "_sync_system_catalog_locked") as sync_mock:
            changed = svc.ensure_system_catalog_warm()

        self.assertFalse(changed)
        sync_mock.assert_not_called()

    def test_system_catalog_warm_runs_full_sync_when_signature_missing(self) -> None:
        from app.assistant_config.service import (
            AssistantConfigService,
            _SYSTEM_CATALOG_SIGNATURE_SETTING_KEY,
        )  # noqa: E402
        from app.system_settings.models import AppSetting  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.ensure_system_catalog_synced()
        self.db.query(AppSetting).filter(AppSetting.key == _SYSTEM_CATALOG_SIGNATURE_SETTING_KEY).delete()
        self.db.commit()

        with patch.object(svc, "_sync_system_catalog_locked") as sync_mock:
            changed = svc.ensure_system_catalog_warm()

        self.assertTrue(changed)
        sync_mock.assert_called_once()

    def test_system_catalog_warm_runs_full_sync_when_expected_asset_is_missing(self) -> None:
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.ensure_system_catalog_synced()
        (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == "system_context_capture__workflow")
            .delete(synchronize_session=False)
        )
        self.db.commit()

        with patch.object(svc, "_sync_system_catalog_locked") as sync_mock:
            changed = svc.ensure_system_catalog_warm()

        self.assertTrue(changed)
        sync_mock.assert_called_once()

    def test_startup_catalog_warmup_runs_explicit_sync_once(self) -> None:
        from app.assistant_config.bootstrap import warm_assistant_config_system_catalog  # noqa: E402

        fake_db = unittest.mock.MagicMock()
        with patch("app.assistant_config.bootstrap.SessionLocal", return_value=fake_db) as session_mock, patch(
            "app.assistant_config.bootstrap.AssistantConfigService"
        ) as service_cls:
            warm_assistant_config_system_catalog()

        session_mock.assert_called_once_with()
        service_cls.assert_called_once_with(fake_db)
        service_cls.return_value.ensure_system_catalog_warm.assert_called_once_with()
        fake_db.close.assert_called_once_with()

    def test_create_update_delete_remote_tool(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.schemas import AssistantToolCreateRequest, AssistantToolUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.encrypt_api_key", return_value="enc"), patch(
            "app.assistant_config.service.api_key_hint", return_value="****"
        ):
            tool = svc.create_tool(
                AssistantToolCreateRequest(
                    name="rt",
                    description="d",
                    kind="remote",
                    enabled=True,
                    endpoint_url="https://api.example.com/endpoint",
                    http_method="POST",
                    api_key="k",
                )
            )
        self.assertEqual(self.db.query(AssistantTool).count(), 1)

        tool2 = svc.update_tool(
            tool.id,
            AssistantToolUpdateRequest(description="d2", timeout_seconds=10),
        )
        self.assertEqual(tool2.description, "d2")
        self.assertEqual(tool2.timeout_seconds, 10)

        svc.delete_tool(tool.id)
        self.assertEqual(self.db.query(AssistantTool).count(), 0)

    def test_create_update_delete_skill_non_system(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillCreateRequest, AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="s1",
                description="d",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="agent_loop",
                system_prompt="sys",
                enabled=True,
            )
        )
        self.assertEqual(self.db.query(AssistantSkill).count(), 1)

        updated = svc.update_skill(created.id, AssistantSkillUpdateRequest(description="d2"))
        self.assertEqual(updated.description, "d2")

        svc.delete_skill(created.id)
        self.assertIsNone(self.db.query(AssistantSkill).filter(AssistantSkill.id == created.id).first())
        self.assertEqual(self.db.query(AssistantSkill).filter(AssistantSkill.is_system.is_(False)).count(), 0)

    def test_create_skill_langgraph_persists_pattern(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="lg1",
                description="d",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="agent_loop",
                system_prompt="sys",
                enabled=True,
            )
        )
        self.assertEqual(created.mode, "langgraph")
        self.assertEqual(created.langgraph_pattern, "agent_loop")

    def test_create_workflow_skill_without_workflow_seeds_default_graph(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="wf_seed_default",
                description="d",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="workflow_dag",
                enabled=True,
            )
        )

        self.assertIsNotNone(created.workflow)
        node_by_id = {node.node_id: node for node in ((created.workflow.nodes if created.workflow else []) or [])}
        edge_pairs = {
            (edge.source_node_id, edge.target_node_id)
            for edge in ((created.workflow.edges if created.workflow else []) or [])
        }

        self.assertSetEqual(set(node_by_id.keys()), {"start", "llm_1", "output_1"})
        self.assertEqual(node_by_id["output_1"].node_type, "output")
        self.assertEqual((node_by_id["output_1"].config or {}).get("textTemplate"), "{{llm_1.response}}")
        self.assertIn(("start", "llm_1"), edge_pairs)
        self.assertIn(("llm_1", "output_1"), edge_pairs)

    def test_update_skill_switch_to_workflow_without_workflow_seeds_default_graph(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest, AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="switch_seed_default",
                description="d",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="agent_loop",
                system_prompt="sys",
                enabled=True,
            )
        )

        updated = svc.update_skill(
            created.id,
            AssistantSkillUpdateRequest(
                langgraph_pattern="workflow_dag",
            ),
        )

        self.assertIsNotNone(updated.workflow)
        node_by_id = {node.node_id: node for node in ((updated.workflow.nodes if updated.workflow else []) or [])}
        edge_pairs = {
            (edge.source_node_id, edge.target_node_id)
            for edge in ((updated.workflow.edges if updated.workflow else []) or [])
        }

        self.assertSetEqual(set(node_by_id.keys()), {"start", "llm_1", "output_1"})
        self.assertEqual(node_by_id["output_1"].node_type, "output")
        self.assertEqual((node_by_id["output_1"].config or {}).get("textTemplate"), "{{llm_1.response}}")
        self.assertIn(("start", "llm_1"), edge_pairs)
        self.assertIn(("llm_1", "output_1"), edge_pairs)

    def test_update_skill_rejects_non_langgraph_mode(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest, AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="lg2",
                description="d",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="agent_loop",
                system_prompt="sys",
                enabled=True,
            )
        )

        updated = svc.update_skill(created.id, AssistantSkillUpdateRequest(langgraph_pattern="agent_loop"))
        self.assertEqual(updated.langgraph_pattern, "agent_loop")

        with self.assertRaises(ValidationError):
            AssistantSkillUpdateRequest(mode="steps")

    def test_create_workflow_skill_auto_syncs_tools_from_nodes(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest, WorkflowInput, WorkflowNodeInput, WorkflowEdgeInput  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        class _SysTool:
            def __init__(self, name: str) -> None:
                self.name = name

        svc = AssistantConfigService(self.db)
        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="tool_1",
                    node_type="tool",
                    label="Tool",
                    config={"toolName": "create_entry", "inputBindings": {"title": "{{start.user_input}}"}},
                ),
                WorkflowNodeInput(
                    node_id="llm_1",
                    node_type="llm",
                    label="LLM",
                    config={"outputMode": "text"},
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={"outputMode": "text", "textTemplate": "{{llm_1.response}}"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="tool_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="tool_1", target_node_id="llm_1"),
                WorkflowEdgeInput(edge_id="e3", source_node_id="llm_1", target_node_id="output_1"),
            ],
        )

        with patch(
            "app.assistant_config.service.ToolRegistry.list_system_tools",
            return_value=[_SysTool("create_entry")],
        ):
            created = svc.create_skill(
                AssistantSkillCreateRequest(
                    name="wf_sync_tools",
                    description="d",
                    intent_examples=[],
                    tools=[],
                    mode="langgraph",
                    langgraph_pattern="workflow_dag",
                    system_prompt="x",
                    enabled=True,
                    workflow=workflow,
                )
            )

        self.assertEqual(created.tools, ["create_entry"])

    def test_create_workflow_skill_rejects_unavailable_tool(self) -> None:
        from app.assistant_config.schemas import AssistantSkillCreateRequest, WorkflowInput, WorkflowNodeInput, WorkflowEdgeInput  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="tool_1",
                    node_type="tool",
                    label="Tool",
                    config={"toolName": "missing_tool", "inputBindings": {"q": "{{start.user_input}}"}},
                ),
                WorkflowNodeInput(
                    node_id="llm_1",
                    node_type="llm",
                    label="LLM",
                    config={"outputMode": "text"},
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={"outputMode": "text", "textTemplate": "{{llm_1.response}}"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="tool_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="tool_1", target_node_id="llm_1"),
                WorkflowEdgeInput(edge_id="e3", source_node_id="llm_1", target_node_id="output_1"),
            ],
        )

        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[]):
            with self.assertRaises(ApiException) as ctx:
                svc.create_skill(
                    AssistantSkillCreateRequest(
                        name="wf_missing_tool",
                        description="d",
                        intent_examples=[],
                        tools=[],
                        mode="langgraph",
                        langgraph_pattern="workflow_dag",
                        system_prompt="x",
                        enabled=True,
                        workflow=workflow,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.code, 42203)

    def test_create_workflow_skill_rejects_missing_custom_model(self) -> None:
        from uuid import uuid4
        from app.assistant_config.schemas import AssistantSkillCreateRequest, WorkflowInput, WorkflowNodeInput, WorkflowEdgeInput  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="llm_1",
                    node_type="llm",
                    label="LLM",
                    config={
                        "outputMode": "text",
                        "modelSource": "custom",
                        "modelId": str(uuid4()),
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={"outputMode": "text", "textTemplate": "{{llm_1.response}}"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="llm_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="llm_1", target_node_id="output_1"),
            ],
        )

        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[]):
            with self.assertRaises(ApiException) as ctx:
                svc.create_skill(
                    AssistantSkillCreateRequest(
                        name="wf_missing_model",
                        description="d",
                        intent_examples=[],
                        tools=[],
                        mode="langgraph",
                        langgraph_pattern="workflow_dag",
                        system_prompt="x",
                        enabled=True,
                        workflow=workflow,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.code, 42207)

    def test_create_workflow_skill_rejects_custom_model_type_mismatch(self) -> None:
        from app.ai_registry.models import AiCredential, AiModel  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillCreateRequest, WorkflowInput, WorkflowNodeInput, WorkflowEdgeInput  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        cred = AiCredential(
            name="svc-model-cred",
            base_url="https://example.com/v1",
            api_key_encrypted="enc",
            api_key_hint="****",
        )
        self.db.add(cred)
        self.db.commit()
        self.db.refresh(cred)

        embedding_model = AiModel(
            credential_id=cred.id,
            name="text-embedding-3-small",
            model_type="embedding",
        )
        self.db.add(embedding_model)
        self.db.commit()
        self.db.refresh(embedding_model)

        svc = AssistantConfigService(self.db)
        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="llm_1",
                    node_type="llm",
                    label="LLM",
                    config={
                        "outputMode": "text",
                        "modelSource": "custom",
                        "modelId": str(embedding_model.id),
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={"outputMode": "text", "textTemplate": "{{llm_1.response}}"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="llm_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="llm_1", target_node_id="output_1"),
            ],
        )

        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[]):
            with self.assertRaises(ApiException) as ctx:
                svc.create_skill(
                    AssistantSkillCreateRequest(
                        name="wf_model_type_mismatch",
                        description="d",
                        intent_examples=[],
                        tools=[],
                        mode="langgraph",
                        langgraph_pattern="workflow_dag",
                        system_prompt="x",
                        enabled=True,
                        workflow=workflow,
                    )
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.code, 42207)

    def test_collect_workflow_tool_names_includes_agent_main_and_container_body(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        workflow_nodes = [
            {"node_type": "start", "config": {}},
            {
                "node_type": "agent",
                "config": {
                    "toolNames": ["create_entry", "  "],
                },
            },
            {
                "node_type": "iteration",
                "config": {
                    "bodyNodes": [
                        {"nodeType": "start", "config": {}},
                        {
                            "nodeType": "agent",
                            "config": {
                                "toolNames": ["update_entry"],
                            },
                        },
                    ],
                },
            },
        ]

        collected = AssistantConfigService._collect_workflow_tool_names(workflow_nodes)
        self.assertEqual(collected, {"create_entry", "update_entry"})

    def test_collect_workflow_custom_model_ids_includes_agent_main_and_container_body(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        main_model_id = uuid4()
        body_model_id = uuid4()
        workflow_nodes = [
            {"node_type": "start", "config": {}},
            {
                "node_type": "agent",
                "config": {
                    "toolNames": ["create_entry"],
                    "modelSource": "custom",
                    "modelId": str(main_model_id),
                },
            },
            {
                "node_type": "loop",
                "config": {
                    "bodyNodes": [
                        {"nodeType": "start", "config": {}},
                        {
                            "nodeType": "agent",
                            "config": {
                                "toolNames": ["update_entry"],
                                "modelSource": "custom",
                                "modelId": str(body_model_id),
                            },
                        },
                    ],
                },
            },
        ]

        collected = AssistantConfigService._collect_workflow_custom_model_ids(workflow_nodes)
        self.assertEqual(collected, {main_model_id, body_model_id})

    def test_system_workflow_mutations_are_readonly(self) -> None:
        from app.assistant_config.schemas import AssistantWorkflowUpdateRequest, WorkflowPublishRequest  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc, workflow = self._system_workflow()
        version_id = svc.list_workflow_versions(workflow.id).versions[0].id
        draft = svc._get_workflow_draft_input(workflow)  # noqa: SLF001

        operations = [
            lambda: svc.update_workflow_entity(
                workflow.id,
                AssistantWorkflowUpdateRequest(description="mutated"),
            ),
            lambda: svc.publish_workflow(
                workflow.id,
                WorkflowPublishRequest(workflow=draft),
            ),
            lambda: svc.rollback_workflow_version(workflow.id, version_id),
            lambda: svc.delete_workflow_version(workflow.id, version_id),
            lambda: svc.clear_workflow_versions(workflow.id),
            lambda: svc.delete_workflow(workflow.id),
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ApiException) as ctx:
                    operation()
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.code, 40034)

    def test_system_agent_mutations_are_readonly(self) -> None:
        from app.assistant_config.schemas import AgentPublishDraftInput, AgentPublishRequest, AssistantAgentProfileUpdateRequest  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        svc, profile = self._system_agent()
        version_id = svc.list_agent_profile_versions(profile.id).versions[0].id
        draft = svc._get_agent_profile_draft(profile)  # noqa: SLF001

        operations = [
            lambda: svc.update_agent_profile(
                profile.id,
                AssistantAgentProfileUpdateRequest(description="mutated"),
            ),
            lambda: svc.publish_agent_profile(
                profile.id,
                AgentPublishRequest(
                    draft=AgentPublishDraftInput.model_validate(draft.model_dump()),
                ),
            ),
            lambda: svc.rollback_agent_profile_version(profile.id, version_id),
            lambda: svc.delete_agent_profile_version(profile.id, version_id),
            lambda: svc.clear_agent_profile_versions(profile.id),
            lambda: svc.delete_agent_profile(profile.id),
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ApiException) as ctx:
                    operation()
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.code, 40035)

    def test_copy_custom_workflow_uses_current_draft(self) -> None:
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest, AssistantWorkflowUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name="copy_custom_workflow",
                description="initial",
                enabled=True,
            )
        )
        draft = svc._get_workflow_draft_input(created).model_copy(deep=True)  # noqa: SLF001
        output_node = next(node for node in draft.nodes if node.node_id == "output_1")
        output_node.label = "Draft Output"
        svc.update_workflow_entity(
            created.id,
            AssistantWorkflowUpdateRequest(
                description="draft description",
                workflow=draft,
            ),
        )

        copied = svc.copy_workflow(created.id)
        copied_draft = svc._get_workflow_draft_input(copied)  # noqa: SLF001

        self.assertFalse(copied.is_system)
        self.assertEqual(copied.description, "draft description")
        self.assertEqual(copied.draft_version_id, copied.published_version_id)
        self.assertEqual(
            svc._workflow_input_to_snapshot(copied_draft),  # noqa: SLF001
            svc._workflow_input_to_snapshot(draft),  # noqa: SLF001
        )

    def test_copy_system_workflow_uses_canonical_baseline(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion  # noqa: E402

        svc, workflow = self._system_workflow()
        baseline = svc._resolve_system_workflow_baseline_input(workflow)  # noqa: SLF001
        self.assertIsNotNone(baseline)

        mutated = baseline.model_copy(deep=True)
        mutated.nodes[0].label = "Mutated Baseline"
        published = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.id == workflow.published_version_id)
            .first()
        )
        self.assertIsNotNone(published)
        published.snapshot = svc._workflow_input_to_snapshot(mutated)  # noqa: SLF001
        workflow.description = "mutated description"
        self.db.commit()

        copied = svc.copy_workflow(workflow.id)
        refreshed_system = svc.get_workflow(workflow.id)
        copied_input = svc._get_workflow_draft_input(copied)  # noqa: SLF001
        refreshed_input = svc._get_workflow_draft_input(refreshed_system)  # noqa: SLF001

        self.assertFalse(copied.is_system)
        self.assertEqual(
            svc._workflow_input_to_snapshot(copied_input),  # noqa: SLF001
            svc._workflow_input_to_snapshot(baseline),  # noqa: SLF001
        )
        self.assertEqual(refreshed_input.nodes[0].label, "Mutated Baseline")
        self.assertEqual(copied.description, "mutated description")

    def test_copy_custom_agent_uses_current_draft(self) -> None:
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest, AssistantAgentProfileUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name="copy_custom_agent",
                description="initial",
                system_prompt="Initial prompt",
                tools=[],
                kb_config={"enabled": False},
                enabled=True,
                model_source="default",
            )
        )
        svc.update_agent_profile(
            created.id,
            AssistantAgentProfileUpdateRequest(
                description="draft description",
                system_prompt="Draft prompt",
            ),
        )

        copied = svc.copy_agent_profile(created.id)
        copied_draft = svc._get_agent_profile_draft(copied)  # noqa: SLF001

        self.assertFalse(copied.is_system)
        self.assertEqual(copied.description, "draft description")
        self.assertEqual(copied.system_prompt, "Draft prompt")
        self.assertEqual(copied.draft_version_id, copied.published_version_id)
        self.assertEqual(copied_draft.system_prompt, "Draft prompt")

    def test_copy_system_agent_uses_canonical_baseline(self) -> None:
        from app.assistant_config.models import AssistantAgentProfileVersion  # noqa: E402

        svc, profile = self._system_agent()
        baseline = svc._resolve_system_agent_baseline_draft(profile)  # noqa: SLF001
        self.assertIsNotNone(baseline)

        published = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.id == profile.published_version_id)
            .first()
        )
        self.assertIsNotNone(published)
        mutated_snapshot = dict(published.snapshot or {})
        mutated_snapshot["system_prompt"] = "Mutated prompt"
        published.snapshot = mutated_snapshot
        profile.description = "mutated description"
        self.db.commit()

        copied = svc.copy_agent_profile(profile.id)
        refreshed_system = svc.get_agent_profile(profile.id)
        copied_draft = svc._get_agent_profile_draft(copied)  # noqa: SLF001
        system_draft = svc._get_agent_profile_draft(refreshed_system)  # noqa: SLF001

        self.assertFalse(copied.is_system)
        self.assertEqual(copied_draft.system_prompt, baseline.system_prompt)
        self.assertEqual(list(copied_draft.tools or []), list(baseline.tools or []))
        self.assertEqual(system_draft.system_prompt, "Mutated prompt")
        self.assertEqual(copied.description, "mutated description")
