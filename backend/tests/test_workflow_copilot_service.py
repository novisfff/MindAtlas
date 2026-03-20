from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class _FakeCopilotClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.last_cfg = None
        self.last_messages = None

    def call_chat(self, cfg, messages):  # noqa: ANN001, ANN201
        self.last_cfg = cfg
        self.last_messages = messages
        return json.dumps(self.payload, ensure_ascii=False)

    def parse_chat_content(self, raw):  # noqa: ANN001, ANN201
        return raw


class _RawTextCopilotClient:
    def __init__(self, content: str):
        self.content = content
        self.last_cfg = None
        self.last_messages = None

    def call_chat(self, cfg, messages):  # noqa: ANN001, ANN201
        self.last_cfg = cfg
        self.last_messages = messages
        return self.content

    def parse_chat_content(self, raw):  # noqa: ANN001, ANN201
        return raw


class _WrappedRawCopilotClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.last_cfg = None
        self.last_messages = None

    def call_chat(self, cfg, messages):  # noqa: ANN001, ANN201
        self.last_cfg = cfg
        self.last_messages = messages
        return json.dumps(self.payload, ensure_ascii=False)

    def parse_chat_content(self, raw):  # noqa: ANN001, ANN201
        return ""


class _EmptyCopilotClient:
    def __init__(self):
        self.last_cfg = None
        self.last_messages = None

    def call_chat(self, cfg, messages):  # noqa: ANN001, ANN201
        self.last_cfg = cfg
        self.last_messages = messages
        return None

    def parse_chat_content(self, raw):  # noqa: ANN001, ANN201
        return ""


class _RetryCopilotClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.last_cfg = None
        self.last_messages = None
        self.call_count = 0

    def call_chat(self, cfg, messages):  # noqa: ANN001, ANN201
        self.last_cfg = cfg
        self.last_messages = messages
        self.call_count += 1
        if self.responses:
            return self.responses.pop(0)
        return None

    def parse_chat_content(self, raw):  # noqa: ANN001, ANN201
        return raw


class WorkflowCopilotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_workflow(self):
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest
        from app.assistant_config.service import AssistantConfigService

        service = AssistantConfigService(self.db)
        return service.create_workflow(
            AssistantWorkflowCreateRequest(
                name=f"wf_copilot_{uuid4().hex[:8]}",
                description="workflow copilot test",
                enabled=True,
            )
        )

    @staticmethod
    def _build_iteration_draft():
        from app.assistant_config.schemas import WorkflowInput

        return WorkflowInput.model_validate(
            {
                "nodes": [
                    {
                        "nodeId": "start",
                        "nodeType": "start",
                        "label": "Start",
                        "positionX": 120,
                        "positionY": 220,
                        "config": {"inputMode": "text", "memoryMode": "auto", "structuredFields": [], "sessionVars": []},
                    },
                    {
                        "nodeId": "iter_1",
                        "nodeType": "iteration",
                        "label": "Iteration",
                        "positionX": 460,
                        "positionY": 220,
                        "config": {
                            "inputSource": "{{start.user_input}}",
                            "outputVariable": "results",
                            "outputSelector": "{{container.item}}",
                            "parallelMode": False,
                            "errorStrategy": "fail_fast",
                            "flattenOutput": True,
                            "bodyNodes": [
                                {
                                    "nodeId": "start",
                                    "nodeType": "start",
                                    "label": "Start",
                                    "positionX": 40,
                                    "positionY": 72,
                                    "config": None,
                                }
                            ],
                            "bodyEdges": [],
                        },
                    },
                    {
                        "nodeId": "output_1",
                        "nodeType": "output",
                        "label": "Output",
                        "positionX": 820,
                        "positionY": 220,
                        "config": {"outputMode": "text", "textTemplate": "{{iter_1.results}}"},
                    },
                ],
                "edges": [
                    {
                        "edgeId": "edge_start_iter_1",
                        "sourceNodeId": "start",
                        "targetNodeId": "iter_1",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                    {
                        "edgeId": "edge_iter_1_output_1",
                        "sourceNodeId": "iter_1",
                        "targetNodeId": "output_1",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                ],
            }
        )

    @staticmethod
    def _build_copilot_context_draft(*, structured_start: bool = False):
        from app.assistant_config.schemas import WorkflowInput

        start_config = {
            "inputMode": "structured" if structured_start else "text",
            "memoryMode": "structured",
            "structuredFields": [
                {"name": "topic", "type": "string", "description": "要处理的主题"},
                {"name": "limit", "type": "integer", "description": "限制条数"},
            ],
            "sessionVars": [
                {"name": "workspace_id", "type": "string", "description": "当前工作区 ID"},
            ],
        }
        return WorkflowInput.model_validate(
            {
                "nodes": [
                    {
                        "nodeId": "start",
                        "nodeType": "start",
                        "label": "Start",
                        "positionX": 120,
                        "positionY": 220,
                        "config": start_config,
                    },
                    {
                        "nodeId": "tool_lookup",
                        "nodeType": "tool",
                        "label": "Lookup",
                        "positionX": 420,
                        "positionY": 220,
                        "config": {
                            "toolName": "remote_lookup",
                            "inputBindings": {"query": "{{start.user_input}}"},
                        },
                    },
                    {
                        "nodeId": "llm_1",
                        "nodeType": "llm",
                        "label": "Summarize",
                        "positionX": 720,
                        "positionY": 220,
                        "config": {
                            "systemPrompt": "请总结工具结果",
                            "userInput": "{{tool_lookup.result}}",
                            "outputMode": "structured",
                            "outputFields": [
                                {"name": "summary", "type": "string", "nullable": False},
                            ],
                            "modelSource": "default",
                        },
                    },
                    {
                        "nodeId": "agent_1",
                        "nodeType": "agent",
                        "label": "Agent",
                        "positionX": 720,
                        "positionY": 440,
                        "config": {
                            "userInput": "{{start.user_input}}",
                            "toolNames": ["remote_lookup"],
                            "knowledgeEnabled": True,
                            "knowledgeMode": "hybrid",
                            "knowledgeTopK": 5,
                            "maxIterations": 12,
                            "modelSource": "default",
                        },
                    },
                    {
                        "nodeId": "extract_1",
                        "nodeType": "parameter_extractor",
                        "label": "Extract",
                        "positionX": 1020,
                        "positionY": 220,
                        "config": {
                            "inputContent": "{{llm_1.response}}",
                            "instruction": "抽取联系人信息",
                            "outputFields": [
                                {"name": "email", "type": "string", "nullable": False},
                                {"name": "phone", "type": "string", "nullable": True},
                            ],
                            "modelSource": "default",
                        },
                    },
                    {
                        "nodeId": "output_1",
                        "nodeType": "output",
                        "label": "Output",
                        "positionX": 1320,
                        "positionY": 220,
                        "config": {
                            "outputMode": "structured",
                            "outputFields": [
                                {"name": "final_summary", "type": "string", "value": "{{llm_1.summary}}"},
                            ],
                        },
                    },
                ],
                "edges": [
                    {
                        "edgeId": "edge_start_tool_lookup",
                        "sourceNodeId": "start",
                        "targetNodeId": "tool_lookup",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                    {
                        "edgeId": "edge_tool_lookup_llm_1",
                        "sourceNodeId": "tool_lookup",
                        "targetNodeId": "llm_1",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                    {
                        "edgeId": "edge_llm_1_extract_1",
                        "sourceNodeId": "llm_1",
                        "targetNodeId": "extract_1",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                    {
                        "edgeId": "edge_extract_1_output_1",
                        "sourceNodeId": "extract_1",
                        "targetNodeId": "output_1",
                        "sourceHandle": "output",
                        "targetHandle": "input",
                    },
                ],
            }
        )

    def test_respond_returns_proposal_with_simulated_workflow(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _FakeCopilotClient(
            {
                "status": "proposal",
                "message": "我补了一段分析节点。",
                "proposal": {
                    "title": "Add analysis node",
                    "summary": "在主流程里新增一个 LLM 节点。",
                    "operations": [
                        {
                            "type": "add_node",
                            "nodeType": "llm",
                            "nodeId": "llm_analysis",
                            "label": "Analysis",
                            "config": {
                                "outputMode": "text",
                                "userInput": "{{start.user_input}}",
                            },
                        }
                    ],
                },
            }
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="加一个分析节点",
                    draft=draft,
                ),
            )

        self.assertEqual(response.status, "proposal")
        self.assertIsNotNone(response.proposal)
        assert response.proposal is not None
        node_ids = {node.node_id for node in response.proposal.proposed_workflow.nodes}
        self.assertIn("llm_analysis", node_ids)
        self.assertNotEqual(response.proposal.base_draft_hash, response.proposal.proposed_draft_hash)

    def test_selection_scope_rejects_out_of_scope_update(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest, WorkflowCopilotSelectionInput
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService
        from app.common.exceptions import ApiException

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _FakeCopilotClient(
            {
                "status": "proposal",
                "message": "尝试修改输出节点。",
                "proposal": {
                    "title": "Bad selection edit",
                    "summary": "越权修改了未选中的节点。",
                    "operations": [
                        {
                            "type": "update_node",
                            "nodeId": "output_1",
                            "configPatch": {
                                "textTemplate": "{{llm_1.response}}\nupdated",
                            },
                        }
                    ],
                },
            }
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            with self.assertRaises(ApiException) as ctx:
                service.respond(
                    workflow_id=workflow.id,
                    request=WorkflowCopilotRequest(
                        mode="edit_selection",
                        instruction="修改我选中的 llm 节点",
                        draft=draft,
                        selection=WorkflowCopilotSelectionInput(
                            scope="selection",
                            node_ids=["llm_1"],
                            edge_ids=[],
                        ),
                    ),
                )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("selection", ctx.exception.message.lower())

    def test_container_scope_updates_only_body_nodes(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest, WorkflowCopilotSelectionInput
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_iteration_draft()
        client = _FakeCopilotClient(
            {
                "status": "proposal",
                "message": "我给容器 body 补了一个 llm 节点。",
                "proposal": {
                    "title": "Container edit",
                    "summary": "仅在 iteration body 内新增节点。",
                    "operations": [
                        {
                            "type": "add_node",
                            "containerId": "iter_1",
                            "nodeType": "llm",
                            "nodeId": "llm_body_1",
                            "label": "Body LLM",
                            "config": {
                                "outputMode": "text",
                                "userInput": "{{container.item}}",
                            },
                        }
                    ],
                },
            }
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="edit_selection",
                    instruction="给容器 body 加一个 llm 节点",
                    draft=draft,
                    selection=WorkflowCopilotSelectionInput(
                        scope="container",
                        node_ids=["start"],
                        edge_ids=[],
                        container_id="iter_1",
                    ),
                ),
            )

        self.assertEqual(response.status, "proposal")
        assert response.proposal is not None
        container_node = next(node for node in response.proposal.proposed_workflow.nodes if node.node_id == "iter_1")
        body_nodes = list((container_node.config or {}).get("bodyNodes") or [])
        body_node_ids = {str(item.get("nodeId") or "") for item in body_nodes}
        self.assertIn("llm_body_1", body_node_ids)
        top_level_ids = {node.node_id for node in response.proposal.proposed_workflow.nodes}
        self.assertNotIn("llm_body_1", top_level_ids)

    def test_question_response_skips_proposal(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _FakeCopilotClient(
            {
                "status": "question",
                "message": "你希望输出文本还是结构化结果？",
                "suggestions": ["输出文本", "输出结构化 JSON"],
            }
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="帮我生成一个处理流程",
                    draft=draft,
                ),
            )

        self.assertEqual(response.status, "question")
        self.assertIsNone(response.proposal)
        self.assertEqual(response.suggestions, ["输出文本", "输出结构化 JSON"])

    def test_invalid_json_content_falls_back_to_analysis_message(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _RawTextCopilotClient(
            "先分析用户需求，再补一个 llm 节点用于创建记录，最后接 output 节点。"
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="对用户需求进行分析，然后创建记录",
                    draft=draft,
                ),
            )

        self.assertEqual(response.status, "analysis")
        self.assertIsNone(response.proposal)
        self.assertIn("先分析用户需求", response.message)
        self.assertGreaterEqual(len(response.suggestions), 1)

    def test_wrapped_raw_content_without_choices_still_falls_back_to_analysis(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _WrappedRawCopilotClient(
            {
                "message": {
                    "content": "建议先新增一个 llm 节点分析用户需求，再新增 tool 节点创建记录。"
                }
            }
        )
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="生成一个分析用户需求，然后创建记录的智能体",
                    draft=draft,
                ),
            )

        self.assertEqual(response.status, "analysis")
        self.assertIsNone(response.proposal)
        self.assertIn("llm 节点", response.message)

    def test_empty_provider_response_falls_back_to_no_op_message(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        service = WorkflowCopilotService(self.db, client=_EmptyCopilotClient())

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="生成一个分析用户需求，然后创建记录的智能体",
                    draft=draft,
                ),
            )

        self.assertEqual(response.status, "no_op")
        self.assertIsNone(response.proposal)
        self.assertIn("暂时没有拿到 AI 返回结果", response.message)
        self.assertGreaterEqual(len(response.suggestions), 1)

    def test_empty_provider_response_retries_once_before_no_op(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = AssistantConfigService._build_default_workflow_input()
        client = _RetryCopilotClient([
            None,
            json.dumps({
                "status": "question",
                "message": "你希望输出文本还是结构化结果？",
                "suggestions": ["输出文本"],
            }, ensure_ascii=False),
        ])
        service = WorkflowCopilotService(self.db, client=client)

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            response = service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="生成一个分析用户需求，然后创建记录的智能体",
                    draft=draft,
                ),
            )

        self.assertEqual(client.call_count, 2)
        self.assertEqual(response.status, "question")
        self.assertEqual(response.message, "你希望输出文本还是结构化结果？")

    def test_prompt_messages_include_enhanced_catalogs_and_constraints(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_copilot_context_draft()
        client = _FakeCopilotClient(
            {
                "status": "question",
                "message": "请确认是否需要继续。",
            }
        )
        service = WorkflowCopilotService(self.db, client=client)
        service._config_service.list_system_tool_definitions = unittest.mock.Mock(return_value=[
            {
                "name": "kb_search",
                "description": "Search knowledge base",
                "input_params": [SimpleNamespace(name="query", param_type="string", description="Search query")],
                "output_params": [SimpleNamespace(name="references", param_type="array", description="KB refs")],
            }
        ])
        service._config_service.list_tools = unittest.mock.Mock(return_value=[
            SimpleNamespace(
                name="remote_lookup",
                description="Lookup remote records",
                input_params=[{"name": "query", "param_type": "string"}],
                output_params=[{"name": "items", "param_type": "array"}],
            )
        ])

        with unittest.mock.patch(
            "app.assistant_config.workflow_copilot_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ):
            service.respond(
                workflow_id=workflow.id,
                request=WorkflowCopilotRequest(
                    mode="generate",
                    instruction="帮我优化一下当前流程",
                    draft=draft,
                ),
            )

        assert client.last_messages is not None
        system_prompt = client.last_messages[0]["content"]
        payload = json.loads(client.last_messages[1]["content"])

        self.assertIn("camelCase", system_prompt)
        self.assertIn("configPatch", system_prompt)
        self.assertIn("containerId", system_prompt)
        self.assertIn('"type": "autolayout"', system_prompt)

        agent_catalog = next(item for item in payload["nodeCatalog"] if item["type"] == "agent")
        self.assertIn("description", agent_catalog)
        self.assertIn("requiredConfig", agent_catalog)
        self.assertIn("enumHints", agent_catalog)
        self.assertEqual(agent_catalog["allowedInContainerBody"], True)
        self.assertEqual(agent_catalog["isContainer"], False)

        start_catalog = next(item for item in payload["nodeCatalog"] if item["type"] == "start")
        start_behavior_notes = "\n".join(start_catalog["behaviorNotes"])
        self.assertIn("start.memory_recent_dialogue", start_behavior_notes)
        self.assertIn("start.memory_conversation_summary", start_behavior_notes)
        self.assertIn("start.memory_skill_facts", start_behavior_notes)

        reference_start_fields = {item["name"] for item in payload["referenceCatalog"]["start"]["fields"]}
        self.assertIn("user_input", reference_start_fields)
        self.assertIn("memory_recent_dialogue", reference_start_fields)
        self.assertIn("memory_conversation_summary", reference_start_fields)
        self.assertIn("memory_skill_facts", reference_start_fields)

        node_refs = {item["nodeId"]: item for item in payload["referenceCatalog"]["nodes"]}
        self.assertIn("llm_1", node_refs)
        self.assertIn("agent_1", node_refs)
        self.assertIn("extract_1", node_refs)
        self.assertIn("tool_lookup", node_refs)
        self.assertIn("summary", {field["name"] for field in node_refs["llm_1"]["fields"]})
        self.assertIn("response", {field["name"] for field in node_refs["agent_1"]["fields"]})
        self.assertIn("email", {field["name"] for field in node_refs["extract_1"]["fields"]})
        self.assertIn("items", {field["name"] for field in node_refs["tool_lookup"]["fields"]})

        env_fields = {item["name"] for item in payload["referenceCatalog"]["environment"]}
        self.assertIn("workspace_id", env_fields)

        kb_tool = next(item for item in payload["availableTools"] if item["name"] == "kb_search")
        self.assertEqual(kb_tool["agentToolAllowed"], False)
        self.assertIn("knowledgeEnabled", kb_tool["usageHint"])
        remote_tool = next(item for item in payload["availableTools"] if item["name"] == "remote_lookup")
        self.assertEqual(remote_tool["outputParams"][0]["name"], "items")

        add_node_operation = next(item for item in payload["operationCatalog"] if item["type"] == "add_node")
        self.assertIn("requiredFields", add_node_operation)
        self.assertIn("minimalExample", add_node_operation)
        self.assertEqual(add_node_operation["minimalExample"]["nodeType"], "llm")

    def test_tool_catalog_handles_custom_tool_without_output_params_attr(self) -> None:
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))
        service._config_service.list_system_tool_definitions = unittest.mock.Mock(return_value=[])
        service._config_service.list_tools = unittest.mock.Mock(return_value=[
            SimpleNamespace(
                name="remote_lookup",
                description="Lookup remote records",
                input_params=[{"name": "query", "param_type": "string"}],
            )
        ])

        catalog = service._build_tool_catalog_summary()

        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["name"], "remote_lookup")
        self.assertEqual(catalog[0]["inputParams"][0]["name"], "query")
        self.assertEqual(catalog[0]["outputParams"], [])

    def test_reference_catalog_respects_structured_start_mode(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_copilot_context_draft(structured_start=True)
        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))

        messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="generate",
                instruction="生成一个处理流程",
                draft=draft,
            ),
        )
        payload = json.loads(messages[1]["content"])
        start_fields = {item["name"] for item in payload["referenceCatalog"]["start"]["fields"]}
        self.assertNotIn("user_input", start_fields)
        self.assertIn("topic", start_fields)
        self.assertIn("limit", start_fields)
        self.assertIn("memory_recent_dialogue", start_fields)

    def test_mode_specific_context_and_summary_trimming(self) -> None:
        from app.assistant_config.schemas import (
            WorkflowCopilotRequest,
            WorkflowCopilotSelectionInput,
            WorkflowCopilotTestRunContextInput,
            WorkflowCopilotValidationContextInput,
            WorkflowCopilotValidationIssueInput,
        )
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        base_draft = self._build_copilot_context_draft()
        expanded_payload = base_draft.model_dump(by_alias=True)
        for index in range(50):
            expanded_payload["nodes"].append(
                {
                    "nodeId": f"llm_extra_{index}",
                    "nodeType": "llm",
                    "label": f"Extra {index}",
                    "positionX": 2000 + index * 20,
                    "positionY": 200 + index * 10,
                    "config": {
                        "outputMode": "text",
                        "userInput": "{{start.user_input}}",
                    },
                }
            )
        from app.assistant_config.schemas import WorkflowInput
        draft = WorkflowInput.model_validate(expanded_payload)
        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))

        messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="fix_validation",
                instruction="修复当前校验问题",
                draft=draft,
                selection=WorkflowCopilotSelectionInput(
                    scope="selection",
                    node_ids=["llm_1"],
                    edge_ids=[],
                ),
                validation_context=WorkflowCopilotValidationContextInput(
                    errors=[
                        WorkflowCopilotValidationIssueInput(
                            severity="error",
                            node_id="extract_1",
                            message="缺少必填字段映射",
                            source="backend",
                        )
                    ],
                    warnings=[],
                ),
                test_run_context=WorkflowCopilotTestRunContextInput(
                    selected_run_id="run_001",
                    result={"status": "failed"},
                    trace=[{"event": "node_end", "nodeId": "llm_1", "status": "ok"}],
                    raw=[{"event": "node_error", "nodeId": "tool_lookup", "status": "failed"}],
                ),
            ),
        )
        payload = json.loads(messages[1]["content"])
        mode_context = payload["modeContext"]
        self.assertIn("validationFocus", mode_context)
        related_nodes = mode_context["validationFocus"]["relatedNodes"]
        self.assertTrue(any(item["node"]["nodeId"] == "extract_1" for item in related_nodes))
        self.assertLessEqual(len(payload["workflowSummary"]["nodes"]), 40)
        self.assertGreater(payload["workflowSummary"]["truncatedNodeCount"], 0)
        self.assertIn("llm_1", payload["workflowSummary"]["focusNodeIds"])

        analyze_messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="analyze_test_run",
                instruction="分析最近一次试运行失败原因",
                draft=draft,
                test_run_context=WorkflowCopilotTestRunContextInput(
                    selected_run_id="run_002",
                    result={"status": "failed"},
                    trace=[{"event": "node_error", "nodeId": "tool_lookup", "status": "failed"}],
                    raw=[{"event": "node_end", "nodeId": "llm_1", "status": "ok"}],
                ),
            ),
        )
        analyze_payload = json.loads(analyze_messages[1]["content"])
        self.assertIn("testRunFocus", analyze_payload["modeContext"])
        self.assertIn("tool_lookup", analyze_payload["modeContext"]["testRunFocus"]["failedNodeIds"])

    def test_edit_selection_mode_context_includes_primary_target_for_main_node(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest, WorkflowCopilotSelectionInput
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_copilot_context_draft()
        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))

        messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="edit_selection",
                instruction="帮我修改 llm_1",
                draft=draft,
                selection=WorkflowCopilotSelectionInput(
                    scope="selection",
                    node_ids=["llm_1"],
                    edge_ids=[],
                ),
            ),
        )
        payload = json.loads(messages[1]["content"])
        mode_context = payload["modeContext"]
        self.assertEqual(mode_context["selectionIntent"], "user_selected_this_target_to_modify")
        self.assertEqual(mode_context["allowedExpansion"], "minimal_supporting_changes_only")
        self.assertEqual(mode_context["primaryTarget"]["nodeId"], "llm_1")
        self.assertEqual(mode_context["primaryTarget"]["nodeType"], "llm")
        self.assertEqual(mode_context["primaryTarget"]["label"], "Summarize")
        self.assertEqual(mode_context["primaryTarget"]["scope"], "selection")
        self.assertTrue(mode_context["primaryTarget"]["targetFound"])
        self.assertIn("llm_1", payload["selection"]["nodeIds"])
        self.assertEqual(payload["selection"]["primaryNodeId"], "llm_1")
        self.assertEqual(payload["selection"]["selectedNodeCount"], 1)
        self.assertIn("modeContext.primaryTarget", messages[0]["content"])

    def test_edit_selection_mode_context_includes_primary_target_for_container_body(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest, WorkflowCopilotSelectionInput
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_iteration_draft()
        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))

        messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="edit_selection",
                instruction="帮我修改 iteration body 的 start 节点",
                draft=draft,
                selection=WorkflowCopilotSelectionInput(
                    scope="container",
                    node_ids=["start"],
                    edge_ids=[],
                    container_id="iter_1",
                ),
            ),
        )
        payload = json.loads(messages[1]["content"])
        primary_target = payload["modeContext"]["primaryTarget"]
        self.assertEqual(primary_target["scope"], "container")
        self.assertEqual(primary_target["nodeId"], "start")
        self.assertEqual(primary_target["nodeType"], "start")
        self.assertEqual(primary_target["containerId"], "iter_1")
        self.assertEqual(primary_target["containerLabel"], "Iteration")
        self.assertEqual(primary_target["displayPath"], "Iteration / Start")
        self.assertTrue(primary_target["targetFound"])
        selection_detail = payload["modeContext"]["selectionDetail"]
        self.assertIn("containerBody", selection_detail)
        self.assertEqual(selection_detail["selectedNodes"][0]["nodeId"], "start")

    def test_edit_selection_mode_context_preserves_multi_selection_details(self) -> None:
        from app.assistant_config.schemas import WorkflowCopilotRequest, WorkflowCopilotSelectionInput
        from app.assistant_config.workflow_copilot_service import WorkflowCopilotService

        workflow = self._create_workflow()
        draft = self._build_copilot_context_draft()
        service = WorkflowCopilotService(self.db, client=_FakeCopilotClient({"status": "question", "message": "x"}))

        messages = service._build_prompt_messages(
            workflow_id=workflow.id,
            workflow=workflow,
            request=WorkflowCopilotRequest(
                mode="edit_selection",
                instruction="先改 llm_1，再看 extract_1",
                draft=draft,
                selection=WorkflowCopilotSelectionInput(
                    scope="selection",
                    node_ids=["llm_1", "extract_1"],
                    edge_ids=[],
                ),
            ),
        )
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["modeContext"]["primaryTarget"]["nodeId"], "llm_1")
        self.assertEqual(payload["modeContext"]["primaryTarget"]["selectedNodeCount"], 2)
        selected_nodes = payload["modeContext"]["selectionDetail"]["selectedNodes"]
        self.assertEqual({item["nodeId"] for item in selected_nodes}, {"llm_1", "extract_1"})
