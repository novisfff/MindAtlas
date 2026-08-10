from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, text

import pytest

from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked
from scripts.schema_database_state import classify_database_state


DEPLOY_MIGRATE = Path(__file__).resolve().parents[2] / "deploy" / "migrate.sh"
BACKEND_ROOT = DEPLOY_MIGRATE.parents[1] / "backend"
POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
REQUIRE_POSTGRES = os.environ.get("MINDATLAS_REQUIRE_SCHEMA_POSTGRES", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}

if not POSTGRES_URL and REQUIRE_POSTGRES:
    pytest.fail(
        "MINDATLAS_TEST_POSTGRES_URL not set while "
        "MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1; deploy migration gate must run",
        pytrace=False,
    )


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _run_migrate(url: str, *, deployment_class: str = "rehearsal") -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": url,
            "MINDATLAS_DEPLOYMENT_CLASS": deployment_class,
            "APP_ENV": "test",
            "APP_BUILD_REVISION": "test-deploy-migrate-clean-root",
            "MINDATLAS_TEST_POSTGRES_DESTRUCTIVE": "1",
        }
    )
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get(
        "PATH", ""
    )
    return subprocess.run(
        ["sh", str(DEPLOY_MIGRATE)],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def disposable_engine():  # noqa: ANN201
    if not POSTGRES_URL:
        pytest.skip("MINDATLAS_TEST_POSTGRES_URL is required for shell integration")
    engine = create_engine(_sqlalchemy_url(POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


class _FakeInspector:
    def __init__(self, tables: tuple[str, ...], versions: tuple[str, ...]):
        self.tables = tables
        self.versions = versions


@pytest.mark.parametrize(
    ("tables", "versions", "expected"),
    [
        ((), (), "empty"),
        (("assistant_chat_run",), ("pre_ga_v1_0001",), "versioned"),
        (("legacy_table",), (), "nonempty_unversioned"),
        ((), ("pre_ga_v1_0001", "second_head"), "unknown"),
    ],
)
def test_schema_database_state_is_fail_closed(
    tables: tuple[str, ...],
    versions: tuple[str, ...],
    expected: str,
) -> None:
    assert classify_database_state(tables, versions) == expected


def test_deploy_migrate_has_no_auto_stamp_and_requires_identity() -> None:
    source = DEPLOY_MIGRATE.read_text(encoding="utf-8")

    assert "alembic stamp" not in source
    assert "unsupported_nonempty_unversioned_database" in source
    assert "MINDATLAS_DEPLOYMENT_CLASS" in source


def test_deploy_migrate_installs_and_reuses_clean_root(disposable_engine) -> None:
    first = _run_migrate(POSTGRES_URL)
    assert first.returncode == 0, first.stderr
    with disposable_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "pre_ga_v1_0001"
    second = _run_migrate(POSTGRES_URL)
    assert second.returncode == 0, second.stderr


def test_deploy_migrate_rejects_nonempty_unversioned_database(disposable_engine) -> None:
    with disposable_engine.begin() as connection:
        connection.execute(text("CREATE TABLE migrate_probe (id integer)"))
    result = _run_migrate(POSTGRES_URL)
    assert result.returncode == 65
    assert "unsupported_nonempty_unversioned_database" in result.stderr


def test_deploy_migrate_rejects_old_head_without_executing_archive(
    disposable_engine,
) -> None:
    with disposable_engine.begin() as connection:
        connection.execute(
            text(
                'CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'
            )
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "b6e2d4f8a901"},
        )
    result = _run_migrate(POSTGRES_URL)
    assert result.returncode != 0
    assert "Can't locate revision" in (result.stdout + result.stderr)


def test_deploy_migrate_rejects_wrong_family_after_clean_upgrade(
    disposable_engine,
) -> None:
    upgrade_clean_root_checked(
        POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-deploy-migrate-clean-root",
    )
    with disposable_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE mindatlas_schema_identity "
                "DROP CONSTRAINT ck_schema_identity_family"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE mindatlas_schema_identity "
                "DISABLE TRIGGER trg_mindatlas_schema_identity_guard"
            )
        )
        connection.execute(
            text(
                "UPDATE mindatlas_schema_identity SET schema_family = 'pre_ga_v2' "
                "WHERE singleton_key = 'current'"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE mindatlas_schema_identity "
                "ENABLE TRIGGER trg_mindatlas_schema_identity_guard"
            )
        )
    result = _run_migrate(POSTGRES_URL)
    assert result.returncode == 2
    assert result.stderr.strip().endswith("marker_malformed")


def test_deploy_migrate_rejects_invalid_class_before_database_access() -> None:
    result = _run_migrate(
        "postgresql://mindatlas:mindatlas@127.0.0.1:1/"
        "mindatlas_test_pre_ga_v1_invalid_class",
        deployment_class="staging",
    )
    assert result.returncode == 64
    assert result.stderr.strip() == "schema_deployment_class_invalid"


def test_deploy_migrate_bounds_connection_failure() -> None:
    result = _run_migrate(
        "postgresql://mindatlas:mindatlas@127.0.0.1:1/"
        "mindatlas_test_pre_ga_v1_connection_failure?connect_timeout=1"
    )
    assert result.returncode == 66
    assert result.stderr.strip() == "schema_database_state_unknown"
