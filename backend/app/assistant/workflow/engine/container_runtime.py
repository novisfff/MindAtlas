from __future__ import annotations

from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine import runtime_helpers as rt
from app.assistant.workflow.engine.runtime_helpers import cfg_list_value, stringify
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState


def normalize_container_body_nodes(
    node_cfg: dict[str, Any],
    *,
    normalize_config: Callable[[dict | None], dict] | None = None,
) -> list[dict[str, Any]]:
    raw_nodes = cfg_list_value(node_cfg, "body_nodes", "bodyNodes")
    nodes: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("node_id", raw.get("nodeId", "")) or "").strip()
        node_type = str(raw.get("node_type", raw.get("nodeType", "")) or "").strip()
        if not node_id or not node_type:
            continue
        cfg = raw.get("config")
        normalized_cfg = normalize_config(cfg) if callable(normalize_config) and isinstance(cfg, dict) else (cfg if isinstance(cfg, dict) else {})
        nodes.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "label": str(raw.get("label", "") or node_id),
                "config": normalized_cfg,
            }
        )

    if not nodes:
        nodes = [
            {
                "node_id": "start",
                "node_type": "start",
                "label": "start",
                "config": {},
            }
        ]

    if not any(node.get("node_type") == "start" for node in nodes):
        nodes.insert(
            0,
            {
                "node_id": "start",
                "node_type": "start",
                "label": "start",
                "config": {},
            },
        )

    return nodes


