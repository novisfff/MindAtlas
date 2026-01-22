from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class LightRagOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry_type.models import EntryType  # noqa: E402

        self.entry_type = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add(self.entry_type)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_entry_create_writes_upsert_outbox_event(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.lightrag.models import EntryIndexOutbox  # noqa: E402

        svc = EntryService(self.db)
        entry = svc.create(
            EntryRequest(
                title="t",
                summary=None,
                content="c",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            )
        )

        events = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry.id)
            .order_by(EntryIndexOutbox.created_at.asc())
            .all()
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].op, "upsert")
        self.assertEqual(events[0].status, "pending")
        self.assertIsNotNone(events[0].entry_updated_at)

    def test_entry_delete_writes_delete_outbox_event(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.lightrag.models import EntryIndexOutbox  # noqa: E402

        svc = EntryService(self.db)
        entry = svc.create(
            EntryRequest(
                title="t",
                summary=None,
                content="c",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            )
        )

        svc.delete(entry.id)

        delete_events = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry.id, EntryIndexOutbox.op == "delete")
            .all()
        )
        self.assertEqual(len(delete_events), 1)
        self.assertEqual(delete_events[0].status, "pending")
        self.assertIsNone(delete_events[0].entry_updated_at)
