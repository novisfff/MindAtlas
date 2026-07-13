"""PostgreSQL migration gate for Plan 03 model capability probes (Task 8).

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


PLAN01_HEAD = "acf208493c87"
PLAN03_PROBE_REVISION = "b666b11a5faa"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN03_DOWNGRADE_BLOCKED_PROBE_DATA"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL probe migration gate skipped "
        "(local SQLite cannot enforce Plan 03 triggers)"
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


def _reset_to_plan01_parent() -> None:
    """Bring disposable DB to Plan 01 head (parent of probe migration)."""
    _configure_database_env(_POSTGRES_URL)
    # Clear probe rows if present so downgrade can succeed, then stamp/upgrade.
    try:
        with create_engine(_as_sqlalchemy_url(_POSTGRES_URL), future=True).connect() as conn:
            conn.execute(text("DROP TABLE IF EXISTS ai_model_capability_probe CASCADE"))
            # pointer column may remain if mid-state; ignore
            conn.commit()
    except Exception:
        pass
    try:
        _run_alembic("stamp", PLAN01_HEAD)
    except Exception:
        _run_alembic("upgrade", PLAN01_HEAD)
    # Ensure parent schema exists.
    _run_alembic("upgrade", PLAN01_HEAD)


def _insert_credential_model(conn, *, name: str, model_name: str) -> tuple[uuid.UUID, uuid.UUID]:
    cred_id = uuid.uuid4()
    model_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO ai_credential (
                id, name, base_url, api_key_encrypted, api_key_hint,
                runtime_revision, created_at, updated_at
            ) VALUES (
                :id, :name, :base_url, :enc, :hint,
                1, now(), now()
            )
            """
        ),
        {
            "id": cred_id,
            "name": name,
            "base_url": "https://api.example.com/v1",
            "enc": "enc",
            "hint": "****",
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO ai_model (
                id, credential_id, name, model_type, runtime_revision,
                created_at, updated_at
            ) VALUES (
                :id, :cred, :name, 'llm', 1, now(), now()
            )
            """
        ),
        {"id": model_id, "cred": cred_id, "name": model_name},
    )
    return cred_id, model_id


def _insert_probe(conn, *, model_id: uuid.UUID, digest: str = _DIGEST_A) -> uuid.UUID:
    probe_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO ai_model_capability_probe (
                id, model_id, probe_contract_version, adapter_key, adapter_revision,
                model_config_digest, status, capabilities, probe_digest,
                safe_error_code, safe_error_summary, created_at
            ) VALUES (
                :id, :model_id, 1, 'openai_chat_completions', '1',
                :config_digest, 'passed', CAST(:caps AS jsonb), :probe_digest,
                NULL, NULL, now()
            )
            """
        ),
        {
            "id": probe_id,
            "model_id": model_id,
            "config_digest": digest,
            "caps": '{"streaming":{"observation":"passed"}}',
            "probe_digest": _DIGEST_B,
        },
    )
    return probe_id


def test_upgrade_preserves_plan01_revisions_and_null_pointer() -> None:
    _reset_to_plan01_parent()
    with _engine() as engine:
        with engine.begin() as conn:
            _, model_id = _insert_credential_model(conn, name="p3-c1", model_name="m1")
            rev = conn.execute(
                text("SELECT runtime_revision FROM ai_model WHERE id = :id"),
                {"id": model_id},
            ).scalar()
            assert int(rev) == 1

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        assert _current_revision(engine) == PLAN03_PROBE_REVISION
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT runtime_revision, current_capability_probe_id "
                    "FROM ai_model WHERE id = :id"
                ),
                {"id": model_id},
            ).mappings().one()
            assert int(row["runtime_revision"]) == 1
            assert row["current_capability_probe_id"] is None
            # Plan 01 columns still present; not recreated as nullable.
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'ai_model'"
                    )
                )
            }
            assert "runtime_revision" in cols
            assert "current_capability_probe_id" in cols


