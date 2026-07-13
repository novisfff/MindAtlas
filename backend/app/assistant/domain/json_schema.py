from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator

from app.assistant.domain.contracts import ToolParamContract
from app.assistant.domain.digests import JsonValue, canonical_json_bytes, sha256_canonical_json

MAX_BINDING_SCHEMA_BYTES = 256 * 1024
MAX_BINDING_SCHEMA_DEPTH = 64
MAX_ENUM_VALUES = 256
MAX_ENUM_STRING_BYTES = 4096
MAX_ENUM_CANONICAL_BYTES = 64 * 1024

_SECRET_PROPERTY_TOKENS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)

_PRESERVED_ANNOTATIONS = frozenset(
    {
        "title",
        "description",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
    }
)

_REJECTED_ANNOTATIONS = frozenset(
    {
        "default",
        "example",
        "examples",
    }
)

_TYPE_ORDER = {
    "null": 0,
    "boolean": 1,
    "integer": 2,
    "number": 3,
    "string": 4,
    "array": 5,
    "object": 6,
}


def _is_secret_property_name(name: str) -> bool:
    compact = "".join(ch for ch in name.lower() if ch.isalnum() or ch == "_")
    # Normalize separators so api-key / apiKey / api_key all match.
    normalized = compact.replace("_", "")
    tokens = {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
    if normalized in tokens:
        return True
    # Also match exact underscore forms from the locked token list.
    return name.lower() in _SECRET_PROPERTY_TOKENS or compact in _SECRET_PROPERTY_TOKENS


def _json_copy(value: Any, *, path: str) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        # digests rejects non-finite floats via canonicalization later; fail early.
        from math import isinf, isnan

        if isnan(value) or isinf(value):
            raise ValueError(f"NaN/Infinity are not valid JSON Schema values at {path}")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"schema mapping keys must be strings at {path}")
            out[key] = _json_copy(item, path=f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_copy(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"non-JSON value {type(value)!r} at {path}")


def _depth_of(value: JsonValue, depth: int = 1) -> int:
    if isinstance(value, dict):
        if not value:
            return depth
        return max(_depth_of(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        if not value:
            return depth
        return max(_depth_of(item, depth + 1) for item in value)
    return depth


def _sorted_unique_strings(values: Sequence[Any], *, path: str) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{path} must be an array of strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise ValueError(f"{path} members must be strings")
        if item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    cleaned.sort()
    return cleaned


def _normalize_type(value: Any, *, path: str) -> str | list[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        members: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{path} type union members must be strings")
            if item in seen:
                continue
            seen.add(item)
            members.append(item)
        members.sort(key=lambda item: (_TYPE_ORDER.get(item, 99), item))
        return members
    raise ValueError(f"{path} type must be a string or array of strings")


def _check_enum_bounds(values: Sequence[Any], *, path: str) -> None:
    if len(values) > MAX_ENUM_VALUES:
        raise ValueError(f"enum at {path} exceeds {MAX_ENUM_VALUES} values")
    for item in values:
        if isinstance(item, str) and len(item.encode("utf-8")) > MAX_ENUM_STRING_BYTES:
            raise ValueError(f"enum string at {path} exceeds {MAX_ENUM_STRING_BYTES} UTF-8 bytes")
    enum_bytes = canonical_json_bytes(list(values))
    if len(enum_bytes) > MAX_ENUM_CANONICAL_BYTES:
        raise ValueError(f"enum at {path} exceeds {MAX_ENUM_CANONICAL_BYTES} canonical bytes")


def _normalize_schema_node(
    node: Any,
    *,
    path: str,
    property_name: str | None,
) -> JsonValue:
    if not isinstance(node, Mapping):
        # boolean schemas are valid Draft 2020-12, but MindAtlas binding Schemas
        # require object documents for digest stability.
        raise ValueError(f"schema node at {path} must be an object")

    raw = dict(node)
    out: dict[str, JsonValue] = {}

    nullable = raw.pop("nullable", None)
    if nullable is not None:
        if nullable is not True:
            raise ValueError(f"ambiguous nullable at {path}")
        if "type" not in raw:
            raise ValueError(f"ambiguous nullable without concrete type at {path}")

    for key, value in raw.items():
        if key in _REJECTED_ANNOTATIONS:
            raise ValueError(f"rejected annotation {key!r} at {path}")
        if key.startswith("x-"):
            raise ValueError(f"unapproved extension annotation {key!r} at {path}")
        if key == "$ref":
            if not isinstance(value, str):
                raise ValueError(f"$ref at {path} must be a string")
            if not value.startswith("#/"):
                raise ValueError(f"remote or absolute $ref is forbidden at {path}: {value}")
            out[key] = value
            continue
        if key == "type":
            out[key] = _normalize_type(value, path=f"{path}.type")
            continue
        if key == "required":
            out[key] = _sorted_unique_strings(value, path=f"{path}.required")
            continue
        if key in {"enum"}:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                raise ValueError(f"enum at {path} must be an array")
            if property_name is not None and _is_secret_property_name(property_name):
                raise ValueError(f"secret-bearing enum is forbidden at {path}")
            enum_values = [_json_copy(item, path=f"{path}.enum[{index}]") for index, item in enumerate(value)]
            _check_enum_bounds(enum_values, path=f"{path}.enum")
            out[key] = enum_values  # preserve order
            continue
        if key == "const":
            if property_name is not None and _is_secret_property_name(property_name):
                raise ValueError(f"secret-bearing const is forbidden at {path}")
            out[key] = _json_copy(value, path=f"{path}.const")
            continue
        if key in {"properties", "patternProperties", "$defs", "dependentSchemas"}:
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}.{key} must be an object")
            nested: dict[str, JsonValue] = {}
            for child_key in sorted(value.keys()):
                if not isinstance(child_key, str):
                    raise TypeError(f"non-string key in {path}.{key}")
                child_property = child_key if key == "properties" else property_name
                nested[child_key] = _normalize_schema_node(
                    value[child_key],
                    path=f"{path}.{key}.{child_key}",
                    property_name=child_property,
                )
            out[key] = nested
            continue
        if key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                raise ValueError(f"{path}.{key} must be an array")
            out[key] = [
                _normalize_schema_node(item, path=f"{path}.{key}[{index}]", property_name=property_name)
                for index, item in enumerate(value)
            ]
            continue
        if key in {"items", "contains", "not", "if", "then", "else", "propertyNames", "additionalProperties", "unevaluatedProperties", "unevaluatedItems"}:
            if isinstance(value, Mapping):
                out[key] = _normalize_schema_node(value, path=f"{path}.{key}", property_name=property_name)
            elif isinstance(value, bool) and key in {"additionalProperties", "unevaluatedProperties", "unevaluatedItems"}:
                out[key] = value
            else:
                out[key] = _json_copy(value, path=f"{path}.{key}")
            continue
        if key in _PRESERVED_ANNOTATIONS:
            out[key] = _json_copy(value, path=f"{path}.{key}")
            continue
        # Other Draft keywords (minLength, maximum, pattern, etc.) are preserved
        # as bounded JSON after deep-copy validation.
        out[key] = _json_copy(value, path=f"{path}.{key}")

    if nullable is True:
        type_value = out.get("type")
        if isinstance(type_value, str):
            members = [type_value]
        elif isinstance(type_value, list):
            members = list(type_value)
        else:
            raise ValueError(f"ambiguous nullable without concrete type at {path}")
        if "null" not in members:
            members.append("null")
        out["type"] = _normalize_type(members, path=f"{path}.type")

    return out


def _collect_local_refs(node: JsonValue, refs: list[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for value in node.values():
            _collect_local_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _collect_local_refs(item, refs)


def _resolve_local_pointer(document: dict[str, JsonValue], pointer: str) -> bool:
    if not pointer.startswith("#/"):
        return False
    parts = pointer[2:].split("/")
    cursor: JsonValue = document
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(cursor, dict) or part not in cursor:
            return False
        cursor = cursor[part]
    return True


def normalize_binding_schema(
    schema: Mapping[str, JsonValue],
    *,
    require_object_root: bool,
) -> dict[str, JsonValue]:
    if not isinstance(schema, Mapping):
        raise TypeError("schema must be a mapping")
    copied = _json_copy(schema, path="$")
    if not isinstance(copied, dict):
        raise ValueError("schema root must be an object")

    normalized = _normalize_schema_node(copied, path="$", property_name=None)
    if not isinstance(normalized, dict):
        raise ValueError("normalized schema root must be an object")

    if require_object_root:
        root_type = normalized.get("type")
        if root_type != "object" and not (
            isinstance(root_type, list) and root_type == ["object"]
        ):
            raise ValueError("input binding schema root must have type object")

    depth = _depth_of(normalized)
    if depth > MAX_BINDING_SCHEMA_DEPTH:
        raise ValueError(f"schema depth {depth} exceeds limit {MAX_BINDING_SCHEMA_DEPTH}")

    raw_bytes = canonical_json_bytes(normalized)
    if len(raw_bytes) > MAX_BINDING_SCHEMA_BYTES:
        raise ValueError(
            f"schema size {len(raw_bytes)} exceeds byte limit {MAX_BINDING_SCHEMA_BYTES}"
        )

    refs: list[str] = []
    _collect_local_refs(normalized, refs)
    for ref in refs:
        if not ref.startswith("#/"):
            raise ValueError(f"remote or absolute $ref is forbidden: {ref}")
        if not _resolve_local_pointer(normalized, ref):
            raise ValueError(f"missing local $ref target: {ref}")

    try:
        Draft202012Validator.check_schema(normalized)
    except SchemaError as exc:
        raise ValueError(f"invalid Draft 2020-12 schema: {exc.message}") from exc

    return normalized


def binding_schema_digest(schema: Mapping[str, JsonValue]) -> str:
    normalized = normalize_binding_schema(schema, require_object_root=False)
    # If caller already normalized an object-root input schema, reusing require_object_root=False
    # keeps digest shared between input/output paths over the same body.
    if schema.get("type") == "object" or (
        isinstance(schema.get("type"), list) and schema.get("type") == ["object"]
    ):
        # Prefer object-root validation when the document claims object root so publish/runtime
        # digests stay aligned with input contracts.
        normalized = normalize_binding_schema(schema, require_object_root=True)
    return sha256_canonical_json(normalized)


def _param_type_schema(
    param_type: str,
    *,
    items_type: str | None,
    description: str | None,
) -> dict[str, JsonValue]:
    supported = {"string", "number", "boolean", "array", "object"}
    if param_type not in supported:
        raise ValueError(f"unknown or lossy param_type: {param_type!r}")
    node: dict[str, JsonValue] = {"type": param_type}
    if description:
        node["description"] = description
    if param_type == "array":
        if items_type is None:
            # Registry currently projects bare arrays; treat as free JSON items only when
            # items_type is omitted, but still fail nested arrays explicitly.
            node["items"] = {}
        else:
            if items_type not in {"string", "number", "boolean", "object"}:
                raise ValueError(f"unknown or lossy array items_type: {items_type!r}")
            node["items"] = {"type": items_type}
    elif items_type is not None:
        raise ValueError("items_type is only valid for array params")
    return node


def tool_params_to_binding_schema(
    params: Sequence[ToolParamContract],
    *,
    require_object_root: bool = True,
) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {}
    required: list[str] = []
    for param in params:
        if param.name in properties:
            raise ValueError(f"duplicate parameter name: {param.name!r}")
        properties[param.name] = _param_type_schema(
            param.param_type,
            items_type=param.items_type,
            description=param.description,
        )
        if param.required:
            required.append(param.name)

    # Deterministic property order by name.
    ordered_properties = {key: properties[key] for key in sorted(properties)}
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": ordered_properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = sorted(set(required))
    return normalize_binding_schema(schema, require_object_root=require_object_root)


def system_tool_contract_set_digest(
    ordered: Sequence[tuple[str, str, str]],
) -> str:
    payload: list[dict[str, JsonValue]] = [
        {
            "name": name,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
        }
        for name, input_digest, output_digest in ordered
    ]
    return sha256_canonical_json(payload)


__all__ = [
    "MAX_BINDING_SCHEMA_BYTES",
    "MAX_BINDING_SCHEMA_DEPTH",
    "MAX_ENUM_CANONICAL_BYTES",
    "MAX_ENUM_STRING_BYTES",
    "MAX_ENUM_VALUES",
    "ToolParamContract",
    "binding_schema_digest",
    "normalize_binding_schema",
    "system_tool_contract_set_digest",
    "tool_params_to_binding_schema",
]
