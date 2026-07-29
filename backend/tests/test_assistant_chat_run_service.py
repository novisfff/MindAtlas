from __future__ import annotations

import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding


bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_9 = "9" * 64


class AssistantChatRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402
        from app.assistant.runtime.contracts import (
            AssistantRuntimeSubject,
            PreparedRolloutRevision,
        )
        from app.assistant.runtime.repository import AssistantRuntimeRepository
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )

        self.conv = Conversation(title="run")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)

        self.user_msg = Message(conversation_id=self.conv.id, role="user", content="hi")
        self.assistant_msg = Message(conversation_id=self.conv.id, role="assistant", content="")
        self.db.add(self.user_msg)
        self.db.add(self.assistant_msg)
        self.db.commit()
        self.db.refresh(self.user_msg)
        self.db.refresh(self.assistant_msg)

        profile = AssistantMainAgentProfile(
            profile_key="default",
            display_name="Main Agent",
            is_default=True,
            migration_state="native",
            runtime_enabled=False,
        )
        self.db.add(profile)
        self.db.flush()
        profile_version = AssistantMainAgentProfileVersion(
            profile_id=profile.id,
            sequence_no=1,
            version_name="v1",
            version_source="save",
            origin="api",
            snapshot={"schemaVersion": 2},
            content_digest=DIGEST_A,
        )
        self.db.add(profile_version)
        self.db.flush()
        _cred, model, _binding = create_default_model_binding(self.db)
        subject = AssistantRuntimeSubject(
            profile_version_id=profile_version.id,
            profile_content_digest=DIGEST_A,
            model_id=model.id,
            model_identity_digest=DIGEST_B,
            package_closure=(),
            package_closure_digest=DIGEST_C,
            capability_closure_digest=DIGEST_D,
            seed_manifest_digest=DIGEST_E,
            build_revision="build-test-1",
            runtime_contract_version=1,
            checkpoint_codec_version=3,
            capability_feature_digest=DIGEST_F,
        )
        repo = AssistantRuntimeRepository(self.db)
        prepared = repo.create_prepared_revision(
            PreparedRolloutRevision.from_subject(
                subject=subject,
                revision_id=uuid.uuid4(),
                prepared_by_operator_id=None,
                prepared_reason="chat-run-service-test",
            )
        )
        self.db.commit()
        self._run_kwargs = {
            "main_agent_rollout_revision_id": prepared.id,
            "main_agent_profile_version_id": profile_version.id,
            "resolved_model_id": model.id,
            "runtime_closure_digest": DIGEST_9,
            "runtime_contract_version": 1,
            "required_checkpoint_codec_version": 3,
            "required_capability_feature_digest": DIGEST_F,
            "required_app_build_revision": "build-test-1",
            "capability_ledger_mode": "enforced",
            "commit": True,
        }

    def tearDown(self) -> None:
        self.db.close()

    def test_create_run_enforces_single_active_run(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
            **self._run_kwargs,
        )
        self.assertIsNotNone(run.id)
        self.assertEqual(run.runtime_kind, "main_agent")
        self.assertIsNotNone(svc.get_active_run(conversation_id=self.conv.id))

        with self.assertRaises(ValueError):
            svc.create_run(
                conversation=self.conv,
                user_message=self.user_msg,
                assistant_message=self.assistant_msg,
                **self._run_kwargs,
            )

    def test_append_event_and_checkpoint(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
            **self._run_kwargs,
        )
        seq1 = svc.append_event(run_id=run.id, event_name="message_start", payload={"messageId": str(self.assistant_msg.id)})
        seq2 = svc.append_event(run_id=run.id, event_name="content_delta", payload={"delta": "A"})
        self.assertEqual(seq1, 1)
        self.assertEqual(seq2, 2)

        svc.update_checkpoint(run_id=run.id, checkpoint_seq=1)
        svc.update_checkpoint(run_id=run.id, checkpoint_seq=2)
        refreshed = svc.get_run(conversation_id=self.conv.id, run_id=run.id)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.last_event_seq, 2)
        self.assertEqual(refreshed.checkpoint_seq, 2)

    def test_request_stop_idempotent(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
            **self._run_kwargs,
        )
        first = svc.request_stop(conversation_id=self.conv.id, run_id=run.id)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.status, "cancelling")
        second = svc.request_stop(conversation_id=self.conv.id, run_id=run.id)
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.status, "cancelling")


if __name__ == "__main__":
    unittest.main()
