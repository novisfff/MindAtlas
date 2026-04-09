from __future__ import annotations

import logging
from typing import Any, Callable
from uuid import UUID

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import sessionmaker

from app.ai_registry.runtime import (
    resolve_openai_compat_config,
    resolve_openai_compat_config_by_model_id,
)
from app.assistant.memory_computation import AssistantMemoryComputationService
from app.assistant.memory_service import AssistantMemoryService
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.orchestration.openai_fallback_client import OpenAiFallbackConfig
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
from app.config import get_settings


logger = logging.getLogger(__name__)


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


def _workflow_call_source_node_scope(node_id: str, metadata: dict[str, Any] | None) -> str:
    scope_prefix = str((metadata or {}).get("__scope_prefix__", "") or "").strip()
    raw_node_id = str(node_id or "").strip()
    if not scope_prefix:
        return raw_node_id
    return f"{scope_prefix}::{raw_node_id}" if raw_node_id else scope_prefix


def _workflow_call_scope_key(
    *,
    source_workflow_id: UUID,
    source_node_scope: str,
    target_workflow_id: UUID,
) -> str:
    return f"{source_workflow_id}|{source_node_scope}|{target_workflow_id}"


def _merge_summary_text(parent_summary: str, child_summary: str, *, max_chars: int) -> str:
    left = str(parent_summary or "").strip()
    right = str(child_summary or "").strip()
    if not left:
        return AssistantMemoryService.truncate_summary(right, max_chars=max_chars)
    if not right or left == right:
        return AssistantMemoryService.truncate_summary(left, max_chars=max_chars)
    return AssistantMemoryService.truncate_summary(f"{left}\n\n{right}", max_chars=max_chars)


def _merge_facts(
    parent_facts: list[str],
    child_facts: list[str],
    *,
    max_items: int,
) -> list[str]:
    return AssistantMemoryService.normalize_l2_facts(
        [*list(parent_facts or []), *list(child_facts or [])],
        max_items=max_items,
    )


def _normalize_scope_memory_payload(
    raw_payload: Any,
    *,
    max_chars: int,
    max_items: int,
) -> dict[str, Any]:
    return AssistantMemoryService.normalize_workflow_call_scope_memory(
        raw_payload,
        max_chars=max_chars,
        max_items=max_items,
    )


def _build_child_memory_context(
    *,
    parent_memory_context: dict[str, Any],
    scope_key: str | None,
    scope_payload: dict[str, Any],
    max_chars: int,
    max_items: int,
) -> dict[str, Any]:
    next_context = dict(parent_memory_context or {})
    next_scopes = dict(next_context.get("workflow_call_scopes", {})) if isinstance(next_context.get("workflow_call_scopes"), dict) else {}
    if scope_key and (scope_payload.get("conversationSummary") or scope_payload.get("skillFacts")):
        next_scopes[scope_key] = dict(scope_payload)
    next_context["workflow_call_scopes"] = next_scopes

    parent_summary = str(parent_memory_context.get("l1_text", "") or "")
    parent_facts_raw = parent_memory_context.get("l2_facts", [])
    parent_facts = list(parent_facts_raw) if isinstance(parent_facts_raw, list) else []
    child_summary = str(scope_payload.get("conversationSummary", "") or "")
    child_facts = list(scope_payload.get("skillFacts", []) or [])
    merged_facts = _merge_facts(parent_facts, child_facts, max_items=max_items)

    next_context["l1_text"] = _merge_summary_text(parent_summary, child_summary, max_chars=max_chars)
    next_context["l2_facts"] = merged_facts
    next_context["l2_text"] = AssistantMemoryService.render_l2_text(merged_facts)
    return next_context


