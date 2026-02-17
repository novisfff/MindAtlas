from __future__ import annotations

import unittest
from unittest.mock import patch
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
        self.assertEqual(self.db.query(AssistantSkill).count(), 0)

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

    def test_update_skill_rejects_non_langgraph_mode(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = AssistantSkill(
            name="lg2",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="sys",
            is_system=False,
            enabled=True,
        )
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        updated = svc.update_skill(skill.id, AssistantSkillUpdateRequest(langgraph_pattern="agent_loop"))
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
                    config={"isOutput": True, "outputMode": "text"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="tool_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="tool_1", target_node_id="llm_1"),
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
                    config={"isOutput": True, "outputMode": "text"},
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="tool_1"),
                WorkflowEdgeInput(edge_id="e2", source_node_id="tool_1", target_node_id="llm_1"),
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
                        "isOutput": True,
                        "outputMode": "text",
                        "modelSource": "custom",
                        "modelId": str(uuid4()),
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="llm_1"),
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
                        "isOutput": True,
                        "outputMode": "text",
                        "modelSource": "custom",
                        "modelId": str(embedding_model.id),
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(edge_id="e1", source_node_id="start", target_node_id="llm_1"),
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
