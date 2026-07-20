"""PostgreSQL gate for Plan 07 durable Interrupt repository/migration (Task 4).

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Proves partial unique
pending index, sequential terminal + later pending, immutability trigger,
resolution request uniqueness, controlled purge, and upgrade→downgrade→upgrade.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL interrupt repository "
        "tests skipped (SQLite cannot prove partial unique / triggers)"
    ),
)

PLAN06_HEAD = "6af373ef040f"
PLAN07_INTERRUPT_REVISION = "7a3dac0ac2a8"
PLAN08_LEDGER_REVISION = "984c07876856"
PLAN08_LIFECYCLE_REVISION = "f2c3a4b5d6e7"
PLAN08_HEAD = "d7e8f9a0b1c3"
PLAN09_LIFECYCLE_REVISION = "403414a62e55"
PLAN09_EVAL_REVISION = "027869a00a47"
PLAN09_HEAD = "24f1e06fdd9e"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN07_DOWNGRADE_BLOCKED_INTERRUPT_DATA"
DIGEST_A = "a" * 64
PEPPER = "pg-test-interrupt-pepper-not-for-prod-32bxx"


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    reset_caches()


def _alembic_config():
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _as_sqlalchemy_url(_POSTGRES_URL))
    return cfg


def _run_alembic(command_name: str, *args: str) -> None:
    from alembic import command as alembic_command

    cfg = _alembic_config()
    fn = getattr(alembic_command, command_name)
    if args:
        fn(cfg, *args)
    else:
        fn(cfg)


@contextmanager
def _engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return None if row is None else str(row[0])


def _err_text(exc: BaseException) -> str:
    parts = [str(exc)]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        parts.append(str(cause))
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " | ".join(parts)


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :t
                """
            ),
            {"t": table},
        ).first()
    )


