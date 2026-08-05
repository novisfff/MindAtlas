"""Strict loaders and renderers for captured pre-GA schema artifacts."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.schema.canonical import sha256_canonical_json
from app.schema.canonical import structural_fingerprint
from app.schema.catalog import CatalogReadError, PostgresCatalogReader
from app.schema.contracts import (
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
    JsonValue,
)
from app.schema.exclusions import LEGACY_TABLE_NAMES, expected_legacy_object_keys


class SchemaManifestError(RuntimeError):
    """Bounded manifest failure safe for automation and logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


class SqlObjectRegistryError(SchemaManifestError):
    """Bounded retained-SQL installation failure."""


@dataclass(frozen=True)
class RetainedSqlObject:
    key: CanonicalObjectKey
    canonical_definition: Mapping[str, JsonValue]
    definition_digest: str
    create_sql: str
    dependencies: tuple[CanonicalObjectKey, ...]


@dataclass(frozen=True)
class RetainedSqlObjectRegistry:
    schema_version: int
    canonicalization_version: int
    source_head: str
    creation_order: tuple[RetainedSqlObject, ...]
    registry_digest: str


@dataclass(frozen=True)
class SchemaExclusionItem:
    key: CanonicalObjectKey
    definition: Mapping[str, JsonValue]
    definition_digest: str
    reason_code: str
    source_revision: str
    live_reference_count: int
    expected_in_clean_baseline: bool


@dataclass(frozen=True)
class SchemaExclusionManifest:
    canonicalization_version: int
    schema_family: str
    source_head: str
    source_structural_fingerprint: str
    normalized_structural_fingerprint: str
    reference_scan_digest: str
    objects: tuple[SchemaExclusionItem, ...]
    deviation_evidence_digest: str
    manifest_digest: str

    @property
    def object_keys(self) -> tuple[CanonicalObjectKey, ...]:
        return tuple(item.key for item in self.objects)


@dataclass(frozen=True)
class PreSquashSchemaSnapshot:
    schema_family: str
    source_head: str
    source_structural_fingerprint: str
    normalized_structural_fingerprint: str
    legacy_business_row_count: int
    known_inert_seed_row_count: int
    source_document: CanonicalSchemaDocument
    normalized_application_document: CanonicalSchemaDocument
    manifest_digest: str

    @property
    def snapshot_digest(self) -> str:
        return self.manifest_digest


DEFAULT_MANIFEST_ROOT = Path(__file__).resolve().parent / "manifests"
DEFAULT_SQL_OBJECT_REGISTRY_PATH = (
    DEFAULT_MANIFEST_ROOT / "pre_ga_v1-sql-objects.json"
)
DEFAULT_EXCLUSION_MANIFEST_PATH = (
    DEFAULT_MANIFEST_ROOT / "pre_ga_v1-exclusions.json"
)
DEFAULT_PRE_SQUASH_SNAPSHOT_PATH = (
    DEFAULT_MANIFEST_ROOT / "pre_ga_v1-pre-squash-schema.json"
)

_LEGACY_IMPORT = "app.assistant.migration"
_SQL_KINDS = frozenset({"function", "trigger", "view", "materialized_view"})
_SQL_KIND_ORDER = {
    "function": 0,
    "view": 1,
    "materialized_view": 2,
    "trigger": 3,
}


def _sql_creation_sort_key(key: CanonicalObjectKey) -> tuple[int, str, str, str]:
    if key.kind == "trigger":
        return _SQL_KIND_ORDER[key.kind], key.schema, key.qualifier, key.name
    return _SQL_KIND_ORDER[key.kind], key.schema, key.name, key.qualifier


def ordered_retained_sql_objects(
    document: CanonicalSchemaDocument,
) -> tuple[CanonicalSchemaObject, ...]:
    return tuple(
        sorted(
            (item for item in document.objects if item.key.kind in _SQL_KINDS),
            key=lambda item: _sql_creation_sort_key(item.key),
        )
    )


