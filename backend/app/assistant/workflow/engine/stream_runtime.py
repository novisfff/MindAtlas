from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Iterator

from app.assistant.run_control import AssistantRunCancelled, ensure_not_cancelled
from app.assistant.workflow.engine import runtime_helpers as rt

_OUTPUT_SEGMENT_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class RuntimeEventHandlers:
    on_tool_call_start: Callable | None = None
    on_tool_call_end: Callable | None = None
    on_analysis_start: Callable | None = None
    on_analysis_delta: Callable | None = None
    on_analysis_end: Callable | None = None
    on_node_start: Callable | None = None
    on_node_output_delta: Callable | None = None
    on_node_end: Callable | None = None
    on_branch_decision: Callable | None = None
    on_node_snapshot: Callable | None = None
    on_human_approval_requested: Callable[[dict[str, Any]], None] | None = None
    on_human_approval_resolved: Callable[[dict[str, Any]], None] | None = None


def build_runtime_metadata(
    runtime_events: Queue[tuple[str, dict[str, Any]]],
    *,
    handlers: RuntimeEventHandlers,
) -> tuple[dict[str, Any], Callable[..., None]]:
    def push_runtime_event(event_name: str, **payload: Any) -> None:
        runtime_events.put((event_name, payload))

    def _on_content_delta(chunk: Any, **extra: Any) -> None:
        push_runtime_event("content_delta", chunk=chunk, **extra)

    metadata: dict[str, Any] = {
        "on_content_delta": _on_content_delta,
    }
    if handlers.on_tool_call_start:
        metadata["on_tool_call_start"] = lambda tool_call_id, tool_name, args, **extra: (
            push_runtime_event(
                "tool_call_start",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args,
                **extra,
            )
        )
    if handlers.on_tool_call_end:
        metadata["on_tool_call_end"] = lambda tool_call_id, status, result, **extra: (
            push_runtime_event(
                "tool_call_end",
                tool_call_id=tool_call_id,
                status=status,
                result=result,
                **extra,
            )
        )
    if handlers.on_analysis_start:
        metadata["on_analysis_start"] = lambda analysis_id: (
            push_runtime_event("analysis_start", analysis_id=analysis_id)
        )
    if handlers.on_analysis_delta:
        metadata["on_analysis_delta"] = lambda analysis_id, chunk: (
            push_runtime_event("analysis_delta", analysis_id=analysis_id, chunk=chunk)
        )
    if handlers.on_analysis_end:
        metadata["on_analysis_end"] = lambda analysis_id: (
            push_runtime_event("analysis_end", analysis_id=analysis_id)
        )
    if handlers.on_node_start:
        metadata["on_node_start"] = lambda node_id, node_type, **extra: (
            push_runtime_event("node_start", node_id=node_id, node_type=node_type, **extra)
        )
    if handlers.on_node_output_delta:
        metadata["on_node_output_delta"] = lambda node_id, delta, **extra: (
            push_runtime_event("node_output_delta", node_id=node_id, delta=delta, **extra)
        )
    if handlers.on_node_end:
        metadata["on_node_end"] = lambda node_id, status, **extra: (
            push_runtime_event("node_end", node_id=node_id, status=status, **extra)
        )
    if handlers.on_branch_decision:
        metadata["on_branch_decision"] = lambda node_id, handle, **extra: (
            push_runtime_event("branch_decision", node_id=node_id, handle=handle, **extra)
        )
    if handlers.on_node_snapshot:
        metadata["on_node_snapshot"] = (
            lambda node_id, node_type, status, input, output, error_message=None, hard_truncated=False, **extra: (
                push_runtime_event(
                    "node_snapshot",
                    node_id=node_id,
                    node_type=node_type,
                    status=status,
                    input=input,
                    output=output,
                    error_message=error_message,
                    hard_truncated=hard_truncated,
                    **extra,
                )
            )
        )
    if handlers.on_human_approval_requested:
        metadata["on_human_approval_requested"] = lambda payload: (
            push_runtime_event("human_approval_requested", approval=payload)
        )
    if handlers.on_human_approval_resolved:
        metadata["on_human_approval_resolved"] = lambda payload: (
            push_runtime_event("human_approval_resolved", approval=payload)
        )

    return metadata, push_runtime_event


