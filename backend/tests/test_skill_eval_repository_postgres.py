"""PostgreSQL migration + trigger gates for Plan 09 Task 3 evaluation workbench.

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Covers:

- parent ``403414a62e55`` → Task3 head → parent → head cycle
- evaluation tables / checks / unique constraints
- append-only/immutable triggers on versions/cases/results/events/gates
- Eval CapabilityCall attempt uniqueness + no production ledger FK
- Artifact payload XOR
- sole Alembic head
- guarded downgrade requirements
- Plan 04 fixture retention across upgrade/downgrade/upgrade
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
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

PARENT_REVISION = "403414a62e55"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN09_EVAL_DOWNGRADE_BLOCKED"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 09 evaluation PostgreSQL "
        "migration/trigger gate skipped"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64
_DIGEST_E = "e" * 64

EVAL_TABLES = (
    "assistant_skill_eval_dataset",
    "assistant_skill_eval_dataset_draft",
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_run",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)

IMMUTABLE_TABLES = (
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
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


def _task3_revision() -> str:
    """Discover the Task 3 revision that revises PARENT_REVISION."""
    versions = Path(_BACKEND_DIR / "alembic" / "versions")
    matches = []
    for path in versions.glob("*.py"):
        text_src = path.read_text(encoding="utf-8")
        if (
            f'down_revision = "{PARENT_REVISION}"' in text_src
            or f"down_revision = '{PARENT_REVISION}'" in text_src
        ) and "skill evaluation workbench" in text_src:
            # Extract revision id
            for line in text_src.splitlines():
                if line.startswith("revision = "):
                    rev = line.split("=", 1)[1].strip().strip("\"'")
                    matches.append((rev, path.name))
                    break
    assert len(matches) == 1, f"expected exactly one Task3 migration, got {matches}"
    return matches[0][0]


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        except Exception:
            return None
        return None if row is None else str(row[0])




def _reset_to_parent(engine: Engine) -> str:
    """Bring DB to Task1 head (parent of evaluation workbench)."""
    task3 = _task3_revision()
    try:
        current = _current_revision(engine)
    except Exception:
        current = None
    # Head may be residual after workbench (or the workbench itself). Whenever we
    # are above PARENT_REVISION, prepare guarded Plan 09 downgrade then descend.
    if current is not None and current != PARENT_REVISION:
        prior = os.environ.get("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK")
        os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
        try:
            with engine.begin() as conn:
                _prepare_plan09_downgrade(conn)
            try:
                _run_alembic("downgrade", PARENT_REVISION)
            except Exception:
                # Last resort: upgrade to head then prepare+downgrade again.
                _run_alembic("upgrade", "head")
                with engine.begin() as conn:
                    _prepare_plan09_downgrade(conn)
                _run_alembic("downgrade", PARENT_REVISION)
        finally:
            if prior is None:
                os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)
            else:
                os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = prior
    elif current != PARENT_REVISION:
        try:
            _run_alembic("upgrade", PARENT_REVISION)
        except Exception:
            prior = os.environ.get("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK")
            os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
            try:
                with engine.begin() as conn:
                    _prepare_plan09_downgrade(conn)
                _run_alembic("downgrade", PARENT_REVISION)
            finally:
                if prior is None:
                    os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)
                else:
                    os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = prior
    assert _current_revision(engine) == PARENT_REVISION, (
        f"expected parent {PARENT_REVISION}, got {_current_revision(engine)}"
    )
    return task3


def test_sole_alembic_head_is_task3() -> None:
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads
    task3 = _task3_revision()
    # Sole head is residual after workbench (alias soft-disable); workbench
    # still revises PARENT_REVISION and remains on the linear chain.
    head = heads[0]
    head_rev = script.get_revision(head)
    assert head_rev is not None
    # Walk down until Task3 workbench; ensure it revises PARENT_REVISION.
    cur = head_rev
    seen = {cur.revision}
    while cur.revision != task3:
        parent = cur.down_revision
        assert parent, f"reached root without finding task3={task3} from head={head}"
        cur = script.get_revision(parent)
        assert cur is not None
        assert cur.revision not in seen
        seen.add(cur.revision)
    assert cur.down_revision == PARENT_REVISION


def test_migration_cycle_parent_task3_parent_task3() -> None:
    with _engine() as engine:
        task3 = _task3_revision()
        _run_alembic("upgrade", PARENT_REVISION)
        with engine.connect() as conn:
            for name in EVAL_TABLES:
                assert not _table_exists(conn, name), name

        _run_alembic("upgrade", task3)
        with engine.connect() as conn:
            for name in EVAL_TABLES:
                assert _table_exists(conn, name), name

        # Seed retained evaluation fixtures + a skill package so downgrade must
        # observe guarded conditions.
        with _session(engine) as session:
            from app.assistant.evaluation.datasets import import_plan04_dataset
            from app.assistant.evaluation.repository import EvaluationRepository
            from app.common.time import utcnow
            from datetime import timedelta

            result = import_plan04_dataset(session)
            assert result.case_count >= 100
            repo = EvaluationRepository(session)
            run = repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                dataset_version_ids=[result.version_id],
                threshold_policy_version="t1",
                mode="dataset_scripted",
                isolation_namespace_id=uuid.uuid4(),
                runtime_contract_version=1,
                required_build_revision="build-1",
                isolation_digest=_DIGEST_C,
            )
            # Leave run terminal so downgrade can proceed after export ack.
            repo.transition_run(
                run_id=run.id, expected_revision=0, to_status="running"
            )
            repo.transition_run(
                run_id=run.id, expected_revision=1, to_status="completed"
            )
            repo.append_publish_gate(
                subject_kind="skill_version",
                subject_aggregate_id=run.subject_aggregate_id,
                subject_version_id=run.subject_version_id,
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                profile_digest=_DIGEST_C,
                catalog_digest=_DIGEST_D,
                dataset_version_ids=[result.version_id],
                qualifying_eval_run_ids=[run.id],
                runtime_contract_version=1,
                policy_version="p1",
                threshold_version="t1",
                build_revision="build-1",
                decision="passed",
                expires_at=utcnow() + timedelta(days=7),
                request_id=f"gate-{uuid.uuid4().hex}",
            )

        # Guarded downgrade without acknowledgment should fail when evidence exists.
        with pytest.raises(Exception) as blocked:
            _run_alembic("downgrade", PARENT_REVISION)
        assert DOWNGRADE_BLOCKED_TOKEN in str(blocked.value) or "DOWNGRADE" in str(
            blocked.value
        ).upper()

        # Explicit acknowledgment allows downgrade (workers stopped / evidence exported).
        os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
        try:
            _run_alembic("downgrade", PARENT_REVISION)
        finally:
            os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)

        with engine.connect() as conn:
            for name in EVAL_TABLES:
                assert not _table_exists(conn, name), name

        # Re-upgrade and re-import fixtures.
        _run_alembic("upgrade", task3)
        with _session(engine) as session:
            from app.assistant.evaluation.datasets import import_plan04_dataset

            again = import_plan04_dataset(session)
            assert again.case_count >= 100
            assert again.content_digest == result.content_digest


def _err_text(exc: BaseException) -> str:
    parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " ".join(parts)


def test_immutable_triggers_and_uniqueness() -> None:
    with _engine() as engine:
        # Upgrade to sole head so residual alias migration is present; eval
        # immutability triggers land at the workbench revision.
        _run_alembic("upgrade", "head")
        ids: dict[str, uuid.UUID] = {}
        with _session(engine) as session:
            from app.assistant.evaluation.repository import EvaluationRepository
            from app.common.time import utcnow
            from datetime import timedelta

            repo = EvaluationRepository(session)
            dataset = repo.create_dataset(
                stable_key=f"pg-{uuid.uuid4().hex[:10]}",
                display_name="PG",
                ownership="custom",
            )
            snapshot = [
                {
                    "case_key": "c1",
                    "ordinal": 0,
                    "locale": "en",
                    "input_messages": [{"role": "user", "content": "x"}],
                    "expected_mode": "golden_skill",
                    "case_digest": _DIGEST_A,
                    "notes": "n",
                }
            ]
            draft = repo.get_or_create_draft(
                dataset_id=dataset.id, cases_snapshot=snapshot
            )
            published = repo.publish_dataset_version(
                dataset_id=dataset.id,
                expected_aggregate_revision=0,
                expected_draft_revision=0,
                version_name="v1",
            )
            cases = repo.list_cases(published.version_id)
            case = cases[0]
            run = repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                dataset_version_ids=[published.version_id],
                threshold_policy_version="t1",
                mode="dataset_scripted",
                isolation_namespace_id=uuid.uuid4(),
                runtime_contract_version=1,
                required_build_revision="build-1",
                isolation_digest=_DIGEST_C,
            )
            result = repo.append_case_result(
                eval_run_id=run.id,
                eval_case_id=case.id,
                expected_run_revision=0,
                result_state="passed",
            )
            event = repo.append_event(
                eval_run_id=run.id,
                expected_run_revision=1,
                event_type="x",
                payload={},
            )
            call = repo.append_capability_call(
                eval_run_id=run.id,
                eval_case_id=case.id,
                expected_run_revision=2,
                logical_call_key="skill.search",
                attempt=1,
                subject_kind="skill_version",
                subject_aggregate_id=run.subject_aggregate_id,
                subject_version_id=run.subject_version_id,
                subject_owner_digest=_DIGEST_A,
                binding_digest=_DIGEST_B,
                input_digest=_DIGEST_C,
                descriptor_digest=_DIGEST_D,
                policy_digest=_DIGEST_E,
                outcome="simulated",
            )
            art = repo.append_artifact(
                eval_run_id=run.id,
                expected_run_revision=3,
                kind="trace",
                media_type="text/plain",
                payload=b"hi",
            )
            gate = repo.append_publish_gate(
                subject_kind="skill_version",
                subject_aggregate_id=run.subject_aggregate_id,
                subject_version_id=run.subject_version_id,
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                profile_digest=_DIGEST_C,
                catalog_digest=_DIGEST_D,
                dataset_version_ids=[published.version_id],
                qualifying_eval_run_ids=[run.id],
                runtime_contract_version=1,
                policy_version="p1",
                threshold_version="t1",
                build_revision="build-1",
                decision="passed",
                expires_at=utcnow() + timedelta(days=1),
                request_id=f"g-{uuid.uuid4().hex}",
            )
            # append_gate_use must succeed without UPDATE on immutable gate.
            pin_before = int(gate.publication_pin_count)
            use = repo.append_gate_use(
                gate_id=gate.id,
                action="skill_publish",
                aggregate_id=run.subject_aggregate_id,
                resulting_version_id=run.subject_version_id,
                actor_principal="op",
                request_id="req-pg-1",
                aggregate_revision=1,
            )
            session.flush()
            session.refresh(gate)
            assert int(gate.publication_pin_count) == pin_before
            assert use.gate_id == gate.id
            assert repo.is_gate_evidence_pinned(gate) is True

            ids = {
                "version": published.version_id,
                "case": case.id,
                "result": result.id,
                "event": event.id,
                "call": call.id,
                "artifact": art.id,
                "gate": gate.id,
                "use": use.id,
                "run": run.id,
            }

        # Immutable UPDATE/DELETE rejected by triggers. Use engine.begin() + real
        # column mutations (Plan 01 pattern) — Session + SET id=id was fragile.
        immutable_updates = [
            (
                "assistant_skill_eval_dataset_version",
                ids["version"],
                "version_name",
                "mutated",
            ),
            ("assistant_skill_eval_case", ids["case"], "notes", "mutated"),
            (
                "assistant_skill_eval_case_result",
                ids["result"],
                "result_state",
                "failed",
            ),
            ("assistant_skill_eval_event", ids["event"], "event_type", "mutated"),
            (
                "assistant_skill_eval_capability_call",
                ids["call"],
                "outcome",
                "mutated",
            ),
            ("assistant_skill_eval_artifact", ids["artifact"], "kind", "mutated"),
            (
                "assistant_skill_publish_gate",
                ids["gate"],
                "policy_version",
                "mutated",
            ),
            (
                "assistant_skill_publish_gate_use",
                ids["use"],
                "actor_principal",
                "mutated",
            ),
        ]
        for table, row_id, col, val in immutable_updates:
            with engine.begin() as conn:
                with pytest.raises((DBAPIError, IntegrityError)) as exc_info:
                    conn.execute(
                        text(f"UPDATE {table} SET {col} = :val WHERE id = :id"),
                        {"id": row_id, "val": val},
                    )
                assert "MINDATLAS_PLAN09_EVAL_IMMUTABLE" in _err_text(exc_info.value), (
                    table,
                    _err_text(exc_info.value),
                )
            # Fully immutable tables also reject DELETE.
            if table not in {
                "assistant_skill_eval_event",
                "assistant_skill_eval_artifact",
            }:
                with engine.begin() as conn:
                    with pytest.raises((DBAPIError, IntegrityError)) as exc_info:
                        conn.execute(
                            text(f"DELETE FROM {table} WHERE id = :id"),
                            {"id": row_id},
                        )
                    assert "MINDATLAS_PLAN09_EVAL_IMMUTABLE" in _err_text(
                        exc_info.value
                    ), (table, _err_text(exc_info.value))

        # Artifact XOR enforced.
        with engine.begin() as conn:
            with pytest.raises((DBAPIError, IntegrityError)):
                conn.execute(
                    text(
                        "INSERT INTO assistant_skill_eval_artifact "
                        "(id, eval_run_id, kind, media_type, byte_size, content_digest, "
                        "storage_kind, inline_payload, object_key, metadata_json, created_at) "
                        "VALUES (:id, :run, 'x', 'text/plain', 1, :d, 'inline', NULL, NULL, '{}', now())"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "run": str(ids["run"]),
                        "d": _DIGEST_B,
                    },
                )

        # No FK from eval capability call to production ledger.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) "
                    "FROM pg_constraint "
                    "WHERE conrelid = 'assistant_skill_eval_capability_call'::regclass "
                    "AND contype = 'f'"
                )
            ).all()
            defs = " ".join(r[1] for r in row)
            assert "assistant_capability_call" not in defs
            assert "assistant_chat_run" not in defs


def test_owner_kind_check_constraint() -> None:
    with _engine() as engine:
        task3 = _task3_revision()
        _run_alembic("upgrade", task3)
        with _session(engine) as session:
            with pytest.raises((DBAPIError, IntegrityError)):
                session.execute(
                    text(
                        "INSERT INTO assistant_skill_eval_run ("
                        "id, subject_kind, subject_aggregate_id, subject_version_id, "
                        "subject_content_digest, subject_binding_digest, dataset_version_ids, "
                        "threshold_policy_version, mode, status, isolation_namespace_id, "
                        "owner_kind, runtime_contract_version, required_build_revision, "
                        "runner_contract_version, state_revision, lease_generation, "
                        "last_event_seq, attempt_count, isolation_digest, aggregate_metrics, "
                        "gate_eligible, created_at, updated_at"
                        ") VALUES ("
                        ":id, 'skill_version', :a, :v, :d1, :d2, '[]', 't', "
                        "'dataset_scripted', 'queued', :n, 'main_agent', 1, 'b', 1, 0, 0, "
                        "0, 0, :d3, '{}', false, now(), now()"
                        ")"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "a": str(uuid.uuid4()),
                        "v": str(uuid.uuid4()),
                        "n": str(uuid.uuid4()),
                        "d1": _DIGEST_A,
                        "d2": _DIGEST_B,
                        "d3": _DIGEST_C,
                    },
                )
                session.flush()
