"""LangGraph 执行引擎 - 支持 agent_loop 与 workflow_dag 两种子图模式"""
from __future__ import annotations

import logging
from datetime import date
from queue import Queue
from typing import Any, Callable, Iterator
from uuid import UUID

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skill_catalog.base import SkillDefinition
from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine import execution_context as _exec_ctx
from app.assistant.workflow.engine import execution_plan as _exec_plan
from app.assistant.workflow.engine import execution_services as _exec_services
from app.assistant.workflow.engine.graph_runner import (
    adapt_graph_runnable,
    merge_graph_state,
    snapshot_graph_state,
)
from app.assistant.workflow.engine import runtime_helpers as _rt
from app.assistant.workflow.engine.runtime_dependency_resolver import (
    WorkflowEngineExecutionScope,
)
from app.assistant.workflow.engine import snapshots as _snap
from app.assistant.workflow.engine import stream_runtime as _stream
from app.assistant.workflow.engine.state import AssistantState, NodeOutput, WorkflowState
from app.config import get_settings

logger = logging.getLogger(__name__)

_NODE_SNAPSHOT_STRING_LIMIT = 64 * 1024
_NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT = 4000


# ==================== Helpers ====================

KB_CITATION_INSTRUCTIONS = _copy.build_kb_citation_instructions("zh")


def _extract_single_template_reference(template: str) -> tuple[str, str] | None:
    return _rt.extract_single_template_reference(template)


def _parse_output_boolean(value: Any) -> bool:
    return _rt.parse_output_boolean(value)


# Kept as compatibility wrapper for tests/patch targets.
def _coerce_output_field_value(field_name: str, rendered_value: str, field_spec: dict[str, Any]) -> Any:
    return _rt.coerce_output_field_value(field_name, rendered_value, field_spec)


def _emit(metadata: dict, event: str, **kwargs: Any) -> None:
    _rt.emit(metadata, event, **kwargs)


# Kept as compatibility wrapper for tests/patch targets.
def _wrap_tool_with_db(tool: Any, db_bind: Any) -> Callable:
    return _rt.wrap_tool_with_db(tool, db_bind)


