from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class StatsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402
        from app.relation.models import Relation, RelationType  # noqa: E402
        from app.tag.models import Tag  # noqa: E402

        t1 = EntryType(code="t1", name="T1", color="#1", graph_enabled=True, ai_enabled=True, enabled=True)
        t2 = EntryType(code="t2", name="T2", color="#2", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add_all([t1, t2])
        self.db.commit()

        e1 = Entry(
            title="e1",
            content=None,
            type_id=t1.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        e2 = Entry(
            title="e2",
            content=None,
            type_id=t1.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        e3 = Entry(
            title="e3",
            content=None,
            type_id=t2.id,
            time_mode=TimeMode.POINT,
            time_at=datetime.now(timezone.utc),
        )
        self.db.add_all([e1, e2, e3])
        self.db.commit()

        self.db.add_all([Tag(name="a"), Tag(name="b")])
        self.db.commit()

        rt = RelationType(code="ref", name="Ref", directed=True, enabled=True)
        self.db.add(rt)
        self.db.commit()

        self.db.add(Relation(source_entry_id=e1.id, target_entry_id=e2.id, relation_type_id=rt.id))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_dashboard_stats(self) -> None:
        from app.stats.service import StatsService  # noqa: E402

        svc = StatsService(self.db)
        out = svc.get_dashboard_stats()

        self.assertEqual(out.total_entries, 3)
        self.assertEqual(out.total_tags, 2)
        self.assertEqual(out.total_relations, 1)

        counts = {c.type_name: c.count for c in out.entries_by_type}
        self.assertEqual(counts["T1"], 2)
        self.assertEqual(counts["T2"], 1)

