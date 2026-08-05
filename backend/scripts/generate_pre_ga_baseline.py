#!/usr/bin/env python3
"""Generate or verify the deterministic first pre-GA Alembic root."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import tempfile

from alembic.autogenerate import produce_migrations, render_python_code
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.assistant.durable.codec import (  # noqa: E402
    CURRENT_CHECKPOINT_CODEC_VERSION,
)
from app.assistant.durable.worker_registry import (  # noqa: E402
    RUNTIME_CONTRACT_VERSION,
    default_capability_feature_digest,
)
from app.assistant.runtime.system_seed.expected import (  # noqa: E402
    SEED_CONTRACT_DIGEST,
)
from app.model_registry import load_all_live_models  # noqa: E402
from app.operator_auth.constants import (  # noqa: E402
    OPERATOR_AUTH_CONTRACT_VERSION,
)
from app.schema.application_contract import (  # noqa: E402
    LogicalApplicationContractError,
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import (  # noqa: E402
    SchemaComparisonError,
    canonical_json_bytes,
    compare_documents,
    sha256_canonical_json,
    structural_fingerprint,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader  # noqa: E402
from app.schema.contracts import (  # noqa: E402
    CLEAN_ROOT_REVISION,
    SCHEMA_FAMILY,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    DeploymentClass,
)
from app.schema.exclusions import LEGACY_TABLE_NAMES  # noqa: E402
from app.schema.identity import (  # noqa: E402
    SCHEMA_IDENTITY_CONTROL_FINGERPRINT,
    SchemaIdentityError,
    read_schema_identity,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import (  # noqa: E402
    SchemaManifestError,
    install_retained_sql_objects,
    load_retained_sql_object_registry,
    validate_manifest_set,
)

DEFAULT_STAGED_ROOT = (
    BACKEND_ROOT
    / "alembic"
    / "baseline_staging"
    / "pre_ga_v1_0001_clean_baseline.py"
)
_EXPECTED_MANIFEST_FILENAME = "pre_ga_v1-expected.json"


class BaselineGenerationError(RuntimeError):
    """Bounded generator failure safe for stderr and automation."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class GeneratorContext:
    database_url: str
    manifest_root: Path = (
        BACKEND_ROOT / "app" / "schema" / "manifests"
    )
    live_versions_dir: Path = BACKEND_ROOT / "alembic" / "versions"


_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_ALLOWED_EMPTY_OBJECTS = {
    ("extension", "pg_catalog", "plpgsql", ""),
    ("namespace", "public", "public", ""),
}
_HEADER = '''"""Install the first supported MindAtlas pre-GA schema directly."""

from __future__ import annotations

import hashlib
import json
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pre_ga_v1_0001"
down_revision = None
branch_labels = ("pre_ga_v1",)
depends_on = None

SCHEMA_FAMILY = "pre_ga_v1"
SCHEMA_REVISION = "pre_ga_v1_0001"
TEST_DOWNGRADE_ACK = "MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE"

'''


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _require_python_runtime() -> None:
    if sys.version_info[:2] != (3, 11):
        raise BaselineGenerationError("generator_python_version_unsupported")


def _manifest_paths(root: Path) -> dict[str, Path]:
    return {
        name: root / name
        for name in (
            "pre_ga_v1-exclusions.json",
            "pre_ga_v1-pre-squash-schema.json",
            "pre_ga_v1-sql-objects.json",
            "pre_ga_v1-clean-application-contract.json",
        )
    }


def _validate_manifests(context: GeneratorContext):  # noqa: ANN201
    paths = _manifest_paths(context.manifest_root)
    try:
        validate_manifest_set(
            paths["pre_ga_v1-exclusions.json"],
            paths["pre_ga_v1-pre-squash-schema.json"],
            paths["pre_ga_v1-sql-objects.json"],
        )
        return load_logical_application_contract(
            paths["pre_ga_v1-clean-application-contract.json"],
            snapshot_path=paths["pre_ga_v1-pre-squash-schema.json"],
            exclusion_path=paths["pre_ga_v1-exclusions.json"],
        )
    except (SchemaManifestError, LogicalApplicationContractError) as exc:
        raise BaselineGenerationError("generator_manifest_invalid") from exc


def _require_no_exclusion_tables() -> None:
    load_all_live_models()
    if set(LEGACY_TABLE_NAMES) & set(Base.metadata.tables):
        raise BaselineGenerationError("generator_live_metadata_exclusion_present")


