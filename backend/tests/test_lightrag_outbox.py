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
        from app.tag.models import Tag  # noqa: E402

        self.entry_type = EntryType(code="t", name="T", graph_enabled=True, ai_enabled=True, enabled=True)
        self.entry_type2 = EntryType(code="t2", name="T2", graph_enabled=True, ai_enabled=True, enabled=True)
        self.tag1 = Tag(name="tag1", color=None, description=None)
        self.tag2 = Tag(name="tag2", color=None, description=None)
        self.db.add_all([self.entry_type, self.entry_type2, self.tag1, self.tag2])
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

    def test_entry_update_only_tags_and_type_does_not_enqueue_upsert(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.lightrag.models import EntryIndexOutbox  # noqa: E402

        svc = EntryService(self.db)
        entry = svc.create(
            EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
                tag_ids=[self.tag1.id],
            )
        )

        svc.update(
            entry.id,
            EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=self.entry_type2.id,  # only type changed
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
                tag_ids=[self.tag2.id],  # only tags changed
            ),
        )

        events = self.db.query(EntryIndexOutbox).filter(EntryIndexOutbox.entry_id == entry.id).all()
        self.assertEqual(len(events), 1)

    def test_entry_update_coalesces_when_active_upsert_exists(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.lightrag.models import EntryIndexOutbox  # noqa: E402

        svc = EntryService(self.db)
        entry = svc.create(
            EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            )
        )

        # Simulate that the initial create indexing succeeded, so update should enqueue a new one.
        first = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry.id, EntryIndexOutbox.op == "upsert")
            .first()
        )
        assert first is not None
        first.status = "succeeded"
        self.db.commit()

        svc.update(
            entry.id,
            EntryRequest(
                title="t2",
                summary="s",
                content="c2",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            ),
        )
        svc.update(
            entry.id,
            EntryRequest(
                title="t3",
                summary="s",
                content="c3",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            ),
        )

        upserts = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry.id, EntryIndexOutbox.op == "upsert")
            .order_by(EntryIndexOutbox.created_at.asc())
            .all()
        )
        # 1 from create (succeeded) + 1 active from updates (coalesced)
        self.assertEqual(len(upserts), 2)
        self.assertEqual(sum(1 for e in upserts if e.status == "pending"), 1)

    def test_entry_patch_time_does_not_enqueue_upsert(self) -> None:
        from app.entry.models import TimeMode  # noqa: E402
        from app.entry.schemas import EntryRequest, EntryTimePatch  # noqa: E402
        from app.entry.service import EntryService  # noqa: E402
        from app.lightrag.models import EntryIndexOutbox  # noqa: E402

        svc = EntryService(self.db)
        entry = svc.create(
            EntryRequest(
                title="t",
                summary="s",
                content="c",
                type_id=self.entry_type.id,
                time_mode=TimeMode.POINT,
                time_at=datetime.now(timezone.utc),
            )
        )

        # Ensure base indexing event is considered finished.
        first = (
            self.db.query(EntryIndexOutbox)
            .filter(EntryIndexOutbox.entry_id == entry.id, EntryIndexOutbox.op == "upsert")
            .first()
        )
        assert first is not None
        first.status = "succeeded"
        self.db.commit()

        svc.patch_time(
            entry.id,
            EntryTimePatch(time_at=datetime.now(timezone.utc)),
        )

        events = self.db.query(EntryIndexOutbox).filter(EntryIndexOutbox.entry_id == entry.id).all()
        self.assertEqual(len(events), 1)