def build_exclusion_items(
    document: CanonicalSchemaDocument,
) -> tuple[CanonicalSchemaObject, ...]:
    """Discover only the exact, reviewed 27-object Legacy closure."""
    by_key = {item.key: item for item in document.objects}
    expected = tuple(
        CanonicalObjectKey(*parts) for parts in expected_legacy_object_keys()
    )
    missing = sorted(set(expected) - set(by_key))
    if missing:
        raise SchemaManifestError("legacy_exclusion_object_missing")

    discovered = tuple(by_key[key] for key in expected)
    if tuple(item.key for item in discovered) != tuple(sorted(expected)):
        raise SchemaManifestError("legacy_exclusion_allowlist_mismatch")

    expected_set = set(expected)
    unexpected_attached_triggers = tuple(
        item.key
        for item in document.objects
        if item.key.kind == "trigger"
        and item.key.qualifier in LEGACY_TABLE_NAMES
        and item.key not in expected_set
    )
    if unexpected_attached_triggers:
        raise SchemaManifestError("legacy_exclusion_allowlist_mismatch")

    excluded_function_keys = {
        key for key in expected_set if key.kind == "function"
    }
    for item in document.objects:
        if item.key.kind != "trigger" or item.key in expected_set:
            continue
        raw_function = item.definition.get("function")
        if not isinstance(raw_function, Mapping):
            raise SchemaManifestError("retained_sql_object_invalid")
        function_key = CanonicalObjectKey(
            "function",
            str(raw_function.get("schema", "")),
            str(raw_function.get("name", "")),
            str(raw_function.get("identityArguments", "")),
        )
        if function_key in excluded_function_keys:
            raise SchemaManifestError("legacy_exclusion_live_dependency")
    return discovered


def _resolved_import_targets(
    node: ast.Import | ast.ImportFrom,
    *,
    path: Path,
    app_root: Path,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)

    if node.level:
        relative = path.relative_to(app_root)
        package_parts = ["app", *relative.parent.parts]
        parents_to_remove = node.level - 1
        if parents_to_remove >= len(package_parts):
            raise SchemaManifestError("legacy_reference_scan_failed")
        if parents_to_remove:
            package_parts = package_parts[:-parents_to_remove]
        module_parts = package_parts
        if node.module:
            module_parts = [*module_parts, *node.module.split(".")]
    else:
        module_parts = (node.module or "").split(".")

    prefix = ".".join(part for part in module_parts if part)
    return tuple(
        f"{prefix}.{alias.name}" if prefix else alias.name
        for alias in node.names
    )


def scan_live_legacy_imports(app_root: Path) -> tuple[str, ...]:
    """Return stable AST import references outside the retirement boundary."""
    app_root = app_root.resolve()
    retirement_boundary = app_root / "assistant" / "migration"
    hits: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        if path.is_relative_to(retirement_boundary):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            raise SchemaManifestError("legacy_reference_scan_failed") from exc
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for target in _resolved_import_targets(
                node,
                path=path,
                app_root=app_root,
            ):
                if target == _LEGACY_IMPORT or target.startswith(
                    f"{_LEGACY_IMPORT}."
                ):
                    relative = path.relative_to(app_root).as_posix()
                    hits.append(f"{relative}:{node.lineno}:{target}")
    return tuple(sorted(hits))


def reference_scan_digest(scan_result: Sequence[str]) -> str:
    if any(not isinstance(item, str) for item in scan_result):
        raise SchemaManifestError("legacy_reference_scan_failed")
    normalized = tuple(sorted(scan_result))
    if len(normalized) != len(set(normalized)):
        raise SchemaManifestError("legacy_reference_scan_failed")
    return sha256_canonical_json(list(normalized))


