"""PostgreSQL migration gate for Plan 04 main-agent aggregate flags (Task 1).

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. CI provides
a disposable PostgreSQL 15 database. Does not create backend/tests/_postgres.py
because Plan 01 already owns the disposable URL contract.
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


PLAN03_HEAD = "b666b11a5faa"
PLAN06_HEAD = "6af373ef040f"
PLAN07_HEAD = "7a3dac0ac2a8"
PLAN08_HEAD = "984c07876856"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN04_DOWNGRADE_BLOCKED_ENABLED_AGGREGATES"

CATALOG_CHECK = "ck_assistant_skill_package_catalog_disabled"
RUNTIME_CHECK = "ck_assistant_main_agent_profile_runtime_disabled"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL main-agent migration gate skipped "
        "(local SQLite cannot exercise Plan 04 upgrade/downgrade preflight)"
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


def _plan04_revision() -> str:
    """Resolve the Plan 04 enable-flags migration (child of Plan 03 head).

    Plan 06 and later may extend the chain; the sole Alembic head is no longer
    necessarily Plan 04. Walk the script map for the revision whose
    ``down_revision`` is the Plan 03 parent.
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    # Prefer the known Plan 04 id when present (stable across Plan 05/06 heads).
    known = "9ed6f561a381"
    try:
        rev = script.get_revision(known)
        if rev is not None and rev.down_revision == PLAN03_HEAD:
            return known
    except Exception:
        rev = None
    matches: list[str] = []
    for r in script.walk_revisions():
        if r.down_revision == PLAN03_HEAD:
            matches.append(r.revision)
    assert matches, (
        f"Plan 04 migration missing: no revision revises parent {PLAN03_HEAD}"
    )
    assert len(matches) == 1, (
        f"expected sole Plan 04 child of {PLAN03_HEAD}, got {matches}"
    )
    return matches[0]


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


def _clear_enabled_flags(conn) -> None:
    conn.execute(text("UPDATE assistant_skill_package SET catalog_enabled = false"))
    conn.execute(text("UPDATE assistant_main_agent_profile SET runtime_enabled = false"))


def _reset_to_plan03_parent() -> None:
    """Bring disposable DB to Plan 03 head (parent of Plan 04 flag migration)."""
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True)
    try:
        try:
            current = _current_revision(engine)
        except Exception:
            current = None

        plan04 = None
        try:
            plan04 = _plan04_revision()
        except AssertionError:
            plan04 = None

        if current is not None and current != PLAN03_HEAD:
            # Descendant of Plan 03 (Plan 04/05/06/...): clear enable flags when
            # present, then downgrade through the chain to Plan 03.
            if plan04 is not None:
                with engine.begin() as conn:
                    _clear_enabled_flags(conn)
                    if current in {PLAN06_HEAD, PLAN07_HEAD, PLAN08_HEAD}:
                        from tests.test_durable_interrupt_repository_postgres import (
                            _purge_interrupt_and_active,
                        )

                        _purge_interrupt_and_active(conn)
            # Plan 06 downgrade refuses durable data; empty disposable DBs pass.
            prior_ack = os.environ.get(
                "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"
            )
            os.environ["MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"] = "1"
            try:
                _run_alembic("downgrade", PLAN03_HEAD)
            finally:
                if prior_ack is None:
                    os.environ.pop(
                        "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA", None
                    )
                else:
                    os.environ[
                        "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"
                    ] = prior_ack
        elif current != PLAN03_HEAD:
            # Mid/unknown state: ensure schema reaches parent via upgrade path.
            _run_alembic("upgrade", PLAN03_HEAD)

        assert _current_revision(engine) == PLAN03_HEAD, (
            f"expected Plan 03 parent {PLAN03_HEAD}, got {_current_revision(engine)}"
        )
        with engine.connect() as conn:
            pkg_checks = _check_names(conn, "assistant_skill_package")
            profile_checks = _check_names(conn, "assistant_main_agent_profile")
            assert CATALOG_CHECK in pkg_checks, (
                f"parent schema must retain {CATALOG_CHECK}, got {pkg_checks}"
            )
            assert RUNTIME_CHECK in profile_checks, (
                f"parent schema must retain {RUNTIME_CHECK}, got {profile_checks}"
            )
    finally:
        engine.dispose()


