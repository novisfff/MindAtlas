from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Literal


EnvVarType = Literal["string", "number", "integer", "boolean", "object", "array"]
EnvVarOperation = Literal["set", "increment", "append", "clear"]

_ENV_VAR_TYPES: set[str] = {"string", "number", "integer", "boolean", "object", "array"}
_ENV_VAR_OPERATIONS: set[str] = {"set", "increment", "append", "clear"}
_ENV_VAR_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_MISSING = object()


@dataclass(frozen=True)
class WorkflowEnvVarSpec:
    name: str
    type: EnvVarType
    default_value: Any
    description: str = ""



def default_env_var_value(var_type: str) -> Any:
    normalized = str(var_type or "string").strip().lower() or "string"
    if normalized == "string":
        return ""
    if normalized == "number":
        return 0.0
    if normalized == "integer":
        return 0
    if normalized == "boolean":
        return False
    if normalized == "object":
        return {}
    if normalized == "array":
        return []
    raise ValueError(f"Unsupported env var type: {var_type}")



def _coerce_number(value: Any, *, var_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"env var '{var_name}' expects number, got boolean")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"env var '{var_name}' expects number, got empty string")
        try:
            return float(text)
        except Exception as exc:
            raise ValueError(f"env var '{var_name}' expects number, got: {value}") from exc
    raise ValueError(f"env var '{var_name}' expects number, got: {type(value).__name__}")



def _coerce_integer(value: Any, *, var_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"env var '{var_name}' expects integer, got boolean")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"env var '{var_name}' expects integer, got non-integer number: {value}")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"env var '{var_name}' expects integer, got empty string")
        if not re.fullmatch(r"[-+]?\d+", text):
            raise ValueError(f"env var '{var_name}' expects integer, got: {value}")
        try:
            return int(text)
        except Exception as exc:
            raise ValueError(f"env var '{var_name}' expects integer, got: {value}") from exc
    raise ValueError(f"env var '{var_name}' expects integer, got: {type(value).__name__}")