def _compute_next_scope_memory_payload(
    *,
    db_session: Any,
    workflow_name: str,
    previous_payload: dict[str, Any],
    user_text: str,
    assistant_text: str,
    max_chars: int,
    max_items: int,
) -> dict[str, Any]:
    next_payload = {
        "conversationSummary": str(previous_payload.get("conversationSummary", "") or "").strip(),
        "skillFacts": list(previous_payload.get("skillFacts", []) or []),
    }
    try:
        resolved_cfg = resolve_openai_compat_config(db_session, component="assistant", model_type="llm")
        cfg = (
            OpenAiFallbackConfig(
                api_key=resolved_cfg.api_key,
                base_url=resolved_cfg.base_url,
                model=resolved_cfg.model,
            )
            if resolved_cfg is not None
            else None
        )
        memory_compute = AssistantMemoryComputationService()
        next_summary, _ = memory_compute.compute_next_l1_summary(
            cfg=cfg,
            prev_summary=next_payload["conversationSummary"],
            user_text=user_text,
            assistant_text=assistant_text,
            max_chars=max_chars,
        )
        next_facts, _ = memory_compute.compute_next_l2_facts(
            cfg=cfg,
            prev_facts=list(next_payload["skillFacts"]),
            skill_name=workflow_name,
            user_text=user_text,
            assistant_text=assistant_text,
            max_items=max_items,
        )
        next_payload["conversationSummary"] = next_summary
        next_payload["skillFacts"] = next_facts
    except Exception:
        logger.exception("workflow_call child memory update failed workflow=%s", workflow_name)
    return _normalize_scope_memory_payload(
        next_payload,
        max_chars=max_chars,
        max_items=max_items,
    )


