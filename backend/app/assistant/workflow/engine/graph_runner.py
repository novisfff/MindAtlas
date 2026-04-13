from __future__ import annotations

from typing import Any, Callable, Iterator

from langgraph.graph.message import add_messages

from app.assistant.workflow.engine.state import (
    _merge_branch_decisions,
    _merge_memory_context,
    _merge_node_outputs,
    _merge_trace,
)


def merge_graph_state(base_state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(update, dict):
        return dict(base_state)

    merged = dict(base_state)

    if "messages" in update:
        merged["messages"] = add_messages(
            list(merged.get("messages", []) or []),
            list(update.get("messages", []) or []),
        )
    if "memory_context" in update and isinstance(update.get("memory_context"), dict):
        merged["memory_context"] = _merge_memory_context(
            merged.get("memory_context", {}) if isinstance(merged.get("memory_context"), dict) else {},
            update["memory_context"],
        )
    if "node_outputs" in update and isinstance(update.get("node_outputs"), dict):
        merged["node_outputs"] = _merge_node_outputs(
            merged.get("node_outputs", {}) if isinstance(merged.get("node_outputs"), dict) else {},
            update["node_outputs"],
        )
    if "execution_trace" in update and isinstance(update.get("execution_trace"), list):
        merged["execution_trace"] = _merge_trace(
            list(merged.get("execution_trace", []) or []),
            [str(item) for item in update["execution_trace"]],
        )
    if "branch_decisions" in update and isinstance(update.get("branch_decisions"), dict):
        merged["branch_decisions"] = _merge_branch_decisions(
            merged.get("branch_decisions", {}) if isinstance(merged.get("branch_decisions"), dict) else {},
            {str(key): str(value) for key, value in update["branch_decisions"].items()},
        )

    for key, value in update.items():
        if key in {"messages", "memory_context", "node_outputs", "execution_trace", "branch_decisions"}:
            continue
        merged[key] = value
    return merged


def snapshot_graph_state(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(state)
    for key in (
        "metadata",
        "memory_context",
        "node_outputs",
        "branch_decisions",
        "sys_vars",
        "workflow_node_types",
        "node_llms",
        "structured_input",
        "env_vars",
        "env_specs",
    ):
        value = snapshot.get(key)
        if isinstance(value, dict):
            snapshot[key] = dict(value)
    for key in ("messages", "execution_trace"):
        value = snapshot.get(key)
        if isinstance(value, list):
            snapshot[key] = list(value)
    return snapshot


class GraphRunnableAdapter:
    def __init__(
        self,
        *,
        compiled: Any,
        fallback_invoke: Callable[[dict[str, Any]], dict[str, Any]],
        fallback_stream: Callable[[dict[str, Any]], Iterator[dict[str, Any]]] | None = None,
    ) -> None:
        self._compiled = compiled
        self._fallback_invoke = fallback_invoke
        self._fallback_stream = fallback_stream

    def __getattr__(self, item: str) -> Any:
        return getattr(self._compiled, item)

    def compile(self) -> "GraphRunnableAdapter":
        return self

    def invoke(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        if hasattr(self._compiled, "invoke"):
            result = self._compiled.invoke(initial_state)
            if isinstance(result, dict):
                return result
            return snapshot_graph_state(initial_state)

        if hasattr(self._compiled, "stream"):
            merged = snapshot_graph_state(initial_state)
            saw_update = False
            for update in self._compiled.stream(initial_state):
                if isinstance(update, dict):
                    saw_update = True
                    merged = merge_graph_state(merged, update)
            if saw_update:
                return merged

        return self._fallback_invoke(snapshot_graph_state(initial_state))

    def stream(self, initial_state: dict[str, Any]) -> Iterator[dict[str, Any]]:
        if hasattr(self._compiled, "stream"):
            for update in self._compiled.stream(initial_state):
                if isinstance(update, dict):
                    yield update
            return

        if hasattr(self._compiled, "invoke"):
            result = self._compiled.invoke(initial_state)
            if isinstance(result, dict):
                yield result
            return

        if self._fallback_stream is not None:
            yield from self._fallback_stream(snapshot_graph_state(initial_state))
            return

        yield self._fallback_invoke(snapshot_graph_state(initial_state))


def adapt_graph_runnable(
    *,
    compiled: Any,
    fallback_invoke: Callable[[dict[str, Any]], dict[str, Any]],
    fallback_stream: Callable[[dict[str, Any]], Iterator[dict[str, Any]]] | None = None,
) -> GraphRunnableAdapter:
    return GraphRunnableAdapter(
        compiled=compiled,
        fallback_invoke=fallback_invoke,
        fallback_stream=fallback_stream,
    )


def invoke_graph_runnable(runnable: Any, initial_state: dict[str, Any]) -> dict[str, Any]:
    if hasattr(runnable, "invoke"):
        result = runnable.invoke(initial_state)
        if isinstance(result, dict):
            return result
    if hasattr(runnable, "stream"):
        merged = snapshot_graph_state(initial_state)
        for update in runnable.stream(initial_state):
            if isinstance(update, dict):
                merged = merge_graph_state(merged, update)
        return merged
    raise RuntimeError("Graph runnable is not invokable")


def stream_graph_runnable(runnable: Any, initial_state: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if hasattr(runnable, "stream"):
        for update in runnable.stream(initial_state):
            if isinstance(update, dict):
                yield update
        return
    if hasattr(runnable, "invoke"):
        result = runnable.invoke(initial_state)
        if isinstance(result, dict):
            yield result
        return
    raise RuntimeError("Graph runnable is not streamable")
