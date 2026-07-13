from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

JsonPrimitive = None | bool | int | float | str
JsonValue = JsonPrimitive | list["JsonValue"] | tuple["JsonValue", ...] | dict[str, "JsonValue"]


def _reject_non_finite_float(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        raise ValueError("NaN and Infinity are not valid JSON values")
    return value


def _validate_json_value(value: Any, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return _reject_non_finite_float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        raise TypeError(f"bytes are not valid JSON values at {path}")
    if isinstance(value, (set, frozenset)):
        raise TypeError(f"sets are not valid JSON values at {path}")
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping keys must be strings at {path}")
            normalized[key] = _validate_json_value(item, path=f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _validate_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"unsupported JSON value type {type(value)!r} at {path}")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize a narrow JSON value to deterministic UTF-8 bytes."""
    validated = _validate_json_value(value)
    return json.dumps(
        validated,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("sha256_bytes requires bytes input")
    return hashlib.sha256(bytes(value)).hexdigest()


def sha256_canonical_json(value: JsonValue) -> str:
    return sha256_bytes(canonical_json_bytes(value))
