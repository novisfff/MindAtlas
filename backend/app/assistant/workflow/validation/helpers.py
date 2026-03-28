from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any

from app.assistant.workflow.code_executor import (
    extract_javascript_imports,
    extract_python_imports,
    get_javascript_allowed_modules,
    get_python_allowed_modules,
    has_javascript_dynamic_import,
)
from app.assistant.workflow.env_vars import parse_env_var_specs
from app.assistant.workflow.validation.contracts import (
    _CODE_EXECUTOR_INPUT_KEY_RE,
    _CONTAINER_BODY_ALLOWED_NODE_TYPES,
    _OUTPUT_FIELD_NAME_RE,
    _OUTPUT_FIELD_TYPES,
    _START_MEMORY_RESERVED_FIELDS,
    _START_MEMORY_MODES,
    _START_MEMORY_STRUCTURED_FIELDS,
    _START_INPUT_FIELD_NAME_RE,
    _START_INPUT_FIELD_TYPES,
    _START_INPUT_MODES,
)
from app.assistant.workflow.validation.models import ValidationError


def cfg_get(cfg: dict, *keys: str, default=None):
    for key in keys:
        if key in cfg:
            return cfg.get(key)
    return default


def is_valid_node_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_]+", value))


def extract_container_body(cfg: dict) -> tuple[list[dict], list[dict]]:
    body_nodes_raw = cfg_get(cfg, "body_nodes", "bodyNodes", default=[])
    body_edges_raw = cfg_get(cfg, "body_edges", "bodyEdges", default=[])
    body_nodes = body_nodes_raw if isinstance(body_nodes_raw, list) else []
    body_edges = body_edges_raw if isinstance(body_edges_raw, list) else []
    return body_nodes, body_edges


def validate_container_subflow(
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
        if not node_id or not is_valid_node_id(node_id):
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


def iter_config_template_texts(cfg: dict) -> list[str]:
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
        "url",
        "json_body_template", "jsonBodyTemplate",
        "raw_body_template", "rawBodyTemplate",
        "bearer_token", "bearerToken",
        "api_key_value", "apiKeyValue",
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

    for key in ("headers", "query_params", "queryParams", "form_body", "formBody"):
        items = cfg.get(key)
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, str):
                    texts.append(value)

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


def resolve_start_memory_mode_contract(cfg: dict) -> tuple[str, list[str]]:
    errors: list[str] = []
    raw_mode = cfg_get(cfg, "memory_mode", "memoryMode", default="auto")
    mode = str(raw_mode or "auto").strip().lower() or "auto"
    if mode not in _START_MEMORY_MODES:
        errors.append(f"start memoryMode is invalid: {raw_mode}")
        mode = "auto"
    return mode, errors


def resolve_start_input_contract(cfg: dict) -> tuple[str, str, set[str], list[str]]:
    errors: list[str] = []
    raw_mode = cfg_get(cfg, "input_mode", "inputMode", default="text")
    mode = str(raw_mode or "text").strip().lower()
    if mode not in _START_INPUT_MODES:
        errors.append(f"start inputMode is invalid: {raw_mode}")
        mode = "text"
    memory_mode, memory_mode_errors = resolve_start_memory_mode_contract(cfg)
    errors.extend(memory_mode_errors)

    if mode == "text":
        allowed = {"user_input"}
        if memory_mode == "structured":
            allowed.update(_START_MEMORY_STRUCTURED_FIELDS)
        return mode, memory_mode, allowed, errors

    structured_fields_raw = cfg_get(cfg, "structured_fields", "structuredFields", default=None)
    if not isinstance(structured_fields_raw, list) or not structured_fields_raw:
        errors.append("start structured mode requires at least one structured field")
        allowed = set(_START_MEMORY_STRUCTURED_FIELDS) if memory_mode == "structured" else set()
        return mode, memory_mode, allowed, errors

    field_names: set[str] = set()
    for idx, raw_field in enumerate(structured_fields_raw, start=1):
        if not isinstance(raw_field, dict):
            errors.append(f"start structured field #{idx} must be an object")
            continue
        field_name = str(raw_field.get("name", "") or "").strip()
        if not field_name:
            errors.append(f"start structured field #{idx} requires name")
            continue
        if field_name == "user_input" or field_name in _START_MEMORY_RESERVED_FIELDS:
            errors.append(f"start structured field name '{field_name}' is reserved")
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
        if field_type == "array":
            items_type_raw = raw_field.get("items_type", raw_field.get("itemsType"))
            items_type = str(items_type_raw or "").strip().lower()
            if not items_type:
                errors.append(f"start structured field '{field_name}' array type requires itemsType")
            elif items_type not in (_OUTPUT_FIELD_TYPES - {"array"}):
                errors.append(
                    f"start structured field '{field_name}' has invalid array items type: {items_type_raw}"
                )

        required = raw_field.get("required", False)
        if not isinstance(required, bool):
            errors.append(f"start structured field '{field_name}' required must be boolean")

        field_names.add(field_name)

    allowed_fields = set(field_names)
    if memory_mode == "structured":
        allowed_fields.update(_START_MEMORY_STRUCTURED_FIELDS)
    return mode, memory_mode, allowed_fields, errors


def resolve_start_env_var_contract(cfg: dict) -> tuple[dict[str, str], list[str]]:
    if not isinstance(cfg, dict):
        return ({}, [])

    specs, parse_errors = parse_env_var_specs(
        cfg_get(cfg, "session_vars", "sessionVars", default=None)
    )
    if parse_errors:
        return ({}, parse_errors)
    return ({spec.name: spec.type for spec in specs}, [])


def validate_output_fields_config(
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


def validate_code_executor_imports(
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


def validate_code_executor_signature(
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


def validate_code_executor_input_bindings(
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
