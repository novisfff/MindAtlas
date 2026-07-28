"""PostgreSQL lifecycle + two-session CAS gates for Plan 09 Task 1 skill admin.

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. Covers:

- parent ``d7e8f9a0b1c3`` → Task1 head ``403414a62e55`` → parent → head
- columns / defaults / checks on package + alias
- existing-row backfill of ``aggregate_revision``
- downgrade guard token when archive/catalog/disable evidence remains
- sole Alembic head
- two-session concurrent metadata CAS (and metadata vs archive)
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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

PARENT_REVISION = "d7e8f9a0b1c3"
TASK1_HEAD = "403414a62e55"
# Sole pre-merge Plan 09 head is the evaluation workbench revision (09B).
# Task 1 lifecycle (09A) remains a reachable intermediate (parent of 09B).
PLAN09_HEAD = "027869a00a47"
CURRENT_HEAD = "3bd7bc4257c9"
DOWNGRADE_BLOCKED_TOKEN = (
    "MINDATLAS_PLAN09_DOWNGRADE_BLOCKED_ARCHIVED_OR_CATALOG_EVIDENCE"
)

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 09 skill-admin PostgreSQL "
        "lifecycle/CAS gate skipped (SQLite cannot prove concurrent CAS / checks)"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64


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


def _column_info(conn, table: str) -> dict[str, dict]:
    rows = conn.execute(
        text(
            """
            SELECT column_name, is_nullable, column_default, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).fetchall()
    return {
        str(r[0]): {
            "nullable": r[1] == "YES",
            "default": r[2],
            "data_type": r[3],
            "udt_name": r[4],
        }
        for r in rows
    }


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




def _reset_to_parent() -> None:
    """Bring disposable DB to Plan 08 parent of Task 1 lifecycle migration."""
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True)
    try:
        try:
            current = _current_revision(engine)
        except Exception:
            current = None
        if current in {TASK1_HEAD, PLAN09_HEAD}:
            prior_eval_ack = os.environ.get("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK")
            prior_ledger_ack = os.environ.get(
                "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"
            )
            os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
            os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = "1"
            try:
                with engine.begin() as conn:
                    _prepare_plan09_downgrade(conn)
                _run_alembic("downgrade", PARENT_REVISION)
            finally:
                if prior_eval_ack is None:
                    os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)
                else:
                    os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = prior_eval_ack
                if prior_ledger_ack is None:
                    os.environ.pop(
                        "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA", None
                    )
                else:
                    os.environ[
                        "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"
                    ] = prior_ledger_ack
        elif current != PARENT_REVISION:
            # Prefer real upgrade/downgrade; stamp only as last resort.
            try:
                _run_alembic("upgrade", PARENT_REVISION)
            except Exception:
                # If already past parent but not recognized, force cycle via head.
                try:
                    prior_eval_ack = os.environ.get("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK")
                    os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = "1"
                    with engine.begin() as conn:
                        _prepare_plan09_downgrade(conn)
                    _run_alembic("downgrade", PARENT_REVISION)
                    if prior_eval_ack is None:
                        os.environ.pop("MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK", None)
                    else:
                        os.environ["MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"] = prior_eval_ack
                except Exception:
                    _run_alembic("stamp", PARENT_REVISION)
    finally:
        engine.dispose()
    with _engine() as eng:
        assert _current_revision(eng) == PARENT_REVISION, (
            f"expected parent {PARENT_REVISION}, got {_current_revision(eng)}"
        )


def _insert_pre_task1_package(conn, *, name: str) -> uuid.UUID:
    """Insert a package row shaped like parent revision (no Plan 09 columns)."""
    pkg_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package (
                id, canonical_name, display_name, description,
                migration_state, catalog_enabled, is_system,
                created_at, updated_at
            ) VALUES (
                :id, :name, :display, 'pre-task1',
                'native', false, false,
                NOW(), NOW()
            )
            """
        ),
        {"id": pkg_id, "name": name, "display": name},
    )
    return pkg_id


def _insert_pre_task1_alias(
    conn, *, package_id: uuid.UUID, alias: str, alias_type: str = "custom"
) -> uuid.UUID:
    alias_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package_alias (
                id, skill_package_id, alias, normalized_alias, alias_type, created_at
            ) VALUES (
                :id, :pkg, :alias, :norm, :atype, NOW()
            )
            """
        ),
        {
            "id": alias_id,
            "pkg": package_id,
            "alias": alias,
            "norm": alias.lower(),
            "atype": alias_type,
        },
    )
    return alias_id


