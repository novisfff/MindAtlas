"""Real MinIO integration barriers for Plan 06 durable Artifacts.

Skipped unless MinIO env is configured:

- ``MINDATLAS_TEST_MINIO=1`` (or truthy)
- ``MINIO_ENDPOINT``, ``MINIO_ACCESS_KEY``, ``MINIO_SECRET_KEY``

Uses a private test bucket distinct from the attachment bucket. Proves:
upload-before-row crash orphan gates, same-key idempotent retry, missing object
reconciliation, and that the private bucket has no anonymous download policy.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import session_scope

bootstrap_backend_imports()
reset_caches()

_MINIO_ENABLED = os.environ.get("MINDATLAS_TEST_MINIO", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "").strip()
_ACCESS = os.environ.get("MINIO_ACCESS_KEY", "").strip()
_SECRET = os.environ.get("MINIO_SECRET_KEY", "").strip()
_ATTACHMENT_BUCKET = os.environ.get("MINIO_BUCKET", "mindatlas").strip() or "mindatlas"
_ARTIFACT_BUCKET = (
    os.environ.get("ASSISTANT_ARTIFACT_BUCKET", "mindatlas-assistant-artifacts-test").strip()
    or "mindatlas-assistant-artifacts-test"
)

pytestmark = pytest.mark.skipif(
    not (_MINIO_ENABLED and _ENDPOINT and _ACCESS and _SECRET),
    reason=(
        "MINDATLAS_TEST_MINIO not enabled or MinIO credentials missing; "
        "real MinIO artifact barriers skipped"
    ),
)


def _minio_client():
    from minio import Minio

    endpoint = _ENDPOINT
    secure = os.environ.get("MINIO_SECURE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        parsed = urlparse(endpoint)
        secure = parsed.scheme == "https"
        endpoint = (parsed.netloc or parsed.path).rstrip("/")
    return Minio(endpoint, access_key=_ACCESS, secret_key=_SECRET, secure=secure)


def _settings(**overrides):
    from app.config import Settings

    base = {
        "ASSISTANT_ARTIFACT_BUCKET": _ARTIFACT_BUCKET,
        "MINIO_BUCKET": _ATTACHMENT_BUCKET,
        "ASSISTANT_WORKER_LEASE_TTL_SEC": "30",
        "ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC": "5",
        "ASSISTANT_WORKER_MAX_RECOVERY_ATTEMPTS": "5",
        "ASSISTANT_WORKER_RETRY_BASE_MS": "500",
        "ASSISTANT_WORKER_RETRY_MAX_MS": "30000",
        "ASSISTANT_ARTIFACT_ORPHAN_SCAN_INTERVAL_SEC": "60",
        "ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC": "900",
        "ASSISTANT_DURABLE_CLOCK_SKEW_SEC": "30",
        "ASSISTANT_ARTIFACT_INLINE_MAX_BYTES": "32",
        "ASSISTANT_ARTIFACT_MAX_BYTES": "1048576",
        "ASSISTANT_ARTIFACT_RUN_MAX_BYTES": "4194304",
        "MINIO_ENDPOINT": _ENDPOINT,
        "MINIO_ACCESS_KEY": _ACCESS,
        "MINIO_SECRET_KEY": _SECRET,
        "MINIO_SECURE": os.environ.get("MINIO_SECURE", "false"),
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return Settings(_env_file=None, **base)


def _make_run(session, *, status: str = "running", **kwargs):
    from app.assistant.models import AssistantChatRun, Conversation

    conv = Conversation(title=f"minio-art-{uuid.uuid4().hex[:8]}")
    session.add(conv)
    session.flush()
    run = AssistantChatRun(
        conversation_id=conv.id,
        status=status,
        runtime_kind="main_agent",
        runtime_contract_version=1,
        required_app_build_revision="build-minio-art-1",
        state_revision=int(kwargs.pop("state_revision", 0)),
        last_event_seq=int(kwargs.pop("last_event_seq", 0)),
        memory_commit_status=kwargs.pop("memory_commit_status", "pending"),
        **kwargs,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run, conv


@pytest.fixture(scope="module")
def minio_backend() -> Iterator[object]:
    from app.assistant.durable.artifacts import MinioArtifactObjectBackend

    client = _minio_client()
    backend = MinioArtifactObjectBackend(client=client)
    backend.ensure_bucket(_ARTIFACT_BUCKET)
    yield backend


def test_private_bucket_has_no_anonymous_download_policy(minio_backend) -> None:
    """Attachment bucket may be public-download; artifact bucket must not be."""
    client = _minio_client()
    # Ensure attachment bucket still supports anonymous download setup path
    # (we do not change it here). Artifact bucket must not be world-readable.
    try:
        # minio-py does not expose get_bucket_policy consistently across versions;
        # probe via anonymous List/Get expectation: authenticated client works.
        assert client.bucket_exists(_ARTIFACT_BUCKET)
    except Exception as exc:  # pragma: no cover - connectivity
        pytest.fail(f"artifact bucket unavailable: {exc}")

    # Policy JSON if available must not grant anonymous GetObject/* on *
    try:
        policy = client.get_bucket_policy(_ARTIFACT_BUCKET)
    except Exception:
        # No policy (or private) is acceptable — private by default.
        policy = ""
    policy_l = str(policy or "").lower()
    if policy_l:
        # Reject common anonymous download grant patterns.
        assert "arn:aws:s3:::" + _ARTIFACT_BUCKET + "/*" not in policy_l or (
            '"principal":"*"' not in policy_l.replace(" ", "")
            and '"principal":{"aws":"*"}' not in policy_l.replace(" ", "")
            and '"principal":"*"' not in policy_l.replace(" ", "")
        )
        assert "public-read" not in policy_l
        # If a statement exists with Principal * and s3:GetObject, fail closed.
        compact = policy_l.replace(" ", "").replace("\n", "")
        if '"principal":"*"' in compact or '"principal":{"aws":"*"}' in compact:
            assert "s3:getobject" not in compact


def test_minio_upload_before_row_then_idempotent_retry(minio_backend) -> None:
    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.artifacts import DurableArtifactService, build_object_key
    from app.assistant.durable.models import AssistantRunArtifact

    with session_scope() as session:
        run, _ = _make_run(session, status="running")
        svc = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        body = b"minio-crash-barrier-" + uuid.uuid4().bytes + (b"x" * 64)
        prepared = svc.prepare(run_id=run.id, content=body, kind="blob")
        assert prepared.storage_kind == "object"
        key = prepared.object_key
        assert key == build_object_key(run_id=run.id, content_sha256=sha256_bytes(body))
        st = minio_backend.stat(bucket=_ARTIFACT_BUCKET, object_key=key)
        assert st is not None
        assert st.size == len(body)
        # No row yet
        assert (
            session.scalar(
                select(AssistantRunArtifact).where(AssistantRunArtifact.run_id == run.id)
            )
            is None
        )
        # Idempotent same-key retry
        prepared2 = svc.prepare(run_id=run.id, content=body, kind="blob")
        assert prepared2.object_key == key
        row = svc.commit_row(prepared2)
        session.commit()
        assert row.object_key == key
        chunk = svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=0, limit=20)
        assert chunk["returnedBytes"] == 20
        assert "object_key" not in chunk
        assert key not in str(chunk)
        # cleanup
        minio_backend.delete(bucket=_ARTIFACT_BUCKET, object_key=key)


def test_minio_orphan_scanner_keeps_live_run_object(minio_backend) -> None:
    from app.assistant.durable.artifacts import DurableArtifactService

    with session_scope() as session:
        run, _ = _make_run(session, status="running")
        svc = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        body = b"live-run-orphan-" + uuid.uuid4().bytes + (b"y" * 40)
        prepared = svc.prepare(run_id=run.id, content=body)
        key = prepared.object_key
        assert key is not None
        # Scanner with tiny grace still must not delete live nonterminal Run object.
        deleted = svc.scan_orphans(
            prefix=f"assistant-runs/{run.id}/",
            max_keys=50,
            grace_sec=0,
            now=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        # grace_sec=0 means age > 0; object is older than 0 almost immediately, but
        # live nonterminal gate must block.
        assert deleted == 0
        assert minio_backend.stat(bucket=_ARTIFACT_BUCKET, object_key=key) is not None
        minio_backend.delete(bucket=_ARTIFACT_BUCKET, object_key=key)


def test_minio_orphan_scanner_deletes_terminal_unreferenced(minio_backend) -> None:
    from app.assistant.durable.artifacts import DurableArtifactService

    with session_scope() as session:
        run, _ = _make_run(session, status="completed")
        svc = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        body = b"terminal-orphan-" + uuid.uuid4().bytes + (b"z" * 40)
        prepared = svc.prepare(run_id=run.id, content=body)
        key = prepared.object_key
        assert key is not None
        # Force age gate by using grace_sec=0 and future clock.
        deleted = svc.scan_orphans(
            prefix=f"assistant-runs/{run.id}/",
            max_keys=50,
            grace_sec=0,
            now=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert deleted >= 1
        assert minio_backend.stat(bucket=_ARTIFACT_BUCKET, object_key=key) is None


def test_minio_missing_object_needs_reconciliation(minio_backend) -> None:
    from app.assistant.durable.artifacts import (
        ARTIFACT_NEEDS_RECONCILIATION,
        ArtifactStorageError,
        DurableArtifactService,
    )

    with session_scope() as session:
        run, _ = _make_run(session, status="running")
        svc = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        body = b"missing-object-" + uuid.uuid4().bytes + (b"m" * 40)
        row = svc.put_bytes(run_id=run.id, content=body, kind="blob")
        key = str(row.object_key)
        minio_backend.delete(bucket=_ARTIFACT_BUCKET, object_key=key)
        with pytest.raises(ArtifactStorageError) as exc:
            svc.read_chunk(run_id=run.id, artifact_id=row.id, offset=0, limit=8)
        assert exc.value.code == ARTIFACT_NEEDS_RECONCILIATION


def test_minio_gc_outbox_after_conversation_delete(minio_backend) -> None:
    from app.assistant.durable.artifacts import DurableArtifactService
    from app.assistant.durable.models import AssistantRunArtifactGc
    from app.assistant.service import AssistantService

    with session_scope() as session:
        run, conv = _make_run(session, status="completed")
        svc = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        body = b"gc-delete-" + uuid.uuid4().bytes + (b"g" * 40)
        row = svc.put_bytes(run_id=run.id, content=body, kind="blob")
        key = str(row.object_key)
        AssistantService(session).delete_conversation(conv.id)
        gcs = list(session.scalars(select(AssistantRunArtifactGc)))
        assert any(g.object_key == key for g in gcs)
        # Object still present until processor
        assert minio_backend.stat(bucket=_ARTIFACT_BUCKET, object_key=key) is not None
        svc2 = DurableArtifactService(
            session,
            backend=minio_backend,
            settings=_settings(),
            bucket_name=_ARTIFACT_BUCKET,
        )
        n = svc2.process_gc_outbox()
        assert n >= 1
        assert minio_backend.stat(bucket=_ARTIFACT_BUCKET, object_key=key) is None
