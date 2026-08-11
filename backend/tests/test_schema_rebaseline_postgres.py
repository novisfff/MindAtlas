"""PostgreSQL rebaseline boundary checks for the clean-root release.

The historical migration chain is archived.  Release tests reconstruct the
reviewed pre-squash source from the committed catalog manifest after installing
the clean root; they never import or execute an archived revision.
"""

from __future__ import annotations

from dataclasses import replace
import os
import uuid
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.pre_squash_fixture import install_pre_squash_fixture
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import (  # noqa: E402
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    DeploymentClass,
)
from app.schema.identity import read_schema_identity  # noqa: E402
import app.schema.rebaseline as rebaseline_module  # noqa: E402
from app.schema.rebaseline import (  # noqa: E402
    MAINTENANCE_ACKNOWLEDGEMENT,
    REBASELINE_ADVISORY_LOCK_KEY,
    RebaselineRefused,
    RebaselineRequest,
    apply_rebaseline,
    validate_rebaseline_source,
)


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root rebaseline proof",
)


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _set_database_comment(engine: Engine, deployment_class: str | None) -> None:
    with engine.begin() as connection:
        database_name = connection.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        if deployment_class is None:
            connection.exec_driver_sql(f'COMMENT ON DATABASE "{database_name}" IS NULL')
            return
        connection.exec_driver_sql(
            f'COMMENT ON DATABASE "{database_name}" IS %s',
            (f"mindatlas:deployment_class={deployment_class}",),
        )


def _request(deployment_class: DeploymentClass) -> RebaselineRequest:
    return RebaselineRequest(
        deployment_class=deployment_class,
        acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
        build_revision="test-clean-root-rebaseline",
    )


@pytest.fixture()
def presquash_engine(clean_root_engine: Engine) -> Iterator[Engine]:
    """Reconstruct the committed source snapshot without archived migrations."""
    assert _POSTGRES_URL
    install_pre_squash_fixture(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-presquash-rebaseline",
    )
    _set_database_comment(clean_root_engine, "rehearsal")
    yield clean_root_engine


@pytest.fixture()
def clean_root_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    assert _POSTGRES_URL
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "rehearsal")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_BUILD_REVISION", "test-clean-root-rebaseline")
    reset_caches()
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-clean-root-rebaseline",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def _read_head(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        )


def _assert_presquash_unchanged(engine: Engine) -> None:
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == PRE_SQUASH_HEAD
        )
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='assistant_runtime_migration_item'"
                )
            ).first()
            is not None
        )
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='mindatlas_schema_identity'"
                )
            ).first()
            is None
        )


def test_clean_root_rebaseline_is_idempotent_and_does_not_mutate(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "rehearsal")
    with clean_root_engine.connect() as connection:
        report = apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert report.result == "already_rebaselined"
    assert report.before_revision == CLEAN_ROOT_REVISION
    assert report.after_revision == CLEAN_ROOT_REVISION
    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION
    with clean_root_engine.connect() as connection:
        marker = read_schema_identity(connection)
    assert marker.schema_revision == CLEAN_ROOT_REVISION
    assert marker.deployment_class is DeploymentClass.REHEARSAL


def test_presquash_rebaseline_preserves_retained_rows_and_drops_only_legacy(
    presquash_engine: Engine,
) -> None:
    retained_id = uuid.uuid4()
    key = f"rebaseline-{retained_id.hex}"
    with presquash_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_setting "
                "(id, key, value_json, created_at, updated_at) "
                "VALUES (:id, :key, CAST(:value AS json), NOW(), NOW())"
            ),
            {"id": retained_id, "key": key, "value": '{"v": 1}'},
        )
        before = connection.execute(
            text("SELECT value_json FROM app_setting WHERE id=:id"),
            {"id": retained_id},
        ).scalar_one()
    with presquash_engine.connect() as connection:
        report = apply_rebaseline(
            connection,
            _request(DeploymentClass.REHEARSAL),
        )

    assert report.result == "rebaselined"
    assert report.before_revision == PRE_SQUASH_HEAD
    assert report.after_revision == CLEAN_ROOT_REVISION
    assert report.retained_data_unchanged is True
    assert report.removed_known_inert_seed_rows == 1
    with presquash_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == CLEAN_ROOT_REVISION
        )
        assert (
            connection.execute(
                text("SELECT value_json FROM app_setting WHERE id=:id"),
                {"id": retained_id},
            ).scalar_one()
            == before
        )
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='assistant_runtime_migration_item'"
                )
            ).first()
            is None
        )
        marker = read_schema_identity(connection)
    assert marker.schema_revision == CLEAN_ROOT_REVISION


def test_presquash_rebaseline_succeeds_for_development_identity(
    presquash_engine: Engine,
) -> None:
    _set_database_comment(presquash_engine, "development")
    with presquash_engine.connect() as connection:
        report = apply_rebaseline(
            connection,
            _request(DeploymentClass.DEVELOPMENT),
        )

    assert report.result == "rebaselined"
    assert report.before_revision == PRE_SQUASH_HEAD
    assert report.after_revision == CLEAN_ROOT_REVISION
    with presquash_engine.connect() as connection:
        marker = read_schema_identity(connection)
    assert marker.deployment_class is DeploymentClass.DEVELOPMENT


