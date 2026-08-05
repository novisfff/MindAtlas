from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.schema.canonical import canonical_json_bytes, sha256_canonical_json
from app.schema.application_contract import (
    LogicalApplicationContractError,
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import compare_documents
from app.schema.catalog import PostgresCatalogReader
from app.schema.contracts import DeploymentClass, SchemaRuntimeIdentityMaterial
from app.schema.contracts import CanonicalSchemaDocument, CanonicalSchemaObject
from app.schema.identity import (
    DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH,
    SchemaIdentityError,
    load_expected_schema_contract,
    read_schema_identity,
    schema_runtime_identity_digest,
    schema_runtime_identity_payload,
)
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import (
    build_staged_alembic_directory,
    run_staged_alembic,
)


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture()
def empty_postgres_engine():  # noqa: ANN201
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def _identity_material() -> SchemaRuntimeIdentityMaterial:
    return SchemaRuntimeIdentityMaterial(
        schema_family="pre_ga_v1",
        schema_revision="pre_ga_v1_0001",
        structural_fingerprint="1" * 64,
        seed_contract_digest="2" * 64,
        deployment_class=DeploymentClass.REHEARSAL,
        runtime_contract_version=1,
        checkpoint_codec_version=3,
        capability_feature_digest="3" * 64,
        operator_auth_contract_version="operator-auth-v1",
    )


def test_runtime_identity_digest_binds_every_required_input() -> None:
    material = _identity_material()
    payload = schema_runtime_identity_payload(material)

    assert set(payload) == {
        "schemaFamily",
        "schemaRevision",
        "structuralFingerprint",
        "seedContractDigest",
        "deploymentClass",
        "runtimeContractVersion",
        "checkpointCodecVersion",
        "capabilityFeatureDigest",
        "operatorAuthContractVersion",
    }
    assert schema_runtime_identity_digest(material) == sha256_canonical_json(
        payload
    )


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("schema_revision", "pre_ga_v1_0002"),
        ("structural_fingerprint", "4" * 64),
        ("seed_contract_digest", "5" * 64),
        ("deployment_class", DeploymentClass.PRODUCTION),
        ("runtime_contract_version", 2),
        ("checkpoint_codec_version", 4),
        ("capability_feature_digest", "6" * 64),
        ("operator_auth_contract_version", "operator-auth-v2"),
    ),
)
def test_runtime_identity_digest_changes_with_each_bound_input(
    field_name: str,
    changed_value: object,
) -> None:
    original = _identity_material()
    changed = replace(original, **{field_name: changed_value})

    assert schema_runtime_identity_digest(changed) != (
        schema_runtime_identity_digest(original)
    )


def test_runtime_identity_digest_ignores_mapping_insertion_order() -> None:
    payload = schema_runtime_identity_payload(_identity_material())
    reversed_payload = dict(reversed(tuple(payload.items())))

    assert sha256_canonical_json(reversed_payload) == (
        sha256_canonical_json(payload)
    )


def test_expected_schema_contract_is_strict_and_self_digesting() -> None:
    expected = load_expected_schema_contract()
    logical = load_logical_application_contract()

    assert DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH.is_file()
    assert expected.schema_family == "pre_ga_v1"
    assert expected.schema_revision == "pre_ga_v1_0001"
    assert expected.application_structural_fingerprint == (
        logical.logical_application_fingerprint
    )
    assert expected.schema_identity_control_fingerprint == (
        "6bf3db9018a22c66055ade8d16a98dac2fdcf4fd0d97b03077da3bc5641dade7"
    )
    assert expected.checkpoint_codec_version == 3
    assert expected.manifest_digest == sha256_canonical_json(
        expected.to_payload()
    )


def test_expected_schema_contract_rejects_extra_fields(tmp_path: Path) -> None:
    payload = json.loads(
        DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    payload["unexpected"] = True
    path = tmp_path / "expected.json"
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        SchemaIdentityError,
        match="expected_schema_manifest_invalid",
    ):
        load_expected_schema_contract(path)


def test_expected_schema_contract_rejects_self_digest_drift(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    payload["manifestDigest"] = "0" * 64
    path = tmp_path / "expected.json"
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        SchemaIdentityError,
        match="expected_schema_manifest_digest_mismatch",
    ):
        load_expected_schema_contract(path)


def test_expected_schema_contract_rejects_cross_reference_drift(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    payload["applicationStructuralFingerprint"] = "0" * 64
    digest_payload = {
        key: value for key, value in payload.items() if key != "manifestDigest"
    }
    payload["manifestDigest"] = sha256_canonical_json(digest_payload)
    path = tmp_path / "expected.json"
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        SchemaIdentityError,
        match="expected_schema_manifest_cross_reference_mismatch",
    ):
        load_expected_schema_contract(path)


