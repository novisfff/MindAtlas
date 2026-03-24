from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine.runtime_helpers import emit, logger, stringify

StoppedBy = Literal["final_answer", "max_iterations", "tool_error", "invalid_tool"]
KnowledgeMode = Literal["none", "skill_kb", "node_kb"]
RecentDialogueInjection = Literal["none", "message_flow"]


class _KnowledgeQueryArgs(BaseModel):
    query: str = Field(..., description="Knowledge base search query")


@dataclass(frozen=True)
class AgentExecutionHooks:
    metadata: dict[str, Any]
    content_passthrough_enabled: bool = False
    node_output_delta_enabled: bool = False


@dataclass(frozen=True)
class AgentExecutionRequest:
    llm: Any
    system_prompt: str
    conversation_messages: list[dict[str, Any]]
    bound_tools: list[Any]
    tool_runners: dict[str, Callable[..., Any]]
    max_iterations: int
    stream_output_enabled: bool
    execution_hooks: AgentExecutionHooks
    trace_context: dict[str, str]
    knowledge_mode: KnowledgeMode = "none"
    recent_dialogue_injection: RecentDialogueInjection = "none"
    tool_kind_resolver: Callable[[str], str] | None = None
    locale: str | None = None


@dataclass(frozen=True)
class AgentExecutionResult:
    final_text: str
    round_count: int
    used_tools: list[str]
    stopped_by: StoppedBy
    error_message: str | None = None


def build_internal_kb_tool(
    *,
    base_kb_tool: Any,
    wrapped_kb_tool: Callable[..., Any],
    description: str | None = None,
    knowledge_mode: str | None = None,
    knowledge_top_k: int | None = None,
) -> tuple[StructuredTool, Callable[..., Any]]:
    def _invoke_internal_kb_search(query: str) -> Any:
        invoke_args: dict[str, Any] = {"query": query}
        if knowledge_mode is not None:
            invoke_args["mode"] = knowledge_mode
        if knowledge_top_k is not None:
            invoke_args["top_k"] = knowledge_top_k
        return wrapped_kb_tool(**invoke_args)

    tool = StructuredTool.from_function(
        func=_invoke_internal_kb_search,
        name=str(getattr(base_kb_tool, "name", "kb_search") or "kb_search"),
        description=description or (
            "Search the knowledge base for relevant records and references. "
            "Use this when the answer may depend on existing notes or stored knowledge."
        ),
        args_schema=_KnowledgeQueryArgs,
    )
    return tool, _invoke_internal_kb_search


def normalize_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        text = raw_args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def extract_tool_calls(message: Any) -> list[dict[str, Any]]:
    raw_calls = getattr(message, "tool_calls", None)
    if not isinstance(raw_calls, list):
        raw_calls = []

    if not raw_calls:
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            additional_calls = additional_kwargs.get("tool_calls")
            if isinstance(additional_calls, list):
                raw_calls = additional_calls

    normalized: list[dict[str, Any]] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue

        call_id = str(item.get("id") or f"tool_{uuid.uuid4().hex[:8]}").strip()
        name = ""
        raw_args: Any = {}

        if isinstance(item.get("function"), dict):
            fn = item["function"]
            name = str(fn.get("name", "") or "").strip()
            raw_args = fn.get("arguments")
        else:
            name = str(item.get("name", "") or "").strip()
            raw_args = item.get("args")

        if not name:
            continue

        normalized.append(
            {
                "id": call_id or f"tool_{uuid.uuid4().hex[:8]}",
                "name": name,
                "args": normalize_tool_args(raw_args),
            }
        )

    return normalized


