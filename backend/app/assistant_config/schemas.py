from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, model_validator

from app.common.schemas import CamelModel, OrmModel


ToolKind = Literal["local", "remote"]
AuthType = Literal["none", "bearer", "basic", "api-key"]
BodyType = Literal["none", "form-data", "x-www-form-urlencoded", "json", "xml", "raw"]
SkillMode = Literal["langgraph"]
LanggraphPattern = Literal["agent_loop", "workflow_dag"]
TargetType = Literal["workflow", "agent"]
AgentModelSource = Literal["default", "custom"]
OutputFieldType = Literal["string", "number", "integer", "boolean", "object", "array"]
VersionSource = Literal["save", "publish"]
SystemBehaviorKey = Literal["weekly_report_generation", "monthly_report_generation"]
WorkflowCallBindingMode = Literal["pinned", "latest"]

# 允许的 URL scheme
ALLOWED_URL_SCHEMES = {"http", "https"}


def validate_endpoint_url(url: str | None) -> None:
    """验证 endpoint_url 的基本安全性"""
    if not url:
        return
    url = url.strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError(f"endpoint_url 只允许 http/https scheme，不允许: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("endpoint_url 缺少主机名")
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "localhost.localdomain"):
        raise ValueError("endpoint_url 不允许访问 localhost")


# ==================== Tool Schemas ====================

class InputParamSchema(CamelModel):
    """输入参数定义"""
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    param_type: str = Field(default="string", max_length=32)  # string|number|boolean|array|object
    required: bool = False


class OutputParamSchema(CamelModel):
    """输出参数定义"""
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    param_type: str = Field(default="string", max_length=32)  # string|number|boolean|array|object


class SystemToolDefinitionResponse(CamelModel):
    """系统工具完整定义（代码即真相）。"""
    name: str
    description: str | None = None
    display_name: str = Field(alias="displayName")
    display_description: str | None = Field(default=None, alias="displayDescription")
    kind: ToolKind = "local"
    is_system: bool = True
    enabled: bool = True
    input_params: list[InputParamSchema] | None = None
    output_params: list[OutputParamSchema] | None = None
    returns: str | None = None
    json_schema: dict | None = None


class SystemToolEnabledUpdateRequest(CamelModel):
    """更新系统工具启用状态（默认启用；禁用才会落库为覆盖配置）。"""
    enabled: bool = True


class AssistantToolCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    kind: ToolKind = "remote"
    enabled: bool = True

    # 输入参数
    input_params: list[InputParamSchema] | None = None
    # 输出参数（暂不持久化，保留接口兼容）
    output_params: list[OutputParamSchema] | None = None

    endpoint_url: str | None = Field(default=None, max_length=2048)
    http_method: str | None = Field(default="POST", max_length=10)
    headers: dict[str, str] | None = None

    # Query params
    query_params: dict[str, str] | None = None

    # Body config
    body_type: BodyType | None = "none"
    body_content: str | None = None

    # Auth config
    auth_type: AuthType | None = "none"
    auth_header_name: str | None = Field(default="Authorization", max_length=128)
    auth_scheme: str | None = Field(default="Bearer", max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    timeout_seconds: int | None = Field(default=15, ge=1, le=120)
    payload_wrapper: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> "AssistantToolCreateRequest":
        if self.kind == "remote":
            if not (self.endpoint_url or "").strip():
                raise ValueError("endpoint_url is required when kind=remote")
            # 验证 URL 安全性
            validate_endpoint_url(self.endpoint_url)
        return self


class AssistantToolUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    enabled: bool | None = None

    # 输入参数
    input_params: list[InputParamSchema] | None = None
    # 输出参数（暂不持久化，保留接口兼容）
    output_params: list[OutputParamSchema] | None = None

    endpoint_url: str | None = Field(default=None, max_length=2048)
    http_method: str | None = Field(default=None, max_length=10)
    headers: dict[str, str] | None = None

    # Query params
    query_params: dict[str, str] | None = None

    # Body config
    body_type: BodyType | None = None
    body_content: str | None = None

    # Auth config
    auth_type: AuthType | None = None
    auth_header_name: str | None = Field(default=None, max_length=128)
    auth_scheme: str | None = Field(default=None, max_length=32)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)

    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    payload_wrapper: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _validate(self) -> "AssistantToolUpdateRequest":
        # 验证 URL 安全性（如果提供了 endpoint_url）
        if self.endpoint_url:
            validate_endpoint_url(self.endpoint_url)
        return self


