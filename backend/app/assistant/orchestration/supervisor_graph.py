from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.assistant.orchestration.supervisor_state import SupervisorState
from app.assistant.skill_catalog.base import DEFAULT_SKILL_NAME


def _route_branch(state: SupervisorState) -> str:
    status = str(state.get("execution_status", "pending") or "pending")
    if status == "failed":
        return "fail"

    selected_skill = str(state.get("selected_skill", "") or "")
    if not selected_skill:
        return "fail"
    if selected_skill == DEFAULT_SKILL_NAME:
        return "execute_default_skill"
    return "execute_selected_skill"


def _after_execute_branch(state: SupervisorState) -> str:
    status = str(state.get("execution_status", "pending") or "pending")
    if status == "failed":
        return "fail"
    return "finish"


def _finish_node(state: SupervisorState) -> dict[str, Any]:
    status = str(state.get("execution_status", "") or "")
    if status not in {"completed", "failed"}:
        status = "completed"
    return {"execution_status": status}


def _fail_node(state: SupervisorState) -> dict[str, Any]:
    return {"execution_status": "failed"}


def build_supervisor_graph(
    *,
    route_once_node: Callable[[SupervisorState], dict[str, Any]],
    execute_selected_skill_node: Callable[[SupervisorState], dict[str, Any]],
    execute_default_skill_node: Callable[[SupervisorState], dict[str, Any]],
) -> Any:
    graph = StateGraph(SupervisorState)
    graph.add_node("route_once", route_once_node)
    graph.add_node("execute_selected_skill", execute_selected_skill_node)
    graph.add_node("execute_default_skill", execute_default_skill_node)
    graph.add_node("finish", _finish_node)
    graph.add_node("fail", _fail_node)

    graph.set_entry_point("route_once")
    graph.add_conditional_edges(
        "route_once",
        _route_branch,
        {
            "execute_selected_skill": "execute_selected_skill",
            "execute_default_skill": "execute_default_skill",
            "fail": "fail",
        },
    )
    graph.add_conditional_edges(
        "execute_selected_skill",
        _after_execute_branch,
        {
            "finish": "finish",
            "fail": "fail",
        },
    )
    graph.add_conditional_edges(
        "execute_default_skill",
        _after_execute_branch,
        {
            "finish": "finish",
            "fail": "fail",
        },
    )
    graph.add_edge("finish", END)
    graph.add_edge("fail", END)
    return graph.compile()
