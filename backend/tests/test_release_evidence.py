from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tarfile
from uuid import UUID

import pytest


_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
_DIGESTS = {
    "runtime": "1" * 64,
    "feature": "2" * 64,
    "schema": "3" * 64,
    "control": "4" * 64,
    "target": "5" * 64,
    "runner": "6" * 64,
    "trust": "7" * 64,
    "lock": "8" * 64,
    "scenario": "9" * 64,
    "assertions": "a" * 64,
}


def _infrastructure():
    from app.release.contracts import QualificationInfrastructureIdentityV1

    return QualificationInfrastructureIdentityV1.build(
        platform="linux/amd64",
        scripted_provider_image_digest="b" * 64,
        postgres_image_digest="c" * 64,
        minio_image_digest="d" * 64,
        minio_client_image_digest="e" * 64,
        release_images_lock_digest="f" * 64,
        compiler_image_digest="0" * 64,
        compiler_bootstrap_lock_digest="1" * 64,
    )


def _manifest(*, reverse: bool = False):
    from app.release.contracts import (
        ReleaseArtifactRefV1,
        ReleaseAssertionResultV1,
        ReleaseEvidenceManifestV1,
    )

    artifacts = (
        ReleaseArtifactRefV1(
            artifact_id="junit-main",
            artifact_kind="junit",
            media_type="application/xml",
            sha256_digest="a" * 64,
            byte_size=10,
        ),
        ReleaseArtifactRefV1(
            artifact_id="scenario-main",
            artifact_kind="scenario_trace",
            media_type="application/json",
            sha256_digest="b" * 64,
            byte_size=12,
        ),
    )
    assertions = (
        ReleaseAssertionResultV1(
            assertion_id="schema-identity",
            passed=True,
            safe_failure_code=None,
            observation_digest="c" * 64,
            artifact_digests=("a" * 64,),
            duration_ms=3,
        ),
        ReleaseAssertionResultV1(
            assertion_id="scenario-trace",
            passed=True,
            safe_failure_code=None,
            observation_digest="d" * 64,
            artifact_digests=("b" * 64,),
            duration_ms=4,
        ),
    )
    if reverse:
        artifacts = tuple(reversed(artifacts))
        assertions = tuple(reversed(assertions))
    return ReleaseEvidenceManifestV1.build(
        runner_contract_version=1,
        runner_identity_digest=_DIGESTS["runner"],
        release_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        evidence_kind="automated_qualification",
        qualification_target_digest=_DIGESTS["target"],
        build_revision="release-20260814",
        image_set_digest="1" * 64,
        deployed_artifact_set_digest="2" * 64,
        schema_family="pre_ga_v1",
        schema_revision="pre_ga_v1_0002",
        schema_application_fingerprint=_DIGESTS["schema"],
        schema_control_fingerprint=_DIGESTS["control"],
        schema_identity_contract_version=1,
        schema_contract_material_digest="3" * 64,
        schema_deployment_class="rehearsal",
        schema_seed_contract_digest="4" * 64,
        schema_runtime_contract_version=1,
        schema_checkpoint_codec_version=3,
        schema_capability_feature_digest=_DIGESTS["feature"],
        schema_runtime_identity_digest=_DIGESTS["runtime"],
        operator_auth_contract_version="operator-auth-v1",
        rollout_revision_id=UUID("00000000-0000-0000-0000-000000000002"),
        rollout_revision_digest="5" * 64,
        runtime_closure_digest="6" * 64,
        profile_version_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_content_digest="7" * 64,
        model_id=UUID("00000000-0000-0000-0000-000000000004"),
        model_identity_digest="8" * 64,
        package_closure_digest="9" * 64,
        capability_closure_digest="a" * 64,
        seed_manifest_digest="b" * 64,
        worker_runtime_contract_version=1,
        worker_checkpoint_codec_version=3,
        worker_capability_feature_digest="c" * 64,
        create_entry_contract_digest="d" * 64,
        write_policy_digest="e" * 64,
        write_cohort_digest="f" * 64,
        reconciliation_contract_version=1,
        dependency_lock_set_digest=_DIGESTS["lock"],
        scenario_set_digest=_DIGESTS["scenario"],
        required_assertion_set_digest=_DIGESTS["assertions"],
        evidence_trust_set_digest=_DIGESTS["trust"],
        qualification_infrastructure_identity=_infrastructure(),
        started_at=_NOW,
        ended_at=_NOW + timedelta(seconds=2),
        assertion_results=assertions,
        artifact_refs=artifacts,
    )


