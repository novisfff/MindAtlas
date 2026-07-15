"""PostgreSQL migration gate for Plan 06 durable agent run foundation (Task 1).

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. CI provides
a disposable PostgreSQL 15 database.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


PLAN05_HEAD = "9ed6f561a381"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN06_DOWNGRADE_BLOCKED_DURABLE_DATA"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN06_DOWNGRADE_ACK_PURGE_DURABLE_DATA"
ACTIVE_STATUSES = (
    "queued",
    "running",
    "recovering",
    "waiting_approval",
    "waiting_input",
    "cancelling",
    "needs_reconciliation",
)

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL durable-run migration gate skipped "
        "(local SQLite cannot exercise Plan 06 upgrade/downgrade/triggers)"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


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
def _engine() -> Engine:
    assert _POSTGRES_URL
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        return None if row is None else str(row[0])


def _err_text(exc: BaseException) -> str:
    parts = [str(exc)]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(str(orig))
    return " | ".join(parts)


def _plan06_revision() -> str:
    """Resolve the sole child of Plan 05 head (Plan 06 durable foundation migration)."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"expected sole alembic head, got {heads}"
    head = heads[0]
    assert head != PLAN05_HEAD, (
        f"Plan 06 migration missing: head is still parent {PLAN05_HEAD}"
    )
    rev = script.get_revision(head)
    assert rev is not None
    assert rev.down_revision == PLAN05_HEAD, (
        f"Plan 06 revision must revise {PLAN05_HEAD}, got down_revision={rev.down_revision}"
    )
    return head


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).fetchone()
    return row is not None


def _column_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).fetchall()
    return {str(r[0]) for r in rows}


def _index_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = :table
            """
        ),
        {"table": table},
    ).fetchall()
    return {str(r[0]) for r in rows}


def _check_names(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE n.nspname = 'public'
              AND t.relname = :table
              AND c.contype = 'c'
            """
        ),
        {"table": table},
    ).fetchall()
    return {str(r[0]) for r in rows}


def _purge_durable_data(conn) -> None:
    """Clear durable Plan 06 data so downgrade can proceed under acknowledgment.

    Maintenance-only helper: sets the purge GUC and also uses
    ``session_replication_role=replica`` so self-referential RESTRICT FKs and
    immutability triggers do not block a full export-and-purge procedure.
    """
    conn.execute(text("SET LOCAL mindatlas.allow_durable_run_purge = 'on'"))
    # Skip user triggers / FK checks for maintenance purge of immutable history.
    conn.execute(text("SET LOCAL session_replication_role = 'replica'"))
    if _table_exists(conn, "assistant_chat_run"):
        conn.execute(
            text(
                """
                UPDATE assistant_chat_run
                SET current_manifest_revision_id = NULL,
                    current_policy_revision_id = NULL,
                    current_checkpoint_id = NULL,
                    current_budget_revision_id = NULL,
                    current_obligation_revision_id = NULL
                WHERE runtime_kind = 'main_agent'
                   OR current_manifest_revision_id IS NOT NULL
                   OR current_policy_revision_id IS NOT NULL
                   OR current_checkpoint_id IS NOT NULL
                   OR current_budget_revision_id IS NOT NULL
                   OR current_obligation_revision_id IS NOT NULL
                """
            )
        )
    for table in (
        "assistant_run_artifact",
        "assistant_run_checkpoint",
        "assistant_run_provider_message",
        "assistant_run_obligation_revision",
        "assistant_run_budget_revision",
        "assistant_run_policy_revision",
        "assistant_run_manifest_revision",
        "assistant_run_artifact_gc",
        "assistant_worker_registration",
    ):
        if _table_exists(conn, table):
            conn.execute(text(f"DELETE FROM {table}"))
    if _table_exists(conn, "assistant_chat_run"):
        conn.execute(
            text("DELETE FROM assistant_chat_run WHERE runtime_kind = 'main_agent'")
        )
    conn.execute(text("SET LOCAL session_replication_role = 'origin'"))