def test_marker_reader_does_not_chain_raw_malformed_values() -> None:
    class FakeResult:
        def mappings(self):  # noqa: ANN201
            return self

        def all(self):  # noqa: ANN201
            return [
                {
                    "singleton_key": "current",
                    "schema_family": "pre_ga_v1",
                    "schema_revision": "pre_ga_v1_0001",
                    "structural_fingerprint": "1" * 64,
                    "runtime_identity_digest": "2" * 64,
                    "seed_contract_digest": "3" * 64,
                    "deployment_class": "sensitive-malformed-marker-value",
                    "runtime_contract_version": 1,
                    "checkpoint_codec_version": 3,
                    "capability_feature_digest": "4" * 64,
                    "operator_auth_contract_version": "operator-auth-v1",
                    "identity_contract_version": 1,
                    "created_at": None,
                    "updated_at": None,
                }
            ]

    class FakeConnection:
        def execute(self, statement, parameters):  # noqa: ANN001, ANN201
            return FakeResult()

    with pytest.raises(
        SchemaIdentityError,
        match="marker_malformed",
    ) as exc:
        read_schema_identity(FakeConnection())

    assert exc.value.__cause__ is None


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root migration proof",
)
def test_staged_alembic_environment_has_only_clean_root_head(tmp_path: Path) -> None:
    staged = build_staged_alembic_directory(tmp_path)

    result = run_staged_alembic(staged, "heads")

    assert result.returncode == 0, result.stderr
    assert "pre_ga_v1_0001 (pre_ga_v1) (head)" in result.stdout
    assert "b6e2d4f8a901" not in result.stdout


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root migration proof",
)
@pytest.mark.parametrize(
    "deployment_class",
    ("development", "rehearsal", "production"),
)
def test_clean_root_upgrades_empty_postgres_and_writes_exact_marker(
    empty_postgres_engine,
    tmp_path: Path,
    deployment_class: str,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)

    result = run_staged_alembic(
        staged,
        "upgrade",
        "head",
        database_url=_POSTGRES_URL,
        deployment_class=deployment_class,
    )

    assert result.returncode == 0, result.stderr
    with empty_postgres_engine.connect() as connection:
        marker = read_schema_identity(connection)
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    assert revision == "pre_ga_v1_0001"
    assert marker.schema_family == "pre_ga_v1"
    assert marker.schema_revision == "pre_ga_v1_0001"
    assert marker.deployment_class.value == deployment_class
    expected = load_expected_schema_contract()
    assert marker.structural_fingerprint == (
        expected.application_structural_fingerprint
    )
    assert marker.seed_contract_digest == expected.seed_contract_digest
    assert marker.runtime_contract_version == expected.runtime_contract_version
    assert marker.checkpoint_codec_version == expected.checkpoint_codec_version
    assert marker.capability_feature_digest == expected.capability_feature_digest
    assert marker.operator_auth_contract_version == (
        expected.operator_auth_contract_version
    )
    assert marker.identity_contract_version == 1
    assert marker.runtime_identity_digest == schema_runtime_identity_digest(
        marker.to_identity_material()
    )


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root migration proof",
)
@pytest.mark.parametrize("deployment_class", (None, "shared"))
def test_clean_root_refuses_missing_or_unknown_deployment_class(
    empty_postgres_engine,
    tmp_path: Path,
    deployment_class: str | None,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)

    result = run_staged_alembic(
        staged,
        "upgrade",
        "head",
        database_url=_POSTGRES_URL,
        deployment_class=deployment_class,
    )

    assert result.returncode != 0
    assert "schema_deployment_class_invalid" in result.stderr
    with empty_postgres_engine.connect() as connection:
        table_names = set(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public'"
                )
            ).scalars()
        )
    assert table_names == set()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for marker control proof",
)
def test_schema_identity_control_is_exact_and_rejects_unsafe_mutation(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    result = run_staged_alembic(
        staged,
        "upgrade",
        "head",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
    )
    assert result.returncode == 0, result.stderr

    with empty_postgres_engine.connect() as connection:
        constraint_names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_catalog.pg_constraint "
                    "WHERE conrelid = 'public.mindatlas_schema_identity'::regclass"
                )
            ).scalars()
        )
        trigger_names = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_catalog.pg_trigger "
                    "WHERE tgrelid = "
                    "'public.mindatlas_schema_identity'::regclass "
                    "AND NOT tgisinternal"
                )
            ).scalars()
        )
    assert constraint_names == {
        "mindatlas_schema_identity_pkey",
        "ck_schema_identity_singleton",
        "ck_schema_identity_family",
        "ck_schema_identity_deployment_class",
        "ck_schema_identity_digest_shapes",
        "ck_schema_identity_positive_versions",
    }
    assert trigger_names == {"trg_mindatlas_schema_identity_guard"}

    with pytest.raises(DBAPIError), empty_postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM mindatlas_schema_identity"))
    with pytest.raises(DBAPIError), empty_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE mindatlas_schema_identity "
                "SET deployment_class = 'production'"
            )
        )
    with pytest.raises(DBAPIError), empty_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE mindatlas_schema_identity "
                "SET schema_revision = 'pre_ga_v1_0002', "
                "updated_at = clock_timestamp()"
            )
        )

    with empty_postgres_engine.connect() as connection:
        marker = read_schema_identity(connection)
    assert marker.schema_revision == "pre_ga_v1_0001"
    assert marker.deployment_class is DeploymentClass.REHEARSAL


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for control projection proof",
)
def test_clean_root_controls_validate_before_exact_application_projection(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    result = run_staged_alembic(
        staged,
        "upgrade",
        "head",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
    )
    assert result.returncode == 0, result.stderr

    with empty_postgres_engine.connect() as connection:
        raw = PostgresCatalogReader(connection).read_document()
    actual = project_logical_application_document(
        raw,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )
    expected = load_logical_application_contract().logical_application_document

    compare_documents(expected, actual, exclusions=None)
    assert all(
        item.key.name
        not in {
            "alembic_version",
            "mindatlas_schema_identity",
            "mindatlas_guard_schema_identity_mutation",
            "trg_mindatlas_schema_identity_guard",
        }
        for item in actual.objects
    )


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for control rejection proof",
)
def test_clean_root_projection_rejects_each_missing_or_drifted_control(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    _upgrade_rehearsal(staged)
    with empty_postgres_engine.connect() as connection:
        raw = PostgresCatalogReader(connection).read_document()
    control_names = {
        "alembic_version",
        "mindatlas_schema_identity",
        "mindatlas_guard_schema_identity_mutation",
        "trg_mindatlas_schema_identity_guard",
    }
    controls = tuple(
        item for item in raw.objects if item.key.name in control_names
    )
    assert len(controls) == 4

    for control in controls:
        missing = CanonicalSchemaDocument(
            1,
            raw.postgres_major,
            tuple(item for item in raw.objects if item.key != control.key),
        )
        with pytest.raises(
            LogicalApplicationContractError,
            match="schema_control_contract_missing",
        ):
            project_logical_application_document(
                missing,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )

        drifted = CanonicalSchemaDocument(
            1,
            raw.postgres_major,
            tuple(
                CanonicalSchemaObject(item.key, {"drifted": True})
                if item.key == control.key
                else item
                for item in raw.objects
            ),
        )
        with pytest.raises(
            LogicalApplicationContractError,
            match="schema_control_contract_drift",
        ):
            project_logical_application_document(
                drifted,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )


def _upgrade_rehearsal(staged: Path) -> None:
    result = run_staged_alembic(
        staged,
        "upgrade",
        "head",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for downgrade proof",
)
def test_root_downgrade_refuses_without_test_guard(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    _upgrade_rehearsal(staged)

    result = run_staged_alembic(
        staged,
        "downgrade",
        "base",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="production",
    )

    assert result.returncode != 0
    assert "schema_test_downgrade_forbidden" in result.stderr
    with empty_postgres_engine.connect() as connection:
        assert read_schema_identity(connection).schema_revision == (
            "pre_ga_v1_0001"
        )


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for downgrade proof",
)
def test_root_downgrade_refuses_business_rows(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    _upgrade_rehearsal(staged)
    with empty_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entry_type ("
                "id, code, name, graph_enabled, ai_enabled, enabled, "
                "created_at, updated_at"
                ") VALUES ("
                "'00000000-0000-0000-0000-000000000001', "
                "'test', 'Test', true, true, true, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            )
        )

    result = run_staged_alembic(
        staged,
        "downgrade",
        "base",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        downgrade_ack="I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION",
    )

    assert result.returncode != 0
    assert "schema_test_downgrade_nonempty" in result.stderr
    with empty_postgres_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM entry_type")
        ).scalar_one()
    assert count == 1


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for downgrade proof",
)
def test_root_downgrade_to_empty_is_test_only_and_reupgrade_is_exact(
    empty_postgres_engine,
    tmp_path: Path,
) -> None:
    staged = build_staged_alembic_directory(tmp_path)
    _upgrade_rehearsal(staged)

    result = run_staged_alembic(
        staged,
        "downgrade",
        "base",
        database_url=_POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        downgrade_ack="I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION",
    )

    assert result.returncode == 0, result.stderr
    with empty_postgres_engine.connect() as connection:
        application_tables = set(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            ).scalars()
        )
    assert application_tables == set()

    _upgrade_rehearsal(staged)
    with empty_postgres_engine.connect() as connection:
        raw = PostgresCatalogReader(connection).read_document()
    actual = project_logical_application_document(
        raw,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )
    expected = load_logical_application_contract().logical_application_document
    compare_documents(expected, actual, exclusions=None)
