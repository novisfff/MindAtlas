from __future__ import annotations

import json
from typing import Any, Callable

from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    get_start_inputs,
    logger,
    resolve_node_template_vars,
)
from app.assistant.workflow.engine.state import NodeOutput, WorkflowState
from app.assistant.workflow.http_request import execute_http_request


def _normalize_kv_rows(
    raw_rows: Any,
    *,
    node_outputs: dict[str, NodeOutput],
    start_inputs: dict[str, Any],
    sys_vars: dict[str, str],
    env_vars: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            continue
        raw_value = item.get("value")
        if isinstance(raw_value, str):
            value = resolve_node_template_vars(
                raw_value,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
        elif raw_value is None:
            value = ""
        else:
            value = str(raw_value)
        row: dict[str, Any] = {"key": key, "value": value}
        row_type = item.get("type")
        if isinstance(row_type, str):
            normalized_type = row_type.strip().lower()
            if normalized_type in {"text", "file"}:
                row["type"] = normalized_type
        enabled = item.get("enabled")
        if isinstance(enabled, bool):
            row["enabled"] = enabled
        rows.append(row)
    return rows


def build_http_request_node(
    node_id: str,
    node_cfg: dict,
    execution_scope: Any | None = None,
) -> Callable[[WorkflowState], dict]:
    safe_diagnostics = bool(
        execution_scope is not None and getattr(execution_scope, "safe_diagnostics", False)
    )

    def http_request_node(state: WorkflowState) -> dict:
        metadata = state.get("metadata", {})
        node_outputs = dict(state.get("node_outputs", {}))
        start_inputs = get_start_inputs(node_outputs)
        sys_vars = state.get("sys_vars", {}) or {}
        env_vars = state.get("env_vars", {}) or {}

        method = node_cfg.get("method", "GET")
        raw_url = node_cfg.get("url", "")
        if not isinstance(raw_url, str) or not raw_url.strip():
            raise RuntimeError(f"DAG http_request node {node_id}: url is required")
        resolved_url = resolve_node_template_vars(
            raw_url,
            node_outputs,
            start_inputs,
            sys_vars,
            env_vars=env_vars,
        )

        body_type = node_cfg.get("body_type", node_cfg.get("bodyType", "none"))
        raw_json_body_template = node_cfg.get("json_body_template", node_cfg.get("jsonBodyTemplate", ""))
        raw_raw_body_template = node_cfg.get("raw_body_template", node_cfg.get("rawBodyTemplate", ""))
        raw_bearer_token = node_cfg.get("bearer_token", node_cfg.get("bearerToken", ""))
        raw_api_key_value = node_cfg.get("api_key_value", node_cfg.get("apiKeyValue", ""))

        json_body_template = (
            resolve_node_template_vars(
                raw_json_body_template,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            if isinstance(raw_json_body_template, str)
            else ""
        )
        raw_body_template = (
            resolve_node_template_vars(
                raw_raw_body_template,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            if isinstance(raw_raw_body_template, str)
            else ""
        )
        bearer_token = (
            resolve_node_template_vars(
                raw_bearer_token,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            if isinstance(raw_bearer_token, str)
            else ""
        )
        api_key_value = (
            resolve_node_template_vars(
                raw_api_key_value,
                node_outputs,
                start_inputs,
                sys_vars,
                env_vars=env_vars,
            )
            if isinstance(raw_api_key_value, str)
            else ""
        )

        headers_rows = _normalize_kv_rows(
            node_cfg.get("headers"),
            node_outputs=node_outputs,
            start_inputs=start_inputs,
            sys_vars=sys_vars,
            env_vars=env_vars,
        )
        query_rows = _normalize_kv_rows(
            node_cfg.get("query_params", node_cfg.get("queryParams")),
            node_outputs=node_outputs,
            start_inputs=start_inputs,
            sys_vars=sys_vars,
            env_vars=env_vars,
        )
        form_rows = _normalize_kv_rows(
            node_cfg.get("form_body", node_cfg.get("formBody")),
            node_outputs=node_outputs,
            start_inputs=start_inputs,
            sys_vars=sys_vars,
            env_vars=env_vars,
        )

        emit(metadata, "on_node_start", node_id=node_id, node_type="http_request")
        try:
            result = execute_http_request(
                method=method,
                url=resolved_url,
                headers=headers_rows,
                query_params=query_rows,
                body_type=body_type,
                json_body_template=json_body_template,
                raw_body_template=raw_body_template,
                form_body=form_rows,
                auth_type=node_cfg.get("auth_type", node_cfg.get("authType", "none")),
                bearer_token=bearer_token,
                api_key_in=node_cfg.get("api_key_in", node_cfg.get("apiKeyIn", "header")),
                api_key_name=node_cfg.get("api_key_name", node_cfg.get("apiKeyName", "X-API-Key")),
                api_key_value=api_key_value,
                timeout_ms=node_cfg.get("timeout_ms", node_cfg.get("timeoutMs")),
                retry_enabled=node_cfg.get("retry_enabled", node_cfg.get("retryEnabled", False)),
                max_retries=node_cfg.get("max_retries", node_cfg.get("maxRetries", 2)),
                retry_interval_ms=node_cfg.get("retry_interval_ms", node_cfg.get("retryIntervalMs", 200)),
                verify_ssl=node_cfg.get("verify_ssl", node_cfg.get("verifySsl", True)),
            )
        except Exception as exc:
            emit(metadata, "on_node_end", node_id=node_id, status="error")
            if safe_diagnostics:
                logger.error(
                    "capability_safe_execution stage=http_request_node node_id=%s exc_class=%s",
                    node_id,
                    type(exc).__name__,
                )
                raise RuntimeError(f"DAG http_request node {node_id} failed: http_request failed") from None
            raise RuntimeError(f"DAG http_request node {node_id} failed: {exc}") from exc

        # NodeOutput must keep full bodies for downstream templates/nodes.
        # safe_diagnostics only affects logs/events (exception path above), not runtime data.
        payload = {
            "body": result.body,
            "status_code": result.status_code,
            "headers": result.headers,
            "ok": result.ok,
            "error_message": result.error_message,
            "response": result.response,
        }
        text_output = result.body if result.body else json.dumps(payload, ensure_ascii=False)
        node_out: NodeOutput = {
            "status": "ok",
            "text": text_output,
            "raw": payload,
            "json_fields": payload,
        }
        emit(metadata, "on_node_end", node_id=node_id, status="ok")
        return {
            "node_outputs": {node_id: node_out},
            "execution_trace": [node_id],
        }

    return http_request_node
