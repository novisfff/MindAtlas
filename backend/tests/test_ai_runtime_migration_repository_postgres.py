"""PostgreSQL migration + repository gates for Plan 10 Task 1 evidence schema.

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Covers:

- parent ``027869a00a47`` → Task1 head → parent → head cycle
- migration/rollout evidence tables / checks / unique constraints
- append-only/immutable triggers
- item/batch transitions, discovery drift, rollout control pointer
- assignment immutability, admission fallback, shadow comparison linkage
- Eval purpose runtime_shadow gate-ineligible
- Plan 06 single-nonterminal production Run uniqueness still holds
- guarded downgrade requirements
- sole Alembic head
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

PARENT_REVISION = "027869a00a47"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_BLOCKED"
TASK1_MESSAGE_TOKEN = "add ai runtime migration audit"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 10 migration PostgreSQL "
        "migration/trigger gate skipped"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64
_DIGEST_E = "e" * 64

MIGRATION_TABLES = (
    "assistant_runtime_migration_item",
    "assistant_runtime_migration_event",
    "assistant_runtime_migration_batch",
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_event",
    "assistant_runtime_rollout_control",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_runtime_shadow_comparison",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
)

IMMUTABLE_TABLES = (
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
    "assistant_runtime_migration_event",
    "assistant_runtime_rollout_event",
)


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
    factory = sessionmaker(bind=engine, autoflush=True, autocommit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :n"
        ),
        {"n": name},
    ).first()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _task1_revision() -> str:
    versions = Path(_BACKEND_DIR / "alembic" / "versions")
    matches = []
    for path in versions.glob("*.py"):
        text_src = path.read_text(encoding="utf-8")
        if (
            f'down_revision = "{PARENT_REVISION}"' in text_src
            or f"down_revision = '{PARENT_REVISION}'" in text_src
        ) and TASK1_MESSAGE_TOKEN in text_src:
            for line in text_src.splitlines():
                if line.startswith("revision = "):
                    rev = line.split("=", 1)[1].strip().strip("\"'")
                    matches.append((rev, path.name))
                    break
    assert len(matches) == 1, f"expected exactly one Task1 migration, got {matches}"
    return matches[0][0]


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        except Exception:
            return None
        return None if row is None else str(row[0])


def _prepare_plan10_downgrade(conn) -> None:
    """Terminalize active batches / shadow runs so guarded downgrade can proceed."""
    if _table_exists(conn, "assistant_runtime_migration_batch"):
        conn.execute(
            text(
                "UPDATE assistant_runtime_migration_batch "
                "SET status = 'cancelled', "
                "    completed_at = COALESCE(completed_at, NOW()), "
                "    state_revision = state_revision + 1 "
                "WHERE status IN ('prepared', 'running')"
            )
        )
    if _table_exists(conn, "assistant_skill_eval_run") and _column_exists(
        conn, "assistant_skill_eval_run", "purpose"
    ):
        conn.execute(
            text(
                "UPDATE assistant_skill_eval_run "
                "SET status = 'cancelled', "
                "    ended_at = COALESCE(ended_at, NOW()), "
                "    state_revision = state_revision + 1 "
                "WHERE purpose = 'runtime_shadow' "
                "AND status IN ('queued', 'running', 'cancelling')"
            )
        )


def _prepare_plan09_for_parent_cycle(conn) -> None:
    """If descending through Plan 09, clear its guards too (when needed)."""
    if _table_exists(conn, "assistant_skill_eval_run"):
        conn.execute(
            text(
                "UPDATE assistant_skill_eval_run "
                "SET status = 'cancelled', "
                "    ended_at = COALESCE(ended_at, NOW()), "
                "    state_revision = state_revision + 1 "
                "WHERE status IN ('queued', 'running', 'cancelling')"
            )
        )
    if _table_exists(conn, "assistant_skill_publish_gate_use"):
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_assistant_skill_publish_gate_use_reject_delete "
                "ON assistant_skill_publish_gate_use"
            )
        )
        conn.execute(text("DELETE FROM assistant_skill_publish_gate_use"))


def _reset_to_parent(engine: Engine) -> str:
    """Bring DB to Plan 09 head (parent of Task 1)."""
    task1 = _task1_revision()
    current = _current_revision(engine)
    if current is not None and current != PARENT_REVISION:
        prior_acks = {
            key: os.environ.get(key)
            for key in (
                "MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK",
                "MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK",
                "MINDATLAS_PLAN10_B2_DOWNGRADE_ACK",
                "MINDATLAS_PLAN10_B2_SKILL_DROP_DOWNGRADE_ACK",
                "MINDATLAS_PLAN10_B2_LEGACY_ID_DROP_DOWNGRADE_ACK",
                "MINDATLAS_PLAN10_B2_LEGACY_DIGEST_DROP_DOWNGRADE_ACK",
                "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK",
                "MINDATLAS_PLAN10_B2_TEST_OVERRIDE",
            )
        }
        for key in prior_acks:
            os.environ[key] = "1"
        try:
            with engine.begin() as conn:
                _prepare_plan10_downgrade(conn)
            try:
                _run_alembic("downgrade", PARENT_REVISION)
            except Exception:
                _run_alembic("upgrade", "head")
                with engine.begin() as conn:
                    _prepare_plan10_downgrade(conn)
                    _prepare_plan09_for_parent_cycle(conn)
                _run_alembic("downgrade", PARENT_REVISION)
        finally:
            for key, prior in prior_acks.items():
                if prior is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prior
    elif current != PARENT_REVISION:
        _run_alembic("upgrade", PARENT_REVISION)
    assert _current_revision(engine) == PARENT_REVISION, (
        f"expected parent {PARENT_REVISION}, got {_current_revision(engine)}"
    )
    return task1


def _seed_conversation_and_run(session: Session, *, runtime_kind: str = "legacy"):
    """Insert the historical Plan-10 Task-1 run shape without current ORM.

    These cases intentionally stop at the Task-1 migration, whose
    ``assistant_chat_run`` table predates Plan 2's frozen runtime-identity
    columns.  The current ORM must not be used against that historical schema.
    """
    conv_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO assistant_conversation
                (id, title, is_archived, created_at, updated_at)
            VALUES
                (:id, :title, false, NOW(), NOW())
            """
        ),
        {"id": conv_id, "title": "plan10-test"},
    )
    session.execute(
        text(
            """
            INSERT INTO assistant_chat_run
                (id, conversation_id, status, runtime_kind, last_event_seq,
                 checkpoint_seq, state_revision, lease_generation,
                 recovery_count, memory_commit_status, created_at, updated_at)
            VALUES
                (:id, :conversation_id, :status, :runtime_kind, 0,
                 0, 0, 0, 0, 'pending', NOW(), NOW())
            """
        ),
        {
            "id": run_id,
            "conversation_id": conv_id,
            "status": "completed",
            "runtime_kind": runtime_kind,
        },
    )
    return (
        SimpleNamespace(id=conv_id, title="plan10-test"),
        SimpleNamespace(id=run_id, conversation_id=conv_id),
    )


