from __future__ import annotations

from pathlib import Path
import json

import pytest

from app.schema.contracts import DeploymentClass
from app.schema.rebaseline import (
    DATA_INVARIANTS,
    MAINTENANCE_ACKNOWLEDGEMENT,
    SAFE_REPORT_FIELDS,
    RetainedTableSnapshot,
    RebaselineRefused,
    RebaselineRequest,
    RebaselineReport,
    build_retained_table_snapshot,
    compare_snapshots,
    validate_acknowledgement,
    validate_data_invariants,
)
from scripts import rebaseline_pre_ga_v1 as rebaseline_script
from scripts.rebaseline_pre_ga_v1 import build_parser, parse_apply_args
from scripts.rebaseline_pre_ga_v1 import write_report_atomic


def _apply_arguments(report_file: Path, acknowledgement: str) -> list[str]:
    return [
        "apply",
        "--database-url-env",
        "DATABASE_URL",
        "--report-file",
        str(report_file),
        "--acknowledge-local-maintenance",
        acknowledgement,
    ]


def test_rebaseline_cli_has_only_inspect_and_apply() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "inspect" in help_text
    assert "apply" in help_text
    assert "--force" not in help_text
    assert "--skip" not in help_text


@pytest.mark.parametrize(
    "acknowledgement",
    [
        "yes",
        "I_ACKNOWLEDGE_THIS_IS_A_NON_PRODUCTION_DATABASE",
        "I_ACKNOWLEDGE_THIS_IS_A_RESETTABLE_NON_PRODUCTION_DATABASE ",
    ],
)
def test_apply_requires_exact_literal_acknowledgement(
    tmp_path: Path,
    acknowledgement: str,
) -> None:
    args = parse_apply_args(
        _apply_arguments(tmp_path / "report.json", acknowledgement)
    )

    with pytest.raises(
        RebaselineRefused,
        match="^maintenance_acknowledgement_missing$",
    ):
        validate_acknowledgement(args)


def test_apply_accepts_only_the_documented_acknowledgement(
    tmp_path: Path,
) -> None:
    args = parse_apply_args(
        _apply_arguments(
            tmp_path / "report.json",
            MAINTENANCE_ACKNOWLEDGEMENT,
        )
    )

    validate_acknowledgement(args)


def test_rebaseline_data_invariants_are_fixed_and_parameter_free() -> None:
    assert tuple(item.name for item in DATA_INVARIANTS) == (
        "main_agent_runs_only",
        "run_runtime_identity_complete",
        "l2_native_identity_complete",
        "operator_singleton",
        "new_rollout_control_singleton",
        "all_foreign_keys_validated",
        "no_active_profile_v1_rollout",
    )
    assert all(item.query.lstrip().upper().startswith("SELECT") for item in DATA_INVARIANTS)
    assert all(":" not in item.query for item in DATA_INVARIANTS)


def test_failed_data_invariant_refuses_with_bounded_code() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def scalar(self, statement):  # noqa: ANN001, ANN201
            query = str(statement)
            self.queries.append(query)
            return "pg_constraint" not in query

    connection = FakeConnection()

    with pytest.raises(RebaselineRefused, match="^data_invariant_failed$"):
        validate_data_invariants(connection)

    assert len(connection.queries) == 6


@pytest.mark.parametrize(
    "build_revision",
    [
        "postgresql://user:password@host/db",
        "token=opaque-secret",
        "build-1\nnext",
        "x" * 129,
    ],
)
def test_build_revision_rejects_unsafe_report_input(build_revision: str) -> None:
    with pytest.raises(ValueError, match="^build_revision is unsafe$"):
        RebaselineRequest(
            deployment_class=DeploymentClass.DEVELOPMENT,
            acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
            build_revision=build_revision,
        )


def test_report_reservation_failure_happens_before_database_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "report.json"
    destination.write_bytes(b"not-a-report")
    connected = False

    def unexpected_create_engine(*args, **kwargs):  # noqa: ANN001, ANN002
        nonlocal connected
        connected = True
        raise AssertionError("database must not be opened")

    monkeypatch.setattr(rebaseline_script, "create_engine", unexpected_create_engine)
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "build-1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")

    result = rebaseline_script.main(
        [
            "inspect",
            "--database-url-env",
            "DATABASE_URL",
            "--report-file",
            str(destination),
        ]
    )

    assert result == 2
    assert "report_destination_collision" in capsys.readouterr().err
    assert connected is False
    assert destination.read_bytes() == b"not-a-report"


