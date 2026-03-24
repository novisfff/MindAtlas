from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class AssistantMemoryL1ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = make_session()

        from app.assistant.models import Conversation  # noqa: E402

        self.conv = Conversation(title="l1")
        self.db.add(self.conv)
        self.db.commit()
        self.db.refresh(self.conv)

    def tearDown(self) -> None:
        self.db.close()

    def test_get_l1_summary_returns_empty_when_not_exists(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        service = AssistantMemoryService(self.db)
        self.assertEqual(service.get_l1_summary(self.conv.id), "")

    def test_upsert_l1_summary_creates_row(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402
        from app.assistant.models import AssistantConversationL1Memory  # noqa: E402

        service = AssistantMemoryService(self.db)
        service.upsert_l1_summary(self.conv.id, "first summary")

        row = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == self.conv.id)
            .first()
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.summary_text, "first summary")

    def test_upsert_l1_summary_updates_same_conversation_row(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402
        from app.assistant.models import AssistantConversationL1Memory  # noqa: E402

        service = AssistantMemoryService(self.db)
        service.upsert_l1_summary(self.conv.id, "summary v1")
        service.upsert_l1_summary(self.conv.id, "summary v2")

        rows = (
            self.db.query(AssistantConversationL1Memory)
            .filter(AssistantConversationL1Memory.conversation_id == self.conv.id)
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].summary_text, "summary v2")

    def test_truncate_summary_applies_max_chars(self) -> None:
        from app.assistant.memory_service import AssistantMemoryService  # noqa: E402

        self.assertEqual(
            AssistantMemoryService.truncate_summary("abcdefgh", max_chars=5),
            "abcde",
        )
