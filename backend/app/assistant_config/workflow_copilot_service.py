from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_registry.models import AiModel
from app.ai_registry.runtime import resolve_openai_compat_config
from app.assistant.memory_computation import AssistantMemoryComputationService
from app.assistant.orchestration.openai_fallback_client import (
    OpenAiFallbackClient,
    OpenAiFallbackConfig,
)
from app.assistant.workflow.validation.validator import (
    validate_parallel_branches,
    validate_workflow,
)
from app.assistant_config.schemas import (
    ConditionExpressionInput,
    WorkflowCopilotOperation,
    WorkflowCopilotProposalResponse,
    WorkflowCopilotRequest,
    WorkflowCopilotResponse,
    WorkflowInput,
    WorkflowValidationResponse,
)
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException

logger = logging.getLogger(__name__)

_NODE_ID_RE = re.compile(r"[a-zA-Z0-9_]+")
_CONTAINER_NODE_TYPES = {"iteration", "loop"}
_ALLOWED_COPILOT_STATUSES = {"proposal", "question", "analysis", "no_op"}
_ALLOWED_LAYOUT_RECOMMENDATIONS = {"keep", "autolayout"}
_DEFAULT_LABEL_BY_TYPE: dict[str, str] = {
    "start": "Start",
    "llm": "LLM",
    "agent": "Agent",
    "tool": "Tool",
    "if_else": "If Else",
    "parameter_extractor": "Parameter Extractor",
    "knowledge_retrieval": "Knowledge Retrieval",
    "iteration": "Iteration",
    "loop": "Loop",
    "code_executor": "Code Executor",
    "http_request": "HTTP Request",
    "variable_assign": "Variable Assign",
    "human_in_loop": "Human In Loop",
    "output": "Output",
}
_NODE_CONFIG_HINTS: dict[str, list[str]] = {
    "start": ["inputMode", "memoryMode", "structuredFields", "sessionVars"],
    "llm": ["systemPrompt", "userInput", "outputMode", "outputFields", "knowledgeEnabled", "modelSource", "modelId"],
    "agent": ["systemPrompt", "userInput", "toolNames", "knowledgeEnabled", "knowledgeMode", "knowledgeTopK", "maxIterations", "modelSource", "modelId"],
    "tool": ["toolName", "inputBindings"],
    "if_else": ["branches", "elseHandle"],
    "parameter_extractor": ["inputContent", "instruction", "outputFields", "modelSource", "modelId"],
    "knowledge_retrieval": ["query", "mode", "topK"],
    "iteration": ["inputSource", "outputVariable", "outputSelector", "parallelMode", "errorStrategy", "flattenOutput", "bodyNodes", "bodyEdges"],
    "loop": ["initialVars", "updateMappings", "terminationLogic", "terminationConditions", "maxIterations", "bodyNodes", "bodyEdges"],
    "code_executor": ["language", "entrypoint", "inputBindings", "outputFields", "code"],
    "http_request": ["method", "url", "headers", "queryParams", "bodyType", "jsonBodyTemplate", "rawBodyTemplate", "formBody", "authType", "timeoutMs", "retryEnabled"],
    "variable_assign": ["variableName", "operation", "valueTemplate"],
    "human_in_loop": ["title", "instruction", "fields", "approveLabel", "rejectLabel", "requireRejectComment"],
    "output": ["outputMode", "textTemplate", "outputFields"],
}
_CONTAINER_BODY_ALLOWED_NODE_TYPES = {
    "start",
    "llm",
    "agent",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "code_executor",
    "http_request",
    "variable_assign",
    "human_in_loop",
}
_START_FIELD_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_START_MEMORY_STRUCTURED_FIELDS = [
    "memory_recent_dialogue",
    "memory_conversation_summary",
    "memory_skill_facts",
]
_START_MEMORY_MODES = {"auto", "off", "structured"}
_MAX_SUMMARY_NODES = 40
_MAX_SUMMARY_EDGES = 60
_MAX_REFERENCE_NODES = 40
_MAX_TEXT_PREVIEW = 220
_MAX_TEXT_DETAIL = 480
_MAX_CONVERSATION_ITEMS = 8
_MAX_CONVERSATION_CHARS = 1200
_MAX_FALLBACK_MESSAGE_CHARS = 2000
_COPILOT_EMPTY_RESPONSE_MAX_ATTEMPTS = 2
_AGENT_KNOWLEDGE_MODES = ["naive", "local", "global", "hybrid", "mix"]
_TOOL_USAGE_HINTS: dict[str, str] = {
    "kb_search": "知识库检索能力不应作为 agent.toolNames 填写；agent 节点请改用 knowledgeEnabled/knowledgeMode/knowledgeTopK，其他场景优先使用 knowledge_retrieval 节点。",
}
_NODE_DESCRIPTIONS: dict[str, str] = {
    "start": "工作流入口，定义输入模式、记忆模式和环境变量。",
    "llm": "通用 LLM 节点，适合生成文本或结构化输出。",
    "agent": "可自主决定是否调用工具或知识库的 LLM 节点。",
    "tool": "调用单个外部工具，并把结果暴露给下游节点。",
    "if_else": "条件分支节点，根据表达式决定走哪条分支。",
    "parameter_extractor": "从输入内容中提取结构化字段。",
    "knowledge_retrieval": "执行显式知识库检索，为下游节点提供引用或全文负载。",
    "iteration": "对输入集合逐项执行 body 子流并聚合结果。",
    "loop": "带状态变量的循环子流，直到终止条件满足。",
    "code_executor": "执行受控代码片段并返回声明过的输出字段。",
    "http_request": "发送 HTTP 请求并把响应映射为可引用字段。",
    "variable_assign": "对变量执行 set/append 等赋值操作。",
    "human_in_loop": "等待人工审批或补充字段，再继续执行。",
    "output": "工作流最终输出节点。",
}
_NODE_REQUIRED_CONFIG: dict[str, list[str]] = {
    "start": ["inputMode"],
    "llm": ["userInput", "outputMode"],
    "agent": ["userInput"],
    "tool": ["toolName"],
    "if_else": ["branches"],
    "parameter_extractor": ["outputFields"],
    "knowledge_retrieval": ["query"],
    "iteration": ["inputSource", "bodyNodes"],
    "loop": ["bodyNodes"],
    "code_executor": ["language", "entrypoint", "code", "outputFields"],
    "http_request": ["method", "url"],
    "variable_assign": ["variableName", "operation", "valueTemplate"],
    "human_in_loop": ["instruction", "fields"],
    "output": ["outputMode"],
}
_NODE_ENUM_HINTS: dict[str, dict[str, Any]] = {
    "start": {
        "inputMode": ["text", "structured"],
        "memoryMode": ["auto", "off", "structured"],
    },
    "llm": {
        "outputMode": ["text", "structured"],
        "knowledgeInjectMode": ["references_only", "full_payload"],
        "modelSource": ["default", "custom"],
    },
    "agent": {
        "knowledgeMode": _AGENT_KNOWLEDGE_MODES,
        "modelSource": ["default", "custom"],
        "maxIterations": "1..20",
    },
    "if_else": {
        "branch.logic": ["and", "or"],
        "conditionType": ["expression", "default"],
    },
    "parameter_extractor": {
        "modelSource": ["default", "custom"],
    },
    "knowledge_retrieval": {
        "mode": _AGENT_KNOWLEDGE_MODES,
    },
    "code_executor": {
        "language": ["python", "javascript"],
    },
    "http_request": {
        "method": ["GET", "POST", "PUT", "PATCH", "DELETE"],
        "bodyType": ["none", "json", "raw", "x-www-form-urlencoded", "form-data"],
        "authType": ["none", "bearer", "api_key"],
        "apiKeyIn": ["header", "query"],
    },
    "human_in_loop": {
        "field.type": ["string", "number", "integer", "boolean", "array"],
        "field.widget": ["input", "textarea", "switch", "select", "radio", "tag_selector", "date", "time"],
    },
    "output": {
        "outputMode": ["text", "structured"],
    },
}
_NODE_BEHAVIOR_NOTES: dict[str, list[str]] = {
    "start": [
        "structured 模式下仅暴露 configured structuredFields；text 模式下默认暴露 start.user_input。",
        "memoryMode=structured 时才会暴露 start.memory_recent_dialogue / start.memory_conversation_summary / start.memory_skill_facts。",
    ],
    "llm": [
        "结构化输出时仍会保留 response，同时额外暴露 outputFields 中的字段。",
        "显式知识绑定必须引用上游 knowledge_retrieval 节点。",
    ],
    "agent": [
        "优先通过 toolNames 或 knowledgeEnabled 获取能力，修改已有配置时优先用 configPatch。",
        "kb_search 不是普通 toolNames 候选；启用知识库请改用 knowledgeEnabled 相关字段。",
    ],
    "tool": [
        "输出字段优先来自工具定义的 outputParams；未知时至少可引用 result。",
    ],
    "iteration": [
        "body 内节点引用仍使用 body 内 stored node id，不引入额外命名。",
    ],
    "loop": [
        "循环变量通常来自 initialVars，并在 updateMappings 中持续更新。",
    ],
    "output": [
        "text 模式常用 textTemplate；structured 模式需提供 outputFields。",
    ],
}
_OPERATION_CATALOG: list[dict[str, Any]] = [
    {
        "type": "add_node",
        "requiredFields": ["type", "nodeType"],
        "optionalFields": ["containerId", "nodeId", "label", "config", "positionX", "positionY"],
        "notes": "优先只补新增节点自身最小必要 config；container 范围必须带 containerId。",
        "minimalExample": {
            "type": "add_node",
            "nodeType": "llm",
            "nodeId": "llm_analysis",
            "label": "Analysis",
            "config": {"outputMode": "text", "userInput": "{{start.user_input}}"},
        },
    },
    {
        "type": "update_node",
        "requiredFields": ["type", "nodeId"],
        "optionalFields": ["label", "configPatch", "config", "replaceConfig", "positionX", "positionY"],
        "notes": "修改已有节点时优先使用 configPatch；仅在确需整体替换时才使用 replaceConfig=true + config。",
        "minimalExample": {
            "type": "update_node",
            "nodeId": "llm_1",
            "configPatch": {"systemPrompt": "请总结上游结果，并输出简明结论。"},
        },
    },
    {
        "type": "remove_node",
        "requiredFields": ["type", "nodeId"],
        "optionalFields": ["containerId"],
        "notes": "删除节点会连带移除与该节点相连的边。",
        "minimalExample": {
            "type": "remove_node",
            "nodeId": "tool_legacy",
        },
    },
    {
        "type": "add_edge",
        "requiredFields": ["type", "sourceNodeId", "targetNodeId"],
        "optionalFields": ["containerId", "edgeId", "sourceHandle", "targetHandle", "conditionType", "conditionExpr", "label"],
        "notes": "引用现有 stored node id；if_else 分支边需要正确的 sourceHandle。",
        "minimalExample": {
            "type": "add_edge",
            "sourceNodeId": "start",
            "targetNodeId": "llm_analysis",
            "sourceHandle": "output",
            "targetHandle": "input",
        },
    },
    {
        "type": "remove_edge",
        "requiredFields": ["type"],
        "optionalFields": ["containerId", "edgeId", "sourceNodeId", "targetNodeId", "sourceHandle", "targetHandle"],
        "notes": "优先使用 edgeId；若没有 edgeId，则需要 source/target/handle 组合唯一定位。",
        "minimalExample": {
            "type": "remove_edge",
            "edgeId": "edge_start_llm_1",
        },
    },
    {
        "type": "move_node",
        "requiredFields": ["type", "nodeId", "positionX", "positionY"],
        "optionalFields": ["containerId"],
        "notes": "仅用于调整布局，不修改 config。",
        "minimalExample": {
            "type": "move_node",
            "nodeId": "output_1",
            "positionX": 920,
            "positionY": 220,
        },
    },
    {
        "type": "autolayout",
        "requiredFields": ["type"],
        "optionalFields": [],
        "notes": "只需自动布局时单独返回该操作，并将 layoutRecommendation 设为 autolayout。",
        "minimalExample": {
            "type": "autolayout",
        },
    },
]