def _insert_historical_run(
    session: Session,
    *,
    conversation_id: uuid.UUID,
    status: str,
    runtime_kind: str,
) -> uuid.UUID:
    """Insert one historical Run for constraints tested before Plan 2."""
    run_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO assistant_chat_run
                (id, conversation_id, status, runtime_kind, last_event_seq,
                 checkpoint_seq, state_revision, lease_generation,
                 recovery_count, memory_commit_status, created_at, updated_at)
            VALUES
                (:id, :conversation_id, :status, :runtime_kind, 0,
                 0, 0, 0, 0, 'pending', NOW(), NOW())
            """
        ),
        {
            "id": run_id,
            "conversation_id": conversation_id,
            "status": status,
            "runtime_kind": runtime_kind,
        },
    )
    return run_id


def _seed_eval_run(session: Session, *, purpose: str = "admin_evaluation"):
    from app.assistant.evaluation.repository import EvaluationRepository

    repo = EvaluationRepository(session)
    return repo.create_run(
        subject_kind="skill_version",
        subject_aggregate_id=uuid.uuid4(),
        subject_version_id=uuid.uuid4(),
        subject_content_digest=_DIGEST_A,
        subject_binding_digest=_DIGEST_B,
        dataset_version_ids=[],
        threshold_policy_version="t1",
        mode="interactive_scripted",
        isolation_namespace_id=uuid.uuid4(),
        runtime_contract_version=1,
        required_build_revision="build-1",
        isolation_digest=_DIGEST_C,
        purpose=purpose,
    )


def test_sole_alembic_head_is_task1() -> None:
    """Sole head remains linear; Task 1 audit revision is an ancestor of tip."""
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    task1 = _task1_revision()
    tip = heads[0]
    assert script.get_revision(task1) is not None
    walk = list(script.walk_revisions(base="base", head=tip))
    rev_ids = {r.revision for r in walk}
    assert task1 in rev_ids, f"task1 {task1} not in ancestry of tip {tip}"
    task1_rev = script.get_revision(task1)
    assert task1_rev is not None
    assert task1_rev.down_revision == PARENT_REVISION


def test_migration_cycle_parent_task1_parent_task1() -> None:
    with _engine() as engine:
        task1 = _reset_to_parent(engine)
        with engine.connect() as conn:
            for name in MIGRATION_TABLES:
                assert not _table_exists(conn, name), name
            assert not _column_exists(conn, "assistant_skill_eval_run", "purpose")

        _run_alembic("upgrade", task1)
        with engine.connect() as conn:
            for name in MIGRATION_TABLES:
                assert _table_exists(conn, name), name
            assert _column_exists(conn, "assistant_skill_eval_run", "purpose")
            # singleton control row
            row = conn.execute(
                text(
                    "SELECT singleton_key, state_revision, active_rollout_revision_id "
                    "FROM assistant_runtime_rollout_control"
                )
            ).first()
            assert row is not None
            assert row[0] == "singleton"
            assert int(row[1]) == 0
            assert row[2] is None

        # Seed evidence so downgrade requires ACK.
        with _session(engine) as session:
            from app.assistant.migration.repository import RuntimeMigrationRepository

            repo = RuntimeMigrationRepository(session)
            repo.upsert_discovered_item(
                subject_kind="skill",
                source_type="legacy_skill",
                source_id="seed-1",
                source_name="quick_stats",
                source_name_normalized="quick_stats",
                source_digest=_DIGEST_A,
                actor_principal="op-1",
            )
            repo.prepare_rollout_revision(
                revision_label="legacy-default-v1",
                runtime_mode="legacy",
                eligible_closure_digest=_DIGEST_B,
                build_revision="development",
                cohort_salt_fingerprint=_DIGEST_C,
            )

        # Downgrade without ACK must fail when evidence exists.
        blocked = False
        try:
            os.environ.pop("MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK", None)
            _run_alembic("downgrade", PARENT_REVISION)
        except Exception as exc:
            blocked = DOWNGRADE_BLOCKED_TOKEN in str(exc)
        assert blocked, "expected guarded downgrade refusal without ACK"

        prior = os.environ.get("MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK")
        os.environ["MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK"] = "1"
        try:
            with engine.begin() as conn:
                _prepare_plan10_downgrade(conn)
            _run_alembic("downgrade", PARENT_REVISION)
        finally:
            if prior is None:
                os.environ.pop("MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK", None)
            else:
                os.environ["MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK"] = prior

        assert _current_revision(engine) == PARENT_REVISION
        with engine.connect() as conn:
            for name in MIGRATION_TABLES:
                assert not _table_exists(conn, name), name

        _run_alembic("upgrade", task1)
        assert _current_revision(engine) == task1


def test_repository_item_batch_rollout_and_immutability() -> None:
    with _engine() as engine:
        task1 = _reset_to_parent(engine)
        _run_alembic("upgrade", task1)

        with _session(engine) as session:
            from app.assistant.migration.repository import (
                CODE_IMMUTABLE,
                CODE_STALE_REVISION,
                RuntimeMigrationRepository,
                RuntimeMigrationRepositoryError,
            )

            def _assert_update_blocked(sql: str, params: dict) -> None:
                """Trigger refusal must not abort the outer transaction."""
                nested = session.begin_nested()
                try:
                    session.execute(text(sql), params)
                    session.flush()
                    raise AssertionError("expected immutability trigger to fire")
                except (IntegrityError, DBAPIError):
                    nested.rollback()
                else:  # pragma: no cover
                    nested.rollback()

            repo = RuntimeMigrationRepository(session)
            item, outcome = repo.upsert_discovered_item(
                subject_kind="skill",
                source_type="legacy_skill",
                source_id="skill-pg-1",
                source_name="quick_stats",
                source_name_normalized="quick_stats",
                source_digest=_DIGEST_A,
            )
            assert outcome == "created"
            mapped = repo.transition_item(
                item_id=item.id,
                expected_revision=int(item.state_revision),
                to_state="mapped",
                target_type="package",
                target_id=str(uuid.uuid4()),
                target_digest=_DIGEST_D,
            )
            assert mapped.state == "mapped"

            # Append-only: direct UPDATE on event must fail.
            event = repo.list_item_events(item.id)[0]
            _assert_update_blocked(
                "UPDATE assistant_runtime_migration_event "
                "SET new_state = 'archived' WHERE id = :id",
                {"id": str(event.id)},
            )

            batch = repo.prepare_batch(
                command_kind="inventory",
                source_snapshot_digest=_DIGEST_A,
                configuration_digest=_DIGEST_B,
                build_revision="development",
                schema_revision=task1,
                environment="test",
                database_fingerprint="fp-pg",
                request_id=f"req-{uuid.uuid4()}",
            )
            running = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=0,
                to_status="running",
            )
            assert running.status == "running"
            with pytest.raises(RuntimeMigrationRepositoryError) as ctx:
                repo.transition_batch(
                    batch_id=batch.id,
                    expected_revision=0,
                    to_status="completed",
                )
            assert ctx.value.code == CODE_STALE_REVISION

            rev = repo.prepare_rollout_revision(
                revision_label=f"rev-{uuid.uuid4().hex[:8]}",
                runtime_mode="legacy",
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                cohort_salt_fingerprint=_DIGEST_B,
            )
            # Immutable revision content: UPDATE blocked by trigger.
            _assert_update_blocked(
                "UPDATE assistant_runtime_rollout_revision "
                "SET shadow_percent = 50 WHERE id = :id",
                {"id": str(rev.id)},
            )

            control = repo.ensure_rollout_control()
            control = repo.activate_rollout_revision(
                rollout_revision_id=rev.id,
                expected_control_revision=int(control.state_revision),
            )
            assert control.active_rollout_revision_id == rev.id

            conv_id = uuid.uuid4()
            a1 = repo.create_assignment(
                conversation_id=conv_id,
                rollout_revision_id=rev.id,
                assigned_runtime_kind="legacy",
                assignment_reason="hash",
                cohort_key_digest=_DIGEST_C,
            )
            with pytest.raises(RuntimeMigrationRepositoryError) as ctx2:
                repo.create_assignment(
                    conversation_id=conv_id,
                    rollout_revision_id=rev.id,
                    assigned_runtime_kind="main_agent",
                    assignment_reason="hash",
                    cohort_key_digest=_DIGEST_C,
                )
            assert ctx2.value.code == CODE_IMMUTABLE

            # Assignment UPDATE blocked.
            _assert_update_blocked(
                "UPDATE assistant_runtime_rollout_assignment "
                "SET assigned_runtime_kind = 'main_agent' WHERE id = :id",
                {"id": str(a1.id)},
            )

            gate = repo.append_cleanup_gate(
                gate_kind="deploy_b1",
                decision="failed",
                schema_revision=task1,
                build_revision="development",
                inventory_digest=_DIGEST_A,
                evidence_digest=_DIGEST_B,
                snapshot_counts={"blockers": 1},
            )
            assert gate.decision == "failed"
            _assert_update_blocked(
                "UPDATE assistant_runtime_cleanup_gate "
                "SET decision = 'passed' WHERE id = :id",
                {"id": str(gate.id)},
            )


def test_shadow_comparison_and_eval_purpose_and_plan06_unique() -> None:
    with _engine() as engine:
        task1 = _reset_to_parent(engine)
        _run_alembic("upgrade", task1)

        with _session(engine) as session:
            from app.assistant.evaluation.repository import (
                CODE_SYNTHETIC_GATE_INELIGIBLE,
                EvaluationRepository,
                EvaluationRepositoryError,
            )
            from app.assistant.migration.repository import RuntimeMigrationRepository
            conv, prod_run = _seed_conversation_and_run(session, runtime_kind="legacy")
            # Second nonterminal production run must fail Plan 06 unique.
            _insert_historical_run(
                session,
                conversation_id=conv.id,
                status="queued",
                runtime_kind="legacy",
            )
            with pytest.raises((IntegrityError, DBAPIError)):
                _insert_historical_run(
                    session,
                    conversation_id=conv.id,
                    status="running",
                    runtime_kind="legacy",
                )
            session.rollback()

            # Re-seed after rollback.
            conv, prod_run = _seed_conversation_and_run(session, runtime_kind="legacy")
            eval_run = _seed_eval_run(session, purpose="runtime_shadow")
            assert eval_run.purpose == "runtime_shadow"
            assert eval_run.gate_eligible is False

            eval_repo = EvaluationRepository(session)
            # Claim then try to mark gate_eligible — must fail for runtime_shadow.
            eval_repo.transition_run(
                run_id=eval_run.id, expected_revision=0, to_status="running"
            )
            with pytest.raises(EvaluationRepositoryError) as ctx:
                eval_repo.transition_run(
                    run_id=eval_run.id,
                    expected_revision=1,
                    to_status="completed",
                    gate_eligible=True,
                )
            assert ctx.value.code == CODE_SYNTHETIC_GATE_INELIGIBLE

            # Direct DB constraint: purpose=runtime_shadow + gate_eligible=true.
            with pytest.raises((IntegrityError, DBAPIError)):
                session.execute(
                    text(
                        "UPDATE assistant_skill_eval_run "
                        "SET gate_eligible = true WHERE id = :id"
                    ),
                    {"id": str(eval_run.id)},
                )
                session.flush()
            session.rollback()

            # Re-create for comparison pair after rollback.
            conv, prod_run = _seed_conversation_and_run(session, runtime_kind="legacy")
            eval_run = _seed_eval_run(session, purpose="runtime_shadow")
            # Production + Eval may coexist (eval is not a ChatRun).
            mig = RuntimeMigrationRepository(session)
            pair = mig.create_shadow_comparison(
                production_run_id=prod_run.id,
                eval_run_id=eval_run.id,
                input_digest=_DIGEST_A,
                context_digest=_DIGEST_B,
                private_input_payload_digest=_DIGEST_C,
            )
            assert pair.production_run_id == prod_run.id
            assert pair.eval_run_id == eval_run.id

            # Shadow comparison UPDATE blocked (append-only update trigger).
            with pytest.raises((IntegrityError, DBAPIError)):
                session.execute(
                    text(
                        "UPDATE assistant_runtime_shadow_comparison "
                        "SET result_state = 'match' WHERE id = :id"
                    ),
                    {"id": str(pair.id)},
                )
                session.flush()
            session.rollback()


def test_admission_fallback_event_with_legacy_run() -> None:
    with _engine() as engine:
        task1 = _reset_to_parent(engine)
        _run_alembic("upgrade", task1)

        with _session(engine) as session:
            from app.assistant.migration.repository import (
                CODE_CONFLICT,
                RuntimeMigrationRepository,
                RuntimeMigrationRepositoryError,
            )

            repo = RuntimeMigrationRepository(session)
            rev = repo.prepare_rollout_revision(
                revision_label=f"fb-{uuid.uuid4().hex[:8]}",
                runtime_mode="legacy",
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                cohort_salt_fingerprint=_DIGEST_B,
            )
            _conv, legacy_run = _seed_conversation_and_run(
                session, runtime_kind="legacy"
            )
            req = f"admission-{uuid.uuid4()}"
            event = repo.record_admission_fallback(
                request_id=req,
                rollout_revision_id=rev.id,
                resulting_legacy_run_id=legacy_run.id,
                admission_failure_digest=_DIGEST_C,
                build_revision="development",
                schema_revision=task1,
            )
            assert event.candidate_runtime_kind == "main_agent"
            assert event.selected_runtime_kind == "legacy"
            same = repo.record_admission_fallback(
                request_id=req,
                rollout_revision_id=rev.id,
                resulting_legacy_run_id=legacy_run.id,
                admission_failure_digest=_DIGEST_C,
            )
            assert same.id == event.id

            _conv2, other_run = _seed_conversation_and_run(
                session, runtime_kind="legacy"
            )
            with pytest.raises(RuntimeMigrationRepositoryError) as ctx:
                repo.record_admission_fallback(
                    request_id=req,
                    rollout_revision_id=rev.id,
                    resulting_legacy_run_id=other_run.id,
                    admission_failure_digest=_DIGEST_C,
                )
            assert ctx.value.code == CODE_CONFLICT


def test_discovery_backfill_pg() -> None:
    with _engine() as engine:
        task1 = _reset_to_parent(engine)
        _run_alembic("upgrade", task1)

        fixture = (
            _BACKEND_DIR
            / "tests"
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        import json

        records = json.loads(fixture.read_text(encoding="utf-8"))
        records = {
            **records,
            "schema_head": task1,
            "environment": "pg-test",
            "database_fingerprint": "pg-fp",
            "build_revision": "development",
        }

        with _session(engine) as session:
            from app.assistant.migration.discovery import (
                backfill_discovered_from_records,
            )
            from app.assistant.migration.repository import RuntimeMigrationRepository

            result = backfill_discovered_from_records(
                session,
                records,
                request_id=f"pg-backfill-{uuid.uuid4()}",
                actor_principal="op-pg",
                dry_run=False,
                batch_size=100,
            )
            assert result.created > 0
            assert result.batch_id is not None
            repo = RuntimeMigrationRepository(session)
            batch = repo.get_batch(result.batch_id)
            assert batch is not None
            assert batch.status == "completed"
            assert batch.processed_count == result.created + result.unchanged + result.drifted
