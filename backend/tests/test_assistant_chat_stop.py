from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
from tests.assistant_runtime_support import seed_main_agent_runtime  # noqa: E402
reset_caches()



class AssistantChatStopTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation, Message  # noqa: E402

        self.conv = Conversation(title="stop")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)

        self.user_msg = Message(conversation_id=self.conv.id, role="user", content="hello")
        self.assistant_msg = Message(conversation_id=self.conv.id, role="assistant", content="")
        self.db.add(self.user_msg)
        self.db.add(self.assistant_msg)
        self.db.commit()
        self.db.refresh(self.user_msg)
        self.db.refresh(self.assistant_msg)

    def tearDown(self) -> None:
        self.db.close()

    def test_stop_run_marks_cancelled_for_queued_main_agent(self) -> None:
        from app.assistant.run_service import AssistantChatRunService  # noqa: E402
        from app.assistant.service import AssistantService  # noqa: E402

        run_svc = AssistantChatRunService(self.db)
        seeded = seed_main_agent_runtime(self.db, build_revision="build-stop-chat")
        run = run_svc.create_run(
            conversation=self.conv,
            user_message=self.user_msg,
            assistant_message=self.assistant_msg,
            **seeded.as_create_run_kwargs(),
        )
        svc = AssistantService(self.db)

        # Queued Main-Agent stop is direct cancel (no Legacy cancelling path).
        payload = svc.stop_run(conversation_id=self.conv.id, run_id=run.id)
        self.assertEqual(payload["status"], "cancelled")

        payload_2 = svc.stop_run(conversation_id=self.conv.id, run_id=run.id)
        self.assertEqual(payload_2["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
