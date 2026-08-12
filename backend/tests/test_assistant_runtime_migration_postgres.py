"""Clean-root PostgreSQL gate for the durable Main-Agent runtime schema.

The unpublished Plan 1/2 migration chain is archived and is intentionally not
executable by release-critical tests.  This module verifies the supported
installation path and a few runtime DDL invariants from an empty database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION, PRE_SQUASH_HEAD  # noqa: E402

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
        "clean-root runtime migration gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; clean-root runtime migration "
        "gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead."
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture()
def clean_root_engine() -> Engine:
    assert _POSTGRES_URL
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-runtime-migration-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def test_clean_root_is_sole_head_and_archived_head_is_unreachable(
    clean_root_engine: Engine,
) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    with clean_root_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == CLEAN_ROOT_REVISION

    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [CLEAN_ROOT_REVISION]
    from alembic.util.exc import CommandError

    with pytest.raises(CommandError, match="Can't locate revision"):
        script.get_revision(PRE_SQUASH_HEAD)


def test_clean_root_has_complete_main_agent_runtime_shape(
    clean_root_engine: Engine,
) -> None:
    required_tables = {
        "assistant_chat_run",
        "assistant_main_agent_rollout_revision",
        "assistant_main_agent_rollout_control",
        "assistant_runtime_bootstrap_gate_use",
        "assistant_main_agent_rollout_event",
        "assistant_worker_registration",
    }
    with clean_root_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        ).scalars()
        actual = {str(value) for value in rows}
        assert required_tables <= actual
        checks = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(c.oid) "
                    "FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = 'public' "
                    "AND t.relname = 'assistant_chat_run' AND c.contype = 'c'"
                )
            )
        }
    assert any(
        "main_agent" in definition and "legacy" not in definition
        for definition in checks.values()
    )


def test_clean_root_runtime_controls_are_present_and_enabled(
    clean_root_engine: Engine,
) -> None:
    with clean_root_engine.connect() as connection:
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                )
            )
        }
    assert "trg_assistant_chat_run_runtime_identity_immutable" in trigger_names
    assert "trg_assistant_rollout_revision_immutable" in trigger_names
