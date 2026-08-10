from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.schema.canonical import canonical_json_bytes
from scripts.verify_pre_ga_schema import (
    SchemaEvidence,
    SchemaVerificationError,
    _validate_exit_proof,
)


EVIDENCE_PATH = (
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


def test_schema_evidence_is_allowlisted_and_self_digesting() -> None:
    payload = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    assert set(payload) == ALLOWED_EVIDENCE_KEYS
    claimed = payload.pop("verificationDigest")
    assert claimed == sha256_canonical_json(payload)


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
                "developmentSuccess": True,
                "rejectionsNoMutation": True,
                "rejectionCodes": [
                    "database_deployment_identity_unknown",
                    "legacy_exclusion_data_present",
                    "pre_squash_fingerprint_mismatch",
                    "pre_squash_head_mismatch",
                    "production_rebaseline_forbidden",
                    "rebaseline_lock_unavailable",
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
