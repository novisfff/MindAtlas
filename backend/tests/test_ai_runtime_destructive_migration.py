"""PostgreSQL gate for Plan 10 Deploy B2 destructive migration.

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

PARENT_REVISION = "6417df0243be"
B2_REVISION = "ca6f564ef4bd"
PREFLIGHT_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_PREFLIGHT_BLOCKED"
B2_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_B2_DOWNGRADE_ACK"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 10 B2 destructive migration "
        "PostgreSQL gate skipped"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


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


def _reset_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))


def _upgrade_to(rev: str) -> None:
    _run_alembic("upgrade", rev)


def _set_env(**kwargs: str | None) -> dict[str, str | None]:
    prev: dict[str, str | None] = {}
    for key, value in kwargs.items():
        prev[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return prev


def _restore_env(prev: dict[str, str | None]) -> None:
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _insert_conversation(conn, conv_id: str, title: str = "b2") -> None:
    """Insert a conversation with required NOT NULL columns for parent head."""
    cols = {
        r[0]
        for r in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'assistant_conversation' "
                "AND table_schema = current_schema()"
            )
        ).fetchall()
    }
    fields = ["id", "created_at", "updated_at"]
    values = ["CAST(:id AS uuid)", "NOW()", "NOW()"]
    params: dict[str, object] = {"id": conv_id}
    if "title" in cols:
        fields.append("title")
        values.append(":title")
        params["title"] = title
    if "is_archived" in cols:
        fields.append("is_archived")
        values.append("false")
    conn.execute(
        text(
            f"INSERT INTO assistant_conversation ({', '.join(fields)}) "
            f"VALUES ({', '.join(values)})"
        ),
        params,
    )


def _seed_native_l2(engine: Engine) -> None:
    """Insert one package + conversation + native L2 row at parent head."""
    conv_id = str(uuid.uuid4())
    pkg_id = str(uuid.uuid4())
    l2_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _insert_conversation(conn, conv_id, title="b2")

        # Package table columns vary; use minimal required set from model.
        conn.execute(
            text(
                "INSERT INTO assistant_skill_package ("
                "id, canonical_name, display_name, description, migration_state, "
                "catalog_enabled, is_system, aggregate_revision, created_at, updated_at"
                ") VALUES ("
                "CAST(:id AS uuid), :name, :dname, 'd', 'cutover', false, false, 0, NOW(), NOW()"
                ")"
            ),
            {"id": pkg_id, "name": f"pkg-{pkg_id[:8]}", "dname": "Pkg"},
        )

        # Detect whether skill_name still exists (parent head).
        has_skill_name = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'assistant_conversation_skill_l2_memory' "
                "AND column_name = 'skill_name' "
                "AND table_schema = current_schema()"
            )
        ).fetchone()
        if has_skill_name:
            conn.execute(
                text(
                    "INSERT INTO assistant_conversation_skill_l2_memory ("
                    "id, conversation_id, skill_name, facts, version, "
                    "skill_package_id, memory_namespace, created_at, updated_at"
                    ") VALUES ("
                    "CAST(:id AS uuid), CAST(:cid AS uuid), 'smart-capture', "
                    "CAST('[]' AS json), 1, CAST(:pid AS uuid), 'default', NOW(), NOW()"
                    ")"
                ),
                {"id": l2_id, "cid": conv_id, "pid": pkg_id},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO assistant_conversation_skill_l2_memory ("
                    "id, conversation_id, facts, version, "
                    "skill_package_id, memory_namespace, created_at, updated_at"
                    ") VALUES ("
                    "CAST(:id AS uuid), CAST(:cid AS uuid), "
                    "CAST('[]' AS json), 1, CAST(:pid AS uuid), 'default', NOW(), NOW()"
                    ")"
                ),
                {"id": l2_id, "cid": conv_id, "pid": pkg_id},
            )


def test_upgrade_blocked_without_maintenance_ack() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(
            **{
                B2_ACK_ENV: None,
                B2_TEST_OVERRIDE_ENV: None,
            }
        )
        try:
            _upgrade_to(PARENT_REVISION)
            assert _current_revision(engine) == PARENT_REVISION
            with pytest.raises(Exception) as excinfo:
                _upgrade_to(B2_REVISION)
            assert PREFLIGHT_BLOCKED_TOKEN in str(excinfo.value)
            assert _current_revision(engine) == PARENT_REVISION
        finally:
            _restore_env(prev)


def test_upgrade_blocked_on_invalid_l2_even_with_ack() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(
            **{
                B2_ACK_ENV: "1",
                B2_TEST_OVERRIDE_ENV: None,
            }
        )
        try:
            _upgrade_to(PARENT_REVISION)
            # Seed a legacy-null L2 row that blocks B2.
            with engine.begin() as conn:
                conv_id = str(uuid.uuid4())
                l2_id = str(uuid.uuid4())
                _insert_conversation(conn, conv_id, title="legacy")
                conn.execute(
                    text(
                        "INSERT INTO assistant_conversation_skill_l2_memory ("
                        "id, conversation_id, skill_name, facts, version, "
                        "skill_package_id, memory_namespace, created_at, updated_at"
                        ") VALUES ("
                        "CAST(:id AS uuid), CAST(:cid AS uuid), 'legacy-name', "
                        "CAST('[]' AS json), 1, NULL, NULL, NOW(), NOW()"
                        ")"
                    ),
                    {"id": l2_id, "cid": conv_id},
                )
            with pytest.raises(Exception) as excinfo:
                _upgrade_to(B2_REVISION)
            msg = str(excinfo.value)
            assert PREFLIGHT_BLOCKED_TOKEN in msg
            assert "invalid_l2_rows" in msg
            assert _current_revision(engine) == PARENT_REVISION
        finally:
            _restore_env(prev)


def test_upgrade_drops_skill_name_and_human_approval() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(
            **{
                B2_ACK_ENV: "1",
                B2_TEST_OVERRIDE_ENV: None,
                DOWNGRADE_ACK_ENV: None,
            }
        )
        try:
            _upgrade_to(PARENT_REVISION)
            _seed_native_l2(engine)
            _upgrade_to(B2_REVISION)
            assert _current_revision(engine) == B2_REVISION

            with engine.connect() as conn:
                skill_name = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'assistant_conversation_skill_l2_memory' "
                        "AND column_name = 'skill_name' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
                assert skill_name is None

                nullable_pkg = conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'assistant_conversation_skill_l2_memory' "
                        "AND column_name = 'skill_package_id' "
                        "AND table_schema = current_schema()"
                    )
                ).scalar()
                assert nullable_pkg == "NO"

                nullable_ns = conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'assistant_conversation_skill_l2_memory' "
                        "AND column_name = 'memory_namespace' "
                        "AND table_schema = current_schema()"
                    )
                ).scalar()
                assert nullable_ns == "NO"

                approval = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'assistant_human_approval' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
                assert approval is None

                # assistant_skill still present after ca6f; dropped by 5cc5a70095f9.
                skill_table = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'assistant_skill' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
                assert skill_table is not None

                legacy_idx = conn.execute(
                    text(
                        "SELECT 1 FROM pg_indexes "
                        "WHERE indexname = 'uq_assistant_l2_memory_legacy_conversation_skill' "
                        "AND schemaname = current_schema()"
                    )
                ).fetchone()
                assert legacy_idx is None

                native_idx = conn.execute(
                    text(
                        "SELECT 1 FROM pg_indexes "
                        "WHERE indexname = 'uq_assistant_l2_memory_native_package_namespace' "
                        "AND schemaname = current_schema()"
                    )
                ).fetchone()
                assert native_idx is not None
        finally:
            _restore_env(prev)


def test_downgrade_requires_ack_and_does_not_claim_data_restore() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(
            **{
                B2_ACK_ENV: "1",
                DOWNGRADE_ACK_ENV: None,
            }
        )
        try:
            _upgrade_to(B2_REVISION)
            with pytest.raises(Exception) as excinfo:
                _run_alembic("downgrade", PARENT_REVISION)
            assert "MINDATLAS_PLAN10_B2_DOWNGRADE_BLOCKED" in str(excinfo.value)

            os.environ[DOWNGRADE_ACK_ENV] = "1"
            _run_alembic("downgrade", PARENT_REVISION)
            assert _current_revision(engine) == PARENT_REVISION
            with engine.connect() as conn:
                # Structural skill_name recreated, but approval table empty skeleton.
                skill_name = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'assistant_conversation_skill_l2_memory' "
                        "AND column_name = 'skill_name' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
                assert skill_name is not None
        finally:
            _restore_env(prev)


SKILL_DROP_REVISION = "5cc5a70095f9"
SKILL_DROP_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_SKILL_DROP_BLOCKED"


def test_skill_drop_blocked_without_ack() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(
            **{
                B2_ACK_ENV: "1",  # allow ca6f
                B2_TEST_OVERRIDE_ENV: None,
            }
        )
        try:
            _upgrade_to(B2_REVISION)  # through ca6f with ack
            # clear ack for skill drop
            os.environ.pop(B2_ACK_ENV, None)
            os.environ.pop(B2_TEST_OVERRIDE_ENV, None)
            with pytest.raises(Exception) as excinfo:
                _upgrade_to(SKILL_DROP_REVISION)
            assert SKILL_DROP_BLOCKED_TOKEN in str(excinfo.value)
            assert _current_revision(engine) == B2_REVISION
        finally:
            _restore_env(prev)


def test_skill_drop_removes_assistant_skill_table() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(**{B2_ACK_ENV: "1", B2_TEST_OVERRIDE_ENV: None})
        try:
            _upgrade_to(SKILL_DROP_REVISION)
            assert _current_revision(engine) == SKILL_DROP_REVISION
            with engine.connect() as conn:
                skill_table = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'assistant_skill' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
                assert skill_table is None
                # provenance columns remain without FK
                for table in ("assistant_skill_package", "assistant_main_agent_profile"):
                    col = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = :t AND column_name = 'legacy_skill_id' "
                            "AND table_schema = current_schema()"
                        ),
                        {"t": table},
                    ).fetchone()
                    assert col is not None
        finally:
            _restore_env(prev)


LEGACY_ID_DROP_REVISION = "d3a9fcac15c7"
LEGACY_DIGEST_DROP_REVISION = "3bd7bc4257c9"


def test_legacy_skill_id_columns_dropped() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(**{B2_ACK_ENV: "1", B2_TEST_OVERRIDE_ENV: None})
        try:
            _upgrade_to(LEGACY_ID_DROP_REVISION)
            assert _current_revision(engine) == LEGACY_ID_DROP_REVISION
            with engine.connect() as conn:
                for table in ("assistant_skill_package", "assistant_main_agent_profile"):
                    col = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name = :t AND column_name = 'legacy_skill_id' "
                            "AND table_schema = current_schema()"
                        ),
                        {"t": table},
                    ).fetchone()
                    assert col is None, table
                assert not conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'assistant_skill' "
                        "AND table_schema = current_schema()"
                    )
                ).fetchone()
        finally:
            _restore_env(prev)


def test_legacy_source_digest_columns_dropped() -> None:
    with _engine() as engine:
        _reset_schema(engine)
        prev = _set_env(**{B2_ACK_ENV: "1", B2_TEST_OVERRIDE_ENV: None})
        try:
            _upgrade_to(LEGACY_DIGEST_DROP_REVISION)
            assert _current_revision(engine) == LEGACY_DIGEST_DROP_REVISION
            with engine.connect() as conn:
                for table in ("assistant_skill_package", "assistant_main_agent_profile"):
                    for column in ("legacy_skill_id", "legacy_source_digest"):
                        col = conn.execute(
                            text(
                                "SELECT 1 FROM information_schema.columns "
                                "WHERE table_name = :t AND column_name = :c "
                                "AND table_schema = current_schema()"
                            ),
                            {"t": table, "c": column},
                        ).fetchone()
                        assert col is None, f"{table}.{column}"
                    # SHA-256 check constraints must be gone too.
                    ck = conn.execute(
                        text(
                            "SELECT 1 FROM information_schema.table_constraints "
                            "WHERE table_schema = current_schema() "
                            "AND table_name = :t "
                            "AND constraint_name = :n"
                        ),
                        {
                            "t": table,
                            "n": f"ck_{table}_legacy_source_digest",
                        },
                    ).fetchone()
                    assert ck is None, f"ck_{table}_legacy_source_digest"
        finally:
            _restore_env(prev)
