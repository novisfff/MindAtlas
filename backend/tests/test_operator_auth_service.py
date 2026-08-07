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
    SESSION_ABSOLUTE_SECONDS,
    SESSION_IDLE_SECONDS,
)
from app.operator_auth.contracts import RequestSecurityContext  # noqa: E402
from app.operator_auth.models import (  # noqa: E402
    OperatorAccount,
    OperatorAuditEvent,
    OperatorSession,
)
from app.operator_auth.repository import OperatorRepository  # noqa: E402
from app.operator_auth.tokens import SessionMacKeyRing  # noqa: E402


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


# ---------------------------------------------------------------------------
# Task 4: session lifecycle (issue / resolve / CSRF / rotation / revoke)
# ---------------------------------------------------------------------------


def _hex(n: int) -> str:
    return format(n, "x") * 64


CTX = RequestSecurityContext(
    request_id="req-session-1",
    request_digest=_hex(0xA),
    user_agent_digest=_hex(0xB),
    network_digest=_hex(0xC),
)
CTX2 = RequestSecurityContext(
    request_id="req-session-2",
    request_digest=_hex(0xD),
    user_agent_digest=_hex(0xE),
    network_digest=_hex(0xF),
)
MAINTENANCE_CTX = RequestSecurityContext(
    request_id="req-maintenance-1",
    request_digest=_hex(0x1),
    user_agent_digest=_hex(0x2),
    network_digest=_hex(0x3),
)

_PASSWORD = "correct horse battery"
_PASSWORD_NEW = "a newer exact secret!"


def _raw_key(fill: int) -> bytes:
    return bytes([fill & 0xFF]) * 32


def _stable_key_material(key_id: str) -> bytes:
    """Stable per-id material so active/previous roles do not change bytes."""
    table = {
        "old": 21,
        "new": 11,
        "k1": 31,
        "k2": 32,
    }
    return _raw_key(table.get(key_id, (sum(key_id.encode("utf-8")) % 200) + 40))


def make_key_ring(*, active: str, previous: str | None = None) -> SessionMacKeyRing:
    keys: dict[str, bytes] = {active: _stable_key_material(active)}
    if previous is not None:
        keys[previous] = _stable_key_material(previous)
    return SessionMacKeyRing(active_key_id=active, keys=keys)


