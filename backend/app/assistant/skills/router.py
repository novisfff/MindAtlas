"""Skill Router - 意图判断层"""
from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.assistant.skills.base import SkillDefinition, DEFAULT_SKILL_NAME, is_default_skill
from app.assistant.skills.converters import db_skill_to_definition_light
from app.assistant_config.registry import SkillRegistry

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """你是一个意图分类器，判断用户输入需要使用哪个 Skill。

## 当前日期
今天是 {current_date}

## 可用的 Skills

{skills_list}

## 重要规则
- **每次只返回一个 Skill**，不要返回多个
- 优先选择最匹配用户意图的单个 Skill
- 只有当用户意图与某个 Skill 的描述/示例**一致**时，才选择该 Skill
- **闲聊、问候、知识问答、写作润色、翻译、泛化的“总结/介绍/分析”** → 返回 `{{"skills": ["{default_skill_name}"]}}`
- 如果不确定，返回 `{{"skills": ["{default_skill_name}"]}}`

## 输出格式

返回 JSON 对象：
{{"skills": ["skill_name"]}}

不需要 Skill 时：
{{"skills": ["{default_skill_name}"]}}

"""


def _build_skills_list(skills: list[SkillDefinition]) -> str:
    """构建 Skill 列表描述"""
    lines = []
    for skill in skills:
        intent_examples = skill.intent_examples or []
        if not isinstance(intent_examples, list):
            intent_examples = []
        examples = ", ".join(f'"{e}"' for e in intent_examples[:3])
        lines.append(f"- **{skill.name}**: {skill.description}")
        lines.append(f"  示例: {examples}")
    return "\n".join(lines)


def _parse_router_json(content: str) -> dict:
    """尽量从 LLM 输出中解析出 JSON 对象。"""
    raw = (content or "").strip()
    if not raw:
        return {}

    # 常见：```json ... ```
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1].strip()
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        return json.loads(raw)
    except Exception:
        # 兜底：截取第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                return {}
        return {}


class SkillRouter:
    """Skill 路由器"""

    def __init__(self, api_key: str, base_url: str, model: str, db: Session | None = None):
        # Optional dependency (tests may not install LangChain)
        from langchain_openai import ChatOpenAI  # type: ignore

        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0,
        )
        self.db = db

    def _list_skills(self) -> list[SkillDefinition]:
        """获取所有可用 Skills（系统 + 数据库启用技能）。

        路由阶段使用轻量级转换，不加载 steps 避免 N+1 查询。
        """
        system_skills: list[SkillDefinition] = list(SkillRegistry.list_system_skills())
        if self.db is None:
            return system_skills

        registry = SkillRegistry(self.db)
        # 如果数据库中存在同名 Skill 且 enabled=False，则视为显式禁用（不回退到系统 Skill）。
        # 这样前端"禁用系统技能"能真正生效，行为与 ToolRegistry 的禁用逻辑保持一致。
        # 但默认技能(general_chat)必须始终可用，不受禁用影响
        from app.assistant_config.models import AssistantSkill
        disabled_names = {
            name for (name,) in (
                self.db.query(AssistantSkill.name)
                .filter(
                    AssistantSkill.enabled.is_(False),
                    AssistantSkill.name != DEFAULT_SKILL_NAME  # 排除默认技能
                )
                .all()
            )
        }
        if disabled_names:
            system_skills = [s for s in system_skills if s.name not in disabled_names]

        # 路由阶段不需要 steps，使用轻量级转换
        db_skills = [db_skill_to_definition_light(s) for s in registry.list_enabled_db_skills(include_steps=False)]

        merged: dict[str, SkillDefinition] = {s.name: s for s in system_skills}
        for s in db_skills:
            merged[s.name] = s
        return list(merged.values())

    def route(self, user_input: str) -> list[str]:
        """判断用户意图，返回 Skill 名称列表（可为空）"""
        all_skills = self._list_skills()
        # 排除 hidden skill 从候选列表，避免 LLM 总选默认 skill
        candidate_skills = [s for s in all_skills if not s.hidden]
        # 检查默认 skill 是否可用（未被禁用）
        default_available = any(s.name == DEFAULT_SKILL_NAME for s in all_skills)

        prompt = ROUTER_PROMPT.format(
            skills_list=_build_skills_list(candidate_skills),
            current_date=date.today().isoformat(),
            default_skill_name=DEFAULT_SKILL_NAME,
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ]

        try:
            response = self.llm.invoke(messages)
            content = response.content.strip()

            # 解析 JSON
            result = _parse_router_json(content)
            skills = result.get("skills", [])

            # 验证 Skill 存在，只取第一个有效的（仅在候选列表中）
            valid_names = {s.name for s in candidate_skills}
            if isinstance(skills, list):
                for s in skills:
                    if s == DEFAULT_SKILL_NAME and default_available:
                        logger.info("Routed to default skill: %s", s)
                        return [DEFAULT_SKILL_NAME]
                    if s in valid_names:
                        logger.info("Routed to skill: %s", s)
                        return [s]  # 只返回一个 Skill

            # 无匹配时 fallback 到默认 skill
            logger.debug("No valid skill found")
            if default_available:
                logger.info("Falling back to default skill: %s", DEFAULT_SKILL_NAME)
                return [DEFAULT_SKILL_NAME]
            return []

        except Exception as e:
            logger.warning("Router failed: %s", e)
            # 异常时也 fallback 到默认 skill
            if default_available:
                logger.info("Falling back to default skill: %s", DEFAULT_SKILL_NAME)
                return [DEFAULT_SKILL_NAME]
            return []
