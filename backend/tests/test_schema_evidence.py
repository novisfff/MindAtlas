from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.schema.canonical import canonical_json_bytes
from scripts.verify_pre_ga_schema import (
    SchemaEvidence,
    SchemaVerificationError,
    _validate_exit_proof,
)


COMMITTED_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "superpowers"
    / "evidence"
    / "2026-07-28-pre-ga-clean-baseline.json"
)

ALLOWED_EVIDENCE_KEYS = {
    "schemaVersion",
    "schemaFamily",
    "schemaRevision",
    "applicationStructuralFingerprint",
    "schemaIdentityControlFingerprint",
    "runtimeIdentityDigest",
    "seedContractDigest",
    "deploymentClass",
    "runtimeContractVersion",
    "checkpointCodecVersion",
    "capabilityFeatureDigest",
    "operatorAuthContractVersion",
    "oldRevisionCount",
    "oldFinalHead",
    "archiveManifestDigest",
    "archiveVerified",
    "exclusionObjectCount",
    "exclusionManifestDigest",
    "logicalEquivalenceVerified",
    "freshUpgradeVerified",
    "testOnlyDowngradeGuardVerified",
    "guardedRebaselineMatrixVerified",
    "wrongFamilyRejected",
    "workerClaimRejectedOnDrift",
    "deployAutoStampAbsent",
    "postgresMajor",
    "buildRevision",
    "verificationDigest",
}


def sha256_canonical_json(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _sample_schema_evidence() -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "schemaFamily": "pre_ga_v1",
        "schemaRevision": "pre_ga_v1_0001",
        "applicationStructuralFingerprint": "a" * 64,
        "schemaIdentityControlFingerprint": "b" * 64,
        "runtimeIdentityDigest": "c" * 64,
        "seedContractDigest": "d" * 64,
        "deploymentClass": "rehearsal",
        "runtimeContractVersion": 1,
        "checkpointCodecVersion": 3,
        "capabilityFeatureDigest": "e" * 64,
        "operatorAuthContractVersion": "operator-auth-v1",
        "oldRevisionCount": 60,
        "oldFinalHead": "b6e2d4f8a901",
        "archiveManifestDigest": "f" * 64,
        "archiveVerified": True,
        "exclusionObjectCount": 27,
        "exclusionManifestDigest": "0" * 64,
        "logicalEquivalenceVerified": True,
        "freshUpgradeVerified": True,
        "testOnlyDowngradeGuardVerified": True,
        "guardedRebaselineMatrixVerified": True,
        "wrongFamilyRejected": True,
        "workerClaimRejectedOnDrift": True,
        "deployAutoStampAbsent": True,
        "postgresMajor": 15,
        "buildRevision": "ci-build",
    }
    return {
        **payload,
        "verificationDigest": sha256_canonical_json(payload),
    }


def test_schema_evidence_is_allowlisted_and_self_digesting() -> None:
    payload = _sample_schema_evidence()
    assert set(payload) == ALLOWED_EVIDENCE_KEYS
    claimed = payload.pop("verificationDigest")
    assert claimed == sha256_canonical_json(payload)


def test_release_evidence_is_only_emitted_by_ci() -> None:
    assert not COMMITTED_EVIDENCE_PATH.exists()


def test_schema_evidence_model_forbids_extra_fields() -> None:
    with pytest.raises(ValueError):
        SchemaEvidence.model_validate({"unexpected": "value"})


def test_exit_proof_requires_executed_checks_and_self_digest() -> None:
    payload = {
        "schemaVersion": 1,
        "deploymentClass": "rehearsal",
        "buildRevision": "ci-build",
        "checks": [],
    }
    with pytest.raises(SchemaVerificationError, match="^exit_proof_invalid$"):
        _validate_exit_proof(
            payload,
            deployment_class="rehearsal",
            build_revision="ci-build",
        )


