from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
OPERATOR_ID = UUID("00000000-0000-0000-0000-0000000000a0")
SESSION_ID = UUID("00000000-0000-0000-0000-0000000000b0")


def _identity(*, deployment_class: str) -> str:
    from app.schema.contracts import DeploymentClass, SchemaRuntimeIdentityMaterial
    from app.schema.identity import schema_runtime_identity_digest

    return schema_runtime_identity_digest(
        SchemaRuntimeIdentityMaterial(
            schema_family="pre_ga_v1",
            schema_revision="pre_ga_v1_0002",
            structural_fingerprint="3" * 64,
            seed_contract_digest="4" * 64,
            deployment_class=DeploymentClass(deployment_class),
            runtime_contract_version=1,
            checkpoint_codec_version=3,
            capability_feature_digest="5" * 64,
            operator_auth_contract_version="operator-auth-v1",
        )
    )


def _fixture(tmp_path: Path):
    from app.release.contracts import (
        ContentAddressedEvidenceRef,
        QualificationInfrastructureIdentityV1,
        ReleaseArtifactRefV1,
        ReleaseAssertionResultV1,
        ReleaseEvidenceManifestV1,
        ReleaseQualificationTargetV1,
        schema_contract_material_digest,
    )
    from app.release.evidence import ContentAddressedEvidenceStore
    from app.release.runner import ReleaseObservation, ReleaseRunner
    from app.release.trust import ReleaseEvidenceSigner, ReleaseTrustSetV1, attestation_object_digest

    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="qualification-key", private_key_bytes=bytes(range(32))
    )
    trust_set = ReleaseTrustSetV1.build((signer.trust_key(),))
    store = ContentAddressedEvidenceStore(tmp_path / "evidence")
    artifact_bytes = b"safe release artifact"
    artifact_digest = store.put_artifact(artifact_bytes, media_type="application/json")
    artifact = ReleaseArtifactRefV1(
        artifact_id="scenario-trace",
        artifact_kind="scenario_trace",
        media_type="application/json",
        sha256_digest=artifact_digest,
        byte_size=len(artifact_bytes),
    )
    infrastructure = QualificationInfrastructureIdentityV1.build(
        platform="linux/amd64",
        scripted_provider_image_digest="6" * 64,
        postgres_image_digest="7" * 64,
        minio_image_digest="8" * 64,
        minio_client_image_digest="9" * 64,
        release_images_lock_digest="a" * 64,
        compiler_image_digest="b" * 64,
        compiler_bootstrap_lock_digest="c" * 64,
    )
    shared = {
        "runner_contract_version": 1,
        "runner_identity_digest": "d" * 64,
        "build_revision": "release-20260814",
        "image_set_digest": "e" * 64,
        "deployed_artifact_set_digest": "f" * 64,
        "schema_family": "pre_ga_v1",
        "schema_revision": "pre_ga_v1_0002",
        "schema_application_fingerprint": "3" * 64,
        "schema_control_fingerprint": "2" * 64,
        "schema_identity_contract_version": 1,
        "schema_contract_material_digest": schema_contract_material_digest(
            schema_family="pre_ga_v1",
            schema_revision="pre_ga_v1_0002",
            schema_application_fingerprint="3" * 64,
            schema_control_fingerprint="2" * 64,
            schema_identity_contract_version=1,
            schema_seed_contract_digest="4" * 64,
            schema_runtime_contract_version=1,
            schema_checkpoint_codec_version=3,
            schema_capability_feature_digest="5" * 64,
            operator_auth_contract_version="operator-auth-v1",
        ),
        "schema_seed_contract_digest": "4" * 64,
        "schema_runtime_contract_version": 1,
        "schema_checkpoint_codec_version": 3,
        "schema_capability_feature_digest": "5" * 64,
        "operator_auth_contract_version": "operator-auth-v1",
        "rollout_revision_id": UUID("00000000-0000-0000-0000-000000000a20"),
        "rollout_revision_digest": "1" * 64,
        "runtime_closure_digest": "2" * 64,
        "profile_version_id": UUID("00000000-0000-0000-0000-000000000a21"),
        "profile_content_digest": "3" * 64,
        "model_id": UUID("00000000-0000-0000-0000-000000000a22"),
        "model_identity_digest": "4" * 64,
        "package_closure_digest": "5" * 64,
        "capability_closure_digest": "6" * 64,
        "seed_manifest_digest": "7" * 64,
        "worker_runtime_contract_version": 1,
        "worker_checkpoint_codec_version": 3,
        "worker_capability_feature_digest": "8" * 64,
        "create_entry_contract_digest": "9" * 64,
        "write_policy_digest": "a" * 64,
        "write_cohort_digest": "b" * 64,
        "reconciliation_contract_version": 1,
        "dependency_lock_set_digest": "c" * 64,
        "scenario_set_digest": "d" * 64,
        "required_assertion_set_digest": "e" * 64,
        "evidence_trust_set_digest": trust_set.trust_set_digest,
        "qualification_infrastructure_identity": infrastructure,
    }
    target = ReleaseQualificationTargetV1.build(
        **{
            **{key: value for key, value in shared.items() if key not in {"qualification_infrastructure_identity"}},
            "production_schema_deployment_class": "production",
            "production_schema_runtime_identity_digest": _identity(deployment_class="production"),
        }
    )

    manifests = []
    for kind, run_id in (
        ("automated_qualification", UUID("00000000-0000-0000-0000-000000000030")),
        ("production_rehearsal", UUID("00000000-0000-0000-0000-000000000031")),
    ):
        values = {
            **shared,
            "release_run_id": run_id,
            "evidence_kind": kind,
            "qualification_target_digest": target.qualification_target_digest,
            "schema_deployment_class": "rehearsal",
            "schema_runtime_identity_digest": _identity(deployment_class="rehearsal"),
            "started_at": NOW,
            "ended_at": NOW + timedelta(seconds=1),
            "assertion_results": (
                ReleaseAssertionResultV1(
                    assertion_id="scenario-trace",
                    passed=True,
                    safe_failure_code=None,
                    observation_digest="f" * 64,
                    artifact_digests=(artifact_digest,),
                    duration_ms=1,
                ),
            ),
            "artifact_refs": (artifact,),
        }
        manifest = ReleaseEvidenceManifestV1.build(**values)
        attestation = signer.sign(manifest)
        store.put_evidence_object(manifest, attestation)
        manifests.append(
            ContentAddressedEvidenceRef(
                evidence_kind=kind,
                manifest_digest=manifest.manifest_digest,
                attestation_digest=attestation_object_digest(attestation),
            )
        )
    return target, store, trust_set, tuple(manifests)


