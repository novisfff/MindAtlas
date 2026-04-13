from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Iterator
from uuid import UUID

from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.ai_registry.runtime import resolve_openai_compat_config
from app.assistant.memory_computation import AssistantMemoryComputationService
from app.assistant.memory_service import AssistantMemoryService
from app.assistant.models import AssistantChatRun, Conversation, Message
from app.assistant.orchestration.chat_events import ChatEventAdapter
from app.assistant.orchestration.openai_fallback_client import (
    OpenAiFallbackClient,
    OpenAiFallbackConfig,
)
from app.assistant.run_control import AssistantRunCancelled, ensure_not_cancelled
from app.assistant.run_service import (
    RUN_ACTIVE_STATUSES,
    RUN_STATUS_CANCELLING,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_WAITING_APPROVAL,
    AssistantChatRunService,
)
from app.assistant.workflow.human_approval_runtime import (
    cancel_pending_human_approvals_for_run,
    list_pending_approvals_for_conversation,
    submit_human_approval_decision,
)
from app.assistant.workflow.engine.runtime_helpers import invoke_callback
from app.common.request_context import reset_request_locale, set_request_locale
from app.common.exceptions import ApiException
from app.common.time import utcnow
from app.config import get_settings
from app.database import SessionLocal
from app.system_settings.service import resolve_system_locale

logger = logging.getLogger(__name__)


_OpenAiConfig = OpenAiFallbackConfig
_CHECKPOINT_MIN_CHARS = 128
_CHECKPOINT_MAX_INTERVAL_SEC = 1.0
_RUN_EVENT_POLL_SEC = 0.2
_CANCEL_POLL_SEC = 0.2

