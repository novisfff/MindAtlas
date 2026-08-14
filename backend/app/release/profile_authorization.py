"""Short-lived rehearsal-only authorization envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.release.contracts import (
    REHEARSAL_AUTH_DOMAIN,
    RehearsalProfileAuthorizationV1,
    SignedRehearsalProfileAuthorizationV1,
)
from app.release.trust import (
    ReleaseEvidenceSigner,
    ReleaseEvidenceTrustError,
    ReleaseEvidenceTrustKeyV1,
    ReleaseTrustSetV1,
    _b64url_decode,
    _b64url_encode,
)


class RehearsalAuthorizationError(ValueError):
    safe_code = "rehearsal_authorization_invalid"

    def __init__(self, safe_code: str, message: str | None = None) -> None:
        self.safe_code = safe_code
        super().__init__(message or safe_code)


@dataclass(frozen=True)
class RehearsalAuthorizationBundle:
    signed: SignedRehearsalProfileAuthorizationV1
    _trust_key: ReleaseEvidenceTrustKeyV1

    @property
    def authorization(self) -> RehearsalProfileAuthorizationV1:
        return self.signed.authorization

    @property
    def key_id(self) -> str:
        return self.signed.key_id

    @property
    def domain(self) -> str:
        return self.signed.domain

    def trust_key(self) -> ReleaseEvidenceTrustKeyV1:
        return self._trust_key

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.signed.model_dump(*args, **kwargs)


def build_rehearsal_authorization(
    *,
    key_id: str,
    private_key_bytes: bytes,
    nonce: bytes,
    **claims: Any,
) -> RehearsalAuthorizationBundle:
    authorization = RehearsalProfileAuthorizationV1.build(nonce=nonce, **claims)
    signer = ReleaseEvidenceSigner.from_private_key_bytes(
        key_id=key_id,
        private_key_bytes=private_key_bytes,
    )
    public = signer.trust_key()
    # The rehearsal domain is separate from release evidence even though both
    # use the same key primitive.
    signature = signer._private_key.sign(
        REHEARSAL_AUTH_DOMAIN.encode("ascii") + b"\x00" + bytes.fromhex(authorization.authorization_digest)
    )
    signed = SignedRehearsalProfileAuthorizationV1(
        domain="mindatlas:rehearsal-authorization:v1",
        key_id=key_id,
        authorization=authorization,
        signature_base64url=_b64url_encode(signature),
    )
    # Bind the independent domain permission to the same public key.
    public = public.model_copy(
        update={
            "allowed_domains": ("rehearsal_authorization",),
            "allowed_evidence_kinds": (),
        }
    )
    return RehearsalAuthorizationBundle(signed=signed, _trust_key=public)


class RehearsalAuthorizationVerifier:
    def __init__(self, trust: ReleaseTrustSetV1 | ReleaseEvidenceTrustKeyV1) -> None:
        self.trust = trust if isinstance(trust, ReleaseTrustSetV1) else ReleaseTrustSetV1.build((trust,))

    def _key(self, key_id: str) -> ReleaseEvidenceTrustKeyV1:
        key = next((item for item in self.trust.keys if item.key_id == key_id), None)
        if key is None:
            raise RehearsalAuthorizationError("trust_key_unknown")
        if key.revoked:
            raise RehearsalAuthorizationError("trust_key_revoked")
        if "rehearsal_authorization" not in key.allowed_domains:
            raise RehearsalAuthorizationError("rehearsal_domain_not_allowed")
        return key

    def verify(self, value: RehearsalAuthorizationBundle | SignedRehearsalProfileAuthorizationV1, *, now: datetime | None = None) -> bool:
        signed = value.signed if isinstance(value, RehearsalAuthorizationBundle) else value
        if signed.domain != "mindatlas:rehearsal-authorization:v1":
            raise RehearsalAuthorizationError("authorization_domain_invalid")
        key = self._key(signed.key_id)
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        authorization = signed.authorization
        if observed < authorization.issued_at:
            raise RehearsalAuthorizationError("authorization_not_yet_valid")
        if observed > authorization.expires_at:
            raise RehearsalAuthorizationError("authorization_expired")
        if observed < key.not_before or observed > key.not_after:
            raise RehearsalAuthorizationError("trust_key_outside_validity")
        try:
            public = Ed25519PublicKey.from_public_bytes(_b64url_decode(key.public_key_base64url))
            public.verify(
                _b64url_decode(signed.signature_base64url),
                REHEARSAL_AUTH_DOMAIN.encode("ascii") + b"\x00" + bytes.fromhex(authorization.authorization_digest),
            )
        except (ValueError, InvalidSignature):
            raise RehearsalAuthorizationError("authorization_signature_invalid") from None
        return True

    def allows_current_subject(
        self,
        value: RehearsalAuthorizationBundle | SignedRehearsalProfileAuthorizationV1,
        *,
        deployment_class: str,
        profile_run_id: UUID,
        qualification_target_digest: str,
        now: datetime | None = None,
        current_subject: dict[str, Any] | None = None,
    ) -> bool:
        self.verify(value, now=now)
        signed = value.signed if isinstance(value, RehearsalAuthorizationBundle) else value
        authorization = signed.authorization
        if deployment_class != "rehearsal":
            raise RehearsalAuthorizationError("deployment_class_mismatch")
        if authorization.deployment_class != "rehearsal":
            raise RehearsalAuthorizationError("deployment_class_mismatch")
        if authorization.profile_run_id != profile_run_id:
            raise RehearsalAuthorizationError("profile_run_mismatch")
        if authorization.qualification_target_digest != qualification_target_digest:
            raise RehearsalAuthorizationError("qualification_target_mismatch")
        if current_subject is not None:
            for field in (
                "build_revision",
                "image_set_digest",
                "deployed_artifact_set_digest",
                "schema_runtime_identity_digest",
                "schema_contract_material_digest",
                "dependency_lock_set_digest",
                "scenario_set_digest",
                "required_assertion_set_digest",
                "runner_identity_digest",
                "evidence_trust_set_digest",
            ):
                if field in current_subject and current_subject[field] != getattr(authorization, field):
                    raise RehearsalAuthorizationError("authorization_subject_mismatch")
        return True


__all__ = [
    "RehearsalAuthorizationBundle",
    "RehearsalAuthorizationError",
    "RehearsalAuthorizationVerifier",
    "build_rehearsal_authorization",
]
