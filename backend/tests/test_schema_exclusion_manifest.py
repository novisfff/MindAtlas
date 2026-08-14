from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from app.schema.canonical import sha256_canonical_json
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    NEXT_RESERVED_REVISION,
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
)
from app.schema.exclusions import (
    LEGACY_FUNCTION_KEYS,
    LEGACY_TABLE_NAMES,
    PLAN10_IMMUTABLE_TABLES,
    PLAN10_UPDATE_ONLY_TABLES,
    expected_legacy_object_keys,
)
from app.schema.sql_objects import (
    DEFAULT_EXCLUSION_MANIFEST_PATH,
    DEFAULT_PRE_SQUASH_SNAPSHOT_PATH,
    DEFAULT_SQL_OBJECT_REGISTRY_PATH,
    SchemaManifestError,
    build_exclusion_items,
    load_exclusion_manifest,
    load_pre_squash_snapshot,
    load_retained_sql_object_registry,
    ordered_retained_sql_objects,
    reference_scan_digest,
    renderable_sql_object,
    scan_live_legacy_imports,
    validate_manifest_set,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = BACKEND_ROOT / "alembic" / "versions"
DEVIATION_EVIDENCE = (
    BACKEND_ROOT.parent
    / "docs"
    / "superpowers"
    / "evidence"
    / "2026-07-28-pre-ga-clean-baseline-deviation.md"
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_legacy_source_allowlist_is_exact() -> None:
    keys = expected_legacy_object_keys()

    assert len(LEGACY_TABLE_NAMES) == 11
    assert len(LEGACY_FUNCTION_KEYS) == 1
    assert len(PLAN10_IMMUTABLE_TABLES) == 7
    assert len(PLAN10_UPDATE_ONLY_TABLES) == 1
    assert len(keys) == 27
    assert len(set(keys)) == 27
    assert all("*" not in part for key in keys for part in key)
    assert all(not part.endswith("_") for key in keys for part in key if part)


def test_legacy_source_allowlist_has_only_declared_object_kinds() -> None:
    keys = expected_legacy_object_keys()
    assert {key[0] for key in keys} == {"function", "table", "trigger"}
    assert all(key[1] == "public" for key in keys)
    assert sum(key[0] == "table" for key in keys) == 11
    assert sum(key[0] == "function" for key in keys) == 1
    assert sum(key[0] == "trigger" for key in keys) == 15
    assert keys == tuple(sorted(keys))


def test_trigger_keys_are_bound_to_exact_table_names() -> None:
    trigger_keys = [key for key in expected_legacy_object_keys() if key[0] == "trigger"]
    expected_targets = set(PLAN10_IMMUTABLE_TABLES) | set(PLAN10_UPDATE_ONLY_TABLES)

    assert {key[3] for key in trigger_keys} == expected_targets
    assert all(key[2].startswith(f"trg_{key[3]}_reject_") for key in trigger_keys)


def test_plan4_revision_is_additive_and_live() -> None:
    assert NEXT_RESERVED_REVISION != CLEAN_ROOT_REVISION
    revisions = list(ALEMBIC_VERSIONS.glob(f"{NEXT_RESERVED_REVISION}*.py"))
    assert len(revisions) == 1
    source = revisions[0].read_text(encoding="utf-8")
    assert 'down_revision = "pre_ga_v1_0001"' in source
    assert "pre_ga_v1_0001_clean_baseline.py" not in source


def test_live_metadata_registration_does_not_import_legacy_models() -> None:
    registration_files = (BACKEND_ROOT / "alembic" / "env.py", BACKEND_ROOT / "tests" / "_db.py")
    violations = {
        str(path.relative_to(BACKEND_ROOT)): sorted(
            module
            for module in _imported_modules(path)
            if module == "app.assistant.migration"
            or module.startswith("app.assistant.migration.")
        )
        for path in registration_files
    }

    assert violations == {
        "alembic/env.py": [],
        "tests/_db.py": [],
    }


def _schema_object(
    kind: str,
    name: str,
    *,
    qualifier: str = "",
    definition: dict[str, object] | None = None,
) -> CanonicalSchemaObject:
    return CanonicalSchemaObject(
        key=CanonicalObjectKey(kind, "public", name, qualifier),
        definition=definition or {"name": name},
    )


def _schema_document(*objects: CanonicalSchemaObject) -> CanonicalSchemaDocument:
    return CanonicalSchemaDocument(
        canonicalization_version=1,
        postgres_major=15,
        objects=tuple(sorted(objects, key=lambda item: item.key)),
    )


def test_exclusion_discovery_requires_all_27_exact_keys() -> None:
    objects = tuple(
        _schema_object(kind, name, qualifier=qualifier)
        for kind, _schema, name, qualifier in expected_legacy_object_keys()
    )
    discovered = build_exclusion_items(_schema_document(*objects))

    assert tuple(item.key for item in discovered) == tuple(item.key for item in objects)

    with pytest.raises(SchemaManifestError) as exc:
        build_exclusion_items(_schema_document(*objects[:-1]))
    assert exc.value.safe_code == "legacy_exclusion_object_missing"


def test_retained_function_and_trigger_render_with_explicit_dependencies() -> None:
    function = _schema_object(
        "function",
        "audit_entry",
        definition={
            "identityArguments": "",
            "definition": (
                "CREATE OR REPLACE FUNCTION public.audit_entry() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
            ),
        },
    )
    trigger = _schema_object(
        "trigger",
        "trg_entry_audit",
        qualifier="entry",
        definition={
            "table": {"schema": "public", "name": "entry"},
            "function": {
                "schema": "public",
                "name": "audit_entry",
                "identityArguments": "",
            },
            "definition": (
                "CREATE TRIGGER trg_entry_audit BEFORE UPDATE ON entry "
                "FOR EACH ROW EXECUTE FUNCTION audit_entry()"
            ),
        },
    )

    function_payload = renderable_sql_object(function)
    trigger_payload = renderable_sql_object(trigger)

    assert function_payload["createSql"] == function.definition["definition"]
    assert function_payload["dependencies"] == []
    assert trigger_payload["dependencies"] == [
        {"kind": "function", "schema": "public", "name": "audit_entry", "qualifier": ""},
        {"kind": "table", "schema": "public", "name": "entry", "qualifier": ""},
    ]
    assert trigger_payload["definitionDigest"] == trigger.definition_digest


def test_retained_sql_creation_order_is_functions_then_schema_table_trigger() -> None:
    function_b = _schema_object(
        "function",
        "function_b",
        definition={"definition": "SELECT 2", "identityArguments": ""},
    )
    function_a = _schema_object(
        "function",
        "function_a",
        definition={"definition": "SELECT 1", "identityArguments": ""},
    )
    trigger_on_z = _schema_object(
        "trigger",
        "aaa_trigger",
        qualifier="z_table",
        definition={"definition": "SELECT 3"},
    )
    trigger_on_a = _schema_object(
        "trigger",
        "zzz_trigger",
        qualifier="a_table",
        definition={"definition": "SELECT 4"},
    )

    ordered = ordered_retained_sql_objects(
        _schema_document(trigger_on_z, function_b, trigger_on_a, function_a)
    )

    assert tuple(item.key for item in ordered) == (
        function_a.key,
        function_b.key,
        trigger_on_a.key,
        trigger_on_z.key,
    )


def test_registry_preserves_explicit_view_dependencies(tmp_path: Path) -> None:
    dependency = CanonicalObjectKey("table", "public", "entry")
    view = _schema_object(
        "view",
        "entry_view",
        definition={
            "definition": "SELECT id FROM entry;",
            "checkOption": "NONE",
            "securityBarrier": False,
            "securityInvoker": False,
        },
    )
    registry_payload = {
        "schemaVersion": 1,
        "canonicalizationVersion": 1,
        "sourceHead": "b6e2d4f8a901",
        "objects": [renderable_sql_object(view, dependencies=(dependency,))],
    }
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                **registry_payload,
                "registryDigest": sha256_canonical_json(registry_payload),
            }
        ),
        encoding="utf-8",
    )

    registry = load_retained_sql_object_registry(path)

    assert registry.creation_order[0].dependencies == (dependency,)


