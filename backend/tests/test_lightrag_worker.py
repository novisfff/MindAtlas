"""Unit tests for LightRAG worker state machine and outbox repo."""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class OutboxRepoTests(unittest.TestCase):
    """Tests for OutboxRepo claim/mark operations."""

    def setUp(self) -> None:
        self.db = make_session()

        from app.entry_type.models import EntryType
        from app.entry.models import Entry, TimeMode
        from app.lightrag.models import EntryIndexOutbox

        # Create entry type
        self.entry_type = EntryType(
            code="test", name="Test", graph_enabled=True, ai_enabled=True, enabled=True
        )
        self.db.add(self.entry_type)
        self.db.commit()

        # Create test entry
        self.entry = Entry(
            title="Test Entry",
            content="Test content",
            type_id=self.entry_type.id,
            time_mode=TimeMode.NONE,
        )
        self.db.add(self.entry)
        self.db.commit()
        self.db.refresh(self.entry)

    def tearDown(self) -> None:
        self.db.close()

    def test_claim_batch_pending_to_processing(self) -> None:
        """Pending messages should be claimed and transitioned to processing."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        # Create pending outbox event
        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="pending",
            attempts=0,
            available_at=now - timedelta(seconds=10),
        )
        self.db.add(outbox)
        self.db.commit()
        outbox_id = outbox.id

        repo = OutboxRepo(self.db)
        result = repo.claim_batch(
            now=now,
            batch_size=10,
            worker_id="test-worker",
            lock_ttl_sec=300,
            max_attempts=6,
        )

        self.assertEqual(len(result.claimed), 1)

        # Verify state changes
        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "processing")
        self.assertEqual(outbox.attempts, 1)
        self.assertEqual(outbox.locked_by, "test-worker")
        self.assertIsNotNone(outbox.locked_at)

    def test_claim_batch_respects_max_attempts(self) -> None:
        """Messages at max attempts should not be claimed."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        # Create outbox at max attempts
        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="pending",
            attempts=6,  # max_attempts
            available_at=now - timedelta(seconds=10),
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        result = repo.claim_batch(
            now=now,
            batch_size=10,
            worker_id="test-worker",
            lock_ttl_sec=300,
            max_attempts=6,
        )

        self.assertEqual(len(result.claimed), 0)

    def test_claim_batch_expired_lock_can_be_reclaimed(self) -> None:
        """Processing messages with expired locks should be reclaimable."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)
        lock_ttl_sec = 300

        # Create processing message with expired lock
        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now - timedelta(seconds=600),
            locked_at=now - timedelta(seconds=400),  # Older than lock_ttl
            locked_by="crashed-worker",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        result = repo.claim_batch(
            now=now,
            batch_size=10,
            worker_id="new-worker",
            lock_ttl_sec=lock_ttl_sec,
            max_attempts=6,
        )

        self.assertEqual(len(result.claimed), 1)

        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "processing")
        self.assertEqual(outbox.attempts, 2)  # Incremented
        self.assertEqual(outbox.locked_by, "new-worker")

    def test_claim_batch_active_lock_not_reclaimed(self) -> None:
        """Processing messages with active locks should not be reclaimed."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)
        lock_ttl_sec = 300

        # Create processing message with active lock
        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now - timedelta(seconds=600),
            locked_at=now - timedelta(seconds=100),  # Within lock_ttl
            locked_by="active-worker",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        result = repo.claim_batch(
            now=now,
            batch_size=10,
            worker_id="new-worker",
            lock_ttl_sec=lock_ttl_sec,
            max_attempts=6,
        )

        self.assertEqual(len(result.claimed), 0)

    def test_mark_succeeded(self) -> None:
        """mark_succeeded should update status and clear lock fields."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="worker",
            last_error="previous error",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        repo.mark_succeeded(outbox_id=outbox.id)

        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "succeeded")
        self.assertIsNone(outbox.locked_at)
        self.assertIsNone(outbox.locked_by)
        self.assertIsNone(outbox.last_error)

    def test_mark_retry(self) -> None:
        """mark_retry should update status, available_at, and error."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)
        next_available = now + timedelta(seconds=60)

        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="worker",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        repo.mark_retry(
            outbox_id=outbox.id,
            next_available_at=next_available,
            error_message="test error",
        )

        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "pending")
        # SQLite doesn't preserve timezone info, compare timestamps only
        self.assertEqual(
            outbox.available_at.replace(tzinfo=None),
            next_available.replace(tzinfo=None),
        )
        self.assertIsNone(outbox.locked_at)
        self.assertIsNone(outbox.locked_by)
        self.assertEqual(outbox.last_error, "test error")

    def test_mark_dead(self) -> None:
        """mark_dead should update status to dead and record error."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=6,
            available_at=now,
            locked_at=now,
            locked_by="worker",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db)
        repo.mark_dead(
            outbox_id=outbox.id,
            error_message="max attempts exceeded",
        )

        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "dead")
        self.assertIsNone(outbox.locked_at)
        self.assertIsNone(outbox.locked_by)
        self.assertEqual(outbox.last_error, "max attempts exceeded")


class BackoffTests(unittest.TestCase):
    """Tests for exponential backoff calculation."""

    def test_backoff_increases_with_attempts(self) -> None:
        """Backoff delay should increase with attempts."""
        from app.lightrag.outbox_repo import compute_backoff

        backoff1 = compute_backoff(1, base_sec=2.0, cap_sec=60.0)
        backoff2 = compute_backoff(2, base_sec=2.0, cap_sec=60.0)
        backoff3 = compute_backoff(3, base_sec=2.0, cap_sec=60.0)

        # Due to jitter, we check the expected base values
        # attempts=1: base * 2^0 = 2 sec
        # attempts=2: base * 2^1 = 4 sec
        # attempts=3: base * 2^2 = 8 sec
        self.assertLessEqual(backoff1.total_seconds(), 2.0 * 1.1)  # With 10% jitter
        self.assertGreaterEqual(backoff1.total_seconds(), 2.0)

        self.assertLessEqual(backoff2.total_seconds(), 4.0 * 1.1)
        self.assertGreaterEqual(backoff2.total_seconds(), 4.0)

        self.assertLessEqual(backoff3.total_seconds(), 8.0 * 1.1)
        self.assertGreaterEqual(backoff3.total_seconds(), 8.0)

    def test_backoff_respects_cap(self) -> None:
        """Backoff should not exceed the cap."""
        from app.lightrag.outbox_repo import compute_backoff

        # Very high attempts should still be capped
        backoff = compute_backoff(20, base_sec=2.0, cap_sec=60.0)

        # Max is cap + 10% jitter
        self.assertLessEqual(backoff.total_seconds(), 60.0 * 1.1)


class IndexerTests(unittest.TestCase):
    """Tests for Indexer wrapper behavior without real external dependencies."""

    def setUp(self) -> None:
        # Ensure skip path is deterministic for unit tests.
        os.environ["LIGHTRAG_ENABLED"] = "false"
        reset_caches()

    def tearDown(self) -> None:
        os.environ.pop("LIGHTRAG_ENABLED", None)
        reset_caches()

    def test_handle_upsert_skips_when_disabled(self) -> None:
        """handle() should fast-skip when LIGHTRAG_ENABLED=false."""
        from app.lightrag.indexer import Indexer
        from app.lightrag.types import IndexRequest

        indexer = Indexer()
        req = IndexRequest(
            outbox_id=uuid4(),
            entry_id=uuid4(),
            op="upsert",
            entry_updated_at=datetime.now(timezone.utc),
            attempts=1,
        )

        result = indexer.handle(req)

        self.assertTrue(result.ok)
        self.assertIn("skipped", (result.detail or "").lower())

    def test_handle_delete_skips_when_disabled(self) -> None:
        """handle() should fast-skip delete when LIGHTRAG_ENABLED=false."""
        from app.lightrag.indexer import Indexer
        from app.lightrag.types import IndexRequest

        indexer = Indexer()
        req = IndexRequest(
            outbox_id=uuid4(),
            entry_id=uuid4(),
            op="delete",
            entry_updated_at=None,
            attempts=1,
        )

        result = indexer.handle(req)

        self.assertTrue(result.ok)
        self.assertIn("skipped", (result.detail or "").lower())

    def test_handle_invalid_op(self) -> None:
        """handle() should reject invalid op values."""
        from app.lightrag.indexer import Indexer
        from app.lightrag.types import IndexRequest

        indexer = Indexer()
        req = IndexRequest(
            outbox_id=uuid4(),
            entry_id=uuid4(),
            op="invalid_op",
            entry_updated_at=None,
            attempts=1,
        )

        result = indexer.handle(req)

        self.assertFalse(result.ok)
        self.assertFalse(bool(result.retryable))
        self.assertIn("invalid op", result.detail or "")

    def test_handle_upsert_uses_runtime_loop(self) -> None:
        """handle(upsert) should run ainsert on the LightRAG runtime loop."""
        prev = os.environ.get("LIGHTRAG_ENABLED")
        os.environ["LIGHTRAG_ENABLED"] = "true"
        reset_caches()

        def _restore() -> None:
            if prev is None:
                os.environ.pop("LIGHTRAG_ENABLED", None)
            else:
                os.environ["LIGHTRAG_ENABLED"] = prev
            reset_caches()

        self.addCleanup(_restore)

        from app.lightrag.indexer import Indexer
        from app.lightrag.types import DocumentPayload, IndexRequest

        class _FakeLoop:
            def __init__(self) -> None:
                self.last_awaitable = None

            def run_until_complete(self, awaitable):  # noqa: ANN001
                self.last_awaitable = awaitable
                return "track-123"

        class _FakeRuntime:
            def __init__(self) -> None:
                self.loop = _FakeLoop()
                self.called = False

            def call(self, fn, *, timeout_sec=None):  # noqa: ANN001
                self.called = True
                return fn()

        class _FakeRag:
            def __init__(self) -> None:
                self.ainsert_calls: list[dict] = []

            def insert(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError("sync insert() should not be used (loop mismatch risk)")

            def ainsert(self, *args, **kwargs):  # noqa: ANN001
                self.ainsert_calls.append({"args": args, "kwargs": kwargs})
                return object()

        fake_runtime = _FakeRuntime()
        fake_rag = _FakeRag()

        indexer = Indexer()
        payload = DocumentPayload(
            entry_id=uuid4(),
            entry_updated_at=datetime.now(timezone.utc),
            type_id=uuid4(),
            type_code="t",
            type_name="T",
            type_enabled=True,
            graph_enabled=True,
            ai_enabled=True,
            title="Hello",
            summary=None,
            content="World",
            tags=[],
            tag_ids=[],
            text="Title: Hello\nContent: World\n",
        )
        req = IndexRequest(
            outbox_id=uuid4(),
            entry_id=payload.entry_id,
            op="upsert",
            entry_updated_at=payload.entry_updated_at,
            attempts=1,
            payload=payload,
        )

        with patch("app.lightrag.indexer.get_rag", return_value=fake_rag), patch(
            "app.lightrag.runtime.get_lightrag_runtime", return_value=fake_runtime
        ):
            result = indexer.handle(req)

        self.assertTrue(result.ok)
        self.assertIn("track-123", result.detail or "")
        self.assertTrue(fake_runtime.called)
        self.assertEqual(len(fake_rag.ainsert_calls), 1)
        self.assertEqual(fake_rag.ainsert_calls[0]["kwargs"]["ids"], [str(payload.entry_id)])
        self.assertEqual(fake_rag.ainsert_calls[0]["kwargs"]["file_paths"], [str(payload.entry_id)])


class WorkerPayloadTests(unittest.TestCase):
    """Tests for Worker payload construction and behavior matrix."""

    def setUp(self) -> None:
        self.db = make_session()

        from app.entry.models import Entry, TimeMode
        from app.entry_type.models import EntryType
        from app.lightrag.models import EntryIndexOutbox
        from app.tag.models import Tag

        self.entry_type_on = EntryType(code="on", name="On", graph_enabled=True, ai_enabled=True, enabled=True)
        self.entry_type_off = EntryType(code="off", name="Off", graph_enabled=False, ai_enabled=True, enabled=True)
        self.db.add_all([self.entry_type_on, self.entry_type_off])
        self.db.commit()

        self.tag = Tag(name="t1", color=None, description=None)
        self.db.add(self.tag)
        self.db.commit()

        self.entry_on = Entry(
            title="Hello",
            summary="S",
            content="C",
            type_id=self.entry_type_on.id,
            time_mode=TimeMode.NONE,
        )
        self.entry_on.tags = [self.tag]
        self.db.add(self.entry_on)
        self.db.commit()
        self.db.refresh(self.entry_on)

        self.entry_off = Entry(
            title="NoGraph",
            summary=None,
            content="C2",
            type_id=self.entry_type_off.id,
            time_mode=TimeMode.NONE,
        )
        self.db.add(self.entry_off)
        self.db.commit()
        self.db.refresh(self.entry_off)

        now = datetime.now(timezone.utc)
        self.outbox_on = EntryIndexOutbox(
            entry_id=self.entry_on.id,
            op="upsert",
            entry_updated_at=self.entry_on.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="w",
        )
        self.outbox_off = EntryIndexOutbox(
            entry_id=self.entry_off.id,
            op="upsert",
            entry_updated_at=self.entry_off.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="w",
        )
        self.db.add_all([self.outbox_on, self.outbox_off])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_worker_builds_payload_for_indexable_entry(self) -> None:
        """Worker should build payload with text for indexable entries."""
        from app.lightrag.outbox_repo import OutboxRepo
        from app.lightrag.types import IndexResult, WorkerConfig
        from app.lightrag.worker import Worker

        class _FakeIndexer:
            def __init__(self) -> None:
                self.last_req = None

            def handle(self, req):  # noqa: ANN001
                self.last_req = req
                assert req.op == "upsert"
                assert req.payload is not None
                assert "Title:" in req.payload.text
                return IndexResult(ok=True)

        fake = _FakeIndexer()
        cfg = WorkerConfig(
            enabled=True,
            poll_interval_ms=2000,
            batch_size=10,
            max_attempts=6,
            lock_ttl_sec=300,
            worker_id="w",
        )
        repo = OutboxRepo(self.db, worker_id="w")
        worker = Worker(cfg, indexer=fake, session_factory=lambda: self.db)
        worker._process_one(self.db, repo, self.outbox_on, datetime.now(timezone.utc))

        self.db.refresh(self.outbox_on)
        self.assertEqual(self.outbox_on.status, "succeeded")
        self.assertIsNotNone(fake.last_req)

    def test_worker_translates_upsert_to_delete_when_disabled_by_type(self) -> None:
        """Worker should translate upsert to delete when EntryType disables indexing."""
        from app.lightrag.outbox_repo import OutboxRepo
        from app.lightrag.types import IndexResult, WorkerConfig
        from app.lightrag.worker import Worker

        class _FakeIndexer:
            def __init__(self) -> None:
                self.last_req = None

            def handle(self, req):  # noqa: ANN001
                self.last_req = req
                assert req.op == "delete"
                assert req.payload is None
                return IndexResult(ok=True)

        fake = _FakeIndexer()
        cfg = WorkerConfig(
            enabled=True,
            poll_interval_ms=2000,
            batch_size=10,
            max_attempts=6,
            lock_ttl_sec=300,
            worker_id="w",
        )
        repo = OutboxRepo(self.db, worker_id="w")
        worker = Worker(cfg, indexer=fake, session_factory=lambda: self.db)
        worker._process_one(self.db, repo, self.outbox_off, datetime.now(timezone.utc))

        self.db.refresh(self.outbox_off)
        self.assertEqual(self.outbox_off.status, "succeeded")
        self.assertIsNotNone(fake.last_req)


class DocumentPayloadTests(unittest.TestCase):
    """Tests for document payload building."""

    def test_render_entry_text_format(self) -> None:
        """render_entry_text should produce expected format."""
        from app.lightrag.documents import render_entry_text

        text = render_entry_text(
            title="Test Title",
            summary="Test summary",
            content="Test content",
            type_name="Knowledge",
            type_code="knowledge",
            tags=["tag1", "tag2"],
        )

        self.assertIn("Title: Test Title", text)
        self.assertIn("Type: Knowledge (knowledge)", text)
        self.assertIn("Tags: tag1, tag2", text)
        self.assertIn("Summary:", text)
        self.assertIn("Test summary", text)
        self.assertIn("Content:", text)
        self.assertIn("Test content", text)

    def test_should_index_returns_true_when_all_enabled(self) -> None:
        """should_index should return True when all flags are enabled."""
        from app.lightrag.documents import should_index
        from app.lightrag.types import DocumentPayload

        payload = DocumentPayload(
            entry_id=uuid4(),
            entry_updated_at=datetime.now(timezone.utc),
            type_id=uuid4(),
            type_code="test",
            type_name="Test",
            type_enabled=True,
            graph_enabled=True,
            ai_enabled=True,
            title="Test",
            summary=None,
            content=None,
            tags=[],
            tag_ids=[],
            text="test",
        )

        self.assertTrue(should_index(payload))

    def test_should_index_returns_false_when_graph_disabled(self) -> None:
        """should_index should return False when graph_enabled is False."""
        from app.lightrag.documents import should_index
        from app.lightrag.types import DocumentPayload

        payload = DocumentPayload(
            entry_id=uuid4(),
            entry_updated_at=datetime.now(timezone.utc),
            type_id=uuid4(),
            type_code="test",
            type_name="Test",
            type_enabled=True,
            graph_enabled=False,
            ai_enabled=True,
            title="Test",
            summary=None,
            content=None,
            tags=[],
            tag_ids=[],
            text="test",
        )

        self.assertFalse(should_index(payload))


class LockOwnershipTests(unittest.TestCase):
    """Tests for lock ownership validation in mark_* methods."""

    def setUp(self) -> None:
        self.db = make_session()

        from app.entry_type.models import EntryType
        from app.entry.models import Entry, TimeMode

        self.entry_type = EntryType(
            code="test", name="Test", graph_enabled=True, ai_enabled=True, enabled=True
        )
        self.db.add(self.entry_type)
        self.db.commit()

        self.entry = Entry(
            title="Test Entry",
            content="Test content",
            type_id=self.entry_type.id,
            time_mode=TimeMode.NONE,
        )
        self.db.add(self.entry)
        self.db.commit()
        self.db.refresh(self.entry)

    def tearDown(self) -> None:
        self.db.close()

    def test_mark_succeeded_fails_if_lock_lost(self) -> None:
        """mark_succeeded should return False if another worker owns the lock."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="other-worker",
        )
        self.db.add(outbox)
        self.db.commit()

        # Create repo with different worker_id
        repo = OutboxRepo(self.db, worker_id="my-worker")
        result = repo.mark_succeeded(outbox_id=outbox.id)

        self.assertFalse(result)

        # Status should remain unchanged
        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "processing")
        self.assertEqual(outbox.locked_by, "other-worker")

    def test_mark_succeeded_works_when_owning_lock(self) -> None:
        """mark_succeeded should return True when worker owns the lock."""
        from app.lightrag.models import EntryIndexOutbox
        from app.lightrag.outbox_repo import OutboxRepo

        now = datetime.now(timezone.utc)

        outbox = EntryIndexOutbox(
            entry_id=self.entry.id,
            op="upsert",
            entry_updated_at=self.entry.updated_at,
            status="processing",
            attempts=1,
            available_at=now,
            locked_at=now,
            locked_by="my-worker",
        )
        self.db.add(outbox)
        self.db.commit()

        repo = OutboxRepo(self.db, worker_id="my-worker")
        result = repo.mark_succeeded(outbox_id=outbox.id)

        self.assertTrue(result)

        self.db.refresh(outbox)
        self.assertEqual(outbox.status, "succeeded")


if __name__ == "__main__":
    unittest.main()