def _operator_session(db):
    from app.common.time import utcnow
    from app.operator_auth.models import OperatorAccount

    account = OperatorAccount(
        id=OPERATOR_ID,
        singleton_key="operator",
        role="operator",
        password_hash="test-password-hash",
        password_changed_at=utcnow(),
    )
    db.add(account)
    db.flush()
    from app.operator_auth.contracts import OperatorPrincipal

    return OperatorPrincipal(
        operator_id=OPERATOR_ID,
        role="operator",
        session_id=SESSION_ID,
    )


def test_create_candidate_is_server_derived_and_exactly_replayable(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import CreatePreGaLaunchCandidateRequest
    from app.pre_ga_launch.models import PreGaLaunchControl
    from app.pre_ga_launch.service import PreGaLaunchService
    from tests._db import make_session

    db = make_session()
    try:
        principal = _operator_session(db)
        db.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
        target, store, trust_set, refs = _fixture(tmp_path)
        service = PreGaLaunchService(
            db,
            evidence_store=store,
            trust_set=trust_set,
            target_provider=lambda: target,
        )
        request = CreatePreGaLaunchCandidateRequest(
            automated_evidence_ref=refs[0],
            rehearsal_evidence_ref=refs[1],
                request_id=UUID("00000000-0000-0000-0000-0000000000c0"),
            reason="qualification",
        )
        first = service.create_candidate(request, principal=principal)
        replay = service.create_candidate(request, principal=principal)
        assert first.candidate.id == replay.candidate.id
        assert replay.replayed is True
        assert first.candidate.passed is True
        assert first.candidate.safe_failure_codes == []
        assert first.candidate.subject_digest == first.candidate.subject_json["subjectDigest"]
    finally:
        db.close()


def test_mismatching_signed_evidence_persists_failed_candidate(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import CreatePreGaLaunchCandidateRequest
    from app.pre_ga_launch.models import PreGaLaunchControl
    from app.pre_ga_launch.service import PreGaLaunchService
    from tests._db import make_session

    db = make_session()
    try:
        principal = _operator_session(db)
        db.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
        target, store, trust_set, refs = _fixture(tmp_path)
        from app.release.contracts import ReleaseEvidenceManifestV1
        from app.release.trust import ReleaseEvidenceSigner, attestation_object_digest

        raw = store.read_evidence_object(refs[0].evidence_kind, refs[0].manifest_digest)
        payload = __import__("json").loads(raw)
        payload["manifest"]["buildRevision"] = "different-build"
        payload["manifest"].pop("manifestDigest")
        mismatched = ReleaseEvidenceManifestV1.build(**payload["manifest"])
        signer = ReleaseEvidenceSigner.from_private_key_bytes(
            key_id="qualification-key", private_key_bytes=bytes(range(32))
        )
        attestation = signer.sign(mismatched)
        store.put_evidence_object(mismatched, attestation)
        from app.release.contracts import ContentAddressedEvidenceRef

        bad_ref = ContentAddressedEvidenceRef(
            evidence_kind=refs[0].evidence_kind,
            manifest_digest=mismatched.manifest_digest,
            attestation_digest=attestation_object_digest(attestation),
        )
        service = PreGaLaunchService(
            db,
            evidence_store=store,
            trust_set=trust_set,
            target_provider=lambda: target,
        )
        request = CreatePreGaLaunchCandidateRequest(
            automated_evidence_ref=bad_ref,
            rehearsal_evidence_ref=refs[1],
                request_id=UUID("00000000-0000-0000-0000-0000000000c1"),
            reason="mismatch",
        )
        result = service.create_candidate(request, principal=principal)
        assert result.candidate.passed is False
        assert "launch_evidence_automated_build_revision_mismatch" in result.candidate.safe_failure_codes
    finally:
        db.close()


def test_consumed_authorization_ignores_expiry_and_operational_snapshot(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import (
        ConsumePreGaLaunchCandidateRequest,
        CreatePreGaLaunchCandidateRequest,
    )
    from app.pre_ga_launch.models import PreGaLaunchControl
    from app.pre_ga_launch.service import PreGaLaunchService
    from tests._db import make_session

    db = make_session()
    try:
        principal = _operator_session(db)
        db.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
        target, store, trust_set, refs = _fixture(tmp_path)
        service = PreGaLaunchService(
            db,
            evidence_store=store,
            trust_set=trust_set,
            target_provider=lambda: target,
        )
        created = service.create_candidate(
            CreatePreGaLaunchCandidateRequest(
                automated_evidence_ref=refs[0],
                rehearsal_evidence_ref=refs[1],
                    request_id=UUID("00000000-0000-0000-0000-0000000000c2"),
                reason="consume",
            ),
            principal=principal,
        )
        consumed = service.consume_candidate(
            created.candidate.id,
            ConsumePreGaLaunchCandidateRequest(
                expected_control_revision=0,
                request_id=UUID("00000000-0000-0000-0000-0000000000c3"),
                reason="launch",
            ),
            principal=principal,
        )
        assert consumed.control.revision == 1
        assert service.evaluate_current_launch().launched is True
        # Evaluation is durable-subject based; candidate expiry is only a
        # consumption-time rule.
        service.repo.database_now = lambda: NOW + timedelta(hours=48)
        assert service.evaluate_current_launch().launched is True
    finally:
        db.close()


def test_invalid_evidence_is_not_persisted(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import CreatePreGaLaunchCandidateRequest
    from app.pre_ga_launch.service import PreGaLaunchError, PreGaLaunchService
    from app.release.contracts import ContentAddressedEvidenceRef
    from tests._db import make_session

    db = make_session()
    try:
        principal = _operator_session(db)
        target, store, trust_set, _ = _fixture(tmp_path)
        service = PreGaLaunchService(
            db,
            evidence_store=store,
            trust_set=trust_set,
            target_provider=lambda: target,
        )
        with pytest.raises(PreGaLaunchError, match="launch_evidence_invalid"):
            service.create_candidate(
                CreatePreGaLaunchCandidateRequest(
                    automated_evidence_ref=ContentAddressedEvidenceRef(
                        evidence_kind="automated_qualification",
                        manifest_digest="0" * 64,
                        attestation_digest="1" * 64,
                    ),
                    rehearsal_evidence_ref=ContentAddressedEvidenceRef(
                        evidence_kind="production_rehearsal",
                        manifest_digest="0" * 64,
                        attestation_digest="1" * 64,
                    ),
                    request_id=uuid4(),
                    reason="invalid",
                ),
                principal=principal,
            )
    finally:
        db.close()