def _revision_literals(path: Path) -> tuple[object, object]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise BaselineGenerationError("live_revision_scan_failed") from exc
    values: dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        ):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except (TypeError, ValueError) as exc:
                raise BaselineGenerationError("live_revision_scan_failed") from exc
    if set(values) != {"revision", "down_revision"}:
        raise BaselineGenerationError("live_revision_scan_failed")
    return values["revision"], values["down_revision"]


def require_single_live_root(live_versions_dir: Path) -> tuple[str, ...]:
    try:
        paths = tuple(sorted(live_versions_dir.glob("*.py")))
    except OSError as exc:
        raise BaselineGenerationError("live_revision_scan_failed") from exc
    revisions: list[str] = []
    roots: list[str] = []
    for path in paths:
        if path.name == "__init__.py":
            continue
        revision, down_revision = _revision_literals(path)
        if not isinstance(revision, str) or not revision:
            raise BaselineGenerationError("live_revision_scan_failed")
        revisions.append(revision)
        if down_revision is None:
            roots.append(revision)
    if len(revisions) != len(set(revisions)) or len(roots) != 1:
        raise BaselineGenerationError("live_revision_roots_invalid")
    return tuple(sorted(roots))


def require_safe_output_destination(
    output: Path,
    live_versions_dir: Path,
    live_roots: tuple[str, ...],
) -> None:
    if output.resolve().parent != live_versions_dir.resolve():
        return
    if live_roots != (CLEAN_ROOT_REVISION,) or not output.is_file():
        raise BaselineGenerationError("live_version_destination_forbidden")
    revision, down_revision = _revision_literals(output)
    if revision != CLEAN_ROOT_REVISION or down_revision is not None:
        raise BaselineGenerationError("live_version_destination_forbidden")


def _catalog_key(item) -> tuple[str, str, str, str]:  # noqa: ANN001
    return (item.key.kind, item.key.schema, item.key.name, item.key.qualifier)


def _require_empty_database(connection) -> None:  # noqa: ANN001
    try:
        document = PostgresCatalogReader(connection).read_document()
    except CatalogReadError as exc:
        raise BaselineGenerationError("generator_catalog_unavailable") from exc
    if {_catalog_key(item) for item in document.objects} != _ALLOWED_EMPTY_OBJECTS:
        raise BaselineGenerationError("generator_database_not_empty")


def _prove_model_reference(connection, expected) -> None:  # noqa: ANN001
    transaction = connection.begin()
    try:
        Base.metadata.create_all(connection)
        install_retained_sql_objects(connection)
        raw_actual = PostgresCatalogReader(connection).read_document()
        actual = project_logical_application_document(
            raw_actual,
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )
        compare_documents(
            expected.logical_application_document,
            actual,
            exclusions=None,
        )
        if structural_fingerprint(actual) != expected.logical_application_fingerprint:
            raise BaselineGenerationError("model_reference_fingerprint_mismatch")
    except BaselineGenerationError:
        raise
    except (
        CatalogReadError,
        LogicalApplicationContractError,
        SchemaComparisonError,
        SchemaManifestError,
        SQLAlchemyError,
    ) as exc:
        raise BaselineGenerationError("model_reference_mismatch") from exc
    finally:
        transaction.rollback()


def _normalize_rendered_body(body: str) -> str:
    # ``render_python_code`` targets Alembic's Mako template and therefore
    # doubles percent signs. This generator embeds the body directly, so undo
    # that template-layer escaping before writing executable migration code.
    body = body.replace("%%", "%")
    return "\n".join(line.rstrip() for line in body.splitlines()).rstrip()


def _indent_statement(statement: str) -> str:
    return f"    {statement}"


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _retained_upgrade_lines() -> tuple[str, ...]:
    registry = load_retained_sql_object_registry()
    return tuple(
        _indent_statement(f"op.execute({item.create_sql!r})")
        for item in registry.creation_order
    )


def _deferred_foreign_key_constraints():  # noqa: ANN201
    constraints = tuple(
        constraint
        for table in Base.metadata.tables.values()
        for constraint in table.foreign_key_constraints
        if constraint.use_alter
    )
    if any(constraint.name is None for constraint in constraints):
        raise BaselineGenerationError("deferred_foreign_key_unnamed")
    return tuple(
        sorted(
            constraints,
            key=lambda constraint: (
                constraint.table.fullname,
                str(constraint.name),
            ),
        )
    )


