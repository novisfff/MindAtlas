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
    r"(?:uuid|integer|bigint|boolean|jsonb?|text|bytea|date|timemode|"
    r"timestamp with time zone|character varying\([0-9]+\))\Z"
)


class PreSquashFixtureError(RuntimeError):
    """Bounded fixture reconstruction failure for PostgreSQL CI output."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


def _postgres_error_suffix(exc: Exception) -> str:
    """Return a bounded, non-sensitive PostgreSQL error discriminator."""
    original = getattr(exc, "orig", None)
    code = getattr(original, "pgcode", None) or getattr(original, "sqlstate", None)
    if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code):
        return code.lower()
    name = type(original).__name__ if original is not None else type(exc).__name__
    normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    message = str(original or exc).lower()
    # TypeErrors from a DBAPI adapter are useful to distinguish (for example,
    # percent-formatting vs. parameter-shape mistakes), while arbitrary SQL
    # text and server diagnostics must never escape this fixture boundary.
    message_token = re.sub(r"[^a-z0-9]+", "_", message).strip("_")
    if message_token and len(message_token) <= 48:
        return f"{normalized[:24]}_{message_token}"
    return normalized[:32] or "error"


def _snapshot_mismatch_code(actual, expected) -> str:  # noqa: ANN001
    """Classify a fixture mismatch without emitting catalog definitions."""
    actual_keys = tuple(item.key for item in actual.objects)
    expected_keys = tuple(item.key for item in expected.objects)
    if actual_keys != expected_keys:
        return "fixture_snapshot_keys_mismatch"
    for actual_item, expected_item in zip(actual.objects, expected.objects):
        if canonical_json_bytes(actual_item.definition) != canonical_json_bytes(
            expected_item.definition
        ):
            kind = re.sub(r"[^a-z0-9]+", "_", actual_item.key.kind.lower())
            name = re.sub(r"[^a-z0-9]+", "_", actual_item.key.name.lower())
            return f"fixture_snapshot_{kind}_{name}_mismatch"[:96]
    return "fixture_snapshot_fingerprint_mismatch"


def _quote(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("fixture identifier is invalid")
    return '"' + identifier.replace('"', '""') + '"'


def _exec_literal(connection, statement: str) -> None:  # noqa: ANN001
    """Execute committed DDL without DBAPI percent-parameter expansion."""
    connection.execution_options(no_parameters=True).exec_driver_sql(statement)


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


def _install_source_snapshot(connection, snapshot) -> None:  # noqa: ANN001
    """Materialize the committed pre-squash catalog, including raw ordinals."""
    # The clean root intentionally uses the version-2 logical contract and may
    # order columns differently from the historical physical catalog.  Build
    # the source fixture from the captured document itself so rebaseline tests
    # exercise the exact source fingerprint rather than a model approximation.
    for item in snapshot.source_document.objects:
        if item.key.kind != "enum":
            continue
        labels = item.definition.get("labels")
        if not isinstance(labels, list) or not all(
            isinstance(label, str) and "'" not in label for label in labels
        ):
            raise ValueError("fixture enum definition is invalid")
        label_sql = ", ".join("'" + label + "'" for label in labels)
        try:
            _exec_literal(
                connection,
                f"CREATE TYPE {_quote(item.key.schema)}.{_quote(item.key.name)} "
                f"AS ENUM ({label_sql})"
            )
        except Exception:
            raise PreSquashFixtureError(
                f"legacy_enum_{item.key.name}"
            ) from None

    sequence_items = [
        item
        for item in snapshot.source_document.objects
        if item.key.kind == "sequence"
    ]
    for item in sorted(sequence_items, key=lambda value: value.key.name):
        definition = item.definition
        try:
            sequence_type = str(definition["type"])
            start = int(definition["start"])
            increment = int(definition["increment"])
            minimum = int(definition["minimum"])
            maximum = int(definition["maximum"])
            cache = int(definition["cache"])
            cycle = "CYCLE" if definition.get("cycle") else "NO CYCLE"
            _exec_literal(
                connection,
                f"CREATE SEQUENCE {_quote(item.key.schema)}.{_quote(item.key.name)} "
                f"AS {sequence_type} START WITH {start} INCREMENT BY {increment} "
                f"MINVALUE {minimum} MAXVALUE {maximum} CACHE {cache} {cycle}"
            )
        except Exception:
            raise PreSquashFixtureError(
                f"legacy_sequence_{item.key.name}"
            ) from None

    table_items = [
        item
        for item in snapshot.source_document.objects
        if item.key.kind == "table"
    ]
    for item in sorted(table_items, key=lambda value: value.key.name):
        try:
            _exec_literal(
                connection,
                render_table_ddl(item.key.schema, item.key.name, item.definition)
            )
        except Exception as exc:
            raise PreSquashFixtureError(
                f"legacy_table_{item.key.name}_{_postgres_error_suffix(exc)}"
            ) from None

    # Foreign keys may refer to a table that sorts later, so add every captured
    # constraint only after all tables exist.
    for item in sorted(table_items, key=lambda value: value.key.name):
        constraints = item.definition.get("constraints", [])
        for constraint in constraints:
            if constraint.get("type") != "f":
                continue
            try:
                _exec_literal(
                    connection,
                    _render_constraint(item.key.schema, item.key.name, constraint)
                )
            except Exception as exc:
                raise PreSquashFixtureError(
                    f"legacy_fk_{constraint.get('name', 'unknown')}_"
                    f"{_postgres_error_suffix(exc)}"
                ) from None

    for item in snapshot.source_document.objects:
        if item.key.kind != "function":
            continue
        definition = item.definition.get("definition")
        if not isinstance(definition, str):
            raise ValueError("fixture function definition is invalid")
        try:
            # Function bodies may contain PostgreSQL ``RAISE`` placeholders
            # (``%``).  Bypass SQLAlchemy's DBAPI parameter mapping explicitly;
            # otherwise psycopg2 treats those literal placeholders as a Python
            # format string when an empty parameter mapping is supplied.
            _exec_literal(connection, definition)
        except Exception as exc:
            raise PreSquashFixtureError(
                f"legacy_function_{item.key.name}_{_postgres_error_suffix(exc)}"
            ) from None

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
            try:
                _exec_literal(connection, definition)
            except Exception as exc:
                raise PreSquashFixtureError(
                    f"legacy_index_{name}_{_postgres_error_suffix(exc)}"
                ) from None

    for item in snapshot.source_document.objects:
        if item.key.kind != "trigger":
            continue
        definition = item.definition.get("definition")
        if not isinstance(definition, str):
            raise ValueError("fixture trigger definition is invalid")
        try:
            _exec_literal(connection, definition)
        except Exception as exc:
            raise PreSquashFixtureError(
                f"legacy_trigger_{item.key.name}_{_postgres_error_suffix(exc)}"
            ) from None

    # The source catalog intentionally contains this one inert Plan 10 control
    # seed.  Rebaseline removes it as an allowlisted non-business artifact.
    connection.execute(
        text(
            "INSERT INTO \"public\".\"assistant_runtime_rollout_control\" "
            "(id, singleton_key, active_rollout_revision_id, state_revision, "
            "created_at, updated_at) VALUES "
            "('00000000-0000-0000-0000-000000000001', 'singleton', NULL, 0, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )

    # Restore the source revision control row exactly as a migration would.
    connection.execute(
        text(
            "INSERT INTO \"public\".\"alembic_version\" (version_num) "
            "VALUES (:revision)"
        ),
        {"revision": PRE_SQUASH_HEAD},
    )

    sequence_item = next(
        (
            item
            for item in sequence_items
            if item.key.name == "assistant_chat_run_event_id_seq"
        ),
        None,
    )
    if sequence_item is not None:
        owned_by = sequence_item.definition.get("ownedBy")
        if isinstance(owned_by, Mapping):
            try:
                _exec_literal(
                    connection,
                    f"ALTER SEQUENCE {_quote(sequence_item.key.schema)}."
                    f"{_quote(sequence_item.key.name)} OWNED BY "
                    f"{_quote(str(owned_by['schema']))}."
                    f"{_quote(str(owned_by['table']))}."
                    f"{_quote(str(owned_by['column']))}"
                )
            except Exception:
                raise PreSquashFixtureError(
                    f"legacy_sequence_owner_{sequence_item.key.name}"
                ) from None


def install_pre_squash_fixture(
    database_url: str,
    *,
    deployment_class: str = "rehearsal",
    app_env: str = "test",
    build_revision: str = "test-pre-squash-fixture",
) -> None:
    """Install and validate the committed pre-squash source fixture."""
    snapshot = load_pre_squash_snapshot()
    try:
        engine = create_engine(_sqlalchemy_url(database_url), future=True)
    except Exception:
        raise PreSquashFixtureError("fixture_database_unavailable") from None
    try:
        try:
            with engine.begin() as connection:
                _install_source_snapshot(connection, snapshot)
                database_name = connection.scalar(text("SELECT current_database()"))
                if not isinstance(database_name, str):
                    raise ValueError("fixture database identity is unavailable")
                connection.exec_driver_sql(
                    f'COMMENT ON DATABASE {_quote(database_name)} IS %s',
                    (f"mindatlas:deployment_class={deployment_class}",),
                )
        except PreSquashFixtureError:
            raise
        except Exception:
            raise PreSquashFixtureError("legacy_install_failed") from None

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
            raise PreSquashFixtureError(
                _snapshot_mismatch_code(actual, snapshot.source_document)
            )
    finally:
        engine.dispose()


__all__ = [
    "PreSquashFixtureError",
    "install_pre_squash_fixture",
    "render_table_ddl",
]
