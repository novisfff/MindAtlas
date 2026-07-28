"""Operator repository lockout, initialization lock, and safe audit staging."""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.operator_auth.audit import (  # noqa: E402
    OPERATOR_AUDIT_EVENT_TYPES,
    OperatorAuditRepository,
)
from app.operator_auth.constants import (  # noqa: E402
    LOGIN_FAILURE_LIMIT,
    LOGIN_LOCK_SECONDS,
    LOGIN_WINDOW_SECONDS,
)
from app.operator_auth.contracts import RequestSecurityContext  # noqa: E402
from app.operator_auth.models import OperatorAccount, OperatorAuditEvent  # noqa: E402
from app.operator_auth.repository import OperatorRepository  # noqa: E402


_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_CTX = RequestSecurityContext(
    request_id="req-test-lockout-1",
    request_digest=_HEX_A,
    user_agent_digest=_HEX_B,
    network_digest=_HEX_C,
)

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()


class FrozenDbClock:
    """Injectable database clock for lockout unit tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def frozen_db_clock() -> FrozenDbClock:
    return FrozenDbClock()


@pytest.fixture
def repository(frozen_db_clock: FrozenDbClock) -> Iterator[OperatorRepository]:
    from tests._db import make_session

    session = make_session()
    try:
        yield OperatorRepository(session, now_fn=frozen_db_clock)
        session.commit()
    finally:
        session.close()


def test_fifth_failure_locks_for_database_time(
    repository: OperatorRepository, frozen_db_clock: FrozenDbClock
) -> None:
    account = repository.seed_account(password="correct horse battery")
    for _index in range(4):
        state = repository.record_login_failure(account.id)
        assert state.locked_until is None
    state = repository.record_login_failure(account.id)
    assert state.failed_login_count == 5
    assert state.locked_until == frozen_db_clock.now + timedelta(minutes=15)


def test_failure_after_window_starts_new_window(
    repository: OperatorRepository, frozen_db_clock: FrozenDbClock
) -> None:
    account = repository.seed_account(password="correct horse battery")
    repository.record_login_failure(account.id)
    frozen_db_clock.advance(minutes=16)
    state = repository.record_login_failure(account.id)
    assert state.failed_login_count == 1
    assert state.locked_until is None


def test_active_lock_survives_failure_window_reset(
    repository: OperatorRepository, frozen_db_clock: FrozenDbClock
) -> None:
    """Spaced failures make lock outlive the window; later failure must keep lock."""
    account = repository.seed_account(password="correct horse battery")
    # First failure starts the window at t0.
    repository.record_login_failure(account.id)
    # Offset remaining failures so locked_until outlives the failure window.
    offset_seconds = 60
    frozen_db_clock.advance(seconds=offset_seconds)
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        repository.record_login_failure(account.id)

    locked = repository.get_account_by_id(account.id)
    assert locked is not None
    assert repository.is_login_locked(locked)
    original_locked_until = locked.locked_until
    assert original_locked_until is not None
    assert original_locked_until == frozen_db_clock.now + timedelta(
        seconds=LOGIN_LOCK_SECONDS
    )

    # Past window start + WINDOW, still strictly before locked_until.
    # now is t0+offset; need now >= t0+WINDOW and now < t0+offset+LOCK.
    frozen_db_clock.advance(seconds=LOGIN_WINDOW_SECONDS - offset_seconds + 1)
    assert repository.is_login_locked(locked)

    state = repository.record_login_failure(account.id)
    assert state.failed_login_count == 1
    assert state.locked_until == original_locked_until
    assert repository.is_login_locked(state)

    frozen_db_clock.advance(seconds=LOGIN_LOCK_SECONDS)
    assert repository.is_login_locked(state) is False


def test_successful_clear_resets_failure_window(
    repository: OperatorRepository, frozen_db_clock: FrozenDbClock
) -> None:
    account = repository.seed_account(password="correct horse battery")
    for _ in range(3):
        repository.record_login_failure(account.id)
    state = repository.clear_login_failures(account.id)
    assert state.failed_login_count == 0
    assert state.failed_login_window_started_at is None
    assert state.locked_until is None


def test_audit_append_allowlists_event_types_and_safe_metadata(
    repository: OperatorRepository,
) -> None:
    account = repository.seed_account(password="correct horse battery")
    audit = OperatorAuditRepository(repository.db, operator_repository=repository)

    row = audit.append(
        event_type="login_succeeded",
        outcome="succeeded",
        context=_CTX,
        operator_id=account.id,
        session_id=None,
        metadata={"attempt": 1, "locked": False},
    )
    assert row.event_type == "login_succeeded"
    assert row.outcome == "succeeded"
    assert row.request_digest == _HEX_A
    assert row.metadata_json == {"attempt": 1, "locked": False}
    assert "login_succeeded" in OPERATOR_AUDIT_EVENT_TYPES

    with pytest.raises(ValueError, match="event_type"):
        audit.append(
            event_type="not_a_real_event",  # type: ignore[arg-type]
            outcome="succeeded",
            context=_CTX,
            operator_id=None,
            session_id=None,
        )

    with pytest.raises(ValueError, match="metadata"):
        audit.append(
            event_type="login_rejected",
            outcome="rejected",
            context=_CTX,
            operator_id=None,
            session_id=None,
            metadata={"password": ["nested", "list"]},  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# PostgreSQL concurrency gate
# ---------------------------------------------------------------------------


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@contextmanager
def _pg_engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True, pool_size=4
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _ensure_operator_schema(engine: Engine) -> None:
    """Ensure Task 2 head is present; truncate operator tables for isolation.

    The disposable operator-auth DB is stamped at parent then upgraded only
    through ``9f3c1a7e2b40``, so ``app_setting`` may be absent. Create a
    minimal table so ``lock_initialization`` can row-lock the marker.
    """
    with engine.begin() as conn:
        rev = None
        try:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception:
            rev = None
        if rev != "9f3c1a7e2b40":
            pytest.fail(
                f"operator auth schema not at 9f3c1a7e2b40 (got {rev!r}); "
                "run postgres suite / upgrade to 9f3c1a7e2b40 first — "
                "PostgreSQL security gates must not soft-skip when misconfigured"
            )
        # Minimal app_setting so FOR UPDATE on the initialization marker works.
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app_setting (
                    id UUID PRIMARY KEY,
                    key VARCHAR(128) NOT NULL UNIQUE,
                    value_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        conn.execute(
            text(
                "TRUNCATE operator_audit_event, operator_session, "
                "operator_account RESTART IDENTITY CASCADE"
            )
        )
        # Leave app_setting empty (uninitialized) but present for row locks.


def count_enabled_operator_accounts(session_factory: sessionmaker) -> int:
    session = session_factory()
    try:
        return int(
            session.execute(
                select(OperatorAccount).where(OperatorAccount.enabled.is_(True))
            ).scalars().all().__len__()
        )
    finally:
        session.close()


def run_two_initializers(session_factory: sessionmaker) -> list[str]:
    """Two concurrent transactions race lock_initialization + singleton insert."""
    barrier = threading.Barrier(2)
    results: list[str] = []
    results_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker() -> None:
        session: Session = session_factory()
        try:
            repo = OperatorRepository(session)
            barrier.wait(timeout=10)
            repo.lock_initialization()
            existing = repo.get_singleton_account(for_update=True)
            if existing is not None:
                session.rollback()
                with results_lock:
                    results.append("system_already_initialized")
                return
            repo.seed_account(password="correct horse battery")
            # Hold the lock briefly so the peer must wait on the advisory lock.
            time.sleep(0.15)
            session.commit()
            with results_lock:
                results.append("committed")
        except BaseException as exc:  # noqa: BLE001 - surface in parent
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    if errors:
        raise errors[0]
    return results


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL not set; concurrency proof requires PostgreSQL",
)
def test_initialization_lock_serializes_two_postgres_transactions() -> None:
    with _pg_engine() as engine:
        _ensure_operator_schema(engine)
        factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
        results = run_two_initializers(factory)
        assert sorted(results) == ["committed", "system_already_initialized"]
        assert count_enabled_operator_accounts(factory) == 1


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL not set; audit database-time proof needs PostgreSQL",
)
def test_audit_append_uses_database_time_on_postgres() -> None:
    with _pg_engine() as engine:
        _ensure_operator_schema(engine)
        factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
        session = factory()
        try:
            repo = OperatorRepository(session)
            account = repo.seed_account(password="correct horse battery")
            before = repo.database_now()
            audit = OperatorAuditRepository(session, operator_repository=repo)
            row = audit.append(
                event_type="operator_account_initialized",
                outcome="succeeded",
                context=_CTX,
                operator_id=account.id,
                session_id=None,
            )
            session.commit()
            assert isinstance(row.occurred_at, datetime)
            assert row.occurred_at >= before - timedelta(seconds=2)
            assert row.event_type == "operator_account_initialized"
            # Append-only still enforced after repository insert.
            with pytest.raises(Exception):
                session.execute(
                    text(
                        "UPDATE operator_audit_event SET reason_code='x' WHERE id=:id"
                    ),
                    {"id": row.id},
                )
                session.commit()
            session.rollback()
            remaining = session.execute(
                select(OperatorAuditEvent).where(OperatorAuditEvent.id == row.id)
            ).scalar_one()
            assert remaining.reason_code is None
        finally:
            session.close()
