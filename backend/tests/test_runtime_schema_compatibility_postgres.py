"""PostgreSQL runtime-compatibility gates for the supported clean root.

The superseded pre-GA migration lineage is archived and must not be executed
by release-critical tests.  These checks start from an empty disposable
database and exercise the exact root that the application supports today.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.compatibility import runtime_schema_compatibility  # noqa: E402
from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root compatibility proof",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture()
def clean_root_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    assert _POSTGRES_URL
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "rehearsal")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "APP_BUILD_REVISION", "test-runtime-compatibility-clean-root"
    )
    reset_caches()
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-runtime-compatibility-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def test_clean_root_is_family_bound_and_runtime_compatible(
    clean_root_engine: Engine,
) -> None:
    with clean_root_engine.connect() as connection:
        snapshot = runtime_schema_compatibility().evaluate(connection)

    assert snapshot.compatible is True
    assert snapshot.safe_reason is None
    assert snapshot.schema_family == "pre_ga_v1"
    assert snapshot.schema_revision == CLEAN_ROOT_REVISION


def test_clean_root_requires_matching_process_deployment_class(
    clean_root_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "production")
    reset_caches()
    with clean_root_engine.connect() as connection:
        snapshot = runtime_schema_compatibility().evaluate(connection)

    assert snapshot.compatible is False
    assert snapshot.safe_reason == "schema_incompatible"
    assert snapshot.diagnostic_code == "deployment_class_mismatch"