def _reset_to_plan05_parent() -> None:
    """Bring disposable DB to Plan 05 head (parent of Plan 06 durable migration)."""
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True)
    try:
        try:
            current = _current_revision(engine)
        except Exception:
            current = None

        plan06 = None
        try:
            plan06 = _plan06_revision()
        except AssertionError:
            plan06 = None

        if plan06 is not None and current == plan06:
            with engine.begin() as conn:
                _purge_durable_data(conn)
            os.environ[DOWNGRADE_ACK_ENV] = "1"
            try:
                _run_alembic("downgrade", PLAN05_HEAD)
            finally:
                os.environ.pop(DOWNGRADE_ACK_ENV, None)
        elif current != PLAN05_HEAD:
            _run_alembic("upgrade", PLAN05_HEAD)

        assert _current_revision(engine) == PLAN05_HEAD, (
            f"expected Plan 05 parent {PLAN05_HEAD}, got {_current_revision(engine)}"
        )
    finally:
        engine.dispose()


def _insert_conversation(conn) -> uuid.UUID:
    conv_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO assistant_conversation (id, title, is_archived, created_at, updated_at)
            VALUES (:id, :title, false, NOW(), NOW())
            """
        ),
        {"id": conv_id, "title": f"plan06-{conv_id.hex[:8]}"},
    )
    return conv_id


def _insert_run(
    conn,
    *,
    conversation_id: uuid.UUID,
    status: str = "queued",
    runtime_kind: str = "legacy",
    **extra,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    cols = [
        "id",
        "conversation_id",
        "status",
        "last_event_seq",
        "checkpoint_seq",
        "created_at",
        "updated_at",
    ]
    vals = [
        ":id",
        ":conversation_id",
        ":status",
        "0",
        "0",
        "NOW()",
        "NOW()",
    ]
    params: dict = {
        "id": run_id,
        "conversation_id": conversation_id,
        "status": status,
    }
    # After upgrade, runtime_kind exists; before it does not.
    if "runtime_kind" in _column_names(conn, "assistant_chat_run"):
        cols.insert(3, "runtime_kind")
        vals.insert(3, ":runtime_kind")
        params["runtime_kind"] = runtime_kind
        if runtime_kind == "main_agent":
            cols.append("runtime_contract_version")
            vals.append(":rcv")
            params["rcv"] = extra.get("runtime_contract_version", 1)
            cols.append("required_app_build_revision")
            vals.append(":build")
            params["build"] = extra.get("required_app_build_revision", "build-test")
    conn.execute(
        text(
            f"INSERT INTO assistant_chat_run ({', '.join(cols)}) VALUES ({', '.join(vals)})"
        ),
        params,
    )
    return run_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upgrade_creates_schema_and_backfills_legacy() -> None:
    plan06 = _plan06_revision()
    _reset_to_plan05_parent()

    with _engine() as engine:
        with engine.begin() as conn:
            conv_id = _insert_conversation(conn)
            run_id = _insert_run(conn, conversation_id=conv_id, status="queued")
            # Legacy L2 memory row.
            mem_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_conversation_skill_l2_memory
                        (id, conversation_id, skill_name, facts, version, created_at, updated_at)
                    VALUES
                        (:id, :cid, 'legacy-skill', '[]'::json, 1, NOW(), NOW())
                    """
                ),
                {"id": mem_id, "cid": conv_id},
            )
            l1_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_conversation_l1_memory
                        (id, conversation_id, summary_text, created_at, updated_at)
                    VALUES
                        (:id, :cid, 'summary', NOW(), NOW())
                    """
                ),
                {"id": l1_id, "cid": conv_id},
            )

    _run_alembic("upgrade", plan06)

    with _engine() as engine:
        assert _current_revision(engine) == plan06
        with engine.connect() as conn:
            # Child tables exist.
            for table in (
                "assistant_worker_registration",
                "assistant_run_manifest_revision",
                "assistant_run_provider_message",
                "assistant_run_policy_revision",
                "assistant_run_budget_revision",
                "assistant_run_obligation_revision",
                "assistant_run_checkpoint",
                "assistant_run_artifact",
                "assistant_run_artifact_gc",
            ):
                assert _table_exists(conn, table), f"missing {table}"

            run_cols = _column_names(conn, "assistant_chat_run")
            for col in (
                "runtime_kind",
                "state_revision",
                "lease_owner",
                "memory_commit_status",
                "current_manifest_revision_id",
            ):
                assert col in run_cols

            event_cols = _column_names(conn, "assistant_chat_run_event")
            for col in ("event_key", "payload_version", "visibility"):
                assert col in event_cols

            l2_cols = _column_names(conn, "assistant_conversation_skill_l2_memory")
            for col in (
                "skill_package_id",
                "memory_namespace",
                "facts_v2",
                "last_applied_run_id",
            ):
                assert col in l2_cols

            # Backfill: existing run is legacy with no durable pointers.
            row = conn.execute(
                text(
                    """
                    SELECT runtime_kind, state_revision, memory_commit_status,
                           current_manifest_revision_id, lease_generation, recovery_count
                    FROM assistant_chat_run WHERE id = :id
                    """
                ),
                {"id": run_id},
            ).fetchone()
            assert row is not None
            assert row[0] == "legacy"
            assert int(row[1]) == 0
            assert row[2] == "not_applicable"
            assert row[3] is None
            assert int(row[4]) == 0
            assert int(row[5]) == 0

            mem = conn.execute(
                text(
                    """
                    SELECT skill_package_id, memory_namespace, facts_v2, facts
                    FROM assistant_conversation_skill_l2_memory WHERE id = :id
                    """
                ),
                {"id": mem_id},
            ).fetchone()
            assert mem is not None
            assert mem[0] is None
            assert mem[1] is None
            # facts_v2 remains null; legacy facts untouched.
            assert mem[2] is None

            indexes = _index_names(conn, "assistant_chat_run")
            assert "uq_assistant_chat_run_active_conversation" in indexes

            event_indexes = _index_names(conn, "assistant_chat_run_event")
            assert "uq_assistant_chat_run_event_key" in event_indexes

            l2_indexes = _index_names(conn, "assistant_conversation_skill_l2_memory")
            assert "uq_assistant_l2_memory_legacy_conversation_skill" in l2_indexes
            assert "uq_assistant_l2_memory_native_package_namespace" in l2_indexes
            # Old unconditional unique index must be gone.
            assert "ix_assistant_l2_memory_conversation_skill" not in l2_indexes


def test_active_run_uniqueness_and_duplicate_preflight_refusal() -> None:
    plan06 = _plan06_revision()
    _reset_to_plan05_parent()

    with _engine() as engine:
        with engine.begin() as conn:
            conv_id = _insert_conversation(conn)
            _insert_run(conn, conversation_id=conv_id, status="queued")
            _insert_run(conn, conversation_id=conv_id, status="running")

    # Upgrade must refuse when duplicate active rows already exist.
    with pytest.raises(Exception) as exc_info:
        _run_alembic("upgrade", plan06)
    assert "MINDATLAS_PLAN06_DUPLICATE_ACTIVE_RUN" in _err_text(exc_info.value)

    # Clean up to a single active row and upgrade successfully.
    with _engine() as engine:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE assistant_chat_run
                    SET status = 'completed'
                    WHERE conversation_id = :cid AND status = 'running'
                    """
                ),
                {"cid": conv_id},
            )

    _run_alembic("upgrade", plan06)

    with _engine() as engine:
        # Second active run for same conversation must fail (partial unique index).
        with pytest.raises((IntegrityError, DBAPIError)):
            with engine.begin() as conn:
                _insert_run(
                    conn,
                    conversation_id=conv_id,
                    status="queued",
                    runtime_kind="legacy",
                )

        with engine.begin() as conn:
            # Terminal + active is fine.
            _insert_run(
                conn,
                conversation_id=conv_id,
                status="completed",
                runtime_kind="legacy",
            )
            # Different conversation active is fine.
            other = _insert_conversation(conn)
            _insert_run(
                conn,
                conversation_id=other,
                status="recovering",
                runtime_kind="main_agent",
            )