def _quoted(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _required_sql_definition(item: CanonicalSchemaObject) -> str:
    raw = item.definition.get("definition")
    if not isinstance(raw, str) or not raw.strip():
        raise SchemaManifestError("retained_sql_object_invalid")
    return raw


def _trigger_dependencies(item: CanonicalSchemaObject) -> list[CanonicalObjectKey]:
    raw_table = item.definition.get("table")
    raw_function = item.definition.get("function")
    if not isinstance(raw_table, Mapping) or not isinstance(raw_function, Mapping):
        raise SchemaManifestError("retained_sql_object_invalid")
    try:
        function = CanonicalObjectKey(
            "function",
            raw_function["schema"],
            raw_function["name"],
            raw_function.get("identityArguments", ""),
        )
        table = CanonicalObjectKey(
            "table",
            raw_table["schema"],
            raw_table["name"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaManifestError("retained_sql_object_invalid") from exc
    return [function, table]


def renderable_sql_object(
    item: CanonicalSchemaObject,
    *,
    dependencies: Sequence[CanonicalObjectKey] | None = None,
) -> dict[str, JsonValue]:
    """Render one definition-locked SQL object for deterministic recreation."""
    if item.key.kind not in _SQL_KINDS:
        raise SchemaManifestError("retained_sql_object_kind_unsupported")

    if item.key.kind in {"function", "trigger"}:
        create_sql = _required_sql_definition(item)
    elif item.key.kind == "view":
        definition = _required_sql_definition(item)
        create_sql = (
            f"CREATE VIEW {_quoted(item.key.schema)}.{_quoted(item.key.name)} "
            f"AS {definition}"
        )
    else:
        definition = _required_sql_definition(item)
        create_sql = (
            f"CREATE MATERIALIZED VIEW {_quoted(item.key.schema)}."
            f"{_quoted(item.key.name)} AS {definition} WITH NO DATA"
        )

    resolved_dependencies = list(dependencies or ())
    if dependencies is None and item.key.kind == "trigger":
        resolved_dependencies = _trigger_dependencies(item)
    if any(not isinstance(key, CanonicalObjectKey) for key in resolved_dependencies):
        raise SchemaManifestError("retained_sql_object_invalid")
    if item.key.kind != "trigger":
        resolved_dependencies = sorted(resolved_dependencies)
        if len(resolved_dependencies) != len(set(resolved_dependencies)):
            raise SchemaManifestError("retained_sql_object_invalid")

    return {
        "key": item.key.to_payload(),
        "canonicalDefinition": item.definition,
        "definitionDigest": item.definition_digest,
        "createSql": create_sql,
        "dependencies": [key.to_payload() for key in resolved_dependencies],
    }


def _reject_float(_value: str) -> None:
    raise ValueError("floats are not permitted")


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_float,
            parse_constant=_reject_float,
            object_pairs_hook=_reject_duplicate_members,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SchemaManifestError("schema_manifest_invalid") from exc
    if not isinstance(raw, Mapping):
        raise SchemaManifestError("schema_manifest_invalid")
    return raw


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_exact_int(value: Any, expected: int) -> bool:
    return type(value) is int and value == expected


def _parse_object_key(payload: Any, *, safe_code: str) -> CanonicalObjectKey:
    if not isinstance(payload, Mapping):
        raise SchemaManifestError(safe_code)
    _require_exact_keys(
        payload,
        frozenset({"kind", "schema", "name", "qualifier"}),
        safe_code=safe_code,
    )
    try:
        return CanonicalObjectKey.from_payload(payload)
    except ValueError as exc:
        raise SchemaManifestError(safe_code) from exc


def parse_schema_document(payload: Any) -> CanonicalSchemaDocument:
    if not isinstance(payload, Mapping):
        raise SchemaManifestError("schema_document_invalid")
    _require_exact_keys(
        payload,
        frozenset({"canonicalizationVersion", "postgresMajor", "objects"}),
        safe_code="schema_document_invalid",
    )
    raw_objects = payload.get("objects")
    if not isinstance(raw_objects, Sequence) or isinstance(raw_objects, (str, bytes)):
        raise SchemaManifestError("schema_document_invalid")
    if not _is_exact_int(payload.get("canonicalizationVersion"), 1):
        raise SchemaManifestError("schema_document_invalid")
    postgres_major = payload.get("postgresMajor")
    if type(postgres_major) is not int or postgres_major <= 0:
        raise SchemaManifestError("schema_document_invalid")
    objects: list[CanonicalSchemaObject] = []
    try:
        for raw_object in raw_objects:
            if not isinstance(raw_object, Mapping):
                raise TypeError
            _require_exact_keys(
                raw_object,
                frozenset({"key", "definition"}),
                safe_code="schema_document_invalid",
            )
            definition = raw_object["definition"]
            if not isinstance(definition, Mapping):
                raise TypeError
            objects.append(
                CanonicalSchemaObject(
                    _parse_object_key(
                        raw_object["key"],
                        safe_code="schema_document_invalid",
                    ),
                    definition,
                )
            )
        return CanonicalSchemaDocument(
            canonicalization_version=payload["canonicalizationVersion"],
            postgres_major=payload["postgresMajor"],
            objects=tuple(objects),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaManifestError("schema_document_invalid") from exc


def _require_exact_keys(
    payload: Mapping[str, Any],
    expected: frozenset[str],
    *,
    safe_code: str,
) -> None:
    if set(payload) != expected:
        raise SchemaManifestError(safe_code)


def _parse_registry_item(payload: Any) -> RetainedSqlObject:
    if not isinstance(payload, Mapping):
        raise SchemaManifestError("sql_object_registry_invalid")
    _require_exact_keys(
        payload,
        frozenset(
            {
                "key",
                "canonicalDefinition",
                "definitionDigest",
                "createSql",
                "dependencies",
            }
        ),
        safe_code="sql_object_registry_invalid",
    )
    try:
        key = _parse_object_key(
            payload["key"],
            safe_code="sql_object_registry_invalid",
        )
        canonical_definition = payload["canonicalDefinition"]
        if not isinstance(canonical_definition, Mapping):
            raise TypeError
        canonical_object = CanonicalSchemaObject(key, canonical_definition)
        definition_digest = payload["definitionDigest"]
        create_sql = payload["createSql"]
        raw_dependencies = payload["dependencies"]
        if not isinstance(definition_digest, str) or not isinstance(create_sql, str):
            raise TypeError
        if not create_sql.strip():
            raise TypeError
        if not isinstance(raw_dependencies, Sequence) or isinstance(
            raw_dependencies, (str, bytes)
        ):
            raise TypeError
        dependencies = tuple(
            _parse_object_key(raw, safe_code="sql_object_registry_invalid")
            for raw in raw_dependencies
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaManifestError("sql_object_registry_invalid") from exc
    if key.kind not in _SQL_KINDS:
        raise SchemaManifestError("sql_object_registry_invalid")
    if canonical_object.definition_digest != definition_digest:
        raise SchemaManifestError("sql_object_definition_digest_mismatch")
    expected_rendering = renderable_sql_object(canonical_object)
    if expected_rendering["createSql"] != create_sql:
        raise SchemaManifestError("sql_object_create_sql_mismatch")
    if key.kind == "trigger":
        expected_dependencies = tuple(
            _parse_object_key(raw, safe_code="sql_object_registry_invalid")
            for raw in expected_rendering["dependencies"]
        )
        if dependencies != expected_dependencies:
            raise SchemaManifestError("sql_object_dependency_mismatch")
    elif dependencies != tuple(sorted(set(dependencies))):
        raise SchemaManifestError("sql_object_dependency_mismatch")
    return RetainedSqlObject(
        key=key,
        canonical_definition=canonical_object.definition,
        definition_digest=definition_digest,
        create_sql=create_sql,
        dependencies=dependencies,
    )


def load_retained_sql_object_registry(
    path: Path = DEFAULT_SQL_OBJECT_REGISTRY_PATH,
) -> RetainedSqlObjectRegistry:
    raw = _load_json(path)
    _require_exact_keys(
        raw,
        frozenset(
            {
                "schemaVersion",
                "canonicalizationVersion",
                "sourceHead",
                "objects",
                "registryDigest",
            }
        ),
        safe_code="sql_object_registry_invalid",
    )
    claimed_digest = raw.get("registryDigest")
    digest_payload = {key: value for key, value in raw.items() if key != "registryDigest"}
    if (
        not isinstance(claimed_digest, str)
        or sha256_canonical_json(digest_payload) != claimed_digest
    ):
        raise SchemaManifestError("sql_object_registry_digest_mismatch")
    if (
        not _is_exact_int(raw.get("schemaVersion"), 1)
        or not _is_exact_int(raw.get("canonicalizationVersion"), 1)
        or raw.get("sourceHead") != PRE_SQUASH_HEAD
    ):
        raise SchemaManifestError("sql_object_registry_invalid")
    raw_objects = raw.get("objects")
    if not isinstance(raw_objects, Sequence) or isinstance(raw_objects, (str, bytes)):
        raise SchemaManifestError("sql_object_registry_invalid")
    objects = tuple(_parse_registry_item(item) for item in raw_objects)
    keys = tuple(item.key for item in objects)
    if len(keys) != len(set(keys)):
        raise SchemaManifestError("sql_object_registry_invalid")
    expected_order = tuple(
        sorted(objects, key=lambda item: _sql_creation_sort_key(item.key))
    )
    if objects != expected_order:
        raise SchemaManifestError("sql_object_registry_invalid")
    return RetainedSqlObjectRegistry(
        schema_version=1,
        canonicalization_version=1,
        source_head=PRE_SQUASH_HEAD,
        creation_order=objects,
        registry_digest=claimed_digest,
    )


def install_retained_sql_objects(connection: Connection) -> None:
    """Install and re-validate the committed retained SQL registry."""
    registry = load_retained_sql_object_registry()
    try:
        before = PostgresCatalogReader(connection).read_document()
    except CatalogReadError as exc:
        raise SqlObjectRegistryError(exc.safe_code) from exc
    available = {item.key for item in before.objects}

    if any(
        sha256_canonical_json(item.canonical_definition)
        != item.definition_digest
        for item in registry.creation_order
    ):
        raise SqlObjectRegistryError("sql_object_definition_digest_mismatch")
    registry_keys = {item.key for item in registry.creation_order}
    if registry_keys & available:
        raise SqlObjectRegistryError("sql_object_collision")

    for item in registry.creation_order:
        if any(dependency not in available for dependency in item.dependencies):
            raise SqlObjectRegistryError("sql_object_dependency_missing")
        try:
            connection.execute(text(item.create_sql))
        except SQLAlchemyError as exc:
            raise SqlObjectRegistryError("sql_object_install_failed") from exc
        available.add(item.key)

    try:
        after = PostgresCatalogReader(connection).read_document()
    except CatalogReadError as exc:
        raise SqlObjectRegistryError(exc.safe_code) from exc
    installed_by_key = {item.key: item for item in after.objects}
    for item in registry.creation_order:
        actual = installed_by_key.get(item.key)
        if actual is None:
            raise SqlObjectRegistryError("sql_object_install_missing")
        if actual.definition_digest != item.definition_digest:
            raise SqlObjectRegistryError("sql_object_install_drift")


def _parse_exclusion_item(payload: Any) -> SchemaExclusionItem:
    if not isinstance(payload, Mapping):
        raise SchemaManifestError("exclusion_manifest_invalid")
    _require_exact_keys(
        payload,
        frozenset(
            {
                "key",
                "definition",
                "definitionDigest",
                "reasonCode",
                "sourceRevision",
                "liveReferenceCount",
                "expectedInCleanBaseline",
            }
        ),
        safe_code="exclusion_manifest_invalid",
    )
    try:
        key = _parse_object_key(
            payload["key"],
            safe_code="exclusion_manifest_invalid",
        )
        definition = payload["definition"]
        if not isinstance(definition, Mapping):
            raise TypeError
        item = CanonicalSchemaObject(key, definition)
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaManifestError("exclusion_manifest_invalid") from exc
    definition_digest = payload.get("definitionDigest")
    if not _valid_sha256(definition_digest):
        raise SchemaManifestError("exclusion_manifest_invalid")
    if item.definition_digest != definition_digest:
        raise SchemaManifestError("exclusion_definition_digest_mismatch")
    if (
        payload.get("reasonCode")
        != "unpublished_plan10_legacy_runtime_evidence"
        or payload.get("sourceRevision") != "6417df0243be"
        or not _is_exact_int(payload.get("liveReferenceCount"), 0)
        or payload.get("expectedInCleanBaseline") is not False
    ):
        raise SchemaManifestError("exclusion_manifest_invalid")
    return SchemaExclusionItem(
        key=key,
        definition=item.definition,
        definition_digest=definition_digest,
        reason_code="unpublished_plan10_legacy_runtime_evidence",
        source_revision="6417df0243be",
        live_reference_count=0,
        expected_in_clean_baseline=False,
    )


def load_exclusion_manifest(
    path: Path = DEFAULT_EXCLUSION_MANIFEST_PATH,
) -> SchemaExclusionManifest:
    raw = _load_json(path)
    _require_exact_keys(
        raw,
        frozenset(
            {
                "schemaVersion",
                "canonicalizationVersion",
                "schemaFamily",
                "sourceHead",
                "sourceStructuralFingerprint",
                "normalizedStructuralFingerprint",
                "referenceScanDigest",
                "objects",
                "deviationEvidenceDigest",
                "manifestDigest",
            }
        ),
        safe_code="exclusion_manifest_invalid",
    )
    claimed_digest = raw.get("manifestDigest")
    digest_payload = {key: value for key, value in raw.items() if key != "manifestDigest"}
    if (
        not _valid_sha256(claimed_digest)
        or sha256_canonical_json(digest_payload) != claimed_digest
    ):
        raise SchemaManifestError("exclusion_manifest_digest_mismatch")
    if (
        not _is_exact_int(raw.get("schemaVersion"), 1)
        or not _is_exact_int(raw.get("canonicalizationVersion"), 1)
        or raw.get("schemaFamily") != SCHEMA_FAMILY
        or raw.get("sourceHead") != PRE_SQUASH_HEAD
    ):
        raise SchemaManifestError("exclusion_manifest_invalid")
    for digest_name in (
        "sourceStructuralFingerprint",
        "normalizedStructuralFingerprint",
        "referenceScanDigest",
        "deviationEvidenceDigest",
    ):
        if not _valid_sha256(raw.get(digest_name)):
            raise SchemaManifestError("exclusion_manifest_invalid")
    raw_objects = raw.get("objects")
    if not isinstance(raw_objects, Sequence) or isinstance(raw_objects, (str, bytes)):
        raise SchemaManifestError("exclusion_manifest_invalid")
    objects = tuple(_parse_exclusion_item(item) for item in raw_objects)
    expected_keys = tuple(
        CanonicalObjectKey(*parts) for parts in expected_legacy_object_keys()
    )
    if tuple(item.key for item in objects) != expected_keys:
        raise SchemaManifestError("exclusion_manifest_allowlist_mismatch")
    return SchemaExclusionManifest(
        canonicalization_version=1,
        schema_family=SCHEMA_FAMILY,
        source_head=PRE_SQUASH_HEAD,
        source_structural_fingerprint=raw["sourceStructuralFingerprint"],
        normalized_structural_fingerprint=raw["normalizedStructuralFingerprint"],
        reference_scan_digest=raw["referenceScanDigest"],
        objects=objects,
        deviation_evidence_digest=raw["deviationEvidenceDigest"],
        manifest_digest=claimed_digest,
    )


def load_pre_squash_snapshot(
    path: Path = DEFAULT_PRE_SQUASH_SNAPSHOT_PATH,
) -> PreSquashSchemaSnapshot:
    raw = _load_json(path)
    _require_exact_keys(
        raw,
        frozenset(
            {
                "schemaVersion",
                "canonicalizationVersion",
                "schemaFamily",
                "sourceHead",
                "sourceStructuralFingerprint",
                "normalizedStructuralFingerprint",
                "legacyBusinessRowCount",
                "knownInertSeedRowCount",
                "sourceDocument",
                "normalizedApplicationDocument",
                "snapshotDigest",
            }
        ),
        safe_code="pre_squash_snapshot_invalid",
    )
    claimed_digest = raw.get("snapshotDigest")
    digest_payload = {key: value for key, value in raw.items() if key != "snapshotDigest"}
    if (
        not _valid_sha256(claimed_digest)
        or sha256_canonical_json(digest_payload) != claimed_digest
    ):
        raise SchemaManifestError("pre_squash_snapshot_digest_mismatch")
    if (
        not _is_exact_int(raw.get("schemaVersion"), 1)
        or not _is_exact_int(raw.get("canonicalizationVersion"), 1)
        or raw.get("schemaFamily") != SCHEMA_FAMILY
        or raw.get("sourceHead") != PRE_SQUASH_HEAD
        or not _is_exact_int(raw.get("legacyBusinessRowCount"), 0)
        or not _is_exact_int(raw.get("knownInertSeedRowCount"), 1)
    ):
        raise SchemaManifestError("pre_squash_snapshot_invalid")
    source = parse_schema_document(raw.get("sourceDocument"))
    normalized = parse_schema_document(raw.get("normalizedApplicationDocument"))
    source_fingerprint = raw.get("sourceStructuralFingerprint")
    normalized_fingerprint = raw.get("normalizedStructuralFingerprint")
    if (
        not _valid_sha256(source_fingerprint)
        or not _valid_sha256(normalized_fingerprint)
        or structural_fingerprint(source) != source_fingerprint
        or structural_fingerprint(normalized) != normalized_fingerprint
    ):
        raise SchemaManifestError("pre_squash_snapshot_fingerprint_mismatch")
    return PreSquashSchemaSnapshot(
        schema_family=SCHEMA_FAMILY,
        source_head=PRE_SQUASH_HEAD,
        source_structural_fingerprint=source_fingerprint,
        normalized_structural_fingerprint=normalized_fingerprint,
        legacy_business_row_count=0,
        known_inert_seed_row_count=1,
        source_document=source,
        normalized_application_document=normalized,
        manifest_digest=claimed_digest,
    )


def validate_manifest_set(
    exclusion_path: Path,
    snapshot_path: Path,
    registry_path: Path,
) -> tuple[
    SchemaExclusionManifest,
    PreSquashSchemaSnapshot,
    RetainedSqlObjectRegistry,
]:
    exclusions = load_exclusion_manifest(exclusion_path)
    snapshot = load_pre_squash_snapshot(snapshot_path)
    registry = load_retained_sql_object_registry(registry_path)
    if (
        exclusions.source_structural_fingerprint
        != snapshot.source_structural_fingerprint
        or exclusions.normalized_structural_fingerprint
        != snapshot.normalized_structural_fingerprint
    ):
        raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    expected_excluded = set(exclusions.object_keys)
    source_by_key = {item.key: item for item in snapshot.source_document.objects}
    normalized_by_key = {
        item.key: item for item in snapshot.normalized_application_document.objects
    }
    if not expected_excluded <= set(source_by_key):
        raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    if expected_excluded & set(normalized_by_key):
        raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    expected_normalized = tuple(
        item
        for item in snapshot.source_document.objects
        if item.key not in expected_excluded
    )
    if expected_normalized != snapshot.normalized_application_document.objects:
        raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    for exclusion in exclusions.objects:
        if source_by_key[exclusion.key].definition_digest != exclusion.definition_digest:
            raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    expected_sql = tuple(
        item
        for item in snapshot.normalized_application_document.objects
        if item.key.kind in _SQL_KINDS
    )
    expected_sql_by_key = {item.key: item for item in expected_sql}
    if set(expected_sql_by_key) != {item.key for item in registry.creation_order}:
        raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    for item in registry.creation_order:
        expected = expected_sql_by_key[item.key]
        if expected.definition_digest != item.definition_digest:
            raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
        if any(dependency not in normalized_by_key for dependency in item.dependencies):
            raise SchemaManifestError("schema_manifest_cross_reference_mismatch")
    return exclusions, snapshot, registry