def test_manifest_digest_and_attestation_are_canonical() -> None:
    from app.release.contracts import canonical_release_manifest_bytes
    from app.release.trust import ReleaseEvidenceSigner, verify_release_attestation

    first = _manifest()
    second = _manifest(reverse=True)
    assert canonical_release_manifest_bytes(first) == canonical_release_manifest_bytes(second)
    assert first.manifest_digest == second.manifest_digest

    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="qualification-key",
        private_key_bytes=bytes(range(32)),
    )
    attestation = signer.sign(first)
    assert attestation.domain == "mindatlas:release-evidence:v1"
    assert verify_release_attestation(first, attestation, signer.trust_key()).manifest_digest == first.manifest_digest


def test_manifest_rejects_unknown_fields_bad_digest_and_unreachable_artifacts() -> None:
    from app.release.contracts import ReleaseEvidenceManifestV1

    payload = _manifest().model_dump(mode="json", by_alias=True)
    payload["unexpected"] = True
    with pytest.raises(ValueError):
        ReleaseEvidenceManifestV1.model_validate(payload)

    payload = _manifest().model_dump(mode="json", by_alias=True)
    payload["manifestDigest"] = "0" * 64
    with pytest.raises(ValueError, match="manifest_digest"):
        ReleaseEvidenceManifestV1.model_validate(payload)

    with pytest.raises(ValueError, match="reachable"):
        _manifest_with_unreachable_artifact()


def _manifest_with_unreachable_artifact():
    from app.release.contracts import ReleaseArtifactRefV1

    manifest = _manifest()
    refs = tuple(manifest.artifact_refs) + (
        ReleaseArtifactRefV1(
            artifact_id="orphan",
            artifact_kind="metric_summary",
            media_type="application/json",
            sha256_digest="e" * 64,
            byte_size=1,
        ),
    )
    payload = manifest.model_dump(mode="json", by_alias=False)
    payload["artifact_refs"] = refs
    payload.pop("manifest_digest", None)
    return type(manifest).build(**payload)


def test_content_addressed_store_is_idempotent_and_rejects_collisions(tmp_path: Path) -> None:
    from app.release.evidence import ContentAddressedEvidenceStore, ReleaseEvidenceCollision

    store = ContentAddressedEvidenceStore(tmp_path)
    digest = store.put_artifact(b"safe-artifact", media_type="application/json")
    assert store.read_artifact(digest) == b"safe-artifact"
    assert store.put_artifact(b"safe-artifact", media_type="application/json") == digest
    with pytest.raises(ReleaseEvidenceCollision):
        store.put_artifact(b"different", media_type="application/json", digest=digest)


def test_trust_set_rejects_revoked_or_wrong_domain() -> None:
    from app.release.trust import (
        ReleaseEvidenceTrustError,
        ReleaseTrustSetV1,
        verify_release_attestation,
    )

    manifest = _manifest()
    signer = __import__("app.release.trust", fromlist=["ReleaseEvidenceSigner"]).ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="qualification-key",
        private_key_bytes=bytes(range(32)),
    )
    attestation = signer.sign(manifest)
    key = signer.trust_key(revoked=True)
    trust_set = ReleaseTrustSetV1.build((key,))
    with pytest.raises(ReleaseEvidenceTrustError, match="revoked"):
        verify_release_attestation(manifest, attestation, trust_set)


def test_canonical_manifest_serialization_has_no_float_or_secret_markers() -> None:
    from app.release.contracts import canonical_release_manifest_bytes

    encoded = canonical_release_manifest_bytes(_manifest())
    assert b"password" not in encoded.lower()
    assert b"authorization" not in encoded.lower()
    json.loads(encoded)


def test_release_runner_rejects_sensitive_observation_payload(tmp_path: Path) -> None:
    from app.release.evidence import ContentAddressedEvidenceStore, ReleaseEvidenceIntegrityError
    from app.release.runner import ReleaseObservation, ReleaseRunner
    from app.release.trust import ReleaseEvidenceSigner

    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="qualification-key",
        private_key_bytes=bytes(range(32)),
    )
    runner = ReleaseRunner(
        store=ContentAddressedEvidenceStore(tmp_path),
        signer=signer,
    )
    with pytest.raises(ReleaseEvidenceIntegrityError, match="sensitive"):
        runner.observation_result(
            ReleaseObservation(
                assertion_id="safe-assertion",
                passed=True,
                safe_failure_code=None,
                payload={"prompt": "must never be evidence"},
            )
        )
