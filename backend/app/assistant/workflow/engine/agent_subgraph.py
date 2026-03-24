from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.assistant.workflow.engine import engine as engine_runtime
from app.assistant.workflow.engine.agent_execution_core import (
    AgentExecutionHooks,
    AgentExecutionRequest,
    build_internal_kb_tool,
    run_agent_execution,
)
from app.assistant.workflow.engine.runtime_helpers import AGENT_MAX_ITERATIONS


def _message_to_payload(message: BaseMessage) -> dict[str, Any] | None:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": str(message.content or "")}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": str(message.content or "")}
    if isinstance(message, ToolMessage):
        payload: dict[str, Any] = {
            "role": "tool",
            "content": str(message.content or ""),
        }
        tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        return payload
    if isinstance(message, AIMessage):
        payload = {"role": "assistant", "content": str(message.content or "")}
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(tool_calls, list) and tool_calls:
            payload["tool_calls"] = tool_calls
        return payload
    role = str(getattr(message, "type", "") or "").strip().lower()
    content = str(getattr(message, "content", "") or "")
    if role in {"human", "user"}:
        return {"role": "user", "content": content}
    if role in {"ai", "assistant"}:
        return {"role": "assistant", "content": content}
    if role == "tool":
        return {"role": "tool", "content": content}
    if role == "system":
        return {"role": "system", "content": content}
    return None


def build_agent_subgraph(
    *,
    skill: Any,
    llm: Any,
    tools: list[Any],
    db_bind: Any,
    state_type: Any,
) -> Any:
    from langgraph.graph import END, StateGraph

    base_tool_map = {str(getattr(tool, "name", "") or "").strip(): tool for tool in tools}
    kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))

    bound_tools: list[Any] = []
    tool_runners: dict[str, Any] = {}
    kb_bound = False
    for tool in tools:
        tool_name = str(getattr(tool, "name", "") or "").strip()
        if not tool_name:
            continue
        if tool_name == "kb_search":
            if kb_bound or not kb_enabled:
                continue
            wrapped_kb_tool = engine_runtime._wrap_tool_with_db(tool, db_bind)
            kb_tool, kb_runner = build_internal_kb_tool(
                base_kb_tool=tool,
                wrapped_kb_tool=wrapped_kb_tool,
            )
            bound_tools.append(kb_tool)
            tool_runners["kb_search"] = kb_runner
            kb_bound = True
            continue

        bound_tools.append(tool)
        tool_runners[tool_name] = engine_runtime._wrap_tool_with_db(tool, db_bind)

    if kb_enabled and not kb_bound:
        base_kb_tool = base_tool_map.get("kb_search")
        if base_kb_tool is not None:
            wrapped_kb_tool = engine_runtime._wrap_tool_with_db(base_kb_tool, db_bind)
            kb_tool, kb_runner = build_internal_kb_tool(
                base_kb_tool=base_kb_tool,
                wrapped_kb_tool=wrapped_kb_tool,
            )
            bound_tools.append(kb_tool)
            tool_runners["kb_search"] = kb_runner
            kb_bound = True

    def agent_node(state: dict[str, Any]) -> dict[str, Any]:
        metadata = state.get("metadata", {}) or {}
        raw_messages = list(state.get("messages", []) or [])
        payload_messages = [
            item for item in (_message_to_payload(message) for message in raw_messages)
            if isinstance(item, dict)
        ]

        system_prompt = ""
        conversation_messages: list[dict[str, Any]] = []
        for item in payload_messages:
            if item.get("role") == "system" and not system_prompt:
                system_prompt = str(item.get("content", "") or "")
                continue
            conversation_messages.append(item)

        result = run_agent_execution(
            AgentExecutionRequest(
                llm=llm,
                system_prompt=system_prompt,
                conversation_messages=conversation_messages,
                bound_tools=bound_tools,
                tool_runners=tool_runners,
                max_iterations=AGENT_MAX_ITERATIONS,
                stream_output_enabled=True,
                execution_hooks=AgentExecutionHooks(
                    metadata=metadata,
                    content_passthrough_enabled=True,
                    node_output_delta_enabled=False,
                ),
                trace_context={},
                knowledge_mode="skill_kb" if kb_bound else "none",
                recent_dialogue_injection="none",
            )
        )

        if result.stopped_by == "max_iterations":
            final_text = "工具调用次数过多，未能完成任务。请尝试缩小问题范围或换一种问法。"
        elif result.stopped_by in {"invalid_tool", "tool_error"}:
            raise RuntimeError(result.error_message or "Agent tool execution failed")
        else:
            final_text = result.final_text

        return {
            "messages": [AIMessage(content=final_text)],
            "iteration_count": result.round_count,
        }

    graph = StateGraph(state_type)
    graph.add_node("agent", agent_node)
    graph.set_entry_point("agent")
    graph.add_edge("agent", END)
    return graph.compile()
