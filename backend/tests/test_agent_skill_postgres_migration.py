"""PostgreSQL migration gate for Plan 01 agent-skill contracts (Task 10).

Local unit runs skip unless ``MINDATLAS_TEST_POSTGRES_URL`` is set. CI provides
a disposable PostgreSQL 15 database and exercises upgrade/downgrade guards that
SQLite ``create_all`` cannot prove.
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


PRE_PLAN01_HEAD = "a7b8c9d0e1f2"
PLAN01_REVISION = "acf208493c87"
PLAN09_HEAD = "027869a00a47"
# This historical suite stops at Plan 09 because Plan 10 intentionally removes
# the legacy runtime tables whose Plan 01 preservation semantics it verifies.
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN01_DOWNGRADE_BLOCKED_NATIVE_DATA"


def _assert_at_or_after_plan01(rev: str | None) -> None:
    assert rev is not None and rev != PRE_PLAN01_HEAD, (
        f"expected alembic head at/after Plan 01 ({PLAN01_REVISION}), got {rev}"
    )

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; PostgreSQL migration gate skipped "
        "(local SQLite cannot enforce Plan 01 triggers)"
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64
_DIGEST_E = "e" * 64


def _resolved_binding_snapshot(
    *,
    input_digest: str,
    output_digest: str,
    resolution_digest: str | None = None,
    dependency_closure_digest: str | None = None,
    binding_contract_digest: str | None = None,
    dependency_closure: list | None = None,
    omit_digest_keys: bool = False,
) -> str:
    """Build a JSON snapshot that satisfies the row-level schema-pair CHECK.

    The deferred closure guard still validates digest key presence/equality.
    """
    import json

    snap: dict = {
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
        "inputSchemaDigest": input_digest,
        "outputSchemaDigest": output_digest,
        "dependencyClosure": dependency_closure or [],
    }
    if not omit_digest_keys:
        snap["resolutionDigest"] = resolution_digest or input_digest
        snap["dependencyClosureDigest"] = dependency_closure_digest or output_digest
        snap["bindingContractDigest"] = binding_contract_digest or output_digest
        # nested path also accepted by trigger
        snap["target"] = {"resolutionDigest": snap["resolutionDigest"]}
    return json.dumps(snap, separators=(",", ":"))




def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    # Ensure settings re-read the disposable URL for alembic env.py.
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


def _reset_to_head() -> None:
    """Create the Plan 09 historical schema from a disposable empty database."""
    _configure_database_env(_POSTGRES_URL)
    with _engine() as engine:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    # This historical suite stops before Plan 10 removes the legacy tables.
    _run_alembic("upgrade", PLAN09_HEAD)


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


def _disable_immutable_triggers(conn, *tables: str):
    """Temporarily disable USER triggers so disposable cleanup can rewrite immutable rows."""
    for table in tables:
        conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))


def _enable_immutable_triggers(conn, *tables: str):
    for table in tables:
        conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))


def _rewrite_version_origins_to_legacy(conn, package_id: uuid.UUID | None = None) -> None:
    """Rewrite skill version origins past immutability (CI/disposable superuser only)."""
    _disable_immutable_triggers(conn, "assistant_skill_version")
    try:
        if package_id is None:
            conn.execute(
                text(
                    "UPDATE assistant_skill_version SET origin = 'legacy' "
                    "WHERE origin IN ('api','import')"
                )
            )
        else:
            conn.execute(
                text(
                    "UPDATE assistant_skill_version SET origin = 'legacy' "
                    "WHERE skill_package_id = :id"
                ),
                {"id": package_id},
            )
    finally:
        _enable_immutable_triggers(conn, "assistant_skill_version")


def _insert_package_and_save_version(
    conn,
    *,
    canonical_name: str,
    origin: str = "legacy",
    migration_state: str = "shadow",
    sequence_no: int = 1,
) -> tuple[uuid.UUID, uuid.UUID]:
    package_id = uuid.uuid4()
    version_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_package (
                id, canonical_name, display_name, description,
                migration_state, catalog_enabled, is_system,
                created_at, updated_at
            ) VALUES (
                :id, :name, :display, :desc,
                :state, false, false,
                NOW(), NOW()
            )
            """
        ),
        {
            "id": package_id,
            "name": canonical_name,
            "display": canonical_name,
            "desc": "pg-gate",
            "state": migration_state,
        },
    )
    conn.execute(
        text(
            """
            INSERT INTO assistant_skill_version (
                id, skill_package_id, sequence_no, version_name, version_source,
                source_draft_version_id, origin, skill_md, mindatlas_yaml,
                frontmatter, extension_manifest, resource_index,
                skill_md_digest, manifest_digest, resource_index_digest,
                content_digest, binding_set_digest, version_digest, created_at
            ) VALUES (
                :id, :pkg, :seq, :vname, 'save',
                NULL, :origin, :skill_md, NULL,
                CAST(:frontmatter AS json), NULL, CAST(:resource_index AS json),
                :d1, :d2, :d3,
                :d4, NULL, NULL, NOW()
            )
            """
        ),
        {
            "id": version_id,
            "pkg": package_id,
            "seq": sequence_no,
            "vname": f"draft-{sequence_no}",
            "origin": origin,
            "skill_md": "---\nname: x\ndescription: y\n---\n",
            "frontmatter": '{"name":"x","description":"y"}',
            "resource_index": "[]",
            "d1": _DIGEST_A,
            "d2": _DIGEST_B,
            "d3": _DIGEST_C,
            # Unique draft content digest per sequence for same package.
            "d4": f"{sequence_no:064x}"[-64:],
        },
    )
    return package_id, version_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_ready() -> None:
    """Ensure the disposable database is at (or past) Plan 01 head once per module."""
    _reset_to_head()
    with _engine() as engine:
        rev = _current_revision(engine)
        assert rev is not None and rev != PRE_PLAN01_HEAD, (
            f"expected alembic head at/after Plan 01 ({PLAN01_REVISION}), got {rev}"
        )


