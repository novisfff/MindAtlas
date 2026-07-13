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
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN01_DOWNGRADE_BLOCKED_NATIVE_DATA"

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
    """Bring disposable DB to Plan 01 head, discarding derived v2 rows if needed."""
    _configure_database_env(_POSTGRES_URL)
    # Prefer upgrade path; if already past/at head this is a no-op.
    try:
        _run_alembic("upgrade", "head")
    except Exception:
        # If head is blocked somehow, stamp parent then upgrade.
        _run_alembic("stamp", PRE_PLAN01_HEAD)
        _run_alembic("upgrade", "head")


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
    """Ensure the disposable database is at Plan 01 head once per module."""
    _reset_to_head()
    with _engine() as engine:
        rev = _current_revision(engine)
        assert rev == PLAN01_REVISION, f"expected head {PLAN01_REVISION}, got {rev}"


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
                    "UPDATE assistant_skill_version SET origin = 'legacy' "
                    "WHERE origin IN ('api','import')"
                )
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile SET migration_state = 'shadow' "
                    "WHERE migration_state IN ('native','cutover')"
                )
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile_version SET origin = 'bootstrap' "
                    "WHERE origin NOT IN ('bootstrap','legacy')"
                )
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

    _run_alembic("upgrade", "head")

    with _engine() as engine:
        assert _current_revision(engine) == PLAN01_REVISION
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

    # Model: skipped increment rejected; valid accepted.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text("UPDATE ai_model SET name = 'gpt-rev-2' WHERE id = :id"),
                {"id": model_id},
            )
        assert "MINDATLAS_PLAN01_REVISION" in _err_text(exc_info.value)
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

    # Cross-package draft pointer rejected at commit.
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text(
                    "UPDATE assistant_skill_package SET draft_version_id = :ver "
                    "WHERE id = :pkg"
                ),
                {"pkg": pkg_a, "ver": ver_b},
            )
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
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
            conn.execute(
                text(
                    "UPDATE assistant_skill_package SET published_version_id = :ver "
                    "WHERE id = :pkg"
                ),
                {"pkg": pkg_a, "ver": ver_a},
            )
        msg = _err_text(exc_info.value)
        assert (
            "MINDATLAS_PLAN01_POINTER_SOURCE" in msg
            or "MINDATLAS_PLAN01_POINTER_OWNERSHIP" in msg
        )

    # Publish source_draft must belong to same package.
    with engine.begin() as conn:
        publish_id = uuid.uuid4()
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
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
        # Snapshot omits required digest keys and has empty closure index while
        # we will also insert a dependency row → count mismatch / missing keys.
        bad_snapshot = (
            '{"inputSchemaDigest":"'
            + _DIGEST_A
            + '","outputSchemaDigest":"'
            + _DIGEST_B
            + '","dependencyClosure":[]}'
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
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
        assert "MINDATLAS_PLAN01_CLOSURE" in _err_text(exc_info.value)


def test_binding_closure_rejects_dependency_index_mismatch(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        _pkg, version_id = _insert_package_and_save_version(
            conn, canonical_name=f"closure2-{suffix}", origin="legacy"
        )
        binding_id = uuid.uuid4()
        # Complete digest keys but index claims a dependency that is not inserted.
        snap = (
            "{"
            f'"inputSchemaDigest":"{_DIGEST_A}",'
            f'"outputSchemaDigest":"{_DIGEST_B}",'
            f'"resolutionDigest":"{_DIGEST_D}",'
            f'"dependencyClosureDigest":"{_DIGEST_E}",'
            f'"bindingContractDigest":"{_DIGEST_E}",'
            '"dependencyClosure":['
            f'{{"ordinal":0,"path":"tool:search_entries","dependencyDigest":"{_DIGEST_C}"}}'
            "]"
            "}"
        )
        with pytest.raises((IntegrityError, DBAPIError)) as exc_info:
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
        msg = _err_text(exc_info.value)
        assert "MINDATLAS_PLAN01_CLOSURE" in msg


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

    # Ensure we are still on Plan 01 head (no destructive DDL ran).
    assert _current_revision(engine) == PLAN01_REVISION

    # Soft-remove native blockers so other tests / CI downgrade step can proceed.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE assistant_skill_package SET migration_state = 'shadow' "
                "WHERE id = :id"
            ),
            {"id": package_id},
        )
        conn.execute(
            text(
                "UPDATE assistant_skill_version SET origin = 'legacy' "
                "WHERE skill_package_id = :id"
            ),
            {"id": package_id},
        )


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
        conn.execute(
            text(
                "UPDATE assistant_skill_version SET origin = 'legacy' "
                "WHERE skill_package_id = :id"
            ),
            {"id": pkg_id},
        )

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
        conn.execute(
            text(
                "ALTER TABLE assistant_main_agent_profile_version "
                "DISABLE TRIGGER USER"
            )
        )
        try:
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile_version "
                    "SET origin = 'bootstrap' WHERE id = :id"
                ),
                {"id": version_id},
            )
        finally:
            conn.execute(
                text(
                    "ALTER TABLE assistant_main_agent_profile_version "
                    "ENABLE TRIGGER USER"
                )
            )


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
        # Disable immutability triggers briefly to rewrite origins for disposable CI DB.
        for table in (
            "assistant_skill_version",
            "assistant_main_agent_profile_version",
        ):
            conn.execute(
                text(f"ALTER TABLE {table} DISABLE TRIGGER USER")
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
                    "UPDATE assistant_main_agent_profile SET migration_state = 'shadow' "
                    "WHERE migration_state IN ('native','cutover')"
                )
            )
            conn.execute(
                text(
                    "UPDATE assistant_main_agent_profile_version SET origin = 'bootstrap' "
                    "WHERE origin NOT IN ('bootstrap','legacy')"
                )
            )
        finally:
            for table in (
                "assistant_skill_version",
                "assistant_main_agent_profile_version",
            ):
                conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))

    _run_alembic("downgrade", PRE_PLAN01_HEAD)
    with _engine() as eng:
        assert _current_revision(eng) == PRE_PLAN01_HEAD
    _run_alembic("upgrade", "head")
    with _engine() as eng:
        assert _current_revision(eng) == PLAN01_REVISION


# ---------------------------------------------------------------------------
# Two-session draft sequence uniqueness
# ---------------------------------------------------------------------------


def test_two_session_sequence_conflict(engine: Engine) -> None:
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        package_id, _ = _insert_package_and_save_version(
            conn,
            canonical_name=f"seq-{suffix}",
            origin="legacy",
            sequence_no=1,
        )

    engine2 = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True
    )
    try:
        c1 = engine.connect()
        c2 = engine2.connect()
        try:
            t1 = c1.begin()
            t2 = c2.begin()
            params_common = {
                "pkg": package_id,
                "seq": 2,
                "skill_md": "---\nname: x\ndescription: y\n---\n",
                "frontmatter": '{"name":"x","description":"y"}',
                "resource_index": "[]",
                "d1": _DIGEST_A,
                "d2": _DIGEST_B,
                "d3": _DIGEST_C,
            }
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
            c1.execute(
                insert_sql,
                {
                    **params_common,
                    "id": uuid.uuid4(),
                    "vname": "draft-2a",
                    "d4": "1" * 64,
                },
            )
            c2.execute(
                insert_sql,
                {
                    **params_common,
                    "id": uuid.uuid4(),
                    "vname": "draft-2b",
                    "d4": "2" * 64,
                },
            )
            t1.commit()
            with pytest.raises((IntegrityError, DBAPIError)):
                t2.commit()
        finally:
            c1.close()
            c2.close()
    finally:
        engine2.dispose()