def test_retained_sql_registry_loader_verifies_self_and_definition_digests(
    tmp_path: Path,
) -> None:
    function = _schema_object(
        "function",
        "audit_entry",
        definition={
            "identityArguments": "",
            "definition": (
                "CREATE OR REPLACE FUNCTION public.audit_entry() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
            ),
        },
    )
    payload = {
        "schemaVersion": 1,
        "canonicalizationVersion": 1,
        "sourceHead": "b6e2d4f8a901",
        "objects": [renderable_sql_object(function)],
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {**payload, "registryDigest": sha256_canonical_json(payload)},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    registry = load_retained_sql_object_registry(registry_path)

    assert len(registry.creation_order) == 1
    assert registry.creation_order[0].definition_digest == function.definition_digest

    tampered = json.loads(registry_path.read_text(encoding="utf-8"))
    tampered["objects"][0]["createSql"] += " -- drift"
    registry_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(SchemaManifestError) as exc:
        load_retained_sql_object_registry(registry_path)
    assert exc.value.safe_code == "sql_object_registry_digest_mismatch"

    tampered = {**payload}
    tampered["objects"] = [dict(payload["objects"][0])]
    tampered["objects"][0]["createSql"] += " -- recomputed drift"
    tampered["registryDigest"] = sha256_canonical_json(tampered)
    registry_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(SchemaManifestError) as exc:
        load_retained_sql_object_registry(registry_path)
    assert exc.value.safe_code == "sql_object_create_sql_mismatch"


def test_live_reference_scan_excludes_only_retirement_boundary(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    retired = app_root / "assistant" / "migration"
    assistant = app_root / "assistant"
    retired.mkdir(parents=True)
    assistant.mkdir(parents=True, exist_ok=True)
    retired.joinpath("internal.py").write_text(
        "from app.assistant.migration.models import Legacy\n",
        encoding="utf-8",
    )
    assistant.joinpath("service.py").write_text(
        "import app.assistant.migration.models\n",
        encoding="utf-8",
    )
    assistant.joinpath("parent_alias.py").write_text(
        "from app.assistant import migration\n",
        encoding="utf-8",
    )
    assistant.joinpath("relative.py").write_text(
        "from .migration import models\n",
        encoding="utf-8",
    )
    nested = assistant / "nested"
    nested.mkdir()
    nested.joinpath("parent_relative.py").write_text(
        "from .. import migration\n",
        encoding="utf-8",
    )
    app_root.joinpath("package_relative.py").write_text(
        "from .assistant.migration import models\n",
        encoding="utf-8",
    )

    hits = scan_live_legacy_imports(app_root)

    assert hits == (
        "assistant/nested/parent_relative.py:1:app.assistant.migration",
        "assistant/parent_alias.py:1:app.assistant.migration",
        "assistant/relative.py:1:app.assistant.migration.models",
        "assistant/service.py:1:app.assistant.migration.models",
        "package_relative.py:1:app.assistant.migration.models",
    )
    assert reference_scan_digest(()) == sha256_canonical_json([])
    assert reference_scan_digest(hits) == sha256_canonical_json(list(hits))


def test_committed_capture_manifests_are_cross_validated_and_exact() -> None:
    exclusions, snapshot, registry = validate_manifest_set(
        DEFAULT_EXCLUSION_MANIFEST_PATH,
        DEFAULT_PRE_SQUASH_SNAPSHOT_PATH,
        DEFAULT_SQL_OBJECT_REGISTRY_PATH,
    )

    assert exclusions == load_exclusion_manifest()
    assert snapshot == load_pre_squash_snapshot()
    assert registry == load_retained_sql_object_registry()
    assert snapshot.manifest_digest == snapshot.snapshot_digest
    assert len(exclusions.objects) == 27
    assert len(snapshot.source_document.objects) == 207
    assert len(snapshot.normalized_application_document.objects) == 180
    assert len(registry.creation_order) == 101
    assert exclusions.reference_scan_digest == reference_scan_digest(
        scan_live_legacy_imports(BACKEND_ROOT / "app")
    )
    assert exclusions.deviation_evidence_digest == hashlib.sha256(
        DEVIATION_EVIDENCE.read_bytes()
    ).hexdigest()
    assert {
        item.key.kind for item in registry.creation_order
    } == {"function", "trigger"}


@pytest.mark.parametrize("mutation", ["boolean_version", "extra_key_field"])
def test_registry_loader_rejects_non_exact_json_schema(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = json.loads(DEFAULT_SQL_OBJECT_REGISTRY_PATH.read_text(encoding="utf-8"))
    if mutation == "boolean_version":
        payload["schemaVersion"] = True
    elif mutation == "extra_key_field":
        payload["objects"][0]["key"]["oid"] = 123
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(mutation)
    digest_payload = {
        key: value for key, value in payload.items() if key != "registryDigest"
    }
    payload["registryDigest"] = sha256_canonical_json(digest_payload)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SchemaManifestError) as exc:
        load_retained_sql_object_registry(path)

    assert exc.value.safe_code == "sql_object_registry_invalid"


@pytest.mark.parametrize(
    "duplicate_location",
    ["top_level", "object_key", "canonical_definition"],
)
def test_registry_loader_rejects_duplicate_json_members(
    tmp_path: Path,
    duplicate_location: str,
) -> None:
    raw = DEFAULT_SQL_OBJECT_REGISTRY_PATH.read_text(encoding="utf-8")
    if duplicate_location == "top_level":
        raw = raw.replace("{", '{"schemaVersion":999,', 1)
    elif duplicate_location == "object_key":
        raw = raw.replace(
            '"key":{"kind":"function"',
            '"key":{"kind":"table","kind":"function"',
            1,
        )
    elif duplicate_location == "canonical_definition":
        raw = raw.replace(
            '"canonicalDefinition":{"definition":',
            '"canonicalDefinition":{"definition":"drift","definition":',
            1,
        )
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(duplicate_location)
    path = tmp_path / "registry.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SchemaManifestError) as exc:
        load_retained_sql_object_registry(path)

    assert exc.value.safe_code == "schema_manifest_invalid"


def test_committed_capture_contains_no_environment_or_catalog_identity_fields() -> None:
    forbidden_keys = {
        "capturedAt",
        "databaseUrl",
        "generatedAt",
        "machinePath",
        "oid",
        "owner",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    for path in (
        DEFAULT_EXCLUSION_MANIFEST_PATH,
        DEFAULT_PRE_SQUASH_SNAPSHOT_PATH,
        DEFAULT_SQL_OBJECT_REGISTRY_PATH,
    ):
        walk(json.loads(path.read_text(encoding="utf-8")))