def test_report_path_failure_is_bounded_before_database_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connected = False

    def unexpected_create_engine(*args, **kwargs):  # noqa: ANN001, ANN002
        nonlocal connected
        connected = True
        raise AssertionError("database must not be opened")

    monkeypatch.setattr(rebaseline_script, "create_engine", unexpected_create_engine)
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "build-1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")

    result = rebaseline_script.main(
        [
            "inspect",
            "--database-url-env",
            "DATABASE_URL",
            "--report-file",
            str(tmp_path / "missing-parent" / "report.json"),
        ]
    )

    assert result == 2
    assert capsys.readouterr().err.strip() == "report_path_invalid"
    assert connected is False


def test_database_setup_failure_is_bounded_and_does_not_leak_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    def unavailable(*args, **kwargs):  # noqa: ANN001, ANN002
        raise SQLAlchemyError(
            "could not connect to postgresql://user:password@host/db"
        )

    monkeypatch.setattr(rebaseline_script, "create_engine", unavailable)
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "build-1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")

    result = rebaseline_script.main(
        [
            "inspect",
            "--database-url-env",
            "DATABASE_URL",
            "--report-file",
            str(tmp_path / "report.json"),
        ]
    )

    stderr = capsys.readouterr().err
    assert result == 2
    assert "rebaseline_database_unavailable" in stderr
    assert "postgresql://" not in stderr
    assert "password" not in stderr
    assert "Traceback" not in stderr


def test_main_rejects_unsafe_build_revision_before_database_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connected = False

    def unexpected_create_engine(*args, **kwargs):  # noqa: ANN001, ANN002
        nonlocal connected
        connected = True
        raise AssertionError("database must not be opened")

    monkeypatch.setattr(rebaseline_script, "create_engine", unexpected_create_engine)
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "token=opaque")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")

    result = rebaseline_script.main(
        [
            "inspect",
            "--database-url-env",
            "DATABASE_URL",
            "--report-file",
            str(tmp_path / "report.json"),
        ]
    )

    assert result == 2
    assert capsys.readouterr().err.strip() == "build_revision_invalid"
    assert connected is False


def test_retained_table_snapshot_is_keyed_and_row_order_independent() -> None:
    key = bytes(range(32))
    rows = ('{"id":1,"value":"a"}', '{"id":2,"value":"b"}')

    forward = build_retained_table_snapshot("public.entry", rows, key)
    reverse = build_retained_table_snapshot(
        "public.entry",
        tuple(reversed(rows)),
        key,
    )

    assert forward == reverse
    assert isinstance(forward, RetainedTableSnapshot)
    assert forward.row_count == 2
    assert isinstance(forward.keyed_digest, bytes)
    assert len(forward.keyed_digest) == 32


def test_retained_snapshot_comparison_rejects_any_row_change() -> None:
    key = b"k" * 32
    before = (
        build_retained_table_snapshot(
            "public.entry",
            ('{"id":1,"value":"before"}',),
            key,
        ),
    )
    after = (
        build_retained_table_snapshot(
            "public.entry",
            ('{"id":1,"value":"after"}',),
            key,
        ),
    )

    with pytest.raises(RebaselineRefused, match="^retained_data_changed$"):
        compare_snapshots(before, after)


def test_report_writer_is_allowlisted_atomic_and_idempotent(tmp_path: Path) -> None:
    report = RebaselineReport(
        operation_id="a" * 32,
        result="rebaselined",
        deployment_class=DeploymentClass.DEVELOPMENT,
        before_revision="b6e2d4f8a901",
        after_revision="pre_ga_v1_0001",
        before_structural_fingerprint="a" * 64,
        after_structural_fingerprint="b" * 64,
        runtime_identity_digest="c" * 64,
        exclusion_manifest_digest="d" * 64,
        excluded_object_count=27,
        removed_known_inert_seed_rows=1,
        removed_legacy_business_rows=0,
        retained_table_count=3,
        retained_row_count=4,
        retained_data_unchanged=True,
        archive_manifest_digest="e" * 64,
        build_revision="build-1",
    )
    path = tmp_path / "report.json"

    write_report_atomic(report, path)
    first = path.read_bytes()
    write_report_atomic(report, path)

    assert path.read_bytes() == first
    assert set(json.loads(first)) == SAFE_REPORT_FIELDS