@pytest.fixture()
def engine(pg_ready: None):
    with _engine() as eng:
        yield eng


# ---------------------------------------------------------------------------
# Upgrade path + revision column defaults
# ---------------------------------------------------------------------------


def test_upgrade_path_and_revision_defaults(engine: Engine) -> None:
    """Upgrade head and verify revision columns default to 1 on existing identities."""
    with engine.begin() as conn:
        tool_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO assistant_tool (
                    id, name, description, kind, is_system, enabled,
                    endpoint_url, http_method, timeout_seconds,
                    created_at, updated_at
                ) VALUES (
                    :id, :name, 'pg gate tool', 'remote', false, true,
                    'https://example.com/tool', 'POST', 30,
                    NOW(), NOW()
                )
                """
            ),
            {"id": tool_id, "name": f"pg-tool-{tool_id.hex[:8]}"},
        )
        rev = conn.execute(
            text("SELECT config_revision FROM assistant_tool WHERE id = :id"),
            {"id": tool_id},
        ).scalar()
        assert int(rev) == 1

        cred_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO ai_credential (
                    id, name, base_url, api_key_encrypted, api_key_hint,
                    created_at, updated_at
                ) VALUES (
                    :id, :name, 'https://api.example.com/v1', 'enc', '****',
                    NOW(), NOW()
                )
                """
            ),
            {"id": cred_id, "name": f"pg-cred-{cred_id.hex[:8]}"},
        )
        model_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO ai_model (
                    id, credential_id, name, model_type, created_at, updated_at
                ) VALUES (
                    :id, :cred, 'gpt-pg', 'llm', NOW(), NOW()
                )
                """
            ),
            {"id": model_id, "cred": cred_id},
        )
        model_rev = conn.execute(
            text("SELECT runtime_revision FROM ai_model WHERE id = :id"),
            {"id": model_id},
        ).scalar()
        cred_rev = conn.execute(
            text("SELECT runtime_revision FROM ai_credential WHERE id = :id"),
            {"id": cred_id},
        ).scalar()
        assert int(model_rev) == 1
        assert int(cred_rev) == 1

        # Cleanup identities created for this assertion (mutable tables).
        conn.execute(text("DELETE FROM ai_model WHERE id = :id"), {"id": model_id})
        conn.execute(text("DELETE FROM ai_credential WHERE id = :id"), {"id": cred_id})
        conn.execute(text("DELETE FROM assistant_tool WHERE id = :id"), {"id": tool_id})


def test_legacy_rows_survive_parent_to_head_upgrade() -> None:
    """Insert a tool at parent revision, upgrade to head, assert row + revision=1."""
    _configure_database_env(_POSTGRES_URL)

    # Drop v2 native blockers then downgrade to parent.
    with _engine() as engine:
        with engine.begin() as conn:
            # Clear any residual native blockers from other tests in this process.
            conn.execute(
                text(
                    "UPDATE assistant_skill_package SET migration_state = 'shadow' "
                    "WHERE migration_state IN ('native','cutover')"
                )
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile SET migration_state = 'shadow' "
                    "WHERE migration_state IN ('native','cutover')"
                )
            )
            # Immutable version tables need USER triggers disabled before origin rewrite.
            _disable_immutable_triggers(
                conn,
                "assistant_skill_version",
                "assistant_main_agent_profile_version",
            )
            try:
                conn.execute(
                    text(
                        "UPDATE assistant_skill_version SET origin = 'legacy' "
                        "WHERE origin IN ('api','import')"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE assistant_main_agent_profile_version SET origin = 'bootstrap' "
                        "WHERE origin NOT IN ('bootstrap','legacy')"
                    )
                )
            finally:
                _enable_immutable_triggers(
                    conn,
                    "assistant_skill_version",
                    "assistant_main_agent_profile_version",
                )

    _run_alembic("downgrade", PRE_PLAN01_HEAD)

    tool_id = uuid.uuid4()
    tool_name = f"legacy-preserve-{tool_id.hex[:8]}"
    with _engine() as engine:
        with engine.begin() as conn:
            # Parent schema: no config_revision column yet.
            cols = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'assistant_tool'"
                    )
                )
            }
            assert "config_revision" not in cols
            conn.execute(
                text(
                    """
                    INSERT INTO assistant_tool (
                        id, name, description, kind, is_system, enabled,
                        endpoint_url, http_method, timeout_seconds,
                        created_at, updated_at
                    ) VALUES (
                        :id, :name, 'preserve me', 'remote', false, true,
                        'https://example.com/legacy', 'GET', 15,
                        NOW(), NOW()
                    )
                    """
                ),
                {"id": tool_id, "name": tool_name},
            )

    _run_alembic("upgrade", PLAN09_HEAD)

    with _engine() as engine:
        _assert_at_or_after_plan01(_current_revision(engine))
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT name, description, kind, config_revision "
                    "FROM assistant_tool WHERE id = :id"
                ),
                {"id": tool_id},
            ).fetchone()
            assert row is not None
            assert row[0] == tool_name
            assert row[1] == "preserve me"
            assert row[2] == "remote"
            assert int(row[3]) == 1
            conn.execute(text("DELETE FROM assistant_tool WHERE id = :id"), {"id": tool_id})


# ---------------------------------------------------------------------------
# Immutability triggers
# ---------------------------------------------------------------------------


def test_immutability_triggers_reject_update_and_delete(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        package_id, version_id = _insert_package_and_save_version(
            conn, canonical_name=f"immut-{suffix}", origin="legacy"
        )
        alias_id = uuid.uuid4()
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
                "pkg": package_id,
                "alias": f"alias-{suffix}",
                "norm": f"alias-{suffix}",
            },
        )
        import hashlib

        blob_id = uuid.uuid4()
        content = b"hello-blob"
        real_sha = hashlib.sha256(content).hexdigest()
        # Blob + resource must land in the same transaction so the deferred
        # orphan-blob guard is satisfied at commit.
        conn.execute(
            text(
                """
                INSERT INTO assistant_skill_resource_blob (
                    id, sha256, byte_size, content, created_at
                ) VALUES (
                    :id, :sha, :size, :content, NOW()
                )
                """
            ),
            {
                "id": blob_id,
                "sha": real_sha,
                "size": len(content),
                "content": content,
            },
        )
        resource_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO assistant_skill_version_resource (
                    id, skill_version_id, path, resource_kind, media_type,
                    byte_size, sha256, blob_id, executable, created_at
                ) VALUES (
                    :id, :ver, 'references/a.md', 'references', 'text/markdown',
                    :size, :sha, :blob, false, NOW()
                )
                """
            ),
            {
                "id": resource_id,
                "ver": version_id,
                "size": len(content),
                "sha": real_sha,
                "blob": blob_id,
            },
        )

    immutable_targets = [
        ("assistant_skill_package_alias", alias_id, "alias"),
        ("assistant_skill_version", version_id, "version_name"),
        ("assistant_skill_resource_blob", blob_id, "byte_size"),
        ("assistant_skill_version_resource", resource_id, "media_type"),
    ]

    for table, row_id, col in immutable_targets:
        with engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
                if col == "byte_size":
                    conn.execute(
                        text(f"UPDATE {table} SET {col} = {col} + 1 WHERE id = :id"),
                        {"id": row_id},
                    )
                else:
                    conn.execute(
                        text(f"UPDATE {table} SET {col} = :val WHERE id = :id"),
                        {"id": row_id, "val": "mutated"},
                    )
            assert "MINDATLAS_PLAN01_IMMUTABLE_ROW" in _err_text(exc_info.value)

        with engine.begin() as conn:
            with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
                conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
            assert "MINDATLAS_PLAN01_IMMUTABLE_ROW" in _err_text(exc_info.value)


