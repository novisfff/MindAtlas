from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.engine.runtime_helpers import (
    START_STRUCTURED_FIELD_NAME_RE,
    coerce_start_structured_field_value,
    emit,
    resolve_start_memory_mode,
    resolve_start_env_specs,
    resolve_start_input_mode,
    resolve_structured_memory_fields,
    resolve_start_structured_fields,
    START_MEMORY_RESERVED_FIELD_NAMES,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.assistant.workflow.env_vars import build_initial_env_vars, serialize_env_specs

def build_start_node(
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    env_specs = resolve_start_env_specs(node_cfg)
    serialized_env_specs = serialize_env_specs(env_specs)
    node_id = str(node_cfg.get("__node_id", "start") or "start").strip() or "start"

    def start_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {}) if isinstance(state, dict) else {}
        input_mode = resolve_start_input_mode(node_cfg)
        configured_memory_mode = resolve_start_memory_mode(node_cfg)
        memory_mode = resolve_start_memory_mode(
            {"memory_mode": state.get("memory_mode")},
            default_mode=configured_memory_mode,
        )
        user_input = state.get("user_input", "")
        structured_input = state.get("structured_input")
        sys_vars = state.get("sys_vars", {}) or {}
        memory_context = state.get("memory_context")
        memory_fields = resolve_structured_memory_fields(memory_context)
        env_vars = build_initial_env_vars(env_specs)
        emit(metadata, "on_node_start", node_id=node_id, node_type="start")
        try:
            if input_mode == "structured":
                if not isinstance(structured_input, dict):
                    raise RuntimeError("start node requires structured_input when inputMode=structured")
                structured_fields = resolve_start_structured_fields(node_cfg)
                if not structured_fields:
                    raise RuntimeError("start node structured mode requires structuredFields")
                allowed_fields: set[str] = set()
                resolved_fields: dict[str, Any] = {}
                for field in structured_fields:
                    field_name = str(field.get("name", "") or "").strip()
                    if not field_name:
                        continue
                    if (
                        field_name == "user_input"
                        or field_name in START_MEMORY_RESERVED_FIELD_NAMES
                        or not START_STRUCTURED_FIELD_NAME_RE.fullmatch(field_name)
                    ):
                        raise RuntimeError(f"start node has invalid structured field name: {field_name}")
                    if field_name in allowed_fields:
                        raise RuntimeError(f"start node has duplicated structured field: {field_name}")
                    allowed_fields.add(field_name)
                    field_required = bool(field.get("required", False))
                    if field_name not in structured_input:
                        if field_required:
                            raise RuntimeError(f"missing required structured input field: {field_name}")
                        continue
                    resolved_fields[field_name] = coerce_start_structured_field_value(
                        field_name,
                        field.get("type", "string"),
                        structured_input.get(field_name),
                    )

                unknown_fields = set(str(key) for key in structured_input.keys()) - allowed_fields
                if unknown_fields:
                    unknown_text = ", ".join(sorted(unknown_fields))
                    raise RuntimeError(f"structured_input contains unknown fields: {unknown_text}")
                if memory_mode == "structured":
                    resolved_fields.update(memory_fields)

                result = {
                    "node_outputs": {
                        "start": NodeOutput(
                            status="ok",
                            text=json.dumps(resolved_fields, ensure_ascii=False),
                            raw=resolved_fields,
                            json_fields=resolved_fields,
                        ),
                    },
                    "execution_trace": ["start"],
                    "env_vars": env_vars,
                    "env_specs": serialized_env_specs,
                }
            else:
                result = {
                    "node_outputs": {
                        "start": NodeOutput(
                            status="ok",
                            text=user_input,
                            raw=user_input,
                            json_fields={
                                "user_input": user_input,
                                "sys_date": sys_vars.get("date", ""),
                                "sys_datetime": sys_vars.get("datetime", ""),
                                "sys_conversation_id": sys_vars.get("conversation_id", ""),
                                **(memory_fields if memory_mode == "structured" else {}),
                            },
                        ),
                    },
                    "execution_trace": ["start"],
                    "env_vars": env_vars,
                    "env_specs": serialized_env_specs,
                }
            emit(metadata, "on_node_end", node_id=node_id, status="ok")
            return result
        except Exception:
            emit(metadata, "on_node_end", node_id=node_id, status="error")
            raise
    return start_node
