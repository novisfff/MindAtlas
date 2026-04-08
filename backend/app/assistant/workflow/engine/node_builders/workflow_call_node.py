from __future__ import annotations

from typing import Any, Callable

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import sessionmaker

from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skill_catalog.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.assistant.workflow.engine.container_runtime import build_scoped_metadata
from app.assistant.workflow.engine.runtime_helpers import (
    extract_single_template_reference,
    get_start_inputs,
    resolve_node_template_vars,
    stringify,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.assistant.workflow.engine.workflow_node_llm_resolver import (
    resolve_workflow_node_llms_from_nodes,
)
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.service import AssistantConfigService


def _normalize_binding_mode(value: Any) -> str:
    binding_mode = str(value or "pinned").strip().lower()
    if binding_mode in {"pinned", "latest"}:
        return binding_mode
    return "pinned"


def _resolve_workflow_call_input_payload(
    *,
    node_cfg: dict[str, Any],
    state: WorkflowState,
    contract_input_fields: list[Any],
) -> dict[str, Any]:
    node_outputs = dict(state.get("node_outputs", {}))
    start_inputs = get_start_inputs(node_outputs)
    sys_vars = state.get("sys_vars", {}) or {}
    env_vars = state.get("env_vars", {}) or {}

    input_bindings = node_cfg.get("input_bindings", node_cfg.get("inputBindings"))
    bindings = input_bindings if isinstance(input_bindings, dict) else {}

    def _resolve_binding_value(raw_template: str) -> Any:
        single_ref = extract_single_template_reference(raw_template)
        if single_ref is None:
            return resolve_node_template_vars(
                raw_template,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )

        ref_node_id, ref_field = single_ref
        if ref_node_id == "start":
            return start_inputs.get(ref_field, "")
        if ref_node_id == "sys":
            return sys_vars.get(ref_field, "")
        if ref_node_id == "env":
            return env_vars.get(ref_field, "")
        if ref_node_id == "container":
            container_out = node_outputs.get("container", {}) if isinstance(node_outputs.get("container", {}), dict) else {}
            container_json = container_out.get("json_fields", {}) if isinstance(container_out.get("json_fields"), dict) else {}
            return container_json.get(ref_field, container_out.get("text", ""))

        out = node_outputs.get(ref_node_id)
        if not isinstance(out, dict):
            return ""
        if ref_field == "text":
            return out.get("text", "")
        if ref_field == "raw":
            return out.get("raw")
        json_fields = out.get("json_fields", {})
        if isinstance(json_fields, dict) and ref_field in json_fields:
            return json_fields.get(ref_field)
        return out.get("text", "")

    payload: dict[str, Any] = {}
    for field in contract_input_fields:
        field_name = str(getattr(field, "name", "") or "").strip()
        if not field_name:
            continue
        raw_binding = bindings.get(field_name, "")
        if isinstance(raw_binding, str):
            payload[field_name] = _resolve_binding_value(raw_binding)
        elif raw_binding is None:
            payload[field_name] = ""
        else:
            payload[field_name] = raw_binding
    return payload


def _build_child_skill_definition(
    *,
    workflow_name: str,
    workflow_description: str,
    workflow_input: Any,
    tool_names: set[str],
) -> SkillDefinition:
    workflow_nodes = [
        WorkflowNodeDefinition(
            node_id=node.node_id,
            node_type=node.node_type,
            label=node.label,
            position_x=node.position_x,
            position_y=node.position_y,
            config=node.config or {},
        )
        for node in workflow_input.nodes
    ]

    workflow_edges: list[WorkflowEdgeDefinition] = []
    for edge in workflow_input.edges:
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

    return SkillDefinition(
        name=f"{workflow_name}__workflow_call",
        description=workflow_description or "",
        intent_examples=[],
        tools=sorted(tool_names),
        mode="langgraph",
        langgraph_pattern="workflow_dag",
        kb=SkillKBConfig(enabled=False),
        workflow_nodes=workflow_nodes,
        workflow_edges=workflow_edges,
    )


def build_workflow_call_node(
    node_id: str,
    node_cfg: dict[str, Any],
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
) -> Callable[[WorkflowState], dict]:
    def workflow_call_node(state: WorkflowState) -> dict:
        from app.assistant.workflow.engine import engine as engine_runtime
        from app.assistant.workflow.engine.execution_plan import (
            build_initial_messages,
            build_initial_state,
            resolve_workflow_runtime_context,
        )

        target_workflow_id = AssistantConfigService._parse_uuid_value(
            AssistantConfigService._cfg_get(
                node_cfg,
                "target_workflow_id",
                "targetWorkflowId",
                default=None,
            )
        )
        if target_workflow_id is None:
            raise RuntimeError(f"DAG workflow_call node {node_id}: targetWorkflowId is required")

        binding_mode = _normalize_binding_mode(
            AssistantConfigService._cfg_get(node_cfg, "binding_mode", "bindingMode", default="pinned")
        )
        target_version_id = AssistantConfigService._parse_uuid_value(
            AssistantConfigService._cfg_get(
                node_cfg,
                "target_published_version_id",
                "targetPublishedVersionId",
                default=None,
            )
        )

        session_factory = sessionmaker(bind=db_bind)
        with session_factory() as session:
            config_service = AssistantConfigService(session)
            resolved = config_service._resolve_workflow_call_target(
                target_workflow_id=target_workflow_id,
                binding_mode=binding_mode,
                target_published_version_id=target_version_id,
            )

            metadata = state.get("metadata", {}) if isinstance(state.get("metadata", {}), dict) else {}
            runtime_stack = [str(item) for item in metadata.get("__workflow_call_stack__", [])] if isinstance(metadata, dict) else []
            if str(resolved.workflow.id) in runtime_stack:
                raise RuntimeError(
                    f"DAG workflow_call node {node_id}: recursive workflow call detected for {resolved.workflow.name}"
                )

            structured_input = _resolve_workflow_call_input_payload(
                node_cfg=node_cfg,
                state=state,
                contract_input_fields=resolved.contract.input_fields,
            )

            child_tool_names = config_service._collect_workflow_tool_names(resolved.workflow_input.nodes)
            child_tool_map: dict[str, Any] = dict(tool_map)
            if child_tool_names:
                registry = ToolRegistry(session)
                for tool_name in child_tool_names:
                    if tool_name in child_tool_map:
                        continue
                    tool = registry.resolve(tool_name)
                    if tool is None:
                        raise RuntimeError(
                            f"DAG workflow_call node {node_id}: child workflow tool not found: {tool_name}"
                        )
                    child_tool_map[tool_name] = tool

            default_headers = build_openai_compat_client_headers()

            def _resolve_child_node_llm(model_id: str, runtime_key: str) -> ChatOpenAI:
                cfg = resolve_openai_compat_config_by_model_id(
                    session,
                    model_id=model_id,
                    model_type="llm",
                )
                if cfg is None:
                    raise RuntimeError(
                        f"DAG workflow_call node {node_id}: child workflow node {runtime_key} references unavailable llm model: {model_id}"
                    )
                return ChatOpenAI(
                    api_key=cfg.api_key,
                    base_url=cfg.base_url,
                    model=cfg.model,
                    streaming=True,
                    default_headers=default_headers,
                )

            child_skill = _build_child_skill_definition(
                workflow_name=resolved.workflow.name,
                workflow_description=resolved.workflow.description or "",
                workflow_input=resolved.workflow_input,
                tool_names=child_tool_names,
            )
            child_node_llms = resolve_workflow_node_llms_from_nodes(
                workflow_nodes=child_skill.workflow_nodes,
                normalize_config=engine_runtime._normalize_config,
                normalize_container_body_nodes=engine_runtime._normalize_container_body_nodes,
                resolve_node_custom_llm=_resolve_child_node_llm,
            )
            child_node_llms, workflow_node_types, output_stream_source_node_id = resolve_workflow_runtime_context(
                skill_name=child_skill.name,
                workflow_nodes=child_skill.workflow_nodes,
                normalize_config=engine_runtime._normalize_config,
                extract_single_template_reference=extract_single_template_reference,
                resolve_workflow_node_llms=lambda: child_node_llms,
            )

            scoped_metadata = build_scoped_metadata(node_id, metadata)
            scoped_metadata["__workflow_call_stack__"] = [*runtime_stack, str(resolved.workflow.id)]
            scoped_metadata.pop("on_content_delta", None)

            initial_state = build_initial_state(
                pattern="workflow_dag",
                messages=build_initial_messages(history=[], user_input=""),
                skill_name=child_skill.name,
                user_input="",
                kb_enabled=False,
                memory_mode=str(state.get("memory_mode", "auto") or "auto"),
                metadata=scoped_metadata,
                sys_vars=state.get("sys_vars", {}) or {},
                workflow_node_types=workflow_node_types,
                node_llms=child_node_llms,
                stream_output_enabled=False,
                output_stream_source_node_id=output_stream_source_node_id,
                structured_input=structured_input,
                memory_context=state.get("memory_context", {}) if isinstance(state.get("memory_context"), dict) else {},
            )

            compiled = engine_runtime.build_workflow_dag_subgraph(
                child_skill,
                child_skill.workflow_nodes,
                child_skill.workflow_edges,
                llm,
                args_llm,
                child_tool_map,
                db_bind,
                node_llms=child_node_llms,
            )
            runnable = compiled.compile() if hasattr(compiled, "compile") and not hasattr(compiled, "invoke") else compiled
            if not hasattr(runnable, "invoke"):
                raise RuntimeError(
                    f"DAG workflow_call node {node_id}: child workflow graph is not invokable"
                )
            final_state = runnable.invoke(initial_state)

        if not isinstance(final_state, dict):
            raise RuntimeError(f"DAG workflow_call node {node_id}: child workflow returned invalid state")

        child_node_outputs = final_state.get("node_outputs", {})
        if not isinstance(child_node_outputs, dict):
            child_node_outputs = {}
        execution_trace = final_state.get("execution_trace", [])
        if not isinstance(execution_trace, list):
            execution_trace = []

        child_output_node_id = next(
            (
                str(trace_node_id)
                for trace_node_id in reversed(execution_trace)
                if workflow_node_types.get(str(trace_node_id)) == "output"
            ),
            "",
        )
        if not child_output_node_id:
            raise RuntimeError(f"DAG workflow_call node {node_id}: child workflow did not execute an output node")

        child_output = child_node_outputs.get(child_output_node_id)
        if not isinstance(child_output, dict):
            raise RuntimeError(f"DAG workflow_call node {node_id}: child workflow output payload is missing")

        child_json_fields = child_output.get("json_fields") if isinstance(child_output.get("json_fields"), dict) else {}
        response_value = child_json_fields.get("response", child_output.get("text", ""))
        node_out = NodeOutput(
            status="ok",
            text=stringify(response_value),
            raw=child_output.get("raw", response_value),
            json_fields={
                "response": response_value,
                **child_json_fields,
            },
        )

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return workflow_call_node