class AssistantToolResponse(OrmModel):
    id: UUID
    name: str
    description: str | None
    kind: str
    is_system: bool
    enabled: bool

    # 输入参数
    input_params: list[dict] | None
    # 输出参数（当前仅系统工具定义可提供，远程工具默认 None）
    output_params: list[dict] | None = None

    endpoint_url: str | None
    http_method: str | None
    headers: dict | None

    # Query params
    query_params: dict | None

    # Body config
    body_type: str | None
    body_content: str | None

    # Auth config
    auth_type: str | None
    auth_header_name: str | None
    auth_scheme: str | None
    api_key_hint: str | None

    timeout_seconds: int | None
    payload_wrapper: str | None

    created_at: datetime
    updated_at: datetime


# ==================== Skill Schemas ====================

class OutputFieldSpecInput(CamelModel):
    """输出字段规格定义（API 输入）"""
    name: str = Field(..., min_length=1, max_length=64)
    type: OutputFieldType = "string"
    nullable: bool = False
    items_type: OutputFieldType | None = None
    enum: list[str] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "OutputFieldSpecInput":
        if not re.fullmatch(r"[a-zA-Z0-9_]+", self.name):
            raise ValueError(f"Invalid output field name: {self.name}")
        if self.type == "array" and not self.items_type:
            raise ValueError("items_type is required when type=array")
        if self.items_type == "array":
            raise ValueError("items_type cannot be array")
        return self


# ==================== Workflow DAG Schemas ====================

NodeType = Literal[
    "start", "llm", "agent", "tool", "if_else",
    "parameter_extractor", "knowledge_retrieval",
    "iteration", "loop", "code_executor", "http_request", "variable_assign", "human_in_loop", "workflow_call", "output",
]
ConditionOperator = Literal[
    "contains", "not_contains", "starts_with", "ends_with",
    "is", "is_not", "is_empty", "is_not_empty",
    # legacy operators kept for compatibility with old saved workflows
    "equals", "not_equals", "gt", "lt", "gte", "lte",
]


class ConditionExpressionInput(CamelModel):
    """IF/ELSE 条件表达式"""
    id: str = Field(..., min_length=1, max_length=64)
    variable: str = Field(..., min_length=1, max_length=256)
    operator: ConditionOperator
    value: str | None = None
    handle: str = Field(..., min_length=1, max_length=64)


class WorkflowNodeInput(CamelModel):
    """工作流节点输入"""
    node_id: str = Field(..., min_length=1, max_length=128)
    node_type: NodeType
    label: str = Field(default="", max_length=256)
    position_x: float = 0.0
    position_y: float = 0.0
    config: dict | None = None

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowNodeInput":
        if not re.fullmatch(r"[a-zA-Z0-9_]+", self.node_id):
            raise ValueError(f"Invalid node_id: {self.node_id}")
        return self


class WorkflowEdgeInput(CamelModel):
    """工作流边输入"""
    edge_id: str = Field(..., min_length=1, max_length=128)
    source_node_id: str = Field(..., min_length=1, max_length=128)
    target_node_id: str = Field(..., min_length=1, max_length=128)
    source_handle: str = Field(default="output", max_length=64)
    target_handle: str = Field(default="input", max_length=64)
    condition_type: Literal["expression", "default"] | None = None
    condition_expr: ConditionExpressionInput | None = None
    label: str | None = Field(default=None, max_length=256)


class WorkflowInput(CamelModel):
    """工作流 DAG 输入（nodes + edges + viewport）"""
    nodes: list[WorkflowNodeInput] = Field(..., min_length=1)
    edges: list[WorkflowEdgeInput] = Field(default_factory=list)
    viewport: dict | None = None


