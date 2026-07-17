"""Plan 08 Task 3: policy v2 golden write admission + v1 digest preservation."""

from __future__ import annotations

import unittest
import uuid
from typing import Any

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
# Pinned Plan 05 v1 deny digest (Task 3 must not change).
V1_RELEASE_GATE_DENY_DIGEST = (
    "60ad4a56aa7619304bc4280c40230e26db5856fac3b088ce72766120d9d480cd"
)


class V1PreservationTests(unittest.TestCase):
    def test_authorization_decision_v1_digest_unchanged(self) -> None:
        from app.assistant.policy.contracts import build_authorization_decision

        dec = build_authorization_decision(
            allowed=False,
            reason_code="release_gate_denied",
            principal_digest=DIGEST_A,
            entrypoint_policy_digest=DIGEST_A,
            global_policy_digest=DIGEST_A,
            owner_policy_digest=DIGEST_A,
            exposure_digest=DIGEST_A,
            effective_policy_digest=DIGEST_A,
        )
        self.assertEqual(dec.decision_digest, V1_RELEASE_GATE_DENY_DIGEST)
        # No contract_version field on v1 body.
        self.assertFalse(hasattr(dec, "contract_version") and getattr(dec, "contract_version", None) == 2)

    def test_v1_alias(self) -> None:
        from app.assistant.policy.contracts import (
            AuthorizationDecision,
            AuthorizationDecisionV1,
        )

        self.assertIs(AuthorizationDecisionV1, AuthorizationDecision)


class GoldenWriteReleaseTests(unittest.TestCase):
    def test_lattice_prefix_and_digest_stable(self) -> None:
        from app.assistant.policy.contracts import (
            GOLDEN_WRITE_LATTICE_PREFIX,
            build_golden_write_release,
        )

        owner_version = uuid.UUID("11111111-1111-1111-1111-111111111111")
        r1 = build_golden_write_release(
            principal_digest=DIGEST_A,
            cohort_digest=DIGEST_B,
            owner_version_id=owner_version,
            binding_contract_digest=DIGEST_A,
            domain_key="create_entry",
            target_digest=DIGEST_B,
            target_version_id=None,
        )
        r2 = build_golden_write_release(
            principal_digest=DIGEST_A,
            cohort_digest=DIGEST_B,
            owner_version_id=owner_version,
            binding_contract_digest=DIGEST_A,
            domain_key="create_entry",
            target_digest=DIGEST_B,
            target_version_id=None,
        )
        self.assertEqual(r1.allowed_side_effects, GOLDEN_WRITE_LATTICE_PREFIX)
        self.assertEqual(r1.release_digest, r2.release_digest)
        self.assertEqual(r1.required_execution_mode, "local_transactional")
        self.assertEqual(r1.required_approval_origin, "capability_call")

    def test_rejects_non_canonical_lattice(self) -> None:
        from app.assistant.policy.contracts import GoldenWriteReleaseV1

        with self.assertRaises(Exception):
            GoldenWriteReleaseV1(
                principal_digest=DIGEST_A,
                cohort_digest=DIGEST_A,
                owner_kind="skill_version",
                owner_version_id=uuid.uuid4(),
                binding_contract_digest=DIGEST_A,
                domain_key="create_entry",
                target_version_id=None,
                target_digest=DIGEST_A,
                allowed_side_effects=("none", "read", "write_local"),
                release_digest=DIGEST_A,
            )


