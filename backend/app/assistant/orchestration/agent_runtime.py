"""LangGraph Supervisor 根智能体运行时。"""
from __future__ import annotations

import logging
import uuid
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.assistant.orchestration.intent_router import SkillRouter
from app.assistant.orchestration.supervisor_graph import build_supervisor_graph
from app.assistant.orchestration.supervisor_state import SupervisorState
from app.assistant.run_control import AssistantRunCancelled, ensure_not_cancelled
from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME
from app.assistant.workflow.engine.engine import LangGraphEngine
from app.assistant_config.registry import SkillRegistry

logger = logging.getLogger(__name__)

_GRAPH_DONE_SENTINEL = object()


class AssistantAgent:
    """AI 助手 Agent - 基于 LangGraph Supervisor + Skill 子图机制"""

    def __init__(self, api_key: str, base_url: str, model: str, db: Session | None = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.db = db

        # 初始化 Router 和 LangGraph 引擎
        self.router = SkillRouter(api_key, base_url, model, db)
        self.langgraph_engine = LangGraphEngine(api_key, base_url, model, db)
        self._supervisor_graph = build_supervisor_graph(
            route_once_node=self._route_once_node,
            execute_selected_skill_node=self._execute_selected_skill_node,
            execute_default_skill_node=self._execute_default_skill_node,
        )

    @staticmethod
    def _push_stream_chunk(state: SupervisorState, chunk: str) -> None:
        stream_queue = state.get("stream_queue")
        if stream_queue is None:
            return
        stream_queue.put(chunk)

    @staticmethod
    def _resolve_skill_definition(db: Session | None, skill_name: str):
        if db is not None:
            return SkillRegistry(db).resolve(skill_name, include_workflow=True)
        return SkillRegistry.resolve_system_skill(skill_name)

    def _resolve_engine_for_skill(self, skill_def):
        engine = self.langgraph_engine
        model_source = str(getattr(skill_def, "model_source", "default") or "default").strip().lower()
        selected_model_id = str(getattr(skill_def, "model_id", "") or "").strip()
        if (
            self.db is not None
            and getattr(skill_def, "langgraph_pattern", None) == "agent_loop"
            and model_source == "custom"
            and selected_model_id
        ):
            cfg = resolve_openai_compat_config_by_model_id(
                self.db,
                model_id=selected_model_id,
                model_type="llm",
            )
            if cfg is None:
                raise ValueError(
                    f"Skill {getattr(skill_def, 'name', '')} references unavailable llm model: {selected_model_id}"
                )
            engine = LangGraphEngine(cfg.api_key, cfg.base_url, cfg.model, self.db)
        return engine

    def _route_once_node(self, state: SupervisorState) -> dict[str, Any]:
        ensure_not_cancelled(state.get("cancel_checker"), message="assistant run cancelled before route")
        runtime_context = state.get("runtime_context") or {}
        decision = self.router.route(
            str(state.get("user_input", "") or ""),
            history=list(state.get("history", []) or []),
            runtime_context=runtime_context,
        )

        selected_skill = str(decision.selected_skill or "")
        if not selected_skill:
            error_message = "No executable skill selected and default skill is unavailable"
            logger.error(
                "supervisor route failed conversation_id=%s message_id=%s reason=%s",
                runtime_context.get("conversation_id", ""),
                runtime_context.get("message_id", ""),
                decision.fallback_reason,
            )
            return {
                "route_skill": decision.skill,
                "route_reason": decision.reason,
                "selected_skill": "",
                "selected_skill_hidden": False,
                "execution_status": "failed",
                "error_code": "route_unavailable",
                "error_message": error_message,
            }

        return {
            "route_skill": decision.skill,
            "route_reason": decision.reason,
            "selected_skill": selected_skill,
            "selected_skill_hidden": selected_skill == DEFAULT_SKILL_NAME,
            "execution_status": "running",
        }

    def _execute_skill(self, state: SupervisorState, *, skill_name: str) -> dict[str, Any]:
        ensure_not_cancelled(state.get("cancel_checker"), message="assistant run cancelled before skill execution")
        runtime_context = state.get("runtime_context") or {}
        skill_id = f"skill_{uuid.uuid4().hex[:8]}"
        hidden = skill_name == DEFAULT_SKILL_NAME

        on_skill_start = state.get("on_skill_start")
        if on_skill_start:
            on_skill_start(skill_id, skill_name, hidden)
            self._push_stream_chunk(state, "")

        try:
            skill_def = self._resolve_skill_definition(self.db, skill_name)
            if skill_def is None:
                raise ValueError(f"Skill not found or disabled: {skill_name}")
            workflow_nodes = list(getattr(skill_def, "workflow_nodes", []) or [])
            workflow_node_labels: dict[str, str] = {}
            for node in workflow_nodes:
                if isinstance(node, dict):
                    node_id = str(node.get("node_id", "") or "").strip()
                    node_label = str(node.get("label", "") or "").strip()
                else:
                    node_id = str(getattr(node, "node_id", "") or "").strip()
                    node_label = str(getattr(node, "label", "") or "").strip()
                if node_id:
                    workflow_node_labels[node_id] = node_label

            logger.info(
                "supervisor executing skill=%s conversation_id=%s message_id=%s",
                skill_name,
                runtime_context.get("conversation_id", ""),
                runtime_context.get("message_id", ""),
            )

            engine = self._resolve_engine_for_skill(skill_def)
            on_node_start = state.get("on_node_start")
            on_node_end = state.get("on_node_end")
            emit_workflow_node_events = (
                str(getattr(skill_def, "langgraph_pattern", "") or "").strip().lower() == "workflow_dag"
            )

            def _resolve_node_label(node_id: str) -> str:
                node_key = str(node_id or "").strip()
                if not node_key:
                    return ""
                return str(workflow_node_labels.get(node_key, "") or "")

            def _handle_node_start(node_id: str, node_type: str) -> None:
                if not (emit_workflow_node_events and callable(on_node_start)):
                    return
                node_key = str(node_id or "").strip()
                if not node_key:
                    return
                on_node_start(node_key, str(node_type or "").strip(), _resolve_node_label(node_key))

            def _handle_node_end(node_id: str, status: str) -> None:
                if not (emit_workflow_node_events and callable(on_node_end)):
                    return
                node_key = str(node_id or "").strip()
                if not node_key:
                    return
                on_node_end(node_key, str(status or "").strip(), _resolve_node_label(node_key))

            for delta in engine.execute(
                skill=skill_def,
                user_input=str(state.get("user_input", "") or ""),
                history=list(state.get("history", []) or []),
                runtime_context=runtime_context,
                on_tool_call_start=state.get("on_tool_call_start"),
                on_tool_call_end=state.get("on_tool_call_end"),
                on_analysis_start=state.get("on_analysis_start"),
                on_analysis_delta=state.get("on_analysis_delta"),
                on_analysis_end=state.get("on_analysis_end"),
                on_node_start=_handle_node_start if emit_workflow_node_events and callable(on_node_start) else None,
                on_node_end=_handle_node_end if emit_workflow_node_events and callable(on_node_end) else None,
                on_human_approval_requested=state.get("on_human_approval_requested"),
                on_human_approval_resolved=state.get("on_human_approval_resolved"),
                cancel_checker=state.get("cancel_checker"),
            ):
                ensure_not_cancelled(state.get("cancel_checker"), message="assistant run cancelled during skill execution")
                # Keep empty chunks as stream ticks so service layer can flush queued SSE side events
                # (tool/analysis/human approval notifications) while the workflow is waiting.
                self._push_stream_chunk(state, str(delta or ""))

            on_skill_end = state.get("on_skill_end")
            if on_skill_end:
                on_skill_end(skill_id, "completed")
                self._push_stream_chunk(state, "")

            return {
                "execution_status": "completed",
                "selected_skill": skill_name,
                "selected_skill_hidden": hidden,
            }
        except AssistantRunCancelled:
            on_skill_end = state.get("on_skill_end")
            if on_skill_end:
                on_skill_end(skill_id, "cancelled")
                self._push_stream_chunk(state, "")
            raise
        except Exception as exc:
            logger.error(
                "supervisor skill failed skill=%s conversation_id=%s message_id=%s error=%s",
                skill_name,
                runtime_context.get("conversation_id", ""),
                runtime_context.get("message_id", ""),
                exc,
                exc_info=True,
            )
            on_skill_end = state.get("on_skill_end")
            if on_skill_end:
                on_skill_end(skill_id, "error")
                self._push_stream_chunk(state, "")
            return {
                "execution_status": "failed",
                "error_code": "skill_execution_failed",
                "error_message": str(exc),
                "selected_skill": skill_name,
                "selected_skill_hidden": hidden,
            }

    def _execute_selected_skill_node(self, state: SupervisorState) -> dict[str, Any]:
        skill_name = str(state.get("selected_skill", "") or "")
        if not skill_name:
            return {
                "execution_status": "failed",
                "error_code": "selected_skill_missing",
                "error_message": "Selected skill is missing",
            }
        return self._execute_skill(state, skill_name=skill_name)

    def _execute_default_skill_node(self, state: SupervisorState) -> dict[str, Any]:
        return self._execute_skill(state, skill_name=DEFAULT_SKILL_NAME)

    def stream(
        self,
        history: list[dict],
        user_input: str,
        runtime_context: dict | None = None,
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
        """流式生成回复 - 由 Supervisor 根图编排单 Skill 执行。"""
        logger.debug("assistant supervisor stream start")

        stream_queue: Queue[object] = Queue()
        final_state_holder: list[dict[str, Any]] = []
        graph_errors: list[Exception] = []

        initial_state: SupervisorState = {
            "history": history,
            "user_input": user_input,
            "runtime_context": runtime_context or {},
            "execution_status": "pending",
            "stream_queue": stream_queue,
            "on_tool_call_start": on_tool_call_start,
            "on_tool_call_end": on_tool_call_end,
            "on_skill_start": on_skill_start,
            "on_skill_end": on_skill_end,
            "on_analysis_start": on_analysis_start,
            "on_analysis_delta": on_analysis_delta,
            "on_analysis_end": on_analysis_end,
            "on_node_start": on_node_start,
            "on_node_end": on_node_end,
            "on_human_approval_requested": on_human_approval_requested,
            "on_human_approval_resolved": on_human_approval_resolved,
            "cancel_checker": cancel_checker,
        }

        def _run_graph() -> None:
            try:
                final_state: dict[str, Any] = {}
                runner = self._supervisor_graph
                if hasattr(runner, "stream"):
                    for update in runner.stream(initial_state):
                        if not isinstance(update, dict):
                            continue
                        for payload in update.values():
                            if isinstance(payload, dict):
                                final_state.update(payload)
                elif hasattr(runner, "invoke"):
                    result = runner.invoke(initial_state)
                    if isinstance(result, dict):
                        final_state.update(result)
                else:
                    # Unit-test stub fallback: run equivalent branch logic without graph runtime.
                    state = dict(initial_state)
                    state.update(self._route_once_node(state))
                    if str(state.get("execution_status", "")) != "failed":
                        if str(state.get("selected_skill", "")) == DEFAULT_SKILL_NAME:
                            state.update(self._execute_default_skill_node(state))
                        else:
                            state.update(self._execute_selected_skill_node(state))
                    final_state.update(state)
                final_state_holder.append(final_state)
            except Exception as exc:
                graph_errors.append(exc)
            finally:
                stream_queue.put(_GRAPH_DONE_SENTINEL)

        graph_thread = Thread(target=_run_graph, daemon=True)
        graph_thread.start()

        graph_done = False
        cancel_error: AssistantRunCancelled | None = None
        while not graph_done:
            try:
                ensure_not_cancelled(cancel_checker, message="assistant run cancelled while waiting graph output")
            except AssistantRunCancelled as exc:
                cancel_error = exc
                break
            try:
                item = stream_queue.get(timeout=0.1)
            except Empty:
                if not graph_thread.is_alive() and stream_queue.empty():
                    break
                continue

            if item is _GRAPH_DONE_SENTINEL:
                graph_done = True
                continue

            yield str(item)

        if cancel_error is not None:
            graph_thread.join(timeout=0.2)
            raise cancel_error

        graph_thread.join()

        if graph_errors:
            raise graph_errors[0]

        final_state = final_state_holder[0] if final_state_holder else {}
        execution_status = str(final_state.get("execution_status", "") or "")
        if execution_status == "failed":
            error_message = str(final_state.get("error_message", "") or "Supervisor execution failed")
            raise RuntimeError(error_message)

        logger.debug("assistant supervisor stream end")
