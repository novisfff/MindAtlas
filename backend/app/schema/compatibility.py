"""Family-bound runtime schema compatibility evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from threading import Lock
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.schema.application_contract import (
    LogicalApplicationContractError,
    SchemaControlStage,
    project_logical_application_document,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader
from app.schema.canonical import structural_fingerprint
from app.schema.contracts import (
    CanonicalSchemaDocument,
    CLEAN_ROOT_REVISION,
    DeploymentClass,
    SchemaCompatibilitySnapshot,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
)
from app.schema.exclusions import LEGACY_TABLE_NAMES
from app.schema.identity import (
    SchemaIdentityError,
    load_expected_schema_contract_v2,
    read_schema_identity,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import load_exclusion_manifest


@dataclass(frozen=True)
class SchemaCompatibilityRequirement:
    schema_family: str
    minimum_revision_ordinal: int
    compatible_revisions: Mapping[str, int]
    expected_application_fingerprints: Mapping[str, str]
    expected_marker_control_fingerprints: Mapping[str, str]
    seed_contract_digest: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str


def _load_requirement() -> SchemaCompatibilityRequirement:
    expected = load_expected_schema_contract_v2()
    return SchemaCompatibilityRequirement(
        schema_family=expected.schema_family,
        minimum_revision_ordinal=2,
        compatible_revisions={expected.schema_revision: 2},
        expected_application_fingerprints={
            expected.schema_revision: expected.application_structural_fingerprint,
        },
        expected_marker_control_fingerprints={
            expected.schema_revision: expected.schema_identity_control_fingerprint,
        },
        seed_contract_digest=expected.seed_contract_digest,
        runtime_contract_version=expected.runtime_contract_version,
        checkpoint_codec_version=expected.checkpoint_codec_version,
        capability_feature_digest=expected.capability_feature_digest,
        operator_auth_contract_version=expected.operator_auth_contract_version,
    )


class _LazyRequirement:
    def __init__(self) -> None:
        self._value: SchemaCompatibilityRequirement | None = None
        self._lock = Lock()

    def __getattr__(self, name: str):  # noqa: ANN001
        value = self._value
        if value is None:
            with self._lock:
                value = self._value
                if value is None:
                    try:
                        value = _load_requirement()
                    except Exception:
                        raise AttributeError("schema requirement unavailable") from None
                    self._value = value
        return getattr(value, name)


PLAN3_SCHEMA_REQUIREMENT = _LazyRequirement()


def _requirement() -> SchemaCompatibilityRequirement | None:
    try:
        return _load_requirement()
    except Exception:
        return None


def _incompatible_snapshot(code: str) -> SchemaCompatibilitySnapshot:
    return SchemaCompatibilitySnapshot(
        compatible=False,
        safe_reason="schema_incompatible",
        diagnostic_code=code,
        schema_family=None,
        schema_revision=None,
        deployment_class=None,
        structural_fingerprint=None,
        runtime_identity_digest=None,
    )


class FamilyBoundRuntimeSchemaCompatibility:
    """Evaluate every family, catalog, marker, and runtime identity dimension."""

    def _incompatible(self, code: str) -> SchemaCompatibilitySnapshot:
        return _incompatible_snapshot(code)

    def evaluate(self, db) -> SchemaCompatibilitySnapshot:  # noqa: ANN001
        try:
            return self._evaluate(db)
        except SchemaIdentityError as exc:
            return _incompatible_snapshot(exc.safe_code)
        except (SQLAlchemyError, CatalogReadError):
            return _incompatible_snapshot("catalog_unavailable")
        except Exception:
            return _incompatible_snapshot("catalog_unavailable")

    def is_compatible(self, db) -> bool:  # noqa: ANN001
        return self.evaluate(db).compatible

    def _evaluate(self, db) -> SchemaCompatibilitySnapshot:  # noqa: ANN001
        requirement = _requirement()
        if requirement is None:
            return _incompatible_snapshot("schema_manifest_invalid")
        db.execute(text("SET LOCAL search_path = public"))
        rows = tuple(
            str(value)
            for value in db.execute(
                text(
                    "SELECT version_num FROM \"public\".\"alembic_version\" "
                    "ORDER BY version_num"
                )
            ).scalars()
        )
        if len(rows) == 0:
            return _incompatible_snapshot("head_missing")
        if len(rows) != 1:
            return _incompatible_snapshot("head_ambiguous")
        revision = rows[0]
        ordinal = requirement.compatible_revisions.get(revision)
        if ordinal is None or ordinal < requirement.minimum_revision_ordinal:
            return _incompatible_snapshot("revision_incompatible")

        marker = read_schema_identity(db)
        if marker.schema_family != requirement.schema_family:
            return _incompatible_snapshot("family_mismatch")
        if marker.schema_revision != revision:
            return _incompatible_snapshot("revision_incompatible")
        deployment = self._deployment_class()
        if deployment is None or marker.deployment_class is not deployment:
            return _incompatible_snapshot("deployment_class_mismatch")
        build = str(getattr(get_settings(), "app_build_revision", "") or "").strip()
        if not build or build == "unknown" or (
            deployment in {DeploymentClass.REHEARSAL, DeploymentClass.PRODUCTION}
            and build == "development"
        ):
            return _incompatible_snapshot("build_identity_invalid")

        manifest = load_exclusion_manifest()
        document = PostgresCatalogReader(db).read_document()
        keys = {item.key for item in document.objects}
        if keys.intersection(set(manifest.object_keys)) or any(
            item.key.name in LEGACY_TABLE_NAMES for item in document.objects
        ):
            return _incompatible_snapshot("legacy_object_present")
        try:
            projected = project_logical_application_document(
                document,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )
        except LogicalApplicationContractError:
            return _incompatible_snapshot("marker_control_mismatch")
        fingerprint = structural_fingerprint(projected)
        expected_fingerprint = requirement.expected_application_fingerprints[revision]
        if fingerprint != expected_fingerprint:
            return _incompatible_snapshot("fingerprint_mismatch")

        expected = load_expected_schema_contract_v2()
        logical_keys = {item.key for item in projected.objects}
        controls = tuple(item for item in document.objects if item.key not in logical_keys)
        control_fingerprint = structural_fingerprint(
            CanonicalSchemaDocument(1, document.postgres_major, controls)
        )
        if control_fingerprint != requirement.expected_marker_control_fingerprints[revision]:
            return _incompatible_snapshot("marker_control_mismatch")
        if marker.identity_contract_version != SCHEMA_IDENTITY_CONTRACT_VERSION:
            return _incompatible_snapshot("identity_contract_mismatch")
        if marker.seed_contract_digest != requirement.seed_contract_digest:
            return _incompatible_snapshot("seed_contract_mismatch")
        if marker.runtime_contract_version != requirement.runtime_contract_version:
            return _incompatible_snapshot("runtime_contract_mismatch")
        if marker.checkpoint_codec_version != requirement.checkpoint_codec_version:
            return _incompatible_snapshot("checkpoint_codec_mismatch")
        if marker.capability_feature_digest != requirement.capability_feature_digest:
            return _incompatible_snapshot("capability_feature_mismatch")
        if marker.operator_auth_contract_version != requirement.operator_auth_contract_version:
            return _incompatible_snapshot("operator_auth_contract_mismatch")
        if schema_runtime_identity_digest(marker.to_identity_material()) != marker.runtime_identity_digest:
            return _incompatible_snapshot("runtime_identity_mismatch")
        if marker.structural_fingerprint != expected.application_structural_fingerprint:
            return _incompatible_snapshot("fingerprint_mismatch")
        return SchemaCompatibilitySnapshot(
            compatible=True,
            safe_reason=None,
            diagnostic_code=None,
            schema_family=marker.schema_family,
            schema_revision=marker.schema_revision,
            deployment_class=marker.deployment_class,
            structural_fingerprint=marker.structural_fingerprint,
            runtime_identity_digest=marker.runtime_identity_digest,
        )

    @staticmethod
    def _deployment_class() -> DeploymentClass | None:
        raw = os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "").strip()
        try:
            return DeploymentClass(raw)
        except ValueError:
            return None


_RUNTIME_SCHEMA_COMPATIBILITY = FamilyBoundRuntimeSchemaCompatibility()


def runtime_schema_compatibility() -> FamilyBoundRuntimeSchemaCompatibility:
    return _RUNTIME_SCHEMA_COMPATIBILITY


__all__ = [
    "FamilyBoundRuntimeSchemaCompatibility",
    "PLAN3_SCHEMA_REQUIREMENT",
    "SchemaCompatibilityRequirement",
    "runtime_schema_compatibility",
]
