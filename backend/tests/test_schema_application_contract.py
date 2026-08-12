from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import traceback

import pytest

from app.schema.application_contract import (
    ALEMBIC_VERSION_KEY,
    PRE_SQUASH_CONTROL_CONTRACT_DIGEST,
    PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD,
    DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH,
    LogicalApplicationContractError,
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import (
    SchemaComparisonError,
    compare_documents,
    normalize_document,
    sha256_canonical_json,
    structural_fingerprint,
)
from app.schema.contracts import (
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
)
from app.schema.sql_objects import (
    load_exclusion_manifest,
    load_pre_squash_snapshot,
)


def _column(name: str, ordinal: int) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "name": name,
        "formattedType": "uuid" if name == "id" else "text",
        "nullable": False,
        "defaultExpression": None,
        "identityKind": "",
        "generatedKind": "",
        "collation": None,
    }


def _constraint() -> dict[str, object]:
    return {
        "name": "entry_pkey",
        "type": "p",
        "definition": "PRIMARY KEY (id)",
        "deferrable": False,
        "initiallyDeferred": False,
        "validated": True,
        "foreignKeyUpdateAction": None,
        "foreignKeyDeleteAction": None,
        "foreignKeyMatchType": None,
    }


def _index(
    *,
    key_attribute_numbers: tuple[int, ...],
    include_attribute_numbers: tuple[int, ...],
) -> dict[str, object]:
    return {
        "name": "idx_entry_name",
        "parentTable": {"schema": "public", "name": "entry"},
        "accessMethod": "btree",
        "unique": False,
        "primary": False,
        "exclusion": False,
        "valid": True,
        "ready": True,
        "nullsNotDistinct": False,
        "definition": (
            "CREATE INDEX idx_entry_name ON public.entry USING btree (name) "
            "INCLUDE (id)"
        ),
        "expressions": None,
        "predicate": None,
        "keyAttributeNumbers": list(key_attribute_numbers),
        "includeAttributeNumbers": list(include_attribute_numbers),
    }


def _table(
    *,
    column_ordinals: tuple[int, int] = (1, 2),
    key_attribute_numbers: tuple[int, ...] = (2,),
    include_attribute_numbers: tuple[int, ...] = (1,),
) -> CanonicalSchemaObject:
    return CanonicalSchemaObject(
        key=CanonicalObjectKey("table", "public", "entry"),
        definition={
            "relationKind": "r",
            "persistence": "p",
            "partitionStrategy": None,
            "partitionBound": None,
            "columns": [
                _column("id", column_ordinals[0]),
                _column("name", column_ordinals[1]),
            ],
            "constraints": [_constraint()],
            "indexes": [
                _index(
                    key_attribute_numbers=key_attribute_numbers,
                    include_attribute_numbers=include_attribute_numbers,
                )
            ],
        },
    )


def _document(
    *objects: CanonicalSchemaObject,
    version: int = 1,
) -> CanonicalSchemaDocument:
    return CanonicalSchemaDocument(
        canonicalization_version=version,  # type: ignore[arg-type]
        postgres_major=15,
        objects=tuple(sorted(objects, key=lambda item: item.key)),
    )


def _unsafe_document_with_version(version: object) -> CanonicalSchemaDocument:
    document = object.__new__(CanonicalSchemaDocument)
    object.__setattr__(document, "canonicalization_version", version)
    object.__setattr__(document, "postgres_major", 15)
    object.__setattr__(document, "objects", ())
    return document


def _mutate_table(
    table: CanonicalSchemaObject,
    *,
    collection: str,
    field: str,
    value: object,
) -> CanonicalSchemaObject:
    definition = deepcopy(table.definition)
    definition[collection][0][field] = value  # type: ignore[index]
    return CanonicalSchemaObject(key=table.key, definition=definition)