class AssistantService:
    _attached_run_stream_ids: set[str] = set()
    _attached_run_stream_lock = threading.Lock()
    _background_run_threads: dict[str, threading.Thread] = {}
    _background_run_threads_lock = threading.Lock()

    def __init__(self, db: Session):
        self.db = db
        self._openai_fallback_client = OpenAiFallbackClient()
        self._memory_computation_service = AssistantMemoryComputationService(self._openai_fallback_client)

    @staticmethod
    def _normalize_locale(locale: str | None) -> str:
        return resolve_system_locale(preferred_locale=locale)

    @classmethod
    def _localized_text(cls, locale: str | None, *, zh: str, en: str) -> str:
        return zh if cls._normalize_locale(locale) == "zh" else en

    @classmethod
    def _mark_run_stream_attached(cls, run_id: str) -> None:
        key = str(run_id or "").strip()
        if not key:
            return
        with cls._attached_run_stream_lock:
            cls._attached_run_stream_ids.add(key)

    @classmethod
    def _mark_run_stream_detached(cls, run_id: str) -> None:
        key = str(run_id or "").strip()
        if not key:
            return
        with cls._attached_run_stream_lock:
            cls._attached_run_stream_ids.discard(key)

    @classmethod
    def _is_run_stream_attached(cls, run_id: str) -> bool:
        key = str(run_id or "").strip()
        if not key:
            return False
        with cls._attached_run_stream_lock:
            return key in cls._attached_run_stream_ids

    # Backward-compatible aliases used by existing tests/helpers.
    @classmethod
    def _mark_assistant_stream_active(cls, run_id: str) -> None:
        cls._mark_run_stream_attached(run_id)

    @classmethod
    def _mark_assistant_stream_inactive(cls, run_id: str) -> None:
        cls._mark_run_stream_detached(run_id)

    @classmethod
    def _is_assistant_stream_active(cls, run_id: str) -> bool:
        return cls._is_run_stream_attached(run_id)

    @classmethod
    def _register_background_thread(cls, run_id: str, thread: threading.Thread) -> None:
        with cls._background_run_threads_lock:
            cls._background_run_threads[run_id] = thread

    @classmethod
    def _clear_background_thread(cls, run_id: str) -> None:
        with cls._background_run_threads_lock:
            cls._background_run_threads.pop(run_id, None)

    @classmethod
    def _has_background_thread(cls, run_id: str) -> bool:
        with cls._background_run_threads_lock:
            thread = cls._background_run_threads.get(run_id)
        return bool(thread and thread.is_alive())

    @staticmethod
    def _serialize_run(run: AssistantChatRun) -> dict[str, Any]:
        return {
            "runId": str(run.id),
            "conversationId": str(run.conversation_id),
            "messageId": str(run.assistant_message_id) if run.assistant_message_id else None,
            "status": str(run.status or ""),
            "lastEventSeq": int(run.last_event_seq or 0),
            "checkpointSeq": int(run.checkpoint_seq or 0),
            "cancelRequestedAt": run.cancel_requested_at,
            "startedAt": run.started_at,
            "endedAt": run.ended_at,
        }

    def list_conversations(self, archived: bool | None = None) -> list[Conversation]:
        q = self.db.query(Conversation)
        if archived is not None:
            q = q.filter(Conversation.is_archived.is_(archived))
        return q.order_by(
            Conversation.last_message_at.desc().nullslast(),
            Conversation.updated_at.desc(),
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
                status_code=404,
                code=40400,
                message=f"Conversation not found: {conversation_id}",
            )
        return conversation

    def get_conversation_basic(self, conversation_id: UUID) -> Conversation:
        conversation = self.db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            raise ApiException(
                status_code=404,
                code=40400,
                message=f"Conversation not found: {conversation_id}",
            )
        return conversation

    def list_pending_approvals(self, conversation_id: UUID) -> list[dict]:
        self.get_conversation_basic(conversation_id)
        return list_pending_approvals_for_conversation(self.db, conversation_id)

    def get_active_run_payload(self, conversation_id: UUID) -> dict[str, Any] | None:
        self.get_conversation_basic(conversation_id)
        run = AssistantChatRunService(self.db).get_active_run(conversation_id=conversation_id)
        if run is None:
            return None
        return self._serialize_run(run)

    def stop_run(self, *, conversation_id: UUID, run_id: UUID) -> dict[str, Any]:
        self.get_conversation_basic(conversation_id)
        run_svc = AssistantChatRunService(self.db)
        existing = run_svc.get_run(conversation_id=conversation_id, run_id=run_id)
        if existing is None:
            raise ApiException(status_code=404, code=40400, message=f"Run not found: {run_id}")

        existing_status = str(existing.status or "")
        was_active = existing_status in RUN_ACTIVE_STATUSES
        run = run_svc.request_stop(conversation_id=conversation_id, run_id=run_id)
        if run is None:
            raise ApiException(status_code=404, code=40400, message=f"Run not found: {run_id}")

        if was_active and existing_status != RUN_STATUS_CANCELLING and str(run.status or "") == RUN_STATUS_CANCELLING:
            cancel_pending_human_approvals_for_run(self.db, run_id=str(run.id))
            try:
                run_svc.append_event(
                    run_id=run.id,
                    event_name="run_status",
                    payload={"status": RUN_STATUS_CANCELLING},
                )
            except Exception:
                logger.exception("append cancelling run_status event failed run_id=%s", run.id)
        return self._serialize_run(run)

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
            payload = submit_human_approval_decision(
                self.db,
                approval_id=approval_id,
                decision=decision,
                values=values or {},
                comment=comment,
                expected_conversation_id=conversation_id,
            )
            self._ensure_disconnected_approval_followup(
                conversation_id=conversation_id,
                approval_payload=payload,
            )
            return payload
        except ValueError as exc:
            raise ApiException(
                status_code=400,
                code=42251,
                message=str(exc),
            ) from exc

    def _ensure_disconnected_approval_followup(self, *, conversation_id: UUID, approval_payload: dict) -> None:
        # 背景 run 活跃时由执行线程继续，不生成兜底消息。
        run_id_raw = str(approval_payload.get("runId", "") or "").strip()
        run: AssistantChatRun | None = None
        if run_id_raw:
            try:
                run = self.db.get(AssistantChatRun, UUID(run_id_raw))
            except Exception:
                run = None
        if run is not None and str(run.status or "") in RUN_ACTIVE_STATUSES:
            return
        if run_id_raw and (self._is_run_stream_attached(run_id_raw) or self._has_background_thread(run_id_raw)):
            return

        status = str(approval_payload.get("status", "") or "").strip().lower()
        if status not in {"approved", "rejected"}:
            return

        followup = (
            "已收到你的确认。原对话连接已结束，本次流程不会继续自动执行。请发送“继续上次流程”后我会重新开始。"
            if status == "approved"
            else "已收到你的拒绝。原对话连接已结束，本次流程已停止。你可以随时重新发起。"
        )

        target_message_id = approval_payload.get("messageId")
        target_message: Message | None = None
        if target_message_id:
            try:
                target_message = self.db.get(Message, UUID(str(target_message_id)))
            except Exception:
                target_message = None
        target_message_is_empty = (
            target_message is not None
            and target_message.conversation_id == conversation_id
            and target_message.role == "assistant"
            and not str(target_message.content or "").strip()
        )

        if target_message_is_empty:
            target_message.content = followup
        else:
            self.db.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=followup,
                )
            )
        conversation = self.get_conversation_basic(conversation_id)
        conversation.last_message_at = utcnow()
        self.db.commit()

    def delete_conversation(self, conversation_id: UUID) -> None:
        conversation = self.get_conversation_basic(conversation_id)
        self.db.delete(conversation)
        self.db.commit()

    def chat_stream(self, conversation_id: UUID, user_message: str, *, stream_output: bool = True) -> Iterator[bytes]:
        """SSE 聊天入口：创建 run、后台执行、附着回放流。"""
        conversation = self.get_conversation_basic(conversation_id)
        locale = resolve_system_locale(self.db)
        run_svc = AssistantChatRunService(self.db)
        active = run_svc.get_active_run(conversation_id=conversation.id)
        if active is not None:
            raise ApiException(
                status_code=409,
                code=42260,
                message="Conversation already has an active run. Stop it before sending a new message.",
            )

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

        try:
            run = run_svc.create_run(
                conversation=conversation,
                user_message=user_msg,
                assistant_message=assistant_msg,
            )
        except ValueError as exc:
            raise ApiException(status_code=409, code=42260, message=str(exc)) from exc

        run_svc.append_event(
            run_id=run.id,
            event_name="run_status",
            payload={"status": "queued"},
        )
        self._start_background_run(run_id=run.id, stream_output=stream_output, locale=locale)
        yield from self.stream_run(conversation.id, run_id=run.id, after_seq=0)

    def stream_run(self, conversation_id: UUID, *, run_id: UUID, after_seq: int = 0) -> Iterator[bytes]:
        bind = self.db.bind or self.db.get_bind()
        read_session_factory = sessionmaker(bind=bind, future=True)
        with read_session_factory() as read_db:
            run = AssistantChatRunService(read_db).get_run(conversation_id=conversation_id, run_id=run_id)
            if run is None:
                raise ApiException(status_code=404, code=40400, message=f"Run not found: {run_id}")
        run_key = str(run_id)
        last_seq = max(0, int(after_seq or 0))
        terminal_poll_confirmed = False
        self._mark_run_stream_attached(run_key)
        try:
            while True:
                # Use a fresh read session per poll so updates committed by
                # background workers and other sessions are always visible.
                with read_session_factory() as read_db:
                    read_svc = AssistantChatRunService(read_db)
                    events = read_svc.list_events_after(run_id=run_id, after_seq=last_seq, limit=200)
                    for event in events:
                        payload = dict(event.payload or {})
                        payload["seq"] = int(event.seq)
                        last_seq = int(event.seq)
                        yield self._sse(event.event_name, payload)

                    current = read_svc.get_run(conversation_id=conversation_id, run_id=run_id)
                if current is None:
                    break
                status = str(current.status or "")
                if status in {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED}:
                    if last_seq >= int(current.last_event_seq or 0):
                        if terminal_poll_confirmed:
                            break
                        terminal_poll_confirmed = True
                    else:
                        terminal_poll_confirmed = False
                else:
                    terminal_poll_confirmed = False
                time.sleep(_RUN_EVENT_POLL_SEC)
        except GeneratorExit:
            logger.info("assistant run stream disconnected conversation_id=%s run_id=%s", conversation_id, run_id)
            raise
        finally:
            self._mark_run_stream_detached(run_key)

    def _start_background_run(self, *, run_id: UUID, stream_output: bool, locale: str) -> None:
        run_key = str(run_id)
        if self._has_background_thread(run_key):
            return

        def _runner() -> None:
            db = SessionLocal()
            locale_token = set_request_locale(locale)
            try:
                AssistantService(db)._run_chat_background(run_id=run_id, stream_output=stream_output, locale=locale)
            except Exception:
                logger.exception("assistant background run crashed run_id=%s", run_id)
            finally:
                reset_request_locale(locale_token)
                try:
                    db.close()
                except Exception:
                    pass
                self._clear_background_thread(run_key)

        thread = threading.Thread(
            target=_runner,
            name=f"assistant-run-{run_key[:8]}",
            daemon=True,
        )
        self._register_background_thread(run_key, thread)
        thread.start()

    def _run_chat_background(self, *, run_id: UUID, stream_output: bool, locale: str | None = None) -> None:
        resolved_locale = resolve_system_locale(self.db, preferred_locale=locale)
        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            return
        conversation = self.get_conversation_basic(run.conversation_id)
        user_msg = self.db.get(Message, run.user_message_id) if run.user_message_id else None
        assistant_msg = self.db.get(Message, run.assistant_message_id) if run.assistant_message_id else None
        if user_msg is None or assistant_msg is None:
            with SessionLocal() as status_db:
                AssistantChatRunService(status_db).update_run_status(
                    run_id=run_id,
                    status=RUN_STATUS_FAILED,
                    error_message="run messages not found",
                )
            return

        latest_event_seq = int(run.last_event_seq or 0)
        run_event_lock = threading.RLock()
        state_lock = threading.RLock()

        def _update_run_status(*, status: str, error_message: str | None = None) -> None:
            with run_event_lock:
                with SessionLocal() as status_db:
                    AssistantChatRunService(status_db).update_run_status(
                        run_id=run_id,
                        status=status,
                        error_message=error_message,
                    )

        def _update_checkpoint(*, checkpoint_seq: int) -> None:
            with run_event_lock:
                with SessionLocal() as checkpoint_db:
                    AssistantChatRunService(checkpoint_db).update_checkpoint(
                        run_id=run_id,
                        checkpoint_seq=checkpoint_seq,
                    )

        def _append_event(event_name: str, payload: dict[str, Any]) -> None:
            nonlocal latest_event_seq
            with run_event_lock:
                with SessionLocal() as event_db:
                    latest_event_seq = AssistantChatRunService(event_db).append_event(
                        run_id=run_id,
                        event_name=event_name,
                        payload=payload if isinstance(payload, dict) else {},
                    )

        last_cancel_check_at = 0.0
        cancelled = False

        def _cancel_checker() -> bool:
            nonlocal last_cancel_check_at, cancelled
            if cancelled:
                return True
            now = time.monotonic()
            if (now - last_cancel_check_at) < _CANCEL_POLL_SEC:
                return False
            last_cancel_check_at = now
            with run_event_lock:
                with SessionLocal() as cancel_db:
                    cancelled = AssistantChatRunService(cancel_db).is_cancel_requested(run_id=run_id)
            return cancelled

        content_parts: list[str] = []
        pending_chars = 0
        last_checkpoint_at = time.monotonic()
        checkpoint_force_requested = False

        event_adapter = ChatEventAdapter(_append_event)

        def _persist_checkpoint(*, force: bool = False) -> None:
            nonlocal pending_chars, last_checkpoint_at
            now = time.monotonic()
            if not force:
                if pending_chars < _CHECKPOINT_MIN_CHARS and (now - last_checkpoint_at) < _CHECKPOINT_MAX_INTERVAL_SEC:
                    return
            try:
                with state_lock:
                    assistant_msg.content = "".join(content_parts)
                    if event_adapter.tool_calls_data:
                        assistant_msg.tool_calls = list(event_adapter.tool_calls_data)
                    if event_adapter.tool_results_data:
                        assistant_msg.tool_results = list(event_adapter.tool_results_data)
                    if event_adapter.skill_calls_data:
                        assistant_msg.skill_calls = list(event_adapter.skill_calls_data)
                    if event_adapter.analysis_steps:
                        assistant_msg.analysis = list(event_adapter.analysis_steps)
                    conversation.last_message_at = utcnow()
                    self.db.commit()
                    latest_seq_snapshot = int(latest_event_seq)
                if latest_seq_snapshot > 0:
                    _update_checkpoint(checkpoint_seq=latest_seq_snapshot)
            except Exception:
                self.db.rollback()
                logger.exception("assistant checkpoint persist failed run_id=%s", run_id)
            pending_chars = 0
            last_checkpoint_at = now

        def _on_human_approval_requested(payload: dict) -> None:
            nonlocal checkpoint_force_requested
            _update_run_status(status=RUN_STATUS_WAITING_APPROVAL)
            _append_event("run_status", {"status": RUN_STATUS_WAITING_APPROVAL})
            with state_lock:
                event_adapter.on_human_approval_requested(payload)
                checkpoint_force_requested = True

        def _on_human_approval_resolved(payload: dict) -> None:
            if not _cancel_checker():
                _update_run_status(status=RUN_STATUS_RUNNING)
                _append_event("run_status", {"status": RUN_STATUS_RUNNING})
            with state_lock:
                event_adapter.on_human_approval_resolved(payload)

        def _wrap_event_callback(fn: Callable[..., None]) -> Callable[..., None]:
            def _wrapped(*args: Any, **kwargs: Any) -> None:
                with state_lock:
                    if args:
                        fn(*args, **kwargs)
                        return
                    invoke_callback(fn, **kwargs)

            return _wrapped

        try:
            ensure_not_cancelled(_cancel_checker)
            _append_event(
                "message_start",
                {
                    "conversationId": str(conversation.id),
                    "messageId": str(assistant_msg.id),
                    "runId": str(run_id),
                },
            )
            _update_run_status(status=RUN_STATUS_RUNNING)
            _append_event("run_status", {"status": RUN_STATUS_RUNNING})

            agent_db = SessionLocal()
            try:
                for delta in self._generate_response(
                    conversation.id,
                    message_id=assistant_msg.id,
                    run_id=run_id,
                    stream_output=stream_output,
                    locale=resolved_locale,
                    on_tool_call_start=_wrap_event_callback(event_adapter.on_tool_call_start),
                    on_tool_call_end=_wrap_event_callback(event_adapter.on_tool_call_end),
                    on_skill_start=_wrap_event_callback(event_adapter.on_skill_start),
                    on_skill_end=_wrap_event_callback(event_adapter.on_skill_end),
                    on_analysis_start=_wrap_event_callback(event_adapter.on_analysis_start),
                    on_analysis_delta=_wrap_event_callback(event_adapter.on_analysis_delta),
                    on_analysis_end=_wrap_event_callback(event_adapter.on_analysis_end),
                    on_node_start=_wrap_event_callback(event_adapter.on_node_start),
                    on_node_end=_wrap_event_callback(event_adapter.on_node_end),
                    on_human_approval_requested=_on_human_approval_requested,
                    on_human_approval_resolved=_on_human_approval_resolved,
                    cancel_checker=_cancel_checker,
                    db=agent_db,
                ):
                    ensure_not_cancelled(_cancel_checker)
                    chunk = str(delta or "")
                    if chunk:
                        with state_lock:
                            content_parts.append(chunk)
                            pending_chars += len(chunk)
                        _append_event("content_delta", {"delta": chunk})
                    force_checkpoint = False
                    with state_lock:
                        if checkpoint_force_requested:
                            checkpoint_force_requested = False
                            force_checkpoint = True
                    _persist_checkpoint(force=force_checkpoint)
            finally:
                try:
                    agent_db.close()
                except Exception:
                    pass

            _persist_checkpoint(force=True)
            if not conversation.title:
                title = self._generate_title(user_msg.content or "", assistant_msg.content or "", locale=locale)
                if title:
                    conversation.title = title
                    self.db.commit()
                    _append_event("title_updated", {"title": title})

            _update_run_status(status=RUN_STATUS_COMPLETED)
            _append_event("run_status", {"status": RUN_STATUS_COMPLETED})
            _append_event("message_end", {"finishReason": "stop"})
            _update_checkpoint(checkpoint_seq=latest_event_seq)
            self._update_l1_summary_after_run(
                conversation_id=conversation.id,
                run_id=run_id,
                user_text=str(user_msg.content or ""),
                assistant_text=str(assistant_msg.content or ""),
            )
            self._update_l2_memory_after_run(
                conversation_id=conversation.id,
                run_id=run_id,
                skill_name=self._resolve_selected_skill_for_l2(event_adapter.skill_calls_data),
                user_text=str(user_msg.content or ""),
                assistant_text=str(assistant_msg.content or ""),
            )
        except AssistantRunCancelled:
            logger.info("assistant background run cancelled run_id=%s", run_id)
            _persist_checkpoint(force=True)
            _append_event("workflow_steps", {"steps": [], "count": 0})
            _update_run_status(status=RUN_STATUS_CANCELLED)
            _append_event("run_status", {"status": RUN_STATUS_CANCELLED})
            _append_event("message_end", {"finishReason": "cancelled"})
            _update_checkpoint(checkpoint_seq=latest_event_seq)
        except Exception as exc:
            logger.error("assistant background run failed run_id=%s error=%s", run_id, exc, exc_info=True)
            _persist_checkpoint(force=True)
            _update_run_status(
                status=RUN_STATUS_FAILED,
                error_message=str(exc),
            )
            _append_event("error", {"error": "Failed to generate response"})
            _append_event("run_status", {"status": RUN_STATUS_FAILED})
            _append_event("message_end", {"finishReason": "error"})
            _update_checkpoint(checkpoint_seq=latest_event_seq)

    def _generate_response(
        self,
        conversation_id: UUID,
        message_id: UUID | None = None,
        run_id: UUID | None = None,
        stream_output: bool = True,
        locale: str | None = None,
        db: Session | None = None,
        on_tool_call_start: Callable[[str, str, dict], None] | None = None,
        on_tool_call_end: Callable[[str, str, str], None] | None = None,
        on_skill_start: Callable[[str, str, bool], None] | None = None,
        on_skill_end: Callable[[str, str], None] | None = None,
        on_analysis_start: Callable[[str], None] | None = None,
        on_analysis_delta: Callable[[str, str], None] | None = None,
        on_analysis_end: Callable[[str], None] | None = None,
        on_node_start: Callable[[str, str, str], None] | None = None,
        on_node_end: Callable[[str, str, str], None] | None = None,
        on_human_approval_requested: Callable[[dict], None] | None = None,
        on_human_approval_resolved: Callable[[dict], None] | None = None,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        logger.debug("assistant._generate_response start conversation_id=%s", conversation_id)
        db_session = db or self.db
        resolved_locale = resolve_system_locale(db_session, preferred_locale=locale)
        cfg = self._get_openai_config(db_session)
        if not cfg:
            logger.debug("assistant: no active AI provider config, using fallback response")
            yield from self._fallback_response(locale=resolved_locale)
            return
        try:
            from app.assistant.orchestration.agent_runtime import AssistantAgent

            agent = AssistantAgent(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
                db=db_session,
            )
            history = self._build_llm_messages(conversation_id, db=db_session, locale=resolved_locale)
            user_input = history[-1]["content"] if history else ""
            for delta in agent.stream(
                history[:-1],
                user_input,
                runtime_context={
                    "conversation_id": str(conversation_id),
                    "stream_output": bool(stream_output),
                    "run_id": str(run_id) if run_id else (str(message_id) if message_id else None),
                    "channel_type": "assistant_chat",
                    "message_id": str(message_id) if message_id else None,
                    "locale": resolved_locale,
                },
                on_tool_call_start=on_tool_call_start,
                on_tool_call_end=on_tool_call_end,
                on_skill_start=on_skill_start,
                on_skill_end=on_skill_end,
                on_analysis_start=on_analysis_start,
                on_analysis_delta=on_analysis_delta,
                on_analysis_end=on_analysis_end,
                on_node_start=on_node_start,
                on_node_end=on_node_end,
                on_human_approval_requested=on_human_approval_requested,
                on_human_approval_resolved=on_human_approval_resolved,
                cancel_checker=cancel_checker,
            ):
                ensure_not_cancelled(cancel_checker)
                yield delta
            return
        except AssistantRunCancelled:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error("LangChain Agent failed: %s", error_msg, exc_info=True)
            lowered = error_msg.lower()
            if any(k in lowered for k in ("blocked", "content_filter", "content filter", "policy", "safety")):
                yield self._localized_text(
                    resolved_locale,
                    zh="抱歉，您的请求被 AI 服务拒绝，请尝试换一种表达方式。",
                    en="Sorry, the AI service refused this request. Please try rephrasing it.",
                )
                return
            yield self._localized_text(
                resolved_locale,
                zh="抱歉，处理您的请求时出现错误，请稍后重试。",
                en="Sorry, something went wrong while processing your request. Please try again later.",
            )
            return

    def _fallback_response(self, error: bool = False, *, locale: str | None = None) -> Iterator[str]:
        if error:
            msg = self._localized_text(
                locale,
                zh="抱歉，AI 服务暂时不可用，请稍后重试。",
                en="Sorry, the AI service is temporarily unavailable. Please try again later.",
            )
        else:
            msg = self._localized_text(
                locale,
                zh="抱歉，当前没有配置 AI 服务。请在设置中配置 AI Provider。",
                en="Sorry, no AI service is configured right now. Please configure an AI provider in Settings.",
            )
        yield from self._chunk_text(msg)

    def _chunk_text(self, text: str, chunk_size: int = 16) -> Iterator[str]:
        value = (text or "").strip()
        if not value:
            return
        for i in range(0, len(value), chunk_size):
            yield value[i : i + chunk_size]

    def _sse(self, event: str, data: dict) -> bytes:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _generate_title(self, user_message: str, assistant_response: str, *, locale: str | None = None) -> str | None:
        cfg = self._get_openai_config()
        if not cfg:
            return None
        if self._normalize_locale(locale) == "zh":
            prompt = f"""根据以下对话内容，生成一个简短的对话标题（不超过20个字）。
只输出标题本身，不要加引号或其他标点。

用户: {user_message[:200]}
助手: {assistant_response[:200]}

标题:"""
        else:
            prompt = f"""Generate a short conversation title from the dialog below (no more than 20 words).
Return only the title text without quotes or extra punctuation.

User: {user_message[:200]}
Assistant: {assistant_response[:200]}

Title:"""
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

    @staticmethod
    def _truncate_l1_prompt_text(text: str, *, max_chars: int) -> str:
        return AssistantMemoryComputationService.truncate_prompt_text(text, max_chars=max_chars)

    def _build_l1_incremental_summary_messages(
        self,
        *,
        prev_summary: str,
        user_text: str,
        assistant_text: str,
        max_chars: int,
    ) -> list[dict[str, str]]:
        return self._memory_computation_service.build_l1_incremental_summary_messages(
            prev_summary=prev_summary,
            user_text=user_text,
            assistant_text=assistant_text,
            max_chars=max_chars,
        )

    def _update_l1_summary_after_run(
        self,
        *,
        conversation_id: UUID,
        run_id: UUID,
        user_text: str,
        assistant_text: str,
    ) -> None:
        started = time.monotonic()
        status = "skipped"
        prev_chars = 0
        next_chars = 0
        try:
            with SessionLocal() as memory_db:
                memory_service = AssistantMemoryService(memory_db)
                prev_summary = memory_service.get_l1_summary(conversation_id)
                prev_chars = len(prev_summary)
                settings = get_settings()
                max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))

                cfg = self._get_openai_config(memory_db)
                if not cfg:
                    next_chars = prev_chars
                    status = "skipped"
                    return

                next_summary, status = self._memory_computation_service.compute_next_l1_summary(
                    cfg=cfg,
                    prev_summary=prev_summary,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    max_chars=max_chars,
                )
                next_chars = len(next_summary)
                if next_summary != prev_summary:
                    memory_service.upsert_l1_summary(conversation_id, next_summary)
        except Exception:
            status = "failed"
            logger.exception("assistant l1 summary update failed conversation_id=%s run_id=%s", conversation_id, run_id)
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "assistant l1 summary update conversation_id=%s run_id=%s status=%s prev_chars=%s next_chars=%s elapsed_ms=%s",
                conversation_id,
                run_id,
                status,
                prev_chars,
                next_chars,
                elapsed_ms,
            )

    @staticmethod
    def _parse_json_object_text(content: str) -> dict[str, Any]:
        return AssistantMemoryComputationService.parse_json_object_text(content)

    @staticmethod
    def _resolve_selected_skill_for_l2(skill_calls_data: list[dict[str, Any]] | None) -> str:
        items = skill_calls_data if isinstance(skill_calls_data, list) else []
        for item in reversed(items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                return name
        return ""

    def _build_l2_incremental_facts_messages(
        self,
        *,
        prev_facts: list[str],
        skill_name: str,
        user_text: str,
        assistant_text: str,
        max_items: int,
    ) -> list[dict[str, str]]:
        return self._memory_computation_service.build_l2_incremental_facts_messages(
            prev_facts=prev_facts,
            skill_name=skill_name,
            user_text=user_text,
            assistant_text=assistant_text,
            max_items=max_items,
        )

    def _update_l2_memory_after_run(
        self,
        *,
        conversation_id: UUID,
        run_id: UUID,
        skill_name: str,
        user_text: str,
        assistant_text: str,
    ) -> None:
        started = time.monotonic()
        status = "skipped"
        normalized_skill_name = str(skill_name or "").strip()
        prev_count = 0
        next_count = 0
        try:
            if not normalized_skill_name:
                return
            with SessionLocal() as memory_db:
                memory_service = AssistantMemoryService(memory_db)
                settings = get_settings()
                max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))
                prev_facts = memory_service.get_l2_facts(conversation_id, normalized_skill_name)
                prev_facts = memory_service.normalize_l2_facts(prev_facts, max_items=max_items)
                prev_count = len(prev_facts)

                cfg = self._get_openai_config(memory_db)
                if not cfg:
                    next_count = prev_count
                    status = "skipped"
                    return

                next_facts, status = self._memory_computation_service.compute_next_l2_facts(
                    cfg=cfg,
                    prev_facts=prev_facts,
                    skill_name=normalized_skill_name,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    max_items=max_items,
                )
                next_count = len(next_facts)
                if next_facts != prev_facts:
                    memory_service.upsert_l2_facts(conversation_id, normalized_skill_name, next_facts)
        except Exception:
            status = "failed"
            logger.exception(
                "assistant l2 memory update failed conversation_id=%s run_id=%s skill_name=%s",
                conversation_id,
                run_id,
                normalized_skill_name,
            )
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "assistant l2 memory update conversation_id=%s run_id=%s skill_name=%s status=%s prev_count=%s "
                "next_count=%s elapsed_ms=%s",
                conversation_id,
                run_id,
                normalized_skill_name,
                status,
                prev_count,
                next_count,
                elapsed_ms,
            )

    def _get_openai_config(self, db: Session | None = None) -> _OpenAiConfig | None:
        db_session = db or self.db
        try:
            cfg = resolve_openai_compat_config(db_session, component="assistant", model_type="llm")
        except Exception:
            return None
        if not cfg:
            return None
        return _OpenAiConfig(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
        )

    def _build_api_url(self, base_url: str, endpoint: str) -> str:
        return self._openai_fallback_client.build_api_url(base_url, endpoint)

    def _build_llm_messages(self, conversation_id: UUID, db: Session | None = None, *, locale: str | None = None) -> list[dict]:
        db_session = db or self.db
        history = (
            db_session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        out: list[dict] = [
            {
                "role": "system",
                "content": self._localized_text(
                    locale,
                    zh=(
                        "你是 MindAtlas 的 AI 助手，一个智能的个人秘书。"
                        "你可以帮助用户管理知识和经历记录。"
                        "请用简洁、友好的方式回答问题。"
                    ),
                    en=(
                        "You are the MindAtlas AI assistant, an intelligent personal secretary. "
                        "You help users manage their knowledge and life records. "
                        "Respond in a concise and friendly way."
                    ),
                ),
            }
        ]
        for msg in history[-20:]:
            role = (msg.role or "").strip()
            if role not in {"user", "assistant", "system"}:
                continue
            if role == "assistant" and not (msg.content or "").strip():
                continue
            out.append({"role": role, "content": msg.content or ""})
        return out

    def _openai_stream(self, cfg: _OpenAiConfig, messages: list[dict]) -> Iterator[str]:
        yield from self._openai_fallback_client.stream_chat(cfg, messages)

    def _call_openai(self, cfg: _OpenAiConfig, messages: list[dict]) -> str | None:
        return self._openai_fallback_client.call_chat(cfg, messages)

    def _parse_openai_content(self, raw: str | None) -> str:
        return self._openai_fallback_client.parse_chat_content(raw)
