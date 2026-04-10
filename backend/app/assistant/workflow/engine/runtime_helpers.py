from __future__ import annotations

import json
import inspect
import logging
import re
from functools import lru_cache
from typing import Any, Callable

from sqlalchemy.orm import sessionmaker

from app.assistant.tools._context import reset_current_db, set_current_db
from app.assistant.workflow.human_fields import (
    coerce_human_field_value_by_type,
    normalize_human_field_options,
    normalize_human_field_type,
    normalize_human_field_widget,
)
from app.assistant.workflow.engine.state import NodeOutput, StepOutput
from app.assistant.workflow.env_vars import WorkflowEnvVarSpec, parse_env_var_specs

logger = logging.getLogger(__name__)

AGENT_MAX_ITERATIONS = 12
TRACE_CONTEXT_METADATA_KEY = "__trace_context__"

MAX_TEXT_LEN = 8000
START_STRUCTURED_FIELD_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
START_MEMORY_MODES = {"auto", "off", "structured"}
START_MEMORY_STRUCTURED_FIELD_TO_CONTEXT_KEY: dict[str, str] = {
    "memory_recent_dialogue": "l0_text",
    "memory_conversation_summary": "l1_text",
    "memory_skill_facts": "l2_text",
}
START_MEMORY_STRUCTURED_FIELD_NAMES = set(START_MEMORY_STRUCTURED_FIELD_TO_CONTEXT_KEY.keys())
START_MEMORY_LEGACY_FIELD_NAMES = {"memory_l0", "memory_l1", "memory_l2"}
START_MEMORY_RESERVED_FIELD_NAMES = (
    START_MEMORY_STRUCTURED_FIELD_NAMES | START_MEMORY_LEGACY_FIELD_NAMES
)

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_NODE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")
_OUTPUT_SINGLE_VAR_RE = re.compile(r"^\s*\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}\s*$")
_IF_ELSE_HANDLE_RE = re.compile(r"[a-zA-Z0-9_]+")
_IF_ELSE_LEGACY_OPERATOR_MAP = {
    "equals": "is",
    "not_equals": "is_not",
}


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def truncate(text: Any, max_len: int = MAX_TEXT_LEN) -> str:
    s = text if isinstance(text, str) else stringify(text)
    return s[:max_len] if len(s) > max_len else s


def extract_json_object(content: str) -> dict[str, Any] | None:
    raw = (content or "").strip()
    if not raw:
        return None

    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1].strip()
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return None


def extract_single_template_reference(template: str) -> tuple[str, str] | None:
    matched = _OUTPUT_SINGLE_VAR_RE.fullmatch(template or "")
    if not matched:
        return None
    return matched.group(1), matched.group(2)


def parse_output_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"invalid boolean value: {value}")


def coerce_output_field_value(field_name: str, rendered_value: str, field_spec: dict[str, Any]) -> Any:
    raw_type = field_spec.get("type", "string")
    field_type = str(raw_type or "string").strip().lower() or "string"
    nullable = bool(field_spec.get("nullable", False))
    trimmed = rendered_value.strip()
    if nullable and trimmed == "":
        return None

    if field_type == "string":
        out_value: Any = rendered_value
    elif field_type == "number":
        out_value = float(trimmed)
    elif field_type == "integer":
        out_value = int(trimmed)
    elif field_type == "boolean":
        out_value = parse_output_boolean(trimmed if trimmed else rendered_value)
    elif field_type == "object":
        parsed = json.loads(trimmed)
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        out_value = parsed
    elif field_type == "array":
        parsed = json.loads(trimmed)
        if not isinstance(parsed, list):
            raise ValueError("must be a JSON array")
        items_type_raw = field_spec.get("items_type", field_spec.get("itemsType", ""))
        items_type = str(items_type_raw or "").strip().lower()
        if items_type and items_type != "array":
            for idx, item in enumerate(parsed):
                if items_type == "string" and not isinstance(item, str):
                    raise ValueError(f"array item {idx} must be string")
                if items_type == "number" and (
                    not isinstance(item, (int, float)) or isinstance(item, bool)
                ):
                    raise ValueError(f"array item {idx} must be number")
                if items_type == "integer" and (
                    not isinstance(item, int) or isinstance(item, bool)
                ):
                    raise ValueError(f"array item {idx} must be integer")
                if items_type == "boolean" and not isinstance(item, bool):
                    raise ValueError(f"array item {idx} must be boolean")
                if items_type == "object" and not isinstance(item, dict):
                    raise ValueError(f"array item {idx} must be object")
        out_value = parsed
    else:
        raise ValueError(f"unsupported type: {field_type}")

    if out_value is None:
        return None

    enum_values = field_spec.get("enum")
    if isinstance(enum_values, list) and enum_values:
        if str(out_value) not in {str(item) for item in enum_values}:
            raise ValueError(f"value '{out_value}' not in enum")

    return out_value