def test_projection_removes_only_physical_attribute_numbers() -> None:
    old = _document(
        _table(
            column_ordinals=(1, 3),
            key_attribute_numbers=(3,),
            include_attribute_numbers=(1,),
        )
    )
    clean = _document(_table())

    old_logical = project_logical_application_document(
        old,
        control_stage=SchemaControlStage.MODEL_REFERENCE,
    )
    clean_logical = project_logical_application_document(
        clean,
        control_stage=SchemaControlStage.MODEL_REFERENCE,
    )

    assert old_logical.canonicalization_version == 2
    assert old_logical.to_payload() == clean_logical.to_payload()
    table = old_logical.objects[0].definition
    assert all("ordinal" not in column for column in table["columns"])
    assert all(
        "keyAttributeNumbers" not in index for index in table["indexes"]
    )
    assert all(
        "includeAttributeNumbers" not in index for index in table["indexes"]
    )


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("columns", "defaultExpression", "1"),
        ("constraints", "name", "ck_changed"),
        ("indexes", "name", "idx_changed"),
        ("indexes", "predicate", "id IS NOT NULL"),
    ],
)
def test_projection_preserves_semantic_differences(
    collection: str,
    field: str,
    value: object,
) -> None:
    left = project_logical_application_document(
        _document(_table()),
        control_stage=SchemaControlStage.MODEL_REFERENCE,
    )
    right = project_logical_application_document(
        _document(
            _mutate_table(
                _table(),
                collection=collection,
                field=field,
                value=value,
            )
        ),
        control_stage=SchemaControlStage.MODEL_REFERENCE,
    )

    with pytest.raises(SchemaComparisonError) as exc:
        compare_documents(left, right, exclusions=None)
    assert exc.value.safe_code == "unmanifested_schema_difference"


def test_document_versions_are_closed() -> None:
    assert _document(version=1).canonicalization_version == 1
    assert _document(version=2).canonicalization_version == 2
    with pytest.raises(ValueError, match="unsupported canonicalization version"):
        _document(version=3)
    with pytest.raises(ValueError, match="unsupported canonicalization version"):
        CanonicalSchemaDocument(True, 15, ())  # type: ignore[arg-type]


def test_pre_squash_control_is_definition_locked_before_extraction() -> None:
    snapshot = load_pre_squash_snapshot()

    projected = project_logical_application_document(
        snapshot.normalized_application_document,
        control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
    )

    assert ALEMBIC_VERSION_KEY not in {item.key for item in projected.objects}
    assert PRE_SQUASH_CONTROL_CONTRACT_DIGEST == sha256_canonical_json(
        PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD
    )


def test_pre_squash_control_drift_fails_closed() -> None:
    snapshot = load_pre_squash_snapshot()
    objects: list[CanonicalSchemaObject] = []
    for item in snapshot.normalized_application_document.objects:
        if item.key == ALEMBIC_VERSION_KEY:
            definition = deepcopy(item.definition)
            definition["persistence"] = "u"
            item = CanonicalSchemaObject(item.key, definition)
        objects.append(item)
    drifted = _document(*objects)

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            drifted,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )

    assert exc.value.safe_code == "schema_control_contract_drift"
    assert str(exc.value) == "schema_control_contract_drift"


def test_pre_squash_control_missing_fails_closed() -> None:
    snapshot = load_pre_squash_snapshot()
    without_control = _document(
        *(
            item
            for item in snapshot.normalized_application_document.objects
            if item.key != ALEMBIC_VERSION_KEY
        )
    )

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            without_control,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )

    assert exc.value.safe_code == "schema_control_contract_missing"


def test_model_reference_rejects_schema_controls() -> None:
    snapshot = load_pre_squash_snapshot()

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            snapshot.normalized_application_document,
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "schema_control_stage_invalid"


@pytest.mark.parametrize(
    ("kind", "name", "qualifier"),
    [
        ("table", "mindatlas_schema_identity", ""),
        ("function", "mindatlas_guard_schema_identity_mutation", ""),
        (
            "trigger",
            "trg_mindatlas_schema_identity_guard",
            "mindatlas_schema_identity",
        ),
    ],
)
def test_current_stages_reject_reserved_identity_controls(
    kind: str,
    name: str,
    qualifier: str,
) -> None:
    reserved = CanonicalSchemaObject(
        CanonicalObjectKey(kind, "public", name, qualifier),
        {"reserved": True},
    )

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(reserved),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "schema_control_stage_invalid"