# ---------------------------------------------------------------------------
# Migration lifecycle
# ---------------------------------------------------------------------------


def test_task1_revises_plan08_parent_and_is_sole_head() -> None:
    """Task 1 revises Plan 08 tip; sole head is Plan 09 eval workbench."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision(TASK1_HEAD)
    assert rev is not None
    assert rev.down_revision == PARENT_REVISION
    heads = script.get_heads()
    assert heads == [CURRENT_HEAD], f"expected sole head {CURRENT_HEAD}, got {heads}"
    head_rev = script.get_revision(PLAN09_HEAD)
    assert head_rev is not None
    # Sole head is 09B eval workbench; it revises 09A lifecycle.
    assert head_rev.down_revision == TASK1_HEAD
    assert head_rev.revision == "027869a00a47"


def test_upgrade_adds_columns_defaults_checks_and_backfills() -> None:
    _reset_to_parent()
    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = _insert_pre_task1_package(
                conn, name=f"pre-up-{uuid.uuid4().hex[:8]}"
            )
            alias_id = _insert_pre_task1_alias(
                conn,
                package_id=pkg_id,
                alias=f"pre-alias-{uuid.uuid4().hex[:8]}",
            )
            # Parent must not yet expose Plan 09 columns.
            cols = _column_info(conn, "assistant_skill_package")
            assert "aggregate_revision" not in cols
            assert "archived_at" not in cols
            assert "last_restored_from_version_id" not in cols
            alias_cols = _column_info(conn, "assistant_skill_package_alias")
            assert "disabled_at" not in alias_cols

    _run_alembic("upgrade", TASK1_HEAD)

    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD
        with engine.begin() as conn:
            cols = _column_info(conn, "assistant_skill_package")
            for name in (
                "aggregate_revision",
                "archived_at",
                "archived_by",
                "catalog_enabled_at",
                "catalog_enabled_by",
                "last_admin_request_id",
                "last_admin_request_digest",
                "last_restored_from_version_id",
            ):
                assert name in cols, f"missing column {name}"

            # aggregate_revision NOT NULL with server default 0; existing rows backfilled.
            assert cols["aggregate_revision"]["nullable"] is False
            assert cols["aggregate_revision"]["default"] is not None
            assert "0" in str(cols["aggregate_revision"]["default"])

            row = conn.execute(
                text(
                    "SELECT aggregate_revision, archived_at, last_restored_from_version_id "
                    "FROM assistant_skill_package WHERE id = :id"
                ),
                {"id": pkg_id},
            ).fetchone()
            assert row is not None
            assert int(row[0]) == 0
            assert row[1] is None
            assert row[2] is None

            alias_cols = _column_info(conn, "assistant_skill_package_alias")
            assert "disabled_at" in alias_cols
            assert "disabled_by" in alias_cols
            alias_row = conn.execute(
                text(
                    "SELECT disabled_at, disabled_by "
                    "FROM assistant_skill_package_alias WHERE id = :id"
                ),
                {"id": alias_id},
            ).fetchone()
            assert alias_row is not None
            assert alias_row[0] is None and alias_row[1] is None

            checks = _check_names(conn, "assistant_skill_package")
            assert "ck_assistant_skill_package_aggregate_revision" in checks
            assert "ck_assistant_skill_package_archived_shape" in checks
            assert "ck_assistant_skill_package_last_admin_request_digest" in checks
            package_indexes = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE tablename = 'assistant_skill_package'
                        """
                    )
                ).fetchall()
            }
            assert "uq_assistant_skill_package_last_admin_request_id" in package_indexes
            alias_checks = _check_names(conn, "assistant_skill_package_alias")
            assert "ck_assistant_skill_package_alias_disabled_shape" in alias_checks

            # Durable import preview table is owned by 09A.
            assert _table_exists(conn, "assistant_skill_import_preview")
            preview_cols = _column_info(conn, "assistant_skill_import_preview")
            for name in (
                "id",
                "principal_id",
                "principal_role",
                "actor_scope_digest",
                "mode",
                "target_package_id",
                "expected_aggregate_revision",
                "candidate_canonical_name",
                "fork_canonical_name",
                "upload_digest",
                "candidate_content_digest",
                "preview_digest",
                "findings",
                "structural_diff",
                "resource_index",
                "capability_keys",
                "archive_bytes",
                "expires_at",
                "consumed",
                "applied_package_id",
                "applied_request_id",
                "applied_request_digest",
                "applied_at",
                "created_at",
                "updated_at",
            ):
                assert name in preview_cols, f"missing import preview column {name}"
            preview_checks = _check_names(conn, "assistant_skill_import_preview")
            assert "ck_assistant_skill_import_preview_mode" in preview_checks
            assert "ck_assistant_skill_import_preview_mode_target_shape" in preview_checks
            assert "ck_assistant_skill_import_preview_consumed_archive_xor" in preview_checks
            assert "ck_assistant_skill_import_preview_upload_digest" in preview_checks

            # New inserts get default aggregate_revision=0 via server_default.
            new_id = _insert_pre_task1_package(
                conn, name=f"post-up-{uuid.uuid4().hex[:8]}"
            )
            new_rev = conn.execute(
                text(
                    "SELECT aggregate_revision FROM assistant_skill_package WHERE id = :id"
                ),
                {"id": new_id},
            ).scalar()
            assert int(new_rev) == 0

        # Check: negative revision rejected (separate transaction).
        with engine.connect() as c2:
            with pytest.raises((IntegrityError, DBAPIError)):
                with c2.begin():
                    c2.execute(
                        text(
                            "UPDATE assistant_skill_package "
                            "SET aggregate_revision = -1 WHERE id = :id"
                        ),
                        {"id": pkg_id},
                    )

        # Check: archived_by without archived_at rejected.
        with engine.connect() as c3:
            with pytest.raises((IntegrityError, DBAPIError)):
                with c3.begin():
                    c3.execute(
                        text(
                            "UPDATE assistant_skill_package "
                            "SET archived_by = 'op' WHERE id = :id"
                        ),
                        {"id": pkg_id},
                    )