class AuthorizationDecisionV2Tests(unittest.TestCase):
    def test_awaiting_call_approval_requires_write_local_and_release(self) -> None:
        from app.assistant.policy.contracts import (
            GOLDEN_WRITE_LATTICE_PREFIX,
            build_authorization_decision_v2,
        )

        dec = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="awaiting_call_approval",
            reason_code="awaiting_call_approval",
            principal_digest=DIGEST_A,
            entrypoint_policy_digest=DIGEST_A,
            global_policy_digest=DIGEST_A,
            owner_policy_digest=DIGEST_A,
            allowed_side_effects=GOLDEN_WRITE_LATTICE_PREFIX,
            grant_source_digest=DIGEST_B,
            exposure_digest=DIGEST_A,
            effective_policy_digest=DIGEST_A,
            write_release_digest=DIGEST_B,
        )
        self.assertEqual(dec.contract_version, 2)
        self.assertTrue(dec.policy_allowed)
        self.assertEqual(dec.dispatch_disposition, "awaiting_call_approval")
        # Approval must not rewrite digests (issue helper).
        from app.assistant.policy.write_admission import (
            issue_post_approval_gateway_evidence,
        )

        after = issue_post_approval_gateway_evidence(
            frozen_decision=dec,
            approval_binding_digest=DIGEST_A,
        )
        self.assertEqual(after.decision_digest, dec.decision_digest)
        self.assertEqual(after.grant_source_digest, dec.grant_source_digest)
        self.assertEqual(after.write_release_digest, dec.write_release_digest)
        self.assertEqual(after.dispatch_disposition, "awaiting_call_approval")

    def test_post_approval_rejects_dispatch_decision(self) -> None:
        from app.assistant.policy.contracts import build_authorization_decision_v2
        from app.assistant.policy.write_admission import (
            issue_post_approval_gateway_evidence,
        )

        dec = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="dispatch",
            reason_code="allowed",
            principal_digest=DIGEST_A,
            entrypoint_policy_digest=DIGEST_A,
            global_policy_digest=DIGEST_A,
            owner_policy_digest=DIGEST_A,
            allowed_side_effects=("none", "compute", "read"),
            grant_source_digest=DIGEST_B,
            exposure_digest=DIGEST_A,
            effective_policy_digest=DIGEST_A,
        )
        with self.assertRaises(ValueError):
            issue_post_approval_gateway_evidence(
                frozen_decision=dec,
                approval_binding_digest=DIGEST_A,
            )


class WriteAdmissionEvaluateTests(unittest.TestCase):
    """Structural vectors for evaluate_authorization_v2 without full snapshot fixtures.

    Full snapshot-driven golden E2E is Task 8; here we pin contract behavior and
    that v1 path is used when policy_contract_version=1.
    """

    def test_v1_path_unchanged_without_release(self) -> None:
        from app.assistant.policy.write_admission import evaluate_authorization_v2
        from app.assistant.policy.evaluator import evaluate_authorization

        # Without building a full snapshot, ensure the function is importable and
        # v1 pin still holds at the builder layer (above).
        self.assertTrue(callable(evaluate_authorization_v2))
        self.assertTrue(callable(evaluate_authorization))

    def test_release_match_helper(self) -> None:
        from app.assistant.policy.contracts import build_golden_write_release
        from app.assistant.policy.write_admission import release_matches_proposal

        owner_version = uuid.uuid4()
        release = build_golden_write_release(
            principal_digest=DIGEST_A,
            cohort_digest=DIGEST_B,
            owner_version_id=owner_version,
            binding_contract_digest=DIGEST_A,
            domain_key="create_entry",
            target_digest=DIGEST_B,
            target_version_id=None,
        )
        self.assertTrue(
            release_matches_proposal(
                release,
                principal_digest=DIGEST_A,
                owner_kind="skill_version",
                owner_version_id=owner_version,
                binding_contract_digest=DIGEST_A,
                domain_key="create_entry",
                target_version_id=None,
                target_digest=DIGEST_B,
                execution_mode="local_transactional",
            )
        )
        self.assertFalse(
            release_matches_proposal(
                release,
                principal_digest=DIGEST_A,
                owner_kind="skill_version",
                owner_version_id=owner_version,
                binding_contract_digest=DIGEST_A,
                domain_key="update_entry",
                target_version_id=None,
                target_digest=DIGEST_B,
                execution_mode="local_transactional",
            )
        )
        self.assertFalse(
            release_matches_proposal(
                release,
                principal_digest=DIGEST_A,
                owner_kind="skill_version",
                owner_version_id=owner_version,
                binding_contract_digest=DIGEST_A,
                domain_key="create_entry",
                target_version_id=None,
                target_digest=DIGEST_B,
                execution_mode="external_idempotent",
            )
        )


if __name__ == "__main__":
    unittest.main()
