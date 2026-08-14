from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()



@unittest.skip('legacy HITL approval followup removed (Plan 10 B2)')
class AssistantServiceApprovalFollowupTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_conversation_with_pending_approval(self, *, assistant_content: str = ""):
        from app.assistant.models import Conversation, Message  # noqa: E402
        from app.assistant_config.models import AssistantHumanApproval  # noqa: E402

        conversation = Conversation(title="hitl")
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
        )
        self.db.add(assistant_message)
        self.db.commit()
        self.db.refresh(assistant_message)

        approval = AssistantHumanApproval(
            run_id="run_chat_approval_1",
            channel_type="assistant_chat",
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            node_id="human_confirm",
            node_label="人工确认",
            status="pending",
            request_payload={"instruction": "confirm", "requireRejectComment": False},
            field_schema=[{"name": "content", "type": "string", "required": False}],
            initial_values={"content": "draft"},
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return conversation, assistant_message, approval

    def test_submit_approval_backfills_empty_assistant_message_when_run_disconnected(self) -> None:
        from app.assistant.models import Message  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, assistant_message, approval = self._create_conversation_with_pending_approval(
            assistant_content="",
        )
        service = AssistantService(self.db)

        payload = service.submit_approval_decision(
            conversation_id=conversation.id,
            approval_id=approval.id,
            decision="approved",
            values={"content": "final"},
            comment=None,
        )

        self.assertFalse(payload.get("runtimeWaiting", True))
        self.db.refresh(assistant_message)
        self.assertIn("原对话连接已结束", assistant_message.content)
        count = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.role == "assistant")
            .count()
        )
        self.assertEqual(count, 1)

    def test_submit_approval_creates_new_followup_when_target_message_not_empty(self) -> None:
        from app.assistant.models import Message  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, assistant_message, approval = self._create_conversation_with_pending_approval(
            assistant_content="existing content",
        )
        service = AssistantService(self.db)

        _ = service.submit_approval_decision(
            conversation_id=conversation.id,
            approval_id=approval.id,
            decision="rejected",
            values={"content": "final"},
            comment=None,
        )

        self.db.refresh(assistant_message)
        self.assertEqual(assistant_message.content, "existing content")
        assistant_messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.role == "assistant")
            .order_by(Message.created_at.asc())
            .all()
        )
        self.assertEqual(len(assistant_messages), 2)
        self.assertIn("本次流程已停止", assistant_messages[-1].content)

    def test_runtime_waiting_but_stream_inactive_still_creates_followup(self) -> None:
        from app.assistant.models import Message  # noqa: E402
        from app.assistant.workflow.human_approval_runtime import GLOBAL_HUMAN_LOOP_COORDINATOR  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, assistant_message, approval = self._create_conversation_with_pending_approval(
            assistant_content="",
        )
        service = AssistantService(self.db)

        approval_id_str = str(approval.id)
        GLOBAL_HUMAN_LOOP_COORDINATOR.register(approval_id_str)
        try:
            payload = service.submit_approval_decision(
                conversation_id=conversation.id,
                approval_id=approval.id,
                decision="approved",
                values={"content": "final"},
                comment=None,
            )
        finally:
            GLOBAL_HUMAN_LOOP_COORDINATOR.unregister(approval_id_str)

        self.assertTrue(payload.get("runtimeWaiting", False))
        self.db.refresh(assistant_message)
        self.assertIn("原对话连接已结束", assistant_message.content)
        count = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.role == "assistant")
            .count()
        )
        self.assertEqual(count, 1)

    def test_runtime_waiting_with_active_stream_does_not_write_followup_for_empty_target(self) -> None:
        from app.assistant.workflow.human_approval_runtime import GLOBAL_HUMAN_LOOP_COORDINATOR  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, assistant_message, approval = self._create_conversation_with_pending_approval(
            assistant_content="",
        )
        service = AssistantService(self.db)

        approval_id_str = str(approval.id)
        run_id = "run_chat_approval_1"
        GLOBAL_HUMAN_LOOP_COORDINATOR.register(approval_id_str)
        AssistantService._mark_assistant_stream_active(run_id)
        try:
            payload = service.submit_approval_decision(
                conversation_id=conversation.id,
                approval_id=approval.id,
                decision="approved",
                values={"content": "final"},
                comment=None,
            )
        finally:
            AssistantService._mark_assistant_stream_inactive(run_id)
            GLOBAL_HUMAN_LOOP_COORDINATOR.unregister(approval_id_str)

        self.assertTrue(payload.get("runtimeWaiting", False))
        self.db.refresh(assistant_message)
        self.assertEqual(assistant_message.content, "")

    def test_runtime_waiting_with_active_stream_keeps_non_empty_target_message(self) -> None:
        from app.assistant.workflow.human_approval_runtime import GLOBAL_HUMAN_LOOP_COORDINATOR  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        conversation, assistant_message, approval = self._create_conversation_with_pending_approval(
            assistant_content="streaming content",
        )
        service = AssistantService(self.db)

        approval_id_str = str(approval.id)
        run_id = "run_chat_approval_1"
        GLOBAL_HUMAN_LOOP_COORDINATOR.register(approval_id_str)
        AssistantService._mark_assistant_stream_active(run_id)
        try:
            payload = service.submit_approval_decision(
                conversation_id=conversation.id,
                approval_id=approval.id,
                decision="approved",
                values={"content": "final"},
                comment=None,
            )
        finally:
            AssistantService._mark_assistant_stream_inactive(run_id)
            GLOBAL_HUMAN_LOOP_COORDINATOR.unregister(approval_id_str)

        self.assertTrue(payload.get("runtimeWaiting", False))
        self.db.refresh(assistant_message)
        self.assertEqual(assistant_message.content, "streaming content")


if __name__ == "__main__":
    unittest.main()
