from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

import pytest


def _target():
    from app.release.contracts import ReleaseQualificationTargetV1

    digest = "a" * 64
    return ReleaseQualificationTargetV1.build(
        build_revision="release-fixture",
        image_set_digest="b" * 64,
        deployed_artifact_set_digest="c" * 64,
        schema_family="pre_ga_v1",
        schema_revision="pre_ga_v1_0002",
        schema_application_fingerprint="d" * 64,
        schema_control_fingerprint="e" * 64,
        schema_identity_contract_version=1,
        production_schema_deployment_class="production",
        schema_seed_contract_digest="f" * 64,
        schema_runtime_contract_version=1,
        schema_checkpoint_codec_version=3,
        schema_capability_feature_digest="1" * 64,
        production_schema_runtime_identity_digest="2" * 64,
        schema_contract_material_digest="3" * 64,
        operator_auth_contract_version="operator-auth-v1",
        rollout_revision_id=UUID("00000000-0000-0000-0000-000000000101"),
        rollout_revision_digest="4" * 64,
        runtime_closure_digest="5" * 64,
        profile_version_id=UUID("00000000-0000-0000-0000-000000000102"),
        profile_content_digest="6" * 64,
        model_id=UUID("00000000-0000-0000-0000-000000000103"),
        model_identity_digest="7" * 64,
        package_closure_digest="8" * 64,
        capability_closure_digest="9" * 64,
        seed_manifest_digest=digest,
        worker_runtime_contract_version=1,
        worker_checkpoint_codec_version=3,
        worker_capability_feature_digest="b" * 64,
        create_entry_contract_digest="c" * 64,
        write_policy_digest="d" * 64,
        write_cohort_digest="e" * 64,
        reconciliation_contract_version=1,
        dependency_lock_set_digest="f" * 64,
        scenario_set_digest="1" * 64,
        required_assertion_set_digest="2" * 64,
        runner_contract_version=1,
        runner_identity_digest="3" * 64,
        evidence_trust_set_digest="4" * 64,
    )


def test_fixture_recomputes_provisioning_and_fixture_digest(tmp_path: Path) -> None:
    from app.release.target_fixture import (
        RehearsalInitializationFixturePort,
        RehearsalInitializationFixtureV1,
    )

    fixture = RehearsalInitializationFixtureV1.build(
        target=_target(),
        provisioning={
            "profile": {"profileVersionId": "00000000-0000-0000-0000-000000000102"},
            "model": {"modelId": "00000000-0000-0000-0000-000000000103"},
            "capabilities": [],
        },
    )
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture.model_dump(mode="json", by_alias=True)), encoding="utf-8")
    path.chmod(0o600)
    loaded = RehearsalInitializationFixtureV1.from_file(path)
    port = RehearsalInitializationFixturePort(
        fixture=loaded,
        profile_run_id=UUID("00000000-0000-0000-0000-000000000104"),
    )
    assert port.target.qualification_target_digest == fixture.target.qualification_target_digest
    assert port.fixture_digest == fixture.fixture_digest
    assert port.provisioning()["capabilities"] == []


def test_fixture_rejects_sensitive_material_and_digest_drift() -> None:
    from app.release.target_fixture import RehearsalInitializationFixtureV1, TargetFixtureError

    with pytest.raises(TargetFixtureError, match="sensitive"):
        RehearsalInitializationFixtureV1.build(
            target=_target(),
            provisioning={"providerCredential": "must-never-be-captured"},
        )

    fixture = RehearsalInitializationFixtureV1.build(target=_target(), provisioning={"profile": {}})
    payload = fixture.model_dump(mode="json", by_alias=True)
    payload["fixtureDigest"] = "0" * 64
    with pytest.raises((TargetFixtureError, ValueError), match="digest"):
        RehearsalInitializationFixtureV1.model_validate(payload)

    with pytest.raises((TargetFixtureError, ValueError), match="target_digest"):
        RehearsalInitializationFixtureV1.build(
            target=_target(),
            provisioning={"targetDigest": "0" * 64},
        )