# ---------------------------------------------------------------------------
# Tool / model / credential revision guards
# ---------------------------------------------------------------------------


def test_revision_guards_tool_model_credential(engine: Engine) -> None:
    with engine.begin() as conn:
        tool_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO assistant_tool (
                    id, name, description, kind, is_system, enabled,
                    endpoint_url, http_method, timeout_seconds, config_revision,
                    created_at, updated_at
                ) VALUES (
                    :id, :name, 'rev tool', 'remote', false, true,
                    'https://example.com/a', 'POST', 30, 1,
                    NOW(), NOW()
                )
                """
            ),
            {"id": tool_id, "name": f"rev-tool-{tool_id.hex[:8]}"},
        )
        cred_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO ai_credential (
                    id, name, base_url, api_key_encrypted, api_key_hint,
                    runtime_revision, created_at, updated_at
                ) VALUES (
                    :id, :name, 'https://api.example.com/v1', 'enc-a', '****',
                    1, NOW(), NOW()
                )
                """
            ),
            {"id": cred_id, "name": f"rev-cred-{cred_id.hex[:8]}"},
        )
        model_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO ai_model (
                    id, credential_id, name, model_type, runtime_revision,
                    created_at, updated_at
                ) VALUES (
                    :id, :cred, 'gpt-rev', 'llm', 1, NOW(), NOW()
                )
                """
            ),
            {"id": model_id, "cred": cred_id},
        )

    # Execution change without +1 rejected.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text(
                    "UPDATE assistant_tool SET endpoint_url = 'https://example.com/b' "
                    "WHERE id = :id"
                ),
                {"id": tool_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)

    # Revision-only rejected.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text("UPDATE assistant_tool SET config_revision = 2 WHERE id = :id"),
                {"id": tool_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)

    # Exactly one valid increment accepted.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE assistant_tool SET endpoint_url = 'https://example.com/b', "
                "config_revision = 2 WHERE id = :id"
            ),
            {"id": tool_id},
        )
        rev = conn.execute(
            text("SELECT config_revision FROM assistant_tool WHERE id = :id"),
            {"id": tool_id},
        ).scalar()
        assert int(rev) == 2

    # Double revision increment (+2) rejected.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text(
                    "UPDATE assistant_tool SET endpoint_url = 'https://example.com/c', "
                    "config_revision = 4 WHERE id = :id"
                ),
                {"id": tool_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)

    # Model: skipped increment rejected; valid accepted.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text("UPDATE ai_model SET name = 'gpt-rev-2' WHERE id = :id"),
                {"id": model_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_model SET name = 'gpt-rev-2', runtime_revision = 2 WHERE id = :id"
            ),
            {"id": model_id},
        )

    # Credential: revision-only rejected; valid base_url change accepted.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text("UPDATE ai_credential SET runtime_revision = 2 WHERE id = :id"),
                {"id": cred_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ai_credential SET base_url = 'https://api.example.com/v2', "
                "runtime_revision = 2 WHERE id = :id"
            ),
            {"id": cred_id},
        )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ai_model WHERE id = :id"), {"id": model_id})
        conn.execute(text("DELETE FROM ai_credential WHERE id = :id"), {"id": cred_id})
        conn.execute(text("DELETE FROM assistant_tool WHERE id = :id"), {"id": tool_id})


# ---------------------------------------------------------------------------
# Ownership deferred guards
# ---------------------------------------------------------------------------


def test_ownership_deferred_guards_reject_cross_package_pointers(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        pkg_a, ver_a = _insert_package_and_save_version(
            conn, canonical_name=f"own-a-{suffix}", origin="legacy"
        )
        pkg_b, ver_b = _insert_package_and_save_version(
            conn, canonical_name=f"own-b-{suffix}", origin="legacy"
        )

    # Cross-package draft pointer rejected at commit (DEFERRABLE INITIALLY DEFERRED).
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET draft_version_id = :ver "
                "WHERE id = :pkg"
            ),
            {"pkg": pkg_a, "ver": ver_b},
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            trans.commit()
        assert "MINDATLAS_PLAN01_POINTER_OWNERSHIP" in _err_text(exc_info.value)

    # Same-package save pointer accepted.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET draft_version_id = :ver WHERE id = :pkg"
            ),
            {"pkg": pkg_a, "ver": ver_a},
        )

    # Wrong version_source for published pointer: create publish shape is hard without
    # source_draft; attempting published_version_id -> save row must fail POINTER_SOURCE.
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET published_version_id = :ver "
                "WHERE id = :pkg"
            ),
            {"pkg": pkg_a, "ver": ver_a},
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            trans.commit()
        msg = _err_text(exc_info.value)
        assert (
            "MINDATLAS_PLAN01_POINTER_SOURCE" in msg
            or "MINDATLAS_PLAN01_POINTER_OWNERSHIP" in msg
        )

    # Publish source_draft must belong to same package.
    with engine.connect() as conn:
        trans = conn.begin()
        publish_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO assistant_skill_version (
                    id, skill_package_id, sequence_no, version_name, version_source,
                    source_draft_version_id, origin, skill_md, mindatlas_yaml,
                    frontmatter, extension_manifest, resource_index,
                    skill_md_digest, manifest_digest, resource_index_digest,
                    content_digest, binding_set_digest, version_digest, created_at
                ) VALUES (
                    :id, :pkg, 2, 'pub-1', 'publish',
                    :src, 'legacy', '---\nname: x\ndescription: y\n---\n', NULL,
                    CAST(:frontmatter AS json), NULL, CAST(:resource_index AS json),
                    :d1, :d2, :d3, :d4, :d5, :d5, NOW()
                )
                """
            ),
            {
                "id": publish_id,
                "pkg": pkg_a,
                "src": ver_b,  # other package
                "frontmatter": '{"name":"x","description":"y"}',
                "resource_index": "[]",
                "d1": _DIGEST_A,
                "d2": _DIGEST_B,
                "d3": _DIGEST_C,
                "d4": _DIGEST_D,
                "d5": _DIGEST_E,
            },
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            trans.commit()
        assert "MINDATLAS_PLAN01_SOURCE_DRAFT" in _err_text(exc_info.value)