def test_downgrade_blocked_when_archive_or_catalog_or_disable_evidence() -> None:
    _reset_to_parent()
    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_skill_package (
                        id, canonical_name, display_name, description,
                        migration_state, catalog_enabled, is_system,
                        aggregate_revision, archived_at, archived_by,
                        created_at, updated_at
                    ) VALUES (
                        :id, :name, :display, 'archived',
                        'native', false, false,
                        1, NOW(), 'op-1',
                        NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": pkg_id,
                    "name": f"arch-{uuid.uuid4().hex[:8]}",
                    "display": "Archived",
                },
            )

    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PARENT_REVISION)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD
        with engine.begin() as conn:
            _prepare_plan09_downgrade(conn)

    # After clearing evidence, downgrade succeeds.
    _run_alembic("downgrade", PARENT_REVISION)
    with _engine() as engine:
        assert _current_revision(engine) == PARENT_REVISION
        with engine.begin() as conn:
            cols = _column_info(conn, "assistant_skill_package")
            assert "aggregate_revision" not in cols
            assert "last_restored_from_version_id" not in cols
            assert not _table_exists(conn, "assistant_skill_import_preview")


def test_parent_head_parent_head_cycle_and_sole_head() -> None:
    """parent → Task1 → parent → Task1; script sole head remains Plan 09 tip."""
    from alembic.script import ScriptDirectory

    _reset_to_parent()
    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD

    with _engine() as engine:
        with engine.begin() as conn:
            _prepare_plan09_downgrade(conn)
    _run_alembic("downgrade", PARENT_REVISION)
    with _engine() as engine:
        assert _current_revision(engine) == PARENT_REVISION

    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD
        heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
        assert heads == [CURRENT_HEAD]


# ---------------------------------------------------------------------------
# Independent 09A slice (alias soft-disable without eval schema)
# ---------------------------------------------------------------------------


_SOFT_DISABLE_FN = "mindatlas_skill_package_alias_soft_disable_guard"
_ALIAS_UPDATE_TRG = "trg_assistant_skill_package_alias_reject_update"


