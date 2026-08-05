"""Fail-closed projection from physical catalogs to application contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from app.schema.canonical import sha256_canonical_json
from app.schema.contracts import (
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
    JsonValue,
)


class SchemaControlStage(StrEnum):
    """Closed schema-control expectations for current implementation stages."""

    PRE_SQUASH_MIGRATED = "pre_squash_migrated"
    MODEL_REFERENCE = "model_reference"


class LogicalApplicationContractError(RuntimeError):
    """Bounded logical-contract failure safe for control flow and logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


ALEMBIC_VERSION_KEY = CanonicalObjectKey(
    "table",
    "public",
    "alembic_version",
)
ALEMBIC_VERSION_DEFINITION_DIGEST = (
    "c215428519337adf9885ec83e0da716e1e2ea82f058b4b162a89941e22965149"
)
PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD: Mapping[str, JsonValue] = {
    "schemaVersion": 1,
    "objects": [
        {
            "key": ALEMBIC_VERSION_KEY.to_payload(),
            "definitionDigest": ALEMBIC_VERSION_DEFINITION_DIGEST,
        }
    ],
}
PRE_SQUASH_CONTROL_CONTRACT_DIGEST = sha256_canonical_json(
    PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD
)

_RESERVED_IDENTITY_CONTROL_KEYS = frozenset(
    {
        CanonicalObjectKey("table", "public", "mindatlas_schema_identity"),
        CanonicalObjectKey(
            "function",
            "public",
            "mindatlas_guard_schema_identity_mutation",
        ),
        CanonicalObjectKey(
            "trigger",
            "public",
            "trg_mindatlas_schema_identity_guard",
            "mindatlas_schema_identity",
        ),
    }
)

_TABLE_KEYS = frozenset(
    {
        "relationKind",
        "persistence",
        "partitionStrategy",
        "partitionBound",
        "columns",
        "constraints",
        "indexes",
    }
)
_COLUMN_KEYS = frozenset(
    {
        "ordinal",
        "name",
        "formattedType",
        "nullable",
        "defaultExpression",
        "identityKind",
        "generatedKind",
        "collation",
    }
)
_CONSTRAINT_KEYS = frozenset(
    {
        "name",
        "type",
        "definition",
        "deferrable",
        "initiallyDeferred",
        "validated",
        "foreignKeyUpdateAction",
        "foreignKeyDeleteAction",
        "foreignKeyMatchType",
    }
)
_INDEX_KEYS = frozenset(
    {
        "name",
        "parentTable",
        "accessMethod",
        "unique",
        "primary",
        "exclusion",
        "valid",
        "ready",
        "nullsNotDistinct",
        "definition",
        "expressions",
        "predicate",
        "keyAttributeNumbers",
        "includeAttributeNumbers",
    }
)
_PHYSICAL_COLUMN_KEYS = frozenset({"ordinal"})
_PHYSICAL_INDEX_KEYS = frozenset(
    {"keyAttributeNumbers", "includeAttributeNumbers"}
)


def _require_mapping(value: Any) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    return value


def _require_sequence(value: Any) -> Sequence[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    return value


def _require_exact_keys(
    value: Mapping[str, JsonValue],
    expected: frozenset[str],
) -> None:
    if set(value) != expected:
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _require_text(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _require_optional_text(value: Any) -> None:
    if value is not None and not isinstance(value, str):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _require_boolean(value: Any) -> None:
    if type(value) is not bool:
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _require_attribute_numbers(value: Any, *, allow_zero: bool) -> None:
    numbers = _require_sequence(value)
    if any(
        type(number) is not int or number < (0 if allow_zero else 1)
        for number in numbers
    ):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _validate_column(item: Mapping[str, JsonValue]) -> None:
    ordinal = item["ordinal"]
    if type(ordinal) is not int or ordinal <= 0:
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    _require_text(item["name"])
    _require_text(item["formattedType"])
    _require_boolean(item["nullable"])
    _require_optional_text(item["defaultExpression"])
    if not isinstance(item["identityKind"], str):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    if not isinstance(item["generatedKind"], str):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    _require_optional_text(item["collation"])


def _validate_constraint(item: Mapping[str, JsonValue]) -> None:
    for field in ("name", "type", "definition"):
        _require_text(item[field])
    for field in ("deferrable", "initiallyDeferred", "validated"):
        _require_boolean(item[field])
    for field in (
        "foreignKeyUpdateAction",
        "foreignKeyDeleteAction",
        "foreignKeyMatchType",
    ):
        _require_optional_text(item[field])


def _validate_index(item: Mapping[str, JsonValue]) -> None:
    for field in ("name", "accessMethod", "definition"):
        _require_text(item[field])
    parent = _require_mapping(item["parentTable"])
    _require_exact_keys(parent, frozenset({"schema", "name"}))
    _require_text(parent["schema"])
    _require_text(parent["name"])
    for field in (
        "unique",
        "primary",
        "exclusion",
        "valid",
        "ready",
        "nullsNotDistinct",
    ):
        _require_boolean(item[field])
    _require_optional_text(item["expressions"])
    _require_optional_text(item["predicate"])
    _require_attribute_numbers(item["keyAttributeNumbers"], allow_zero=True)
    _require_attribute_numbers(item["includeAttributeNumbers"], allow_zero=False)


def _validate_project_item(
    item: Mapping[str, JsonValue],
    expected_keys: frozenset[str],
) -> None:
    if expected_keys == _COLUMN_KEYS:
        _validate_column(item)
    elif expected_keys == _CONSTRAINT_KEYS:
        _validate_constraint(item)
    elif expected_keys == _INDEX_KEYS:
        _validate_index(item)
    else:  # pragma: no cover - callers are closed in this module
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )


def _project_items(
    value: Any,
    *,
    expected_keys: frozenset[str],
    physical_keys: frozenset[str],
    sort_fields: tuple[str, ...],
) -> list[dict[str, JsonValue]]:
    projected: list[dict[str, JsonValue]] = []
    for raw_item in _require_sequence(value):
        item = _require_mapping(raw_item)
        _require_exact_keys(item, expected_keys)
        _validate_project_item(item, expected_keys)
        projected.append(
            {
                key: nested
                for key, nested in item.items()
                if key not in physical_keys
            }
        )
    try:
        result = sorted(
            projected,
            key=lambda item: tuple(str(item[field]) for field in sort_fields),
        )
        identities = tuple(
            tuple(str(item[field]) for field in sort_fields)
            for item in result
        )
        if len(identities) != len(set(identities)):
            raise LogicalApplicationContractError(
                "logical_schema_projection_invalid"
            )
        return result
    except KeyError as exc:
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        ) from exc