# ---------------------------------------------------------------------------
# Binding closure deferred guard
# ---------------------------------------------------------------------------


def test_binding_closure_deferred_guard_rejects_missing_digest_keys(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        _pkg, version_id = _insert_package_and_save_version(
            conn, canonical_name=f"closure-{suffix}", origin="legacy"
        )

    binding_id = uuid.uuid4()
    # Snapshot omits required digest keys (resolutionDigest / dependencyClosureDigest /
    # bindingContractDigest). Guard is DEFERRABLE INITIALLY DEFERRED → assert on commit.
    bad_snapshot = _resolved_binding_snapshot(
        input_digest=_DIGEST_A,
        output_digest=_DIGEST_B,
        omit_digest_keys=True,
    )
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text(
                """
                INSERT INTO assistant_skill_capability_binding (
                    id, skill_version_id, ordinal, capability_type, capability_key,
                    resolution_status, target_identity,
                    resolved_tool_id, resolved_workflow_version_id, resolved_agent_version_id,
                    resolved_revision, input_schema_digest, output_schema_digest,
                    config_digest, executable_revision, resolution_digest,
                    dependency_closure_digest, binding_contract_digest,
                    resolution_snapshot, created_at
                ) VALUES (
                    :id, :ver, 0, 'tool', 'search_entries',
                    'resolved', 'system-tool:search_entries',
                    NULL, NULL, NULL,
                    NULL, :d1, :d2,
                    :d3, 'build-1', :d4,
                    :d5, :d5,
                    CAST(:snap AS json), NOW()
                )
                """
            ),
            {
                "id": binding_id,
                "ver": version_id,
                "d1": _DIGEST_A,
                "d2": _DIGEST_B,
                "d3": _DIGEST_C,
                "d4": _DIGEST_D,
                "d5": _DIGEST_E,
                "snap": bad_snapshot,
            },
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            trans.commit()
        assert "MINDATLAS_PLAN01_CLOSURE" in _err_text(exc_info.value)


def test_binding_closure_rejects_dependency_index_mismatch(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        _pkg, version_id = _insert_package_and_save_version(
            conn, canonical_name=f"closure2-{suffix}", origin="legacy"
        )

    binding_id = uuid.uuid4()
    # Complete digest keys but index claims a dependency that is not inserted.
    snap = _resolved_binding_snapshot(
        input_digest=_DIGEST_A,
        output_digest=_DIGEST_B,
        resolution_digest=_DIGEST_D,
        dependency_closure_digest=_DIGEST_E,
        binding_contract_digest=_DIGEST_E,
        dependency_closure=[
            {
                "ordinal": 0,
                "path": "tool:search_entries",
                "dependencyDigest": _DIGEST_C,
            }
        ],
    )
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text(
                """
                INSERT INTO assistant_skill_capability_binding (
                    id, skill_version_id, ordinal, capability_type, capability_key,
                    resolution_status, target_identity,
                    resolved_tool_id, resolved_workflow_version_id, resolved_agent_version_id,
                    resolved_revision, input_schema_digest, output_schema_digest,
                    config_digest, executable_revision, resolution_digest,
                    dependency_closure_digest, binding_contract_digest,
                    resolution_snapshot, created_at
                ) VALUES (
                    :id, :ver, 0, 'tool', 'search_entries',
                    'resolved', 'system-tool:search_entries',
                    NULL, NULL, NULL,
                    NULL, :d1, :d2,
                    :d3, 'build-1', :d4,
                    :d5, :d5,
                    CAST(:snap AS json), NOW()
                )
                """
            ),
            {
                "id": binding_id,
                "ver": version_id,
                "d1": _DIGEST_A,
                "d2": _DIGEST_B,
                "d3": _DIGEST_C,
                "d4": _DIGEST_D,
                "d5": _DIGEST_E,
                "snap": snap,
            },
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            trans.commit()
        assert "MINDATLAS_PLAN01_CLOSURE" in _err_text(exc_info.value)


# ---------------------------------------------------------------------------
# Downgrade preflight
# ---------------------------------------------------------------------------


def test_downgrade_preflight_blocks_native_data(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        package_id, _version_id = _insert_package_and_save_version(
            conn,
            canonical_name=f"native-block-{suffix}",
            origin="api",
            migration_state="native",
        )

    _configure_database_env(_POSTGRES_URL)
    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PRE_PLAN01_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)

    # Ensure blocked downgrade did not leave pre-Plan01 (no destructive DDL ran).
    _assert_at_or_after_plan01(_current_revision(engine))

    # Soft-remove native blockers so other tests / CI downgrade step can proceed.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET migration_state = 'shadow' "
                "WHERE id = :id"
            ),
            {"id": package_id},
        )
        # assistant_skill_version is immutable — disable USER triggers before origin rewrite.
        _rewrite_version_origins_to_legacy(conn, package_id)


