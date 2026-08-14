"""Allowlisted content-addressed evidence storage and verification."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.assistant.domain.digests import sha256_bytes
from app.release.contracts import (
    ReleaseEvidenceManifestV1,
    canonical_release_manifest_bytes,
)
from app.release.trust import verify_release_attestation


ARTIFACT_KEY_PREFIX = "release-evidence-artifacts/v1"
EVIDENCE_KEY_PREFIX = "release-evidence/v1"
_SAFE_MARKER_RE = re.compile(
    r"(?i)(password|passwd|authorization|bearer|api[_-]?key|secret|token|database_url|prompt|entry[_-]?body)"
)


class ReleaseEvidenceCollision(ValueError):
    safe_code = "release_evidence_collision"


class ReleaseEvidenceIntegrityError(ValueError):
    safe_code = "release_evidence_integrity_invalid"


def assert_safe_evidence_payload(value: Any) -> None:
    """Reject secret-like keys/values before they can become evidence bytes."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _SAFE_MARKER_RE.search(str(key)):
                raise ReleaseEvidenceIntegrityError("release_evidence_sensitive_field")
            assert_safe_evidence_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_safe_evidence_payload(item)
    elif isinstance(value, str) and _SAFE_MARKER_RE.search(value):
        raise ReleaseEvidenceIntegrityError("release_evidence_sensitive_value")


class ContentAddressedEvidenceStore:
    """Filesystem implementation of the release object-store contract.

    Production composes the same interface with MinIO.  The filesystem store
    is intentionally deterministic and is used by unit/offline verification.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def artifact_key(digest: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ReleaseEvidenceIntegrityError("artifact_digest_invalid")
        return f"{ARTIFACT_KEY_PREFIX}/{digest[:2]}/{digest}"

    @staticmethod
    def evidence_key(evidence_kind: str, manifest_digest: str) -> str:
        if evidence_kind not in {"automated_qualification", "production_rehearsal"}:
            raise ReleaseEvidenceIntegrityError("evidence_kind_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
            raise ReleaseEvidenceIntegrityError("manifest_digest_invalid")
        return f"{EVIDENCE_KEY_PREFIX}/{evidence_kind}/{manifest_digest[:2]}/{manifest_digest}.json"

    def _path(self, key: str) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put_artifact(
        self,
        content: bytes,
        *,
        media_type: str,
        digest: str | None = None,
    ) -> str:
        if media_type not in {"application/json", "application/xml"}:
            raise ReleaseEvidenceIntegrityError("artifact_media_type_invalid")
        if not isinstance(content, bytes) or not content:
            raise ReleaseEvidenceIntegrityError("artifact_bytes_invalid")
        actual = sha256_bytes(content)
        target = digest or actual
        if target != actual:
            path = self._path(self.artifact_key(target))
            if path.exists() and path.read_bytes() != content:
                raise ReleaseEvidenceCollision("release evidence object collision")
            raise ReleaseEvidenceIntegrityError("artifact_digest_mismatch")
        path = self._path(self.artifact_key(target))
        if path.exists():
            if path.read_bytes() != content:
                raise ReleaseEvidenceCollision("release evidence object collision")
            return target
        path.write_bytes(content)
        if path.read_bytes() != content:
            raise ReleaseEvidenceIntegrityError("artifact_read_after_write_mismatch")
        return target

    def read_artifact(self, digest: str) -> bytes:
        path = self._path(self.artifact_key(digest))
        try:
            content = path.read_bytes()
        except OSError:
            raise ReleaseEvidenceIntegrityError("artifact_missing") from None
        if sha256_bytes(content) != digest:
            raise ReleaseEvidenceIntegrityError("artifact_digest_mismatch")
        return content

    def put_evidence_object(self, manifest: ReleaseEvidenceManifestV1, attestation: Any) -> str:
        for ref in manifest.artifact_refs:
            content = self.read_artifact(ref.sha256_digest)
            if len(content) != ref.byte_size:
                raise ReleaseEvidenceIntegrityError("artifact_size_mismatch")
        from app.release.contracts import _dump

        payload = {
            "manifest": _dump(manifest),
            "attestation": _dump(attestation),
        }
        assert_safe_evidence_payload(payload)
        encoded = __import__("app.schema.canonical", fromlist=["canonical_json_bytes"]).canonical_json_bytes(payload)
        key = self.evidence_key(manifest.evidence_kind, manifest.manifest_digest)
        path = self._path(key)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ReleaseEvidenceCollision("release evidence object collision")
            return key
        path.write_bytes(encoded)
        if path.read_bytes() != encoded:
            raise ReleaseEvidenceIntegrityError("evidence_read_after_write_mismatch")
        return key

    def read_evidence_object(self, evidence_kind: str, manifest_digest: str) -> bytes:
        path = self._path(self.evidence_key(evidence_kind, manifest_digest))
        try:
            return path.read_bytes()
        except OSError:
            raise ReleaseEvidenceIntegrityError("evidence_missing") from None


def verify_evidence_object(
    payload: dict[str, Any],
    trust_set: Any,
    *,
    now: Any = None,
) -> tuple[ReleaseEvidenceManifestV1, Any]:
    if set(payload) != {"manifest", "attestation"}:
        raise ReleaseEvidenceIntegrityError("evidence_object_shape_invalid")
    assert_safe_evidence_payload(payload)
    try:
        manifest = ReleaseEvidenceManifestV1.model_validate(payload["manifest"])
    except ValueError:
        raise ReleaseEvidenceIntegrityError("manifest_invalid") from None
    attestation = payload["attestation"]
    verify_release_attestation(manifest, attestation, trust_set, now=now)
    return manifest, attestation


__all__ = [
    "ARTIFACT_KEY_PREFIX",
    "ContentAddressedEvidenceStore",
    "EVIDENCE_KEY_PREFIX",
    "ReleaseEvidenceCollision",
    "ReleaseEvidenceIntegrityError",
    "assert_safe_evidence_payload",
    "verify_evidence_object",
]
