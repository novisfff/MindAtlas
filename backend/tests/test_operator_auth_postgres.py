"""PostgreSQL gates for the operator control-plane schema on the clean root.

Requires ``MINDATLAS_TEST_POSTGRES_URL``. Proves:

- the release-critical fixture installs the complete clean root directly
- singleton / digest / expiry / revoke-reason checks
- unique token_digest and singleton_key
- append-only ``operator_audit_event`` (UPDATE/DELETE raise SQLSTATE 55000)
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402

CLEAN_SCHEMA_HEAD = CLEAN_ROOT_REVISION

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_REQUIRE_POSTGRES = os.environ.get("MINDATLAS_REQUIRE_POSTGRES", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}

if not _POSTGRES_URL and _REQUIRE_POSTGRES:
    # Release-critical gate: never pytest.skip when CI/runner demands PostgreSQL.
    pytest.fail(
        "MINDATLAS_TEST_POSTGRES_URL not set while MINDATLAS_REQUIRE_POSTGRES=1; "
        "operator-auth PostgreSQL schema gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; operator-auth PostgreSQL schema "
        "gate skipped (SQLite cannot prove append-only trigger / checks). "
        "Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    os.environ["APP_ENV"] = "test"
    os.environ["APP_BUILD_REVISION"] = "test-operator-auth-clean-root"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
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
        try:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        except Exception:
            return None
        return None if row is None else str(row[0])


def _err_text(exc: BaseException) -> str:
    parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " | ".join(parts)


def _sqlstate(exc: BaseException) -> str | None:
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        pgcode = getattr(cur, "pgcode", None)
        if pgcode:
            return str(pgcode)
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException):
            cur = orig
            continue
        break
    return None


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    ).first()
    return row is not None


def _drop_public_schema(engine: Engine) -> None:
    reset_disposable_public_schema(engine)


def _ensure_clean_root() -> None:
    """Install the live clean root directly on an empty disposable database."""
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        _drop_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-operator-auth-clean-root",
    )
    with _engine() as engine:
        assert _current_revision(engine) == CLEAN_SCHEMA_HEAD, (
            f"expected clean root {CLEAN_SCHEMA_HEAD}, got {_current_revision(engine)}"
        )


class _PgMigrator:
    """Minimal migrator helper matching the plan's assertion surface."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upgrade(self, revision: str) -> None:
        _configure_database_env(_POSTGRES_URL)
        _run_alembic("upgrade", revision)

    def unique_columns(self, table: str) -> set[tuple[str, ...]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        co.conname,
                        array_agg(a.attname ORDER BY u.ord) AS cols
                    FROM pg_constraint co
                    JOIN pg_class t ON co.conrelid = t.oid
                    JOIN pg_namespace n ON t.relnamespace = n.oid
                    JOIN LATERAL unnest(co.conkey) WITH ORDINALITY AS u(attnum, ord)
                        ON true
                    JOIN pg_attribute a
                        ON a.attrelid = t.oid AND a.attnum = u.attnum
                    WHERE n.nspname = 'public'
                      AND t.relname = :table
                      AND co.contype IN ('u', 'p')
                    GROUP BY co.conname, co.contype
                    """
                ),
                {"table": table},
            ).fetchall()
            # Plan asserts unique_columns for singleton_key only (exclude PK).
            result: set[tuple[str, ...]] = set()
            for name, cols in rows:
                col_tuple = tuple(str(c) for c in cols)
                # Skip pure primary-key (id) uniqueness.
                if col_tuple == ("id",):
                    continue
                # Unique indexes created via UNIQUE constraint also appear here.
                if "pkey" in str(name):
                    continue
                result.add(col_tuple)
            # Also include unique indexes that are not constraints (unlikely) —
            # but token_digest uses UniqueConstraint so it is covered.
            idx_rows = conn.execute(
                text(
                    """
                    SELECT
                        i.relname AS index_name,
                        array_agg(a.attname ORDER BY u.ord) AS cols
                    FROM pg_index ix
                    JOIN pg_class t ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_namespace n ON t.relnamespace = n.oid
                    JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS u(attnum, ord)
                        ON true
                    JOIN pg_attribute a
                        ON a.attrelid = t.oid AND a.attnum = u.attnum
                    WHERE n.nspname = 'public'
                      AND t.relname = :table
                      AND ix.indisunique
                      AND NOT ix.indisprimary
                    GROUP BY i.relname
                    """
                ),
                {"table": table},
            ).fetchall()
            for _name, cols in idx_rows:
                result.add(tuple(str(c) for c in cols))
            return result

    def has_check(self, table: str, name: str) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    JOIN pg_namespace n ON t.relnamespace = n.oid
                    WHERE n.nspname = 'public'
                      AND t.relname = :table
                      AND c.contype = 'c'
                      AND c.conname = :name
                    """
                ),
                {"table": table, "name": name},
            ).first()
            return row is not None

    def has_unique(self, table: str, columns: tuple[str, ...]) -> bool:
        return tuple(columns) in self.unique_columns(table)


