"""Skill Router - 意图判断层"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.workflow import execution_copy as _copy
from app.config import get_settings
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME, SkillDefinition
from app.assistant.skill_catalog.converters import db_skill_to_definition_light
from app.assistant_config.registry import SkillRegistry
from app.system_settings.service import resolve_system_locale

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    """路由决策结果（仅保留 skill 命中与默认回退语义）。"""

    skill: str
    reason: str
    selected_skill: str
    fallback_reason: str | None = None


ROUTER_PROMPT = """你是一个意图分类器，判断用户输入需要使用哪个 Skill。

## 当前日期
今天是 {current_date}

## 可用的 Skills

{skills_list}

## 连续对话上下文
- 最近对话历史将以多条 user/assistant 消息提供给你
- 最近一次已执行 Skill（可能为空）：`{last_skill_hint}`
- 当用户出现“继续/按刚才那个/就这个”等省略表达时，可结合历史与最近 Skill 推断
- 如果本轮出现明显新任务意图，优先匹配新 Skill，不要被旧上下文绑定

## 重要规则
- **每次只返回一个 Skill**，不要返回多个
- 只有当用户意图与某个 Skill 的描述/示例一致时，才返回该 Skill
- **闲聊、问候、知识问答、写作润色、翻译、泛化的“总结/介绍/分析”** 应返回 `{default_skill_name}`
- 如果不确定，返回空 skill（让系统走默认）

## 输出格式（严格 JSON）
返回一个 JSON 对象：
{{
  "skill": "skill_name",
  "reason": "一句话说明为什么"
}}