def test_downgrade_preflight_blocks_each_predicate_separately(engine: Engine) -> None:
    """Exercise package origin / profile native / profile origin predicates."""
    _configure_database_env(_POSTGRES_URL)

    # 1) package origin api/import with shadow migration_state
    with engine.begin() as conn:
        pkg_id, _ = _insert_package_and_save_version(
            conn,
            canonical_name=f"origin-api-{uuid.uuid4().hex[:8]}",
            origin="import",
            migration_state="shadow",
        )
    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PRE_PLAN01_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)
    with engine.begin() as conn:
        _rewrite_version_origins_to_legacy(conn, pkg_id)

    # 2) main agent profile native state
    profile_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile (
                    id, profile_key, display_name, is_default,
                    migration_state, runtime_enabled, created_at, updated_at
                ) VALUES (
                    :id, :key, :display, false,
                    'native', false, NOW(), NOW()
                )
                """
            ),
            {
                "id": profile_id,
                "key": f"profile-native-{profile_id.hex[:8]}",
                "display": "pg-native",
            },
        )
    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PRE_PLAN01_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE assistant_main_agent_profile SET migration_state = 'shadow' "
                "WHERE id = :id"
            ),
            {"id": profile_id},
        )

    # 3) profile version non-derived origin
    version_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assistant_main_agent_profile_version (
                    id, profile_id, sequence_no, version_name, version_source,
                    source_draft_version_id, origin, snapshot, content_digest,
                    source_ref, created_at
                ) VALUES (
                    :id, :profile, 1, 'draft-1', 'save',
                    NULL, 'api', CAST(:snap AS json), :d1,
                    NULL, NOW()
                )
                """
            ),
            {
                "id": version_id,
                "profile": profile_id,
                "snap": "{}",
                "d1": _DIGEST_A,
            },
        )
    with pytest.raises(Exception) as exc_info:
        _run_alembic("downgrade", PRE_PLAN01_HEAD)
    assert DOWNGRADE_BLOCKED_TOKEN in _err_text(exc_info.value)
    with engine.begin() as conn:
        # Neutralize immutable origin via temporary trigger disable (CI superuser).
        _disable_immutable_triggers(conn, "assistant_main_agent_profile_version")
        try:
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile_version "
                    "SET origin = 'bootstrap' WHERE id = :id"
                ),
                {"id": version_id},
            )
        finally:
            _enable_immutable_triggers(conn, "assistant_main_agent_profile_version")


