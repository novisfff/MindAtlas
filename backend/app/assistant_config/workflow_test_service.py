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

from app.ai_registry.runtime import resolve_openai_compat_config
from app.assistant.memory_computation import AssistantMemoryComputationService
from app.assistant.memory_service import AssistantMemoryService
from app.assistant.orchestration.openai_fallback_client import OpenAiFallbackConfig
from app.assistant.skill_catalog.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.assistant.workflow.validation.validator import (
    validate_parallel_branches,
    validate_workflow,
)
from app.assistant_config.models import AssistantWorkflow
from app.assistant_config.schemas import WorkflowInput, WorkflowTestRunRequest
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.config import get_settings

logger = logging.getLogger(__name__)

DELTA_FLUSH_INTERVAL_MS = 120
DELTA_FLUSH_CHAR_THRESHOLD = 96


@dataclass(frozen=True)
class PreparedWorkflowTestRun:
    skill_id: UUID | None
    workflow_id: UUID
    display_name: str
    workflow: WorkflowInput
    user_input: str | None
    structured_input: dict[str, Any] | None
    session_id: str
    history: list[dict[str, str]]
    session_memory: "PreparedWorkflowSessionMemory"
    start_input_mode: str
    stream_output: bool
    skill_definition: SkillDefinition


@dataclass(frozen=True)
class PreparedWorkflowSessionMemory:
    conversation_summary: str
    skill_facts: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "conversationSummary": self.conversation_summary,
            "skillFacts": list(self.skill_facts),
        }


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_parse(text: str) -> dict[str, Any] | list[Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _build_trace_context_payload(extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    node_id = str(extra.get("node_id", "") or "").strip()
    if node_id:
        payload["nodeId"] = node_id

    node_type = str(extra.get("node_type", "") or "").strip()
    if node_type:
        payload["nodeType"] = node_type

    node_execution_id = str(extra.get("node_execution_id", "") or "").strip()
    if node_execution_id:
        payload["nodeExecutionId"] = node_execution_id

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


class WorkflowTestRunService:
    """Workflow test-run service for editor draft execution (no persistence)."""

    def __init__(self, db: Session):
        self.db = db
        self.config_service = AssistantConfigService(db)
        self._memory_computation_service = AssistantMemoryComputationService()

    @staticmethod
    def _sse(event: str, data: dict[str, Any]) -> bytes:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _prepare_internal(
        self,
        *,
        scope_skill_id: UUID | None,
        scope_workflow_id: UUID,
        display_name: str,
        description: str,
        kb_enabled: bool,
        request: WorkflowTestRunRequest,
    ) -> PreparedWorkflowTestRun:
        workflow = request.workflow
        start_input_mode = "text"
        start_structured_fields: list[dict[str, Any]] = []
        for node in workflow.nodes:
            if node.node_type != "start":
                continue
            cfg = node.config if isinstance(node.config, dict) else {}
            raw_mode = str(cfg.get("input_mode", cfg.get("inputMode", "text")) or "text").strip().lower()
            start_input_mode = "structured" if raw_mode == "structured" else "text"
            raw_fields = cfg.get("structured_fields", cfg.get("structuredFields"))
            if isinstance(raw_fields, list):
                start_structured_fields = [item for item in raw_fields if isinstance(item, dict)]
            break

        normalized_session_id = str(request.session_id or uuid4()).strip()
        normalized_history = [
            {
                "role": str(getattr(item, "role", item.get("role")) if isinstance(item, dict) else item.role).strip(),
                "content": str(
                    getattr(item, "content", item.get("content")) if isinstance(item, dict) else item.content
                ).strip(),
            }
            for item in request.history
        ]
        normalized_session_memory = self._normalize_session_memory(request.session_memory)

        if start_input_mode == "structured":
            provided_input = request.structured_input
            if not isinstance(provided_input, dict):
                raise ApiException(
                    status_code=422,
                    code=42210,
                    message="structured_input is required when start inputMode=structured",
                )
            field_map: dict[str, dict[str, Any]] = {}
            for field in start_structured_fields:
                field_name = str(field.get("name", "") or "").strip()
                if not field_name:
                    continue
                field_map[field_name] = field

            unknown_fields = sorted(set(str(key) for key in provided_input.keys()) - set(field_map.keys()))
            if unknown_fields:
                raise ApiException(
                    status_code=422,
                    code=42211,
                    message=f"structured_input contains unknown fields: {', '.join(unknown_fields)}",
                )

            for field_name, field in field_map.items():
                required = bool(field.get("required", False))
                if required and field_name not in provided_input:
                    raise ApiException(
                        status_code=422,
                        code=42212,
                        message=f"missing required structured input field: {field_name}",
                    )
                if field_name not in provided_input:
                    continue
                value = provided_input[field_name]
                field_type = str(field.get("type", "string") or "string").strip().lower()
                if field_type == "string" and not isinstance(value, str):
                    raise ApiException(status_code=422, code=42213, message=f"field '{field_name}' must be string")
                if field_type == "number" and (
                    not isinstance(value, (int, float)) or isinstance(value, bool)
                ):
                    raise ApiException(status_code=422, code=42213, message=f"field '{field_name}' must be number")
                if field_type == "integer" and (
                    not isinstance(value, int) or isinstance(value, bool)
                ):
                    raise ApiException(status_code=422, code=42213, message=f"field '{field_name}' must be integer")
                if field_type == "boolean" and not isinstance(value, bool):
                    raise ApiException(status_code=422, code=42213, message=f"field '{field_name}' must be boolean")

        nodes_raw = [
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "label": (node.label or node.node_id),
                "config": node.config,
            }
            for node in workflow.nodes
        ]
        edges_raw = [
            {
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "source_handle": edge.source_handle,
            }
            for edge in workflow.edges
        ]

        topology = validate_workflow(nodes_raw, edges_raw)
        if not topology.valid:
            msg = "; ".join(error.message for error in topology.errors[:5])
            raise ApiException(status_code=422, code=42201, message=f"Invalid workflow topology: {msg}")

        parallel = validate_parallel_branches(nodes_raw, edges_raw)
        if not parallel.valid:
            msg = "; ".join(error.message for error in parallel.errors[:5])
            raise ApiException(status_code=422, code=42202, message=f"Invalid parallel branches: {msg}")

        workflow_tool_names = self.config_service.validate_workflow_dependencies(workflow)

        workflow_nodes = [
            WorkflowNodeDefinition(
                node_id=node.node_id,
                node_type=node.node_type,
                label=node.label,
                position_x=node.position_x,
                position_y=node.position_y,
                config=node.config or {},
            )
            for node in workflow.nodes
        ]

        workflow_edges: list[WorkflowEdgeDefinition] = []
        for edge in workflow.edges:
            condition_expr = None
            if edge.condition_expr is not None:
                condition_expr = ConditionExpression(
                    id=edge.condition_expr.id,
                    variable=edge.condition_expr.variable,
                    operator=edge.condition_expr.operator,
                    value=edge.condition_expr.value,
                    handle=edge.condition_expr.handle,
                )
            workflow_edges.append(
                WorkflowEdgeDefinition(
                    edge_id=edge.edge_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    source_handle=edge.source_handle,
                    target_handle=edge.target_handle,
                    condition_type=edge.condition_type,
                    condition_expr=condition_expr,
                    label=edge.label,
                )
            )

        skill_definition = SkillDefinition(
            name=f"{display_name}__workflow_test",
            description=description or "",
            intent_examples=[],
            tools=sorted(workflow_tool_names),
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            system_prompt=None,
            kb=SkillKBConfig(enabled=kb_enabled),
            workflow_nodes=workflow_nodes,
            workflow_edges=workflow_edges,
        )

        return PreparedWorkflowTestRun(
            skill_id=scope_skill_id,
            workflow_id=scope_workflow_id,
            display_name=display_name,
            workflow=workflow,
            user_input=request.user_input if start_input_mode == "text" else None,
            structured_input=request.structured_input if start_input_mode == "structured" else None,
            session_id=normalized_session_id,
            history=normalized_history,
            session_memory=normalized_session_memory,
            start_input_mode=start_input_mode,
            stream_output=bool(request.stream_output),
            skill_definition=skill_definition,
        )

    def _normalize_session_memory(
        self,
        session_memory: Any,
    ) -> PreparedWorkflowSessionMemory:
        raw = session_memory
        if raw is None:
            return PreparedWorkflowSessionMemory(conversation_summary="", skill_facts=[])

        settings = get_settings()
        max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))
        max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))
        raw_summary = raw.get("conversation_summary", raw.get("conversationSummary", "")) if isinstance(raw, dict) else (
            getattr(raw, "conversation_summary", "")
        )
        raw_facts = raw.get("skill_facts", raw.get("skillFacts", [])) if isinstance(raw, dict) else (
            getattr(raw, "skill_facts", [])
        )
        conversation_summary = AssistantMemoryService.truncate_summary(
            str(raw_summary or "").strip(),
            max_chars=max_chars,
        )
        skill_facts = AssistantMemoryService.normalize_l2_facts(
            list(raw_facts or []),
            max_items=max_items,
        )
        return PreparedWorkflowSessionMemory(
            conversation_summary=conversation_summary,
            skill_facts=skill_facts,
        )

    def prepare(self, skill_id: UUID, request: WorkflowTestRunRequest) -> PreparedWorkflowTestRun:
        """Compatibility route: prepare test-run by skill id."""
        skill = self.config_service.get_skill(skill_id)
        if skill.workflow_id is None:
            raise ApiException(
                status_code=409,
                code=42231,
                message=f"Skill '{skill.name}' is bound to an agent, not a workflow",
            )
        kb_cfg_raw = getattr(skill, "kb_config", None)
        kb_enabled = bool(kb_cfg_raw.get("enabled", False)) if isinstance(kb_cfg_raw, dict) else False
        return self._prepare_internal(
            scope_skill_id=skill.id,
            scope_workflow_id=skill.workflow_id,
            display_name=skill.name,
            description=skill.description or "",
            kb_enabled=kb_enabled,
            request=request,
        )

    def prepare_for_workflow(self, workflow_id: UUID, request: WorkflowTestRunRequest) -> PreparedWorkflowTestRun:
        workflow: AssistantWorkflow = self.config_service.get_workflow(workflow_id)
        return self._prepare_internal(
            scope_skill_id=None,
            scope_workflow_id=workflow.id,
            display_name=workflow.name,
            description=workflow.description or "",
            kb_enabled=False,
            request=request,
        )

    def _build_engine(self, *, api_key: str, base_url: str, model: str):
        # Lazy import keeps workflow test service importable in lightweight unit-test envs.
        from app.assistant.workflow.engine.engine import LangGraphEngine

        return LangGraphEngine(
            api_key=api_key,
            base_url=base_url,
            model=model,
            db=self.db,
        )

    @staticmethod
    def _build_runtime_conversation_id(session_id: str) -> str:
        value = str(session_id or "").strip() or uuid4().hex
        return f"workflow_test_session:{value}"

    def _compute_next_session_memory(
        self,
        *,
        cfg: OpenAiFallbackConfig | None,
        prepared: PreparedWorkflowTestRun,
        assistant_text: str,
        run_id: str,
    ) -> PreparedWorkflowSessionMemory:
        previous = prepared.session_memory
        if prepared.start_input_mode != "text":
            return previous

        settings = get_settings()
        max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))
        max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))

        next_summary = previous.conversation_summary
        next_facts = list(previous.skill_facts)

        try:
            next_summary, _ = self._memory_computation_service.compute_next_l1_summary(
                cfg=cfg,
                prev_summary=previous.conversation_summary,
                user_text=prepared.user_input or "",
                assistant_text=assistant_text,
                max_chars=max_chars,
            )
        except Exception:
            logger.exception(
                "workflow test run l1 compute failed scope=%s run_id=%s",
                prepared.display_name,
                run_id,
            )

        try:
            next_facts, _ = self._memory_computation_service.compute_next_l2_facts(
                cfg=cfg,
                prev_facts=previous.skill_facts,
                skill_name=prepared.display_name,
                user_text=prepared.user_input or "",
                assistant_text=assistant_text,
                max_items=max_items,
            )
        except Exception:
            logger.exception(
                "workflow test run l2 compute failed scope=%s run_id=%s",
                prepared.display_name,
                run_id,
            )

        return PreparedWorkflowSessionMemory(
            conversation_summary=next_summary,
            skill_facts=next_facts,
        )

    def stream(self, prepared: PreparedWorkflowTestRun) -> Iterator[bytes]:
        """Execute prepared workflow test run and return SSE event stream."""
        run_id = uuid4().hex
        started_at = _utc_iso_now()
        started_perf = time.perf_counter()
        runtime_conversation_id = self._build_runtime_conversation_id(prepared.session_id)

        event_queue: deque[bytes] = deque()
        final_parts: list[str] = []
        pending_content_delta_parts: list[str] = []
        pending_content_delta_chars = 0
        pending_node_delta_parts: dict[tuple[str, str], list[str]] = {}
        pending_node_delta_chars: dict[tuple[str, str], int] = {}
        last_delta_flush_at = time.perf_counter()
        tool_started_perf: dict[str, float] = {}
        tool_started_at: dict[str, str] = {}

        def enqueue(event_name: str, **payload: Any) -> None:
            event_queue.append(self._sse(event_name, payload))

        def flush() -> Iterator[bytes]:
            while event_queue:
                yield event_queue.popleft()

        def _has_pending_delta() -> bool:
            if pending_content_delta_chars > 0:
                return True
            return any(char_count > 0 for char_count in pending_node_delta_chars.values())

        def _should_flush_delta() -> bool:
            if pending_content_delta_chars >= DELTA_FLUSH_CHAR_THRESHOLD:
                return True
            if any(char_count >= DELTA_FLUSH_CHAR_THRESHOLD for char_count in pending_node_delta_chars.values()):
                return True
            elapsed_ms = (time.perf_counter() - last_delta_flush_at) * 1000
            return elapsed_ms >= DELTA_FLUSH_INTERVAL_MS

        def flush_pending_delta(*, force: bool = False) -> None:
            nonlocal pending_content_delta_chars, last_delta_flush_at
            if not _has_pending_delta():
                return
            if not force and not _should_flush_delta():
                return

            ts = _utc_iso_now()
            for (node_id, node_execution_id), parts in list(pending_node_delta_parts.items()):
                if not parts:
                    continue
                enqueue(
                    "node_output_delta",
                    runId=run_id,
                    nodeId=node_id,
                    delta="".join(parts),
                    ts=ts,
                    **({"nodeExecutionId": node_execution_id} if node_execution_id else {}),
                )
            if pending_content_delta_chars > 0 and pending_content_delta_parts:
                enqueue(
                    "content_delta",
                    runId=run_id,
                    delta="".join(pending_content_delta_parts),
                    ts=ts,
                )

            pending_content_delta_parts.clear()
            pending_content_delta_chars = 0
            pending_node_delta_parts.clear()
            pending_node_delta_chars.clear()
            last_delta_flush_at = time.perf_counter()

        def _queue_key_event(event_name: str, **payload: Any) -> None:
            flush_pending_delta(force=True)
            enqueue(event_name, **payload)

        enqueue(
            "run_start",
            runId=run_id,
            skillId=str(prepared.skill_id or prepared.workflow_id),
            streamOutput=prepared.stream_output,
            startedAt=started_at,
        )
        yield from flush()

        try:
            cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")
            if cfg is None:
                duration_ms = int((time.perf_counter() - started_perf) * 1000)
                enqueue(
                    "run_error",
                    runId=run_id,
                    message="No active AI provider configured",
                    stage="bootstrap",
                    ts=_utc_iso_now(),
                )
                enqueue(
                    "run_end",
                    runId=run_id,
                    status="error",
                    durationMs=duration_ms,
                    finalText="",
                    finalJson=None,
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
            logger.exception(
                "workflow test run bootstrap failed scope=%s run_id=%s",
                prepared.display_name,
                run_id,
            )
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            enqueue(
                "run_error",
                runId=run_id,
                message=str(exc) or "Workflow test run bootstrap failed",
                stage="bootstrap",
                ts=_utc_iso_now(),
            )
            enqueue(
                "run_end",
                runId=run_id,
                status="error",
                durationMs=duration_ms,
                finalText="",
                finalJson=None,
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

            def on_node_start(node_id: str, node_type: str, **extra: Any) -> None:
                _queue_key_event(
                    "node_start",
                    runId=run_id,
                    nodeId=node_id,
                    nodeType=node_type,
                    ts=_utc_iso_now(),
                    **_build_trace_context_payload(extra),
                )

            def on_node_output_delta(node_id: str, delta: str, **extra: Any) -> None:
                nonlocal last_delta_flush_at
                delta_text = str(delta or "")
                if not delta_text:
                    return
                had_pending = _has_pending_delta()
                node_execution_id = str(extra.get("node_execution_id", "") or "").strip()
                node_key = (node_id, node_execution_id)
                node_parts = pending_node_delta_parts.setdefault(node_key, [])
                node_parts.append(delta_text)
                pending_node_delta_chars[node_key] = pending_node_delta_chars.get(node_key, 0) + len(delta_text)
                if not had_pending:
                    last_delta_flush_at = time.perf_counter()
                flush_pending_delta(force=False)

            def on_node_end(node_id: str, status: str, **extra: Any) -> None:
                _queue_key_event(
                    "node_end",
                    runId=run_id,
                    nodeId=node_id,
                    status=status,
                    ts=_utc_iso_now(),
                    **_build_trace_context_payload(extra),
                )

            def on_branch_decision(node_id: str, handle: str, **extra: Any) -> None:
                _queue_key_event(
                    "branch_decision",
                    runId=run_id,
                    nodeId=node_id,
                    handle=handle,
                    ts=_utc_iso_now(),
                    **_build_trace_context_payload(extra),
                )

            def on_node_snapshot(
                node_id: str,
                node_type: str,
                status: str,
                input_data: Any,
                output_data: Any,
                error_message: str | None,
                hard_truncated: bool,
                **extra: Any,
            ) -> None:
                _queue_key_event(
                    "node_snapshot",
                    runId=run_id,
                    nodeId=node_id,
                    nodeType=node_type,
                    status=status,
                    input=input_data,
                    output=output_data,
                    errorMessage=error_message,
                    hardTruncated=hard_truncated,
                    ts=_utc_iso_now(),
                    **_build_trace_context_payload(extra),
                )

            def on_human_approval_requested(payload: dict[str, Any]) -> None:
                _queue_key_event(
                    "human_approval_requested",
                    runId=run_id,
                    approval=payload,
                    ts=_utc_iso_now(),
                )

            def on_human_approval_resolved(payload: dict[str, Any]) -> None:
                _queue_key_event(
                    "human_approval_resolved",
                    runId=run_id,
                    approval=payload,
                    ts=_utc_iso_now(),
                )

            for delta in engine.execute(
                skill=prepared.skill_definition,
                user_input=prepared.user_input or "",
                history=prepared.history,
                runtime_context={
                    "stream_output": prepared.stream_output,
                    "conversation_id": runtime_conversation_id,
                    "structured_input": prepared.structured_input,
                    "run_id": run_id,
                    "channel_type": "workflow_test",
                    "workflow_id": str(prepared.workflow_id),
                    "skill_id": str(prepared.skill_id) if prepared.skill_id else None,
                    **(
                        {"session_memory": prepared.session_memory.to_payload()}
                        if prepared.start_input_mode == "text"
                        else {}
                    ),
                },
                on_tool_call_start=on_tool_call_start,
                on_tool_call_end=on_tool_call_end,
                on_node_start=on_node_start,
                on_node_output_delta=on_node_output_delta,
                on_node_end=on_node_end,
                on_branch_decision=on_branch_decision,
                on_node_snapshot=on_node_snapshot,
                on_human_approval_requested=on_human_approval_requested,
                on_human_approval_resolved=on_human_approval_resolved,
            ):
                yield from flush()
                if not delta:
                    continue
                text = str(delta)
                final_parts.append(text)
                had_pending = _has_pending_delta()
                pending_content_delta_parts.append(text)
                pending_content_delta_chars += len(text)
                if not had_pending:
                    last_delta_flush_at = time.perf_counter()
                flush_pending_delta(force=False)
                yield from flush()

            flush_pending_delta(force=True)
            yield from flush()
            final_text = "".join(final_parts)
            final_json = _safe_json_parse(final_text)
            next_session_memory_payload: dict[str, Any] | None = None
            if prepared.start_input_mode == "text":
                next_session_memory = self._compute_next_session_memory(
                    cfg=OpenAiFallbackConfig(
                        api_key=cfg.api_key,
                        base_url=cfg.base_url,
                        model=cfg.model,
                    ),
                    prepared=prepared,
                    assistant_text=final_text,
                    run_id=run_id,
                )
                next_session_memory_payload = next_session_memory.to_payload()
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            enqueue(
                "run_end",
                runId=run_id,
                status="completed",
                durationMs=duration_ms,
                finalText=final_text,
                finalJson=final_json,
                streamOutput=prepared.stream_output,
                **({"sessionMemory": next_session_memory_payload} if next_session_memory_payload is not None else {}),
            )
            yield from flush()
        except GeneratorExit:
            raise
        except Exception as exc:
            logger.exception("workflow test run failed scope=%s run_id=%s", prepared.display_name, run_id)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            final_text = "".join(final_parts)
            flush_pending_delta(force=True)
            enqueue(
                "run_error",
                runId=run_id,
                message=str(exc) or "Workflow test run failed",
                stage="runtime",
                ts=_utc_iso_now(),
            )
            enqueue(
                "run_end",
                runId=run_id,
                status="error",
                durationMs=duration_ms,
                finalText=final_text,
                finalJson=None,
                streamOutput=prepared.stream_output,
            )
            yield from flush()
