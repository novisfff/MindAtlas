from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, model_validator

from app.common.schemas import CamelModel, OrmModel


ToolKind = Literal["local", "remote"]
AuthType = Literal["none", "bearer", "basic", "api-key"]
BodyType = Literal["none", "form-data", "x-www-form-urlencoded", "json", "xml", "raw"]
SkillMode = Literal["langgraph"]
LanggraphPattern = Literal["agent_loop", "workflow_dag"]
OutputFieldType = Literal["string", "number", "integer", "boolean", "object", "array"]

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
    "start", "llm", "tool", "if_else",
    "parameter_extractor", "knowledge_retrieval",
    "iteration", "loop",
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


class WorkflowValidationError(CamelModel):
    """工作流验证错误项"""
    node_id: str | None = None
    message: str


class WorkflowValidationResponse(CamelModel):
    """工作流拓扑验证响应"""
    valid: bool
    errors: list[WorkflowValidationError] = Field(default_factory=list)


class AssistantSkillCreateRequest(CamelModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=512)
    intent_examples: list[str] = []
    tools: list[str] = []
    mode: SkillMode = "langgraph"
    langgraph_pattern: LanggraphPattern | None = None
    system_prompt: str | None = Field(default=None, max_length=4096)
    enabled: bool = True
    kb_config: dict | None = None
    workflow: WorkflowInput | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillCreateRequest":
        if self.langgraph_pattern is None:
            raise ValueError("langgraph_pattern is required")
        if self.langgraph_pattern == "agent_loop" and not (self.system_prompt or "").strip():
            raise ValueError("agent_loop requires system_prompt")
        if self.langgraph_pattern == "workflow_dag" and not self.workflow:
            raise ValueError("workflow_dag requires workflow data")
        return self


class AssistantSkillUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=512)
    intent_examples: list[str] | None = None
    tools: list[str] | None = None
    mode: SkillMode | None = None
    langgraph_pattern: LanggraphPattern | None = None
    system_prompt: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    kb_config: dict | None = None
    workflow: WorkflowInput | None = None

    @model_validator(mode="after")
    def _validate(self) -> "AssistantSkillUpdateRequest":
        if self.mode is not None and self.mode != "langgraph":
            raise ValueError("mode must be langgraph")
        if self.langgraph_pattern == "agent_loop" and self.system_prompt is not None:
            if not self.system_prompt.strip():
                raise ValueError("agent_loop requires system_prompt")
        if self.langgraph_pattern == "workflow_dag" and self.workflow is None:
            raise ValueError("workflow_dag update requires workflow data")
        return self


class ResetSkillRequest(CamelModel):
    confirm: bool = False


class ResetAllSkillsResponse(CamelModel):
    """重置所有系统技能的响应"""
    reset_count: int
    deleted_count: int
    created_count: int
    affected: list[dict]


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


class AssistantSkillResponse(OrmModel):
    id: UUID
    name: str
    description: str
    intent_examples: list[str] | None
    tools: list[str] | None
    mode: str
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
