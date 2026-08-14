"""Production launch-subject derivation and evidence comparison."""

from __future__ import annotations

from typing import Any

from app.pre_ga_launch.contracts import PreGaLaunchSubjectV1
from app.release.contracts import ReleaseEvidenceManifestV1, ReleaseQualificationTargetV1
from app.schema.contracts import DeploymentClass, SchemaRuntimeIdentityMaterial
from app.schema.identity import schema_runtime_identity_digest


class LaunchSubjectError(ValueError):
    safe_code = "launch_subject_invalid"


def _target_manifest_match(target: ReleaseQualificationTargetV1, manifest: ReleaseEvidenceManifestV1) -> None:
    pairs = {
        "qualification_target_digest": target.qualification_target_digest,
        "build_revision": target.build_revision,
        "image_set_digest": target.image_set_digest,
        "deployed_artifact_set_digest": target.deployed_artifact_set_digest,
        "schema_family": target.schema_family,
        "schema_revision": target.schema_revision,
        "schema_application_fingerprint": target.schema_application_fingerprint,
        "schema_control_fingerprint": target.schema_control_fingerprint,
        "schema_identity_contract_version": target.schema_identity_contract_version,
        "schema_contract_material_digest": target.schema_contract_material_digest,
        "schema_seed_contract_digest": target.schema_seed_contract_digest,
        "schema_runtime_contract_version": target.schema_runtime_contract_version,
        "schema_checkpoint_codec_version": target.schema_checkpoint_codec_version,
        "schema_capability_feature_digest": target.schema_capability_feature_digest,
        "operator_auth_contract_version": target.operator_auth_contract_version,
        "rollout_revision_id": target.rollout_revision_id,
        "rollout_revision_digest": target.rollout_revision_digest,
        "runtime_closure_digest": target.runtime_closure_digest,
        "profile_version_id": target.profile_version_id,
        "profile_content_digest": target.profile_content_digest,
        "model_id": target.model_id,
        "model_identity_digest": target.model_identity_digest,
        "package_closure_digest": target.package_closure_digest,
        "capability_closure_digest": target.capability_closure_digest,
        "seed_manifest_digest": target.seed_manifest_digest,
        "worker_runtime_contract_version": target.worker_runtime_contract_version,
        "worker_checkpoint_codec_version": target.worker_checkpoint_codec_version,
        "worker_capability_feature_digest": target.worker_capability_feature_digest,
        "create_entry_contract_digest": target.create_entry_contract_digest,
        "write_policy_digest": target.write_policy_digest,
        "write_cohort_digest": target.write_cohort_digest,
        "reconciliation_contract_version": target.reconciliation_contract_version,
        "dependency_lock_set_digest": target.dependency_lock_set_digest,
        "scenario_set_digest": target.scenario_set_digest,
        "required_assertion_set_digest": target.required_assertion_set_digest,
        "runner_contract_version": target.runner_contract_version,
        "runner_identity_digest": target.runner_identity_digest,
        "evidence_trust_set_digest": target.evidence_trust_set_digest,
    }
    for field, expected in pairs.items():
        actual = getattr(manifest, field)
        if str(actual) != str(expected):
            raise LaunchSubjectError(f"launch_evidence_{field}_mismatch")