def _coerce_tool_args(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    return _rt.coerce_tool_args(tool, args)


# Kept as compatibility wrapper for tests/patch targets.
def _resolve_tool_output_param_names(tool_name: str, tool: Any) -> list[str]:
    return _rt.resolve_tool_output_param_names(tool_name, tool)


def _build_chat_openai_client(
    *,
    api_key: str,
    base_url: str,
    model: str,
    streaming: bool,
    temperature: float | None = None,
) -> ChatOpenAI:
    default_headers = build_openai_compat_client_headers()
    kwargs: dict[str, Any] = {
        "api_key": (api_key or "").strip(),
        "base_url": (base_url or "").strip(),
        "model": model,
        "streaming": streaming,
        "default_headers": default_headers,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        return ChatOpenAI(**kwargs)
    except TypeError:
        return ChatOpenAI()


def _emit_node_snapshot(
    metadata: dict[str, Any],
    *,
    node_id: str,
    node_type: str,
    status: str,
    input_data: Any,
    output_data: Any,
    error_message: str | None = None,
) -> None:
    _snap.emit_node_snapshot(
        metadata,
        node_id=node_id,
        node_type=node_type,
        status=status,
        input_data=input_data,
        output_data=output_data,
        error_message=error_message,
        string_limit=_NODE_SNAPSHOT_STRING_LIMIT,
    )


def _normalize_l2_memory_payload(raw_text: Any, raw_facts: Any) -> tuple[str, list[str], int]:
    normalized_text = str(raw_text or "")
    if isinstance(raw_facts, list):
        normalized_facts = [str(item).strip() for item in raw_facts if str(item).strip()]
        return normalized_text, normalized_facts, len(normalized_facts)
    if isinstance(raw_facts, tuple):
        normalized_facts = [str(item).strip() for item in raw_facts if str(item).strip()]
        return normalized_text, normalized_facts, len(normalized_facts)
    if isinstance(raw_facts, int):
        return normalized_text, [], max(0, raw_facts)
    return normalized_text, [], 0


# ==================== Workflow DAG State & Helpers (Phase 2 - Task 14) ====================


def _normalize_config(cfg: dict | None) -> dict:
    return _rt.normalize_config(cfg)


def _build_node_snapshot_input(
    node_type: str,
    node_cfg: dict[str, Any],
    state: WorkflowState,
) -> dict[str, Any]:
    return _snap.build_node_snapshot_input(
        node_type,
        node_cfg,
        state,
        text_preview_limit=_NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT,
    )


def _build_node_snapshot_output(
    node_type: str,
    node_out: NodeOutput | None,
    result: dict[str, Any] | None,
) -> Any:
    return _snap.build_node_snapshot_output(
        node_type,
        node_out,
        result,
        text_preview_limit=_NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT,
    )


def _wrap_workflow_node_with_snapshot(
    node_id: str,
    node_type: str,
    node_cfg: dict[str, Any],
    node_fn: Callable[[WorkflowState], dict],
) -> Callable[[WorkflowState], dict]:
    return _snap.wrap_workflow_node_with_snapshot(
        node_id,
        node_type,
        node_cfg,
        node_fn,
        build_input_fn=_build_node_snapshot_input,
        build_output_fn=_build_node_snapshot_output,
        emit_snapshot_fn=_emit_node_snapshot,
    )


def _normalize_container_body_nodes(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from app.assistant.workflow.engine.container_runtime import normalize_container_body_nodes
    return normalize_container_body_nodes(node_cfg, normalize_config=_rt.normalize_config)


def _execute_container_body(
    *,
    container_node_id: str,
    container_node_type: str,
    node_cfg: dict[str, Any],
    parent_state: WorkflowState,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
    container_input: Any = "",
    container_fields: dict[str, Any] | None = None,
    execution_scope: WorkflowEngineExecutionScope | None = None,
) -> dict[str, Any]:
    from app.assistant.workflow.engine.container_runtime import execute_container_body
    return execute_container_body(
        container_node_id=container_node_id,
        container_node_type=container_node_type,
        node_cfg=node_cfg,
        parent_state=parent_state,
        llm=llm,
        args_llm=args_llm,
        tool_map=tool_map,
        db_bind=db_bind,
        node_llms=node_llms,
        container_input=container_input,
        container_fields=container_fields,
        execution_scope=execution_scope,
    )


# ==================== DAG Node Builders (Task 14.3) ====================


def _build_start_node(
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    from app.assistant.workflow.engine.node_builders.start_node import build_start_node
    return build_start_node(node_cfg)


def _build_dag_llm_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.llm_node import build_dag_llm_node
    return build_dag_llm_node(*args, **kwargs)



def _build_dag_agent_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.dag_agent_node import build_dag_agent_node
    return build_dag_agent_node(*args, **kwargs)


def _build_output_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.output_node import build_output_node
    return build_output_node(*args, **kwargs)



def _build_dag_tool_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.tool_node import build_dag_tool_node
    return build_dag_tool_node(*args, **kwargs)



def _build_code_executor_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.code_executor_node import build_code_executor_node
    return build_code_executor_node(*args, **kwargs)



def _build_http_request_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.http_request_node import build_http_request_node
    return build_http_request_node(*args, **kwargs)



def _build_variable_assign_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.variable_assign_node import build_variable_assign_node
    return build_variable_assign_node(*args, **kwargs)



def _build_human_in_loop_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.human_in_loop_node import build_human_in_loop_node
    return build_human_in_loop_node(*args, **kwargs)


def _build_workflow_call_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.workflow_call_node import build_workflow_call_node
    return build_workflow_call_node(*args, **kwargs)



def _build_if_else_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.if_else_node import build_if_else_node
    return build_if_else_node(*args, **kwargs)



def _build_param_extractor_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.param_extractor_node import build_param_extractor_node
    return build_param_extractor_node(*args, **kwargs)



def _build_kr_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.knowledge_node import build_kr_node
    return build_kr_node(*args, **kwargs)



def _build_iteration_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.iteration_node import build_iteration_node
    return build_iteration_node(*args, **kwargs)



def _build_loop_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.loop_node import build_loop_node
    return build_loop_node(*args, **kwargs)


# ==================== DAG Compiler (Task 14.4) ====================


def _build_workflow_runner_nodes(
    *,
    dag_plan: Any,
    deps: Any,
) -> dict[str, Callable[[WorkflowState], dict]]:
    from app.assistant.workflow.engine.workflow_dag_assembler import _build_workflow_node_fn

    node_fns: dict[str, Callable[[WorkflowState], dict]] = {}
    for node_id in dag_plan.topo_order:
        node_type = dag_plan.type_map[node_id]
        node_cfg = dag_plan.node_map[node_id]
        node_fn = _build_workflow_node_fn(
            node_id=node_id,
            node_type=node_type,
            node_cfg=node_cfg,
            deps=deps,
        )
        node_fns[node_id] = _wrap_workflow_node_with_snapshot(node_id, node_type, node_cfg, node_fn)
    return node_fns


def _run_workflow_dag_without_langgraph(
    *,
    initial_state: dict[str, Any],
    dag_plan: Any,
    node_fns: dict[str, Callable[[WorkflowState], dict]],
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not dag_plan.start_node_id:
        raise ValueError("Workflow DAG has no start node")

    state = snapshot_graph_state(initial_state)
    state.setdefault("workflow_node_types", dict(dag_plan.type_map))
    state.setdefault("node_outputs", {})
    state.setdefault("execution_trace", [])
    state.setdefault("branch_decisions", {})

    in_degree: dict[str, int] = {node_id: 0 for node_id in dag_plan.node_map}
    for edge in dag_plan.edges_raw:
        target = str(edge.get("target_node_id", "") or "").strip()
        if target in in_degree:
            in_degree[target] = in_degree.get(target, 0) + 1

    queue: list[str] = [str(dag_plan.start_node_id)]
    executed_nodes: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in executed_nodes:
            continue
        node_fn = node_fns.get(current)
        if node_fn is None:
            continue

        result = node_fn(state) or {}
        if not isinstance(result, dict):
            result = {}
        state = merge_graph_state(state, result)
        executed_nodes.add(current)

        if callable(on_step):
            on_step(snapshot_graph_state(state))

        outgoing = dag_plan.out_edges.get(current, [])
        if not outgoing:
            continue

        node_type = str(dag_plan.type_map.get(current, "") or "")
        if node_type in {"if_else", "human_in_loop"}:
            decisions = state.get("branch_decisions", {}) if isinstance(state.get("branch_decisions"), dict) else {}
            chosen_handle = str(decisions.get(current, "else") or "else")
            if chosen_handle == "default":
                chosen_handle = "else"
            for target, handle, _condition in outgoing:
                normalized_handle = "else" if str(handle or "output") == "default" else str(handle or "output")
                if normalized_handle != chosen_handle:
                    continue
                in_degree[target] = max(0, in_degree.get(target, 0) - 1)
                if in_degree[target] == 0:
                    queue.append(target)
            continue

        for target, _handle, _condition in outgoing:
            in_degree[target] = max(0, in_degree.get(target, 0) - 1)
            if in_degree[target] == 0:
                queue.append(target)

    return state


def build_workflow_dag_subgraph(
    skill: SkillDefinition,
    nodes: list,
    edges: list,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
    execution_scope: WorkflowEngineExecutionScope | None = None,
) -> Any:
    """Compile a workflow DAG into a LangGraph StateGraph."""
    from langgraph.graph import END, StateGraph
    from app.assistant.workflow.engine.workflow_dag_assembler import (
        WorkflowNodeBuilderDeps,
        add_workflow_graph_edges,
        add_workflow_graph_nodes,
    )
    from app.assistant.workflow.engine.workflow_dag_plan import build_workflow_dag_plan
    from app.assistant.workflow.validation.validator import validate_workflow_compile

    dag_plan = build_workflow_dag_plan(
        nodes,
        edges,
        normalize_config=_normalize_config,
    )
    nodes_raw = dag_plan.nodes_raw
    edges_raw = dag_plan.edges_raw

    validation = validate_workflow_compile(
        nodes_raw,
        edges_raw,
        tool_names=set(tool_map.keys()),
        require_output_node=False,
    )
    if not validation.valid:
        msg = "; ".join(err.message for err in validation.errors[:5])
        raise ValueError(f"Invalid workflow DAG: {msg}")

    # Build graph
    graph = StateGraph(WorkflowState)
    builder_deps = WorkflowNodeBuilderDeps(
        llm=llm,
        args_llm=args_llm,
        tool_map=tool_map,
        db_bind=db_bind,
        node_llms=node_llms,
        execution_scope=execution_scope,
        build_start_node=_build_start_node,
        build_dag_llm_node=_build_dag_llm_node,
        build_dag_agent_node=_build_dag_agent_node,
        build_output_node=_build_output_node,
        build_dag_tool_node=_build_dag_tool_node,
        build_code_executor_node=_build_code_executor_node,
        build_http_request_node=_build_http_request_node,
        build_variable_assign_node=_build_variable_assign_node,
        build_human_in_loop_node=_build_human_in_loop_node,
        build_workflow_call_node=_build_workflow_call_node,
        build_if_else_node=_build_if_else_node,
        build_param_extractor_node=_build_param_extractor_node,
        build_kr_node=_build_kr_node,
        build_iteration_node=_build_iteration_node,
        build_loop_node=_build_loop_node,
    )
    runner_nodes = _build_workflow_runner_nodes(
        dag_plan=dag_plan,
        deps=builder_deps,
    )
    add_workflow_graph_nodes(
        graph=graph,
        dag_plan=dag_plan,
        deps=builder_deps,
        wrap_workflow_node_with_snapshot=_wrap_workflow_node_with_snapshot,
    )

    # Find start node
    start_nid = dag_plan.start_node_id
    if not start_nid:
        raise ValueError("Workflow DAG has no start node")

    graph.set_entry_point(start_nid)
    add_workflow_graph_edges(
        graph=graph,
        dag_plan=dag_plan,
        end_sentinel=END,
    )

    compiled = graph.compile()

    def _fallback_invoke(initial_state: dict[str, Any]) -> dict[str, Any]:
        return _run_workflow_dag_without_langgraph(
            initial_state=initial_state,
            dag_plan=dag_plan,
            node_fns=runner_nodes,
        )

    def _fallback_stream(initial_state: dict[str, Any]):
        emitted = False

        def _on_step(state: dict[str, Any]) -> None:
            nonlocal emitted
            emitted = True
            yield_states.append(state)

        yield_states: list[dict[str, Any]] = []
        final_state = _run_workflow_dag_without_langgraph(
            initial_state=initial_state,
            dag_plan=dag_plan,
            node_fns=runner_nodes,
            on_step=_on_step,
        )
        if not emitted:
            yield snapshot_graph_state(final_state)
            return
        for state in yield_states:
            yield state

    return adapt_graph_runnable(
        compiled=compiled,
        fallback_invoke=_fallback_invoke,
        fallback_stream=_fallback_stream,
    )


# ==================== Agent Loop Subgraph (Phase 3) ====================

_AGENT_MAX_ITERATIONS = 12


def _build_agent_node(*args, **kwargs):
    from app.assistant.workflow.engine.node_builders.agent_node import build_agent_node
    return build_agent_node(*args, **kwargs)

def build_agent_subgraph(
    skill: SkillDefinition,
    llm: ChatOpenAI,
    tools: list,
    db_bind: Any,
) -> Any:
    from app.assistant.workflow.engine.agent_subgraph import build_agent_subgraph as _build

    return _build(
        skill=skill,
        llm=llm,
        tools=tools,
        db_bind=db_bind,
        state_type=AssistantState,
    )


# ==================== LRU Graph Cache (Task 5.3) ====================

def _make_cache_key(skill: SkillDefinition, kb_enabled: bool, model: str) -> tuple:
    from app.assistant.workflow.engine.workflow_graph_cache import make_cache_key

    return make_cache_key(skill, kb_enabled, model)


def _get_or_compile_graph(
    key: tuple,
    compile_fn: Callable[[], Any],
) -> Any:
    from app.assistant.workflow.engine.workflow_graph_cache import get_or_compile_graph

    return get_or_compile_graph(key, compile_fn)


# ==================== LangGraph Engine (Tasks 5.1-5.2, 6.1-6.2, 7.1) ====================


class LangGraphEngine:
    """LangGraph 执行引擎入口。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        db: Session | None = None,
        execution_scope: WorkflowEngineExecutionScope | None = None,
    ):
        self.model = model
        self.db = db
        # execution_scope=None preserves exact Legacy ToolRegistry/component lookup.
        self.execution_scope = execution_scope
        self.llm = _build_chat_openai_client(
            api_key=api_key,
            base_url=base_url,
            model=model,
            streaming=True,
        )
        self.args_llm = _build_chat_openai_client(
            api_key=api_key,
            base_url=base_url,
            model=model,
            streaming=False,
            temperature=0,
        )
        self._tool_cache: dict[str, Any] = {}
        self._node_llm_cache: dict[str, tuple[tuple[str, str, str], ChatOpenAI]] = {}

    def _capability_scope_active(self) -> bool:
        return self.execution_scope is not None

    def _safe_diagnostics(self) -> bool:
        scope = self.execution_scope
        return bool(scope is not None and getattr(scope, "safe_diagnostics", False))

    def _get_tool(self, tool_name: str, *, source_locator: str | None = None) -> Any:
        if tool_name in self._tool_cache:
            return self._tool_cache[tool_name]

        scope = self.execution_scope
        if scope is not None:
            locator = source_locator or f"root/tool:{tool_name}"
            target = scope.dependency_resolver.require_tool(
                source_locator=locator,
                tool_name=tool_name,
            )
            tool = getattr(target, "tool_object_or_record", target)
            if tool:
                self._tool_cache[tool_name] = tool
            return tool

        from app.assistant_config.registry import ToolRegistry
        if self.db is not None:
            registry = ToolRegistry(self.db)
            tool = registry.resolve(tool_name)
        else:
            tool = ToolRegistry.resolve_system_tool(tool_name)
        if tool:
            self._tool_cache[tool_name] = tool
        return tool

    def _get_db_bind(self) -> Any:
        if self.db is not None:
            return self.db.get_bind()
        from app.database import engine
        return engine

    def _build_tools(self, skill: SkillDefinition) -> list:
        """构建工具列表，Task 6.1: agent_loop 模式下 kb_search 作为普通工具。"""
        tool_names = list(skill.tools or [])
        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))

        # agent_loop + kb_enabled: 将 kb_search 加入工具列表
        if skill.langgraph_pattern == "agent_loop" and kb_enabled:
            if "kb_search" not in tool_names:
                tool_names.append("kb_search")

        tools = []
        scope = self.execution_scope
        for name in tool_names:
            if scope is not None:
                # Exact closure only — never ToolRegistry name fallback.
                locator = f"root/tool:{name}"
                # Prefer node-scoped locators when present in skill tools list only as names.
                try:
                    tool = self._get_tool(name, source_locator=locator)
                except Exception:
                    # Try alternate root node locator patterns used by Plan 01 freezes,
                    # including nested body tools under iteration/loop containers.
                    found = None
                    for candidate in (
                        f"root/node:tool_0/tool:{name}",
                        f"root/node:{name}/tool:{name}",
                    ):
                        try:
                            found = self._get_tool(name, source_locator=candidate)
                            break
                        except Exception:
                            continue
                    if found is None:
                        # Scan skill workflow nodes (and nested container bodies) for
                        # matching tool_name / toolNames and try body-aware locators.
                        def _scan_nodes(nodes: Any, *, container_id: str | None = None) -> Any:
                            for node in nodes or []:
                                cfg = getattr(node, "config", None) or {}
                                if not isinstance(cfg, dict):
                                    # dict-shaped nodes from published input
                                    if isinstance(node, dict):
                                        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
                                    else:
                                        continue
                                node_id = str(
                                    getattr(node, "node_id", None)
                                    or (node.get("node_id") if isinstance(node, dict) else None)
                                    or (node.get("nodeId") if isinstance(node, dict) else None)
                                    or ""
                                ).strip()
                                node_type = str(
                                    getattr(node, "node_type", None)
                                    or (node.get("node_type") if isinstance(node, dict) else None)
                                    or (node.get("nodeType") if isinstance(node, dict) else None)
                                    or ""
                                ).strip()
                                candidates: list[str] = []
                                node_tool = str(cfg.get("tool_name", cfg.get("toolName", "")) or "").strip()
                                raw_tool_names = cfg.get("toolNames", cfg.get("tool_names"))
                                name_matches = node_tool == name
                                if not name_matches and isinstance(raw_tool_names, list):
                                    name_matches = any(
                                        isinstance(item, str) and item.strip() == name
                                        for item in raw_tool_names
                                    )
                                if name == "kb_search" and (
                                    node_type == "knowledge_retrieval"
                                    or (
                                        node_type == "agent"
                                        and bool(cfg.get("knowledgeEnabled", cfg.get("knowledge_enabled")))
                                    )
                                ):
                                    name_matches = True
                                if name_matches and node_id:
                                    if container_id:
                                        candidates.append(
                                            f"root/node:{container_id}/body/node:{node_id}/tool:{name}"
                                        )
                                    candidates.append(f"root/node:{node_id}/tool:{name}")
                                for candidate in candidates:
                                    try:
                                        return self._get_tool(name, source_locator=candidate)
                                    except Exception:
                                        continue
                                if node_type in {"iteration", "loop"} and node_id:
                                    body_nodes = cfg.get("bodyNodes", cfg.get("body_nodes"))
                                    if isinstance(body_nodes, list):
                                        nested = _scan_nodes(body_nodes, container_id=node_id)
                                        if nested is not None:
                                            return nested
                            return None

                        found = _scan_nodes(getattr(skill, "workflow_nodes", None) or [])
                    if found is None:
                        raise
                    tool = found
            else:
                tool = self._get_tool(name)
            if tool:
                tools.append(tool)
            else:
                if self._safe_diagnostics():
                    logger.warning("LangGraph tool not found tool_name=%s", name)
                else:
                    logger.warning("LangGraph tool not found: %s", name)
        return tools

    def _build_agent_system_prompt(self, skill: SkillDefinition, tool_names: list[str], *, locale: str | None) -> str:
        """Task 6.2: 构建 agent_loop 系统提示词，含 KB 引导。"""
        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))
        return _copy.build_agent_system_prompt(
            locale=locale,
            skill_name=skill.name,
            skill_description=skill.description,
            tool_names=tool_names,
            current_date=date.today(),
            base_prompt=skill.system_prompt or "",
            kb_enabled=kb_enabled,
        )

    def _resolve_node_custom_llm(self, model_id: str, *, node_id: str) -> ChatOpenAI:
        scope = self.execution_scope
        if scope is not None:
            requested = None
            try:
                from uuid import UUID as _UUID

                requested = _UUID(str(model_id))
            except Exception:
                requested = None
            locator_candidates = [
                f"root/node:{node_id}/model",
                f"root/node:{node_id.split('::')[-1]}/model" if "::" in str(node_id) else "",
                f"root/model",
            ]
            activated = None
            last_exc: BaseException | None = None
            for locator in locator_candidates:
                if not locator:
                    continue
                try:
                    activated = scope.dependency_resolver.require_model(
                        source_locator=locator,
                        requested_model_id=requested,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    continue
            if activated is None:
                if self._safe_diagnostics():
                    logger.error(
                        "capability_safe_execution stage=resolve_node_model node_id=%s exc_class=%s",
                        node_id,
                        type(last_exc).__name__ if last_exc else "-",
                    )
                raise RuntimeError(
                    f"Workflow node {node_id} references unavailable llm model under capability scope"
                )
            handle = getattr(activated, "client_or_credential_handle", None)
            if not isinstance(handle, dict):
                raise RuntimeError(
                    f"Workflow node {node_id} model credential handle invalid under capability scope"
                )
            api_key = str(handle.get("api_key") or "")
            base_url = str(handle.get("base_url") or "")
            model_name = str(
                handle.get("model_name") or getattr(activated, "model_name", None) or model_id
            )
            cache_key = f"scope:{model_id}:{base_url}:{model_name}"
            fingerprint = (base_url, model_name, api_key)
            cached = self._node_llm_cache.get(cache_key)
            if cached and cached[0] == fingerprint:
                return cached[1]
            node_llm = _build_chat_openai_client(
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                streaming=True,
            )
            self._node_llm_cache[cache_key] = (fingerprint, node_llm)
            return node_llm

        if self.db is None:
            raise RuntimeError(
                f"Workflow node {node_id} requires custom model {model_id}, but DB session is unavailable"
            )

        cfg = resolve_openai_compat_config_by_model_id(
            self.db,
            model_id=model_id,
            model_type="llm",
        )
        if cfg is None:
            raise RuntimeError(
                f"Workflow node {node_id} references unavailable llm model: {model_id}"
            )

        cache_key = str(cfg.model_id)
        fingerprint = (cfg.base_url, cfg.model, cfg.api_key)
        cached = self._node_llm_cache.get(cache_key)
        if cached and cached[0] == fingerprint:
            return cached[1]

        node_llm = _build_chat_openai_client(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            streaming=True,
        )
        self._node_llm_cache[cache_key] = (fingerprint, node_llm)
        return node_llm

    def _resolve_workflow_node_llms(self, skill: SkillDefinition) -> dict[str, ChatOpenAI]:
        from app.assistant.workflow.engine.workflow_node_llm_resolver import resolve_workflow_node_llms

        return resolve_workflow_node_llms(
            skill=skill,
            normalize_config=_normalize_config,
            normalize_container_body_nodes=_normalize_container_body_nodes,
            resolve_node_custom_llm=lambda model_id, node_id: self._resolve_node_custom_llm(model_id, node_id=node_id),
        )

    def _load_l1_summary(self, *, conversation_id_uuid: UUID | None) -> str:
        scope = self.execution_scope
        if scope is not None and not bool(getattr(scope, "allow_ambient_memory", False)):
            return ""
        if self.db is None or conversation_id_uuid is None:
            return ""
        try:
            from app.assistant.memory_service import AssistantMemoryService

            settings = get_settings()
            max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))
            memory_service = AssistantMemoryService(self.db)
            summary = memory_service.get_l1_summary(conversation_id_uuid)
            return memory_service.truncate_summary(summary, max_chars=max_chars)
        except Exception:
            if self._safe_diagnostics():
                logger.error(
                    "capability_safe_execution stage=memory_l1 conversation_id=%s",
                    conversation_id_uuid,
                )
            else:
                logger.exception("assistant memory l1 load failed conversation_id=%s", conversation_id_uuid)
            return ""

    def _load_l2_text(self, *, conversation_id_uuid: UUID | None, skill_name: str) -> tuple[str, list[str]]:
        scope = self.execution_scope
        if scope is not None and not bool(getattr(scope, "allow_ambient_memory", False)):
            return "", []
        if self.db is None or conversation_id_uuid is None:
            return "", []
        normalized_skill_name = str(skill_name or "").strip()
        if not normalized_skill_name:
            return "", []
        try:
            from app.assistant.memory_service import AssistantMemoryService

            settings = get_settings()
            max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))
            memory_service = AssistantMemoryService(self.db)
            facts = memory_service.get_l2_facts(conversation_id_uuid, normalized_skill_name)
            normalized = memory_service.normalize_l2_facts(facts, max_items=max_items)
            return memory_service.render_l2_text(normalized), normalized
        except Exception:
            if self._safe_diagnostics():
                logger.error(
                    "capability_safe_execution stage=memory_l2 conversation_id=%s skill=%s",
                    conversation_id_uuid,
                    normalized_skill_name,
                )
            else:
                logger.exception(
                    "assistant memory l2 load failed conversation_id=%s skill=%s",
                    conversation_id_uuid,
                    normalized_skill_name,
                )
            return "", []

    def _load_runtime_memory_overrides(
        self,
        *,
        raw_context: dict[str, Any],
    ) -> tuple[str | None, list[str] | None, dict[str, dict[str, Any]]]:
        scope = self.execution_scope
        if scope is not None and not bool(getattr(scope, "allow_ambient_memory", False)):
            return None, None, {}
        raw_override = raw_context.get("session_memory", raw_context.get("sessionMemory"))
        if not isinstance(raw_override, dict):
            return None, None, {}

        from app.assistant.memory_service import AssistantMemoryService

        settings = get_settings()
        l1_override: str | None = None
        l2_override: list[str] | None = None
        workflow_call_scope_overrides: dict[str, dict[str, Any]] = {}

        if "conversation_summary" in raw_override or "conversationSummary" in raw_override:
            max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))
            l1_override = AssistantMemoryService.truncate_summary(
                str(raw_override.get("conversation_summary", raw_override.get("conversationSummary", "")) or "").strip(),
                max_chars=max_chars,
            )

        if "skill_facts" in raw_override or "skillFacts" in raw_override:
            max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))
            l2_override = AssistantMemoryService.normalize_l2_facts(
                raw_override.get("skill_facts", raw_override.get("skillFacts", [])),
                max_items=max_items,
            )

        workflow_call_scope_overrides = AssistantMemoryService.normalize_workflow_call_scopes(
            raw_override.get("workflow_call_scopes", raw_override.get("workflowCallScopes", {})),
            max_chars=max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000)),
            max_items=max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20)),
        )

        return l1_override, l2_override, workflow_call_scope_overrides

    def execute(
        self,
        skill: SkillDefinition,
        user_input: str,
        history: list[dict],
        runtime_context: dict[str, Any] | None = None,
        on_tool_call_start: Callable | None = None,
        on_tool_call_end: Callable | None = None,
        on_analysis_start: Callable | None = None,
        on_analysis_delta: Callable | None = None,
        on_analysis_end: Callable | None = None,
        on_node_start: Callable | None = None,
        on_node_output_delta: Callable | None = None,
        on_node_end: Callable | None = None,
        on_branch_decision: Callable | None = None,
        on_node_snapshot: Callable | None = None,
        on_human_approval_requested: Callable[[dict[str, Any]], None] | None = None,
        on_human_approval_resolved: Callable[[dict[str, Any]], None] | None = None,
        cancel_checker: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """执行 LangGraph skill，yield 流式内容。"""
        logger.info("LangGraphEngine.execute: skill=%s pattern=%s",
                     skill.name, skill.langgraph_pattern)

        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))
        db_bind = self._get_db_bind()
        tools = self._build_tools(skill)
        tool_map = {getattr(t, "name", ""): t for t in tools}
        parsed_ctx = _exec_ctx.parse_execution_context(
            runtime_context=runtime_context,
            parse_output_boolean=_parse_output_boolean,
        )
        stream_output_enabled = parsed_ctx.stream_output_enabled
        structured_input = parsed_ctx.structured_input
        run_id = parsed_ctx.run_id
        channel_type = parsed_ctx.channel_type
        conversation_id_uuid = parsed_ctx.conversation_id_uuid
        message_id_uuid = parsed_ctx.message_id_uuid
        workflow_id_uuid = parsed_ctx.workflow_id_uuid
        skill_id_uuid = parsed_ctx.skill_id_uuid
        sys_vars = parsed_ctx.sys_vars

        # 构建回调 metadata：通过线程安全队列实时转发事件，避免节点内流式内容被整段缓存
        runtime_events: Queue[tuple[str, dict[str, Any]]] = Queue()
        event_handlers = _stream.RuntimeEventHandlers(
            on_tool_call_start=on_tool_call_start,
            on_tool_call_end=on_tool_call_end,
            on_analysis_start=on_analysis_start,
            on_analysis_delta=on_analysis_delta,
            on_analysis_end=on_analysis_end,
            on_node_start=on_node_start,
            on_node_output_delta=on_node_output_delta,
            on_node_end=on_node_end,
            on_branch_decision=on_branch_decision,
            on_node_snapshot=on_node_snapshot,
            on_human_approval_requested=on_human_approval_requested,
            on_human_approval_resolved=on_human_approval_resolved,
        )
        metadata, _push_runtime_event = _stream.build_runtime_metadata(
            runtime_events,
            handlers=event_handlers,
        )
        raw_session_memory = parsed_ctx.raw_context.get("session_memory", parsed_ctx.raw_context.get("sessionMemory"))
        if isinstance(raw_session_memory, dict):
            raw_scope_payload = raw_session_memory.get("workflow_call_scopes", raw_session_memory.get("workflowCallScopes"))
            if isinstance(raw_scope_payload, dict):
                metadata["workflow_call_session_scopes"] = raw_scope_payload

        _exec_services.attach_human_loop_runtime(
            db=self.db,
            db_bind=db_bind,
            metadata=metadata,
            run_id=run_id,
            channel_type=channel_type,
            conversation_id_uuid=conversation_id_uuid,
            workflow_id_uuid=workflow_id_uuid,
            skill_id_uuid=skill_id_uuid,
            message_id_uuid=message_id_uuid,
            emit=_emit,
            cancel_checker=cancel_checker,
        )

        # 构建初始消息
        messages = _exec_plan.build_initial_messages(
            history=history,
            user_input=user_input,
        )
        settings = get_settings()
        default_memory_mode = _rt.resolve_start_memory_mode(
            {},
            default_mode=str(getattr(settings, "assistant_memory_mode_default", "auto") or "auto"),
        )
        l0_turns = max(1, int(getattr(settings, "assistant_memory_l0_turns", 6) or 6))
        l0_max_chars = max(1, int(getattr(settings, "assistant_memory_l0_max_chars", 25000) or 25000))
        try:
            from app.assistant.orchestration.memory_context import build_l0_window

            l0_window = build_l0_window(
                history=history,
                user_input=user_input,
                turns_limit=l0_turns,
                chars_limit=l0_max_chars,
            )
        except Exception:
            logger.error(
                "assistant memory l0 build failed conversation_id=%s skill=%s",
                conversation_id_uuid,
                skill.name,
                exc_info=True,
            )
            l0_window = {
                "l0_text": "",
                "l0_messages": [],
                "l0_source_count": 0,
                "l0_trimmed_chars": 0,
            }
        l1_override, l2_facts_override, workflow_call_scope_overrides = self._load_runtime_memory_overrides(
            raw_context=parsed_ctx.raw_context
        )
        if l1_override is None:
            l1_text = self._load_l1_summary(conversation_id_uuid=conversation_id_uuid)
        else:
            l1_text = l1_override
        if l2_facts_override is None:
            l2_text_raw, l2_facts_raw = self._load_l2_text(
                conversation_id_uuid=conversation_id_uuid,
                skill_name=skill.name,
            )
            l2_text, l2_facts, l2_fact_count = _normalize_l2_memory_payload(l2_text_raw, l2_facts_raw)
        else:
            from app.assistant.memory_service import AssistantMemoryService

            l2_text_raw = AssistantMemoryService.render_l2_text(l2_facts_override)
            l2_text, l2_facts, l2_fact_count = _normalize_l2_memory_payload(l2_text_raw, l2_facts_override)
        l0_messages = l0_window.get("l0_messages")
        if not isinstance(l0_messages, list):
            l0_messages = []
        memory_context = {
            "l0_text": str(l0_window.get("l0_text", "") or ""),
            "l0_messages": l0_messages,
            "l0_source_count": int(l0_window.get("l0_source_count", 0) or 0),
            "l0_trimmed_chars": int(l0_window.get("l0_trimmed_chars", 0) or 0),
            "l1_text": l1_text,
            "l2_text": l2_text,
            "l2_facts": l2_facts,
            "workflow_call_scopes": workflow_call_scope_overrides,
        }
        logger.info(
            "assistant memory context prepared conversation_id=%s skill=%s l0_source_count=%s "
            "l0_message_count=%s l0_trimmed_chars=%s l0_chars=%s l1_chars=%s l2_chars=%s l2_count=%s",
            conversation_id_uuid,
            skill.name,
            memory_context["l0_source_count"],
            len(memory_context["l0_messages"]),
            memory_context["l0_trimmed_chars"],
            len(memory_context["l0_text"]),
            len(memory_context["l1_text"]),
            len(memory_context["l2_text"]),
            l2_fact_count,
        )

        # 根据 pattern 编译/获取图
        # Capability scope bypasses the global graph cache: the current key omits
        # binding/dependency digests while compiled nodes capture Tool/model objects.
        scope = self.execution_scope
        allow_global_cache = True
        if scope is not None:
            allow_global_cache = bool(getattr(scope, "allow_global_graph_cache", False))
        cache_key = _make_cache_key(skill, kb_enabled, self.model) if allow_global_cache else None

        pattern = skill.langgraph_pattern
        if pattern not in {"agent_loop", "workflow_dag"}:
            raise ValueError(
                f"Unsupported langgraph_pattern '{pattern}' for skill '{skill.name}'. "
                "Supported patterns: agent_loop, workflow_dag."
            )

        workflow_node_types: dict[str, str] = {}
        node_llms: dict[str, ChatOpenAI] = {}
        output_stream_source_node_id = ""
        memory_mode = default_memory_mode

        if pattern == "agent_loop":
            # 设置 system prompt
            tool_names = [getattr(t, "name", "") for t in tools]
            sys_prompt = self._build_agent_system_prompt(skill, tool_names, locale=parsed_ctx.locale)
            if memory_mode == "auto":
                memory_block = _rt.render_memory_injection_block(
                    memory_context=memory_context,
                    max_chars=max(
                        1,
                        int(getattr(settings, "assistant_memory_injection_max_chars", 30000) or 30000),
                    ),
                    locale=parsed_ctx.locale,
                )
                if memory_block:
                    sys_prompt = f"{sys_prompt}\n\n{memory_block}"
            messages[0] = SystemMessage(content=sys_prompt)

            if allow_global_cache and cache_key is not None:
                compiled = _get_or_compile_graph(
                    cache_key,
                    lambda: build_agent_subgraph(skill, self.llm, tools, db_bind),
                )
            else:
                compiled = build_agent_subgraph(skill, self.llm, tools, db_bind)
        elif pattern == "workflow_dag":
            wf_nodes = getattr(skill, "workflow_nodes", None) or []
            wf_edges = getattr(skill, "workflow_edges", None) or []
            for raw_node in wf_nodes:
                node_type = str(getattr(raw_node, "node_type", "") or "").strip().lower()
                if node_type != "start":
                    continue
                raw_cfg = getattr(raw_node, "config", None)
                normalized_cfg = _normalize_config(raw_cfg) if isinstance(raw_cfg, dict) else {}
                memory_mode = _rt.resolve_start_memory_mode(
                    normalized_cfg,
                    default_mode=default_memory_mode,
                )
                break
            node_llms, workflow_node_types, output_stream_source_node_id = (
                _exec_plan.resolve_workflow_runtime_context(
                    skill_name=skill.name,
                    workflow_nodes=wf_nodes,
                    normalize_config=_normalize_config,
                    extract_single_template_reference=_extract_single_template_reference,
                    resolve_workflow_node_llms=lambda: self._resolve_workflow_node_llms(skill),
                )
            )

            def _compile_workflow_dag():
                return build_workflow_dag_subgraph(
                    skill, wf_nodes, wf_edges,
                    self.llm, self.args_llm, tool_map, db_bind,
                    node_llms=node_llms,
                    execution_scope=self.execution_scope,
                )

            if allow_global_cache and cache_key is not None:
                compiled = _get_or_compile_graph(cache_key, _compile_workflow_dag)
            else:
                compiled = _compile_workflow_dag()
        initial_state: dict[str, Any] = _exec_plan.build_initial_state(
            pattern=pattern,
            messages=messages,
            skill_name=skill.name,
            workflow_id=getattr(skill, "workflow_id", None),
            workflow_version_id=getattr(skill, "workflow_version_id", None),
            user_input=user_input,
            kb_enabled=kb_enabled,
            memory_mode=memory_mode,
            metadata=metadata,
            sys_vars=sys_vars,
            workflow_node_types=workflow_node_types,
            node_llms=node_llms,
            stream_output_enabled=stream_output_enabled,
            output_stream_source_node_id=output_stream_source_node_id,
            structured_input=structured_input,
            memory_context=memory_context,
        )

        # 执行图
        try:
            yield from _stream.run_graph_stream(
                compiled=compiled,
                initial_state=initial_state,
                runtime_events=runtime_events,
                push_runtime_event=_push_runtime_event,
                handlers=event_handlers,
                stream_output_enabled=stream_output_enabled,
                cancel_checker=cancel_checker,
            )
        except Exception as e:
            if self._safe_diagnostics():
                logger.error(
                    "capability_safe_execution stage=langgraph_execute skill=%s exc_class=%s",
                    skill.name,
                    type(e).__name__,
                )
            else:
                logger.error(
                    "LangGraph execution failed: skill=%s error=%s",
                    skill.name,
                    e,
                    exc_info=True,
                )
            raise
