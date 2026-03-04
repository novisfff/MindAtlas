from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine.workflow_dag_plan import build_workflow_node_maps


def build_initial_messages(
    *,
    history: list[dict[str, Any]] | None,
    user_input: str,
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [SystemMessage(content="")]
    for item in (history or [])[-10:]:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role == "system":
            continue
        if role == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=user_input))
    return messages


def resolve_workflow_runtime_context(
    *,
    skill_name: str,
    workflow_nodes: list[Any],
    normalize_config: Callable[[dict[str, Any] | None], dict[str, Any]],
    extract_single_template_reference: Callable[[str], tuple[str, str] | None],
    resolve_workflow_node_llms: Callable[[], dict[str, ChatOpenAI]],
) -> tuple[dict[str, ChatOpenAI], dict[str, str], str]:
    if not workflow_nodes:
        raise ValueError(f"workflow_dag skill {skill_name} has no workflow nodes")

    node_llms = resolve_workflow_node_llms()
    workflow_node_types, workflow_node_configs = build_workflow_node_maps(
        workflow_nodes,
        normalize_config=normalize_config,
    )

    output_stream_source_node_id = ""
    output_node_ids = [node_id for node_id, node_type in workflow_node_types.items() if node_type == "output"]
    if len(output_node_ids) != 1:
        # Keep passthrough optimization only for the single-output topology.
        return node_llms, workflow_node_types, output_stream_source_node_id

    output_node_cfg = workflow_node_configs.get(output_node_ids[0], {})
    output_mode = str(output_node_cfg.get("output_mode", "text") or "text").strip().lower()
    if output_mode == "json":
        output_mode = "structured"
    if output_mode == "text":
        text_template = output_node_cfg.get("text_template", "")
        if isinstance(text_template, str):
            single_ref = extract_single_template_reference(text_template)
            if single_ref is not None:
                ref_node_id, ref_field = single_ref
                ref_node_cfg = workflow_node_configs.get(ref_node_id, {})
                ref_output_mode = str(
                    ref_node_cfg.get("output_mode", "text") or "text"
                ).strip().lower()
                if ref_output_mode == "json":
                    ref_output_mode = "structured"
                if (
                    workflow_node_types.get(ref_node_id) == "llm"
                    and ref_output_mode == "text"
                    and ref_field in {"response", "text"}
                ):
                    output_stream_source_node_id = ref_node_id

    return node_llms, workflow_node_types, output_stream_source_node_id


def build_initial_state(
    *,
    pattern: str,
    messages: list[BaseMessage],
    skill_name: str,
    user_input: str,
    kb_enabled: bool,
    memory_mode: str,
    metadata: dict[str, Any],
    sys_vars: dict[str, str],
    workflow_node_types: dict[str, str],
    node_llms: dict[str, ChatOpenAI],
    stream_output_enabled: bool,
    output_stream_source_node_id: str,
    structured_input: dict[str, Any] | None,
    memory_context: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_memory_context = memory_context if isinstance(memory_context, dict) else {}
    if pattern == "workflow_dag":
        return {
            "messages": messages,
            "skill_name": skill_name,
            "user_input": user_input,
            "kb_enabled": kb_enabled,
            "memory_mode": memory_mode,
            "metadata": metadata,
            "memory_context": normalized_memory_context,
            "node_outputs": {},
            "execution_trace": [],
            "branch_decisions": {},
            "sys_vars": sys_vars,
            "workflow_node_types": workflow_node_types,
            "node_llms": node_llms,
            "stream_output_enabled": stream_output_enabled,
            "output_stream_source_node_id": output_stream_source_node_id,
            "structured_input": structured_input,
        }

    return {
        "messages": messages,
        "skill_name": skill_name,
        "user_input": user_input,
        "kb_enabled": kb_enabled,
        "memory_mode": memory_mode,
        "memory_context": normalized_memory_context,
        "iteration_count": 0,
        "metadata": metadata,
        "current_step": 1,
        "step_outputs": {},
        "summary_trace": [],
    }