def _insert_package(conn, *, name: str | None = None) -> uuid.UUID:
    package_id = uuid.uuid4()
    canonical = name or f"plan04-pkg-{package_id.hex[:8]}"
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package (
                id, canonical_name, display_name, description,
                migration_state, catalog_enabled, is_system,
                created_at, updated_at
            ) VALUES (
                :id, :name, :display, :desc,
                'shadow', false, false,
                NOW(), NOW()
            )
            """
        ),
        {
            "id": package_id,
            "name": canonical,
            "display": canonical,
            "desc": "plan04-gate",
        },
    )
    return package_id


def _insert_profile(conn, *, key: str | None = None) -> uuid.UUID:
    profile_id = uuid.uuid4()
    profile_key = key or f"plan04-profile-{profile_id.hex[:8]}"
    conn.execute(
        text(
            """
            INSERT INTO assistant_main_agent_profile (
                id, profile_key, display_name, is_default,
                migration_state, runtime_enabled, created_at, updated_at
            ) VALUES (
                :id, :key, :display, false,
                'bootstrap', false, NOW(), NOW()
            )
            """
        ),
        {
            "id": profile_id,
            "key": profile_key,
            "display": profile_key,
        },
    )
    return profile_id


def test_parent_head_is_plan03() -> None:
    """Plan 04 migration must revise the sole Plan 03 head b666b11a5faa."""
    plan04 = _plan04_revision()
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    rev = script.get_revision(plan04)
    assert rev is not None
    assert rev.down_revision == PLAN03_HEAD
    assert len(script.get_heads()) == 1


def test_upgrade_drops_only_disabled_checks_and_preserves_defaults() -> None:
    _reset_to_plan03_parent()
    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = _insert_package(conn, name=f"pre-up-{uuid.uuid4().hex[:8]}")
            profile_id = _insert_profile(conn, key=f"pre-up-{uuid.uuid4().hex[:8]}")

        # Parent still enforces disabled-only checks (separate autocommit connection).
        with engine.connect() as conn:
            with conn.begin():
                with pytest.raises((IntegrityError, DBAPIError)):
                    conn.execute(
                        text(
                            "UPDATE assistant_skill_package "
                            "SET catalog_enabled = true WHERE id = :id"
                        ),
                        {"id": pkg_id},
                    )

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        assert _current_revision(engine) == PLAN08_HEAD
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
        assert heads == [PLAN08_HEAD]

        with engine.begin() as conn:
            pkg_checks = _check_names(conn, "assistant_skill_package")
            profile_checks = _check_names(conn, "assistant_main_agent_profile")
            assert CATALOG_CHECK not in pkg_checks
            assert RUNTIME_CHECK not in profile_checks
            # Other Plan 01 checks must remain.
            assert "ck_assistant_skill_package_migration_state" in pkg_checks
            assert "ck_assistant_main_agent_profile_migration_state" in profile_checks

            # Defaults remain false for preserved rows and new inserts.
            row = conn.execute(
                text(
                    "SELECT catalog_enabled FROM assistant_skill_package WHERE id = :id"
                ),
                {"id": pkg_id},
            ).fetchone()
            assert row is not None and row[0] is False

            row = conn.execute(
                text(
                    "SELECT runtime_enabled FROM assistant_main_agent_profile WHERE id = :id"
                ),
                {"id": profile_id},
            ).fetchone()
            assert row is not None and row[0] is False

            new_pkg = _insert_package(conn, name=f"post-up-{uuid.uuid4().hex[:8]}")
            new_profile = _insert_profile(conn, key=f"post-up-{uuid.uuid4().hex[:8]}")
            assert (
                conn.execute(
                    text(
                        "SELECT catalog_enabled FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": new_pkg},
                ).scalar()
                is False
            )
            assert (
                conn.execute(
                    text(
                        "SELECT runtime_enabled FROM assistant_main_agent_profile WHERE id = :id"
                    ),
                    {"id": new_profile},
                ).scalar()
                is False
            )

            # After upgrade, flags may be set true (checks dropped).
            conn.execute(
                text(
                    "UPDATE assistant_skill_package "
                    "SET catalog_enabled = true WHERE id = :id"
                ),
                {"id": new_pkg},
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile "
                    "SET runtime_enabled = true WHERE id = :id"
                ),
                {"id": new_profile},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT catalog_enabled FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": new_pkg},
                ).scalar()
                is True
            )
            assert (
                conn.execute(
                    text(
                        "SELECT runtime_enabled FROM assistant_main_agent_profile WHERE id = :id"
                    ),
                    {"id": new_profile},
                ).scalar()
                is True
            )
            # Reset so other tests can downgrade.
            _clear_enabled_flags(conn)


def test_data_preservation_across_upgrade() -> None:
    _reset_to_plan03_parent()
    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = _insert_package(conn, name=f"preserve-{uuid.uuid4().hex[:8]}")
            profile_id = _insert_profile(conn, key=f"preserve-{uuid.uuid4().hex[:8]}")
            conn.execute(
                text(
                    """
                    UPDATE assistant_skill_package
                    SET display_name = 'Preserve Me', description = 'keep'
                    WHERE id = :id
                    """
                ),
                {"id": pkg_id},
            )
            conn.execute(
                text(
                    """
                    UPDATE assistant_main_agent_profile
                    SET display_name = 'Preserve Profile'
                    WHERE id = :id
                    """
                ),
                {"id": profile_id},
            )

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        with engine.connect() as conn:
            pkg = conn.execute(
                text(
                    "SELECT canonical_name, display_name, description, catalog_enabled, "
                    "migration_state FROM assistant_skill_package WHERE id = :id"
                ),
                {"id": pkg_id},
            ).fetchone()
            assert pkg is not None
            assert pkg[1] == "Preserve Me"
            assert pkg[2] == "keep"
            assert pkg[3] is False
            assert pkg[4] == "shadow"

            profile = conn.execute(
                text(
                    "SELECT profile_key, display_name, runtime_enabled, migration_state "
                    "FROM assistant_main_agent_profile WHERE id = :id"
                ),
                {"id": profile_id},
            ).fetchone()
            assert profile is not None
            assert profile[1] == "Preserve Profile"
            assert profile[2] is False
            assert profile[3] == "bootstrap"


def test_downgrade_blocked_when_any_flag_true() -> None:
    _reset_to_plan03_parent()
    _run_alembic("upgrade", "head")

    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = _insert_package(conn, name=f"block-pkg-{uuid.uuid4().hex[:8]}")
            conn.execute(
                text(
                    "UPDATE assistant_skill_package "
                    "SET catalog_enabled = true WHERE id = :id"
                ),
                {"id": pkg_id},
            )

    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PLAN03_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    with _engine() as engine:
        assert _current_revision(engine) == PLAN08_HEAD
        with engine.begin() as conn:
            # Still true; no data deletion on blocked downgrade.
            assert (
                conn.execute(
                    text(
                        "SELECT catalog_enabled FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": pkg_id},
                ).scalar()
                is True
            )
            _clear_enabled_flags(conn)

    # Profile path
    with _engine() as engine:
        with engine.begin() as conn:
            profile_id = _insert_profile(conn, key=f"block-prof-{uuid.uuid4().hex[:8]}")
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile "
                    "SET runtime_enabled = true WHERE id = :id"
                ),
                {"id": profile_id},
            )

    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PLAN03_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    with _engine() as engine:
        assert _current_revision(engine) == PLAN08_HEAD
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text(
                        "SELECT runtime_enabled FROM assistant_main_agent_profile WHERE id = :id"
                    ),
                    {"id": profile_id},
                ).scalar()
                is True
            )
            _clear_enabled_flags(conn)


def test_parent_head_parent_head_cycle_and_sole_head() -> None:
    """parent -> head -> parent -> head with sole head after upgrade."""
    _reset_to_plan03_parent()
    with _engine() as engine:
        with engine.begin() as conn:
            pkg_id = _insert_package(conn, name=f"cycle-{uuid.uuid4().hex[:8]}")
            profile_id = _insert_profile(conn, key=f"cycle-{uuid.uuid4().hex[:8]}")

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        assert _current_revision(engine) == PLAN08_HEAD
        with engine.connect() as conn:
            assert CATALOG_CHECK not in _check_names(conn, "assistant_skill_package")
            assert RUNTIME_CHECK not in _check_names(
                conn, "assistant_main_agent_profile"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT catalog_enabled FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": pkg_id},
                ).scalar()
                is False
            )
            assert (
                conn.execute(
                    text(
                        "SELECT runtime_enabled FROM assistant_main_agent_profile WHERE id = :id"
                    ),
                    {"id": profile_id},
                ).scalar()
                is False
            )

    _run_alembic("downgrade", PLAN03_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == PLAN03_HEAD
        with engine.connect() as conn:
            assert CATALOG_CHECK in _check_names(conn, "assistant_skill_package")
            assert RUNTIME_CHECK in _check_names(
                conn, "assistant_main_agent_profile"
            )
            # Data preserved; still false so re-add check is valid.
            assert (
                conn.execute(
                    text(
                        "SELECT id FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": pkg_id},
                ).scalar()
                == pkg_id
            )
            assert (
                conn.execute(
                    text(
                        "SELECT id FROM assistant_main_agent_profile WHERE id = :id"
                    ),
                    {"id": profile_id},
                ).scalar()
                == profile_id
            )

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        assert _current_revision(engine) == PLAN08_HEAD
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
        assert heads == [PLAN08_HEAD]
        with engine.connect() as conn:
            assert CATALOG_CHECK not in _check_names(conn, "assistant_skill_package")
            assert RUNTIME_CHECK not in _check_names(
                conn, "assistant_main_agent_profile"
            )
            assert (
                conn.execute(
                    text(
                        "SELECT catalog_enabled, migration_state "
                        "FROM assistant_skill_package WHERE id = :id"
                    ),
                    {"id": pkg_id},
                ).fetchone()
                == (False, "shadow")
            )
