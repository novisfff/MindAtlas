from __future__ import annotations

from datetime import date
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine.agent_execution_core import (
    AgentExecutionHooks,
    AgentExecutionRequest,
    build_internal_kb_tool,
    run_agent_execution,
)
from app.assistant.workflow.engine import engine as engine_runtime
from app.assistant.workflow.engine.runtime_helpers import (
    cfg_bool_value,
    cfg_int_value,
    cfg_string_list,
    emit,
    get_start_inputs,
    render_memory_injection_block,
    resolve_node_template_vars,
    resolve_start_memory_mode,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.config import get_settings


_AGENT_KNOWLEDGE_MODES = {"naive", "local", "global", "hybrid", "mix"}


def _normalize_l0_messages(memory_context: dict[str, Any]) -> list[dict[str, str]]:
    l0_messages_raw = memory_context.get("l0_messages")
    if not isinstance(l0_messages_raw, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in l0_messages_raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        content = str(item.get("content", "") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _resolve_agent_knowledge_config(node_cfg: dict[str, Any]) -> dict[str, Any]:
    enabled = cfg_bool_value(node_cfg, "knowledge_enabled", "knowledgeEnabled", default=False)

    raw_mode = str(node_cfg.get("knowledge_mode", node_cfg.get("knowledgeMode", "")) or "").strip().lower()
    knowledge_mode = raw_mode if raw_mode in _AGENT_KNOWLEDGE_MODES else None

    raw_top_k = node_cfg.get("knowledge_top_k", node_cfg.get("knowledgeTopK"))
    knowledge_top_k: int | None = None
    if raw_top_k is not None and str(raw_top_k).strip():
        try:
            knowledge_top_k = max(1, min(50, int(raw_top_k)))
        except Exception:
            knowledge_top_k = None

    return {
        "enabled": enabled,
        "mode": knowledge_mode,
        "top_k": knowledge_top_k,
    }


def _build_agent_kb_tool(
    *,
    node_id: str,
    tool_map: dict[str, Any],
    db_bind: Any,
    knowledge_mode: str | None,
    knowledge_top_k: int | None,
    locale: str | None,
) -> tuple[Any, Callable[..., Any]]:
    base_kb_tool = tool_map.get("kb_search")
    if base_kb_tool is None:
        raise RuntimeError(
            f"DAG agent node {node_id} requires kb_search runtime tool when knowledgeEnabled=true"
        )

    wrapped_kb_tool = engine_runtime._wrap_tool_with_db(base_kb_tool, db_bind)
    return build_internal_kb_tool(
        base_kb_tool=base_kb_tool,
        wrapped_kb_tool=wrapped_kb_tool,
        description=_copy.build_internal_kb_tool_description(locale),
        knowledge_mode=knowledge_mode,
        knowledge_top_k=knowledge_top_k,
    )


def build_dag_agent_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
    execution_scope: Any | None = None,
) -> Callable[[WorkflowState], dict]:
    _ = execution_scope
    def agent_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        locale = sys_vars.get("locale")
        env_vars = state.get("env_vars", {}) or {}
        runtime_node_llms = state.get("node_llms", {}) or {}
        if not isinstance(runtime_node_llms, dict):
            runtime_node_llms = {}

        llm_for_node = runtime_node_llms.get(node_id)
        if llm_for_node is None and node_llms is not None:
            llm_for_node = node_llms.get(node_id)
        if llm_for_node is None:
            llm_for_node = llm

        configured_tool_names = cfg_string_list(node_cfg, "tool_names", "toolNames")
        if any(tool_name == "kb_search" for tool_name in configured_tool_names):
            raise RuntimeError(
                f"DAG agent node {node_id} must not include kb_search in toolNames; use knowledgeEnabled instead"
            )
        knowledge_cfg = _resolve_agent_knowledge_config(node_cfg)
        if not configured_tool_names and not knowledge_cfg["enabled"]:
            raise RuntimeError(
                f"DAG agent node {node_id} requires at least one toolNames entry or knowledgeEnabled=true"
            )

        bound_tools: list[Any] = []
        tool_runners: dict[str, Callable[..., Any]] = {}
        for tool_name in configured_tool_names:
            tool = tool_map.get(tool_name)
            if tool is None:
                raise RuntimeError(
                    f"DAG agent node {node_id} references unavailable tool: {tool_name}"
                )
            bound_tools.append(tool)
            tool_runners[tool_name] = engine_runtime._wrap_tool_with_db(tool, db_bind)

        if knowledge_cfg["enabled"]:
            kb_tool, kb_runner = _build_agent_kb_tool(
                node_id=node_id,
                tool_map=tool_map,
                db_bind=db_bind,
                knowledge_mode=knowledge_cfg["mode"],
                knowledge_top_k=knowledge_cfg["top_k"],
                locale=locale,
            )
            bound_tools.append(kb_tool)
            tool_runners["kb_search"] = kb_runner

        system_prompt_raw = node_cfg.get("system_prompt", node_cfg.get("systemPrompt", ""))
        if not isinstance(system_prompt_raw, str):
            system_prompt_raw = ""
        system_prompt = resolve_node_template_vars(
            system_prompt_raw,
            node_outputs,
            start_inputs,
            sys_vars,
            env_vars=env_vars,
        )

        user_input_template = node_cfg.get("user_input", node_cfg.get("userInput", "{{start.user_input}}"))
        if not isinstance(user_input_template, str):
            user_input_template = "{{start.user_input}}"
        user_input_rendered = resolve_node_template_vars(
            user_input_template,
            node_outputs,
            start_inputs,
            sys_vars,
            env_vars=env_vars,
        )
        if not user_input_rendered.strip():
            user_input_rendered = start_inputs.get("user_input", "") or state.get("user_input", "")

        memory_mode = resolve_start_memory_mode(
            {"memory_mode": state.get("memory_mode")},
            default_mode="auto",
        )
        memory_context = state.get("memory_context") if isinstance(state.get("memory_context"), dict) else {}
        l0_messages = _normalize_l0_messages(memory_context)

        today = date.today()
        memory_block = ""
        if memory_mode == "auto":
            settings = get_settings()
            memory_block = render_memory_injection_block(
                memory_context=memory_context,
                max_chars=max(
                    1,
                    int(getattr(settings, "assistant_memory_injection_max_chars", 30000) or 30000),
                ),
                locale=locale,
            )
        full_prompt = _copy.build_dag_agent_system_prompt(
            locale=locale,
            current_date=today,
            task_prompt=system_prompt,
            memory_block=memory_block,
            knowledge_enabled=bool(knowledge_cfg["enabled"]),
        )

        conversation_messages: list[dict[str, Any]] = [
            {"role": "system", "content": full_prompt},
        ]
        if memory_mode == "auto" and l0_messages:
            conversation_messages.extend(l0_messages)
        conversation_messages.append({"role": "user", "content": user_input_rendered})

        stream_output_enabled = bool(state.get("stream_output_enabled", True))
        output_stream_source_node_id = str(state.get("output_stream_source_node_id", "") or "")

        max_iterations = cfg_int_value(
            node_cfg,
            "max_iterations",
            "maxIterations",
            default=12,
            min_value=1,
            max_value=20,
        )

        emit(metadata, "on_node_start", node_id=node_id, node_type="agent")
        result = run_agent_execution(
            AgentExecutionRequest(
                llm=llm_for_node,
                system_prompt=full_prompt,
                conversation_messages=conversation_messages[1:],
                bound_tools=bound_tools,
                tool_runners=tool_runners,
                max_iterations=max_iterations,
                stream_output_enabled=stream_output_enabled,
                execution_hooks=AgentExecutionHooks(
                    metadata=metadata,
                    content_passthrough_enabled=output_stream_source_node_id == node_id,
                    node_output_delta_enabled=True,
                ),
                trace_context={"node_id": node_id, "node_type": "agent"},
                knowledge_mode="node_kb" if knowledge_cfg["enabled"] else "none",
                recent_dialogue_injection="message_flow" if memory_mode == "auto" and l0_messages else "none",
                locale=locale,
            )
        )
        if result.stopped_by == "invalid_tool":
            raise RuntimeError(
                f"DAG agent node {node_id} requested unavailable or non-whitelisted tool: {result.error_message or ''}".rstrip()
            )
        if result.stopped_by == "tool_error":
            raise RuntimeError(
                f"DAG agent node {node_id} tool call failed: {result.error_message or 'unknown error'}"
            )
        if result.stopped_by == "max_iterations":
            raise RuntimeError(
                f"DAG agent node {node_id} exceeded maxIterations={max_iterations}"
            )

        node_out: NodeOutput = {
            "status": "ok",
            "text": result.final_text,
            "raw": result.final_text,
            "json_fields": {"response": result.final_text},
        }

        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return agent_node
