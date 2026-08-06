from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.schema.canonical import canonical_json_bytes
from scripts.verify_pre_ga_schema import SchemaEvidence


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