@dataclass
class _ScopeContext:
    scope: str
    container_id: str | None
    selected_node_ids: set[str]
    selected_edge_ids: set[str]
    new_node_ids: set[str]
    new_edge_ids: set[str]


class WorkflowCopilotService:
    def __init__(self, db: Session, client: OpenAiFallbackClient | None = None) -> None:
        self.db = db
        self._client = client or OpenAiFallbackClient()
        self._config_service = AssistantConfigService(db)

    def respond(self, *, workflow_id: UUID, request: WorkflowCopilotRequest) -> WorkflowCopilotResponse:
        workflow = self._config_service.get_workflow(workflow_id)
        instruction = str(request.instruction or "").strip()
        if not instruction and not request.conversation:
            return WorkflowCopilotResponse(
                status="question",
                message="请先描述你想生成、修改、修复或分析的内容。",
                suggestions=[
                    "生成一个抓取网页后总结结果的子流程",
                    "修复当前校验错误",
                    "分析最近一次试运行并提出改动",
                ],
            )

        cfg = self._resolve_llm_config()
        prompt_messages = self._build_prompt_messages(workflow_id=workflow_id, workflow=workflow, request=request)
        raw = None
        content = ""
        for attempt in range(_COPILOT_EMPTY_RESPONSE_MAX_ATTEMPTS):
            raw = self._client.call_chat(cfg, prompt_messages)
            content = self._extract_model_response_text(raw)
            if content:
                break
            logger.warning(
                "workflow copilot empty model response workflow_id=%s attempt=%s raw=%s",
                workflow_id,
                attempt + 1,
                str(raw or "")[:500],
            )
            if attempt < _COPILOT_EMPTY_RESPONSE_MAX_ATTEMPTS - 1:
                time.sleep(0.2)
        if not content:
            logger.warning(
                "workflow copilot returned empty response workflow_id=%s raw=%s",
                workflow_id,
                str(raw or "")[:500],
            )
            return self._build_empty_response_fallback()
        parsed = AssistantMemoryComputationService.parse_json_object_text(content)
        if not parsed:
            logger.warning(
                "workflow copilot returned unparsable content workflow_id=%s content=%s raw=%s",
                workflow_id,
                content[:500],
                str(raw or "")[:500],
            )
            fallback = self._build_invalid_json_fallback_response(content)
            if fallback is not None:
                return fallback
            raise ApiException(status_code=502, code=50240, message="Workflow Copilot returned invalid JSON")
        return self._build_response_from_model(
            workflow=workflow,
            request=request,
            parsed=parsed,
        )

    @staticmethod
    def _build_empty_response_fallback() -> WorkflowCopilotResponse:
        return WorkflowCopilotResponse(
            status="no_op",
            message="抱歉，Workflow Copilot 暂时没有拿到 AI 返回结果，请稍后重试。",
            suggestions=[
                "重新发送一次当前请求",
                "把需求描述得更具体一些",
                "先让我只修改一个小范围节点",
            ],
        )

    def _extract_model_response_text(self, raw: Any) -> str:
        direct = str(self._client.parse_chat_content(raw) or "").strip()
        if direct:
            return direct
        return self._extract_text_from_raw_response(raw)

    def _build_invalid_json_fallback_response(self, content: str) -> WorkflowCopilotResponse | None:
        message = self._sanitize_fallback_message(content)
        if not message:
            return None
        return WorkflowCopilotResponse(
            status="analysis",
            message=message,
            suggestions=[
                "继续帮我整理成可直接应用的结构化提案",
                "只修改当前选中的节点",
                "先给我一个最小可行版本",
            ],
        )

    @staticmethod
    def _sanitize_fallback_message(content: str) -> str:
        text = str(content or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1].strip()
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            return ""
        if len(text) > _MAX_FALLBACK_MESSAGE_CHARS:
            text = f"{text[:_MAX_FALLBACK_MESSAGE_CHARS]}..."
        return text

    def _extract_text_from_raw_response(self, raw: Any) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except Exception:
            return text
        return self._extract_text_from_payload(payload)

    def _extract_text_from_payload(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, list):
            parts = [self._extract_text_from_payload(item) for item in payload[:20]]
            return "\n".join(part for part in parts if part).strip()
        if not isinstance(payload, dict):
            return ""

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            for candidate in (
                first.get("message"),
                first.get("delta"),
                first.get("text"),
                first.get("content"),
            ):
                extracted = self._extract_text_from_payload(candidate)
                if extracted:
                    return extracted

        for key in ("message", "content", "output_text", "response", "text", "answer"):
            extracted = self._extract_text_from_payload(payload.get(key))
            if extracted:
                return extracted

        if isinstance(payload.get("output"), list):
            extracted = self._extract_text_from_payload(payload.get("output"))
            if extracted:
                return extracted

        for item in payload.values():
            extracted = self._extract_text_from_payload(item)
            if extracted:
                return extracted
        return ""

    def _resolve_llm_config(self) -> OpenAiFallbackConfig:
        cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")
        if cfg is None:
            raise ApiException(status_code=409, code=40960, message="No active AI provider configured")
        return OpenAiFallbackConfig(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
        )

    def _build_response_from_model(
        self,
        *,
        workflow,
        request: WorkflowCopilotRequest,
        parsed: dict[str, Any],
    ) -> WorkflowCopilotResponse:
        status = str(parsed.get("status") or "proposal").strip().lower()
        if status not in _ALLOWED_COPILOT_STATUSES:
            status = "proposal"
        message = str(parsed.get("message") or "").strip() or "已生成一份工作流改动建议。"
        suggestions = self._normalize_string_list(parsed.get("suggestions"))

        if status == "question":
            return WorkflowCopilotResponse(
                status="question",
                message=message,
                suggestions=suggestions,
            )

        proposal_block = parsed.get("proposal") if isinstance(parsed.get("proposal"), dict) else parsed
        if status == "analysis" and not isinstance(proposal_block, dict):
            return WorkflowCopilotResponse(
                status="analysis",
                message=message,
                suggestions=suggestions,
            )

        if not isinstance(proposal_block, dict):
            return WorkflowCopilotResponse(
                status="no_op",
                message=message,
                suggestions=suggestions,
            )

        operations = self._parse_operations(proposal_block.get("operations"))
        if status == "analysis" and not operations:
            return WorkflowCopilotResponse(
                status="analysis",
                message=message,
                suggestions=suggestions,
            )
        if not operations:
            return WorkflowCopilotResponse(
                status="no_op",
                message=message,
                suggestions=suggestions,
            )

        layout_recommendation = str(
            proposal_block.get("layoutRecommendation")
            or parsed.get("layoutRecommendation")
            or "keep"
        ).strip().lower()
        if layout_recommendation not in _ALLOWED_LAYOUT_RECOMMENDATIONS:
            layout_recommendation = "keep"
        warnings = self._normalize_string_list(proposal_block.get("warnings"))
        proposal = self._simulate_proposal(
            workflow=workflow,
            draft=request.draft,
            selection=request.selection,
            operations=operations,
            title=str(proposal_block.get("title") or "Workflow Proposal").strip() or "Workflow Proposal",
            summary=str(proposal_block.get("summary") or message).strip() or message,
            layout_recommendation=layout_recommendation,
            warnings=warnings,
        )
        return WorkflowCopilotResponse(
            status="proposal",
            message=message,
            proposal=proposal,
            suggestions=suggestions,
        )

    def _parse_operations(self, value: Any) -> list[WorkflowCopilotOperation]:
        if not isinstance(value, list):
            return []
        operations: list[WorkflowCopilotOperation] = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            try:
                operations.append(WorkflowCopilotOperation.model_validate(raw))
            except Exception as exc:
                logger.warning("workflow copilot skipped invalid operation error=%s raw=%s", exc, raw)
        return operations

    def _simulate_proposal(
        self,
        *,
        workflow,
        draft: WorkflowInput,
        selection,
        operations: list[WorkflowCopilotOperation],
        title: str,
        summary: str,
        layout_recommendation: str,
        warnings: list[str],
    ) -> WorkflowCopilotProposalResponse:
        draft_payload = draft.model_dump(by_alias=True, exclude_none=False)
        scope = _ScopeContext(
            scope=str(getattr(selection, "scope", "workflow") or "workflow"),
            container_id=str(getattr(selection, "container_id", "") or "").strip() or None,
            selected_node_ids={str(item).strip() for item in (getattr(selection, "node_ids", []) or []) if str(item).strip()},
            selected_edge_ids={str(item).strip() for item in (getattr(selection, "edge_ids", []) or []) if str(item).strip()},
            new_node_ids=set(),
            new_edge_ids=set(),
        )
        if scope.scope == "container" and not scope.container_id:
            raise ApiException(status_code=422, code=42260, message="container scope requires containerId")
        if scope.scope not in {"workflow", "selection", "container"}:
            raise ApiException(status_code=422, code=42261, message=f"Unsupported copilot selection scope: {scope.scope}")

        all_warnings = list(warnings)
        affected_node_ids: set[str] = set()
        layout = layout_recommendation

        for operation in operations:
            if operation.type == "autolayout":
                layout = "autolayout"
                continue
            self._apply_operation(
                draft_payload=draft_payload,
                scope=scope,
                operation=operation,
                affected_node_ids=affected_node_ids,
                warnings=all_warnings,
            )

        proposed_workflow = WorkflowInput.model_validate(draft_payload)
        validation = self._validate_workflow_payload(workflow=workflow, workflow_input=proposed_workflow)
        base_hash = build_workflow_draft_hash(draft)
        proposed_hash = build_workflow_draft_hash(proposed_workflow)

        return WorkflowCopilotProposalResponse(
            title=title,
            summary=summary,
            operations=operations,
            proposed_workflow=proposed_workflow,
            base_draft_hash=base_hash,
            proposed_draft_hash=proposed_hash,
            layout_recommendation=layout,
            validation=validation,
            affected_node_ids=sorted(affected_node_ids),
            warnings=all_warnings,
        )

    def _apply_operation(
        self,
        *,
        draft_payload: dict[str, Any],
        scope: _ScopeContext,
        operation: WorkflowCopilotOperation,
        affected_node_ids: set[str],
        warnings: list[str],
    ) -> None:
        container_id = str(operation.container_id or "").strip() or scope.container_id
        if scope.scope == "container":
            if container_id != scope.container_id:
                raise ApiException(status_code=422, code=42262, message="Container-scoped copilot operations must stay within the selected container")
        elif container_id:
            raise ApiException(status_code=422, code=42263, message="Top-level copilot operations cannot target containerId")

        if container_id:
            container_node = self._find_top_level_node(draft_payload["nodes"], container_id)
            if container_node is None or str(container_node.get("nodeType") or "") not in _CONTAINER_NODE_TYPES:
                raise ApiException(status_code=422, code=42264, message=f"Container not found: {container_id}")
            container_config = container_node.get("config")
            if not isinstance(container_config, dict):
                container_config = {}
                container_node["config"] = container_config
            body_nodes = self._ensure_container_body_nodes(container_config)
            body_edges = self._ensure_container_body_edges(container_config)
            self._apply_operation_to_graph(
                node_list=body_nodes,
                edge_list=body_edges,
                scope=scope,
                operation=operation,
                affected_node_ids=affected_node_ids,
                warnings=warnings,
                graph_label=f"container:{container_id}",
            )
            container_config["bodyNodes"] = body_nodes
            container_config["bodyEdges"] = body_edges
            affected_node_ids.add(container_id)
            return

        self._apply_operation_to_graph(
            node_list=draft_payload["nodes"],
            edge_list=draft_payload["edges"],
            scope=scope,
            operation=operation,
            affected_node_ids=affected_node_ids,
            warnings=warnings,
            graph_label="workflow",
        )

    def _apply_operation_to_graph(
        self,
        *,
        node_list: list[dict[str, Any]],
        edge_list: list[dict[str, Any]],
        scope: _ScopeContext,
        operation: WorkflowCopilotOperation,
        affected_node_ids: set[str],
        warnings: list[str],
        graph_label: str,
    ) -> None:
        node_id = str(operation.node_id or "").strip()
        edge_id = str(operation.edge_id or "").strip()

        if operation.type == "add_node":
            node_type = str(operation.node_type or "").strip()
            if not node_type:
                raise ApiException(status_code=422, code=42265, message="add_node requires nodeType")
            current_ids = {str(item.get("nodeId") or "").strip() for item in node_list}
            normalized_node_id = self._normalize_or_generate_node_id(
                requested=node_id,
                node_type=node_type,
                existing_ids=current_ids,
            )
            if node_id and normalized_node_id != node_id:
                warnings.append(f"Requested nodeId '{node_id}' was normalized to '{normalized_node_id}'")
            label = self._build_unique_label(
                requested=operation.label,
                node_type=node_type,
                existing_labels=(str(item.get("label") or "") for item in node_list),
            )
            config = self._deep_merge_dict(
                self._create_default_node_config(node_type=node_type, container=graph_label.startswith("container:")),
                operation.config if isinstance(operation.config, dict) else {},
            )
            pos_x, pos_y = self._resolve_position(
                node_list=node_list,
                requested_x=operation.position_x,
                requested_y=operation.position_y,
            )
            node_list.append({
                "nodeId": normalized_node_id,
                "nodeType": node_type,
                "label": label,
                "positionX": pos_x,
                "positionY": pos_y,
                "config": config,
            })
            scope.new_node_ids.add(normalized_node_id)
            affected_node_ids.add(normalized_node_id)
            return

        if operation.type == "update_node":
            node = self._find_required_node(node_list, node_id=node_id)
            self._ensure_existing_node_allowed(scope, node_id=node_id)
            next_label = str(operation.label or "").strip()
            if next_label:
                node["label"] = self._build_unique_label(
                    requested=next_label,
                    node_type=str(node.get("nodeType") or "llm"),
                    existing_labels=(
                        str(item.get("label") or "")
                        for item in node_list
                        if str(item.get("nodeId") or "") != node_id
                    ),
                )
            node_config = node.get("config") if isinstance(node.get("config"), dict) else {}
            replace_config = bool(operation.replace_config)
            if replace_config:
                next_config = dict(operation.config or {}) if isinstance(operation.config, dict) else {}
            else:
                next_config = dict(node_config)
                if isinstance(operation.config, dict):
                    next_config = self._deep_merge_dict(next_config, operation.config)
                if isinstance(operation.config_patch, dict):
                    next_config = self._deep_merge_dict(next_config, operation.config_patch)
            node["config"] = next_config
            if operation.position_x is not None:
                node["positionX"] = float(operation.position_x)
            if operation.position_y is not None:
                node["positionY"] = float(operation.position_y)
            affected_node_ids.add(node_id)
            return

        if operation.type == "move_node":
            node = self._find_required_node(node_list, node_id=node_id)
            self._ensure_existing_node_allowed(scope, node_id=node_id)
            if operation.position_x is None or operation.position_y is None:
                raise ApiException(status_code=422, code=42266, message="move_node requires positionX and positionY")
            node["positionX"] = float(operation.position_x)
            node["positionY"] = float(operation.position_y)
            affected_node_ids.add(node_id)
            return

        if operation.type == "remove_node":
            self._ensure_existing_node_allowed(scope, node_id=node_id)
            removed = self._remove_node(node_list, node_id=node_id)
            if not removed:
                raise ApiException(status_code=422, code=42267, message=f"Node not found in {graph_label}: {node_id}")
            edge_list[:] = [
                item
                for item in edge_list
                if str(item.get("sourceNodeId") or "") != node_id and str(item.get("targetNodeId") or "") != node_id
            ]
            affected_node_ids.add(node_id)
            return

        if operation.type == "add_edge":
            source_node_id = str(operation.source_node_id or "").strip()
            target_node_id = str(operation.target_node_id or "").strip()
            if not source_node_id or not target_node_id:
                raise ApiException(status_code=422, code=42268, message="add_edge requires sourceNodeId and targetNodeId")
            self._ensure_node_exists(node_list, source_node_id, graph_label)
            self._ensure_node_exists(node_list, target_node_id, graph_label)
            self._ensure_edge_endpoint_allowed(scope, node_id=source_node_id)
            self._ensure_edge_endpoint_allowed(scope, node_id=target_node_id)
            current_edge_ids = {str(item.get("edgeId") or "").strip() for item in edge_list}
            normalized_edge_id = self._normalize_or_generate_edge_id(
                requested=edge_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                existing_ids=current_edge_ids,
            )
            edge_payload: dict[str, Any] = {
                "edgeId": normalized_edge_id,
                "sourceNodeId": source_node_id,
                "targetNodeId": target_node_id,
                "sourceHandle": str(operation.source_handle or "output").strip() or "output",
                "targetHandle": str(operation.target_handle or "input").strip() or "input",
                "conditionType": operation.condition_type,
                "conditionExpr": operation.condition_expr.model_dump(by_alias=True) if operation.condition_expr else None,
                "label": operation.label,
            }
            edge_list.append(edge_payload)
            scope.new_edge_ids.add(normalized_edge_id)
            affected_node_ids.update({source_node_id, target_node_id})
            return

        if operation.type == "remove_edge":
            removed_edge = self._remove_edge(edge_list, operation)
            if removed_edge is None:
                raise ApiException(status_code=422, code=42269, message="Edge not found for remove_edge operation")
            resolved_edge_id = str(removed_edge.get("edgeId") or "").strip()
            if scope.scope == "selection":
                if resolved_edge_id not in scope.selected_edge_ids and resolved_edge_id not in scope.new_edge_ids:
                    source_node_id = str(removed_edge.get("sourceNodeId") or "").strip()
                    target_node_id = str(removed_edge.get("targetNodeId") or "").strip()
                    if source_node_id not in scope.selected_node_ids or target_node_id not in scope.selected_node_ids:
                        raise ApiException(status_code=422, code=42270, message="Selection-scoped copilot operations cannot remove edges outside the selected scope")
            affected_node_ids.update({
                str(removed_edge.get("sourceNodeId") or "").strip(),
                str(removed_edge.get("targetNodeId") or "").strip(),
            })
            return

        raise ApiException(status_code=422, code=42271, message=f"Unsupported copilot operation: {operation.type}")

    def _build_prompt_messages(self, *, workflow_id: UUID, workflow, request: WorkflowCopilotRequest) -> list[dict[str, str]]:
        tools = self._build_tool_catalog_summary()
        models = self._build_model_catalog_summary()
        selection_summary = self._summarize_selection(request)
        focus_node_ids = self._resolve_focus_node_ids(request=request, draft=request.draft)
        workflow_summary = self._summarize_workflow(
            request.draft,
            focus_node_ids=focus_node_ids,
            container_id=selection_summary.get("containerId"),
        )
        validation_summary = self._summarize_validation_context(request)
        test_run_summary = self._summarize_test_run_context(request)
        node_catalog = self._build_node_catalog_summary()
        reference_catalog = self._build_reference_catalog(
            request.draft,
            tools=tools,
            selection=request.selection,
        )
        operation_catalog = self._build_operation_catalog()
        mode_context = self._build_mode_context(
            request=request,
            draft=request.draft,
            selection_summary=selection_summary,
        )
        convo = [
            {
                "role": str(item.role),
                "content": str(item.content).strip()[:_MAX_CONVERSATION_CHARS],
            }
            for item in (request.conversation or [])[-_MAX_CONVERSATION_ITEMS:]
            if str(item.content).strip()
        ]
        payload = {
            "workflowId": str(workflow_id),
            "workflowName": getattr(workflow, "name", ""),
            "mode": request.mode,
            "instruction": str(request.instruction or "").strip(),
            "modeContext": mode_context,
            "selection": selection_summary,
            "workflowSummary": workflow_summary,
            "nodeCatalog": node_catalog,
            "referenceCatalog": reference_catalog,
            "availableTools": tools,
            "availableModels": models,
            "operationCatalog": operation_catalog,
            "validationContext": validation_summary,
            "testRunContext": test_run_summary,
            "conversation": convo,
        }
        system_prompt = self._build_system_prompt()
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ]

    def _build_system_prompt(self) -> str:
        few_shot_examples = [
            {
                "title": "主流程新增 llm 节点并连线",
                "payload": {
                    "status": "proposal",
                    "message": "我补了一个分析节点，并接到了主链路里。",
                    "proposal": {
                        "title": "Add analysis node",
                        "summary": "新增一个 llm 节点并连接 start -> llm_analysis。",
                        "layoutRecommendation": "keep",
                        "warnings": [],
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
                            },
                            {
                                "type": "add_edge",
                                "sourceNodeId": "start",
                                "targetNodeId": "llm_analysis",
                                "sourceHandle": "output",
                                "targetHandle": "input",
                            },
                        ],
                    },
                    "suggestions": [],
                },
            },
            {
                "title": "修改已有节点时优先 configPatch",
                "payload": {
                    "status": "proposal",
                    "message": "我局部更新了 llm_1 的系统提示词。",
                    "proposal": {
                        "title": "Patch llm node",
                        "summary": "保留原有配置，只更新 systemPrompt。",
                        "layoutRecommendation": "keep",
                        "warnings": [],
                        "operations": [
                            {
                                "type": "update_node",
                                "nodeId": "llm_1",
                                "configPatch": {
                                    "systemPrompt": "请根据上游结果输出简明总结。"
                                },
                            }
                        ],
                    },
                    "suggestions": [],
                },
            },
            {
                "title": "container body 内新增节点",
                "payload": {
                    "status": "proposal",
                    "message": "我只在 iter_1 的 body 里补了一个节点。",
                    "proposal": {
                        "title": "Add body llm",
                        "summary": "在 iteration body 内新增 llm 节点。",
                        "layoutRecommendation": "keep",
                        "warnings": [],
                        "operations": [
                            {
                                "type": "add_node",
                                "containerId": "iter_1",
                                "nodeType": "llm",
                                "nodeId": "llm_body_1",
                                "label": "Body LLM",
                                "config": {
                                    "outputMode": "text",
                                    "userInput": "{{start.user_input}}",
                                },
                            }
                        ],
                    },
                    "suggestions": [],
                },
            },
            {
                "title": "只需自动布局",
                "payload": {
                    "status": "proposal",
                    "message": "当前只需要重新布局。",
                    "proposal": {
                        "title": "Auto layout",
                        "summary": "不改业务逻辑，只做自动布局。",
                        "layoutRecommendation": "autolayout",
                        "warnings": [],
                        "operations": [
                            {"type": "autolayout"}
                        ],
                    },
                    "suggestions": [],
                },
            },
        ]
        examples_text = "\n\n".join(
            f"示例 {index}. {item['title']}\n{json.dumps(item['payload'], ensure_ascii=False, indent=2)}"
            for index, item in enumerate(few_shot_examples, start=1)
        )
        return (
            "你是 Workflow Copilot，负责帮助用户编辑工作流草稿。"
            "你只能输出严格 JSON，对工作流的改动必须通过 operations 描述，绝不能输出整份 workflow 作为直接修改结果。"
            "\n\n硬性要求："
            "\n1) 只能输出一个严格 JSON 对象，不能输出 Markdown code fence、注释、解释性前后缀或额外文本；"
            "\n2) JSON key 必须统一使用 camelCase；"
            "\n3) 允许的 status 仅为 proposal / question / analysis / no_op；"
            "\n4) proposedWorkflow 由后端根据 operations 模拟生成，你只负责 message / proposal / suggestions；"
            "\n5) 修改已有节点时优先使用 configPatch，只有确需整体替换时才用 replaceConfig=true + config；"
            "\n6) 引用模板必须使用 stored node id，例如 {{start.user_input}}、{{llm_1.response}}，不要使用显示名称；"
            "\n7) container 范围操作必须显式带 containerId，且不能越出当前 scope；"
            "\n8) 不要编造不存在的 node / tool / model / reference；"
            "\n9) 如果只需自动布局，输出单个 autolayout operation，并将 layoutRecommendation 设为 autolayout；"
            "\n10) 请优先使用提供的 nodeCatalog / referenceCatalog / operationCatalog / availableTools / availableModels；"
            "\n11) 当 mode=edit_selection 时，必须优先围绕 modeContext.primaryTarget 生成修改，不要把“修改这个节点”泛化成整段流程重构；"
            "\n12) 当 mode=edit_selection 时，只有在支撑 primaryTarget 行为正确时，才允许最小必要的相邻节点或连线改动；若用户文本与 selection 冲突，以 primaryTarget 为准。"
            "\n\n输出格式固定为："
            "{"
            '"status":"proposal|question|analysis|no_op",'
            '"message":"给用户的简短回复",'
            '"proposal":{'
            '"title":"提案标题",'
            '"summary":"提案摘要",'
            '"layoutRecommendation":"keep|autolayout",'
            '"warnings":["可选警告"],'
            '"operations":[...]'
            "},"
            '"suggestions":["下一步建议"]'
            "}。"
            "\n\noperations 仅允许以下类型：add_node, update_node, remove_node, add_edge, remove_edge, move_node, autolayout。"
            f"\n\n合法输出示例：\n{examples_text}"
        )

    def _build_tool_catalog_summary(self) -> list[dict[str, Any]]:
        system_tools = self._config_service.list_system_tool_definitions(include_disabled=False, include_schema=False)
        custom_tools = self._config_service.list_tools(sync_system=True, include_disabled=False)
        tool_items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tool in system_tools:
            name = str(tool.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            tool_items.append({
                "name": name,
                "description": tool.get("description"),
                "inputParams": self._normalize_tool_params(tool.get("input_params") or []),
                "outputParams": self._normalize_tool_params(tool.get("output_params") or []),
                "usageHint": _TOOL_USAGE_HINTS.get(name),
                "agentToolAllowed": name != "kb_search",
            })
        for tool in custom_tools:
            name = str(getattr(tool, "name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            tool_items.append({
                "name": name,
                "description": getattr(tool, "description", None),
                "inputParams": self._normalize_tool_params(getattr(tool, "input_params", None) or []),
                "outputParams": self._normalize_tool_params(getattr(tool, "output_params", None) or []),
                "usageHint": _TOOL_USAGE_HINTS.get(name),
                "agentToolAllowed": name != "kb_search",
            })
        tool_items.sort(key=lambda item: str(item.get("name") or ""))
        return tool_items[:80]

    def _build_model_catalog_summary(self) -> list[dict[str, str]]:
        try:
            rows = (
                self.db.query(AiModel)
                .filter(AiModel.model_type == "llm")
                .order_by(AiModel.name.asc())
                .all()
            )
        except Exception:
            logger.debug("workflow copilot skipped model catalog lookup", exc_info=True)
            return []
        return [
            {
                "id": str(item.id),
                "name": str(item.name or ""),
            }
            for item in rows
        ]

    def _build_node_catalog_summary(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for node_type in _DEFAULT_LABEL_BY_TYPE:
            required_config = list(_NODE_REQUIRED_CONFIG.get(node_type, []))
            optional_config = [
                key for key in _NODE_CONFIG_HINTS.get(node_type, [])
                if key not in required_config
            ]
            catalog.append({
                "type": node_type,
                "description": _NODE_DESCRIPTIONS.get(node_type, ""),
                "defaultLabel": _DEFAULT_LABEL_BY_TYPE.get(node_type, node_type),
                "requiredConfig": required_config,
                "commonOptionalConfig": optional_config,
                "enumHints": _NODE_ENUM_HINTS.get(node_type, {}),
                "defaultConfigSample": self._create_default_node_config(node_type=node_type, container=False),
                "behaviorNotes": _NODE_BEHAVIOR_NOTES.get(node_type, []),
                "allowedInContainerBody": node_type in _CONTAINER_BODY_ALLOWED_NODE_TYPES,
                "isContainer": node_type in _CONTAINER_NODE_TYPES,
                "configHints": _NODE_CONFIG_HINTS.get(node_type, []),
            })
        return catalog

    @staticmethod
    def _build_operation_catalog() -> list[dict[str, Any]]:
        return list(_OPERATION_CATALOG)

    def _build_reference_catalog(
        self,
        draft: WorkflowInput,
        *,
        tools: list[dict[str, Any]],
        selection,
    ) -> dict[str, Any]:
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        nodes = payload.get("nodes") or []
        tool_by_name = {str(item.get("name") or ""): item for item in tools}
        start_node = self._find_top_level_node(nodes, "start")
        top_level_nodes = [node for node in nodes if isinstance(node, dict) and str(node.get("nodeId") or "") != "start"]
        focus_node_ids = self._resolve_focus_node_ids(request=None, draft=draft, selection_override=selection)
        ordered_top_nodes = self._prioritize_node_dicts(top_level_nodes, focus_node_ids=focus_node_ids)
        node_refs = [
            self._build_node_reference_entry(node, tool_by_name)
            for node in ordered_top_nodes[:_MAX_REFERENCE_NODES]
        ]
        node_refs = [item for item in node_refs if item is not None]
        catalog: dict[str, Any] = {
            "start": self._build_node_reference_entry(start_node, tool_by_name) if isinstance(start_node, dict) else None,
            "nodes": node_refs,
            "system": [
                {"name": "date", "type": "string", "example": "{{sys.date}}"},
                {"name": "datetime", "type": "string", "example": "{{sys.datetime}}"},
                {"name": "conversation_id", "type": "string", "example": "{{sys.conversation_id}}"},
            ],
            "environment": self._build_environment_reference_entries(start_node),
            "truncatedNodeCount": max(0, len(ordered_top_nodes) - len(node_refs)),
        }
        if selection is not None and str(getattr(selection, "scope", "") or "") == "container":
            container_id = str(getattr(selection, "container_id", "") or "").strip()
            container_node = self._find_top_level_node(nodes, container_id)
            if isinstance(container_node, dict):
                body_summary = self._summarize_body_graph(container_node, focus_node_ids=set())
                body_tool_refs = [
                    self._build_node_reference_entry(node, tool_by_name)
                    for node in self._extract_body_nodes(container_node)
                ]
                catalog["containerBody"] = {
                    "containerId": container_id,
                    "nodes": [item for item in body_tool_refs if item is not None],
                    "edges": body_summary.get("edges", []),
                    "notes": [
                        "container body 内引用仍使用 body 内 stored node id。",
                        "不要给 body 节点引用额外添加 containerId 前缀。",
                    ],
                }
        return catalog

    def _build_mode_context(
        self,
        *,
        request: WorkflowCopilotRequest,
        draft: WorkflowInput,
        selection_summary: dict[str, Any],
    ) -> dict[str, Any]:
        mode = str(request.mode or "generate").strip()
        context: dict[str, Any] = {
            "mode": mode,
            "guidance": {
                "generate": "优先参考 nodeCatalog / referenceCatalog / availableTools，生成局部、可执行的小到中型子流程。",
                "edit_selection": "必须优先围绕 primaryTarget 修改当前选中目标，只允许最小必要的支撑性改动，优先局部 patch。",
                "fix_validation": "先根据 validationContext 定位相关节点，再给出最小修复方案。",
                "analyze_test_run": "结合 testRunContext 解释失败原因，并给出可应用的图改动。",
            }.get(mode, "优先输出局部、可执行、低风险的 proposal。"),
        }
        if mode == "edit_selection":
            context["selectionIntent"] = "user_selected_this_target_to_modify"
            context["editingDirective"] = (
                "优先修改 primaryTarget。只有在支撑该目标节点行为正确时，才允许扩展到最小必要的相邻节点或连线。"
                "如果用户文本指令与 selection 冲突，以 primaryTarget 为最高优先级。"
            )
            context["allowedExpansion"] = "minimal_supporting_changes_only"
            context["primaryTarget"] = self._build_primary_target(draft, selection_summary)
            context["selectionDetail"] = self._build_selection_detail(draft, request.selection, selection_summary)
        elif mode == "fix_validation":
            context["validationFocus"] = self._build_validation_focus(draft, request)
        elif mode == "analyze_test_run":
            context["testRunFocus"] = self._build_test_run_focus(draft, request)
        return context

    def _summarize_workflow(
        self,
        draft: WorkflowInput,
        *,
        focus_node_ids: list[str] | None = None,
        container_id: str | None = None,
    ) -> dict[str, Any]:
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        nodes = [item for item in (payload.get("nodes") or []) if isinstance(item, dict)]
        edges = [item for item in (payload.get("edges") or []) if isinstance(item, dict)]
        prioritized_nodes = self._prioritize_node_dicts(nodes, focus_node_ids=set(focus_node_ids or []))
        prioritized_edges = self._prioritize_edge_dicts(edges, focus_node_ids=set(focus_node_ids or []))
        node_summaries = [
            self._summarize_node(node, detailed=str(node.get("nodeId") or "") in set(focus_node_ids or []))
            for node in prioritized_nodes[:_MAX_SUMMARY_NODES]
        ]
        edge_summaries = [
            self._summarize_edge(edge)
            for edge in prioritized_edges[:_MAX_SUMMARY_EDGES]
        ]
        focus_summaries = [
            self._summarize_node_context(payload, node_id)
            for node_id in (focus_node_ids or [])[:12]
        ]
        focus_summaries = [item for item in focus_summaries if item is not None]
        result: dict[str, Any] = {
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "focusNodeIds": list((focus_node_ids or [])[:12]),
            "nodes": node_summaries,
            "edges": edge_summaries,
            "focusedNodes": focus_summaries,
            "viewport": payload.get("viewport"),
            "hash": build_workflow_draft_hash(draft),
            "truncatedNodeCount": max(0, len(prioritized_nodes) - len(node_summaries)),
            "truncatedEdgeCount": max(0, len(prioritized_edges) - len(edge_summaries)),
        }
        if container_id:
            container_node = self._find_top_level_node(nodes, container_id)
            if isinstance(container_node, dict):
                result["containerSelection"] = self._summarize_body_graph(
                    container_node,
                    focus_node_ids=set(focus_node_ids or []),
                )
        return result

    def _summarize_selection(self, request: WorkflowCopilotRequest) -> dict[str, Any]:
        selection = request.selection
        if selection is None:
            return {
                "scope": "workflow",
                "nodeIds": [],
                "edgeIds": [],
                "containerId": None,
                "primaryNodeId": None,
                "selectedNodeCount": 0,
            }
        node_ids = [str(item).strip() for item in selection.node_ids if str(item).strip()]
        return {
            "scope": selection.scope,
            "nodeIds": node_ids,
            "edgeIds": list(selection.edge_ids),
            "containerId": selection.container_id,
            "primaryNodeId": node_ids[0] if node_ids else None,
            "selectedNodeCount": len(node_ids),
        }

    def _summarize_validation_context(self, request: WorkflowCopilotRequest) -> dict[str, Any] | None:
        if request.validation_context is None:
            return None
        return {
            "errors": [
                {
                    "severity": item.severity,
                    "nodeId": item.node_id,
                    "subflowNodeId": item.subflow_node_id,
                    "message": item.message,
                    "source": item.source,
                }
                for item in request.validation_context.errors[:20]
            ],
            "warnings": [
                {
                    "severity": item.severity,
                    "nodeId": item.node_id,
                    "subflowNodeId": item.subflow_node_id,
                    "message": item.message,
                    "source": item.source,
                }
                for item in request.validation_context.warnings[:20]
            ],
        }

    def _summarize_test_run_context(self, request: WorkflowCopilotRequest) -> dict[str, Any] | None:
        if request.test_run_context is None:
            return None
        trace = request.test_run_context.trace
        raw = request.test_run_context.raw
        trace_count = len(trace) if isinstance(trace, list) else None
        raw_count = len(raw) if isinstance(raw, list) else None
        return {
            "selectedRunId": request.test_run_context.selected_run_id,
            "result": self._trim_json_like(request.test_run_context.result, max_chars=4000),
            "trace": self._trim_json_like(trace, max_chars=8000),
            "raw": self._trim_json_like(raw, max_chars=8000),
            "traceEventCount": trace_count,
            "rawEventCount": raw_count,
        }

    def _build_selection_detail(
        self,
        draft: WorkflowInput,
        selection,
        selection_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if selection is None:
            return None
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        nodes = [item for item in (payload.get("nodes") or []) if isinstance(item, dict)]
        edges = [item for item in (payload.get("edges") or []) if isinstance(item, dict)]
        scope = str(selection_summary.get("scope") or "workflow")
        if scope == "container":
            container_id = str(selection_summary.get("containerId") or "")
            container_node = self._find_top_level_node(nodes, container_id)
            selected_node_ids = {
                str(item).strip()
                for item in (selection_summary.get("nodeIds") or [])
                if str(item).strip()
            }
            if not isinstance(container_node, dict):
                return {
                    "scope": "container",
                    "containerId": container_id,
                    "selectedNodeIds": list(selection_summary.get("nodeIds") or []),
                    "selectedEdgeIds": list(selection_summary.get("edgeIds") or []),
                }
            return {
                "scope": "container",
                "containerId": container_id,
                "selectedNodeIds": list(selection_summary.get("nodeIds") or []),
                "selectedEdgeIds": list(selection_summary.get("edgeIds") or []),
                "containerNode": self._summarize_node(container_node, detailed=True),
                "selectedNodes": [
                    self._summarize_node(node, detailed=True)
                    for node in self._extract_body_nodes(container_node)
                    if str(node.get("nodeId") or "") in selected_node_ids
                ],
                "containerBody": self._summarize_body_graph(container_node, focus_node_ids=set(selection_summary.get("nodeIds") or [])),
            }

        selected_node_ids = {str(item).strip() for item in (selection_summary.get("nodeIds") or []) if str(item).strip()}
        selected_edge_ids = {str(item).strip() for item in (selection_summary.get("edgeIds") or []) if str(item).strip()}
        return {
            "scope": scope,
            "selectedNodeIds": sorted(selected_node_ids),
            "selectedEdgeIds": sorted(selected_edge_ids),
            "selectedNodes": [
                self._summarize_node(node, detailed=True)
                for node in nodes
                if str(node.get("nodeId") or "") in selected_node_ids
            ],
            "selectedEdges": [
                self._summarize_edge(edge)
                for edge in edges
                if str(edge.get("edgeId") or "") in selected_edge_ids
            ],
        }

    def _build_primary_target(
        self,
        draft: WorkflowInput,
        selection_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        primary_node_id = str(selection_summary.get("primaryNodeId") or "").strip()
        if not primary_node_id:
            return None

        payload = draft.model_dump(by_alias=True, exclude_none=False)
        nodes = [item for item in (payload.get("nodes") or []) if isinstance(item, dict)]
        scope = str(selection_summary.get("scope") or "workflow")
        selected_node_count = int(selection_summary.get("selectedNodeCount") or 0)

        if scope == "container":
            container_id = str(selection_summary.get("containerId") or "").strip()
            container_node = self._find_top_level_node(nodes, container_id)
            container_label = None
            body_node = None
            if isinstance(container_node, dict):
                container_label = str(container_node.get("label") or "").strip() or container_id
                body_node = self._find_top_level_node(self._extract_body_nodes(container_node), primary_node_id)
            node_label = (
                str(body_node.get("label") or "").strip() or primary_node_id
                if isinstance(body_node, dict)
                else primary_node_id
            )
            display_path = " / ".join(item for item in [container_label, node_label] if item)
            return {
                "scope": "container",
                "nodeId": primary_node_id,
                "nodeType": (
                    str(body_node.get("nodeType") or "").strip() or None
                    if isinstance(body_node, dict)
                    else None
                ),
                "label": node_label if isinstance(body_node, dict) else None,
                "containerId": container_id or None,
                "containerLabel": container_label,
                "displayPath": display_path or primary_node_id,
                "selectedNodeCount": selected_node_count,
                "targetFound": isinstance(body_node, dict),
            }

        target_node = self._find_top_level_node(nodes, primary_node_id)
        node_label = (
            str(target_node.get("label") or "").strip() or primary_node_id
            if isinstance(target_node, dict)
            else primary_node_id
        )
        return {
            "scope": scope,
            "nodeId": primary_node_id,
            "nodeType": (
                str(target_node.get("nodeType") or "").strip() or None
                if isinstance(target_node, dict)
                else None
            ),
            "label": node_label if isinstance(target_node, dict) else None,
            "displayPath": node_label,
            "selectedNodeCount": selected_node_count,
            "targetFound": isinstance(target_node, dict),
        }

    def _build_validation_focus(self, draft: WorkflowInput, request: WorkflowCopilotRequest) -> dict[str, Any] | None:
        if request.validation_context is None:
            return None
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        related_nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in list(request.validation_context.errors)[:12] + list(request.validation_context.warnings)[:12]:
            node_id = str(item.node_id or "").strip()
            subflow_node_id = str(item.subflow_node_id or "").strip()
            if node_id:
                key = f"{node_id}::{subflow_node_id}"
                if key in seen:
                    continue
                seen.add(key)
                context = self._summarize_node_context(payload, node_id, body_node_id=subflow_node_id or None)
                if context is not None:
                    related_nodes.append(context)
        return {
            "relatedNodeIds": sorted({
                str(item.node_id).strip()
                for item in list(request.validation_context.errors) + list(request.validation_context.warnings)
                if str(item.node_id or "").strip()
            }),
            "relatedNodes": related_nodes[:12],
        }

    def _build_test_run_focus(self, draft: WorkflowInput, request: WorkflowCopilotRequest) -> dict[str, Any] | None:
        if request.test_run_context is None:
            return None
        node_ids, failed_node_ids = self._extract_node_ids_from_test_run(
            trace=request.test_run_context.trace,
            raw=request.test_run_context.raw,
        )
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        related_nodes = [
            self._summarize_node_context(payload, node_id)
            for node_id in node_ids[:12]
        ]
        return {
            "relevantNodeIds": node_ids[:12],
            "failedNodeIds": failed_node_ids[:12],
            "relatedNodes": [item for item in related_nodes if item is not None][:12],
        }

    def _resolve_focus_node_ids(
        self,
        *,
        request: WorkflowCopilotRequest | None,
        draft: WorkflowInput,
        selection_override=None,
    ) -> list[str]:
        payload = draft.model_dump(by_alias=True, exclude_none=False)
        nodes = [item for item in (payload.get("nodes") or []) if isinstance(item, dict)]
        ordered: list[str] = []
        seen: set[str] = set()

        def add(node_id: str) -> None:
            value = str(node_id or "").strip()
            if not value or value in seen:
                return
            seen.add(value)
            ordered.append(value)

        selection = selection_override if selection_override is not None else getattr(request, "selection", None)
        if selection is not None:
            for item in getattr(selection, "node_ids", []) or []:
                add(str(item))
            scope = str(getattr(selection, "scope", "") or "").strip()
            if scope == "container":
                container_id = str(getattr(selection, "container_id", "") or "").strip()
                add(container_id)
                container_node = self._find_top_level_node(nodes, container_id)
                if isinstance(container_node, dict):
                    for body_node in self._extract_body_nodes(container_node):
                        add(str(body_node.get("nodeId") or ""))

        if request is not None and request.validation_context is not None:
            for item in list(request.validation_context.errors) + list(request.validation_context.warnings):
                add(str(item.node_id or ""))

        if request is not None and request.test_run_context is not None:
            node_ids, _failed_node_ids = self._extract_node_ids_from_test_run(
                trace=request.test_run_context.trace,
                raw=request.test_run_context.raw,
            )
            for node_id in node_ids[:12]:
                add(node_id)

        return ordered

    @staticmethod
    def _normalize_tool_params(raw_items: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        result: list[dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                result.append({
                    "name": name,
                    "type": str(item.get("param_type", item.get("paramType", "string")) or "string"),
                    "description": item.get("description"),
                })
                continue
            name = str(getattr(item, "name", "") or "").strip()
            if not name:
                continue
            result.append({
                "name": name,
                "type": str(getattr(item, "param_type", getattr(item, "paramType", "string")) or "string"),
                "description": getattr(item, "description", None),
            })
        return result[:20]

    @staticmethod
    def _prioritize_node_dicts(node_list: list[dict[str, Any]], *, focus_node_ids: set[str]) -> list[dict[str, Any]]:
        def _sort_key(node: dict[str, Any]) -> tuple[int, str]:
            node_id = str(node.get("nodeId") or "").strip()
            return (0 if node_id in focus_node_ids else 1, node_id)

        return sorted(node_list, key=_sort_key)

    @staticmethod
    def _prioritize_edge_dicts(edge_list: list[dict[str, Any]], *, focus_node_ids: set[str]) -> list[dict[str, Any]]:
        def _sort_key(edge: dict[str, Any]) -> tuple[int, str]:
            source_id = str(edge.get("sourceNodeId") or "").strip()
            target_id = str(edge.get("targetNodeId") or "").strip()
            focused = source_id in focus_node_ids or target_id in focus_node_ids
            return (0 if focused else 1, str(edge.get("edgeId") or ""))

        return sorted(edge_list, key=_sort_key)

    def _summarize_node(self, node: dict[str, Any], *, detailed: bool) -> dict[str, Any]:
        node_type = str(node.get("nodeType") or "")
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        summary = {
            "nodeId": node.get("nodeId"),
            "nodeType": node_type,
            "label": node.get("label"),
            "position": {
                "x": node.get("positionX"),
                "y": node.get("positionY"),
            },
            "configSummary": self._summarize_node_config(node_type=node_type, cfg=cfg, detailed=detailed),
        }
        if node_type in _CONTAINER_NODE_TYPES:
            body_nodes = self._extract_body_nodes(node)
            body_edges = self._extract_body_edges(node)
            summary["bodyNodeIds"] = [str(item.get("nodeId") or "") for item in body_nodes[:20]]
            summary["bodyEdgeCount"] = len(body_edges)
        return summary

    def _summarize_body_graph(self, container_node: dict[str, Any], *, focus_node_ids: set[str]) -> dict[str, Any]:
        body_nodes = self._extract_body_nodes(container_node)
        body_edges = self._extract_body_edges(container_node)
        prioritized_nodes = self._prioritize_node_dicts(body_nodes, focus_node_ids=focus_node_ids)
        prioritized_edges = self._prioritize_edge_dicts(body_edges, focus_node_ids=focus_node_ids)
        return {
            "bodyNodeCount": len(body_nodes),
            "bodyEdgeCount": len(body_edges),
            "bodyNodes": [
                self._summarize_node(
                    node,
                    detailed=str(node.get("nodeId") or "") in focus_node_ids or len(focus_node_ids) == 0,
                )
                for node in prioritized_nodes[:20]
            ],
            "edges": [self._summarize_edge(edge) for edge in prioritized_edges[:20]],
        }

    def _summarize_node_config(self, *, node_type: str, cfg: dict[str, Any], detailed: bool) -> dict[str, Any]:
        if node_type == "start":
            normalized = self._normalize_start_config(cfg)
            return {
                "inputMode": normalized["inputMode"],
                "memoryMode": normalized["memoryMode"],
                "structuredFields": normalized["structuredFields"] if detailed else [item["name"] for item in normalized["structuredFields"]],
                "sessionVars": normalized["sessionVars"] if detailed else [item["name"] for item in normalized["sessionVars"]],
            }
        if node_type == "llm":
            output_fields = self._extract_output_field_names(cfg, "outputFields", "output_fields")
            return {
                "systemPrompt": self._preview_text(self._cfg_get(cfg, "systemPrompt", "system_prompt", default=""), detailed=detailed),
                "userInput": self._preview_text(self._cfg_get(cfg, "userInput", "user_input", default=""), detailed=detailed),
                "outputMode": self._normalize_output_mode(self._cfg_get(cfg, "outputMode", "output_mode", default="text")),
                "outputFields": output_fields,
                "knowledgeEnabled": bool(self._cfg_get(cfg, "knowledgeEnabled", "knowledge_enabled", default=False)),
                "knowledgeSourceNodeIds": self._cfg_string_list(cfg, "knowledgeSourceNodeIds", "knowledge_source_node_ids"),
                "knowledgeInjectMode": self._cfg_get(cfg, "knowledgeInjectMode", "knowledge_inject_mode", default=None),
                "modelSource": self._cfg_get(cfg, "modelSource", "model_source", default="default"),
            }
        if node_type == "agent":
            return {
                "systemPrompt": self._preview_text(self._cfg_get(cfg, "systemPrompt", "system_prompt", default=""), detailed=detailed),
                "userInput": self._preview_text(self._cfg_get(cfg, "userInput", "user_input", default=""), detailed=detailed),
                "toolNames": self._cfg_string_list(cfg, "toolNames", "tool_names"),
                "knowledgeEnabled": bool(self._cfg_get(cfg, "knowledgeEnabled", "knowledge_enabled", default=False)),
                "knowledgeMode": self._cfg_get(cfg, "knowledgeMode", "knowledge_mode", default=None),
                "knowledgeTopK": self._cfg_get(cfg, "knowledgeTopK", "knowledge_top_k", default=None),
                "maxIterations": self._cfg_get(cfg, "maxIterations", "max_iterations", default=12),
                "modelSource": self._cfg_get(cfg, "modelSource", "model_source", default="default"),
            }
        if node_type == "tool":
            input_bindings = self._cfg_get(cfg, "inputBindings", "input_bindings", default={})
            binding_keys = sorted(input_bindings.keys()) if isinstance(input_bindings, dict) else []
            return {
                "toolName": self._cfg_get(cfg, "toolName", "tool_name", default=""),
                "inputBindingKeys": binding_keys,
                "inputBindings": input_bindings if detailed and isinstance(input_bindings, dict) else None,
            }
        if node_type == "if_else":
            branches = self._cfg_get(cfg, "branches", default=[])
            branch_summaries = []
            if isinstance(branches, list):
                for item in branches[:10]:
                    if not isinstance(item, dict):
                        continue
                    conditions = item.get("conditions") if isinstance(item.get("conditions"), list) else []
                    branch_summaries.append({
                        "id": item.get("id"),
                        "label": item.get("label"),
                        "logic": item.get("logic"),
                        "conditionCount": len(conditions),
                    })
            return {
                "branchCount": len(branch_summaries),
                "elseHandle": self._cfg_get(cfg, "elseHandle", "else_handle", default="else"),
                "branches": branch_summaries if detailed else branch_summaries[:4],
            }
        if node_type == "parameter_extractor":
            return {
                "inputContent": self._preview_text(self._cfg_get(cfg, "inputContent", "input_content", default=""), detailed=detailed),
                "instruction": self._preview_text(self._cfg_get(cfg, "instruction", default=""), detailed=detailed),
                "outputFields": self._extract_output_field_names(cfg, "outputFields", "output_fields"),
                "modelSource": self._cfg_get(cfg, "modelSource", "model_source", default="default"),
            }
        if node_type == "knowledge_retrieval":
            return {
                "query": self._preview_text(self._cfg_get(cfg, "query", default=""), detailed=detailed),
                "mode": self._cfg_get(cfg, "mode", default=None),
                "topK": self._cfg_get(cfg, "topK", "top_k", default=None),
            }
        if node_type == "iteration":
            return {
                "inputSource": self._preview_text(self._cfg_get(cfg, "inputSource", "input_source", default=""), detailed=detailed),
                "outputVariable": self._cfg_get(cfg, "outputVariable", "output_variable", default="results"),
                "outputSelector": self._preview_text(self._cfg_get(cfg, "outputSelector", "output_selector", default=""), detailed=detailed),
                "parallelMode": self._cfg_get(cfg, "parallelMode", "parallel_mode", default=False),
                "errorStrategy": self._cfg_get(cfg, "errorStrategy", "error_strategy", default=None),
                "flattenOutput": self._cfg_get(cfg, "flattenOutput", "flatten_output", default=None),
                "bodyNodeIds": [str(item.get("nodeId") or "") for item in self._extract_body_nodes_from_cfg(cfg)[:20]],
                "bodyEdgeCount": len(self._extract_body_edges_from_cfg(cfg)),
            }
        if node_type == "loop":
            initial_vars = self._cfg_get(cfg, "initialVars", "initial_vars", default=[])
            initial_var_names = []
            if isinstance(initial_vars, list):
                initial_var_names = [
                    str(item.get("name") or "").strip()
                    for item in initial_vars
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                ]
            return {
                "initialVars": initial_var_names,
                "terminationLogic": self._cfg_get(cfg, "terminationLogic", "termination_logic", default="and"),
                "maxIterations": self._cfg_get(cfg, "maxIterations", "max_iterations", default=10),
                "bodyNodeIds": [str(item.get("nodeId") or "") for item in self._extract_body_nodes_from_cfg(cfg)[:20]],
                "bodyEdgeCount": len(self._extract_body_edges_from_cfg(cfg)),
            }
        if node_type == "code_executor":
            return {
                "language": self._cfg_get(cfg, "language", default="python"),
                "entrypoint": self._cfg_get(cfg, "entrypoint", default="main"),
                "inputBindingKeys": sorted((self._cfg_get(cfg, "inputBindings", "input_bindings", default={}) or {}).keys()) if isinstance(self._cfg_get(cfg, "inputBindings", "input_bindings", default={}), dict) else [],
                "outputFields": self._extract_output_field_names(cfg, "outputFields", "output_fields"),
                "codePreview": self._preview_text(self._cfg_get(cfg, "code", default=""), detailed=detailed),
            }
        if node_type == "http_request":
            return {
                "method": self._cfg_get(cfg, "method", default="GET"),
                "url": self._preview_text(self._cfg_get(cfg, "url", default=""), detailed=detailed),
                "bodyType": self._cfg_get(cfg, "bodyType", "body_type", default="none"),
                "authType": self._cfg_get(cfg, "authType", "auth_type", default="none"),
                "timeoutMs": self._cfg_get(cfg, "timeoutMs", "timeout_ms", default=15000),
                "retryEnabled": self._cfg_get(cfg, "retryEnabled", "retry_enabled", default=False),
            }
        if node_type == "variable_assign":
            return {
                "variableName": self._cfg_get(cfg, "variableName", "variable_name", default=""),
                "operation": self._cfg_get(cfg, "operation", default="set"),
                "valueTemplate": self._preview_text(self._cfg_get(cfg, "valueTemplate", "value_template", default=""), detailed=detailed),
            }
        if node_type == "human_in_loop":
            field_names = []
            fields_raw = self._cfg_get(cfg, "fields", default=[])
            if isinstance(fields_raw, list):
                field_names = [
                    str(item.get("name") or "").strip()
                    for item in fields_raw
                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                ]
            return {
                "title": self._preview_text(self._cfg_get(cfg, "title", default=""), detailed=detailed),
                "instruction": self._preview_text(self._cfg_get(cfg, "instruction", default=""), detailed=detailed),
                "fieldNames": field_names,
                "requireRejectComment": self._cfg_get(cfg, "requireRejectComment", "require_reject_comment", default=True),
            }
        if node_type == "output":
            return {
                "outputMode": self._normalize_output_mode(self._cfg_get(cfg, "outputMode", "output_mode", default="text")),
                "textTemplate": self._preview_text(self._cfg_get(cfg, "textTemplate", "text_template", default=""), detailed=detailed),
                "outputFields": self._extract_output_field_names(cfg, "outputFields", "output_fields"),
            }
        return {"configKeys": sorted(cfg.keys())[:20]}

    @staticmethod
    def _summarize_edge(edge: dict[str, Any]) -> dict[str, Any]:
        return {
            "edgeId": edge.get("edgeId"),
            "sourceNodeId": edge.get("sourceNodeId"),
            "targetNodeId": edge.get("targetNodeId"),
            "sourceHandle": edge.get("sourceHandle"),
            "targetHandle": edge.get("targetHandle"),
            "conditionType": edge.get("conditionType"),
            "label": edge.get("label"),
        }

    def _summarize_node_context(
        self,
        payload: dict[str, Any],
        node_id: str,
        *,
        body_node_id: str | None = None,
    ) -> dict[str, Any] | None:
        nodes = [item for item in (payload.get("nodes") or []) if isinstance(item, dict)]
        if body_node_id:
            container_node = self._find_top_level_node(nodes, node_id)
            if not isinstance(container_node, dict):
                return None
            body_node = self._find_top_level_node(self._extract_body_nodes(container_node), body_node_id)
            if not isinstance(body_node, dict):
                return None
            return {
                "scope": "container",
                "containerId": node_id,
                "node": self._summarize_node(body_node, detailed=True),
            }
        top_level = self._find_top_level_node(nodes, node_id)
        if isinstance(top_level, dict):
            return {
                "scope": "workflow",
                "node": self._summarize_node(top_level, detailed=True),
            }
        if "::" in node_id:
            container_id, body_id = node_id.split("::", 1)
            return self._summarize_node_context(payload, container_id, body_node_id=body_id)
        return None

    def _build_node_reference_entry(
        self,
        node: dict[str, Any] | None,
        tool_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        node_id = str(node.get("nodeId") or "").strip()
        node_type = str(node.get("nodeType") or "").strip()
        if not node_id or not node_type:
            return None
        fields = self._build_node_reference_fields(node, tool_by_name)
        return {
            "nodeId": node_id,
            "nodeType": node_type,
            "label": node.get("label"),
            "fields": fields,
        }

    def _build_node_reference_fields(
        self,
        node: dict[str, Any],
        tool_by_name: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        node_type = str(node.get("nodeType") or "").strip()
        node_id = str(node.get("nodeId") or "").strip()
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        fields: list[tuple[str, str]] = []
        if node_type == "start":
            normalized = self._normalize_start_config(cfg)
            if normalized["inputMode"] == "structured":
                fields.extend((item["name"], self._normalize_param_type(item.get("type"))) for item in normalized["structuredFields"])
            else:
                fields.append(("user_input", "string"))
            if normalized["memoryMode"] == "structured":
                fields.extend((name, "string") for name in _START_MEMORY_STRUCTURED_FIELDS)
        elif node_type in {"llm", "output"}:
            fields.append(("response", "string"))
            fields.extend((name, "string") for name in self._extract_output_field_names(cfg, "outputFields", "output_fields"))
        elif node_type == "agent":
            fields.append(("response", "string"))
        elif node_type == "tool":
            fields.append(("result", "object"))
            tool_name = str(self._cfg_get(cfg, "toolName", "tool_name", default="") or "").strip()
            for item in tool_by_name.get(tool_name, {}).get("outputParams", []) or []:
                name = str(item.get("name") or "").strip()
                if name:
                    fields.append((name, self._normalize_param_type(item.get("type"))))
        elif node_type == "parameter_extractor":
            fields.extend((name, "string") for name in self._extract_output_field_names(cfg, "outputFields", "output_fields"))
        elif node_type == "knowledge_retrieval":
            fields.extend([
                ("result", "object"),
                ("query", "string"),
                ("mode", "string"),
                ("references", "array"),
                ("references_count", "number"),
            ])
        elif node_type == "code_executor":
            fields.extend((name, self._infer_output_field_type(cfg, name)) for name in self._extract_output_field_names(cfg, "outputFields", "output_fields"))
        elif node_type == "http_request":
            fields.extend([
                ("body", "object"),
                ("status_code", "number"),
                ("headers", "object"),
                ("ok", "boolean"),
                ("error_message", "string"),
                ("response", "string"),
            ])
        elif node_type == "human_in_loop":
            fields.extend([
                ("decision", "string"),
                ("comment", "string"),
            ])
            field_defs = self._cfg_get(cfg, "fields", default=[])
            if isinstance(field_defs, list):
                for item in field_defs:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if name:
                        fields.append((name, self._normalize_param_type(item.get("type"))))
        elif node_type == "iteration":
            output_variable = str(self._cfg_get(cfg, "outputVariable", "output_variable", default="results") or "results").strip() or "results"
            fields.extend([
                (output_variable, "array"),
                ("count", "number"),
                ("errors", "array"),
            ])
        elif node_type == "loop":
            fields.extend([
                ("iterations", "number"),
                ("terminated", "boolean"),
                ("last_item", "object"),
            ])
            initial_vars = self._cfg_get(cfg, "initialVars", "initial_vars", default=[])
            if isinstance(initial_vars, list):
                for item in initial_vars:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if name:
                        fields.append((name, self._normalize_param_type(item.get("type"))))
        elif node_type == "if_else":
            fields.append(("handle", "string"))

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for field_name, field_type in fields:
            if field_name in seen:
                continue
            seen.add(field_name)
            deduped.append({
                "name": field_name,
                "type": field_type,
                "example": f"{{{{{node_id}.{field_name}}}}}",
            })
        return deduped

    def _build_environment_reference_entries(self, start_node: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(start_node, dict):
            return []
        cfg = start_node.get("config") if isinstance(start_node.get("config"), dict) else {}
        normalized = self._normalize_start_config(cfg)
        return [
            {
                "name": item["name"],
                "type": self._normalize_param_type(item.get("type")),
                "example": f"{{{{env.{item['name']}}}}}",
                "description": item.get("description"),
            }
            for item in normalized["sessionVars"]
        ]

    @staticmethod
    def _cfg_get(cfg: dict[str, Any], *names: str, default: Any = None) -> Any:
        for name in names:
            if name in cfg:
                return cfg.get(name)
        return default

    @staticmethod
    def _cfg_string_list(cfg: dict[str, Any], *names: str) -> list[str]:
        raw = WorkflowCopilotService._cfg_get(cfg, *names, default=[])
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    @staticmethod
    def _normalize_output_mode(raw: Any) -> str:
        mode = str(raw or "text").strip().lower()
        if mode == "json":
            return "structured"
        return "structured" if mode == "structured" else "text"

    @staticmethod
    def _normalize_param_type(raw: Any) -> str:
        value = str(raw or "string").strip().lower() or "string"
        if value == "integer":
            return "number"
        if value in {"string", "number", "boolean", "array", "object"}:
            return value
        return "string"

    def _normalize_start_config(self, raw_cfg: Any) -> dict[str, Any]:
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        raw_input_mode = str(self._cfg_get(cfg, "inputMode", "input_mode", default="text") or "text").strip().lower()
        input_mode = "structured" if raw_input_mode == "structured" else "text"
        raw_memory_mode = str(self._cfg_get(cfg, "memoryMode", "memory_mode", default="auto") or "auto").strip().lower()
        memory_mode = raw_memory_mode if raw_memory_mode in _START_MEMORY_MODES else "auto"
        raw_fields = self._cfg_get(cfg, "structuredFields", "structured_fields", default=[])
        structured_fields: list[dict[str, Any]] = []
        if isinstance(raw_fields, list):
            for item in raw_fields:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or not _START_FIELD_NAME_RE.fullmatch(name) or name == "user_input" or name in _START_MEMORY_STRUCTURED_FIELDS:
                    continue
                structured_fields.append({
                    "name": name,
                    "type": str(item.get("type") or "string"),
                    "description": item.get("description"),
                })
        raw_session_vars = self._cfg_get(cfg, "sessionVars", "session_vars", default=[])
        session_vars: list[dict[str, Any]] = []
        if isinstance(raw_session_vars, list):
            for item in raw_session_vars:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or not _START_FIELD_NAME_RE.fullmatch(name):
                    continue
                session_vars.append({
                    "name": name,
                    "type": str(item.get("type") or "string"),
                    "description": item.get("description"),
                })
        return {
            "inputMode": input_mode,
            "memoryMode": memory_mode,
            "structuredFields": structured_fields,
            "sessionVars": session_vars,
        }

    @staticmethod
    def _extract_output_field_names(cfg: dict[str, Any], *names: str) -> list[str]:
        raw = WorkflowCopilotService._cfg_get(cfg, *names, default=[])
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    result.append(name)
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result

    def _infer_output_field_type(self, cfg: dict[str, Any], field_name: str) -> str:
        raw = self._cfg_get(cfg, "outputFields", "output_fields", default=[])
        if not isinstance(raw, list):
            return "string"
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name == field_name:
                return self._normalize_param_type(item.get("type"))
        return "string"

    @staticmethod
    def _extract_body_nodes(container_node: dict[str, Any]) -> list[dict[str, Any]]:
        cfg = container_node.get("config") if isinstance(container_node.get("config"), dict) else {}
        return WorkflowCopilotService._extract_body_nodes_from_cfg(cfg)

    @staticmethod
    def _extract_body_nodes_from_cfg(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        raw_nodes = WorkflowCopilotService._cfg_get(cfg, "bodyNodes", "body_nodes", default=[])
        if not isinstance(raw_nodes, list):
            return []
        return [dict(item) for item in raw_nodes if isinstance(item, dict)]

    @staticmethod
    def _extract_body_edges(container_node: dict[str, Any]) -> list[dict[str, Any]]:
        cfg = container_node.get("config") if isinstance(container_node.get("config"), dict) else {}
        return WorkflowCopilotService._extract_body_edges_from_cfg(cfg)

    @staticmethod
    def _extract_body_edges_from_cfg(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        raw_edges = WorkflowCopilotService._cfg_get(cfg, "bodyEdges", "body_edges", default=[])
        if not isinstance(raw_edges, list):
            return []
        return [dict(item) for item in raw_edges if isinstance(item, dict)]

    def _extract_node_ids_from_test_run(self, *, trace: Any, raw: Any) -> tuple[list[str], list[str]]:
        ordered: list[str] = []
        failed: list[str] = []
        seen: set[str] = set()
        seen_failed: set[str] = set()

        def visit(item: Any) -> None:
            if isinstance(item, list):
                for sub in item[:100]:
                    visit(sub)
                return
            if not isinstance(item, dict):
                return
            node_id = str(item.get("nodeId", item.get("node_id", "")) or "").strip()
            event = str(item.get("event") or "").strip().lower()
            status = str(item.get("status") or "").strip().lower()
            if node_id and node_id not in seen:
                seen.add(node_id)
                ordered.append(node_id)
            if node_id and node_id not in seen_failed and (
                status in {"failed", "error", "cancelled"}
                or "error" in event
                or event in {"node_error", "run_error"}
            ):
                seen_failed.add(node_id)
                failed.append(node_id)

        visit(trace)
        visit(raw)
        return ordered, failed

    @staticmethod
    def _preview_text(value: Any, *, detailed: bool) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        limit = _MAX_TEXT_DETAIL if detailed else _MAX_TEXT_PREVIEW
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    def _validate_workflow_payload(self, *, workflow, workflow_input: WorkflowInput) -> WorkflowValidationResponse:
        result = validate_workflow(workflow_input.nodes, workflow_input.edges)
        parallel_result = validate_parallel_branches(workflow_input.nodes, workflow_input.edges)
        all_errors: list[dict[str, Any]] = [
            {"node_id": e.node_id, "message": e.message}
            for e in (result.errors + parallel_result.errors)
        ]
        if len(all_errors) == 0:
            try:
                self._config_service.validate_workflow_dependencies(workflow_input)
            except ApiException as exc:
                all_errors.append({"node_id": None, "message": exc.message})
        for message in self._config_service.collect_workflow_extra_validation_errors(
            workflow=workflow,
            workflow_input=workflow_input,
        ):
            all_errors.append({"node_id": None, "message": message})
        return WorkflowValidationResponse(
            valid=len(all_errors) == 0,
            errors=all_errors,
        )

    @staticmethod
    def _trim_json_like(value: Any, *, max_chars: int) -> Any:
        text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        text = str(text)
        if len(text) <= max_chars:
            return value
        return f"{text[:max_chars]}..."

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text:
                result.append(text)
        return result[:10]

    @staticmethod
    def _find_top_level_node(node_list: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
        normalized = str(node_id or "").strip()
        for node in node_list:
            if str(node.get("nodeId") or "").strip() == normalized:
                return node
        return None

    def _find_required_node(self, node_list: list[dict[str, Any]], *, node_id: str) -> dict[str, Any]:
        node = self._find_top_level_node(node_list, node_id)
        if node is None:
            raise ApiException(status_code=422, code=42272, message=f"Node not found: {node_id}")
        return node

    @staticmethod
    def _ensure_node_exists(node_list: list[dict[str, Any]], node_id: str, graph_label: str) -> None:
        if not any(str(item.get("nodeId") or "").strip() == node_id for item in node_list):
            raise ApiException(status_code=422, code=42273, message=f"Node not found in {graph_label}: {node_id}")

    @staticmethod
    def _ensure_container_body_nodes(container_config: dict[str, Any]) -> list[dict[str, Any]]:
        raw_nodes = container_config.get("bodyNodes")
        if isinstance(raw_nodes, list):
            return [dict(item) for item in raw_nodes if isinstance(item, dict)]
        default_start = {
            "nodeId": "start",
            "nodeType": "start",
            "label": _DEFAULT_LABEL_BY_TYPE["start"],
            "positionX": 40,
            "positionY": 72,
            "config": None,
        }
        return [default_start]

    @staticmethod
    def _ensure_container_body_edges(container_config: dict[str, Any]) -> list[dict[str, Any]]:
        raw_edges = container_config.get("bodyEdges")
        if isinstance(raw_edges, list):
            return [dict(item) for item in raw_edges if isinstance(item, dict)]
        return []

    @staticmethod
    def _remove_node(node_list: list[dict[str, Any]], *, node_id: str) -> bool:
        before = len(node_list)
        node_list[:] = [item for item in node_list if str(item.get("nodeId") or "").strip() != node_id]
        return len(node_list) != before

    @staticmethod
    def _remove_edge(edge_list: list[dict[str, Any]], operation: WorkflowCopilotOperation) -> dict[str, Any] | None:
        resolved_index = -1
        for index, edge in enumerate(edge_list):
            edge_id = str(edge.get("edgeId") or "").strip()
            if operation.edge_id and edge_id == str(operation.edge_id).strip():
                resolved_index = index
                break
            if not operation.edge_id:
                source_node_id = str(edge.get("sourceNodeId") or "").strip()
                target_node_id = str(edge.get("targetNodeId") or "").strip()
                source_handle = str(edge.get("sourceHandle") or "output").strip() or "output"
                target_handle = str(edge.get("targetHandle") or "input").strip() or "input"
                if (
                    source_node_id == str(operation.source_node_id or "").strip()
                    and target_node_id == str(operation.target_node_id or "").strip()
                    and source_handle == (str(operation.source_handle or "output").strip() or "output")
                    and target_handle == (str(operation.target_handle or "input").strip() or "input")
                ):
                    resolved_index = index
                    break
        if resolved_index < 0:
            return None
        removed = edge_list[resolved_index]
        del edge_list[resolved_index]
        return removed

    @staticmethod
    def _ensure_existing_node_allowed(scope: _ScopeContext, *, node_id: str) -> None:
        if scope.scope != "selection":
            return
        if node_id in scope.selected_node_ids or node_id in scope.new_node_ids:
            return
        raise ApiException(status_code=422, code=42274, message="Selection-scoped copilot operations cannot modify nodes outside the current selection")

    @staticmethod
    def _ensure_edge_endpoint_allowed(scope: _ScopeContext, *, node_id: str) -> None:
        if scope.scope != "selection":
            return
        if node_id in scope.selected_node_ids or node_id in scope.new_node_ids:
            return
        raise ApiException(status_code=422, code=42275, message="Selection-scoped copilot operations cannot connect to nodes outside the current selection")

    @staticmethod
    def _normalize_or_generate_node_id(*, requested: str, node_type: str, existing_ids: set[str]) -> str:
        candidate = str(requested or "").strip()
        if candidate and _NODE_ID_RE.fullmatch(candidate) and candidate not in existing_ids:
            return candidate
        base = str(node_type or "node").strip().lower() or "node"
        if base == "start":
            base = "start_copy"
        if not _NODE_ID_RE.fullmatch(base):
            base = re.sub(r"[^a-zA-Z0-9_]+", "_", base).strip("_") or "node"
        index = 1
        while True:
            candidate = f"{base}_{index}"
            if candidate not in existing_ids:
                return candidate
            index += 1

    @staticmethod
    def _normalize_or_generate_edge_id(*, requested: str, source_node_id: str, target_node_id: str, existing_ids: set[str]) -> str:
        candidate = str(requested or "").strip()
        if candidate and candidate not in existing_ids:
            return candidate
        base = f"edge_{source_node_id}_{target_node_id}".strip("_")
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", base)
        index = 1
        while True:
            edge_id = f"{base}_{index}"
            if edge_id not in existing_ids:
                return edge_id
            index += 1

    @staticmethod
    def _normalize_label(value: Any) -> str:
        return str(value or "").replace(".", " ").strip()

    def _build_unique_label(self, *, requested: Any, node_type: str, existing_labels: Iterable[str]) -> str:
        base_label = self._normalize_label(requested) or _DEFAULT_LABEL_BY_TYPE.get(node_type, "Node")
        seen = {
            self._normalize_label(label).casefold()
            for label in existing_labels
            if self._normalize_label(label)
        }
        if base_label.casefold() not in seen:
            return base_label
        index = 2
        while f"{base_label}#{index}".casefold() in seen:
            index += 1
        return f"{base_label}#{index}"

    @staticmethod
    def _deep_merge_dict(base: dict[str, Any] | None, updates: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(base or {})
        for key, value in (updates or {}).items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = WorkflowCopilotService._deep_merge_dict(result.get(key), value)
            else:
                result[key] = value
        return result

    @staticmethod
    def _resolve_position(
        *,
        node_list: list[dict[str, Any]],
        requested_x: float | None,
        requested_y: float | None,
    ) -> tuple[float, float]:
        if requested_x is not None and requested_y is not None:
            return float(requested_x), float(requested_y)
        if not node_list:
            return 120.0, 220.0
        max_x = max(float(item.get("positionX") or 0.0) for item in node_list)
        if requested_y is not None:
            return max_x + 320.0, float(requested_y)
        avg_y = sum(float(item.get("positionY") or 0.0) for item in node_list) / max(len(node_list), 1)
        return max_x + 320.0, avg_y

    @staticmethod
    def _create_default_node_config(*, node_type: str, container: bool) -> dict[str, Any] | None:
        if node_type == "start":
            if container:
                return None
            return {
                "inputMode": "text",
                "memoryMode": "auto",
                "structuredFields": [],
                "sessionVars": [],
            }
        if node_type == "llm":
            return {
                "outputMode": "text",
                "userInput": "{{start.user_input}}",
                "modelSource": "default",
            }
        if node_type == "agent":
            return {
                "userInput": "{{start.user_input}}",
                "toolNames": [],
                "maxIterations": 12,
                "knowledgeEnabled": False,
                "modelSource": "default",
            }
        if node_type == "output":
            return {
                "outputMode": "text",
                "textTemplate": "{{start.user_input}}",
            }
        if node_type == "tool":
            return {
                "toolName": "",
                "inputBindings": {},
            }
        if node_type == "if_else":
            return {
                "branches": [
                    {
                        "id": "branch_1",
                        "label": "IF",
                        "logic": "and",
                        "conditions": [
                            {
                                "id": "cond_1",
                                "variable": "start.user_input",
                                "operator": "contains",
                                "value": "",
                            }
                        ],
                    }
                ],
                "elseHandle": "else",
            }
        if node_type == "parameter_extractor":
            return {
                "modelSource": "default",
                "inputContent": "",
                "outputFields": [
                    {"name": "result", "type": "string", "nullable": False},
                ],
            }
        if node_type == "knowledge_retrieval":
            return {"query": "{{start.user_input}}"}
        if node_type == "code_executor":
            return {
                "language": "python",
                "entrypoint": "main",
                "inputBindings": {"arg1": "", "arg2": ""},
                "outputFields": [{"name": "result", "type": "string", "nullable": False}],
                "code": "def main(arg1=None, arg2=None):\n    return {'result': ''}\n",
            }
        if node_type == "http_request":
            return {
                "method": "GET",
                "url": "",
                "headers": [],
                "queryParams": [],
                "bodyType": "none",
                "jsonBodyTemplate": "",
                "rawBodyTemplate": "",
                "formBody": [],
                "authType": "none",
                "bearerToken": "",
                "apiKeyIn": "header",
                "apiKeyName": "X-API-Key",
                "apiKeyValue": "",
                "timeoutMs": 15000,
                "retryEnabled": False,
                "maxRetries": 2,
                "retryIntervalMs": 200,
                "verifySsl": True,
            }
        if node_type == "variable_assign":
            return {
                "variableName": "",
                "operation": "set",
                "valueTemplate": "",
            }
        if node_type == "human_in_loop":
            return {
                "title": "",
                "instruction": "",
                "fields": [
                    {
                        "name": "value",
                        "label": "Value",
                        "type": "string",
                        "required": True,
                        "valueTemplate": "",
                    }
                ],
                "approveLabel": "",
                "rejectLabel": "",
                "requireRejectComment": True,
            }
        if node_type == "iteration":
            return {
                "inputSource": "",
                "outputVariable": "results",
                "outputSelector": "{{container.item}}",
                "parallelMode": False,
                "errorStrategy": "fail_fast",
                "flattenOutput": True,
                "bodyNodes": [
                    {
                        "nodeId": "start",
                        "nodeType": "start",
                        "label": _DEFAULT_LABEL_BY_TYPE["start"],
                        "positionX": 40,
                        "positionY": 72,
                        "config": None,
                    }
                ],
                "bodyEdges": [],
            }
        if node_type == "loop":
            return {
                "initialVars": [],
                "updateMappings": [],
                "terminationLogic": "and",
                "terminationConditions": [],
                "maxIterations": 10,
                "bodyNodes": [
                    {
                        "nodeId": "start",
                        "nodeType": "start",
                        "label": _DEFAULT_LABEL_BY_TYPE["start"],
                        "positionX": 40,
                        "positionY": 72,
                        "config": None,
                    }
                ],
                "bodyEdges": [],
            }
        return None


def _stable_json_like(value: Any) -> Any:
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_stable_json_like(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _stable_json_like(value[key])
            for key in sorted(value.keys())
        }
    if isinstance(value, ConditionExpressionInput):
        return _stable_json_like(value.model_dump(by_alias=True, exclude_none=False))
    return str(value)


def build_workflow_draft_hash(workflow: WorkflowInput) -> str:
    payload = workflow.model_dump(by_alias=True, exclude_none=False)
    normalized = {
        "nodes": sorted(
            [
                {
                    "nodeId": item.get("nodeId"),
                    "nodeType": item.get("nodeType"),
                    "label": item.get("label") or "",
                    "positionX": _stable_json_like(item.get("positionX", 0)),
                    "positionY": _stable_json_like(item.get("positionY", 0)),
                    "config": _stable_json_like(item.get("config")),
                }
                for item in (payload.get("nodes") or [])
                if isinstance(item, dict)
            ],
            key=lambda item: str(item.get("nodeId") or ""),
        ),
        "edges": sorted(
            [
                {
                    "edgeId": item.get("edgeId"),
                    "sourceNodeId": item.get("sourceNodeId"),
                    "targetNodeId": item.get("targetNodeId"),
                    "sourceHandle": item.get("sourceHandle") or "output",
                    "targetHandle": item.get("targetHandle") or "input",
                    "conditionType": item.get("conditionType"),
                    "conditionExpr": _stable_json_like(item.get("conditionExpr")),
                    "label": item.get("label"),
                }
                for item in (payload.get("edges") or [])
                if isinstance(item, dict)
            ],
            key=lambda item: (
                str(item.get("edgeId") or ""),
                str(item.get("sourceNodeId") or ""),
                str(item.get("targetNodeId") or ""),
            ),
        ),
        "viewport": _stable_json_like(payload.get("viewport")),
    }
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    hash_value = 2166136261
    for char in serialized:
        hash_value ^= ord(char)
        hash_value = (hash_value * 16777619) & 0xFFFFFFFF
    return f"{hash_value:08x}"
