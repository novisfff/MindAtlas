from __future__ import annotations

from typing import Any, Callable


def build_agent_subgraph(
    *,
    skill: Any,
    llm: Any,
    tools: list[Any],
    db_bind: Any,
    build_agent_node: Callable[[Any, Any, list[Any]], Callable[[dict[str, Any]], dict[str, Any]]],
    build_tool_node: Callable[[list[Any], Any], Callable[[dict[str, Any]], dict[str, Any]]],
    state_type: Any,
) -> Any:
    from langgraph.graph import END, StateGraph

    agent_node = build_agent_node(skill, llm, tools)
    tool_node = build_tool_node(tools, db_bind)

    graph = StateGraph(state_type)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")

    def should_continue(state: dict[str, Any]) -> str:
        msgs = state.get("messages", [])
        last = msgs[-1] if msgs else None
        if last and getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()