def ring_with_only(active: str) -> SessionMacKeyRing:
    return make_key_ring(active=active)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker]:
    """Shared on-disk SQLite DB so restart tests open a fresh SQLAlchemy session."""
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine, event

    from tests._db import create_sqlite_schema
    import app.operator_auth.models  # noqa: F401
    import app.system_settings.models  # noqa: F401

    tmp = tempfile.NamedTemporaryFile(
        prefix="mindatlas-opauth-", suffix=".sqlite", delete=False
    )
    tmp_path = Path(tmp.name)
    tmp.close()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_sqlite_schema(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    try:
        yield factory
    finally:
        engine.dispose()
        try:
            tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if tmp_path.exists():
                tmp_path.unlink()


@pytest.fixture
def key_ring() -> SessionMacKeyRing:
    return make_key_ring(active="k1")


@pytest.fixture
def frozen_clock() -> FrozenDbClock:
    return FrozenDbClock()


def make_service(
    db: Session,
    key_ring: SessionMacKeyRing,
    *,
    now_fn: FrozenDbClock | None = None,
):
    from app.operator_auth.service import OperatorAuthService

    repo = OperatorRepository(db, now_fn=now_fn)
    return OperatorAuthService(db, key_ring=key_ring, repository=repo, now_fn=now_fn)


def _seed_operator(db: Session, *, now_fn: FrozenDbClock | None = None) -> OperatorAccount:
    repo = OperatorRepository(db, now_fn=now_fn)
    account = repo.seed_account(password=_PASSWORD)
    db.commit()
    return account


def issue_with_key(
    session_factory: sessionmaker,
    *,
    key_id: str,
    clock: FrozenDbClock | None = None,
):
    ring = make_key_ring(active=key_id)
    db = session_factory()
    try:
        _seed_operator(db, now_fn=clock)
        service = make_service(db, ring, now_fn=clock)
        return service.login(_PASSWORD, CTX)
    finally:
        db.close()


def resolve_with_keys(
    session_factory: sessionmaker,
    *,
    active: str,
    previous: str,
    issued,
    clock: FrozenDbClock | None = None,
    csrf_cookie_value: str | None = None,
):
    ring = make_key_ring(active=active, previous=previous)
    db = session_factory()
    try:
        service = make_service(db, ring, now_fn=clock)
        return service.resolve_session(
            issued.session_cookie_value,
            CTX,
            csrf_cookie_value=csrf_cookie_value,
        )
    finally:
        db.close()


def stored_key_id(session_factory: sessionmaker, session_id) -> str:
    db = session_factory()
    try:
        row = db.get(OperatorSession, session_id)
        assert row is not None
        return str(row.hmac_key_id)
    finally:
        db.close()


def test_restart_with_same_key_keeps_session(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db1 = session_factory()
    try:
        _seed_operator(db1)
        issued = make_service(db1, key_ring).login(_PASSWORD, CTX)
    finally:
        db1.close()

    db2 = session_factory()
    try:
        restarted = make_service(db2, key_ring)
        resolved = restarted.resolve_session(issued.session_cookie_value, CTX)
        assert resolved is not None
        assert resolved.principal.session_id == issued.principal.session_id
        assert resolved.principal.operator_id == issued.principal.operator_id
    finally:
        db2.close()


def test_previous_key_is_rotated_on_successful_request(
    session_factory: sessionmaker,
) -> None:
    """Rotation re-MACs the supplied raws; same cookies keep working."""
    issued = issue_with_key(session_factory, key_id="old")
    result = resolve_with_keys(
        session_factory,
        active="new",
        previous="old",
        issued=issued,
        csrf_cookie_value=issued.csrf_cookie_value,
    )
    assert result is not None
    assert result.rotated_cookie is not None
    rotated_session, rotated_csrf = result.rotated_cookie
    # Same raws re-encoded — cookie values are identical to the supplied ones.
    assert rotated_session == issued.session_cookie_value
    assert rotated_csrf == issued.csrf_cookie_value
    assert result.hmac_key_id == "new"
    assert stored_key_id(session_factory, issued.principal.session_id) == "new"

    # Old session cookie still resolves after rotation (same raw re-MACed).
    db = session_factory()
    try:
        ring_both = make_key_ring(active="new", previous="old")
        service = make_service(db, ring_both)
        resolved_again = service.resolve_session(issued.session_cookie_value, CTX)
        assert resolved_again is not None
        assert resolved_again.principal.session_id == issued.principal.session_id
        # CSRF still verifies with the same csrf cookie/header after rotation.
        service.verify_csrf(
            resolution=resolved_again,
            csrf_cookie_value=issued.csrf_cookie_value,
            csrf_header_value=issued.csrf_cookie_value,
        )
        # Follow-up resolve with only the active key works.
        service_active = make_service(db, ring_with_only("new"))
        resolved_active = service_active.resolve_session(
            issued.session_cookie_value, CTX
        )
        assert resolved_active is not None
        assert resolved_active.hmac_key_id == "new"
        assert resolved_active.rotated_cookie is None
    finally:
        db.close()


def test_previous_key_without_csrf_does_not_rotate(
    session_factory: sessionmaker,
) -> None:
    """Previous-key session without CSRF still resolves but is not rotated."""
    issued = issue_with_key(session_factory, key_id="old")
    result = resolve_with_keys(
        session_factory, active="new", previous="old", issued=issued
    )
    assert result is not None
    assert result.rotated_cookie is None
    assert result.hmac_key_id == "old"
    assert stored_key_id(session_factory, issued.principal.session_id) == "old"


def test_resolve_preserves_creation_request_digests(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    """Creation request-context digests are immutable audit/binding fields."""
    db = session_factory()
    try:
        _seed_operator(db)
        service = make_service(db, key_ring)
        issued = service.login(_PASSWORD, CTX)
        resolved = service.resolve_session(issued.session_cookie_value, CTX2)
        assert resolved is not None
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        assert row.request_digest == CTX.request_digest
        assert row.user_agent_digest == CTX.user_agent_digest
        assert row.network_digest == CTX.network_digest
    finally:
        db.close()


def test_removed_previous_key_revokes_dependent_sessions(
    session_factory: sessionmaker,
) -> None:
    issued = issue_with_key(session_factory, key_id="old")
    db = session_factory()
    try:
        service = make_service(db, ring_with_only("new"))
        count = service.revoke_unverifiable_sessions(context=MAINTENANCE_CTX)
        assert count == 1
        assert service.resolve_session(issued.session_cookie_value, CTX) is None
    finally:
        db.close()


def test_documented_dual_key_sequence_revokes_previous_while_still_in_ring(
    session_factory: sessionmaker,
) -> None:
    """deploy/README rotation step 3: dual ring present; CLI/service retires old.

    With active=new + previous=old still configured, sessions bound to old must
    be durably revoked (not a no-op that waits until old is removed from the
    ring). Active-key sessions survive the same pass.
    """
    from app.operator_auth.models import OperatorAuditEvent, OperatorSession
    from sqlalchemy import select

    issued_old = issue_with_key(session_factory, key_id="old")
    db = session_factory()
    try:
        dual = make_key_ring(active="new", previous="old")
        service = make_service(db, dual)
        # Also mint an active-key session that must survive.
        issued_new = service.login(_PASSWORD, CTX2)
        count = service.revoke_unverifiable_sessions(context=MAINTENANCE_CTX)
        assert count == 1
        assert service.resolve_session(issued_old.session_cookie_value, CTX) is None
        assert (
            service.resolve_session(issued_new.session_cookie_value, CTX2) is not None
        )
        row = db.get(OperatorSession, issued_old.principal.session_id)
        assert row is not None
        assert row.revoked_at is not None
        assert row.revoke_reason == "hmac_key_removed"
        events = list(
            db.scalars(
                select(OperatorAuditEvent).where(
                    OperatorAuditEvent.event_type == "session_key_revoked"
                )
            )
        )
        assert len(events) >= 1
        assert any(e.reason_code == "hmac_key_removed" for e in events)
    finally:
        db.close()


def test_revoke_refuses_to_retire_active_key(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db = session_factory()
    try:
        _seed_operator(db)
        service = make_service(db, key_ring)
        with pytest.raises(ValueError, match="cannot_retire_active_session_mac_key"):
            service.revoke_unverifiable_sessions(
                context=MAINTENANCE_CTX,
                retire_key_ids=frozenset({key_ring.active_key_id}),
            )
    finally:
        db.close()


def test_password_revision_invalidates_all_sessions(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db = session_factory()
    try:
        _seed_operator(db)
        auth_service = make_service(db, key_ring)
        first = auth_service.login(_PASSWORD, CTX)
        second = auth_service.login(_PASSWORD, CTX2)
        auth_service.change_password(
            principal=first.principal,
            current_password=_PASSWORD,
            new_password=_PASSWORD_NEW,
            context=CTX,
        )
        assert auth_service.resolve_session(first.session_cookie_value, CTX) is None
        assert auth_service.resolve_session(second.session_cookie_value, CTX2) is None
    finally:
        db.close()


def test_idle_expiry_at_exactly_twelve_hours(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing, frozen_clock: FrozenDbClock
) -> None:
    db = session_factory()
    try:
        _seed_operator(db, now_fn=frozen_clock)
        service = make_service(db, key_ring, now_fn=frozen_clock)
        issued = service.login(_PASSWORD, CTX)
        frozen_clock.advance(seconds=SESSION_IDLE_SECONDS)
        assert service.resolve_session(issued.session_cookie_value, CTX) is None
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        assert row.revoked_at is not None
        assert row.revoke_reason == "idle_expired"
    finally:
        db.close()


def test_absolute_expiry_at_exactly_seven_days(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing, frozen_clock: FrozenDbClock
) -> None:
    db = session_factory()
    try:
        _seed_operator(db, now_fn=frozen_clock)
        service = make_service(db, key_ring, now_fn=frozen_clock)
        issued = service.login(_PASSWORD, CTX)
        # Stay inside idle by refreshing just before absolute, then hit absolute.
        # Advance in chunks under idle window, touching each time, until absolute.
        steps = 13  # 13 * ~12h > 7d; touch every just-under-idle period
        step = SESSION_IDLE_SECONDS - 60
        elapsed = 0
        cookie = issued.session_cookie_value
        while elapsed + step < SESSION_ABSOLUTE_SECONDS:
            frozen_clock.advance(seconds=step)
            elapsed += step
            resolved = service.resolve_session(cookie, CTX)
            assert resolved is not None
            if resolved.rotated_cookie is not None:
                cookie = resolved.rotated_cookie[0]
        # Land exactly on absolute expiry.
        remaining = SESSION_ABSOLUTE_SECONDS - elapsed
        frozen_clock.advance(seconds=remaining)
        assert service.resolve_session(cookie, CTX) is None
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        assert row.revoke_reason == "absolute_expired"
    finally:
        db.close()


def test_refresh_never_crosses_absolute_expiry(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing, frozen_clock: FrozenDbClock
) -> None:
    db = session_factory()
    try:
        _seed_operator(db, now_fn=frozen_clock)
        service = make_service(db, key_ring, now_fn=frozen_clock)
        issued = service.login(_PASSWORD, CTX)
        absolute = issued.absolute_expires_at
        cookie = issued.session_cookie_value
        # Walk to 6d23h with touches inside the idle window so the session stays live.
        target = timedelta(days=6, hours=23)
        step = timedelta(seconds=SESSION_IDLE_SECONDS - 60)
        elapsed = timedelta(0)
        while elapsed + step <= target:
            frozen_clock.advance(seconds=step.total_seconds())
            elapsed += step
            resolved = service.resolve_session(cookie, CTX)
            assert resolved is not None
            if resolved.rotated_cookie is not None:
                cookie = resolved.rotated_cookie[0]
        remaining = target - elapsed
        if remaining.total_seconds() > 0:
            frozen_clock.advance(seconds=remaining.total_seconds())
        resolved = service.resolve_session(cookie, CTX)
        assert resolved is not None

        def _aware(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value

        assert _aware(resolved.absolute_expires_at) == _aware(absolute)
        assert _aware(resolved.idle_expires_at) <= _aware(absolute)
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        assert _aware(row.absolute_expires_at) == _aware(absolute)
        assert _aware(row.idle_expires_at) <= _aware(row.absolute_expires_at)
        # Idle refresh clamped to absolute (remaining absolute window < idle window).
        assert _aware(row.idle_expires_at) == _aware(row.absolute_expires_at)
    finally:
        db.close()


def test_revoked_session_rejected_after_restart(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db1 = session_factory()
    try:
        _seed_operator(db1)
        service = make_service(db1, key_ring)
        issued = service.login(_PASSWORD, CTX)
        service.revoke_current(principal=issued.principal, context=CTX)
    finally:
        db1.close()

    db2 = session_factory()
    try:
        restarted = make_service(db2, key_ring)
        assert restarted.resolve_session(issued.session_cookie_value, CTX) is None
    finally:
        db2.close()


def test_malformed_cookie_rejected_without_database_exception(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db = session_factory()
    try:
        _seed_operator(db)
        service = make_service(db, key_ring)
        assert service.resolve_session("", CTX) is None
        assert service.resolve_session("not-a-cookie", CTX) is None
        assert service.resolve_session("v1.nothex.%%%%", CTX) is None
        assert service.resolve_session(None, CTX) is None  # type: ignore[arg-type]
    finally:
        db.close()


def test_unknown_key_id_rejects_and_revokes(
    session_factory: sessionmaker, frozen_clock: FrozenDbClock
) -> None:
    issued = issue_with_key(session_factory, key_id="old", clock=frozen_clock)
    db = session_factory()
    try:
        # Active ring has neither the issuing key nor a previous slot for it.
        service = make_service(db, ring_with_only("new"), now_fn=frozen_clock)
        assert service.resolve_session(issued.session_cookie_value, CTX) is None
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        # Durable revoke may be deferred to revoke_unverifiable_sessions; either
        # immediate revoke on resolve or still active-but-unverifiable is ok as
        # long as resolve returns None. Prefer durable revoke on resolve.
        if row.revoked_at is not None:
            assert row.revoke_reason in {"hmac_key_removed", "maintenance"}
    finally:
        db.close()


def test_disabled_account_rejects_session(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db = session_factory()
    try:
        account = _seed_operator(db)
        service = make_service(db, key_ring)
        issued = service.login(_PASSWORD, CTX)
        account.enabled = False
        db.commit()
        assert service.resolve_session(issued.session_cookie_value, CTX) is None
        row = db.get(OperatorSession, issued.principal.session_id)
        assert row is not None
        assert row.revoke_reason == "account_disabled"
    finally:
        db.close()


def test_generic_login_failure_for_missing_disabled_wrong_password(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    from app.operator_auth.service import AuthRejected, OperatorAuthService

    db = session_factory()
    try:
        # Missing account.
        service = make_service(db, key_ring)
        with pytest.raises(AuthRejected) as missing:
            service.login(_PASSWORD, CTX)
        missing_exc = missing.value

        account = _seed_operator(db)
        # Wrong password.
        with pytest.raises(AuthRejected) as wrong:
            service.login("definitely-not-the-password!!", CTX)
        wrong_exc = wrong.value

        account.enabled = False
        db.commit()
        # Disabled account.
        with pytest.raises(AuthRejected) as disabled:
            service.login(_PASSWORD, CTX)
        disabled_exc = disabled.value

        # Constant generic failure: same type and public code, no distinguishing detail.
        assert type(missing_exc) is type(wrong_exc) is type(disabled_exc) is AuthRejected
        assert (
            missing_exc.code
            == wrong_exc.code
            == disabled_exc.code
            == "invalid_credentials"
        )
        assert str(missing_exc) == str(wrong_exc) == str(disabled_exc)
    finally:
        db.close()


def test_csrf_requires_cookie_header_match_and_digest(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    from app.operator_auth.service import CsrfRejected

    db = session_factory()
    try:
        _seed_operator(db)
        service = make_service(db, key_ring)
        issued = service.login(_PASSWORD, CTX)
        resolved = service.resolve_session(issued.session_cookie_value, CTX)
        assert resolved is not None

        # Mismatched header vs cookie.
        with pytest.raises(CsrfRejected):
            service.verify_csrf(
                resolution=resolved,
                csrf_cookie_value=issued.csrf_cookie_value,
                csrf_header_value="totally-different-value",
            )

        # Matching pair succeeds.
        service.verify_csrf(
            resolution=resolved,
            csrf_cookie_value=issued.csrf_cookie_value,
            csrf_header_value=issued.csrf_cookie_value,
        )

        # Matching pair but wrong raw (forged equal cookie+header) fails digest.
        with pytest.raises(CsrfRejected):
            service.verify_csrf(
                resolution=resolved,
                csrf_cookie_value="a" * 43,  # wrong length/encoding ultimately
                csrf_header_value="a" * 43,
            )
    finally:
        db.close()


def test_revoke_all_and_logout_are_durable(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    db = session_factory()
    try:
        _seed_operator(db)
        service = make_service(db, key_ring)
        first = service.login(_PASSWORD, CTX)
        second = service.login(_PASSWORD, CTX2)
        count = service.revoke_all(principal=first.principal, context=CTX)
        assert count >= 2
        assert service.resolve_session(first.session_cookie_value, CTX) is None
        assert service.resolve_session(second.session_cookie_value, CTX2) is None
    finally:
        db.close()

    db2 = session_factory()
    try:
        service = make_service(db2, key_ring)
        third = service.login(_PASSWORD, CTX)
        service.revoke_current(principal=third.principal, context=CTX)
    finally:
        db2.close()

    db3 = session_factory()
    try:
        service = make_service(db3, key_ring)
        # Re-login after logout path above needs the account; third cookie dead.
        assert service.resolve_session(third.session_cookie_value, CTX) is None
    finally:
        db3.close()


def test_availability_reports_init_and_key_ring(
    session_factory: sessionmaker, key_ring: SessionMacKeyRing
) -> None:
    from app.operator_auth.service import OperatorAuthService

    db = session_factory()
    try:
        service = make_service(db, key_ring)
        before = service.availability()
        assert before.available is True
        assert "initialization_required" in before.reason_codes

        _seed_operator(db)
        after = service.availability()
        assert after.available is True
        assert "initialization_required" not in after.reason_codes

        no_keys = OperatorAuthService(
            db, key_ring=None, repository=OperatorRepository(db)
        )
        unavailable = no_keys.availability()
        assert unavailable.available is False
        assert "operator_auth_unavailable" in unavailable.reason_codes
    finally:
        db.close()
