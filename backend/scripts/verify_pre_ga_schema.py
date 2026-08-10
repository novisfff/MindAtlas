"""Fail-closed verification for the pre-GA clean schema lineage."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import warnings

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schema.application_contract import (
    LogicalApplicationContractError,
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import (
    SchemaComparisonError,
    canonical_json_bytes,
    normalize_document,
    structural_fingerprint,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    DeploymentClass,
)
from app.schema.exclusions import (
    LEGACY_TABLE_NAMES,
    expected_legacy_object_keys,
)
from app.schema.identity import (
    SchemaIdentityError,
    load_expected_schema_contract,
    read_schema_identity,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import (
    SchemaManifestError,
    load_exclusion_manifest,
    load_pre_squash_snapshot,
)
from scripts.archive_pre_ga_lineage import (
    ArchiveLineageError,
    DEFAULT_ARCHIVE_PATHS,
    check_archive,
)


_LEGACY_CONTROL_TABLE = "assistant_runtime_rollout_control"
_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_KNOWN_CLI_WARNING = (
    "The default value of `allowed_objects` will change in a future version. "
    "Pass an explicit value (e.g., allowed_objects='messages' or "
    "allowed_objects='core') to suppress this warning."
)


class SchemaVerificationError(RuntimeError):
    """Bounded verification failure safe for automation and logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class EquivalenceVerification:
    old_application_fingerprint: str
    clean_application_fingerprint: str
    clean_control_fingerprint: str
    exclusion_count: int


@dataclass(frozen=True)
class FreshVerification:
    application_fingerprint: str
    control_fingerprint: str
    deployment_class: str
    exclusion_count: int


class SchemaEvidence(BaseModel):
    """Allowlisted, secret-free final verification evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: int
    schemaFamily: str
    schemaRevision: str
    applicationStructuralFingerprint: str
    schemaIdentityControlFingerprint: str
    runtimeIdentityDigest: str
    seedContractDigest: str
    deploymentClass: str
    runtimeContractVersion: int
    checkpointCodecVersion: int
    capabilityFeatureDigest: str
    operatorAuthContractVersion: str
    oldRevisionCount: int
    oldFinalHead: str
    archiveManifestDigest: str
    archiveVerified: bool = Field()
    exclusionObjectCount: int
    exclusionManifestDigest: str
    logicalEquivalenceVerified: bool = Field()
    freshUpgradeVerified: bool = Field()
    testOnlyDowngradeGuardVerified: bool = Field()
    guardedRebaselineMatrixVerified: bool = Field()
    wrongFamilyRejected: bool = Field()
    workerClaimRejectedOnDrift: bool = Field()
    deployAutoStampAbsent: bool = Field()
    postgresMajor: int
    buildRevision: str
    verificationDigest: str

    @field_validator(
        "archiveVerified",
        "logicalEquivalenceVerified",
        "freshUpgradeVerified",
        "testOnlyDowngradeGuardVerified",
        "guardedRebaselineMatrixVerified",
        "wrongFamilyRejected",
        "workerClaimRejectedOnDrift",
        "deployAutoStampAbsent",
    )
    @classmethod
    def _require_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("verification evidence must be true")
        return value

    @field_validator(
        "applicationStructuralFingerprint",
        "schemaIdentityControlFingerprint",
        "runtimeIdentityDigest",
        "seedContractDigest",
        "capabilityFeatureDigest",
        "archiveManifestDigest",
        "exclusionManifestDigest",
        "verificationDigest",
    )
    @classmethod
    def _require_digest(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("evidence digest must be lowercase SHA-256")
        return value


_EXIT_PROOF_CHECKS = frozenset(
    {
        "fresh_upgrade",
        "test_only_downgrade_guard",
        "guarded_rebaseline_matrix",
        "wrong_family_rejected",
        "worker_claim_rejected_on_drift",
        "deploy_auto_stamp_absent",
    }
)


def _validate_exit_proof(
    payload: object,
    *,
    deployment_class: str,
    build_revision: str,
) -> dict[str, bool]:
    """Validate proof observations produced by the release-gate runner.

    The final evidence must not manufacture booleans for suites that were not
    run.  Each flag is therefore derived from a self-digesting, exact set of
    named checks and their observed database/runtime facts.
    """
    if not isinstance(payload, dict) or set(payload) != {
        "schemaVersion",
        "deploymentClass",
        "buildRevision",
        "checks",
        "proofDigest",
    }:
        raise SchemaVerificationError("exit_proof_invalid")
    if payload["schemaVersion"] != 1:
        raise SchemaVerificationError("exit_proof_invalid")
    if payload["deploymentClass"] != deployment_class:
        raise SchemaVerificationError("exit_proof_invalid")
    if payload["buildRevision"] != build_revision:
        raise SchemaVerificationError("exit_proof_invalid")
    claimed_digest = payload["proofDigest"]
    unsigned = {key: value for key, value in payload.items() if key != "proofDigest"}
    if (
        not isinstance(claimed_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", claimed_digest) is None
        or _evidence_digest(unsigned) != claimed_digest
    ):
        raise SchemaVerificationError("exit_proof_invalid")
    checks = payload["checks"]
    if not isinstance(checks, list) or len(checks) != len(_EXIT_PROOF_CHECKS):
        raise SchemaVerificationError("exit_proof_invalid")
    by_name: dict[str, dict[str, object]] = {}
    for item in checks:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "result",
            "observations",
        }:
            raise SchemaVerificationError("exit_proof_invalid")
        name = item["name"]
        observations = item["observations"]
        if (
            not isinstance(name, str)
            or name in by_name
            or name not in _EXIT_PROOF_CHECKS
            or item["result"] != "pass"
            or not isinstance(observations, dict)
        ):
            raise SchemaVerificationError("exit_proof_invalid")
        by_name[name] = observations
    if set(by_name) != _EXIT_PROOF_CHECKS:
        raise SchemaVerificationError("exit_proof_invalid")

    required_observations: dict[str, tuple[str, ...]] = {
        "fresh_upgrade": ("beforeHead", "afterHead", "markerRevision"),
        "test_only_downgrade_guard": (
            "rejectedError",
            "headAfterRejected",
            "emptyAfterAcknowledged",
        ),
        "guarded_rebaseline_matrix": (
            "beforeHead",
            "afterHead",
            "retainedDataUnchanged",
            "developmentSuccess",
            "rejectionsNoMutation",
            "rejectionCodes",
        ),
        "wrong_family_rejected": ("error", "mutationBlocked"),
        "worker_claim_rejected_on_drift": ("error", "mutationBlocked"),
        "deploy_auto_stamp_absent": ("sourceContainsAutoStamp",),
    }
    for name, fields in required_observations.items():
        observations = by_name[name]
        if any(field not in observations for field in fields):
            raise SchemaVerificationError("exit_proof_invalid")
    if not (
        by_name["fresh_upgrade"]["beforeHead"] is None
        and by_name["fresh_upgrade"]["afterHead"] == CLEAN_ROOT_REVISION
        and by_name["fresh_upgrade"]["markerRevision"] == CLEAN_ROOT_REVISION
    ):
        raise SchemaVerificationError("exit_proof_invalid")
    if not (
        by_name["test_only_downgrade_guard"]["rejectedError"]
        == "schema_test_downgrade_forbidden"
        and by_name["test_only_downgrade_guard"]["headAfterRejected"]
        == CLEAN_ROOT_REVISION
        and by_name["test_only_downgrade_guard"]["emptyAfterAcknowledged"] is True
    ):
        raise SchemaVerificationError("exit_proof_invalid")
    rejection_codes = by_name["guarded_rebaseline_matrix"]["rejectionCodes"]
    if not (
        by_name["guarded_rebaseline_matrix"]["beforeHead"] == PRE_SQUASH_HEAD
        and by_name["guarded_rebaseline_matrix"]["afterHead"]
        == CLEAN_ROOT_REVISION
        and by_name["guarded_rebaseline_matrix"]["retainedDataUnchanged"] is True
        and by_name["guarded_rebaseline_matrix"]["developmentSuccess"] is True
        and by_name["guarded_rebaseline_matrix"]["rejectionsNoMutation"] is True
        and isinstance(rejection_codes, list)
        and {
            "production_rebaseline_forbidden",
            "database_deployment_identity_unknown",
            "legacy_exclusion_data_present",
            "pre_squash_fingerprint_mismatch",
            "pre_squash_head_mismatch",
            "rebaseline_lock_unavailable",
        }
        <= set(rejection_codes)
    ):
        raise SchemaVerificationError("exit_proof_invalid")
    for name in ("wrong_family_rejected", "worker_claim_rejected_on_drift"):
        if not (
            by_name[name]["error"] == "schema_incompatible"
            and by_name[name]["mutationBlocked"] is True
        ):
            raise SchemaVerificationError("exit_proof_invalid")
    if by_name["deploy_auto_stamp_absent"]["sourceContainsAutoStamp"] is not False:
        raise SchemaVerificationError("exit_proof_invalid")
    return {name: True for name in _EXIT_PROOF_CHECKS}


_EVIDENCE_SECRET_PATTERN = re.compile(
    r"(?i)(?:postgres(?:ql)?://|https?://|password|token|cookie|"
    r"authorization|-----begin(?: [^-]+)? private key-----|sk-[A-Za-z0-9])"
)


def _assert_safe_evidence(value: object) -> None:
    if isinstance(value, str) and _EVIDENCE_SECRET_PATTERN.search(value):
        raise SchemaVerificationError("evidence_content_forbidden")
    if isinstance(value, dict):
        for item in value.values():
            _assert_safe_evidence(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_safe_evidence(item)


def _atomic_write_evidence(path: Path, evidence: SchemaEvidence) -> None:
    payload = evidence.model_dump()
    _assert_safe_evidence(payload)
    encoded = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            raise SchemaVerificationError("evidence_read_failed") from None
        try:
            existing_canonical = canonical_json_bytes(
                json.loads(existing.decode("utf-8"))
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            raise SchemaVerificationError("evidence_existing_content_invalid") from None
        if existing_canonical != encoded:
            raise SchemaVerificationError("evidence_existing_content_mismatch")
        return
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise SchemaVerificationError("evidence_write_failed") from None


def _evidence_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _read_database_url(env_name: str) -> str:
    if _ENV_NAME_PATTERN.fullmatch(env_name) is None:
        raise SchemaVerificationError("database_url_env_invalid")
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise SchemaVerificationError("database_url_missing")
    return value


def _is_known_cli_warning(item: warnings.WarningMessage) -> bool:
    return (
        item.category.__module__ == "langchain_core._api.deprecation"
        and item.category.__name__ == "LangChainPendingDeprecationWarning"
        and str(item.message) == _KNOWN_CLI_WARNING
    )


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _read_single_head(connection, expected: str, safe_code: str) -> None:  # noqa: ANN001
    rows = tuple(
        str(value)
        for value in connection.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    )
    if rows != (expected,):
        raise SchemaVerificationError(safe_code)


def _application_schemas(connection) -> tuple[str, ...]:  # noqa: ANN001
    schemas = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT nspname FROM pg_catalog.pg_namespace "
                "WHERE nspname <> 'information_schema' "
                "AND nspname NOT LIKE 'pg_catalog' "
                "AND nspname NOT LIKE 'pg_toast%' "
                "AND nspname NOT LIKE 'pg_temp%' "
                "ORDER BY nspname"
            )
        ).scalars()
    )
    if "public" not in schemas:
        raise SchemaVerificationError("application_namespace_missing")
    return schemas


def _require_inert_legacy_state(connection) -> None:  # noqa: ANN001
    for table_name in LEGACY_TABLE_NAMES:
        if table_name == _LEGACY_CONTROL_TABLE:
            continue
        count = int(
            connection.execute(
                text(f'SELECT count(*) FROM "{table_name}"')
            ).scalar_one()
        )
        if count != 0:
            raise SchemaVerificationError("legacy_exclusion_data_present")

    controls = connection.execute(
        text(
            "SELECT singleton_key, active_rollout_revision_id, state_revision "
            "FROM assistant_runtime_rollout_control"
        )
    ).mappings().all()
    if len(controls) != 1:
        raise SchemaVerificationError("legacy_exclusion_data_present")
    control = controls[0]
    if (
        control["singleton_key"] != "singleton"
        or control["active_rollout_revision_id"] is not None
        or control["state_revision"] != 0
    ):
        raise SchemaVerificationError("legacy_exclusion_data_present")


def _read_old_database(database_url: str):  # noqa: ANN201
    engine = None
    try:
        engine = create_engine(
            _sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            _read_single_head(
                connection,
                PRE_SQUASH_HEAD,
                "pre_squash_head_mismatch",
            )
            _require_inert_legacy_state(connection)
            return PostgresCatalogReader(
                connection,
                schemas=_application_schemas(connection),
            ).read_document()
    except SchemaVerificationError:
        raise
    except (CatalogReadError, SQLAlchemyError):
        raise SchemaVerificationError("schema_source_unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()


def _read_clean_database(database_url: str):  # noqa: ANN201
    engine = None
    try:
        engine = create_engine(
            _sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            _read_single_head(
                connection,
                CLEAN_ROOT_REVISION,
                "clean_root_head_mismatch",
            )
            document = PostgresCatalogReader(
                connection,
                schemas=_application_schemas(connection),
            ).read_document()
            marker = read_schema_identity(connection)
            return document, marker
    except SchemaVerificationError:
        raise
    except SchemaIdentityError as exc:
        raise SchemaVerificationError(exc.safe_code) from None
    except (CatalogReadError, SQLAlchemyError):
        raise SchemaVerificationError("clean_schema_unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()


def _verify_clean_contract(
    clean_document,  # noqa: ANN001
    marker,  # noqa: ANN001
    *,
    exclusions,  # noqa: ANN001
    logical_contract,  # noqa: ANN001
    expected,  # noqa: ANN001
    required_deployment_class: DeploymentClass | None,
) -> tuple[FreshVerification, CanonicalSchemaDocument]:
    normalize_document(
        clean_document,
        manifest=exclusions,
        side="clean",
    )
    logical_clean = project_logical_application_document(
        clean_document,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )

    logical_keys = {item.key for item in logical_clean.objects}
    clean_controls = tuple(
        item for item in clean_document.objects if item.key not in logical_keys
    )
    control_document = CanonicalSchemaDocument(
        canonicalization_version=1,
        postgres_major=clean_document.postgres_major,
        objects=clean_controls,
    )
    control_fingerprint = structural_fingerprint(control_document)
    if control_fingerprint != expected.schema_identity_control_fingerprint:
        raise SchemaVerificationError("schema_control_contract_drift")

    logical_bytes = canonical_json_bytes(logical_clean.to_payload())
    expected_bytes = canonical_json_bytes(
        logical_contract.logical_application_document.to_payload()
    )
    application_fingerprint = structural_fingerprint(logical_clean)
    if (
        logical_bytes != expected_bytes
        or application_fingerprint
        != logical_contract.logical_application_fingerprint
        or application_fingerprint
        != expected.application_structural_fingerprint
    ):
        raise SchemaVerificationError("logical_application_schema_difference")

    if (
        marker.schema_family != SCHEMA_FAMILY
        or marker.schema_revision != CLEAN_ROOT_REVISION
        or marker.structural_fingerprint != application_fingerprint
        or marker.seed_contract_digest != expected.seed_contract_digest
        or marker.runtime_contract_version != expected.runtime_contract_version
        or marker.checkpoint_codec_version
        != expected.checkpoint_codec_version
        or marker.capability_feature_digest
        != expected.capability_feature_digest
        or marker.operator_auth_contract_version
        != expected.operator_auth_contract_version
        or marker.identity_contract_version
        != SCHEMA_IDENTITY_CONTRACT_VERSION
        or (
            required_deployment_class is not None
            and marker.deployment_class is not required_deployment_class
        )
        or marker.runtime_identity_digest
        != schema_runtime_identity_digest(marker.to_identity_material())
    ):
        raise SchemaVerificationError("marker_contract_mismatch")

    return (
        FreshVerification(
            application_fingerprint=application_fingerprint,
            control_fingerprint=control_fingerprint,
            deployment_class=marker.deployment_class.value,
            exclusion_count=len(exclusions.objects),
        ),
        logical_clean,
    )


def verify_equivalence(
    *,
    old_database_url: str,
    clean_database_url: str,
) -> EquivalenceVerification:
    """Prove old-head and clean-root logical application bytes are identical."""
    try:
        exclusions = load_exclusion_manifest()
        snapshot = load_pre_squash_snapshot()
        logical_contract = load_logical_application_contract()
        expected = load_expected_schema_contract()

        expected_exclusion_keys = {
            CanonicalObjectKey(*parts)
            for parts in expected_legacy_object_keys()
        }
        if set(exclusions.object_keys) != expected_exclusion_keys:
            raise SchemaVerificationError(
                "legacy_exclusion_allowlist_mismatch"
            )

        old_document = _read_old_database(old_database_url)
        if (
            structural_fingerprint(old_document)
            != exclusions.source_structural_fingerprint
            or canonical_json_bytes(old_document.to_payload())
            != canonical_json_bytes(snapshot.source_document.to_payload())
        ):
            raise SchemaVerificationError("pre_squash_schema_drift")
        old_without_legacy = normalize_document(
            old_document,
            manifest=exclusions,
            side="old",
        )
        logical_old = project_logical_application_document(
            old_without_legacy,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )

        clean_document, marker = _read_clean_database(clean_database_url)
        clean_proof, logical_clean = _verify_clean_contract(
            clean_document,
            marker,
            exclusions=exclusions,
            logical_contract=logical_contract,
            expected=expected,
            required_deployment_class=None,
        )

        old_bytes = canonical_json_bytes(logical_old.to_payload())
        clean_bytes = canonical_json_bytes(logical_clean.to_payload())
        expected_bytes = canonical_json_bytes(
            logical_contract.logical_application_document.to_payload()
        )
        if old_bytes != clean_bytes or old_bytes != expected_bytes:
            raise SchemaVerificationError(
                "logical_application_schema_difference"
            )

        old_fingerprint = structural_fingerprint(logical_old)
        clean_fingerprint = structural_fingerprint(logical_clean)
        expected_fingerprint = logical_contract.logical_application_fingerprint
        if (
            old_fingerprint != expected_fingerprint
            or clean_fingerprint != expected_fingerprint
            or expected.application_structural_fingerprint
            != expected_fingerprint
        ):
            raise SchemaVerificationError(
                "logical_application_schema_difference"
            )

        return EquivalenceVerification(
            old_application_fingerprint=old_fingerprint,
            clean_application_fingerprint=clean_fingerprint,
            clean_control_fingerprint=clean_proof.control_fingerprint,
            exclusion_count=len(exclusions.objects),
        )
    except SchemaVerificationError:
        raise
    except (
        LogicalApplicationContractError,
        SchemaComparisonError,
        SchemaIdentityError,
        SchemaManifestError,
    ) as exc:
        raise SchemaVerificationError(exc.safe_code) from None


def verify_fresh(
    *,
    clean_database_url: str,
    deployment_class: str,
) -> FreshVerification:
    try:
        try:
            required_deployment_class = DeploymentClass(deployment_class)
        except ValueError:
            raise SchemaVerificationError(
                "schema_deployment_class_invalid"
            ) from None
        exclusions = load_exclusion_manifest()
        logical_contract = load_logical_application_contract()
        expected = load_expected_schema_contract()
        clean_document, marker = _read_clean_database(clean_database_url)
        proof, _logical_clean = _verify_clean_contract(
            clean_document,
            marker,
            exclusions=exclusions,
            logical_contract=logical_contract,
            expected=expected,
            required_deployment_class=required_deployment_class,
        )
        return proof
    except SchemaVerificationError:
        raise
    except (
        LogicalApplicationContractError,
        SchemaComparisonError,
        SchemaIdentityError,
        SchemaManifestError,
    ) as exc:
        raise SchemaVerificationError(exc.safe_code) from None


def verify_exit(
    *,
    fresh_database_url: str,
    rebaseline_database_url: str,
    deployment_class: str,
    output: Path,
    proof_file: Path | None = None,
) -> SchemaEvidence:
    """Run the fixed final verification contract and atomically attest it."""
    try:
        required = DeploymentClass(deployment_class)
    except ValueError:
        raise SchemaVerificationError("schema_deployment_class_invalid") from None

    try:
        archive_manifest = check_archive(paths=DEFAULT_ARCHIVE_PATHS)
        exclusions = load_exclusion_manifest()
        expected = load_expected_schema_contract()
        logical_contract = load_logical_application_contract()
        clean_document, clean_marker = _read_clean_database(fresh_database_url)
        fresh = verify_fresh(
            clean_database_url=fresh_database_url,
            deployment_class=required.value,
        )
        # The second database is the post-rebaseline/clean-family proof target.
        verify_fresh(
            clean_database_url=rebaseline_database_url,
            deployment_class=required.value,
        )
        if clean_document.postgres_major != 15:
            raise SchemaVerificationError("postgres_major_unsupported")
        if clean_marker.runtime_identity_digest != schema_runtime_identity_digest(
            clean_marker.to_identity_material()
        ):
            raise SchemaVerificationError("runtime_identity_mismatch")
        if (
            clean_marker.schema_family != SCHEMA_FAMILY
            or clean_marker.schema_revision != CLEAN_ROOT_REVISION
            or clean_marker.deployment_class is not required
        ):
            raise SchemaVerificationError("marker_contract_mismatch")
        if (
            logical_contract.logical_application_fingerprint
            != expected.application_structural_fingerprint
        ):
            raise SchemaVerificationError("expected_manifest_invalid")
        root = BACKEND_ROOT / "alembic" / "versions" / (
            "pre_ga_v1_0001_clean_baseline.py"
        )
        if not root.is_file() or "alembic stamp" in root.read_text("utf-8"):
            raise SchemaVerificationError("clean_root_artifact_invalid")
        if "alembic stamp" in (
            REPO_ROOT / "deploy" / "migrate.sh"
        ).read_text("utf-8"):
            raise SchemaVerificationError("deploy_auto_stamp_present")
        build_revision = os.environ.get("APP_BUILD_REVISION", "").strip()
        if not build_revision or build_revision in {"development", "unknown"}:
            raise SchemaVerificationError("build_identity_invalid")
        if proof_file is None:
            raise SchemaVerificationError("exit_proof_missing")
        try:
            proof_payload = json.loads(proof_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise SchemaVerificationError("exit_proof_missing") from None
        proof_flags = _validate_exit_proof(
            proof_payload,
            deployment_class=required.value,
            build_revision=build_revision,
        )

        payload: dict[str, object] = {
            "schemaVersion": 1,
            "schemaFamily": expected.schema_family,
            "schemaRevision": expected.schema_revision,
            "applicationStructuralFingerprint": expected.application_structural_fingerprint,
            "schemaIdentityControlFingerprint": expected.schema_identity_control_fingerprint,
            "runtimeIdentityDigest": clean_marker.runtime_identity_digest,
            "seedContractDigest": expected.seed_contract_digest,
            "deploymentClass": required.value,
            "runtimeContractVersion": expected.runtime_contract_version,
            "checkpointCodecVersion": expected.checkpoint_codec_version,
            "capabilityFeatureDigest": expected.capability_feature_digest,
            "operatorAuthContractVersion": expected.operator_auth_contract_version,
            "oldRevisionCount": archive_manifest.revision_count,
            "oldFinalHead": archive_manifest.original_final_head,
            "archiveManifestDigest": archive_manifest.manifest_digest,
            "archiveVerified": True,
            "exclusionObjectCount": len(exclusions.objects),
            "exclusionManifestDigest": exclusions.manifest_digest,
            "logicalEquivalenceVerified": fresh.application_fingerprint
            == expected.application_structural_fingerprint,
            "freshUpgradeVerified": proof_flags["fresh_upgrade"],
            "testOnlyDowngradeGuardVerified": proof_flags[
                "test_only_downgrade_guard"
            ],
            "guardedRebaselineMatrixVerified": proof_flags[
                "guarded_rebaseline_matrix"
            ],
            "wrongFamilyRejected": proof_flags["wrong_family_rejected"],
            "workerClaimRejectedOnDrift": proof_flags[
                "worker_claim_rejected_on_drift"
            ],
            "deployAutoStampAbsent": proof_flags["deploy_auto_stamp_absent"],
            "postgresMajor": clean_document.postgres_major,
            "buildRevision": build_revision,
        }
        if any(value is not True for key, value in payload.items() if key.endswith("Verified") or key.endswith("Rejected") or key == "deployAutoStampAbsent"):
            raise SchemaVerificationError("verification_incomplete")
        digest = _evidence_digest(payload)
        evidence = SchemaEvidence(**payload, verificationDigest=digest)
        _atomic_write_evidence(output, evidence)
        return evidence
    except SchemaVerificationError:
        raise
    except (ArchiveLineageError, OSError, ValueError) as exc:
        safe_code = getattr(exc, "safe_code", "evidence_verification_failed")
        raise SchemaVerificationError(safe_code) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    equivalence = subparsers.add_parser("equivalence", allow_abbrev=False)
    equivalence.add_argument("--old-database-url-env", required=True)
    equivalence.add_argument("--clean-database-url-env", required=True)
    fresh = subparsers.add_parser("fresh", allow_abbrev=False)
    fresh.add_argument("--clean-database-url-env", required=True)
    fresh.add_argument("--deployment-class", required=True)
    runtime = subparsers.add_parser("runtime", allow_abbrev=False)
    runtime.add_argument("--database-url-env", required=True)
    exit_mode = subparsers.add_parser("exit", allow_abbrev=False)
    exit_mode.add_argument("--fresh-database-url-env", required=True)
    exit_mode.add_argument("--rebaseline-database-url-env", required=True)
    exit_mode.add_argument("--deployment-class", required=True)
    exit_mode.add_argument("--output", required=True)
    exit_mode.add_argument("--proof-file", required=True)
    args = parser.parse_args(argv)

    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            if args.mode == "equivalence":
                result = verify_equivalence(
                    old_database_url=_read_database_url(
                        args.old_database_url_env
                    ),
                    clean_database_url=_read_database_url(
                        args.clean_database_url_env
                    ),
                )
                success_message = (
                    "schema_equivalence_ok "
                    f"old_head={PRE_SQUASH_HEAD} "
                    f"clean_head={CLEAN_ROOT_REVISION} "
                    "application_fingerprint="
                    f"{result.clean_application_fingerprint} "
                    f"control_fingerprint={result.clean_control_fingerprint} "
                    f"exclusions={result.exclusion_count}"
                )
            elif args.mode in {"fresh", "runtime"}:
                deployment_class = (
                    os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "").strip()
                    if args.mode == "runtime"
                    else args.deployment_class
                )
                fresh_result = verify_fresh(
                    clean_database_url=_read_database_url(
                        args.database_url_env
                        if args.mode == "runtime"
                        else args.clean_database_url_env
                    ),
                    deployment_class=deployment_class,
                )
                success_message = (
                    "schema_fresh_ok "
                    f"clean_head={CLEAN_ROOT_REVISION} "
                    f"deployment_class={fresh_result.deployment_class} "
                    "application_fingerprint="
                    f"{fresh_result.application_fingerprint} "
                    f"control_fingerprint={fresh_result.control_fingerprint} "
                    f"exclusions={fresh_result.exclusion_count}"
                )
            else:
                evidence = verify_exit(
                    fresh_database_url=_read_database_url(
                        args.fresh_database_url_env
                    ),
                    rebaseline_database_url=_read_database_url(
                        args.rebaseline_database_url_env
                    ),
                    deployment_class=args.deployment_class,
                    output=Path(args.output).resolve(),
                    proof_file=Path(args.proof_file).resolve(),
                )
                success_message = (
                    "schema_exit_ok "
                    f"schema_revision={evidence.schemaRevision} "
                    f"verification_digest={evidence.verificationDigest}"
                )
        if any(
            not _is_known_cli_warning(item) for item in caught_warnings
        ):
            raise SchemaVerificationError("verification_warning_unexpected")
        print(success_message)
        return 0
    except SchemaVerificationError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
