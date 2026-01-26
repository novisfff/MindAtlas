"""Unit tests for LightRAG Entry Relation Recommendations API (Phase 5.5 + Phase 2)."""
from __future__ import annotations

import json
import math
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
from app.lightrag.service import (  # noqa: E402
    _extract_candidate_entry_ids,
    _parse_relation_recommendation_payload,
    RelationRecommendation,
)
from app.relation.models import Relation, RelationType  # noqa: E402


class ExtractCandidateEntryIdsTests(unittest.TestCase):
    """Tests for _extract_candidate_entry_ids helper function."""

    def test_empty_context_returns_empty_set(self) -> None:
        result = _extract_candidate_entry_ids({})
        self.assertEqual(result, set())

    def test_extracts_from_chunks_doc_id(self) -> None:
        id1 = uuid4()
        id2 = uuid4()
        ctx = {"chunks": [{"doc_id": str(id1)}, {"doc_id": str(id2)}]}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1, id2})

    def test_extracts_from_chunks_file_path(self) -> None:
        id1 = uuid4()
        ctx = {"chunks": [{"file_path": str(id1)}]}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1})

    def test_extracts_from_entities_entry_id(self) -> None:
        id1 = uuid4()
        id2 = uuid4()
        ctx = {"entities": [{"entry_id": str(id1)}, {"entry_id": str(id2)}]}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1, id2})

    def test_extracts_from_relationships_entry_id(self) -> None:
        id1 = uuid4()
        ctx = {"relationships": [{"entry_id": str(id1)}]}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1})

    def test_combines_all_sources(self) -> None:
        id1, id2, id3 = uuid4(), uuid4(), uuid4()
        ctx = {
            "chunks": [{"doc_id": str(id1)}],
            "entities": [{"entry_id": str(id2)}],
            "relationships": [{"entry_id": str(id3)}],
        }
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1, id2, id3})

    def test_deduplicates_across_sources(self) -> None:
        id1 = uuid4()
        ctx = {
            "chunks": [{"doc_id": str(id1)}],
            "entities": [{"entry_id": str(id1)}],
            "relationships": [{"entry_id": str(id1)}],
        }
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1})

    def test_drops_invalid_uuids(self) -> None:
        id1 = uuid4()
        ctx = {
            "chunks": [
                {"doc_id": str(id1)},
                {"doc_id": "not-a-uuid"},
                {"doc_id": ""},
                {"doc_id": None},
            ],
            "entities": [{"entry_id": "invalid"}],
        }
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1})

    def test_handles_none_and_missing_keys(self) -> None:
        ctx = {"chunks": None, "entities": None}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, set())

    def test_handles_non_dict_items(self) -> None:
        id1 = uuid4()
        ctx = {"chunks": [{"doc_id": str(id1)}, "string", 123, None]}
        result = _extract_candidate_entry_ids(ctx)
        self.assertEqual(result, {id1})