def test_exit_proof_derives_flags_only_from_observed_matrix() -> None:
    checks = [
        {
            "name": "fresh_upgrade",
            "result": "pass",
            "observations": {
                "beforeHead": None,
                "afterHead": "pre_ga_v1_0001",
                "markerRevision": "pre_ga_v1_0001",
            },
        },
        {
            "name": "test_only_downgrade_guard",
            "result": "pass",
            "observations": {
                "rejectedError": "schema_test_downgrade_forbidden",
                "headAfterRejected": "pre_ga_v1_0001",
                "emptyAfterAcknowledged": True,
            },
        },
        {
            "name": "guarded_rebaseline_matrix",
            "result": "pass",
            "observations": {
                "beforeHead": "b6e2d4f8a901",
                "afterHead": "pre_ga_v1_0001",
                "retainedDataUnchanged": True,
                "rehearsalSuccess": True,
                "developmentSuccess": True,
                "idempotentSecondApply": True,
                "snapshotRollback": True,
                "reportPathRejectedBeforeDatabase": True,
                "parserHasNoBypass": True,
                "rejectionsNoMutation": True,
                "rejectionCodes": [
                    "data_invariant_failed",
                    "database_deployment_identity_missing",
                    "deployment_identity_mismatch",
                    "database_deployment_identity_unknown",
                    "legacy_exclusion_data_present",
                    "pre_squash_fingerprint_mismatch",
                    "pre_squash_head_mismatch",
                    "production_rebaseline_forbidden",
                    "rebaseline_lock_unavailable",
                    "retained_data_changed",
                ],
                "rejectionScenarios": [
                    {
                        "name": "production",
                        "error": "production_rebaseline_forbidden",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "missing_identity",
                        "error": "database_deployment_identity_missing",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "unknown_identity",
                        "error": "database_deployment_identity_unknown",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "mismatched_identity",
                        "error": "deployment_identity_mismatch",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "missing_head",
                        "error": "pre_squash_head_mismatch",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "multiple_heads",
                        "error": "pre_squash_head_mismatch",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "legacy_business_row",
                        "error": "legacy_exclusion_data_present",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "non_inert_legacy_control",
                        "error": "legacy_exclusion_data_present",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "source_schema_drift",
                        "error": "pre_squash_fingerprint_mismatch",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "extra_legacy_prefix_object",
                        "error": "pre_squash_fingerprint_mismatch",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "data_invariant",
                        "error": "data_invariant_failed",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "lock_contention",
                        "error": "rebaseline_lock_unavailable",
                        "sourceStateUnchanged": True,
                    },
                    {
                        "name": "retained_snapshot_rollback",
                        "error": "retained_data_changed",
                        "sourceStateUnchanged": True,
                    },
                ],
            },
        },
        {
            "name": "wrong_family_rejected",
            "result": "pass",
            "observations": {"error": "schema_incompatible", "mutationBlocked": True},
        },
        {
            "name": "worker_claim_rejected_on_drift",
            "result": "pass",
            "observations": {"error": "schema_incompatible", "mutationBlocked": True},
        },
        {
            "name": "deploy_auto_stamp_absent",
            "result": "pass",
            "observations": {"sourceContainsAutoStamp": False},
        },
    ]
    unsigned = {
        "schemaVersion": 1,
        "deploymentClass": "rehearsal",
        "buildRevision": "ci-build",
        "checks": checks,
    }
    payload = {
        **unsigned,
        "proofDigest": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }

    assert _validate_exit_proof(
        payload,
        deployment_class="rehearsal",
        build_revision="ci-build",
    ) == {
        "fresh_upgrade": True,
        "test_only_downgrade_guard": True,
        "guarded_rebaseline_matrix": True,
        "wrong_family_rejected": True,
        "worker_claim_rejected_on_drift": True,
        "deploy_auto_stamp_absent": True,
    }

    payload["checks"][0]["observations"]["afterHead"] = "fake"
    with pytest.raises(SchemaVerificationError, match="^exit_proof_invalid$"):
        _validate_exit_proof(
            payload,
            deployment_class="rehearsal",
            build_revision="ci-build",
        )


def test_exit_proof_rejects_an_incomplete_rebaseline_rejection_matrix() -> None:
    checks = [
        {
            "name": "fresh_upgrade",
            "result": "pass",
            "observations": {
                "beforeHead": None,
                "afterHead": "pre_ga_v1_0001",
                "markerRevision": "pre_ga_v1_0001",
            },
        },
        {
            "name": "test_only_downgrade_guard",
            "result": "pass",
            "observations": {
                "rejectedError": "schema_test_downgrade_forbidden",
                "headAfterRejected": "pre_ga_v1_0001",
                "emptyAfterAcknowledged": True,
            },
        },
        {
            "name": "guarded_rebaseline_matrix",
            "result": "pass",
            "observations": {
                "beforeHead": "b6e2d4f8a901",
                "afterHead": "pre_ga_v1_0001",
                "retainedDataUnchanged": True,
                "rehearsalSuccess": True,
                "developmentSuccess": True,
                "idempotentSecondApply": True,
                "snapshotRollback": True,
                "reportPathRejectedBeforeDatabase": True,
                "parserHasNoBypass": True,
                "rejectionsNoMutation": True,
                "rejectionCodes": [
                    "database_deployment_identity_unknown",
                    "legacy_exclusion_data_present",
                    "pre_squash_fingerprint_mismatch",
                    "pre_squash_head_mismatch",
                    "production_rebaseline_forbidden",
                    "rebaseline_lock_unavailable",
                ],
                "rejectionScenarios": [],
            },
        },
        {
            "name": "wrong_family_rejected",
            "result": "pass",
            "observations": {"error": "schema_incompatible", "mutationBlocked": True},
        },
        {
            "name": "worker_claim_rejected_on_drift",
            "result": "pass",
            "observations": {"error": "schema_incompatible", "mutationBlocked": True},
        },
        {
            "name": "deploy_auto_stamp_absent",
            "result": "pass",
            "observations": {"sourceContainsAutoStamp": False},
        },
    ]
    unsigned = {
        "schemaVersion": 1,
        "deploymentClass": "rehearsal",
        "buildRevision": "ci-build",
        "checks": checks,
    }
    payload = {
        **unsigned,
        "proofDigest": hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest(),
    }

    with pytest.raises(SchemaVerificationError, match="^exit_proof_invalid$"):
        _validate_exit_proof(
            payload,
            deployment_class="rehearsal",
            build_revision="ci-build",
        )
