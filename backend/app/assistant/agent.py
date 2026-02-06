"""LangChain Agent 配置 - 基于 Skill 机制"""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Iterator

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skills.base import DEFAULT_SKILL_NAME
from app.assistant.skills.executor import SkillExecutor
from app.assistant.skills.router import SkillRouter

logger = logging.getLogger(__name__)


class AssistantAgent:
    """AI 助手 Agent - 基于 Skill 机制"""

    def __init__(self, api_key: str, base_url: str, model: str, db: Session | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.db = db
        default_headers = build_openai_compat_client_headers()

        # LLM 用于直接回复（不需要 Skill 时）
        self.llm = ChatOpenAI(
            api_key=(api_key or "").strip(),
            base_url=(base_url or "").strip(),
            model=model,
            streaming=True,
            default_headers=default_headers,
        )

        # 初始化 Router 和 Executor
        self.router = SkillRouter(api_key, base_url, model, db)
        self.executor = SkillExecutor(api_key, base_url, model, db)

    def stream(
        self,
        history: list[dict],
        user_input: str,
        on_tool_call_start: Callable[[str, str, dict], None] | None = None,
        on_tool_call_end: Callable[[str, str, str], None] | None = None,
        on_skill_start: Callable[[str, str, bool], None] | None = None,
        on_skill_end: Callable[[str, str], None] | None = None,
        on_analysis_start: Callable[[str], None] | None = None,
        on_analysis_delta: Callable[[str, str], None] | None = None,
        on_analysis_end: Callable[[str], None] | None = None,
    ) -> Iterator[str]:
        """流式生成回复 - 基于 Skill 机制"""
        logger.debug("agent.stream start")
        # 1. 路由：判断用户意图，选择 Skills
        skills = self.router.route(user_input)
        logger.debug("agent routed to skill: %s", skills[0] if skills else "none")

        # 2. 无 Skill：直接用 LLM 回复
        if not skills:
            logger.debug("agent: no skills, using direct reply")
            yield from self._direct_reply(history, user_input)
            logger.debug("agent.stream end (direct reply)")
            return

        # 3. 执行单个 Skill
        skill_name = skills[0]
        logger.debug("agent executing skill: %s", skill_name)

        # 通知 Skill 开始
        skill_id = f"skill_{uuid.uuid4().hex[:8]}"
        hidden = (skill_name == DEFAULT_SKILL_NAME)
        if on_skill_start:
            on_skill_start(skill_id, skill_name, hidden)
            yield ""  # 触发事件发送

        try:
            logger.debug("agent skill %s executor.execute start", skill_name)
            yield from self.executor.execute(
                skill_name=skill_name,
                user_input=user_input,
                history=history,
                on_tool_call_start=on_tool_call_start,
                on_tool_call_end=on_tool_call_end,
                on_analysis_start=on_analysis_start,
                on_analysis_delta=on_analysis_delta,
                on_analysis_end=on_analysis_end,
            )
            logger.debug("agent skill %s executor.execute end", skill_name)
            # 通知 Skill 完成
            if on_skill_end:
                on_skill_end(skill_id, "completed")
                yield ""
        except Exception as e:
            logger.error("Skill %s failed: %s", skill_name, e, exc_info=True)
            if on_skill_end:
                on_skill_end(skill_id, "error")
                yield ""
            raise

        logger.debug("agent.stream end (skill completed)")

    def _direct_reply(self, history: list[dict], user_input: str) -> Iterator[str]:
        """直接用 LLM 回复（不需要 Skill 时）"""
        messages = [
            {"role": "system", "content": "你是 MindAtlas 的 AI 助手，友好地回复用户。"},
        ]
        for msg in history:
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": user_input})

        for chunk in self.llm.stream(messages):
            if chunk.content:
                yield chunk.content