def test_pointer_ownership_and_immutability_and_checks() -> None:
    _reset_to_plan01_parent()
    _run_alembic("upgrade", "head")
    with _engine() as engine:
        with engine.begin() as conn:
            _, model_a = _insert_credential_model(conn, name="p3-a", model_name="ma")
            _, model_b = _insert_credential_model(conn, name="p3-b", model_name="mb")
            probe_a = _insert_probe(conn, model_id=model_a)
            probe_b = _insert_probe(conn, model_id=model_b, digest=_DIGEST_C)

            # Valid A pointer succeeds.
            conn.execute(
                text(
                    "UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"
                ),
                {"p": probe_a, "m": model_a},
            )

            # A pointer to B probe fails.
            with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
                conn.execute(
                    text(
                        "UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"
                    ),
                    {"p": probe_b, "m": model_a},
                )
            assert "MINDATLAS_PLAN03_PROBE" in _err_text(exc_info.value)
            conn.rollback()

        with engine.begin() as conn:
            # re-seed after rollback of outer transaction state
            conn.execute(text("DELETE FROM ai_model_capability_probe"))
            conn.execute(text("DELETE FROM ai_model"))
            conn.execute(text("DELETE FROM ai_credential"))
            _, model_a = _insert_credential_model(conn, name="p3-a2", model_name="ma2")
            _, model_b = _insert_credential_model(conn, name="p3-b2", model_name="mb2")
            probe_a = _insert_probe(conn, model_id=model_a)
            probe_b = _insert_probe(conn, model_id=model_b, digest=_DIGEST_C)
            conn.execute(
                text(
                    "UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"
                ),
                {"p": probe_a, "m": model_a},
            )

            # Direct probe UPDATE fails.
            with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
                conn.execute(
                    text(
                        "UPDATE ai_model_capability_probe SET status = 'failed' WHERE id = :id"
                    ),
                    {"id": probe_a},
                )
            assert "immutable" in _err_text(exc_info.value).lower() or "MINDATLAS_PLAN03_PROBE" in _err_text(
                exc_info.value
            )
            conn.rollback()

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ai_model_capability_probe"))
            conn.execute(text("DELETE FROM ai_model"))
            conn.execute(text("DELETE FROM ai_credential"))
            _, model_a = _insert_credential_model(conn, name="p3-a3", model_name="ma3")

            # Invalid status
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO ai_model_capability_probe (
                            id, model_id, probe_contract_version, adapter_key, adapter_revision,
                            model_config_digest, status, capabilities, probe_digest, created_at
                        ) VALUES (
                            :id, :model_id, 1, 'openai_chat_completions', '1',
                            :d, 'weird', CAST('{"x":1}' AS jsonb), :d, now()
                        )
                        """
                    ),
                    {"id": uuid.uuid4(), "model_id": model_a, "d": _DIGEST_A},
                )
            conn.rollback()

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ai_model_capability_probe"))
            conn.execute(text("DELETE FROM ai_model"))
            conn.execute(text("DELETE FROM ai_credential"))
            _, model_a = _insert_credential_model(conn, name="p3-a4", model_name="ma4")

            # Invalid digest (not 64 hex)
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO ai_model_capability_probe (
                            id, model_id, probe_contract_version, adapter_key, adapter_revision,
                            model_config_digest, status, capabilities, probe_digest, created_at
                        ) VALUES (
                            :id, :model_id, 1, 'openai_chat_completions', '1',
                            'not-a-digest', 'passed', CAST('{"x":1}' AS jsonb), :d, now()
                        )
                        """
                    ),
                    {"id": uuid.uuid4(), "model_id": model_a, "d": _DIGEST_A},
                )
            conn.rollback()

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ai_model_capability_probe"))
            conn.execute(text("DELETE FROM ai_model"))
            conn.execute(text("DELETE FROM ai_credential"))
            _, model_a = _insert_credential_model(conn, name="p3-a5", model_name="ma5")

            # Non-object JSON capabilities
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text(
                        """
                        INSERT INTO ai_model_capability_probe (
                            id, model_id, probe_contract_version, adapter_key, adapter_revision,
                            model_config_digest, status, capabilities, probe_digest, created_at
                        ) VALUES (
                            :id, :model_id, 1, 'openai_chat_completions', '1',
                            :d, 'passed', CAST('[1,2,3]' AS jsonb), :d, now()
                        )
                        """
                    ),
                    {"id": uuid.uuid4(), "model_id": model_a, "d": _DIGEST_A},
                )
            conn.rollback()

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ai_model_capability_probe"))
            conn.execute(text("DELETE FROM ai_model"))
            conn.execute(text("DELETE FROM ai_credential"))
            _, model_a = _insert_credential_model(conn, name="p3-a6", model_name="ma6")
            _, model_b = _insert_credential_model(conn, name="p3-b6", model_name="mb6")
            probe_a = _insert_probe(conn, model_id=model_a)
            probe_b = _insert_probe(conn, model_id=model_b, digest=_DIGEST_C)

            # Two models cannot point at each other's rows.
            conn.execute(
                text("UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"),
                {"p": probe_a, "m": model_a},
            )
            with pytest.raises((IntegrityError, DBAPIError)):
                conn.execute(
                    text("UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"),
                    {"p": probe_a, "m": model_b},
                )
            conn.rollback()


