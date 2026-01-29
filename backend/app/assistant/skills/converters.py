"""Skill converters"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from app.assistant.skills.base import (
    OutputFieldSpec,
    SkillDefinition,
    SkillKBConfig,
    SkillStep,
)

if TYPE_CHECKING:
    from app.assistant_config.models import AssistantSkill


def _parse_skill_kb_config(raw: Any) -> SkillKBConfig | None:
    if not raw or not isinstance(raw, dict):
        return None
    return SkillKBConfig(enabled=bool(raw.get("enabled", False)))


def db_skill_to_definition_light(skill: AssistantSkill) -> SkillDefinition:
    """轻量级转换 - 用于路由阶段，不加载 steps（避免 N+1）。"""
    raw_intent_examples = skill.intent_examples or []
    if not isinstance(raw_intent_examples, list):
        raw_intent_examples = []
    intent_examples = [str(x) for x in raw_intent_examples]

    raw_tools = skill.tools or []
    if not isinstance(raw_tools, list):
        raw_tools = []
    tools = [str(x) for x in raw_tools]

    return SkillDefinition(
        name=skill.name,
        description=skill.description or "",
        intent_examples=intent_examples,
        tools=tools,
        mode=skill.mode or "steps",
        system_prompt=skill.system_prompt,
        steps=[],  # 路由阶段不需要 steps
        kb=_parse_skill_kb_config(getattr(skill, "kb_config", None)),
    )


def db_skill_to_definition(skill: AssistantSkill) -> SkillDefinition:
    """将数据库 AssistantSkill 模型转换为 SkillDefinition。"""
    raw_intent_examples = skill.intent_examples or []
    if not isinstance(raw_intent_examples, list):
        raw_intent_examples = []
    intent_examples = [str(x) for x in raw_intent_examples]

    raw_tools = skill.tools or []
    if not isinstance(raw_tools, list):
        raw_tools = []
    tools = [str(x) for x in raw_tools]

    steps: list[SkillStep] = []
    for s in (skill.steps or []):
        step_type = s.type if s.type in ("analysis", "tool", "summary") else "analysis"
        args_from = s.args_from if s.args_from in ("context", "previous", "custom", "json") else None
        output_mode = getattr(s, "output_mode", None)
        if output_mode not in ("text", "json"):
            output_mode = None

        output_fields_raw = getattr(s, "output_fields", None)
        output_fields: list[OutputFieldSpec] | list[str] | None = None
        if isinstance(output_fields_raw, list):
            # 检查是否为新格式（dict 列表）
            if output_fields_raw and isinstance(output_fields_raw[0], dict):
                # 新格式：解析为 OutputFieldSpec
                specs: list[OutputFieldSpec] = []
                for v in output_fields_raw:
                    if isinstance(v, dict):
                        try:
                            specs.append(OutputFieldSpec(**v))
                        except Exception:
                            name = v.get("name", "")
                            if isinstance(name, str) and name.strip():
                                specs.append(OutputFieldSpec(name=name.strip()))
                output_fields = specs or None
            else:
                # 旧格式：字符串列表
                cleaned: list[str] = []
                for v in output_fields_raw:
                    if isinstance(v, str) and v.strip():
                        cleaned.append(v.strip())
                output_fields = cleaned or None

        include_in_summary = getattr(s, "include_in_summary", None)
        if include_in_summary is None:
            include_in_summary = True
        steps.append(
            SkillStep(
                type=step_type,
                instruction=s.instruction,
                tool_name=s.tool_name,
                args_from=args_from,
                args_template=getattr(s, "args_template", None),
                output_mode=output_mode,
                output_fields=output_fields,
                include_in_summary=bool(include_in_summary),
            )
        )

    return SkillDefinition(
        name=skill.name,
        description=skill.description or "",
        intent_examples=intent_examples,
        tools=tools,
        mode=skill.mode or "steps",
        system_prompt=skill.system_prompt,
        steps=steps,
        kb=_parse_skill_kb_config(getattr(skill, "kb_config", None)),
    )
