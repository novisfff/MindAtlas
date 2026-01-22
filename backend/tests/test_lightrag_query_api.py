"""Unit tests for LightRAG Query API (Phase 5)."""
from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.lightrag.errors import LightRagConfigError  # noqa: E402
from app.lightrag.router import router as lightrag_router  # noqa: E402


class _FakeRag:
    def __init__(self, *, delay_sec: float = 0.0) -> None:
        self.delay_sec = delay_sec
        self.calls = 0

    class _FloatLike:
        def __init__(self, v: float) -> None:
            self.v = v

        def __float__(self) -> float:
            return float(self.v)

    def query(self, q: str, **kwargs):  # noqa: ANN001
        self.calls += 1
        if self.delay_sec:
            time.sleep(self.delay_sec)
        return {
            "answer": f"echo:{q}",
            "sources": [{"docId": "d1", "content": "c1", "score": self._FloatLike(0.9)}],
        }


class LightRagQueryApiTests(unittest.TestCase):
    def _make_app(self) -> FastAPI:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(lightrag_router)
        return app

    def setUp(self) -> None:
        reset_caches()
        os.environ["LIGHTRAG_ENABLED"] = "true"

    def tearDown(self) -> None:
        reset_caches()

    def test_query_ok_non_stream(self) -> None:
        app = self._make_app()
        client = TestClient(app)

        with patch("app.lightrag.service.get_rag", return_value=_FakeRag()):
            resp = client.post("/api/lightrag/query", json={"query": "hi", "mode": "hybrid", "topK": 3})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["code"], 0)
        self.assertIn("data", payload)
        self.assertEqual(payload["data"]["answer"], "echo:hi")
        self.assertEqual(payload["data"]["metadata"]["mode"], "hybrid")
        self.assertEqual(payload["data"]["metadata"]["topK"], 3)
        self.assertIsInstance(payload["data"]["sources"], list)
        self.assertAlmostEqual(payload["data"]["sources"][0]["score"], 0.9, places=6)

    def test_query_validation_error_missing_query(self) -> None:
        app = self._make_app()
        client = TestClient(app)

        resp = client.post("/api/lightrag/query", json={"mode": "hybrid"})
        self.assertEqual(resp.status_code, 422)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 42200)

    def test_query_disabled_404(self) -> None:
        os.environ["LIGHTRAG_ENABLED"] = "false"
        reset_caches()

        app = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/lightrag/query", json={"query": "hi"})
        self.assertEqual(resp.status_code, 404)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 40410)

    def test_query_stream_sse(self) -> None:
        app = self._make_app()
        client = TestClient(app)

        with patch("app.lightrag.service.get_rag", return_value=_FakeRag()):
            resp = client.post("/api/lightrag/query", json={"query": "hi", "stream": True})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
        body = resp.text
        self.assertIn("data:", body)

    def test_query_cache_hit_when_enabled(self) -> None:
        os.environ["LIGHTRAG_QUERY_CACHE_TTL_SEC"] = "3600"
        os.environ["LIGHTRAG_QUERY_CACHE_MAXSIZE"] = "128"
        reset_caches()
        self.addCleanup(lambda: os.environ.pop("LIGHTRAG_QUERY_CACHE_TTL_SEC", None))
        self.addCleanup(lambda: os.environ.pop("LIGHTRAG_QUERY_CACHE_MAXSIZE", None))

        app = self._make_app()
        client = TestClient(app)

        rag = _FakeRag()
        with patch("app.lightrag.service.get_rag", return_value=rag):
            resp1 = client.post("/api/lightrag/query", json={"query": "hi", "mode": "hybrid", "topK": 3})
            resp2 = client.post("/api/lightrag/query", json={"query": "hi", "mode": "hybrid", "topK": 3})

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        payload2 = resp2.json()
        self.assertTrue(payload2["success"])
        self.assertTrue(payload2["data"]["metadata"]["cacheHit"])
        self.assertEqual(rag.calls, 1)

    def test_query_timeout_504(self) -> None:
        os.environ["LIGHTRAG_QUERY_TIMEOUT_SEC"] = "0.01"
        reset_caches()

        app = self._make_app()
        client = TestClient(app)

        with patch("app.lightrag.service.get_rag", return_value=_FakeRag(delay_sec=0.1)):
            resp = client.post("/api/lightrag/query", json={"query": "slow"})

        self.assertEqual(resp.status_code, 504)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 50400)
        self.assertIn("timeout", payload["message"].lower())

    def test_query_config_error_hides_details(self) -> None:
        app = self._make_app()
        client = TestClient(app)

        with patch("app.lightrag.service.get_rag", side_effect=LightRagConfigError("Neo4j password missing (NEO4J_PASSWORD)")):
            resp = client.post("/api/lightrag/query", json={"query": "hi"})

        self.assertEqual(resp.status_code, 500)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["code"], 50011)
        self.assertEqual(payload["message"], "LightRAG config error")
        self.assertTrue(payload.get("data") in (None, ""))


if __name__ == "__main__":
    unittest.main()