def test_projection_rejects_unreviewed_nested_table_fields() -> None:
    table = _table()
    definition = deepcopy(table.definition)
    definition["columns"][0]["storage"] = "extended"  # type: ignore[index]

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(CanonicalSchemaObject(table.key, definition)),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "logical_schema_projection_invalid"


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("columns", "ordinal", "1"),
        ("columns", "name", 1),
        ("constraints", "validated", 1),
        ("indexes", "keyAttributeNumbers", ["2"]),
        ("indexes", "unique", 1),
    ],
)
def test_projection_rejects_malformed_catalog_field_types(
    collection: str,
    field: str,
    value: object,
) -> None:
    table = _mutate_table(
        _table(),
        collection=collection,
        field=field,
        value=value,
    )

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(table),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "logical_schema_projection_invalid"


def test_projection_rejects_duplicate_column_identity() -> None:
    table = _table()
    definition = deepcopy(table.definition)
    definition["columns"][1]["name"] = "id"  # type: ignore[index]

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(CanonicalSchemaObject(table.key, definition)),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "logical_schema_projection_invalid"


def test_projection_rejects_already_projected_document() -> None:
    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(version=2),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "logical_schema_projection_invalid"


def test_projection_rejects_boolean_version_at_its_boundary() -> None:
    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _unsafe_document_with_version(True),
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )

    assert exc.value.safe_code == "logical_schema_projection_invalid"


def test_legacy_exclusions_cannot_be_applied_after_projection() -> None:
    legacy = CanonicalSchemaObject(
        CanonicalObjectKey(
            "table",
            "public",
            "assistant_runtime_migration_item",
        ),
        {"legacy": True},
    )
    manifest = {
        "objects": [
            {
                "key": legacy.key.to_payload(),
                "definitionDigest": legacy.definition_digest,
            }
        ]
    }

    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(
            _document(legacy, version=2),
            manifest=manifest,
            side="old",
        )

    assert exc.value.safe_code == "exclusion_document_version_invalid"


def test_legacy_exclusions_reject_boolean_version_at_their_boundary() -> None:
    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(
            _unsafe_document_with_version(True),
            manifest={"objects": []},
            side="old",
        )

    assert exc.value.safe_code == "exclusion_document_version_invalid"


def test_invalid_control_stage_does_not_retain_untrusted_input() -> None:
    sentinel = "postgresql://user:secret@host/db"

    with pytest.raises(LogicalApplicationContractError) as exc:
        project_logical_application_document(
            _document(),
            control_stage=sentinel,  # type: ignore[arg-type]
        )

    rendered = "".join(
        traceback.format_exception(
            type(exc.value),
            exc.value,
            exc.value.__traceback__,
        )
    )
    assert str(exc.value) == "schema_control_stage_invalid"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert sentinel not in rendered


def _logical_manifest_payload() -> dict[str, object]:
    snapshot = load_pre_squash_snapshot()
    exclusions = load_exclusion_manifest()
    logical = project_logical_application_document(
        snapshot.normalized_application_document,
        control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
    )
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "schemaFamily": "pre_ga_v1",
        "sourceHead": "b6e2d4f8a901",
        "sourceSnapshotDigest": snapshot.snapshot_digest,
        "exclusionManifestDigest": exclusions.manifest_digest,
        "controlContractDigest": PRE_SQUASH_CONTROL_CONTRACT_DIGEST,
        "canonicalizationVersion": 2,
        "logicalApplicationDocument": logical.to_payload(),
        "logicalApplicationFingerprint": structural_fingerprint(logical),
    }
    return {
        **payload,
        "manifestDigest": sha256_canonical_json(payload),
    }


