"""Plan 09 Task 12 — disposable PostgreSQL lifecycle slice.

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Focused and fast:

1. Sole Alembic head is ``027869a00a47``.
2. Durable gate-use uniqueness (one consume per gate/action) on PG.
3. 09A lifecycle intermediate remains independent of 09B eval head.
4. Import-preview durable row shape present at head (cross-session surface).

Does not re-run the full admin migration suite — that lives in
``test_agent_skill_admin_postgres_migration.py``.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

PLAN09_HEAD = "027869a00a47"
TASK1_HEAD = "403414a62e55"
PARENT_REVISION = "d7e8f9a0b1c3"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 09 lifecycle PostgreSQL "
        "slice skipped"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _alembic_config():
    from alembic.config import Config

    assert _POSTGRES_URL
    # Ensure alembic/env.py reads disposable PG, not a cached sqlite .env URL.
    _configure_database_env(_POSTGRES_URL)
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _as_sqlalchemy_url(_POSTGRES_URL))
    return cfg


def _run_alembic(command_name: str, *args: str) -> None:
    from alembic import command

    cfg = _alembic_config()
    fn = getattr(command, command_name)
    fn(cfg, *args)


@contextmanager
def _engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True
    )
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        return None if row is None else str(row[0])


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).fetchone()
    return row is not None


def _ensure_head() -> None:
    """Upgrade disposable DB to sole Plan 09 head (destructive OK).

    Idempotent when already at head. If a previous interrupted cycle left eval
    tables while alembic_version lagged, stamp to head rather than re-running
    create_table.
    """
    destructive = os.environ.get("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "").strip()
    if destructive != "1":
        pytest.skip("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1 required for upgrade")
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        try:
            current = _current_revision(engine)
        except Exception:
            current = None
        if current == PLAN09_HEAD:
            return
        with engine.connect() as conn:
            head_schema_present = _table_exists(conn, "assistant_skill_publish_gate")
    if head_schema_present:
        _run_alembic("stamp", PLAN09_HEAD)
    else:
        _run_alembic("upgrade", PLAN09_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == PLAN09_HEAD, (
            f"expected {PLAN09_HEAD}, got {_current_revision(engine)}"
        )


def _insert_gate(conn, *, gate_id: uuid.UUID, package_id: uuid.UUID, version_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)
    empty = json.dumps([])
    empty_obj = json.dumps({})
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_publish_gate (
                id, subject_kind, subject_aggregate_id, subject_version_id,
                subject_content_digest, subject_binding_digest,
                profile_digest, catalog_digest,
                dataset_version_ids, qualifying_eval_run_ids,
                runtime_contract_version, policy_version, threshold_version,
                build_revision, action, decision,
                assertion_snapshot, metric_snapshot, waiver_codes,
                created_at, expires_at, publication_pin_count, request_id
            ) VALUES (
                :id, 'skill_version', :agg, :ver,
                :d1, :d2, :d3, :d4,
                CAST(:ds AS json), CAST(:runs AS json),
                1, 'plan09-policy-v1', 't1',
                'development', 'skill_catalog_enable', 'passed',
                CAST(:assert AS json), CAST(:metrics AS json), CAST(:waivers AS json),
                :created, :expires, 0, :req
            )
            """
        ),
        {
            "id": gate_id,
            "agg": package_id,
            "ver": version_id,
            "d1": _DIGEST_A,
            "d2": _DIGEST_B,
            "d3": _DIGEST_A,
            "d4": _DIGEST_B,
            "ds": empty,
            "runs": empty,
            "assert": empty_obj,
            "metrics": empty_obj,
            "waivers": empty,
            "created": now,
            "expires": expires,
            "req": f"gate-req-{uuid.uuid4().hex[:10]}",
        },
    )


