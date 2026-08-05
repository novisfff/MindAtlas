from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from app.schema.application_contract import (
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import compare_documents
from app.schema.catalog import PostgresCatalogReader
from tests.postgres_destructive_guard import reset_disposable_public_schema

from app.database import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_ENV = BACKEND_ROOT / "alembic" / "env.py"
_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

_CAPTURED_SERVER_DEFAULTS = {
    ("ai_model_capability_probe", "created_at"): "now()",
    ("assistant_agent_profile", "enabled"): "true",
    ("assistant_agent_profile", "is_system"): "false",
    ("assistant_chat_run", "checkpoint_seq"): "0",
    ("assistant_chat_run", "last_event_seq"): "0",
    ("assistant_chat_run", "status"): "'queued'",
    ("assistant_conversation_l1_memory", "summary_text"): "''",
    ("assistant_conversation_skill_l2_memory", "version"): "1",
    ("assistant_conversation_workflow_call_memory", "version"): "1",
    ("assistant_main_agent_rollout_control", "created_at"): "now()",
    ("assistant_main_agent_rollout_control", "updated_at"): "now()",
    ("assistant_main_agent_rollout_event", "created_at"): "now()",
    ("assistant_main_agent_rollout_revision", "created_at"): "now()",
    ("assistant_runtime_bootstrap_gate_use", "created_at"): "now()",
    ("assistant_skill_publish_gate", "action"): "'skill_publish'",
    ("assistant_target_folder", "color_token"): "'slate'",
    ("assistant_target_folder", "description"): "''",
    ("assistant_target_folder", "icon_key"): "'folder'",
    ("assistant_workflow", "enabled"): "true",
    ("assistant_workflow", "is_system"): "false",
    ("attachment", "index_to_knowledge_graph"): "false",
    ("attachment_index_outbox", "attempts"): "0",
    ("attachment_index_outbox", "available_at"): "now()",
    ("attachment_parse_outbox", "attempts"): "0",
    ("attachment_parse_outbox", "available_at"): "now()",
    ("attachment_parse_outbox", "created_at"): "now()",
    ("attachment_parse_outbox", "status"): "'pending'",
    ("attachment_parse_outbox", "updated_at"): "now()",
    ("entry_index_outbox", "attempts"): "0",
    ("entry_index_outbox", "available_at"): "now()",
    ("operator_account", "enabled"): "true",
    ("operator_account", "failed_login_count"): "0",
    ("operator_account", "password_revision"): "1",
    ("operator_account", "role"): "'operator'",
    ("operator_account", "singleton_key"): "'operator'",
    ("operator_audit_event", "metadata_json"): "'{}'::jsonb",
}

_CAPTURED_FOREIGN_KEY_NAMES = {
    ("ai_model_capability_probe", ("model_id",)): (
        "fk_ai_model_capability_probe_model_id"
    ),
    ("assistant_agent_profile", ("draft_version_id",)): (
        "fk_assistant_agent_profile_draft_version"
    ),
    ("assistant_agent_profile", ("folder_id",)): (
        "fk_assistant_agent_profile_folder_id"
    ),
    ("assistant_agent_profile", ("published_version_id",)): (
        "fk_assistant_agent_profile_published_version"
    ),
    ("assistant_chat_run", ("main_agent_profile_version_id",)): (
        "fk_assistant_chat_run_main_agent_profile_version_id"
    ),
    ("assistant_chat_run", ("main_agent_rollout_revision_id",)): (
        "fk_assistant_chat_run_main_agent_rollout_revision_id"
    ),
    ("assistant_chat_run", ("resolved_model_id",)): (
        "fk_assistant_chat_run_resolved_model_id"
    ),
    ("assistant_conversation_l1_memory", ("last_applied_run_id",)): (
        "fk_assistant_l1_memory_last_applied_run_id"
    ),
    (
        "assistant_conversation_skill_l2_memory",
        ("last_applied_run_id",),
    ): "fk_assistant_l2_memory_last_applied_run_id",
    (
        "assistant_conversation_skill_l2_memory",
        ("skill_package_id",),
    ): "fk_assistant_l2_memory_skill_package_id",
    ("assistant_skill_eval_artifact", ("eval_run_id",)): (
        "fk_assistant_skill_eval_artifact_eval_run_id"
    ),
    ("assistant_skill_eval_capability_call", ("eval_case_id",)): (
        "fk_assistant_skill_eval_capability_call_eval_case_id"
    ),
    ("assistant_skill_eval_capability_call", ("eval_run_id",)): (
        "fk_assistant_skill_eval_capability_call_eval_run_id"
    ),
    ("assistant_skill_eval_case", ("dataset_version_id",)): (
        "fk_assistant_skill_eval_case_dataset_version_id"
    ),
    ("assistant_skill_eval_case_result", ("eval_case_id",)): (
        "fk_assistant_skill_eval_case_result_eval_case_id"
    ),
    ("assistant_skill_eval_case_result", ("eval_run_id",)): (
        "fk_assistant_skill_eval_case_result_eval_run_id"
    ),
    ("assistant_skill_eval_dataset_draft", ("dataset_id",)): (
        "fk_assistant_skill_eval_dataset_draft_dataset_id"
    ),
    ("assistant_skill_eval_dataset_version", ("dataset_id",)): (
        "fk_assistant_skill_eval_dataset_version_dataset_id"
    ),
    ("assistant_skill_eval_event", ("eval_run_id",)): (
        "fk_assistant_skill_eval_event_eval_run_id"
    ),
    ("assistant_skill_publish_gate_use", ("gate_id",)): (
        "fk_assistant_skill_publish_gate_use_gate_id"
    ),
    ("assistant_target_folder", ("parent_id",)): (
        "fk_assistant_target_folder_parent_id"
    ),
    ("assistant_workflow", ("draft_version_id",)): (
        "fk_assistant_workflow_draft_version"
    ),
    ("assistant_workflow", ("folder_id",)): (
        "fk_assistant_workflow_folder_id"
    ),
    ("assistant_workflow", ("published_version_id",)): (
        "fk_assistant_workflow_published_version"
    ),
    ("operator_session", ("operator_account_id",)): (
        "fk_operator_session_operator_account_id"
    ),
}

_CAPTURED_UNIQUE_CONSTRAINT_NAMES = {
    ("assistant_main_agent_profile", ("profile_key",)): (
        "assistant_main_agent_profile_profile_key_key"
    ),
    ("assistant_main_agent_rollout_event", ("request_id",)): (
        "uq_ma_rollout_event_request_id"
    ),
    ("assistant_main_agent_rollout_revision", ("revision_digest",)): (
        "uq_ma_rollout_revision_digest"
    ),
    ("assistant_main_agent_rollout_revision", ("revision_label",)): (
        "uq_ma_rollout_revision_label"
    ),
    ("assistant_runtime_bootstrap_gate_use", ("bootstrap_request_id",)): (
        "uq_runtime_bootstrap_gate_use_request_id"
    ),
    ("assistant_runtime_bootstrap_gate_use", ("rollout_revision_id",)): (
        "uq_runtime_bootstrap_gate_use_rollout_revision_id"
    ),
    ("assistant_skill_eval_capability_call", ("eval_call_id",)): (
        "uq_assistant_skill_eval_capability_call_eval_call_id"
    ),
    ("assistant_skill_eval_dataset", ("stable_key",)): (
        "uq_assistant_skill_eval_dataset_stable_key"
    ),
    ("assistant_skill_eval_dataset_draft", ("dataset_id",)): (
        "uq_assistant_skill_eval_dataset_draft_dataset_id"
    ),
    ("assistant_skill_package", ("canonical_name",)): (
        "assistant_skill_package_canonical_name_key"
    ),
    ("assistant_skill_package_alias", ("normalized_alias",)): (
        "assistant_skill_package_alias_normalized_alias_key"
    ),
    ("assistant_skill_publish_gate", ("request_id",)): (
        "uq_assistant_skill_publish_gate_request_id"
    ),
    ("operator_session", ("token_digest",)): (
        "uq_operator_session_token_digest"
    ),
}

_CAPTURED_INDEXES = {
    ("ai_model", "idx_ai_model_current_capability_probe_id"): (
        ("current_capability_probe_id",),
        False,
    ),
    ("ai_provider", "ix_ai_provider_name"): (("name",), True),
    ("assistant_main_agent_profile", "ix_assistant_main_agent_profile_profile_key"): (
        ("profile_key",),
        False,
    ),
    (
        "assistant_skill_capability_binding",
        "ix_as_skill_cap_bind_res_agent_ver",
    ): (("resolved_agent_version_id",), False),
    (
        "assistant_skill_capability_binding",
        "ix_as_skill_cap_bind_res_tool",
    ): (("resolved_tool_id",), False),
    (
        "assistant_skill_capability_binding",
        "ix_as_skill_cap_bind_res_wf_ver",
    ): (("resolved_workflow_version_id",), False),
    (
        "assistant_skill_capability_dependency",
        "ix_as_skill_cap_dep_res_agent_ver",
    ): (("resolved_agent_version_id",), False),
    (
        "assistant_skill_capability_dependency",
        "ix_as_skill_cap_dep_res_model",
    ): (("resolved_model_id",), False),
    (
        "assistant_skill_capability_dependency",
        "ix_as_skill_cap_dep_res_tool",
    ): (("resolved_tool_id",), False),
    (
        "assistant_skill_capability_dependency",
        "ix_as_skill_cap_dep_res_wf_ver",
    ): (("resolved_workflow_version_id",), False),
    (
        "assistant_skill_eval_capability_call",
        "ix_assistant_skill_eval_capability_call_eval_call_id",
    ): (("eval_call_id",), False),
    (
        "assistant_skill_eval_dataset",
        "ix_assistant_skill_eval_dataset_stable_key",
    ): (("stable_key",), False),
    (
        "assistant_skill_eval_dataset_draft",
        "ix_assistant_skill_eval_dataset_draft_dataset_id",
    ): (("dataset_id",), False),
    ("assistant_skill_package", "ix_assistant_skill_package_canonical_name"): (
        ("canonical_name",),
        False,
    ),
    (
        "assistant_skill_package_alias",
        "ix_assistant_skill_package_alias_normalized_alias",
    ): (("normalized_alias",), False),
    ("attachment", "ix_attachment_entry_id"): (("entry_id",), False),
    ("attachment", "ix_attachment_id"): (("id",), False),
    ("entry", "ix_entry_id"): (("id",), False),
    ("entry", "ix_entry_time_at"): (("time_at",), False),
    ("entry", "ix_entry_type_id"): (("type_id",), False),
    ("entry_type", "ix_entry_type_code"): (("code",), True),
    ("relation", "ix_relation_id"): (("id",), False),
    ("relation", "ix_relation_relation_type_id"): (
        ("relation_type_id",),
        False,
    ),
    ("relation", "ix_relation_source_entry_id"): (
        ("source_entry_id",),
        False,
    ),
    ("relation", "ix_relation_target_entry_id"): (
        ("target_entry_id",),
        False,
    ),
    ("relation_type", "ix_relation_type_code"): (("code",), True),
    ("relation_type", "ix_relation_type_id"): (("id",), False),
    ("tag", "ix_tag_id"): (("id",), False),
    ("tag", "ix_tag_name"): (("name",), True),
}

_FORBIDDEN_INDEX_NAMES = {
    "ix_ai_model_current_capability_probe_id",
    "ix_ai_model_capability_probe_model_id",
    "ix_assistant_agent_profile_version_agent_profile_id",
    "ix_assistant_workflow_version_workflow_id",
    "ix_assistant_skill_capability_binding_resolved_agent_version_id",
    "ix_assistant_skill_capability_binding_resolved_tool_id",
    "ix_assistant_skill_capability_binding_resolved_workflow_8e94",
    "ix_assistant_skill_capability_dependency_resolved_agent_8f23",
    "ix_assistant_skill_capability_dependency_resolved_model_id",
    "ix_assistant_skill_capability_dependency_resolved_tool_id",
    "ix_assistant_skill_capability_dependency_resolved_workf_b41d",
}


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def test_live_model_registry_loads_every_expected_table() -> None:
    from app.model_registry import load_all_live_models

    load_all_live_models()
    names = set(Base.metadata.tables)

    assert "operator_account" in names
    assert "assistant_main_agent_rollout_revision" in names
    assert "assistant_chat_run" in names
    assert "assistant_runtime_migration_item" not in names


def test_alembic_env_uses_only_central_registry() -> None:
    source = ALEMBIC_ENV.read_text("utf-8")

    assert "from app.model_registry import load_all_live_models" in source
    assert "load_all_live_models()" in source
    assert "app.assistant.migration" not in source


def test_live_metadata_declares_all_captured_server_defaults() -> None:
    from app.model_registry import load_all_live_models

    load_all_live_models()
    for (table_name, column_name), expected in _CAPTURED_SERVER_DEFAULTS.items():
        column = Base.metadata.tables[table_name].c[column_name]
        assert column.server_default is not None, f"{table_name}.{column_name}"
        assert str(column.server_default.arg) == expected


def test_live_metadata_uses_captured_jsonb_types() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    from app.model_registry import load_all_live_models

    load_all_live_models()
    for table_name, column_name in (
        ("assistant_main_agent_rollout_event", "result_json"),
        ("assistant_main_agent_rollout_revision", "package_closure_json"),
    ):
        column = Base.metadata.tables[table_name].c[column_name]
        assert isinstance(column.type, JSONB), f"{table_name}.{column_name}"


def test_live_metadata_declares_all_captured_lowercase_sha256_checks() -> None:
    from sqlalchemy import CheckConstraint

    from app.model_registry import load_all_live_models

    load_all_live_models()
    expected = load_logical_application_contract().logical_application_document
    expected_checks = []
    for item in expected.objects:
        if item.key.kind != "table":
            continue
        for constraint in item.definition["constraints"]:
            regex_count = constraint["definition"].count(
                "~ '^[0-9a-f]{64}$'"
            )
            if regex_count:
                expected_checks.append(
                    (item.key.name, constraint["name"], regex_count)
                )

    assert len(expected_checks) == 63
    for table_name, constraint_name, regex_count in expected_checks:
        by_name = {
            constraint.name: constraint
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert constraint_name in by_name, f"{table_name}.{constraint_name}"
        assert str(by_name[constraint_name].sqltext).count(
            "~ '^[0-9a-f]{64}$'"
        ) == regex_count

    portable_interrupt_checks = {
        "ck_assistant_run_interrupt_budget_suspension_digest",
        "ck_assistant_run_interrupt_field_schema_digest",
        "ck_assistant_run_interrupt_request_digest",
        "ck_assistant_run_interrupt_resolution_digest",
        "ck_assistant_run_interrupt_resume_token_digest",
    }
    interrupt_by_name = {
        constraint.name: constraint
        for constraint in Base.metadata.tables[
            "assistant_run_interrupt"
        ].constraints
        if isinstance(constraint, CheckConstraint)
    }
    for name in portable_interrupt_checks:
        sqltext = str(interrupt_by_name[name].sqltext)
        assert "length(" in sqltext
        assert "~ '^[0-9a-f]{64}$'" not in sqltext


def test_live_metadata_declares_captured_constraint_identities() -> None:
    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

    from app.model_registry import load_all_live_models

    load_all_live_models()
    for (table_name, columns), expected_name in (
        _CAPTURED_FOREIGN_KEY_NAMES.items()
    ):
        constraints = Base.metadata.tables[table_name].constraints
        actual = {
            tuple(constraint.column_keys): constraint.name
            for constraint in constraints
            if isinstance(constraint, ForeignKeyConstraint)
        }
        assert actual[columns] == expected_name, f"{table_name}.{columns}"

    for (table_name, columns), expected_name in (
        _CAPTURED_UNIQUE_CONSTRAINT_NAMES.items()
    ):
        constraints = Base.metadata.tables[table_name].constraints
        actual = {
            tuple(column.name for column in constraint.columns): constraint.name
            for constraint in constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert actual[columns] == expected_name, f"{table_name}.{columns}"

    forbidden_anonymous_uniques = {
        ("ai_provider", ("name",)),
        ("entry_type", ("code",)),
        ("relation_type", ("code",)),
        ("tag", ("name",)),
    }
    for table_name, columns in forbidden_anonymous_uniques:
        constraints = Base.metadata.tables[table_name].constraints
        assert not any(
            isinstance(constraint, UniqueConstraint)
            and tuple(column.name for column in constraint.columns) == columns
            for constraint in constraints
        ), f"{table_name}.{columns}"


def test_live_metadata_declares_captured_non_digest_checks() -> None:
    from sqlalchemy import CheckConstraint

    from app.model_registry import load_all_live_models

    load_all_live_models()
    expected_checks = {
        ("ai_credential", "ck_ai_credential_runtime_revision_positive"): (
            "runtime_revision > 0"
        ),
        ("ai_model", "ck_ai_model_runtime_revision_positive"): (
            "runtime_revision > 0"
        ),
        (
            "ai_model_capability_probe",
            "ck_ai_model_capability_probe_capabilities_object",
        ): "jsonb_typeof(capabilities) = 'object'",
        (
            "assistant_skill_capability_binding",
            "ck_assistant_skill_capability_binding_snapshot_schema_pair",
        ): (
            "resolution_status = 'unresolved' OR ( "
            "(resolution_snapshot::jsonb) ? 'outputSchema' "
            "AND (resolution_snapshot::jsonb) ? 'outputSchemaDigest' "
            "AND (resolution_snapshot::jsonb) ? 'inputSchema' "
            "AND (resolution_snapshot::jsonb) ? 'inputSchemaDigest' "
            "AND ((resolution_snapshot::jsonb)->>'outputSchemaDigest') "
            "= output_schema_digest "
            "AND ((resolution_snapshot::jsonb)->>'inputSchemaDigest') "
            "= input_schema_digest)"
        ),
        ("assistant_tool", "ck_assistant_tool_config_revision_positive"): (
            "config_revision > 0"
        ),
        (
            "operator_audit_event",
            "ck_operator_audit_event_metadata_object",
        ): "jsonb_typeof(metadata_json) = 'object'",
    }
    for (table_name, constraint_name), expected_sql in expected_checks.items():
        checks = {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert checks[constraint_name] == expected_sql, (
            f"{table_name}.{constraint_name}"
        )


def test_live_metadata_declares_captured_index_identities() -> None:
    from app.model_registry import load_all_live_models

    load_all_live_models()
    all_index_names = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
    }
    assert not (_FORBIDDEN_INDEX_NAMES & all_index_names)
    assert "ix_entry_type_id" not in {
        index.name for index in Base.metadata.tables["entry_type"].indexes
    }

    for (table_name, index_name), (columns, unique) in _CAPTURED_INDEXES.items():
        indexes = {
            index.name: index for index in Base.metadata.tables[table_name].indexes
        }
        assert index_name in indexes, f"{table_name}.{index_name}"
        index = indexes[index_name]
        assert tuple(column.name for column in index.columns) == columns
        assert index.unique is unique


def test_cyclic_pointer_foreign_keys_are_deferred_for_generation() -> None:
    from app.model_registry import load_all_live_models

    load_all_live_models()
    deferred = {
        ("assistant_agent_profile", "draft_version_id"),
        ("assistant_agent_profile", "published_version_id"),
        ("assistant_workflow", "draft_version_id"),
        ("assistant_workflow", "published_version_id"),
    }
    for table_name, column_name in deferred:
        foreign_keys = tuple(
            Base.metadata.tables[table_name].c[column_name].foreign_keys
        )
        assert len(foreign_keys) == 1
        assert foreign_keys[0].use_alter is True, (
            f"{table_name}.{column_name}"
        )


def test_sqlite_support_normalizes_postgres_contract_metadata() -> None:
    from app.model_registry import load_all_live_models
    from tests._db import make_session

    load_all_live_models()
    report_contract = {
        table_name: (
            Base.metadata.tables[table_name].c.content.type,
            frozenset(Base.metadata.tables[table_name].constraints),
        )
        for table_name in ("weekly_report", "monthly_report")
    }
    session = make_session()
    session.close()
    for table_name, (content_type, constraints) in report_contract.items():
        table = Base.metadata.tables[table_name]
        assert table.c.content.type is content_type
        assert frozenset(table.constraints) == constraints


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for model/schema equivalence",
)
def test_live_metadata_plus_retained_sql_matches_captured_clean_schema() -> None:
    from app.model_registry import load_all_live_models
    from app.schema.sql_objects import install_retained_sql_objects

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    try:
        load_all_live_models()
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            install_retained_sql_objects(connection)
            raw_actual = PostgresCatalogReader(connection).read_document()

        actual = project_logical_application_document(
            raw_actual,
            control_stage=SchemaControlStage.MODEL_REFERENCE,
        )
        expected = (
            load_logical_application_contract().logical_application_document
        )
        compare_documents(expected, actual, exclusions=None)
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for retained SQL proof",
)
def test_retained_sql_installer_rejects_collisions_before_mutation() -> None:
    from app.model_registry import load_all_live_models
    from app.schema.sql_objects import (
        SqlObjectRegistryError,
        install_retained_sql_objects,
        load_retained_sql_object_registry,
    )

    registry = load_retained_sql_object_registry()
    function = next(
        item for item in registry.creation_order if item.key.kind == "function"
    )
    assert function.key.qualifier == ""
    quoted_name = function.key.name.replace('"', '""')
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    try:
        load_all_live_models()
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            connection.execute(
                text(
                    f'CREATE FUNCTION public."{quoted_name}"() '
                    "RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RETURN NEW; END; $$"
                )
            )
            definition_before = connection.execute(
                text(
                    "SELECT pg_get_functiondef(proc.oid) "
                    "FROM pg_catalog.pg_proc AS proc "
                    "JOIN pg_catalog.pg_namespace AS ns "
                    "ON ns.oid = proc.pronamespace "
                    "WHERE ns.nspname = 'public' "
                    "AND proc.proname = :name "
                    "AND pg_get_function_identity_arguments(proc.oid) = ''"
                ),
                {"name": function.key.name},
            ).scalar_one()

            with pytest.raises(
                SqlObjectRegistryError,
                match="sql_object_collision",
            ):
                install_retained_sql_objects(connection)

            definition_after = connection.execute(
                text(
                    "SELECT pg_get_functiondef(proc.oid) "
                    "FROM pg_catalog.pg_proc AS proc "
                    "JOIN pg_catalog.pg_namespace AS ns "
                    "ON ns.oid = proc.pronamespace "
                    "WHERE ns.nspname = 'public' "
                    "AND proc.proname = :name "
                    "AND pg_get_function_identity_arguments(proc.oid) = ''"
                ),
                {"name": function.key.name},
            ).scalar_one()
            assert definition_after == definition_before
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for baseline generation",
)
def test_generator_is_byte_reproducible_and_self_contained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.schema.exclusions import LEGACY_TABLE_NAMES
    from scripts.generate_pre_ga_baseline import (
        GeneratorContext,
        generate_baseline,
    )

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()
    context = GeneratorContext(database_url=_POSTGRES_URL)

    monkeypatch.setenv("PYTHONHASHSEED", "1")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    first = generate_baseline(context)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONHASHSEED", "777")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "production")
    second = generate_baseline(context)

    assert first == second
    source = first.decode("utf-8")
    tree = ast.parse(source)
    imports = {
        ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    assert imports == {
        "from __future__ import annotations",
        "import hashlib",
        "import json",
        "import os",
        "from alembic import op",
        "import sqlalchemy as sa",
        "from sqlalchemy.dialects import postgresql",
    }
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "pre_ga_v1_0001",
        "down_revision": None,
    }
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    upgrade_source = ast.get_source_segment(source, upgrade)
    assert upgrade_source is not None
    assert "DROP " not in upgrade_source.upper()
    assert 'os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "")' in upgrade_source
    assert "schema_deployment_class_invalid" in upgrade_source
    assert "mindatlas_schema_identity" in upgrade_source
    assert "ck_schema_identity_singleton" in upgrade_source
    assert "ck_schema_identity_family" in upgrade_source
    assert "ck_schema_identity_deployment_class" in upgrade_source
    assert "ck_schema_identity_digest_shapes" in upgrade_source
    assert "ck_schema_identity_positive_versions" in upgrade_source
    assert "mindatlas_guard_schema_identity_mutation" in upgrade_source
    assert "trg_mindatlas_schema_identity_guard" in upgrade_source
    assert "runtime_identity_digest" in upgrade_source
    forbidden = set(LEGACY_TABLE_NAMES) | {
        "b6e2d4f8a901",
        "9f3c1a7e2b40",
        "app.assistant.migration",
    }
    assert all(value not in source for value in forbidden)
    deferred_foreign_keys = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.foreign_key_constraints
        if constraint.use_alter
    }
    assert None not in deferred_foreign_keys
    assert len(deferred_foreign_keys) == 19
    for constraint_name in deferred_foreign_keys:
        assert f"op.create_foreign_key('{constraint_name}'" in source

    downgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    downgrade_source = ast.get_source_segment(source, downgrade)
    assert downgrade_source is not None
    assert "schema_test_downgrade_forbidden" in downgrade_source
    assert "schema_test_downgrade_nonempty" in downgrade_source
    assert "I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION" in downgrade_source
    assert downgrade_source.count("DROP TRIGGER") == 72
    assert downgrade_source.count("DROP FUNCTION") == 31
    assert "DROP TABLE mindatlas_schema_identity" in downgrade_source
    assert 'DROP TYPE "public"."timemode"' in downgrade_source
    assert downgrade_source.index("op.drop_table('entry')") < (
        downgrade_source.index('DROP TYPE "public"."timemode"')
    )
    for constraint_name in deferred_foreign_keys:
        assert f"op.drop_constraint('{constraint_name}'" in downgrade_source
    assert downgrade_source.index("schema_test_downgrade_nonempty") < (
        downgrade_source.index("DROP TRIGGER")
    )
    assert downgrade_source.index("DROP TRIGGER") < downgrade_source.index(
        "DROP FUNCTION"
    )
    assert downgrade_source.index("DROP FUNCTION") < downgrade_source.index(
        "op.drop_constraint"
    )
    assert downgrade_source.index("op.drop_constraint") < (
        downgrade_source.index("op.drop_table")
    )

    with create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True).connect() as connection:
        keys = tuple(
            item.key for item in PostgresCatalogReader(connection).read_document().objects
        )
    assert {(key.kind, key.schema, key.name) for key in keys} == {
        ("extension", "pg_catalog", "plpgsql"),
        ("namespace", "public", "public"),
    }


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for determinism proof",
)
def test_generator_is_cross_process_byte_reproducible(tmp_path: Path) -> None:
    script = BACKEND_ROOT / "scripts" / "generate_pre_ga_baseline.py"
    output_one = tmp_path / "seed-1" / "baseline.py"
    output_two = tmp_path / "seed-777" / "baseline.py"
    cwd_two = tmp_path / "different-working-directory"
    cwd_two.mkdir()
    configurations = (
        ("1", "C", "development", BACKEND_ROOT, output_one),
        ("777", "C.UTF-8", "production", cwd_two, output_two),
    )

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()
    try:
        for hash_seed, locale_name, deployment_class, cwd, output in (
            configurations
        ):
            environment = os.environ.copy()
            environment.update(
                {
                    "CROSS_PROCESS_GENERATOR_DATABASE_URL": _POSTGRES_URL,
                    "PYTHONHASHSEED": hash_seed,
                    "LC_ALL": locale_name,
                    "LANG": locale_name,
                    "MINDATLAS_DEPLOYMENT_CLASS": deployment_class,
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--database-url-env",
                    "CROSS_PROCESS_GENERATOR_DATABASE_URL",
                    "--output",
                    str(output),
                    "--write",
                ],
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert "pre_ga_baseline_ok" in result.stdout

        first = output_one.read_bytes()
        second = output_two.read_bytes()
        assert first == second
        assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    finally:
        reset_engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
        reset_disposable_public_schema(reset_engine)
        reset_engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for baseline generation",
)
def test_generator_cli_writes_checks_and_preserves_output_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts.generate_pre_ga_baseline import main

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()
    monkeypatch.setenv("GENERATOR_TEST_DATABASE_URL", _POSTGRES_URL)
    output = tmp_path / "nested" / "pre_ga_v1_0001_clean_baseline.py"
    args = [
        "--database-url-env",
        "GENERATOR_TEST_DATABASE_URL",
        "--output",
        str(output),
    ]

    assert main([*args, "--write"]) == 0
    generated = output.read_bytes()
    assert main([*args, "--check"]) == 0
    assert output.read_bytes() == generated

    output.write_bytes(b"sentinel\n")
    with create_engine(
        _sqlalchemy_url(_POSTGRES_URL), future=True
    ).begin() as connection:
        connection.execute(text("CREATE TABLE generator_collision (id integer)"))
    try:
        assert main([*args, "--write"]) == 2
        assert output.read_bytes() == b"sentinel\n"
    finally:
        reset_engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
        reset_disposable_public_schema(reset_engine)
        reset_engine.dispose()


