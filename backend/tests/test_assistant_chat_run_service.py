from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class AssistantChatRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402

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

    def tearDown(self) -> None:
        self.db.close()

    def test_create_run_enforces_single_active_run(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
        )
        self.assertIsNotNone(run.id)
        self.assertIsNotNone(svc.get_active_run(conversation_id=self.conv.id))

        with self.assertRaises(ValueError):
            svc.create_run(
                conversation=self.conv,
                user_message=self.user_msg,
                assistant_message=self.assistant_msg,
            )

    def test_append_event_and_checkpoint(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402

        svc = AssistantChatRunService(self.db)
        run = svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
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