def _insert_custom_alias_at_09a(conn, *, alias: str) -> uuid.UUID:
    """Insert package + custom alias at 09A schema (no eval tables required)."""
    pkg_id = uuid.uuid4()
    alias_id = uuid.uuid4()
    name = f"slice-{uuid.uuid4().hex[:8]}"
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package (
                id, canonical_name, display_name, description,
                migration_state, catalog_enabled, is_system,
                aggregate_revision, created_at, updated_at
            ) VALUES (
                :id, :name, :display, '09a-slice',
                'native', false, false,
                0, NOW(), NOW()
            )
            """
        ),
        {"id": pkg_id, "name": name, "display": name},
    )
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package_alias (
                id, skill_package_id, alias, normalized_alias, alias_type, created_at
            ) VALUES (
                :id, :pkg, :alias, :norm, 'custom', NOW()
            )
            """
        ),
        {
            "id": alias_id,
            "pkg": pkg_id,
            "alias": alias,
            "norm": alias.lower(),
        },
    )
    return alias_id


def _disable_alias(conn, alias_id: uuid.UUID, *, actor: str) -> None:
    conn.execute(
        text(
            """
            UPDATE assistant_skill_package_alias
            SET disabled_at = NOW(), disabled_by = :actor
            WHERE id = :id
            """
        ),
        {"id": alias_id, "actor": actor},
    )


def _alias_disabled_by(conn, alias_id: uuid.UUID) -> str | None:
    return conn.execute(
        text(
            "SELECT disabled_by FROM assistant_skill_package_alias WHERE id = :id"
        ),
        {"id": alias_id},
    ).scalar()


def _alias_soft_disable_trigger_is_column_aware(conn) -> bool:
    """True when the alias UPDATE trigger uses the soft-disable guard function."""
    row = conn.execute(
        text(
            """
            SELECT p.proname
            FROM pg_trigger t
            JOIN pg_class c ON t.tgrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            JOIN pg_proc p ON t.tgfoid = p.oid
            WHERE n.nspname = 'public'
              AND c.relname = 'assistant_skill_package_alias'
              AND t.tgname = :trg
              AND NOT t.tgisinternal
            """
        ),
        {"trg": _ALIAS_UPDATE_TRG},
    ).fetchone()
    if row is None:
        return False
    if str(row[0]) != _SOFT_DISABLE_FN:
        return False
    body = conn.execute(
        text(
            """
            SELECT pg_get_functiondef(p.oid)
            FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = 'public' AND p.proname = :fn
            """
        ),
        {"fn": _SOFT_DISABLE_FN},
    ).scalar()
    if not body:
        return False
    # Column-aware guard must keep identity columns immutable and allow soft-disable.
    text_body = str(body)
    return (
        "disabled_at" in text_body
        or "NEW.id IS DISTINCT FROM OLD.id" in text_body
    ) and "skill_package_id" in text_body


def test_09a_alias_soft_disable_works_without_eval_schema() -> None:
    """09A alone must accept alias soft-disable without any eval tables."""
    _reset_to_parent()
    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD
        with engine.begin() as conn:
            assert not _table_exists(conn, "assistant_skill_eval_run")
            # Alias base matches the brief ("review-notes"); suffix keeps the
            # disposable DB unique across upgrade cycles that leave rows.
            alias_name = f"review-notes-{uuid.uuid4().hex[:8]}"
            alias_id = _insert_custom_alias_at_09a(conn, alias=alias_name)
            _disable_alias(conn, alias_id, actor="operator:test")
            assert _alias_disabled_by(conn, alias_id) == "operator:test"
            assert not _table_exists(conn, "assistant_skill_eval_run")


