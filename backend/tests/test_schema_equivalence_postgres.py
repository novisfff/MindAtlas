from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import warnings

import pytest
from sqlalchemy import create_engine, text

from app.schema.application_contract import (
    SchemaControlStage,
    project_logical_application_document,
)
from app.schema.canonical import SchemaComparisonError, compare_documents
from app.schema.application_contract import load_logical_application_contract
from app.schema.sql_objects import load_pre_squash_snapshot
from scripts.verify_pre_ga_schema import (
    EquivalenceVerification,
    SchemaVerificationError,
)
from scripts import verify_pre_ga_schema as verifier
from tests.schema_baseline_support import (
    build_clean_root_alembic_directory,
    run_staged_alembic,
    temporary_postgres_databases,
)
from tests import schema_baseline_support


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
BACKEND_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for schema equivalence proof",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _committed_old_logical_document():  # noqa: ANN202
    return project_logical_application_document(
        load_pre_squash_snapshot().normalized_application_document,
        control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
    )


def test_clean_root_matches_committed_normalized_old_snapshot(
    tmp_path: Path,
) -> None:
    with temporary_postgres_databases(
        _POSTGRES_URL,
        labels=("clean_root",),
    ) as (clean_database_url,):
        staged = build_clean_root_alembic_directory(tmp_path)
        clean_upgrade = run_staged_alembic(
            staged,
            "upgrade",
            "head",
            database_url=clean_database_url,
            deployment_class="rehearsal",
        )
        assert clean_upgrade.returncode == 0, clean_upgrade.stderr

        clean_document, _marker = verifier._read_clean_database(
            clean_database_url
        )

        logical_old = _committed_old_logical_document()
        logical_clean = project_logical_application_document(
            clean_document,
            control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
        )

        compare_documents(logical_old, logical_clean, exclusions=None)
        compare_documents(
            load_logical_application_contract().logical_application_document,
            logical_clean,
            exclusions=None,
        )

        result = verifier.verify_fresh(
            clean_database_url=clean_database_url,
            deployment_class="rehearsal",
        )
        expected_fingerprint = (
            load_logical_application_contract().logical_application_fingerprint
        )
        assert result.application_fingerprint == expected_fingerprint


@pytest.mark.parametrize(
    "mutation_sql",
    (
        'ALTER TABLE entry_type ALTER COLUMN description SET NOT NULL',
        """
        ALTER TABLE ai_component_binding
        DROP CONSTRAINT ai_component_binding_embedding_model_id_fkey;
        ALTER TABLE ai_component_binding
        ADD CONSTRAINT ai_component_binding_embedding_model_id_fkey
        FOREIGN KEY (embedding_model_id) REFERENCES ai_model(id)
        ON DELETE CASCADE
        """,
        """
        ALTER TABLE ai_component_binding
        DROP CONSTRAINT ck_ai_component_binding_component;
        ALTER TABLE ai_component_binding
        ADD CONSTRAINT ck_ai_component_binding_component
        CHECK (component IN ('assistant', 'lightrag'))
        """,
        """
        DROP INDEX ix_entry_type_code;
        CREATE INDEX ix_entry_type_code ON entry_type ((lower(code)))
        """,
        """
        CREATE OR REPLACE FUNCTION mindatlas_reject_operator_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $function$
        BEGIN
            RETURN NEW;
        END;
        $function$
        """,
        """
        ALTER TABLE operator_audit_event
        DISABLE TRIGGER trg_operator_audit_event_append_only
        """,
        "ALTER TYPE timemode ADD VALUE 'MUTATED' BEFORE 'POINT'",
        "ALTER SEQUENCE assistant_chat_run_event_id_seq INCREMENT BY 2",
        "CREATE SCHEMA equivalence_drift",
        "CREATE EXTENSION hstore",
    ),
    ids=(
        "column_nullability",
        "foreign_key_delete_action",
        "check_constraint_body",
        "index_expression",
        "function_body",
        "trigger_enabled_state",
        "enum_label_order",
        "sequence_increment",
        "extra_namespace",
        "extra_extension",
    ),
)
def test_clean_root_semantic_drift_fails_exact_equivalence(
    tmp_path: Path,
    mutation_sql: str,
) -> None:
    with temporary_postgres_databases(
        _POSTGRES_URL,
        labels=("clean_root",),
    ) as (clean_database_url,):
        staged = build_clean_root_alembic_directory(tmp_path)
        clean_upgrade = run_staged_alembic(
            staged,
            "upgrade",
            "head",
            database_url=clean_database_url,
            deployment_class="rehearsal",
        )
        assert clean_upgrade.returncode == 0, clean_upgrade.stderr

        clean_engine = create_engine(
            _sqlalchemy_url(clean_database_url),
            future=True,
        )
        try:
            with clean_engine.begin() as connection:
                connection.execute(text(mutation_sql))
        finally:
            clean_engine.dispose()

        clean_document, _marker = verifier._read_clean_database(
            clean_database_url
        )

        with pytest.raises(SchemaComparisonError) as exc:
            logical_clean = project_logical_application_document(
                clean_document,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )
            compare_documents(
                _committed_old_logical_document(),
                logical_clean,
                exclusions=None,
            )

        assert exc.value.safe_code == "unmanifested_schema_difference"


