from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from app.schema.contracts import DeploymentClass
from app.schema.application_contract import (
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.catalog import PostgresCatalogReader
from app.schema.canonical import structural_fingerprint
from app.schema.identity import (
    load_expected_schema_contract,
    read_schema_identity,
)
from app.schema.rebaseline import (
    MAINTENANCE_ACKNOWLEDGEMENT,
    REBASELINE_ADVISORY_LOCK_KEY,
    RebaselineRefused,
    RebaselineRequest,
    SAFE_REPORT_FIELDS,
    drop_verified_legacy_objects,
    snapshot_retained_tables,
    apply_rebaseline,
    validate_rebaseline_source,
)
from app.schema.sql_objects import load_exclusion_manifest
from app.schema import rebaseline as rebaseline_module
from tests.postgres_destructive_guard import (
    assert_disposable_postgres_target,
)


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_SOURCE_URL = os.environ.get(
    "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL",
    "",
).strip()
_DATABASE_NAME = re.compile(r"[a-z0-9_]+")


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@contextmanager
def _cloned_old_head_database() -> Iterator[Engine]:
    assert_disposable_postgres_target(_POSTGRES_URL)
    assert_disposable_postgres_target(_SOURCE_URL)
    base = make_url(_POSTGRES_URL)
    source = make_url(_SOURCE_URL)
    if (
        (base.host, base.port, base.username)
        != (source.host, source.port, source.username)
        or _DATABASE_NAME.fullmatch(source.database or "") is None
    ):
        raise RuntimeError("rebaseline source database target mismatch")
    clone_name = f"mindatlas_test_plan08_rebaseline_{uuid.uuid4().hex[:10]}"
    clone_url = base.set(database=clone_name).render_as_string(
        hide_password=False
    )
    assert_disposable_postgres_target(clone_url)
    admin_url = base.set(database="postgres").render_as_string(
        hide_password=False
    )
    admin = create_engine(
        _sqlalchemy_url(admin_url),
        future=True,
        isolation_level="AUTOCOMMIT",
    )
    clone: Engine | None = None
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    f'CREATE DATABASE "{clone_name}" '
                    f'WITH TEMPLATE "{source.database}"'
                )
            )
        clone = create_engine(_sqlalchemy_url(clone_url), future=True)
        yield clone
    finally:
        active_error = sys.exc_info()[1]
        cleanup_error: Exception | None = None
        if clone is not None:
            clone.dispose()
        try:
            with admin.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :database_name "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": clone_name},
                )
                connection.execute(
                    text(f'DROP DATABASE IF EXISTS "{clone_name}"')
                )
        except Exception as exc:  # noqa: BLE001
            cleanup_error = exc
        finally:
            admin.dispose()
        if cleanup_error is not None and active_error is None:
            raise cleanup_error


@pytest.fixture()
def old_head_database() -> Iterator[Engine]:
    with _cloned_old_head_database() as engine:
        yield engine


def _set_database_comment(engine: Engine, comment: str | None) -> None:
    with engine.begin() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        assert _DATABASE_NAME.fullmatch(database_name) is not None
        if comment is None:
            connection.exec_driver_sql(
                f'COMMENT ON DATABASE "{database_name}" IS NULL'
            )
        else:
            connection.exec_driver_sql(
                f'COMMENT ON DATABASE "{database_name}" IS %s',
                (comment,),
            )


def _read_heads(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars()
        )


