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
        """Legacy blocking approvals removed (Plan 10 B2). Always empty."""
        self.get_conversation_basic(conversation_id)
        return []

    def get_active_run_payload(self, conversation_id: UUID) -> dict[str, Any] | None:
        self.get_conversation_basic(conversation_id)
        run = AssistantChatRunService(self.db).get_active_run(conversation_id=conversation_id)
        if run is None:
            return None
        return self._serialize_run(run)

    def stop_run(self, *, conversation_id: UUID, run_id: UUID) -> dict[str, Any]:
        self.get_conversation_basic(conversation_id)
        run_svc = AssistantChatRunService(self.db)
        try:
            existing = run_svc.get_run(conversation_id=conversation_id, run_id=run_id)
        except ValueError as exc:
            # Evaluation-namespace IDs must surface as production not-found.
            from app.assistant.evaluation.contracts import (
                reraise_evaluation_id_as_not_found,
            )

            reraise_evaluation_id_as_not_found(
                exc,
                not_found=ApiException(
                    status_code=404, code=40400, message=f"Run not found: {run_id}"
                ),
            )
        if existing is None:
            raise ApiException(status_code=404, code=40400, message=f"Run not found: {run_id}")

        # Plan 06 Task 7: Main Agent stop is database-driven CAS via DurableRunRepository.
        # Legacy keeps the in-process request_stop + optional run_status event path.
        if str(existing.runtime_kind or "") == "main_agent":
            return self._stop_main_agent_run(run=existing)

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

    def _stop_main_agent_run(self, *, run: AssistantChatRun) -> dict[str, Any]:
        """Durable stop: revision CAS, no lease required; never consults stream attachment."""
        from app.assistant.durable.repository import (
            CODE_RUN_FINALIZING,
            CODE_STALE_REVISION,
            CODE_TERMINAL_IMMUTABLE,
            DurableRunConflict,
            DurableRunRepository,
            EventSpec,
            STATUS_CANCELLING,
            STATUS_CANCELLED,
            TERMINAL_STATUSES,
        )

        repo = DurableRunRepository(self.db)

        def _cancel_pending_durable_interrupt() -> None:
            from app.assistant.workflow.durable.interrupts import (
                DurableInterruptRepository,
            )

            try:
                interrupt_repo = DurableInterruptRepository(self.db)
                pending_interrupt = interrupt_repo.get_pending_for_run(
                    run.id,
                    for_update=False,
                )
                if pending_interrupt is not None:
                    interrupt_repo.cancel_interrupt(
                        run_id=run.id,
                        interrupt_id=pending_interrupt.id,
                        comment="run stopped",
                    )
                    self.db.commit()
            except Exception as exc:
                self.db.rollback()
                logger.exception(
                    "cancel pending durable interrupt after stop failed run_id=%s",
                    run.id,
                )
                raise ApiException(
                    status_code=500,
                    code=42272,
                    message="run stopped but durable interrupt cleanup failed; retry stop",
                    details={"reasonCode": "durable_interrupt_cleanup_failed", "runId": str(run.id)},
                ) from exc

        # Retry a few times on concurrent revision bumps (worker heartbeat is
        # non-semantic and does not bump; result/recovery/stop do).
        last_conflict: DurableRunConflict | None = None
        for _ in range(5):
            self.db.refresh(run)
            status = str(run.status or "")
            if status in TERMINAL_STATUSES:
                # Retry cleanup even after the Run transition committed; a prior
                # response may have been lost between stop CAS and interrupt close.
                _cancel_pending_durable_interrupt()
                self.db.refresh(run)
                return self._serialize_run(run)

            expected_revision = int(run.state_revision or 0)
            events: list[EventSpec] = []
            # Emit public run_status only when we expect a real transition into
            # cancelling or direct cancelled. Idempotent cancelling reuses nothing.
            if status != STATUS_CANCELLING:
                target_hint = (
                    STATUS_CANCELLED
                    if status in {"queued", "waiting_approval", "waiting_input", "needs_reconciliation"}
                    else STATUS_CANCELLING
                )
                events.append(
                    EventSpec(
                        event_key=f"run.stop:{run.id}:{expected_revision}:{target_hint}",
                        event_name="run_status",
                        payload={"status": target_hint, "runId": str(run.id)},
                        visibility="public",
                    )
                )
            try:
                result = repo.request_stop(
                    run_id=run.id,
                    expected_revision=expected_revision,
                    events=events,
                )
            except DurableRunConflict as exc:
                last_conflict = exc
                if exc.code == CODE_RUN_FINALIZING:
                    raise ApiException(
                        status_code=409,
                        code=42270,
                        message="run_finalizing: accepted content is committing; stop cannot cancel",
                        details={"reasonCode": CODE_RUN_FINALIZING, "runId": str(run.id)},
                    ) from exc
                if exc.code == CODE_TERMINAL_IMMUTABLE:
                    self.db.refresh(run)
                    return self._serialize_run(run)
                if exc.code == CODE_STALE_REVISION:
                    continue
                raise ApiException(
                    status_code=409,
                    code=42271,
                    message=f"stop conflict: {exc.code}",
                    details={"reasonCode": exc.code, "runId": str(run.id)},
                ) from exc

            new_status = str(result.status or "")
            if new_status in {STATUS_CANCELLING, STATUS_CANCELLED}:
                _cancel_pending_durable_interrupt()
                try:
                    cancel_pending_human_approvals_for_run(self.db, run_id=str(run.id))
                except Exception:
                    logger.exception(
                        "cancel pending approvals after durable stop failed run_id=%s",
                        run.id,
                    )
            return self._serialize_run(result.run)

        code = last_conflict.code if last_conflict is not None else CODE_STALE_REVISION
        raise ApiException(
            status_code=409,
            code=42271,
            message=f"stop conflict after retries: {code}",
            details={"reasonCode": code, "runId": str(run.id)},
        )

    def submit_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: UUID,
        decision: str,
        values: dict | None,
        comment: str | None,
    ) -> dict:
        """Legacy blocking approval decisions removed (Plan 10 B2)."""
        _ = (approval_id, decision, values, comment)
        self.get_conversation_basic(conversation_id)
        raise ApiException(
            status_code=410,
            code=41011,
            message=(
                "Legacy blocking HumanLoop / assistant_human_approval is removed. "
                "Use durable Main Agent interrupts."
            ),
            details={"legacyHitlRemoved": True, "replacement": "durable_interrupt"},
        )

    def _ensure_disconnected_approval_followup(self, *, conversation_id: UUID, approval_payload: dict) -> None:
        """No-op: legacy approval follow-up path removed (Plan 10 B2)."""
        _ = (conversation_id, approval_payload)
        return

    def delete_conversation(self, conversation_id: UUID) -> None:
        conversation = self.get_conversation_basic(conversation_id)
        # Plan 06: enqueue object-backed Artifact GC before cascade delete.
        # Outbox rows have no Run FK and survive conversation/Run deletion.
        from app.assistant.durable.artifacts import enqueue_conversation_artifact_gc

        enqueue_conversation_artifact_gc(self.db, conversation_id)
        # PostgreSQL immutability triggers require the purge flag for durable child DELETE.
        # SQLite unit tests ignore unknown local settings; production uses PostgreSQL.
        try:
            from sqlalchemy import text

            self.db.execute(text("SET LOCAL mindatlas.allow_durable_run_purge = 'on'"))
        except Exception:
            pass
        self.db.delete(conversation)
        self.db.commit()

    def chat_stream(self, conversation_id: UUID, user_message: str, *, stream_output: bool = True) -> Iterator[bytes]:
        """SSE chat entry: atomic Main-Agent admission, then attach to the Run stream.

        Plan 2 Task 8: Message + Run + initial event are created only after locked
        readiness/closure/worker gates succeed. No runtime selector and no Legacy
        daemon spawn. ``stream_output`` is retained for call-site compatibility.
        """
        _ = stream_output
        # Ensure the conversation exists before admission (404, not 503).
        conversation = self.get_conversation_basic(conversation_id)
        from app.assistant.runtime.admission import (
            ADMISSION_HTTP_REASON,
            AssistantAdmissionError,
            AssistantChatAdmissionService,
            ConcurrentChatAdmission,
        )

        try:
            run = AssistantChatAdmissionService(self.db).admit_and_create(
                conversation_id=conversation.id,
                user_message=user_message,
            )
        except AssistantAdmissionError as exc:
            raise ApiException(
                status_code=503,
                code=50310,
                message="Assistant is not ready to accept a new Run.",
                details={
                    "admissionReason": ADMISSION_HTTP_REASON.get(
                        exc.reason_code, exc.reason_code
                    )
                },
            ) from exc
        except ConcurrentChatAdmission as exc:
            raise ApiException(
                status_code=409,
                code=42260,
                message="Conversation already has an active Run.",
            ) from exc
        yield from self.stream_run(
            conversation.id, run_id=run.id, after_seq=0
        )

    def stream_run(self, conversation_id: UUID, *, run_id: UUID, after_seq: int = 0) -> Iterator[bytes]:
        """Replay committed public events after ``afterSeq``.

        Plan 06 §9: transport is at-least-once; consumers dedupe by Run/seq/event
        identity. Internal rows (column visibility or payload marker) advance the
        server cursor but are never yielded. Disconnect closes only this reader —
        it never cancels the Run.
        """
        from app.assistant.main_agent.events import is_internal_event, strip_visibility_marker

        bind = self.db.bind or self.db.get_bind()
        read_session_factory = sessionmaker(bind=bind, future=True, expire_on_commit=False)
        with read_session_factory() as read_db:
            try:
                run = AssistantChatRunService(read_db).get_run(
                    conversation_id=conversation_id, run_id=run_id
                )
            except ValueError as exc:
                from app.assistant.evaluation.contracts import (
                    reraise_evaluation_id_as_not_found,
                )

                reraise_evaluation_id_as_not_found(
                    exc,
                    not_found=ApiException(
                        status_code=404, code=40400, message=f"Run not found: {run_id}"
                    ),
                )
            if run is None:
                raise ApiException(status_code=404, code=40400, message=f"Run not found: {run_id}")
            runtime_kind = str(getattr(run, "runtime_kind", None) or "legacy")
        run_key = str(run_id)
        last_seq = max(0, int(after_seq or 0))
        terminal_poll_confirmed = False
        # Attachment bookkeeping is Legacy-only; Main Agent never consults it.
        track_attachment = runtime_kind != "main_agent"
        if track_attachment:
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
                        # Advance cursor for internal rows but do not yield them.
                        last_seq = int(event.seq)
                        visibility = str(getattr(event, "visibility", None) or "public").strip().lower()
                        if visibility == "internal" or is_internal_event(payload):
                            continue
                        public_payload = strip_visibility_marker(payload)
                        public_payload["seq"] = int(event.seq)
                        event_key = getattr(event, "event_key", None)
                        if event_key:
                            public_payload["eventKey"] = str(event_key)
                        public_payload.setdefault("runId", str(run_id))
                        yield self._sse(event.event_name, public_payload)

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
            # Disconnect closes only this reader Session — Run continues.
            logger.info("assistant run stream disconnected conversation_id=%s run_id=%s", conversation_id, run_id)
            raise
        finally:
            if track_attachment:
                self._mark_run_stream_detached(run_key)

    def _start_background_run(self, *, run_id: UUID, stream_output: bool, locale: str) -> None:
        """Legacy chat daemon entry — removed (Plan 10)."""
        _ = (stream_output, locale)
        raise RuntimeError(
            f"Legacy chat daemon is removed; cannot start background run {run_id}. "
            "Use Main Agent durable worker only."
        )

    def _run_chat_background(self, *, run_id: UUID, stream_output: bool, locale: str | None = None) -> None:
        """Legacy Supervisor background runner — removed (Plan 10 B2)."""
        _ = (stream_output, locale)
        raise RuntimeError(
            f"Legacy chat background runner is removed; cannot continue run {run_id}. "
            "Use Main Agent durable worker only."
        )

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
        """Legacy IntentRouter/Supervisor generation path — removed (Plan 10 B2)."""
        _ = (
            conversation_id,
            message_id,
            run_id,
            stream_output,
            locale,
            db,
            on_tool_call_start,
            on_tool_call_end,
            on_skill_start,
            on_skill_end,
            on_analysis_start,
            on_analysis_delta,
            on_analysis_end,
            on_node_start,
            on_node_end,
            on_human_approval_requested,
            on_human_approval_resolved,
            cancel_checker,
        )
        raise RuntimeError(
            "Legacy AssistantAgent/Supervisor runtime is removed; "
            "use Main Agent durable worker only"
        )
        yield from ()  # pragma: no cover — make this a generator

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
