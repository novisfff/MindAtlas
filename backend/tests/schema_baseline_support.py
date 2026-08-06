"""Test-only PostgreSQL database and clean-root Alembic harnesses."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

from tests.postgres_destructive_guard import assert_disposable_postgres_target


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CLEAN_ROOT = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "pre_ga_v1_0001_clean_baseline.py"
)
_DATABASE_LABEL_PATTERN = re.compile(r"[a-z0-9_]+")


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@contextmanager
def temporary_postgres_databases(
    base_url: str,
    *,
    labels: Sequence[str],
) -> Iterator[tuple[str, ...]]:
    assert_disposable_postgres_target(base_url)
    if not labels or any(
        _DATABASE_LABEL_PATTERN.fullmatch(label) is None for label in labels
    ):
        raise ValueError("temporary database labels must be lowercase identifiers")

    parsed = make_url(base_url)
    token = uuid.uuid4().hex[:10]
    database_names = tuple(
        f"mindatlas_test_plan08_equiv_{token}_{label}" for label in labels
    )
    if len(set(database_names)) != len(database_names) or any(
        len(name) > 63 for name in database_names
    ):
        raise ValueError("temporary database names are invalid")
    database_urls = tuple(
        parsed.set(database=name).render_as_string(hide_password=False)
        for name in database_names
    )
    for database_url in database_urls:
        assert_disposable_postgres_target(database_url)

    admin_url = parsed.set(database="postgres").render_as_string(
        hide_password=False
    )
    admin_engine = create_engine(
        _sqlalchemy_url(admin_url),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    created: list[str] = []
    try:
        with admin_engine.connect() as connection:
            for database_name in database_names:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
                created.append(database_name)
        yield database_urls
    finally:
        active_error = sys.exc_info()[1]
        cleanup_error: Exception | None = None
        try:
            try:
                with admin_engine.connect() as connection:
                    for database_name in reversed(created):
                        operations = (
                            (
                                text(
                                    "SELECT pg_terminate_backend(pid) "
                                    "FROM pg_stat_activity "
                                    "WHERE datname = :database_name "
                                    "AND pid <> pg_backend_pid()"
                                ),
                                {"database_name": database_name},
                            ),
                            (
                                text(
                                    f'DROP DATABASE IF EXISTS "{database_name}"'
                                ),
                                None,
                            ),
                        )
                        for statement, parameters in operations:
                            try:
                                connection.execute(statement, parameters)
                            except Exception as exc:  # noqa: BLE001
                                if cleanup_error is None:
                                    cleanup_error = exc
            except Exception as exc:  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = exc
        finally:
            admin_engine.dispose()
        if cleanup_error is not None and active_error is None:
            raise cleanup_error


def _build_alembic_directory(root: Path) -> tuple[Path, Path]:
    script_directory = root / "alembic"
    versions = script_directory / "versions"
    versions.mkdir(parents=True)
    shutil.copy2(BACKEND_ROOT / "alembic" / "env.py", script_directory / "env.py")
    shutil.copy2(
        BACKEND_ROOT / "alembic" / "script.py.mako",
        script_directory / "script.py.mako",
    )
    source_ini = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
    source_ini = source_ini.replace(
        "script_location = alembic",
        f"script_location = {script_directory}",
        1,
    )
    (root / "alembic.ini").write_text(source_ini, encoding="utf-8")
    return script_directory, versions


def build_clean_root_alembic_directory(root: Path) -> Path:
    _script_directory, versions = _build_alembic_directory(root)
    shutil.copy2(CLEAN_ROOT, versions / CLEAN_ROOT.name)
    return root


def run_staged_alembic(
    root: Path,
    command: str,
    target: str | None = None,
    *,
    database_url: str | None = None,
    deployment_class: str | None = None,
    app_env: str | None = None,
    downgrade_ack: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    if deployment_class is None:
        environment.pop("MINDATLAS_DEPLOYMENT_CLASS", None)
    else:
        environment["MINDATLAS_DEPLOYMENT_CLASS"] = deployment_class
    if app_env is None:
        environment.pop("APP_ENV", None)
    else:
        environment["APP_ENV"] = app_env
    if app_env == "production":
        environment["MINDATLAS_CANONICAL_ORIGIN"] = "https://mindatlas.test"
        environment["CORS_ORIGINS"] = "https://mindatlas.test"
    if downgrade_ack is None:
        environment.pop("MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE", None)
    else:
        environment["MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE"] = downgrade_ack

    arguments = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(root / "alembic.ini"),
        command,
    ]
    if target is not None:
        arguments.append(target)
    return subprocess.run(
        arguments,
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
