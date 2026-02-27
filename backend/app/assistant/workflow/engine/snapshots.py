from __future__ import annotations

from typing import Any, Callable

from app.assistant.workflow.engine import runtime_helpers as rt
from app.assistant.workflow.engine.snapshot_input_resolvers import (
    SnapshotInputContext,
    build_node_snapshot_input as build_snapshot_input,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

NODE_SNAPSHOT_STRING_LIMIT = 64 * 1024
NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT = 4000


def trim_snapshot_string(
    value: str,
    truncation_meta: dict[str, bool],
    *,
    string_limit: int = NODE_SNAPSHOT_STRING_LIMIT,
) -> str:
    if len(value) <= string_limit:
        return value
    truncation_meta["hard_truncated"] = True
    return value[:string_limit] + "...(truncated)"


def sanitize_node_snapshot_value(
    value: Any,
    truncation_meta: dict[str, bool],
    *,
    string_limit: int = NODE_SNAPSHOT_STRING_LIMIT,
    _visited: set[int] | None = None,
) -> Any:
    visited = _visited if _visited is not None else set()

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return trim_snapshot_string(value, truncation_meta, string_limit=string_limit)

    if isinstance(value, bytes):
        return trim_snapshot_string(value.decode("utf-8", errors="replace"), truncation_meta, string_limit=string_limit)

    if isinstance(value, dict):
        identity = id(value)
        if identity in visited:
            return "(circular)"
        visited.add(identity)
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = trim_snapshot_string(str(key), truncation_meta, string_limit=string_limit)
            sanitized[key_text] = sanitize_node_snapshot_value(
                item,
                truncation_meta,
                string_limit=string_limit,
                _visited=visited,
            )
        visited.remove(identity)
        return sanitized

    if isinstance(value, (list, tuple, set)):
        identity = id(value)
        if identity in visited:
            return ["(circular)"]
        visited.add(identity)
        sanitized_list = [
            sanitize_node_snapshot_value(
                item,
                truncation_meta,
                string_limit=string_limit,
                _visited=visited,
            )
            for item in list(value)
        ]
        visited.remove(identity)
        return sanitized_list

    return trim_snapshot_string(rt.stringify(value), truncation_meta, string_limit=string_limit)


def emit_node_snapshot(
    metadata: dict[str, Any],
    *,
    node_id: str,
    node_type: str,
    status: str,
    input_data: Any,
    output_data: Any,
    error_message: str | None = None,
    string_limit: int = NODE_SNAPSHOT_STRING_LIMIT,
) -> None:
    truncation_meta = {"hard_truncated": False}
    safe_input = sanitize_node_snapshot_value(
        input_data,
        truncation_meta,
        string_limit=string_limit,
    )
    safe_output = sanitize_node_snapshot_value(
        output_data,
        truncation_meta,
        string_limit=string_limit,
    )
    safe_error = None
    if error_message:
        safe_error = trim_snapshot_string(
            str(error_message),
            truncation_meta,
            string_limit=string_limit,
        )

    rt.emit(
        metadata,
        "on_node_snapshot",
        node_id=node_id,
        node_type=node_type,
        status=status,
        input=safe_input,
        output=safe_output,
        error_message=safe_error,
        hard_truncated=bool(truncation_meta["hard_truncated"]),
    )


def build_node_snapshot_input(
    node_type: str,
    node_cfg: dict[str, Any],
    state: WorkflowState,
    *,
    text_preview_limit: int = NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT,
) -> dict[str, Any]:
    node_outputs = dict(state.get("node_outputs", {}))
    start_inputs = rt.get_start_inputs(node_outputs)
    sys_vars = state.get("sys_vars", {}) or {}
    env_vars = state.get("env_vars", {}) or {}
    env_specs = state.get("env_specs", {}) or {}
    ctx = SnapshotInputContext(
        state=state,
        node_outputs=node_outputs,
        start_inputs=start_inputs,
        sys_vars=sys_vars,
        env_vars=env_vars,
        env_specs=env_specs,
        text_preview_limit=text_preview_limit,
    )
    return build_snapshot_input(node_type, node_cfg, ctx)


def build_node_snapshot_output(
    node_type: str,
    node_out: NodeOutput | None,
    result: dict[str, Any] | None,
    *,
    text_preview_limit: int = NODE_SNAPSHOT_TEXT_PREVIEW_LIMIT,
) -> Any:
    if not isinstance(node_out, dict):
        return None

    if node_type == "if_else":
        json_fields = node_out.get("json_fields", {})
        chosen_handle = json_fields.get("handle") if isinstance(json_fields, dict) else None
        if chosen_handle is None:
            chosen_handle = node_out.get("raw", node_out.get("text"))
        return {"chosenHandle": chosen_handle}

    if node_type == "iteration":
        raw_payload = node_out.get("raw")
        if isinstance(raw_payload, dict):
            errors = raw_payload.get("errors")
            return {
                "count": raw_payload.get("count"),
                "errors": errors,
                "errorsCount": len(errors) if isinstance(errors, list) else 0,
                "itemsPreview": rt.truncate(rt.stringify(raw_payload.get("items", [])), text_preview_limit),
            }

    if node_type == "loop":
        raw_payload = node_out.get("raw")
        if isinstance(raw_payload, dict):
            return {
                "iterations": raw_payload.get("iterations"),
                "terminated": raw_payload.get("terminated"),
                "lastItemPreview": rt.truncate(rt.stringify(raw_payload.get("last_item")), text_preview_limit),
                "vars": raw_payload.get("vars"),
            }

    if node_type == "human_in_loop":
        raw_payload = node_out.get("raw")
        if isinstance(raw_payload, dict):
            return {
                "decision": raw_payload.get("decision"),
                "comment": raw_payload.get("comment"),
                "values": raw_payload.get("values"),
                "approvalId": raw_payload.get("approval_id"),
            }

    raw_payload = node_out.get("raw")
    if raw_payload is not None:
        return raw_payload
    json_fields = node_out.get("json_fields")
    if isinstance(json_fields, dict):
        return json_fields
    text_payload = node_out.get("text")
    if text_payload is not None:
        return text_payload
    return result


def wrap_workflow_node_with_snapshot(
    node_id: str,
    node_type: str,
    node_cfg: dict[str, Any],
    node_fn: Callable[[WorkflowState], dict],
    *,
    build_input_fn: Callable[[str, dict[str, Any], WorkflowState], dict[str, Any]] | None = None,
    build_output_fn: Callable[[str, NodeOutput | None, dict[str, Any] | None], Any] | None = None,
    emit_snapshot_fn: Callable[..., None] | None = None,
) -> Callable[[WorkflowState], dict]:
    input_builder = build_input_fn or build_node_snapshot_input
    output_builder = build_output_fn or build_node_snapshot_output
    snapshot_emitter = emit_snapshot_fn or emit_node_snapshot

    def wrapped(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {}) if isinstance(state, dict) else {}
        try:
            snapshot_input = input_builder(node_type, node_cfg, state)
        except Exception as exc:
            snapshot_input = {
                "snapshotError": f"failed to build input snapshot: {exc}",
            }
        try:
            result = node_fn(state)
        except Exception as exc:
            snapshot_emitter(
                metadata,
                node_id=node_id,
                node_type=node_type,
                status="error",
                input_data=snapshot_input,
                output_data=None,
                error_message=str(exc),
            )
            raise

        node_out: NodeOutput | None = None
        if isinstance(result, dict):
            node_outputs = result.get("node_outputs")
            if isinstance(node_outputs, dict):
                candidate = node_outputs.get(node_id)
                if isinstance(candidate, dict):
                    node_out = candidate
        try:
            output_data = output_builder(
                node_type,
                node_out,
                result if isinstance(result, dict) else None,
            )
        except Exception as exc:
            output_data = {
                "snapshotError": f"failed to build output snapshot: {exc}",
            }
        snapshot_emitter(
            metadata,
            node_id=node_id,
            node_type=node_type,
            status="ok",
            input_data=snapshot_input,
            output_data=output_data,
            error_message=None,
        )
        return result

    return wrapped
