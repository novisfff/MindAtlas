from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict


class StepOutput(TypedDict, total=False):
    status: str
    text: str
    raw: Any
    json_fields: dict[str, Any]
    allowed_fields: list[str]
    tool_meta: dict | None


def _merge_memory_context(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key == "workflow_call_scopes":
            existing = merged.get("workflow_call_scopes", {})
            next_scopes = dict(existing) if isinstance(existing, dict) else {}
            if isinstance(value, dict):
                for scope_key, scope_value in value.items():
                    normalized_key = str(scope_key or "").strip()
                    if not normalized_key:
                        continue
                    if isinstance(scope_value, dict):
                        next_scopes[normalized_key] = dict(scope_value)
            merged["workflow_call_scopes"] = next_scopes
            continue
        merged[key] = value
    return merged


class AssistantState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    skill_name: str
    user_input: str
    kb_enabled: bool
    memory_mode: str
    sys_vars: dict[str, str]
    iteration_count: int
    metadata: dict
    memory_context: Annotated[dict[str, Any], _merge_memory_context]
    current_step: int
    step_outputs: dict[int, StepOutput]
    summary_trace: list[dict]


class NodeOutput(TypedDict, total=False):
    status: str
    text: str
    raw: Any
    json_fields: dict[str, Any]


def _merge_node_outputs(left: dict[str, NodeOutput], right: dict[str, NodeOutput]) -> dict[str, NodeOutput]:
    merged = dict(left)
    merged.update(right)
    return merged


def _merge_trace(left: list[str], right: list[str]) -> list[str]:
    seen = set(left)
    merged = list(left)
    for item in right:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _merge_branch_decisions(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    merged = dict(left)
    merged.update(right)
    return merged


class WorkflowState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    skill_name: str
    workflow_id: str
    workflow_version_id: str
    user_input: str
    kb_enabled: bool
    memory_mode: str
    metadata: dict
    memory_context: Annotated[dict[str, Any], _merge_memory_context]
    node_outputs: Annotated[dict[str, NodeOutput], _merge_node_outputs]
    execution_trace: Annotated[list[str], _merge_trace]
    branch_decisions: Annotated[dict[str, str], _merge_branch_decisions]
    sys_vars: dict[str, str]
    workflow_node_types: dict[str, str]
    node_llms: dict[str, Any]
    stream_output_enabled: bool
    output_stream_source_node_id: str
    structured_input: dict[str, Any]
    env_vars: dict[str, Any]
    env_specs: dict[str, dict[str, Any]]
