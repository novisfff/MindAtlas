"""Test-only Alembic harness exposing only the staged clean root."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
STAGED_ROOT = (
    BACKEND_ROOT
    / "alembic"
    / "baseline_staging"
    / "pre_ga_v1_0001_clean_baseline.py"
)


def build_staged_alembic_directory(root: Path) -> Path:
    script_directory = root / "alembic"
    versions = script_directory / "versions"
    versions.mkdir(parents=True)
    shutil.copy2(BACKEND_ROOT / "alembic" / "env.py", script_directory / "env.py")
    shutil.copy2(
        BACKEND_ROOT / "alembic" / "script.py.mako",
        script_directory / "script.py.mako",
    )
    shutil.copy2(STAGED_ROOT, versions / STAGED_ROOT.name)

    source_ini = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
    source_ini = source_ini.replace(
        "script_location = alembic",
        f"script_location = {script_directory}",
        1,
    )
    (root / "alembic.ini").write_text(source_ini, encoding="utf-8")
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