def _insert_use(
    conn,
    *,
    use_id: uuid.UUID,
    gate_id: uuid.UUID,
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    request_id: str,
    aggregate_revision: int = 1,
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_publish_gate_use (
                id, gate_id, action, aggregate_id, resulting_version_id,
                actor_principal, request_id, aggregate_revision, created_at
            ) VALUES (
                :id, :gate, 'skill_catalog_enable', :agg, :ver,
                'op', :req, :rev, NOW()
            )
            """
        ),
        {
            "id": use_id,
            "gate": gate_id,
            "agg": package_id,
            "ver": version_id,
            "req": request_id,
            "rev": aggregate_revision,
        },
    )


def test_sole_alembic_head_is_plan09_eval() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert heads == [PLAN09_HEAD], f"expected sole head {PLAN09_HEAD}, got {heads}"
    head = script.get_revision(PLAN09_HEAD)
    assert head is not None
    assert head.down_revision == TASK1_HEAD
    task1 = script.get_revision(TASK1_HEAD)
    assert task1 is not None
    assert task1.down_revision == PARENT_REVISION


def test_head_has_lifecycle_and_gate_tables() -> None:
    _ensure_head()
    with _engine() as engine:
        assert _current_revision(engine) == PLAN09_HEAD
        with engine.begin() as conn:
            for table in (
                "assistant_skill_package",
                "assistant_skill_publish_gate",
                "assistant_skill_publish_gate_use",
                "assistant_skill_eval_run",
                "assistant_skill_import_preview",
            ):
                assert _table_exists(conn, table), f"missing table {table}"

            rows = conn.execute(
                text(
                    """
                    SELECT c.conname
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    JOIN pg_namespace n ON t.relnamespace = n.oid
                    WHERE n.nspname = 'public'
                      AND t.relname = 'assistant_skill_publish_gate_use'
                      AND c.contype = 'u'
                    """
                )
            ).fetchall()
            names = {str(r[0]) for r in rows}
            assert "uq_assistant_skill_publish_gate_use_gate_action" in names


def test_gate_use_unique_blocks_second_consume() -> None:
    """Durable proof: one gate_id+action row only (second insert conflicts)."""
    _ensure_head()
    gate_id = uuid.uuid4()
    package_id = uuid.uuid4()
    version_id = uuid.uuid4()

    with _engine() as engine:
        with engine.begin() as conn:
            _insert_gate(conn, gate_id=gate_id, package_id=package_id, version_id=version_id)
            _insert_use(
                conn,
                use_id=uuid.uuid4(),
                gate_id=gate_id,
                package_id=package_id,
                version_id=version_id,
                request_id=f"req-ok-{uuid.uuid4().hex[:8]}",
            )

        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                _insert_use(
                    conn,
                    use_id=uuid.uuid4(),
                    gate_id=gate_id,
                    package_id=package_id,
                    version_id=version_id,
                    request_id=f"req-dup-{uuid.uuid4().hex[:8]}",
                    aggregate_revision=2,
                )

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM assistant_skill_publish_gate_use "
                    "WHERE gate_id = :g"
                ),
                {"g": gate_id},
            ).scalar()
            assert int(count or 0) == 1


def test_import_preview_table_supports_cross_session_columns() -> None:
    """Import preview is durable (not process-memory) at Plan 09 head."""
    _ensure_head()
    with _engine() as engine:
        with engine.connect() as conn:
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema='public'
                          AND table_name='assistant_skill_import_preview'
                        """
                    )
                ).fetchall()
            }
            for required in (
                "id",
                "expires_at",
                "principal_id",
                "upload_digest",
                "preview_digest",
                "archive_bytes",
                "consumed",
            ):
                assert required in cols, f"missing import-preview column {required}: {cols}"


def test_09a_reachable_as_independent_intermediate() -> None:
    """09A lifecycle head is parent of 09B and remains a valid revision target."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision(TASK1_HEAD)
    assert rev is not None
    assert rev.revision == TASK1_HEAD
    head = script.get_revision(PLAN09_HEAD)
    assert head is not None
    assert head.down_revision == TASK1_HEAD
