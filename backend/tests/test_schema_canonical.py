from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from app.schema.canonical import (
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
    SchemaComparisonError,
    canonical_json_bytes,
    compare_documents,
    normalize_catalog_sql,
    normalize_document,
    structural_fingerprint,
)


def _table_object(name: str, *, nullable: bool = False) -> CanonicalSchemaObject:
    return CanonicalSchemaObject(
        key=CanonicalObjectKey("table", "public", name),
        definition={
            "name": name,
            "columns": [
                {
                    "name": "id",
                    "formattedType": "uuid",
                    "nullable": nullable,
                }
            ],
        },
    )


def _document(*objects: CanonicalSchemaObject) -> CanonicalSchemaDocument:
    return CanonicalSchemaDocument(
        canonicalization_version=1,
        postgres_major=15,
        objects=tuple(sorted(objects, key=lambda item: item.key)),
    )


def _manifest_for(
    obj: CanonicalSchemaObject,
    *,
    definition_digest: str | None = None,
) -> dict[str, object]:
    return {
        "objects": [
            {
                "key": obj.key.to_payload(),
                "definitionDigest": definition_digest or obj.definition_digest,
            }
        ]
    }


def test_canonical_json_is_utf8_sorted_and_compact() -> None:
    payload = {"z": [2, 1], "ä": {"b": True, "a": None}}
    assert canonical_json_bytes(payload) == (
        b'{"z":[2,1],"\xc3\xa4":{"a":null,"b":true}}'
    )


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        float("nan"),
        b"bytes",
        {"set"},
        {1: "non-string-key"},
        datetime(2026, 7, 28, 12, 0, 0),
    ],
)
def test_canonical_json_rejects_non_contract_values(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_json_bytes(value)  # type: ignore[arg-type]


def test_catalog_sql_normalization_is_narrow_and_stable() -> None:
    assert normalize_catalog_sql("\r\n SELECT 1;  \r\nline two\t\r\n\r\n") == (
        " SELECT 1;\nline two"
    )
    assert normalize_catalog_sql(None) is None


def test_structural_digest_has_fixed_vector() -> None:
    document = _document(
        CanonicalSchemaObject(
            key=CanonicalObjectKey("namespace", "public", "public"),
            definition={"name": "public"},
        )
    )
    expected_bytes = (
        b'{"canonicalizationVersion":1,"objects":[{"definition":{"name":'
        b'"public"},"key":{"kind":"namespace","name":"public","qualifier":'
        b'"","schema":"public"}}],"postgresMajor":15}'
    )
    assert canonical_json_bytes(document.to_payload()) == expected_bytes
    assert structural_fingerprint(document) == hashlib.sha256(expected_bytes).hexdigest()


def test_schema_document_requires_unique_sorted_objects() -> None:
    first = _table_object("a")
    second = _table_object("b")
    with pytest.raises(ValueError, match="unique and sorted"):
        CanonicalSchemaDocument(1, 15, (second, first))
    with pytest.raises(ValueError, match="unique and sorted"):
        CanonicalSchemaDocument(1, 15, (first, first))


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "public", "name": "entry", "qualifier": ""},
        {"kind": 1, "schema": "public", "name": "entry", "qualifier": ""},
        {"kind": "table", "schema": None, "name": "entry", "qualifier": ""},
        {"kind": "table", "schema": "public", "name": "", "qualifier": ""},
        {"kind": "table", "schema": "public", "name": "entry", "qualifier": 1},
    ],
)
def test_canonical_object_key_rejects_malformed_payload(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="invalid canonical object key"):
        CanonicalObjectKey.from_payload(payload)


def test_comparator_rejects_unmanifested_difference() -> None:
    left = _document(_table_object("entry"))
    right = _document(_table_object("entry", nullable=True))
    with pytest.raises(SchemaComparisonError) as exc:
        compare_documents(left, right, exclusions=None)
    assert exc.value.safe_code == "unmanifested_schema_difference"


def test_exclusion_digest_must_match_before_removal() -> None:
    legacy = _table_object("assistant_runtime_migration_item")
    left = _document(legacy)
    manifest = _manifest_for(legacy, definition_digest="0" * 64)
    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(left, manifest=manifest, side="old")
    assert exc.value.safe_code == "exclusion_definition_mismatch"


def test_exact_exclusion_is_removed_only_from_old_side() -> None:
    retained = _table_object("entry")
    legacy = _table_object("assistant_runtime_migration_item")
    manifest = _manifest_for(legacy)
    normalized = normalize_document(
        _document(legacy, retained),
        manifest=manifest,
        side="old",
    )
    assert normalized.objects == (retained,)

    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(
            _document(legacy, retained),
            manifest=manifest,
            side="clean",
        )
    assert exc.value.safe_code == "exclusion_object_present_in_clean_schema"


def test_missing_manifest_object_fails_closed() -> None:
    legacy = _table_object("assistant_runtime_migration_item")
    manifest = _manifest_for(legacy)
    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(_document(), manifest=manifest, side="old")
    assert exc.value.safe_code == "exclusion_object_missing"


def test_comparator_accepts_only_definition_locked_exclusion() -> None:
    retained = _table_object("entry")
    legacy = _table_object("assistant_runtime_migration_item")
    manifest = _manifest_for(legacy)
    compare_documents(
        _document(legacy, retained),
        _document(retained),
        exclusions=manifest,
    )