def _deferred_foreign_key_upgrade_lines() -> tuple[str, ...]:
    lines: list[str] = []
    for constraint in _deferred_foreign_key_constraints():
        elements = tuple(constraint.elements)
        referred_tables = {element.column.table for element in elements}
        if len(referred_tables) != 1:
            raise BaselineGenerationError("deferred_foreign_key_invalid")
        referred_table = next(iter(referred_tables))
        positional = (
            repr(constraint.name),
            repr(constraint.table.name),
            repr(referred_table.name),
            repr([element.parent.name for element in elements]),
            repr([element.column.name for element in elements]),
        )
        keywords: list[str] = []
        if constraint.table.schema is not None:
            keywords.append(f"source_schema={constraint.table.schema!r}")
        if referred_table.schema is not None:
            keywords.append(f"referent_schema={referred_table.schema!r}")
        for option in (
            "onupdate",
            "ondelete",
            "deferrable",
            "initially",
            "match",
        ):
            value = getattr(constraint, option)
            if value is not None:
                keywords.append(f"{option}={value!r}")
        arguments = ", ".join((*positional, *keywords))
        lines.append(_indent_statement(f"op.create_foreign_key({arguments})"))
    return tuple(lines)


def _deferred_foreign_key_downgrade_lines() -> tuple[str, ...]:
    return tuple(
        _indent_statement(
            "op.drop_constraint("
            f"{constraint.name!r}, {constraint.table.name!r}, "
            "type_='foreignkey'"
            + (
                f", schema={constraint.table.schema!r}"
                if constraint.table.schema is not None
                else ""
            )
            + ")"
        )
        for constraint in reversed(_deferred_foreign_key_constraints())
    )


def _retained_downgrade_lines() -> tuple[str, ...]:
    registry = load_retained_sql_object_registry()
    lines: list[str] = []
    for item in reversed(registry.creation_order):
        if item.key.kind != "trigger":
            continue
        statement = (
            f"DROP TRIGGER {_quote_identifier(item.key.name)} ON "
            f"{_quote_identifier(item.key.schema)}."
            f"{_quote_identifier(item.key.qualifier)}"
        )
        lines.append(_indent_statement(f"op.execute({statement!r})"))
    for item in reversed(registry.creation_order):
        if item.key.kind != "function":
            continue
        statement = (
            f"DROP FUNCTION {_quote_identifier(item.key.schema)}."
            f"{_quote_identifier(item.key.name)}({item.key.qualifier})"
        )
        lines.append(_indent_statement(f"op.execute({statement!r})"))
    return tuple(lines)


_SCHEMA_IDENTITY_GUARD_SQL = """CREATE FUNCTION mindatlas_guard_schema_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected_revision text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'schema identity deletion is forbidden';
  END IF;
  IF NEW.singleton_key <> OLD.singleton_key
     OR NEW.schema_family <> OLD.schema_family
     OR NEW.deployment_class <> OLD.deployment_class
     OR NEW.created_at <> OLD.created_at
     OR NEW.identity_contract_version < OLD.identity_contract_version THEN
    RAISE EXCEPTION 'schema identity immutable field changed';
  END IF;
  expected_revision := current_setting(
    'mindatlas.schema_migration_revision', true
  );
  IF expected_revision IS NULL OR expected_revision = ''
     OR NEW.schema_revision <> expected_revision
     OR NEW.schema_revision = OLD.schema_revision
     OR NEW.updated_at <= OLD.updated_at THEN
    RAISE EXCEPTION 'schema identity advance is not migration-authorized';
  END IF;
  RETURN NEW;
END;
$$"""

_SCHEMA_IDENTITY_TRIGGER_SQL = """CREATE TRIGGER trg_mindatlas_schema_identity_guard
BEFORE UPDATE OR DELETE ON mindatlas_schema_identity
FOR EACH ROW EXECUTE FUNCTION mindatlas_guard_schema_identity_mutation()"""