def run_agent_execution(request: AgentExecutionRequest) -> AgentExecutionResult:
    llm_with_tools = (
        request.llm.bind_tools(request.bound_tools, parallel_tool_calls=False)
        if request.bound_tools
        else request.llm
    )

    conversation_messages: list[dict[str, Any]] = [
        {"role": "system", "content": request.system_prompt},
        *[dict(item) for item in request.conversation_messages],
    ]
    used_tools: list[str] = []
    tool_kind_resolver = request.tool_kind_resolver or (
        lambda tool_name: "knowledge" if tool_name == "kb_search" else "tool"
    )

    metadata = request.execution_hooks.metadata
    trace_context = request.trace_context or {}
    node_id = str(trace_context.get("node_id", "") or "").strip()
    node_type = str(trace_context.get("node_type", "") or "").strip()

    for round_index in range(request.max_iterations):
        merged = None
        round_chunks: list[str] = []
        for chunk in llm_with_tools.stream(conversation_messages):
            if merged is None:
                merged = chunk
            else:
                merged = merged + chunk
            if not chunk.content:
                continue
            content = str(chunk.content)
            round_chunks.append(content)
            if request.execution_hooks.node_output_delta_enabled and node_id:
                emit(metadata, "on_node_output_delta", node_id=node_id, delta=content)
            if request.execution_hooks.content_passthrough_enabled and request.stream_output_enabled:
                emit(metadata, "on_content_delta", chunk=content)

        if merged is None:
            return AgentExecutionResult(
                final_text="",
                round_count=round_index + 1,
                used_tools=list(used_tools),
                stopped_by="tool_error",
                error_message="Agent execution produced no model output",
            )

        round_text = "".join(round_chunks).strip()
        tool_calls = extract_tool_calls(merged)
        if not tool_calls:
            return AgentExecutionResult(
                final_text=round_text,
                round_count=round_index + 1,
                used_tools=list(used_tools),
                stopped_by="final_answer",
            )

        selected_call = tool_calls[0]
        if len(tool_calls) > 1:
            logger.warning("Agent execution returned multiple tool calls; only the first one is executed")

        tool_call_id = selected_call["id"]
        tool_name = selected_call["name"]
        tool_args = selected_call["args"]
        tool_kind = str(tool_kind_resolver(tool_name) or "tool").strip().lower() or "tool"

        emit(
            metadata,
            "on_tool_call_start",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args=tool_args,
            **({"node_id": node_id} if node_id else {}),
            **({"node_type": node_type} if node_type else {}),
            agent_round=round_index + 1,
            tool_call_index=1,
            tool_kind=tool_kind,
        )

        selected_tool_runner = request.tool_runners.get(tool_name)
        if selected_tool_runner is None:
            error_message = _copy.build_tool_unavailable_message(request.locale, tool_name)
            emit(
                metadata,
                "on_tool_call_end",
                tool_call_id=tool_call_id,
                status="error",
                result=error_message,
                **({"node_id": node_id} if node_id else {}),
                **({"node_type": node_type} if node_type else {}),
                agent_round=round_index + 1,
                tool_call_index=1,
                tool_kind=tool_kind,
            )
            return AgentExecutionResult(
                final_text=round_text,
                round_count=round_index + 1,
                used_tools=list(used_tools),
                stopped_by="invalid_tool",
                error_message=error_message,
            )

        try:
            tool_result = selected_tool_runner(**tool_args)
        except Exception as exc:
            error_message = _copy.build_tool_execution_failed_message(request.locale, exc)
            emit(
                metadata,
                "on_tool_call_end",
                tool_call_id=tool_call_id,
                status="error",
                result=error_message,
                **({"node_id": node_id} if node_id else {}),
                **({"node_type": node_type} if node_type else {}),
                agent_round=round_index + 1,
                tool_call_index=1,
                tool_kind=tool_kind,
            )
            return AgentExecutionResult(
                final_text=round_text,
                round_count=round_index + 1,
                used_tools=list(used_tools),
                stopped_by="tool_error",
                error_message=error_message,
            )

        tool_result_text = stringify(tool_result)
        emit(
            metadata,
            "on_tool_call_end",
            tool_call_id=tool_call_id,
            status="completed",
            result=tool_result_text,
            **({"node_id": node_id} if node_id else {}),
            **({"node_type": node_type} if node_type else {}),
            agent_round=round_index + 1,
            tool_call_index=1,
            tool_kind=tool_kind,
        )
        used_tools.append(tool_name)

        conversation_messages.append(
            {
                "role": "assistant",
                "content": round_text,
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_args, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        conversation_messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result_text,
            }
        )

    return AgentExecutionResult(
        final_text="",
        round_count=request.max_iterations,
        used_tools=list(used_tools),
        stopped_by="max_iterations",
        error_message=f"Agent execution exceeded maxIterations={request.max_iterations}",
    )
