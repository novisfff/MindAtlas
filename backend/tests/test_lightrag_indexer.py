"""Unit tests for LightRAG Indexer upsert behavior."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class _StubRuntime:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()

    def call(self, fn, *, timeout_sec=None):  # noqa: ANN001, D401
        return fn()

    def close(self) -> None:
        try:
            self.loop.close()
        except Exception:
            pass


class _StubRag:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.calls.append(("delete", doc_id))

    async def ainsert(self, text: str, *, ids=None, file_paths=None, file_path=None) -> str:  # noqa: ANN001
        self.calls.append(("insert", text, ids, file_paths, file_path))
        return "track-id"


class _StubRagDeleteFails(_StubRag):
    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.calls.append(("delete", doc_id))
        raise RuntimeError("boom")


class IndexerUpsertTests(unittest.TestCase):
    def test_upsert_replaces_existing_doc_id(self) -> None:
        from app.lightrag.indexer import Indexer

        runtime = _StubRuntime()
        rag = _StubRag()
        indexer = Indexer()
        entry_id = "133b7f95-5c82-49a0-bd91-b42e81f189d5"

        with patch("app.lightrag.runtime.get_lightrag_runtime", return_value=runtime):
            track_id = indexer._upsert_by_entry_id(rag, entry_id=entry_id, text="hello")

        runtime.close()

        self.assertEqual(track_id, "track-id")
        self.assertGreaterEqual(len(rag.calls), 2)
        self.assertEqual(rag.calls[0], ("delete", entry_id))
        self.assertEqual(rag.calls[1][0], "insert")
        self.assertEqual(rag.calls[1][2], [entry_id])
        self.assertEqual(rag.calls[1][3], [entry_id])

    def test_upsert_ignores_delete_errors(self) -> None:
        from app.lightrag.indexer import Indexer

        runtime = _StubRuntime()
        rag = _StubRagDeleteFails()
        indexer = Indexer()
        entry_id = "133b7f95-5c82-49a0-bd91-b42e81f189d5"

        with patch("app.lightrag.runtime.get_lightrag_runtime", return_value=runtime):
            track_id = indexer._upsert_by_entry_id(rag, entry_id=entry_id, text="hello")

        runtime.close()

        self.assertEqual(track_id, "track-id")
        self.assertEqual(rag.calls[0], ("delete", entry_id))
        self.assertEqual(rag.calls[1][0], "insert")