@pytest.fixture(scope="module")
def pg_migrator() -> Iterator[_PgMigrator]:
    _ensure_clean_root()
    with _engine() as engine:
        yield _PgMigrator(engine)


@pytest.fixture
def pg_session(pg_migrator: _PgMigrator) -> Iterator[Session]:
    # Ensure head is applied once per session fixture usage.
    if _current_revision(pg_migrator.engine) != CLEAN_SCHEMA_HEAD:
        pg_migrator.upgrade(CLEAN_SCHEMA_HEAD)
    with _session(pg_migrator.engine) as session:
        # Clean operator tables between tests (audit trigger blocks DELETE —
        # truncate is CASCADE and bypasses row triggers via TRUNCATE).
        session.execute(text("TRUNCATE operator_audit_event, operator_session, operator_account RESTART IDENTITY CASCADE"))
        session.commit()
        yield session
        session.rollback()


def seed_operator_account(session: Session, **overrides: Any):
    from app.operator_auth.models import OperatorAccount
    from app.common.time import utcnow

    now = utcnow()
    row = OperatorAccount(
        id=overrides.get("id", uuid.uuid4()),
        singleton_key=overrides.get("singleton_key", "operator"),
        role=overrides.get("role", "operator"),
        password_hash=overrides.get("password_hash", "argon2-placeholder-not-a-secret"),
        password_revision=overrides.get("password_revision", 1),
        enabled=overrides.get("enabled", True),
        failed_login_window_started_at=overrides.get("failed_login_window_started_at"),
        failed_login_count=overrides.get("failed_login_count", 0),
        locked_until=overrides.get("locked_until"),
        password_changed_at=overrides.get("password_changed_at", now),
        created_at=overrides.get("created_at", now),
        updated_at=overrides.get("updated_at", now),
    )
    session.add(row)
    session.flush()
    return row


def seed_operator_audit_event(session: Session, **overrides: Any):
    from app.operator_auth.models import OperatorAuditEvent
    from app.common.time import utcnow

    row = OperatorAuditEvent(
        id=overrides.get("id", uuid.uuid4()),
        occurred_at=overrides.get("occurred_at", utcnow()),
        event_type=overrides.get("event_type", "login_succeeded"),
        outcome=overrides.get("outcome", "succeeded"),
        operator_id=overrides.get("operator_id"),
        session_id=overrides.get("session_id"),
        request_id=overrides.get("request_id", "req-test-1"),
        request_digest=overrides.get("request_digest", _HEX_A),
        user_agent_digest=overrides.get("user_agent_digest", _HEX_B),
        network_digest=overrides.get("network_digest", _HEX_C),
        reason_code=overrides.get("reason_code"),
        metadata_json=overrides.get("metadata_json", {}),
    )
    session.add(row)
    session.flush()
    return row


