from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine.container_runtime import execute_container_body
from app.assistant.workflow.engine.runtime_helpers import (
    cfg_bool_value,
    coerce_array_input,
    emit,
    get_start_inputs,
    parse_loose_json_value,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_iteration_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def iteration_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = dict(state.get("env_vars", {}) or {})

        input_source_tpl = str(node_cfg.get("input_source", node_cfg.get("inputSource", "")) or "").strip()
        if not input_source_tpl:
            raise RuntimeError(f"DAG iteration node {node_id}: inputSource is required")
        rendered_input = resolve_node_template_vars(
            input_source_tpl,
            node_outputs,
            start_inputs,
            sys_vars,
            env_vars=env_vars,
        )
        items = coerce_array_input(parse_loose_json_value(rendered_input))
        output_variable = str(node_cfg.get("output_variable", node_cfg.get("outputVariable", "results")) or "results").strip() or "results"
        output_selector_tpl = str(node_cfg.get("output_selector", node_cfg.get("outputSelector", "{{container.item}}")) or "{{container.item}}")
        parallel_mode = cfg_bool_value(node_cfg, "parallel_mode", "parallelMode", default=False)
        error_strategy = str(node_cfg.get("error_strategy", node_cfg.get("errorStrategy", "fail_fast")) or "fail_fast").strip().lower()
        flatten_output = cfg_bool_value(node_cfg, "flatten_output", "flattenOutput", default=True)

        emit(metadata, "on_node_start", node_id=node_id, node_type="iteration")

        aggregated: list[Any] = []
        errors_payload: list[dict[str, Any]] = []

        def _run_single(
            index: int,
            item: Any,
            env_snapshot: dict[str, Any],
        ) -> tuple[int, Any, dict[str, Any], dict[str, Any]]:
            container_fields = {"item": item, "index": index}
            parent_state_for_item: WorkflowState = dict(state)
            parent_state_for_item["env_vars"] = dict(env_snapshot)
            body_result = execute_container_body(
                container_node_id=node_id,
                container_node_type="iteration",
                node_cfg=node_cfg,
                parent_state=parent_state_for_item,
                llm=llm,
                args_llm=args_llm,
                tool_map=tool_map,
                db_bind=db_bind,
                node_llms=node_llms,
                container_input=item,
                container_fields=container_fields,
            )
            next_env_vars = dict(body_result.get("env_vars", env_snapshot) or env_snapshot)
            selected_text = resolve_node_template_vars(
                output_selector_tpl,
                body_result.get("all_node_outputs", {}),
                {"user_input": item, "item": item, "index": index},
                sys_vars,
                container_fields=container_fields,
                env_vars=next_env_vars,
            )
            selected_value = parse_loose_json_value(selected_text)
            updates: dict[str, Any] = {}
            for key, value in next_env_vars.items():
                if env_snapshot.get(key) != value:
                    updates[key] = value
            return index, selected_value, next_env_vars, updates

        current_env_vars = dict(env_vars)

        if parallel_mode and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
                base_env_snapshot = dict(current_env_vars)
                futures = {
                    executor.submit(_run_single, index, item, base_env_snapshot): (index, item)
                    for index, item in enumerate(items)
                }
                results_by_index: dict[int, Any] = {}
                env_updates_by_index: dict[int, dict[str, Any]] = {}
                for future in as_completed(futures):
                    index, item = futures[future]
                    try:
                        _, value, _, updates = future.result()
                        results_by_index[index] = value
                        env_updates_by_index[index] = updates
                    except Exception as exc:
                        err_item = {"index": index, "item": item, "error": str(exc)}
                        if error_strategy == "skip_item":
                            errors_payload.append(err_item)
                            continue
                        raise RuntimeError(f"DAG iteration node {node_id} failed at index {index}: {exc}") from exc
                for index in range(len(items)):
                    if index in results_by_index:
                        aggregated.append(results_by_index[index])
                    if index in env_updates_by_index:
                        current_env_vars.update(env_updates_by_index[index])
        else:
            for index, item in enumerate(items):
                try:
                    _, value, next_env_vars, _ = _run_single(index, item, current_env_vars)
                    aggregated.append(value)
                    current_env_vars = next_env_vars
                except Exception as exc:
                    err_item = {"index": index, "item": item, "error": str(exc)}
                    if error_strategy == "skip_item":
                        errors_payload.append(err_item)
                        continue
                    raise RuntimeError(f"DAG iteration node {node_id} failed at index {index}: {exc}") from exc

        if flatten_output:
            flattened: list[Any] = []
            for item in aggregated:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            aggregated = flattened

        raw_payload = {
            "items": aggregated,
            "count": len(aggregated),
            "errors": errors_payload,
        }
        node_out = NodeOutput(
            status="ok",
            text=json.dumps(raw_payload, ensure_ascii=False),
            raw=raw_payload,
            json_fields={
                output_variable: aggregated,
                "count": len(aggregated),
                "errors": errors_payload,
            },
        )

        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
            "env_vars": current_env_vars,
        }

    return iteration_node