def _marker_upgrade_lines(expected) -> tuple[str, ...]:  # noqa: ANN001
    if CURRENT_CHECKPOINT_CODEC_VERSION != 3:
        raise BaselineGenerationError("checkpoint_codec_contract_invalid")
    feature_digest = default_capability_feature_digest()
    lines = (
        "    runtime_identity_payload = {",
        f'        "schemaFamily": {SCHEMA_FAMILY!r},',
        f'        "schemaRevision": {CLEAN_ROOT_REVISION!r},',
        "        \"structuralFingerprint\": "
        f"{expected.logical_application_fingerprint!r},",
        f'        "seedContractDigest": {SEED_CONTRACT_DIGEST!r},',
        '        "deploymentClass": deployment_class,',
        f'        "runtimeContractVersion": {RUNTIME_CONTRACT_VERSION!r},',
        f'        "checkpointCodecVersion": {CURRENT_CHECKPOINT_CODEC_VERSION!r},',
        f'        "capabilityFeatureDigest": {feature_digest!r},',
        "        \"operatorAuthContractVersion\": "
        f"{OPERATOR_AUTH_CONTRACT_VERSION!r},",
        "    }",
        "    runtime_identity_digest = hashlib.sha256(",
        "        json.dumps(",
        "            runtime_identity_payload,",
        "            sort_keys=True,",
        "            ensure_ascii=False,",
        "            separators=(\",\", \":\"),",
        '        ).encode("utf-8")',
        "    ).hexdigest()",
        "    op.create_table(",
        '        "mindatlas_schema_identity",',
        '        sa.Column("singleton_key", sa.String(32), primary_key=True, nullable=False),',
        '        sa.Column("schema_family", sa.String(32), nullable=False),',
        '        sa.Column("schema_revision", sa.String(64), nullable=False),',
        '        sa.Column("structural_fingerprint", sa.CHAR(64), nullable=False),',
        '        sa.Column("runtime_identity_digest", sa.CHAR(64), nullable=False),',
        '        sa.Column("seed_contract_digest", sa.CHAR(64), nullable=False),',
        '        sa.Column("deployment_class", sa.String(16), nullable=False),',
        '        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),',
        '        sa.Column("checkpoint_codec_version", sa.Integer(), nullable=False),',
        '        sa.Column("capability_feature_digest", sa.CHAR(64), nullable=False),',
        '        sa.Column("operator_auth_contract_version", sa.String(64), nullable=False),',
        '        sa.Column("identity_contract_version", sa.Integer(), nullable=False),',
        '        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),',
        '        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),',
        "        sa.CheckConstraint(",
        "            \"singleton_key = 'current'\",",
        '            name="ck_schema_identity_singleton",',
        "        ),",
        "        sa.CheckConstraint(",
        "            \"schema_family = 'pre_ga_v1'\",",
        '            name="ck_schema_identity_family",',
        "        ),",
        "        sa.CheckConstraint(",
        "            \"deployment_class IN ('development','rehearsal','production')\",",
        '            name="ck_schema_identity_deployment_class",',
        "        ),",
        "        sa.CheckConstraint(",
        "            \"structural_fingerprint ~ '^[0-9a-f]{64}$' \"",
        "            \"AND runtime_identity_digest ~ '^[0-9a-f]{64}$' \"",
        "            \"AND seed_contract_digest ~ '^[0-9a-f]{64}$' \"",
        "            \"AND capability_feature_digest ~ '^[0-9a-f]{64}$'\",",
        '            name="ck_schema_identity_digest_shapes",',
        "        ),",
        "        sa.CheckConstraint(",
        "            \"runtime_contract_version > 0 AND checkpoint_codec_version > 0 \"",
        "            \"AND identity_contract_version > 0\",",
        '            name="ck_schema_identity_positive_versions",',
        "        ),",
        "    )",
        f"    op.execute({_SCHEMA_IDENTITY_GUARD_SQL!r})",
        f"    op.execute({_SCHEMA_IDENTITY_TRIGGER_SQL!r})",
        "    op.get_bind().execute(",
        "        sa.text(",
        "            \"INSERT INTO mindatlas_schema_identity (\"",
        "            \"singleton_key, schema_family, schema_revision, \"",
        "            \"structural_fingerprint, runtime_identity_digest, \"",
        "            \"seed_contract_digest, deployment_class, \"",
        "            \"runtime_contract_version, checkpoint_codec_version, \"",
        "            \"capability_feature_digest, operator_auth_contract_version, \"",
        "            \"identity_contract_version, created_at, updated_at\"",
        "            \") VALUES (\"",
        "            \"'current', :family, :revision, :fingerprint, \"",
        "            \":runtime_identity_digest, :seed_digest, :deployment_class, \"",
        "            \":runtime_contract_version, :checkpoint_codec_version, \"",
        "            \":feature_digest, :operator_auth_version, 1, \"",
        "            \"CURRENT_TIMESTAMP, CURRENT_TIMESTAMP\"",
        "            \")\"",
        "        ),",
        "        {",
        f'            "family": {SCHEMA_FAMILY!r},',
        f'            "revision": {CLEAN_ROOT_REVISION!r},',
        "            \"fingerprint\": "
        f"{expected.logical_application_fingerprint!r},",
        '            "runtime_identity_digest": runtime_identity_digest,',
        f'            "seed_digest": {SEED_CONTRACT_DIGEST!r},',
        '            "deployment_class": deployment_class,',
        f'            "runtime_contract_version": {RUNTIME_CONTRACT_VERSION!r},',
        "            \"checkpoint_codec_version\": "
        f"{CURRENT_CHECKPOINT_CODEC_VERSION!r},",
        f'            "feature_digest": {feature_digest!r},',
        "            \"operator_auth_version\": "
        f"{OPERATOR_AUTH_CONTRACT_VERSION!r},",
        "        },",
        "    )",
    )
    return lines


