from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine.state import WorkflowState
from app.assistant.workflow.engine.workflow_dag_plan import WorkflowDagPlan


@dataclass(frozen=True)
class WorkflowNodeBuilderDeps:
    llm: ChatOpenAI
    args_llm: ChatOpenAI
    tool_map: dict[str, Any]
    db_bind: Any
    node_llms: dict[str, ChatOpenAI] | None
    build_start_node: Callable[[dict[str, Any]], Callable[[WorkflowState], dict]]
    build_dag_llm_node: Callable[..., Callable[[WorkflowState], dict]]
    build_dag_agent_node: Callable[..., Callable[[WorkflowState], dict]]
    build_output_node: Callable[..., Callable[[WorkflowState], dict]]
    build_dag_tool_node: Callable[..., Callable[[WorkflowState], dict]]
    build_code_executor_node: Callable[..., Callable[[WorkflowState], dict]]
    build_http_request_node: Callable[..., Callable[[WorkflowState], dict]]
    build_variable_assign_node: Callable[..., Callable[[WorkflowState], dict]]
    build_human_in_loop_node: Callable[..., Callable[[WorkflowState], dict]]
    build_workflow_call_node: Callable[..., Callable[[WorkflowState], dict]]
    build_if_else_node: Callable[..., Callable[[WorkflowState], dict]]
    build_param_extractor_node: Callable[..., Callable[[WorkflowState], dict]]
    build_kr_node: Callable[..., Callable[[WorkflowState], dict]]
    build_iteration_node: Callable[..., Callable[[WorkflowState], dict]]
    build_loop_node: Callable[..., Callable[[WorkflowState], dict]]


def _build_workflow_node_fn(
    *,
    node_id: str,
    node_type: str,
    node_cfg: dict[str, Any],
    deps: WorkflowNodeBuilderDeps,
) -> Callable[[WorkflowState], dict]:
    if node_type == "start":
        return deps.build_start_node(node_cfg)
    if node_type == "llm":
        return deps.build_dag_llm_node(node_id, node_cfg, deps.llm, node_llms=deps.node_llms)
    if node_type == "agent":
        return deps.build_dag_agent_node(
            node_id,
            node_cfg,
            deps.llm,
            deps.tool_map,
            deps.db_bind,
            node_llms=deps.node_llms,
        )
    if node_type == "output":
        return deps.build_output_node(node_id, node_cfg)
    if node_type == "tool":
        return deps.build_dag_tool_node(node_id, node_cfg, deps.tool_map, deps.args_llm, deps.db_bind)
    if node_type == "code_executor":
        return deps.build_code_executor_node(node_id, node_cfg)
    if node_type == "http_request":
        return deps.build_http_request_node(node_id, node_cfg)
    if node_type == "variable_assign":
        return deps.build_variable_assign_node(node_id, node_cfg)
    if node_type == "human_in_loop":
        return deps.build_human_in_loop_node(node_id, node_cfg)
    if node_type == "workflow_call":
        return deps.build_workflow_call_node(
            node_id,
            node_cfg,
            deps.llm,
            deps.args_llm,
            deps.tool_map,
            deps.db_bind,
        )
    if node_type == "if_else":
        return deps.build_if_else_node(node_id, node_cfg)
    if node_type == "parameter_extractor":
        return deps.build_param_extractor_node(node_id, node_cfg, deps.llm, node_llms=deps.node_llms)
    if node_type == "knowledge_retrieval":
        return deps.build_kr_node(node_id, node_cfg, deps.tool_map, deps.db_bind)
    if node_type == "iteration":
        return deps.build_iteration_node(
            node_id,
            node_cfg,
            deps.llm,
            deps.args_llm,
            deps.tool_map,
            deps.db_bind,
            node_llms=deps.node_llms,
        )
    if node_type == "loop":
        return deps.build_loop_node(
            node_id,
            node_cfg,
            deps.llm,
            deps.args_llm,
            deps.tool_map,
            deps.db_bind,
            node_llms=deps.node_llms,
        )
    raise ValueError(f"Unknown node type: {node_type}")


def add_workflow_graph_nodes(
    *,
    graph: Any,
    dag_plan: WorkflowDagPlan,
    deps: WorkflowNodeBuilderDeps,
    wrap_workflow_node_with_snapshot: Callable[
        [str, str, dict[str, Any], Callable[[WorkflowState], dict]],
        Callable[[WorkflowState], dict],
    ],
) -> None:
    for node_id in dag_plan.topo_order:
        node_type = dag_plan.type_map[node_id]
        node_cfg = dag_plan.node_map[node_id]
        node_fn = _build_workflow_node_fn(
            node_id=node_id,
            node_type=node_type,
            node_cfg=node_cfg,
            deps=deps,
        )
        node_fn = wrap_workflow_node_with_snapshot(node_id, node_type, node_cfg, node_fn)
        graph.add_node(node_id, node_fn)


def add_workflow_graph_edges(
    *,
    graph: Any,
    dag_plan: WorkflowDagPlan,
    end_sentinel: Any,
) -> None:
    for src_node_id in dag_plan.topo_order:
        targets = dag_plan.out_edges.get(src_node_id, [])
        if not targets:
            graph.add_edge(src_node_id, end_sentinel)
            continue

        if dag_plan.type_map[src_node_id] in {"if_else", "human_in_loop"}:
            handle_to_target: dict[str, str] = {}
            for tgt, handle, _ in targets:
                normalized_handle = "else" if handle == "default" else handle
                handle_to_target[normalized_handle] = tgt

            def _make_branch_router(node_id: str, handle_map: dict[str, str]):
                def router(state: WorkflowState) -> str:
                    decisions = state.get("branch_decisions", {})
                    chosen = decisions.get(node_id, "else")
                    if chosen == "default":
                        chosen = "else"
                    return handle_map.get(chosen, handle_map.get("else", handle_map.get("default", end_sentinel)))

                return router

            graph.add_conditional_edges(
                src_node_id,
                _make_branch_router(src_node_id, handle_to_target),
                {tgt: tgt for tgt, _, _ in targets},
            )
            continue

        if len(targets) == 1:
            graph.add_edge(src_node_id, targets[0][0])
            continue

        for tgt, _, _ in targets:
            graph.add_edge(src_node_id, tgt)
