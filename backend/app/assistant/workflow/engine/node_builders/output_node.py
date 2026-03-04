from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.engine.runtime_helpers import (
    coerce_output_field_value,
    emit,
    extract_single_template_reference,
    get_start_inputs,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState


_OUTPUT_STREAM_CHUNK_SIZE = 12


def _iter_stream_chunks(text: str) -> list[str]:
    value = str(text or "")
    if not value:
        return []
    return [value[i:i + _OUTPUT_STREAM_CHUNK_SIZE] for i in range(0, len(value), _OUTPUT_STREAM_CHUNK_SIZE)]


def build_output_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def output_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}
        workflow_node_types = state.get("workflow_node_types", {}) or {}
        stream_output_enabled = bool(state.get("stream_output_enabled", True))
        output_stream_source_node_id = str(state.get("output_stream_source_node_id", "") or "")

        output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        if output_mode == "json":
            output_mode = "structured"
        if output_mode not in {"text", "structured"}:
            raise RuntimeError(f"DAG output node {node_id}: unsupported output_mode={output_mode}")

        emit(metadata, "on_node_start", node_id=node_id, node_type="output")

        if output_mode == "text":
            text_template = node_cfg.get("text_template", "{{start.user_input}}")
            if not isinstance(text_template, str):
                raise RuntimeError(
                    f"DAG output node {node_id}: textTemplate must be string in text mode"
                )

            rendered_text = resolve_node_template_vars(
                text_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
            )
            node_out: NodeOutput = {
                "status": "ok",
                "text": rendered_text,
                "raw": rendered_text,
                "json_fields": {"response": rendered_text},
            }

            # When output is a direct single-ref passthrough from an LLM source, LLM node streams tokens.
            single_ref = extract_single_template_reference(text_template)
            should_skip_final_emit = (
                stream_output_enabled
                and single_ref is not None
                and single_ref[0] == output_stream_source_node_id
                and workflow_node_types.get(single_ref[0]) == "llm"
                and single_ref[1] in {"response", "text"}
            )
            if not should_skip_final_emit and rendered_text:
                if stream_output_enabled:
                    for chunk in _iter_stream_chunks(rendered_text):
                        emit(
                            metadata,
                            "on_content_delta",
                            chunk=chunk,
                            source_node_id=node_id,
                            source_node_type="output",
                        )
                else:
                    emit(
                        metadata,
                        "on_content_delta",
                        chunk=rendered_text,
                        source_node_id=node_id,
                        source_node_type="output",
                    )

            emit(metadata, "on_node_end", node_id=node_id, status="ok")
            return {
                "node_outputs": {node_id: node_out},
                "execution_trace": [node_id],
            }

        output_fields = node_cfg.get("output_fields")
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError(
                f"DAG output node {node_id}: structured mode requires output_fields"
            )

        structured_payload: dict[str, Any] = {}
        for raw_field in output_fields:
            if not isinstance(raw_field, dict):
                raise RuntimeError(
                    f"DAG output node {node_id}: output_fields items must be objects"
                )
            field_name = str(raw_field.get("name", "") or "").strip()
            if not field_name:
                raise RuntimeError(f"DAG output node {node_id}: output field name is required")
            value_template = raw_field.get("value", "")
            if not isinstance(value_template, str):
                raise RuntimeError(
                    f"DAG output node {node_id}: output field '{field_name}' requires string value"
                )
            rendered_value = resolve_node_template_vars(
                value_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
            )
            try:
                coerced = coerce_output_field_value(field_name, rendered_value, raw_field)
            except Exception as exc:
                raise RuntimeError(
                    f"DAG output node {node_id}: output field '{field_name}' invalid value: {exc}"
                ) from exc
            structured_payload[field_name] = coerced

        json_text = json.dumps(structured_payload, ensure_ascii=False)
        json_fields = dict(structured_payload)
        json_fields["response"] = json_text
        node_out = NodeOutput(
            status="ok",
            text=json_text,
            raw=structured_payload,
            json_fields=json_fields,
        )
        if json_text:
            if stream_output_enabled:
                for chunk in _iter_stream_chunks(json_text):
                    emit(
                        metadata,
                        "on_content_delta",
                        chunk=chunk,
                        source_node_id=node_id,
                        source_node_type="output",
                    )
            else:
                emit(
                    metadata,
                    "on_content_delta",
                    chunk=json_text,
                    source_node_id=node_id,
                    source_node_type="output",
                )

        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return output_node