def emit(metadata: dict[str, Any], event: str, **kwargs: Any) -> None:
    next_kwargs = _apply_trace_context(metadata, event, kwargs)
    cb = metadata.get(event)
    if not callable(cb):
        return
    invoke_callback(cb, **next_kwargs)


def invoke_callback(cb: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        return cb(**kwargs)
    except TypeError:
        # Backward compatibility for callbacks that only accept a subset of fields.
        try:
            sig = inspect.signature(cb)
        except Exception:
            raise

        accepts_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if accepts_var_kwargs:
            raise

        accepted_kwargs: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                if name in kwargs:
                    accepted_kwargs[name] = kwargs[name]

        unknown_keys = [k for k in kwargs if k not in accepted_kwargs]
        if not unknown_keys:
            raise

        if accepted_kwargs:
            return cb(**accepted_kwargs)

        if len(kwargs) == 1:
            return cb(next(iter(kwargs.values())))
        raise


def with_node_execution_context(
    metadata: dict[str, Any] | None,
    *,
    node_id: str,
    node_type: str,
    node_execution_id: str,
) -> dict[str, Any]:
    next_metadata = dict(metadata or {})
    next_metadata[TRACE_CONTEXT_METADATA_KEY] = {
        "node_id": str(node_id or "").strip(),
        "node_type": str(node_type or "").strip(),
        "node_execution_id": str(node_execution_id or "").strip(),
    }
    return next_metadata


def _apply_trace_context(
    metadata: dict[str, Any] | None,
    event: str,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    raw_context = metadata.get(TRACE_CONTEXT_METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(raw_context, dict):
        return kwargs

    next_kwargs = dict(kwargs)
    node_id = str(raw_context.get("node_id", "") or "").strip()
    node_type = str(raw_context.get("node_type", "") or "").strip()
    node_execution_id = str(raw_context.get("node_execution_id", "") or "").strip()

    if node_execution_id and "node_execution_id" not in next_kwargs:
        next_kwargs["node_execution_id"] = node_execution_id

    if event in {"on_tool_call_start", "on_tool_call_end"}:
        if node_id and "node_id" not in next_kwargs:
            next_kwargs["node_id"] = node_id
        if node_type and "node_type" not in next_kwargs:
            next_kwargs["node_type"] = node_type
        return next_kwargs

    if event in {
        "on_node_start",
        "on_node_output_delta",
        "on_node_end",
        "on_branch_decision",
        "on_node_snapshot",
    }:
        if node_id and "node_id" not in next_kwargs:
            next_kwargs["node_id"] = node_id
        if event in {"on_node_start", "on_node_snapshot"} and node_type and "node_type" not in next_kwargs:
            next_kwargs["node_type"] = node_type
    return next_kwargs


def _tool_schema_json(args_schema: Any) -> dict[str, Any] | None:
    if args_schema is None:
        return None
    if hasattr(args_schema, "model_json_schema"):
        try:
            schema = args_schema.model_json_schema()
            return schema if isinstance(schema, dict) else None
        except Exception:
            return None
    if hasattr(args_schema, "schema"):
        try:
            schema = args_schema.schema()
            return schema if isinstance(schema, dict) else None
        except Exception:
            return None
    return None


def _json_schema_types(prop: dict[str, Any]) -> list[str]:
    types: list[str] = []
    raw_type = prop.get("type")
    if isinstance(raw_type, str):
        types.append(raw_type)
    elif isinstance(raw_type, list):
        types.extend(str(item).strip() for item in raw_type if str(item).strip())

    for key in ("anyOf", "oneOf", "allOf"):
        items = prop.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                types.extend(_json_schema_types(item))
    return types


def _json_schema_primary_type(prop: dict[str, Any]) -> str | None:
    candidates = [item for item in _json_schema_types(prop) if item != "null"]
    for expected in ("object", "array", "integer", "number", "boolean", "string"):
        if expected in candidates:
            return expected
    return candidates[0] if candidates else None


def _json_schema_allows_null(prop: dict[str, Any]) -> bool:
    return "null" in _json_schema_types(prop)


def _prepare_tool_arg_for_schema(value: Any, prop: dict[str, Any]) -> Any:
    expected_type = _json_schema_primary_type(prop)
    if expected_type is None:
        return value

    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed == "" and _json_schema_allows_null(prop) and expected_type != "string":
            return None
        if expected_type in {"array", "object"} and trimmed:
            try:
                parsed = json.loads(trimmed)
            except Exception:
                return value
            if expected_type == "array" and isinstance(parsed, list):
                return parsed
            if expected_type == "object" and isinstance(parsed, dict):
                return parsed

    return value


def coerce_tool_args(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}

    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return dict(args)

    prepared_args = dict(args)
    schema_json = _tool_schema_json(args_schema)
    props = schema_json.get("properties") if isinstance(schema_json, dict) else None
    if isinstance(props, dict):
        prepared_args = {
            key: _prepare_tool_arg_for_schema(value, props.get(key) or {})
            for key, value in prepared_args.items()
        }

    if hasattr(args_schema, "model_validate"):
        validated = args_schema.model_validate(prepared_args)
        if hasattr(validated, "model_dump"):
            return validated.model_dump(mode="python", exclude_unset=True)
        if hasattr(validated, "dict"):
            return validated.dict(exclude_unset=True)
        return prepared_args

    if hasattr(args_schema, "parse_obj"):
        validated = args_schema.parse_obj(prepared_args)
        if hasattr(validated, "dict"):
            return validated.dict(exclude_unset=True)
        return prepared_args

    return prepared_args


def wrap_tool_with_db(tool: Any, db_bind: Any) -> Callable:
    def wrapped(**args: Any) -> Any:
        call_args = coerce_tool_args(tool, args)
        session = sessionmaker(bind=db_bind)()
        token = set_current_db(session)
        try:
            tool_func = getattr(tool, "func", None)
            if callable(tool_func):
                return tool_func(**call_args)
            if callable(tool):
                return tool(**call_args)
            return tool.invoke(call_args)
        finally:
            session.close()
            reset_current_db(token)

    wrapped.__name__ = getattr(tool, "name", "tool")
    wrapped.__doc__ = getattr(tool, "description", "")
    return wrapped


def normalize_config(cfg: dict | None) -> dict:
    """Normalize camelCase config keys to snake_case for engine consumption."""
    if not cfg:
        return {}
    camel_re = re.compile(r"([a-z0-9])([A-Z])")
    out: dict = {}
    for key, value in cfg.items():
        snake = camel_re.sub(r"\1_\2", key).lower()
        out[snake] = value
    return out


def resolve_template_vars(
    template: str,
    step_outputs: dict[int, StepOutput],
    user_input: str,
    current_step: int | None = None,
) -> str:
    if not template:
        return ""

    last_step_no = max(step_outputs.keys()) if step_outputs else 0

    def _repl(match: re.Match) -> str:
        var = match.group(1)

        if var == "user_input":
            return truncate(user_input)
        if var == "last_step_result":
            if last_step_no and last_step_no in step_outputs:
                return truncate(step_outputs[last_step_no].get("text", ""))
            return ""
        if var == "last_step_result_raw":
            if last_step_no and last_step_no in step_outputs:
                raw = step_outputs[last_step_no].get("raw")
                return truncate(stringify(raw) if raw is not None else "")
            return ""

        matched = re.fullmatch(r"step_(\d+)_result", var)
        if matched:
            n = int(matched.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            return truncate(out.get("text", "")) if out else ""

        matched = re.fullmatch(r"step_(\d+)_result_raw", var)
        if matched:
            n = int(matched.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if out:
                raw = out.get("raw")
                return truncate(stringify(raw) if raw is not None else "")
            return ""

        matched = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var)
        if matched:
            n = int(matched.group(1))
            field = matched.group(2)
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if not out:
                return ""
            allowed = out.get("allowed_fields", [])
            if field not in allowed:
                raise ValueError(f"Disallowed template field: {var}")
            val = out.get("json_fields", {}).get(field, "")
            return truncate(stringify(val) if not isinstance(val, str) else val)

        logger.warning("Unknown template variable: %s", var)
        return ""

    return _TEMPLATE_VAR_RE.sub(_repl, template)


def resolve_json_template(
    template: str,
    step_outputs: dict[int, StepOutput],
    user_input: str,
    current_step: int | None = None,
    allowed_keys: set[str] | None = None,
) -> dict[str, Any]:
    if not (template or "").strip():
        return {}

    last_step_no = max(step_outputs.keys()) if step_outputs else 0

    def _repl(match: re.Match) -> str:
        var = match.group(1)

        if var == "user_input":
            return json.dumps(user_input, ensure_ascii=False)
        if var == "last_step_result":
            if last_step_no and last_step_no in step_outputs:
                return json.dumps(step_outputs[last_step_no].get("text", ""), ensure_ascii=False)
            return json.dumps("", ensure_ascii=False)
        if var == "last_step_result_raw":
            if last_step_no and last_step_no in step_outputs:
                raw = step_outputs[last_step_no].get("raw")
                if raw is not None:
                    return json.dumps(raw, ensure_ascii=False, default=str)
            return json.dumps("", ensure_ascii=False)

        matched = re.fullmatch(r"step_(\d+)_result", var)
        if matched:
            n = int(matched.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            return json.dumps(out.get("text", "") if out else "", ensure_ascii=False)

        matched = re.fullmatch(r"step_(\d+)_result_raw", var)
        if matched:
            n = int(matched.group(1))
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if out:
                raw = out.get("raw")
                if raw is not None:
                    return json.dumps(raw, ensure_ascii=False, default=str)
            return json.dumps("", ensure_ascii=False)

        matched = re.fullmatch(r"step_(\d+)_([a-zA-Z0-9_]+)", var)
        if matched:
            n = int(matched.group(1))
            field = matched.group(2)
            if current_step is not None and n >= current_step:
                raise ValueError(f"Template references future step: {var}")
            out = step_outputs.get(n)
            if not out:
                return json.dumps("", ensure_ascii=False)
            allowed = out.get("allowed_fields", [])
            if field not in allowed:
                raise ValueError(f"Disallowed template field: {var}")
            val = out.get("json_fields", {}).get(field, "")
            return json.dumps(val, ensure_ascii=False, default=str)

        logger.warning("Unknown json template variable: %s", var)
        return json.dumps("", ensure_ascii=False)

    rendered = _TEMPLATE_VAR_RE.sub(_repl, template)
    try:
        obj = json.loads(rendered)
        if not isinstance(obj, dict):
            return {}
        if allowed_keys is not None:
            return {k: v for k, v in obj.items() if k in allowed_keys}
        return obj
    except Exception as e:
        logger.warning("Failed to parse json template: %s", e)
        return {}


def cfg_bool_value(cfg: dict[str, Any], *keys: str, default: bool = False) -> bool:
    raw = default
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def cfg_int_value(
    cfg: dict[str, Any],
    *keys: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw: Any = default
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    try:
        val = int(raw)
    except Exception:
        val = default
    return max(min_value, min(max_value, val))


def cfg_string_list(cfg: dict[str, Any], *keys: str) -> list[str]:
    raw: Any = None
    for key in keys:
        if key in cfg:
            raw = cfg.get(key)
            break
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            result.append(text)
    return result


def cfg_list_value(cfg: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        if key in cfg and isinstance(cfg.get(key), list):
            return cfg.get(key) or []
    return []


def coerce_start_structured_field_value(field_name: str, field_type_raw: Any, value: Any) -> Any:
    field_type = str(field_type_raw or "string").strip().lower() or "string"
    if field_type == "string":
        if isinstance(value, str):
            return value
        raise ValueError(f"start structured field '{field_name}' must be string")
    if field_type == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(f"start structured field '{field_name}' must be number")
    if field_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)
        raise ValueError(f"start structured field '{field_name}' must be integer")
    if field_type == "boolean":
        if isinstance(value, bool):
            return value
        raise ValueError(f"start structured field '{field_name}' must be boolean")
    if field_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"start structured field '{field_name}' must be array")
        return list(value)
    raise ValueError(f"start structured field '{field_name}' has unsupported type: {field_type_raw}")


def resolve_start_input_mode(node_cfg: dict[str, Any]) -> str:
    raw_mode = str(node_cfg.get("input_mode", "text") or "text").strip().lower()
    return "structured" if raw_mode == "structured" else "text"


def resolve_start_memory_mode(node_cfg: dict[str, Any], *, default_mode: str = "auto") -> str:
    normalized_default = str(default_mode or "auto").strip().lower()
    if normalized_default not in START_MEMORY_MODES:
        normalized_default = "auto"
    raw_mode = str(
        node_cfg.get("memory_mode", node_cfg.get("memoryMode", normalized_default)) or normalized_default
    ).strip().lower()
    if raw_mode not in START_MEMORY_MODES:
        return normalized_default
    return raw_mode


def resolve_start_structured_fields(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_fields = node_cfg.get("structured_fields")
    if not isinstance(raw_fields, list):
        return []
    fields: list[dict[str, Any]] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("name", "") or "").strip()
        if not field_name:
            continue
        fields.append(item)
    return fields


def resolve_start_env_specs(node_cfg: dict[str, Any]) -> list[WorkflowEnvVarSpec]:
    raw_session_vars = node_cfg.get("session_vars", node_cfg.get("sessionVars"))
    specs, errors = parse_env_var_specs(raw_session_vars)
    if errors:
        raise RuntimeError(f"start node sessionVars invalid: {'; '.join(errors)}")
    return specs


def resolve_structured_memory_fields(memory_context: dict[str, Any] | None) -> dict[str, str]:
    ctx = memory_context if isinstance(memory_context, dict) else {}
    resolved: dict[str, str] = {}
    for field_name, context_key in START_MEMORY_STRUCTURED_FIELD_TO_CONTEXT_KEY.items():
        resolved[field_name] = str(ctx.get(context_key, "") or "").strip()
    return resolved


def render_memory_injection_block(
    *,
    memory_context: dict[str, Any] | None,
    max_chars: int,
    locale: str | None = None,
) -> str:
    sections = resolve_structured_memory_fields(memory_context)
    from app.assistant.workflow.execution_copy import build_memory_injection_block

    return build_memory_injection_block(
        locale=locale,
        conversation_summary=sections.get("memory_conversation_summary", ""),
        skill_facts=sections.get("memory_skill_facts", ""),
        max_chars=max_chars,
    )


def resolve_node_template_vars(
    template: str,
    node_outputs: dict[str, NodeOutput],
    start_inputs: dict[str, Any],
    sys_vars: dict[str, str] | None = None,
    container_fields: dict[str, Any] | None = None,
    env_vars: dict[str, Any] | None = None,
) -> str:
    if not template:
        return ""
    sys_ctx = sys_vars or {}
    container_ctx = container_fields or {}
    env_ctx = env_vars or {}

    def _repl(match: re.Match) -> str:
        node_id = match.group(1)
        field = match.group(2)

        if node_id == "start":
            return truncate(start_inputs.get(field, ""))
        if node_id == "sys":
            return truncate(sys_ctx.get(field, ""))
        if node_id == "container":
            if field in container_ctx:
                return truncate(container_ctx.get(field, ""))
            container_out = node_outputs.get("container", {})
            container_json = container_out.get("json_fields", {}) if isinstance(container_out, dict) else {}
            return truncate(container_json.get(field, ""))
        if node_id == "env":
            if field not in env_ctx:
                logger.warning("Template references unknown env variable: env.%s", field)
                return ""
            value = env_ctx.get(field)
            return truncate(stringify(value) if not isinstance(value, str) else value)

        out = node_outputs.get(node_id)
        if not out:
            logger.warning("Template references node with no output: %s.%s", node_id, field)
            return ""

        if field == "text":
            return truncate(out.get("text", ""))
        if field == "raw":
            raw = out.get("raw")
            return truncate(stringify(raw) if raw is not None else "")

        json_fields = out.get("json_fields", {})
        if field in json_fields:
            val = json_fields[field]
            return truncate(stringify(val) if not isinstance(val, str) else val)

        return truncate(out.get("text", ""))

    return _NODE_VAR_RE.sub(_repl, template)


def normalize_if_else_operator(raw: Any) -> str:
    op = str(raw or "is").strip().lower()
    if not op:
        return "is"
    return _IF_ELSE_LEGACY_OPERATOR_MAP.get(op, op)


def _default_if_else_condition(idx: int = 1) -> dict[str, Any]:
    return {
        "id": f"cond_{idx}",
        "variable": "",
        "operator": "is",
        "value": "",
    }


def normalize_if_else_config(node_cfg: dict[str, Any]) -> dict[str, Any]:
    else_handle = str(node_cfg.get("else_handle") or "else").strip() or "else"
    if not _IF_ELSE_HANDLE_RE.fullmatch(else_handle):
        else_handle = "else"

    branches_raw = node_cfg.get("branches")
    normalized_branches: list[dict[str, Any]] = []

    if isinstance(branches_raw, list) and branches_raw:
        for branch_idx, branch in enumerate(branches_raw, start=1):
            if not isinstance(branch, dict):
                continue
            branch_id = str(branch.get("id") or f"if_{branch_idx}").strip()
            if not _IF_ELSE_HANDLE_RE.fullmatch(branch_id):
                branch_id = f"if_{branch_idx}"
            logic = str(branch.get("logic") or "and").strip().lower()
            if logic not in {"and", "or"}:
                logic = "and"
            label = str(branch.get("label") or ("IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}")).strip()
            if not label:
                label = "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}"

            conds_raw = branch.get("conditions")
            conds: list[dict[str, Any]] = []
            if isinstance(conds_raw, list):
                for cond_idx, cond in enumerate(conds_raw, start=1):
                    if not isinstance(cond, dict):
                        continue
                    cond_id = str(cond.get("id") or f"{branch_id}_cond_{cond_idx}").strip() or f"{branch_id}_cond_{cond_idx}"
                    conds.append({
                        "id": cond_id,
                        "variable": str(cond.get("variable") or "").strip(),
                        "operator": normalize_if_else_operator(cond.get("operator")),
                        "value": None if cond.get("value") is None else str(cond.get("value")),
                    })
            if not conds:
                conds = [_default_if_else_condition()]
            normalized_branches.append(
                {"id": branch_id, "label": label, "logic": logic, "conditions": conds}
            )

    if not normalized_branches:
        legacy_conds = node_cfg.get("conditions")
        handle_order: list[str] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        if isinstance(legacy_conds, list):
            for cond_idx, cond in enumerate(legacy_conds, start=1):
                if not isinstance(cond, dict):
                    continue
                handle = str(cond.get("handle") or "").strip()
                if not handle:
                    continue
                if handle in {"default", "else"}:
                    continue
                if not _IF_ELSE_HANDLE_RE.fullmatch(handle):
                    continue
                if handle not in grouped:
                    grouped[handle] = []
                    handle_order.append(handle)
                grouped[handle].append({
                    "id": str(cond.get("id") or f"{handle}_cond_{cond_idx}").strip() or f"{handle}_cond_{cond_idx}",
                    "variable": str(cond.get("variable") or "").strip(),
                    "operator": normalize_if_else_operator(cond.get("operator")),
                    "value": None if cond.get("value") is None else str(cond.get("value")),
                })
        for branch_idx, handle in enumerate(handle_order, start=1):
            normalized_branches.append({
                "id": handle,
                "label": "IF" if branch_idx == 1 else f"ELIF {branch_idx - 1}",
                "logic": "and",
                "conditions": grouped.get(handle) or [_default_if_else_condition()],
            })

    return {"branches": normalized_branches, "else_handle": else_handle}


def _extract_output_param_names(output_params: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(output_params, list):
        return names
    for item in output_params:
        name = ""
        if isinstance(item, dict):
            name = str(item.get("name", "") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
        if name:
            names.append(name)
    return names


@lru_cache(maxsize=1)
def _get_system_tool_output_param_map() -> dict[str, list[str]]:
    try:
        from app.assistant_config.registry import ToolRegistry

        mapping: dict[str, list[str]] = {}
        for definition in ToolRegistry.list_system_tool_definitions():
            name = getattr(definition, "name", "")
            if not name:
                continue
            mapping[name] = _extract_output_param_names(
                getattr(definition, "output_params", None)
            )
        return mapping
    except Exception as e:
        logger.debug("Failed to load system tool output param map: %s", e)
        return {}


def resolve_tool_output_param_names(tool_name: str, tool: Any) -> list[str]:
    from_tool = _extract_output_param_names(getattr(tool, "output_params", None))
    if from_tool:
        return from_tool
    return _get_system_tool_output_param_map().get(tool_name, [])


def normalize_human_in_loop_fields(node_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_fields = node_cfg.get("fields")
    if not isinstance(raw_fields, list):
        return []
    fields: list[dict[str, Any]] = []
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("name", "") or "").strip()
        if not field_name:
            continue
        field_type = normalize_human_field_type(item.get("type", "string"))
        widget = normalize_human_field_widget(field_type, item.get("widget"))
        options = normalize_human_field_options(item.get("options"))
        if widget in {"select", "radio"} and not options:
            options = []
        raw_allow_custom = item.get("allow_custom", item.get("allowCustom", None))
        allow_custom = bool(raw_allow_custom) if isinstance(raw_allow_custom, bool) else True
        if widget != "tag_selector":
            allow_custom = False
        options_template = str(item.get("options_template", item.get("optionsTemplate", "")) or "")
        option_value_key = str(item.get("option_value_key", item.get("optionValueKey", "")) or "").strip()
        fields.append(
            {
                "name": field_name,
                "label": str(item.get("label", "") or ""),
                "type": field_type,
                "widget": widget,
                "options": options,
                "options_template": options_template,
                "option_value_key": option_value_key,
                "allow_custom": allow_custom,
                "placeholder": str(item.get("placeholder", "") or ""),
                "required": bool(item.get("required", False)),
                "value_template": str(item.get("value_template", item.get("valueTemplate", "")) or ""),
            }
        )
    return fields


def parse_human_field_options_from_rendered(
    *,
    rendered: str,
    field_name: str,
    option_value_key: str = "",
) -> list[str]:
    parsed = parse_loose_json_value(rendered)
    candidate_items: list[Any]
    if isinstance(parsed, list):
        candidate_items = parsed
    elif isinstance(parsed, dict):
        nested = None
        for key in ("result", "items", "data", "list"):
            value = parsed.get(key)
            if isinstance(value, list):
                nested = value
                break
        candidate_items = nested if isinstance(nested, list) else []
    elif isinstance(parsed, str):
        text = parsed.strip()
        if not text:
            candidate_items = []
        else:
            candidate_items = [segment.strip() for segment in re.split(r"[,;\n，；]+", text)]
    else:
        candidate_items = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidate_items:
        value_text = ""
        if isinstance(item, str):
            value_text = item.strip()
        elif isinstance(item, (int, float, bool)):
            value_text = str(item).strip()
        elif isinstance(item, dict):
            if option_value_key:
                raw = item.get(option_value_key)
                if raw is not None:
                    value_text = str(raw).strip()
            if not value_text:
                for key in ("value", "code", "name", "label", "id", "type"):
                    raw = item.get(key)
                    if raw is None:
                        continue
                    candidate = str(raw).strip()
                    if candidate:
                        value_text = candidate
                        break
        if not value_text or value_text in seen:
            continue
        seen.add(value_text)
        normalized.append(value_text)

    if rendered.strip() and not normalized:
        logger.warning(
            "human_in_loop field '%s' failed to parse options from template result",
            field_name,
        )
    return normalized


def coerce_human_field_value(field_name: str, field_type: str, value: Any) -> Any:
    return coerce_human_field_value_by_type(
        field_name=field_name,
        field_type=field_type,
        value=value,
        error_cls=RuntimeError,
        subject="human_in_loop field",
    )


def coerce_array_input(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return [text]
    return [value]


def parse_loose_json_value(value: str) -> Any:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("[") or text.startswith('"'):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def eval_condition(actual: str, operator: str, value: str) -> bool:
    op = normalize_if_else_operator(operator)
    actual_str = "" if actual is None else str(actual)
    value_str = "" if value is None else str(value)
    actual_ci = actual_str.casefold()
    value_ci = value_str.casefold()

    if op == "is":
        return actual_ci == value_ci
    if op == "is_not":
        return actual_ci != value_ci
    if op == "contains":
        return value_ci in actual_ci
    if op == "not_contains":
        return value_ci not in actual_ci
    if op == "starts_with":
        return actual_ci.startswith(value_ci)
    if op == "ends_with":
        return actual_ci.endswith(value_ci)
    if op == "is_empty":
        return not actual_str.strip()
    if op == "is_not_empty":
        return bool(actual_str.strip())
    try:
        a, v = float(actual_str), float(value_str)
        if op == "gt":
            return a > v
        if op == "lt":
            return a < v
        if op == "gte":
            return a >= v
        if op == "lte":
            return a <= v
    except (ValueError, TypeError):
        pass
    return False


def get_start_inputs(node_outputs: dict[str, NodeOutput]) -> dict[str, Any]:
    start_out = node_outputs.get("start")
    if start_out:
        return start_out.get("json_fields", {})
    return {}