def test_downgrade_succeeds_for_derived_shadow_only(engine: Engine) -> None:
    """With only legacy/bootstrap derived data, downgrade to parent then re-upgrade."""
    _configure_database_env(_POSTGRES_URL)
    with engine.begin() as conn:
        # Neutralize any residual native/api blockers without deleting immutables.
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET migration_state = 'shadow' "
                "WHERE migration_state IN ('native','cutover')"
            )
        )
        conn.execute(
            text(
                "UPDATE assistant_main_agent_profile SET migration_state = 'shadow' "
                "WHERE migration_state IN ('native','cutover')"
            )
        )
        # Disable immutability triggers briefly to rewrite origins for disposable CI DB.
        _disable_immutable_triggers(
            conn,
            "assistant_skill_version",
            "assistant_main_agent_profile_version",
        )
        try:
            conn.execute(
                text(
                    "UPDATE assistant_skill_version SET origin = 'legacy' "
                    "WHERE origin IN ('api','import')"
                )
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile_version SET origin = 'bootstrap' "
                    "WHERE origin NOT IN ('bootstrap','legacy')"
                )
            )
        finally:
            _enable_immutable_triggers(
                conn,
                "assistant_skill_version",
                "assistant_main_agent_profile_version",
            )

    _run_alembic("downgrade", PRE_PLAN01_HEAD)
    with _engine() as eng:
        assert _current_revision(eng) == PRE_PLAN01_HEAD
    _run_alembic("upgrade", PLAN09_HEAD)
    with _engine() as eng:
        _assert_at_or_after_plan01(_current_revision(eng))


