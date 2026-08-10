from __future__ import annotations

import pytest


def test_pre_squash_fixture_renders_manifest_table_definition() -> None:
    from tests.pre_squash_fixture import render_table_ddl

    definition = {
        "columns": [
            {
                "ordinal": 1,
                "name": "id",
                "formattedType": "uuid",
                "nullable": False,
                "defaultExpression": None,
                "generatedKind": "",
                "identityKind": "",
                "collation": None,
            }
        ],
        "constraints": [
            {
                "type": "p",
                "name": "sample_pkey",
                "definition": "PRIMARY KEY (id)",
            }
        ],
        "indexes": [],
    }

    sql = render_table_ddl("public", "sample", definition)

    assert 'CREATE TABLE "public"."sample"' in sql
    assert '"id" uuid NOT NULL' in sql
    assert 'CONSTRAINT "sample_pkey" PRIMARY KEY (id)' in sql


def test_pre_squash_fixture_rejects_unknown_column_type() -> None:
    from tests.pre_squash_fixture import render_table_ddl

    with pytest.raises(ValueError, match="unsupported fixture column type"):
        render_table_ddl(
            "public",
            "sample",
            {
                "columns": [
                    {
                        "ordinal": 1,
                        "name": "bad",
                        "formattedType": "USER-DEFINED",
                        "nullable": False,
                        "defaultExpression": None,
                        "generatedKind": "",
                        "identityKind": "",
                        "collation": None,
                    }
                ],
                "constraints": [],
                "indexes": [],
            },
        )


def test_pre_squash_fixture_preserves_deferrable_constraint_flags() -> None:
    from tests.pre_squash_fixture import render_table_ddl

    sql = render_table_ddl(
        "public",
        "sample",
        {
            "columns": [
                {
                    "ordinal": 1,
                    "name": "id",
                    "formattedType": "uuid",
                    "nullable": False,
                    "defaultExpression": None,
                    "generatedKind": "",
                    "identityKind": "",
                    "collation": None,
                }
            ],
            "constraints": [
                {
                    "type": "u",
                    "name": "sample_id_uq",
                    "definition": "UNIQUE (id)",
                    "deferrable": True,
                    "initiallyDeferred": True,
                }
            ],
            "indexes": [],
        },
    )

    assert 'CONSTRAINT "sample_id_uq" UNIQUE (id) DEFERRABLE INITIALLY DEFERRED' in sql
