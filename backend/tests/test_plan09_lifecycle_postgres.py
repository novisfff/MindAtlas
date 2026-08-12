"""Current Plan 09 lifecycle capability checks on the clean schema root.

The historical Plan 09 migration tests are retained as ``.py.archived``
documentation only.  These tests exercise the durable tables and constraints
after a fresh ``pre_ga_v1_0001`` install, which is the supported release path.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

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
        "clean-root Plan 09 lifecycle gate must hard-fail",
        pytrace=False,
    )
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root lifecycle proof",
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
        build_revision="test-plan09-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def _insert_gate(conn, *, gate_id: uuid.UUID, package_id: uuid.UUID, version_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_publish_gate (
                id, subject_kind, subject_aggregate_id, subject_version_id,
                subject_content_digest, subject_binding_digest,
                profile_digest, catalog_digest, dataset_version_ids,
                qualifying_eval_run_ids, runtime_contract_version,
                policy_version, threshold_version, build_revision, action,
                decision, assertion_snapshot, metric_snapshot, waiver_codes,
                created_at, expires_at, publication_pin_count, request_id
            ) VALUES (
                :id, 'skill_version', :agg, :ver, :d1, :d2, :d3, :d4,
                CAST(:ds AS json), CAST(:runs AS json), 1,
                'plan09-policy-v1', 't1', 'test-plan09',
                'skill_catalog_enable', 'passed', CAST(:assert AS json),
                CAST(:metrics AS json), CAST(:waivers AS json),
                :created, :expires, 0, :req
            )
            """
        ),
        {
            "id": gate_id,
            "agg": package_id,
            "ver": version_id,
            "d1": "a" * 64,
            "d2": "b" * 64,
            "d3": "a" * 64,
            "d4": "b" * 64,
            "ds": json.dumps([]),
            "runs": json.dumps([]),
            "assert": json.dumps({}),
            "metrics": json.dumps({}),
            "waivers": json.dumps([]),
            "created": now,
            "expires": now + timedelta(hours=1),
            "req": f"gate-req-{uuid.uuid4().hex[:10]}",
        },
    )


def _insert_use(conn, *, gate_id: uuid.UUID, package_id: uuid.UUID, version_id: uuid.UUID) -> None:
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_publish_gate_use (
                id, gate_id, action, aggregate_id, resulting_version_id,
                actor_principal, request_id, aggregate_revision, created_at
            ) VALUES (
                :id, :gate, 'skill_catalog_enable', :agg, :ver,
                'operator', :req, 1, NOW()
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "gate": gate_id,
            "agg": package_id,
            "ver": version_id,
            "req": f"use-req-{uuid.uuid4().hex[:10]}",
        },
    )


def test_live_alembic_has_one_clean_root_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [CLEAN_ROOT_REVISION]
    assert script.get_revision(CLEAN_ROOT_REVISION) is not None


def test_clean_root_has_lifecycle_tables_and_gate_constraint(
    clean_root_engine: Engine,
) -> None:
    with clean_root_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == CLEAN_ROOT_REVISION
        for table in (
            "assistant_skill_package",
            "assistant_skill_publish_gate",
            "assistant_skill_publish_gate_use",
            "assistant_skill_eval_run",
            "assistant_skill_import_preview",
        ):
            assert connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:name"
                ),
                {"name": table},
            ).first()
        constraints = connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'public.assistant_skill_publish_gate_use'::regclass "
                "AND contype = 'u'"
            )
        ).scalars()
        assert "uq_assistant_skill_publish_gate_use_gate_action" in set(constraints)


def test_gate_use_unique_blocks_second_consume(clean_root_engine: Engine) -> None:
    gate_id, package_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with clean_root_engine.begin() as connection:
        _insert_gate(
            connection,
            gate_id=gate_id,
            package_id=package_id,
            version_id=version_id,
        )
        _insert_use(
            connection,
            gate_id=gate_id,
            package_id=package_id,
            version_id=version_id,
        )
    with pytest.raises(IntegrityError):
        with clean_root_engine.begin() as connection:
            _insert_use(
                connection,
                gate_id=gate_id,
                package_id=package_id,
                version_id=version_id,
            )


def test_import_preview_is_cross_session_durable(clean_root_engine: Engine) -> None:
    with clean_root_engine.connect() as connection:
        columns = set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' "
                    "AND table_name='assistant_skill_import_preview'"
                )
            ).scalars()
        )
    assert {
        "id",
        "expires_at",
        "principal_id",
        "upload_digest",
        "preview_digest",
        "archive_bytes",
        "consumed",
    } <= columns
