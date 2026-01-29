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


def build_json_output_constraint(field_specs: list[OutputFieldSpec]) -> str:
    """根据字段规格生成 JSON 输出约束字符串

    示例输出：
    - {"type_code": string}
    - {"tags": string[]}
    - {"time_mode": "POINT"|"RANGE", "time_at": string|null}
    """
    if not field_specs:
        return ""

    def format_type(spec: OutputFieldSpec) -> str:
        """格式化单个字段的类型表示"""
        base_type = spec.type

        # 处理枚举
        if spec.enum:
            type_str = "|".join(f'"{v}"' for v in spec.enum)
        elif base_type == "array":
            # 数组类型
            items = spec.items_type or "string"
            type_str = f"{items}[]"
        else:
            type_str = base_type

        # 处理 nullable
        if spec.nullable:
            type_str = f"{type_str}|null"

        return type_str

    fields_str = ", ".join(
        f'"{spec.name}": {format_type(spec)}'
        for spec in field_specs
    )

    return (
        f"输出要求：只输出一个 JSON 对象：{{{fields_str}}}；"
        "禁止输出额外描述、Markdown、代码块围栏。"
    )


# ==================== Knowledge Base 配置 ====================


class SkillKBConfig(BaseModel):
    """Skill 级别的知识库配置（仅 Agent 模式支持）"""
    enabled: bool = False  # 是否启用知识库


# ==================== Skill 数据结构 ====================


class SkillStep(BaseModel):
    """Skill 执行步骤"""
    type: Literal["analysis", "tool", "summary"]
    instruction: Optional[str] = None
    tool_name: Optional[str] = None
    args_from: Optional[Literal["context", "previous", "custom", "json"]] = None
    args_template: Optional[str] = None
    output_mode: Optional[AnalysisOutputMode] = None
    output_fields: Optional[list[OutputFieldSpec] | list[str]] = None  # 支持新旧两种格式
    include_in_summary: Optional[bool] = True


class SkillDefinition(BaseModel):
    """Skill 定义"""
    name: str
    description: str
    intent_examples: list[str]
    tools: list[str] = Field(default_factory=list)  # 该 Skill 需要的工具列表
    mode: Literal["steps", "agent"] = "steps"  # 执行模式
    system_prompt: Optional[str] = None  # Agent 模式的系统提示词
    steps: list[SkillStep] = Field(default_factory=list)  # Steps 模式的执行步骤
    kb: Optional[SkillKBConfig] = None  # 知识库配置

    @property
    def hidden(self) -> bool:
        """是否隐藏（UI 不展示），按名称计算"""
        return self.name == DEFAULT_SKILL_NAME
