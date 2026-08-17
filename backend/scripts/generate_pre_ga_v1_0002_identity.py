#!/usr/bin/env python3
"""Generate/check the committed additive ``pre_ga_v1_0002`` identity."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schema.application_contract import SchemaControlStage, project_logical_application_document
from app.schema.canonical import CanonicalSchemaDocument, structural_fingerprint
from app.schema.catalog import PostgresCatalogReader
from app.schema.identity import load_expected_schema_contract_v2, read_schema_identity
from app.release.contracts import schema_contract_material_digest
from app.schema.contracts import DeploymentClass, SchemaRuntimeIdentityMaterial
from app.schema.identity import schema_runtime_identity_digest
from app.schema.canonical import canonical_json_bytes


MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "pre_ga_v1_0002_create_entry_launch.py"
MANIFEST_PATH = BACKEND_ROOT / "app" / "schema" / "manifests" / "pre_ga_v1_0002-expected.json"
ROOT_PATH = BACKEND_ROOT / "alembic" / "versions" / "pre_ga_v1_0001_clean_baseline.py"
REVIEWED_ROOT_DIGEST = "61b6da16636244fbbff123b6c337e11735b22449d8b182706d4965d09fa74455"


def _migration_module():
    spec = importlib.util.spec_from_file_location("mindatlas_pre_ga_v1_0002", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("pre_ga_0002_migration_unloadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect_current(database_url: str) -> dict[str, str | int]:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision != "pre_ga_v1_0002":
                raise RuntimeError("pre_ga_0002_revision_missing")
            document = PostgresCatalogReader(connection).read_document()
            projected = project_logical_application_document(
                document,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )
            logical_keys = {item.key for item in projected.objects}
            controls = tuple(item for item in document.objects if item.key not in logical_keys)
            control_document = CanonicalSchemaDocument(1, document.postgres_major, controls)
            marker = read_schema_identity(connection)
            return {
                "application": structural_fingerprint(projected),
                "control": structural_fingerprint(control_document),
                "marker": marker.structural_fingerprint,
                "revision": marker.schema_revision,
                "runtime": marker.runtime_identity_digest,
                "seed": marker.seed_contract_digest,
                "runtimeContractVersion": marker.runtime_contract_version,
                "checkpointCodecVersion": marker.checkpoint_codec_version,
                "capabilityFeatureDigest": marker.capability_feature_digest,
                "operatorAuthContractVersion": marker.operator_auth_contract_version,
                "identityContractVersion": marker.identity_contract_version,
                "deploymentClass": marker.deployment_class.value,
            }
    finally:
        engine.dispose()


def check() -> None:
    if __import__("hashlib").sha256(ROOT_PATH.read_bytes()).hexdigest() != REVIEWED_ROOT_DIGEST:
        raise RuntimeError("pre_ga_root_changed")
    migration = _migration_module()
    expected = load_expected_schema_contract_v2()
    if migration.EXPECTED_APPLICATION_FINGERPRINT != expected.application_structural_fingerprint:
        raise RuntimeError("pre_ga_0002_migration_manifest_mismatch")
    database_url = os.environ.get("MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL", "").strip()
    if database_url:
        actual = inspect_current(database_url)
        if (
            actual["revision"] != expected.schema_revision
            or actual["application"] != expected.application_structural_fingerprint
            or actual["control"] != expected.schema_identity_control_fingerprint
            or actual["marker"] != expected.application_structural_fingerprint
            or actual["runtime"]
            != expected.runtime_identity_digests[str(actual["deploymentClass"])]
        ):
            raise RuntimeError("pre_ga_0002_identity_mismatch")
        rendered = _render_manifest(actual)
        if MANIFEST_PATH.read_bytes() != rendered:
            raise RuntimeError("pre_ga_0002_manifest_not_generated")
    print("pre_ga_v1_0002_identity_check_ok")


def _render_manifest(actual: dict[str, str | int]) -> bytes:
    """Render the exact canonical manifest from a live 0002 introspection."""
    required = (
        "application",
        "control",
        "marker",
        "revision",
        "runtime",
        "seed",
        "runtimeContractVersion",
        "checkpointCodecVersion",
        "capabilityFeatureDigest",
        "operatorAuthContractVersion",
        "identityContractVersion",
    )
    if any(key not in actual for key in required):
        raise RuntimeError("pre_ga_0002_identity_incomplete")
    material_digest = schema_contract_material_digest(
        schema_family="pre_ga_v1",
        schema_revision=str(actual["revision"]),
        schema_application_fingerprint=str(actual["application"]),
        schema_control_fingerprint=str(actual["control"]),
        schema_identity_contract_version=int(actual["identityContractVersion"]),
        schema_seed_contract_digest=str(actual["seed"]),
        schema_runtime_contract_version=int(actual["runtimeContractVersion"]),
        schema_checkpoint_codec_version=int(actual["checkpointCodecVersion"]),
        schema_capability_feature_digest=str(actual["capabilityFeatureDigest"]),
        operator_auth_contract_version=str(actual["operatorAuthContractVersion"]),
    )
    runtime_identities = {}
    for deployment in DeploymentClass:
        runtime_identities[deployment.value] = schema_runtime_identity_digest(
            SchemaRuntimeIdentityMaterial(
                schema_family="pre_ga_v1",
                schema_revision=str(actual["revision"]),
                structural_fingerprint=str(actual["application"]),
                seed_contract_digest=str(actual["seed"]),
                deployment_class=deployment,
                runtime_contract_version=int(actual["runtimeContractVersion"]),
                checkpoint_codec_version=int(actual["checkpointCodecVersion"]),
                capability_feature_digest=str(actual["capabilityFeatureDigest"]),
                operator_auth_contract_version=str(actual["operatorAuthContractVersion"]),
            )
        )
    payload = {
        "schemaVersion": 1,
        "schemaFamily": "pre_ga_v1",
        "schemaRevision": str(actual["revision"]),
        "applicationStructuralFingerprint": str(actual["application"]),
        "schemaIdentityControlFingerprint": str(actual["control"]),
        "schemaSeedContractDigest": str(actual["seed"]),
        "runtimeContractVersion": int(actual["runtimeContractVersion"]),
        "checkpointCodecVersion": int(actual["checkpointCodecVersion"]),
        "capabilityFeatureDigest": str(actual["capabilityFeatureDigest"]),
        "operatorAuthContractVersion": str(actual["operatorAuthContractVersion"]),
        "identityContractVersion": int(actual["identityContractVersion"]),
        "schemaContractMaterialDigest": material_digest,
        "runtimeIdentityDigests": runtime_identities,
    }
    payload["manifestDigest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return canonical_json_bytes(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write:
        # Generation is intentionally a fresh-database operation.  The current
        # implementation writes only after a checked live 0002 catalog exists;
        # this prevents a caller from supplying arbitrary fingerprints.
        database_url = os.environ.get("MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL", "").strip()
        if not database_url:
            parser.error("--write requires MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL")
        actual = inspect_current(database_url)
        if actual["application"] != actual["marker"]:
            raise SystemExit("pre_ga_0002_identity_mismatch")
        rendered = _render_manifest(actual)
        temporary = MANIFEST_PATH.with_name(MANIFEST_PATH.name + ".tmp")
        temporary.write_bytes(rendered)
        temporary.replace(MANIFEST_PATH)
        second = _render_manifest(inspect_current(database_url))
        if second != rendered:
            raise SystemExit("pre_ga_0002_identity_render_not_stable")
        check()
        print("pre_ga_v1_0002_identity_written_byte_identical")
        return 0
    check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
