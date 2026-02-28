from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.engine.runtime_helpers import (
    cfg_bool_value,
    coerce_human_field_value,
    emit,
    get_start_inputs,
    normalize_human_in_loop_fields,
    parse_human_field_options_from_rendered,
    parse_loose_json_value,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.assistant.workflow.human_approval_runtime import HumanLoopRuntime

def build_human_in_loop_node(
    node_id: str,
    node_cfg: dict[str, Any],
) -> Callable[[WorkflowState], dict]:
    def human_in_loop_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        runtime = metadata.get("human_loop_runtime")
        if not isinstance(runtime, HumanLoopRuntime):
            raise RuntimeError(
                f"DAG human_in_loop node {node_id}: human loop runtime is unavailable"
            )

        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}

        instruction = str(node_cfg.get("instruction", "") or "").strip()
        if not instruction:
            raise RuntimeError(f"DAG human_in_loop node {node_id}: instruction is required")

        fields = normalize_human_in_loop_fields(node_cfg)
        if not fields:
            raise RuntimeError(f"DAG human_in_loop node {node_id}: fields are required")

        initial_values: dict[str, Any] = {}
        field_schema: list[dict[str, Any]] = []
        for field in fields:
            field_name = str(field.get("name", "") or "").strip()
            if not field_name:
                continue
            field_type = str(field.get("type", "string") or "string").strip().lower() or "string"
            field_widget = str(field.get("widget", "") or "").strip().lower() or ("switch" if field_type == "boolean" else "input")
            value_template = str(field.get("value_template", "") or "")
            rendered = resolve_node_template_vars(
                value_template,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            parsed = parse_loose_json_value(rendered)
            try:
                initial_values[field_name] = coerce_human_field_value(field_name, field_type, parsed)
            except Exception:
                initial_values[field_name] = rendered

            resolved_options = list(field.get("options", []) or [])
            options_template = str(field.get("options_template", "") or "")
            if options_template.strip():
                rendered_options = resolve_node_template_vars(
                    options_template,
                    node_outputs,
                    start_inputs,
                    sys_vars,
                    env_vars=env_vars,
                )
                parsed_options = parse_human_field_options_from_rendered(
                    rendered=rendered_options,
                    field_name=field_name,
                    option_value_key=str(field.get("option_value_key", "") or ""),
                )
                if parsed_options:
                    resolved_options = parsed_options

            if field_widget in {"select", "radio"} and not resolved_options:
                raise RuntimeError(
                    f"DAG human_in_loop node {node_id}: field '{field_name}' has no options after template resolution"
                )

            field_schema.append(
                {
                    "name": field_name,
                    "label": str(field.get("label", "") or ""),
                    "type": field_type,
                    "widget": field_widget,
                    "options": resolved_options,
                    "allowCustom": bool(field.get("allow_custom", False)),
                    "placeholder": str(field.get("placeholder", "") or ""),
                    "required": bool(field.get("required", False)),
                }
            )

        request_payload = {
            "title": str(node_cfg.get("title", "") or ""),
            "instruction": instruction,
            "approveLabel": str(node_cfg.get("approve_label", node_cfg.get("approveLabel", "")) or ""),
            "rejectLabel": str(node_cfg.get("reject_label", node_cfg.get("rejectLabel", "")) or ""),
            "requireRejectComment": cfg_bool_value(
                node_cfg,
                "require_reject_comment",
                "requireRejectComment",
                default=True,
            ),
        }

        emit(metadata, "on_node_start", node_id=node_id, node_type="human_in_loop")
        approval_payload = runtime.create_and_wait(
            node_id=node_id,
            node_label=str(node_cfg.get("__node_label", "") or node_id),
            request_payload=request_payload,
            field_schema=field_schema,
            initial_values=initial_values,
        )

        decision = str(approval_payload.get("decision", "") or "").strip().lower()
        if decision not in {"approved", "rejected"}:
            emit(metadata, "on_node_end", node_id=node_id, status="error")
            raise RuntimeError(
                f"DAG human_in_loop node {node_id}: invalid approval decision: {decision or '<empty>'}"
            )

        submitted_values = approval_payload.get("submittedValues", {})
        if not isinstance(submitted_values, dict):
            submitted_values = {}
        final_values = dict(initial_values)
        final_values.update(submitted_values)
        comment = approval_payload.get("comment")
        comment_text = str(comment) if comment is not None else ""
        approval_id = str(approval_payload.get("id", "") or "")

        node_raw = {
            "decision": decision,
            "comment": comment_text,
            "values": final_values,
            "approval_id": approval_id,
            "submitted_values": submitted_values,
        }
        json_text = json.dumps(node_raw, ensure_ascii=False)
        node_json_fields: dict[str, Any] = {
            "decision": decision,
            "comment": comment_text,
            **final_values,
            "response": json_text,
        }

        emit(metadata, "on_branch_decision", node_id=node_id, handle=decision)
        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {
                node_id: NodeOutput(
                    status="ok",
                    text=json_text,
                    raw=node_raw,
                    json_fields=node_json_fields,
                )
            },
            "branch_decisions": {node_id: decision},
            "execution_trace": [node_id],
        }

    return human_in_loop_node
