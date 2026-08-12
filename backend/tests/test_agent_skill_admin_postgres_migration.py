"""Current skill-admin persistence checks on the clean schema root.

The old migration-transition fixture is archived.  The supported release gate
starts from an empty database and verifies the resulting admin invariants.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402


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
        "clean-root skill-admin gate must hard-fail",
        pytrace=False,
    )
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root skill-admin proof",
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
        build_revision="test-skill-admin-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def test_skill_admin_schema_is_current_and_has_cas_controls(
    clean_root_engine: Engine,
) -> None:
    with clean_root_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == CLEAN_ROOT_REVISION
        for table in (
            "assistant_skill_package",
            "assistant_skill_version",
            "assistant_skill_package_alias",
            "assistant_skill_import_preview",
        ):
            assert connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:name"
                ),
                {"name": table},
            ).first()
        checks = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid='public.assistant_skill_package'::regclass"
                )
            ).scalars()
        )
        assert {
            "ck_assistant_skill_package_aggregate_revision",
            "ck_assistant_skill_package_archived_shape",
            "ck_assistant_skill_package_last_admin_request_digest",
        } <= checks


def test_skill_admin_request_id_is_idempotency_key(clean_root_engine: Engine) -> None:
    package_id = uuid.uuid4()
    request_id = f"request-{uuid.uuid4().hex}"
    values = {
        "id": package_id,
        "name": f"pg-{uuid.uuid4().hex[:10]}",
        "request": request_id,
    }
    sql = text(
        """
        INSERT INTO assistant_skill_package (
            id, canonical_name, display_name, description, migration_state,
            catalog_enabled, is_system, aggregate_revision,
            last_admin_request_id, last_admin_request_digest,
            created_at, updated_at
        ) VALUES (
            :id, :name, :name, 'test', 'native', false, false, 0,
            :request, :digest, NOW(), NOW()
        )
        """
    )
    with clean_root_engine.begin() as connection:
        connection.execute(sql, {**values, "digest": "a" * 64})
    with pytest.raises((DBAPIError, IntegrityError)):
        with clean_root_engine.begin() as connection:
            connection.execute(
                sql,
                {
                    **values,
                    "id": uuid.uuid4(),
                    "digest": "b" * 64,
                },
            )
