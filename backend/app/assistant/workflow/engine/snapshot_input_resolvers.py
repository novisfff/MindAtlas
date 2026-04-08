from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.assistant.workflow.engine import runtime_helpers as rt
from app.assistant.workflow.engine.state import WorkflowState


@dataclass(frozen=True)
class SnapshotInputContext:
    state: WorkflowState
    node_outputs: dict[str, Any]
    start_inputs: dict[str, Any]
    sys_vars: dict[str, Any]
    env_vars: dict[str, Any]
    env_specs: dict[str, Any]
    text_preview_limit: int


def build_node_snapshot_input(
    node_type: str,
    node_cfg: dict[str, Any],
    ctx: SnapshotInputContext,
) -> dict[str, Any]:
    if node_type == "start":
        input_mode = rt.resolve_start_input_mode(node_cfg)
        return {
            "inputMode": input_mode,
            "user_input": ctx.state.get("user_input", "") if input_mode == "text" else None,
            "structuredInput": ctx.state.get("structured_input", {}) if input_mode == "structured" else None,
            "sys_vars": ctx.sys_vars,
            "envVars": ctx.env_vars,
            "envSpecs": ctx.env_specs,
        }

    if node_type == "llm":
        user_input_template = node_cfg.get("user_input", "{{start.user_input}}")
        if not isinstance(user_input_template, str):
            user_input_template = "{{start.user_input}}"
        rendered_user_input = rt.resolve_node_template_vars(
            template=user_input_template,
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        system_prompt_template = node_cfg.get("system_prompt", "")
        if not isinstance(system_prompt_template, str):
            system_prompt_template = ""
        rendered_system_prompt = rt.resolve_node_template_vars(
            template=system_prompt_template,
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        raw_output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        output_mode = "structured" if raw_output_mode == "json" else raw_output_mode
        return {
            "systemPrompt": rendered_system_prompt,
            "userInput": rendered_user_input,
            "outputMode": output_mode,
            "knowledgeBindings": {
                "enabled": rt.cfg_bool_value(node_cfg, "knowledge_enabled", "knowledgeEnabled", default=False),
                "sourceNodeIds": rt.cfg_string_list(node_cfg, "knowledge_source_node_ids", "knowledgeSourceNodeIds"),
                "injectMode": str(
                    node_cfg.get("knowledge_inject_mode", node_cfg.get("knowledgeInjectMode", "references_only"))
                    or "references_only"
                ),
                "maxRefs": rt.cfg_int_value(
                    node_cfg,
                    "knowledge_max_refs",
                    "knowledgeMaxRefs",
                    default=20,
                    min_value=1,
                    max_value=100,
                ),
            },
        }

    if node_type == "agent":
        user_input_template = node_cfg.get("user_input", "{{start.user_input}}")
        if not isinstance(user_input_template, str):
            user_input_template = "{{start.user_input}}"
        rendered_user_input = rt.resolve_node_template_vars(
            template=user_input_template,
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        system_prompt_template = node_cfg.get("system_prompt", "")
        if not isinstance(system_prompt_template, str):
            system_prompt_template = ""
        rendered_system_prompt = rt.resolve_node_template_vars(
            template=system_prompt_template,
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        raw_top_k = node_cfg.get("knowledge_top_k", node_cfg.get("knowledgeTopK"))
        knowledge_top_k = None
        if raw_top_k is not None and str(raw_top_k).strip():
            try:
                knowledge_top_k = int(raw_top_k)
            except Exception:
                knowledge_top_k = None
        return {
            "systemPrompt": rendered_system_prompt,
            "userInput": rendered_user_input,
            "toolNames": rt.cfg_string_list(node_cfg, "tool_names", "toolNames"),
            "maxIterations": rt.cfg_int_value(
                node_cfg,
                "max_iterations",
                "maxIterations",
                default=12,
                min_value=1,
                max_value=20,
            ),
            "knowledge": {
                "enabled": rt.cfg_bool_value(node_cfg, "knowledge_enabled", "knowledgeEnabled", default=False),
                "mode": (
                    str(node_cfg.get("knowledge_mode", node_cfg.get("knowledgeMode", "")) or "").strip().lower()
                    or None
                ),
                "topK": knowledge_top_k,
            },
        }

    if node_type == "output":
        raw_output_mode = str(node_cfg.get("output_mode", "text") or "text").strip().lower()
        output_mode = "structured" if raw_output_mode == "json" else raw_output_mode
        if output_mode == "text":
            return {
                "outputMode": output_mode,
                "textTemplate": node_cfg.get("text_template", "{{start.user_input}}"),
            }
        output_fields = node_cfg.get("output_fields")
        if not isinstance(output_fields, list):
            output_fields = []
        return {
            "outputMode": output_mode,
            "outputFields": output_fields,
        }

    if node_type == "tool":
        input_bindings = node_cfg.get("input_bindings", node_cfg.get("inputBindings"))
        resolved_args: dict[str, Any] = {}
        if isinstance(input_bindings, dict):
            for key, raw_tpl in input_bindings.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                if isinstance(raw_tpl, str):
                    resolved_args[key_text] = rt.resolve_node_template_vars(
                        template=raw_tpl,
                        node_outputs=ctx.node_outputs,
                        start_inputs=ctx.start_inputs,
                        sys_vars=ctx.sys_vars,
                        env_vars=ctx.env_vars,
                    )
                elif raw_tpl is None:
                    resolved_args[key_text] = ""
                else:
                    resolved_args[key_text] = str(raw_tpl)
        return {
            "toolName": node_cfg.get("tool_name", ""),
            "resolvedArgs": resolved_args,
        }

    if node_type == "workflow_call":
        input_bindings = node_cfg.get("input_bindings", node_cfg.get("inputBindings"))
        resolved_inputs: dict[str, Any] = {}
        if isinstance(input_bindings, dict):
            for key, raw_tpl in input_bindings.items():
                binding_key = str(key or "").strip()
                if not binding_key:
                    continue
                if isinstance(raw_tpl, str):
                    resolved_inputs[binding_key] = rt.resolve_node_template_vars(
                        template=raw_tpl,
                        node_outputs=ctx.node_outputs,
                        start_inputs=ctx.start_inputs,
                        sys_vars=ctx.sys_vars,
                        env_vars=ctx.env_vars,
                    )
                elif raw_tpl is None:
                    resolved_inputs[binding_key] = ""
                else:
                    resolved_inputs[binding_key] = raw_tpl
        return {
            "targetWorkflowId": node_cfg.get("target_workflow_id", node_cfg.get("targetWorkflowId")),
            "bindingMode": node_cfg.get("binding_mode", node_cfg.get("bindingMode", "pinned")),
            "targetPublishedVersionId": node_cfg.get(
                "target_published_version_id",
                node_cfg.get("targetPublishedVersionId"),
            ),
            "resolvedInputs": resolved_inputs,
        }

    if node_type == "code_executor":
        input_bindings = node_cfg.get("input_bindings", node_cfg.get("inputBindings"))
        resolved_inputs: dict[str, Any] = {}
        if isinstance(input_bindings, dict):
            for key, raw_tpl in input_bindings.items():
                binding_key = str(key or "").strip()
                if not binding_key:
                    continue
                if isinstance(raw_tpl, str):
                    resolved_inputs[binding_key] = rt.resolve_node_template_vars(
                        template=raw_tpl,
                        node_outputs=ctx.node_outputs,
                        start_inputs=ctx.start_inputs,
                        sys_vars=ctx.sys_vars,
                        env_vars=ctx.env_vars,
                    )
                elif raw_tpl is None:
                    resolved_inputs[binding_key] = ""
                else:
                    resolved_inputs[binding_key] = raw_tpl
        return {
            "language": str(node_cfg.get("language", "python") or "python").strip().lower(),
            "entrypoint": str(node_cfg.get("entrypoint", "main") or "main").strip() or "main",
            "timeoutMs": node_cfg.get("timeout_ms", node_cfg.get("timeoutMs")),
            "resolvedInputs": resolved_inputs,
            "outputFields": node_cfg.get("output_fields", node_cfg.get("outputFields", [])),
        }

    if node_type == "http_request":
        def _resolve_rows(raw_rows: Any) -> list[dict[str, Any]]:
            if not isinstance(raw_rows, list):
                return []
            resolved: list[dict[str, Any]] = []
            for item in raw_rows:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key", "") or "").strip()
                if not key:
                    continue
                enabled = item.get("enabled", True)
                value_raw = item.get("value")
                if isinstance(value_raw, str):
                    value = rt.resolve_node_template_vars(
                        template=value_raw,
                        node_outputs=ctx.node_outputs,
                        start_inputs=ctx.start_inputs,
                        sys_vars=ctx.sys_vars,
                        env_vars=ctx.env_vars,
                    )
                elif value_raw is None:
                    value = ""
                else:
                    value = str(value_raw)
                row: dict[str, Any] = {
                    "key": key,
                    "value": value,
                    "enabled": enabled if isinstance(enabled, bool) else True,
                }
                row_type = item.get("type")
                if isinstance(row_type, str):
                    normalized_type = row_type.strip().lower()
                    if normalized_type in {"text", "file"}:
                        row["type"] = normalized_type
                resolved.append(row)
            return resolved

        raw_url = node_cfg.get("url", "")
        resolved_url = rt.resolve_node_template_vars(
            template=str(raw_url or ""),
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        raw_json_body = node_cfg.get("json_body_template", node_cfg.get("jsonBodyTemplate", ""))
        raw_raw_body = node_cfg.get("raw_body_template", node_cfg.get("rawBodyTemplate", ""))
        resolved_json_body = rt.resolve_node_template_vars(
            template=str(raw_json_body or ""),
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        resolved_raw_body = rt.resolve_node_template_vars(
            template=str(raw_raw_body or ""),
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        resolved_bearer_token = rt.resolve_node_template_vars(
            template=str(node_cfg.get("bearer_token", node_cfg.get("bearerToken", "")) or ""),
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        resolved_api_key_value = rt.resolve_node_template_vars(
            template=str(node_cfg.get("api_key_value", node_cfg.get("apiKeyValue", "")) or ""),
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        body_type = str(node_cfg.get("body_type", node_cfg.get("bodyType", "none")) or "none").strip().lower()
        body_preview = ""
        if body_type == "json":
            body_preview = rt.truncate(resolved_json_body, ctx.text_preview_limit)
        elif body_type == "raw":
            body_preview = rt.truncate(resolved_raw_body, ctx.text_preview_limit)
        elif body_type == "x-www-form-urlencoded":
            body_preview = rt.truncate(
                rt.stringify(_resolve_rows(node_cfg.get("form_body", node_cfg.get("formBody")))),
                ctx.text_preview_limit,
            )
        elif body_type == "form-data":
            body_preview = rt.truncate(
                rt.stringify(_resolve_rows(node_cfg.get("form_body", node_cfg.get("formBody")))),
                ctx.text_preview_limit,
            )
        return {
            "method": str(node_cfg.get("method", "GET") or "GET").strip().upper(),
            "url": resolved_url,
            "headers": _resolve_rows(node_cfg.get("headers")),
            "queryParams": _resolve_rows(node_cfg.get("query_params", node_cfg.get("queryParams"))),
            "bodyType": body_type,
            "bodyPreview": body_preview,
            "auth": {
                "authType": str(node_cfg.get("auth_type", node_cfg.get("authType", "none")) or "none"),
                "apiKeyIn": str(node_cfg.get("api_key_in", node_cfg.get("apiKeyIn", "header")) or "header"),
                "apiKeyName": str(node_cfg.get("api_key_name", node_cfg.get("apiKeyName", "X-API-Key")) or "X-API-Key"),
                "bearerToken": rt.truncate(resolved_bearer_token, 120),
                "apiKeyValue": rt.truncate(resolved_api_key_value, 120),
            },
            "timeoutMs": node_cfg.get("timeout_ms", node_cfg.get("timeoutMs", 15000)),
            "retryEnabled": rt.cfg_bool_value(node_cfg, "retry_enabled", "retryEnabled", default=False),
            "maxRetries": node_cfg.get("max_retries", node_cfg.get("maxRetries", 2)),
            "retryIntervalMs": node_cfg.get("retry_interval_ms", node_cfg.get("retryIntervalMs", 200)),
            "verifySsl": rt.cfg_bool_value(node_cfg, "verify_ssl", "verifySsl", default=True),
        }

    if node_type == "if_else":
        normalized_cfg = rt.normalize_if_else_config(node_cfg)
        branches = normalized_cfg.get("branches", [])
        summarized_branches: list[dict[str, Any]] = []
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            conditions = branch.get("conditions")
            summarized_conditions: list[dict[str, Any]] = []
            if isinstance(conditions, list):
                for cond in conditions:
                    if not isinstance(cond, dict):
                        continue
                    value_template = cond.get("value")
                    rhs_template = "" if value_template is None else str(value_template)
                    rhs_value = rt.resolve_node_template_vars(
                        template=rhs_template,
                        node_outputs=ctx.node_outputs,
                        start_inputs=ctx.start_inputs,
                        sys_vars=ctx.sys_vars,
                        env_vars=ctx.env_vars,
                    )
                    summarized_conditions.append(
                        {
                            "variable": str(cond.get("variable", "") or ""),
                            "operator": rt.normalize_if_else_operator(cond.get("operator")),
                            "value": rhs_value,
                        }
                    )
            summarized_branches.append(
                {
                    "id": str(branch.get("id", "") or ""),
                    "logic": str(branch.get("logic", "and") or "and"),
                    "conditions": summarized_conditions,
                }
            )
        return {
            "elseHandle": normalized_cfg.get("else_handle", "else"),
            "branches": summarized_branches,
        }

    if node_type == "parameter_extractor":
        input_content_template = node_cfg.get("input_content", node_cfg.get("inputContent", ""))
        if not isinstance(input_content_template, str):
            input_content_template = ""
        instruction_template = node_cfg.get("instruction", "")
        if not isinstance(instruction_template, str):
            instruction_template = ""
        output_fields = node_cfg.get("output_fields")
        if output_fields is None:
            output_fields = node_cfg.get("outputFields")
        if not isinstance(output_fields, list):
            output_fields = []
        return {
            "inputContent": rt.resolve_node_template_vars(
                template=input_content_template,
                node_outputs=ctx.node_outputs,
                start_inputs=ctx.start_inputs,
                sys_vars=ctx.sys_vars,
                env_vars=ctx.env_vars,
            ),
            "instruction": rt.resolve_node_template_vars(
                template=instruction_template,
                node_outputs=ctx.node_outputs,
                start_inputs=ctx.start_inputs,
                sys_vars=ctx.sys_vars,
                env_vars=ctx.env_vars,
            ),
            "outputFields": output_fields,
        }

    if node_type == "knowledge_retrieval":
        query_template = node_cfg.get("query", "{{start.user_input}}")
        if not isinstance(query_template, str):
            query_template = "{{start.user_input}}"
        raw_top_k = node_cfg.get("top_k", node_cfg.get("topK"))
        top_k = None
        if raw_top_k is not None and str(raw_top_k).strip():
            try:
                top_k = int(raw_top_k)
            except Exception:
                top_k = None
        return {
            "query": rt.resolve_node_template_vars(
                template=query_template,
                node_outputs=ctx.node_outputs,
                start_inputs=ctx.start_inputs,
                sys_vars=ctx.sys_vars,
                env_vars=ctx.env_vars,
            ),
            "mode": node_cfg.get("mode"),
            "topK": top_k,
        }

    if node_type == "iteration":
        input_source_tpl = str(node_cfg.get("input_source", node_cfg.get("inputSource", "")) or "")
        rendered_input = rt.resolve_node_template_vars(
            template=input_source_tpl,
            node_outputs=ctx.node_outputs,
            start_inputs=ctx.start_inputs,
            sys_vars=ctx.sys_vars,
            env_vars=ctx.env_vars,
        )
        return {
            "inputSource": input_source_tpl,
            "resolvedInput": rt.truncate(rendered_input, ctx.text_preview_limit),
            "outputVariable": str(node_cfg.get("output_variable", node_cfg.get("outputVariable", "results")) or "results"),
            "outputSelector": str(node_cfg.get("output_selector", node_cfg.get("outputSelector", "{{container.item}}")) or "{{container.item}}"),
            "parallelMode": rt.cfg_bool_value(node_cfg, "parallel_mode", "parallelMode", default=False),
            "errorStrategy": str(node_cfg.get("error_strategy", node_cfg.get("errorStrategy", "fail_fast")) or "fail_fast"),
            "flattenOutput": rt.cfg_bool_value(node_cfg, "flatten_output", "flattenOutput", default=True),
        }

    if node_type == "loop":
        return {
            "initialVars": rt.cfg_list_value(node_cfg, "initial_vars", "initialVars"),
            "updateMappings": rt.cfg_list_value(node_cfg, "update_mappings", "updateMappings"),
            "terminationLogic": str(node_cfg.get("termination_logic", node_cfg.get("terminationLogic", "and")) or "and"),
            "terminationConditions": rt.cfg_list_value(node_cfg, "termination_conditions", "terminationConditions"),
            "maxIterations": rt.cfg_int_value(
                node_cfg,
                "max_iterations",
                "maxIterations",
                default=10,
                min_value=1,
                max_value=1000,
            ),
        }

    if node_type == "variable_assign":
        operation = str(node_cfg.get("operation", "set") or "set").strip().lower()
        value_template = str(node_cfg.get("value_template", node_cfg.get("valueTemplate", "")) or "")
        variable_name = str(node_cfg.get("variable_name", node_cfg.get("variableName", "")) or "").strip()
        payload: dict[str, Any] = {
            "variableName": variable_name,
            "operation": operation,
            "currentEnvValue": ctx.env_vars.get(variable_name),
        }
        if operation != "clear":
            payload["valueTemplate"] = value_template
            payload["resolvedValuePreview"] = rt.resolve_node_template_vars(
                template=value_template,
                node_outputs=ctx.node_outputs,
                start_inputs=ctx.start_inputs,
                sys_vars=ctx.sys_vars,
                env_vars=ctx.env_vars,
            )
        return payload

    if node_type == "human_in_loop":
        fields = rt.normalize_human_in_loop_fields(node_cfg)
        resolved_initial_values: dict[str, Any] = {}
        for field in fields:
            value_template = str(field.get("value_template", "") or "")
            rendered = rt.resolve_node_template_vars(
                template=value_template,
                node_outputs=ctx.node_outputs,
                start_inputs=ctx.start_inputs,
                sys_vars=ctx.sys_vars,
                env_vars=ctx.env_vars,
            )
            parsed = rt.parse_loose_json_value(rendered)
            try:
                resolved_initial_values[str(field.get("name", ""))] = rt.coerce_human_field_value(
                    str(field.get("name", "")),
                    str(field.get("type", "string")),
                    parsed,
                )
            except Exception:
                resolved_initial_values[str(field.get("name", ""))] = rendered
        return {
            "title": str(node_cfg.get("title", "") or ""),
            "instruction": str(node_cfg.get("instruction", "") or ""),
            "approveLabel": str(node_cfg.get("approve_label", node_cfg.get("approveLabel", "")) or ""),
            "rejectLabel": str(node_cfg.get("reject_label", node_cfg.get("rejectLabel", "")) or ""),
            "requireRejectComment": rt.cfg_bool_value(
                node_cfg,
                "require_reject_comment",
                "requireRejectComment",
                default=True,
            ),
            "fields": [
                {
                    "name": str(field.get("name", "") or ""),
                    "label": str(field.get("label", "") or ""),
                    "type": str(field.get("type", "string") or "string"),
                    "widget": str(field.get("widget", "") or ""),
                    "options": list(field.get("options", []) or []),
                    "optionsTemplate": str(field.get("options_template", "") or ""),
                    "optionValueKey": str(field.get("option_value_key", "") or ""),
                    "allowCustom": bool(field.get("allow_custom", False)),
                    "placeholder": str(field.get("placeholder", "") or ""),
                    "required": bool(field.get("required", False)),
                }
                for field in fields
            ],
            "initialValues": resolved_initial_values,
        }

    return {
        "config": node_cfg,
    }
