"""PostgreSQL execution/trigger gate for the Plan 08 Attempt lifecycle.

Skipped unless ``MINDATLAS_TEST_POSTGRES_URL`` points at a disposable database.
SQLite cannot exercise the PL/pgSQL lifecycle trigger covered here.

Local invocation (the database must be independently created and disposable)::

    MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1 \
    MINDATLAS_TEST_POSTGRES_URL=postgresql://.../mindatlas_test_plan08_<suffix> \
    python -m pytest tests/test_capability_call_migration_postgres.py -q
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL unset; PostgreSQL Attempt lifecycle tests "
        "skipped (SQLite cannot exercise PL/pgSQL triggers)"
    ),
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
WORKER_ID = "plan08-attempt-pg-worker"
PLAN08_LEDGER_REVISION = "984c07876856"
PLAN08_LIFECYCLE_REVISION = "f2c3a4b5d6e7"
PLAN08_EVIDENCE_REVISION = "d7e8f9a0b1c3"
PLAN09_LIFECYCLE_REVISION = "403414a62e55"
PLAN09_EVAL_REVISION = "027869a00a47"
PLAN09_HEAD = "24f1e06fdd9e"
DISPOSABLE_DATABASE_PREFIX = "mindatlas_test_plan08_"
DESTRUCTIVE_OPT_IN_ENV = "MINDATLAS_TEST_POSTGRES_DESTRUCTIVE"


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


def _assert_disposable_database(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("MINDATLAS_TEST_POSTGRES_URL must use PostgreSQL")
    database = str(parsed.database or "").lower()
    if not database.startswith(DISPOSABLE_DATABASE_PREFIX):
        raise RuntimeError(
            "MINDATLAS_TEST_POSTGRES_URL must use the generated disposable "
            f"database prefix {DISPOSABLE_DATABASE_PREFIX!r}"
        )
    if os.environ.get(DESTRUCTIVE_OPT_IN_ENV, "").strip() != "1":
        raise RuntimeError(
            f"{DESTRUCTIVE_OPT_IN_ENV}=1 is required for destructive PostgreSQL tests"
        )


def _alembic_config():
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _as_sqlalchemy_url(_POSTGRES_URL))
    return cfg



def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    ).first()
    return row is not None

def _prepare_plan09_downgrade(conn) -> None:
    """Prepare DB for Plan 09 guarded downgrades without touching immutable rows.

    - Complete/cancel active eval runs (downgrade refuses queued/running).
    - Clear package archive/catalog/alias evidence (blocks 09A lifecycle downgrade).
    Immutable eval tables are dropped by the migration itself once ACK is set.
    """
    if _table_exists(conn, "assistant_skill_eval_run"):
        # Terminalize active runs so eval downgrade active-run guard passes.
        conn.execute(
            text(
                "UPDATE assistant_skill_eval_run "
                "SET status = 'cancelled', "
                "    ended_at = COALESCE(ended_at, NOW()), "
                "    state_revision = state_revision + 1 "
                "WHERE status IN ('queued', 'running', 'cancelling')"
            )
        )
    # gate_use pins block eval downgrade even with ACK — must remove uses.
    # gate_use is IMMUTABLE (no DELETE). Drop the reject-delete trigger temporarily.
    if _table_exists(conn, "assistant_skill_publish_gate_use"):
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_assistant_skill_publish_gate_use_reject_delete "
                "ON assistant_skill_publish_gate_use"
            )
        )
        conn.execute(text("DELETE FROM assistant_skill_publish_gate_use"))
    if _table_exists(conn, "assistant_skill_package"):
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET "
                "archived_at = NULL, archived_by = NULL, "
                "catalog_enabled_at = NULL, catalog_enabled_by = NULL, "
                "catalog_enabled = false"
            )
        )
    if _table_exists(conn, "assistant_skill_package_alias"):
        conn.execute(
            text(
                "UPDATE assistant_skill_package_alias SET "
                "disabled_at = NULL, disabled_by = NULL "
                "WHERE disabled_at IS NOT NULL"
            )
        )
    if _table_exists(conn, "assistant_main_agent_profile"):
        conn.execute(
            text(
                "UPDATE assistant_main_agent_profile SET runtime_enabled = false"
            )
        )




def _downgrade_to_ledger_revision() -> None:
    """Downgrade to Plan 08 ledger revision, clearing Plan 09 blockers first."""
    from alembic import command

    prior_eval_ack = os.environ.get("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK")
    prior_ledger_ack = os.environ.get("MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA")
    os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
    os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = "1"
    try:
        with _engine() as engine:
            with engine.begin() as conn:
                _prepare_plan09_downgrade(conn)
        command.downgrade(_alembic_config(), PLAN08_LEDGER_REVISION)
    finally:
        if prior_eval_ack is None:
            os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)
        else:
            os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = prior_eval_ack
        if prior_ledger_ack is None:
            os.environ.pop("MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA", None)
        else:
            os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = prior_ledger_ack



@pytest.fixture(scope="module", autouse=True)
def _upgrade_to_ledger_revision() -> Iterator[None]:
    from alembic import command

    assert _POSTGRES_URL
    _assert_disposable_database(_POSTGRES_URL)
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        with engine.connect() as connection:
            try:
                row = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).first()
                current = None if row is None else str(row[0])
            except DBAPIError:
                current = None
    if current in {
        PLAN08_LIFECYCLE_REVISION,
        PLAN08_EVIDENCE_REVISION,
        PLAN09_LIFECYCLE_REVISION,
        PLAN09_EVAL_REVISION,
        PLAN09_HEAD,
    }:
        # Descend Plan 09 → Plan 08 tip → ledger revision used by this suite.
        _downgrade_to_ledger_revision()
    elif current != PLAN08_LEDGER_REVISION:
        command.upgrade(_alembic_config(), PLAN08_LEDGER_REVISION)
    yield


@contextmanager
def _engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL),
        future=True,
        pool_pre_ping=True,
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


def _seed_claimed_attempt(
    session: Session,
    *,
    external_effect_boundary: bool = False,
):
    from app.assistant.capability_calls.repository import (
        CapabilityCallRepository,
        ProposeCallSpec,
    )
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunManifestRevision,
    )
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.models import AssistantChatRun, Conversation

    conversation = Conversation(title=f"plan08-attempt-pg-{uuid.uuid4().hex[:10]}")
    session.add(conversation)
    session.flush()
    run = AssistantChatRun(
        conversation_id=conversation.id,
        status="running",
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="plan08-attempt-pg-build",
        capability_ledger_mode="enforced",
        state_revision=1,
        last_event_seq=0,
        memory_commit_status="pending",
        lease_owner=WORKER_ID,
        lease_generation=1,
    )
    session.add(run)
    session.flush()
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={"test": "attempt-lifecycle"},
    )
    payload = uuid.uuid4().bytes
    artifact = AssistantRunArtifact(
        run_id=run.id,
        kind="call_input",
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        inline_bytes=payload,
        metadata_json={},
    )
    session.add_all([manifest, artifact])
    session.flush()

    lease = LeaseToken(
        run_id=run.id,
        worker_id=WORKER_ID,
        lease_generation=1,
    )
    repo = CapabilityCallRepository(session)
    call, _created = repo.create_or_verify_proposed(
        ProposeCallSpec(
            call_id=uuid.uuid4(),
            run_id=run.id,
            expected_run_revision=1,
            lease=lease,
            manifest_revision_id=manifest.id,
            logical_call_key=f"provider:pg:{uuid.uuid4().hex}",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="plan08_attempt_test",
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_B,
            input_artifact_id=artifact.id,
            input_digest=DIGEST_A,
            side_effect_class=(
                "write_external" if external_effect_boundary else "compute"
            ),
            execution_mode=(
                "external_idempotent"
                if external_effect_boundary
                else "pure_replayable"
            ),
            idempotency_key=f"plan08-attempt-pg-{uuid.uuid4().hex}",
        )
    )
    call = repo.transition_call(
        call_id=call.id,
        expected_call_revision=0,
        expected_run_revision=1,
        to_status="authorized",
        lease=lease,
    )
    call, attempt = repo.claim_attempt(
        call_id=call.id,
        expected_call_revision=int(call.state_revision),
        expected_run_revision=1,
        lease=lease,
        worker_id=WORKER_ID,
        mark_side_effect_started=external_effect_boundary,
    )
    session.commit()
    return repo, attempt


def _error_text(exc: BaseException) -> str:
    values = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        values.append(str(orig))
    return " | ".join(values)


def test_disposable_database_guard_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(DESTRUCTIVE_OPT_IN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=DESTRUCTIVE_OPT_IN_ENV):
        _assert_disposable_database(
            "postgresql://localhost/mindatlas_test_plan08_guard"
        )


@pytest.mark.parametrize(
    "url",
    (
        "sqlite:///mindatlas_test_plan08_guard.db",
        "postgresql://localhost/production_test",
    ),
)
def test_disposable_database_guard_rejects_wrong_dialect_or_prefix(
    monkeypatch,
    url: str,
) -> None:
    monkeypatch.setenv(DESTRUCTIVE_OPT_IN_ENV, "1")
    with pytest.raises(RuntimeError):
        _assert_disposable_database(url)


def test_stamped_plan08_database_upgrades_to_executable_attempt_lifecycle() -> None:
    from alembic import command

    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(session)
        attempt_id = attempt.id

        with pytest.raises(DBAPIError) as exc_info:
            repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="claimed",
                to_status="dispatched",
                request_digest=DIGEST_A,
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY" in _error_text(exc_info.value)
        session.rollback()

    command.upgrade(_alembic_config(), "head")

    with _engine() as engine, _session(engine) as session:
        from app.assistant.capability_calls.repository import CapabilityCallRepository

        repo = CapabilityCallRepository(session)
        attempt = repo.transition_attempt(
            attempt_id=attempt_id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=DIGEST_A,
        )
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=DIGEST_B,
        )
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="response_received",
            to_status="committed",
        )
        session.commit()

        assert attempt.status == "committed"
        assert attempt.request_digest == DIGEST_A
        assert attempt.response_digest == DIGEST_B
        assert attempt.ended_at is not None


def test_external_effect_boundary_survives_claim_and_dispatch_trigger() -> None:
    from alembic import command

    command.upgrade(_alembic_config(), "head")
    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(
            session,
            external_effect_boundary=True,
        )
        call = repo.get_call(attempt.call_id)
        assert call is not None
        assert call.side_effect_started_at is not None
        assert attempt.side_effect_started is True
        assert attempt.side_effect_started_at == call.side_effect_started_at

        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=DIGEST_A,
        )
        session.commit()

        assert attempt.status == "dispatched"
        assert attempt.side_effect_started is True
        assert attempt.side_effect_started_at == call.side_effect_started_at


def test_attempt_lifecycle_downgrade_restores_unconditional_rejection() -> None:
    from alembic import command

    _downgrade_to_ledger_revision()
    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(session)
        with pytest.raises(DBAPIError) as exc_info:
            repo.transition_attempt(
                attempt_id=attempt.id,
                expected_status="claimed",
                to_status="dispatched",
                request_digest=DIGEST_A,
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY" in _error_text(exc_info.value)
        session.rollback()
    command.upgrade(_alembic_config(), "head")


def test_attempt_trigger_rejects_delete() -> None:
    with _engine() as engine, _session(engine) as session:
        _repo, attempt = _seed_claimed_attempt(session)
        attempt_id = attempt.id

    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text("DELETE FROM assistant_capability_call_attempt WHERE id = :id"),
                {"id": attempt_id},
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY" in _error_text(exc_info.value)


@pytest.mark.parametrize(
    ("assignment", "token"),
    (
        ("attempt_number = attempt_number + 1", "MINDATLAS_PLAN08_ATTEMPT_IMMUTABLE"),
        ("worker_id = 'forged-worker'", "MINDATLAS_PLAN08_ATTEMPT_IMMUTABLE"),
    ),
)
def test_attempt_trigger_rejects_identity_or_counter_rewrite(
    assignment: str,
    token: str,
) -> None:
    with _engine() as engine, _session(engine) as session:
        _repo, attempt = _seed_claimed_attempt(session)
        attempt_id = attempt.id

    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text(
                    "UPDATE assistant_capability_call_attempt "
                    f"SET {assignment} WHERE id = :id"
                ),
                {"id": attempt_id},
            )
        assert token in _error_text(exc_info.value)


def test_attempt_trigger_rejects_evidence_rewrite() -> None:
    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(session)
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=DIGEST_A,
        )
        session.commit()
        attempt_id = attempt.id

    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text(
                    "UPDATE assistant_capability_call_attempt "
                    "SET request_digest = :digest WHERE id = :id"
                ),
                {"digest": DIGEST_B, "id": attempt_id},
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_IMMUTABLE" in _error_text(exc_info.value)


@pytest.mark.parametrize("illegal_status", ("committed", "response_received"))
def test_attempt_trigger_rejects_illegal_status_jump(illegal_status: str) -> None:
    with _engine() as engine, _session(engine) as session:
        _repo, attempt = _seed_claimed_attempt(session)
        attempt_id = attempt.id

    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text(
                    "UPDATE assistant_capability_call_attempt "
                    "SET status = :status WHERE id = :id"
                ),
                {"status": illegal_status, "id": attempt_id},
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_TRANSITION" in _error_text(exc_info.value)


def test_attempt_trigger_rejects_regressive_status_transition() -> None:
    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(session)
        attempt = repo.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=DIGEST_A,
        )
        session.commit()
        attempt_id = attempt.id

    with engine.begin() as connection:
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(
                text(
                    "UPDATE assistant_capability_call_attempt "
                    "SET status = 'claimed' WHERE id = :id"
                ),
                {"id": attempt_id},
            )
        assert "MINDATLAS_PLAN08_ATTEMPT_TRANSITION" in _error_text(exc_info.value)


@pytest.mark.parametrize("operation", ("update", "delete"))
def test_referenced_reconciliation_evidence_artifact_is_immutable(operation: str) -> None:
    from app.assistant.capability_calls.models import AssistantCapabilityReconciliation
    from app.assistant.durable.models import AssistantRunArtifact

    with _engine() as engine, _session(engine) as session:
        repo, attempt = _seed_claimed_attempt(session)
        call = repo.get_call(attempt.call_id)
        payload = b'{"contractVersion":1,"serverIssued":true}'
        artifact = AssistantRunArtifact(
            run_id=call.run_id,
            kind="capability_call_evidence",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            inline_bytes=payload,
            metadata_json={"serverIssued": True},
        )
        session.add(artifact)
        session.flush()
        session.add(
            AssistantCapabilityReconciliation(
                call_id=call.id,
                run_id=call.run_id,
                revision=1,
                decision="mark_failed",
                actor_admin_id=uuid.uuid4(),
                authorization_evidence={"verifiedClaims": []},
                reason="migration immutability test",
                evidence_artifact_ids=[str(artifact.id)],
                expected_call_revision=int(call.state_revision),
                expected_run_revision=1,
                resulting_call_revision=int(call.state_revision),
                resulting_run_revision=2,
                resolution_request_id=uuid.uuid4(),
            )
        )
        session.commit()
        artifact_id = artifact.id

    with engine.begin() as connection:
        statement = (
            "UPDATE assistant_run_artifact SET display_label = 'forged' WHERE id = :id"
            if operation == "update"
            else "DELETE FROM assistant_run_artifact WHERE id = :id"
        )
        with pytest.raises(DBAPIError) as exc_info:
            connection.execute(text(statement), {"id": artifact_id})
        assert "MINDATLAS_PLAN08_RECONCILIATION_EVIDENCE_IMMUTABLE" in _error_text(
            exc_info.value
        )


def test_two_session_cross_call_resolution_request_reuse_is_stable_conflict() -> None:
    from tests.test_capability_call_reconciliation import (
        _evidence_artifact,
        _evidence_verifier,
        _seed_external_call,
        _trusted_authorizer,
    )
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityReconciliation,
    )
    from app.assistant.capability_calls.reconciliation import (
        CapabilityReconciliationService,
        ReconciliationDecisionRequest,
    )
    from app.assistant.capability_calls.repository import CapabilityCallConflict

    resolution_id = uuid.uuid4()
    with _engine() as engine, _session(engine) as first:
        run, call, input_artifact = _seed_external_call(first)
        evidence = _evidence_artifact(
            first,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        other = AssistantCapabilityCall(
            run_id=run.id,
            manifest_revision_id=call.manifest_revision_id,
            logical_call_key=f"provider:pg-other:{uuid.uuid4().hex}",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="external_write",
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            input_artifact_id=input_artifact.id,
            input_digest=DIGEST_A,
            side_effect_class="write_external",
            execution_mode="external_idempotent",
            idempotency_key="idem-" + uuid.uuid4().hex,
            provider_tool_call_id="pg-other-" + uuid.uuid4().hex,
            status="failed",
            state_revision=4,
            attempt_count=1,
        )
        first.add(other)
        first.flush()
        first.add(
            AssistantCapabilityReconciliation(
                call_id=other.id,
                run_id=run.id,
                revision=1,
                decision="mark_failed",
                actor_admin_id=uuid.uuid4(),
                authorization_evidence={"verifiedClaims": []},
                reason="first session",
                evidence_artifact_ids=[],
                expected_call_revision=3,
                expected_run_revision=1,
                resulting_call_revision=4,
                resulting_run_revision=2,
                resolution_request_id=resolution_id,
            )
        )
        first.commit()
        call_id = call.id
        evidence_id = evidence.id

        with _session(engine) as second:
            with pytest.raises(CapabilityCallConflict, match="another Call"):
                CapabilityReconciliationService(
                    second,
                    operator_authorizer=_trusted_authorizer(),
                    evidence_verifier=_evidence_verifier(),
                ).apply(
                    ReconciliationDecisionRequest(
                        call_id=call_id,
                        expected_call_revision=3,
                        expected_run_revision=2,
                        decision="mark_failed",
                        reason="second session must conflict",
                        evidence_artifact_ids=(evidence_id,),
                        resolution_request_id=resolution_id,
                    )
                )
