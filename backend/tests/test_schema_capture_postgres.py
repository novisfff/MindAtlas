from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from scripts import capture_pre_ga_schema as capture_script
from tests.postgres_destructive_guard import reset_disposable_public_schema


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = BACKEND_ROOT / "scripts" / "capture_pre_ga_schema.py"
PLAN1_HEAD = "9f3c1a7e2b40"
PRE_SQUASH_HEAD = "b6e2d4f8a901"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for schema capture coverage",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _alembic_upgrade(url: str, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["MINDATLAS_PLAN10_B2_TEST_OVERRIDE"] = "1"
    env.setdefault("APP_ENV", "test")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _run_capture(
    url: str,
    output_dir: Path,
    *,
    mode: str = "write",
) -> subprocess.CompletedProcess[str]:
    env_name = "MINDATLAS_TEST_SCHEMA_CAPTURE_URL"
    manifest_env = "MINDATLAS_TEST_SCHEMA_MANIFEST_DIR"
    env = os.environ.copy()
    env[env_name] = url
    env[manifest_env] = str(output_dir)
    runner = (
        "import os, sys; from pathlib import Path; "
        "from scripts.capture_pre_ga_schema import main; "
        f"raise SystemExit(main(sys.argv[1:], _test_manifest_root="
        f"Path(os.environ[{manifest_env!r}])))"
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            runner,
            "--database-url-env",
            env_name,
            f"--{mode}",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def postgres_engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def test_capture_refuses_wrong_head(postgres_engine: Engine, tmp_path: Path) -> None:
    _alembic_upgrade(_POSTGRES_URL, PLAN1_HEAD)

    result = _run_capture(_POSTGRES_URL, tmp_path)

    assert result.returncode != 0
    assert "pre_squash_head_mismatch" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_capture_sanitizes_invalid_database_url(tmp_path: Path) -> None:
    secret = "not-a-database-url-with-secret-material"

    result = _run_capture(secret, tmp_path)

    assert result.returncode != 0
    assert result.stderr.strip() == "schema_source_unavailable"
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_public_capture_cli_ignores_test_manifest_root_environment(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)
    env_name = "MINDATLAS_TEST_SCHEMA_CAPTURE_URL"
    env = os.environ.copy()
    env[env_name] = _POSTGRES_URL
    env["APP_ENV"] = "test"
    env["MINDATLAS_SCHEMA_MANIFEST_DIR"] = str(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE_SCRIPT),
            "--database-url-env",
            env_name,
            "--check",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_capture_refuses_nonempty_legacy_evidence(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO assistant_runtime_migration_item
                    (id, subject_kind, source_type, source_id, source_name,
                     source_name_normalized, source_digest, evidence_json,
                     source_revision, target_revision, attempt_count,
                     state_revision, state, created_at, updated_at)
                VALUES
                    ('00000000-0000-0000-0000-000000000001', 'skill',
                     'legacy', 'x', '', '', :digest, '{}'::json, 0, 0, 0,
                     0, 'discovered', NOW(), NOW())
                """
            ),
            {"digest": "a" * 64},
        )

    result = _run_capture(_POSTGRES_URL, tmp_path)

    assert result.returncode != 0
    assert "legacy_exclusion_data_present" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["missing_control", "nonzero_revision"])
def test_capture_refuses_non_inert_rollout_control(
    postgres_engine: Engine,
    tmp_path: Path,
    mutation: str,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)
    with postgres_engine.begin() as connection:
        if mutation == "missing_control":
            connection.execute(text("DELETE FROM assistant_runtime_rollout_control"))
        elif mutation == "nonzero_revision":
            connection.execute(
                text(
                    "UPDATE assistant_runtime_rollout_control "
                    "SET state_revision = 1"
                )
            )
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(mutation)

    result = _run_capture(_POSTGRES_URL, tmp_path)

    assert result.returncode != 0
    assert result.stderr.strip() == "legacy_exclusion_data_present"
    assert list(tmp_path.iterdir()) == []


def test_atomic_manifest_install_rolls_back_second_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = {
        name: (capture_script.DEFAULT_MANIFEST_ROOT / name).read_bytes()
        for name in capture_script._FILENAMES
    }
    paths = capture_script._destination_paths(tmp_path)
    real_replace = capture_script.os.replace
    call_count = 0

    def fail_second_replace(source: str, destination: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("injected second rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(capture_script.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="injected second rename failure"):
        capture_script._write_outputs(paths, outputs)

    assert list(tmp_path.iterdir()) == []


def test_capture_writes_three_sanitized_manifests_and_checks_byte_identity(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)

    write_result = _run_capture(_POSTGRES_URL, tmp_path)

    assert write_result.returncode == 0, write_result.stderr
    paths = sorted(path.name for path in tmp_path.iterdir())
    assert paths == [
        "pre_ga_v1-exclusions.json",
        "pre_ga_v1-pre-squash-schema.json",
        "pre_ga_v1-sql-objects.json",
    ]
    exclusions = json.loads(
        (tmp_path / "pre_ga_v1-exclusions.json").read_text(encoding="utf-8")
    )
    snapshot = json.loads(
        (tmp_path / "pre_ga_v1-pre-squash-schema.json").read_text(encoding="utf-8")
    )
    registry = json.loads(
        (tmp_path / "pre_ga_v1-sql-objects.json").read_text(encoding="utf-8")
    )
    assert len(exclusions["objects"]) == 27
    assert snapshot["legacyBusinessRowCount"] == 0
    assert snapshot["knownInertSeedRowCount"] == 1
    assert len(snapshot["sourceDocument"]["objects"]) == 207
    assert len(snapshot["normalizedApplicationDocument"]["objects"]) == 180
    assert len(registry["objects"]) == 101
    assert _POSTGRES_URL not in write_result.stdout
    assert _POSTGRES_URL not in write_result.stderr
    assert "CREATE " not in write_result.stdout
    assert "CREATE " not in write_result.stderr
    for path in tmp_path.iterdir():
        content = path.read_text("utf-8")
        assert _POSTGRES_URL not in content
        assert "plan3_local_test" not in content
        assert "127.0.0.1" not in content
        assert str(tmp_path) not in content

    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    check_result = _run_capture(_POSTGRES_URL, tmp_path, mode="check")
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert check_result.returncode == 0, check_result.stderr
    assert before == after


def test_capture_reads_one_repeatable_read_read_only_snapshot(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)
    observed: dict[str, str] = {}
    real_reader = capture_script.PostgresCatalogReader

    class SnapshotRecordingReader(real_reader):
        def read_document(self):  # noqa: ANN201
            observed["isolation"] = self.connection.execute(
                text("SHOW transaction_isolation")
            ).scalar_one()
            observed["read_only"] = self.connection.execute(
                text("SHOW transaction_read_only")
            ).scalar_one()
            return super().read_document()

    monkeypatch.setattr(
        capture_script,
        "PostgresCatalogReader",
        SnapshotRecordingReader,
    )

    capture_script._build_outputs(_POSTGRES_URL)

    assert observed == {
        "isolation": "repeatable read",
        "read_only": "on",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("excluded_function_body", "schema_capture_drift"),
        ("excluded_trigger_rename", "legacy_exclusion_object_missing"),
        ("prefix_only_table", "schema_capture_drift"),
        ("retained_fk_to_excluded", "legacy_exclusion_live_dependency"),
        ("retained_view_to_excluded", "legacy_exclusion_live_dependency"),
        ("retained_sequence_owned_by_excluded", "legacy_exclusion_live_dependency"),
    ],
)
def test_capture_drift_cases_fail_closed_without_changing_manifests(
    postgres_engine: Engine,
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    _alembic_upgrade(_POSTGRES_URL, PRE_SQUASH_HEAD)
    baseline = _run_capture(_POSTGRES_URL, tmp_path)
    assert baseline.returncode == 0, baseline.stderr
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with postgres_engine.begin() as connection:
        if mutation == "excluded_function_body":
            definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef("
                    "'public.mindatlas_reject_plan10_immutable_mutation()'"
                    "::regprocedure)"
                )
            ).scalar_one()
            cursor = connection.connection.driver_connection.cursor()
            try:
                cursor.execute(
                    str(definition).replace("is not allowed", "is forbidden", 1)
                )
            finally:
                cursor.close()
        elif mutation == "excluded_trigger_rename":
            connection.execute(
                text(
                    "ALTER TRIGGER "
                    "trg_assistant_runtime_rollout_revision_reject_update "
                    "ON assistant_runtime_rollout_revision RENAME TO "
                    "trg_assistant_runtime_rollout_revision_unreviewed"
                )
            )
        elif mutation == "prefix_only_table":
            connection.execute(
                text("CREATE TABLE assistant_runtime_unreviewed (id integer)")
            )
        elif mutation == "retained_fk_to_excluded":
            connection.execute(
                text(
                    """
                    CREATE TABLE capture_retained_dependency (
                        id uuid PRIMARY KEY,
                        legacy_id uuid REFERENCES assistant_runtime_migration_item(id)
                    )
                    """
                )
            )
        elif mutation == "retained_view_to_excluded":
            connection.execute(
                text(
                    "CREATE VIEW capture_retained_view AS "
                    "SELECT id FROM assistant_runtime_migration_item"
                )
            )
        elif mutation == "retained_sequence_owned_by_excluded":
            connection.execute(text("CREATE SEQUENCE capture_retained_sequence"))
            connection.execute(
                text(
                    "ALTER SEQUENCE capture_retained_sequence OWNED BY "
                    "assistant_runtime_migration_item.source_revision"
                )
            )
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(mutation)

    result = _run_capture(_POSTGRES_URL, tmp_path)
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert result.returncode != 0
    assert result.stderr.strip() == expected_code
    assert before == after