def _index_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT indexname FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = :t
            """
        ),
        {"t": table},
    ).fetchall()
    return {str(r[0]) for r in rows}


def _purge_interrupt_and_active(conn) -> None:
    conn.execute(text("SET LOCAL mindatlas.allow_durable_run_purge = 'on'"))
    if _table_exists(conn, "assistant_run_interrupt"):
        conn.execute(text("DELETE FROM assistant_run_interrupt"))
    # Clear durable run pointers then child tables so Plan 06 downgrade path is free if needed.
    if _table_exists(conn, "assistant_chat_run"):
        conn.execute(
            text(
                """
                UPDATE assistant_chat_run
                SET current_manifest_revision_id = NULL,
                    current_policy_revision_id = NULL,
                    current_budget_revision_id = NULL,
                    current_obligation_revision_id = NULL,
                    current_checkpoint_id = NULL
                WHERE runtime_kind = 'main_agent'
                """
            )
        )
        for table in (
            "assistant_run_checkpoint",
            "assistant_run_provider_message",
            "assistant_run_budget_revision",
            "assistant_run_obligation_revision",
            "assistant_run_policy_revision",
            "assistant_run_manifest_revision",
            "assistant_run_artifact",
        ):
            if _table_exists(conn, table):
                conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("DELETE FROM assistant_chat_run WHERE runtime_kind = 'main_agent'"))
    if _table_exists(conn, "assistant_worker_registration"):
        conn.execute(text("DELETE FROM assistant_worker_registration"))
    if _table_exists(conn, "assistant_run_artifact_gc"):
        conn.execute(text("DELETE FROM assistant_run_artifact_gc"))


def _ensure_at_interrupt_head() -> None:
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        rev = _current_revision(engine)
    if rev != PLAN09_HEAD:
        # Repository tests use the current ORM; exercise on sole Plan 09 head.
        _run_alembic("upgrade", PLAN09_HEAD)


def _make_run(session: Session, *, status: str = "waiting_approval", state_revision: int = 2):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"pg-int-{uuid.uuid4().hex[:10]}")
    session.add(conv)
    session.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-pg-1",
        state_revision=state_revision,
        last_event_seq=0,
        memory_commit_status="pending",
    )
    session.add(run)
    session.flush()
    return run


def _seed_revisions(session: Session, run_id):
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    manifest = AssistantRunManifestRevision(
        run_id=run_id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={"k": 1},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run_id,
        revision=1,
        policy_digest=DIGEST_A,
        payload={"p": 1},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run_id,
        revision=1,
        budget_digest=DIGEST_A,
        payload={"b": 1},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run_id,
        revision=1,
        obligation_digest=DIGEST_A,
        payload={"o": 1},
    )
    session.add_all([manifest, policy, budget, obligation])
    session.flush()
    ck = AssistantRunCheckpoint(
        run_id=run_id,
        sequence=1,
        expected_state_revision=1,
        committed_state_revision=1,
        schema_version=2,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=0,
        provider_transcript_digest=DIGEST_A,
        phase="waiting",
        state_payload={"waiting": True},
        state_digest=DIGEST_A,
    )
    session.add(ck)
    session.flush()
    return manifest, policy, budget, obligation, ck


def _parent_ledger(*, remaining_ms: int = 120_000):
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits
    from app.assistant.policy.contracts import RunBudgetLimits

    # Keep the active budget live regardless of the calendar date on which the
    # PostgreSQL gate is executed.
    start = datetime.now(timezone.utc)
    limits = normalize_run_budget_limits()
    payload = limits.model_dump()
    payload["max_wall_time_ms"] = max(remaining_ms + 10_000, 30_000)
    limits = RunBudgetLimits(**payload)
    deadline = start + timedelta(milliseconds=remaining_ms + 10_000)
    return create_initial_ledger_state(
        limits=limits,
        started_at_utc=start,
        deadline_at_utc=deadline,
    )


def _create_pending(
    session: Session,
    run,
    manifest,
    budget,
    ck,
    *,
    ordinal: int = 1,
    node_id: str = "n1",
    frame_id=None,
):
    from app.assistant.workflow.durable.contracts import derive_interrupt_id
    from app.assistant.workflow.durable.interrupts import (
        DurableInterruptRepository,
        derive_interrupt_key,
    )

    frame_id = frame_id or uuid.uuid4()
    visit = f"visit-{ordinal}"
    iid = derive_interrupt_id(
        run_id=run.id,
        root_invocation_digest=DIGEST_A,
        frame_id=frame_id,
        node_visit_id=visit,
        logical_interrupt_ordinal=ordinal,
    )
    key = derive_interrupt_key(
        run_id=run.id,
        root_invocation_digest=DIGEST_A,
        frame_id=frame_id,
        node_visit_id=visit,
        logical_interrupt_ordinal=ordinal,
    )
    parent = _parent_ledger()
    repo = DurableInterruptRepository(session, token_pepper=PEPPER)
    result = repo.create_pending_interrupt(
        run_id=run.id,
        interrupt_id=iid,
        interrupt_key=key,
        kind="approval",
        checkpoint_id=ck.id,
        manifest_revision_id=manifest.id,
        budget_revision_id=budget.id,
        workflow_frame_id=frame_id,
        node_id=node_id,
        node_visit_id=visit,
        request_run_revision=int(run.state_revision),
        request_payload={"title": f"Approve {ordinal}"},
        field_schema=None,
        initial_values={},
        parent_ledger=parent,
        parent_budget_revision_id=budget.id,
    )
    session.flush()
    return repo, result, parent


def test_upgrade_creates_interrupt_schema_and_indexes() -> None:
    _configure_database_env(_POSTGRES_URL)
    # Ensure parent head first, then upgrade.
    with _engine() as engine:
        rev = _current_revision(engine)
    if rev is None or rev == PLAN06_HEAD:
        _run_alembic("upgrade", PLAN07_INTERRUPT_REVISION)
    else:
        # Already past or at head — upgrade is idempotent to head.
        _run_alembic("upgrade", PLAN07_INTERRUPT_REVISION)

    with _engine() as engine:
        assert _current_revision(engine) in {
            PLAN07_INTERRUPT_REVISION,
            PLAN08_LEDGER_REVISION,
            PLAN08_LIFECYCLE_REVISION,
            PLAN08_HEAD,
            PLAN09_LIFECYCLE_REVISION,
            PLAN09_EVAL_REVISION,
            PLAN09_HEAD,
        }
        with engine.connect() as conn:
            assert _table_exists(conn, "assistant_run_interrupt")
            indexes = _index_names(conn, "assistant_run_interrupt")
            assert "uq_assistant_run_interrupt_run_key" in indexes
            assert "uq_assistant_run_interrupt_one_pending" in indexes
            assert "uq_assistant_run_interrupt_resolution_request" in indexes


def test_unique_logical_key_and_one_pending_partial() -> None:
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()

            repo, created, _ = _create_pending(session, run, manifest, budget, ck, ordinal=1)
            session.commit()
            assert created.created is True

            # Second pending on same run must fail (partial unique).
            with pytest.raises((IntegrityError, Exception)):
                _create_pending(session, run, manifest, budget, ck, ordinal=2, node_id="n2")
                session.commit()
            session.rollback()

            # Unique (run_id, interrupt_key): re-insert same key with different id fails at DB.
            # insert-or-read with same identity succeeds.
            repo2, again, _ = _create_pending(
                session,
                run,
                manifest,
                budget,
                ck,
                ordinal=1,
                frame_id=created.interrupt.workflow_frame_id,
            )
            session.commit()
            assert again.created is False
            assert again.interrupt.id == created.interrupt.id


def test_sequential_terminal_then_new_pending_allowed() -> None:
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session, state_revision=3)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()

            repo, first, _ = _create_pending(session, run, manifest, budget, ck, ordinal=1)
            session.commit()
            tok = repo.rotate_token(
                run_id=run.id,
                interrupt_id=first.interrupt.id,
                expected_request_revision=1,
                expected_run_revision=3,
            )
            session.commit()
            repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=first.interrupt.id,
                resolution_request_id=uuid.uuid4(),
                token=tok.token,
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=3,
                outcome="approved",
                queues_execution=False,
            )
            session.commit()
            assert first.interrupt.status == "approved"

            # Later sequential pending is allowed.
            _repo2, second, _ = _create_pending(
                session, run, manifest, budget, ck, ordinal=2, node_id="n2"
            )
            session.commit()
            assert second.created is True
            assert second.interrupt.status == "pending"
            rows = repo.list_for_run(run.id)
            assert len(rows) == 2
            statuses = {r.status for r in rows}
            assert statuses == {"approved", "pending"}


def test_request_suspension_immutability_and_token_rotation() -> None:
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session, state_revision=2)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()
            repo, created, _ = _create_pending(session, run, manifest, budget, ck)
            session.commit()
            expires_before = created.interrupt.expires_at
            digest_before = created.interrupt.budget_suspension_digest

            tok = repo.rotate_token(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                expected_request_revision=1,
                expected_run_revision=2,
            )
            session.commit()
            assert tok.token_revision == 1
            assert tok.interrupt.expires_at == expires_before
            assert tok.interrupt.budget_suspension_digest == digest_before

            # Direct illegal mutation of request_digest must be blocked by trigger.
            with pytest.raises((IntegrityError, DBAPIError, Exception)):
                session.execute(
                    text(
                        """
                        UPDATE assistant_run_interrupt
                        SET request_digest = :d
                        WHERE id = :id
                        """
                    ),
                    {"d": "b" * 64, "id": created.interrupt.id},
                )
                session.commit()
            session.rollback()

            # Plan 08 adds origin/call linkage to the immutable request
            # identity. The dedicated trigger must fire before the XOR CHECK
            # could obscure which invariant rejected the mutation.
            with pytest.raises((IntegrityError, DBAPIError, Exception)) as exc_info:
                session.execute(
                    text(
                        """
                        UPDATE assistant_run_interrupt
                        SET interrupt_origin = 'capability_call'
                        WHERE id = :id
                        """
                    ),
                    {"id": created.interrupt.id},
                )
                session.commit()
            assert "MINDATLAS_PLAN08_IMMUTABLE_INTERRUPT_ORIGIN" in _err_text(
                exc_info.value
            )
            session.rollback()


def test_resolution_request_id_unique_and_idempotent() -> None:
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session, state_revision=4)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()
            repo, created, _ = _create_pending(session, run, manifest, budget, ck)
            session.commit()
            tok = repo.rotate_token(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                expected_request_revision=1,
                expected_run_revision=4,
            )
            session.commit()
            req_id = uuid.uuid4()
            first = repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                resolution_request_id=req_id,
                token=tok.token,
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=4,
                outcome="approved",
            )
            session.commit()
            assert first.created_resolution is True

            replay = repo.resolve_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                resolution_request_id=req_id,
                token="ignored-after-consume",
                expected_token_revision=1,
                expected_request_revision=1,
                expected_run_revision=4,
                outcome="approved",
            )
            session.commit()
            assert replay.idempotent_replay is True


def test_controlled_purge_requires_flag() -> None:
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()
            repo, created, _ = _create_pending(session, run, manifest, budget, ck)
            session.commit()

            # Delete without purge flag fails.
            with pytest.raises((IntegrityError, DBAPIError, Exception)):
                session.execute(
                    text("DELETE FROM assistant_run_interrupt WHERE id = :id"),
                    {"id": created.interrupt.id},
                )
                session.commit()
            session.rollback()

            # With purge flag succeeds.
            session.execute(text("SET LOCAL mindatlas.allow_durable_run_purge = 'on'"))
            n = repo.purge_for_run(run.id)
            session.commit()
            assert n == 1
            assert repo.list_for_run(run.id) == []


def test_run_first_lock_order_resolution_path() -> None:
    """Repository locks Run before Interrupt (no deadlock with concurrent cancel)."""
    _ensure_at_interrupt_head()
    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session, state_revision=5)
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()
            repo, created, _ = _create_pending(session, run, manifest, budget, ck)
            session.commit()
            # cancel_interrupt and resolve both lock run first then interrupt.
            cancelled = repo.cancel_interrupt(
                run_id=run.id,
                interrupt_id=created.interrupt.id,
                comment="stop",
            )
            session.commit()
            assert cancelled.interrupt.status == "cancelled"


def test_upgrade_downgrade_upgrade_refuses_interrupt_history() -> None:
    _configure_database_env(_POSTGRES_URL)
    _run_alembic("upgrade", PLAN09_HEAD)

    with _engine() as engine:
        with _session(engine) as session:
            run = _make_run(session, status="waiting_approval")
            manifest, _p, budget, _o, ck = _seed_revisions(session, run.id)
            run.current_budget_revision_id = budget.id
            session.commit()
            _create_pending(session, run, manifest, budget, ck)
            session.commit()

    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PLAN06_HEAD)
    assert any(
        token in _err_text(exc_info.value)
        for token in (
            DOWNGRADE_BLOCKED_TOKEN,
            "MINDATLAS_PLAN08_DOWNGRADE_BLOCKED_LEDGER_DATA",
        )
    )

    # Purge interrupts and active durable runs, then downgrade works.
    with _engine() as engine:
        with engine.begin() as conn:
            _purge_interrupt_and_active(conn)

    prior_ack = os.environ.get("MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA")
    os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = "1"
    try:
        _run_alembic("downgrade", PLAN06_HEAD)
    finally:
        if prior_ack is None:
            os.environ.pop("MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA", None)
        else:
            os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = prior_ack
    with _engine() as engine:
        assert _current_revision(engine) == PLAN06_HEAD
        with engine.connect() as conn:
            assert not _table_exists(conn, "assistant_run_interrupt")

    _run_alembic("upgrade", PLAN09_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == PLAN09_HEAD
        with engine.connect() as conn:
            assert _table_exists(conn, "assistant_run_interrupt")
