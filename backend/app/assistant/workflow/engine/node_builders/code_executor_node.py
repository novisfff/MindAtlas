from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.code_executor import CodeExecutionError, execute_code
from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    get_start_inputs,
    parse_loose_json_value,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_code_executor_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def code_executor_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}

        language = str(node_cfg.get("language", "python") or "python").strip().lower()
        code_text = node_cfg.get("code")
        if not isinstance(code_text, str) or not code_text.strip():
            raise RuntimeError(f"DAG code_executor node {node_id}: code is required")
        entrypoint = str(node_cfg.get("entrypoint", "main") or "main").strip() or "main"
        timeout_raw = node_cfg.get("timeout_ms", node_cfg.get("timeoutMs"))
        timeout_ms: int | None = None
        if timeout_raw is not None and str(timeout_raw).strip():
            try:
                timeout_ms = int(timeout_raw)
            except Exception:
                timeout_ms = None

        input_bindings = node_cfg.get("input_bindings", node_cfg.get("inputBindings"))
        if not isinstance(input_bindings, dict):
            raise RuntimeError(
                f"DAG code_executor node {node_id} requires inputBindings object"
            )
        resolved_inputs: dict[str, Any] = {}
        for key, raw_tpl in input_bindings.items():
            binding_key = str(key or "").strip()
            if not binding_key:
                continue
            if isinstance(raw_tpl, str):
                rendered = resolve_node_template_vars(
                    raw_tpl,
                    node_outputs,
                    start_inputs,
                    sys_vars,
                    env_vars=env_vars,
                )
                resolved_inputs[binding_key] = parse_loose_json_value(rendered)
            elif raw_tpl is None:
                resolved_inputs[binding_key] = ""
            else:
                resolved_inputs[binding_key] = raw_tpl

        output_fields = node_cfg.get("output_fields", node_cfg.get("outputFields"))
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError(
                f"DAG code_executor node {node_id} requires outputFields list"
            )
        normalized_output_fields = [field for field in output_fields if isinstance(field, dict)]
        if not normalized_output_fields:
            raise RuntimeError(
                f"DAG code_executor node {node_id} requires valid outputFields items"
            )

        emit(metadata, "on_node_start", node_id=node_id, node_type="code_executor")
        try:
            executed = execute_code(
                language=language,
                code=code_text,
                entrypoint=entrypoint,
                inputs=resolved_inputs,
                output_fields=normalized_output_fields,
                timeout_ms=timeout_ms,
            )
        except CodeExecutionError as exc:
            emit(metadata, "on_node_end", node_id=node_id, status="error")
            raise RuntimeError(f"DAG code_executor node {node_id} failed: {exc}") from exc

        payload = dict(executed.output)
        json_text = json.dumps(payload, ensure_ascii=False)
        json_fields: dict[str, Any] = dict(payload)
        json_fields["response"] = json_text
        if executed.stdout:
            json_fields["_stdout"] = executed.stdout
        if executed.stderr:
            json_fields["_stderr"] = executed.stderr

        node_out: NodeOutput = {
            "status": "ok",
            "text": json_text,
            "raw": payload,
            "json_fields": json_fields,
        }
        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return code_executor_node
