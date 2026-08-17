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


def test_deployment_identity_prefers_local_image_id_over_registry_repo_digest(
    tmp_path: Path,
) -> None:
    from app.release.trust import ReleaseEvidenceSigner
    from scripts.render_release_deployment_identity import render

    local_backend = "sha256:" + "a" * 64
    local_web = "sha256:" + "b" * 64
    inspection = tmp_path / "inspection.json"
    inspection.write_text(
        json.dumps(
            [
                {
                    "RepoTags": ["mindatlas-release-backend:" + "c" * 40],
                    "RepoDigests": ["registry.example/backend@sha256:" + "1" * 64],
                    "Id": local_backend,
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.revision": "c" * 40,
                            "io.mindatlas.dependency-lock-set-sha256": "d" * 64,
                        }
                    },
                },
                {
                    "RepoTags": ["mindatlas-release-scripted-provider:" + "c" * 40],
                    "Id": "sha256:" + "e" * 64,
                    "Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}},
                },
                {
                    "RepoTags": ["mindatlas-release-web:" + "c" * 40],
                    "RepoDigests": ["registry.example/web@sha256:" + "2" * 64],
                    "Id": local_web,
                    "Config": {"Labels": {"org.opencontainers.image.revision": "c" * 40}},
                },
            ]
        ),
        encoding="utf-8",
    )
    key = bytes(range(32))
    key_path = tmp_path / "key"
    key_path.write_bytes(key)
    fd = os.open(key_path, os.O_RDONLY)
    try:
        output = tmp_path / "identity.json"
        render(
            input_path=inspection,
            output_path=output,
            build_revision="c" * 40,
            dependency_lock_set_digest="d" * 64,
            key_id="deployment-key",
            signing_key_fd=fd,
        )
    finally:
        os.close(fd)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["identity"]["apiImageDigest"] == "a" * 64
    assert payload["identity"]["assistantWorkerImageDigest"] == "a" * 64
    assert payload["identity"]["webImageDigest"] == "b" * 64


