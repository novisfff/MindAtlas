#!/usr/bin/env python3
"""Capture or verify the first supported pre-GA PostgreSQL schema contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schema.canonical import (  # noqa: E402
    canonical_json_bytes,
    normalize_document,
    sha256_canonical_json,
    structural_fingerprint,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader  # noqa: E402
from app.schema.contracts import PRE_SQUASH_HEAD, SCHEMA_FAMILY  # noqa: E402
from app.schema.exclusions import LEGACY_TABLE_NAMES  # noqa: E402
from app.schema.sql_objects import (  # noqa: E402
    DEFAULT_MANIFEST_ROOT,
    SchemaManifestError,
    build_exclusion_items,
    ordered_retained_sql_objects,
    reference_scan_digest,
    renderable_sql_object,
    scan_live_legacy_imports,
    validate_manifest_set,
)


class CaptureError(RuntimeError):
    """Bounded capture failure safe for stderr and automation."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_CONTROL_TABLE = "assistant_runtime_rollout_control"
_DEVIATION_PATH = (
    BACKEND_ROOT.parent
    / "docs"
    / "superpowers"
    / "evidence"
    / "2026-07-28-pre-ga-clean-baseline-deviation.md"
)
_FILENAMES = (
    "pre_ga_v1-exclusions.json",
    "pre_ga_v1-pre-squash-schema.json",
    "pre_ga_v1-sql-objects.json",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _read_database_url(env_name: str) -> str:
    if _ENV_NAME.fullmatch(env_name) is None:
        raise CaptureError("database_url_env_invalid")
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise CaptureError("database_url_missing")
    return value


def _require_pre_squash_head(connection) -> None:  # noqa: ANN001
    rows = connection.execute(
        text("SELECT version_num FROM alembic_version ORDER BY version_num")
    ).scalars()
    heads = tuple(str(item) for item in rows)
    if heads != (PRE_SQUASH_HEAD,):
        raise CaptureError("pre_squash_head_mismatch")


def _require_inert_legacy_state(connection) -> None:  # noqa: ANN001
    business_tables = tuple(
        table_name
        for table_name in LEGACY_TABLE_NAMES
        if table_name != _CONTROL_TABLE
    )
    for table_name in business_tables:
        count = int(
            connection.execute(
                text(f'SELECT count(*) FROM "{table_name}"')
            ).scalar_one()
        )
        if count != 0:
            raise CaptureError("legacy_exclusion_data_present")


    controls = connection.execute(
        text(
            """
            SELECT singleton_key, active_rollout_revision_id, state_revision
            FROM assistant_runtime_rollout_control
            """
        )
    ).mappings().all()
    if len(controls) != 1:
        raise CaptureError("legacy_exclusion_data_present")
    control = controls[0]
    if (
        control["singleton_key"] != "singleton"
        or control["active_rollout_revision_id"] is not None
        or control["state_revision"] != 0
    ):
        raise CaptureError("legacy_exclusion_data_present")


def _require_exclusion_dependency_closure(connection) -> None:  # noqa: ANN001
    row = connection.execute(
        text(
            """
            WITH excluded_tables AS (
                SELECT cls.oid
                FROM pg_catalog.pg_class AS cls
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = 'public'
                  AND cls.relname = ANY(:excluded_tables)
                  AND cls.relkind IN ('r', 'p')
            ),
            excluded_function AS (
                SELECT proc.oid
                FROM pg_catalog.pg_proc AS proc
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
                WHERE ns.nspname = 'public'
                  AND proc.proname = 'mindatlas_reject_plan10_immutable_mutation'
                  AND pg_catalog.pg_get_function_identity_arguments(proc.oid) = ''
            ),
            excluded_row_types AS (
                SELECT typ.oid
                FROM pg_catalog.pg_type AS typ
                JOIN excluded_tables AS table_ref ON table_ref.oid = typ.typrelid
            ),
            violations AS (
                SELECT 1
                FROM pg_catalog.pg_constraint AS con
                JOIN pg_catalog.pg_class AS source_table
                  ON source_table.oid = con.conrelid
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_table.relnamespace
                JOIN excluded_tables AS target_table
                  ON target_table.oid = con.confrelid
                WHERE con.contype = 'f'
                  AND source_ns.nspname = 'public'
                  AND NOT (source_table.relname = ANY(:excluded_tables))

                UNION ALL

                SELECT 1
                FROM pg_catalog.pg_rewrite AS rewrite
                JOIN pg_catalog.pg_class AS source_view
                  ON source_view.oid = rewrite.ev_class
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_view.relnamespace
                JOIN pg_catalog.pg_depend AS dep
                  ON dep.classid = 'pg_rewrite'::regclass
                 AND dep.objid = rewrite.oid
                JOIN excluded_tables AS target_table
                  ON dep.refclassid = 'pg_class'::regclass
                 AND dep.refobjid = target_table.oid
                WHERE source_ns.nspname = 'public'
                  AND source_view.relkind IN ('v', 'm')
                  AND source_view.oid <> target_table.oid

                UNION ALL

                SELECT 1
                FROM pg_catalog.pg_rewrite AS rewrite
                JOIN pg_catalog.pg_class AS source_view
                  ON source_view.oid = rewrite.ev_class
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_view.relnamespace
                JOIN pg_catalog.pg_depend AS dep
                  ON dep.classid = 'pg_rewrite'::regclass
                 AND dep.objid = rewrite.oid
                JOIN excluded_function AS target_function
                  ON dep.refclassid = 'pg_proc'::regclass
                 AND dep.refobjid = target_function.oid
                WHERE source_ns.nspname = 'public'
                  AND source_view.relkind IN ('v', 'm')

                UNION ALL

                SELECT 1
                FROM pg_catalog.pg_depend AS dep
                JOIN pg_catalog.pg_class AS source_sequence
                  ON dep.classid = 'pg_class'::regclass
                 AND dep.objid = source_sequence.oid
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_sequence.relnamespace
                JOIN excluded_tables AS target_table
                  ON dep.refclassid = 'pg_class'::regclass
                 AND dep.refobjid = target_table.oid
                WHERE source_ns.nspname = 'public'
                  AND source_sequence.relkind = 'S'
                  AND dep.deptype IN ('a', 'i')

                UNION ALL

                SELECT 1
                FROM pg_catalog.pg_depend AS dep
                JOIN pg_catalog.pg_proc AS source_function
                  ON dep.classid = 'pg_proc'::regclass
                 AND dep.objid = source_function.oid
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_function.pronamespace
                LEFT JOIN excluded_tables AS target_table
                  ON dep.refclassid = 'pg_class'::regclass
                 AND dep.refobjid = target_table.oid
                LEFT JOIN excluded_function AS target_function
                  ON dep.refclassid = 'pg_proc'::regclass
                 AND dep.refobjid = target_function.oid
                LEFT JOIN excluded_row_types AS target_type
                  ON dep.refclassid = 'pg_type'::regclass
                 AND dep.refobjid = target_type.oid
                WHERE source_ns.nspname = 'public'
                  AND source_function.oid NOT IN (SELECT oid FROM excluded_function)
                  AND (
                      target_table.oid IS NOT NULL
                      OR target_function.oid IS NOT NULL
                      OR target_type.oid IS NOT NULL
                  )

                UNION ALL

                SELECT 1
                FROM pg_catalog.pg_trigger AS trg
                JOIN pg_catalog.pg_class AS source_table
                  ON source_table.oid = trg.tgrelid
                JOIN pg_catalog.pg_namespace AS source_ns
                  ON source_ns.oid = source_table.relnamespace
                JOIN excluded_function AS target_function
                  ON target_function.oid = trg.tgfoid
                WHERE NOT trg.tgisinternal
                  AND source_ns.nspname = 'public'
                  AND NOT (source_table.relname = ANY(:excluded_tables))
            )
            SELECT 1 FROM violations
            LIMIT 1
            """
        ),
        {"excluded_tables": list(LEGACY_TABLE_NAMES)},
    ).first()
    if row is not None:
        raise CaptureError("legacy_exclusion_live_dependency")


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CaptureError("deviation_evidence_unavailable") from exc


def _exclusion_item_payload(item) -> dict[str, object]:  # noqa: ANN001
    return {
        "key": item.key.to_payload(),
        "definition": item.definition,
        "definitionDigest": item.definition_digest,
        "reasonCode": "unpublished_plan10_legacy_runtime_evidence",
        "sourceRevision": "6417df0243be",
        "liveReferenceCount": 0,
        "expectedInCleanBaseline": False,
    }


def _build_outputs(database_url: str) -> dict[str, bytes]:
    engine = None
    try:
        engine = create_engine(
            _sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                _require_pre_squash_head(connection)
                _require_inert_legacy_state(connection)
                _require_exclusion_dependency_closure(connection)
                source_document = PostgresCatalogReader(connection).read_document()
                exclusion_items = build_exclusion_items(source_document)
    except CaptureError:
        raise
    except SchemaManifestError as exc:
        raise CaptureError(exc.safe_code) from exc
    except CatalogReadError as exc:
        raise CaptureError(exc.safe_code) from exc
    except SQLAlchemyError as exc:
        raise CaptureError("schema_source_unavailable") from exc
    finally:
        if engine is not None:
            engine.dispose()

    live_references = scan_live_legacy_imports(BACKEND_ROOT / "app")
    if live_references:
        raise CaptureError("legacy_live_reference_present")
    scan_digest = reference_scan_digest(live_references)

    exclusion_objects = [
        {
            "key": item.key.to_payload(),
            "definitionDigest": item.definition_digest,
        }
        for item in exclusion_items
    ]
    try:
        normalized_document = normalize_document(
            source_document,
            manifest={"objects": exclusion_objects},
            side="old",
        )
    except Exception as exc:
        safe_code = getattr(exc, "safe_code", "schema_normalization_failed")
        raise CaptureError(safe_code) from exc

    source_fingerprint = structural_fingerprint(source_document)
    normalized_fingerprint = structural_fingerprint(normalized_document)
    exclusion_payload = {
        "schemaVersion": 1,
        "canonicalizationVersion": source_document.canonicalization_version,
        "schemaFamily": SCHEMA_FAMILY,
        "sourceHead": PRE_SQUASH_HEAD,
        "sourceStructuralFingerprint": source_fingerprint,
        "normalizedStructuralFingerprint": normalized_fingerprint,
        "referenceScanDigest": scan_digest,
        "objects": [_exclusion_item_payload(item) for item in exclusion_items],
        "deviationEvidenceDigest": _sha256_file(_DEVIATION_PATH),
    }
    exclusion_manifest = {
        **exclusion_payload,
        "manifestDigest": sha256_canonical_json(exclusion_payload),
    }

    retained_sql = ordered_retained_sql_objects(normalized_document)
    registry_payload = {
        "schemaVersion": 1,
        "canonicalizationVersion": source_document.canonicalization_version,
        "sourceHead": PRE_SQUASH_HEAD,
        "objects": [renderable_sql_object(item) for item in retained_sql],
    }
    registry = {
        **registry_payload,
        "registryDigest": sha256_canonical_json(registry_payload),
    }

    snapshot_payload = {
        "schemaVersion": 1,
        "canonicalizationVersion": source_document.canonicalization_version,
        "schemaFamily": SCHEMA_FAMILY,
        "sourceHead": PRE_SQUASH_HEAD,
        "sourceStructuralFingerprint": source_fingerprint,
        "normalizedStructuralFingerprint": normalized_fingerprint,
        "legacyBusinessRowCount": 0,
        "knownInertSeedRowCount": 1,
        "sourceDocument": source_document.to_payload(),
        "normalizedApplicationDocument": normalized_document.to_payload(),
    }
    snapshot = {
        **snapshot_payload,
        "snapshotDigest": sha256_canonical_json(snapshot_payload),
    }
    return {
        "pre_ga_v1-exclusions.json": canonical_json_bytes(exclusion_manifest) + b"\n",
        "pre_ga_v1-pre-squash-schema.json": canonical_json_bytes(snapshot) + b"\n",
        "pre_ga_v1-sql-objects.json": canonical_json_bytes(registry) + b"\n",
    }


def _repository_manifest_root() -> Path:
    root = DEFAULT_MANIFEST_ROOT
    resolved = root.resolve()
    if root.is_symlink() or not resolved.is_relative_to(BACKEND_ROOT.resolve()):
        raise CaptureError("manifest_destination_invalid")
    return root


def _destination_paths(root: Path) -> dict[str, Path]:
    return {name: root / name for name in _FILENAMES}


def _validate_destinations(paths: dict[str, Path]) -> None:
    if tuple(paths) != _FILENAMES:
        raise CaptureError("manifest_destination_invalid")
    for path in paths.values():
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise CaptureError("manifest_destination_invalid")


def _write_temporary_files(
    root: Path,
    outputs: dict[str, bytes],
) -> dict[str, Path]:
    temporary: dict[str, Path] = {}
    try:
        for name in _FILENAMES:
            fd, raw_path = tempfile.mkstemp(
                prefix=f".{name}.",
                suffix=".tmp",
                dir=root,
            )
            path = Path(raw_path)
            temporary[name] = path
            with os.fdopen(fd, "wb") as handle:
                handle.write(outputs[name])
                handle.flush()
                os.fsync(handle.fileno())
        validate_manifest_set(
            temporary["pre_ga_v1-exclusions.json"],
            temporary["pre_ga_v1-pre-squash-schema.json"],
            temporary["pre_ga_v1-sql-objects.json"],
        )
        return temporary
    except Exception:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise


def _check_outputs(paths: dict[str, Path], outputs: dict[str, bytes]) -> None:
    try:
        validate_manifest_set(
            paths["pre_ga_v1-exclusions.json"],
            paths["pre_ga_v1-pre-squash-schema.json"],
            paths["pre_ga_v1-sql-objects.json"],
        )
        if any(paths[name].read_bytes() != outputs[name] for name in _FILENAMES):
            raise CaptureError("schema_capture_drift")
    except CaptureError:
        raise
    except (OSError, SchemaManifestError) as exc:
        raise CaptureError("schema_capture_drift") from exc


def _write_outputs(paths: dict[str, Path], outputs: dict[str, bytes]) -> None:
    existing = tuple(path.exists() for path in paths.values())
    if any(existing):
        if not all(existing):
            raise CaptureError("schema_capture_drift")
        _check_outputs(paths, outputs)
        return

    root = next(iter(paths.values())).parent
    temporary = _write_temporary_files(root, outputs)
    installed: list[Path] = []
    try:
        for name in _FILENAMES:
            os.replace(temporary[name], paths[name])
            installed.append(paths[name])
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        for path in installed:
            path.unlink(missing_ok=True)
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise


def main(
    argv: list[str] | None = None,
    *,
    _test_manifest_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--database-url-env", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    try:
        outputs = _build_outputs(_read_database_url(args.database_url_env))
        root = (
            _test_manifest_root.resolve()
            if _test_manifest_root is not None
            else _repository_manifest_root()
        )
        if args.write:
            root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise CaptureError("manifest_destination_invalid")
        paths = _destination_paths(root)
        _validate_destinations(paths)
        if args.write:
            _write_outputs(paths, outputs)
        else:
            _check_outputs(paths, outputs)
        snapshot_payload = json.loads(
            outputs["pre_ga_v1-pre-squash-schema.json"].decode("utf-8")
        )
        print(
            "schema_capture_ok exclusions=27 legacy_business_rows=0 "
            "known_inert_seed_rows=1 "
            f"normalized_fingerprint={snapshot_payload['normalizedStructuralFingerprint']} "
            "manifests=3"
        )
        return 0
    except CaptureError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2
    except SchemaManifestError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2
    except OSError:
        print("manifest_write_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
