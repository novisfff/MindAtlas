"""Skill 基础数据结构"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# 默认 Skill 名称常量
DEFAULT_SKILL_NAME = "general_chat"

AnalysisOutputMode = Literal["text", "json"]


class SkillStep(BaseModel):
    """Skill 执行步骤"""
    type: Literal["analysis", "tool", "summary"]
    instruction: Optional[str] = None
    tool_name: Optional[str] = None
    args_from: Optional[Literal["context", "previous", "custom", "json"]] = None
    args_template: Optional[str] = None
    output_mode: Optional[AnalysisOutputMode] = None
    output_fields: Optional[list[str]] = None
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

    @property
    def hidden(self) -> bool:
        """是否隐藏（UI 不展示），按名称计算"""
        return self.name == DEFAULT_SKILL_NAME