def _build_child_skill_definition(
    *,
    workflow_id: str,
    workflow_version_id: str | None,
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
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
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

            settings = get_settings()
            max_chars = max(1, int(getattr(settings, "assistant_memory_l1_max_chars", 2000) or 2000))
            max_items = max(1, int(getattr(settings, "assistant_memory_l2_max_items", 20) or 20))
            source_workflow_id = AssistantConfigService._parse_uuid_value(state.get("workflow_id"))
            source_node_scope = _workflow_call_source_node_scope(node_id, metadata)
            scope_key = (
                _workflow_call_scope_key(
                    source_workflow_id=source_workflow_id,
                    source_node_scope=source_node_scope,
                    target_workflow_id=resolved.workflow.id,
                )
                if source_workflow_id is not None
                else None
            )

            input_payload = _resolve_workflow_call_input_payload(
                node_cfg=node_cfg,
                state=state,
                contract_input_fields=resolved.contract.input_fields,
            )
            child_user_input = ""
            child_structured_input: dict[str, Any] | None = None
            if resolved.contract.input_mode == "text":
                child_user_input = stringify(input_payload.get("user_input", ""))
            else:
                child_structured_input = input_payload

            parent_memory_context = state.get("memory_context", {}) if isinstance(state.get("memory_context"), dict) else {}
            cached_scopes = parent_memory_context.get("workflow_call_scopes", {})
            cached_scope_payload = (
                cached_scopes.get(scope_key)
                if scope_key and isinstance(cached_scopes, dict)
                else None
            )
            conversation_id = str((state.get("sys_vars", {}) or {}).get("conversation_id", "") or "").strip()
            conversation_id_uuid = AssistantConfigService._parse_uuid_value(conversation_id)
            if cached_scope_payload is not None:
                scope_payload = _normalize_scope_memory_payload(
                    cached_scope_payload,
                    max_chars=max_chars,
                    max_items=max_items,
                )
            elif conversation_id_uuid is not None and source_workflow_id is not None and scope_key is not None:
                scope_payload = _normalize_scope_memory_payload(
                    AssistantMemoryService(session).get_workflow_call_memory(
                        conversation_id=conversation_id_uuid,
                        source_workflow_id=source_workflow_id,
                        source_node_scope=source_node_scope,
                        target_workflow_id=resolved.workflow.id,
                    ),
                    max_chars=max_chars,
                    max_items=max_items,
                )
            else:
                scope_payload = {"conversationSummary": "", "skillFacts": []}
            child_memory_context = _build_child_memory_context(
                parent_memory_context=parent_memory_context,
                scope_key=scope_key,
                scope_payload=scope_payload,
                max_chars=max_chars,
                max_items=max_items,
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
                workflow_id=str(resolved.workflow.id),
                workflow_version_id=str(resolved.version.id),
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
                messages=build_initial_messages(history=[], user_input=child_user_input),
                skill_name=child_skill.name,
                workflow_id=child_skill.workflow_id,
                workflow_version_id=child_skill.workflow_version_id,
                user_input=child_user_input,
                kb_enabled=False,
                memory_mode=str(state.get("memory_mode", "auto") or "auto"),
                metadata=scoped_metadata,
                sys_vars=state.get("sys_vars", {}) or {},
                workflow_node_types=workflow_node_types,
                node_llms=child_node_llms,
                stream_output_enabled=False,
                output_stream_source_node_id=output_stream_source_node_id,
                structured_input=child_structured_input,
                memory_context=child_memory_context,
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
        response_text = stringify(response_value)

        final_memory_context = final_state.get("memory_context", {})
        child_final_scopes = (
            dict(final_memory_context.get("workflow_call_scopes", {}))
            if isinstance(final_memory_context, dict) and isinstance(final_memory_context.get("workflow_call_scopes"), dict)
            else {}
        )
        next_parent_memory_context = dict(
            state.get("memory_context", {}) if isinstance(state.get("memory_context"), dict) else {}
        )
        next_parent_scopes = (
            dict(next_parent_memory_context.get("workflow_call_scopes", {}))
            if isinstance(next_parent_memory_context.get("workflow_call_scopes"), dict)
            else {}
        )
        next_parent_scopes.update(child_final_scopes)

        if source_workflow_id is not None and scope_key is not None:
            with session_factory() as memory_session:
                updated_scope_payload = _compute_next_scope_memory_payload(
                    db_session=memory_session,
                    workflow_name=resolved.workflow.name,
                    previous_payload=scope_payload,
                    user_text=child_user_input if resolved.contract.input_mode == "text" else stringify(child_structured_input or {}),
                    assistant_text=response_text,
                    max_chars=max_chars,
                    max_items=max_items,
                )
            if updated_scope_payload.get("conversationSummary") or updated_scope_payload.get("skillFacts"):
                next_parent_scopes[scope_key] = updated_scope_payload
                if conversation_id_uuid is not None:
                    with session_factory() as memory_session:
                        AssistantMemoryService(memory_session).upsert_workflow_call_memory(
                            conversation_id=conversation_id_uuid,
                            source_workflow_id=source_workflow_id,
                            source_node_scope=source_node_scope,
                            target_workflow_id=resolved.workflow.id,
                            summary_text=str(updated_scope_payload.get("conversationSummary", "") or "").strip(),
                            facts=list(updated_scope_payload.get("skillFacts", []) or []),
                        )

        if next_parent_scopes:
            next_parent_memory_context["workflow_call_scopes"] = next_parent_scopes
            session_scope_payload = metadata.get("workflow_call_session_scopes")
            if isinstance(session_scope_payload, dict):
                session_scope_payload.clear()
                for scope_name, scope_value in next_parent_scopes.items():
                    if not isinstance(scope_value, dict):
                        continue
                    session_scope_payload[str(scope_name)] = {
                        "conversationSummary": str(scope_value.get("conversationSummary", "") or "").strip(),
                        "skillFacts": list(scope_value.get("skillFacts", []) or []),
                    }

        node_out = NodeOutput(
            status="ok",
            text=response_text,
            raw=child_output.get("raw", response_value),
            json_fields={
                "response": response_value,
                **child_json_fields,
            },
        )

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
            "memory_context": next_parent_memory_context,
        }

    return workflow_call_node
