"""Canonical schema documents and fail-closed comparison primitives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from app.schema.contracts import (
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
    JsonValue,
)


def validate_json_value(value: Any, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise TypeError(f"floats are not canonical schema JSON values at {path}")
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError(f"timezone-naive datetimes are not valid at {path}")
        raise TypeError(f"datetimes are not canonical schema JSON values at {path}")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"bytes are not canonical schema JSON values at {path}")
    if isinstance(value, (set, frozenset)):
        raise TypeError(f"sets are not canonical schema JSON values at {path}")
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping keys must be strings at {path}")
            normalized[key] = validate_json_value(item, path=f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence):
        return [
            validate_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"unsupported canonical schema JSON type at {path}")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize strict schema JSON as deterministic compact UTF-8 bytes."""
    validated = validate_json_value(value)
    return json.dumps(
        validated,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("sha256_hex requires bytes")
    return hashlib.sha256(bytes(value)).hexdigest()


def sha256_canonical_json(value: JsonValue) -> str:
    return sha256_hex(canonical_json_bytes(value))


def normalize_catalog_sql(value: str | None) -> str | None:
    """Apply only the committed whitespace normalization to catalog SQL."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("catalog SQL must be a string or None")
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized = [line.rstrip(" \t") for line in lines]
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return "\n".join(normalized)


def structural_fingerprint(document: CanonicalSchemaDocument) -> str:
    return sha256_canonical_json(document.to_payload())


class SchemaComparisonError(RuntimeError):
    """Bounded comparison failure safe for control flow and sanitized logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


def _manifest_items(
    manifest: Mapping[str, Any] | object,
) -> tuple[tuple[CanonicalObjectKey, str], ...]:
    raw_items = (
        manifest.get("objects")
        if isinstance(manifest, Mapping)
        else getattr(manifest, "objects", None)
    )
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise SchemaComparisonError("exclusion_manifest_invalid")

    items: list[tuple[CanonicalObjectKey, str]] = []
    for raw in raw_items:
        if isinstance(raw, Mapping):
            raw_key = raw.get("key")
            digest = raw.get("definitionDigest")
        else:
            raw_key = getattr(raw, "key", None)
            digest = getattr(raw, "definition_digest", None)
        try:
            key = (
                raw_key
                if isinstance(raw_key, CanonicalObjectKey)
                else CanonicalObjectKey.from_payload(raw_key)
            )
        except (TypeError, ValueError):
            raise SchemaComparisonError("exclusion_manifest_invalid") from None
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise SchemaComparisonError("exclusion_manifest_invalid")
        items.append((key, digest))

    keys = tuple(key for key, _digest in items)
    if len(keys) != len(set(keys)):
        raise SchemaComparisonError("exclusion_manifest_invalid")
    return tuple(sorted(items, key=lambda item: item[0]))


def normalize_document(
    document: CanonicalSchemaDocument,
    *,
    manifest: Mapping[str, Any] | object,
    side: Literal["old", "clean"],
) -> CanonicalSchemaDocument:
    """Apply exact, definition-locked exclusions to one comparison side."""
    if (
        type(document.canonicalization_version) is not int
        or document.canonicalization_version != 1
    ):
        raise SchemaComparisonError("exclusion_document_version_invalid")
    manifest_items = _manifest_items(manifest)
    by_key = {item.key: item for item in document.objects}

    if side == "clean":
        if any(key in by_key for key, _digest in manifest_items):
            raise SchemaComparisonError("exclusion_object_present_in_clean_schema")
        return document
    if side != "old":
        raise ValueError("comparison side must be old or clean")

    for key, expected_digest in manifest_items:
        actual = by_key.get(key)
        if actual is None:
            raise SchemaComparisonError("exclusion_object_missing")
        if actual.definition_digest != expected_digest:
            raise SchemaComparisonError("exclusion_definition_mismatch")

    excluded_keys = {key for key, _digest in manifest_items}
    return CanonicalSchemaDocument(
        canonicalization_version=document.canonicalization_version,
        postgres_major=document.postgres_major,
        objects=tuple(item for item in document.objects if item.key not in excluded_keys),
    )


def compare_documents(
    left: CanonicalSchemaDocument,
    right: CanonicalSchemaDocument,
    *,
    exclusions: Mapping[str, Any] | object | None,
) -> None:
    """Require byte-identical documents after only committed exclusions."""
    if exclusions is not None:
        left = normalize_document(left, manifest=exclusions, side="old")
        right = normalize_document(right, manifest=exclusions, side="clean")
    if canonical_json_bytes(left.to_payload()) != canonical_json_bytes(right.to_payload()):
        raise SchemaComparisonError("unmanifested_schema_difference")
