"""Current main-agent persistence checks on the clean schema root."""

from __future__ import annotations

import os
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402
from tests.main_agent_postgres_support import insert_complete_main_agent_run  # noqa: E402


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_REQUIRE_POSTGRES = os.environ.get("MINDATLAS_REQUIRE_POSTGRES", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}
if not _POSTGRES_URL and _REQUIRE_POSTGRES:
    pytest.fail(
        "MINDATLAS_TEST_POSTGRES_URL not set while MINDATLAS_REQUIRE_POSTGRES=1; "
        "clean-root main-agent gate must hard-fail",
        pytrace=False,
    )
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root main-agent proof",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture()
def clean_root_engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-main-agent-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def test_main_agent_run_shape_is_available_at_clean_root(
    clean_root_engine: Engine,
) -> None:
    run_id = insert_complete_main_agent_run(
        clean_root_engine,
        status="queued",
        required_app_build_revision="test-main-agent-clean-root",
    )
    with clean_root_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == CLEAN_ROOT_REVISION
        row = connection.execute(
            text(
                "SELECT runtime_kind, status, state_revision, "
                "required_checkpoint_codec_version "
                "FROM assistant_chat_run WHERE id=:id"
            ),
            {"id": run_id},
        ).one()
    assert row.runtime_kind == "main_agent"
    assert row.status == "queued"
    assert row.state_revision == 0
    assert row.required_checkpoint_codec_version == 3
