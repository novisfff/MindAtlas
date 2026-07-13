"""Runtime JSON Schema compilation and value validation (Plan 02).

Imports Plan 01 ``normalize_binding_schema`` / ``binding_schema_digest`` without
wrapping or renaming. Adds only compile + validate APIs plus a bounded
thread-safe LRU of compiled validators. Never stores request values in cache.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator

from app.assistant.capabilities.contracts import CapabilityValidationIssue
from app.assistant.capabilities.errors import CapabilitySchemaValidationError
from app.assistant.domain.digests import JsonValue
from app.assistant.domain.json_schema import (
    binding_schema_digest,
    normalize_binding_schema,
)

MAX_VALIDATION_ISSUES = 20
_COMPILER_CACHE_SIZE = 128

# Generic keyword -> safe message. Never include rejected values, enum members,
# pattern text, defaults, examples, or schema descriptions.
_KEYWORD_SAFE_MESSAGES: dict[str, str] = {
    "type": "value has an incorrect type",
    "required": "required property is missing",
    "additionalProperties": "unexpected property is present",
    "properties": "object properties are invalid",
    "items": "array item is invalid",
    "minItems": "array has too few items",
    "maxItems": "array has too many items",
    "minLength": "string is too short",
    "maxLength": "string is too long",
    "minimum": "number is below the minimum",
    "maximum": "number is above the maximum",
    "exclusiveMinimum": "number is below the exclusive minimum",
    "exclusiveMaximum": "number is above the exclusive maximum",
    "multipleOf": "number is not a valid multiple",
    "pattern": "string does not match the required pattern",
    "enum": "value is not one of the allowed values",
    "const": "value does not match the required constant",
    "anyOf": "value does not match any allowed schema",
    "oneOf": "value does not match exactly one allowed schema",
    "allOf": "value does not match all required schemas",
    "not": "value matches a disallowed schema",
    "prefixItems": "array prefix item is invalid",
    "contains": "array does not contain a required item",
    "minProperties": "object has too few properties",
    "maxProperties": "object has too many properties",
    "dependentRequired": "dependent required property is missing",
    "dependentSchemas": "dependent schema validation failed",
    "if": "conditional schema validation failed",
    "then": "then-schema validation failed",
    "else": "else-schema validation failed",
    "propertyNames": "property name is invalid",
    "unevaluatedProperties": "unevaluated property is present",
    "unevaluatedItems": "unevaluated array item is present",
}


def _json_pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def _absolute_pointer(parts: list[str | int]) -> str:
    if not parts:
        return "/"
    return "/" + "/".join(
        _json_pointer_escape(str(part)) if not isinstance(part, int) else str(part)
        for part in parts
    )


def _safe_message_for_keyword(keyword: str) -> str:
    return _KEYWORD_SAFE_MESSAGES.get(keyword, "value failed schema validation")


@dataclass(frozen=True)
class CompiledBindingSchema:
    digest: str
    require_object_root: bool
    normalized_body: dict[str, JsonValue]
    _validator: Draft202012Validator = field(repr=False, compare=False)


class _CompilerCache:
    """Bounded thread-safe LRU keyed by (expected_digest, require_object_root)."""

    def __init__(self, maxsize: int = _COMPILER_CACHE_SIZE) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._data: OrderedDict[tuple[str, bool], CompiledBindingSchema] = OrderedDict()

    def get(self, key: tuple[str, bool]) -> CompiledBindingSchema | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            self._data.move_to_end(key)
            return item

    def put(self, key: tuple[str, bool], value: CompiledBindingSchema) -> CompiledBindingSchema:
        with self._lock:
            existing = self._data.get(key)
            if existing is not None:
                self._data.move_to_end(key)
                return existing
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
            return value


_COMPILER_CACHE = _CompilerCache()


def compile_binding_schema(
    normalized_body: Mapping[str, JsonValue],
    *,
    expected_digest: str,
    require_object_root: bool,
) -> CompiledBindingSchema:
    """Compile a Plan 01-normalized binding schema for value validation.

    Verifies ``binding_schema_digest(normalized_body) == expected_digest``,
    validates the Draft 2020-12 document, and applies the requested root check.
    """
    if not isinstance(normalized_body, Mapping):
        raise TypeError("normalized_body must be a mapping")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("expected_digest must be a lowercase 64-character SHA-256 hex digest")

    cache_key = (expected_digest, require_object_root)
    cached = _COMPILER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # Re-normalize through Plan 01 helper so runtime bytes/digests stay identical
    # to publish-time. This is not a second dialect: it is the same function.
    body = normalize_binding_schema(normalized_body, require_object_root=require_object_root)
    actual_digest = binding_schema_digest(body)
    if actual_digest != expected_digest:
        raise ValueError("binding schema digest mismatch")

    try:
        Draft202012Validator.check_schema(body)
    except SchemaError as exc:
        # Do not leak schema contents from the exception message.
        raise ValueError("invalid Draft 2020-12 schema") from None

    # format is annotation-only: no format_checker, so OpenClaw date strings stay accepted.
    validator = Draft202012Validator(body)
    compiled = CompiledBindingSchema(
        digest=actual_digest,
        require_object_root=require_object_root,
        normalized_body=body,
        _validator=validator,
    )
    return _COMPILER_CACHE.put(cache_key, compiled)


def _issue_sort_key(issue: CapabilityValidationIssue) -> tuple[str, str, str, str]:
    return (
        issue.instance_pointer,
        issue.schema_pointer,
        issue.keyword,
        issue.safe_message,
    )


def validate_json_value(
    compiled: CompiledBindingSchema,
    value: JsonValue,
    *,
    label: Literal["input", "output"],
) -> None:
    """Validate a JSON value against a compiled binding schema.

    Raises ``CapabilitySchemaValidationError`` with at most 20 deterministically
    ordered safe issues. Never echoes rejected values, enum members, patterns,
    defaults, examples, or schema descriptions.
    """
    if not isinstance(compiled, CompiledBindingSchema):
        raise TypeError("compiled must be a CompiledBindingSchema")
    if label not in {"input", "output"}:
        raise ValueError("label must be 'input' or 'output'")

    if compiled.require_object_root and not isinstance(value, dict):
        issue = CapabilityValidationIssue(
            instance_pointer="/",
            schema_pointer="/type",
            keyword="type",
            safe_message=_safe_message_for_keyword("type"),
        )
        raise CapabilitySchemaValidationError(label=label, issues=(issue,))

    errors = sorted(compiled._validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    issues: list[CapabilityValidationIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for error in errors:
        keyword = str(error.validator or "unknown")
        instance_pointer = _absolute_pointer(list(error.absolute_path))
        schema_path = list(error.absolute_schema_path)
        schema_pointer = _absolute_pointer(schema_path) if schema_path else "/"
        safe_message = _safe_message_for_keyword(keyword)
        issue = CapabilityValidationIssue(
            instance_pointer=instance_pointer,
            schema_pointer=schema_pointer,
            keyword=keyword,
            safe_message=safe_message,
        )
        key = _issue_sort_key(issue)
        if key in seen:
            continue
        seen.add(key)
        issues.append(issue)

    issues.sort(key=_issue_sort_key)
    issues = issues[:MAX_VALIDATION_ISSUES]
    raise CapabilitySchemaValidationError(label=label, issues=tuple(issues))


__all__ = [
    "CompiledBindingSchema",
    "MAX_VALIDATION_ISSUES",
    "binding_schema_digest",
    "compile_binding_schema",
    "normalize_binding_schema",
    "validate_json_value",
]