# ---------------------------------------------------------------------------
# Two-session draft sequence uniqueness
# ---------------------------------------------------------------------------


def test_two_session_sequence_conflict(engine: Engine) -> None:
    """Prove package/sequence uniqueness under concurrent writers.

    Inserts must race in real concurrent transactions. A single-threaded dual
    connection pattern deadlocks on PostgreSQL unique-index waits.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        package_id, _ = _insert_package_and_save_version(
            conn,
            canonical_name=f"seq-{suffix}",
            origin="legacy",
            sequence_no=1,
        )

    insert_sql = text(
        """
        INSERT INTO assistant_skill_version (
            id, skill_package_id, sequence_no, version_name, version_source,
            source_draft_version_id, origin, skill_md, mindatlas_yaml,
            frontmatter, extension_manifest, resource_index,
            skill_md_digest, manifest_digest, resource_index_digest,
            content_digest, binding_set_digest, version_digest, created_at
        ) VALUES (
            :id, :pkg, :seq, :vname, 'save',
            NULL, 'legacy', :skill_md, NULL,
            CAST(:frontmatter AS json), NULL, CAST(:resource_index AS json),
            :d1, :d2, :d3, :d4, NULL, NULL, NOW()
        )
        """
    )
    start = threading.Event()

    def worker(vname: str, content_digest: str) -> str:
        eng = create_engine(
            _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True
        )
        try:
            with eng.connect() as conn:
                # SET in autocommit-like fashion: commit the autobegin first.
                conn.execute(text("SET lock_timeout = '5s'"))
                conn.execute(text("SET statement_timeout = '10s'"))
                conn.commit()
                try:
                    if not start.wait(timeout=10):
                        raise TimeoutError("race start signal timed out")
                    with conn.begin():
                        conn.execute(
                            insert_sql,
                            {
                                "id": uuid.uuid4(),
                                "pkg": package_id,
                                "seq": 2,
                                "vname": vname,
                                "skill_md": "---\nname: x\ndescription: y\n---\n",
                                "frontmatter": '{"name":"x","description":"y"}',
                                "resource_index": "[]",
                                "d1": _DIGEST_A,
                                "d2": _DIGEST_B,
                                "d3": _DIGEST_C,
                                "d4": content_digest,
                            },
                        )
                    return "committed"
                except (IntegrityError, DBAPIError):
                    return "failed"
        finally:
            eng.dispose()


    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(worker, "draft-2a", "1" * 64),
            pool.submit(worker, "draft-2b", "2" * 64),
        ]
        # release both workers together
        start.set()
        results = [f.result(timeout=20) for f in as_completed(futs, timeout=25)]

    assert results.count("committed") == 1, results
    assert results.count("failed") == 1, results

    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM assistant_skill_version "
                "WHERE skill_package_id = :pkg AND sequence_no = 2"
            ),
            {"pkg": package_id},
        ).scalar_one()
    assert int(count) == 1
