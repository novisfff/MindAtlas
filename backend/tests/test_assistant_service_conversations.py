from __future__ import annotations

import unittest
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class AssistantServiceConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_conversation_crud_and_list(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        c1 = svc.create_conversation(title="t1")
        c2 = svc.create_conversation(title="t2")

        all_items = svc.list_conversations()
        self.assertEqual({c.id for c in all_items}, {c1.id, c2.id})

        got = svc.get_conversation_basic(c1.id)
        self.assertEqual(got.id, c1.id)

        svc.delete_conversation(c2.id)
        self.assertEqual({c.id for c in svc.list_conversations()}, {c1.id})

    def test_get_conversation_404(self) -> None:
        from app.assistant.service import AssistantService  # noqa: E402

        svc = AssistantService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.get_conversation(UUID("00000000-0000-0000-0000-000000000001"))
        self.assertEqual(ctx.exception.status_code, 404)

