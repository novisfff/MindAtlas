"""PostgreSQL migration gate for Plan 2 Main-Agent rollout schema (Task 2).

Requires ``MINDATLAS_TEST_POSTGRES_URL``. With ``MINDATLAS_REQUIRE_POSTGRES=1``
this suite hard-fails instead of skipping.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, DatabaseError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema

bootstrap_backend_imports()
reset_caches()

PLAN1_HEAD = "9f3c1a7e2b40"
PLAN2_HEAD = "b6e2d4f8a901"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_1 = "1" * 64
DIGEST_2 = "2" * 64
DIGEST_3 = "3" * 64
DIGEST_9 = "9" * 64

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_REQUIRE_POSTGRES = os.environ.get("MINDATLAS_REQUIRE_POSTGRES", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}

if not _POSTGRES_URL and _REQUIRE_POSTGRES:
    pytest.fail(
        "MINDATLAS_TEST_POSTGRES_URL not set while MINDATLAS_REQUIRE_POSTGRES=1; "
        "Plan 2 runtime migration PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 runtime migration PostgreSQL "
        "gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    # Plan 10 B2 destructive revisions require test override on empty DBs.
    os.environ.setdefault("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", "1")
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _alembic_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _POSTGRES_URL
    env["MINDATLAS_PLAN10_B2_TEST_OVERRIDE"] = "1"
    env.setdefault("APP_ENV", "test")
    return env


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


def _drop_public_schema(engine: Engine) -> None:
    reset_disposable_public_schema(engine)


def _current_heads(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        try:
            rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        except Exception:
            return set()
        return {str(r[0]) for r in rows}


def current_heads(conn_or_engine) -> set[str]:
    if isinstance(conn_or_engine, Engine):
        return _current_heads(conn_or_engine)
    rows = conn_or_engine.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    return {str(r[0]) for r in rows}


def run_alembic_upgrade(_target, revision: str) -> subprocess.CompletedProcess[str]:
    _configure_database_env(_POSTGRES_URL)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=str(_BACKEND_DIR),
        env=_alembic_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def run_alembic_upgrade_checked(_target, revision: str) -> None:
    result = run_alembic_upgrade(_target, revision)
    if result.returncode != 0:
        raise AssertionError(
            f"alembic upgrade {revision} failed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def check_constraint_contains(
    conn_or_engine, table: str, name: str, fragment: str
) -> bool:
    engine = conn_or_engine if isinstance(conn_or_engine, Engine) else conn_or_engine.engine

    def _query(conn: Connection) -> bool:
        row = conn.execute(
            text(
                """
                SELECT pg_get_constraintdef(c.oid) AS def
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                JOIN pg_namespace n ON t.relnamespace = n.oid
                WHERE n.nspname = 'public'
                  AND t.relname = :table
                  AND c.contype = 'c'
                  AND c.conname = :name
                """
            ),
            {"table": table, "name": name},
        ).first()
        if row is None:
            return False
        return fragment in str(row[0])

    if isinstance(conn_or_engine, Engine):
        with conn_or_engine.connect() as conn:
            return _query(conn)
    return _query(conn_or_engine)


def insert_minimal_chat_run(engine: Engine, *, runtime_kind: str) -> uuid.UUID:
    """Insert a Plan-1-shaped chat run (no Plan-2 closure columns)."""
    conv_id = uuid.uuid4()
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assistant_conversation
                    (id, title, is_archived, created_at, updated_at)
                VALUES
                    (:id, :title, false, NOW(), NOW())
                """
            ),
            {"id": conv_id, "title": f"plan2-preflight-{conv_id.hex[:8]}"},
        )
        cols = [
            "id",
            "conversation_id",
            "status",
            "runtime_kind",
            "last_event_seq",
            "checkpoint_seq",
            "state_revision",
            "lease_generation",
            "recovery_count",
            "memory_commit_status",
            "created_at",
            "updated_at",
        ]
        vals = [
            ":id",
            ":conversation_id",
            "'queued'",
            ":runtime_kind",
            "0",
            "0",
            "0",
            "0",
            "0",
            ":memory_commit_status",
            "NOW()",
            "NOW()",
        ]
        params: dict = {
            "id": run_id,
            "conversation_id": conv_id,
            "runtime_kind": runtime_kind,
            "memory_commit_status": (
                "pending" if runtime_kind == "main_agent" else "not_applicable"
            ),
        }
        if runtime_kind == "main_agent":
            cols.extend(
                ["runtime_contract_version", "required_app_build_revision", "capability_ledger_mode"]
            )
            vals.extend([":rcv", ":build", ":ledger"])
            params["rcv"] = 1
            params["build"] = "build-preflight"
            params["ledger"] = "legacy_read_only"
        conn.execute(
            text(
                f"INSERT INTO assistant_chat_run ({', '.join(cols)}) "
                f"VALUES ({', '.join(vals)})"
            ),
            params,
        )
    return run_id