def _write_mutated_logical_manifest(
    tmp_path: Path,
    mutation: str,
) -> Path:
    payload = _logical_manifest_payload()
    if mutation == "duplicate_top_level_member":
        raw = json.dumps(payload, separators=(",", ":"))
        raw = raw.replace("{", '{"schemaVersion":999,', 1)
        path = tmp_path / "logical.json"
        path.write_text(raw, encoding="utf-8")
        return path
    if mutation == "boolean_version":
        payload["schemaVersion"] = True
    elif mutation == "extra_field":
        payload["generatedAt"] = "2026-08-05"
    elif mutation == "snapshot_cross_reference":
        payload["sourceSnapshotDigest"] = "0" * 64
    elif mutation == "exclusion_cross_reference":
        payload["exclusionManifestDigest"] = "0" * 64
    elif mutation == "control_cross_reference":
        payload["controlContractDigest"] = "0" * 64
    elif mutation == "logical_document_cross_reference":
        document = deepcopy(payload["logicalApplicationDocument"])
        document["objects"].pop()  # type: ignore[index]
        payload["logicalApplicationDocument"] = document
        payload["logicalApplicationFingerprint"] = sha256_canonical_json(document)
    elif mutation == "logical_fingerprint":
        payload["logicalApplicationFingerprint"] = "0" * 64
    elif mutation == "manifest_digest":
        payload["manifestDigest"] = "0" * 64
        path = tmp_path / "logical.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path
    else:  # pragma: no cover - parametrization is closed below
        raise AssertionError(mutation)
    digest_payload = {
        key: value for key, value in payload.items() if key != "manifestDigest"
    }
    payload["manifestDigest"] = sha256_canonical_json(digest_payload)
    path = tmp_path / "logical.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_committed_logical_contract_is_self_validating() -> None:
    contract = load_logical_application_contract()

    assert DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH.is_file()
    assert contract.schema_family == "pre_ga_v1"
    assert contract.source_head == "b6e2d4f8a901"
    assert contract.source_snapshot_digest == (
        "3ee2120ded35e7e550f947f726bc38a5eb5f6d3c88bf8b78e191a27b1e634346"
    )
    assert contract.exclusion_manifest_digest == (
        "f27f89bcfe248aa1e29fce60d1d19a51bafee857d1db7b3dff01c9ecfa7321f4"
    )
    assert contract.control_contract_digest == PRE_SQUASH_CONTROL_CONTRACT_DIGEST
    assert contract.logical_application_document.canonicalization_version == 2
    assert len(contract.logical_application_document.objects) == 179
    assert contract.logical_application_fingerprint == structural_fingerprint(
        contract.logical_application_document
    )


@pytest.mark.parametrize(
    ("mutation", "safe_code"),
    [
        ("duplicate_top_level_member", "logical_schema_manifest_invalid"),
        ("boolean_version", "logical_schema_manifest_invalid"),
        ("extra_field", "logical_schema_manifest_invalid"),
        ("snapshot_cross_reference", "logical_schema_cross_reference_mismatch"),
        ("exclusion_cross_reference", "logical_schema_cross_reference_mismatch"),
        ("control_cross_reference", "logical_schema_cross_reference_mismatch"),
        (
            "logical_document_cross_reference",
            "logical_schema_cross_reference_mismatch",
        ),
        ("logical_fingerprint", "logical_schema_manifest_invalid"),
        ("manifest_digest", "logical_schema_manifest_digest_mismatch"),
    ],
)
def test_logical_contract_loader_rejects_drift(
    tmp_path: Path,
    mutation: str,
    safe_code: str,
) -> None:
    path = _write_mutated_logical_manifest(tmp_path, mutation)

    with pytest.raises(LogicalApplicationContractError) as exc:
        load_logical_application_contract(path)

    assert exc.value.safe_code == safe_code
    assert str(exc.value) == safe_code


@pytest.mark.parametrize("failure_source", ["malformed_json", "missing_snapshot"])
def test_logical_contract_loader_discards_untrusted_exception_context(
    tmp_path: Path,
    failure_source: str,
) -> None:
    sentinel = "postgresql-secret-material"
    if failure_source == "malformed_json":
        path = tmp_path / "logical.json"
        path.write_text(f'{{"secret":"{sentinel}"', encoding="utf-8")
        kwargs = {}
    else:
        path = DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH
        kwargs = {"snapshot_path": tmp_path / sentinel}

    with pytest.raises(LogicalApplicationContractError) as exc:
        load_logical_application_contract(path, **kwargs)

    rendered = "".join(
        traceback.format_exception(
            type(exc.value),
            exc.value,
            exc.value.__traceback__,
        )
    )
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert sentinel not in rendered


@pytest.mark.parametrize(
    "raw",
    [
        "[" * 1_100 + "]" * 1_100,
        "9" * 5_000,
    ],
    ids=["excessive_nesting", "oversized_integer"],
)
def test_logical_contract_loader_bounds_json_decoder_resource_failures(
    tmp_path: Path,
    raw: str,
) -> None:
    path = tmp_path / "logical.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(LogicalApplicationContractError) as exc:
        load_logical_application_contract(path)

    rendered = "".join(
        traceback.format_exception(
            type(exc.value),
            exc.value,
            exc.value.__traceback__,
        )
    )
    assert exc.value.safe_code == "logical_schema_manifest_invalid"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert str(path) not in rendered
