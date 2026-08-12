"""PostgreSQL gate for the durable Interrupt repository on the clean root.

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Proves partial unique
 pending index, sequential terminal + later pending, immutability trigger,
 resolution request uniqueness, and controlled purge.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

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

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL interrupt repository "
        "tests skipped (SQLite cannot prove partial unique / triggers)"
    ),
)

DIGEST_A = "a" * 64
PEPPER = "pg-test-interrupt-pepper-not-for-prod-32bxx"


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ["APP_ENV"] = "test"
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    os.environ["APP_BUILD_REVISION"] = "test-durable-interrupt"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


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


def _err_text(exc: BaseException) -> str:
    parts = [str(exc)]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        parts.append(str(cause))
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " | ".join(parts)


def _ensure_at_current_head() -> None:
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-durable-interrupt",
    )


def _make_run(session: Session, *, status: str = "waiting_approval", state_revision: int = 2):
    import app.assistant.runtime.models  # noqa: F401
    from app.assistant.models import AssistantChatRun
    from tests.main_agent_postgres_support import insert_complete_main_agent_run

    engine = session.get_bind()
    assert isinstance(engine, Engine)
    run_id = insert_complete_main_agent_run(
        engine,
        status=status,
        state_revision=state_revision,
    )
    run = session.get(AssistantChatRun, run_id)
    assert run is not None
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


def test_unique_logical_key_and_one_pending_partial() -> None:
    _ensure_at_current_head()
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
    _ensure_at_current_head()
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
    _ensure_at_current_head()
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
    _ensure_at_current_head()
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
    _ensure_at_current_head()
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
    _ensure_at_current_head()
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
