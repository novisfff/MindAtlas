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
            target = getattr(func, "func", None)
            return (target if callable(target) else func)(*args, **kwargs)
        finally:
            reset_current_db(token)

    def test_with_db_calls_structured_tool_function(self) -> None:
        class _StructuredToolLike:
            def __init__(self) -> None:
                self.func = lambda *, value: value

            def __call__(self, *args, **kwargs):  # noqa: ANN002,ANN003
                raise TypeError("BaseTool.__call__ does not accept keyword arguments")

        self.assertEqual(self._with_db(_StructuredToolLike(), value="ok"), "ok")

    def test_create_entry_direct_call_requires_gateway_before_database_access(self) -> None:
        from app.assistant.capability_calls.create_entry_declaration import (  # noqa: E402
            CapabilityGatewayRequired,
        )
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        with self.assertRaises(CapabilityGatewayRequired) as ctx:
            self._with_db(
                create_entry,
                content="记录一下 OpenClaw 工作流的收敛方案。",
                type_code="",
            )
        self.assertEqual(ctx.exception.safe_code, "capability_gateway_required")

    def test_create_entry_direct_call_fails_closed_for_invalid_payload_too(self) -> None:
        from app.assistant.capability_calls.create_entry_declaration import (  # noqa: E402
            CapabilityGatewayRequired,
        )
        from app.assistant.tools.entry_tools import create_entry  # noqa: E402

        with self.assertRaises(CapabilityGatewayRequired) as ctx:
            self._with_db(
                create_entry,
                content="记录一下 OpenClaw 工作流的收敛方案。",
                type_code="NOT_A_REAL_TYPE",
            )
        self.assertEqual(ctx.exception.safe_code, "capability_gateway_required")

    def test_update_entry_is_an_unsupported_write_boundary(self) -> None:
        from app.assistant.capabilities.supported_writes import CapabilityNotSupported  # noqa: E402
        from app.assistant.tools.entry_tools import update_entry  # noqa: E402

        with self.assertRaises(CapabilityNotSupported) as ctx:
            self._with_db(
                update_entry,
                entry_id=str(self.existing_entry.id),
                content="更新已有记录",
                time_mode="RANGE",
                time_from="2026-04-03",
                time_to="2026-04-01",
            )

        self.assertEqual(ctx.exception.error.safe_code, "capability_not_supported")

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
