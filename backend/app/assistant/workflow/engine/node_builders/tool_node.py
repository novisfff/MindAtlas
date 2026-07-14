from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from langchain_openai import ChatOpenAI

from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine import engine as engine_runtime
from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    extract_single_template_reference,
    get_start_inputs,
    logger,
    resolve_node_template_vars,
    stringify,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState

def build_dag_tool_node(
    node_id: str,
    node_cfg: dict,
    tool_map: dict[str, Any],
    args_llm: ChatOpenAI,
    db_bind: Any,
    execution_scope: Any | None = None,
    container_node_id: str | None = None,
) -> Callable[[WorkflowState], dict]:
    def dag_tool_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        locale = sys_vars.get("locale")
        env_vars = state.get("env_vars", {}) or {}
        tool_name = node_cfg.get("tool_name", "")
        tool_call_id = f"tool_{uuid.uuid4().hex[:8]}"
        container_id = str(
            container_node_id
            or node_cfg.get("__container_node_id")
            or node_cfg.get("container_node_id")
            or ""
        ).strip() or None

        tool = None
        if execution_scope is not None:
            # Exact closure only — never parent tool_map / Registry name fallback under Capability scope.
            # Plan 01 freezes body tools as root/node:{container}/body/node:{child}/tool:{name}.
            locator_candidates: list[str] = []
            if container_id:
                locator_candidates.append(
                    f"root/node:{container_id}/body/node:{node_id}/tool:{tool_name}"
                )
            locator_candidates.extend(
                (
                    f"root/node:{node_id}/tool:{tool_name}",
                    f"root/tool:{tool_name}",
                )
            )
            last_exc: BaseException | None = None
            for locator in locator_candidates:
                try:
                    target = execution_scope.dependency_resolver.require_tool(
                        source_locator=locator,
                        tool_name=str(tool_name or ""),
                    )
                    tool = getattr(target, "tool_object_or_record", target)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    continue
            if not tool:
                if getattr(execution_scope, "safe_diagnostics", False):
                    logger.error(
                        "capability_safe_execution stage=tool_node_resolve node_id=%s exc_class=%s",
                        node_id,
                        type(last_exc).__name__ if last_exc is not None else "LookupError",
                    )
                raise RuntimeError(
                    f"DAG tool node {node_id}: tool not found under capability scope"
                ) from None
        else:
            tool = tool_map.get(tool_name)
        if not tool:
            raise RuntimeError(f"DAG tool node {node_id}: tool not found: {tool_name}")

        input_bindings = node_cfg.get("input_bindings")
        if not isinstance(input_bindings, dict):
            raise RuntimeError(
                f"DAG tool node {node_id} requires inputBindings object; legacy argsFrom/argsTemplate are no longer supported"
            )

        def _resolve_binding_value(raw_template: str) -> Any:
            single_ref = extract_single_template_reference(raw_template)
            if single_ref is None:
                return resolve_node_template_vars(
                    raw_template, node_outputs, start_inputs, sys_vars, env_vars=env_vars
                )

            ref_node_id, ref_field = single_ref
            if ref_node_id == "start":
                return start_inputs.get(ref_field, "")
            if ref_node_id == "sys":
                return sys_vars.get(ref_field, "")
            if ref_node_id == "env":
                return env_vars.get(ref_field, "")
            if ref_node_id == "container":
                container_out = node_outputs.get("container", {}) if isinstance(node_outputs.get("container", {}), dict) else {}
                container_json = container_out.get("json_fields", {}) if isinstance(container_out.get("json_fields"), dict) else {}
                return container_json.get(ref_field, container_out.get("text", ""))

            out = node_outputs.get(ref_node_id)
            if not isinstance(out, dict):
                return ""
            if ref_field == "text":
                return out.get("text", "")
            if ref_field == "raw":
                return out.get("raw")
            json_fields = out.get("json_fields", {})
            if isinstance(json_fields, dict) and ref_field in json_fields:
                return json_fields.get(ref_field)
            return out.get("text", "")

        args: dict[str, Any] = {}
        for k, raw_tpl in input_bindings.items():
            key = str(k).strip() if isinstance(k, str) else ""
            if not key:
                continue
            if isinstance(raw_tpl, str):
                args[key] = _resolve_binding_value(raw_tpl)
            elif raw_tpl is None:
                args[key] = ""
            else:
                args[key] = str(raw_tpl)

        emit(metadata, "on_node_start", node_id=node_id, node_type="tool")

        wrapped = engine_runtime._wrap_tool_with_db(tool, db_bind)
        status = "ok"
        call_args = dict(args)
        tool_call_started = False
        try:
            call_args = engine_runtime._coerce_tool_args(tool, args)
            emit(
                metadata,
                "on_tool_call_start",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=call_args,
            )
            tool_call_started = True
            result = wrapped(**call_args)
        except Exception as e:
            if not tool_call_started:
                emit(
                    metadata,
                    "on_tool_call_start",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args={} if execution_scope is not None and getattr(execution_scope, "safe_diagnostics", False) else call_args,
                )
            if execution_scope is not None and getattr(execution_scope, "safe_diagnostics", False):
                logger.error(
                    "capability_safe_execution stage=tool_node_invoke node_id=%s exc_class=%s",
                    node_id,
                    type(e).__name__,
                )
                status = "error"
                result = "tool execution failed"
            else:
                logger.error("DAG tool %s failed: %s", tool_name, e)
                status = "error"
                result = _copy.build_tool_execution_failed_message(locale, e)

        result_str = stringify(result)
        raw: Any = result
        if isinstance(result, str):
            s = result.strip()
            if s.startswith("{") or s.startswith("["):
                try:
                    raw = json.loads(s)
                except Exception:
                    pass

        emit(metadata, "on_tool_call_end", tool_call_id=tool_call_id,
              status="completed" if status == "ok" else "error", result=result_str)

        json_fields: dict[str, Any] = {
            "result": raw if not isinstance(raw, str) else result_str,
        }
        output_param_names = engine_runtime._resolve_tool_output_param_names(tool_name, tool)
        if isinstance(raw, dict):
            for field_name in output_param_names:
                json_fields[field_name] = raw.get(field_name)
        elif isinstance(raw, list) and "items" in output_param_names:
            json_fields["items"] = raw

        node_out: NodeOutput = {"status": status, "text": result_str, "raw": raw, "json_fields": json_fields}
        emit(metadata, "on_node_end", node_id=node_id, status=status)

        if status == "error":
            raise RuntimeError(f"DAG tool node {node_id} failed: {result_str}")

        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }
    return dag_tool_node
