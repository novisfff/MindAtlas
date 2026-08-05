"""Fail-closed projection from physical catalogs to application contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
import re
from typing import Any

from app.schema.canonical import (
    canonical_json_bytes,
    sha256_canonical_json,
    structural_fingerprint,
)
from app.schema.contracts import (
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
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

DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "pre_ga_v1-clean-application-contract.json"
)
_DEFAULT_PRE_SQUASH_SNAPSHOT_PATH = (
    DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH.parent
    / "pre_ga_v1-pre-squash-schema.json"
)
_DEFAULT_EXCLUSION_MANIFEST_PATH = (
    DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH.parent
    / "pre_ga_v1-exclusions.json"
)


@dataclass(frozen=True)
class LogicalApplicationContract:
    schema_family: str
    source_head: str
    source_snapshot_digest: str
    exclusion_manifest_digest: str
    control_contract_digest: str
    logical_application_document: CanonicalSchemaDocument
    logical_application_fingerprint: str
    manifest_digest: str


class _DuplicateJsonMember(ValueError):
    pass


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "schemaFamily",
        "sourceHead",
        "sourceSnapshotDigest",
        "exclusionManifestDigest",
        "controlContractDigest",
        "canonicalizationVersion",
        "logicalApplicationDocument",
        "logicalApplicationFingerprint",
        "manifestDigest",
    }
)
_DOCUMENT_KEYS = frozenset(
    {"canonicalizationVersion", "postgresMajor", "objects"}
)
_OBJECT_KEYS = frozenset({"key", "definition"})
_KEY_KEYS = frozenset({"kind", "schema", "name", "qualifier"})


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateJsonMember
        payload[key] = value
    return payload


def _load_manifest_json(path: Path) -> Mapping[str, Any]:
    payload: Any = None
    invalid = False
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        invalid = True
    if invalid:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None
    if not isinstance(payload, Mapping):
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        )
    return payload


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _parse_logical_document(value: Any) -> CanonicalSchemaDocument:
    if not isinstance(value, Mapping) or set(value) != _DOCUMENT_KEYS:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        )
    if type(value.get("canonicalizationVersion")) is not int or value.get(
        "canonicalizationVersion"
    ) != 2:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        )
    if type(value.get("postgresMajor")) is not int or value.get(
        "postgresMajor"
    ) != 15:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        )
    raw_objects = value.get("objects")
    if not isinstance(raw_objects, Sequence) or isinstance(
        raw_objects, (str, bytes)
    ):
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        )
    objects: list[CanonicalSchemaObject] = []
    document: CanonicalSchemaDocument | None = None
    invalid = False
    try:
        for raw_object in raw_objects:
            if (
                not isinstance(raw_object, Mapping)
                or set(raw_object) != _OBJECT_KEYS
            ):
                raise ValueError
            raw_key = raw_object.get("key")
            definition = raw_object.get("definition")
            if (
                not isinstance(raw_key, Mapping)
                or set(raw_key) != _KEY_KEYS
                or not isinstance(definition, Mapping)
            ):
                raise ValueError
            objects.append(
                CanonicalSchemaObject(
                    CanonicalObjectKey.from_payload(raw_key),
                    definition,
                )
            )
        document = CanonicalSchemaDocument(2, 15, tuple(objects))
    except (TypeError, ValueError):
        invalid = True
    if invalid or document is None:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None
    return document


def load_logical_application_contract(
    path: Path = DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH,
    *,
    snapshot_path: Path = _DEFAULT_PRE_SQUASH_SNAPSHOT_PATH,
    exclusion_path: Path = _DEFAULT_EXCLUSION_MANIFEST_PATH,
) -> LogicalApplicationContract:
    """Load and cross-check the committed version-2 application contract."""
    from app.schema.sql_objects import (
        SchemaManifestError,
        load_exclusion_manifest,
        load_pre_squash_snapshot,
    )

    raw = _load_manifest_json(path)
    if set(raw) != _MANIFEST_KEYS:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None

    claimed_manifest_digest = raw.get("manifestDigest")
    if not _valid_sha256(claimed_manifest_digest):
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None
    digest_payload = {
        key: value for key, value in raw.items() if key != "manifestDigest"
    }
    calculated_manifest_digest: str | None = None
    digest_invalid = False
    try:
        calculated_manifest_digest = sha256_canonical_json(digest_payload)
    except (TypeError, ValueError):
        digest_invalid = True
    if digest_invalid or calculated_manifest_digest is None:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None
    if calculated_manifest_digest != claimed_manifest_digest:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_digest_mismatch"
        ) from None

    if (
        type(raw.get("schemaVersion")) is not int
        or raw.get("schemaVersion") != 1
        or type(raw.get("canonicalizationVersion")) is not int
        or raw.get("canonicalizationVersion") != 2
        or raw.get("schemaFamily") != SCHEMA_FAMILY
        or raw.get("sourceHead") != PRE_SQUASH_HEAD
    ):
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None

    digest_fields = (
        "sourceSnapshotDigest",
        "exclusionManifestDigest",
        "controlContractDigest",
        "logicalApplicationFingerprint",
    )
    if any(not _valid_sha256(raw.get(field)) for field in digest_fields):
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None

    document = _parse_logical_document(raw.get("logicalApplicationDocument"))
    logical_fingerprint = raw["logicalApplicationFingerprint"]
    if structural_fingerprint(document) != logical_fingerprint:
        raise LogicalApplicationContractError(
            "logical_schema_manifest_invalid"
        ) from None

    snapshot = None
    exclusions = None
    reference_invalid = False
    try:
        snapshot = load_pre_squash_snapshot(snapshot_path)
        exclusions = load_exclusion_manifest(exclusion_path)
    except SchemaManifestError:
        reference_invalid = True
    if reference_invalid or snapshot is None or exclusions is None:
        raise LogicalApplicationContractError(
            "logical_schema_cross_reference_mismatch"
        ) from None

    if (
        raw["sourceSnapshotDigest"] != snapshot.snapshot_digest
        or raw["exclusionManifestDigest"] != exclusions.manifest_digest
        or raw["controlContractDigest"]
        != PRE_SQUASH_CONTROL_CONTRACT_DIGEST
    ):
        raise LogicalApplicationContractError(
            "logical_schema_cross_reference_mismatch"
        ) from None

    projected: CanonicalSchemaDocument | None = None
    projection_invalid = False
    try:
        projected = project_logical_application_document(
            snapshot.normalized_application_document,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )
    except LogicalApplicationContractError:
        projection_invalid = True
    if projection_invalid or projected is None:
        raise LogicalApplicationContractError(
            "logical_schema_cross_reference_mismatch"
        ) from None
    if canonical_json_bytes(projected.to_payload()) != canonical_json_bytes(
        document.to_payload()
    ):
        raise LogicalApplicationContractError(
            "logical_schema_cross_reference_mismatch"
        ) from None

    return LogicalApplicationContract(
        schema_family=SCHEMA_FAMILY,
        source_head=PRE_SQUASH_HEAD,
        source_snapshot_digest=raw["sourceSnapshotDigest"],
        exclusion_manifest_digest=raw["exclusionManifestDigest"],
        control_contract_digest=raw["controlContractDigest"],
        logical_application_document=document,
        logical_application_fingerprint=logical_fingerprint,
        manifest_digest=claimed_manifest_digest,
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
    "DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH",
    "PRE_SQUASH_CONTROL_CONTRACT_DIGEST",
    "PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD",
    "LogicalApplicationContract",
    "LogicalApplicationContractError",
    "SchemaControlStage",
    "load_logical_application_contract",
    "project_logical_application_document",
]
