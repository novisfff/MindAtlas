from __future__ import annotations

import json
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow.engine.container_runtime import execute_container_body
from app.assistant.workflow.engine.runtime_helpers import (
    cfg_int_value,
    cfg_list_value,
    emit,
    eval_condition,
    get_start_inputs,
    normalize_if_else_operator,
    parse_loose_json_value,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_loop_node(
    node_id: str,
    node_cfg: dict,
    llm: ChatOpenAI,
    args_llm: ChatOpenAI,
    tool_map: dict[str, Any],
    db_bind: Any,
    node_llms: dict[str, ChatOpenAI] | None = None,
) -> Callable[[WorkflowState], dict]:
    def loop_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = dict(state.get("env_vars", {}) or {})

        initial_vars = cfg_list_value(node_cfg, "initial_vars", "initialVars")
        update_mappings = cfg_list_value(node_cfg, "update_mappings", "updateMappings")
        termination_conditions = cfg_list_value(node_cfg, "termination_conditions", "terminationConditions")
        termination_logic = str(node_cfg.get("termination_logic", node_cfg.get("terminationLogic", "and")) or "and").strip().lower()
        if termination_logic not in {"and", "or"}:
            termination_logic = "and"
        max_iterations = cfg_int_value(
            node_cfg,
            "max_iterations",
            "maxIterations",
            default=10,
            min_value=1,
            max_value=1000,
        )

        loop_vars: dict[str, Any] = {}
        for raw in initial_vars:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name", "") or "").strip()
            if not name:
                continue
            value_tpl = str(raw.get("value", "") or "")
            rendered = resolve_node_template_vars(
                value_tpl,
                node_outputs,
                start_inputs,
                sys_vars,
                container_fields=loop_vars,
                env_vars=env_vars,
            )
            loop_vars[name] = parse_loose_json_value(rendered)

        emit(metadata, "on_node_start", node_id=node_id, node_type="loop")

        iteration_count = 0
        terminated = False
        last_item: Any = None
        iteration_outputs: list[Any] = []

        while iteration_count < max_iterations:
            container_fields = {"index": iteration_count, **loop_vars}
            body_result = execute_container_body(
                container_node_id=node_id,
                container_node_type="loop",
                node_cfg=node_cfg,
                parent_state={
                    **state,
                    "env_vars": env_vars,
                },
                llm=llm,
                args_llm=args_llm,
                tool_map=tool_map,
                db_bind=db_bind,
                node_llms=node_llms,
                container_input=start_inputs.get("user_input", state.get("user_input", "")),
                container_fields=container_fields,
            )
            env_vars = dict(body_result.get("env_vars", env_vars) or env_vars)
            iteration_outputs.append(body_result.get("node_outputs", {}))
            last_node_id = str(body_result.get("last_node_id", "") or "")
            last_out = (body_result.get("all_node_outputs", {}) or {}).get(last_node_id, {})
            last_item = last_out.get("raw", last_out.get("text"))

            for raw in update_mappings:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "") or "").strip()
                if not name:
                    continue
                value_tpl = str(raw.get("value", "") or "")
                rendered = resolve_node_template_vars(
                    value_tpl,
                    body_result.get("all_node_outputs", {}),
                    {"user_input": start_inputs.get("user_input", state.get("user_input", ""))},
                    sys_vars,
                    container_fields={"index": iteration_count, **loop_vars},
                    env_vars=env_vars,
                )
                loop_vars[name] = parse_loose_json_value(rendered)

            if termination_conditions:
                evaluated: list[bool] = []
                for cond in termination_conditions:
                    if not isinstance(cond, dict):
                        continue
                    variable = str(cond.get("variable", "") or "").strip()
                    operator = normalize_if_else_operator(cond.get("operator"))
                    value_tpl = "" if cond.get("value") is None else str(cond.get("value"))
                    rhs_value = resolve_node_template_vars(
                        value_tpl,
                        body_result.get("all_node_outputs", {}),
                        {"user_input": start_inputs.get("user_input", state.get("user_input", ""))},
                        sys_vars,
                        container_fields={"index": iteration_count, **loop_vars},
                        env_vars=env_vars,
                    )

                    actual_value = ""
                    if variable.startswith("sys."):
                        sys_key = variable.split(".", 1)[1] if "." in variable else ""
                        actual_value = str(sys_vars.get(sys_key, "") or "")
                    elif variable.startswith("container."):
                        var_key = variable.split(".", 1)[1] if "." in variable else ""
                        actual_value = str(container_fields.get(var_key, ""))
                    elif variable.startswith("env."):
                        env_key = variable.split(".", 1)[1] if "." in variable else ""
                        actual = env_vars.get(env_key, "")
                        actual_value = str(actual) if actual is not None else ""
                    else:
                        parts = variable.split(".", 1)
                        ref_node = parts[0]
                        ref_field = parts[1] if len(parts) > 1 else "text"
                        out = (body_result.get("all_node_outputs", {}) or {}).get(ref_node, {})
                        actual = out.get("json_fields", {}).get(ref_field, out.get("text", ""))
                        actual_value = str(actual) if actual is not None else ""

                    evaluated.append(eval_condition(actual_value, operator, rhs_value))

                if evaluated:
                    matched = all(evaluated) if termination_logic == "and" else any(evaluated)
                    if matched:
                        terminated = True
                        iteration_count += 1
                        break

            iteration_count += 1

        raw_payload = {
            "iterations": iteration_count,
            "terminated": terminated,
            "last_item": last_item,
            "vars": loop_vars,
        }
        json_fields = {
            "iterations": iteration_count,
            "terminated": terminated,
            "last_item": last_item,
            **loop_vars,
        }
        node_out = NodeOutput(
            status="ok",
            text=json.dumps(raw_payload, ensure_ascii=False),
            raw=raw_payload,
            json_fields=json_fields,
        )

        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
            "env_vars": env_vars,
        }

    return loop_node