@pytest.mark.parametrize(
    "failure_stage",
    ("temporary_create", "file_fsync", "replace", "directory_fsync"),
)
def test_generator_atomic_write_failure_preserves_existing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    output = tmp_path / "baseline.py"
    output.write_bytes(b"old\n")

    def fail(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise OSError(failure_stage)

    if failure_stage == "temporary_create":
        monkeypatch.setattr(generator.tempfile, "mkstemp", fail)
    elif failure_stage == "file_fsync":
        monkeypatch.setattr(generator.os, "fsync", fail)
    elif failure_stage == "replace":
        monkeypatch.setattr(generator.os, "replace", fail)
    else:
        monkeypatch.setattr(generator, "_fsync_directory", fail)

    with pytest.raises(OSError, match=failure_stage):
        generator._write_atomic(output, b"new\n")

    assert output.read_bytes() == b"old\n"
    assert tuple(tmp_path.iterdir()) == (output,)


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for generated-root proof",
)
def test_generated_root_upgrade_matches_captured_clean_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from scripts.generate_pre_ga_baseline import (
        DEFAULT_STAGED_ROOT,
        GeneratorContext,
        generate_baseline,
        main,
    )

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "rehearsal")
    generated_source = generate_baseline(
        GeneratorContext(database_url=_POSTGRES_URL)
    )
    assert DEFAULT_STAGED_ROOT.read_bytes() == generated_source
    monkeypatch.setenv("STAGED_ROOT_TEST_DATABASE_URL", _POSTGRES_URL)
    assert main(
        [
            "--database-url-env",
            "STAGED_ROOT_TEST_DATABASE_URL",
            "--output",
            str(DEFAULT_STAGED_ROOT),
            "--check",
        ]
    ) == 0
    source = DEFAULT_STAGED_ROOT.read_bytes()
    namespace: dict[str, object] = {}
    exec(compile(source, "<generated-pre-ga-root>", "exec"), namespace)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                    ")"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                namespace["upgrade"]()
            raw_actual = PostgresCatalogReader(connection).read_document()
            actual = project_logical_application_document(
                raw_actual,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )
            expected = (
                load_logical_application_contract().logical_application_document
            )
            compare_documents(expected, actual, exclusions=None)
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for expected manifest proof",
)
def test_expected_manifest_is_generated_from_executed_clean_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schema.identity import DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH
    from scripts.generate_pre_ga_baseline import (
        DEFAULT_STAGED_ROOT,
        GeneratorContext,
        generate_expected_manifest,
        main,
    )

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()
    context = GeneratorContext(database_url=_POSTGRES_URL)

    generated = generate_expected_manifest(context)

    assert DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH.read_bytes() == generated
    monkeypatch.setenv("EXPECTED_MANIFEST_GENERATOR_DATABASE_URL", _POSTGRES_URL)
    assert main(
        [
            "--database-url-env",
            "EXPECTED_MANIFEST_GENERATOR_DATABASE_URL",
            "--output",
            str(DEFAULT_STAGED_ROOT),
            "--check",
            "--check-expected-manifest",
        ]
    ) == 0


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for marker drift proof",
)
def test_expected_manifest_rejects_self_consistent_marker_contract_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    from app.assistant.runtime.system_seed.expected import SEED_CONTRACT_DIGEST

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()
    drifted = generator.DEFAULT_STAGED_ROOT.read_bytes().replace(
        SEED_CONTRACT_DIGEST.encode("ascii"),
        ("f" * 64).encode("ascii"),
    )
    assert drifted != generator.DEFAULT_STAGED_ROOT.read_bytes()
    monkeypatch.setattr(generator, "generate_baseline", lambda context: drifted)

    with pytest.raises(
        generator.BaselineGenerationError,
        match="marker_contract_mismatch",
    ):
        generator.generate_expected_manifest(
            generator.GeneratorContext(database_url=_POSTGRES_URL)
        )


