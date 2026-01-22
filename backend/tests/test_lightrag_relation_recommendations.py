"""Unit tests for LightRAG Entry Relation Recommendations API (Phase 5.5)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from app.entry.models import Entry, TimeMode  # noqa: E402
from app.entry_type.models import EntryType  # noqa: E402
from app.lightrag.router import router as lightrag_router  # noqa: E402
from app.lightrag.schemas import (  # noqa: E402
    LightRagQueryMetadata,
    LightRagQueryResponse,
    LightRagSource,
)
from app.relation.models import Relation, RelationType  # noqa: E402


class LightRagEntryRelationRecommendationsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(lightrag_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

        # Common entry type for entries
        self.et = EntryType(
            code="knowledge",
            name="Knowledge",
            graph_enabled=True,
            ai_enabled=True,
            enabled=True,
        )
        self.db.add(self.et)
        self.db.commit()
        self.db.refresh(self.et)

    def tearDown(self) -> None:
        self.db.close()

    def _create_entry(self, *, title: str) -> Entry:
        e = Entry(
            title=title,
            summary=f"summary {title}",
            content=f"content {title}",
            type_id=self.et.id,
            time_mode=TimeMode.NONE,
        )
        self.db.add(e)
        self.db.commit()
        self.db.refresh(e)
        return e

    def _mock_query_response(self, sources: list[LightRagSource]) -> LightRagQueryResponse:
        return LightRagQueryResponse(
            answer="{}",
            sources=sources,
            metadata=LightRagQueryMetadata(mode="hybrid", top_k=10, latency_ms=1, cache_hit=False),
        )

    def test_entry_not_found_returns_404(self) -> None:
        missing = uuid4()
        resp = self.client.get(f"/api/lightrag/entries/{missing}/relation-recommendations")
        self.assertEqual(resp.status_code, 404)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 40400)

    def test_recommendations_dedup_self_invalid_and_sort(self) -> None:
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")
        e3 = self._create_entry(title="C")

        sources = [
            LightRagSource(doc_id=str(e1.id), score=1.0, content="self"),  # should be filtered
            LightRagSource(doc_id=str(e2.id), score=0.9, content="b1"),
            LightRagSource(doc_id=str(e2.id), score=0.95, content="b2"),  # dedup -> max score
            LightRagSource(doc_id=str(e3.id), score=0.8, content="c1"),
            LightRagSource(doc_id="not-a-uuid", score=0.99, content="bad"),  # ignored
        ]
        with patch("app.lightrag.service.LightRagService.recall_sources", new=AsyncMock(return_value=sources)):
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        items = payload["data"]["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["targetEntryId"], str(e2.id))
        self.assertEqual(items[1]["targetEntryId"], str(e3.id))
        self.assertGreaterEqual(items[0]["score"], items[1]["score"])

    def test_min_score_and_limit(self) -> None:
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")
        e3 = self._create_entry(title="C")
        e4 = self._create_entry(title="D")

        sources = [
            LightRagSource(doc_id=str(e2.id), score=0.05, content="b"),
            LightRagSource(doc_id=str(e3.id), score=0.2, content="c"),
            LightRagSource(doc_id=str(e4.id), score=0.4, content="d"),
        ]
        with patch("app.lightrag.service.LightRagService.recall_sources", new=AsyncMock(return_value=sources)):
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?min_score=0.15&limit=1")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        items = payload["data"]["items"]
        # min_score=0.15 filters out e2(0.05), keeps e3(0.2) and e4(0.4)
        # limit=1 returns only highest (e4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["targetEntryId"], str(e4.id))

    def test_exclude_existing_relations_filters_targets(self) -> None:
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")
        e3 = self._create_entry(title="C")

        rt = RelationType(code="RELATED", name="Related", directed=True, enabled=True)
        self.db.add(rt)
        self.db.commit()
        self.db.refresh(rt)

        rel = Relation(source_entry_id=e1.id, target_entry_id=e2.id, relation_type_id=rt.id, description=None)
        self.db.add(rel)
        self.db.commit()

        sources = [
            LightRagSource(doc_id=str(e2.id), score=0.9, content="b"),
            LightRagSource(doc_id=str(e3.id), score=0.8, content="c"),
        ]
        # Patch both phase-1 recall and phase-2 query (phase-2 is enabled because RelationType exists)
        with (
            patch("app.lightrag.service.LightRagService.recall_sources", new=AsyncMock(return_value=sources)),
            patch("app.lightrag.service.LightRagService.llm_only_answer", new=AsyncMock(return_value="{}")),
        ):
            resp = self.client.get(
                f"/api/lightrag/entries/{e1.id}/relation-recommendations?exclude_existing_relations=true"
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        items = payload["data"]["items"]
        # e2 has existing relation, should be filtered out
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["targetEntryId"], str(e3.id))

    def test_filters_nonexistent_entries_from_sources(self) -> None:
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")

        ghost = uuid4()
        sources = [
            LightRagSource(doc_id=str(e2.id), score=0.9, content="b"),
            LightRagSource(doc_id=str(ghost), score=0.95, content="ghost"),
        ]
        with patch("app.lightrag.service.LightRagService.recall_sources", new=AsyncMock(return_value=sources)):
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        items = payload["data"]["items"]
        # ghost UUID doesn't exist in DB, should be filtered
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["targetEntryId"], str(e2.id))

    def test_invalid_limit_returns_422(self) -> None:
        e1 = self._create_entry(title="A")

        # limit < 1
        resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?limit=0")
        self.assertEqual(resp.status_code, 422)

        # limit > 100
        resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?limit=101")
        self.assertEqual(resp.status_code, 422)

    def test_invalid_min_score_returns_422(self) -> None:
        e1 = self._create_entry(title="A")

        # min_score < 0
        resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?min_score=-0.1")
        self.assertEqual(resp.status_code, 422)

        # min_score > 1
        resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?min_score=1.5")
        self.assertEqual(resp.status_code, 422)

    def test_mode_parameter_affects_query(self) -> None:
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")

        sources = [LightRagSource(doc_id=str(e2.id), score=0.9, content="b")]
        with patch("app.lightrag.service.LightRagService.recall_sources", new=AsyncMock(return_value=sources)) as mock_recall:
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?mode=local")

        self.assertEqual(resp.status_code, 200)
        # Verify that recall was called with mode="local"
        mock_recall.assert_called_once()
        call_kwargs = mock_recall.call_args.kwargs
        self.assertEqual(call_kwargs["mode"], "local")


if __name__ == "__main__":
    unittest.main()
