"""PostgreSQL rebaseline boundary checks for the clean-root release.

The historical migration chain is archived.  Release tests therefore never
construct an old-head database or execute an archived revision; they verify
that a clean-root database is idempotent and that the rebaseline command
refuses a non-root source before any mutation.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION, DeploymentClass  # noqa: E402
from app.schema.identity import read_schema_identity  # noqa: E402
from app.schema.rebaseline import (  # noqa: E402
    MAINTENANCE_ACKNOWLEDGEMENT,
    RebaselineRefused,
    RebaselineRequest,
    apply_rebaseline,
    validate_rebaseline_source,
)


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root rebaseline proof",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _set_database_comment(engine: Engine, deployment_class: str) -> None:
    with engine.begin() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        connection.exec_driver_sql(
            f'COMMENT ON DATABASE "{database_name}" IS %s',
            (f"mindatlas:deployment_class={deployment_class}",),
        )


def _request(deployment_class: DeploymentClass) -> RebaselineRequest:
    return RebaselineRequest(
        deployment_class=deployment_class,
        acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
        build_revision="test-clean-root-rebaseline",
    )


@pytest.fixture()
def clean_root_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    assert _POSTGRES_URL
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "rehearsal")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_BUILD_REVISION", "test-clean-root-rebaseline")
    reset_caches()
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-clean-root-rebaseline",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def _read_head(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        )


def test_clean_root_rebaseline_is_idempotent_and_does_not_mutate(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "rehearsal")
    with clean_root_engine.connect() as connection:
        report = apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert report.result == "already_rebaselined"
    assert report.before_revision == CLEAN_ROOT_REVISION
    assert report.after_revision == CLEAN_ROOT_REVISION
    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION
    with clean_root_engine.connect() as connection:
        marker = read_schema_identity(connection)
    assert marker.schema_revision == CLEAN_ROOT_REVISION
    assert marker.deployment_class is DeploymentClass.REHEARSAL


def test_clean_root_rebaseline_rejects_production_before_mutation(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "rehearsal")
    with clean_root_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^production_rebaseline_forbidden$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.PRODUCTION))

    assert exc.value.safe_code == "production_rebaseline_forbidden"
    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION


def test_clean_root_is_rejected_as_a_historical_rebaseline_source(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "development")
    with clean_root_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ):
        validate_rebaseline_source(connection, _request(DeploymentClass.DEVELOPMENT))

    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION
