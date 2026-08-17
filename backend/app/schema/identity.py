"""Family-bound schema identity material and marker access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.schema.canonical import sha256_canonical_json
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    NEXT_RESERVED_REVISION,
    SCHEMA_FAMILY,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
    SCHEMA_IDENTITY_SINGLETON_KEY,
    DeploymentClass,
    JsonValue,
    SchemaRuntimeIdentityMaterial,
)


DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "pre_ga_v1-expected.json"
)
DEFAULT_EXPECTED_SCHEMA_CONTRACT_V2_PATH = (
    Path(__file__).resolve().parent
    / "manifests"
    / "pre_ga_v1_0002-expected.json"
)
# ``pre_ga_v1_0001`` is a sealed historical root. Its marker was generated
# before the current runtime seed/Capability contracts were introduced; the
# additive ``pre_ga_v1_0002`` manifest owns those current values.
CLEAN_ROOT_SEED_CONTRACT_DIGEST = (
    "a728d696b086b0ced78a37de80a7831cd788e22f7668f083a7245706b13334ba"
)
CLEAN_ROOT_CAPABILITY_FEATURE_DIGEST = (
    "11af8408a0d3a6ff93a5170a9bb6758f430773d1e1343ee3982396f0ed9cd3b4"
)
SCHEMA_IDENTITY_CONTROL_FINGERPRINT = (
    "6bf3db9018a22c66055ade8d16a98dac2fdcf4fd0d97b03077da3bc5641dade7"
)
SCHEMA_IDENTITY_GUARD_SQL = """CREATE FUNCTION mindatlas_guard_schema_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected_revision text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'schema identity deletion is forbidden';
  END IF;
  IF NEW.singleton_key <> OLD.singleton_key
     OR NEW.schema_family <> OLD.schema_family
     OR NEW.deployment_class <> OLD.deployment_class
     OR NEW.created_at <> OLD.created_at
     OR NEW.identity_contract_version < OLD.identity_contract_version THEN
    RAISE EXCEPTION 'schema identity immutable field changed';
  END IF;
  expected_revision := current_setting(
    'mindatlas.schema_migration_revision', true
  );
  IF expected_revision IS NULL OR expected_revision = ''
     OR NEW.schema_revision <> expected_revision
     OR NEW.schema_revision = OLD.schema_revision
     OR NEW.updated_at <= OLD.updated_at THEN
    RAISE EXCEPTION 'schema identity advance is not migration-authorized';
  END IF;
  RETURN NEW;
END;
$$"""
SCHEMA_IDENTITY_TRIGGER_SQL = """CREATE TRIGGER trg_mindatlas_schema_identity_guard
BEFORE UPDATE OR DELETE ON mindatlas_schema_identity
FOR EACH ROW EXECUTE FUNCTION mindatlas_guard_schema_identity_mutation()"""
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_EXPECTED_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "schemaFamily",
        "schemaRevision",
        "applicationStructuralFingerprint",
        "schemaIdentityControlFingerprint",
        "seedContractDigest",
        "runtimeContractVersion",
        "checkpointCodecVersion",
        "capabilityFeatureDigest",
        "operatorAuthContractVersion",
        "canonicalizationVersion",
        "manifestDigest",
    }
)


class SchemaIdentityError(RuntimeError):
    """Bounded marker failure safe for compatibility control flow."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class ExpectedSchemaContract:
    schema_family: str
    schema_revision: str
    application_structural_fingerprint: str
    schema_identity_control_fingerprint: str
    seed_contract_digest: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str
    canonicalization_version: int
    manifest_digest: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "schemaFamily": self.schema_family,
            "schemaRevision": self.schema_revision,
            "applicationStructuralFingerprint": (
                self.application_structural_fingerprint
            ),
            "schemaIdentityControlFingerprint": (
                self.schema_identity_control_fingerprint
            ),
            "seedContractDigest": self.seed_contract_digest,
            "runtimeContractVersion": self.runtime_contract_version,
            "checkpointCodecVersion": self.checkpoint_codec_version,
            "capabilityFeatureDigest": self.capability_feature_digest,
            "operatorAuthContractVersion": (
                self.operator_auth_contract_version
            ),
            "canonicalizationVersion": self.canonicalization_version,
        }


@dataclass(frozen=True)
class ExpectedSchemaContractV2:
    schema_family: str
    schema_revision: str
    application_structural_fingerprint: str
    schema_identity_control_fingerprint: str
    seed_contract_digest: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str
    identity_contract_version: int
    schema_contract_material_digest: str
    runtime_identity_digests: dict[str, str]
    manifest_digest: str

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "schemaVersion": 1,
            "schemaFamily": self.schema_family,
            "schemaRevision": self.schema_revision,
            "applicationStructuralFingerprint": self.application_structural_fingerprint,
            "schemaIdentityControlFingerprint": self.schema_identity_control_fingerprint,
            "schemaSeedContractDigest": self.seed_contract_digest,
            "runtimeContractVersion": self.runtime_contract_version,
            "checkpointCodecVersion": self.checkpoint_codec_version,
            "capabilityFeatureDigest": self.capability_feature_digest,
            "operatorAuthContractVersion": self.operator_auth_contract_version,
            "identityContractVersion": self.identity_contract_version,
            "schemaContractMaterialDigest": self.schema_contract_material_digest,
            "runtimeIdentityDigests": self.runtime_identity_digests,
        }


class _DuplicateJsonMember(ValueError):
    pass


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateJsonMember
        payload[key] = value
    return payload


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def load_expected_schema_contract(
    path: Path = DEFAULT_EXPECTED_SCHEMA_CONTRACT_PATH,
) -> ExpectedSchemaContract:
    """Load and cross-check the deterministic clean-root identity contract."""
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise SchemaIdentityError("expected_schema_manifest_invalid") from None
    if not isinstance(raw, dict) or set(raw) != _EXPECTED_MANIFEST_KEYS:
        raise SchemaIdentityError("expected_schema_manifest_invalid")
    digest_fields = (
        "applicationStructuralFingerprint",
        "schemaIdentityControlFingerprint",
        "seedContractDigest",
        "capabilityFeatureDigest",
        "manifestDigest",
    )
    if any(not _valid_sha256(raw.get(field)) for field in digest_fields):
        raise SchemaIdentityError("expected_schema_manifest_invalid")
    if (
        type(raw.get("schemaVersion")) is not int
        or raw["schemaVersion"] != 1
        or raw.get("schemaFamily") != SCHEMA_FAMILY
        or raw.get("schemaRevision") != CLEAN_ROOT_REVISION
        or type(raw.get("runtimeContractVersion")) is not int
        or raw["runtimeContractVersion"] <= 0
        or type(raw.get("checkpointCodecVersion")) is not int
        or raw["checkpointCodecVersion"] <= 0
        or type(raw.get("canonicalizationVersion")) is not int
        or raw["canonicalizationVersion"] != 2
        or not isinstance(raw.get("operatorAuthContractVersion"), str)
        or not raw["operatorAuthContractVersion"]
    ):
        raise SchemaIdentityError("expected_schema_manifest_invalid")
    digest_payload = {
        key: value for key, value in raw.items() if key != "manifestDigest"
    }
    try:
        calculated_digest = sha256_canonical_json(digest_payload)
    except (TypeError, ValueError):
        raise SchemaIdentityError("expected_schema_manifest_invalid") from None
    if calculated_digest != raw["manifestDigest"]:
        raise SchemaIdentityError(
            "expected_schema_manifest_digest_mismatch"
        )

    from app.operator_auth.constants import OPERATOR_AUTH_CONTRACT_VERSION
    from app.schema.application_contract import load_logical_application_contract

    logical = load_logical_application_contract()
    if (
        raw["applicationStructuralFingerprint"]
        != logical.logical_application_fingerprint
        or raw["schemaIdentityControlFingerprint"]
        != SCHEMA_IDENTITY_CONTROL_FINGERPRINT
        or raw["seedContractDigest"] != CLEAN_ROOT_SEED_CONTRACT_DIGEST
        or raw["runtimeContractVersion"] != 1
        or raw["checkpointCodecVersion"] != 3
        or raw["capabilityFeatureDigest"]
        != CLEAN_ROOT_CAPABILITY_FEATURE_DIGEST
        or raw["operatorAuthContractVersion"]
        != OPERATOR_AUTH_CONTRACT_VERSION
    ):
        raise SchemaIdentityError(
            "expected_schema_manifest_cross_reference_mismatch"
        )
    return ExpectedSchemaContract(
        schema_family=SCHEMA_FAMILY,
        schema_revision=CLEAN_ROOT_REVISION,
        application_structural_fingerprint=raw[
            "applicationStructuralFingerprint"
        ],
        schema_identity_control_fingerprint=raw[
            "schemaIdentityControlFingerprint"
        ],
        seed_contract_digest=raw["seedContractDigest"],
        runtime_contract_version=raw["runtimeContractVersion"],
        checkpoint_codec_version=raw["checkpointCodecVersion"],
        capability_feature_digest=raw["capabilityFeatureDigest"],
        operator_auth_contract_version=raw[
            "operatorAuthContractVersion"
        ],
        canonicalization_version=raw["canonicalizationVersion"],
        manifest_digest=raw["manifestDigest"],
    )


_EXPECTED_V2_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "schemaFamily",
        "schemaRevision",
        "applicationStructuralFingerprint",
        "schemaIdentityControlFingerprint",
        "schemaSeedContractDigest",
        "runtimeContractVersion",
        "checkpointCodecVersion",
        "capabilityFeatureDigest",
        "operatorAuthContractVersion",
        "identityContractVersion",
        "schemaContractMaterialDigest",
        "runtimeIdentityDigests",
        "manifestDigest",
    }
)


def load_expected_schema_contract_v2(
    path: Path = DEFAULT_EXPECTED_SCHEMA_CONTRACT_V2_PATH,
) -> ExpectedSchemaContractV2:
    """Load the exact Plan 4 ``pre_ga_v1_0002`` identity contract."""
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise SchemaIdentityError("expected_schema_manifest_v2_invalid") from None
    if not isinstance(raw, dict) or set(raw) != _EXPECTED_V2_MANIFEST_KEYS:
        raise SchemaIdentityError("expected_schema_manifest_v2_invalid")
    digest_fields = (
        "applicationStructuralFingerprint",
        "schemaIdentityControlFingerprint",
        "schemaSeedContractDigest",
        "capabilityFeatureDigest",
        "schemaContractMaterialDigest",
        "manifestDigest",
    )
    if any(not _valid_sha256(raw.get(field)) for field in digest_fields):
        raise SchemaIdentityError("expected_schema_manifest_v2_invalid")
    runtime_identities = raw.get("runtimeIdentityDigests")
    if not isinstance(runtime_identities, dict) or set(runtime_identities) != {
        "development",
        "rehearsal",
        "production",
    } or any(not _valid_sha256(value) for value in runtime_identities.values()):
        raise SchemaIdentityError("expected_schema_manifest_v2_invalid")
    if (
        raw.get("schemaVersion") != 1
        or raw.get("schemaFamily") != SCHEMA_FAMILY
        or raw.get("schemaRevision") != NEXT_RESERVED_REVISION
        or raw.get("identityContractVersion") != SCHEMA_IDENTITY_CONTRACT_VERSION
        or type(raw.get("runtimeContractVersion")) is not int
        or raw["runtimeContractVersion"] <= 0
        or type(raw.get("checkpointCodecVersion")) is not int
        or raw["checkpointCodecVersion"] <= 0
        or not isinstance(raw.get("operatorAuthContractVersion"), str)
        or not raw["operatorAuthContractVersion"]
    ):
        raise SchemaIdentityError("expected_schema_manifest_v2_invalid")
    digest_payload = {key: value for key, value in raw.items() if key != "manifestDigest"}
    if sha256_canonical_json(digest_payload) != raw["manifestDigest"]:
        raise SchemaIdentityError("expected_schema_manifest_v2_digest_mismatch")
    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
    from app.assistant.durable.worker_registry import (
        RUNTIME_CONTRACT_VERSION,
        default_capability_feature_digest,
    )
    from app.assistant.runtime.system_seed.expected import SEED_CONTRACT_DIGEST
    from app.operator_auth.constants import OPERATOR_AUTH_CONTRACT_VERSION
    from app.release.contracts import schema_contract_material_digest

    expected_material = schema_contract_material_digest(
        schema_family=raw["schemaFamily"],
        schema_revision=raw["schemaRevision"],
        schema_application_fingerprint=raw["applicationStructuralFingerprint"],
        schema_control_fingerprint=raw["schemaIdentityControlFingerprint"],
        schema_identity_contract_version=raw["identityContractVersion"],
        schema_seed_contract_digest=raw["schemaSeedContractDigest"],
        schema_runtime_contract_version=raw["runtimeContractVersion"],
        schema_checkpoint_codec_version=raw["checkpointCodecVersion"],
        schema_capability_feature_digest=raw["capabilityFeatureDigest"],
        operator_auth_contract_version=raw["operatorAuthContractVersion"],
    )
    if (
        raw["schemaSeedContractDigest"] != SEED_CONTRACT_DIGEST
        or raw["runtimeContractVersion"] != RUNTIME_CONTRACT_VERSION
        or raw["checkpointCodecVersion"] != CURRENT_CHECKPOINT_CODEC_VERSION
        or raw["capabilityFeatureDigest"] != default_capability_feature_digest()
        or raw["operatorAuthContractVersion"] != OPERATOR_AUTH_CONTRACT_VERSION
        or raw["schemaContractMaterialDigest"] != expected_material
    ):
        raise SchemaIdentityError("expected_schema_manifest_v2_cross_reference_mismatch")
    for deployment_class in DeploymentClass:
        material = SchemaRuntimeIdentityMaterial(
            schema_family=raw["schemaFamily"],
            schema_revision=raw["schemaRevision"],
            structural_fingerprint=raw["applicationStructuralFingerprint"],
            seed_contract_digest=raw["schemaSeedContractDigest"],
            deployment_class=deployment_class,
            runtime_contract_version=raw["runtimeContractVersion"],
            checkpoint_codec_version=raw["checkpointCodecVersion"],
            capability_feature_digest=raw["capabilityFeatureDigest"],
            operator_auth_contract_version=raw["operatorAuthContractVersion"],
        )
        if raw["runtimeIdentityDigests"][deployment_class.value] != schema_runtime_identity_digest(material):
            raise SchemaIdentityError("expected_schema_manifest_v2_runtime_identity_mismatch")
    return ExpectedSchemaContractV2(
        schema_family=raw["schemaFamily"],
        schema_revision=raw["schemaRevision"],
        application_structural_fingerprint=raw["applicationStructuralFingerprint"],
        schema_identity_control_fingerprint=raw["schemaIdentityControlFingerprint"],
        seed_contract_digest=raw["schemaSeedContractDigest"],
        runtime_contract_version=raw["runtimeContractVersion"],
        checkpoint_codec_version=raw["checkpointCodecVersion"],
        capability_feature_digest=raw["capabilityFeatureDigest"],
        operator_auth_contract_version=raw["operatorAuthContractVersion"],
        identity_contract_version=raw["identityContractVersion"],
        schema_contract_material_digest=raw["schemaContractMaterialDigest"],
        runtime_identity_digests={str(key): str(value) for key, value in runtime_identities.items()},
        manifest_digest=raw["manifestDigest"],
    )


@dataclass(frozen=True)
class SchemaIdentityRecord:
    singleton_key: str
    schema_family: str
    schema_revision: str
    structural_fingerprint: str
    runtime_identity_digest: str
    seed_contract_digest: str
    deployment_class: DeploymentClass
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str
    identity_contract_version: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.singleton_key != SCHEMA_IDENTITY_SINGLETON_KEY:
            raise ValueError("schema identity singleton key is invalid")
        if type(self.identity_contract_version) is not int or (
            self.identity_contract_version <= 0
        ):
            raise ValueError("identity_contract_version must be positive")
        if not isinstance(self.created_at, datetime) or (
            self.created_at.tzinfo is None
        ):
            raise ValueError("created_at must be timezone-aware")
        if not isinstance(self.updated_at, datetime) or (
            self.updated_at.tzinfo is None
        ):
            raise ValueError("updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        material = self.to_identity_material()
        if len(self.runtime_identity_digest) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.runtime_identity_digest
        ):
            raise ValueError("runtime_identity_digest must be lowercase SHA-256")
        if not material.operator_auth_contract_version:
            raise ValueError("operator auth contract version must be nonempty")

    def to_identity_material(self) -> SchemaRuntimeIdentityMaterial:
        return SchemaRuntimeIdentityMaterial(
            schema_family=self.schema_family,
            schema_revision=self.schema_revision,
            structural_fingerprint=self.structural_fingerprint,
            seed_contract_digest=self.seed_contract_digest,
            deployment_class=self.deployment_class,
            runtime_contract_version=self.runtime_contract_version,
            checkpoint_codec_version=self.checkpoint_codec_version,
            capability_feature_digest=self.capability_feature_digest,
            operator_auth_contract_version=self.operator_auth_contract_version,
        )


def schema_runtime_identity_payload(
    material: SchemaRuntimeIdentityMaterial,
) -> dict[str, JsonValue]:
    """Return the exact canonical payload bound into a database identity."""
    return {
        "schemaFamily": material.schema_family,
        "schemaRevision": material.schema_revision,
        "structuralFingerprint": material.structural_fingerprint,
        "seedContractDigest": material.seed_contract_digest,
        "deploymentClass": material.deployment_class.value,
        "runtimeContractVersion": material.runtime_contract_version,
        "checkpointCodecVersion": material.checkpoint_codec_version,
        "capabilityFeatureDigest": material.capability_feature_digest,
        "operatorAuthContractVersion": material.operator_auth_contract_version,
    }


def schema_runtime_identity_digest(
    material: SchemaRuntimeIdentityMaterial,
) -> str:
    """Hash every runtime identity dimension using canonical JSON."""
    return sha256_canonical_json(schema_runtime_identity_payload(material))


def install_schema_identity_controls(connection) -> None:  # noqa: ANN001
    """Install the exact clean-root marker control plane on one connection."""
    try:
        connection.execute(
            text(
                "CREATE TABLE mindatlas_schema_identity ("
                "singleton_key VARCHAR(32) NOT NULL, "
                "schema_family VARCHAR(32) NOT NULL, "
                "schema_revision VARCHAR(64) NOT NULL, "
                "structural_fingerprint CHAR(64) NOT NULL, "
                "runtime_identity_digest CHAR(64) NOT NULL, "
                "seed_contract_digest CHAR(64) NOT NULL, "
                "deployment_class VARCHAR(16) NOT NULL, "
                "runtime_contract_version INTEGER NOT NULL, "
                "checkpoint_codec_version INTEGER NOT NULL, "
                "capability_feature_digest CHAR(64) NOT NULL, "
                "operator_auth_contract_version VARCHAR(64) NOT NULL, "
                "identity_contract_version INTEGER NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL, "
                "CONSTRAINT ck_schema_identity_singleton "
                "CHECK (singleton_key = 'current'), "
                "CONSTRAINT ck_schema_identity_family "
                "CHECK (schema_family = 'pre_ga_v1'), "
                "CONSTRAINT ck_schema_identity_deployment_class "
                "CHECK (deployment_class IN "
                "('development','rehearsal','production')), "
                "CONSTRAINT ck_schema_identity_digest_shapes "
                "CHECK (structural_fingerprint ~ '^[0-9a-f]{64}$' "
                "AND runtime_identity_digest ~ '^[0-9a-f]{64}$' "
                "AND seed_contract_digest ~ '^[0-9a-f]{64}$' "
                "AND capability_feature_digest ~ '^[0-9a-f]{64}$'), "
                "CONSTRAINT ck_schema_identity_positive_versions "
                "CHECK (runtime_contract_version > 0 "
                "AND checkpoint_codec_version > 0 "
                "AND identity_contract_version > 0), "
                "PRIMARY KEY (singleton_key)"
                ")"
            )
        )
        connection.execute(text(SCHEMA_IDENTITY_GUARD_SQL))
        connection.execute(text(SCHEMA_IDENTITY_TRIGGER_SQL))
    except SQLAlchemyError:
        raise SchemaIdentityError("marker_control_install_failed") from None


def insert_schema_identity(
    connection,  # noqa: ANN001
    *,
    deployment_class: DeploymentClass,
    expected: ExpectedSchemaContract,
) -> SchemaIdentityRecord:
    """Insert the one clean-root marker and return its validated record."""
    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
    from app.assistant.durable.worker_registry import (
        RUNTIME_CONTRACT_VERSION,
        default_capability_feature_digest,
    )
    from app.assistant.runtime.system_seed.expected import SEED_CONTRACT_DIGEST
    from app.operator_auth.constants import OPERATOR_AUTH_CONTRACT_VERSION

    material = SchemaRuntimeIdentityMaterial(
        schema_family=expected.schema_family,
        schema_revision=expected.schema_revision,
        structural_fingerprint=expected.application_structural_fingerprint,
        seed_contract_digest=SEED_CONTRACT_DIGEST,
        deployment_class=deployment_class,
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
        capability_feature_digest=default_capability_feature_digest(),
        operator_auth_contract_version=OPERATOR_AUTH_CONTRACT_VERSION,
    )
    try:
        connection.execute(
            text(
                "INSERT INTO mindatlas_schema_identity ("
                "singleton_key, schema_family, schema_revision, "
                "structural_fingerprint, runtime_identity_digest, "
                "seed_contract_digest, deployment_class, "
                "runtime_contract_version, checkpoint_codec_version, "
                "capability_feature_digest, operator_auth_contract_version, "
                "identity_contract_version, created_at, updated_at) VALUES ("
                ":singleton_key, :schema_family, :schema_revision, "
                ":structural_fingerprint, :runtime_identity_digest, "
                ":seed_contract_digest, :deployment_class, "
                ":runtime_contract_version, :checkpoint_codec_version, "
                ":capability_feature_digest, :operator_auth_contract_version, "
                ":identity_contract_version, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "singleton_key": SCHEMA_IDENTITY_SINGLETON_KEY,
                "schema_family": material.schema_family,
                "schema_revision": material.schema_revision,
                "structural_fingerprint": material.structural_fingerprint,
                "runtime_identity_digest": schema_runtime_identity_digest(
                    material
                ),
                "seed_contract_digest": material.seed_contract_digest,
                "deployment_class": material.deployment_class.value,
                "runtime_contract_version": material.runtime_contract_version,
                "checkpoint_codec_version": material.checkpoint_codec_version,
                "capability_feature_digest": material.capability_feature_digest,
                "operator_auth_contract_version": (
                    material.operator_auth_contract_version
                ),
                "identity_contract_version": SCHEMA_IDENTITY_CONTRACT_VERSION,
            },
        )
    except SQLAlchemyError:
        raise SchemaIdentityError("marker_insert_failed") from None
    return read_schema_identity(connection)


def read_schema_identity(db) -> SchemaIdentityRecord:  # noqa: ANN001
    """Read and validate the one family identity row without leaking row data."""
    try:
        rows = db.execute(
            text(
                "SELECT singleton_key, schema_family, schema_revision, "
                "structural_fingerprint, runtime_identity_digest, "
                "seed_contract_digest, deployment_class, "
                "runtime_contract_version, checkpoint_codec_version, "
                "capability_feature_digest, operator_auth_contract_version, "
                "identity_contract_version, created_at, updated_at "
                "FROM mindatlas_schema_identity "
                "WHERE :expected_singleton = 'current' "
                "ORDER BY singleton_key"
            ),
            {"expected_singleton": SCHEMA_IDENTITY_SINGLETON_KEY},
        ).mappings().all()
    except SQLAlchemyError:
        raise SchemaIdentityError("marker_unavailable") from None
    if len(rows) != 1:
        raise SchemaIdentityError(
            "marker_missing" if not rows else "marker_ambiguous"
        )
    row = rows[0]
    try:
        return SchemaIdentityRecord(
            singleton_key=row["singleton_key"],
            schema_family=row["schema_family"],
            schema_revision=row["schema_revision"],
            structural_fingerprint=row["structural_fingerprint"],
            runtime_identity_digest=row["runtime_identity_digest"],
            seed_contract_digest=row["seed_contract_digest"],
            deployment_class=DeploymentClass(row["deployment_class"]),
            runtime_contract_version=row["runtime_contract_version"],
            checkpoint_codec_version=row["checkpoint_codec_version"],
            capability_feature_digest=row["capability_feature_digest"],
            operator_auth_contract_version=row[
                "operator_auth_contract_version"
            ],
            identity_contract_version=row["identity_contract_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except (KeyError, TypeError, ValueError):
        raise SchemaIdentityError("marker_malformed") from None
