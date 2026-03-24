from __future__ import annotations

import json
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    extract_json_object,
    get_start_inputs,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_param_extractor_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def param_extractor_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        locale = sys_vars.get("locale")
        env_vars = state.get("env_vars", {}) or {}
        runtime_node_llms = state.get("node_llms", {}) or {}
        if not isinstance(runtime_node_llms, dict):
            runtime_node_llms = {}
        llm_for_node = runtime_node_llms.get(node_id)
        if llm_for_node is None and node_llms is not None:
            llm_for_node = node_llms.get(node_id)
        if llm_for_node is None:
            llm_for_node = llm

        input_content_template = node_cfg.get("input_content")
        if input_content_template is None:
            input_content_template = node_cfg.get("inputContent", "")
        if not isinstance(input_content_template, str):
            input_content_template = ""
        input_content = resolve_node_template_vars(
            input_content_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
        )

        instruction_template = node_cfg.get("instruction", "")
        if not isinstance(instruction_template, str):
            instruction_template = ""
        instruction = resolve_node_template_vars(
            instruction_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
        )

        output_fields = node_cfg.get("output_fields")
        if output_fields is None:
            output_fields = node_cfg.get("outputFields")
        if not isinstance(output_fields, list) or not output_fields:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: output_fields must be non-empty"
            )

        from app.assistant.skill_catalog.base import OutputFieldSpec, build_json_output_constraint
        specs: list[OutputFieldSpec] = []
        for field in output_fields:
            if isinstance(field, str):
                name = field.strip()
                if name:
                    specs.append(OutputFieldSpec(name=name))
                continue
            if not isinstance(field, dict):
                continue

            payload = dict(field)
            if "itemsType" in payload and "items_type" not in payload:
                payload["items_type"] = payload.get("itemsType")
            try:
                specs.append(OutputFieldSpec(**payload))
            except Exception:
                name = str(payload.get("name", "") or "").strip()
                if name:
                    specs.append(OutputFieldSpec(name=name))

        if not specs:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: output_fields are invalid"
            )

        field_names = [spec.name for spec in specs]
        constraint = build_json_output_constraint(specs, locale=locale)
        system_prompt = _copy.build_param_extractor_system_prompt(
            locale=locale,
            instruction=instruction,
            constraint=constraint,
        )

        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"输入内容：\n{input_content}"},
        ]

        emit(metadata, "on_node_start", node_id=node_id, node_type="parameter_extractor")

        chunks: list[str] = []
        for chunk in llm_for_node.stream(msgs):
            if chunk.content:
                chunks.append(chunk.content)
                emit(metadata, "on_node_output_delta", node_id=node_id, delta=chunk.content)

        text = "".join(chunks).strip()
        parsed = extract_json_object(text)
        if parsed is None:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: model output must be a valid JSON object"
            )

        missing_fields = [name for name in field_names if name not in parsed]
        if missing_fields:
            raise RuntimeError(
                f"DAG parameter_extractor node {node_id}: missing output fields: {', '.join(missing_fields)}"
            )

        filtered = {name: parsed.get(name) for name in field_names}
        json_text = json.dumps(filtered, ensure_ascii=False)
        node_out: NodeOutput = {
            "status": "ok",
            "text": json_text,
            "raw": parsed,
            "json_fields": filtered,
        }

        node_outputs[node_id] = node_out
        emit(metadata, "on_node_end", node_id=node_id, status="ok")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return param_extractor_node