def normalize_container_body_edges(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_edges = cfg_list_value(node_cfg, "body_edges", "bodyEdges")
    edges: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source_node_id", raw.get("sourceNodeId", "")) or "").strip()
        target = str(raw.get("target_node_id", raw.get("targetNodeId", "")) or "").strip()
        if not source or not target:
            continue
        edges.append(
            {
                "source_node_id": source,
                "target_node_id": target,
                "source_handle": str(raw.get("source_handle", raw.get("sourceHandle", "output")) or "output"),
                "target_handle": str(raw.get("target_handle", raw.get("targetHandle", "input")) or "input"),
                "condition_expr": raw.get("condition_expr", raw.get("conditionExpr")),
            }
        )
    return edges


def _build_container_start_node(container_input: Any, container_fields: dict[str, Any]) -> Callable[[WorkflowState], dict]:
    def start_node(_state: WorkflowState) -> dict:
        text = stringify(container_input)
        return {
            "node_outputs": {
                "start": NodeOutput(
                    status="ok",
                    text=text,
                    raw=container_input,
                    json_fields={
                        "user_input": container_input,
                        **container_fields,
                    },
                )
            },
            "execution_trace": ["start"],
        }

    return start_node


def build_scoped_metadata(
    scope_prefix: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_metadata = metadata if isinstance(metadata, dict) else {}
    scoped = dict(raw_metadata)
    inherited_scope_prefix = str(raw_metadata.get("__scope_prefix__", "") or "").strip()
    effective_scope_prefix = scope_prefix if not inherited_scope_prefix else f"{inherited_scope_prefix}::{scope_prefix}"
    scoped["__scope_prefix__"] = effective_scope_prefix

    def _scoped_node_id(node_id: str) -> str:
        raw = str(node_id or "").strip()
        prefix = f"{scope_prefix}::"
        if not raw:
            return raw
        if raw.startswith(prefix):
            return raw
        return f"{prefix}{raw}"

    on_node_start = raw_metadata.get("on_node_start")
    if callable(on_node_start):
        def _wrapped_node_start(*, node_id: str, node_type: str, _cb=on_node_start, **extra: Any) -> None:
            rt.invoke_callback(_cb, node_id=_scoped_node_id(node_id), node_type=node_type, **extra)
        scoped["on_node_start"] = _wrapped_node_start

    on_node_output_delta = raw_metadata.get("on_node_output_delta")
    if callable(on_node_output_delta):
        def _wrapped_node_output_delta(*, node_id: str, delta: str, _cb=on_node_output_delta, **extra: Any) -> None:
            rt.invoke_callback(_cb, node_id=_scoped_node_id(node_id), delta=delta, **extra)
        scoped["on_node_output_delta"] = _wrapped_node_output_delta

    on_node_end = raw_metadata.get("on_node_end")
    if callable(on_node_end):
        def _wrapped_node_end(*, node_id: str, status: str, _cb=on_node_end, **extra: Any) -> None:
            rt.invoke_callback(_cb, node_id=_scoped_node_id(node_id), status=status, **extra)
        scoped["on_node_end"] = _wrapped_node_end

    on_branch_decision = raw_metadata.get("on_branch_decision")
    if callable(on_branch_decision):
        def _wrapped_branch_decision(*, node_id: str, handle: str, _cb=on_branch_decision, **extra: Any) -> None:
            rt.invoke_callback(_cb, node_id=_scoped_node_id(node_id), handle=handle, **extra)
        scoped["on_branch_decision"] = _wrapped_branch_decision

    on_node_snapshot = raw_metadata.get("on_node_snapshot")
    if callable(on_node_snapshot):
        def _wrapped_node_snapshot(
            *,
            node_id: str,
            node_type: str,
            status: str,
            input: Any,
            output: Any,
            error_message: str | None = None,
            hard_truncated: bool = False,
            _cb=on_node_snapshot,
            **extra: Any,
        ) -> None:
            rt.invoke_callback(
                _cb,
                node_id=_scoped_node_id(node_id),
                node_type=node_type,
                status=status,
                input=input,
                output=output,
                error_message=error_message,
                hard_truncated=hard_truncated,
                **extra,
            )
        scoped["on_node_snapshot"] = _wrapped_node_snapshot

    on_tool_call_start = raw_metadata.get("on_tool_call_start")
    if callable(on_tool_call_start):
        def _wrapped_tool_call_start(
            *,
            tool_call_id: str,
            tool_name: str,
            args: Any,
            _cb=on_tool_call_start,
            **extra: Any,
        ) -> None:
            next_extra = dict(extra)
            node_id = str(next_extra.get("node_id", "") or "").strip()
            if node_id:
                next_extra["node_id"] = _scoped_node_id(node_id)
            rt.invoke_callback(_cb, tool_call_id=tool_call_id, tool_name=tool_name, args=args, **next_extra)
        scoped["on_tool_call_start"] = _wrapped_tool_call_start

    on_tool_call_end = raw_metadata.get("on_tool_call_end")
    if callable(on_tool_call_end):
        def _wrapped_tool_call_end(
            *,
            tool_call_id: str,
            status: str,
            result: Any,
            _cb=on_tool_call_end,
            **extra: Any,
        ) -> None:
            next_extra = dict(extra)
            node_id = str(next_extra.get("node_id", "") or "").strip()
            if node_id:
                next_extra["node_id"] = _scoped_node_id(node_id)
            rt.invoke_callback(_cb, tool_call_id=tool_call_id, status=status, result=result, **next_extra)
        scoped["on_tool_call_end"] = _wrapped_tool_call_end

    on_human_approval_requested = raw_metadata.get("on_human_approval_requested")
    if callable(on_human_approval_requested):
        def _wrapped_human_approval_requested(*, payload: dict[str, Any], _cb=on_human_approval_requested) -> None:
            next_payload = dict(payload) if isinstance(payload, dict) else {}
            node_id = str(next_payload.get("nodeId", "") or "")
            if node_id:
                next_payload["nodeId"] = _scoped_node_id(node_id)
            _cb(payload=next_payload)
        scoped["on_human_approval_requested"] = _wrapped_human_approval_requested

    on_human_approval_resolved = raw_metadata.get("on_human_approval_resolved")
    if callable(on_human_approval_resolved):
        def _wrapped_human_approval_resolved(*, payload: dict[str, Any], _cb=on_human_approval_resolved) -> None:
            next_payload = dict(payload) if isinstance(payload, dict) else {}
            node_id = str(next_payload.get("nodeId", "") or "")
            if node_id:
                next_payload["nodeId"] = _scoped_node_id(node_id)
            _cb(payload=next_payload)
        scoped["on_human_approval_resolved"] = _wrapped_human_approval_resolved

    # Legacy blocking HITL is fail-closed (attach_human_loop_runtime is a no-op).
    # If a caller still injects human_loop_runtime (e.g. unit FakeRuntime), pass it
    # through unscoped — ScopedHumanLoopRuntimeProxy was removed as dead complexity.
    human_loop_runtime = raw_metadata.get("human_loop_runtime")
    if human_loop_runtime is not None:
        scoped["human_loop_runtime"] = human_loop_runtime

    return scoped


def _build_container_scoped_metadata(
    container_node_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    return build_scoped_metadata(container_node_id, metadata)


def execute_container_body(
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
    execution_scope: Any | None = None,
) -> dict[str, Any]:
    from app.assistant.workflow.engine import engine as engine_runtime

    body_nodes = normalize_container_body_nodes(node_cfg, normalize_config=engine_runtime._normalize_config)
    body_edges = normalize_container_body_edges(node_cfg)
    body_node_map = {str(node["node_id"]): node for node in body_nodes}
    body_type_map = {str(node["node_id"]): str(node["node_type"]) for node in body_nodes}

    if "start" not in body_node_map:
        raise RuntimeError(f"{container_node_type} node {container_node_id} body has no start node")

    out_edges: dict[str, list[tuple[str, str]]] = {}
    in_degree: dict[str, int] = {node_id: 0 for node_id in body_node_map}
    for edge in body_edges:
        src = str(edge["source_node_id"])
        tgt = str(edge["target_node_id"])
        if src not in body_node_map or tgt not in body_node_map:
            continue
        out_edges.setdefault(src, []).append((tgt, str(edge.get("source_handle", "output") or "output")))
        in_degree[tgt] = in_degree.get(tgt, 0) + 1

    metadata = _build_container_scoped_metadata(
        container_node_id,
        parent_state.get("metadata", {}) or {},
    )
    sys_vars = parent_state.get("sys_vars", {}) or {}
    runtime_node_llms_raw = parent_state.get("node_llms", {}) or {}
    runtime_node_llms: dict[str, Any]
    if isinstance(runtime_node_llms_raw, dict):
        runtime_node_llms = dict(runtime_node_llms_raw)
    else:
        runtime_node_llms = {}
    scoped_node_llms: dict[str, Any] = dict(node_llms or {})
    env_vars_local = dict(parent_state.get("env_vars", {}) or {})
    env_specs_raw = parent_state.get("env_specs", {}) or {}
    env_specs_local = dict(env_specs_raw) if isinstance(env_specs_raw, dict) else {}

    container_ctx = dict(container_fields or {})
    start_result = _build_container_start_node(container_input, container_ctx)({})
    node_outputs_local: dict[str, NodeOutput] = dict(parent_state.get("node_outputs", {}))
    node_outputs_local.update(start_result.get("node_outputs", {}))
    node_outputs_local["container"] = NodeOutput(
        status="ok",
        text=stringify(container_ctx),
        raw=container_ctx,
        json_fields=container_ctx,
    )

    execution_trace: list[str] = ["start"]
    branch_decisions: dict[str, str] = {}

    queue: list[str] = []
    for target, _ in out_edges.get("start", []):
        in_degree[target] = max(0, in_degree.get(target, 0) - 1)
        if in_degree[target] == 0:
            queue.append(target)

    executed_nodes = {"start"}

    while queue:
        current = queue.pop(0)
        if current in executed_nodes:
            continue
        node_meta = body_node_map.get(current)
        if not node_meta:
            continue
        node_type = body_type_map.get(current, "")
        cfg = node_meta.get("config") if isinstance(node_meta.get("config"), dict) else {}
        cfg = dict(cfg)
        cfg.setdefault("__node_label", str(node_meta.get("label", "") or current))
        scoped_model_key = f"{container_node_id}::{current}"
        if scoped_model_key in runtime_node_llms:
            runtime_node_llms[current] = runtime_node_llms[scoped_model_key]
        if scoped_model_key in scoped_node_llms:
            scoped_node_llms[current] = scoped_node_llms[scoped_model_key]

        state_for_node: WorkflowState = {
            "metadata": metadata,
            "node_outputs": node_outputs_local,
            "workflow_id": str(parent_state.get("workflow_id", "") or ""),
            "workflow_version_id": str(parent_state.get("workflow_version_id", "") or ""),
            "user_input": stringify(container_input),
            "memory_mode": str(parent_state.get("memory_mode", "auto") or "auto"),
            "memory_context": parent_state.get("memory_context", {}),
            "sys_vars": sys_vars,
            "workflow_node_types": body_type_map,
            "node_llms": runtime_node_llms,
            "stream_output_enabled": bool(parent_state.get("stream_output_enabled", True)),
            "output_stream_source_node_id": str(parent_state.get("output_stream_source_node_id", "") or ""),
            "env_vars": env_vars_local,
            "env_specs": env_specs_local,
        }

        # Body builders need the container id so capability-scope locators match
        # Plan 01 freezes: root/node:{container}/body/node:{child}/...
        cfg["__container_node_id"] = container_node_id

        if node_type == "llm":
            node_fn = engine_runtime._build_dag_llm_node(
                current, cfg, llm, node_llms=scoped_node_llms, execution_scope=execution_scope
            )
        elif node_type == "agent":
            try:
                node_fn = engine_runtime._build_dag_agent_node(
                    current,
                    cfg,
                    llm,
                    tool_map,
                    db_bind,
                    node_llms=scoped_node_llms,
                    execution_scope=execution_scope,
                    container_node_id=container_node_id,
                )
            except TypeError:
                node_fn = engine_runtime._build_dag_agent_node(
                    current,
                    cfg,
                    llm,
                    tool_map,
                    db_bind,
                    node_llms=scoped_node_llms,
                    execution_scope=execution_scope,
                )
        elif node_type == "tool":
            try:
                node_fn = engine_runtime._build_dag_tool_node(
                    current,
                    cfg,
                    tool_map,
                    args_llm,
                    db_bind,
                    execution_scope=execution_scope,
                    container_node_id=container_node_id,
                )
            except TypeError:
                node_fn = engine_runtime._build_dag_tool_node(
                    current, cfg, tool_map, args_llm, db_bind, execution_scope=execution_scope
                )
        elif node_type == "if_else":
            node_fn = engine_runtime._build_if_else_node(current, cfg)
        elif node_type == "parameter_extractor":
            node_fn = engine_runtime._build_param_extractor_node(
                current, cfg, llm, node_llms=scoped_node_llms, execution_scope=execution_scope
            )
        elif node_type == "knowledge_retrieval":
            try:
                node_fn = engine_runtime._build_kr_node(
                    current,
                    cfg,
                    tool_map,
                    db_bind,
                    execution_scope=execution_scope,
                    container_node_id=container_node_id,
                )
            except TypeError:
                node_fn = engine_runtime._build_kr_node(
                    current, cfg, tool_map, db_bind, execution_scope=execution_scope
                )
        elif node_type == "code_executor":
            node_fn = engine_runtime._build_code_executor_node(
                current, cfg, execution_scope=execution_scope
            )
        elif node_type == "http_request":
            node_fn = engine_runtime._build_http_request_node(
                current, cfg, execution_scope=execution_scope
            )
        elif node_type == "variable_assign":
            node_fn = engine_runtime._build_variable_assign_node(current, cfg)
        elif node_type == "human_in_loop":
            node_fn = engine_runtime._build_human_in_loop_node(
                current, cfg, execution_scope=execution_scope
            )
        elif node_type == "workflow_call":
            try:
                node_fn = engine_runtime._build_workflow_call_node(
                    current,
                    cfg,
                    llm,
                    args_llm,
                    tool_map,
                    db_bind,
                    execution_scope=execution_scope,
                    container_node_id=container_node_id,
                )
            except TypeError:
                node_fn = engine_runtime._build_workflow_call_node(
                    current,
                    cfg,
                    llm,
                    args_llm,
                    tool_map,
                    db_bind,
                    execution_scope=execution_scope,
                )
        elif node_type == "start":
            node_fn = _build_container_start_node(container_input, container_ctx)
        else:
            raise RuntimeError(
                f"{container_node_type} node {container_node_id} body node {current} has unsupported type: {node_type}"
            )

        node_fn = engine_runtime._wrap_workflow_node_with_snapshot(current, node_type, cfg, node_fn)
        result = node_fn(state_for_node)
        if isinstance(result.get("node_outputs"), dict):
            node_outputs_local.update(result["node_outputs"])
        if isinstance(result.get("execution_trace"), list):
            execution_trace.extend([str(item) for item in result["execution_trace"]])
        if isinstance(result.get("branch_decisions"), dict):
            branch_decisions.update({str(k): str(v) for k, v in result["branch_decisions"].items()})
        if isinstance(result.get("memory_context"), dict):
            parent_state["memory_context"] = result["memory_context"]
        if isinstance(result.get("env_vars"), dict):
            env_vars_local = dict(result["env_vars"])
        if isinstance(result.get("env_specs"), dict):
            env_specs_local = dict(result["env_specs"])

        executed_nodes.add(current)

        outgoing = out_edges.get(current, [])
        if not outgoing:
            continue
        if node_type in {"if_else", "human_in_loop"}:
            chosen = branch_decisions.get(current)
            for target, handle in outgoing:
                normalized_handle = "else" if handle == "default" else handle
                if normalized_handle != chosen:
                    continue
                in_degree[target] = max(0, in_degree.get(target, 0) - 1)
                if in_degree[target] == 0:
                    queue.append(target)
            continue

        for target, _ in outgoing:
            in_degree[target] = max(0, in_degree.get(target, 0) - 1)
            if in_degree[target] == 0:
                queue.append(target)

    produced = {
        node_id: out
        for node_id, out in node_outputs_local.items()
        if node_id in body_node_map and node_id != "start"
    }
    terminal_nodes = [
        node_id
        for node_id in produced.keys()
        if len(out_edges.get(node_id, [])) == 0
    ]
    last_terminal = terminal_nodes[-1] if terminal_nodes else (list(produced.keys())[-1] if produced else "start")
    return {
        "node_outputs": produced,
        "all_node_outputs": node_outputs_local,
        "last_node_id": last_terminal,
        "execution_trace": execution_trace,
        "env_vars": env_vars_local,
        "env_specs": env_specs_local,
    }