def test_pointer_ownership_and_immutability_and_purge_triggers() -> None:
    plan06 = _plan06_revision()
    _reset_to_plan05_parent()
    _run_alembic("upgrade", plan06)

    with _engine() as engine:
        with engine.begin() as conn:
            conv_a = _insert_conversation(conn)
            conv_b = _insert_conversation(conn)
            run_a = _insert_run(
                conn,
                conversation_id=conv_a,
                status="running",
                runtime_kind="main_agent",
            )
            run_b = _insert_run(
                conn,
                conversation_id=conv_b,
                status="running",
                runtime_kind="main_agent",
            )

            manifest_a = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_manifest_revision
                        (id, run_id, revision, parent_revision_id, parent_digest,
                         manifest_digest, schema_version, payload, created_at)
                    VALUES
                        (:id, :run_id, 1, NULL, NULL, :digest, 1, '{}'::json, NOW())
                    """
                ),
                {"id": manifest_a, "run_id": run_a, "digest": _DIGEST_A},
            )
            policy_a = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_policy_revision
                        (id, run_id, revision, parent_revision_id, parent_digest,
                         policy_digest, payload, created_at)
                    VALUES
                        (:id, :run_id, 1, NULL, NULL, :digest, '{"grants":[]}'::json, NOW())
                    """
                ),
                {"id": policy_a, "run_id": run_a, "digest": _DIGEST_B},
            )
            budget_a = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_budget_revision
                        (id, run_id, revision, parent_revision_id, parent_digest,
                         budget_digest, payload, created_at)
                    VALUES
                        (:id, :run_id, 1, NULL, NULL, :digest, '{}'::json, NOW())
                    """
                ),
                {"id": budget_a, "run_id": run_a, "digest": _DIGEST_B},
            )
            obligation_a = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_obligation_revision
                        (id, run_id, revision, parent_revision_id, parent_digest,
                         obligation_digest, payload, created_at)
                    VALUES
                        (:id, :run_id, 1, NULL, NULL, :digest, '{}'::json, NOW())
                    """
                ),
                {"id": obligation_a, "run_id": run_a, "digest": _DIGEST_C},
            )
            ck_a = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_checkpoint
                        (id, run_id, sequence, expected_state_revision, committed_state_revision,
                         schema_version, manifest_revision_id, policy_revision_id,
                         budget_revision_id, obligation_revision_id,
                         provider_message_ordinal, provider_transcript_digest,
                         phase, logical_unit_id, reason, state_payload, state_digest, created_at)
                    VALUES
                        (:id, :run_id, 1, 0, 1, 1, :m, :p, :b, :o,
                         -1, :td, 'ready_for_provider', NULL, NULL, '{}'::json, :sd, NOW())
                    """
                ),
                {
                    "id": ck_a,
                    "run_id": run_a,
                    "m": manifest_a,
                    "p": policy_a,
                    "b": budget_a,
                    "o": obligation_a,
                    "td": _DIGEST_A,
                    "sd": _DIGEST_A,
                },
            )
            # Valid pointer ownership: same run.
            conn.execute(
                text(
                    """
                    UPDATE assistant_chat_run
                    SET current_manifest_revision_id = :m,
                        current_policy_revision_id = :p,
                        current_budget_revision_id = :b,
                        current_obligation_revision_id = :o,
                        current_checkpoint_id = :ck
                    WHERE id = :run_id
                    """
                ),
                {
                    "m": manifest_a,
                    "p": policy_a,
                    "b": budget_a,
                    "o": obligation_a,
                    "ck": ck_a,
                    "run_id": run_a,
                },
            )

        # Cross-run pointer ownership must fail (deferred constraint trigger fires on commit).
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE assistant_chat_run
                        SET current_manifest_revision_id = :m
                        WHERE id = :run_id
                        """
                    ),
                    {"m": manifest_a, "run_id": run_b},
                )
        assert "POINTER_OWNERSHIP" in _err_text(exc_info.value) or "pointer" in _err_text(
            exc_info.value
        ).lower()

        # Immutability: UPDATE rejected on child rows.
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE assistant_run_manifest_revision
                        SET manifest_digest = :d
                        WHERE id = :id
                        """
                    ),
                    {"d": _DIGEST_B, "id": manifest_a},
                )
        assert "IMMUTABLE" in _err_text(exc_info.value)

        # Direct DELETE rejected without purge flag.
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM assistant_run_manifest_revision WHERE id = :id"),
                    {"id": manifest_a},
                )
        assert "IMMUTABLE" in _err_text(exc_info.value) or "PURGE" in _err_text(
            exc_info.value
        )

        # With purge flag, DELETE is allowed.
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL mindatlas.allow_durable_run_purge = 'on'"))
            # Clear pointers first.
            conn.execute(
                text(
                    """
                    UPDATE assistant_chat_run
                    SET current_manifest_revision_id = NULL,
                        current_policy_revision_id = NULL,
                        current_budget_revision_id = NULL,
                        current_obligation_revision_id = NULL,
                        current_checkpoint_id = NULL
                    WHERE id = :run_id
                    """
                ),
                {"run_id": run_a},
            )
            conn.execute(text("DELETE FROM assistant_run_checkpoint WHERE run_id = :r"), {"r": run_a})
            conn.execute(
                text("DELETE FROM assistant_run_manifest_revision WHERE run_id = :r"),
                {"r": run_a},
            )


def test_upgrade_downgrade_upgrade_and_refuse_durable_data() -> None:
    plan06 = _plan06_revision()
    _reset_to_plan05_parent()
    _run_alembic("upgrade", plan06)

    with _engine() as engine:
        with engine.begin() as conn:
            conv_id = _insert_conversation(conn)
            run_id = _insert_run(
                conn,
                conversation_id=conv_id,
                status="queued",
                runtime_kind="main_agent",
            )
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_run_manifest_revision
                        (id, run_id, revision, parent_revision_id, parent_digest,
                         manifest_digest, schema_version, payload, created_at)
                    VALUES
                        (:id, :run_id, 1, NULL, NULL, :digest, 1, '{}'::json, NOW())
                    """
                ),
                {"id": uuid.uuid4(), "run_id": run_id, "digest": _DIGEST_A},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_worker_registration
                        (worker_id, app_build_revision, runtime_contract_version,
                         supported_checkpoint_codec_versions, capability_feature_digest,
                         started_at, heartbeat_at, draining_at, hostname_label)
                    VALUES
                        ('w1', 'build-1', 1, '[1]'::json, :digest, NOW(), NOW(), NULL, 'h1')
                    """
                ),
                {"digest": _DIGEST_A},
            )

    # Downgrade without acknowledgment must refuse.
    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PLAN05_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    # With acknowledgment but durable data still present: still refuse unless purged.
    # (Plan requires maintenance acknowledgment AND no durable data, or ack after purge.)
    # Our migration refuses when durable data exists unless ACK is set AND data purged
    # by maintenance. Test: set ACK after purge.
    with _engine() as engine:
        with engine.begin() as conn:
            _purge_durable_data(conn)

    os.environ[DOWNGRADE_ACK_ENV] = "1"
    try:
        _run_alembic("downgrade", PLAN05_HEAD)
    finally:
        os.environ.pop(DOWNGRADE_ACK_ENV, None)

    with _engine() as engine:
        assert _current_revision(engine) == PLAN05_HEAD
        with engine.connect() as conn:
            assert not _table_exists(conn, "assistant_run_manifest_revision")
            assert not _table_exists(conn, "assistant_worker_registration")
            run_cols = _column_names(conn, "assistant_chat_run")
            assert "runtime_kind" not in run_cols
            assert "event_key" not in _column_names(conn, "assistant_chat_run_event")

    # Upgrade again works.
    _run_alembic("upgrade", plan06)
    with _engine() as engine:
        assert _current_revision(engine) == plan06
        with engine.connect() as conn:
            assert _table_exists(conn, "assistant_run_manifest_revision")
            assert "runtime_kind" in _column_names(conn, "assistant_chat_run")