def _marker_downgrade_lines() -> tuple[str, ...]:
    return (
        "    op.execute('DROP TRIGGER trg_mindatlas_schema_identity_guard '",
        "               'ON mindatlas_schema_identity')",
        "    op.execute('DROP FUNCTION mindatlas_guard_schema_identity_mutation()')",
        "    op.execute('DROP TABLE mindatlas_schema_identity')",
    )


def _enum_downgrade_lines() -> tuple[str, ...]:
    named_types: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            enum_type = column.type
            if not isinstance(enum_type, SQLAlchemyEnum) or not (
                enum_type.native_enum
            ):
                continue
            if not enum_type.name:
                raise BaselineGenerationError("native_enum_type_unnamed")
            schema = enum_type.schema or table.schema or "public"
            named_types.add((schema, enum_type.name))
    return tuple(
        _indent_statement(
            "op.execute("
            + repr(
                f"DROP TYPE {_quote_identifier(schema)}."
                f"{_quote_identifier(name)}"
            )
            + ")"
        )
        for schema, name in sorted(named_types)
    )


def _render_downgrade_guard() -> str:
    live_tables = tuple(sorted(Base.metadata.tables))
    return "\n".join(
        (
            "    if (",
            "        os.environ.get(\"APP_ENV\", \"\").strip() != \"test\"",
            "        or os.environ.get(TEST_DOWNGRADE_ACK, \"\").strip()",
            "        != \"I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION\"",
            "    ):",
            "        raise RuntimeError(\"schema_test_downgrade_forbidden\")",
            "    connection = op.get_bind()",
            f"    live_tables = {live_tables!r}",
            "    for table_name in live_tables:",
            "        quoted = table_name.replace(chr(34), chr(34) * 2)",
            "        count = connection.execute(",
            "            sa.text(f'SELECT count(*) FROM \"{quoted}\"')",
            "        ).scalar_one()",
            "        if int(count) != 0:",
            "            raise RuntimeError(\"schema_test_downgrade_nonempty\")",
        )
    )


def _render_revision(upgrade_body: str, downgrade_body: str, expected) -> bytes:  # noqa: ANN001
    upgrade_parts = [
        "def upgrade() -> None:",
        "    deployment_class = os.environ.get(\"MINDATLAS_DEPLOYMENT_CLASS\", \"\").strip()",
        "    if deployment_class not in {\"development\", \"rehearsal\", \"production\"}:",
        "        raise RuntimeError(\"schema_deployment_class_invalid\")",
        _normalize_rendered_body(upgrade_body),
        *_deferred_foreign_key_upgrade_lines(),
        *_retained_upgrade_lines(),
        *_marker_upgrade_lines(expected),
    ]
    downgrade_parts = [
        "def downgrade() -> None:",
        _render_downgrade_guard(),
        *_marker_downgrade_lines(),
        *_retained_downgrade_lines(),
        *_deferred_foreign_key_downgrade_lines(),
        _normalize_rendered_body(downgrade_body),
        *_enum_downgrade_lines(),
    ]
    source = (
        _HEADER
        + "\n".join(upgrade_parts)
        + "\n\n\n"
        + "\n".join(downgrade_parts)
        + "\n"
    )
    try:
        ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - defensive render boundary
        raise BaselineGenerationError("generated_revision_invalid") from exc
    return source.encode("utf-8")


