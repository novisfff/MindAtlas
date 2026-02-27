from __future__ import annotations

import json
import logging
from collections import deque
from typing import Callable, Iterator
from uuid import UUID

from sqlalchemy.orm import Session, selectinload

from app.ai_registry.runtime import resolve_openai_compat_config
from app.assistant.models import Conversation, Message
from app.assistant.orchestration.chat_events import ChatEventAdapter
from app.assistant.orchestration.openai_fallback_client import (
    OpenAiFallbackClient,
    OpenAiFallbackConfig,
)
from app.assistant.workflow.human_approval_runtime import (
    list_pending_approvals_for_conversation,
    submit_human_approval_decision,
)
from app.common.exceptions import ApiException
from app.common.time import utcnow

logger = logging.getLogger(__name__)


_OpenAiConfig = OpenAiFallbackConfig


class AssistantService:
    def __init__(self, db: Session):
        self.db = db
        self._openai_fallback_client = OpenAiFallbackClient()

    def list_conversations(self, archived: bool | None = None) -> list[Conversation]:
        q = self.db.query(Conversation)
        if archived is not None:
            q = q.filter(Conversation.is_archived.is_(archived))
        return q.order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.updated_at.desc()
        ).all()

    def create_conversation(self, title: str | None = None) -> Conversation:
        conversation = Conversation(title=title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_conversation(self, conversation_id: UUID) -> Conversation:
        conversation = (
            self.db.query(Conversation)
            .options(selectinload(Conversation.messages))
            .filter(Conversation.id == conversation_id)
            .first()
        )
        if not conversation:
            raise ApiException(
                status_code=404, code=40400,
                message=f"Conversation not found: {conversation_id}"
            )
        return conversation

    def get_conversation_basic(self, conversation_id: UUID) -> Conversation:
        conversation = (
            self.db.query(Conversation)
            .filter(Conversation.id == conversation_id)
            .first()
        )
        if not conversation:
            raise ApiException(
                status_code=404, code=40400,
                message=f"Conversation not found: {conversation_id}"
            )
        return conversation

    def list_pending_approvals(self, conversation_id: UUID) -> list[dict]:
        self.get_conversation_basic(conversation_id)
        return list_pending_approvals_for_conversation(self.db, conversation_id)

    def submit_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: UUID,
        decision: str,
        values: dict | None,
        comment: str | None,
    ) -> dict:
        self.get_conversation_basic(conversation_id)
        try:
            return submit_human_approval_decision(
                self.db,
                approval_id=approval_id,
                decision=decision,
                values=values or {},
                comment=comment,
                expected_conversation_id=conversation_id,
            )
        except ValueError as exc:
            raise ApiException(
                status_code=400,
                code=42251,
                message=str(exc),
            ) from exc

    def delete_conversation(self, conversation_id: UUID) -> None:
        conversation = self.get_conversation_basic(conversation_id)
        self.db.delete(conversation)
        self.db.commit()

    def chat_stream(self, conversation_id: UUID, user_message: str, *, stream_output: bool = True) -> Iterator[bytes]:
        """SSE 流式聊天"""
        conversation = self.get_conversation_basic(conversation_id)
        user_msg = Message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )
        self.db.add(user_msg)
        conversation.last_message_at = utcnow()
        self.db.commit()
        self.db.refresh(user_msg)
        assistant_msg = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
        )
        self.db.add(assistant_msg)
        self.db.commit()
        self.db.refresh(assistant_msg)
        tool_events: deque[bytes] = deque()
        event_adapter = ChatEventAdapter(self._sse, tool_events)
        try:
            yield self._sse("message_start", {
                "conversationId": str(conversation.id),
                "messageId": str(assistant_msg.id)
            })
            content_parts: list[str] = []
            for delta in self._generate_response(
                conversation.id,
                message_id=assistant_msg.id,
                stream_output=stream_output,
                on_tool_call_start=event_adapter.on_tool_call_start,
                on_tool_call_end=event_adapter.on_tool_call_end,
                on_skill_start=event_adapter.on_skill_start,
                on_skill_end=event_adapter.on_skill_end,
                on_analysis_start=event_adapter.on_analysis_start,
                on_analysis_delta=event_adapter.on_analysis_delta,
                on_analysis_end=event_adapter.on_analysis_end,
                on_human_approval_requested=event_adapter.on_human_approval_requested,
                on_human_approval_resolved=event_adapter.on_human_approval_resolved,
            ):
                while tool_events:
                    yield tool_events.popleft()
                if delta:
                    content_parts.append(delta)
                    yield self._sse("content_delta", {"delta": delta})
            while tool_events:
                yield tool_events.popleft()
            assistant_msg.content = "".join(content_parts)
            if event_adapter.tool_calls_data:
                assistant_msg.tool_calls = event_adapter.tool_calls_data
            if event_adapter.tool_results_data:
                assistant_msg.tool_results = event_adapter.tool_results_data
            if event_adapter.skill_calls_data:
                assistant_msg.skill_calls = event_adapter.skill_calls_data
            if event_adapter.analysis_steps:
                assistant_msg.analysis = event_adapter.analysis_steps
            conversation.last_message_at = utcnow()
            self.db.commit()
            if not conversation.title:
                title = self._generate_title(user_message, assistant_msg.content)
                if title:
                    conversation.title = title
                    self.db.commit()
                    yield self._sse("title_updated", {"title": title})
            yield self._sse("message_end", {"finishReason": "stop"})
        except Exception as e:
            logger.error("Chat stream error: %s", e, exc_info=True)
            while tool_events:
                yield tool_events.popleft()
            yield self._sse("error", {"error": "Failed to generate response"})
            yield self._sse("message_end", {"finishReason": "error"})

    def _generate_response(
        self,
        conversation_id: UUID,
        message_id: UUID | None = None,
        stream_output: bool = True,
        on_tool_call_start: Callable[[str, str, dict], None] | None = None,
        on_tool_call_end: Callable[[str, str, str], None] | None = None,
        on_skill_start: Callable[[str, str, bool], None] | None = None,
        on_skill_end: Callable[[str, str], None] | None = None,
        on_analysis_start: Callable[[str], None] | None = None,
        on_analysis_delta: Callable[[str, str], None] | None = None,
        on_analysis_end: Callable[[str], None] | None = None,
        on_human_approval_requested: Callable[[dict], None] | None = None,
        on_human_approval_resolved: Callable[[dict], None] | None = None,
    ) -> Iterator[str]:
        """生成 AI 回复，优先使用 LangChain Agent"""
        logger.debug("assistant._generate_response start conversation_id=%s", conversation_id)
        cfg = self._get_openai_config()
        if not cfg:
            logger.debug("assistant: no active AI provider config, using fallback response")
            yield from self._fallback_response()
            return
        try:
            logger.debug("assistant: creating AssistantAgent")
            from app.assistant.orchestration.agent_runtime import AssistantAgent
            agent = AssistantAgent(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
                db=self.db  # 传递数据库会话
            )
            history = self._build_llm_messages(conversation_id)
            user_input = history[-1]["content"] if history else ""
            logger.debug("assistant: calling agent.stream (user_input_len=%d)", len(user_input))
            for delta in agent.stream(
                history[:-1],
                user_input,
                runtime_context={
                    "conversation_id": str(conversation_id),
                    "stream_output": bool(stream_output),
                    "run_id": str(message_id) if message_id else None,
                    "channel_type": "assistant_chat",
                    "message_id": str(message_id) if message_id else None,
                },
                on_tool_call_start=on_tool_call_start,
                on_tool_call_end=on_tool_call_end,
                on_skill_start=on_skill_start,
                on_skill_end=on_skill_end,
                on_analysis_start=on_analysis_start,
                on_analysis_delta=on_analysis_delta,
                on_analysis_end=on_analysis_end,
                on_human_approval_requested=on_human_approval_requested,
                on_human_approval_resolved=on_human_approval_resolved,
            ):
                yield delta
            logger.debug("assistant._generate_response end (agent completed)")
            return
        except Exception as e:
            error_msg = str(e)
            logger.error("LangChain Agent failed: %s", error_msg, exc_info=True)
            lowered = error_msg.lower()
            if any(k in lowered for k in ("blocked", "content_filter", "content filter", "policy", "safety")):
                yield "抱歉，您的请求被 AI 服务拒绝，请尝试换一种表达方式。"
                return
        logger.debug("assistant: falling back to OpenAI stream")
        messages = self._build_llm_messages(conversation_id)
        try:
            for delta in self._openai_stream(cfg, messages):
                yield delta
            logger.debug("assistant._generate_response end (openai fallback)")
            return
        except Exception as e:
            logger.warning("OpenAI stream failed: %s", e)
        logger.debug("assistant._generate_response end (error fallback)")
        yield from self._fallback_response(error=True)

    def _fallback_response(self, error: bool = False) -> Iterator[str]:
        """回退响应"""
        if error:
            msg = "抱歉，AI 服务暂时不可用，请稍后重试。"
        else:
            msg = "抱歉，当前没有配置 AI 服务。请在设置中配置 AI Provider。"
        yield from self._chunk_text(msg)

    def _chunk_text(self, text: str, chunk_size: int = 16) -> Iterator[str]:
        """将文本分块输出，模拟流式效果"""
        value = (text or "").strip()
        if not value:
            return
        for i in range(0, len(value), chunk_size):
            yield value[i:i + chunk_size]

    def _sse(self, event: str, data: dict) -> bytes:
        """构造 SSE 事件"""
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _generate_title(self, user_message: str, assistant_response: str) -> str | None:
        """根据对话内容生成标题"""
        cfg = self._get_openai_config()
        if not cfg:
            return None
        prompt = f"""根据以下对话内容，生成一个简短的对话标题（不超过20个字）。
只输出标题本身，不要加引号或其他标点。

用户: {user_message[:200]}
助手: {assistant_response[:200]}

标题:"""
        messages = [{"role": "user", "content": prompt}]
        try:
            raw = self._call_openai(cfg, messages)
            title = self._parse_openai_content(raw)
            title = (title or "").strip().strip('"\'')
            if title and len(title) <= 50:
                return title
        except Exception as e:
            logger.warning("Failed to generate title: %s", e)
        return None

    def _get_openai_config(self) -> _OpenAiConfig | None:
        try:
            cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")
        except Exception:
            return None
        if not cfg:
            return None
        return _OpenAiConfig(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model
        )

    def _build_api_url(self, base_url: str, endpoint: str) -> str:
        return self._openai_fallback_client.build_api_url(base_url, endpoint)

    def _build_llm_messages(self, conversation_id: UUID) -> list[dict]:
        """构建 LLM 消息历史"""
        history = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        out: list[dict] = [{
            "role": "system",
            "content": (
                "你是 MindAtlas 的 AI 助手，一个智能的个人秘书。"
                "你可以帮助用户管理知识和经历记录。"
                "请用简洁、友好的方式回答问题。"
            )
        }]
        for msg in history[-20:]:
            role = (msg.role or "").strip()
            if role not in {"user", "assistant", "system"}:
                continue
            if role == "assistant" and not (msg.content or "").strip():
                continue
            out.append({"role": role, "content": msg.content or ""})
        return out

    def _openai_stream(self, cfg: _OpenAiConfig, messages: list[dict]) -> Iterator[str]:
        """OpenAI 流式调用（兼容层，实际由 fallback client 执行）。"""
        yield from self._openai_fallback_client.stream_chat(cfg, messages)

    def _call_openai(self, cfg: _OpenAiConfig, messages: list[dict]) -> str | None:
        """OpenAI 非流式调用（兼容层，实际由 fallback client 执行）。"""
        return self._openai_fallback_client.call_chat(cfg, messages)

    def _parse_openai_content(self, raw: str | None) -> str:
        return self._openai_fallback_client.parse_chat_content(raw)
