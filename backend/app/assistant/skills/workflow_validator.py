"""Workflow DAG topology validator."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Sequence
from uuid import UUID

from app.assistant.skills.code_executor import (
    extract_javascript_imports,
    extract_python_imports,
    get_javascript_allowed_modules,
    get_python_allowed_modules,
    has_javascript_dynamic_import,
)
from app.assistant.skills.workflow_env_vars import parse_env_var_specs

@dataclass
class ValidationError:
    node_id: str | None
    message: str


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)


def _cfg_get(cfg: dict, *keys: str, default=None):
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return default


def _cfg_bool(cfg: dict, *keys: str, default: bool = False) -> bool:
    value = _cfg_get(cfg, *keys, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _cfg_str_list(cfg: dict, *keys: str) -> list[str]:
    value = _cfg_get(cfg, *keys, default=None)
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            out.append(text)
    return out


def _cfg_list(cfg: dict, *keys: str) -> list:
    value = _cfg_get(cfg, *keys, default=None)
    return value if isinstance(value, list) else []


def _is_valid_node_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_]+", value))


def _extract_container_body(cfg: dict) -> tuple[list[dict], list[dict]]:
    body_nodes_raw = _cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
    body_edges_raw = _cfg_get(cfg, "body_edges", "bodyEdges", default=[])
    body_nodes = body_nodes_raw if isinstance(body_nodes_raw, list) else []
    body_edges = body_edges_raw if isinstance(body_edges_raw, list) else []
    return body_nodes, body_edges


def _validate_container_subflow(
    parent_node_id: str,
    parent_type: str,
    body_nodes: list[dict],
    body_edges: list[dict],
    errors: list[ValidationError],
) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    body_node_map: dict[str, dict] = {}
    body_type_map: dict[str, str] = {}
    in_degree: dict[str, int] = {}
    out_edges: dict[str, list[str]] = defaultdict(list)

    for raw in body_nodes:
        if not isinstance(raw, dict):
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} bodyNodes contains non-object node item",
                )
            )
            continue

        node_id = str(raw.get("node_id", raw.get("nodeId", "")) or "").strip()
        node_type = str(raw.get("node_type", raw.get("nodeType", "")) or "").strip()
        if not node_id or not _is_valid_node_id(node_id):
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body node has invalid nodeId: {node_id or '<empty>'}",
                )
            )
            continue
        if node_id in body_node_map:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body node duplicated nodeId: {node_id}",
                )
            )
            continue
        if node_type not in _CONTAINER_BODY_ALLOWED_NODE_TYPES:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body node '{node_id}' has unsupported node type: {node_type}",
                )
            )
        body_node_map[node_id] = raw
        body_type_map[node_id] = node_type
        in_degree[node_id] = 0

        if node_type in {"iteration", "loop"}:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body node '{node_id}' must not nest iteration/loop nodes",
                )
            )

    start_count = sum(1 for t in body_type_map.values() if t == "start")
    if start_count != 1:
        errors.append(
            ValidationError(
                node_id=parent_node_id,
                message=f"{parent_type} body must contain exactly one start node",
            )
        )

    for raw in body_edges:
        if not isinstance(raw, dict):
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} bodyEdges contains non-object edge item",
                )
            )
            continue
        source = str(raw.get("source_node_id", raw.get("sourceNodeId", "")) or "").strip()
        target = str(raw.get("target_node_id", raw.get("targetNodeId", "")) or "").strip()
        if not source or not target:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body edge has empty source/target",
                )
            )
            continue
        if source not in body_node_map:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body edge references unknown source node: {source}",
                )
            )
            continue
        if target not in body_node_map:
            errors.append(
                ValidationError(
                    node_id=parent_node_id,
                    message=f"{parent_type} body edge references unknown target node: {target}",
                )
            )
            continue
        out_edges[source].append(target)
        in_degree[target] = in_degree.get(target, 0) + 1

    queue = deque([node_id for node_id, deg in in_degree.items() if deg == 0])
    topo_order: list[str] = []
    while queue:
        node_id = queue.popleft()
        topo_order.append(node_id)
        for target in out_edges.get(node_id, []):
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    if body_node_map and len(topo_order) != len(body_node_map):
        errors.append(
            ValidationError(
                node_id=parent_node_id,
                message=f"{parent_type} body graph contains cycle(s)",
            )
        )

    return body_node_map, body_type_map, topo_order


def _iter_config_template_texts(cfg: dict) -> list[str]:
    texts: list[str] = []
    if not isinstance(cfg, dict):
        return texts

    for key in (
        "system_prompt", "systemPrompt",
        "user_input", "userInput",
        "input_content", "inputContent",
        "instruction",
        "template",
        "text_template", "textTemplate",
        "query",
        "args_template", "argsTemplate",
        "input_source", "inputSource",
        "output_selector", "outputSelector",
        "value_template", "valueTemplate",
    ):
        value = cfg.get(key, "")
        if isinstance(value, str):
            texts.append(value)

    for key in ("input_bindings", "inputBindings"):
        bindings = cfg.get(key)
        if isinstance(bindings, dict):
            for value in bindings.values():
                if isinstance(value, str):
                    texts.append(value)

    for key in ("initial_vars", "initialVars", "update_mappings", "updateMappings"):
        items = cfg.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    value = item.get("value")
                    if isinstance(value, str):
                        texts.append(value)

    for key in ("output_fields", "outputFields"):
        items = cfg.get(key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, str):
                    texts.append(value)

    for key in ("fields",):
        items = cfg.get(key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value_template", item.get("valueTemplate"))
                if isinstance(value, str):
                    texts.append(value)
                options_template = item.get("options_template", item.get("optionsTemplate"))
                if isinstance(options_template, str):
                    texts.append(options_template)

    for key in ("conditions", "terminationConditions"):
        conds = cfg.get(key)
        if isinstance(conds, list):
            for cond in conds:
                if isinstance(cond, dict):
                    value = cond.get("value")
                    if isinstance(value, str):
                        texts.append(value)

    branches = cfg.get("branches")
    if isinstance(branches, list):
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            conditions = branch.get("conditions")
            if isinstance(conditions, list):
                for condition in conditions:
                    if not isinstance(condition, dict):
                        continue
                    value = condition.get("value")
                    if isinstance(value, str):
                        texts.append(value)

    return texts


_IF_ELSE_HANDLE_RE = re.compile(r"[a-zA-Z0-9_]+")
_IF_ELSE_NEW_OPERATORS = {
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "is",
    "is_not",
    "is_empty",
    "is_not_empty",
}
_IF_ELSE_LEGACY_OPERATORS = {"equals", "not_equals", "gt", "lt", "gte", "lte"}
_IF_ELSE_ALL_OPERATORS = _IF_ELSE_NEW_OPERATORS | _IF_ELSE_LEGACY_OPERATORS
_IF_ELSE_LEGACY_OPERATOR_MAP = {
    "equals": "is",
    "not_equals": "is_not",
}
_SYS_FIELDS = {"date", "datetime", "conversation_id"}
_START_INPUT_MODES = {"text", "structured"}
_START_INPUT_FIELD_TYPES = {"string", "number", "integer", "boolean"}
_START_INPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_OUTPUT_FIELD_NAME_RE = re.compile(r"[a-zA-Z0-9_]+")
_OUTPUT_FIELD_TYPES = {"string", "number", "integer", "boolean", "object", "array"}
_CODE_EXECUTOR_LANGUAGES = {"python", "javascript"}
_CODE_EXECUTOR_ENTRYPOINT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_CODE_EXECUTOR_INPUT_KEY_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_HUMAN_FIELD_TYPES = {"string", "number", "integer", "boolean", "array"}
_HUMAN_FIELD_WIDGETS = {"input", "textarea", "switch", "select", "radio", "tag_selector", "date", "time"}
_HUMAN_FIELD_WIDGET_ALLOWED_TYPES: dict[str, set[str]] = {
    "input": {"string", "number", "integer"},
    "textarea": {"string"},
    "switch": {"boolean"},
    "select": {"string", "number", "integer"},
    "radio": {"string", "number", "integer"},
    "tag_selector": {"array"},
    "date": {"string"},
    "time": {"string"},
}
_ENV_VAR_PATH_RE = re.compile(r"env\\.([a-zA-Z_][a-zA-Z0-9_]*)$")
_SUPPORTED_NODE_TYPES = {
    "start",
    "llm",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "iteration",
    "loop",
    "code_executor",
    "variable_assign",
    "human_in_loop",
    "output",
}
_CONTAINER_BODY_ALLOWED_NODE_TYPES = {
    "start",
    "llm",
    "tool",
    "if_else",
    "parameter_extractor",
    "knowledge_retrieval",
    "code_executor",
    "variable_assign",
    "human_in_loop",
}
_REMOVED_NODE_TYPE_MESSAGES = {
    "answer": "Node type 'answer' is no longer supported. Use the output node instead.",
    "template": "Node type 'template' has been removed. Please refactor with supported nodes.",
    "variable_aggregator": "Node type 'variable_aggregator' has been removed. Please refactor with supported nodes.",
}


def _resolve_start_input_contract(cfg: dict) -> tuple[str, set[str], list[str]]:
    errors: list[str] = []
    raw_mode = _cfg_get(cfg, "input_mode", "inputMode", default="text")
    mode = str(raw_mode or "text").strip().lower()
    if mode not in _START_INPUT_MODES:
        errors.append(f"start inputMode is invalid: {raw_mode}")
        mode = "text"

    if mode == "text":
        return mode, {"user_input"}, errors

    structured_fields_raw = _cfg_get(cfg, "structured_fields", "structuredFields", default=None)
    if not isinstance(structured_fields_raw, list) or not structured_fields_raw:
        errors.append("start structured mode requires at least one structured field")
        return mode, set(), errors

    field_names: set[str] = set()
    for idx, raw_field in enumerate(structured_fields_raw, start=1):
        if not isinstance(raw_field, dict):
            errors.append(f"start structured field #{idx} must be an object")
            continue
        field_name = str(raw_field.get("name", "") or "").strip()
        if not field_name:
            errors.append(f"start structured field #{idx} requires name")
            continue
        if field_name == "user_input":
            errors.append("start structured field name 'user_input' is reserved")
            continue
        if not _START_INPUT_FIELD_NAME_RE.fullmatch(field_name):
            errors.append(f"start structured field name is invalid: {field_name}")
            continue
        if field_name in field_names:
            errors.append(f"start structured field duplicated: {field_name}")
            continue

        field_type_raw = raw_field.get("type", "string")
        field_type = str(field_type_raw or "string").strip().lower()
        if field_type not in _START_INPUT_FIELD_TYPES:
            errors.append(
                f"start structured field '{field_name}' has invalid type: {field_type_raw}"
            )

        required = raw_field.get("required", False)
        if not isinstance(required, bool):
            errors.append(f"start structured field '{field_name}' required must be boolean")

        field_names.add(field_name)

    return mode, field_names, errors


def _resolve_start_env_var_contract(cfg: dict) -> tuple[dict[str, str], list[str]]:
    if not isinstance(cfg, dict):
        return ({}, [])

    specs, parse_errors = parse_env_var_specs(
        _cfg_get(cfg, "session_vars", "sessionVars", default=None)
    )
    if parse_errors:
        return ({}, parse_errors)
    return ({spec.name: spec.type for spec in specs}, [])


def _validate_output_fields_config(
    *,
    node_id: str,
    node_type: str,
    output_fields: Any,
    errors: list[ValidationError],
    required: bool,
) -> None:
    if required and (not isinstance(output_fields, list) or not output_fields):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{node_type} outputFields must be a non-empty list",
            )
        )
        return
    if output_fields is None:
        return
    if not isinstance(output_fields, list):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{node_type} outputFields must be a list",
            )
        )
        return

    for field in output_fields:
        if not isinstance(field, dict):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{node_type} outputFields items must be objects",
                )
            )
            continue
        field_name = str(field.get("name", "") or "").strip()
        if not field_name or not _OUTPUT_FIELD_NAME_RE.fullmatch(field_name):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"Invalid {node_type} output field name: {field_name}",
                )
            )

        field_type_raw = field.get("type", "string")
        field_type = str(field_type_raw or "string").strip().lower() or "string"
        if field_type not in _OUTPUT_FIELD_TYPES:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"Invalid {node_type} output field type: {field_type_raw}",
                )
            )

        nullable_raw = field.get("nullable", None)
        if nullable_raw is not None and not isinstance(nullable_raw, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{node_type} field '{field_name}' nullable must be boolean",
                )
            )

        items_type_raw = field.get("items_type", field.get("itemsType"))
        items_type = str(items_type_raw or "").strip().lower()
        if field_type == "array":
            if not items_type:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} array field '{field_name}' requires itemsType",
                    )
                )
            elif items_type not in _OUTPUT_FIELD_TYPES:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} array field '{field_name}' has invalid itemsType: {items_type_raw}",
                    )
                )
            elif items_type == "array":
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} field '{field_name}' itemsType cannot be array",
                    )
                )
        elif items_type:
            if items_type not in _OUTPUT_FIELD_TYPES:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} field '{field_name}' has invalid itemsType: {items_type_raw}",
                    )
                )
            elif items_type == "array":
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} field '{field_name}' itemsType cannot be array",
                    )
                )

        enum_raw = field.get("enum")
        if enum_raw is not None:
            if not isinstance(enum_raw, list) or any(not isinstance(item, str) for item in enum_raw):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{node_type} field '{field_name}' enum must be string array",
                    )
                )


def _validate_code_executor_imports(
    *,
    node_id: str,
    language: str,
    code_text: str,
    errors: list[ValidationError],
) -> None:
    if language == "python":
        disallowed = sorted(extract_python_imports(code_text) - get_python_allowed_modules())
        if disallowed:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"code_executor imports not allowed: {', '.join(disallowed)}",
                )
            )
        if "__import__(" in code_text or "importlib.import_module" in code_text:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="code_executor dynamic Python import is not allowed",
                )
            )
        return

    if language == "javascript":
        if has_javascript_dynamic_import(code_text):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message="code_executor dynamic JavaScript import() is not allowed",
                )
            )
        disallowed = sorted(extract_javascript_imports(code_text) - get_javascript_allowed_modules())
        if disallowed:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"code_executor imports not allowed: {', '.join(disallowed)}",
                )
            )


def _parse_code_executor_signature_params(params_text: str) -> list[str]:
    params: list[str] = []
    for raw_token in str(params_text or "").split(","):
        token = raw_token.strip()
        if not token:
            continue
        token = token.lstrip("*").lstrip(".").split(":", 1)[0].split("=", 1)[0].strip()
        if token:
            params.append(token)
    return params


def _extract_code_executor_signature_params(
    *,
    language: str,
    entrypoint: str,
    code_text: str,
) -> list[str] | None:
    escaped_entrypoint = re.escape(entrypoint)
    if language == "python":
        match = re.search(
            rf"^\s*def\s+{escaped_entrypoint}\s*\(([^)]*)\)\s*:",
            code_text or "",
            re.MULTILINE,
        )
        if not match:
            return None
        return _parse_code_executor_signature_params(match.group(1))

    patterns = [
        rf"(?:^|\n)\s*(?:async\s+)?function\s+{escaped_entrypoint}\s*\(([^)]*)\)",
        rf"(?:^|\n)\s*(?:const|let|var)\s+{escaped_entrypoint}\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
        rf"(?:^|\n)\s*(?:const|let|var)\s+{escaped_entrypoint}\s*=\s*(?:async\s+)?function\s*\(([^)]*)\)",
        rf"(?:^|\n)\s*(?:module\.exports|exports)\.{escaped_entrypoint}\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
        rf"(?:^|\n)\s*(?:module\.exports|exports)\.{escaped_entrypoint}\s*=\s*(?:async\s+)?function\s*\(([^)]*)\)",
    ]
    for pattern in patterns:
        match = re.search(pattern, code_text or "", re.MULTILINE)
        if not match:
            continue
        return _parse_code_executor_signature_params(match.group(1))
    return None


def _validate_code_executor_signature(
    *,
    node_id: str,
    subject: str,
    language: str,
    entrypoint: str,
    code_text: str,
    expected_params: set[str],
    errors: list[ValidationError],
) -> None:
    params = _extract_code_executor_signature_params(
        language=language,
        entrypoint=entrypoint,
        code_text=code_text,
    )
    if params is None:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} must define function '{entrypoint}(...)'"
                ),
            )
        )
        return

    normalized_params = [str(item or "").strip() for item in params if str(item or "").strip()]
    param_set = set(normalized_params)
    if len(normalized_params) != len(param_set):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} function '{entrypoint}' has duplicated parameters"
                ),
            )
        )
        return

    if param_set != expected_params:
        missing = sorted(expected_params - param_set)
        extra = sorted(param_set - expected_params)
        parts: list[str] = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if extra:
            parts.append(f"extra: {', '.join(extra)}")
        detail = "; ".join(parts) if parts else "parameter mismatch"
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} signature must match inputBindings keys ({detail})"
                ),
            )
        )


def _validate_code_executor_input_bindings(
    *,
    node_id: str,
    subject: str,
    input_bindings: Any,
    errors: list[ValidationError],
) -> set[str]:
    if not isinstance(input_bindings, dict):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} inputBindings must be an object",
            )
        )
        return set()

    normalized_keys: set[str] = set()
    for key, value in input_bindings.items():
        key_name = str(key or "").strip()
        if not key_name:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} inputBindings contains empty key",
                )
            )
            continue
        if not _CODE_EXECUTOR_INPUT_KEY_RE.fullmatch(key_name):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} input binding key is invalid: {key_name}",
                )
            )
            continue
        if key_name in normalized_keys:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} inputBindings has duplicate key: {key_name}",
                )
            )
            continue
        if not isinstance(value, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} input binding '{key_name}' must be a string",
                )
            )
            continue
        normalized_keys.add(key_name)

    return normalized_keys


def _validate_code_executor_node_config(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str,
    validate_timeout: bool,
) -> None:
    language_raw = _cfg_get(cfg, "language", default="python")
    language = str(language_raw or "python").strip().lower()
    if language not in _CODE_EXECUTOR_LANGUAGES:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} language is invalid: {language_raw}",
            )
        )

    code_text = _cfg_get(cfg, "code", default="")
    if not isinstance(code_text, str) or not code_text.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} code is required and must be a string",
            )
        )
    elif language in _CODE_EXECUTOR_LANGUAGES:
        _validate_code_executor_imports(
            node_id=node_id,
            language=language,
            code_text=code_text,
            errors=errors,
        )

    entrypoint_raw = _cfg_get(cfg, "entrypoint", default="main")
    entrypoint = str(entrypoint_raw or "main").strip() or "main"
    if not _CODE_EXECUTOR_ENTRYPOINT_RE.fullmatch(entrypoint):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} entrypoint is invalid: {entrypoint_raw}",
            )
        )

    validated_binding_keys = _validate_code_executor_input_bindings(
        node_id=node_id,
        subject=subject,
        input_bindings=_cfg_get(cfg, "input_bindings", "inputBindings", default=None),
        errors=errors,
    )

    if isinstance(code_text, str) and code_text.strip() and language in _CODE_EXECUTOR_LANGUAGES:
        _validate_code_executor_signature(
            node_id=node_id,
            subject=subject,
            language=language,
            entrypoint=entrypoint,
            code_text=code_text,
            expected_params=validated_binding_keys,
            errors=errors,
        )

    if validate_timeout:
        timeout_raw = _cfg_get(cfg, "timeout_ms", "timeoutMs", default=None)
        if timeout_raw is not None and str(timeout_raw).strip():
            try:
                timeout_ms = int(timeout_raw)
            except Exception:
                timeout_ms = 0
            if timeout_ms < 100 or timeout_ms > 5000:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} timeoutMs must be between 100 and 5000",
                    )
                )

    _validate_output_fields_config(
        node_id=node_id,
        node_type=subject,
        output_fields=_cfg_get(cfg, "output_fields", "outputFields", default=None),
        errors=errors,
        required=True,
    )


def _validate_variable_assign_node_config(
    *,
    node_id: str,
    cfg: dict,
    env_var_types: dict[str, str],
    errors: list[ValidationError],
    subject: str,
) -> None:
    variable_name_raw = _cfg_get(cfg, "variable_name", "variableName", default="")
    variable_name = str(variable_name_raw or "").strip()
    if not variable_name:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variableName is required",
            )
        )
        return
    if not _START_INPUT_FIELD_NAME_RE.fullmatch(variable_name):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variableName is invalid: {variable_name_raw}",
            )
        )
        return
    if variable_name not in env_var_types:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} variable '{variable_name}' is not defined in start sessionVars",
            )
        )
        return

    operation_raw = _cfg_get(cfg, "operation", default="set")
    operation = str(operation_raw or "set").strip().lower()
    if operation not in {"set", "increment", "append", "clear"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} operation is invalid: {operation_raw}",
            )
        )
        return

    value_template = _cfg_get(cfg, "value_template", "valueTemplate", default=None)
    if operation != "clear" and (not isinstance(value_template, str) or not value_template.strip()):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} valueTemplate is required and must be a string",
            )
        )

    var_type = env_var_types.get(variable_name)
    if operation == "increment" and var_type not in {"number", "integer"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} increment supports only number/integer variable, "
                    f"but '{variable_name}' is {var_type}"
                ),
            )
        )
    if operation == "append" and var_type not in {"string", "array"}:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=(
                    f"{subject} append supports only string/array variable, "
                    f"but '{variable_name}' is {var_type}"
                ),
            )
        )


def _validate_human_in_loop_node_config(
    *,
    node_id: str,
    cfg: dict,
    errors: list[ValidationError],
    subject: str,
) -> None:
    instruction = _cfg_get(cfg, "instruction", default=None)
    if not isinstance(instruction, str) or not instruction.strip():
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} instruction is required",
            )
        )

    title = _cfg_get(cfg, "title", default=None)
    if title is not None and not isinstance(title, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} title must be a string",
            )
        )

    approve_label = _cfg_get(cfg, "approve_label", "approveLabel", default=None)
    if approve_label is not None and not isinstance(approve_label, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} approveLabel must be a string",
            )
        )

    reject_label = _cfg_get(cfg, "reject_label", "rejectLabel", default=None)
    if reject_label is not None and not isinstance(reject_label, str):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} rejectLabel must be a string",
            )
        )

    require_reject_comment = _cfg_get(cfg, "require_reject_comment", "requireRejectComment", default=True)
    if not isinstance(require_reject_comment, bool):
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} requireRejectComment must be boolean",
            )
        )

    fields_raw = _cfg_get(cfg, "fields", default=None)
    if not isinstance(fields_raw, list) or not fields_raw:
        errors.append(
            ValidationError(
                node_id=node_id,
                message=f"{subject} fields must be a non-empty list",
            )
        )
        return

    seen_names: set[str] = set()
    for idx, item in enumerate(fields_raw, start=1):
        if not isinstance(item, dict):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field #{idx} must be an object",
                )
            )
            continue
        field_name = str(item.get("name", "") or "").strip()
        if not field_name:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field #{idx} requires name",
                )
            )
            continue
        if not _START_INPUT_FIELD_NAME_RE.fullmatch(field_name):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field name is invalid: {field_name}",
                )
            )
            continue
        if field_name in seen_names:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field name duplicated: {field_name}",
                )
            )
            continue
        seen_names.add(field_name)

        field_type_raw = item.get("type", "string")
        field_type = str(field_type_raw or "string").strip().lower() or "string"
        if field_type not in _HUMAN_FIELD_TYPES:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' has invalid type: {field_type_raw}",
                )
            )
            field_type = "string"

        raw_widget = item.get("widget", None)
        if raw_widget is None:
            widget = "switch" if field_type == "boolean" else "input"
        else:
            widget = str(raw_widget or "").strip().lower()
            if not widget:
                widget = "switch" if field_type == "boolean" else "input"
        if widget not in _HUMAN_FIELD_WIDGETS:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' has invalid widget: {raw_widget}",
                )
            )
        else:
            allowed_types = _HUMAN_FIELD_WIDGET_ALLOWED_TYPES.get(widget, set())
            if field_type not in allowed_types:
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=(
                            f"{subject} field '{field_name}' widget '{widget}' is incompatible "
                            f"with type '{field_type}'"
                        ),
                    )
                )

        required = item.get("required", False)
        if not isinstance(required, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' required must be boolean",
                )
            )

        label = item.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' label must be a string",
                )
            )

        placeholder = item.get("placeholder")
        if placeholder is not None and not isinstance(placeholder, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' placeholder must be a string",
                )
            )

        options = item.get("options")
        options_template = item.get("options_template", item.get("optionsTemplate"))
        has_options_template = isinstance(options_template, str) and bool(options_template.strip())
        if options_template is not None and not isinstance(options_template, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionsTemplate must be a string",
                )
            )

        raw_option_value_key = item.get("option_value_key", item.get("optionValueKey", None))
        option_value_key = ""
        if raw_option_value_key is not None and not isinstance(raw_option_value_key, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionValueKey must be a string",
                )
            )
        elif isinstance(raw_option_value_key, str):
            option_value_key = raw_option_value_key.strip()

        if option_value_key and widget not in {"select", "radio", "tag_selector"}:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' optionValueKey is only supported for select/radio/tag_selector",
                )
            )

        if options is not None:
            if not isinstance(options, list):
                errors.append(
                    ValidationError(
                        node_id=node_id,
                        message=f"{subject} field '{field_name}' options must be a string list",
                    )
                )
            else:
                normalized_options = [
                    str(opt).strip()
                    for opt in options
                    if isinstance(opt, str) and str(opt).strip()
                ]
                if len(normalized_options) != len(options):
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"{subject} field '{field_name}' options must be non-empty strings",
                        )
                    )
                deduped_options = list(dict.fromkeys(normalized_options))
                if widget in {"select", "radio"} and not deduped_options:
                    errors.append(
                        ValidationError(
                            node_id=node_id,
                            message=f"{subject} field '{field_name}' options must be non-empty for {widget}",
                        )
                    )
        elif widget in {"select", "radio"} and not has_options_template:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' options or optionsTemplate are required for {widget}",
                )
            )

        allow_custom = item.get("allow_custom", item.get("allowCustom", None))
        if allow_custom is not None and not isinstance(allow_custom, bool):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' allowCustom must be boolean",
                )
            )
        if widget != "tag_selector" and allow_custom is True:
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' allowCustom is only supported for tag_selector",
                )
            )

        value_template = item.get("value_template", item.get("valueTemplate", ""))
        if value_template is not None and not isinstance(value_template, str):
            errors.append(
                ValidationError(
                    node_id=node_id,
                    message=f"{subject} field '{field_name}' valueTemplate must be a string",
                )
            )


def _normalize_if_else_operator(raw: object) -> str:
    op = str(raw or "is").strip().lower()
    if not op:
        return "is"
    return _IF_ELSE_LEGACY_OPERATOR_MAP.get(op, op)


def _normalize_if_else_handle(raw: object) -> str:
    handle = str(raw or "").strip()
    if handle == "default":
        return "else"
    return handle


def _normalize_if_else_config(cfg: dict) -> dict[str, object]:
    else_handle = _normalize_if_else_handle(_cfg_get(cfg, "else_handle", "elseHandle", default="else"))
    if not else_handle or not _IF_ELSE_HANDLE_RE.fullmatch(else_handle):
        else_handle = "else"

    branches_raw = _cfg_get(cfg, "branches", default=None)
    branches: list[dict[str, object]] = []

    if isinstance(branches_raw, list) and branches_raw:
        for idx, branch in enumerate(branches_raw, start=1):
            if not isinstance(branch, dict):
                continue
            branch_id = _normalize_if_else_handle(branch.get("id"))
            if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                branch_id = f"if_{idx}"
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            label = str(branch.get("label") or ("IF" if idx == 1 else f"ELIF {idx - 1}")).strip() or ("IF" if idx == 1 else f"ELIF {idx - 1}")
            conds: list[dict[str, object]] = []
            conds_raw = branch.get("conditions")
            if isinstance(conds_raw, list):
                for cond_idx, cond in enumerate(conds_raw, start=1):
                    if not isinstance(cond, dict):
                        continue
                    conds.append(
                        {
                            "id": str(cond.get("id") or f"{branch_id}_cond_{cond_idx}").strip() or f"{branch_id}_cond_{cond_idx}",
                            "variable": str(cond.get("variable") or "").strip(),
                            "operator": _normalize_if_else_operator(cond.get("operator")),
                            "value": None if cond.get("value") is None else str(cond.get("value")),
                        }
                    )
            branches.append(
                {
                    "id": branch_id,
                    "label": label,
                    "logic": logic,
                    "conditions": conds,
                }
            )

    if not branches:
        # legacy format: conditions[] where each condition carries handle
        grouped: dict[str, list[dict[str, object]]] = {}
        handle_order: list[str] = []
        legacy_conds = _cfg_get(cfg, "conditions", default=[])
        if isinstance(legacy_conds, list):
            for idx, cond in enumerate(legacy_conds, start=1):
                if not isinstance(cond, dict):
                    continue
                handle = _normalize_if_else_handle(cond.get("handle"))
                if not handle:
                    continue
                if handle in {"else"}:
                    continue
                if not _IF_ELSE_HANDLE_RE.fullmatch(handle):
                    continue
                if handle not in grouped:
                    grouped[handle] = []
                    handle_order.append(handle)
                grouped[handle].append(
                    {
                        "id": str(cond.get("id") or f"{handle}_cond_{idx}").strip() or f"{handle}_cond_{idx}",
                        "variable": str(cond.get("variable") or "").strip(),
                        "operator": _normalize_if_else_operator(cond.get("operator")),
                        "value": None if cond.get("value") is None else str(cond.get("value")),
                    }
                )
        for branch_idx, handle in enumerate(handle_order, start=1):
            branches.append(
                {
                    "id": handle,
                    "label": "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}",
                    "logic": "and",
                    "conditions": grouped.get(handle, []),
                }
            )

    return {"branches": branches, "else_handle": else_handle}


def validate_workflow(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate workflow DAG topology.

    Args:
        nodes: list of objects with node_id, node_type, config attributes
        edges: list of objects with source_node_id, target_node_id, source_handle attributes
    """
    errors: list[ValidationError] = []

    node_map: dict[str, object] = {}
    type_map: dict[str, str] = {}
    config_map: dict[str, dict] = {}
    label_map: dict[str, str] = {}

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        label = getattr(n, "label", None) or (n.get("label") if isinstance(n, dict) else None)
        if nid is None:
            continue
        type_map[nid] = ntype or ""
        config_map[nid] = cfg if isinstance(cfg, dict) else {}
        label_map[nid] = str(label or "")
        if nid in node_map:
            errors.append(ValidationError(node_id=nid, message=f"Duplicate node_id: {nid}"))
        node_map[nid] = n

    node_ids = set(node_map.keys())

    # Rule 0: labels must be non-empty, case-insensitive unique, and cannot include dot
    seen_labels: dict[str, str] = {}
    for nid in node_ids:
        raw_label = label_map.get(nid, "")
        label = str(raw_label or "").strip()
        if not label:
            errors.append(ValidationError(
                node_id=nid,
                message="Node label is required",
            ))
            continue
        if "." in label:
            errors.append(ValidationError(
                node_id=nid,
                message="Node label must not contain '.'",
            ))
        normalized = label.casefold()
        existing = seen_labels.get(normalized)
        if existing and existing != nid:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Duplicate node label (case-insensitive): '{label}'",
            ))
            continue
        seen_labels[normalized] = nid

    # Rule 1: exactly one start node
    start_nodes = [nid for nid, nt in type_map.items() if nt == "start"]
    if len(start_nodes) == 0:
        errors.append(ValidationError(node_id=None, message="Must have exactly one start node"))
    elif len(start_nodes) > 1:
        for nid in start_nodes[1:]:
            errors.append(ValidationError(node_id=nid, message="Multiple start nodes found"))

    start_allowed_fields: set[str] = {"user_input"}
    start_env_var_types: dict[str, str] = {}
    if len(start_nodes) == 1:
        start_node_id = start_nodes[0]
        start_cfg = config_map.get(start_node_id, {})
        if not isinstance(start_cfg, dict):
            start_cfg = {}
        _, start_allowed_fields, start_contract_errors = _resolve_start_input_contract(start_cfg)
        for message in start_contract_errors:
            errors.append(ValidationError(node_id=start_node_id, message=message))
        start_env_var_types, start_env_contract_errors = _resolve_start_env_var_contract(start_cfg)
        for message in start_env_contract_errors:
            errors.append(ValidationError(node_id=start_node_id, message=message))

    # Rule 2: removed / unknown node types are rejected explicitly
    for nid, ntype in type_map.items():
        if ntype in _REMOVED_NODE_TYPE_MESSAGES:
            errors.append(
                ValidationError(node_id=nid, message=_REMOVED_NODE_TYPE_MESSAGES[ntype])
            )
        elif ntype not in _SUPPORTED_NODE_TYPES:
            errors.append(
                ValidationError(node_id=nid, message=f"Unsupported node type: {ntype}")
            )

    # Rule 3: workflow must have exactly one output node
    output_nodes = [nid for nid, ntype in type_map.items() if ntype == "output"]
    if len(output_nodes) == 0:
        errors.append(ValidationError(
            node_id=None,
            message="Must have exactly one output node",
        ))
    elif len(output_nodes) > 1:
        for nid in output_nodes:
            errors.append(ValidationError(
                node_id=nid,
                message="Only one output node is allowed",
            ))

    # Rule 4: node_id uniqueness (already checked above)

    # Build adjacency structures
    out_edges: dict[str, list[str]] = defaultdict(list)
    out_handles: dict[str, list[str]] = defaultdict(list)
    out_edge_count: dict[str, int] = defaultdict(int)
    in_edge_count: dict[str, int] = defaultdict(int)

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        src_handle = getattr(e, "source_handle", None) or (e.get("source_handle") if isinstance(e, dict) else None) or "output"
        if src is None or tgt is None:
            continue

        # Rule 5: edge references valid nodes
        if src not in node_ids:
            errors.append(ValidationError(node_id=src, message=f"Edge references unknown source node: {src}"))
        if tgt not in node_ids:
            errors.append(ValidationError(node_id=tgt, message=f"Edge references unknown target node: {tgt}"))

        if src in node_ids and tgt in node_ids:
            out_edges[src].append(tgt)
            out_handles[src].append(_normalize_if_else_handle(src_handle))
            out_edge_count[src] += 1
            in_edge_count[tgt] += 1

    # Rule 6: no orphan nodes (every non-start node must have at least one in-edge)
    for nid in node_ids:
        if type_map[nid] == "start":
            continue
        if in_edge_count[nid] == 0:
            errors.append(ValidationError(node_id=nid, message="Orphan node: no incoming edges"))

    # Rule 7: cycle detection (Kahn's algorithm)
    in_degree = {nid: 0 for nid in node_ids}
    for nid in node_ids:
        in_degree[nid] = in_edge_count[nid]

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    topo_order: list[str] = []
    while queue:
        nid = queue.popleft()
        topo_order.append(nid)
        for tgt in out_edges[nid]:
            in_degree[tgt] -= 1
            if in_degree[tgt] == 0:
                queue.append(tgt)

    if len(topo_order) != len(node_ids):
        cycle_nodes = node_ids - set(topo_order)
        for nid in cycle_nodes:
            errors.append(ValidationError(node_id=nid, message="Node is part of a cycle"))

    # Rule 8: if_else must have at least 2 outgoing edges
    for nid, nt in type_map.items():
        if nt == "if_else" and out_edge_count[nid] < 2:
            errors.append(ValidationError(node_id=nid, message="if_else node must have at least 2 outgoing edges"))

    # Rule 9: start has no in-edges
    for nid in start_nodes:
        if in_edge_count[nid] > 0:
            errors.append(ValidationError(node_id=nid, message="start node must not have incoming edges"))

    # Rule 9.5: output node must be terminal (no outgoing edges)
    for nid in output_nodes:
        if out_edge_count[nid] > 0:
            errors.append(
                ValidationError(
                    node_id=nid,
                    message="output node must not have outgoing edges",
                )
            )

    # Rule 10: template variable references must point to upstream nodes
    topo_index = {nid: i for i, nid in enumerate(topo_order)}
    _VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")
    for nid in node_ids:
        cfg = config_map.get(nid, {})
        for text in _iter_config_template_texts(cfg):
            for m in _VAR_RE.finditer(text):
                ref_node = m.group(1)
                ref_field = m.group(2)
                if ref_node == "sys":
                    if ref_field not in _SYS_FIELDS:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Template references unsupported sys variable: sys.{ref_field}",
                        ))
                    continue
                if ref_node == "start":
                    if ref_field not in start_allowed_fields:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references unsupported start field: start.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "env":
                    if ref_field not in start_env_var_types:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Template references unknown env variable: env.{ref_field}",
                            )
                        )
                    continue
                if ref_node == "container" and type_map.get(nid) in {"iteration", "loop"}:
                    continue
                if ref_node not in node_ids:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"Template references unknown node: {ref_node}",
                    ))
                elif nid in topo_index and ref_node in topo_index:
                    if topo_index[ref_node] >= topo_index[nid]:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Template references non-upstream node: {ref_node}",
                        ))

        # Rule 11: tool node config validation (save-time)
        if type_map.get(nid) == "tool":
            tool_name = _cfg_get(cfg, "tool_name", "toolName", default="")
            if not isinstance(tool_name, str) or not tool_name.strip():
                errors.append(ValidationError(
                    node_id=nid,
                    message="Tool node requires toolName",
                ))

            input_bindings = _cfg_get(cfg, "input_bindings", "inputBindings")
            if input_bindings is None:
                errors.append(ValidationError(
                    node_id=nid,
                    message="Tool node requires inputBindings; legacy argsFrom/argsTemplate are no longer supported",
                ))
            elif not isinstance(input_bindings, dict):
                errors.append(ValidationError(
                    node_id=nid,
                    message="tool.inputBindings must be an object",
                ))
            else:
                for key, value in input_bindings.items():
                    if not isinstance(key, str) or not key.strip():
                        errors.append(ValidationError(
                            node_id=nid,
                            message="tool.inputBindings contains empty parameter name",
                        ))
                        continue
                    if not isinstance(value, str):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"tool.inputBindings['{key}'] must be a string",
                        ))

        # Rule 12: if_else config and edge-handle validation
        if type_map.get(nid) == "if_else":
            normalized = _normalize_if_else_config(cfg)
            branches = normalized.get("branches")
            else_handle = str(normalized.get("else_handle") or "else")

            if not isinstance(branches, list) or not branches:
                errors.append(ValidationError(
                    node_id=nid,
                    message="if_else requires at least one IF/ELIF branch",
                ))
                continue

            branch_ids: list[str] = []
            for branch in branches:
                if not isinstance(branch, dict):
                    continue
                branch_id = _normalize_if_else_handle(branch.get("id"))
                if not branch_id or not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch has invalid id: {branch.get('id')}",
                    ))
                    continue
                if branch_id in branch_ids:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch id duplicated: {branch_id}",
                    ))
                branch_ids.append(branch_id)

                logic = str(branch.get("logic") or "and").strip().lower()
                if logic not in {"and", "or"}:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch '{branch_id}' has invalid logic: {logic}",
                    ))

                conditions = branch.get("conditions")
                if not isinstance(conditions, list) or not conditions:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else branch '{branch_id}' requires at least one condition",
                    ))
                    continue

                for cond in conditions:
                    if not isinstance(cond, dict):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"if_else branch '{branch_id}' contains invalid condition item",
                        ))
                        continue
                    var = str(cond.get("variable") or "").strip()
                    if not var:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"if_else branch '{branch_id}' contains empty condition variable",
                        ))
                        continue
                    if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Invalid condition variable path: {var}",
                        ))
                    if var.startswith("sys."):
                        sys_field = var.split(".", 1)[1]
                        if sys_field not in _SYS_FIELDS:
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Unsupported sys variable in condition: {var}",
                            ))
                    if var.startswith("env."):
                        env_name = var.split(".", 1)[1]
                        if env_name not in start_env_var_types:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Unknown env variable in condition: {var}",
                                )
                            )

                    raw_op = str(cond.get("operator") or "").strip().lower()
                    op = _normalize_if_else_operator(raw_op)
                    if op not in _IF_ELSE_ALL_OPERATORS:
                        errors.append(ValidationError(
                            node_id=nid,
                            message=f"Unsupported condition operator: {raw_op or cond.get('operator')}",
                        ))
                        continue

                    if op not in {"is_empty", "is_not_empty"}:
                        raw_value = cond.get("value")
                        value = "" if raw_value is None else str(raw_value)
                        if not value.strip():
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Condition operator '{op}' requires value",
                            ))

            normalized_out_handles = [_normalize_if_else_handle(h) for h in out_handles.get(nid, [])]
            if normalized_out_handles.count(else_handle) != 1:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"if_else requires exactly one '{else_handle}' outgoing edge",
                ))

            expected_handles = set(branch_ids)
            expected_handles.add(else_handle)
            for handle in expected_handles:
                count = normalized_out_handles.count(handle)
                if count != 1:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else handle '{handle}' must map to exactly one outgoing edge",
                    ))

            for handle in normalized_out_handles:
                if handle not in expected_handles:
                    errors.append(ValidationError(
                        node_id=nid,
                        message=f"if_else has unknown outgoing handle: {handle}",
                    ))

        if type_map.get(nid) == "human_in_loop":
            _validate_human_in_loop_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="human_in_loop",
            )
            normalized_out_handles = [
                str(handle or "").strip().lower()
                for handle in out_handles.get(nid, [])
            ]
            expected_handles = {"approved", "rejected"}
            for handle in expected_handles:
                count = normalized_out_handles.count(handle)
                if count != 1:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"human_in_loop handle '{handle}' must map to exactly one outgoing edge",
                        )
                    )
            for handle in normalized_out_handles:
                if handle not in expected_handles:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"human_in_loop has unknown outgoing handle: {handle}",
                        )
                    )

        # Rule 13: output node config validation (save-time)
        if type_map.get(nid) == "output":
            output_mode_raw = _cfg_get(cfg, "output_mode", "outputMode", default="text")
            output_mode = str(output_mode_raw or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode not in {"text", "structured"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"Unsupported output outputMode: {output_mode_raw}",
                    )
                )
                continue

            text_template = _cfg_get(cfg, "text_template", "textTemplate", default="")
            output_fields = _cfg_get(cfg, "output_fields", "outputFields", default=None)

            if output_mode == "text":
                if text_template is not None and not isinstance(text_template, str):
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message="output text mode requires textTemplate to be a string",
                        )
                    )
            else:
                if not isinstance(output_fields, list) or not output_fields:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message="output structured mode requires outputFields",
                        )
                    )
                else:
                    for field in output_fields:
                        if not isinstance(field, dict):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message="output outputFields items must be objects",
                                )
                            )
                            continue

                        field_name = str(field.get("name", "") or "").strip()
                        if not field_name or not _OUTPUT_FIELD_NAME_RE.fullmatch(field_name):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Invalid output field name: {field_name}",
                                )
                            )

                        value = field.get("value", None)
                        if not isinstance(value, str):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"output field '{field_name or '<unknown>'}' requires string value template",
                                )
                            )

                        field_type_raw = field.get("type", "string")
                        field_type = str(field_type_raw or "string").strip().lower() or "string"
                        if field_type not in _OUTPUT_FIELD_TYPES:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Invalid output field type: {field_type_raw}",
                                )
                            )

                        nullable_raw = field.get("nullable", None)
                        if nullable_raw is not None and not isinstance(nullable_raw, bool):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"output field '{field_name or '<unknown>'}' nullable must be boolean",
                                )
                            )

                        items_type_raw = field.get("items_type", field.get("itemsType"))
                        items_type = str(items_type_raw or "").strip().lower()
                        if field_type == "array":
                            if not items_type:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=f"output field '{field_name}' type=array requires itemsType",
                                    )
                                )
                            elif items_type not in _OUTPUT_FIELD_TYPES or items_type == "array":
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=(
                                            f"output field '{field_name}' has invalid itemsType: {items_type_raw}"
                                        ),
                                    )
                                )

                        enum_value = field.get("enum")
                        if enum_value is not None:
                            if not isinstance(enum_value, list) or any(
                                not isinstance(item, str) or not item.strip()
                                for item in enum_value
                            ):
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=f"output field '{field_name}' enum must be non-empty string list",
                                    )
                                )

        # Rule 14: llm knowledge binding config validation (save-time)
        if type_map.get(nid) == "llm":
            knowledge_source_ids = _cfg_str_list(cfg, "knowledge_source_node_ids", "knowledgeSourceNodeIds")
            raw_inject_mode = str(
                _cfg_get(cfg, "knowledge_inject_mode", "knowledgeInjectMode", default="references_only")
                or "references_only"
            ).strip().lower()
            if raw_inject_mode not in {"references_only", "full_payload"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"Unsupported llm knowledgeInjectMode: {raw_inject_mode}",
                    )
                )
            raw_max_refs = _cfg_get(cfg, "knowledge_max_refs", "knowledgeMaxRefs", default=None)
            if raw_max_refs is not None and str(raw_max_refs).strip() != "":
                try:
                    max_refs_val = int(raw_max_refs)
                except Exception:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message="llm knowledgeMaxRefs must be an integer",
                        )
                    )
                else:
                    if max_refs_val < 1 or max_refs_val > 100:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message="llm knowledgeMaxRefs must be between 1 and 100",
                            )
                        )

            for source_id in knowledge_source_ids:
                if source_id not in node_ids:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"llm knowledge source node not found: {source_id}",
                        )
                    )
                    continue
                if type_map.get(source_id) != "knowledge_retrieval":
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"llm knowledge source must be knowledge_retrieval node: {source_id}",
                        )
                    )
                if nid in topo_index and source_id in topo_index and topo_index[source_id] >= topo_index[nid]:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"llm knowledge source must be upstream node: {source_id}",
                        )
                    )

        # Rule 15: llm / parameter_extractor model selection validation (save-time)
        if type_map.get(nid) in {"llm", "parameter_extractor"}:
            raw_model_source = _cfg_get(cfg, "model_source", "modelSource", default=None)
            model_source = str(raw_model_source or "default").strip().lower() or "default"
            if model_source not in {"default", "custom"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"Unsupported node modelSource: {raw_model_source}",
                    )
                )

            raw_model_id = _cfg_get(cfg, "model_id", "modelId", default=None)
            model_id = str(raw_model_id).strip() if raw_model_id is not None else ""
            if model_source == "custom" and not model_id:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="custom modelSource requires modelId",
                    )
                )
            if model_source == "default" and model_id:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="default modelSource must not provide modelId",
                    )
                )
            if model_id:
                try:
                    UUID(model_id)
                except Exception:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message=f"Invalid node modelId (must be UUID): {model_id}",
                        )
                    )

        # Rule 16: parameter_extractor config validation (save-time)
        if type_map.get(nid) == "parameter_extractor":
            input_content = _cfg_get(cfg, "input_content", "inputContent", default=None)
            if input_content is not None and not isinstance(input_content, str):
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="parameter_extractor inputContent must be a string",
                    )
                )

            _validate_output_fields_config(
                node_id=nid,
                node_type="parameter_extractor",
                output_fields=_cfg_get(cfg, "output_fields", "outputFields", default=None),
                errors=errors,
                required=True,
            )

        # Rule 17: code_executor config validation (save-time)
        if type_map.get(nid) == "code_executor":
            _validate_code_executor_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="code_executor",
                validate_timeout=True,
            )

        # Rule 17.5: variable_assign config validation (save-time)
        if type_map.get(nid) == "variable_assign":
            _validate_variable_assign_node_config(
                node_id=nid,
                cfg=cfg,
                env_var_types=start_env_var_types,
                errors=errors,
                subject="variable_assign",
            )

        # Rule 18: iteration container validation
        if type_map.get(nid) == "iteration":
            input_source = _cfg_get(cfg, "input_source", "inputSource", default=None)
            if not isinstance(input_source, str) or not input_source.strip():
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="iteration inputSource is required and must be a string",
                    )
                )
            output_variable = str(_cfg_get(cfg, "output_variable", "outputVariable", default="") or "").strip()
            if not output_variable or not _OUTPUT_FIELD_NAME_RE.fullmatch(output_variable):
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="iteration outputVariable must match [a-zA-Z0-9_]+",
                    )
                )
            output_selector = _cfg_get(cfg, "output_selector", "outputSelector", default=None)
            if not isinstance(output_selector, str) or not output_selector.strip():
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="iteration outputSelector is required and must be a string",
                    )
                )
            error_strategy = str(_cfg_get(cfg, "error_strategy", "errorStrategy", default="fail_fast") or "fail_fast").strip().lower()
            if error_strategy not in {"fail_fast", "skip_item"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"iteration errorStrategy is invalid: {error_strategy}",
                    )
                )

            body_nodes, body_edges = _extract_container_body(cfg)
            body_node_map, _, body_topo = _validate_container_subflow(
                nid, "iteration", body_nodes, body_edges, errors
            )
            body_topo_index = {node_id: index for index, node_id in enumerate(body_topo)}
            body_out_handles: dict[str, list[str]] = defaultdict(list)
            for raw_edge in body_edges:
                if not isinstance(raw_edge, dict):
                    continue
                source = str(raw_edge.get("source_node_id", raw_edge.get("sourceNodeId", "")) or "").strip()
                if source not in body_node_map:
                    continue
                handle = str(raw_edge.get("source_handle", raw_edge.get("sourceHandle", "output")) or "output").strip().lower()
                body_out_handles[source].append(handle)
            for body_node_id, raw_body_node in body_node_map.items():
                body_type = str(
                    raw_body_node.get("node_type", raw_body_node.get("nodeType", "")) or ""
                ).strip()
                body_cfg = raw_body_node.get("config")
                if not isinstance(body_cfg, dict):
                    continue
                if body_type == "code_executor":
                    _validate_code_executor_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"iteration body node '{body_node_id}' code_executor",
                        validate_timeout=True,
                    )
                if body_type == "variable_assign":
                    _validate_variable_assign_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        env_var_types=start_env_var_types,
                        errors=errors,
                        subject=f"iteration body node '{body_node_id}' variable_assign",
                    )
                if body_type == "human_in_loop":
                    _validate_human_in_loop_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"iteration body node '{body_node_id}' human_in_loop",
                    )
                    normalized_body_handles = body_out_handles.get(body_node_id, [])
                    expected_handles = {"approved", "rejected"}
                    for handle in expected_handles:
                        count = normalized_body_handles.count(handle)
                        if count != 1:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"iteration body node '{body_node_id}' "
                                        f"handle '{handle}' must map to exactly one outgoing edge"
                                    ),
                                )
                            )
                    for handle in normalized_body_handles:
                        if handle not in expected_handles:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"iteration body node '{body_node_id}' has unknown "
                                        f"outgoing handle: {handle}"
                                    ),
                                )
                            )
                for text in _iter_config_template_texts(body_cfg):
                    for m in _VAR_RE.finditer(text):
                        ref_node = m.group(1)
                        ref_field = m.group(2)
                        if ref_node == "sys":
                            if ref_field not in _SYS_FIELDS:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=f"iteration body node '{body_node_id}' references unsupported sys variable: sys.{ref_field}",
                                    )
                                )
                            continue
                        if ref_node == "start":
                            if ref_field != "user_input":
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=(
                                            f"iteration body node '{body_node_id}' references unsupported start field: "
                                            f"start.{ref_field}"
                                        ),
                                    )
                                )
                            continue
                        if ref_node == "env":
                            if ref_field not in start_env_var_types:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=(
                                            f"iteration body node '{body_node_id}' references unknown "
                                            f"env variable: env.{ref_field}"
                                        ),
                                    )
                                )
                            continue
                        if ref_node == "container":
                            continue
                        if ref_node not in body_node_map:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"iteration body node '{body_node_id}' references unknown node: {ref_node}",
                                )
                            )
                            continue
                        if (
                            body_node_id in body_topo_index
                            and ref_node in body_topo_index
                            and body_topo_index[ref_node] >= body_topo_index[body_node_id]
                        ):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"iteration body node '{body_node_id}' references non-upstream node: {ref_node}"
                                    ),
                                )
                            )

        # Rule 17: loop container validation
        if type_map.get(nid) == "loop":
            max_iterations_raw = _cfg_get(cfg, "max_iterations", "maxIterations", default=10)
            try:
                max_iterations = int(max_iterations_raw)
            except Exception:
                max_iterations = 0
            if max_iterations < 1 or max_iterations > 1000:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="loop maxIterations must be between 1 and 1000",
                    )
                )

            termination_logic = str(_cfg_get(cfg, "termination_logic", "terminationLogic", default="and") or "and").strip().lower()
            if termination_logic not in {"and", "or"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"loop terminationLogic is invalid: {termination_logic}",
                    )
                )

            initial_vars = _cfg_get(cfg, "initial_vars", "initialVars", default=[])
            if initial_vars is not None and not isinstance(initial_vars, list):
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="loop initialVars must be a list",
                    )
                )
            elif isinstance(initial_vars, list):
                for item in initial_vars:
                    if not isinstance(item, dict):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message="loop initialVars items must be objects",
                            )
                        )
                        continue
                    name = str(item.get("name", "") or "").strip()
                    if not name or not _OUTPUT_FIELD_NAME_RE.fullmatch(name):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"loop initialVars contains invalid variable name: {name}",
                            )
                        )

            update_mappings = _cfg_get(cfg, "update_mappings", "updateMappings", default=[])
            if update_mappings is not None and not isinstance(update_mappings, list):
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="loop updateMappings must be a list",
                    )
                )
            elif isinstance(update_mappings, list):
                for item in update_mappings:
                    if not isinstance(item, dict):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message="loop updateMappings items must be objects",
                            )
                        )
                        continue
                    name = str(item.get("name", "") or "").strip()
                    value = item.get("value")
                    if not name or not _OUTPUT_FIELD_NAME_RE.fullmatch(name):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"loop updateMappings contains invalid variable name: {name}",
                            )
                        )
                    if not isinstance(value, str) or not value.strip():
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"loop updateMappings variable '{name or '<empty>'}' requires string value",
                            )
                        )

            termination_conditions = _cfg_get(cfg, "termination_conditions", "terminationConditions", default=[])
            if termination_conditions is not None and not isinstance(termination_conditions, list):
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message="loop terminationConditions must be a list",
                    )
                )
            elif isinstance(termination_conditions, list):
                for cond in termination_conditions:
                    if not isinstance(cond, dict):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message="loop terminationConditions contains invalid condition item",
                            )
                        )
                        continue
                    var = str(cond.get("variable", "") or "").strip()
                    if not var:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message="loop terminationConditions requires variable",
                            )
                        )
                        continue
                    if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=f"Invalid loop condition variable path: {var}",
                            )
                        )
                        continue
                    if var.startswith("sys."):
                        sys_field = var.split(".", 1)[1]
                        if sys_field not in _SYS_FIELDS:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Unsupported sys variable in loop condition: {var}",
                                )
                            )
                    if var.startswith("env."):
                        env_name = var.split(".", 1)[1]
                        if env_name not in start_env_var_types:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Unknown env variable in loop condition: {var}",
                                )
                            )

            body_nodes, body_edges = _extract_container_body(cfg)
            body_node_map, _, body_topo = _validate_container_subflow(
                nid, "loop", body_nodes, body_edges, errors
            )
            body_topo_index = {node_id: index for index, node_id in enumerate(body_topo)}
            body_out_handles: dict[str, list[str]] = defaultdict(list)
            for raw_edge in body_edges:
                if not isinstance(raw_edge, dict):
                    continue
                source = str(raw_edge.get("source_node_id", raw_edge.get("sourceNodeId", "")) or "").strip()
                if source not in body_node_map:
                    continue
                handle = str(raw_edge.get("source_handle", raw_edge.get("sourceHandle", "output")) or "output").strip().lower()
                body_out_handles[source].append(handle)
            for body_node_id, raw_body_node in body_node_map.items():
                body_type = str(
                    raw_body_node.get("node_type", raw_body_node.get("nodeType", "")) or ""
                ).strip()
                body_cfg = raw_body_node.get("config")
                if not isinstance(body_cfg, dict):
                    continue
                if body_type == "code_executor":
                    _validate_code_executor_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"loop body node '{body_node_id}' code_executor",
                        validate_timeout=True,
                    )
                if body_type == "variable_assign":
                    _validate_variable_assign_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        env_var_types=start_env_var_types,
                        errors=errors,
                        subject=f"loop body node '{body_node_id}' variable_assign",
                    )
                if body_type == "human_in_loop":
                    _validate_human_in_loop_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"loop body node '{body_node_id}' human_in_loop",
                    )
                    normalized_body_handles = body_out_handles.get(body_node_id, [])
                    expected_handles = {"approved", "rejected"}
                    for handle in expected_handles:
                        count = normalized_body_handles.count(handle)
                        if count != 1:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"loop body node '{body_node_id}' handle '{handle}' "
                                        "must map to exactly one outgoing edge"
                                    ),
                                )
                            )
                    for handle in normalized_body_handles:
                        if handle not in expected_handles:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"loop body node '{body_node_id}' has unknown outgoing handle: {handle}"
                                    ),
                                )
                            )
                for text in _iter_config_template_texts(body_cfg):
                    for m in _VAR_RE.finditer(text):
                        ref_node = m.group(1)
                        ref_field = m.group(2)
                        if ref_node == "sys":
                            if ref_field not in _SYS_FIELDS:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=f"loop body node '{body_node_id}' references unsupported sys variable: sys.{ref_field}",
                                    )
                                )
                            continue
                        if ref_node == "start":
                            if ref_field != "user_input":
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=(
                                            f"loop body node '{body_node_id}' references unsupported start field: "
                                            f"start.{ref_field}"
                                        ),
                                    )
                                )
                            continue
                        if ref_node == "env":
                            if ref_field not in start_env_var_types:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=(
                                            f"loop body node '{body_node_id}' references unknown "
                                            f"env variable: env.{ref_field}"
                                        ),
                                    )
                                )
                            continue
                        if ref_node == "container":
                            continue
                        if ref_node not in body_node_map:
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"loop body node '{body_node_id}' references unknown node: {ref_node}",
                                )
                            )
                            continue
                        if (
                            body_node_id in body_topo_index
                            and ref_node in body_topo_index
                            and body_topo_index[ref_node] >= body_topo_index[body_node_id]
                        ):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=(
                                        f"loop body node '{body_node_id}' references non-upstream node: {ref_node}"
                                    ),
                                )
                            )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_workflow_compile(
    nodes: Sequence,
    edges: Sequence,
    tool_names: set[str] | None = None,
) -> ValidationResult:
    """Extended validation for compilation (Task 13.2).

    Checks tool_name existence, output_fields format, condition expressions.
    """
    result = validate_workflow(nodes, edges)
    errors = list(result.errors)
    start_env_var_types: dict[str, str] = {}
    for n in nodes:
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        if ntype != "start":
            continue
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        if not isinstance(cfg, dict):
            cfg = {}
        start_env_var_types, _ = _resolve_start_env_var_contract(cfg)
        break

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        cfg = getattr(n, "config", None) or (n.get("config") if isinstance(n, dict) else None)
        if not isinstance(cfg, dict):
            cfg = {}

        # Tool node: runtime tool map existence
        if ntype == "tool" and tool_names is not None:
            tool_name = _cfg_get(cfg, "tool_name", "toolName", default="")
            if isinstance(tool_name, str) and tool_name.strip() and tool_name not in tool_names:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"Tool node references unknown tool: {tool_name}",
                ))

        # LLM node: output_mode/output_fields format
        if ntype == "llm":
            output_mode_raw = _cfg_get(cfg, "output_mode", "outputMode", default="text")
            output_mode = str(output_mode_raw or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode not in {"text", "structured"}:
                errors.append(ValidationError(
                    node_id=nid,
                    message=f"Unsupported llm output_mode: {output_mode_raw}",
                ))

            output_fields = _cfg_get(cfg, "output_fields", "outputFields")
            if output_mode == "structured" and (not isinstance(output_fields, list) or not output_fields):
                errors.append(ValidationError(
                    node_id=nid,
                    message="LLM structured mode requires output_fields",
                ))

            if output_fields is not None and output_fields != []:
                if not isinstance(output_fields, list):
                    errors.append(ValidationError(
                        node_id=nid,
                        message="LLM node output_fields must be a list",
                    ))
                else:
                    for f in output_fields:
                        if isinstance(f, dict):
                            name = f.get("name", "")
                            if not name or not re.fullmatch(r"[a-zA-Z0-9_]+", str(name)):
                                errors.append(ValidationError(
                                    node_id=nid,
                                    message=f"Invalid output field name: {name}",
                                ))

        # Output node: output_mode/output_fields format
        if ntype == "output":
            output_mode_raw = _cfg_get(cfg, "output_mode", "outputMode", default="text")
            output_mode = str(output_mode_raw or "text").strip().lower()
            if output_mode == "json":
                output_mode = "structured"
            if output_mode not in {"text", "structured"}:
                errors.append(
                    ValidationError(
                        node_id=nid,
                        message=f"Unsupported output output_mode: {output_mode_raw}",
                    )
                )
                continue

            text_template = _cfg_get(cfg, "text_template", "textTemplate", default="")
            output_fields = _cfg_get(cfg, "output_fields", "outputFields", default=None)
            if output_mode == "text":
                if text_template is not None and not isinstance(text_template, str):
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message="output text mode requires textTemplate to be a string",
                        )
                    )
            else:
                if not isinstance(output_fields, list) or not output_fields:
                    errors.append(
                        ValidationError(
                            node_id=nid,
                            message="output structured mode requires output_fields",
                        )
                    )
                else:
                    for f in output_fields:
                        if not isinstance(f, dict):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message="output output_fields items must be objects",
                                )
                            )
                            continue
                        name = str(f.get("name", "") or "").strip()
                        if not name or not re.fullmatch(r"[a-zA-Z0-9_]+", name):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"Invalid output field name: {name}",
                                )
                            )
                        if not isinstance(f.get("value"), str):
                            errors.append(
                                ValidationError(
                                    node_id=nid,
                                    message=f"output field '{name or '<unknown>'}' requires string value",
                                )
                            )

        # if_else: compile-time variable path check for normalized branches
        if ntype == "if_else":
            normalized = _normalize_if_else_config(cfg)
            branches = normalized.get("branches", [])
            if isinstance(branches, list):
                for branch in branches:
                    if not isinstance(branch, dict):
                        continue
                    for cond in (branch.get("conditions") or []):
                        if not isinstance(cond, dict):
                            continue
                        var = str(cond.get("variable") or "").strip()
                        if not var:
                            continue
                        if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                            errors.append(ValidationError(
                                node_id=nid,
                                message=f"Invalid condition variable path: {var}",
                            ))
                            continue
                        if var.startswith("sys."):
                            sys_field = var.split(".", 1)[1]
                            if sys_field not in _SYS_FIELDS:
                                errors.append(ValidationError(
                                    node_id=nid,
                                    message=f"Unsupported sys variable in condition: {var}",
                                ))
                        if var.startswith("env."):
                            env_name = var.split(".", 1)[1]
                            if env_name not in start_env_var_types:
                                errors.append(
                                    ValidationError(
                                        node_id=nid,
                                        message=f"Unknown env variable in condition: {var}",
                                    )
                                )

        if ntype == "code_executor":
            _validate_code_executor_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="code_executor",
                validate_timeout=True,
            )
        if ntype == "variable_assign":
            _validate_variable_assign_node_config(
                node_id=nid,
                cfg=cfg,
                env_var_types=start_env_var_types,
                errors=errors,
                subject="variable_assign",
            )
        if ntype == "human_in_loop":
            _validate_human_in_loop_node_config(
                node_id=nid,
                cfg=cfg,
                errors=errors,
                subject="human_in_loop",
            )

        # iteration/loop body nodes: compile-time checks
        if ntype in {"iteration", "loop"}:
            body_nodes, _ = _extract_container_body(cfg)
            for raw_body_node in body_nodes:
                if not isinstance(raw_body_node, dict):
                    continue
                body_node_id = str(raw_body_node.get("node_id", raw_body_node.get("nodeId", "")) or "").strip() or "<unknown>"
                body_type = str(raw_body_node.get("node_type", raw_body_node.get("nodeType", "")) or "").strip()
                body_cfg = raw_body_node.get("config")
                if not isinstance(body_cfg, dict):
                    body_cfg = {}

                if body_type == "tool" and tool_names is not None:
                    body_tool_name = _cfg_get(body_cfg, "tool_name", "toolName", default="")
                    if (
                        isinstance(body_tool_name, str)
                        and body_tool_name.strip()
                        and body_tool_name not in tool_names
                    ):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=(
                                    f"{ntype} body node '{body_node_id}' references unknown tool: {body_tool_name}"
                                ),
                            )
                        )

                if body_type == "llm":
                    body_output_mode_raw = _cfg_get(body_cfg, "output_mode", "outputMode", default="text")
                    body_output_mode = str(body_output_mode_raw or "text").strip().lower()
                    if body_output_mode == "json":
                        body_output_mode = "structured"
                    if body_output_mode not in {"text", "structured"}:
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=(
                                    f"{ntype} body node '{body_node_id}' has unsupported llm output_mode: "
                                    f"{body_output_mode_raw}"
                                ),
                            )
                        )

                    body_output_fields = _cfg_get(body_cfg, "output_fields", "outputFields")
                    if body_output_mode == "structured" and (
                        not isinstance(body_output_fields, list) or not body_output_fields
                    ):
                        errors.append(
                            ValidationError(
                                node_id=nid,
                                message=(
                                    f"{ntype} body node '{body_node_id}' structured mode requires output_fields"
                                ),
                            )
                        )

                if body_type == "if_else":
                    normalized = _normalize_if_else_config(body_cfg)
                    branches = normalized.get("branches", [])
                    if isinstance(branches, list):
                        for branch in branches:
                            if not isinstance(branch, dict):
                                continue
                            for cond in (branch.get("conditions") or []):
                                if not isinstance(cond, dict):
                                    continue
                                var = str(cond.get("variable") or "").strip()
                                if not var:
                                    continue
                                if not re.fullmatch(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", var):
                                    errors.append(
                                        ValidationError(
                                            node_id=nid,
                                            message=(
                                                f"{ntype} body node '{body_node_id}' has invalid "
                                                f"condition variable path: {var}"
                                            ),
                                        )
                                    )
                                    continue
                                if var.startswith("sys."):
                                    sys_field = var.split(".", 1)[1]
                                    if sys_field not in _SYS_FIELDS:
                                        errors.append(
                                            ValidationError(
                                                node_id=nid,
                                                message=(
                                                    f"{ntype} body node '{body_node_id}' uses unsupported "
                                                    f"sys variable in condition: {var}"
                                                ),
                                            )
                                        )
                                if var.startswith("env."):
                                    env_name = var.split(".", 1)[1]
                                    if env_name not in start_env_var_types:
                                        errors.append(
                                            ValidationError(
                                                node_id=nid,
                                                message=(
                                                    f"{ntype} body node '{body_node_id}' uses unknown "
                                                    f"env variable in condition: {var}"
                                                ),
                                            )
                                        )

                if body_type == "code_executor":
                    _validate_code_executor_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"{ntype} body node '{body_node_id}' code_executor",
                        validate_timeout=True,
                    )
                if body_type == "variable_assign":
                    _validate_variable_assign_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        env_var_types=start_env_var_types,
                        errors=errors,
                        subject=f"{ntype} body node '{body_node_id}' variable_assign",
                    )
                if body_type == "human_in_loop":
                    _validate_human_in_loop_node_config(
                        node_id=nid,
                        cfg=body_cfg,
                        errors=errors,
                        subject=f"{ntype} body node '{body_node_id}' human_in_loop",
                    )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_parallel_branches(
    nodes: Sequence,
    edges: Sequence,
) -> ValidationResult:
    """Validate parallel branch constraints (Task 13.3).

    - Max parallel depth: 3
    - Max fan-out edges per node: 5
    - No nested if_else inside parallel branches
    """
    errors: list[ValidationError] = []

    node_types: dict[str, str] = {}
    out_edges: dict[str, list[str]] = defaultdict(list)

    for n in nodes:
        nid = getattr(n, "node_id", None) or (n.get("node_id") if isinstance(n, dict) else None)
        ntype = getattr(n, "node_type", None) or (n.get("node_type") if isinstance(n, dict) else None)
        if nid:
            node_types[nid] = ntype or ""

    for e in edges:
        src = getattr(e, "source_node_id", None) or (e.get("source_node_id") if isinstance(e, dict) else None)
        tgt = getattr(e, "target_node_id", None) or (e.get("target_node_id") if isinstance(e, dict) else None)
        if src and tgt:
            out_edges[src].append(tgt)

    # Find fan-out points (nodes with >1 outgoing edges, excluding if_else which is conditional)
    fan_out_nodes = [
        nid for nid, targets in out_edges.items()
        if len(targets) > 1 and node_types.get(nid) != "if_else"
    ]

    # Max fan-out: 5 edges
    for nid in fan_out_nodes:
        if len(out_edges[nid]) > 5:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Fan-out exceeds limit: {len(out_edges[nid])} edges (max 5)",
            ))

    # Parallel depth check via DFS from fan-out points
    def _find_parallel_depth(start: str, depth: int) -> int:
        """Recursively find max parallel nesting depth."""
        max_depth = depth
        for tgt in out_edges.get(start, []):
            # Check if this target is itself a fan-out (nested parallel)
            if len(out_edges.get(tgt, [])) > 1 and node_types.get(tgt) != "if_else":
                nested = _find_parallel_depth(tgt, depth + 1)
                max_depth = max(max_depth, nested)
        return max_depth

    for nid in fan_out_nodes:
        depth = _find_parallel_depth(nid, 1)
        if depth > 3:
            errors.append(ValidationError(
                node_id=nid,
                message=f"Parallel branch nesting depth {depth} exceeds limit (max 3)",
            ))

    # No if_else inside parallel branches
    for fan_nid in fan_out_nodes:
        visited: set[str] = set()
        queue = deque(out_edges.get(fan_nid, []))
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            if node_types.get(cur) == "if_else":
                errors.append(ValidationError(
                    node_id=cur,
                    message="if_else node not allowed inside parallel branches",
                ))
            queue.extend(out_edges.get(cur, []))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
