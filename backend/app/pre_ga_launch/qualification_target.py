"""Authoritative non-secret qualification-target construction.

The request layer never supplies any of these values.  The default provider
reads only deployment-owned identity material, the current schema marker, and
the active immutable runtime closure.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.operator_auth.constants import OPERATOR_AUTH_CONTRACT_VERSION
from app.release.contracts import (
    ReleaseQualificationTargetV1,
    runner_identity_digest,
    schema_contract_material_digest,
)
from app.release.scenarios import (
    REQUIRED_ASSERTION_SET_DIGEST,
    SCENARIO_SET_DIGEST,
)
from app.release.trust import ReleaseEvidenceTrustError, load_trust_set
from app.schema.contracts import DeploymentClass
from app.schema.identity import (
    SchemaIdentityError,
    load_expected_schema_contract_v2,
    read_schema_identity,
)


@dataclass(frozen=True)
class DeployedArtifactIdentity:
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str

    @classmethod
    def from_file(cls, path: Path) -> "DeployedArtifactIdentity":
        """Load an immutable deployment manifest at a server-owned path."""
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("deployment_identity_path_must_be_absolute_regular_file")
        try:
            if not path.is_file() or path.stat().st_mode & 0o222:
                raise ValueError("deployment_identity_file_must_be_read_only")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError):
            raise ValueError("deployment_identity_invalid") from None
        if not isinstance(raw, dict) or set(raw) != {
            "schemaVersion",
            "buildRevision",
            "imageSetDigest",
            "deployedArtifactSetDigest",
        }:
            raise ValueError("deployment_identity_invalid")
        if raw.get("schemaVersion") != 1:
            raise ValueError("deployment_identity_invalid")
        digest_re = re.compile(r"^[0-9a-f]{64}$")
        values = (
            raw.get("buildRevision"),
            raw.get("imageSetDigest"),
            raw.get("deployedArtifactSetDigest"),
        )
        if not isinstance(values[0], str) or not values[0].strip() or any(
            not isinstance(value, str) or digest_re.fullmatch(value) is None
            for value in values[1:]
        ):
            raise ValueError("deployment_identity_invalid")
        return cls(
            build_revision=values[0].strip(),
            image_set_digest=values[1],
            deployed_artifact_set_digest=values[2],
        )


class QualificationTargetProvider(Protocol):
    def current(self) -> ReleaseQualificationTargetV1: ...


class QualificationTargetUnavailable(RuntimeError):
    """Bounded server-side target failure; never contains target contents."""


class ServerOwnedQualificationTargetProvider:
    """Build the current production target from authoritative server ports."""

    def __init__(self, db: Session, *, settings: Any) -> None:
        self.db = db
        self.settings = settings

    def _artifact_identity(self) -> DeployedArtifactIdentity:
        raw_path = str(
            getattr(self.settings, "release_deployment_identity_path", "") or ""
        ).strip()
        if not raw_path:
            raise QualificationTargetUnavailable("deployed_artifact_identity_missing")
        try:
            identity = DeployedArtifactIdentity.from_file(Path(raw_path))
        except ValueError:
            raise QualificationTargetUnavailable("deployed_artifact_identity_invalid") from None
        build = str(getattr(self.settings, "app_build_revision", "") or "").strip()
        if not build or identity.build_revision != build:
            raise QualificationTargetUnavailable("deployed_artifact_build_mismatch")
        return identity

    def current(self) -> ReleaseQualificationTargetV1:
        try:
            expected = load_expected_schema_contract_v2()
            marker = read_schema_identity(self.db)
        except (SchemaIdentityError, ValueError):
            raise QualificationTargetUnavailable("schema_identity_unavailable") from None
        if marker.deployment_class is not DeploymentClass.PRODUCTION:
            raise QualificationTargetUnavailable("schema_deployment_class_mismatch")
        if (
            marker.schema_family != expected.schema_family
            or marker.schema_revision != expected.schema_revision
            or marker.structural_fingerprint != expected.application_structural_fingerprint
            or marker.identity_contract_version != expected.identity_contract_version
            or marker.seed_contract_digest != expected.seed_contract_digest
            or marker.runtime_contract_version != expected.runtime_contract_version
            or marker.checkpoint_codec_version != expected.checkpoint_codec_version
            or marker.capability_feature_digest != expected.capability_feature_digest
            or marker.operator_auth_contract_version != expected.operator_auth_contract_version
            or marker.runtime_identity_digest != expected.runtime_identity_digests["production"]
        ):
            raise QualificationTargetUnavailable("schema_identity_mismatch")
        material_digest = schema_contract_material_digest(
            schema_family=marker.schema_family,
            schema_revision=marker.schema_revision,
            schema_application_fingerprint=marker.structural_fingerprint,
            schema_control_fingerprint=expected.schema_identity_control_fingerprint,
            schema_identity_contract_version=marker.identity_contract_version,
            schema_seed_contract_digest=marker.seed_contract_digest,
            schema_runtime_contract_version=marker.runtime_contract_version,
            schema_checkpoint_codec_version=marker.checkpoint_codec_version,
            schema_capability_feature_digest=marker.capability_feature_digest,
            operator_auth_contract_version=marker.operator_auth_contract_version,
        )
        if material_digest != expected.schema_contract_material_digest:
            raise QualificationTargetUnavailable("schema_contract_material_mismatch")

        control = AssistantRuntimeRepository(self.db).get_control()
        rollout_id = getattr(control, "active_rollout_revision_id", None)
        if rollout_id is None:
            raise QualificationTargetUnavailable("rollout_inactive")
        try:
            closure = AssistantRuntimeClosureBuilder(self.db).build(
                rollout_revision_id=rollout_id,
                lock=True,
            )
        except Exception:
            raise QualificationTargetUnavailable("runtime_closure_drift") from None

        identity = self._artifact_identity()
        if identity.build_revision != closure.build_revision:
            raise QualificationTargetUnavailable("deployed_artifact_build_mismatch")
        from app.release.generated_lock_digests import DEPENDENCY_LOCK_SET_SHA256
        try:
            trust_path = Path(
                str(getattr(self.settings, "release_trust_set_path", "") or "")
            )
            trust = load_trust_set(trust_path)
        except (OSError, ValueError, ReleaseEvidenceTrustError):
            raise QualificationTargetUnavailable("release_trust_set_unavailable") from None

        runner_contract_version = 1
        runner_digest = runner_identity_digest(
            build_revision=identity.build_revision,
            runner_contract_version=runner_contract_version,
            scenario_set_digest=SCENARIO_SET_DIGEST,
            required_assertion_set_digest=REQUIRED_ASSERTION_SET_DIGEST,
        )
        from app.assistant.capability_calls.write_guard import (
            CREATE_ENTRY_CONTRACT_DIGEST,
            RECONCILIATION_CONTRACT_VERSION,
            WRITE_COHORT_DIGEST,
            WRITE_POLICY_DIGEST,
        )
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION

        return build_qualification_target(
            build_revision=identity.build_revision,
            image_set_digest=identity.image_set_digest,
            deployed_artifact_set_digest=identity.deployed_artifact_set_digest,
            schema_family=marker.schema_family,
            schema_revision=marker.schema_revision,
            schema_application_fingerprint=marker.structural_fingerprint,
            schema_control_fingerprint=expected.schema_identity_control_fingerprint,
            schema_identity_contract_version=marker.identity_contract_version,
            production_schema_deployment_class=DeploymentClass.PRODUCTION.value,
            schema_seed_contract_digest=marker.seed_contract_digest,
            schema_runtime_contract_version=marker.runtime_contract_version,
            schema_checkpoint_codec_version=marker.checkpoint_codec_version,
            schema_capability_feature_digest=marker.capability_feature_digest,
            production_schema_runtime_identity_digest=marker.runtime_identity_digest,
            schema_contract_material_digest=material_digest,
            operator_auth_contract_version=OPERATOR_AUTH_CONTRACT_VERSION,
            rollout_revision_id=closure.rollout_revision_id,
            rollout_revision_digest=closure.rollout_revision_digest,
            runtime_closure_digest=closure.closure_digest,
            profile_version_id=closure.profile_version_id,
            profile_content_digest=closure.profile_content_digest,
            model_id=closure.model_id,
            model_identity_digest=closure.model_identity_digest,
            package_closure_digest=closure.package_closure_digest,
            capability_closure_digest=closure.capability_closure_digest,
            seed_manifest_digest=closure.seed_manifest_digest,
            worker_runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            worker_checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
            worker_capability_feature_digest=default_capability_feature_digest(),
            create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
            write_policy_digest=WRITE_POLICY_DIGEST,
            write_cohort_digest=WRITE_COHORT_DIGEST,
            reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
            dependency_lock_set_digest=DEPENDENCY_LOCK_SET_SHA256,
            scenario_set_digest=SCENARIO_SET_DIGEST,
            required_assertion_set_digest=REQUIRED_ASSERTION_SET_DIGEST,
            runner_contract_version=runner_contract_version,
            runner_identity_digest=runner_digest,
            evidence_trust_set_digest=trust.trust_set_digest,
        )


def build_qualification_target(**values: Any) -> ReleaseQualificationTargetV1:
    """Build a target from server-owned values only."""
    return ReleaseQualificationTargetV1.build(**values)


__all__ = [
    "DeployedArtifactIdentity",
    "QualificationTargetProvider",
    "QualificationTargetUnavailable",
    "ServerOwnedQualificationTargetProvider",
    "build_qualification_target",
]
