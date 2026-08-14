from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_release_profile_has_locked_standalone_topology() -> None:
    from scripts.run_pre_ga_release import validate_compose

    result = validate_compose(
        ROOT / "deploy" / "compose.release-qualification.yml",
        ROOT / "deploy" / "release-images.lock",
    )
    assert result["networkInternal"] is True
    assert result["workers"] == ["release-worker-a", "release-worker-b"]
    assert len(result["services"]) == 9


def test_release_profile_rejects_mutable_or_defaulted_topology(tmp_path: Path) -> None:
    import yaml

    from scripts.run_pre_ga_release import ReleaseCliError, validate_compose

    source = ROOT / "deploy" / "compose.release-qualification.yml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["services"]["postgres"]["image"] = "postgres:latest"
    bad = tmp_path / "compose.yml"
    bad.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ReleaseCliError, match="mutable"):
        validate_compose(bad, ROOT / "deploy" / "release-images.lock")

    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["services"]["api"]["environment"]["DATABASE_URL"] = "postgres://x:${PASSWORD:-default}@postgres/db"
    bad.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ReleaseCliError, match="default"):
        validate_compose(bad, ROOT / "deploy" / "release-images.lock")


def test_rendered_deployment_identity_is_signed_and_role_bound(tmp_path: Path) -> None:
    from app.release.contracts import DeployedArtifactIdentityV1
    from app.release.trust import ReleaseEvidenceSigner, verify_deployed_artifact_identity
    from scripts.render_release_deployment_identity import render

    inspection = tmp_path / "inspection.json"
    inspection.write_text(
        json.dumps(
            {
                "images": {
                    "api": {"imageDigest": "sha256:" + "a" * 64},
                    "assistant-worker": {"imageDigest": "sha256:" + "a" * 64},
                    "web": {"imageDigest": "sha256:" + "b" * 64},
                }
            }
        ),
        encoding="utf-8",
    )
    key_path = tmp_path / "key"
    key_path.write_bytes(bytes(range(32)))
    fd = os.open(key_path, os.O_RDONLY)
    try:
        output = tmp_path / "identity.json"
        render(
            input_path=inspection,
            output_path=output,
            build_revision="release-fixture",
            dependency_lock_set_digest="c" * 64,
            key_id="deployment-key",
            signing_key_fd=fd,
        )
    finally:
        os.close(fd)
    signer = ReleaseEvidenceSigner.from_private_key_bytes(key_id="deployment-key", private_key_bytes=bytes(range(32)))
    trust = signer.trust_key(allowed_domains=("deployed_artifact_identity",), allowed_evidence_kinds=())
    identity = verify_deployed_artifact_identity(json.loads(output.read_text(encoding="utf-8")), trust)
    assert isinstance(identity, DeployedArtifactIdentityV1)
    assert identity.api_image_digest == identity.assistant_worker_image_digest
    assert output.stat().st_mode & 0o222 == 0


def test_release_env_example_contains_names_but_no_usable_secret() -> None:
    text = (ROOT / "deploy" / "release.env.example").read_text(encoding="utf-8")
    assert "RELEASE_SIGNING_KEY_FD=" in text
    assert "password=" not in text.lower()
    assert "token=" not in text.lower()
    assert "sha256:" not in text


def test_protected_workflow_wrapper_has_distinct_inner_executor_contract() -> None:
    wrapper = (ROOT / ".github" / "scripts" / "run-protected-release-qualification").read_text(
        encoding="utf-8"
    )
    assert 'MINDATLAS_RELEASE_PROFILE_EXECUTOR:?MINDATLAS_RELEASE_PROFILE_EXECUTOR is required' in wrapper
    assert 'export MINDATLAS_RELEASE_PROTECTED_RUNNER="$MINDATLAS_RELEASE_PROFILE_EXECUTOR"' in wrapper
    assert '--oci-bundle "$MINDATLAS_RELEASE_OCI_BUNDLE"' in wrapper
    assert 'MINDATLAS_RELEASE_PROTECTED_RUNNER:?MINDATLAS_RELEASE_PROTECTED_RUNNER is required' not in wrapper


def test_target_capture_sanitizes_configuration_and_requires_secure_cookies() -> None:
    from scripts.run_pre_ga_release import (
        ReleaseCliError,
        _sanitize_provisioning_configuration,
        _validate_auth_set_cookie_headers,
    )

    payload = _sanitize_provisioning_configuration(
        {
            "profileVersionId": "profile-v1",
            "basePrompt": "must not be captured",
            "providerApiKey": "must not be captured",
            "displayName": "Default Main Agent",
            "nested": {"promptText": "must not be captured", "revision": 2},
        }
    )
    assert payload == {
        "profileVersionId": "profile-v1",
        "displayName": "Default Main Agent",
        "nested": {"revision": 2},
    }

    headers = (
        "mindatlas_session=opaque; Path=/; HttpOnly; Secure; SameSite=Lax",
        "mindatlas_csrf=opaque; Path=/; Secure; SameSite=Lax",
    )
    _validate_auth_set_cookie_headers(headers)
    with pytest.raises(ReleaseCliError, match="cookie_contract"):
        _validate_auth_set_cookie_headers(
            ("mindatlas_session=opaque; Path=/; HttpOnly; SameSite=Lax",)
        )


def test_target_capture_writes_authenticated_target_and_fixture_without_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.release.target_fixture import RehearsalInitializationFixtureV1
    from app.release.contracts import ReleaseQualificationTargetV1
    from scripts import run_pre_ga_release

    target = ReleaseQualificationTargetV1.build(
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
        seed_manifest_digest="a" * 64,
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

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            assert base_url == "https://target.example"

        def login(self, password: str) -> None:
            assert password == "operator-password"

        def get(self, path: str):
            if path == "/api/pre-ga-launch/qualification-target":
                return target.model_dump(mode="json", by_alias=True)
            return {"items": [{"id": "safe-id", "digest": "a" * 64}]}

    monkeypatch.setattr(run_pre_ga_release, "_TargetHttpClient", FakeClient)
    password_file = tmp_path / "operator-password"
    password_file.write_bytes(b"operator-password\n")
    fd = os.open(password_file, os.O_RDONLY)
    try:
        target_path = tmp_path / "target.json"
        fixture_path = tmp_path / "fixture.json"
        result = run_pre_ga_release.capture_target(
            type(
                "Args",
                (),
                {
                    "target_url": "https://target.example",
                    "operator_password_fd": fd,
                    "output": target_path,
                    "provisioning_bundle": fixture_path,
                },
            )()
        )
    finally:
        os.close(fd)
    assert result["state"] == "target-captured"
    fixture = RehearsalInitializationFixtureV1.from_file(fixture_path)
    assert fixture.target.qualification_target_digest == target.qualification_target_digest
    assert b"operator-password" not in target_path.read_bytes()
    assert b"operator-password" not in fixture_path.read_bytes()
    assert target_path.stat().st_mode & 0o077 == 0
    assert fixture_path.stat().st_mode & 0o077 == 0