def build_launch_subject(
    target: ReleaseQualificationTargetV1,
    automated_manifest: ReleaseEvidenceManifestV1,
    rehearsal_manifest: ReleaseEvidenceManifestV1,
) -> PreGaLaunchSubjectV1:
    if automated_manifest.evidence_kind != "automated_qualification" or rehearsal_manifest.evidence_kind != "production_rehearsal":
        raise LaunchSubjectError("launch_evidence_kind_mismatch")
    if automated_manifest.schema_deployment_class != "rehearsal" or rehearsal_manifest.schema_deployment_class != "rehearsal":
        raise LaunchSubjectError("launch_evidence_deployment_class_mismatch")
    _target_manifest_match(target, automated_manifest)
    _target_manifest_match(target, rehearsal_manifest)
    for manifest in (automated_manifest, rehearsal_manifest):
        expected_runtime_identity = schema_runtime_identity_digest(
            SchemaRuntimeIdentityMaterial(
                schema_family=manifest.schema_family,
                schema_revision=manifest.schema_revision,
                structural_fingerprint=manifest.schema_application_fingerprint,
                seed_contract_digest=manifest.schema_seed_contract_digest,
                deployment_class=DeploymentClass.REHEARSAL,
                runtime_contract_version=manifest.schema_runtime_contract_version,
                checkpoint_codec_version=manifest.schema_checkpoint_codec_version,
                capability_feature_digest=manifest.schema_capability_feature_digest,
                operator_auth_contract_version=manifest.operator_auth_contract_version,
            )
        )
        if manifest.schema_runtime_identity_digest != expected_runtime_identity:
            raise LaunchSubjectError("launch_evidence_schema_runtime_identity_mismatch")
    if automated_manifest.schema_runtime_identity_digest != rehearsal_manifest.schema_runtime_identity_digest:
        raise LaunchSubjectError("launch_evidence_schema_runtime_identity_mismatch")
    if automated_manifest.qualification_infrastructure_identity != rehearsal_manifest.qualification_infrastructure_identity:
        raise LaunchSubjectError("qualification_infrastructure_mismatch")
    return build_launch_subject_snapshot(target, automated_manifest, rehearsal_manifest)


def build_launch_subject_snapshot(
    target: ReleaseQualificationTargetV1,
    automated_manifest: ReleaseEvidenceManifestV1,
    rehearsal_manifest: ReleaseEvidenceManifestV1,
) -> PreGaLaunchSubjectV1:
    """Bind target/evidence digests after semantic validation or for a failed candidate.

    A failed candidate still records one canonical current observation. The
    caller must validate object signatures before calling this helper; this
    function deliberately does not turn invalid evidence into trusted launch
    authority.
    """
    return PreGaLaunchSubjectV1.build(
        qualification_target_digest=target.qualification_target_digest,
        build_revision=target.build_revision,
        image_set_digest=target.image_set_digest,
        deployed_artifact_set_digest=target.deployed_artifact_set_digest,
        schema_family=target.schema_family,
        schema_revision=target.schema_revision,
        schema_runtime_identity_digest=target.production_schema_runtime_identity_digest,
        deployment_class=target.production_schema_deployment_class,
        operator_auth_contract_version=target.operator_auth_contract_version,
        rollout_revision_id=target.rollout_revision_id,
        rollout_revision_digest=target.rollout_revision_digest,
        runtime_closure_digest=target.runtime_closure_digest,
        profile_version_id=target.profile_version_id,
        profile_content_digest=target.profile_content_digest,
        model_id=target.model_id,
        model_identity_digest=target.model_identity_digest,
        package_closure_digest=target.package_closure_digest,
        capability_closure_digest=target.capability_closure_digest,
        seed_manifest_digest=target.seed_manifest_digest,
        worker_runtime_contract_version=target.worker_runtime_contract_version,
        worker_checkpoint_codec_version=target.worker_checkpoint_codec_version,
        worker_capability_feature_digest=target.worker_capability_feature_digest,
        create_entry_contract_digest=target.create_entry_contract_digest,
        write_policy_digest=target.write_policy_digest,
        write_cohort_digest=target.write_cohort_digest,
        reconciliation_contract_version=target.reconciliation_contract_version,
        dependency_lock_set_digest=target.dependency_lock_set_digest,
        automated_evidence_manifest_digest=automated_manifest.manifest_digest,
        rehearsal_evidence_manifest_digest=rehearsal_manifest.manifest_digest,
        scenario_set_digest=target.scenario_set_digest,
        required_assertion_set_digest=target.required_assertion_set_digest,
        runner_contract_version=target.runner_contract_version,
        runner_identity_digest=target.runner_identity_digest,
        evidence_trust_set_digest=target.evidence_trust_set_digest,
    )


__all__ = [
    "LaunchSubjectError",
    "build_launch_subject",
    "build_launch_subject_snapshot",
]
