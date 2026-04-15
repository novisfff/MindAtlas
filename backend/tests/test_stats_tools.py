from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class StatsToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402
        from app.relation.models import Relation, RelationType  # noqa: E402
        from app.tag.models import Tag  # noqa: E402

        self.t1 = EntryType(code="t1", name="T1", color="#1", graph_enabled=True, ai_enabled=True, enabled=True)
        self.t2 = EntryType(code="t2", name="T2", color="#2", graph_enabled=True, ai_enabled=True, enabled=True)
        self.t3 = EntryType(code="t3", name="T3", color="#3", graph_enabled=True, ai_enabled=True, enabled=True)
        self.db.add_all([self.t1, self.t2, self.t3])
        self.db.commit()

        self.tag_a = Tag(name="alpha", color="#a")
        self.tag_b = Tag(name="beta", color="#b")
        self.tag_c = Tag(name="gamma", color="#c")
        self.db.add_all([self.tag_a, self.tag_b, self.tag_c])
        self.db.commit()

        self.e1 = Entry(
            title="e1",
            content=None,
            type_id=self.t1.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
        )
        self.e2 = Entry(
            title="e2",
            content=None,
            type_id=self.t1.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
        )
        self.e3 = Entry(
            title="e3",
            content=None,
            type_id=self.t2.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.e4 = Entry(
            title="e4",
            content=None,
            type_id=self.t2.id,
            time_mode=TimeMode.RANGE,
            time_from=datetime(2026, 3, 28, 0, 0, tzinfo=timezone.utc),
            time_to=datetime(2026, 4, 3, 23, 59, tzinfo=timezone.utc),
            created_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        self.e1.tags = [self.tag_a, self.tag_b]
        self.e2.tags = [self.tag_a]
        self.e3.tags = []
        self.e4.tags = [self.tag_b]
        self.db.add_all([self.e1, self.e2, self.e3, self.e4])
        self.db.commit()

        rt = RelationType(code="ref", name="Ref", directed=True, enabled=True)
        self.db.add(rt)
        self.db.commit()

        self.db.add(Relation(source_entry_id=self.e1.id, target_entry_id=self.e2.id, relation_type_id=rt.id))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _with_db(self, func, *args, **kwargs):
        from app.assistant.tools._context import reset_current_db, set_current_db  # noqa: E402

        token = set_current_db(self.db)
        try:
            return func(*args, **kwargs)
        finally:
            reset_current_db(token)

    def test_get_statistics_reuses_dashboard_totals_and_exposes_hotspots(self) -> None:
        from app.assistant.tools.stats_tools import get_statistics  # noqa: E402

        payload = json.loads(self._with_db(get_statistics))

        self.assertEqual(payload["total_entries"], 4)
        self.assertEqual(payload["total_tags"], 3)
        self.assertEqual(payload["total_relations"], 1)
        self.assertEqual(payload["total_types"], 3)
        self.assertEqual(payload["window_scope"], "all_time")
        self.assertEqual(payload["entries_by_type"]["T1"], 2)
        self.assertEqual(payload["entries_by_type"]["T2"], 2)
        self.assertEqual(payload["entries_by_type"]["T3"], 0)
        self.assertEqual(payload["entries_by_tag"]["alpha"], 2)
        self.assertEqual(payload["entries_by_tag"]["beta"], 2)
        self.assertEqual(payload["entries_by_tag"]["gamma"], 0)
        self.assertEqual([item["type_name"] for item in payload["top_types"]], ["T1", "T2"])
        self.assertEqual([item["name"] for item in payload["top_tags"]], ["alpha", "beta"])

    def test_get_statistics_scopes_overview_by_business_time_range(self) -> None:
        from app.assistant.tools.stats_tools import get_statistics  # noqa: E402

        payload = json.loads(
            self._with_db(
                get_statistics,
                start_date="2026-04-01",
                end_date="2026-04-10",
            )
        )

        self.assertEqual(payload["window_scope"], "custom_range")
        self.assertEqual(payload["window_start"], "2026-04-01")
        self.assertEqual(payload["window_end"], "2026-04-10")
        self.assertEqual(payload["time_basis"], "business_time")
        self.assertEqual(payload["total_entries"], 2)
        self.assertEqual(payload["total_types"], 2)
        self.assertEqual(payload["total_tags"], 2)
        self.assertEqual(payload["total_relations"], 1)
        self.assertEqual(payload["entries_by_type"], {"T1": 1, "T2": 1})
        self.assertEqual(payload["entries_by_tag"], {"beta": 2, "alpha": 1})
        self.assertEqual([item["type_name"] for item in payload["top_types"]], ["T1", "T2"])
        self.assertEqual([item["name"] for item in payload["top_tags"]], ["beta", "alpha"])

    def test_get_tag_statistics_returns_non_zero_top_tags_and_others_count(self) -> None:
        from app.assistant.tools.stats_tools import get_tag_statistics  # noqa: E402

        payload = json.loads(self._with_db(get_tag_statistics, top_n=1))

        self.assertEqual(payload["total_tags"], 3)
        self.assertEqual(payload["window_scope"], "all_time")
        self.assertEqual(payload["top_n"], 1)
        self.assertEqual(payload["others_count"], 1)
        self.assertEqual(len(payload["tags"]), 1)
        self.assertEqual(payload["tags"][0]["name"], "alpha")
        self.assertEqual(payload["tags"][0]["entry_count"], 2)

    def test_get_tag_statistics_scopes_to_business_time_window(self) -> None:
        from app.assistant.tools.stats_tools import get_tag_statistics  # noqa: E402

        payload = json.loads(
            self._with_db(
                get_tag_statistics,
                start_date="2026-04-03",
                end_date="2026-04-03",
                top_n=5,
            )
        )

        self.assertEqual(payload["window_scope"], "custom_range")
        self.assertEqual(payload["window_start"], "2026-04-03")
        self.assertEqual(payload["window_end"], "2026-04-03")
        self.assertEqual(payload["time_basis"], "business_time")
        self.assertEqual(payload["total_tags"], 1)
        self.assertEqual(payload["others_count"], 0)
        self.assertEqual([item["name"] for item in payload["tags"]], ["beta"])

    def test_analyze_activity_supports_new_relative_periods(self) -> None:
        from app.assistant.tools.stats_tools import analyze_activity  # noqa: E402

        with unittest.mock.patch("app.assistant.tools.stats_tools.utcnow", return_value=datetime(2026, 4, 14, tzinfo=timezone.utc)):
            payload = json.loads(self._with_db(analyze_activity, period="7d"))

        self.assertEqual(payload["resolved_period"], "7d")
        self.assertEqual(payload["days"], 7)
        self.assertEqual(payload["entries_created"], 2)
        self.assertEqual(payload["metric_scope"], "created_at")
        self.assertEqual(payload["active_buckets"], 2)
        self.assertEqual(payload["latest_bucket"]["date"], "2026-04-14")
        self.assertEqual(len(payload["trend"]), 7)

    def test_analyze_activity_uses_custom_date_range_over_period(self) -> None:
        from app.assistant.tools.stats_tools import analyze_activity  # noqa: E402

        payload = json.loads(
            self._with_db(
                analyze_activity,
                start_date="2026-04-01",
                end_date="2026-04-10",
                period="90d",
            )
        )

        self.assertEqual(payload["resolved_period"], "custom")
        self.assertEqual(payload["window_scope"], "custom_range")
        self.assertEqual(payload["start_date"], "2026-04-01")
        self.assertEqual(payload["end_date"], "2026-04-10")
        self.assertEqual(payload["entries_created"], 2)
        self.assertEqual(payload["peak_bucket"]["date"], "2026-04-01")
        self.assertEqual(payload["latest_bucket"]["date"], "2026-04-10")

    def test_analyze_activity_empty_window_reports_no_peak(self) -> None:
        from app.assistant.tools.stats_tools import analyze_activity  # noqa: E402
        from app.entry.models import Entry  # noqa: E402
        from app.relation.models import Relation  # noqa: E402

        self.db.query(Relation).delete()
        self.db.query(Entry).delete()
        self.db.commit()

        with unittest.mock.patch("app.assistant.tools.stats_tools.utcnow", return_value=datetime(2026, 4, 14, tzinfo=timezone.utc)):
            payload = json.loads(self._with_db(analyze_activity, period="90d"))

        self.assertEqual(payload["resolved_period"], "90d")
        self.assertEqual(payload["entries_created"], 0)
        self.assertEqual(payload["active_buckets"], 0)
        self.assertIsNone(payload["peak_bucket"])
        self.assertEqual(payload["latest_bucket"]["count"], 0)