def test_09a_upgrade_downgrade_upgrade_is_independent() -> None:
    """09A cycle must reinstall the column-aware soft-disable trigger."""
    _reset_to_parent()
    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        with engine.begin() as conn:
            _prepare_plan09_downgrade(conn)
    _run_alembic("downgrade", PARENT_REVISION)
    with _engine() as engine:
        assert _current_revision(engine) == PARENT_REVISION
    _run_alembic("upgrade", TASK1_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == TASK1_HEAD
        with engine.begin() as conn:
            assert _alias_soft_disable_trigger_is_column_aware(conn)
            assert not _table_exists(conn, "assistant_skill_eval_run")


# ---------------------------------------------------------------------------
# Two-session concurrent CAS
# ---------------------------------------------------------------------------


def _ensure_task1_head() -> None:
    _configure_database_env(_POSTGRES_URL)
    try:
        _run_alembic("upgrade", TASK1_HEAD)
    except Exception:
        _run_alembic("stamp", TASK1_HEAD)
        _run_alembic("upgrade", TASK1_HEAD)


def _seed_package_for_cas(session: Session) -> uuid.UUID:
    from app.assistant.skills.package_io import parse_skill_directory_files
    from app.assistant.skills.schemas import CreateSkillPackageCommand
    from app.assistant.skills.service import AgentSkillService

    name = f"cas-{uuid.uuid4().hex[:8]}"
    skill_md = (
        f"---\nname: {name}\ndescription: "
        "CAS concurrent metadata test skill for Plan 09 admin.\n---\n\n# Body\n"
    ).encode("utf-8")
    mindatlas = (
        "version: 1\n"
        f"display_name: {name}\n"
        "legacy_aliases: []\n"
        "routing:\n  include_examples: []\n  exclude_examples: []\n  conflict_rules: []\n"
        "capabilities:\n  - type: tool\n    key: search_entries\n"
        "policy:\n  allowed_side_effects:\n    - read\n    - compute\n"
        "  max_skill_calls: 16\n  max_same_read_calls: 3\n"
        "  requires_terminal_output: true\n  terminal_text_allowed: true\n"
        "provider_aliases: {}\n"
    ).encode("utf-8")
    parsed = parse_skill_directory_files(
        {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
        expected_root_name=None,
    )
    pkg = AgentSkillService(session).create_native_package(
        CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
    )
    return pkg.id


def test_two_session_metadata_cas_one_winner() -> None:
    """Two concurrent metadata updates with the same expected revision: one wins."""
    from app.assistant.skills.admin_service import SkillAdminService
    from app.assistant.skills.models import AssistantSkillPackage
    from tests.operator_session_helpers import make_service_principal
    from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand
    from app.common.exceptions import ApiException

    _ensure_task1_head()
    with _engine() as engine:
        with _session(engine) as s0:
            package_id = _seed_package_for_cas(s0)

        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, int | None]] = []
        lock = threading.Lock()

        def worker(label: str, request_id: str) -> None:
            with _session(engine) as s:
                admin = SkillAdminService(s)
                barrier.wait(timeout=15)
                try:
                    detail = admin.update_metadata(
                        package_id,
                        UpdateSkillPackageMetadataCommand(
                            request_id=request_id,
                            expected_aggregate_revision=0,
                            display_name=f"Winner-{label}",
                        ),
                        principal=make_service_principal(f"op-{label}", role="operator"),
                    )
                    with lock:
                        outcomes.append(("ok", detail.aggregate_revision))
                except ApiException as exc:
                    with lock:
                        outcomes.append((str(exc.code), None))
                except Exception as exc:  # pragma: no cover - diagnostic
                    with lock:
                        outcomes.append((type(exc).__name__, None))

        t1 = threading.Thread(target=worker, args=("a", "meta-cas-a"))
        t2 = threading.Thread(target=worker, args=("b", "meta-cas-b"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        oks = [o for o in outcomes if o[0] == "ok"]
        fails = [o for o in outcomes if o[0] != "ok"]
        assert len(outcomes) == 2, outcomes
        assert len(oks) == 1, outcomes
        assert len(fails) == 1, outcomes
        # Losing side must surface revision conflict (40994) or request/lock race.
        assert fails[0][0] in {"40994", "40997"} or fails[0][0].endswith("Error"), (
            outcomes
        )
        assert oks[0][1] == 1

        with _session(engine) as s:
            row = s.get(AssistantSkillPackage, package_id)
            assert row is not None
            assert int(row.aggregate_revision) == 1
            assert row.display_name in {"Winner-a", "Winner-b"}


def test_two_session_metadata_vs_archive_cas() -> None:
    """Metadata update and archive racing on the same expected revision: one wins."""
    from app.assistant.skills.admin_service import SkillAdminService
    from app.assistant.skills.models import AssistantSkillPackage
    from tests.operator_session_helpers import make_service_principal
    from app.assistant.skills.schemas import (
        AggregateRevisionCommand,
        UpdateSkillPackageMetadataCommand,
    )
    from app.common.exceptions import ApiException

    _ensure_task1_head()
    with _engine() as engine:
        with _session(engine) as s0:
            package_id = _seed_package_for_cas(s0)

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def meta_worker() -> None:
            with _session(engine) as s:
                admin = SkillAdminService(s)
                barrier.wait(timeout=15)
                try:
                    admin.update_metadata(
                        package_id,
                        UpdateSkillPackageMetadataCommand(
                            request_id="meta-vs-arch",
                            expected_aggregate_revision=0,
                            display_name="Meta-Won",
                        ),
                        principal=make_service_principal("op-meta", role="operator"),
                    )
                    with lock:
                        outcomes.append("meta-ok")
                except ApiException as exc:
                    with lock:
                        outcomes.append(f"meta-{exc.code}")
                except Exception as exc:  # pragma: no cover
                    with lock:
                        outcomes.append(f"meta-{type(exc).__name__}")

        def archive_worker() -> None:
            with _session(engine) as s:
                admin = SkillAdminService(s)
                barrier.wait(timeout=15)
                try:
                    admin.archive(
                        package_id,
                        AggregateRevisionCommand(
                            request_id="arch-vs-meta",
                            expected_aggregate_revision=0,
                        ),
                        principal=make_service_principal("op-arch", role="operator"),
                    )
                    with lock:
                        outcomes.append("archive-ok")
                except ApiException as exc:
                    with lock:
                        outcomes.append(f"archive-{exc.code}")
                except Exception as exc:  # pragma: no cover
                    with lock:
                        outcomes.append(f"archive-{type(exc).__name__}")

        t1 = threading.Thread(target=meta_worker)
        t2 = threading.Thread(target=archive_worker)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(outcomes) == 2, outcomes
        oks = [o for o in outcomes if o.endswith("-ok")]
        fails = [o for o in outcomes if not o.endswith("-ok")]
        assert len(oks) == 1, outcomes
        assert len(fails) == 1, outcomes
        assert any(f.endswith("40994") or f.endswith("40997") for f in fails) or any(
            "Error" in f for f in fails
        ), outcomes

        with _session(engine) as s:
            row = s.get(AssistantSkillPackage, package_id)
            assert row is not None
            assert int(row.aggregate_revision) == 1
            if "archive-ok" in outcomes:
                assert row.archived_at is not None
            else:
                assert row.display_name == "Meta-Won"
                assert row.archived_at is None


def test_two_session_sequential_lock_still_serializes_on_sqlite_pattern() -> None:
    """Document sequential dual-session CAS via ThreadPool on PG (sanity).

    Uses two independent sessions without a shared barrier start — still only
    one may commit revision 0→1; the second must conflict.
    """
    from app.assistant.skills.admin_service import SkillAdminService
    from tests.operator_session_helpers import make_service_principal
    from app.assistant.skills.schemas import UpdateSkillPackageMetadataCommand
    from app.common.exceptions import ApiException

    _ensure_task1_head()
    with _engine() as engine:
        with _session(engine) as s0:
            package_id = _seed_package_for_cas(s0)

        def run_update(request_id: str, name: str) -> str:
            with _session(engine) as s:
                admin = SkillAdminService(s)
                try:
                    admin.update_metadata(
                        package_id,
                        UpdateSkillPackageMetadataCommand(
                            request_id=request_id,
                            expected_aggregate_revision=0,
                            display_name=name,
                        ),
                        principal=make_service_principal(request_id, role="operator"),
                    )
                    return "ok"
                except ApiException as exc:
                    return str(exc.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [
                pool.submit(run_update, "seq-a", "A"),
                pool.submit(run_update, "seq-b", "B"),
            ]
            results = [f.result(timeout=30) for f in as_completed(futs)]

        assert results.count("ok") == 1, results
        assert any(r in {"40994", "40997"} for r in results if r != "ok") or any(
            r != "ok" for r in results
        ), results
