"""Unit tests for attachment -> LightRAG index outbox enqueue."""
from __future__ import annotations

import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class AttachmentLightRagOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_attachment_parse_worker_enqueues_attachment_index_outbox(self) -> None:
        from app.attachment.worker import Worker, WorkerConfig
        from app.lightrag.models import AttachmentIndexOutbox

        cfg = WorkerConfig(
            enabled=True,
            poll_interval_ms=10,
            batch_size=1,
            max_attempts=3,
            lock_ttl_sec=60,
            worker_id="t",
        )
        worker = Worker(cfg, session_factory=lambda: self.db)

        attachment_id = uuid4()
        entry_id = uuid4()

        worker._enqueue_attachment_index(self.db, attachment_id=attachment_id, entry_id=entry_id)

        row = self.db.query(AttachmentIndexOutbox).filter(AttachmentIndexOutbox.attachment_id == attachment_id).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.entry_id, entry_id)
        self.assertEqual(row.op, "upsert")
        self.assertEqual(row.status, "pending")