def test_deployment_identity_renderer_refuses_output_overwrite(tmp_path: Path) -> None:
    from scripts.render_release_deployment_identity import DeploymentIdentityError, render

    inspection = tmp_path / "inspection.json"
    inspection.write_text(
        json.dumps(
            {
                "api": {"imageDigest": "sha256:" + "a" * 64},
                "assistant-worker": {"imageDigest": "sha256:" + "a" * 64},
                "web": {"imageDigest": "sha256:" + "b" * 64},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "identity.json"
    output.write_text("existing", encoding="utf-8")
    key_path = tmp_path / "key"
    key_path.write_bytes(bytes(range(32)))
    fd = os.open(key_path, os.O_RDONLY)
    try:
        with pytest.raises(DeploymentIdentityError, match="output_collision"):
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


def test_release_env_example_contains_names_but_no_usable_secret() -> None:
    text = (ROOT / "deploy" / "release.env.example").read_text(encoding="utf-8")
    assert "RELEASE_SIGNING_KEY_FD=" in text
    assert "password=" not in text.lower()
    assert "token=" not in text.lower()
    assert "sha256:" not in text
    assert "MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE=" in text


def test_protected_workflow_wrapper_has_distinct_inner_executor_contract() -> None:
    wrapper = (ROOT / ".github" / "scripts" / "run-protected-release-qualification").read_text(
        encoding="utf-8"
    )
    assert 'MINDATLAS_RELEASE_PROFILE_EXECUTOR:?MINDATLAS_RELEASE_PROFILE_EXECUTOR is required' in wrapper
    assert 'export MINDATLAS_RELEASE_PROTECTED_RUNNER="$MINDATLAS_RELEASE_PROFILE_EXECUTOR"' in wrapper
    assert '--oci-bundle "$MINDATLAS_RELEASE_OCI_BUNDLE"' in wrapper
    assert 'MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE:?MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE is required' in wrapper
    assert '--deployment-identity "$MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE"' in wrapper
    assert 'MINDATLAS_RELEASE_PROTECTED_RUNNER:?MINDATLAS_RELEASE_PROTECTED_RUNNER is required' not in wrapper


def test_release_workflow_never_materializes_signing_key_value() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-qualification.yml").read_text(
        encoding="utf-8"
    )
    assert "AUTOMATED_EVIDENCE_SIGNING_KEY_B64" not in workflow
    assert "MINDATLAS_AUTOMATION_SIGNING_KEY_FD" in workflow
    assert "render_release_deployment_identity.py" in workflow
    assert "artifact verify" in workflow
    assert "MINDATLAS_RELEASE_DEPLOYMENT_IDENTITY_FILE" in workflow
    assert "RELEASE_TARGET_ALIAS: ${{ inputs.target_alias }}" in workflow


def test_artifact_build_reports_docker_boundary_instead_of_protected_runner_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_pre_ga_release

    monkeypatch.setattr(run_pre_ga_release.shutil, "which", lambda name: None if name == "docker" else "/usr/bin/node")
    args = type(
        "Args",
        (),
        {
            "source_revision": "a" * 40,
            "output_dir": tmp_path / "artifacts",
            "no_build": False,
            "signing_key_fd": None,
            "deployment_key_id": None,
        },
    )()
    with pytest.raises(run_pre_ga_release.ReleaseCliError, match="docker_unavailable"):
        run_pre_ga_release.build_artifact(args)


def test_artifact_verify_checks_signed_identity_and_archive_shape(tmp_path: Path) -> None:
    import hashlib
    import io
    import tarfile

    from app.release.contracts import DeployedArtifactIdentityV1
    from app.release.trust import ReleaseEvidenceSigner, ReleaseTrustSetV1
    from scripts import run_pre_ga_release

    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="deployment-key",
        private_key_bytes=bytes(range(32)),
    )
    def config_bytes(labels: dict[str, str], command: str) -> tuple[str, bytes]:
        content = json.dumps(
            {
                "config": {"Labels": labels, "Cmd": [command]},
                "rootfs": {"type": "layers", "diff_ids": []},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(content).hexdigest() + ".json", content

    backend_config_name, backend_config = config_bytes(
        {
            "org.opencontainers.image.revision": "a" * 40,
            "io.mindatlas.platform": "linux/amd64",
            "io.mindatlas.dependency-lock-set-sha256": "c" * 64,
        },
        "backend",
    )
    provider_config_name, provider_config = config_bytes(
        {
            "org.opencontainers.image.revision": "a" * 40,
            "io.mindatlas.platform": "linux/amd64",
            "io.mindatlas.dependency-lock-set-sha256": "c" * 64,
        },
        "scripted-provider",
    )
    web_config_name, web_config = config_bytes(
        {
            "org.opencontainers.image.revision": "a" * 40,
            "io.mindatlas.platform": "linux/amd64",
            "io.mindatlas.frontend-build-content-sha256": "d" * 64,
        },
        "web",
    )
    identity = DeployedArtifactIdentityV1.build(
        build_revision="a" * 40,
        platform="linux/amd64",
        api_image_digest=backend_config_name[:-5],
        assistant_worker_image_digest=backend_config_name[:-5],
        web_image_digest=web_config_name[:-5],
        dependency_lock_set_digest="c" * 64,
    )
    signed = signer.sign_deployed_artifact_identity(identity)
    identity_path = tmp_path / "deployment-identity.json"
    identity_path.write_text(json.dumps(signed.model_dump(mode="json", by_alias=True), separators=(",", ":")), encoding="utf-8")
    trust_path = tmp_path / "trust.json"
    trust_key = signer.trust_key(allowed_domains=("deployed_artifact_identity",), allowed_evidence_kinds=())
    trust = ReleaseTrustSetV1.build((trust_key,))
    trust_path.write_text(json.dumps(trust.model_dump(mode="json", by_alias=True)), encoding="utf-8")
    bundle = tmp_path / "release-application-images.tar"
    with tarfile.open(bundle, mode="w") as archive:
        for name, content in (
            (backend_config_name, backend_config),
            (provider_config_name, provider_config),
            (web_config_name, web_config),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        for name in ("backend/layer.tar", "provider/layer.tar", "web/layer.tar"):
            member = tarfile.TarInfo(name)
            content = b"layer"
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        manifest = tarfile.TarInfo("manifest.json")
        content = json.dumps(
            [
                {
                    "Config": backend_config_name,
                    "RepoTags": ["mindatlas-release-backend:" + "a" * 40],
                    "Layers": ["backend/layer.tar"],
                },
                {
                    "Config": provider_config_name,
                    "RepoTags": ["mindatlas-release-scripted-provider:" + "a" * 40],
                    "Layers": ["provider/layer.tar"],
                },
                {
                    "Config": web_config_name,
                    "RepoTags": ["mindatlas-release-web:" + "a" * 40],
                    "Layers": ["web/layer.tar"],
                },
            ],
            separators=(",", ":"),
        ).encode("utf-8")
        manifest.size = len(content)
        archive.addfile(manifest, __import__("io").BytesIO(content))

    result = run_pre_ga_release.verify_artifact(
        type(
            "Args",
            (),
            {
                "deployment_identity": identity_path,
                "oci_bundle": bundle,
                "trust_set": trust_path,
                "run_dir": None,
            },
        )()
    )
    assert result["state"] == "artifact-verified"
    assert result["imageSetDigest"] == identity.image_set_digest


def test_artifact_verify_rejects_path_traversal_member(tmp_path: Path) -> None:
    import tarfile

    from scripts import run_pre_ga_release

    bundle = tmp_path / "unsafe.tar"
    with tarfile.open(bundle, mode="w") as archive:
        member = tarfile.TarInfo("../escape")
        member.size = 1
        archive.addfile(member, __import__("io").BytesIO(b"x"))
    with pytest.raises(run_pre_ga_release.ReleaseCliError, match="member_invalid"):
        run_pre_ga_release._validate_archive_bundle(bundle)


def test_artifact_bundle_requires_exact_backend_provider_and_web_exports(tmp_path: Path) -> None:
    import io
    import tarfile
    from types import SimpleNamespace

    from scripts import run_pre_ga_release

    bundle = tmp_path / "missing-images.tar"
    with tarfile.open(bundle, mode="w") as archive:
        member = tarfile.TarInfo("manifest.json")
        content = b"[]"
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    with pytest.raises(run_pre_ga_release.ReleaseCliError, match="image_inventory"):
        run_pre_ga_release._validate_oci_image_bundle(
            bundle,
            SimpleNamespace(
                build_revision="a" * 40,
                dependency_lock_set_digest="b" * 64,
            ),
        )


def test_artifact_build_exports_backend_provider_and_web_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import tarfile

    from scripts import run_pre_ga_release

    source_revision = "a" * 40
    docker_path = "/usr/bin/docker"
    monkeypatch.setattr(run_pre_ga_release.shutil, "which", lambda name: docker_path if name == "docker" else None)
    monkeypatch.setattr(run_pre_ga_release, "_source_revision_is_clean", lambda revision: None)
    monkeypatch.setattr(run_pre_ga_release, "_frontend_content_digest", lambda: "b" * 64)
    calls: list[list[str]] = []
    api_digest = "c" * 64
    web_digest = "d" * 64
    inspection = json.dumps(
        [
            {
                "RepoTags": [f"mindatlas-release-backend:{source_revision}"],
                "RepoDigests": [f"registry.example/backend@sha256:{api_digest}"],
                "Id": "sha256:" + api_digest,
                "Config": {
                    "Labels": {
                        "org.opencontainers.image.revision": source_revision,
                        "io.mindatlas.dependency-lock-set-sha256": "4f55315dba8826009556ce50a207e6776a0bca7449aa0fd900dfa521dab5eb35",
                    },
                    "Env": ["SECRET=should-not-persist"],
                },
            },
            {
                "RepoTags": [f"mindatlas-release-scripted-provider:{source_revision}"],
                "RepoDigests": ["registry.example/provider@sha256:" + "e" * 64],
                "Id": "sha256:" + "e" * 64,
                "Config": {"Labels": {"org.opencontainers.image.revision": source_revision}},
            },
            {
                "RepoTags": [f"mindatlas-release-web:{source_revision}"],
                "RepoDigests": [f"registry.example/web@sha256:{web_digest}"],
                "Id": "sha256:" + web_digest,
                "Config": {"Labels": {"org.opencontainers.image.revision": source_revision}},
            },
        ]
    )

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        if "save" in command:
            output = Path(command[command.index("--output") + 1])
            with tarfile.open(output, mode="w") as archive:
                member = tarfile.TarInfo("manifest.json")
                content = b"[]"
                member.size = len(content)
                archive.addfile(member, __import__("io").BytesIO(content))
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, inspection, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run_pre_ga_release, "_run_checked", fake_run)
    key_path = tmp_path / "deployment-key"
    key_path.write_bytes(bytes(range(32)))
    fd = os.open(key_path, os.O_RDONLY)
    try:
        result = run_pre_ga_release.build_artifact(
            type(
                "Args",
                (),
                {
                    "source_revision": source_revision,
                    "output_dir": tmp_path / "artifacts",
                    "no_build": False,
                    "signing_key_fd": fd,
                    "deployment_key_id": "deployment-key",
                },
            )()
        )
    finally:
        os.close(fd)
    assert result["state"] == "artifact-built"
    assert sum(1 for command in calls if command[1:3] == ["buildx", "build"]) == 3
    assert sum(1 for command in calls if "save" in command) == 1
    assert (tmp_path / "artifacts" / "release-application-images.tar").is_file()
    assert (tmp_path / "artifacts" / "deployment-identity.json").is_file()
    assert (tmp_path / "artifacts" / "artifact-state.json").is_file()
    assert "should-not-persist" not in (
        tmp_path / "artifacts" / "release-image-inspection.json"
    ).read_text(encoding="utf-8")


def test_evidence_promotion_is_conditional_create_only_for_code_owned_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import tarfile

    from app.assistant.domain.digests import sha256_bytes
    from app.release.contracts import ReleaseEvidenceManifestV1
    from app.release.evidence import ContentAddressedEvidenceStore
    from app.release.trust import ReleaseEvidenceSigner, ReleaseTrustSetV1
    from scripts import run_pre_ga_release
    from tests.test_release_evidence import _manifest

    original = _manifest()
    contents = (b"safe-junit", b"safe-scenario")
    digests = tuple(sha256_bytes(content) for content in contents)
    refs = tuple(
        ref.model_copy(update={"sha256_digest": digest, "byte_size": len(content)})
        for ref, digest, content in zip(original.artifact_refs, digests, contents)
    )
    assertions = tuple(
        assertion.model_copy(update={"artifact_digests": (digest,)})
        for assertion, digest in zip(original.assertion_results, digests)
    )
    values = original.model_dump(mode="python", by_alias=False)
    values["qualification_infrastructure_identity"] = original.qualification_infrastructure_identity
    values["artifact_refs"] = refs
    values["assertion_results"] = assertions
    manifest = ReleaseEvidenceManifestV1.build(**values)
    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id="qualification-key",
        private_key_bytes=bytes(range(32)),
    )
    source_store = ContentAddressedEvidenceStore(tmp_path / "source")
    for content in contents:
        source_store.put_artifact(content, media_type="application/json")
    attestation = signer.sign(manifest)
    source_store.put_evidence_object(manifest, attestation)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(
        (tmp_path / "source" / source_store.evidence_key("automated_qualification", manifest.manifest_digest)).read_bytes()
    )
    bundle_path = tmp_path / "artifacts.tar"
    with tarfile.open(bundle_path, mode="w") as archive:
        for digest, content in zip(digests, contents):
            member = tarfile.TarInfo(f"artifacts/{digest}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    trust_path = tmp_path / "trust.json"
    trust_path.write_text(
        json.dumps(
            ReleaseTrustSetV1.build((signer.trust_key(),)).model_dump(mode="json", by_alias=True)
        ),
        encoding="utf-8",
    )
    credential_path = tmp_path / "promotion-credential"
    credential_path.write_bytes(b"opaque-promotion-credential\n")
    credential_fd = os.open(credential_path, os.O_RDONLY)
    monkeypatch.setenv("MINDATLAS_RELEASE_PROMOTION_ROOT", str(tmp_path / "promoted"))
    try:
        result = run_pre_ga_release.promote_evidence(
            type(
                "Args",
                (),
                {
                    "evidence": evidence_path,
                    "artifact_bundle": bundle_path,
                    "trust_set": trust_path,
                    "target_alias": "production-main",
                    "kind": "automated_qualification",
                    "destination_credential_fd": credential_fd,
                    "credential_fd": None,
                },
            )()
        )
    finally:
        os.close(credential_fd)
    assert result["state"] == "evidence-promoted"
    assert result["artifactCount"] == 2
    assert result["manifestDigest"] == manifest.manifest_digest
    promoted_root = tmp_path / "promoted" / "production-main"
    assert (promoted_root / "release-evidence" / "v1" / "automated_qualification" / manifest.manifest_digest[:2] / f"{manifest.manifest_digest}.json").is_file()
    assert result["idempotent"] is False
    second_fd = os.open(credential_path, os.O_RDONLY)
    try:
        assert run_pre_ga_release.promote_evidence(
            type(
                "Args",
                (),
                {
                    "evidence": evidence_path,
                    "artifact_bundle": bundle_path,
                    "trust_set": trust_path,
                    "target_alias": "production-main",
                    "kind": "automated_qualification",
                    "destination_credential_fd": second_fd,
                    "credential_fd": None,
                },
            )()
        )["idempotent"] is True
    finally:
        os.close(second_fd)
    promoted_artifact = (
        promoted_root
        / "release-evidence-artifacts"
        / "v1"
        / digests[0][:2]
        / digests[0]
    )
    promoted_artifact.chmod(0o600)
    promoted_artifact.write_bytes(b"different-bytes")
    collision_fd = os.open(credential_path, os.O_RDONLY)
    try:
        with pytest.raises(run_pre_ga_release.ReleaseCliError, match="object_collision"):
            run_pre_ga_release.promote_evidence(
                type(
                    "Args",
                    (),
                    {
                        "evidence": evidence_path,
                        "artifact_bundle": bundle_path,
                        "trust_set": trust_path,
                        "target_alias": "production-main",
                        "kind": "automated_qualification",
                        "destination_credential_fd": collision_fd,
                        "credential_fd": None,
                    },
                )()
            )
    finally:
        os.close(collision_fd)


def test_production_clone_negative_acceptance_requires_and_verifies_protected_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from scripts import run_pre_ga_release

    source_url = tmp_path / "source-url"
    source_url.write_bytes(b"descriptor-only")
    source_fd = os.open(source_url, os.O_RDONLY)
    executor = tmp_path / "clone-executor"
    executor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executor.chmod(0o700)
    target = tmp_path / "qualification-target.json"
    automation = tmp_path / "automation.json"
    rehearsal = tmp_path / "rehearsal.json"
    bundle = tmp_path / "release.oci.tar"
    trust = tmp_path / "trust.json"
    for path in (target, automation, rehearsal, bundle, trust):
        path.write_bytes(b"fixture")
    run_dir = tmp_path / "clone-run"
    target_digest = "d" * 64
    monkeypatch.setenv("MINDATLAS_RELEASE_CLONE_EXECUTOR", str(executor))
    monkeypatch.setattr(
        run_pre_ga_release,
        "verify_target",
        lambda path: {"targetDigest": target_digest},
    )
    monkeypatch.setattr(run_pre_ga_release, "_verified_manifest", lambda path, trust: (object(), object()))
    monkeypatch.setattr(run_pre_ga_release, "compare_evidence", lambda *args: {"state": "matched"})
    monkeypatch.setattr(
        run_pre_ga_release,
        "_validate_archive_bundle",
        lambda path: {"bundleDigest": "e" * 64, "memberCount": 3},
    )

    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        run_dir.mkdir(mode=0o700)
        (run_dir / "clone-negative-acceptance.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "state": "negative-acceptance-passed",
                    "sourceDeploymentClass": "production",
                    "sourceSchemaRevision": "pre_ga_v1_0002",
                    "sourceControlRevision": 0,
                    "sourceLaunched": False,
                    "qualificationTargetDigest": target_digest,
                    "ociBundleDigest": "e" * 64,
                    "destroyed": True,
                    "cases": {
                        "fileOutputRefused": True,
                        "remoteDestinationRefused": True,
                        "nonemptyDestinationRefused": True,
                        "launchedSourceRefused": True,
                        "legacyRevisionRefused": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run_pre_ga_release.subprocess, "run", fake_run)
    try:
        result = run_pre_ga_release.run_production_clone(
            type(
                "Args",
                (),
                {
                    "source_url_fd": source_fd,
                    "run_dir": run_dir,
                    "qualification_target": target,
                    "automation_evidence": automation,
                    "rehearsal_evidence": rehearsal,
                    "oci_bundle": bundle,
                    "trust_set": trust,
                },
            )()
        )
    finally:
        os.close(source_fd)
    assert result["state"] == "negative-acceptance-passed"
    assert result["qualificationTargetDigest"] == target_digest
    assert calls and "--source-database-url-fd" in calls[0]


def test_launch_verify_accepts_only_safe_passing_summary(tmp_path: Path) -> None:
    from scripts import run_pre_ga_release

    summary = tmp_path / "launch-summary.json"
    summary.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "evidenceKind": "production_rehearsal",
                "releaseSourceRevision": "a" * 40,
                "qualificationTargetDigest": "b" * 64,
                "manifestDigest": "c" * 64,
                "attestationDigest": "d" * 64,
                "artifactAggregateDigest": "e" * 64,
                "keyId": "release-key",
                "assertionPassed": 1,
                "assertionFailed": 0,
                "startedAt": "2026-08-17T00:00:00Z",
                "endedAt": "2026-08-17T00:00:01Z",
                "offlineVerification": "passed",
                "targetContainerVerification": "passed",
                "soakClaimed": False,
            }
        ),
        encoding="utf-8",
    )
    result = run_pre_ga_release.verify_launch_summary(summary)
    assert result["state"] == "launch-summary-verified"
    assert result["qualificationTargetDigest"] == "b" * 64


def test_launch_verify_delegates_live_state_to_fixed_result_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_pre_ga_release

    summary = tmp_path / "launch-summary.json"
    summary.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "evidenceKind": "production_rehearsal",
                "releaseSourceRevision": "a" * 40,
                "qualificationTargetDigest": "b" * 64,
                "manifestDigest": "c" * 64,
                "attestationDigest": "d" * 64,
                "artifactAggregateDigest": "e" * 64,
                "keyId": "release-key",
                "assertionPassed": 1,
                "assertionFailed": 0,
                "startedAt": "2026-08-17T00:00:00Z",
                "endedAt": "2026-08-17T00:00:01Z",
                "offlineVerification": "passed",
                "targetContainerVerification": "passed",
                "soakClaimed": False,
            }
        ),
        encoding="utf-8",
    )
    verifier = tmp_path / "launch-verifier"
    verifier.write_text(
        "#!/bin/sh\n"
        "cat > \"$MINDATLAS_RELEASE_LAUNCH_RESULT\" <<JSON\n"
        "{\"schemaVersion\":1,\"state\":\"launch-verified\","
        "\"qualificationTargetDigest\":\"$MINDATLAS_RELEASE_LAUNCH_TARGET_DIGEST\","
        "\"launched\":true,\"ready\":true,\"controlRevision\":1,"
        "\"activeRunCount\":0,\"unresolvedCallCount\":0,\"workerCount\":2}\n"
        "JSON\n",
        encoding="utf-8",
    )
    verifier.chmod(0o700)
    monkeypatch.setenv("MINDATLAS_RELEASE_LAUNCH_VERIFIER", str(verifier))
    result = run_pre_ga_release.verify_launch(
        type(
            "Args",
            (),
            {"summary": summary, "base_url": "https://release.example", "target": None, "candidate": None},
        )()
    )
    assert result["state"] == "launch-verified"
    assert result["controlRevision"] == 1
    assert result["workerCount"] == 2


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