约束：
- `skill` 只能是一个技能名，或空字符串 `""`
- 不要返回数组
- 禁止输出 Markdown 代码块、禁止输出额外文本
"""


def _build_skills_list(skills: list[SkillDefinition], *, locale: str | None = None) -> str:
    """构建 Skill 列表描述"""
    normalized_locale = resolve_system_locale(preferred_locale=locale)
    lines = []
    for skill in skills:
        intent_examples = skill.intent_examples or []
        if not isinstance(intent_examples, list):
            intent_examples = []
        examples = ", ".join(f'"{e}"' for e in intent_examples[:3])
        lines.append(f"- **{skill.name}**: {skill.description}")
        if normalized_locale == "zh":
            lines.append(f"  示例: {examples}")
        else:
            lines.append(f"  Examples: {examples}")
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


def _truncate_text(content: str, max_chars: int) -> str:
    value = (content or "").strip()
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3] + "..."


class SkillRouter:
    """Skill 路由器"""

    def __init__(self, api_key: str, base_url: str, model: str, db: Session | None = None):
        # Optional dependency (tests may not install LangChain)
        from langchain_openai import ChatOpenAI  # type: ignore

        default_headers = build_openai_compat_client_headers()

        self.llm = ChatOpenAI(
            api_key=(api_key or "").strip(),
            base_url=(base_url or "").strip(),
            model=model,
            temperature=0,
            default_headers=default_headers,
        )
        self.db = db
        settings = get_settings()
        self.history_turns = max(1, int(getattr(settings, "assistant_router_history_turns", 3) or 3))
        self.history_max_chars_per_message = max(
            1,
            int(getattr(settings, "assistant_router_history_max_chars_per_message", 400) or 400),
        )
        self.history_max_messages = max(1, int(getattr(settings, "assistant_router_history_max_messages", 6) or 6))
        self.include_last_skill_hint = bool(getattr(settings, "assistant_router_include_last_skill_hint", True))

    @staticmethod
    def _extract_last_skill_name(skill_calls: Any) -> str:
        if isinstance(skill_calls, list):
            for item in reversed(skill_calls):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    return name
            return ""
        if isinstance(skill_calls, dict):
            return str(skill_calls.get("name") or "").strip()
        return ""

    def _resolve_last_skill_hint(self, runtime_context: dict | None = None) -> str:
        if not self.include_last_skill_hint or self.db is None:
            return ""
        ctx = runtime_context or {}
        raw_conversation_id = str(ctx.get("conversation_id") or "").strip()
        if not raw_conversation_id:
            return ""

        try:
            conversation_id = UUID(raw_conversation_id)
        except Exception:
            return ""

        from app.assistant.models import Message

        rows = (
            self.db.query(Message.skill_calls)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.skill_calls.isnot(None),
            )
            .order_by(Message.created_at.desc())
            .limit(8)
            .all()
        )
        for (skill_calls,) in rows:
            name = self._extract_last_skill_name(skill_calls)
            if name:
                return name
        return ""

    def _build_router_history_messages(self, history: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        if not history:
            return []

        candidate_messages: list[dict[str, str]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if role not in {"user", "assistant"}:
                continue
            content = _truncate_text(
                str(item.get("content") or ""),
                self.history_max_chars_per_message,
            )
            if not content:
                continue
            candidate_messages.append({"role": role, "content": content})

        if not candidate_messages:
            return []

        max_messages = max(1, min(self.history_max_messages, self.history_turns * 2))
        selected_reversed: list[dict[str, str]] = []
        seen_user_turns = 0
        for message in reversed(candidate_messages):
            if message["role"] == "user":
                if seen_user_turns >= self.history_turns:
                    break
                seen_user_turns += 1
            selected_reversed.append(message)
            if len(selected_reversed) >= max_messages:
                break

        return list(reversed(selected_reversed))

    def _list_skills(self, *, locale: str | None = None) -> list[SkillDefinition]:
        """获取所有可用 Skills（系统 + 数据库启用技能）。

        路由阶段使用轻量级转换，不加载 workflow 详情以避免 N+1 查询。
        """
        system_skills: list[SkillDefinition] = list(SkillRegistry.list_system_skills(locale=locale))
        if self.db is None:
            return system_skills

        registry = SkillRegistry(self.db)
        # 如果数据库中存在同名 Skill 且 enabled=False，则视为显式禁用（不回退到系统 Skill）。
        # 这样前端"禁用系统技能"能真正生效，行为与 ToolRegistry 的禁用逻辑保持一致。
        # 但默认技能(general_chat)必须始终可用，不受禁用影响
        
        disabled_names = {
            name
            for (name,) in (
                []  # assistant_skill table dropped
            )
        }
        if disabled_names:
            system_skills = [s for s in system_skills if s.name not in disabled_names]

        # 路由阶段不需要 workflow nodes/edges，使用轻量级转换
        db_skills = [db_skill_to_definition_light(s) for s in registry.list_enabled_db_skills(include_workflow=False)]

        merged: dict[str, SkillDefinition] = {s.name: s for s in system_skills}
        for s in db_skills:
            merged[s.name] = s
        return list(merged.values())

    def _build_route_decision(
        self,
        *,
        result: dict,
        valid_skill_names: set[str],
        default_available: bool,
    ) -> RouteDecision:
        raw_skill = result.get("skill")
        if isinstance(raw_skill, str):
            suggested_skill = raw_skill.strip()
        else:
            suggested_skill = ""

        # 兼容旧格式：{"skills": ["xxx"]}
        if not suggested_skill:
            skills = result.get("skills", [])
            if isinstance(skills, list):
                for item in skills:
                    if isinstance(item, str) and item.strip():
                        suggested_skill = item.strip()
                        break

        reason = str(result.get("reason", "") or "").strip()
        if not reason and isinstance(result.get("skills"), list):
            reason = "legacy router format"
        if not reason:
            reason = "router decision"

        fallback_reason: str | None = None
        selected_skill = suggested_skill

        if not selected_skill:
            fallback_reason = "missing_skill"
            selected_skill = DEFAULT_SKILL_NAME if default_available else ""
        elif selected_skill == DEFAULT_SKILL_NAME:
            if not default_available:
                fallback_reason = "default_skill_unavailable"
                selected_skill = ""
        elif selected_skill not in valid_skill_names:
            fallback_reason = "invalid_skill"
            selected_skill = DEFAULT_SKILL_NAME if default_available else ""
            if not selected_skill:
                fallback_reason = "default_skill_unavailable"

        return RouteDecision(
            skill=suggested_skill,
            reason=reason,
            selected_skill=selected_skill,
            fallback_reason=fallback_reason,
        )

    def _fallback_route_decision(self, reason: str, *, default_available: bool) -> RouteDecision:
        selected = DEFAULT_SKILL_NAME if default_available else ""
        return RouteDecision(
            skill="",
            reason=reason,
            selected_skill=selected,
            fallback_reason="router_error" if selected else "default_skill_unavailable",
        )

    def route(
        self,
        user_input: str,
        history: list[dict[str, Any]] | None = None,
        runtime_context: dict | None = None,
    ) -> RouteDecision:
        """判断用户意图并返回结构化路由决策。"""
        ctx = runtime_context or {}
        locale = resolve_system_locale(
            self.db,
            preferred_locale=str(ctx.get("locale", ctx.get("systemLocale", "")) or "").strip() or None,
        )
        all_skills = self._list_skills(locale=locale)
        # 排除 hidden skill 从候选列表，避免 LLM 总选默认 skill
        candidate_skills = [s for s in all_skills if not s.hidden]
        # 检查默认 skill 是否可用（未被禁用）
        default_available = any(s.name == DEFAULT_SKILL_NAME for s in all_skills)
        router_history = self._build_router_history_messages(history)
        last_skill_hint = self._resolve_last_skill_hint(runtime_context)

        prompt = _copy.build_router_prompt(
            locale=locale,
            skills_list=_build_skills_list(candidate_skills, locale=locale),
            current_date=date.today().isoformat(),
            default_skill_name=DEFAULT_SKILL_NAME,
            last_skill_hint=last_skill_hint or "",
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]
        messages.extend(router_history)
        messages.append({"role": "user", "content": user_input})

        ctx_message_id = str(ctx.get("message_id") or "")
        ctx_conversation_id = str(ctx.get("conversation_id") or "")
        valid_names = {s.name for s in candidate_skills}
        prompt_context_bytes = sum(len(str(item.get("content", "")).encode("utf-8")) for item in messages)

        try:
            response = self.llm.invoke(messages)
            content = str(response.content or "").strip()
            result = _parse_router_json(content)
            decision = self._build_route_decision(
                result=result,
                valid_skill_names=valid_names,
                default_available=default_available,
            )
            logger.info(
                "router decision conversation_id=%s message_id=%s suggested=%s selected=%s fallback_reason=%s "
                "history_messages_used=%s last_skill_hint=%s prompt_context_bytes=%s",
                ctx_conversation_id,
                ctx_message_id,
                decision.skill,
                decision.selected_skill,
                decision.fallback_reason,
                len(router_history),
                last_skill_hint,
                prompt_context_bytes,
            )
            return decision
        except Exception as e:
            logger.warning(
                "router failed conversation_id=%s message_id=%s history_messages_used=%s "
                "last_skill_hint=%s prompt_context_bytes=%s error=%s",
                ctx_conversation_id,
                ctx_message_id,
                len(router_history),
                last_skill_hint,
                prompt_context_bytes,
                e,
            )
            return self._fallback_route_decision("router invoke failed", default_available=default_available)