def dispatch_runtime_event(
    *,
    event_name: str,
    payload: dict[str, Any],
    handlers: RuntimeEventHandlers,
    stream_output_enabled: bool,
    buffered_content_chunks: list[str],
    content_segment_state: dict[str, Any],
) -> tuple[bool, list[str]]:
    yielded: list[str] = []
    graph_done = False

    if event_name == "content_delta":
        chunk = str(payload.get("chunk", "") or "")
        if chunk:
            source_node_id = str(payload.get("source_node_id", "") or "")
            source_node_type = str(payload.get("source_node_type", "") or "").strip().lower()
            if source_node_type == "output" and source_node_id:
                last_output_source = str(content_segment_state.get("last_output_source_node_id", "") or "")
                if last_output_source and last_output_source != source_node_id:
                    if stream_output_enabled:
                        yielded.append(_OUTPUT_SEGMENT_SEPARATOR)
                    else:
                        buffered_content_chunks.append(_OUTPUT_SEGMENT_SEPARATOR)
                content_segment_state["last_output_source_node_id"] = source_node_id
            if stream_output_enabled:
                yielded.append(chunk)
            else:
                buffered_content_chunks.append(chunk)
        return graph_done, yielded

    if event_name == "tool_call_start" and handlers.on_tool_call_start:
        rt.invoke_callback(
            handlers.on_tool_call_start,
            tool_call_id=payload.get("tool_call_id", ""),
            tool_name=payload.get("tool_name", ""),
            args=payload.get("args", {}),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"tool_call_id", "tool_name", "args"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "tool_call_end" and handlers.on_tool_call_end:
        rt.invoke_callback(
            handlers.on_tool_call_end,
            tool_call_id=payload.get("tool_call_id", ""),
            status=payload.get("status", ""),
            result=payload.get("result", ""),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"tool_call_id", "status", "result"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "analysis_start" and handlers.on_analysis_start:
        handlers.on_analysis_start(payload.get("analysis_id", ""))
        yielded.append("")
        return graph_done, yielded

    if event_name == "analysis_delta" and handlers.on_analysis_delta:
        handlers.on_analysis_delta(
            payload.get("analysis_id", ""),
            payload.get("chunk", ""),
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "analysis_end" and handlers.on_analysis_end:
        handlers.on_analysis_end(payload.get("analysis_id", ""))
        yielded.append("")
        return graph_done, yielded

    if event_name == "node_start" and handlers.on_node_start:
        rt.invoke_callback(
            handlers.on_node_start,
            node_id=payload.get("node_id", ""),
            node_type=payload.get("node_type", ""),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"node_id", "node_type"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "node_output_delta" and handlers.on_node_output_delta:
        rt.invoke_callback(
            handlers.on_node_output_delta,
            node_id=payload.get("node_id", ""),
            delta=payload.get("delta", ""),
            node_delta=payload.get("delta", ""),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"node_id", "delta"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "node_end" and handlers.on_node_end:
        rt.invoke_callback(
            handlers.on_node_end,
            node_id=payload.get("node_id", ""),
            status=payload.get("status", ""),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"node_id", "status"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "branch_decision" and handlers.on_branch_decision:
        rt.invoke_callback(
            handlers.on_branch_decision,
            node_id=payload.get("node_id", ""),
            handle=payload.get("handle", ""),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"node_id", "handle"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "node_snapshot" and handlers.on_node_snapshot:
        rt.invoke_callback(
            handlers.on_node_snapshot,
            node_id=payload.get("node_id", ""),
            node_type=payload.get("node_type", ""),
            status=payload.get("status", ""),
            input=payload.get("input"),
            output=payload.get("output"),
            input_data=payload.get("input"),
            output_data=payload.get("output"),
            error_message=payload.get("error_message"),
            hard_truncated=bool(payload.get("hard_truncated", False)),
            **{
                key: value
                for key, value in payload.items()
                if key not in {"node_id", "node_type", "status", "input", "output", "error_message", "hard_truncated"}
            },
        )
        yielded.append("")
        return graph_done, yielded

    if event_name == "human_approval_requested" and handlers.on_human_approval_requested:
        handlers.on_human_approval_requested(payload.get("approval", {}))
        yielded.append("")
        return graph_done, yielded

    if event_name == "human_approval_resolved" and handlers.on_human_approval_resolved:
        handlers.on_human_approval_resolved(payload.get("approval", {}))
        yielded.append("")
        return graph_done, yielded

    if event_name == "graph_tick":
        yielded.append("")
        return graph_done, yielded

    if event_name == "graph_done":
        graph_done = True
        return graph_done, yielded

    return graph_done, yielded


def run_graph_stream(
    *,
    compiled: Any,
    initial_state: dict[str, Any],
    runtime_events: Queue[tuple[str, dict[str, Any]]],
    push_runtime_event: Callable[..., None],
    handlers: RuntimeEventHandlers,
    stream_output_enabled: bool,
    cancel_checker: Callable[[], bool] | None = None,
    event_poll_timeout: float = 0.1,
) -> Iterator[str]:
    graph_errors: list[Exception] = []

    def _run_graph() -> None:
        try:
            for _ in compiled.stream(initial_state):
                push_runtime_event("graph_tick")
        except Exception as exc:  # pragma: no cover - raised on caller thread
            graph_errors.append(exc)
        finally:
            push_runtime_event("graph_done")

    graph_thread = Thread(target=_run_graph, daemon=True)
    graph_thread.start()

    graph_done = False
    buffered_content_chunks: list[str] = []
    content_segment_state: dict[str, Any] = {
        "last_output_source_node_id": "",
    }
    cancel_error: AssistantRunCancelled | None = None
    while not graph_done or not runtime_events.empty():
        try:
            ensure_not_cancelled(cancel_checker, message="assistant run cancelled while polling workflow events")
        except AssistantRunCancelled as exc:
            cancel_error = exc
            break
        try:
            event_name, payload = runtime_events.get(timeout=event_poll_timeout)
        except Empty:
            if graph_done:
                break
            continue
        done, outputs = dispatch_runtime_event(
            event_name=event_name,
            payload=payload,
            handlers=handlers,
            stream_output_enabled=stream_output_enabled,
            buffered_content_chunks=buffered_content_chunks,
            content_segment_state=content_segment_state,
        )
        if done:
            graph_done = True
        for out in outputs:
            yield out

    if cancel_error is not None:
        graph_thread.join(timeout=0.2)
        raise cancel_error

    graph_thread.join()
    if graph_errors:
        raise graph_errors[0]
    if not stream_output_enabled and buffered_content_chunks:
        yield "".join(buffered_content_chunks)
