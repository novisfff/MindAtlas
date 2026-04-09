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


class AssistantConfigServiceMoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _system_workflow(self):
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        return svc, next(item for item in svc.list_workflows(include_disabled=True) if item.is_system)

    def _system_agent(self):
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
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

        self.assertFalse(copied.is_system)
        self.assertEqual(
            svc._workflow_input_to_snapshot(svc._get_workflow_draft_input(copied)),  # noqa: SLF001
            svc._workflow_input_to_snapshot(svc._get_workflow_draft_input(refreshed_system)),  # noqa: SLF001
        )
        self.assertNotEqual(copied.description, "mutated description")

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
        self.assertEqual(copied_draft.system_prompt, system_draft.system_prompt)
        self.assertEqual(list(copied_draft.tools or []), list(system_draft.tools or []))
        self.assertNotEqual(copied.description, "mutated description")
