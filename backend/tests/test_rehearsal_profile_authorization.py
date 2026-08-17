from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest


def _authorization(*, expires_at: datetime | None = None):
    from app.release.profile_authorization import build_rehearsal_authorization

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    return build_rehearsal_authorization(
        authorization_id=UUID("00000000-0000-0000-0000-000000000010"),
        profile_run_id=UUID("00000000-0000-0000-0000-000000000011"),
        qualification_target_digest="1" * 64,
        initialization_fixture_digest="2" * 64,
        build_revision="release-20260814",
        image_set_digest="3" * 64,
        deployed_artifact_set_digest="4" * 64,
        schema_runtime_identity_digest="5" * 64,
        schema_contract_material_digest="6" * 64,
        dependency_lock_set_digest="7" * 64,
        scenario_set_digest="8" * 64,
        required_assertion_set_digest="9" * 64,
        runner_contract_version=1,
        runner_identity_digest="a" * 64,
        evidence_trust_set_digest="b" * 64,
        issued_at=now,
        expires_at=expires_at or now + timedelta(minutes=30),
        nonce=b"rehearsal-nonce",
        key_id="rehearsal-key",
        private_key_bytes=bytes(range(32)),
    )


def test_authorization_is_signed_and_binds_exact_rehearsal_subject() -> None:
    from app.release.profile_authorization import RehearsalAuthorizationVerifier

    signed = _authorization()
    verifier = RehearsalAuthorizationVerifier(signed.trust_key())
    assert verifier.verify(signed, now=signed.authorization.issued_at)
    assert verifier.allows_current_subject(
        signed,
        deployment_class="rehearsal",
        profile_run_id=signed.authorization.profile_run_id,
        qualification_target_digest=signed.authorization.qualification_target_digest,
        now=signed.authorization.issued_at,
    )


def test_authorization_rejects_expiry_profile_or_production_mismatch() -> None:
    from app.release.profile_authorization import RehearsalAuthorizationError, RehearsalAuthorizationVerifier

    signed = _authorization()
    verifier = RehearsalAuthorizationVerifier(signed.trust_key())
    with pytest.raises(RehearsalAuthorizationError, match="deployment"):
        verifier.allows_current_subject(
            signed,
            deployment_class="production",
            profile_run_id=signed.authorization.profile_run_id,
            qualification_target_digest=signed.authorization.qualification_target_digest,
            now=signed.authorization.issued_at,
        )
    with pytest.raises(RehearsalAuthorizationError, match="profile"):
        verifier.allows_current_subject(
            signed,
            deployment_class="rehearsal",
            profile_run_id=UUID("00000000-0000-0000-0000-000000000099"),
            qualification_target_digest=signed.authorization.qualification_target_digest,
            now=signed.authorization.issued_at,
        )
    expired = _authorization(
        expires_at=datetime(2026, 8, 14, 12, 1, tzinfo=timezone.utc)
    )
    expired_verifier = RehearsalAuthorizationVerifier(expired.trust_key())
    with pytest.raises(RehearsalAuthorizationError, match="expired"):
        expired_verifier.verify(expired, now=datetime(2026, 8, 14, 12, 2, tzinfo=timezone.utc))
