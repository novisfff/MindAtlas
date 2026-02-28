from __future__ import annotations

from typing import Callable

from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    eval_condition,
    get_start_inputs,
    normalize_if_else_config,
    normalize_if_else_operator,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_if_else_node(
    node_id: str,
    node_cfg: dict,
) -> Callable[[WorkflowState], dict]:
    def if_else_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}
        normalized_cfg = normalize_if_else_config(node_cfg)
        branches = normalized_cfg.get("branches", [])
        else_handle = str(normalized_cfg.get("else_handle") or "else")

        emit(metadata, "on_node_start", node_id=node_id, node_type="if_else")

        chosen_handle = else_handle
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            branch_handle = str(branch.get("id") or "").strip()
            if not branch_handle:
                continue
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            branch_conditions = branch.get("conditions")
            if not isinstance(branch_conditions, list) or not branch_conditions:
                continue

            results: list[bool] = []
            for cond in branch_conditions:
                if not isinstance(cond, dict):
                    continue
                variable = str(cond.get("variable") or "").strip()
                operator = normalize_if_else_operator(cond.get("operator"))
                value_template = cond.get("value")
                rhs_template = "" if value_template is None else str(value_template)
                rhs_value = resolve_node_template_vars(
                    rhs_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
                )

                actual_value = ""
                if variable.startswith("sys."):
                    sys_key = variable.split(".", 1)[1] if "." in variable else ""
                    actual_value = str(sys_vars.get(sys_key, "") or "")
                elif variable.startswith("env."):
                    env_key = variable.split(".", 1)[1] if "." in variable else ""
                    actual = env_vars.get(env_key, "")
                    actual_value = str(actual) if actual is not None else ""
                else:
                    parts = variable.split(".", 1)
                    ref_node = parts[0]
                    ref_field = parts[1] if len(parts) > 1 else "text"

                    out = node_outputs.get(ref_node, {})
                    actual = out.get("json_fields", {}).get(ref_field, out.get("text", ""))
                    actual_value = str(actual) if actual is not None else ""

                results.append(eval_condition(actual_value, operator, rhs_value))

            if not results:
                continue
            matched = all(results) if logic == "and" else any(results)
            if matched:
                chosen_handle = branch_handle
                break

        emit(metadata, "on_branch_decision", node_id=node_id, handle=chosen_handle)
        emit(metadata, "on_node_end", node_id=node_id, status="ok")

        node_out = NodeOutput(status="ok", text=chosen_handle, raw=chosen_handle, json_fields={"handle": chosen_handle})

        return {
            "node_outputs": {node_id: node_out},
            "branch_decisions": {node_id: chosen_handle},
            "execution_trace": [node_id],
        }
    return if_else_node