def test_delete_current_probe_sets_null_and_model_delete_cascades() -> None:
    _reset_to_plan01_parent()
    _run_alembic("upgrade", "head")
    with _engine() as engine:
        with engine.begin() as conn:
            _, model_id = _insert_credential_model(conn, name="p3-del", model_name="mdel")
            probe_id = _insert_probe(conn, model_id=model_id)
            conn.execute(
                text("UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"),
                {"p": probe_id, "m": model_id},
            )
            # Deleting current probe sets pointer null (ON DELETE SET NULL).
            conn.execute(
                text("DELETE FROM ai_model_capability_probe WHERE id = :id"),
                {"id": probe_id},
            )
            pointer = conn.execute(
                text("SELECT current_capability_probe_id FROM ai_model WHERE id = :id"),
                {"id": model_id},
            ).scalar()
            assert pointer is None

            # Re-insert history and delete model -> cascade history without circular FK failure.
            probe_id2 = _insert_probe(conn, model_id=model_id)
            conn.execute(
                text("UPDATE ai_model SET current_capability_probe_id = :p WHERE id = :m"),
                {"p": probe_id2, "m": model_id},
            )
            conn.execute(text("DELETE FROM ai_model WHERE id = :id"), {"id": model_id})
            remaining = conn.execute(
                text("SELECT COUNT(*) FROM ai_model_capability_probe WHERE model_id = :id"),
                {"id": model_id},
            ).scalar()
            assert int(remaining) == 0


def test_downgrade_with_probe_rows_refuses_then_upgrade_cycle() -> None:
    _reset_to_plan01_parent()
    _run_alembic("upgrade", "head")
    with _engine() as engine:
        with engine.begin() as conn:
            _, model_id = _insert_credential_model(conn, name="p3-down", model_name="mdown")
            _insert_probe(conn, model_id=model_id)
            # also seed a binding-like intact registry row
            binding_id = uuid.uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO ai_component_binding (
                        id, component, llm_model_id, embedding_model_id, created_at, updated_at
                    ) VALUES (
                        :id, 'assistant', :m, NULL, now(), now()
                    )
                    ON CONFLICT (component) DO UPDATE SET llm_model_id = EXCLUDED.llm_model_id
                    """
                ),
                {"id": binding_id, "m": model_id},
            )

    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PLAN01_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    # Export/remove probe rows, then downgrade + re-upgrade.
    with _engine() as engine:
        with engine.begin() as conn:
            conn.execute(text("UPDATE ai_model SET current_capability_probe_id = NULL"))
            conn.execute(text("DELETE FROM ai_model_capability_probe"))

    _run_alembic("downgrade", PLAN01_HEAD)
    with _engine() as engine:
        assert _current_revision(engine) == PLAN01_HEAD
        with engine.connect() as conn:
            # runtime revision preserved
            count = conn.execute(text("SELECT COUNT(*) FROM ai_model")).scalar()
            assert int(count) >= 1
            cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'ai_model'"
                    )
                )
            }
            assert "runtime_revision" in cols
            assert "current_capability_probe_id" not in cols

    _run_alembic("upgrade", "head")
    with _engine() as engine:
        assert _current_revision(engine) == PLAN03_PROBE_REVISION
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT runtime_revision, current_capability_probe_id FROM ai_model LIMIT 1"
                )
            ).mappings().one()
            assert int(row["runtime_revision"]) == 1
            assert row["current_capability_probe_id"] is None
            binding = conn.execute(
                text("SELECT component FROM ai_component_binding WHERE component = 'assistant'")
            ).scalar()
            assert binding == "assistant"