def _request(deployment_class: str) -> RebaselineRequest:
    return RebaselineRequest(
        deployment_class=DeploymentClass(deployment_class),
        acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
        build_revision="test-build",
    )


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
@pytest.mark.parametrize(
    ("env_class", "database_comment", "safe_code"),
    [
        (
            "production",
            "mindatlas:deployment_class=production",
            "production_rebaseline_forbidden",
        ),
        ("development", None, "database_deployment_identity_missing"),
        (
            "development",
            "mindatlas:deployment_class=shared",
            "database_deployment_identity_unknown",
        ),
        (
            "development",
            "mindatlas:deployment_class=rehearsal",
            "deployment_identity_mismatch",
        ),
    ],
)
def test_apply_rejects_non_local_identity(
    old_head_database: Engine,
    env_class: str,
    database_comment: str | None,
    safe_code: str,
) -> None:
    _set_database_comment(old_head_database, database_comment)

    with old_head_database.connect() as connection, pytest.raises(
        RebaselineRefused,
        match=f"^{safe_code}$",
    ):
        validate_rebaseline_source(connection, _request(env_class))

    assert _read_heads(old_head_database) == ("b6e2d4f8a901",)


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
@pytest.mark.parametrize("head_state", ["missing", "wrong", "multiple"])
def test_apply_rejects_missing_wrong_or_multiple_head(
    old_head_database: Engine,
    head_state: str,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    with old_head_database.begin() as connection:
        if head_state == "missing":
            connection.execute(text("DELETE FROM alembic_version"))
        elif head_state == "wrong":
            connection.execute(
                text("UPDATE alembic_version SET version_num = 'wrong_head'")
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('second_head')"
                )
            )

    before = _read_heads(old_head_database)
    with old_head_database.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ):
        validate_rebaseline_source(connection, _request("development"))

    assert _read_heads(old_head_database) == before


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_apply_rejects_advisory_lock_contention_without_mutation(
    old_head_database: Engine,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    before = _read_heads(old_head_database)

    with old_head_database.connect() as lock_holder:
        with lock_holder.begin():
            lock_holder.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": REBASELINE_ADVISORY_LOCK_KEY},
            )
            with old_head_database.connect() as contender, pytest.raises(
                RebaselineRefused,
                match="^rebaseline_lock_unavailable$",
            ):
                apply_rebaseline(contender, _request("development"))

    assert _read_heads(old_head_database) == before


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_exact_old_head_source_passes_definition_locked_preflight(
    old_head_database: Engine,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )

    with old_head_database.connect() as connection:
        validate_rebaseline_source(connection, _request("development"))


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_source_structural_drift_is_rejected_before_mutation(
    old_head_database: Engine,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    with old_head_database.begin() as connection:
        connection.execute(text("CREATE TABLE rebaseline_drift (id integer)"))
    before = _read_heads(old_head_database)

    with old_head_database.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_fingerprint_mismatch$",
    ):
        validate_rebaseline_source(connection, _request("development"))

    assert _read_heads(old_head_database) == before
    with old_head_database.connect() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('public.rebaseline_drift') IS NOT NULL")
        ) is True


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_non_inert_legacy_control_is_rejected_without_cleanup(
    old_head_database: Engine,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    with old_head_database.begin() as connection:
        connection.execute(
            text(
                "UPDATE assistant_runtime_rollout_control "
                "SET state_revision = 1 WHERE singleton_key = 'singleton'"
            )
        )

    with old_head_database.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^legacy_exclusion_data_present$",
    ):
        validate_rebaseline_source(connection, _request("development"))

    assert _read_heads(old_head_database) == ("b6e2d4f8a901",)
    with old_head_database.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT state_revision "
                "FROM assistant_runtime_rollout_control "
                "WHERE singleton_key = 'singleton'"
            )
        ) == 1


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_retained_data_snapshot_locks_and_hashes_exact_old_head(
    old_head_database: Engine,
) -> None:
    with old_head_database.begin() as connection:
        first = snapshot_retained_tables(connection, b"r" * 32)
        second = snapshot_retained_tables(connection, b"r" * 32)

    assert first == second
    assert first
    assert all(item.row_count >= 0 for item in first)
    assert all(len(item.keyed_digest) == 32 for item in first)


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_drop_verified_legacy_objects_leaves_clean_logical_application(
    old_head_database: Engine,
) -> None:
    manifest = load_exclusion_manifest()
    expected = load_logical_application_contract()

    with old_head_database.begin() as connection:
        drop_verified_legacy_objects(connection, manifest)
        document = PostgresCatalogReader(connection).read_document()
        projected = project_logical_application_document(
            document,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )

    assert expected.logical_application_fingerprint == structural_fingerprint(
        projected
    )


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
@pytest.mark.parametrize("deployment_class", ("development", "rehearsal"))
def test_exact_development_old_head_rebaselines_atomically(
    old_head_database: Engine,
    deployment_class: str,
) -> None:
    _set_database_comment(
        old_head_database,
        f"mindatlas:deployment_class={deployment_class}",
    )
    snapshot_key = b"s" * 32
    with old_head_database.begin() as connection:
        before = snapshot_retained_tables(connection, snapshot_key)

    with old_head_database.connect() as connection:
        report = apply_rebaseline(connection, _request(deployment_class))

    with old_head_database.begin() as connection:
        after = snapshot_retained_tables(connection, snapshot_key)
        marker = read_schema_identity(connection)
        current = PostgresCatalogReader(connection).read_document()
    expected = load_expected_schema_contract()
    projected = project_logical_application_document(
        current,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )

    assert report.result == "rebaselined"
    payload = report.to_payload()
    assert set(payload) == SAFE_REPORT_FIELDS
    assert payload["retainedDataUnchanged"] is True
    assert payload["removedLegacyBusinessRows"] == 0
    assert payload["removedKnownInertSeedRows"] == 1
    assert all(
        secret not in str(payload).lower()
        for secret in ("postgresql://", "password", "token", "select ")
    )
    assert report.before_revision == "b6e2d4f8a901"
    assert report.after_revision == "pre_ga_v1_0001"
    assert before == after
    assert _read_heads(old_head_database) == ("pre_ga_v1_0001",)
    assert marker.schema_family == "pre_ga_v1"
    assert marker.deployment_class is DeploymentClass(deployment_class)
    assert marker.structural_fingerprint == (
        expected.application_structural_fingerprint
    )
    assert structural_fingerprint(projected) == (
        expected.application_structural_fingerprint
    )
    assert not ({item.key for item in current.objects} & set(load_exclusion_manifest().object_keys))


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_second_apply_is_idempotent_and_changes_no_marker_state(
    old_head_database: Engine,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    with old_head_database.connect() as connection:
        first = apply_rebaseline(connection, _request("development"))
    with old_head_database.connect() as connection:
        marker_before = read_schema_identity(connection)

    with old_head_database.connect() as connection:
        second = apply_rebaseline(connection, _request("development"))
    with old_head_database.connect() as connection:
        marker_after = read_schema_identity(connection)

    assert first.result == "rebaselined"
    assert second.result == "already_rebaselined"
    assert marker_after == marker_before
    assert _read_heads(old_head_database) == ("pre_ga_v1_0001",)


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL and "
        "MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL are required"
    ),
)
def test_retained_snapshot_mismatch_rolls_back_every_mutation(
    old_head_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_database_comment(
        old_head_database,
        "mindatlas:deployment_class=development",
    )
    original_snapshot = rebaseline_module.snapshot_retained_tables
    calls = 0

    def mismatch_after_mutation(connection, ephemeral_key):  # noqa: ANN001
        nonlocal calls
        calls += 1
        snapshot = original_snapshot(connection, ephemeral_key)
        if calls == 2:
            return snapshot + (
                rebaseline_module.RetainedTableSnapshot(
                    table_key="public.injected_drift",
                    row_count=1,
                    keyed_digest=b"x" * 32,
                ),
            )
        return snapshot

    monkeypatch.setattr(
        rebaseline_module,
        "snapshot_retained_tables",
        mismatch_after_mutation,
    )

    with old_head_database.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^retained_data_changed$",
    ):
        apply_rebaseline(connection, _request("development"))

    assert _read_heads(old_head_database) == ("b6e2d4f8a901",)
    with old_head_database.connect() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('public.mindatlas_schema_identity')")
        ) is None
        assert connection.scalar(
            text(
                "SELECT state_revision "
                "FROM assistant_runtime_rollout_control "
                "WHERE singleton_key = 'singleton'"
            )
        ) == 0