def test_combined_write_preserves_root_when_expected_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    output = tmp_path / "baseline.py"
    output.write_bytes(b"old-root")
    monkeypatch.setenv("ATOMIC_EXPECTED_TEST_DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(generator, "generate_baseline", lambda context: b"new-root")

    def fail_expected(context):  # noqa: ANN001, ANN202
        raise generator.BaselineGenerationError(
            "expected_manifest_generation_failed"
        )

    monkeypatch.setattr(generator, "generate_expected_manifest", fail_expected)

    result = generator.main(
        [
            "--database-url-env",
            "ATOMIC_EXPECTED_TEST_DATABASE_URL",
            "--output",
            str(output),
            "--write",
            "--write-expected-manifest",
        ]
    )

    assert result == 2
    assert output.read_bytes() == b"old-root"


@pytest.mark.parametrize(
    ("root_before", "expected_before"),
    (
        (b"old-root", b"old-expected"),
        (None, b"old-expected"),
        (b"old-root", None),
        (None, None),
    ),
)
@pytest.mark.parametrize(
    "failure_stage",
    (
        "first_replace",
        "second_replace",
        "first_directory_fsync",
        "second_directory_fsync",
    ),
)
def test_group_atomic_writer_restores_both_artifacts_on_single_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    root_before: bytes | None,
    expected_before: bytes | None,
    failure_stage: str,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    root = tmp_path / "root" / "baseline.py"
    expected = tmp_path / "manifest" / "expected.json"
    root.parent.mkdir()
    expected.parent.mkdir()
    if root_before is not None:
        root.write_bytes(root_before)
    if expected_before is not None:
        expected.write_bytes(expected_before)

    real_replace = generator.os.replace
    replace_calls = 0

    def faulting_replace(source, destination):  # noqa: ANN001, ANN202
        nonlocal replace_calls
        if Path(destination) in {root, expected}:
            replace_calls += 1
            if (
                failure_stage == "first_replace" and replace_calls == 1
            ) or (
                failure_stage == "second_replace" and replace_calls == 2
            ):
                raise OSError("injected replace failure")
        return real_replace(source, destination)

    directory_fsync_calls = 0

    def faulting_directory_fsync(path):  # noqa: ANN001, ANN202
        nonlocal directory_fsync_calls
        directory_fsync_calls += 1
        ordinal = "first" if directory_fsync_calls == 1 else "second"
        if failure_stage == f"{ordinal}_directory_fsync":
            raise OSError("injected directory fsync failure")

    monkeypatch.setattr(generator.os, "replace", faulting_replace)
    monkeypatch.setattr(generator, "_fsync_directory", faulting_directory_fsync)

    with pytest.raises(OSError):
        generator._write_atomic_group(
            ((root, b"new-root"), (expected, b"new-expected"))
        )

    assert (root.read_bytes() if root.exists() else None) == root_before
    assert (expected.read_bytes() if expected.exists() else None) == expected_before
    assert set(root.parent.iterdir()) == ({root} if root_before is not None else set())
    assert set(expected.parent.iterdir()) == (
        {expected} if expected_before is not None else set()
    )


def test_group_atomic_writer_rejects_aliases_of_the_same_destination(
    tmp_path: Path,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    real_directory = tmp_path / "real"
    real_directory.mkdir()
    alias_directory = tmp_path / "alias"
    alias_directory.symlink_to(real_directory, target_is_directory=True)
    real_output = real_directory / "artifact"
    alias_output = alias_directory / "artifact"
    real_output.write_bytes(b"old")
    assert real_output.resolve() == alias_output.resolve()

    with pytest.raises(
        generator.BaselineGenerationError,
        match="atomic_output_paths_collide",
    ):
        generator._write_atomic_group(
            ((real_output, b"root"), (alias_output, b"expected"))
        )

    assert real_output.read_bytes() == b"old"
    assert set(real_directory.iterdir()) == {real_output}


def test_group_atomic_writer_commits_two_distinct_paths_without_residue(
    tmp_path: Path,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    root = tmp_path / "root" / "baseline.py"
    expected = tmp_path / "manifest" / "expected.json"
    root.parent.mkdir()
    expected.parent.mkdir()
    root.write_bytes(b"old-root")

    generator._write_atomic_group(
        ((root, b"new-root"), (expected, b"new-expected"))
    )

    assert root.read_bytes() == b"new-root"
    assert expected.read_bytes() == b"new-expected"
    assert set(root.parent.iterdir()) == {root}
    assert set(expected.parent.iterdir()) == {expected}


def test_combined_cli_rejects_output_manifest_alias_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    manifest_root = tmp_path / "manifests"
    manifest_root.mkdir()
    alias_root = tmp_path / "manifest-alias"
    alias_root.symlink_to(manifest_root, target_is_directory=True)
    expected = manifest_root / "pre_ga_v1-expected.json"
    expected.write_bytes(b"old-expected")
    alias_output = alias_root / expected.name
    context = generator.GeneratorContext(
        database_url="postgresql://unused",
        manifest_root=manifest_root,
    )
    monkeypatch.setattr(
        generator,
        "GeneratorContext",
        lambda database_url: context,
    )
    monkeypatch.setenv("COLLISION_TEST_DATABASE_URL", "postgresql://unused")

    def generation_must_not_start(context):  # noqa: ANN001, ANN202
        raise AssertionError("generation must not start for colliding outputs")

    monkeypatch.setattr(generator, "generate_baseline", generation_must_not_start)

    result = generator.main(
        [
            "--database-url-env",
            "COLLISION_TEST_DATABASE_URL",
            "--output",
            str(alias_output),
            "--write",
            "--write-expected-manifest",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "atomic_output_paths_collide"
    assert expected.read_bytes() == b"old-expected"


def test_combined_cli_rejects_symlink_loop_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)
    monkeypatch.setenv("SYMLINK_LOOP_TEST_DATABASE_URL", "postgresql://unused")

    def generation_must_not_start(context):  # noqa: ANN001, ANN202
        raise AssertionError("generation must not start for invalid output paths")

    monkeypatch.setattr(generator, "generate_baseline", generation_must_not_start)

    result = generator.main(
        [
            "--database-url-env",
            "SYMLINK_LOOP_TEST_DATABASE_URL",
            "--output",
            str(loop / "baseline.py"),
            "--write",
            "--write-expected-manifest",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "atomic_output_path_invalid"
    assert set(tmp_path.iterdir()) == {loop}


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for bounded marker failure proof",
)
def test_expected_manifest_wraps_marker_reader_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.generate_pre_ga_baseline as generator

    from app.schema.identity import SchemaIdentityError

    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    engine.dispose()

    def fail_marker(connection):  # noqa: ANN001, ANN202
        raise SchemaIdentityError("marker_malformed")

    monkeypatch.setattr(generator, "read_schema_identity", fail_marker)

    with pytest.raises(
        generator.BaselineGenerationError,
        match="expected_manifest_generation_failed",
    ):
        generator.generate_expected_manifest(
            generator.GeneratorContext(database_url=_POSTGRES_URL)
        )


def test_generator_rejects_multiple_live_roots(tmp_path: Path) -> None:
    from scripts.generate_pre_ga_baseline import (
        BaselineGenerationError,
        require_single_live_root,
    )

    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "old.py").write_text(
        'revision = "old_root"\ndown_revision = None\n',
        encoding="utf-8",
    )
    (versions / "clean.py").write_text(
        'revision = "pre_ga_v1_0001"\ndown_revision = None\n',
        encoding="utf-8",
    )

    with pytest.raises(
        BaselineGenerationError,
        match="live_revision_roots_invalid",
    ):
        require_single_live_root(versions)


def test_generator_allows_only_the_sole_clean_root_in_live_destination(
    tmp_path: Path,
) -> None:
    from scripts.generate_pre_ga_baseline import (
        BaselineGenerationError,
        require_safe_output_destination,
        require_single_live_root,
    )

    versions = tmp_path / "versions"
    staging = tmp_path / "staging"
    versions.mkdir()
    staging.mkdir()
    old = versions / "old.py"
    clean = versions / "clean.py"
    old.write_text(
        'revision = "old_root"\ndown_revision = None\n',
        encoding="utf-8",
    )

    roots = require_single_live_root(versions)
    require_safe_output_destination(staging / clean.name, versions, roots)
    with pytest.raises(
        BaselineGenerationError,
        match="live_version_destination_forbidden",
    ):
        require_safe_output_destination(clean, versions, roots)

    old.unlink()
    clean.write_text(
        'revision = "pre_ga_v1_0001"\ndown_revision = None\n',
        encoding="utf-8",
    )
    clean_roots = require_single_live_root(versions)
    assert clean_roots == ("pre_ga_v1_0001",)
    require_safe_output_destination(clean, versions, clean_roots)