def _project_table_definition(
    raw_definition: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    definition = _require_mapping(raw_definition)
    _require_exact_keys(definition, _TABLE_KEYS)
    _require_text(definition["relationKind"])
    _require_text(definition["persistence"])
    _require_optional_text(definition["partitionStrategy"])
    _require_optional_text(definition["partitionBound"])
    return {
        "relationKind": definition["relationKind"],
        "persistence": definition["persistence"],
        "partitionStrategy": definition["partitionStrategy"],
        "partitionBound": definition["partitionBound"],
        "columns": _project_items(
            definition["columns"],
            expected_keys=_COLUMN_KEYS,
            physical_keys=_PHYSICAL_COLUMN_KEYS,
            sort_fields=("name",),
        ),
        "constraints": _project_items(
            definition["constraints"],
            expected_keys=_CONSTRAINT_KEYS,
            physical_keys=frozenset(),
            sort_fields=("type", "name", "definition"),
        ),
        "indexes": _project_items(
            definition["indexes"],
            expected_keys=_INDEX_KEYS,
            physical_keys=_PHYSICAL_INDEX_KEYS,
            sort_fields=("name", "definition"),
        ),
    }


def _reject_reserved_identity_controls(
    objects_by_key: Mapping[CanonicalObjectKey, CanonicalSchemaObject],
) -> None:
    if _RESERVED_IDENTITY_CONTROL_KEYS.intersection(objects_by_key):
        raise LogicalApplicationContractError(
            "schema_control_stage_invalid"
        )


def project_logical_application_document(
    document: CanonicalSchemaDocument,
    *,
    control_stage: SchemaControlStage,
) -> CanonicalSchemaDocument:
    """Validate controls and project one raw version-1 document to version 2."""
    if (
        type(document.canonicalization_version) is not int
        or document.canonicalization_version != 1
    ):
        raise LogicalApplicationContractError(
            "logical_schema_projection_invalid"
        )
    objects_by_key = {item.key: item for item in document.objects}
    if type(control_stage) is not SchemaControlStage:
        raise LogicalApplicationContractError(
            "schema_control_stage_invalid"
        )
    stage = control_stage

    if stage is SchemaControlStage.PRE_SQUASH_MIGRATED:
        control = objects_by_key.pop(ALEMBIC_VERSION_KEY, None)
        if control is None:
            raise LogicalApplicationContractError(
                "schema_control_contract_missing"
            )
        if control.definition_digest != ALEMBIC_VERSION_DEFINITION_DIGEST:
            raise LogicalApplicationContractError(
                "schema_control_contract_drift"
            )
    elif ALEMBIC_VERSION_KEY in objects_by_key:
        raise LogicalApplicationContractError(
            "schema_control_stage_invalid"
        )

    _reject_reserved_identity_controls(objects_by_key)
    projected = tuple(
        CanonicalSchemaObject(
            key=item.key,
            definition=(
                _project_table_definition(item.definition)
                if item.key.kind == "table"
                else item.definition
            ),
        )
        for item in sorted(objects_by_key.values(), key=lambda value: value.key)
    )
    return CanonicalSchemaDocument(
        canonicalization_version=2,
        postgres_major=document.postgres_major,
        objects=projected,
    )


__all__ = [
    "ALEMBIC_VERSION_DEFINITION_DIGEST",
    "ALEMBIC_VERSION_KEY",
    "PRE_SQUASH_CONTROL_CONTRACT_DIGEST",
    "PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD",
    "LogicalApplicationContractError",
    "SchemaControlStage",
    "project_logical_application_document",
]
