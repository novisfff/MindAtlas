"""Skill converters"""
from __future__ import annotations

from app.assistant.skills.base import SkillDefinition, SkillStep
from app.assistant_config.models import AssistantSkill


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
        steps.append(
            SkillStep(
                type=step_type,
                instruction=s.instruction,
                tool_name=s.tool_name,
                args_from=args_from,
                args_template=getattr(s, "args_template", None),
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
    )
