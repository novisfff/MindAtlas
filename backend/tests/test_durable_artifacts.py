"""Unit tests for Plan 06 private durable Artifact storage + orphan/GC gates.

Uses in-memory object backend + SQLite. Does not claim MinIO concurrency barriers
(see test_durable_artifacts_minio.py for real MinIO integration when available).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import session_scope

bootstrap_backend_imports()
reset_caches()


BUCKET = "mindatlas-assistant-artifacts"


def _settings(**overrides):
    from app.config import Settings

    base = {
        "ASSISTANT_ARTIFACT_BUCKET": BUCKET,
        "MINIO_BUCKET": "mindatlas",
        "ASSISTANT_WORKER_LEASE_TTL_SEC": "30",
        "ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC": "5",
        "ASSISTANT_WORKER_MAX_RECOVERY_ATTEMPTS": "5",
        "ASSISTANT_WORKER_RETRY_BASE_MS": "500",
        "ASSISTANT_WORKER_RETRY_MAX_MS": "30000",
        "ASSISTANT_ARTIFACT_ORPHAN_SCAN_INTERVAL_SEC": "60",
        "ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC": "900",
        "ASSISTANT_DURABLE_CLOCK_SKEW_SEC": "30",
        "ASSISTANT_ARTIFACT_INLINE_MAX_BYTES": "64",
        "ASSISTANT_ARTIFACT_MAX_BYTES": "256",
        "ASSISTANT_ARTIFACT_RUN_MAX_BYTES": "512",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return Settings(_env_file=None, **base)


def _make_run(session, *, status: str = "running", **kwargs):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"art-{uuid.uuid4().hex[:8]}")
    session.add(conv)
    session.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-art-1",
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run, conv


def _service(session, settings=None, backend=None, limits=None):
    from app.assistant.durable.artifacts import (
        DurableArtifactService,
        InMemoryArtifactObjectBackend,
    )

    return DurableArtifactService(
        session,
        backend=backend or InMemoryArtifactObjectBackend(),
        settings=settings or _settings(),
        limits=limits,
        bucket_name=BUCKET,
    )


# ---------------------------------------------------------------------------
# Config / grace formula
# ---------------------------------------------------------------------------


def test_orphan_grace_floor_formula() -> None:
    from app.config import compute_artifact_orphan_grace_floor_sec

    # lease=30 + sum(min(500*2^a, 30000) for a=0..4)/1000 ceil + scan=60 + skew=30
    # backoff ms: 500+1000+2000+4000+8000 = 15500 -> 16s ceil
    floor = compute_artifact_orphan_grace_floor_sec(
        lease_ttl_sec=30,
        retry_base_ms=500,
        retry_max_ms=30_000,
        max_recovery_attempts=5,
        orphan_scan_interval_sec=60,
        clock_skew_sec=30,
    )
    assert floor == 30 + 16 + 60 + 30  # 136
    assert floor <= 900


def test_settings_reject_grace_below_floor() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC="10")


def test_settings_reject_same_bucket_as_attachments() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(ASSISTANT_ARTIFACT_BUCKET="mindatlas", MINIO_BUCKET="mindatlas")


def test_settings_reject_heartbeat_not_less_than_lease_over_3() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(
            ASSISTANT_WORKER_LEASE_TTL_SEC="30",
            ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC="10",
        )


# ---------------------------------------------------------------------------
# Key generation / sanitization
# ---------------------------------------------------------------------------


def test_object_key_is_server_generated_content_addressed() -> None:
    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.artifacts import (
        ARTIFACT_KEY_REJECTED,
        ArtifactStorageError,
        build_object_key,
    )

    run_id = uuid.uuid4()
    digest = sha256_bytes(b"hello")
    key = build_object_key(run_id=run_id, content_sha256=digest)
    assert key == f"assistant-runs/{run_id}/{digest}"
    assert digest in key
    assert str(run_id) in key


def test_reject_client_supplied_object_key() -> None:
    from app.assistant.durable.artifacts import ARTIFACT_KEY_REJECTED, ArtifactStorageError

    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        with pytest.raises(ArtifactStorageError) as exc:
            svc.put_bytes(
                run_id=run.id,
                content=b"x" * 8,
                object_key="evil/key",
            )
        assert exc.value.code == ARTIFACT_KEY_REJECTED


# ---------------------------------------------------------------------------
# Inline / object thresholds, budgets, idempotent retry
# ---------------------------------------------------------------------------


def test_inline_below_threshold_and_object_above() -> None:
    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        # inline max is 64 in test settings
        small = svc.put_bytes(run_id=run.id, content=b"s" * 32, kind="note")
        assert small.storage_kind == "inline"
        assert small.inline_bytes == b"s" * 32
        assert small.object_key is None

        large = svc.put_bytes(run_id=run.id, content=b"L" * 80, kind="blob")
        assert large.storage_kind == "object"
        assert large.inline_bytes is None
        assert large.object_key is not None
        assert large.object_key.startswith(f"assistant-runs/{run.id}/")
        # Object present in private backend
        st = svc.backend.stat(bucket=BUCKET, object_key=large.object_key)
        assert st is not None
        assert st.size == 80


def test_single_artifact_and_run_budget_limits() -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_RUN_BUDGET_EXCEEDED,
        ARTIFACT_TOO_LARGE,
        ArtifactStorageError,
    )

    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        with pytest.raises(ArtifactStorageError) as exc:
            svc.put_bytes(run_id=run.id, content=b"x" * 300)  # max 256
        assert exc.value.code == ARTIFACT_TOO_LARGE

        svc.put_bytes(run_id=run.id, content=b"a" * 200)
        svc.put_bytes(run_id=run.id, content=b"b" * 200)
        with pytest.raises(ArtifactStorageError) as exc2:
            svc.put_bytes(run_id=run.id, content=b"c" * 200)  # 600 > 512
        assert exc2.value.code == ARTIFACT_RUN_BUDGET_EXCEEDED


def test_idempotent_same_content_retry_converges() -> None:
    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        body = b"Z" * 80
        a1 = svc.put_bytes(run_id=run.id, content=body, kind="blob")
        a2 = svc.put_bytes(run_id=run.id, content=body, kind="blob")
        assert a1.id == a2.id
        assert a1.content_sha256 == a2.content_sha256
        # Only one row for same content identity
        from app.assistant.durable.models import AssistantRunArtifact

        all_rows = list(
            session.scalars(
                select(AssistantRunArtifact).where(AssistantRunArtifact.run_id == run.id)
            )
        )
        assert len(all_rows) == 1


def test_idempotent_object_upload_same_key_retry() -> None:
    """Crash after upload before row: retry must reuse same key/object."""
    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.artifacts import build_object_key

    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        body = b"R" * 90
        prepared = svc.prepare(run_id=run.id, content=body, kind="blob")
        assert prepared.storage_kind == "object"
        assert prepared.object_key == build_object_key(
            run_id=run.id, content_sha256=sha256_bytes(body)
        )
        # Object exists, no row yet
        assert svc.backend.stat(bucket=BUCKET, object_key=prepared.object_key) is not None
        from app.assistant.durable.models import AssistantRunArtifact

        assert (
            session.scalar(
                select(AssistantRunArtifact).where(
                    AssistantRunArtifact.run_id == run.id
                )
            )
            is None
        )
        # Retry prepare+commit is idempotent on object
        prepared2 = svc.prepare(run_id=run.id, content=body, kind="blob")
        assert prepared2.object_key == prepared.object_key
        row = svc.commit_row(prepared2)
        assert row.object_key == prepared.object_key
        # Second put still one row
        row2 = svc.put_bytes(run_id=run.id, content=body)
        assert row2.id == row.id


# ---------------------------------------------------------------------------
# Digest / range / cross-run denial / missing object
# ---------------------------------------------------------------------------


def test_read_chunk_range_and_digest_checks() -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_DIGEST_MISMATCH,
        ARTIFACT_RANGE_INVALID,
        ArtifactStorageError,
    )

    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        row = svc.put_bytes(run_id=run.id, content=b"abcdefghij", kind="text")
        chunk = svc.read_chunk(
            run_id=run.id,
            artifact_id=row.id,
            offset=2,
            limit=3,
            expected_digest=row.content_sha256,
        )
        assert chunk["content"] == "cde"
        assert chunk["encoding"] == "utf-8"
        assert chunk["eof"] is False
        assert "object_key" not in chunk
        assert "objectKey" not in chunk
        assert "url" not in chunk
        assert "bucket" not in chunk

        with pytest.raises(ArtifactStorageError) as exc:
            svc.read_chunk(
                run_id=run.id,
                artifact_id=row.id,
                offset=0,
                limit=1,
                expected_digest="a" * 64,
            )
        assert exc.value.code == ARTIFACT_DIGEST_MISMATCH

        with pytest.raises(ArtifactStorageError) as exc2:
            svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=-1, limit=1)
        assert exc2.value.code == ARTIFACT_RANGE_INVALID

        with pytest.raises(ArtifactStorageError) as exc3:
            svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=100, limit=1)
        assert exc3.value.code == ARTIFACT_RANGE_INVALID


def test_cross_run_denial() -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_CROSS_RUN_DENIED,
        ArtifactStorageError,
    )

    with session_scope() as session:
        run_a, _ = _make_run(session)
        run_b, _ = _make_run(session)
        svc = _service(session)
        row = svc.put_bytes(run_id=run_a.id, content=b"secret-payload", kind="blob")
        with pytest.raises(ArtifactStorageError) as exc:
            svc.read_chunk(run_id=run_b.id, artifact_id=row.id, offset=0, limit=10)
        assert exc.value.code == ARTIFACT_CROSS_RUN_DENIED
        # Error must not leak content
        assert "secret-payload" not in str(exc.value)
        assert row.object_key is None or row.object_key not in str(exc.value)


def test_missing_object_on_committed_row_needs_reconciliation() -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_NEEDS_RECONCILIATION,
        ArtifactStorageError,
        InMemoryArtifactObjectBackend,
    )

    with session_scope() as session:
        run, _ = _make_run(session)
        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        row = svc.put_bytes(run_id=run.id, content=b"M" * 80, kind="blob")
        assert row.storage_kind == "object"
        backend.drop_without_delete_api(bucket=BUCKET, object_key=str(row.object_key))
        with pytest.raises(ArtifactStorageError) as exc:
            svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=0, limit=10)
        assert exc.value.code == ARTIFACT_NEEDS_RECONCILIATION


def test_object_read_chunk_roundtrip() -> None:
    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        body = b"0123456789" * 9  # 90 bytes > inline 64
        row = svc.put_bytes(run_id=run.id, content=body, kind="blob")
        chunk = svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=10, limit=5)
        assert chunk["content"] == "01234"
        assert chunk["returnedBytes"] == 5
        assert chunk["totalSize"] == 90


# ---------------------------------------------------------------------------
# Orphan scanner gates
# ---------------------------------------------------------------------------


def test_orphan_scanner_keeps_young_and_referenced_and_live_run() -> None:
    with session_scope() as session:
        run, _ = _make_run(session, status="running")
        backend = __import__(
            "app.assistant.durable.artifacts", fromlist=["InMemoryArtifactObjectBackend"]
        ).InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        # Upload without row (crash barrier)
        body = b"O" * 80
        prepared = svc.prepare(run_id=run.id, content=body)
        assert prepared.object_key is not None
        # Age it beyond grace
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10_000)
        # Live nonterminal Run → must NOT delete
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 0
        assert backend.stat(bucket=BUCKET, object_key=prepared.object_key) is not None

        # Commit the row → still must not delete
        svc.commit_row(prepared)
        session.commit()
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10_000)
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 0


def test_orphan_scanner_deletes_only_when_all_gates_pass() -> None:
    with session_scope() as session:
        run, _ = _make_run(session, status="completed")
        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        body = b"G" * 80
        prepared = svc.prepare(run_id=run.id, content=body)
        # No row, terminal Run, aged past grace → delete
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10_000)
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 1
        assert backend.stat(bucket=BUCKET, object_key=prepared.object_key) is None


def test_orphan_scanner_blocks_on_live_lease() -> None:
    with session_scope() as session:
        now = datetime.now(timezone.utc)
        run, _ = _make_run(
            session,
            status="completed",
            lease_owner="worker-1",
            lease_generation=1,
            lease_expires_at=now + timedelta(minutes=5),
        )
        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        prepared = svc.prepare(run_id=run.id, content=b"L" * 80)
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10_000)
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 0


def test_orphan_scanner_blocks_young_objects() -> None:
    with session_scope() as session:
        run, _ = _make_run(session, status="completed")
        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        prepared = svc.prepare(run_id=run.id, content=b"Y" * 80)
        # Age only 10s; grace 100s
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10)
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 0


def _digest(tag: str) -> str:
    from app.assistant.domain.digests import sha256_bytes

    return sha256_bytes(tag.encode("utf-8"))


def _append_checkpoint(session, run, *, sequence: int, phase: str):
    """Create a minimal Checkpoint + required revision FKs for orphan gate tests."""
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )

    d = _digest(f"{run.id}:{sequence}:{phase}")
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=sequence,
        manifest_digest=d,
        schema_version=1,
        payload={},
    )
    policy = AssistantRunPolicyRevision(
        run_id=run.id,
        revision=sequence,
        policy_digest=d,
        payload={"grants": []},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run.id,
        revision=sequence,
        budget_digest=d,
        payload={},
    )
    obligation = AssistantRunObligationRevision(
        run_id=run.id,
        revision=sequence,
        obligation_digest=d,
        payload={},
    )
    session.add_all([manifest, policy, budget, obligation])
    session.flush()
    ck = AssistantRunCheckpoint(
        run_id=run.id,
        sequence=sequence,
        expected_state_revision=sequence - 1,
        committed_state_revision=sequence,
        schema_version=1,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy.id,
        budget_revision_id=budget.id,
        obligation_revision_id=obligation.id,
        provider_message_ordinal=-1,
        provider_transcript_digest=d,
        phase=phase,
        state_payload={"phase": phase},
        state_digest=d,
    )
    session.add(ck)
    session.flush()
    return ck


def test_orphan_scanner_deletes_terminal_run_with_historical_nonterminal_checkpoints() -> None:
    """Terminal Run status allows orphan GC even when current phase is not 'terminal'.

    Production memory finalizer leaves current checkpoint at ready_for_memory while
    status becomes completed. Historical non-terminal phases are append-only and
    must never permanently block orphan GC (Plan §10 prefer-leak, not infinite retention).
    """
    with session_scope() as session:
        run, _ = _make_run(session, status="completed")
        # Multi-step history ending at ready_for_memory (real production finalizer phase)
        _append_checkpoint(session, run, sequence=1, phase="ready_for_provider")
        _append_checkpoint(session, run, sequence=2, phase="waiting")
        current = _append_checkpoint(session, run, sequence=3, phase="ready_for_memory")
        run.current_checkpoint_id = current.id
        session.commit()

        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        prepared = svc.prepare(run_id=run.id, content=b"H" * 80)
        assert prepared.object_key is not None
        # Unreferenced object aged past grace on a completed multi-step run
        backend.force_age(bucket=BUCKET, object_key=prepared.object_key, age_sec=10_000)
        deleted = svc.scan_orphans(grace_sec=100)
        assert deleted == 1
        assert backend.stat(bucket=BUCKET, object_key=prepared.object_key) is None


# ---------------------------------------------------------------------------
# Conversation deletion outbox + GC
# ---------------------------------------------------------------------------


def test_conversation_deletion_enqueues_gc_and_process_deletes_object() -> None:
    from app.assistant.durable.models import AssistantRunArtifactGc
    from app.assistant.service import AssistantService

    with session_scope() as session:
        run, conv = _make_run(session, status="completed")
        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        row = svc.put_bytes(run_id=run.id, content=b"D" * 80, kind="blob")
        assert row.object_key is not None
        object_key = str(row.object_key)

        # Delete conversation via service (enqueues GC then cascades)
        AssistantService(session).delete_conversation(conv.id)
        gc_rows = list(session.scalars(select(AssistantRunArtifactGc)))
        assert len(gc_rows) >= 1
        assert any(g.object_key == object_key for g in gc_rows)
        # Object still present until GC processor runs
        assert backend.stat(bucket=BUCKET, object_key=object_key) is not None

        # Process outbox
        svc2 = _service(session, backend=backend)
        n = svc2.process_gc_outbox()
        assert n >= 1
        assert backend.stat(bucket=BUCKET, object_key=object_key) is None
        session.expire_all()
        gc_rows = list(session.scalars(select(AssistantRunArtifactGc)))
        assert any(g.status == "deleted" for g in gc_rows)


def test_gc_outbox_idempotent_double_process() -> None:
    from app.assistant.durable.models import AssistantRunArtifactGc

    with session_scope() as session:
        run, _ = _make_run(session, status="completed")
        from app.assistant.durable.artifacts import InMemoryArtifactObjectBackend

        backend = InMemoryArtifactObjectBackend()
        svc = _service(session, backend=backend)
        row = svc.put_bytes(run_id=run.id, content=b"I" * 80)
        svc.enqueue_gc_for_run(run.id)
        session.commit()
        assert svc.process_gc_outbox() >= 1
        assert svc.process_gc_outbox() == 0  # already deleted
        gcs = list(session.scalars(select(AssistantRunArtifactGc)))
        assert all(g.status == "deleted" for g in gcs if g.object_key == row.object_key)


def test_no_public_url_or_key_in_read_payload_or_errors() -> None:
    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        row = svc.put_bytes(run_id=run.id, content=b"P" * 80)
        view = svc.get(run_id=run.id, artifact_id=row.id)
        # StoredArtifactView must not expose object_key
        assert "object_key" not in view.__dataclass_fields__
        assert view.inline_bytes is None  # object-backed
        chunk = svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=0, limit=4)
        blob = str(chunk)
        assert row.object_key not in blob
        assert "presign" not in blob.lower()
        assert "http" not in blob.lower()
        assert BUCKET not in blob


def test_media_type_and_metadata_sanitization() -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_INVALID_INPUT,
        ARTIFACT_MEDIA_TYPE_INVALID,
        ArtifactStorageError,
    )

    with session_scope() as session:
        run, _ = _make_run(session)
        svc = _service(session)
        with pytest.raises(ArtifactStorageError) as exc:
            svc.put_bytes(run_id=run.id, content=b"x", media_type="not a type!!!")
        assert exc.value.code == ARTIFACT_MEDIA_TYPE_INVALID

        with pytest.raises(ArtifactStorageError) as exc2:
            svc.put_bytes(
                run_id=run.id,
                content=b"x",
                metadata={"secret_token": "nope"},
            )
        assert exc2.value.code == ARTIFACT_INVALID_INPUT

        row = svc.put_bytes(
            run_id=run.id,
            content=b"ok",
            media_type="text/plain; charset=utf-8",
            metadata={"source": "unit-test"},
        )
        assert row.media_type.startswith("text/plain")
        assert row.metadata_json["source"] == "unit-test"


def test_limits_cannot_exceed_hard_max() -> None:
    from app.assistant.durable.artifacts import ArtifactLimits

    with pytest.raises(ValueError):
        ArtifactLimits(
            inline_max_bytes=10**9,
            max_bytes=10**9,
            run_max_bytes=10**9,
        )