def seed_operator_session(session: Session, account_id: uuid.UUID, **overrides: Any):
    from app.operator_auth.models import OperatorSession
    from app.common.time import utcnow

    now = utcnow()
    row = OperatorSession(
        id=overrides.get("id", uuid.uuid4()),
        operator_account_id=account_id,
        token_digest=overrides.get("token_digest", _HEX_A),
        csrf_digest=overrides.get("csrf_digest", _HEX_B),
        hmac_key_id=overrides.get("hmac_key_id", "active-key"),
        password_revision=overrides.get("password_revision", 1),
        created_at=overrides.get("created_at", now),
        last_seen_at=overrides.get("last_seen_at", now),
        idle_expires_at=overrides.get("idle_expires_at", now + timedelta(hours=12)),
        absolute_expires_at=overrides.get(
            "absolute_expires_at", now + timedelta(days=7)
        ),
        revoked_at=overrides.get("revoked_at"),
        revoke_reason=overrides.get("revoke_reason"),
        request_digest=overrides.get("request_digest", _HEX_A),
        user_agent_digest=overrides.get("user_agent_digest", _HEX_B),
        network_digest=overrides.get("network_digest", _HEX_C),
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Schema / constraints
# ---------------------------------------------------------------------------


def test_operator_schema_has_required_constraints(pg_migrator: _PgMigrator) -> None:
    pg_migrator.upgrade(CLEAN_SCHEMA_HEAD)
    assert _current_revision(pg_migrator.engine) == CLEAN_SCHEMA_HEAD
    assert pg_migrator.unique_columns("operator_account") == {("singleton_key",)}
    assert pg_migrator.has_check(
        "operator_account", "ck_operator_account_singleton_key"
    )
    assert pg_migrator.has_unique("operator_session", ("token_digest",))

    # Additional required checks from the plan.
    assert pg_migrator.has_check("operator_account", "ck_operator_account_role")
    assert pg_migrator.has_check(
        "operator_account", "ck_operator_account_password_revision_positive"
    )
    assert pg_migrator.has_check(
        "operator_account", "ck_operator_account_failed_login_count_nonnegative"
    )
    assert pg_migrator.has_check(
        "operator_session", "ck_operator_session_token_digest_hex"
    )
    assert pg_migrator.has_check(
        "operator_session", "ck_operator_session_absolute_after_created"
    )
    assert pg_migrator.has_check(
        "operator_session", "ck_operator_session_idle_within_absolute"
    )
    assert pg_migrator.has_check(
        "operator_session", "ck_operator_session_revoke_reason"
    )


def test_operator_alembic_sole_head() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert heads == [CLEAN_SCHEMA_HEAD], (
        f"expected sole head {CLEAN_SCHEMA_HEAD}, got {heads}"
    )


def test_operator_audit_is_append_only(pg_session: Session) -> None:
    event = seed_operator_audit_event(pg_session)
    pg_session.commit()

    with pytest.raises((IntegrityError, DBAPIError)) as upd_info:
        pg_session.execute(
            text("UPDATE operator_audit_event SET event_type='changed' WHERE id=:id"),
            {"id": event.id},
        )
        pg_session.commit()
    assert _sqlstate(upd_info.value) == "55000" or "append-only" in _err_text(
        upd_info.value
    )
    pg_session.rollback()

    with pytest.raises((IntegrityError, DBAPIError)) as del_info:
        pg_session.execute(
            text("DELETE FROM operator_audit_event WHERE id=:id"), {"id": event.id}
        )
        pg_session.commit()
    assert _sqlstate(del_info.value) == "55000" or "append-only" in _err_text(
        del_info.value
    )
    pg_session.rollback()

    # Row still present.
    remaining = pg_session.execute(
        text("SELECT count(*) FROM operator_audit_event WHERE id=:id"),
        {"id": event.id},
    ).scalar()
    assert remaining == 1


def test_singleton_key_rejects_non_operator(pg_session: Session) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_account(pg_session, singleton_key="admin")
        pg_session.commit()
    pg_session.rollback()


def test_singleton_key_unique(pg_session: Session) -> None:
    seed_operator_account(pg_session)
    pg_session.commit()
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_account(pg_session)
        pg_session.commit()
    pg_session.rollback()


def test_role_rejects_unknown(pg_session: Session) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_account(pg_session, role="admin")
        pg_session.commit()
    pg_session.rollback()


def test_password_revision_must_be_positive(pg_session: Session) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_account(pg_session, password_revision=0)
        pg_session.commit()
    pg_session.rollback()


def test_failed_login_count_nonnegative(pg_session: Session) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_account(pg_session, failed_login_count=-1)
        pg_session.commit()
    pg_session.rollback()


def test_session_digest_must_be_lowercase_hex(pg_session: Session) -> None:
    account = seed_operator_account(pg_session)
    pg_session.commit()
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_session(
            pg_session, account.id, token_digest="G" * 64
        )
        pg_session.commit()
    pg_session.rollback()


def test_session_absolute_must_follow_created(pg_session: Session) -> None:
    account = seed_operator_account(pg_session)
    pg_session.commit()
    now = datetime.now(timezone.utc)
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_session(
            pg_session,
            account.id,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now - timedelta(hours=1),
            absolute_expires_at=now - timedelta(seconds=1),
        )
        pg_session.commit()
    pg_session.rollback()


def test_session_idle_cannot_exceed_absolute(pg_session: Session) -> None:
    account = seed_operator_account(pg_session)
    pg_session.commit()
    now = datetime.now(timezone.utc)
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_session(
            pg_session,
            account.id,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(days=8),
            absolute_expires_at=now + timedelta(days=7),
        )
        pg_session.commit()
    pg_session.rollback()


def test_session_revoke_reason_bounded(pg_session: Session) -> None:
    account = seed_operator_account(pg_session)
    pg_session.commit()
    now = datetime.now(timezone.utc)
    with pytest.raises((IntegrityError, DBAPIError)):
        seed_operator_session(
            pg_session,
            account.id,
            revoked_at=now,
            revoke_reason="not-a-valid-reason",
        )
        pg_session.commit()
    pg_session.rollback()

    # Valid reason accepted.
    seed_operator_session(
        pg_session,
        account.id,
        token_digest=_HEX_C,
        revoked_at=now,
        revoke_reason="logout",
    )
    pg_session.commit()
