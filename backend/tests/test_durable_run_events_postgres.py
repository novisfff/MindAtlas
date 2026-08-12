"""PostgreSQL two-session race tests for Plan 06 durable Run CAS + events.

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` is set (same gate as Task 1).
These tests prove concurrent CAS, event-key races, stop/result/ready-for-memory
convergence, and gap-free sequence allocation under real row locks.
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
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

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
        "durable Run event PostgreSQL gate must hard-fail instead of skip",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL two-session durable "
        "repository races skipped (SQLite cannot prove SKIP LOCKED / concurrent CAS)"
    ),
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ["APP_ENV"] = "test"
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    os.environ["APP_BUILD_REVISION"] = "test-durable-run-events"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


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
def _session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="module", autouse=True)
def _current_clean_root_schema() -> Iterator[None]:
    with _engine() as engine:
        reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-durable-run-events",
    )
    yield


def _make_run(session: Session, *, status: str = "queued", **kwargs):
    import app.assistant.runtime.models  # noqa: F401
    from app.assistant.models import AssistantChatRun
    from tests.main_agent_postgres_support import insert_complete_main_agent_run

    engine = session.get_bind()
    assert isinstance(engine, Engine)
    run_id = insert_complete_main_agent_run(
        engine,
        status=status,
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        checkpoint_seq=int(kwargs.pop("checkpoint_seq", 0)),
        runtime_contract_version=int(kwargs.pop("runtime_contract_version", 1)),
        required_checkpoint_codec_version=int(
            kwargs.pop("required_checkpoint_codec_version", 3)
        ),
        required_capability_feature_digest=str(
            kwargs.pop("required_capability_feature_digest", "f" * 64)
        ),
        required_app_build_revision=str(
            kwargs.pop("required_app_build_revision", "build-pg-1")
        ),
        runtime_closure_digest=str(
            kwargs.pop("runtime_closure_digest", "9" * 64)
        ),
        capability_ledger_mode=str(
            kwargs.pop("capability_ledger_mode", "enforced")
        ),
        memory_commit_status=str(kwargs.pop("memory_commit_status", "pending")),
    )
    run = session.get(AssistantChatRun, run_id)
    assert run is not None
    for field, value in kwargs.items():
        setattr(run, field, value)
    session.commit()
    session.refresh(run)
    return run


def _seed_running(session: Session, worker_id: str = "w1"):
    now = datetime.now(timezone.utc)
    return _make_run(
        session,
        status="running",
        state_revision=1,
        lease_owner=worker_id,
        lease_generation=1,
        lease_expires_at=now + timedelta(hours=1),
        heartbeat_at=now,
        started_at=now,
    )


def test_two_session_claim_race_one_winner():
    from app.assistant.durable.repository import (
        CODE_INVALID_SOURCE_STATUS,
        CODE_STALE_REVISION,
        DurableRunConflict,
        DurableRunRepository,
    )

    with _engine() as engine:
        with _session(engine) as s0:
            run = _make_run(s0, status="queued")
            run_id = run.id

        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, str | None]] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                barrier.wait(timeout=10)
                try:
                    result = repo.claim_queued(
                        run_id=run_id,
                        expected_revision=0,
                        worker_id=name,
                        lease_ttl=timedelta(seconds=30),
                    )
                    with lock:
                        outcomes.append(("ok", result.run.lease_owner))
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append((exc.code, None))

        t1 = threading.Thread(target=worker, args=("worker-a",))
        t2 = threading.Thread(target=worker, args=("worker-b",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        oks = [o for o in outcomes if o[0] == "ok"]
        fails = [o for o in outcomes if o[0] != "ok"]
        assert len(oks) == 1, outcomes
        assert len(fails) == 1, outcomes
        assert fails[0][0] in {
            CODE_STALE_REVISION,
            CODE_INVALID_SOURCE_STATUS,
        }

        with _session(engine) as s:
            from app.assistant.models import AssistantChatRun

            final = s.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.status == "running"
            assert final.state_revision == 1
            assert final.lease_owner in {"worker-a", "worker-b"}


def test_two_session_stop_vs_result_one_legal_terminal():
    """Stop and result race: exactly one of cancelling-path or completed wins."""
    from app.assistant.durable.repository import (
        CODE_INVALID_SOURCE_STATUS,
        CODE_STALE_REVISION,
        DurableRunConflict,
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )

    with _engine() as engine:
        with _session(engine) as s0:
            run = _seed_running(s0)
            run_id = run.id

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def stop_worker() -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                barrier.wait(timeout=10)
                try:
                    r = repo.request_stop(
                        run_id=run_id,
                        expected_revision=1,
                        events=[
                            EventSpec(
                                event_key="run.stop:race",
                                event_name="run.stop",
                                payload={},
                            )
                        ],
                    )
                    with lock:
                        outcomes.append(f"stop:{r.status}")
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append(f"stop_fail:{exc.code}")

        def result_worker() -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
                barrier.wait(timeout=10)
                try:
                    r = repo.commit_running_result(
                        run_id=run_id,
                        expected_revision=1,
                        lease=lease,
                        target_status="completed",
                        events=[
                            EventSpec(
                                event_key="run.completed:race",
                                event_name="run.completed",
                                payload={},
                            )
                        ],
                    )
                    with lock:
                        outcomes.append(f"result:{r.status}")
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append(f"result_fail:{exc.code}")

        t1 = threading.Thread(target=stop_worker)
        t2 = threading.Thread(target=result_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        with _session(engine) as s:
            from app.assistant.models import AssistantChatRun

            final = s.get(AssistantChatRun, run_id)
            assert final is not None
            # Exactly one legal intermediate/terminal from the race:
            # stop wins -> cancelling; result wins -> completed.
            assert final.status in {"cancelling", "completed"}, (final.status, outcomes)
            assert final.state_revision == 2
            winners = [o for o in outcomes if o.startswith("stop:") or o.startswith("result:")]
            losers = [o for o in outcomes if "_fail:" in o]
            assert len(winners) == 1, outcomes
            assert len(losers) == 1, outcomes
            if final.status == "cancelling":
                assert any(o.startswith("stop:cancelling") for o in outcomes)
                assert any(
                    o.endswith(CODE_STALE_REVISION) or o.endswith(CODE_INVALID_SOURCE_STATUS)
                    for o in outcomes
                    if o.startswith("result_fail:")
                )
            else:
                assert any(o.startswith("result:completed") for o in outcomes)


def test_two_session_stop_vs_ready_for_memory():
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import (
        CODE_INVALID_SOURCE_STATUS,
        CODE_RUN_FINALIZING,
        CODE_STALE_REVISION,
        DurableChildBundle,
        DurableRunConflict,
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )

    with _engine() as engine:
        with _session(engine) as s0:
            run = _seed_running(s0)
            run_id = run.id
            # Seed revisions required by checkpoint FKs.
            manifest = AssistantRunManifestRevision(
                run_id=run_id,
                revision=1,
                manifest_digest=DIGEST_A,
                schema_version=1,
                payload={},
            )
            policy = AssistantRunPolicyRevision(
                run_id=run_id,
                revision=1,
                policy_digest=DIGEST_A,
                payload={},
            )
            budget = AssistantRunBudgetRevision(
                run_id=run_id,
                revision=1,
                budget_digest=DIGEST_A,
                payload={},
            )
            obligation = AssistantRunObligationRevision(
                run_id=run_id,
                revision=1,
                obligation_digest=DIGEST_A,
                payload={},
            )
            s0.add_all([manifest, policy, budget, obligation])
            s0.commit()
            s0.refresh(manifest)
            s0.refresh(policy)
            s0.refresh(budget)
            s0.refresh(obligation)
            m_id, p_id, b_id, o_id = manifest.id, policy.id, budget.id, obligation.id

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def stop_worker() -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                barrier.wait(timeout=10)
                try:
                    r = repo.request_stop(run_id=run_id, expected_revision=1)
                    with lock:
                        outcomes.append(f"stop:{r.status}")
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append(f"stop_fail:{exc.code}")

        def memory_worker() -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
                ck = AssistantRunCheckpoint(
                    run_id=run_id,
                    sequence=1,
                    expected_state_revision=1,
                    committed_state_revision=2,
                    schema_version=1,
                    manifest_revision_id=m_id,
                    policy_revision_id=p_id,
                    budget_revision_id=b_id,
                    obligation_revision_id=o_id,
                    provider_message_ordinal=0,
                    provider_transcript_digest=DIGEST_A,
                    phase="ready_for_memory",
                    state_payload={},
                    state_digest=DIGEST_C,
                )
                barrier.wait(timeout=10)
                try:
                    r = repo.enter_ready_for_memory(
                        run_id=run_id,
                        expected_revision=1,
                        lease=lease,
                        events=[
                            EventSpec(
                                event_key="memory.ready:race",
                                event_name="memory.ready",
                                payload={},
                                visibility="internal",
                            )
                        ],
                        children=DurableChildBundle(rows=[ck]),
                    )
                    with lock:
                        outcomes.append(f"memory:{r.status}:ready={repo.is_ready_for_memory(r.run)}")
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append(f"memory_fail:{exc.code}")

        t1 = threading.Thread(target=stop_worker)
        t2 = threading.Thread(target=memory_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        with _session(engine) as s:
            from app.assistant.models import AssistantChatRun
            from app.assistant.durable.repository import DurableRunRepository

            final = s.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.state_revision == 2
            # Either stop won (cancelling) or ready_for_memory won (running+phase).
            assert final.status in {"cancelling", "running"}, (final.status, outcomes)
            winners = [o for o in outcomes if o.startswith("stop:") or o.startswith("memory:")]
            assert len(winners) == 1, outcomes
            if final.status == "running":
                repo = DurableRunRepository(s)
                assert repo.is_ready_for_memory(final)
                # Subsequent stop must return run_finalizing
                with pytest.raises(DurableRunConflict) as ctx:
                    repo.request_stop(run_id=run_id, expected_revision=2)
                assert ctx.value.code == CODE_RUN_FINALIZING


def test_two_session_event_key_identical_and_conflict():
    from app.assistant.durable.repository import (
        CODE_EVENT_KEY_CONFLICT,
        CODE_STALE_REVISION,
        DurableRunConflict,
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )
    from app.assistant.models import AssistantChatRun, AssistantChatRunEvent

    with _engine() as engine:
        with _session(engine) as s0:
            run = _seed_running(s0)
            run_id = run.id

        # Session A inserts key
        with _session(engine) as s1:
            repo = DurableRunRepository(s1)
            lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
            r1 = repo.commit_semantic(
                run_id=run_id,
                expected_revision=1,
                lease=lease,
                events=[
                    EventSpec(
                        event_key="unit.prepared:lu-1",
                        event_name="unit.prepared",
                        payload={"v": 1},
                    )
                ],
            )
            assert r1.run.last_event_seq == 1
            assert r1.state_revision == 2

        # Identical replay reuses, no seq/revision advance (Plan §9 pure no-op).
        with _session(engine) as s2:
            repo = DurableRunRepository(s2)
            lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
            r2 = repo.commit_semantic(
                run_id=run_id,
                expected_revision=2,
                lease=lease,
                events=[
                    EventSpec(
                        event_key="unit.prepared:lu-1",
                        event_name="unit.prepared",
                        payload={"v": 1},
                    )
                ],
            )
            assert r2.run.last_event_seq == 1
            assert r2.reused_event_keys == ("unit.prepared:lu-1",)
            assert r2.inserted_event_keys == ()
            assert r2.state_revision == 2

        # Conflicting payload advances nothing
        with _session(engine) as s3:
            repo = DurableRunRepository(s3)
            lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
            with pytest.raises(DurableRunConflict) as ctx:
                repo.commit_semantic(
                    run_id=run_id,
                    expected_revision=2,  # still at 2 after pure identical no-op
                    lease=lease,
                    events=[
                        EventSpec(
                            event_key="unit.prepared:lu-1",
                            event_name="unit.prepared",
                            payload={"v": 999},
                        )
                    ],
                )
            assert ctx.value.code == CODE_EVENT_KEY_CONFLICT

        with _session(engine) as s4:
            final = s4.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.state_revision == 2
            assert final.last_event_seq == 1
            count = (
                s4.query(AssistantChatRunEvent)
                .filter(AssistantChatRunEvent.run_id == run_id)
                .count()
            )
            assert count == 1


def test_two_session_concurrent_different_event_keys_gap_free():
    """Two sessions append different keys serially after each other — gap-free.

    True parallel appends serialize on the Run row lock; sequences stay contiguous.
    """
    from app.assistant.durable.repository import (
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )
    from app.assistant.models import AssistantChatRun, AssistantChatRunEvent

    with _engine() as engine:
        with _session(engine) as s0:
            run = _seed_running(s0)
            run_id = run.id

        barrier = threading.Barrier(2)
        results: list[int] = []
        lock = threading.Lock()
        errors: list[BaseException] = []

        def appender(key: str, expected_rev_holder: dict) -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
                barrier.wait(timeout=10)
                # Retry loop: one will get stale_revision and re-read.
                for _ in range(5):
                    cur = s.get(AssistantChatRun, run_id)
                    assert cur is not None
                    try:
                        r = repo.commit_semantic(
                            run_id=run_id,
                            expected_revision=int(cur.state_revision),
                            lease=lease,
                            events=[
                                EventSpec(
                                    event_key=key,
                                    event_name="tick",
                                    payload={"k": key},
                                )
                            ],
                        )
                        with lock:
                            results.append(r.run.last_event_seq)
                        return
                    except Exception as exc:  # DurableRunConflict stale
                        s.rollback()
                        from app.assistant.durable.repository import (
                            CODE_STALE_REVISION,
                            DurableRunConflict,
                        )

                        if isinstance(exc, DurableRunConflict) and exc.code == CODE_STALE_REVISION:
                            continue
                        with lock:
                            errors.append(exc)
                        return

        t1 = threading.Thread(target=appender, args=("e:a", {}))
        t2 = threading.Thread(target=appender, args=("e:b", {}))
        t1.start()
        t2.start()
        t1.join(timeout=20)
        t2.join(timeout=20)
        assert not errors, errors
        assert sorted(results) == [1, 2], results

        with _session(engine) as s:
            final = s.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.last_event_seq == 2
            seqs = [
                row.seq
                for row in s.query(AssistantChatRunEvent)
                .filter(AssistantChatRunEvent.run_id == run_id)
                .order_by(AssistantChatRunEvent.seq.asc())
                .all()
            ]
            assert seqs == [1, 2]


def test_two_session_duplicate_cancel_finalizer():
    from app.assistant.durable.repository import (
        CODE_INVALID_SOURCE_STATUS,
        CODE_STALE_REVISION,
        CODE_TERMINAL_IMMUTABLE,
        DurableRunConflict,
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )

    with _engine() as engine:
        with _session(engine) as s0:
            now = datetime.now(timezone.utc)
            run = _make_run(
                s0,
                status="cancelling",
                state_revision=3,
                lease_owner="w1",
                lease_generation=1,
                lease_expires_at=now + timedelta(hours=1),
                cancel_requested_at=now,
                started_at=now,
            )
            run_id = run.id

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def finalizer(name: str) -> None:
            with _session(engine) as s:
                repo = DurableRunRepository(s)
                lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
                barrier.wait(timeout=10)
                try:
                    r = repo.finalize_cancellation(
                        run_id=run_id,
                        expected_revision=3,
                        lease=lease,
                        require_lease=True,
                        events=[
                            EventSpec(
                                event_key=f"run.cancelled:{name}",
                                event_name="run.cancelled",
                                payload={"by": name},
                            )
                        ],
                    )
                    with lock:
                        outcomes.append(f"ok:{r.status}")
                except DurableRunConflict as exc:
                    with lock:
                        outcomes.append(f"fail:{exc.code}")

        t1 = threading.Thread(target=finalizer, args=("a",))
        t2 = threading.Thread(target=finalizer, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        oks = [o for o in outcomes if o.startswith("ok:")]
        fails = [o for o in outcomes if o.startswith("fail:")]
        assert len(oks) == 1, outcomes
        assert len(fails) == 1, outcomes
        assert oks[0] == "ok:cancelled"
        assert fails[0].split(":")[1] in {
            CODE_STALE_REVISION,
            CODE_INVALID_SOURCE_STATUS,
            CODE_TERMINAL_IMMUTABLE,
        }

        with _session(engine) as s:
            from app.assistant.models import AssistantChatRun

            final = s.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.status == "cancelled"
            assert final.state_revision == 4


def test_child_append_rollback_no_seq_gap():
    """Failed child insert rolls back event sequence allocation (single txn)."""
    from app.assistant.durable.models import AssistantRunManifestRevision
    from app.assistant.durable.repository import (
        DurableChildBundle,
        DurableRunRepository,
        EventSpec,
        LeaseToken,
    )
    from app.assistant.models import AssistantChatRun, AssistantChatRunEvent

    with _engine() as engine:
        with _session(engine) as s0:
            run = _seed_running(s0)
            run_id = run.id
            good = AssistantRunManifestRevision(
                run_id=run_id,
                revision=1,
                manifest_digest=DIGEST_A,
                schema_version=1,
                payload={"ok": True},
            )
            s0.add(good)
            s0.commit()

        with _session(engine) as s1:
            repo = DurableRunRepository(s1)
            lease = LeaseToken(run_id=run_id, worker_id="w1", lease_generation=1)
            bad = AssistantRunManifestRevision(
                run_id=run_id,
                revision=1,  # unique conflict
                manifest_digest=DIGEST_B,
                schema_version=1,
                payload={"bad": True},
            )
            with pytest.raises(Exception):
                repo.commit_semantic(
                    run_id=run_id,
                    expected_revision=1,
                    lease=lease,
                    events=[
                        EventSpec(
                            event_key="ghost:1",
                            event_name="ghost",
                            payload={},
                        )
                    ],
                    children=DurableChildBundle(rows=[bad]),
                )

        with _session(engine) as s2:
            final = s2.get(AssistantChatRun, run_id)
            assert final is not None
            assert final.state_revision == 1
            assert final.last_event_seq == 0
            ghost = (
                s2.query(AssistantChatRunEvent)
                .filter(AssistantChatRunEvent.event_key == "ghost:1")
                .one_or_none()
            )
            assert ghost is None


def test_terminal_immutability_postgres():
    from app.assistant.durable.repository import (
        CODE_TERMINAL_IMMUTABLE,
        DurableRunConflict,
        DurableRunRepository,
    )

    with _engine() as engine:
        with _session(engine) as s0:
            run = _make_run(s0, status="failed", state_revision=9)
            run_id = run.id

        with _session(engine) as s1:
            repo = DurableRunRepository(s1)
            with pytest.raises(DurableRunConflict) as ctx:
                repo.request_stop(run_id=run_id, expected_revision=9)
            assert ctx.value.code == CODE_TERMINAL_IMMUTABLE
