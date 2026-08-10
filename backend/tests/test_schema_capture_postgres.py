from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.schema.application_contract import (
    PRE_SQUASH_CONTROL_CONTRACT_DIGEST,
    LogicalApplicationContractError,
    SchemaControlStage,
    project_logical_application_document,
)
from app.schema.canonical import canonical_json_bytes, sha256_canonical_json
from app.schema.sql_objects import (
    load_exclusion_manifest,
    load_pre_squash_snapshot,
)
from app.schema.contracts import CLEAN_ROOT_REVISION, PRE_SQUASH_HEAD
from scripts import capture_pre_ga_schema as capture_script
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.pre_squash_fixture import install_pre_squash_fixture


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = BACKEND_ROOT / "scripts" / "capture_pre_ga_schema.py"
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
    env["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    env["APP_BUILD_REVISION"] = "test-schema-capture"
    env["APP_ENV"] = "test"
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
    _alembic_upgrade(_POSTGRES_URL, CLEAN_ROOT_REVISION)

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
    install_pre_squash_fixture(_POSTGRES_URL)
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
    install_pre_squash_fixture(_POSTGRES_URL)
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
    install_pre_squash_fixture(_POSTGRES_URL)
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
    outputs = _four_manifest_outputs()
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


def _four_manifest_outputs() -> dict[str, bytes]:
    old_names = (
        "pre_ga_v1-exclusions.json",
        "pre_ga_v1-pre-squash-schema.json",
        "pre_ga_v1-sql-objects.json",
    )
    outputs = {
        name: (capture_script.DEFAULT_MANIFEST_ROOT / name).read_bytes()
        for name in old_names
    }
    snapshot = load_pre_squash_snapshot()
    exclusions = load_exclusion_manifest()
    logical_document = project_logical_application_document(
        snapshot.normalized_application_document,
        control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
    )
    logical_payload = {
        "schemaVersion": 1,
        "schemaFamily": "pre_ga_v1",
        "sourceHead": PRE_SQUASH_HEAD,
        "sourceSnapshotDigest": snapshot.snapshot_digest,
        "exclusionManifestDigest": exclusions.manifest_digest,
        "controlContractDigest": PRE_SQUASH_CONTROL_CONTRACT_DIGEST,
        "canonicalizationVersion": 2,
        "logicalApplicationDocument": logical_document.to_payload(),
        "logicalApplicationFingerprint": sha256_canonical_json(
            logical_document.to_payload()
        ),
    }
    logical_manifest = {
        **logical_payload,
        "manifestDigest": sha256_canonical_json(logical_payload),
    }
    outputs["pre_ga_v1-clean-application-contract.json"] = (
        canonical_json_bytes(logical_manifest) + b"\n"
    )
    return outputs


def test_atomic_manifest_extension_adds_only_verified_fourth_artifact(
    tmp_path: Path,
) -> None:
    outputs = _four_manifest_outputs()
    old_names = (
        "pre_ga_v1-exclusions.json",
        "pre_ga_v1-pre-squash-schema.json",
        "pre_ga_v1-sql-objects.json",
    )
    for name in old_names:
        (tmp_path / name).write_bytes(outputs[name])
    before = {name: (tmp_path / name).read_bytes() for name in old_names}

    capture_script._write_outputs(
        capture_script._destination_paths(tmp_path),
        outputs,
    )

    assert (tmp_path / "pre_ga_v1-clean-application-contract.json").read_bytes() == (
        outputs["pre_ga_v1-clean-application-contract.json"]
    )
    assert {name: (tmp_path / name).read_bytes() for name in old_names} == before


def test_atomic_manifest_extension_rejects_old_manifest_drift(
    tmp_path: Path,
) -> None:
    outputs = _four_manifest_outputs()
    old_names = (
        "pre_ga_v1-exclusions.json",
        "pre_ga_v1-pre-squash-schema.json",
        "pre_ga_v1-sql-objects.json",
    )
    for name in old_names:
        (tmp_path / name).write_bytes(outputs[name])
    (tmp_path / old_names[0]).write_bytes(b"{}\n")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(capture_script.CaptureError) as exc:
        capture_script._write_outputs(
            capture_script._destination_paths(tmp_path),
            outputs,
        )

    assert exc.value.safe_code == "schema_capture_drift"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_atomic_manifest_extension_rejects_other_partial_sets(
    tmp_path: Path,
) -> None:
    outputs = _four_manifest_outputs()
    logical_name = "pre_ga_v1-clean-application-contract.json"
    (tmp_path / logical_name).write_bytes(outputs[logical_name])
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(capture_script.CaptureError) as exc:
        capture_script._write_outputs(
            capture_script._destination_paths(tmp_path),
            outputs,
        )

    assert exc.value.safe_code == "schema_capture_drift"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_temporary_logical_validation_failure_is_bounded_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = _four_manifest_outputs()
    sentinel = "postgresql://user:secret@host/db"

    def fail_logical_validation(*args: object, **kwargs: object) -> None:
        try:
            raise ValueError(sentinel)
        except ValueError as exc:
            raise LogicalApplicationContractError(
                "logical_schema_manifest_invalid"
            ) from exc

    monkeypatch.setattr(
        capture_script,
        "load_logical_application_contract",
        fail_logical_validation,
    )

    with pytest.raises(capture_script.CaptureError) as exc:
        capture_script._write_outputs(
            capture_script._destination_paths(tmp_path),
            outputs,
        )

    assert exc.value.safe_code == "schema_capture_drift"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert sentinel not in "".join(
        traceback.format_exception(
            type(exc.value),
            exc.value,
            exc.value.__traceback__,
        )
    )
    assert list(tmp_path.iterdir()) == []


def test_capture_writes_four_sanitized_manifests_and_checks_byte_identity(
    postgres_engine: Engine,
    tmp_path: Path,
) -> None:
    install_pre_squash_fixture(_POSTGRES_URL)

    write_result = _run_capture(_POSTGRES_URL, tmp_path)

    assert write_result.returncode == 0, write_result.stderr
    paths = sorted(path.name for path in tmp_path.iterdir())
    assert paths == [
        "pre_ga_v1-clean-application-contract.json",
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
    logical = json.loads(
        (tmp_path / "pre_ga_v1-clean-application-contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(exclusions["objects"]) == 27
    assert snapshot["legacyBusinessRowCount"] == 0
    assert snapshot["knownInertSeedRowCount"] == 1
    assert len(snapshot["sourceDocument"]["objects"]) == 207
    assert len(snapshot["normalizedApplicationDocument"]["objects"]) == 180
    assert len(registry["objects"]) == 101
    assert logical["canonicalizationVersion"] == 2
    assert logical["sourceSnapshotDigest"] == snapshot["snapshotDigest"]
    assert logical["exclusionManifestDigest"] == exclusions["manifestDigest"]
    assert logical["controlContractDigest"] == PRE_SQUASH_CONTROL_CONTRACT_DIGEST
    assert len(logical["logicalApplicationDocument"]["objects"]) == 179
    assert logical["logicalApplicationFingerprint"] == sha256_canonical_json(
        logical["logicalApplicationDocument"]
    )
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
    install_pre_squash_fixture(_POSTGRES_URL)
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
    install_pre_squash_fixture(_POSTGRES_URL)
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
