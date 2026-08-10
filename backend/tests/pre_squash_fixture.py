"""Build a reviewed pre-squash PostgreSQL fixture without old migrations.

The live Alembic directory intentionally contains only the clean root.  Some
capture and rebaseline proofs still need a database that represents the
captured pre-squash source.  This module reconstructs that source from the
committed catalog manifest after installing the live root; it never imports,
copies, or executes an archived migration.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.schema.canonical import canonical_json_bytes, structural_fingerprint
from app.schema.contracts import PRE_SQUASH_HEAD
from app.schema.sql_objects import load_exclusion_manifest, load_pre_squash_snapshot
from tests.schema_baseline_support import (
    _sqlalchemy_url,
    upgrade_clean_root_checked,
)


_SUPPORTED_COLUMN_TYPE = re.compile(
    r"(?:uuid|integer|boolean|jsonb?|text|timestamp with time zone|"
    r"character varying\([0-9]+\))\Z"
)


class PreSquashFixtureError(RuntimeError):
    """Bounded fixture reconstruction failure for PostgreSQL CI output."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


def _quote(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("fixture identifier is invalid")
    return '"' + identifier.replace('"', '""') + '"'


def _constraint_name(constraint: Mapping[str, Any]) -> str:
    name = constraint.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("fixture constraint name is invalid")
    return name


def _constraint_definition(constraint: Mapping[str, Any]) -> str:
    definition = constraint.get("definition")
    if not isinstance(definition, str) or ";" in definition:
        raise ValueError("fixture constraint definition is invalid")
    if constraint.get("deferrable"):
        definition += " DEFERRABLE"
        if constraint.get("initiallyDeferred"):
            definition += " INITIALLY DEFERRED"
    return definition


def render_table_ddl(
    schema: str,
    table_name: str,
    definition: Mapping[str, Any],
) -> str:
    """Render the table/column portion of one captured catalog object."""
    columns = definition.get("columns")
    constraints = definition.get("constraints")
    if not isinstance(columns, list) or not isinstance(constraints, list):
        raise ValueError("fixture table definition is invalid")
    rendered_columns: list[str] = []
    for column in sorted(columns, key=lambda item: int(item["ordinal"])):
        if not isinstance(column, Mapping):
            raise ValueError("fixture column definition is invalid")
        name = column.get("name")
        formatted_type = column.get("formattedType")
        if (
            not isinstance(name, str)
            or not isinstance(formatted_type, str)
            or _SUPPORTED_COLUMN_TYPE.fullmatch(formatted_type) is None
        ):
            raise ValueError("unsupported fixture column type")
        if column.get("generatedKind") or column.get("identityKind"):
            raise ValueError("fixture generated/identity columns are unsupported")
        parts = [_quote(name), formatted_type]
        default = column.get("defaultExpression")
        if default is not None:
            if not isinstance(default, str) or ";" in default:
                raise ValueError("fixture column default is invalid")
            parts.extend(("DEFAULT", default))
        if column.get("nullable") is False:
            parts.append("NOT NULL")
        rendered_columns.append(" ".join(parts))
    rendered_constraints = [
        f"CONSTRAINT {_quote(_constraint_name(constraint))} "
        f"{_constraint_definition(constraint)}"
        for constraint in constraints
        if isinstance(constraint, Mapping)
        and constraint.get("type") != "f"
    ]
    return (
        f"CREATE TABLE {_quote(schema)}.{_quote(table_name)} (\n  "
        + ",\n  ".join([*rendered_columns, *rendered_constraints])
        + "\n)"
    )


def _render_constraint(schema: str, table_name: str, constraint: Mapping[str, Any]) -> str:
    definition = _constraint_definition(constraint)
    return (
        f"ALTER TABLE {_quote(schema)}.{_quote(table_name)} "
        f"ADD CONSTRAINT {_quote(_constraint_name(constraint))} {definition}"
    )


def _drop_identity_controls(connection) -> None:  # noqa: ANN001
    connection.execute(
        text(
            'DROP TRIGGER IF EXISTS "trg_mindatlas_schema_identity_guard" '
            'ON "public"."mindatlas_schema_identity"'
        )
    )
    connection.execute(
        text('DROP FUNCTION IF EXISTS "public"."mindatlas_guard_schema_identity_mutation"()')
    )
    connection.execute(
        text('DROP TABLE IF EXISTS "public"."mindatlas_schema_identity"')
    )


def _install_legacy_objects(connection) -> None:  # noqa: ANN001
    manifest = load_exclusion_manifest()
    table_items = [item for item in manifest.objects if item.key.kind == "table"]
    for item in sorted(table_items, key=lambda value: value.key.name):
        connection.exec_driver_sql(
            render_table_ddl(item.key.schema, item.key.name, item.definition)
        )

    # Foreign keys may refer to a legacy table that sorts later, so add every
    # captured constraint only after all legacy tables exist.
    for item in sorted(table_items, key=lambda value: value.key.name):
        constraints = item.definition.get("constraints", [])
        for constraint in constraints:
            if constraint.get("type") != "f":
                continue
            connection.exec_driver_sql(
                _render_constraint(item.key.schema, item.key.name, constraint)
            )

    for item in manifest.objects:
        if item.key.kind != "function":
            continue
        definition = item.definition.get("definition")
        if not isinstance(definition, str):
            raise ValueError("fixture function definition is invalid")
        connection.exec_driver_sql(definition)

    for item in table_items:
        indexes = item.definition.get("indexes", [])
        constraint_names = {
            _constraint_name(constraint)
            for constraint in item.definition.get("constraints", [])
        }
        for index in indexes:
            if not isinstance(index, Mapping):
                raise ValueError("fixture index definition is invalid")
            name = index.get("name")
            definition = index.get("definition")
            if not isinstance(name, str) or not isinstance(definition, str):
                raise ValueError("fixture index definition is invalid")
            # PostgreSQL creates these indexes while adding PK/UNIQUE
            # constraints.  Recreating them would produce duplicate names.
            if bool(index.get("primary")) or name in constraint_names:
                continue
            connection.exec_driver_sql(definition)

    for item in manifest.objects:
        if item.key.kind != "trigger":
            continue
        definition = item.definition.get("definition")
        if not isinstance(definition, str):
            raise ValueError("fixture trigger definition is invalid")
        connection.exec_driver_sql(definition)

    connection.execute(
        text(
            "INSERT INTO \"public\".\"assistant_runtime_rollout_control\" "
            "(id, singleton_key, active_rollout_revision_id, state_revision, "
            "created_at, updated_at) VALUES "
            "('00000000-0000-0000-0000-000000000001', 'singleton', NULL, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )


def install_pre_squash_fixture(
    database_url: str,
    *,
    deployment_class: str = "rehearsal",
    app_env: str = "test",
    build_revision: str = "test-pre-squash-fixture",
) -> None:
    """Install and validate the committed pre-squash source fixture."""
    try:
        upgrade_clean_root_checked(
            database_url,
            deployment_class=deployment_class,
            app_env=app_env,
            build_revision=build_revision,
        )
    except Exception:
        raise PreSquashFixtureError("clean_root_upgrade_failed") from None
    try:
        engine = create_engine(_sqlalchemy_url(database_url), future=True)
    except Exception:
        raise PreSquashFixtureError("fixture_database_unavailable") from None
    try:
        try:
            with engine.begin() as connection:
                _drop_identity_controls(connection)
                _install_legacy_objects(connection)
                connection.execute(
                    text(
                        "UPDATE \"public\".\"alembic_version\" "
                        "SET version_num = :revision"
                    ),
                    {"revision": PRE_SQUASH_HEAD},
                )
                database_name = connection.scalar(text("SELECT current_database()"))
                if not isinstance(database_name, str):
                    raise ValueError("fixture database identity is unavailable")
                connection.exec_driver_sql(
                    f'COMMENT ON DATABASE {_quote(database_name)} IS %s',
                    (f"mindatlas:deployment_class={deployment_class}",),
                )
        except Exception:
            raise PreSquashFixtureError("legacy_install_failed") from None

        snapshot = load_pre_squash_snapshot()
        try:
            with engine.connect() as connection:
                from app.schema.catalog import PostgresCatalogReader

                actual = PostgresCatalogReader(connection).read_document()
        except Exception:
            raise PreSquashFixtureError("fixture_catalog_read_failed") from None
        if (
            structural_fingerprint(actual)
            != snapshot.source_structural_fingerprint
            or canonical_json_bytes(actual.to_payload())
            != canonical_json_bytes(snapshot.source_document.to_payload())
        ):
            raise PreSquashFixtureError("fixture_snapshot_mismatch")
    finally:
        engine.dispose()


__all__ = [
    "PreSquashFixtureError",
    "install_pre_squash_fixture",
    "render_table_ddl",
]