def _render_from_empty_database(connection, expected) -> bytes:  # noqa: ANN001
    migration_context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "target_metadata": Base.metadata,
            "include_schemas": True,
        },
    )
    try:
        migration_script = produce_migrations(migration_context, Base.metadata)
        upgrade_body = render_python_code(
            migration_script.upgrade_ops,
            sqlalchemy_module_prefix="sa.",
            alembic_module_prefix="op.",
            migration_context=migration_context,
        )
        downgrade_body = render_python_code(
            migration_script.downgrade_ops,
            sqlalchemy_module_prefix="sa.",
            alembic_module_prefix="op.",
            migration_context=migration_context,
        )
    except Exception as exc:
        raise BaselineGenerationError("alembic_autogenerate_failed") from exc
    return _render_revision(upgrade_body, downgrade_body, expected)


def generate_baseline(context: GeneratorContext) -> bytes:
    """Prove live metadata then render one deterministic self-contained root."""
    _require_python_runtime()
    require_single_live_root(context.live_versions_dir)
    expected = _validate_manifests(context)
    _require_no_exclusion_tables()

    engine = create_engine(_sqlalchemy_url(context.database_url), future=True)
    try:
        with engine.connect() as connection:
            _require_empty_database(connection)
            connection.rollback()
            _prove_model_reference(connection, expected)
            _require_empty_database(connection)
            return _render_from_empty_database(connection, expected)
    except BaselineGenerationError:
        raise
    except SQLAlchemyError as exc:
        raise BaselineGenerationError("generator_database_unavailable") from exc
    finally:
        engine.dispose()


_CLEAN_ROOT_CONTROL_KEYS = frozenset(
    {
        CanonicalObjectKey("table", "public", "alembic_version"),
        CanonicalObjectKey(
            "table",
            "public",
            "mindatlas_schema_identity",
        ),
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


def _execute_generated_root(connection, content: bytes) -> None:  # noqa: ANN001
    try:
        namespace: dict[str, object] = {}
        exec(
            compile(content, "<generated-pre-ga-root>", "exec"),
            namespace,
        )
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                ")"
            )
        )
        previous = os.environ.get("MINDATLAS_DEPLOYMENT_CLASS")
        os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "development"
        try:
            with Operations.context(MigrationContext.configure(connection)):
                namespace["upgrade"]()
        finally:
            if previous is None:
                os.environ.pop("MINDATLAS_DEPLOYMENT_CLASS", None)
            else:
                os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = previous
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES (:revision)"
            ),
            {"revision": CLEAN_ROOT_REVISION},
        )
    except (
        AttributeError,
        KeyError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        SyntaxError,
        TypeError,
        ValueError,
    ):
        raise BaselineGenerationError("generated_root_execution_failed") from None