class ParseRelationRecommendationPayloadTests(unittest.TestCase):
    """Tests for _parse_relation_recommendation_payload function."""

    def setUp(self) -> None:
        self.id1 = uuid4()
        self.id2 = uuid4()
        self.candidates = {self.id1, self.id2}
        self.codes = ["RELATED", "USES", "BELONGS_TO"]

    def test_empty_answer_returns_empty_list(self) -> None:
        result = _parse_relation_recommendation_payload(
            "", allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(result, [])

    def test_empty_candidates_returns_empty_list(self) -> None:
        answer = json.dumps({"relations": [{"targetEntryId": str(self.id1), "relevance": 0.8}]})
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=set()
        )
        self.assertEqual(result, [])

    def test_parses_valid_json(self) -> None:
        answer = json.dumps({
            "relations": [
                {"targetEntryId": str(self.id1), "relationType": "RELATED", "relevance": 0.85},
                {"targetEntryId": str(self.id2), "relationType": "USES", "relevance": 0.70},
            ]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].target_id, self.id1)
        self.assertEqual(result[0].relation_type, "RELATED")
        self.assertAlmostEqual(result[0].relevance, 0.85)

    def test_filters_by_whitelist(self) -> None:
        unknown_id = uuid4()
        answer = json.dumps({
            "relations": [
                {"targetEntryId": str(self.id1), "relevance": 0.8},
                {"targetEntryId": str(unknown_id), "relevance": 0.9},
            ]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target_id, self.id1)

    def test_clamps_relevance_to_range(self) -> None:
        answer = json.dumps({
            "relations": [
                {"targetEntryId": str(self.id1), "relevance": 1.5},
                {"targetEntryId": str(self.id2), "relevance": -0.5},
            ]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].relevance, 1.0)
        self.assertEqual(result[1].relevance, 0.0)

    def test_drops_nan_and_infinity(self) -> None:
        # Can't use json.dumps for NaN/Infinity, test with string parsing
        answer = '{"relations": [{"targetEntryId": "' + str(self.id1) + '", "relevance": "NaN"}]}'
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(result, [])

    def test_drops_missing_relevance(self) -> None:
        answer = json.dumps({
            "relations": [{"targetEntryId": str(self.id1), "relationType": "RELATED"}]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(result, [])

    def test_deduplicates_with_max_relevance(self) -> None:
        answer = json.dumps({
            "relations": [
                {"targetEntryId": str(self.id1), "relationType": "RELATED", "relevance": 0.6},
                {"targetEntryId": str(self.id1), "relationType": "USES", "relevance": 0.9},
                {"targetEntryId": str(self.id1), "relationType": "BELONGS_TO", "relevance": 0.7},
            ]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].target_id, self.id1)
        self.assertAlmostEqual(result[0].relevance, 0.9)

    def test_handles_markdown_fence(self) -> None:
        inner = json.dumps({
            "relations": [{"targetEntryId": str(self.id1), "relevance": 0.8}]
        })
        answer = f"```json\n{inner}\n```"
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)

    def test_case_insensitive_relation_type(self) -> None:
        answer = json.dumps({
            "relations": [{"targetEntryId": str(self.id1), "relationType": "related", "relevance": 0.8}]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].relation_type, "RELATED")

    def test_invalid_relation_type_sets_none(self) -> None:
        answer = json.dumps({
            "relations": [{"targetEntryId": str(self.id1), "relationType": "UNKNOWN", "relevance": 0.8}]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].relation_type)

    def test_relevance_as_string(self) -> None:
        answer = json.dumps({
            "relations": [{"targetEntryId": str(self.id1), "relevance": "0.75"}]
        })
        result = _parse_relation_recommendation_payload(
            answer, allowed_relation_type_codes=self.codes, candidate_ids=self.candidates
        )
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].relevance, 0.75)


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

    def _make_graph_context(self, entry_ids: list) -> dict:
        """Build graph_context with chunks for given entry IDs."""
        return {
            "chunks": [{"doc_id": str(eid)} for eid in entry_ids],
            "entities": [],
            "relationships": [],
        }

    def _make_llm_answer(self, items: list[dict]) -> str:
        """Build LLM JSON answer from items."""
        return json.dumps({"relations": items})

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

        # Build graph_context with candidates (including self and invalid)
        graph_context = {
            "chunks": [
                {"doc_id": str(e1.id)},  # self - should be filtered
                {"doc_id": str(e2.id)},
                {"doc_id": str(e3.id)},
                {"doc_id": "not-a-uuid"},  # invalid - ignored
            ],
            "entities": [],
            "relationships": [],
        }
        # LLM returns recommendations with relevance
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e1.id), "relevance": 1.0},  # self - filtered by service
            {"targetEntryId": str(e2.id), "relevance": 0.95},
            {"targetEntryId": str(e3.id), "relevance": 0.8},
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)):
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

        graph_context = self._make_graph_context([e2.id, e3.id, e4.id])
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e2.id), "relevance": 0.25},  # Below HC-3 threshold
            {"targetEntryId": str(e3.id), "relevance": 0.35},  # Above HC-3 threshold
            {"targetEntryId": str(e4.id), "relevance": 0.5},   # Above HC-3 threshold
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)):
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?min_score=0.4&limit=1")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        items = payload["data"]["items"]
        # min_score=0.4 filters out e2(0.25) and e3(0.35), keeps e4(0.5)
        # limit=1 returns only highest (e4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["targetEntryId"], str(e4.id))

    def test_hc3_enforces_minimum_relevance_threshold(self) -> None:
        """HC-3: Server must filter out items with relevance < 0.30 regardless of min_score param."""
        e1 = self._create_entry(title="A")
        e2 = self._create_entry(title="B")
        e3 = self._create_entry(title="C")

        graph_context = self._make_graph_context([e2.id, e3.id])
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e2.id), "relevance": 0.29},  # Below HC-3 threshold
            {"targetEntryId": str(e3.id), "relevance": 0.31},  # Above HC-3 threshold
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)):
            # Even with min_score=0.1, HC-3 enforces 0.30 floor
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?min_score=0.1")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        items = payload["data"]["items"]
        # e2 (0.29) filtered by HC-3, only e3 (0.31) returned
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["targetEntryId"], str(e3.id))
        self.assertGreaterEqual(items[0]["score"], 0.30)

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

        graph_context = self._make_graph_context([e2.id, e3.id])
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e2.id), "relationType": "RELATED", "relevance": 0.9},
            {"targetEntryId": str(e3.id), "relationType": "RELATED", "relevance": 0.8},
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)):
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
        graph_context = self._make_graph_context([e2.id, ghost])
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e2.id), "relevance": 0.9},
            {"targetEntryId": str(ghost), "relevance": 0.95},
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)):
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

        graph_context = self._make_graph_context([e2.id])
        llm_answer = self._make_llm_answer([
            {"targetEntryId": str(e2.id), "relevance": 0.9}
        ])

        mock_return = (llm_answer, graph_context)
        with patch("app.lightrag.service.LightRagService.relation_recommend_with_llm", new=AsyncMock(return_value=mock_return)) as mock_recommend:
            resp = self.client.get(f"/api/lightrag/entries/{e1.id}/relation-recommendations?mode=local")

        self.assertEqual(resp.status_code, 200)
        # Verify that relation_recommend_with_llm was called with mode="local"
        mock_recommend.assert_called_once()
        call_kwargs = mock_recommend.call_args.kwargs
        self.assertEqual(call_kwargs["mode"], "local")


if __name__ == "__main__":
    unittest.main()