def test_equivalence_cli_prints_only_safe_verification_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_url = "postgresql://old-sensitive-url"
    clean_url = "postgresql://clean-sensitive-url"
    monkeypatch.setenv("TASK7_OLD_DATABASE_URL", old_url)
    monkeypatch.setenv("TASK7_CLEAN_DATABASE_URL", clean_url)
    monkeypatch.setattr(
        verifier,
        "verify_equivalence",
        lambda **kwargs: EquivalenceVerification(
            old_application_fingerprint="1" * 64,
            clean_application_fingerprint="1" * 64,
            clean_control_fingerprint="2" * 64,
            exclusion_count=27,
        ),
    )

    result = verifier.main(
        [
            "equivalence",
            "--old-database-url-env",
            "TASK7_OLD_DATABASE_URL",
            "--clean-database-url-env",
            "TASK7_CLEAN_DATABASE_URL",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.strip() == (
        "schema_equivalence_ok "
        "old_head=b6e2d4f8a901 clean_head=pre_ga_v1_0001 "
        f"application_fingerprint={'1' * 64} "
        f"control_fingerprint={'2' * 64} exclusions=27"
    )
    assert old_url not in captured.out
    assert clean_url not in captured.out


def test_fresh_verification_binds_expected_deployment_class(
    tmp_path: Path,
) -> None:
    with temporary_postgres_databases(
        _POSTGRES_URL,
        labels=("clean_root",),
    ) as (clean_database_url,):
        staged = build_clean_root_alembic_directory(tmp_path)
        clean_upgrade = run_staged_alembic(
            staged,
            "upgrade",
            "head",
            database_url=clean_database_url,
            deployment_class="rehearsal",
        )
        assert clean_upgrade.returncode == 0, clean_upgrade.stderr

        result = verifier.verify_fresh(
            clean_database_url=clean_database_url,
            deployment_class="rehearsal",
        )

        expected = load_logical_application_contract()
        assert result.application_fingerprint == (
            expected.logical_application_fingerprint
        )
        assert result.deployment_class == "rehearsal"

        with pytest.raises(SchemaVerificationError) as exc:
            verifier.verify_fresh(
                clean_database_url=clean_database_url,
                deployment_class="production",
            )
        assert exc.value.safe_code == "marker_contract_mismatch"


def test_fresh_cli_prints_only_safe_verification_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clean_url = "postgresql://clean-sensitive-url"
    monkeypatch.setenv("TASK7_CLEAN_DATABASE_URL", clean_url)
    monkeypatch.setattr(
        verifier,
        "verify_fresh",
        lambda **kwargs: verifier.FreshVerification(
            application_fingerprint="1" * 64,
            control_fingerprint="2" * 64,
            deployment_class="rehearsal",
            exclusion_count=27,
        ),
    )

    result = verifier.main(
        [
            "fresh",
            "--clean-database-url-env",
            "TASK7_CLEAN_DATABASE_URL",
            "--deployment-class",
            "rehearsal",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.strip() == (
        "schema_fresh_ok clean_head=pre_ga_v1_0001 "
        "deployment_class=rehearsal "
        f"application_fingerprint={'1' * 64} "
        f"control_fingerprint={'2' * 64} exclusions=27"
    )
    assert clean_url not in captured.out


def test_verification_script_is_directly_executable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify_pre_ga_schema.py", "--help"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_equivalence_cli_sanitizes_invalid_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid_url = "not-a-database-url-with-sensitive-material"
    monkeypatch.setenv("TASK7_INVALID_OLD_URL", invalid_url)
    monkeypatch.setenv("TASK7_UNUSED_CLEAN_URL", "postgresql://unused")

    result = verifier.main(
        [
            "equivalence",
            "--old-database-url-env",
            "TASK7_INVALID_OLD_URL",
            "--clean-database-url-env",
            "TASK7_UNUSED_CLEAN_URL",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "schema_source_unavailable"
    assert invalid_url not in captured.err
    assert "Traceback" not in captured.err


def test_fresh_cli_sanitizes_invalid_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid_url = "not-a-clean-database-url-with-sensitive-material"
    monkeypatch.setenv("TASK7_INVALID_CLEAN_URL", invalid_url)

    result = verifier.main(
        [
            "fresh",
            "--clean-database-url-env",
            "TASK7_INVALID_CLEAN_URL",
            "--deployment-class",
            "rehearsal",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "clean_schema_unavailable"
    assert invalid_url not in captured.err
    assert "Traceback" not in captured.err


def test_direct_cli_invalid_url_emits_only_safe_code() -> None:
    environment = os.environ.copy()
    environment["TASK7_DIRECT_INVALID_OLD_URL"] = "not-a-database-url"
    environment["TASK7_DIRECT_UNUSED_CLEAN_URL"] = "postgresql://unused"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_pre_ga_schema.py",
            "equivalence",
            "--old-database-url-env",
            "TASK7_DIRECT_INVALID_OLD_URL",
            "--clean-database-url-env",
            "TASK7_DIRECT_UNUSED_CLEAN_URL",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "schema_source_unavailable\n"


def test_direct_fresh_cli_invalid_url_emits_only_safe_code() -> None:
    environment = os.environ.copy()
    environment["TASK7_DIRECT_INVALID_CLEAN_URL"] = "not-a-database-url"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_pre_ga_schema.py",
            "fresh",
            "--clean-database-url-env",
            "TASK7_DIRECT_INVALID_CLEAN_URL",
            "--deployment-class",
            "rehearsal",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "clean_schema_unavailable\n"


def test_cli_converts_unexpected_warning_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TASK7_WARNING_OLD_URL", "postgresql://unused-old")
    monkeypatch.setenv("TASK7_WARNING_CLEAN_URL", "postgresql://unused-clean")

    def warning_verification(**kwargs):  # noqa: ANN003, ANN202
        warnings.warn("sensitive-unexpected-warning", UserWarning)
        return EquivalenceVerification(
            old_application_fingerprint="1" * 64,
            clean_application_fingerprint="1" * 64,
            clean_control_fingerprint="2" * 64,
            exclusion_count=27,
        )

    monkeypatch.setattr(verifier, "verify_equivalence", warning_verification)

    result = verifier.main(
        [
            "equivalence",
            "--old-database-url-env",
            "TASK7_WARNING_OLD_URL",
            "--clean-database-url-env",
            "TASK7_WARNING_CLEAN_URL",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "verification_warning_unexpected\n"
    assert "sensitive-unexpected-warning" not in captured.err


def test_temporary_database_cleanup_continues_after_first_drop_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.drop_calls = 0

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001, ANN204
            return False

        def execute(self, statement, parameters=None):  # noqa: ANN001, ANN201
            if str(statement).startswith("DROP DATABASE"):
                self.drop_calls += 1
                if self.drop_calls == 1:
                    raise OSError("injected first cleanup failure")
            return None

    class FakeEngine:
        def __init__(self) -> None:
            self.connection = FakeConnection()
            self.disposed = False

        def connect(self):  # noqa: ANN201
            return self.connection

        def dispose(self) -> None:
            self.disposed = True

    fake_engine = FakeEngine()
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")
    monkeypatch.setattr(
        schema_baseline_support,
        "create_engine",
        lambda *args, **kwargs: fake_engine,
    )

    with pytest.raises(OSError, match="injected first cleanup failure"):
        with temporary_postgres_databases(
            "postgresql://user:password@127.0.0.1/"
            "mindatlas_test_pre_ga_v1_cleanup",
            labels=("old_chain", "clean_root"),
        ):
            pass

    assert fake_engine.connection.drop_calls == 2
    assert fake_engine.disposed is True


@pytest.mark.parametrize(
    ("mutation_sql", "safe_code"),
    (
        (
            "CREATE TABLE assistant_runtime_migration_item (id integer)",
            "exclusion_object_present_in_clean_schema",
        ),
        (
            """
            ALTER TABLE mindatlas_schema_identity
            DISABLE TRIGGER trg_mindatlas_schema_identity_guard
            """,
            "schema_control_contract_drift",
        ),
    ),
    ids=(
        "clean_legacy_object",
        "clean_control_drift",
    ),
)
def test_fresh_verification_rejects_invalid_clean_schema(
    tmp_path: Path,
    mutation_sql: str,
    safe_code: str,
) -> None:
    with temporary_postgres_databases(
        _POSTGRES_URL,
        labels=("clean_root",),
    ) as (clean_database_url,):
        staged = build_clean_root_alembic_directory(tmp_path)
        clean_upgrade = run_staged_alembic(
            staged,
            "upgrade",
            "head",
            database_url=clean_database_url,
            deployment_class="rehearsal",
        )
        assert clean_upgrade.returncode == 0, clean_upgrade.stderr

        engine = create_engine(_sqlalchemy_url(clean_database_url), future=True)
        try:
            with engine.begin() as connection:
                connection.execute(text(mutation_sql))
        finally:
            engine.dispose()

        with pytest.raises(SchemaVerificationError) as exc:
            verifier.verify_fresh(
                clean_database_url=clean_database_url,
                deployment_class="rehearsal",
            )
        assert exc.value.safe_code == safe_code
