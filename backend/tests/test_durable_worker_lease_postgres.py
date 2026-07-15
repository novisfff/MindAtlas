"""PostgreSQL claim / SKIP LOCKED / heartbeat / expiry / takeover tests (Plan 06 Task 5).

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` is set (same gate as Task 1/3).
Proves real concurrent claim, lost-lease (zero-row heartbeat), backoff, draining,
and build/codec incompatibility under PostgreSQL row locks.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL lease/SKIP LOCKED "
        "tests skipped (SQLite cannot prove concurrent claim)"
    ),
)

DIGEST = "a" * 64


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@contextmanager
def _engine():
    assert _POSTGRES_URL
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _ensure_schema(engine) -> None:
    from app.database import Base
    import app.assistant.models  # noqa: F401
    import app.assistant.durable.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def _identity(worker_id: str, *, build: str = "build-pg-1"):
    from app.assistant.durable.worker_registry import WorkerIdentity

    return WorkerIdentity(
        worker_id=worker_id,
        app_build_revision=build,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1,),
        capability_feature_digest=DIGEST,
        hostname_label="pg-test",
    )


def _make_run(session: Session, *, status: str = "queued", **kwargs):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"pg-lease-{uuid.uuid4().hex[:10]}")
    session.add(conv)
    session.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision=kwargs.pop("required_app_build_revision", "build-pg-1"),
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_two_session_claim_skip_locked_one_winner():
    from app.assistant.durable.leases import RunLeaseService

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s0:
            run = _make_run(s0, status="queued")
            run_id = run.id

        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, str | None]] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            with _session(engine) as s:
                svc = RunLeaseService(
                    s,
                    identity=_identity(name),
                    lease_ttl=timedelta(seconds=30),
                )
                barrier.wait(timeout=10)
                claimed = svc.claim_next()
                with lock:
                    if claimed is None:
                        outcomes.append(("none", None))
                    else:
                        outcomes.append(("ok", str(claimed.run_id)))

        t1 = threading.Thread(target=worker, args=("worker-a",))
        t2 = threading.Thread(target=worker, args=("worker-b",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        ok = [o for o in outcomes if o[0] == "ok"]
        none = [o for o in outcomes if o[0] == "none"]
        assert len(ok) == 1, outcomes
        assert len(none) == 1, outcomes
        assert ok[0][1] == str(run_id)

        with _session(engine) as s:
            from app.assistant.durable.repository import DurableRunRepository

            run = DurableRunRepository(s).get_run(run_id)
            assert run is not None
            assert run.status == "running"
            assert run.lease_owner in {"worker-a", "worker-b"}
            assert int(run.lease_generation) == 1
            assert int(run.state_revision) == 1


def test_heartbeat_extends_without_revision_bump_and_lost_lease_zero_rows():
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            run = _make_run(s, status="queued")
            svc = RunLeaseService(s, identity=_identity("hb-worker"))
            claimed = svc.claim_next()
            assert claimed is not None
            rev = claimed.state_revision
            gen = claimed.lease.lease_generation
            expires_before = claimed.run.lease_expires_at

            ok = svc.heartbeat(claimed.lease)
            assert ok is True
            run2 = DurableRunRepository(s).get_run(claimed.run_id)
            assert run2 is not None
            assert int(run2.state_revision) == rev  # no bump
            assert int(run2.lease_generation) == gen
            assert run2.lease_expires_at is not None
            # Expiry should move forward (or at least not go backward).
            if expires_before is not None:
                assert run2.lease_expires_at >= expires_before

            # Wrong generation → zero-row lost lease.
            bad = LeaseToken(
                run_id=claimed.run_id,
                worker_id="hb-worker",
                lease_generation=gen + 99,
            )
            lost = svc.heartbeat(bad)
            assert lost is False

            # Wrong owner → zero-row lost lease.
            bad_owner = LeaseToken(
                run_id=claimed.run_id,
                worker_id="other-worker",
                lease_generation=gen,
            )
            lost2 = svc.heartbeat(bad_owner)
            assert lost2 is False


def test_takeover_expired_running_to_recovering():
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.repository import DurableRunRepository
    from app.common.time import utcnow

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            past = utcnow() - timedelta(seconds=60)
            run = _make_run(
                s,
                status="running",
                state_revision=1,
                lease_owner="dead-worker",
                lease_generation=1,
                lease_expires_at=past,
                heartbeat_at=past,
                started_at=past,
            )
            run_id = run.id

            svc = RunLeaseService(s, identity=_identity("takeover-w"))
            claimed = svc.claim_next()
            assert claimed is not None
            assert claimed.kind == "takeover_running"
            assert claimed.status == "recovering"
            assert claimed.requires_recovery_classification

            run2 = DurableRunRepository(s).get_run(run_id)
            assert run2 is not None
            assert run2.status == "recovering"
            assert run2.lease_owner == "takeover-w"
            assert int(run2.lease_generation) == 2
            assert int(run2.recovery_count) == 1


def test_reclaim_expired_cancelling_cancellation_only():
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.recovery import RecoveryClassifier
    from app.common.time import utcnow

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            past = utcnow() - timedelta(seconds=60)
            run = _make_run(
                s,
                status="cancelling",
                state_revision=2,
                lease_owner="dead-worker",
                lease_generation=1,
                lease_expires_at=past,
                heartbeat_at=past,
                cancel_requested_at=past,
            )
            svc = RunLeaseService(s, identity=_identity("cancel-w"))
            claimed = svc.claim_next()
            assert claimed is not None
            assert claimed.kind == "reclaim_cancelling"
            assert claimed.cancellation_only

            clf = RecoveryClassifier(s)
            decision = clf.classify(
                run=claimed.run,
                claim_kind=claimed.kind,
                worker_app_build_revision="build-pg-1",
            )
            assert decision.kind == "cancel_only"
            assert not decision.allow_provider_io

            result = clf.apply_decision(
                run=claimed.run,
                lease=claimed.lease,
                decision=decision,
                expected_revision=claimed.state_revision,
            )
            assert result is not None
            assert result.status == "cancelled"
            assert result.run.lease_owner is None


def test_build_mismatch_not_claimed():
    from app.assistant.durable.leases import RunLeaseService

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            _make_run(
                s,
                status="queued",
                required_app_build_revision="build-other",
            )
            svc = RunLeaseService(s, identity=_identity("w1", build="build-pg-1"))
            claimed = svc.claim_next()
            assert claimed is None


def test_codec_incompatible_worker_never_claims():
    """Worker without codec v1 support must not claim (registration filter)."""
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.worker_registry import WorkerIdentity

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            _make_run(s, status="queued")
            # Construct identity that does not support codec 1.
            identity = WorkerIdentity(
                worker_id="codec-bad",
                app_build_revision="build-pg-1",
                runtime_contract_version=1,
                supported_checkpoint_codec_versions=(99,),
                capability_feature_digest=DIGEST,
            )
            svc = RunLeaseService(s, identity=identity)
            claimed = svc.claim_next()
            assert claimed is None


def test_backoff_sets_next_attempt_and_clears_lease():
    from app.assistant.durable.leases import RunLeaseService
    from app.assistant.durable.repository import DurableRunRepository

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            run = _make_run(s, status="queued")
            svc = RunLeaseService(
                s,
                identity=_identity("backoff-w"),
                lease_ttl=timedelta(seconds=30),
                retry_base_ms=500,
                retry_max_ms=30000,
            )
            claimed = svc.claim_next()
            assert claimed is not None
            result = svc.schedule_backoff(
                lease=claimed.lease,
                expected_revision=claimed.state_revision,
                attempt=0,
                reason_code="transient_error",
            )
            assert result.run.lease_owner is None
            assert result.run.next_attempt_at is not None
            # Not yet due — claim_next should skip.
            claimed2 = svc.claim_next()
            assert claimed2 is None

            # Force due.
            run2 = DurableRunRepository(s).get_run(run.id)
            assert run2 is not None
            run2.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            # Put back to recoverable status with expired lease.
            run2.status = "recovering"
            run2.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            s.commit()

            claimed3 = svc.claim_next()
            assert claimed3 is not None
            assert claimed3.kind == "reclaim_recovering"


def test_draining_stops_claims():
    from app.assistant.durable.leases import RunLeaseService

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            _make_run(s, status="queued")
            svc = RunLeaseService(s, identity=_identity("drain-w"))
            claimed = svc.claim_next(draining=True)
            assert claimed is None


def test_database_time_used_for_expiry_check():
    """Expiry comparison uses database now() (not a frozen Python clock)."""
    from app.assistant.durable.leases import RunLeaseService
    from sqlalchemy import select, func

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            db_now = s.scalar(select(func.now()))
            assert db_now is not None
            # Lease expired relative to DB time.
            past = db_now - timedelta(seconds=5)
            run = _make_run(
                s,
                status="running",
                state_revision=1,
                lease_owner="old",
                lease_generation=1,
                lease_expires_at=past,
                heartbeat_at=past,
            )
            svc = RunLeaseService(s, identity=_identity("dbtime-w"))
            claimed = svc.claim_next()
            assert claimed is not None
            assert claimed.run_id == run.id
            assert claimed.kind == "takeover_running"


def test_live_lease_not_taken_over():
    from app.assistant.durable.leases import RunLeaseService
    from app.common.time import utcnow

    with _engine() as engine:
        _ensure_schema(engine)
        with _session(engine) as s:
            future = utcnow() + timedelta(hours=1)
            _make_run(
                s,
                status="running",
                state_revision=1,
                lease_owner="live-worker",
                lease_generation=1,
                lease_expires_at=future,
                heartbeat_at=utcnow(),
            )
            svc = RunLeaseService(s, identity=_identity("other-w"))
            claimed = svc.claim_next()
            assert claimed is None
