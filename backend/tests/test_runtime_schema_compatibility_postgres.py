from __future__ import annotations

import pytest

from app.schema.compatibility import runtime_schema_compatibility
from app.schema.rebaseline import apply_rebaseline
from app.schema.contracts import DeploymentClass
from app.schema.rebaseline import MAINTENANCE_ACKNOWLEDGEMENT, RebaselineRequest

from tests.test_schema_rebaseline_postgres import (
    _POSTGRES_URL,
    _SOURCE_URL,
    _cloned_old_head_database,
    _set_database_comment,
)


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason="PostgreSQL compatibility targets are required",
)
def test_clean_root_is_family_bound_and_runtime_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "compatibility-test-build")
    with _cloned_old_head_database() as engine:
        _set_database_comment(
            engine,
            "mindatlas:deployment_class=development",
        )
        request = RebaselineRequest(
            deployment_class=DeploymentClass.DEVELOPMENT,
            acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
            build_revision="compatibility-test-build",
        )
        with engine.connect() as connection:
            apply_rebaseline(connection, request)
            snapshot = runtime_schema_compatibility().evaluate(connection)

        assert snapshot.compatible is True
        assert snapshot.safe_reason is None
        assert snapshot.schema_family == "pre_ga_v1"
        assert snapshot.schema_revision == "pre_ga_v1_0001"


@pytest.mark.skipif(
    not _POSTGRES_URL or not _SOURCE_URL,
    reason="PostgreSQL compatibility targets are required",
)
def test_old_head_is_incompatible_before_rebaseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINDATLAS_DEPLOYMENT_CLASS", "development")
    monkeypatch.setenv("APP_BUILD_REVISION", "compatibility-test-build")
    with _cloned_old_head_database() as engine:
        _set_database_comment(
            engine,
            "mindatlas:deployment_class=development",
        )
        with engine.connect() as connection:
            snapshot = runtime_schema_compatibility().evaluate(connection)

        assert snapshot.compatible is False
        assert snapshot.safe_reason == "schema_incompatible"
        assert snapshot.diagnostic_code == "revision_incompatible"