def insert_complete_main_agent_run(engine: Engine) -> uuid.UUID:
    """Insert a Plan-2 complete Main-Agent run with all frozen closure fields."""
    conv_id = uuid.uuid4()
    run_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    profile_version_id = uuid.uuid4()
    cred_id = uuid.uuid4()
    model_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assistant_conversation
                    (id, title, is_archived, created_at, updated_at)
                VALUES (:id, :title, false, NOW(), NOW())
                """
            ),
            {"id": conv_id, "title": f"plan2-run-{conv_id.hex[:8]}"},
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_credential (
                    id, name, base_url, api_key_encrypted, api_key_hint,
                    runtime_revision, created_at, updated_at
                ) VALUES (
                    :id, :name, 'https://example.test/v1', 'enc-test', '****',
                    1, NOW(), NOW()
                )
                """
            ),
            {"id": cred_id, "name": f"cred-{cred_id.hex[:8]}"},
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_model (
                    id, credential_id, name, model_type, runtime_revision,
                    created_at, updated_at
                ) VALUES (
                    :id, :credential_id, 'gpt-test', 'llm', 1, NOW(), NOW()
                )
                """
            ),
            {"id": model_id, "credential_id": cred_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile (
                    id, profile_key, display_name, is_default, migration_state,
                    runtime_enabled, aggregate_revision, created_at, updated_at
                ) VALUES (
                    :id, 'default', 'Main Agent', true, 'native',
                    false, 0, NOW(), NOW()
                )
                """
            ),
            {"id": profile_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile_version (
                    id, profile_id, sequence_no, version_name, version_source,
                    origin, snapshot, content_digest, created_at
                ) VALUES (
                    :id, :profile_id, 1, 'v1', 'save', 'api',
                    CAST(:snapshot AS json), :digest, NOW()
                )
                """
            ),
            {
                "id": profile_version_id,
                "profile_id": profile_id,
                "snapshot": '{"schemaVersion": 2}',
                "digest": DIGEST_A,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_rollout_revision (
                    id, revision_label, profile_version_id, profile_content_digest,
                    model_id, model_identity_digest, package_closure_json,
                    package_closure_digest, capability_closure_digest,
                    seed_manifest_digest, build_revision, runtime_contract_version,
                    checkpoint_codec_version, capability_feature_digest,
                    revision_digest, prepared_reason, created_at
                ) VALUES (
                    :id, :label, :profile_version_id, :pcd,
                    :model_id, :mid, CAST(:pkg AS json),
                    :pcd2, :ccd, :smd, :build, 1, 3, :cfd,
                    :rd, 'test', NOW()
                )
                """
            ),
            {
                "id": revision_id,
                "label": f"main-agent-{revision_id.hex[:24]}",
                "profile_version_id": profile_version_id,
                "pcd": DIGEST_A,
                "model_id": model_id,
                "mid": DIGEST_B,
                "pkg": "[]",
                "pcd2": DIGEST_C,
                "ccd": DIGEST_D,
                "smd": DIGEST_E,
                "build": "build-test-1",
                "cfd": DIGEST_F,
                "rd": DIGEST_1,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO assistant_chat_run (
                    id, conversation_id, status, runtime_kind,
                    main_agent_rollout_revision_id, main_agent_profile_version_id,
                    resolved_model_id, runtime_closure_digest,
                    runtime_contract_version, required_checkpoint_codec_version,
                    required_capability_feature_digest, required_app_build_revision,
                    capability_ledger_mode, memory_commit_status,
                    last_event_seq, checkpoint_seq, state_revision,
                    lease_generation, recovery_count, created_at, updated_at
                ) VALUES (
                    :id, :conversation_id, 'queued', 'main_agent',
                    :rollout_id, :profile_version_id,
                    :model_id, :closure,
                    1, 3, :feature, 'build-test-1',
                    'enforced', 'pending',
                    0, 0, 0, 0, 0, NOW(), NOW()
                )
                """
            ),
            {
                "id": run_id,
                "conversation_id": conv_id,
                "rollout_id": revision_id,
                "profile_version_id": profile_version_id,
                "model_id": model_id,
                "closure": DIGEST_9,
                "feature": DIGEST_F,
            },
        )
    return run_id


@pytest.fixture
def postgres_at_plan1_head() -> Iterator[Engine]:
    with _engine() as engine:
        _drop_public_schema(engine)
        run_alembic_upgrade_checked(engine, PLAN1_HEAD)
        assert _current_heads(engine) == {PLAN1_HEAD}
        yield engine


@pytest.fixture
def postgres_at_plan2_head() -> Iterator[Engine]:
    with _engine() as engine:
        _drop_public_schema(engine)
        run_alembic_upgrade_checked(engine, PLAN2_HEAD)
        assert _current_heads(engine) == {PLAN2_HEAD}
        yield engine


def test_upgrade_rejects_legacy_run(postgres_at_plan1_head: Engine) -> None:
    insert_minimal_chat_run(postgres_at_plan1_head, runtime_kind="legacy")
    result = run_alembic_upgrade(postgres_at_plan1_head, PLAN2_HEAD)
    assert result.returncode != 0
    combined = f"{result.stdout}\n{result.stderr}"
    assert "legacy_upgrade_not_supported" in combined


def test_upgrade_rejects_nonempty_main_agent_run(postgres_at_plan1_head: Engine) -> None:
    insert_minimal_chat_run(postgres_at_plan1_head, runtime_kind="main_agent")
    result = run_alembic_upgrade(postgres_at_plan1_head, PLAN2_HEAD)
    assert result.returncode != 0
    combined = f"{result.stdout}\n{result.stderr}"
    assert "schema_incompatible" in combined


def test_fresh_upgrade_has_main_agent_only_shape(postgres_at_plan1_head: Engine) -> None:
    run_alembic_upgrade_checked(postgres_at_plan1_head, PLAN2_HEAD)
    assert current_heads(postgres_at_plan1_head) == {PLAN2_HEAD}
    assert check_constraint_contains(
        postgres_at_plan1_head,
        "assistant_chat_run",
        "ck_assistant_chat_run_main_agent_only",
        "main_agent",
    )
    with postgres_at_plan1_head.connect() as conn:
        for table in (
            "assistant_main_agent_rollout_revision",
            "assistant_runtime_bootstrap_gate_use",
            "assistant_main_agent_rollout_control",
            "assistant_main_agent_rollout_event",
        ):
            row = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            ).first()
            assert row is not None, f"missing table {table}"


def test_run_runtime_closure_columns_are_immutable(postgres_at_plan2_head: Engine) -> None:
    run_id = insert_complete_main_agent_run(postgres_at_plan2_head)
    with pytest.raises((DatabaseError, DBAPIError), match="runtime identity is immutable"):
        with postgres_at_plan2_head.begin() as conn:
            conn.execute(
                text(
                    "UPDATE assistant_chat_run "
                    "SET runtime_closure_digest = :digest WHERE id = :run_id"
                ),
                {"digest": "f" * 64, "run_id": run_id},
            )


def test_rollout_revision_is_immutable_on_postgres(postgres_at_plan2_head: Engine) -> None:
    run_id = insert_complete_main_agent_run(postgres_at_plan2_head)
    with postgres_at_plan2_head.connect() as conn:
        rev_id = conn.execute(
            text(
                "SELECT main_agent_rollout_revision_id FROM assistant_chat_run WHERE id=:id"
            ),
            {"id": run_id},
        ).scalar_one()
    with pytest.raises((DatabaseError, DBAPIError), match="rollout revision is immutable"):
        with postgres_at_plan2_head.begin() as conn:
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_rollout_revision "
                    "SET revision_digest = :d WHERE id = :id"
                ),
                {"d": DIGEST_2, "id": rev_id},
            )


def test_plan2_is_sole_alembic_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == [PLAN2_HEAD], f"expected sole head {PLAN2_HEAD}, got {heads}"
