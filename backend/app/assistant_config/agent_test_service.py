from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.ai_registry.runtime import (
    resolve_openai_compat_config,
    resolve_openai_compat_config_by_model_id,
)
from app.assistant.skill_catalog.base import SkillDefinition, SkillKBConfig
from app.assistant_config.models import AssistantTool
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.schemas import AgentTestRunRequest
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.system_settings.service import resolve_system_locale

logger = logging.getLogger(__name__)

DELTA_FLUSH_INTERVAL_MS = 120
DELTA_FLUSH_CHAR_THRESHOLD = 96


@dataclass(frozen=True)
class PreparedAgentTestRun:
    agent_profile_id: UUID
    display_name: str
    user_input: str
    history: list[dict[str, str]]
    stream_output: bool
    skill_definition: SkillDefinition


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_trace_context_payload(extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    agent_round = extra.get("agent_round")
    if isinstance(agent_round, int):
        payload["agentRound"] = agent_round

    tool_call_index = extra.get("tool_call_index")
    if isinstance(tool_call_index, int):
        payload["toolCallIndex"] = tool_call_index

    tool_kind = str(extra.get("tool_kind", "") or "").strip().lower()
    if tool_kind in {"tool", "knowledge"}:
        payload["toolKind"] = tool_kind

    return payload


class AgentTestRunService:
    """Agent test-run service for editor draft execution (no persistence)."""

    def __init__(self, db: Session):
        self.db = db
        self.config_service = AssistantConfigService(db)

    @staticmethod
    def _sse(event: str, data: dict[str, Any]) -> bytes:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _validate_tool_names(self, tool_names: list[str]) -> list[str]:
        normalized = {
            str(name).strip()
            for name in (tool_names or [])
            if isinstance(name, str) and str(name).strip()
        }
        if not normalized:
            return []

        system_names = {
            t.name
            for t in ToolRegistry.list_system_tools()
            if getattr(t, "name", None)
        }
        disabled_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(AssistantTool.enabled.is_(False)).all()
            if name
        }
        enabled_remote_names = {
            name
            for name, in self.db.query(AssistantTool.name).filter(
                AssistantTool.kind == "remote",
                AssistantTool.enabled.is_(True),
            ).all()
            if name
        }

        unavailable: list[str] = []
        for tool_name in sorted(normalized):
            if tool_name in disabled_names:
                unavailable.append(f"{tool_name} (disabled)")
                continue
            if tool_name in system_names or tool_name in enabled_remote_names:
                continue
            unavailable.append(f"{tool_name} (not found)")

        if unavailable:
            raise ApiException(
                status_code=422,
                code=42241,
                message=f"Agent references unavailable tools: {', '.join(unavailable)}",
            )
        return sorted(normalized)

    def prepare(self, agent_profile_id: UUID, request: AgentTestRunRequest) -> PreparedAgentTestRun:
        profile = self.config_service.get_agent_profile(agent_profile_id)
        draft = request.draft
        system_prompt = (draft.system_prompt or "").strip()
        if not system_prompt:
            raise ApiException(status_code=422, code=42242, message="system_prompt is required")

        tools = self._validate_tool_names(draft.tools or [])
        kb_cfg = draft.kb_config if isinstance(draft.kb_config, dict) else {}
        kb_enabled = bool(kb_cfg.get("enabled", False))
        model_source = str(getattr(draft, "model_source", "default") or "default").strip().lower()
        if model_source not in {"default", "custom"}:
            model_source = "default"
        model_id = str(draft.model_id) if getattr(draft, "model_id", None) else None
        if model_source == "default":
            model_id = None

        skill_definition = SkillDefinition(
            name=f"{profile.name}__agent_test",
            description=profile.description or "",
            intent_examples=[],
            tools=tools,
            mode="langgraph",
            langgraph_pattern="agent_loop",
            model_source=model_source,
            model_id=model_id,
            system_prompt=system_prompt,
            kb=SkillKBConfig(enabled=kb_enabled),
            workflow_nodes=[],
            workflow_edges=[],
        )
        return PreparedAgentTestRun(
            agent_profile_id=profile.id,
            display_name=profile.name,
            user_input=request.user_input,
            history=[dict(item) for item in request.history],
            stream_output=bool(request.stream_output),
            skill_definition=skill_definition,
        )

    def _build_engine(self, *, api_key: str, base_url: str, model: str):
        from app.assistant.workflow.engine.engine import LangGraphEngine

        return LangGraphEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            db=self.db,
        )

    def stream(self, prepared: PreparedAgentTestRun) -> Iterator[bytes]:
        """Execute prepared agent test run and return SSE event stream."""
        run_id = uuid4().hex
        started_at = _utc_iso_now()
        started_perf = time.perf_counter()

        event_queue: deque[bytes] = deque()
        final_parts: list[str] = []
        pending_content_parts: list[str] = []
        pending_content_chars = 0
        last_delta_flush_at = time.perf_counter()
        tool_started_perf: dict[str, float] = {}
        tool_started_at: dict[str, str] = {}

        def enqueue(event_name: str, **payload: Any) -> None:
            event_queue.append(self._sse(event_name, payload))

        def flush() -> Iterator[bytes]:
            while event_queue:
                yield event_queue.popleft()

        def _has_pending_delta() -> bool:
            return pending_content_chars > 0

        def _should_flush_delta() -> bool:
            if pending_content_chars >= DELTA_FLUSH_CHAR_THRESHOLD:
                return True
            elapsed_ms = (time.perf_counter() - last_delta_flush_at) * 1000
            return elapsed_ms >= DELTA_FLUSH_INTERVAL_MS

        def flush_pending_delta(*, force: bool = False) -> None:
            nonlocal pending_content_chars, last_delta_flush_at
            if not _has_pending_delta():
                return
            if not force and not _should_flush_delta():
                return
            enqueue(
                "content_delta",
                runId=run_id,
                delta="".join(pending_content_parts),
                ts=_utc_iso_now(),
            )
            pending_content_parts.clear()
            pending_content_chars = 0
            last_delta_flush_at = time.perf_counter()

        def _queue_key_event(event_name: str, **payload: Any) -> None:
            flush_pending_delta(force=True)
            enqueue(event_name, **payload)

        enqueue(
            "run_start",
            runId=run_id,
            agentProfileId=str(prepared.agent_profile_id),
            streamOutput=prepared.stream_output,
            modelSource=getattr(prepared.skill_definition, "model_source", "default"),
            modelId=getattr(prepared.skill_definition, "model_id", None),
            startedAt=started_at,
        )
        yield from flush()

        try:
            if getattr(prepared.skill_definition, "model_source", "default") == "custom":
                selected_model_id = getattr(prepared.skill_definition, "model_id", None)
                cfg = resolve_openai_compat_config_by_model_id(
                    self.db,
                    model_id=selected_model_id or "",
                    model_type="llm",
                )
            else:
                cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")
            if cfg is None:
                duration_ms = int((time.perf_counter() - started_perf) * 1000)
                if getattr(prepared.skill_definition, "model_source", "default") == "custom":
                    missing_msg = "Selected model is unavailable"
                else:
                    missing_msg = "No active AI provider configured"
                enqueue(
                    "run_error",
                    runId=run_id,
                    message=missing_msg,
                    stage="bootstrap",
                    ts=_utc_iso_now(),
                )
                enqueue(
                    "run_end",
                    runId=run_id,
                    status="error",
                    durationMs=duration_ms,
                    finalText="",
                    streamOutput=prepared.stream_output,
                )
                yield from flush()
                return

            engine = self._build_engine(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model,
            )
        except GeneratorExit:
            raise
        except Exception as exc:
            logger.exception("agent test run bootstrap failed agent=%s run_id=%s", prepared.display_name, run_id)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            enqueue(
                "run_error",
                runId=run_id,
                message=str(exc) or "Agent test run bootstrap failed",
                stage="bootstrap",
                ts=_utc_iso_now(),
            )
            enqueue(
                "run_end",
                runId=run_id,
                status="error",
                durationMs=duration_ms,
                finalText="",
                streamOutput=prepared.stream_output,
            )
            yield from flush()
            return

        try:
            def on_tool_call_start(tool_call_id: str, tool_name: str, args: Any, **extra: Any) -> None:
                started_at = _utc_iso_now()
                tool_started_at[tool_call_id] = started_at
                tool_started_perf[tool_call_id] = time.perf_counter()
                _queue_key_event(
                    "tool_call_start",
                    runId=run_id,
                    toolCallId=tool_call_id,
                    name=tool_name,
                    args=args,
                    startedAt=started_at,
                    ts=started_at,
                    **_build_trace_context_payload(extra),
                )

            def on_tool_call_end(tool_call_id: str, status: str, result: Any, **extra: Any) -> None:
                ended_at = _utc_iso_now()
                started_perf = tool_started_perf.pop(tool_call_id, None)
                started_at = tool_started_at.pop(tool_call_id, None)
                duration_ms = (
                    max(0, int((time.perf_counter() - started_perf) * 1000))
                    if isinstance(started_perf, (int, float))
                    else None
                )
                _queue_key_event(
                    "tool_call_end",
                    runId=run_id,
                    toolCallId=tool_call_id,
                    status=status,
                    result=result,
                    startedAt=started_at,
                    endedAt=ended_at,
                    durationMs=duration_ms,
                    ts=ended_at,
                    **_build_trace_context_payload(extra),
                )

            def on_analysis_start(analysis_id: str) -> None:
                _queue_key_event(
                    "analysis_start",
                    runId=run_id,
                    analysisId=analysis_id,
                    ts=_utc_iso_now(),
                )

            def on_analysis_delta(analysis_id: str, chunk: str) -> None:
                enqueue(
                    "analysis_delta",
                    runId=run_id,
                    analysisId=analysis_id,
                    delta=str(chunk or ""),
                    ts=_utc_iso_now(),
                )

            def on_analysis_end(analysis_id: str) -> None:
                _queue_key_event(
                    "analysis_end",
                    runId=run_id,
                    analysisId=analysis_id,
                    ts=_utc_iso_now(),
                )

            for delta in engine.execute(
                skill=prepared.skill_definition,
                user_input=prepared.user_input,
                history=prepared.history,
                runtime_context={
                    "stream_output": prepared.stream_output,
                    "conversation_id": f"agent_test:{run_id}",
                    "run_id": run_id,
                    "channel_type": "agent_test",
                    "locale": resolve_system_locale(self.db),
                },
                on_tool_call_start=on_tool_call_start,
                on_tool_call_end=on_tool_call_end,
                on_analysis_start=on_analysis_start,
                on_analysis_delta=on_analysis_delta,
                on_analysis_end=on_analysis_end,
            ):
                yield from flush()
                if not delta:
                    continue
                text = str(delta)
                final_parts.append(text)
                had_pending = _has_pending_delta()
                pending_content_parts.append(text)
                pending_content_chars += len(text)
                if not had_pending:
                    last_delta_flush_at = time.perf_counter()
                flush_pending_delta(force=False)
                yield from flush()

            flush_pending_delta(force=True)
            yield from flush()
            final_text = "".join(final_parts)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            enqueue(
                "run_end",
                runId=run_id,
                status="completed",
                durationMs=duration_ms,
                finalText=final_text,
                streamOutput=prepared.stream_output,
            )
            yield from flush()
        except GeneratorExit:
            raise
        except Exception as exc:
            logger.exception("agent test run failed agent=%s run_id=%s", prepared.display_name, run_id)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            final_text = "".join(final_parts)
            flush_pending_delta(force=True)
            enqueue(
                "run_error",
                runId=run_id,
                message=str(exc) or "Agent test run failed",
                stage="runtime",
                ts=_utc_iso_now(),
            )
            enqueue(
                "run_end",
                runId=run_id,
                status="error",
                durationMs=duration_ms,
                finalText=final_text,
                streamOutput=prepared.stream_output,
            )
            yield from flush()
