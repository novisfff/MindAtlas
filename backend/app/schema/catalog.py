"""Complete PostgreSQL 15 structural catalog reader for schema identity."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.schema.canonical import (
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    CanonicalSchemaObject,
    JsonValue,
    normalize_catalog_sql,
)


class CatalogReadError(RuntimeError):
    """Bounded catalog read failure that never carries SQL or raw rows."""

    def __init__(self, safe_code: str = "catalog_unavailable") -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


_INDEX_STORAGE_PARAMETERS = re.compile(
    r"\s+WITH\s+\([^)]*\)(?=\s+TABLESPACE\b|\s+WHERE\b|$)",
    flags=re.IGNORECASE,
)
_INDEX_TABLESPACE = re.compile(
    r"\s+TABLESPACE\s+(?:\"(?:\"\"|[^\"])+\"|[^\s]+)(?=\s+WHERE\b|$)",
    flags=re.IGNORECASE,
)


def _normalize_index_definition(value: str | None) -> str | None:
    normalized = normalize_catalog_sql(value)
    if normalized is None:
        return None
    normalized = _INDEX_STORAGE_PARAMETERS.sub("", normalized)
    return _INDEX_TABLESPACE.sub("", normalized)


def _qualified_name(schema: str | None, name: str | None) -> str | None:
    if not name:
        return None
    return f"{schema}.{name}" if schema else str(name)


class PostgresCatalogReader:
    """Read one deterministic structural document from PostgreSQL catalogs."""

    def __init__(
        self,
        connection: Connection,
        *,
        schemas: tuple[str, ...] = ("public",),
    ) -> None:
        cleaned = tuple(sorted({str(item).strip() for item in schemas if str(item).strip()}))
        if not cleaned:
            raise ValueError("at least one application schema is required")
        if any(
            item in {"pg_catalog", "information_schema"}
            or item.startswith("pg_toast")
            or item.startswith("pg_temp")
            for item in cleaned
        ):
            raise ValueError("system and temporary schemas are not application schemas")
        self.connection = connection
        self.schemas = cleaned

    @property
    def _params(self) -> dict[str, object]:
        return {"schemas": list(self.schemas)}

    def read_document(self) -> CanonicalSchemaDocument:
        try:
            version = int(
                self.connection.execute(
                    text("SELECT current_setting('server_version_num')::integer / 10000")
                ).scalar_one()
            )
            if version != 15:
                raise CatalogReadError("postgres_major_unsupported")
            self._assert_supported_object_kinds()
            objects: list[CanonicalSchemaObject] = []
            for reader in (
                self._read_namespaces,
                self._read_extensions,
                self._read_enum_and_domain_types,
                self._read_sequences,
                self._read_views,
                self._read_tables,
                self._read_functions,
                self._read_triggers,
            ):
                objects.extend(reader())
            return CanonicalSchemaDocument(
                canonicalization_version=1,
                postgres_major=version,
                objects=tuple(sorted(objects, key=lambda item: item.key)),
            )
        except CatalogReadError:
            raise
        except (SQLAlchemyError, KeyError, TypeError, ValueError) as exc:
            raise CatalogReadError() from exc

    def _rows(self, sql: str) -> list[Mapping[str, Any]]:
        return list(self.connection.execute(text(sql), self._params).mappings())

    def _assert_supported_object_kinds(self) -> None:
        unsupported_relation = self.connection.execute(
            text(
                """
                SELECT 1
                FROM pg_catalog.pg_class AS cls
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = ANY(:schemas)
                  AND cls.relkind NOT IN ('r', 'p', 'S', 'v', 'm', 'i', 'I')
                LIMIT 1
                """
            ),
            self._params,
        ).first()
        unsupported_type = self.connection.execute(
            text(
                """
                SELECT 1
                FROM pg_catalog.pg_type AS typ
                JOIN pg_catalog.pg_namespace AS ns ON ns.oid = typ.typnamespace
                WHERE ns.nspname = ANY(:schemas)
                  AND typ.typtype IN ('r', 'm', 'b', 'p')
                  AND typ.typelem = 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM pg_catalog.pg_depend AS dep
                      WHERE dep.classid = 'pg_type'::regclass
                        AND dep.objid = typ.oid
                        AND dep.refclassid = 'pg_extension'::regclass
                        AND dep.deptype = 'e'
                  )
                LIMIT 1
                """
            ),
            self._params,
        ).first()
        if unsupported_relation is not None or unsupported_type is not None:
            raise CatalogReadError("unsupported_catalog_object")

    def _read_namespaces(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT nspname AS schema_name
            FROM pg_catalog.pg_namespace
            WHERE nspname = ANY(:schemas)
              AND nspname <> 'information_schema'
              AND nspname NOT LIKE 'pg_catalog'
              AND nspname NOT LIKE 'pg_toast%'
              AND nspname NOT LIKE 'pg_temp%'
            ORDER BY nspname
            """
        )
        return [
            CanonicalSchemaObject(
                key=CanonicalObjectKey(
                    "namespace",
                    str(row["schema_name"]),
                    str(row["schema_name"]),
                ),
                definition={"name": str(row["schema_name"])},
            )
            for row in rows
        ]

    def _read_extensions(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
                ext.extname AS extension_name,
                ext.extversion AS extension_version,
                ns.nspname AS schema_name,
                ext.extrelocatable AS relocatable
            FROM pg_catalog.pg_extension AS ext
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = ext.extnamespace
            ORDER BY ext.extname
            """
        )
        return [
            CanonicalSchemaObject(
                key=CanonicalObjectKey(
                    "extension",
                    str(row["schema_name"]),
                    str(row["extension_name"]),
                ),
                definition={
                    "name": str(row["extension_name"]),
                    "version": str(row["extension_version"]),
                    "schema": str(row["schema_name"]),
                    "relocatable": bool(row["relocatable"]),
                },
            )
            for row in rows
        ]

    def _read_enum_and_domain_types(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                typ.typname AS type_name,
                typ.typtype AS type_kind,
                CASE WHEN typ.typtype = 'd'
                     THEN pg_catalog.format_type(typ.typbasetype, typ.typtypmod)
                     ELSE NULL END AS base_type,
                typ.typnotnull AS not_null,
                typ.typdefault AS default_expression,
                coll_ns.nspname AS collation_schema,
                coll.collname AS collation_name,
                ARRAY(
                    SELECT enum.enumlabel
                    FROM pg_catalog.pg_enum AS enum
                    WHERE enum.enumtypid = typ.oid
                    ORDER BY enum.enumsortorder
                ) AS enum_labels,
                ARRAY(
                    SELECT con.conname
                    FROM pg_catalog.pg_constraint AS con
                    WHERE con.contypid = typ.oid
                    ORDER BY con.conname
                ) AS check_names,
                ARRAY(
                    SELECT pg_catalog.pg_get_constraintdef(con.oid, true)
                    FROM pg_catalog.pg_constraint AS con
                    WHERE con.contypid = typ.oid
                    ORDER BY con.conname
                ) AS check_definitions,
                ARRAY(
                    SELECT con.convalidated
                    FROM pg_catalog.pg_constraint AS con
                    WHERE con.contypid = typ.oid
                    ORDER BY con.conname
                ) AS check_validated
            FROM pg_catalog.pg_type AS typ
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = typ.typnamespace
            LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = typ.typcollation
            LEFT JOIN pg_catalog.pg_namespace AS coll_ns ON coll_ns.oid = coll.collnamespace
            WHERE ns.nspname = ANY(:schemas)
              AND typ.typtype IN ('e', 'd')
            ORDER BY ns.nspname, typ.typname
            """
        )
        objects: list[CanonicalSchemaObject] = []
        for row in rows:
            schema = str(row["schema_name"])
            name = str(row["type_name"])
            if row["type_kind"] == "e":
                definition: dict[str, JsonValue] = {
                    "name": name,
                    "labels": [str(item) for item in row["enum_labels"]],
                }
                kind = "enum"
            else:
                checks = [
                    {
                        "name": str(check_name),
                        "definition": normalize_catalog_sql(str(check_definition)),
                        "validated": bool(validated),
                    }
                    for check_name, check_definition, validated in zip(
                        row["check_names"],
                        row["check_definitions"],
                        row["check_validated"],
                        strict=True,
                    )
                ]
                definition = {
                    "name": name,
                    "baseType": str(row["base_type"]),
                    "defaultExpression": normalize_catalog_sql(row["default_expression"]),
                    "notNull": bool(row["not_null"]),
                    "collation": _qualified_name(
                        row["collation_schema"], row["collation_name"]
                    ),
                    "checks": checks,
                }
                kind = "domain"
            objects.append(
                CanonicalSchemaObject(
                    key=CanonicalObjectKey(kind, schema, name),
                    definition=definition,
                )
            )
        return objects

    def _read_sequences(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                cls.relname AS sequence_name,
                pg_catalog.format_type(seq.seqtypid, NULL) AS formatted_type,
                seq.seqstart AS start_value,
                seq.seqincrement AS increment_value,
                seq.seqmin AS min_value,
                seq.seqmax AS max_value,
                seq.seqcache AS cache_value,
                seq.seqcycle AS cycle,
                owner_ns.nspname AS owner_schema,
                owner_cls.relname AS owner_table,
                owner_attr.attname AS owner_column
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            JOIN pg_catalog.pg_sequence AS seq ON seq.seqrelid = cls.oid
            LEFT JOIN LATERAL (
                SELECT dep.refobjid, dep.refobjsubid
                FROM pg_catalog.pg_depend AS dep
                WHERE dep.classid = 'pg_class'::regclass
                  AND dep.objid = cls.oid
                  AND dep.refclassid = 'pg_class'::regclass
                  AND dep.deptype IN ('a', 'i')
                ORDER BY dep.deptype
                LIMIT 1
            ) AS owned ON true
            LEFT JOIN pg_catalog.pg_class AS owner_cls ON owner_cls.oid = owned.refobjid
            LEFT JOIN pg_catalog.pg_namespace AS owner_ns
                ON owner_ns.oid = owner_cls.relnamespace
            LEFT JOIN pg_catalog.pg_attribute AS owner_attr
                ON owner_attr.attrelid = owned.refobjid
               AND owner_attr.attnum = owned.refobjsubid
            WHERE cls.relkind = 'S'
              AND ns.nspname = ANY(:schemas)
            ORDER BY ns.nspname, cls.relname
            """
        )
        objects: list[CanonicalSchemaObject] = []
        for row in rows:
            owned_by: JsonValue = None
            if row["owner_table"] is not None:
                owned_by = {
                    "schema": str(row["owner_schema"]),
                    "table": str(row["owner_table"]),
                    "column": str(row["owner_column"]),
                }
            objects.append(
                CanonicalSchemaObject(
                    key=CanonicalObjectKey(
                        "sequence",
                        str(row["schema_name"]),
                        str(row["sequence_name"]),
                    ),
                    definition={
                        "type": str(row["formatted_type"]),
                        "start": int(row["start_value"]),
                        "increment": int(row["increment_value"]),
                        "minimum": int(row["min_value"]),
                        "maximum": int(row["max_value"]),
                        "cache": int(row["cache_value"]),
                        "cycle": bool(row["cycle"]),
                        "ownedBy": owned_by,
                    },
                )
            )
        return objects

    def _read_views(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                cls.relname AS view_name,
                cls.relkind AS relation_kind,
                pg_catalog.pg_get_viewdef(cls.oid, true) AS definition,
                COALESCE(info.check_option, 'NONE') AS check_option,
                COALESCE('security_barrier=true' = ANY(cls.reloptions), false)
                    AS security_barrier,
                COALESCE('security_invoker=true' = ANY(cls.reloptions), false)
                    AS security_invoker
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            LEFT JOIN information_schema.views AS info
              ON info.table_schema = ns.nspname AND info.table_name = cls.relname
            WHERE cls.relkind IN ('v', 'm')
              AND ns.nspname = ANY(:schemas)
            ORDER BY ns.nspname, cls.relname
            """
        )
        return [
            CanonicalSchemaObject(
                key=CanonicalObjectKey(
                    "view" if row["relation_kind"] == "v" else "materialized_view",
                    str(row["schema_name"]),
                    str(row["view_name"]),
                ),
                definition={
                    "definition": normalize_catalog_sql(str(row["definition"])),
                    "checkOption": str(row["check_option"]),
                    "securityBarrier": bool(row["security_barrier"]),
                    "securityInvoker": bool(row["security_invoker"]),
                },
            )
            for row in rows
        ]

    def _read_tables(self) -> list[CanonicalSchemaObject]:
        table_rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                cls.relname AS table_name,
                cls.relkind AS relation_kind,
                cls.relpersistence AS persistence,
                part.partstrat AS partition_strategy,
                pg_catalog.pg_get_expr(cls.relpartbound, cls.oid, false)
                    AS partition_bound
            FROM pg_catalog.pg_class AS cls
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            LEFT JOIN pg_catalog.pg_partitioned_table AS part ON part.partrelid = cls.oid
            WHERE cls.relkind IN ('r', 'p')
              AND ns.nspname = ANY(:schemas)
            ORDER BY ns.nspname, cls.relname
            """
        )
        columns = self._read_columns()
        constraints = self._read_constraints()
        indexes = self._read_indexes()
        objects: list[CanonicalSchemaObject] = []
        for row in table_rows:
            table_key = (str(row["schema_name"]), str(row["table_name"]))
            objects.append(
                CanonicalSchemaObject(
                    key=CanonicalObjectKey("table", *table_key),
                    definition={
                        "relationKind": str(row["relation_kind"]),
                        "persistence": str(row["persistence"]),
                        "partitionStrategy": (
                            None
                            if row["partition_strategy"] is None
                            else str(row["partition_strategy"])
                        ),
                        "partitionBound": normalize_catalog_sql(row["partition_bound"]),
                        "columns": columns.get(table_key, []),
                        "constraints": constraints.get(table_key, []),
                        "indexes": indexes.get(table_key, []),
                    },
                )
            )
        return objects

    def _read_columns(self) -> dict[tuple[str, str], list[dict[str, JsonValue]]]:
        rows = self._rows(
            """
            SELECT
              ns.nspname AS schema_name,
              cls.relname AS table_name,
              attr.attnum AS ordinal,
              attr.attname AS column_name,
              pg_catalog.format_type(attr.atttypid, attr.atttypmod) AS formatted_type,
              attr.attnotnull AS not_null,
              attr.attidentity AS identity_kind,
              attr.attgenerated AS generated_kind,
              coll_ns.nspname AS collation_schema,
              coll.collname AS collation_name,
              CASE WHEN attr.attcollation <> 0
                         AND attr.attcollation <> typ.typcollation
                   THEN true ELSE false END AS non_default_collation,
              pg_catalog.pg_get_expr(def.adbin, def.adrelid, false)
                  AS default_expression
            FROM pg_catalog.pg_attribute AS attr
            JOIN pg_catalog.pg_class AS cls ON cls.oid = attr.attrelid
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            JOIN pg_catalog.pg_type AS typ ON typ.oid = attr.atttypid
            LEFT JOIN pg_catalog.pg_attrdef AS def
              ON def.adrelid = attr.attrelid AND def.adnum = attr.attnum
            LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = attr.attcollation
            LEFT JOIN pg_catalog.pg_namespace AS coll_ns ON coll_ns.oid = coll.collnamespace
            WHERE attr.attnum > 0
              AND NOT attr.attisdropped
              AND cls.relkind IN ('r', 'p')
              AND ns.nspname = ANY(:schemas)
            ORDER BY ns.nspname, cls.relname, attr.attnum
            """
        )
        grouped: dict[tuple[str, str], list[dict[str, JsonValue]]] = defaultdict(list)
        for row in rows:
            collation = (
                _qualified_name(row["collation_schema"], row["collation_name"])
                if row["non_default_collation"]
                else None
            )
            grouped[(str(row["schema_name"]), str(row["table_name"]))].append(
                {
                    "ordinal": int(row["ordinal"]),
                    "name": str(row["column_name"]),
                    "formattedType": str(row["formatted_type"]),
                    "nullable": not bool(row["not_null"]),
                    "defaultExpression": normalize_catalog_sql(row["default_expression"]),
                    "identityKind": str(row["identity_kind"] or ""),
                    "generatedKind": str(row["generated_kind"] or ""),
                    "collation": collation,
                }
            )
        return grouped

    def _read_constraints(self) -> dict[tuple[str, str], list[dict[str, JsonValue]]]:
        rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                cls.relname AS table_name,
                con.conname AS constraint_name,
                con.contype AS constraint_type,
                pg_catalog.pg_get_constraintdef(con.oid, true) AS definition,
                con.condeferrable AS deferrable,
                con.condeferred AS initially_deferred,
                con.convalidated AS validated,
                con.confupdtype AS fk_update_action,
                con.confdeltype AS fk_delete_action,
                con.confmatchtype AS fk_match_type
            FROM pg_catalog.pg_constraint AS con
            JOIN pg_catalog.pg_class AS cls ON cls.oid = con.conrelid
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = ANY(:schemas)
              AND cls.relkind IN ('r', 'p')
              AND con.contype IN ('p', 'f', 'u', 'c', 'x')
            ORDER BY ns.nspname, cls.relname, con.contype, con.conname,
                     pg_catalog.pg_get_constraintdef(con.oid, true)
            """
        )
        grouped: dict[tuple[str, str], list[dict[str, JsonValue]]] = defaultdict(list)
        for row in rows:
            name = str(row["constraint_name"] or "")
            if not name:
                raise CatalogReadError("unnamed_application_constraint")
            is_fk = row["constraint_type"] == "f"
            grouped[(str(row["schema_name"]), str(row["table_name"]))].append(
                {
                    "name": name,
                    "type": str(row["constraint_type"]),
                    "definition": normalize_catalog_sql(str(row["definition"])),
                    "deferrable": bool(row["deferrable"]),
                    "initiallyDeferred": bool(row["initially_deferred"]),
                    "validated": bool(row["validated"]),
                    "foreignKeyUpdateAction": (
                        str(row["fk_update_action"]) if is_fk else None
                    ),
                    "foreignKeyDeleteAction": (
                        str(row["fk_delete_action"]) if is_fk else None
                    ),
                    "foreignKeyMatchType": str(row["fk_match_type"]) if is_fk else None,
                }
            )
        return grouped

    def _read_indexes(self) -> dict[tuple[str, str], list[dict[str, JsonValue]]]:
        rows = self._rows(
            """
            SELECT
                ns.nspname AS schema_name,
                tbl.relname AS table_name,
                idx.relname AS index_name,
                am.amname AS access_method,
                ind.indisunique AS is_unique,
                ind.indisprimary AS is_primary,
                ind.indisexclusion AS is_exclusion,
                ind.indisvalid AS is_valid,
                ind.indisready AS is_ready,
                ind.indnullsnotdistinct AS nulls_not_distinct,
                ind.indnkeyatts AS key_attribute_count,
                ind.indkey::smallint[] AS attribute_numbers,
                pg_catalog.pg_get_indexdef(ind.indexrelid, 0, false) AS definition,
                pg_catalog.pg_get_expr(ind.indexprs, ind.indrelid, false)
                    AS expressions,
                pg_catalog.pg_get_expr(ind.indpred, ind.indrelid, false) AS predicate
            FROM pg_catalog.pg_index AS ind
            JOIN pg_catalog.pg_class AS tbl ON tbl.oid = ind.indrelid
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = tbl.relnamespace
            JOIN pg_catalog.pg_class AS idx ON idx.oid = ind.indexrelid
            JOIN pg_catalog.pg_am AS am ON am.oid = idx.relam
            WHERE ns.nspname = ANY(:schemas)
              AND tbl.relkind IN ('r', 'p')
            ORDER BY ns.nspname, tbl.relname, idx.relname
            """
        )
        grouped: dict[tuple[str, str], list[dict[str, JsonValue]]] = defaultdict(list)
        for row in rows:
            attribute_numbers = [int(item) for item in row["attribute_numbers"]]
            key_count = int(row["key_attribute_count"])
            grouped[(str(row["schema_name"]), str(row["table_name"]))].append(
                {
                    "name": str(row["index_name"]),
                    "parentTable": {
                        "schema": str(row["schema_name"]),
                        "name": str(row["table_name"]),
                    },
                    "accessMethod": str(row["access_method"]),
                    "unique": bool(row["is_unique"]),
                    "primary": bool(row["is_primary"]),
                    "exclusion": bool(row["is_exclusion"]),
                    "valid": bool(row["is_valid"]),
                    "ready": bool(row["is_ready"]),
                    "nullsNotDistinct": bool(row["nulls_not_distinct"]),
                    "definition": _normalize_index_definition(str(row["definition"])),
                    "expressions": normalize_catalog_sql(row["expressions"]),
                    "predicate": normalize_catalog_sql(row["predicate"]),
                    "keyAttributeNumbers": attribute_numbers[:key_count],
                    "includeAttributeNumbers": attribute_numbers[key_count:],
                }
            )
        return grouped

    def _read_functions(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
              ns.nspname AS schema_name,
              proc.proname AS function_name,
              pg_catalog.pg_get_function_identity_arguments(proc.oid)
                  AS identity_arguments,
              pg_catalog.pg_get_function_result(proc.oid) AS result_type,
              lang.lanname AS language,
              proc.provolatile AS volatility,
              proc.proisstrict AS is_strict,
              proc.prosecdef AS security_definer,
              proc.proparallel AS parallel_safety,
              proc.prokind AS function_kind,
              pg_catalog.pg_get_functiondef(proc.oid) AS definition
            FROM pg_catalog.pg_proc AS proc
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
            JOIN pg_catalog.pg_language AS lang ON lang.oid = proc.prolang
            WHERE ns.nspname = ANY(:schemas)
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_depend AS dep
                  WHERE dep.classid = 'pg_proc'::regclass
                    AND dep.objid = proc.oid
                    AND dep.refclassid = 'pg_extension'::regclass
                    AND dep.deptype = 'e'
              )
            ORDER BY ns.nspname, proc.proname,
                     pg_catalog.pg_get_function_identity_arguments(proc.oid)
            """
        )
        return [
            CanonicalSchemaObject(
                key=CanonicalObjectKey(
                    "function",
                    str(row["schema_name"]),
                    str(row["function_name"]),
                    str(row["identity_arguments"]),
                ),
                definition={
                    "identityArguments": str(row["identity_arguments"]),
                    "resultType": str(row["result_type"]),
                    "language": str(row["language"]),
                    "volatility": str(row["volatility"]),
                    "strict": bool(row["is_strict"]),
                    "securityDefiner": bool(row["security_definer"]),
                    "parallelSafety": str(row["parallel_safety"]),
                    "functionKind": str(row["function_kind"]),
                    "definition": normalize_catalog_sql(str(row["definition"])),
                },
            )
            for row in rows
        ]

    def _read_triggers(self) -> list[CanonicalSchemaObject]:
        rows = self._rows(
            """
            SELECT
              ns.nspname AS schema_name,
              rel.relname AS table_name,
              trg.tgname AS trigger_name,
              trg.tgenabled AS enabled_state,
              trg.tgtype AS trigger_type,
              proc_ns.nspname AS function_schema,
              proc.proname AS function_name,
              pg_catalog.pg_get_function_identity_arguments(proc.oid)
                  AS function_arguments,
              pg_catalog.pg_get_triggerdef(trg.oid, true) AS definition
            FROM pg_catalog.pg_trigger AS trg
            JOIN pg_catalog.pg_class AS rel ON rel.oid = trg.tgrelid
            JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
            JOIN pg_catalog.pg_proc AS proc ON proc.oid = trg.tgfoid
            JOIN pg_catalog.pg_namespace AS proc_ns ON proc_ns.oid = proc.pronamespace
            WHERE NOT trg.tgisinternal
              AND ns.nspname = ANY(:schemas)
            ORDER BY ns.nspname, rel.relname, trg.tgname
            """
        )
        objects: list[CanonicalSchemaObject] = []
        for row in rows:
            trigger_type = int(row["trigger_type"])
            timing = (
                "instead_of"
                if trigger_type & 64
                else "before"
                if trigger_type & 2
                else "after"
            )
            objects.append(
                CanonicalSchemaObject(
                    key=CanonicalObjectKey(
                        "trigger",
                        str(row["schema_name"]),
                        str(row["trigger_name"]),
                        str(row["table_name"]),
                    ),
                    definition={
                        "table": {
                            "schema": str(row["schema_name"]),
                            "name": str(row["table_name"]),
                        },
                        "enabledState": str(row["enabled_state"]),
                        "timing": timing,
                        "orientation": "row" if trigger_type & 1 else "statement",
                        "firesOnInsert": bool(trigger_type & 4),
                        "firesOnDelete": bool(trigger_type & 8),
                        "firesOnUpdate": bool(trigger_type & 16),
                        "firesOnTruncate": bool(trigger_type & 32),
                        "function": {
                            "schema": str(row["function_schema"]),
                            "name": str(row["function_name"]),
                            "identityArguments": str(row["function_arguments"]),
                        },
                        "definition": normalize_catalog_sql(str(row["definition"])),
                    },
                )
            )
        return objects