def generate_expected_manifest(context: GeneratorContext) -> bytes:
    """Execute the generated root and derive its committed identity contract."""
    content = generate_baseline(context)
    expected = _validate_manifests(context)
    engine = create_engine(_sqlalchemy_url(context.database_url), future=True)
    try:
        with engine.connect() as connection:
            _require_empty_database(connection)
            connection.rollback()
            transaction = connection.begin()
            try:
                _execute_generated_root(connection, content)
                raw = PostgresCatalogReader(connection).read_document()
                logical = project_logical_application_document(
                    raw,
                    control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
                )
                compare_documents(
                    expected.logical_application_document,
                    logical,
                    exclusions=None,
                )
                application_fingerprint = structural_fingerprint(logical)
                controls = tuple(
                    item
                    for item in raw.objects
                    if item.key in _CLEAN_ROOT_CONTROL_KEYS
                )
                if {item.key for item in controls} != _CLEAN_ROOT_CONTROL_KEYS:
                    raise BaselineGenerationError(
                        "schema_control_contract_missing"
                    )
                control_fingerprint = structural_fingerprint(
                    CanonicalSchemaDocument(1, raw.postgres_major, controls)
                )
                if (
                    control_fingerprint
                    != SCHEMA_IDENTITY_CONTROL_FINGERPRINT
                ):
                    raise BaselineGenerationError(
                        "schema_control_contract_drift"
                    )
                marker = read_schema_identity(connection)
                feature_digest = default_capability_feature_digest()
                if (
                    marker.schema_family != SCHEMA_FAMILY
                    or marker.schema_revision != CLEAN_ROOT_REVISION
                    or marker.structural_fingerprint
                    != application_fingerprint
                    or marker.seed_contract_digest != SEED_CONTRACT_DIGEST
                    or marker.deployment_class
                    is not DeploymentClass.DEVELOPMENT
                    or marker.runtime_contract_version
                    != RUNTIME_CONTRACT_VERSION
                    or marker.checkpoint_codec_version
                    != CURRENT_CHECKPOINT_CODEC_VERSION
                    or marker.capability_feature_digest != feature_digest
                    or marker.operator_auth_contract_version
                    != OPERATOR_AUTH_CONTRACT_VERSION
                    or marker.identity_contract_version
                    != SCHEMA_IDENTITY_CONTRACT_VERSION
                    or marker.runtime_identity_digest
                    != schema_runtime_identity_digest(
                        marker.to_identity_material()
                    )
                ):
                    raise BaselineGenerationError(
                        "marker_contract_mismatch"
                    )
                manifest_payload = {
                    "schemaVersion": 1,
                    "schemaFamily": SCHEMA_FAMILY,
                    "schemaRevision": CLEAN_ROOT_REVISION,
                    "applicationStructuralFingerprint": (
                        application_fingerprint
                    ),
                    "schemaIdentityControlFingerprint": control_fingerprint,
                    "seedContractDigest": SEED_CONTRACT_DIGEST,
                    "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
                    "checkpointCodecVersion": (
                        CURRENT_CHECKPOINT_CODEC_VERSION
                    ),
                    "capabilityFeatureDigest": (
                        feature_digest
                    ),
                    "operatorAuthContractVersion": (
                        OPERATOR_AUTH_CONTRACT_VERSION
                    ),
                    "canonicalizationVersion": 2,
                }
                manifest = {
                    **manifest_payload,
                    "manifestDigest": sha256_canonical_json(
                        manifest_payload
                    ),
                }
                result = canonical_json_bytes(manifest) + b"\n"
            except BaselineGenerationError:
                raise
            except (
                CatalogReadError,
                LogicalApplicationContractError,
                SchemaComparisonError,
                SchemaIdentityError,
                SQLAlchemyError,
            ):
                raise BaselineGenerationError(
                    "expected_manifest_generation_failed"
                ) from None
            finally:
                transaction.rollback()
            _require_empty_database(connection)
            connection.rollback()
            return result
    except BaselineGenerationError:
        raise
    except SQLAlchemyError as exc:
        raise BaselineGenerationError("generator_database_unavailable") from exc
    finally:
        engine.dispose()


