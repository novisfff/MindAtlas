from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    get_start_inputs,
    parse_loose_json_value,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.assistant.workflow.env_vars import WorkflowEnvVarSpec, apply_env_var_operation

def build_variable_assign_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def variable_assign_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = dict(state.get("env_vars", {}) or {})
        raw_env_specs = state.get("env_specs", {}) or {}
        if not isinstance(raw_env_specs, dict):
            raw_env_specs = {}

        env_specs: dict[str, WorkflowEnvVarSpec] = {}
        for key, raw_spec in raw_env_specs.items():
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            spec_name = str(spec.get("name", key) or key).strip()
            if not spec_name:
                continue
            spec_type = str(spec.get("type", "string") or "string").strip().lower() or "string"
            default_value = spec.get("defaultValue", spec.get("default_value"))
            description = str(spec.get("description", "") or "")
            env_specs[spec_name] = WorkflowEnvVarSpec(
                name=spec_name,
                type=spec_type,  # type: ignore[arg-type]
                default_value=default_value,
                description=description,
            )

        variable_name = str(
            node_cfg.get("variable_name", node_cfg.get("variableName", ""))
            or ""
        ).strip()
        operation = str(node_cfg.get("operation", "set") or "set").strip().lower()
        value_template = str(
            node_cfg.get("value_template", node_cfg.get("valueTemplate", ""))
            or ""
        )
        operand: Any = None
        if operation != "clear":
            rendered_value = resolve_node_template_vars(
                value_template,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            operand = parse_loose_json_value(rendered_value)

        emit(metadata, "on_node_start", node_id=node_id, node_type="variable_assign")
        try:
            updated_env_vars, before_value, after_value = apply_env_var_operation(
                env_vars,
                env_specs,
                variable_name=variable_name,
                operation=operation,
                operand=operand,
            )
        except Exception as exc:
            emit(metadata, "on_node_end", node_id=node_id, status="error")
            raise RuntimeError(f"DAG variable_assign node {node_id} failed: {exc}") from exc

        payload = {
            "variable": variable_name,
            "operation": operation,
            "before": before_value,
            "after": after_value,
        }
        json_text = json.dumps(payload, ensure_ascii=False)
        node_out: NodeOutput = {
            "status": "ok",
            "text": json_text,
            "raw": payload,
            "json_fields": {
                **payload,
                "response": json_text,
            },
        }
        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
            "env_vars": updated_env_vars,
        }

    return variable_assign_node
