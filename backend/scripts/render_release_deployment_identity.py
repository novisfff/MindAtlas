#!/usr/bin/env python3
"""Render and optionally sign the immutable application deployment identity."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any

from app.release.contracts import DeployedArtifactIdentityV1
from app.release.trust import ReleaseEvidenceSigner


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DeploymentIdentityError(ValueError):
    pass


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise DeploymentIdentityError("deployment_identity_input_invalid") from None


def _read_json_fd(fd: int) -> Any:
    if fd < 0:
        raise DeploymentIdentityError("deployment_identity_input_fd_invalid")
    try:
        raw = os.read(fd, 8 * 1024 * 1024)
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise DeploymentIdentityError("deployment_identity_input_invalid") from None


def _image_digest(value: Any, *, role: str) -> str:
    if isinstance(value, str):
        digest = value
    elif isinstance(value, dict):
        digest = (
            value.get("imageDigest")
            or value.get("digest")
            or value.get("RepoDigest")
            or value.get("RepoDigests")
            or value.get("Id")
        )
        if isinstance(digest, list):
            digest = digest[0] if digest else None
    else:
        digest = None
    if not isinstance(digest, str):
        raise DeploymentIdentityError(f"deployment_identity_{role}_digest_missing")
    if "@" in digest:
        digest = digest.rsplit("@", 1)[1]
    if not _DIGEST_RE.fullmatch(digest):
        raise DeploymentIdentityError(f"deployment_identity_{role}_digest_invalid")
    return digest


def _extract_images(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict) and isinstance(raw.get("images"), dict):
        source = raw["images"]
    elif isinstance(raw, dict):
        source = raw
    elif isinstance(raw, list):
        source = {}
        for item in raw:
            if not isinstance(item, dict):
                raise DeploymentIdentityError("deployment_identity_input_invalid")
            role = item.get("role") or item.get("service") or item.get("name")
            labels = item.get("Config", {}).get("Labels", {})
            tags = item.get("RepoTags", [])
            marker = " ".join(str(value) for value in (role, labels, tags)).lower()
            if "web" in marker or "frontend" in marker:
                source["web"] = item
            elif "backend" in marker or "api" in marker or "mindatlas-release-backend" in marker:
                source["api"] = item
                source["assistant-worker"] = item
            elif isinstance(role, str):
                source[role] = item
    else:
        raise DeploymentIdentityError("deployment_identity_input_invalid")
    aliases = {
        "api": ("api", "backend", "mindatlas-api"),
        "assistant-worker": ("assistant-worker", "assistant_worker", "worker", "worker-a"),
        "web": ("web", "frontend", "mindatlas-web"),
    }
    result: dict[str, str] = {}
    for role, names in aliases.items():
        value = next((source[name] for name in names if name in source), None)
        if value is None:
            raise DeploymentIdentityError(f"deployment_identity_{role}_missing")
        result[role] = _image_digest(value, role=role.replace("-", "_"))
    return result


def _validate_inspection_labels(raw: Any, *, build_revision: str, dependency_lock_set_digest: str) -> None:
    entries = raw if isinstance(raw, list) else list(raw.get("images", {}).values()) if isinstance(raw, dict) and isinstance(raw.get("images"), dict) else []
    for item in entries:
        if not isinstance(item, dict):
            continue
        labels = item.get("Config", {}).get("Labels", {})
        if not isinstance(labels, dict):
            continue
        observed_revision = labels.get("org.opencontainers.image.revision")
        observed_lock = labels.get("io.mindatlas.dependency-lock-set-sha256")
        if observed_revision is not None and observed_revision != build_revision:
            raise DeploymentIdentityError("deployment_identity_build_revision_label_mismatch")
        if observed_lock is not None and observed_lock != dependency_lock_set_digest:
            raise DeploymentIdentityError("deployment_identity_lock_label_mismatch")


def _read_private_key(fd: int) -> bytes:
    if fd < 0:
        raise DeploymentIdentityError("deployment_identity_signing_fd_invalid")
    try:
        raw = os.read(fd, 4096)
    except OSError:
        raise DeploymentIdentityError("deployment_identity_signing_fd_unavailable") from None
    if len(raw) != 32:
        raise DeploymentIdentityError("deployment_identity_private_key_invalid")
    return raw


def render(
    *,
    input_path: Path,
    output_path: Path,
    build_revision: str,
    dependency_lock_set_digest: str,
    key_id: str | None = None,
    signing_key_fd: int | None = None,
) -> dict[str, Any]:
    if not build_revision.strip() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", build_revision):
        raise DeploymentIdentityError("deployment_identity_build_revision_invalid")
    try:
        raw = _read_json(input_path)
        _validate_inspection_labels(
            raw,
            build_revision=build_revision,
            dependency_lock_set_digest=dependency_lock_set_digest,
        )
        images = _extract_images(raw)
    except DeploymentIdentityError:
        raise
    identity = DeployedArtifactIdentityV1.build(
        build_revision=build_revision,
        platform="linux/amd64",
        api_image_digest=images["api"].removeprefix("sha256:"),
        assistant_worker_image_digest=images["assistant-worker"].removeprefix("sha256:"),
        web_image_digest=images["web"].removeprefix("sha256:"),
        dependency_lock_set_digest=dependency_lock_set_digest,
    )
    if signing_key_fd is None or key_id is None:
        raise DeploymentIdentityError("deployment_identity_signature_required")
    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id=key_id,
        private_key_bytes=_read_private_key(signing_key_fd),
    )
    signed = signer.sign_deployed_artifact_identity(identity)
    payload = signed.model_dump(mode="json", by_alias=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    output_path.chmod(0o444)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render signed release deployment identity")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input", type=Path)
    input_group.add_argument("--input-fd", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-revision", required=True)
    parser.add_argument("--dependency-lock-set-digest", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--signing-key-fd", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.input is not None:
            input_path = args.input
        else:
            raw_input = _read_json_fd(args.input_fd)
            # Keep the public render() API path-based for callers/tests; fd
            # mode stays in-memory and never materializes inspection bytes.
            _validate_inspection_labels(
                raw_input,
                build_revision=args.build_revision,
                dependency_lock_set_digest=args.dependency_lock_set_digest,
            )
            images = _extract_images(raw_input)
            identity = DeployedArtifactIdentityV1.build(
                build_revision=args.build_revision,
                platform="linux/amd64",
                api_image_digest=images["api"].removeprefix("sha256:"),
                assistant_worker_image_digest=images["assistant-worker"].removeprefix("sha256:"),
                web_image_digest=images["web"].removeprefix("sha256:"),
                dependency_lock_set_digest=args.dependency_lock_set_digest,
            )
            signer = ReleaseEvidenceSigner.from_private_key_bytes(
                key_id=args.key_id,
                private_key_bytes=_read_private_key(args.signing_key_fd),
            )
            signed = signer.sign_deployed_artifact_identity(identity)
            payload = signed.model_dump(mode="json", by_alias=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            args.output.chmod(0o444)
            print(json.dumps({"domain": payload["domain"], "keyId": payload["keyId"], "imageSetDigest": payload["identity"]["imageSetDigest"], "deployedArtifactSetDigest": payload["identity"]["deployedArtifactSetDigest"]}, separators=(",", ":")))
            return 0
        payload = render(
            input_path=input_path,
            output_path=args.output,
            build_revision=args.build_revision,
            dependency_lock_set_digest=args.dependency_lock_set_digest,
            key_id=args.key_id,
            signing_key_fd=args.signing_key_fd,
        )
    except DeploymentIdentityError as exc:
        print(str(exc))
        return 2
    print(json.dumps({"domain": payload["domain"], "keyId": payload["keyId"], "imageSetDigest": payload["identity"]["imageSetDigest"], "deployedArtifactSetDigest": payload["identity"]["deployedArtifactSetDigest"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
