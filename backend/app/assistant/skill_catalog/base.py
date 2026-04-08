"""Skill 基础数据结构"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# 默认 Skill 名称常量
DEFAULT_SKILL_NAME = "general_chat"


def is_default_skill(name: str) -> bool:
    """判断是否为默认技能 (不可禁用的兜底技能)"""
    return name == DEFAULT_SKILL_NAME


AnalysisOutputMode = Literal["text", "json"]

# 输出字段类型
OutputFieldType = Literal["string", "number", "integer", "boolean", "object", "array"]

# DAG 节点类型
NodeType = Literal[
    "start", "llm", "agent", "tool", "if_else",
    "parameter_extractor", "knowledge_retrieval",
    "iteration", "loop", "code_executor", "http_request", "variable_assign", "human_in_loop", "workflow_call", "output",
]

# 条件运算符
ConditionOperator = Literal[
    "contains", "not_contains", "starts_with", "ends_with",
    "is", "is_not", "is_empty", "is_not_empty",
    # legacy operators kept for runtime compatibility
    "equals", "not_equals", "gt", "lt", "gte", "lte",
]

# ==================== Output Field 配置 ====================


class OutputFieldSpec(BaseModel):
    """输出字段规格定义"""
    name: str  # 字段名，正则：[a-zA-Z0-9_]+
    type: OutputFieldType = "string"  # 字段类型，默认 string
    nullable: bool = False  # 是否可为 null
    items_type: Optional[OutputFieldType] = None  # 仅当 type="array" 时，元素类型
    enum: Optional[list[str]] = None  # 枚举值列表


def normalize_output_fields(raw: Any) -> list[OutputFieldSpec]:
    """将 output_fields 归一化为 OutputFieldSpec 列表

    支持输入格式：
    - list[str]: 旧格式，推导为 type="string"
    - list[dict]: 新格式，解析为 OutputFieldSpec
    - list[OutputFieldSpec]: 直接返回
    - None: 返回空列表
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []

    result: list[OutputFieldSpec] = []
    for item in raw:
        if isinstance(item, OutputFieldSpec):
            result.append(item)
        elif isinstance(item, str):
            # 旧格式：字符串 -> 推导为 string 类型
            name = item.strip()
            if name:
                result.append(OutputFieldSpec(name=name, type="string"))
        elif isinstance(item, dict):
            # 新格式：dict -> 解析为 OutputFieldSpec
            try:
                result.append(OutputFieldSpec(**item))
            except Exception:
                # 解析失败时尝试只取 name
                name = item.get("name", "")
                if isinstance(name, str) and name.strip():
                    result.append(OutputFieldSpec(name=name.strip(), type="string"))
    return result


def build_json_output_constraint(field_specs: list[OutputFieldSpec], locale: str | None = None) -> str:
    """根据字段规格生成 JSON 输出约束字符串

    示例输出：
    - {"type_code": string}
    - {"tags": string[]}
    - {"time_mode": "POINT"|"RANGE", "time_at": string|null}
    """
    from app.assistant.workflow.execution_copy import build_json_output_constraint as _build_constraint

    return _build_constraint(field_specs, locale=locale)


# ==================== Knowledge Base 配置 ====================


class SkillKBConfig(BaseModel):
    """Skill 级别的知识库配置（仅 agent_loop 模式支持）"""
    enabled: bool = False  # 是否启用知识库


# ==================== Skill 数据结构 ====================


class ConditionExpression(BaseModel):
    """IF/ELSE 条件表达式"""
    id: str
    variable: str  # e.g. "llm_1.sentiment"
    operator: ConditionOperator
    value: Optional[str] = None
    handle: str  # output handle name


class IfElseConditionClause(BaseModel):
    """Single condition clause in IF/ELIF branch."""
    id: str
    variable: str  # e.g. "llm_1.response" or "sys.date"
    operator: ConditionOperator
    value: Optional[str] = None


class IfElseBranch(BaseModel):
    """IF/ELIF branch definition."""
    id: str  # also used as source_handle
    label: str = ""
    logic: Literal["and", "or"] = "and"
    conditions: list[IfElseConditionClause] = Field(default_factory=list)


class WorkflowNodeDefinition(BaseModel):
    """工作流 DAG 节点定义"""
    node_id: str
    node_type: NodeType
    label: str = ""
    position_x: float = 0.0
    position_y: float = 0.0
    config: dict = Field(default_factory=dict)


class WorkflowEdgeDefinition(BaseModel):
    """工作流 DAG 边定义"""
    edge_id: str = ""
    source_node_id: str
    target_node_id: str
    source_handle: str = "output"
    target_handle: str = "input"
    condition_type: Optional[Literal["expression", "default"]] = None
    condition_expr: Optional[ConditionExpression] = None
    label: Optional[str] = None


class SkillStep(BaseModel):
    """Legacy step type kept for helper compatibility; no longer used in skill execution."""
    type: Literal["analysis", "tool", "summary"]
    instruction: Optional[str] = None
    tool_name: Optional[str] = None
    args_from: Optional[Literal["context", "previous", "custom", "json"]] = None
    args_template: Optional[str] = None
    output_mode: Optional[AnalysisOutputMode] = None
    output_fields: Optional[list[OutputFieldSpec] | list[str]] = None
    include_in_summary: Optional[bool] = True


class SkillDefinition(BaseModel):
    """Skill 定义"""
    name: str
    description: str
    intent_examples: list[str]
    tools: list[str] = Field(default_factory=list)  # 该 Skill 需要的工具列表
    mode: Literal["langgraph"] = "langgraph"  # 执行模式（仅 LangGraph）
    langgraph_pattern: Optional[Literal["agent_loop", "workflow_dag"]] = None
    # agent_loop 模式可选模型配置：default=系统默认，custom=指定模型
    model_source: Literal["default", "custom"] = "default"
    model_id: Optional[str] = None
    system_prompt: Optional[str] = None
    kb: Optional[SkillKBConfig] = None  # 知识库配置
    # workflow_dag 模式
    workflow_nodes: list[WorkflowNodeDefinition] = Field(default_factory=list)
    workflow_edges: list[WorkflowEdgeDefinition] = Field(default_factory=list)

    @property
    def hidden(self) -> bool:
        """是否隐藏（UI 不展示），按名称计算"""
        return self.name == DEFAULT_SKILL_NAME
