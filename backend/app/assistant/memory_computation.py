from __future__ import annotations

import json
from typing import Literal

from app.assistant.memory_service import AssistantMemoryService
from app.assistant.orchestration.openai_fallback_client import (
    OpenAiFallbackClient,
    OpenAiFallbackConfig,
)

MemoryComputeStatus = Literal["success", "skipped"]

_L1_PROMPT_USER_MAX_CHARS = 2000
_L1_PROMPT_ASSISTANT_MAX_CHARS = 4000
_L2_PROMPT_USER_MAX_CHARS = 2000
_L2_PROMPT_ASSISTANT_MAX_CHARS = 4000


class AssistantMemoryComputationService:
    def __init__(self, client: OpenAiFallbackClient | None = None) -> None:
        self._client = client or OpenAiFallbackClient()

    @staticmethod
    def truncate_prompt_text(text: str, *, max_chars: int) -> str:
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        return value[:max_chars]

    @staticmethod
    def parse_json_object_text(content: str) -> dict:
        raw = str(content or "").strip()
        if not raw:
            return {}

        if raw.startswith("```"):
            parts = raw.split("```")
            if len(parts) >= 3:
                raw = parts[1].strip()
            raw = raw.strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            if 0 <= start < end:
                try:
                    parsed = json.loads(raw[start : end + 1])
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
            return {}

    def build_l1_incremental_summary_messages(
        self,
        *,
        prev_summary: str,
        user_text: str,
        assistant_text: str,
        max_chars: int,
    ) -> list[dict[str, str]]:
        system_prompt = (
            "你是“会话记忆增量摘要器”。请把现有摘要与本轮对话增量融合为新的短期记忆摘要。"
            "要求：1) 保留稳定偏好、任务目标、关键约束、未完成事项与重要事实；"
            "2) 与旧摘要冲突时以本轮新信息为准；3) 严禁编造未出现的信息；"
            "4) 仅输出纯文本摘要，不要 JSON、不要标题、不要解释；"
            f"5) 最终摘要不超过 {max_chars} 个字符。"
        )
        prev_block = str(prev_summary or "").strip() or "(空)"
        user_block = self.truncate_prompt_text(user_text, max_chars=_L1_PROMPT_USER_MAX_CHARS)
        assistant_block = self.truncate_prompt_text(
            assistant_text,
            max_chars=_L1_PROMPT_ASSISTANT_MAX_CHARS,
        )
        user_prompt = (
            f"现有摘要：\n{prev_block}\n\n"
            f"本轮用户输入：\n{user_block}\n\n"
            f"本轮助手输出：\n{assistant_block}\n\n"
            "请输出融合后的新摘要："
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def build_l2_incremental_facts_messages(
        self,
        *,
        prev_facts: list[str],
        skill_name: str,
        user_text: str,
        assistant_text: str,
        max_items: int,
    ) -> list[dict[str, str]]:
        system_prompt = (
            "你是“会话+Skill 事实记忆提取器”。请基于已有 facts 和本轮对话，"
            "输出该 Skill 可复用的稳定事实。要求："
            "1) 仅保留用户偏好、任务约束、关键背景、未完成事项、重要实体与结论；"
            "2) 与旧 facts 冲突时以本轮信息优先；3) 严禁编造；"
            f"4) 必须严格输出 JSON 对象，格式为 {{\"facts\": [\"...\"]}}；5) 最多 {max_items} 条。"
        )
        prev_block = json.dumps(prev_facts, ensure_ascii=False)
        user_block = self.truncate_prompt_text(user_text, max_chars=_L2_PROMPT_USER_MAX_CHARS)
        assistant_block = self.truncate_prompt_text(
            assistant_text,
            max_chars=_L2_PROMPT_ASSISTANT_MAX_CHARS,
        )
        user_prompt = (
            f"skill_name: {skill_name}\n"
            f"existing_facts: {prev_block}\n"
            f"user_turn: {user_block}\n"
            f"assistant_turn: {assistant_block}\n"
            "请输出新的 JSON："
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def compute_next_l1_summary(
        self,
        *,
        cfg: OpenAiFallbackConfig | None,
        prev_summary: str,
        user_text: str,
        assistant_text: str,
        max_chars: int,
    ) -> tuple[str, MemoryComputeStatus]:
        current_summary = str(prev_summary or "").strip()
        if cfg is None:
            return current_summary, "skipped"

        messages = self.build_l1_incremental_summary_messages(
            prev_summary=current_summary,
            user_text=user_text,
            assistant_text=assistant_text,
            max_chars=max_chars,
        )
        raw = self._client.call_chat(cfg, messages)
        candidate = str(self._client.parse_chat_content(raw) or "").strip()
        if not candidate:
            return current_summary, "skipped"
        return AssistantMemoryService.truncate_summary(candidate, max_chars=max_chars), "success"

    def compute_next_l2_facts(
        self,
        *,
        cfg: OpenAiFallbackConfig | None,
        prev_facts: list[str],
        skill_name: str,
        user_text: str,
        assistant_text: str,
        max_items: int,
    ) -> tuple[list[str], MemoryComputeStatus]:
        current_facts = AssistantMemoryService.normalize_l2_facts(prev_facts, max_items=max_items)
        normalized_skill_name = str(skill_name or "").strip()
        if cfg is None or not normalized_skill_name:
            return current_facts, "skipped"

        messages = self.build_l2_incremental_facts_messages(
            prev_facts=current_facts,
            skill_name=normalized_skill_name,
            user_text=user_text,
            assistant_text=assistant_text,
            max_items=max_items,
        )
        raw = self._client.call_chat(cfg, messages)
        candidate = str(self._client.parse_chat_content(raw) or "").strip()
        parsed = self.parse_json_object_text(candidate)
        parsed_facts = parsed.get("facts")
        if not isinstance(parsed_facts, list):
            return current_facts, "skipped"

        next_facts = AssistantMemoryService.normalize_l2_facts(parsed_facts, max_items=max_items)
        if not next_facts:
            return current_facts, "skipped"
        return next_facts, "success"