def _coerce_boolean(value: Any, *, var_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    raise ValueError(f"env var '{var_name}' expects boolean, got: {value}")



def _coerce_object(value: Any, *, var_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"env var '{var_name}' expects object JSON, got empty string")
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise ValueError(f"env var '{var_name}' expects object JSON, got: {value}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"env var '{var_name}' expects object, got: {type(value).__name__}")



def _coerce_array(value: Any, *, var_name: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"env var '{var_name}' expects array JSON, got empty string")
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise ValueError(f"env var '{var_name}' expects array JSON, got: {value}") from exc
        if isinstance(parsed, list):
            return parsed
    raise ValueError(f"env var '{var_name}' expects array, got: {type(value).__name__}")



def coerce_env_var_value(
    value: Any,
    var_type: str,
    *,
    var_name: str,
    for_default: bool = False,
) -> Any:
    normalized = str(var_type or "string").strip().lower() or "string"
    if normalized not in _ENV_VAR_TYPES:
        raise ValueError(f"env var '{var_name}' has unsupported type: {var_type}")

    if value is _MISSING or value is None:
        if for_default:
            return default_env_var_value(normalized)
        raise ValueError(f"env var '{var_name}' value is required")

    if normalized == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    if normalized == "number":
        return _coerce_number(value, var_name=var_name)
    if normalized == "integer":
        return _coerce_integer(value, var_name=var_name)
    if normalized == "boolean":
        return _coerce_boolean(value, var_name=var_name)
    if normalized == "object":
        return _coerce_object(value, var_name=var_name)
    if normalized == "array":
        return _coerce_array(value, var_name=var_name)

    raise ValueError(f"env var '{var_name}' has unsupported type: {var_type}")



def normalize_env_operation(raw: Any) -> EnvVarOperation:
    op = str(raw or "set").strip().lower() or "set"
    if op not in _ENV_VAR_OPERATIONS:
        raise ValueError(f"Unsupported env operation: {raw}")
    return op  # type: ignore[return-value]



def parse_env_var_specs(raw: Any) -> tuple[list[WorkflowEnvVarSpec], list[str]]:
    if raw is None:
        return ([], [])
    if not isinstance(raw, list):
        return ([], ["start sessionVars must be a list"])

    specs: list[WorkflowEnvVarSpec] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            errors.append(f"start sessionVars item #{idx} must be an object")
            continue

        name = str(item.get("name", "") or "").strip()
        if not name:
            errors.append(f"start sessionVars item #{idx} requires name")
            continue
        if not _ENV_VAR_NAME_RE.fullmatch(name):
            errors.append(f"start sessionVars variable name is invalid: {name}")
            continue
        if name in seen_names:
            errors.append(f"start sessionVars variable duplicated: {name}")
            continue

        var_type_raw = item.get("type", "string")
        var_type = str(var_type_raw or "string").strip().lower() or "string"
        if var_type not in _ENV_VAR_TYPES:
            errors.append(f"start sessionVars variable '{name}' has invalid type: {var_type_raw}")
            continue

        raw_default = item.get("default_value", item.get("defaultValue", _MISSING))
        try:
            default_value = coerce_env_var_value(
                raw_default,
                var_type,
                var_name=name,
                for_default=True,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue

        description = str(item.get("description", "") or "")
        specs.append(
            WorkflowEnvVarSpec(
                name=name,
                type=var_type,  # type: ignore[arg-type]
                default_value=default_value,
                description=description,
            )
        )
        seen_names.add(name)

    return (specs, errors)



def build_env_spec_map(specs: list[WorkflowEnvVarSpec]) -> dict[str, WorkflowEnvVarSpec]:
    return {spec.name: spec for spec in specs}



def build_initial_env_vars(specs: list[WorkflowEnvVarSpec]) -> dict[str, Any]:
    return {
        spec.name: copy.deepcopy(spec.default_value)
        for spec in specs
    }



def _stringify_append_operand(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)



def apply_env_var_operation(
    env_vars: dict[str, Any],
    env_specs: dict[str, WorkflowEnvVarSpec],
    *,
    variable_name: str,
    operation: str,
    operand: Any,
) -> tuple[dict[str, Any], Any, Any]:
    name = str(variable_name or "").strip()
    if not name:
        raise ValueError("variable_assign variableName is required")
    if name not in env_specs:
        raise ValueError(f"variable_assign variable not found: {name}")

    spec = env_specs[name]
    op = normalize_env_operation(operation)

    current_value = env_vars.get(name, copy.deepcopy(spec.default_value))
    before_value = copy.deepcopy(current_value)

    if op == "set":
        after_value = coerce_env_var_value(operand, spec.type, var_name=name)
    elif op == "clear":
        after_value = default_env_var_value(spec.type)
    elif op == "increment":
        if spec.type not in {"number", "integer"}:
            raise ValueError(
                f"variable_assign increment requires numeric variable type, got: {spec.type}"
            )
        if spec.type == "integer":
            base = _coerce_integer(current_value, var_name=name)
            delta = _coerce_integer(operand, var_name=name)
            after_value = base + delta
        else:
            base = _coerce_number(current_value, var_name=name)
            delta = _coerce_number(operand, var_name=name)
            after_value = base + delta
    elif op == "append":
        if spec.type == "string":
            base = coerce_env_var_value(current_value, "string", var_name=name)
            after_value = f"{base}{_stringify_append_operand(operand)}"
        elif spec.type == "array":
            base_array = copy.deepcopy(_coerce_array(current_value, var_name=name))
            if isinstance(operand, list):
                base_array.extend(operand)
            else:
                base_array.append(operand)
            after_value = base_array
        else:
            raise ValueError(
                f"variable_assign append supports only string/array variables, got: {spec.type}"
            )
    else:  # pragma: no cover
        raise ValueError(f"Unsupported env operation: {op}")

    updated_env = dict(env_vars)
    updated_env[name] = after_value
    return (updated_env, before_value, copy.deepcopy(after_value))



def serialize_env_specs(specs: list[WorkflowEnvVarSpec]) -> dict[str, dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for spec in specs:
        payload[spec.name] = {
            "name": spec.name,
            "type": spec.type,
            "defaultValue": copy.deepcopy(spec.default_value),
            "description": spec.description,
        }
    return payload
