"""Plan 08 Task 5: call-owned approval binding and safe cards."""

from __future__ import annotations

import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class ApprovalBindingTests(unittest.TestCase):
    def test_binding_digest_stable_and_field_sensitive(self) -> None:
        from app.assistant.capability_calls.approval import (
            build_approval_binding,
            compute_approval_binding_digest,
        )

        call_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        d1 = compute_approval_binding_digest(
            call_id=call_id,
            logical_call_key="k1",
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=DIGEST_A,
            target_version_id=None,
            target_digest=DIGEST_B,
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            principal_digest=DIGEST_A,
            request_revision=1,
        )
        d2 = compute_approval_binding_digest(
            call_id=call_id,
            logical_call_key="k1",
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=DIGEST_A,
            target_version_id=None,
            target_digest=DIGEST_B,
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            principal_digest=DIGEST_A,
            request_revision=1,
        )
        self.assertEqual(d1, d2)
        d3 = compute_approval_binding_digest(
            call_id=call_id,
            logical_call_key="k1",
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=DIGEST_B,  # input drift
            target_version_id=None,
            target_digest=DIGEST_B,
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            principal_digest=DIGEST_A,
            request_revision=1,
        )
        self.assertNotEqual(d1, d3)
        binding = build_approval_binding(
            call_id=call_id,
            logical_call_key="k1",
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=DIGEST_A,
            target_digest=DIGEST_B,
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            principal_digest=DIGEST_A,
        )
        self.assertEqual(binding.approval_binding_digest, d1)
        self.assertEqual(binding.contract_version, 1)

    def test_redaction_and_safe_card(self) -> None:
        from app.assistant.capability_calls.approval import (
            redact_mapping,
            render_safe_approval_card,
        )

        raw = {
            "title": "hello",
            "api_key": "super-secret",
            "nested": {"password": "x", "ok": 1},
        }
        safe = redact_mapping(raw)
        self.assertEqual(safe["api_key"], "[redacted]")
        self.assertEqual(safe["nested"]["password"], "[redacted]")
        self.assertEqual(safe["nested"]["ok"], 1)
        card = render_safe_approval_card(
            action_label="Create entry",
            object_type="entry",
            side_effect_class="write_local",
            owner_label="smart-capture-golden",
            target_label="create_entry",
            fields=raw,
            execution_mode="local_transactional",
        )
        self.assertFalse(card.is_external)
        self.assertTrue(card.retryable)
        self.assertFalse(any("super-secret" in s for s in card.field_summaries))
        self.assertTrue(any("[redacted]" in s for s in card.field_summaries))


class ApprovalAuthorizeTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _seed_awaiting(self):
        from app.assistant.capability_calls.repository import (
            CapabilityCallRepository,
            ProposeCallSpec,
        )
        from app.assistant.durable.models import (
            AssistantRunArtifact,
            AssistantRunManifestRevision,
        )
        from app.assistant.durable.repository import LeaseToken
        from app.assistant.models import AssistantChatRun, Conversation
        import hashlib
        import os

        conv = Conversation(title="t")
        self.db.add(conv)
        self.db.flush()
        run = AssistantChatRun(
            conversation_id=conv.id,
            status="running",
            runtime_kind="main_agent",
            runtime_contract_version=1,
            required_app_build_revision="b1",
            capability_ledger_mode="enforced",
            state_revision=1,
            lease_owner="worker-1",
            lease_generation=1,
            memory_commit_status="pending",
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest=DIGEST_A,
            schema_version=1,
            payload={},
        )
        self.db.add(manifest)
        self.db.flush()
        payload = os.urandom(8)
        art = AssistantRunArtifact(
            run_id=run.id,
            kind="call_input",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(payload),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            inline_bytes=payload,
            metadata_json={},
        )
        self.db.add(art)
        self.db.flush()
        lease = LeaseToken(run_id=run.id, worker_id="worker-1", lease_generation=1)
        repo = CapabilityCallRepository(self.db)
        call_id = uuid.uuid4()
        call, _ = repo.create_or_verify_proposed(
            ProposeCallSpec(
                call_id=call_id,
                run_id=run.id,
                expected_run_revision=1,
                lease=lease,
                manifest_revision_id=manifest.id,
                logical_call_key="provider:0:0:write1",
                owner_kind="skill_version",
                owner_version_id=uuid.uuid4(),
                capability_type="tool",
                domain_key="create_entry",
                descriptor_digest=DIGEST_A,
                authorization_digest=DIGEST_A,
                input_artifact_id=art.id,
                input_digest=DIGEST_A,
                side_effect_class="write_local",
                execution_mode="local_transactional",
                idempotency_key="idem-" + uuid.uuid4().hex,
            )
        )
        call = repo.transition_call(
            call_id=call.id,
            expected_call_revision=0,
            expected_run_revision=1,
            to_status="awaiting_approval",
            lease=lease,
        )
        self.db.commit()
        return run, lease, repo, call

    def test_authorize_after_approval_preserves_authorization_digest(self) -> None:
        from app.assistant.capability_calls.approval import (
            authorize_call_after_approval,
            build_approval_binding,
        )

        run, lease, repo, call = self._seed_awaiting()
        binding = build_approval_binding(
            call_id=call.id,
            logical_call_key=call.logical_call_key,
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=call.input_digest,
            target_digest=DIGEST_B,
            descriptor_digest=call.descriptor_digest,
            authorization_digest=call.authorization_digest,
            principal_digest=DIGEST_A,
        )
        authorized = authorize_call_after_approval(
            repo=repo,
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=1,
            lease=lease,
            approval_binding=binding,
            expected_authorization_digest=DIGEST_A,
        )
        self.db.commit()
        self.assertEqual(authorized.status, "authorized")
        self.assertEqual(authorized.authorization_digest, DIGEST_A)
        self.assertEqual(
            authorized.approval_binding_digest, binding.approval_binding_digest
        )
        # No attempt claimed
        self.assertEqual(authorized.attempt_count, 0)

    def test_input_drift_rejects_approval(self) -> None:
        from app.assistant.capability_calls.approval import (
            authorize_call_after_approval,
            build_approval_binding,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, lease, repo, call = self._seed_awaiting()
        binding = build_approval_binding(
            call_id=call.id,
            logical_call_key=call.logical_call_key,
            owner_digest=DIGEST_A,
            binding_contract_digest=DIGEST_A,
            input_digest=DIGEST_B,  # drifted
            target_digest=DIGEST_B,
            descriptor_digest=call.descriptor_digest,
            authorization_digest=call.authorization_digest,
            principal_digest=DIGEST_A,
        )
        with self.assertRaises(CapabilityCallConflict):
            authorize_call_after_approval(
                repo=repo,
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=1,
                lease=lease,
                approval_binding=binding,
                expected_authorization_digest=DIGEST_A,
            )

    def test_reject_closes_without_attempt(self) -> None:
        from app.assistant.capability_calls.approval import close_non_approved_call

        run, lease, repo, call = self._seed_awaiting()
        closed = close_non_approved_call(
            repo=repo,
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=1,
            lease=lease,
            outcome="rejected",
        )
        self.db.commit()
        self.assertEqual(closed.status, "rejected")
        self.assertEqual(closed.attempt_count, 0)


if __name__ == "__main__":
    unittest.main()
