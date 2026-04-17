from __future__ import annotations

import json
import unittest
from datetime import date as real_date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class EntryToolsValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode  # noqa: E402
        from app.entry_type.models import EntryType  # noqa: E402

        self.knowledge = EntryType(
            code="KNOWLEDGE",
            name="Knowledge",
            color="#1",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.project = EntryType(
            code="PROJECT",
            name="Project",
            color="#2",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add_all([self.knowledge, self.project])
        self.db.commit()

        self.existing_entry = Entry(
            title="Existing Entry",
            summary="seed",
            content="seed content",
            type_id=self.knowledge.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        self.project_entry = Entry(
            title="Project Tracker",
            summary="project seed",
            content="project seed content",
            type_id=self.project.id,
            time_mode=TimeMode.POINT,
            time_at=datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc),
        )
        self.db.add_all([self.existing_entry, self.project_entry])
        self.db.commit()
        self.db.refresh(self.existing_entry)
        self.db.refresh(self.project_entry)

    def tearDown(self) -> None:
        self.db.close()

    def _with_db(self, func, *args, **kwargs):
        from app.assistant.tools._context import reset_current_db, set_current_db  # noqa: E402

        token = set_current_db(self.db)
        try:
            return func(*args, **kwargs)
        finally:
            reset_current_db(token)

    def test_create_entry_blank_type_code_uses_default_enabled_type(self) -> None:
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        payload = json.loads(
            self._with_db(
                create_entry,
                content="记录一下 OpenClaw 工作流的收敛方案。",
                type_code="",
            )
        )

        self.assertEqual(payload["type_code"], "KNOWLEDGE")

    def test_create_entry_rejects_invalid_explicit_type_code(self) -> None:
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        with self.assertRaises(ValueError) as ctx:
            self._with_db(
                create_entry,
                content="记录一下 OpenClaw 工作流的收敛方案。",
                type_code="NOT_A_REAL_TYPE",
            )

        self.assertIn("无效的 type_code", str(ctx.exception))

    def test_create_entry_blank_time_defaults_to_point_today(self) -> None:
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        with patch("app.assistant.tools.entry_tools.date") as mocked_date:
            mocked_date.today.return_value = real_date(2026, 4, 15)
            payload = json.loads(
                self._with_db(
                    create_entry,
                    content="记录一下今天的工作流优化进度。",
                )
            )

        self.assertEqual(payload["time_mode"], "POINT")
        self.assertEqual(payload["time_at"], "2026-04-15")
        self.assertIsNone(payload["time_from"])
        self.assertIsNone(payload["time_to"])

    def test_create_entry_rejects_invalid_explicit_time_inputs(self) -> None:
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        invalid_cases = [
            {"time_mode": "POINT", "time_from": "2026-04-01", "time_to": "2026-04-02"},
            {"time_mode": "RANGE", "time_at": "2026-04-01", "time_from": "2026-04-01", "time_to": "2026-04-02"},
            {"time_mode": "RANGE", "time_from": "2026-04-01"},
            {"time_mode": "POINT", "time_at": "2026/04/01"},
        ]

        for kwargs in invalid_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self._with_db(
                        create_entry,
                        content="记录一下上下文入库的异常案例。",
                        **kwargs,
                    )

    def test_update_entry_rejects_invalid_explicit_time_range(self) -> None:
        from app.assistant.tools.entry_tools import update_entry  # noqa: E402

        with self.assertRaises(ValueError) as ctx:
            self._with_db(
                update_entry,
                entry_id=str(self.existing_entry.id),
                content="更新已有记录",
                time_mode="RANGE",
                time_from="2026-04-03",
                time_to="2026-04-01",
            )

        self.assertIn("time_from", str(ctx.exception))

    def test_search_similar_entries_groups_sources_by_entry_and_prioritizes_direct_entry_hits(self) -> None:
        from app.assistant.tools.entry_tools import build_search_similar_entries_payload  # noqa: E402
        from app.lightrag.schemas import LightRagSource  # noqa: E402

        recalled_sources = [
            LightRagSource(
                kind="attachment",
                entry_id=str(self.existing_entry.id),
                attachment_id=str(uuid4()),
                content="Attachment clue for existing entry.",
            ),
            LightRagSource(
                kind="entry",
                entry_id=str(self.project_entry.id),
                content="Project tracker direct match snippet.",
            ),
            LightRagSource(
                kind="entry",
                entry_id=str(self.project_entry.id),
                content="Project tracker direct match snippet.",
            ),
        ]

        with patch(
            "app.assistant.tools.entry_tools._recall_similar_sources",
            return_value=recalled_sources,
        ):
            payload = self._with_db(
                build_search_similar_entries_payload,
                query="project tracker",
                limit=5,
            )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["items"][0]["id"], str(self.project_entry.id))
        self.assertEqual(payload["items"][0]["retrieval_rank"], 1)
        self.assertEqual(payload["items"][0]["matched_source_kinds"], ["entry"])
        self.assertEqual(payload["items"][0]["matched_snippets"], ["Project tracker direct match snippet."])
        self.assertEqual(payload["items"][1]["id"], str(self.existing_entry.id))
        self.assertEqual(payload["items"][1]["matched_source_kinds"], ["attachment"])

    def test_search_similar_entries_returns_unavailable_when_lightrag_is_disabled(self) -> None:
        from app.assistant.tools.entry_tools import build_search_similar_entries_payload  # noqa: E402
        from app.common.exceptions import ApiException  # noqa: E402

        with patch(
            "app.assistant.tools.entry_tools._recall_similar_sources",
            side_effect=ApiException(status_code=404, code=40410, message="LightRAG is not enabled"),
        ):
            payload = self._with_db(
                build_search_similar_entries_payload,
                query="project tracker",
                limit=5,
            )

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["total"], 0)


if __name__ == "__main__":
    unittest.main()