class WorkflowContractParamSchema(CamelModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    param_type: str = Field(default="string", max_length=32)
    required: bool = False
    nullable: bool = False
    items_type: str | None = Field(default=None, max_length=32)
    enum: list[str] | None = None


class WorkflowCallNodeConfig(CamelModel):
    target_workflow_id: UUID | None = None
    binding_mode: WorkflowCallBindingMode = "pinned"
    target_published_version_id: UUID | None = None
    input_bindings: dict[str, str] = Field(default_factory=dict)


class CallableWorkflowVersionResponse(CamelModel):
    id: UUID
    sequence_no: int
    version_name: str
    version_source: VersionSource
    input_params: list[WorkflowContractParamSchema] = Field(default_factory=list)
    output_params: list[WorkflowContractParamSchema] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CallableWorkflowResponse(CamelModel):
    id: UUID
    name: str
    description: str | None = None
    published_version_id: UUID
    input_params: list[WorkflowContractParamSchema] = Field(default_factory=list)
    output_params: list[WorkflowContractParamSchema] = Field(default_factory=list)
    available_versions: list[CallableWorkflowVersionResponse] = Field(default_factory=list)


def _resolve_workflow_start_input_mode(workflow: WorkflowInput | None) -> str:
    if workflow is None:
        return "text"
    for node in workflow.nodes:
        if node.node_type != "start":
            continue
        cfg = node.config if isinstance(node.config, dict) else {}
        raw_mode = str(cfg.get("input_mode", cfg.get("inputMode", "text")) or "text").strip().lower()
        if raw_mode == "structured":
            return "structured"
        return "text"
    return "text"


class WorkflowConversationHistoryItem(CamelModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowConversationHistoryItem":
        self.content = str(self.content or "").strip()
        if not self.content:
            raise ValueError("history content must not be empty")
        return self


class WorkflowTestSessionMemoryInput(CamelModel):
    conversation_summary: str | None = Field(default=None, max_length=8000)
    skill_facts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowTestSessionMemoryInput":
        if self.conversation_summary is not None:
            self.conversation_summary = str(self.conversation_summary or "").strip()
        normalized_facts: list[str] = []
        for item in self.skill_facts:
            value = str(item or "").strip()
            if not value:
                continue
            normalized_facts.append(value)
        self.skill_facts = normalized_facts
        return self


class WorkflowTestRunRequest(CamelModel):
    """工作流测试运行请求（仅运行草稿，不持久化）。"""
    workflow: WorkflowInput
    user_input: str | None = Field(default=None, max_length=8000)
    structured_input: dict | None = None
    session_id: UUID | None = None
    history: list[WorkflowConversationHistoryItem] = Field(default_factory=list, max_length=100)
    session_memory: WorkflowTestSessionMemoryInput | None = None
    stream_output: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowTestRunRequest":
        mode = _resolve_workflow_start_input_mode(self.workflow)
        if mode == "structured":
            if not isinstance(self.structured_input, dict):
                raise ValueError("structured_input is required when start inputMode=structured")
            if self.user_input is not None and str(self.user_input).strip():
                raise ValueError("user_input is not allowed when start inputMode=structured")
            if self.history:
                raise ValueError("history is not allowed when start inputMode=structured")
            if self.session_memory is not None:
                raise ValueError("session_memory is not allowed when start inputMode=structured")
            return self

        if self.structured_input is not None:
            raise ValueError("structured_input is only allowed when start inputMode=structured")
        if self.user_input is None or not str(self.user_input).strip():
            raise ValueError("user_input is required when start inputMode=text")
        self.user_input = str(self.user_input).strip()
        return self


class HumanApprovalDecisionRequest(CamelModel):
    decision: Literal["approved", "rejected"]
    values: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = Field(default=None, max_length=2000)


class AgentTestRunDraftInput(CamelModel):
    """Agent 测试运行草稿配置（不持久化）。"""
    system_prompt: str = Field(..., min_length=1, max_length=4096)
    tools: list[str] = []
    kb_config: dict | None = None
    model_source: AgentModelSource = "default"
    model_id: UUID | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AgentTestRunDraftInput":
        if not self.system_prompt.strip():
            raise ValueError("system_prompt is required")
        if self.model_source == "custom" and self.model_id is None:
            raise ValueError("custom model_source requires model_id")
        if self.model_source == "default":
            self.model_id = None
        return self


class AgentTestRunRequest(CamelModel):
    """Agent 测试运行请求（仅运行草稿，不持久化）。"""
    draft: AgentTestRunDraftInput
    user_input: str = Field(..., min_length=1, max_length=8000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    stream_output: bool = True

    @model_validator(mode="after")
    def _validate_history(self) -> "AgentTestRunRequest":
        normalized_history: list[dict[str, str]] = []
        for item in self.history:
            if not isinstance(item, dict):
                raise ValueError("history items must be objects")
            role = str(item.get("role", "") or "").strip().lower()
            content = str(item.get("content", "") or "").strip()
            if role not in {"user", "assistant"}:
                raise ValueError("history role must be user or assistant")
            if not content:
                raise ValueError("history content must not be empty")
            if len(content) > 8000:
                raise ValueError("history content exceeds max length 8000")
            normalized_history.append({"role": role, "content": content})
        self.history = normalized_history
        return self


class WorkflowValidationError(CamelModel):
    """工作流验证错误项"""
    node_id: str | None = None
    message: str


class WorkflowValidationResponse(CamelModel):
    """工作流拓扑验证响应"""
    valid: bool
    errors: list[WorkflowValidationError] = Field(default_factory=list)


WorkflowCopilotMode = Literal["generate", "edit_selection", "fix_validation", "analyze_test_run"]
WorkflowCopilotSelectionScope = Literal["workflow", "selection", "container"]
WorkflowCopilotStatus = Literal["proposal", "question", "analysis", "no_op"]
WorkflowCopilotLayoutRecommendation = Literal["keep", "autolayout"]
WorkflowCopilotOperationType = Literal[
    "add_node",
    "update_node",
    "remove_node",
    "add_edge",
    "remove_edge",
    "move_node",
    "autolayout",
]


class WorkflowCopilotSelectionInput(CamelModel):
    scope: WorkflowCopilotSelectionScope = "workflow"
    node_ids: list[str] = Field(default_factory=list, max_length=100)
    edge_ids: list[str] = Field(default_factory=list, max_length=100)
    container_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowCopilotSelectionInput":
        self.node_ids = [str(item or "").strip() for item in self.node_ids if str(item or "").strip()]
        self.edge_ids = [str(item or "").strip() for item in self.edge_ids if str(item or "").strip()]
        if self.scope == "container" and not str(self.container_id or "").strip():
            raise ValueError("container_id is required when selection scope=container")
        if self.scope != "container":
            self.container_id = None
        return self


class WorkflowCopilotValidationIssueInput(CamelModel):
    severity: Literal["error", "warning"]
    node_id: str | None = Field(default=None, max_length=128)
    subflow_node_id: str | None = Field(default=None, max_length=128)
    message: str = Field(..., min_length=1, max_length=2000)
    source: str = Field(default="backend", max_length=64)


class WorkflowCopilotValidationContextInput(CamelModel):
    errors: list[WorkflowCopilotValidationIssueInput] = Field(default_factory=list, max_length=100)
    warnings: list[WorkflowCopilotValidationIssueInput] = Field(default_factory=list, max_length=100)


class WorkflowCopilotTestRunContextInput(CamelModel):
    selected_run_id: str = Field(..., min_length=1, max_length=128)
    result: Any = None
    trace: Any = None
    raw: Any = None


class WorkflowCopilotConversationItem(CamelModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowCopilotConversationItem":
        self.content = str(self.content or "").strip()
        if not self.content:
            raise ValueError("conversation content must not be empty")
        return self


class WorkflowCopilotOperation(CamelModel):
    type: WorkflowCopilotOperationType
    container_id: str | None = Field(default=None, max_length=128)
    node_id: str | None = Field(default=None, max_length=128)
    node_type: NodeType | None = None
    label: str | None = Field(default=None, max_length=256)
    config: dict | None = None
    config_patch: dict | None = None
    replace_config: bool = False
    position_x: float | None = None
    position_y: float | None = None
    edge_id: str | None = Field(default=None, max_length=128)
    source_node_id: str | None = Field(default=None, max_length=128)
    target_node_id: str | None = Field(default=None, max_length=128)
    source_handle: str | None = Field(default=None, max_length=64)
    target_handle: str | None = Field(default=None, max_length=64)
    condition_type: Literal["expression", "default"] | None = None
    condition_expr: ConditionExpressionInput | None = None


class WorkflowCopilotProposalResponse(CamelModel):
    title: str
    summary: str
    operations: list[WorkflowCopilotOperation] = Field(default_factory=list)
    proposed_workflow: WorkflowInput
    base_draft_hash: str
    proposed_draft_hash: str
    layout_recommendation: WorkflowCopilotLayoutRecommendation = "keep"
    validation: WorkflowValidationResponse
    affected_node_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowCopilotRequest(CamelModel):
    mode: WorkflowCopilotMode
    instruction: str = Field(default="", max_length=8000)
    draft: WorkflowInput
    selection: WorkflowCopilotSelectionInput | None = None
    conversation: list[WorkflowCopilotConversationItem] = Field(default_factory=list, max_length=40)
    validation_context: WorkflowCopilotValidationContextInput | None = None
    test_run_context: WorkflowCopilotTestRunContextInput | None = None


class WorkflowCopilotResponse(CamelModel):
    status: WorkflowCopilotStatus
    message: str
    proposal: WorkflowCopilotProposalResponse | None = None
    suggestions: list[str] = Field(default_factory=list)


class AssistantWorkflowCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    enabled: bool = True
    workflow: WorkflowInput | None = None


class AssistantWorkflowUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    workflow: WorkflowInput | None = None


class WorkflowPublishRequest(CamelModel):
    workflow: WorkflowInput
    version_name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=512)


class AssistantAgentProfileCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    system_prompt: str = Field(..., min_length=1, max_length=4096)
    tools: list[str] = []
    kb_config: dict | None = None
    model_source: AgentModelSource = "default"
    model_id: UUID | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "AssistantAgentProfileCreateRequest":
        if not self.system_prompt.strip():
            raise ValueError("system_prompt is required")
        if self.model_source == "custom" and self.model_id is None:
            raise ValueError("custom model_source requires model_id")
        if self.model_source == "default":
            self.model_id = None
        return self


class AssistantAgentProfileUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    system_prompt: str | None = Field(default=None, max_length=4096)
    tools: list[str] | None = None
    kb_config: dict | None = None
    model_source: AgentModelSource | None = None
    model_id: UUID | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantAgentProfileUpdateRequest":
        if self.system_prompt is not None and not self.system_prompt.strip():
            raise ValueError("system_prompt is required")
        if self.model_source == "custom" and self.model_id is None:
            raise ValueError("custom model_source requires model_id")
        if self.model_source == "default":
            self.model_id = None
        return self


class AgentPublishDraftInput(CamelModel):
    system_prompt: str = Field(..., min_length=1, max_length=4096)
    tools: list[str] = []
    kb_config: dict | None = None
    model_source: AgentModelSource = "default"
    model_id: UUID | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AgentPublishDraftInput":
        if not self.system_prompt.strip():
            raise ValueError("system_prompt is required")
        if self.model_source == "custom" and self.model_id is None:
            raise ValueError("custom model_source requires model_id")
        if self.model_source == "default":
            self.model_id = None
        return self


class AgentPublishRequest(CamelModel):
    draft: AgentPublishDraftInput
    version_name: str | None = Field(default=None, max_length=255)


class AssistantSkillCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=512)
    intent_examples: list[str] = []
    tools: list[str] = []
    mode: SkillMode = "langgraph"
    target_type: TargetType | None = None
    workflow_id: UUID | None = None
    agent_profile_id: UUID | None = None
    langgraph_pattern: LanggraphPattern | None = None
    system_prompt: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    kb_config: dict | None = None
    workflow: WorkflowInput | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillCreateRequest":
        if self.mode != "langgraph":
            raise ValueError("mode must be langgraph")
        if self.workflow_id and self.agent_profile_id:
            raise ValueError("workflow_id and agent_profile_id are mutually exclusive")
        if self.target_type == "workflow" and self.agent_profile_id:
            raise ValueError("target_type=workflow cannot set agent_profile_id")
        if self.target_type == "agent" and self.workflow_id:
            raise ValueError("target_type=agent cannot set workflow_id")
        has_new_target = bool(self.target_type or self.workflow_id or self.agent_profile_id)
        if not has_new_target and self.langgraph_pattern is None:
            raise ValueError("target_type or langgraph_pattern is required")
        if self.langgraph_pattern == "agent_loop" and not self.agent_profile_id and not (self.system_prompt or "").strip():
            raise ValueError("agent_loop requires system_prompt")
        return self


class AssistantSkillUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=512)
    intent_examples: list[str] | None = None
    tools: list[str] | None = None
    mode: SkillMode | None = None
    target_type: TargetType | None = None
    workflow_id: UUID | None = None
    agent_profile_id: UUID | None = None
    langgraph_pattern: LanggraphPattern | None = None
    system_prompt: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    kb_config: dict | None = None
    workflow: WorkflowInput | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillUpdateRequest":
        if self.mode is not None and self.mode != "langgraph":
            raise ValueError("mode must be langgraph")
        if self.workflow_id and self.agent_profile_id:
            raise ValueError("workflow_id and agent_profile_id are mutually exclusive")
        if self.target_type == "workflow" and self.agent_profile_id:
            raise ValueError("target_type=workflow cannot set agent_profile_id")
        if self.target_type == "agent" and self.workflow_id:
            raise ValueError("target_type=agent cannot set workflow_id")
        if self.langgraph_pattern == "agent_loop" and self.system_prompt is not None:
            if not self.system_prompt.strip():
                raise ValueError("agent_loop requires system_prompt")
        return self


class ResetSkillRequest(CamelModel):
    confirm: bool = False


class ResetAllSkillsResponse(CamelModel):
    """重置所有系统技能的响应"""
    reset_count: int
    deleted_count: int
    created_count: int
    affected: list[dict]


class ResetAllSystemBehaviorsAffectedItem(CamelModel):
    behavior_key: SystemBehaviorKey
    name: str
    target_type: TargetType
    target_name: str


class ResetAllSystemBehaviorsResponse(CamelModel):
    """重置所有系统 AI 行为绑定的响应"""
    reset_count: int
    affected: list[ResetAllSystemBehaviorsAffectedItem] = []


class WorkflowNodeResponse(OrmModel):
    """工作流节点响应"""
    id: UUID
    node_id: str
    node_type: str
    label: str
    position_x: float
    position_y: float
    config: dict | None
    created_at: datetime
    updated_at: datetime


class WorkflowEdgeResponse(OrmModel):
    """工作流边响应"""
    id: UUID
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_handle: str
    target_handle: str
    condition_type: str | None
    condition_expr: dict | None
    label: str | None
    created_at: datetime
    updated_at: datetime


class AssistantWorkflowResponse(OrmModel):
    id: UUID
    name: str
    description: str
    is_system: bool
    enabled: bool
    workflow_version: int = 1
    workflow_viewport: dict | None = None
    nodes: list[WorkflowNodeResponse] = []
    edges: list[WorkflowEdgeResponse] = []
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None
    referenced_skill_ids: list[UUID] = []
    reference_count: int = 0
    referenced_system_behavior_keys: list[SystemBehaviorKey] = []
    system_behavior_reference_count: int = 0
    created_at: datetime
    updated_at: datetime


class AssistantAgentProfileResponse(OrmModel):
    id: UUID
    name: str
    description: str
    system_prompt: str | None
    tools: list[str] | None
    kb_config: dict | None
    model_source: AgentModelSource = "default"
    model_id: UUID | None = None
    is_system: bool
    enabled: bool
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None
    referenced_skill_ids: list[UUID] = []
    reference_count: int = 0
    referenced_system_behavior_keys: list[SystemBehaviorKey] = []
    system_behavior_reference_count: int = 0
    created_at: datetime
    updated_at: datetime


class SystemBehaviorContractFieldResponse(CamelModel):
    name: str
    type: OutputFieldType
    required: bool = True
    description: str = ""
    items_type: OutputFieldType | None = None


class SystemBehaviorContractSummaryResponse(CamelModel):
    input_fields: list[SystemBehaviorContractFieldResponse] = []
    output_fields: list[SystemBehaviorContractFieldResponse] = []


class SystemBehaviorTargetSummaryResponse(CamelModel):
    id: UUID
    target_type: TargetType
    name: str
    description: str = ""
    enabled: bool
    is_system: bool
    is_canonical_default: bool = False
    workflow_id: UUID | None = None
    agent_profile_id: UUID | None = None
    published_version_id: UUID | None = None


class SystemBehaviorResponse(CamelModel):
    behavior_key: SystemBehaviorKey
    name: str
    description: str
    supported_target_types: list[TargetType] = []
    current_binding: SystemBehaviorTargetSummaryResponse
    canonical_default_target: SystemBehaviorTargetSummaryResponse
    fallback_policy: str
    contract: SystemBehaviorContractSummaryResponse


class SystemBehaviorExampleWorkflowCreateResponse(CamelModel):
    created_workflow: AssistantWorkflowResponse
    system_behavior: SystemBehaviorResponse


class SystemBehaviorExampleWorkflowCreateRequest(CamelModel):
    bind_to_behavior: bool = False


class SystemBehaviorBindingUpdateRequest(CamelModel):
    target_type: TargetType
    workflow_id: UUID | None = None
    agent_profile_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> "SystemBehaviorBindingUpdateRequest":
        if self.workflow_id and self.agent_profile_id:
            raise ValueError("workflow_id and agent_profile_id are mutually exclusive")
        if self.target_type == "workflow":
            if self.workflow_id is None:
                raise ValueError("workflow_id is required when target_type=workflow")
            if self.agent_profile_id is not None:
                raise ValueError("agent_profile_id is not allowed when target_type=workflow")
        if self.target_type == "agent":
            if self.agent_profile_id is None:
                raise ValueError("agent_profile_id is required when target_type=agent")
            if self.workflow_id is not None:
                raise ValueError("workflow_id is not allowed when target_type=agent")
        return self


class TargetVersionResponse(OrmModel):
    id: UUID
    sequence_no: int
    version_name: str
    version_source: VersionSource
    created_at: datetime
    updated_at: datetime


class WorkflowVersionListResponse(CamelModel):
    workflow_id: UUID
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None
    versions: list[TargetVersionResponse] = []


class AgentVersionListResponse(CamelModel):
    agent_profile_id: UUID
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None
    versions: list[TargetVersionResponse] = []


class DeleteVersionResponse(CamelModel):
    deleted_version_id: UUID
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None


class ClearVersionsResponse(CamelModel):
    deleted_count: int = 0
    kept_latest_version_id: UUID | None = None
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None


class RollbackVersionResponse(CamelModel):
    draft_version_id: UUID | None = None
    published_version_id: UUID | None = None
    workflow: WorkflowInput | None = None
    agent_draft: AgentPublishDraftInput | None = None


class SkillTargetSummary(CamelModel):
    id: UUID
    name: str
    enabled: bool


class AssistantSkillResponse(OrmModel):
    id: UUID
    name: str
    description: str
    intent_examples: list[str] | None
    tools: list[str] | None
    mode: str
    target_type: TargetType | None = None
    workflow_id: UUID | None = None
    agent_profile_id: UUID | None = None
    target_summary: SkillTargetSummary | None = None
    langgraph_pattern: str | None = None
    system_prompt: str | None
    is_system: bool
    enabled: bool
    kb_config: dict | None
    workflow_version: int = 1
    workflow_viewport: dict | None = None
    nodes: list[WorkflowNodeResponse] = []
    edges: list[WorkflowEdgeResponse] = []
    created_at: datetime
    updated_at: datetime