def test_clean_root_rebaseline_rejects_production_before_mutation(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "rehearsal")
    with clean_root_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^production_rebaseline_forbidden$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.PRODUCTION))

    assert exc.value.safe_code == "production_rebaseline_forbidden"
    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION


@pytest.mark.parametrize(
    ("database_class", "request_class", "expected_code"),
    [
        (
            None,
            DeploymentClass.REHEARSAL,
            "database_deployment_identity_missing",
        ),
        (
            "development",
            DeploymentClass.REHEARSAL,
            "deployment_identity_mismatch",
        ),
        (
            "shared",
            DeploymentClass.REHEARSAL,
            "database_deployment_identity_unknown",
        ),
    ],
)
def test_presquash_rebaseline_rejects_database_identity_before_mutation(
    presquash_engine: Engine,
    database_class: str | None,
    request_class: DeploymentClass,
    expected_code: str,
) -> None:
    _set_database_comment(presquash_engine, database_class)
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match=f"^{expected_code}$",
    ) as exc:
        apply_rebaseline(connection, _request(request_class))
    assert exc.value.safe_code == expected_code
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_missing_head_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))

    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "pre_squash_head_mismatch"
    with presquash_engine.connect() as connection:
        assert (
            tuple(
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
            == ()
        )
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='mindatlas_schema_identity'"
                )
            ).first()
            is None
        )


def test_presquash_rebaseline_rejects_multiple_heads_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('unreviewed_head')"
            )
        )

    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "pre_squash_head_mismatch"
    with presquash_engine.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars()
        ) == (PRE_SQUASH_HEAD, "unreviewed_head")
        assert (
            connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='mindatlas_schema_identity'"
                )
            ).first()
            is None
        )


def test_presquash_rebaseline_rejects_nonempty_legacy_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assistant_runtime_migration_item "
                "(id, subject_kind, source_type, source_id, source_name, "
                "source_name_normalized, source_digest, evidence_json, "
                "source_revision, target_revision, attempt_count, state_revision, "
                "state, created_at, updated_at) VALUES "
                "(:id, 'skill', 'legacy', 'fixture', '', '', :digest, '{}'::json, "
                "0, 0, 0, 0, 'discovered', NOW(), NOW())"
            ),
            {"id": uuid.uuid4(), "digest": "a" * 64},
        )
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^legacy_exclusion_data_present$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))
    assert exc.value.safe_code == "legacy_exclusion_data_present"
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_non_inert_legacy_control_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text("UPDATE assistant_runtime_rollout_control " "SET state_revision = 1")
        )
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^legacy_exclusion_data_present$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "legacy_exclusion_data_present"
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_schema_drift_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE app_setting ADD COLUMN rebaseline_drift integer")
        )
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_fingerprint_mismatch$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))
    assert exc.value.safe_code == "pre_squash_fingerprint_mismatch"
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_extra_legacy_prefix_object_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE legacy_rebaseline_unreviewed (id integer)")
        )
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_fingerprint_mismatch$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "pre_squash_fingerprint_mismatch"
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_wrong_head_without_mutation(
    presquash_engine: Engine,
) -> None:
    with presquash_engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = 'unreviewed_head'")
        )

    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "pre_squash_head_mismatch"
    with presquash_engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :revision"),
            {"revision": PRE_SQUASH_HEAD},
        )
    _assert_presquash_unchanged(presquash_engine)


def test_presquash_rebaseline_rejects_lock_contention_without_mutation(
    presquash_engine: Engine,
) -> None:
    holder = presquash_engine.connect()
    transaction = holder.begin()
    try:
        holder.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": REBASELINE_ADVISORY_LOCK_KEY},
        )
        with presquash_engine.connect() as connection, pytest.raises(
            RebaselineRefused,
            match="^rebaseline_lock_unavailable$",
        ) as exc:
            apply_rebaseline(
                connection,
                _request(DeploymentClass.REHEARSAL),
            )
        assert exc.value.safe_code == "rebaseline_lock_unavailable"
        _assert_presquash_unchanged(presquash_engine)
    finally:
        transaction.rollback()
        holder.close()


def test_presquash_rebaseline_rolls_back_when_retained_snapshot_changes(
    presquash_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_snapshot = rebaseline_module.snapshot_retained_tables
    calls = 0

    def changed_snapshot(connection, ephemeral_key):  # noqa: ANN001
        nonlocal calls
        calls += 1
        snapshot = real_snapshot(connection, ephemeral_key)
        if calls == 2:
            assert snapshot
            return (
                *snapshot[:-1],
                replace(
                    snapshot[-1],
                    row_count=snapshot[-1].row_count + 1,
                ),
            )
        return snapshot

    monkeypatch.setattr(
        rebaseline_module,
        "snapshot_retained_tables",
        changed_snapshot,
    )
    with presquash_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^retained_data_changed$",
    ) as exc:
        apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))

    assert exc.value.safe_code == "retained_data_changed"
    assert calls == 2
    _assert_presquash_unchanged(presquash_engine)


def test_clean_root_is_rejected_as_a_historical_rebaseline_source(
    clean_root_engine: Engine,
) -> None:
    _set_database_comment(clean_root_engine, "development")
    with clean_root_engine.connect() as connection, pytest.raises(
        RebaselineRefused,
        match="^pre_squash_head_mismatch$",
    ):
        validate_rebaseline_source(connection, _request(DeploymentClass.DEVELOPMENT))

    assert _read_head(clean_root_engine) == CLEAN_ROOT_REVISION
