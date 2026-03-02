from __future__ import annotations

from typing import Any

from app.assistant.workflow.validation.contracts import (
    _HTTP_REQUEST_API_KEY_IN,
    _HTTP_REQUEST_AUTH_TYPES,
    _HTTP_REQUEST_BODY_TYPES,
    _HTTP_REQUEST_METHODS,
)
from app.assistant.workflow.validation.models import ValidationError
from app.assistant.workflow.validation.rules.common import cfg_get
from app.config import get_settings

_FORM_BODY_TYPES = {"text", "file"}


def _validate_kv_rows(
    *,
    node_id: str | None,
    rows: Any,
    field_label: str,
    errors: list[ValidationError],
    subject: str,
    validate_form_type: bool = False,
) -> None:
    if rows is None:
        return
    if not isinstance(rows, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} {field_label} must be a list",
            )
        )
        return
    for idx, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} {field_label} item #{idx} must be an object",
                )
            )
            continue
        key = str(item.get("key", "") or "").strip()
        if not key:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} {field_label} item #{idx} requires key",
                )
            )
        value = item.get("value")
        if value is not None and not isinstance(value, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} {field_label} item #{idx} value must be a string",
                )
            )
        enabled = item.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} {field_label} item #{idx} enabled must be boolean",
                )
            )
        if validate_form_type:
            row_type = item.get("type")
            if row_type is None:
                continue
            row_type_text = str(row_type or "").strip().lower()
            if row_type_text not in _FORM_BODY_TYPES:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} {field_label} item #{idx} type must be one of: text,file",
                    )
                )


def validate_http_request_node_config(
    *,
    node_id: str | None,
    cfg: dict,
    errors: list[ValidationError],
    subject: str,
) -> None:
    method_raw = cfg_get(cfg, "method", default="GET")
    method = str(method_raw or "GET").strip().upper()
    if method not in _HTTP_REQUEST_METHODS:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} method is invalid: {method_raw}",
            )
        )

    url = cfg_get(cfg, "url", default="")
    if not isinstance(url, str) or not url.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} url is required and must be a string",
            )
        )

    _validate_kv_rows(
        node_id=node_id,
        rows=cfg_get(cfg, "headers", default=None),
        field_label="headers",
        errors=errors,
        subject=subject,
    )
    _validate_kv_rows(
        node_id=node_id,
        rows=cfg_get(cfg, "query_params", "queryParams", default=None),
        field_label="queryParams",
        errors=errors,
        subject=subject,
    )
    _validate_kv_rows(
        node_id=node_id,
        rows=cfg_get(cfg, "form_body", "formBody", default=None),
        field_label="formBody",
        errors=errors,
        subject=subject,
        validate_form_type=True,
    )

    body_type_raw = cfg_get(cfg, "body_type", "bodyType", default="none")
    body_type = str(body_type_raw or "none").strip().lower()
    if body_type not in _HTTP_REQUEST_BODY_TYPES:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} bodyType is invalid: {body_type_raw}",
            )
        )

    json_body_template = cfg_get(cfg, "json_body_template", "jsonBodyTemplate", default=None)
    if json_body_template is not None and not isinstance(json_body_template, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} jsonBodyTemplate must be a string",
            )
        )
    raw_body_template = cfg_get(cfg, "raw_body_template", "rawBodyTemplate", default=None)
    if raw_body_template is not None and not isinstance(raw_body_template, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} rawBodyTemplate must be a string",
            )
        )

    auth_type_raw = cfg_get(cfg, "auth_type", "authType", default="none")
    auth_type = str(auth_type_raw or "none").strip().lower()
    if auth_type not in _HTTP_REQUEST_AUTH_TYPES:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} authType is invalid: {auth_type_raw}",
            )
        )

    bearer_token = cfg_get(cfg, "bearer_token", "bearerToken", default=None)
    if bearer_token is not None and not isinstance(bearer_token, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} bearerToken must be a string",
            )
        )
    if auth_type == "bearer" and (not isinstance(bearer_token, str) or not bearer_token.strip()):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} authType=bearer requires bearerToken",
            )
        )

    api_key_in_raw = cfg_get(cfg, "api_key_in", "apiKeyIn", default="header")
    api_key_in = str(api_key_in_raw or "header").strip().lower()
    if api_key_in not in _HTTP_REQUEST_API_KEY_IN:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} apiKeyIn is invalid: {api_key_in_raw}",
            )
        )
    api_key_name = cfg_get(cfg, "api_key_name", "apiKeyName", default="X-API-Key")
    if api_key_name is not None and not isinstance(api_key_name, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} apiKeyName must be a string",
            )
        )
    if auth_type == "api_key" and (
        not isinstance(api_key_name, str) or not api_key_name.strip()
    ):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} authType=api_key requires apiKeyName",
            )
        )
    api_key_value = cfg_get(cfg, "api_key_value", "apiKeyValue", default=None)
    if api_key_value is not None and not isinstance(api_key_value, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} apiKeyValue must be a string",
            )
        )
    if auth_type == "api_key" and (not isinstance(api_key_value, str) or not api_key_value.strip()):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} authType=api_key requires apiKeyValue",
            )
        )

    settings = get_settings()
    max_timeout = max(
        1,
        int(getattr(settings, "workflow_http_request_max_timeout_ms", 60000) or 60000),
    )
    timeout_raw = cfg_get(cfg, "timeout_ms", "timeoutMs", default=15000)
    try:
        timeout_ms = int(timeout_raw)
    except Exception:
        timeout_ms = -1
    if timeout_ms < 1 or timeout_ms > max_timeout:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} timeoutMs must be between 1 and {max_timeout}",
            )
        )

    retry_enabled = cfg_get(cfg, "retry_enabled", "retryEnabled", default=False)
    if not isinstance(retry_enabled, bool):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} retryEnabled must be boolean",
            )
        )
    max_retries_allowed = max(
        0,
        int(getattr(settings, "workflow_http_request_max_retries", 5) or 5),
    )
    max_retries_raw = cfg_get(cfg, "max_retries", "maxRetries", default=2)
    try:
        max_retries = int(max_retries_raw)
    except Exception:
        max_retries = -1
    if max_retries < 0 or max_retries > max_retries_allowed:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} maxRetries must be between 0 and {max_retries_allowed}",
            )
        )

    retry_interval_raw = cfg_get(cfg, "retry_interval_ms", "retryIntervalMs", default=200)
    try:
        retry_interval_ms = int(retry_interval_raw)
    except Exception:
        retry_interval_ms = -1
    if retry_interval_ms < 0 or retry_interval_ms > 5000:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} retryIntervalMs must be between 0 and 5000",
            )
        )

    verify_ssl = cfg_get(cfg, "verify_ssl", "verifySsl", default=True)
    if not isinstance(verify_ssl, bool):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} verifySsl must be boolean",
            )
        )
