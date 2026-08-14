"""Ed25519 signing and public trust-set verification for release evidence."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import Field, model_validator

from app.assistant.domain.digests import sha256_canonical_json
from app.release.contracts import (
    MANIFEST_DIGEST_DOMAIN,
    ReleaseContract,
    ReleaseEvidenceManifestV1,
)


ATTESTATION_DOMAIN = b"mindatlas:release-evidence:v1\x00"
ATTESTATION_OBJECT_DIGEST_DOMAIN = "mindatlas:release-attestation-object:v1"
TRUST_SET_DOMAIN = "mindatlas:release-trust-set:v1"


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in value
    ):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class ReleaseEvidenceTrustError(ValueError):
    def __init__(self, safe_code: str, message: str | None = None) -> None:
        self.safe_code = safe_code
        super().__init__(message or safe_code)


class ReleaseEvidenceTrustKeyV1(ReleaseContract):
    schema_version: Literal[1] = 1
    key_id: str
    public_key_base64url: str = Field(min_length=43, max_length=44)
    allowed_domains: tuple[
        Literal["release_evidence", "deployed_artifact_identity", "rehearsal_authorization"],
        ...,
    ]
    allowed_evidence_kinds: tuple[
        Literal["automated_qualification", "production_rehearsal"],
        ...,
    ]
    not_before: datetime
    not_after: datetime
    revoked: bool = False

    @model_validator(mode="after")
    def validate_key(self) -> "ReleaseEvidenceTrustKeyV1":
        try:
            raw = _b64url_decode(self.public_key_base64url)
            if len(raw) != 32:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, TypeError):
            raise ValueError("invalid public key") from None
        if self.not_after <= self.not_before:
            raise ValueError("trust key validity interval is invalid")
        if "release_evidence" not in self.allowed_domains and self.allowed_evidence_kinds:
            raise ValueError("key without release_evidence domain cannot allow evidence kinds")
        return self


class ReleaseTrustSetV1(ReleaseContract):
    schema_version: Literal[1] = 1
    contract_version: Literal[1] = 1
    keys: tuple[ReleaseEvidenceTrustKeyV1, ...]
    trust_set_digest: str

    @classmethod
    def build(cls, keys: tuple[ReleaseEvidenceTrustKeyV1, ...] | list[ReleaseEvidenceTrustKeyV1]) -> "ReleaseTrustSetV1":
        values: dict[str, Any] = {"keys": tuple(sorted(keys, key=lambda item: item.key_id)), "trust_set_digest": "0" * 64}
        draft = cls.model_construct(**values)
        values["trust_set_digest"] = _trust_set_digest(draft)
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_set(self) -> "ReleaseTrustSetV1":
        ids = [item.key_id for item in self.keys]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate trust key id")
        if self.trust_set_digest != _trust_set_digest(self):
            raise ValueError("trust_set_digest does not match trust set")
        return self


def _trust_set_digest(trust_set: ReleaseTrustSetV1) -> str:
    return sha256_canonical_json(
        {
            "domain": TRUST_SET_DOMAIN,
            "contractVersion": trust_set.contract_version,
            "keys": [
                item.model_dump(mode="json", by_alias=True, exclude_none=False)
                for item in sorted(trust_set.keys, key=lambda key: key.key_id)
            ],
        }
    )


def attestation_object_digest(attestation: Any) -> str:
    """Hash the canonical signed-attestation envelope stored beside evidence."""
    from app.release.contracts import _dump

    payload = (
        dict(attestation)
        if isinstance(attestation, dict)
        else _dump(attestation)
    )
    return sha256_canonical_json(
        {
            "domain": ATTESTATION_OBJECT_DIGEST_DOMAIN,
            "attestation": payload,
        }
    )


class ReleaseEvidenceSigner:
    """Signer built only from already-open private-key bytes."""

    def __init__(self, *, key_id: str, private_key: Ed25519PrivateKey) -> None:
        self.key_id = key_id
        self._private_key = private_key

    @classmethod
    def from_private_key_bytes(cls, *, key_id: str, private_key_bytes: bytes) -> "ReleaseEvidenceSigner":
        if not isinstance(private_key_bytes, bytes) or len(private_key_bytes) != 32:
            raise ValueError("Ed25519 private key must be 32 bytes")
        return cls(key_id=key_id, private_key=Ed25519PrivateKey.from_private_bytes(private_key_bytes))

    def trust_key(
        self,
        *,
        not_before: datetime = datetime(2000, 1, 1, tzinfo=timezone.utc),
        not_after: datetime = datetime(2100, 1, 1, tzinfo=timezone.utc),
        revoked: bool = False,
    ) -> ReleaseEvidenceTrustKeyV1:
        public = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return ReleaseEvidenceTrustKeyV1(
            key_id=self.key_id,
            public_key_base64url=_b64url_encode(public),
            allowed_domains=("release_evidence",),
            allowed_evidence_kinds=("automated_qualification", "production_rehearsal"),
            not_before=not_before,
            not_after=not_after,
            revoked=revoked,
        )

    def sign(self, manifest: ReleaseEvidenceManifestV1):
        from app.release.contracts import SignedReleaseAttestationV1

        signature = self._private_key.sign(
            ATTESTATION_DOMAIN + bytes.fromhex(manifest.manifest_digest)
        )
        return SignedReleaseAttestationV1(
            schema_version=1,
            domain="mindatlas:release-evidence:v1",
            key_id=self.key_id,
            manifest_digest=manifest.manifest_digest,
            signature_base64url=_b64url_encode(signature),
        )


def _as_trust_set(value: ReleaseTrustSetV1 | ReleaseEvidenceTrustKeyV1) -> ReleaseTrustSetV1:
    if isinstance(value, ReleaseTrustSetV1):
        return value
    if isinstance(value, ReleaseEvidenceTrustKeyV1):
        return ReleaseTrustSetV1.build((value,))
    raise ReleaseEvidenceTrustError("trust_set_invalid")


def verify_release_attestation(
    manifest: ReleaseEvidenceManifestV1,
    attestation: Any,
    trust_set: ReleaseTrustSetV1 | ReleaseEvidenceTrustKeyV1,
    *,
    now: datetime | None = None,
):
    from app.release.contracts import SignedReleaseAttestationV1

    if not isinstance(manifest, ReleaseEvidenceManifestV1):
        raise ReleaseEvidenceTrustError("manifest_invalid")
    try:
        envelope = (
            attestation
            if isinstance(attestation, SignedReleaseAttestationV1)
            else SignedReleaseAttestationV1.model_validate(attestation)
        )
    except ValueError:
        raise ReleaseEvidenceTrustError("attestation_invalid") from None
    if envelope.domain != "mindatlas:release-evidence:v1":
        raise ReleaseEvidenceTrustError("attestation_domain_invalid")
    if envelope.manifest_digest != manifest.manifest_digest:
        raise ReleaseEvidenceTrustError("manifest_digest_mismatch")

    trust = _as_trust_set(trust_set)
    key = next((item for item in trust.keys if item.key_id == envelope.key_id), None)
    if key is None:
        raise ReleaseEvidenceTrustError("trust_key_unknown")
    if key.revoked:
        raise ReleaseEvidenceTrustError("trust_key_revoked", "revoked trust key")
    if "release_evidence" not in key.allowed_domains:
        raise ReleaseEvidenceTrustError("trust_domain_not_allowed")
    if manifest.evidence_kind not in key.allowed_evidence_kinds:
        raise ReleaseEvidenceTrustError("evidence_kind_not_allowed")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if observed_at < key.not_before or observed_at > key.not_after:
        raise ReleaseEvidenceTrustError("trust_key_outside_validity")
    if manifest.ended_at < key.not_before or manifest.ended_at > key.not_after:
        raise ReleaseEvidenceTrustError("evidence_outside_key_validity")
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64url_decode(key.public_key_base64url))
        public.verify(
            _b64url_decode(envelope.signature_base64url),
            ATTESTATION_DOMAIN + bytes.fromhex(manifest.manifest_digest),
        )
    except (ValueError, InvalidSignature):
        raise ReleaseEvidenceTrustError("attestation_signature_invalid") from None
    return envelope


def load_trust_set(path: Path) -> ReleaseTrustSetV1:
    try:
        return ReleaseTrustSetV1.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ReleaseEvidenceTrustError("trust_set_invalid") from None


__all__ = [
    "ATTESTATION_DOMAIN",
    "ReleaseEvidenceSigner",
    "ReleaseEvidenceTrustError",
    "ReleaseEvidenceTrustKeyV1",
    "ReleaseTrustSetV1",
    "attestation_object_digest",
    "load_trust_set",
    "verify_release_attestation",
]
