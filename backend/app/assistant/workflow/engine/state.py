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


class AssistantState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    skill_name: str
    user_input: str
    kb_enabled: bool
    memory_mode: str
    sys_vars: dict[str, str]
    iteration_count: int
    metadata: dict
    memory_context: dict[str, Any]
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
    user_input: str
    kb_enabled: bool
    memory_mode: str
    metadata: dict
    memory_context: dict[str, Any]
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
