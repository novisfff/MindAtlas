from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from app.schema.canonical import CanonicalObjectKey, structural_fingerprint
from app.schema.catalog import CatalogReadError, PostgresCatalogReader


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for catalog coverage",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture
def catalog_schema() -> Iterator[tuple[Connection, str, str]]:
    assert _POSTGRES_URL
    token = uuid.uuid4().hex[:12]
    schema = f"catalog_{token}"
    role = f"catalog_role_{token}"
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        connection.execute(text(f'CREATE ROLE "{role}"'))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text(f'CREATE TYPE "{schema}".mood AS ENUM (\'calm\', \'focused\')')
        )
        connection.execute(
            text(
                f'CREATE DOMAIN "{schema}".positive_integer AS integer '
                "NOT NULL CHECK (VALUE > 0)"
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE "{schema}".parent (
                    id bigint GENERATED ALWAYS AS IDENTITY,
                    code text NOT NULL,
                    next_id bigint GENERATED ALWAYS AS (id + 1) STORED,
                    mood "{schema}".mood NOT NULL DEFAULT 'calm',
                    CONSTRAINT pk_parent PRIMARY KEY (id),
                    CONSTRAINT uq_parent_code UNIQUE (code),
                    CONSTRAINT ck_parent_code_nonempty CHECK (length(code) > 0)
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE "{schema}".child (
                    id bigint NOT NULL,
                    parent_id bigint NOT NULL,
                    score "{schema}".positive_integer,
                    note text,
                    CONSTRAINT pk_child PRIMARY KEY (id),
                    CONSTRAINT fk_child_parent FOREIGN KEY (parent_id)
                        REFERENCES "{schema}".parent (id)
                        ON UPDATE CASCADE ON DELETE RESTRICT,
                    CONSTRAINT uq_child_parent_note UNIQUE (parent_id, note),
                    CONSTRAINT ck_child_note CHECK (note IS NULL OR length(note) < 200)
                )
                """
            )
        )
        connection.execute(
            text(
                f'CREATE SEQUENCE "{schema}".owned_counter AS integer '
                "INCREMENT BY 5 MINVALUE 10 MAXVALUE 10000 START WITH 10 CACHE 3 CYCLE"
            )
        )
        connection.execute(
            text(
                f'ALTER SEQUENCE "{schema}".owned_counter '
                f'OWNED BY "{schema}".child.id'
            )
        )
        connection.execute(
            text(
                f'CREATE INDEX ix_child_lower_note ON "{schema}".child '
                "((lower(note))) INCLUDE (score)"
            )
        )
        connection.execute(
            text(
                f'CREATE UNIQUE INDEX ux_child_positive_parent ON "{schema}".child '
                "(parent_id) WHERE score > 0"
            )
        )
        connection.execute(
            text(
                f'CREATE VIEW "{schema}".child_view WITH (security_barrier=true) AS '
                f'SELECT id, parent_id FROM "{schema}".child WHERE score > 0 '
                "WITH LOCAL CHECK OPTION"
            )
        )
        connection.execute(
            text(
                f'CREATE MATERIALIZED VIEW "{schema}".child_summary AS '
                f'SELECT parent_id, count(*) AS item_count FROM "{schema}".child '
                "GROUP BY parent_id WITH NO DATA"
            )
        )
        connection.execute(
            text(
                f"""
                CREATE FUNCTION "{schema}".audit_child_change()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $function$
                BEGIN
                    NEW.note := COALESCE(NEW.note, 'changed');
                    RETURN NEW;
                END;
                $function$
                """
            )
        )
        connection.execute(
            text(
                f'CREATE TRIGGER trg_child_audit BEFORE UPDATE OR DELETE '
                f'ON "{schema}".child FOR EACH ROW '
                f'EXECUTE FUNCTION "{schema}".audit_child_change()'
            )
        )
        connection.execute(
            text(f'ALTER TABLE "{schema}".child DISABLE TRIGGER trg_child_audit')
        )
        yield connection, schema, role
    finally:
        try:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            connection.close()
            engine.dispose()


def _read(connection: Connection, schema: str):
    return PostgresCatalogReader(connection, schemas=(schema,)).read_document()


def test_catalog_reader_covers_every_supported_object_kind(catalog_schema) -> None:
    connection, schema, _role = catalog_schema
    document = _read(connection, schema)
    kinds = {item.key.kind for item in document.objects}
    assert {
        "namespace",
        "extension",
        "enum",
        "domain",
        "sequence",
        "view",
        "materialized_view",
        "table",
        "function",
        "trigger",
    } <= kinds
    assert document.postgres_major == 15


def test_catalog_reader_captures_nested_table_and_sql_object_contracts(
    catalog_schema,
) -> None:
    connection, schema, _role = catalog_schema
    document = _read(connection, schema)

    enum = document.object_by_key(CanonicalObjectKey("enum", schema, "mood"))
    assert enum is not None
    assert enum.definition["labels"] == ["calm", "focused"]

    domain = document.object_by_key(
        CanonicalObjectKey("domain", schema, "positive_integer")
    )
    assert domain is not None
    assert domain.definition["baseType"] == "integer"
    assert domain.definition["notNull"] is True
    assert "VALUE > 0" in str(domain.definition["checks"])

    parent = document.object_by_key(CanonicalObjectKey("table", schema, "parent"))
    assert parent is not None
    columns = {item["name"]: item for item in parent.definition["columns"]}
    assert columns["id"]["identityKind"] == "a"
    assert columns["next_id"]["generatedKind"] == "s"
    assert columns["mood"]["defaultExpression"] == f"'calm'::{schema}.mood"

    child = document.object_by_key(CanonicalObjectKey("table", schema, "child"))
    assert child is not None
    constraints = {item["name"]: item for item in child.definition["constraints"]}
    foreign_key = constraints["fk_child_parent"]
    assert foreign_key["foreignKeyUpdateAction"] == "c"
    assert foreign_key["foreignKeyDeleteAction"] == "r"
    assert foreign_key["foreignKeyMatchType"] == "s"
    indexes = {item["name"]: item for item in child.definition["indexes"]}
    assert indexes["ix_child_lower_note"]["expressions"] is not None
    assert indexes["ix_child_lower_note"]["includeAttributeNumbers"]
    assert indexes["ux_child_positive_parent"]["predicate"] == (
        "((score)::integer > 0)"
    )

    sequence = document.object_by_key(
        CanonicalObjectKey("sequence", schema, "owned_counter")
    )
    assert sequence is not None
    assert sequence.definition["increment"] == 5
    assert sequence.definition["cycle"] is True
    assert sequence.definition["ownedBy"] == {
        "schema": schema,
        "table": "child",
        "column": "id",
    }

    view = document.object_by_key(CanonicalObjectKey("view", schema, "child_view"))
    assert view is not None
    assert view.definition["checkOption"] == "LOCAL"
    assert view.definition["securityBarrier"] is True

    materialized = document.object_by_key(
        CanonicalObjectKey("materialized_view", schema, "child_summary")
    )
    assert materialized is not None
    assert "GROUP BY" in str(materialized.definition["definition"])

    function = document.object_by_key(
        CanonicalObjectKey("function", schema, "audit_child_change")
    )
    assert function is not None
    assert "NEW.note := COALESCE" in str(function.definition["definition"])
    assert function.definition["language"] == "plpgsql"

    trigger = document.object_by_key(
        CanonicalObjectKey("trigger", schema, "trg_child_audit", "child")
    )
    assert trigger is not None
    assert trigger.definition["enabledState"] == "D"
    assert trigger.definition["timing"] == "before"
    assert trigger.definition["orientation"] == "row"
    assert trigger.definition["firesOnUpdate"] is True
    assert trigger.definition["firesOnDelete"] is True
    assert trigger.definition["firesOnInsert"] is False
    assert trigger.definition["function"] == {
        "schema": schema,
        "name": "audit_child_change",
        "identityArguments": "",
    }


def test_owner_acl_comments_statistics_and_fillfactor_are_not_identity(
    catalog_schema,
) -> None:
    connection, schema, role = catalog_schema
    before = _read(connection, schema)
    connection.execute(text(f'COMMENT ON TABLE "{schema}".child IS \'not structural\''))
    connection.execute(text(f'ALTER TABLE "{schema}".child SET (fillfactor = 70)'))
    connection.execute(
        text(f'ALTER INDEX "{schema}".ix_child_lower_note SET (fillfactor = 70)')
    )
    connection.execute(text(f'GRANT SELECT ON "{schema}".child TO "{role}"'))
    connection.execute(text(f'ANALYZE "{schema}".child'))
    connection.execute(text(f'ALTER TABLE "{schema}".child OWNER TO "{role}"'))
    after = _read(connection, schema)
    assert structural_fingerprint(before) == structural_fingerprint(after)


def test_unsupported_application_object_kind_fails_closed(catalog_schema) -> None:
    connection, schema, _role = catalog_schema
    connection.execute(
        text(f'CREATE TYPE "{schema}".composite_payload AS (value integer)')
    )
    with pytest.raises(CatalogReadError) as exc:
        _read(connection, schema)
    assert exc.value.safe_code == "unsupported_catalog_object"


@pytest.mark.parametrize(
    "mutation",
    [
        "function_body",
        "foreign_key_action",
        "partial_predicate",
        "trigger_enabled",
        "enum_label_order",
        "sequence_increment",
        "view_definition",
        "extra_namespace",
        "extra_extension",
    ],
)
def test_structural_mutations_change_fingerprint(catalog_schema, mutation: str) -> None:
    connection, schema, _role = catalog_schema
    before = _read(connection, schema)
    if mutation == "function_body":
        connection.execute(
            text(
                f"""
                CREATE OR REPLACE FUNCTION "{schema}".audit_child_change()
                RETURNS trigger LANGUAGE plpgsql AS $function$
                BEGIN
                    NEW.note := 'different';
                    RETURN NEW;
                END;
                $function$
                """
            )
        )
    elif mutation == "foreign_key_action":
        connection.execute(text(f'ALTER TABLE "{schema}".child DROP CONSTRAINT fk_child_parent'))
        connection.execute(
            text(
                f'ALTER TABLE "{schema}".child ADD CONSTRAINT fk_child_parent '
                f'FOREIGN KEY (parent_id) REFERENCES "{schema}".parent (id) '
                "ON UPDATE RESTRICT ON DELETE CASCADE"
            )
        )
    elif mutation == "partial_predicate":
        connection.execute(text(f'DROP INDEX "{schema}".ux_child_positive_parent'))
        connection.execute(
            text(
                f'CREATE UNIQUE INDEX ux_child_positive_parent ON "{schema}".child '
                "(parent_id) WHERE score > 1"
            )
        )
    elif mutation == "trigger_enabled":
        connection.execute(text(f'ALTER TABLE "{schema}".child ENABLE TRIGGER trg_child_audit'))
    elif mutation == "enum_label_order":
        connection.execute(
            text(f'ALTER TYPE "{schema}".mood ADD VALUE \'alert\' BEFORE \'focused\'')
        )
    elif mutation == "sequence_increment":
        connection.execute(text(f'ALTER SEQUENCE "{schema}".owned_counter INCREMENT BY 7'))
    elif mutation == "view_definition":
        connection.execute(
            text(
                f'CREATE OR REPLACE VIEW "{schema}".child_view '
                f'WITH (security_barrier=true) AS SELECT id, parent_id '
                f'FROM "{schema}".child WHERE score > 1 WITH LOCAL CHECK OPTION'
            )
        )
    elif mutation == "extra_namespace":
        connection.execute(text(f'CREATE SCHEMA "{schema}_extra"'))
    elif mutation == "extra_extension":
        connection.execute(text(f'CREATE EXTENSION hstore WITH SCHEMA "{schema}"'))
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(mutation)

    schemas = (schema, f"{schema}_extra") if mutation == "extra_namespace" else (schema,)
    after = PostgresCatalogReader(connection, schemas=schemas).read_document()
    assert structural_fingerprint(before) != structural_fingerprint(after)
    if mutation == "extra_namespace":
        connection.execute(text(f'DROP SCHEMA "{schema}_extra"'))
    if mutation == "extra_extension":
        connection.execute(text("DROP EXTENSION hstore"))