def _read_database_url(env_name: str) -> str:
    if _ENV_NAME.fullmatch(env_name) is None:
        raise BaselineGenerationError("database_url_env_invalid")
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise BaselineGenerationError("database_url_missing")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_atomic(output: Path, content: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    backup: Path | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        if output.exists():
            backup_descriptor, backup_name = tempfile.mkstemp(
                prefix=f".{output.name}.",
                suffix=".rollback",
                dir=output.parent,
            )
            os.close(backup_descriptor)
            backup = Path(backup_name)
            backup.unlink()
            os.link(output, backup)

        os.replace(temporary, output)
        try:
            _fsync_directory(output.parent)
        except OSError:
            if backup is None:
                output.unlink(missing_ok=True)
            else:
                os.replace(backup, output)
                backup = None
            try:
                _fsync_directory(output.parent)
            except OSError:
                pass
            raise

        if backup is not None:
            try:
                backup.unlink()
            except OSError:
                pass
            backup = None
    finally:
        temporary.unlink(missing_ok=True)
        if backup is not None:
            backup.unlink(missing_ok=True)


def _canonical_output_path(output: Path) -> Path:
    try:
        return output.resolve()
    except RuntimeError:
        raise BaselineGenerationError("atomic_output_path_invalid") from None


def _write_atomic_group(items: tuple[tuple[Path, bytes], ...]) -> None:
    canonical_items = tuple(
        (_canonical_output_path(output), content) for output, content in items
    )
    if not canonical_items or len(
        {output for output, _ in canonical_items}
    ) != len(canonical_items):
        raise BaselineGenerationError("atomic_output_paths_collide")
    items = canonical_items
    records: list[dict[str, object]] = []
    replaced: set[Path] = set()
    parents: list[Path] = []
    try:
        for output, content in items:
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.parent not in parents:
                parents.append(output.parent)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{output.name}.",
                suffix=".tmp",
                dir=output.parent,
            )
            temporary = Path(temporary_name)
            backup: Path | None = None
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if output.exists():
                    backup_descriptor, backup_name = tempfile.mkstemp(
                        prefix=f".{output.name}.",
                        suffix=".rollback",
                        dir=output.parent,
                    )
                    os.close(backup_descriptor)
                    backup = Path(backup_name)
                    backup.unlink()
                    os.link(output, backup)
            except BaseException:
                temporary.unlink(missing_ok=True)
                if backup is not None:
                    backup.unlink(missing_ok=True)
                raise
            records.append(
                {
                    "output": output,
                    "temporary": temporary,
                    "backup": backup,
                }
            )

        for record in records:
            output = record["output"]
            temporary = record["temporary"]
            assert isinstance(output, Path)
            assert isinstance(temporary, Path)
            os.replace(temporary, output)
            record["temporary"] = None
            replaced.add(output)
        for parent in parents:
            _fsync_directory(parent)
    except BaseException:
        for record in reversed(records):
            output = record["output"]
            backup = record["backup"]
            assert isinstance(output, Path)
            if output not in replaced:
                continue
            if isinstance(backup, Path):
                os.replace(backup, output)
                record["backup"] = None
            else:
                output.unlink(missing_ok=True)
        for parent in parents:
            try:
                _fsync_directory(parent)
            except OSError:
                pass
        raise
    finally:
        for record in records:
            temporary = record["temporary"]
            backup = record["backup"]
            if isinstance(temporary, Path):
                temporary.unlink(missing_ok=True)
            if isinstance(backup, Path):
                backup.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--write-expected-manifest", action="store_true")
    parser.add_argument("--check-expected-manifest", action="store_true")
    args = parser.parse_args(argv)

    try:
        if (
            args.write_expected_manifest
            and (not args.write or args.check_expected_manifest)
        ) or (
            args.check_expected_manifest
            and (not args.check or args.write_expected_manifest)
        ):
            raise BaselineGenerationError("expected_manifest_mode_invalid")
        context = GeneratorContext(
            database_url=_read_database_url(args.database_url_env)
        )
        output = _canonical_output_path(args.output)
        expected_output: Path | None = None
        if args.write_expected_manifest or args.check_expected_manifest:
            expected_output = _canonical_output_path(
                context.manifest_root / _EXPECTED_MANIFEST_FILENAME
            )
            if output == expected_output:
                raise BaselineGenerationError(
                    "atomic_output_paths_collide"
                )
        live_roots = require_single_live_root(context.live_versions_dir)
        require_safe_output_destination(
            output,
            context.live_versions_dir,
            live_roots,
        )
        content = generate_baseline(context)
        expected_content: bytes | None = None
        if args.write_expected_manifest or args.check_expected_manifest:
            expected_content = generate_expected_manifest(context)
        if args.write:
            if args.write_expected_manifest:
                assert expected_content is not None
                assert expected_output is not None
                _write_atomic_group(
                    (
                        (output, content),
                        (expected_output, expected_content),
                    )
                )
            else:
                _write_atomic(output, content)
        else:
            try:
                existing = output.read_bytes()
            except OSError as exc:
                raise BaselineGenerationError("baseline_output_missing") from exc
            if existing != content:
                raise BaselineGenerationError("baseline_output_drift")
        if args.check_expected_manifest:
            assert expected_content is not None
            assert expected_output is not None
            try:
                existing_expected = expected_output.read_bytes()
            except OSError as exc:
                raise BaselineGenerationError(
                    "expected_manifest_output_missing"
                ) from exc
            if existing_expected != expected_content:
                raise BaselineGenerationError(
                    "expected_manifest_output_drift"
                )
        print(
            "pre_ga_baseline_ok "
            f"revision={CLEAN_ROOT_REVISION} family={SCHEMA_FAMILY}"
        )
        return 0
    except BaselineGenerationError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2
    except OSError:
        print("baseline_output_write_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
