"""Private durable Run Artifact storage and GC (Plan 06 Task 4).

Object protocol:
1. validate media type / size / content;
2. derive SHA-256 and a server-only content-addressed key under
   ``assistant-runs/{run_id}/...`` (never accept a key from model input);
3. upload idempotently to the private bucket and verify size/digest metadata;
4. commit the Artifact row (and Checkpoint reference) in the semantic transaction;
5. crash after upload before row commit leaves an orphan eligible only under the
   bounded scanner gates below;
6. a committed row whose object is missing/mismatched is not recreated from model
   output — callers treat it as ``needs_reconciliation``.

Artifact content/keys never appear in SSE, logs, metrics, Message summaries,
L1/L2, or public/presigned URLs. ``artifact.read`` is Run-scoped, digest/range
checked, and backend mediated.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Protocol, runtime_checkable
from uuid import UUID, uuid4

from minio import Minio
from minio.error import S3Error
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_bytes
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunArtifactGc,
    AssistantRunCheckpoint,
)
from app.assistant.models import AssistantChatRun
from app.common.storage import StorageError, remove_object_safe
from app.common.time import utcnow
from app.config import (
    ASSISTANT_ARTIFACT_INLINE_MAX_BYTES_HARD_MAX,
    ASSISTANT_ARTIFACT_MAX_BYTES_HARD_MAX,
    ASSISTANT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX,
    Settings,
    compute_artifact_orphan_grace_floor_sec,
    get_settings,
)
from app.system_settings.runtime_config_service import resolve_runtime_storage_config

# ---------------------------------------------------------------------------
# Stable error codes (safe; never carry content/keys)
# ---------------------------------------------------------------------------

ARTIFACT_TOO_LARGE = "artifact_too_large"
ARTIFACT_RUN_BUDGET_EXCEEDED = "artifact_run_budget_exceeded"
ARTIFACT_NOT_FOUND = "artifact_not_found"
ARTIFACT_RANGE_INVALID = "artifact_range_invalid"
ARTIFACT_DIGEST_MISMATCH = "artifact_digest_mismatch"
ARTIFACT_CROSS_RUN_DENIED = "artifact_cross_run_denied"
ARTIFACT_OBJECT_MISSING = "artifact_object_missing"
ARTIFACT_MEDIA_TYPE_INVALID = "artifact_media_type_invalid"
ARTIFACT_INVALID_INPUT = "artifact_invalid_input"
ARTIFACT_STORAGE_ERROR = "artifact_storage_error"
ARTIFACT_NEEDS_RECONCILIATION = "artifact_needs_reconciliation"
ARTIFACT_KEY_REJECTED = "artifact_key_rejected"

STORAGE_KIND_INLINE: Literal["inline"] = "inline"
STORAGE_KIND_OBJECT: Literal["object"] = "object"

OBJECT_KEY_PREFIX = "assistant-runs"
# Evaluation namespace is owned by Plan 09; production Artifact backends must reject it.
# Local constant avoids a hard import dependency on evaluation.contracts.
EVAL_OBJECT_KEY_PREFIX = "skill-eval"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&\-^_.+]{0,126}"
    r"/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&\-^_.+]{0,126}"
    r"(?:\s*;\s*[A-Za-z0-9!#$&\-^_.+]+=[A-Za-z0-9!#$&\-^_.+]+)*$"
)

# Statuses that must never lose a referenced or recoverable object.
NONTERMINAL_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "recovering",
        "waiting_approval",
        "waiting_input",
        "cancelling",
        "needs_reconciliation",
    }
)

META_CONTENT_SHA256 = "content-sha256"
META_BYTE_SIZE = "byte-size"
META_RUN_ID = "run-id"


class ArtifactStorageError(Exception):
    """Stable, content-free Artifact storage failure."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message or "")
        self.details = dict(details or {})
        super().__init__(self.code if not self.message else f"{self.code}: {self.message}")


@dataclass(frozen=True)
class ArtifactLimits:
    """Effective durable Artifact limits (never above checked-in ceilings)."""

    inline_max_bytes: int
    max_bytes: int
    run_max_bytes: int

    def __post_init__(self) -> None:
        if self.inline_max_bytes < 1 or self.max_bytes < 1 or self.run_max_bytes < 1:
            raise ValueError("artifact limits must be positive")
        if self.inline_max_bytes > ASSISTANT_ARTIFACT_INLINE_MAX_BYTES_HARD_MAX:
            raise ValueError("inline_max_bytes exceeds hard max")
        if self.max_bytes > ASSISTANT_ARTIFACT_MAX_BYTES_HARD_MAX:
            raise ValueError("max_bytes exceeds hard max")
        if self.run_max_bytes > ASSISTANT_ARTIFACT_RUN_MAX_BYTES_HARD_MAX:
            raise ValueError("run_max_bytes exceeds hard max")
        if self.inline_max_bytes > self.max_bytes:
            raise ValueError("inline_max_bytes must be <= max_bytes")
        if self.max_bytes > self.run_max_bytes:
            raise ValueError("max_bytes must be <= run_max_bytes")


@dataclass(frozen=True)
class StoredArtifactView:
    """Backend-mediated Artifact view (no object key, no public URL)."""

    artifact_id: UUID
    run_id: UUID
    kind: str
    media_type: str
    display_label: str | None
    storage_kind: Literal["inline", "object"]
    byte_size: int
    content_sha256: str
    # Present only for inline rows; object content is loaded via read_chunk.
    inline_bytes: bytes | None = None


@dataclass(frozen=True)
class PreparedArtifact:
    """Result of validate + optional private-object upload (before row commit)."""

    run_id: UUID
    kind: str
    media_type: str
    display_label: str | None
    storage_kind: Literal["inline", "object"]
    byte_size: int
    content_sha256: str
    inline_bytes: bytes | None
    object_key: str | None
    bucket_name: str | None
    metadata_json: dict[str, Any]


@dataclass(frozen=True)
class ObjectStat:
    """Private object metadata used by the orphan scanner / GC."""

    bucket_name: str
    object_key: str
    size: int
    content_sha256: str | None
    last_modified: datetime


@runtime_checkable
class ArtifactObjectBackend(Protocol):
    """Private object backend protocol (MinIO implementation; mockable in unit tests)."""

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectStat: ...

    def stat(self, *, bucket: str, object_key: str) -> ObjectStat | None: ...

    def get_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes: ...

    def delete(self, *, bucket: str, object_key: str) -> bool: ...

    def list_keys(
        self,
        *,
        bucket: str,
        prefix: str,
        max_keys: int = 1000,
    ) -> list[ObjectStat]: ...


def limits_from_settings(settings: Settings | None = None) -> ArtifactLimits:
    s = settings or get_settings()
    return ArtifactLimits(
        inline_max_bytes=int(s.assistant_artifact_inline_max_bytes),
        max_bytes=int(s.assistant_artifact_max_bytes),
        run_max_bytes=int(s.assistant_artifact_run_max_bytes),
    )


def orphan_grace_floor_sec(settings: Settings | None = None) -> int:
    s = settings or get_settings()
    return compute_artifact_orphan_grace_floor_sec(
        lease_ttl_sec=s.assistant_worker_lease_ttl_sec,
        retry_base_ms=s.assistant_worker_retry_base_ms,
        retry_max_ms=s.assistant_worker_retry_max_ms,
        max_recovery_attempts=s.assistant_worker_max_recovery_attempts,
        orphan_scan_interval_sec=s.assistant_artifact_orphan_scan_interval_sec,
        clock_skew_sec=s.assistant_durable_clock_skew_sec,
    )


def validated_orphan_grace_sec(settings: Settings | None = None) -> int:
    """Return configured grace, never below the derived recovery window."""
    s = settings or get_settings()
    floor = orphan_grace_floor_sec(s)
    return max(int(s.assistant_artifact_orphan_grace_sec), floor)


def build_object_key(*, run_id: UUID, content_sha256: str) -> str:
    """Server-generated content-addressed key. Never accept keys from model input."""
    digest = str(content_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "content digest invalid")
    return f"{OBJECT_KEY_PREFIX}/{run_id}/{digest}"


def assert_production_object_key(object_key: str | None) -> str:
    """Reject evaluation-namespace keys at production Artifact entrypoints.

    Server-generated production keys always use ``assistant-runs/``. Evaluation
    keys under ``skill-eval/`` must never be written or read via this store.
    """
    key = str(object_key or "").strip()
    if not key:
        raise ArtifactStorageError(ARTIFACT_KEY_REJECTED, "object key required")
    if key.startswith(f"{EVAL_OBJECT_KEY_PREFIX}/") or key == EVAL_OBJECT_KEY_PREFIX:
        raise ArtifactStorageError(
            ARTIFACT_KEY_REJECTED,
            "production artifact APIs reject evaluation object keys",
        )
    if not key.startswith(f"{OBJECT_KEY_PREFIX}/"):
        raise ArtifactStorageError(
            ARTIFACT_KEY_REJECTED,
            "object key must use production assistant-runs namespace",
        )
    return key


def parse_run_id_from_object_key(object_key: str) -> UUID | None:
    """Extract Run ID from a server-generated key, or None if malformed."""
    parts = str(object_key or "").split("/")
    if len(parts) != 3 or parts[0] != OBJECT_KEY_PREFIX:
        return None
    try:
        return UUID(parts[1])
    except (ValueError, TypeError, AttributeError):
        return None


def sanitize_media_type(media_type: str | None) -> str:
    raw = (media_type or "").strip() or "application/octet-stream"
    if len(raw) > 255 or not _MEDIA_TYPE_RE.fullmatch(raw):
        raise ArtifactStorageError(ARTIFACT_MEDIA_TYPE_INVALID, "media type invalid")
    return raw


def sanitize_kind(kind: str | None) -> str:
    value = (kind or "").strip()
    if not value or len(value) > 64:
        raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "kind invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", value):
        raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "kind invalid")
    return value


def sanitize_display_label(label: str | None) -> str | None:
    if label is None:
        return None
    value = str(label).strip()
    if not value:
        return None
    if len(value) > 255:
        raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "display label too long")
    return value


def sanitize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Safe metadata only — never store content/keys/URLs/secrets."""
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata must be a mapping")
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata key invalid")
        lowered = key.lower()
        if any(
            token in lowered
            for token in ("content", "body", "payload", "secret", "url", "key", "token", "password")
        ):
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata key denied")
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str) and len(value) > 512:
                raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata value too long")
            if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
                raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata value invalid")
            out[key] = value
        else:
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata value type denied")
        if len(out) > 32:
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "metadata too large")
    return out


def _assert_no_client_object_key(kwargs: Mapping[str, Any]) -> None:
    for forbidden in ("object_key", "objectKey", "key", "bucket", "bucket_name", "url", "presigned"):
        if forbidden in kwargs and kwargs[forbidden] is not None:
            raise ArtifactStorageError(
                ARTIFACT_KEY_REJECTED,
                "object key/url must not be supplied by callers",
            )


# ---------------------------------------------------------------------------
# MinIO private backend
# ---------------------------------------------------------------------------


def _build_minio_client_from_runtime() -> Minio:
    """Build a MinIO client from the same endpoint/credentials as attachments.

    Uses the private ASSISTANT_ARTIFACT_BUCKET, never the public attachment bucket.
    """
    from urllib.parse import urlparse

    config = resolve_runtime_storage_config()
    endpoint = (config.endpoint or "").strip()
    if not endpoint:
        raise StorageError("MinIO endpoint is not configured")
    secure = config.secure
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        parsed = urlparse(endpoint)
        secure = parsed.scheme == "https"
        endpoint = (parsed.netloc or parsed.path).rstrip("/")
    access_key = (config.access_key or "").strip()
    secret_key = (config.secret_key or "").strip()
    if not access_key or not secret_key:
        raise StorageError("MinIO credentials are not configured")
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def _normalize_stat_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MinioArtifactObjectBackend:
    """MinIO implementation of the private Artifact object backend."""

    def __init__(self, client: Minio | None = None) -> None:
        self._client = client

    def _client_or_raise(self) -> Minio:
        if self._client is not None:
            return self._client
        try:
            self._client = _build_minio_client_from_runtime()
        except StorageError as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "storage unavailable") from exc
        return self._client

    def ensure_bucket(self, bucket: str) -> None:
        client = self._client_or_raise()
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
        except S3Error as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "bucket init failed") from exc
        except Exception as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "bucket init failed") from exc

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectStat:
        object_key = assert_production_object_key(object_key)
        client = self._client_or_raise()
        self.ensure_bucket(bucket)
        # Idempotent same-key retry: if object already matches, reuse.
        existing = self.stat(bucket=bucket, object_key=object_key)
        digest = str(metadata.get(META_CONTENT_SHA256) or "").lower()
        size = len(data)
        if existing is not None:
            if existing.size == size and (
                existing.content_sha256 is None or existing.content_sha256 == digest
            ):
                return existing
            # Same key with different content is a protocol error (content-addressed).
            raise ArtifactStorageError(
                ARTIFACT_DIGEST_MISMATCH,
                "object key already holds different content",
            )
        try:
            client.put_object(
                bucket,
                object_key,
                io.BytesIO(data),
                length=size,
                content_type=content_type or "application/octet-stream",
                metadata=dict(metadata),
            )
        except Exception as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object upload failed") from exc
        verified = self.stat(bucket=bucket, object_key=object_key)
        if verified is None or verified.size != size:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object verify failed")
        if verified.content_sha256 and digest and verified.content_sha256 != digest:
            raise ArtifactStorageError(ARTIFACT_DIGEST_MISMATCH, "object digest verify failed")
        return verified

    def stat(self, *, bucket: str, object_key: str) -> ObjectStat | None:
        client = self._client_or_raise()
        try:
            st = client.stat_object(bucket, object_key)
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                return None
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object stat failed") from exc
        except Exception as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object stat failed") from exc
        meta = getattr(st, "metadata", None) or {}
        # MinIO lowercases and may prefix custom metadata with x-amz-meta-.
        digest: str | None = None
        for k, v in dict(meta).items():
            lk = str(k).lower().removeprefix("x-amz-meta-")
            if lk == META_CONTENT_SHA256:
                digest = str(v).strip().lower()
                break
        return ObjectStat(
            bucket_name=bucket,
            object_key=object_key,
            size=int(getattr(st, "size", 0) or 0),
            content_sha256=digest if digest and _SHA256_RE.fullmatch(digest) else None,
            last_modified=_normalize_stat_datetime(getattr(st, "last_modified", None)),
        )

    def get_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        client = self._client_or_raise()
        response = None
        try:
            if length is None:
                response = client.get_object(bucket, object_key, offset=offset)
            else:
                response = client.get_object(bucket, object_key, offset=offset, length=length)
            return response.read()
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchObject", "NoSuchBucket"):
                raise ArtifactStorageError(ARTIFACT_OBJECT_MISSING, "object missing") from exc
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object read failed") from exc
        except ArtifactStorageError:
            raise
        except Exception as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object read failed") from exc
        finally:
            if response is not None:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass

    def delete(self, *, bucket: str, object_key: str) -> bool:
        client = self._client_or_raise()
        return remove_object_safe(client, bucket, object_key)

    def list_keys(
        self,
        *,
        bucket: str,
        prefix: str,
        max_keys: int = 1000,
    ) -> list[ObjectStat]:
        client = self._client_or_raise()
        out: list[ObjectStat] = []
        try:
            for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
                key = getattr(obj, "object_name", None)
                if not key:
                    continue
                # Prefer full stat for digest metadata when available.
                st = self.stat(bucket=bucket, object_key=str(key))
                if st is not None:
                    out.append(st)
                else:
                    out.append(
                        ObjectStat(
                            bucket_name=bucket,
                            object_key=str(key),
                            size=int(getattr(obj, "size", 0) or 0),
                            content_sha256=None,
                            last_modified=_normalize_stat_datetime(
                                getattr(obj, "last_modified", None)
                            ),
                        )
                    )
                if len(out) >= max_keys:
                    break
        except S3Error as exc:
            if getattr(exc, "code", "") in ("NoSuchBucket",):
                return []
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object list failed") from exc
        except ArtifactStorageError:
            raise
        except Exception as exc:
            raise ArtifactStorageError(ARTIFACT_STORAGE_ERROR, "object list failed") from exc
        return out


class InMemoryArtifactObjectBackend:
    """Process-local private object backend for unit tests (no MinIO)."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], tuple[bytes, dict[str, str], datetime]] = {}

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectStat:
        object_key = assert_production_object_key(object_key)
        key = (bucket, object_key)
        existing = self._objects.get(key)
        digest = str(metadata.get(META_CONTENT_SHA256) or "").lower()
        if existing is not None:
            body, meta, created = existing
            if body == bytes(data):
                return ObjectStat(
                    bucket_name=bucket,
                    object_key=object_key,
                    size=len(body),
                    content_sha256=str(meta.get(META_CONTENT_SHA256) or digest or "") or None,
                    last_modified=created,
                )
            raise ArtifactStorageError(
                ARTIFACT_DIGEST_MISMATCH,
                "object key already holds different content",
            )
        created = datetime.now(timezone.utc)
        meta = dict(metadata)
        meta.setdefault("content-type", content_type)
        self._objects[key] = (bytes(data), meta, created)
        return ObjectStat(
            bucket_name=bucket,
            object_key=object_key,
            size=len(data),
            content_sha256=digest or None,
            last_modified=created,
        )

    def stat(self, *, bucket: str, object_key: str) -> ObjectStat | None:
        item = self._objects.get((bucket, object_key))
        if item is None:
            return None
        body, meta, created = item
        digest = str(meta.get(META_CONTENT_SHA256) or "").lower() or None
        return ObjectStat(
            bucket_name=bucket,
            object_key=object_key,
            size=len(body),
            content_sha256=digest,
            last_modified=created,
        )

    def get_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> bytes:
        item = self._objects.get((bucket, object_key))
        if item is None:
            raise ArtifactStorageError(ARTIFACT_OBJECT_MISSING, "object missing")
        body = item[0]
        if offset < 0 or offset > len(body):
            raise ArtifactStorageError(ARTIFACT_RANGE_INVALID, "range invalid")
        if length is None:
            return body[offset:]
        return body[offset : offset + length]

    def delete(self, *, bucket: str, object_key: str) -> bool:
        self._objects.pop((bucket, object_key), None)
        return True

    def list_keys(
        self,
        *,
        bucket: str,
        prefix: str,
        max_keys: int = 1000,
    ) -> list[ObjectStat]:
        out: list[ObjectStat] = []
        for (b, key), (body, meta, created) in sorted(self._objects.items()):
            if b != bucket or not key.startswith(prefix):
                continue
            digest = str(meta.get(META_CONTENT_SHA256) or "").lower() or None
            out.append(
                ObjectStat(
                    bucket_name=bucket,
                    object_key=key,
                    size=len(body),
                    content_sha256=digest,
                    last_modified=created,
                )
            )
            if len(out) >= max_keys:
                break
        return out

    # Test helpers ---------------------------------------------------------
    def force_age(self, *, bucket: str, object_key: str, age_sec: float) -> None:
        item = self._objects.get((bucket, object_key))
        if item is None:
            return
        body, meta, _ = item
        self._objects[(bucket, object_key)] = (
            body,
            meta,
            datetime.now(timezone.utc) - timedelta(seconds=age_sec),
        )

    def drop_without_delete_api(self, *, bucket: str, object_key: str) -> None:
        """Simulate missing object (committed row, lost blob)."""
        self._objects.pop((bucket, object_key), None)


# ---------------------------------------------------------------------------
# Durable Artifact service
# ---------------------------------------------------------------------------


class DurableArtifactService:
    """Persist and read Run-scoped Artifacts in private storage."""

    def __init__(
        self,
        db: Session,
        *,
        backend: ArtifactObjectBackend | None = None,
        settings: Settings | None = None,
        limits: ArtifactLimits | None = None,
        bucket_name: str | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.limits = limits or limits_from_settings(self.settings)
        self.bucket_name = (bucket_name or self.settings.assistant_artifact_bucket).strip()
        if not self.bucket_name:
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "artifact bucket not configured")
        attachment_bucket = (self.settings.minio_bucket or "").strip()
        if attachment_bucket and self.bucket_name == attachment_bucket:
            raise ArtifactStorageError(
                ARTIFACT_INVALID_INPUT,
                "artifact bucket must not equal attachment bucket",
            )
        self.backend: ArtifactObjectBackend = backend or MinioArtifactObjectBackend()

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def prepare(
        self,
        *,
        run_id: UUID,
        content: bytes,
        kind: str = "blob",
        media_type: str = "application/octet-stream",
        display_label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        upload_object: bool = True,
        **kwargs: Any,
    ) -> PreparedArtifact:
        """Validate content and optionally upload object (before row commit).

        ``upload_object=False`` stages an object-backed artifact without calling
        the backend — used only for controlled crash-barrier tests.
        """
        _assert_no_client_object_key(kwargs)
        if not isinstance(content, (bytes, bytearray)):
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "content must be bytes")
        data = bytes(content)
        byte_size = len(data)
        if byte_size > self.limits.max_bytes:
            raise ArtifactStorageError(ARTIFACT_TOO_LARGE, "single artifact exceeds max bytes")
        self._assert_run_budget(run_id, additional=byte_size)
        media = sanitize_media_type(media_type)
        kind_s = sanitize_kind(kind)
        label = sanitize_display_label(display_label)
        meta = sanitize_metadata(metadata)
        digest = sha256_bytes(data)

        # Idempotent same-content retry: reuse existing row identity if present.
        existing = self._find_by_content(run_id, digest, byte_size)
        if existing is not None:
            return PreparedArtifact(
                run_id=run_id,
                kind=str(existing.kind),
                media_type=str(existing.media_type),
                display_label=existing.display_label,
                storage_kind="inline" if existing.storage_kind == "inline" else "object",
                byte_size=int(existing.byte_size),
                content_sha256=str(existing.content_sha256),
                inline_bytes=bytes(existing.inline_bytes) if existing.inline_bytes is not None else None,
                object_key=str(existing.object_key) if existing.object_key else None,
                bucket_name=self.bucket_name if existing.object_key else None,
                metadata_json=dict(existing.metadata_json or {}),
            )

        if byte_size <= self.limits.inline_max_bytes:
            return PreparedArtifact(
                run_id=run_id,
                kind=kind_s,
                media_type=media,
                display_label=label,
                storage_kind=STORAGE_KIND_INLINE,
                byte_size=byte_size,
                content_sha256=digest,
                inline_bytes=data,
                object_key=None,
                bucket_name=None,
                metadata_json=meta,
            )

        object_key = build_object_key(run_id=run_id, content_sha256=digest)
        if upload_object:
            self.backend.put_bytes(
                bucket=self.bucket_name,
                object_key=object_key,
                data=data,
                content_type=media,
                metadata={
                    META_CONTENT_SHA256: digest,
                    META_BYTE_SIZE: str(byte_size),
                    META_RUN_ID: str(run_id),
                },
            )
            # Kill point 7: after Manifest/Artifact upload before Checkpoint/row commit.
            from app.assistant.durable.crash import CrashPoint, maybe_crash

            maybe_crash(CrashPoint.AFTER_MANIFEST_ARTIFACT_UPLOAD_BEFORE_CHECKPOINT)
        return PreparedArtifact(
            run_id=run_id,
            kind=kind_s,
            media_type=media,
            display_label=label,
            storage_kind=STORAGE_KIND_OBJECT,
            byte_size=byte_size,
            content_sha256=digest,
            inline_bytes=None,
            object_key=object_key,
            bucket_name=self.bucket_name,
            metadata_json=meta,
        )

    def commit_row(
        self,
        prepared: PreparedArtifact,
        *,
        artifact_id: UUID | None = None,
    ) -> AssistantRunArtifact:
        """Insert the Artifact row for a prepared payload (semantic txn boundary)."""
        # Plan 09 Task 4: hard tripwire when Eval scope reaches production Artifact writer.
        from app.assistant.evaluation.isolation import (
            tripwire_production_object_key,
            tripwire_production_writer,
        )

        tripwire_production_writer("DurableArtifactService.commit_row")
        if prepared.run_id is None:
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "run_id required")
        existing = self._find_by_content(
            prepared.run_id, prepared.content_sha256, prepared.byte_size
        )
        if existing is not None:
            return existing
        # Defense-in-depth: never persist evaluation-namespace keys on production rows.
        if prepared.object_key is not None:
            tripwire_production_object_key(prepared.object_key)
            assert_production_object_key(prepared.object_key)
        row = AssistantRunArtifact(
            id=artifact_id or uuid4(),
            run_id=prepared.run_id,
            kind=prepared.kind,
            media_type=prepared.media_type,
            display_label=prepared.display_label,
            storage_kind=prepared.storage_kind,
            byte_size=prepared.byte_size,
            content_sha256=prepared.content_sha256,
            inline_bytes=prepared.inline_bytes,
            object_key=prepared.object_key,
            metadata_json=dict(prepared.metadata_json or {}),
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
        except IntegrityError:
            existing = self._find_by_content(
                prepared.run_id, prepared.content_sha256, prepared.byte_size
            )
            if existing is not None:
                return existing
            raise ArtifactStorageError(ARTIFACT_INVALID_INPUT, "artifact row conflict") from None
        return row

    def put_bytes(
        self,
        *,
        run_id: UUID,
        content: bytes,
        kind: str = "blob",
        media_type: str = "application/octet-stream",
        display_label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        commit: bool = True,
        **kwargs: Any,
    ) -> AssistantRunArtifact:
        """Validate, upload (if needed), and optionally commit the Artifact row."""
        prepared = self.prepare(
            run_id=run_id,
            content=content,
            kind=kind,
            media_type=media_type,
            display_label=display_label,
            metadata=metadata,
            **kwargs,
        )
        row = self.commit_row(prepared)
        if commit:
            self.db.commit()
            self.db.refresh(row)
        return row

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(
        self,
        *,
        run_id: UUID,
        artifact_id: UUID,
        expected_digest: str | None = None,
    ) -> StoredArtifactView:
        row = self._load_row(run_id=run_id, artifact_id=artifact_id)
        if expected_digest is not None:
            digest = str(expected_digest).strip().lower()
            if not _SHA256_RE.fullmatch(digest) or digest != str(row.content_sha256).lower():
                raise ArtifactStorageError(ARTIFACT_DIGEST_MISMATCH, "digest mismatch")
        return self._to_view(row)

    def read_chunk(
        self,
        *,
        run_id: UUID,
        artifact_id: UUID,
        offset: int = 0,
        limit: int = 16_384,
        expected_digest: str | None = None,
    ) -> dict[str, Any]:
        """Backend-mediated range read. Never returns object key or URL."""
        if offset < 0 or limit < 1:
            raise ArtifactStorageError(ARTIFACT_RANGE_INVALID, "range invalid")
        row = self._load_row(run_id=run_id, artifact_id=artifact_id)
        if expected_digest is not None:
            digest = str(expected_digest).strip().lower()
            if not _SHA256_RE.fullmatch(digest) or digest != str(row.content_sha256).lower():
                raise ArtifactStorageError(ARTIFACT_DIGEST_MISMATCH, "digest mismatch")
        total = int(row.byte_size)
        if offset > total:
            raise ArtifactStorageError(ARTIFACT_RANGE_INVALID, "range invalid")

        if row.storage_kind == STORAGE_KIND_INLINE:
            body = bytes(row.inline_bytes or b"")
            if len(body) != total:
                raise ArtifactStorageError(
                    ARTIFACT_NEEDS_RECONCILIATION, "inline payload size mismatch"
                )
            chunk = body[offset : offset + limit]
        else:
            object_key = str(row.object_key or "")
            if not object_key:
                raise ArtifactStorageError(
                    ARTIFACT_NEEDS_RECONCILIATION, "object key missing on row"
                )
            st = self.backend.stat(bucket=self.bucket_name, object_key=object_key)
            if st is None:
                raise ArtifactStorageError(
                    ARTIFACT_NEEDS_RECONCILIATION, "object missing for committed row"
                )
            if st.size != total:
                raise ArtifactStorageError(
                    ARTIFACT_NEEDS_RECONCILIATION, "object size mismatch"
                )
            if st.content_sha256 and st.content_sha256 != str(row.content_sha256).lower():
                raise ArtifactStorageError(
                    ARTIFACT_NEEDS_RECONCILIATION, "object digest mismatch"
                )
            try:
                chunk = self.backend.get_bytes(
                    bucket=self.bucket_name,
                    object_key=object_key,
                    offset=offset,
                    length=limit,
                )
            except ArtifactStorageError as exc:
                if exc.code == ARTIFACT_OBJECT_MISSING:
                    raise ArtifactStorageError(
                        ARTIFACT_NEEDS_RECONCILIATION, "object missing for committed row"
                    ) from exc
                raise

        encoding: Literal["utf-8", "base64"]
        try:
            text = chunk.decode("utf-8")
            encoding = "utf-8"
            content: str = text
        except UnicodeDecodeError:
            encoding = "base64"
            content = base64.b64encode(chunk).decode("ascii")

        # Safe structured payload — no key, no URL, no bucket.
        return {
            "artifactId": str(row.id),
            "runId": str(row.run_id),
            "mediaType": str(row.media_type),
            "totalSize": total,
            "offset": offset,
            "returnedBytes": len(chunk),
            "eof": offset + len(chunk) >= total,
            "contentDigest": str(row.content_sha256),
            "encoding": encoding,
            "content": content,
        }

    # ------------------------------------------------------------------
    # Conversation deletion outbox
    # ------------------------------------------------------------------

    def enqueue_gc_for_conversation(self, conversation_id: UUID) -> int:
        """Enqueue object-backed Artifacts for a conversation before DB cascade.

        Must be called in the same transaction that sets
        ``SET LOCAL mindatlas.allow_durable_run_purge = 'on'`` (PostgreSQL)
        and deletes the conversation. Idempotent on (bucket, key, digest).
        """
        run_ids = list(
            self.db.scalars(
                select(AssistantChatRun.id).where(
                    AssistantChatRun.conversation_id == conversation_id
                )
            )
        )
        if not run_ids:
            return 0
        rows = list(
            self.db.scalars(
                select(AssistantRunArtifact).where(
                    AssistantRunArtifact.run_id.in_(run_ids),
                    AssistantRunArtifact.storage_kind == STORAGE_KIND_OBJECT,
                    AssistantRunArtifact.object_key.is_not(None),
                )
            )
        )
        enqueued = 0
        for row in rows:
            if self._enqueue_gc_row(
                bucket_name=self.bucket_name,
                object_key=str(row.object_key),
                content_sha256=str(row.content_sha256),
            ):
                enqueued += 1
        return enqueued

    def enqueue_gc_for_run(self, run_id: UUID) -> int:
        rows = list(
            self.db.scalars(
                select(AssistantRunArtifact).where(
                    AssistantRunArtifact.run_id == run_id,
                    AssistantRunArtifact.storage_kind == STORAGE_KIND_OBJECT,
                    AssistantRunArtifact.object_key.is_not(None),
                )
            )
        )
        enqueued = 0
        for row in rows:
            if self._enqueue_gc_row(
                bucket_name=self.bucket_name,
                object_key=str(row.object_key),
                content_sha256=str(row.content_sha256),
            ):
                enqueued += 1
        return enqueued

    def process_gc_outbox(self, *, limit: int = 50) -> int:
        """Process pending GC outbox rows (idempotent delete)."""
        now = utcnow()
        rows = list(
            self.db.scalars(
                select(AssistantRunArtifactGc)
                .where(
                    AssistantRunArtifactGc.status.in_(("pending", "failed")),
                    (
                        AssistantRunArtifactGc.next_attempt_at.is_(None)
                        | (AssistantRunArtifactGc.next_attempt_at <= now)
                    ),
                )
                .order_by(AssistantRunArtifactGc.created_at.asc())
                .limit(max(1, int(limit)))
            )
        )
        deleted = 0
        for row in rows:
            row.status = "in_progress"
            row.attempts = int(row.attempts or 0) + 1
            self.db.flush()
            ok = self.backend.delete(bucket=str(row.bucket_name), object_key=str(row.object_key))
            if ok:
                row.status = "deleted"
                row.deleted_at = utcnow()
                row.next_attempt_at = None
                deleted += 1
            else:
                row.status = "failed"
                row.next_attempt_at = utcnow() + timedelta(seconds=min(300, 5 * int(row.attempts)))
            self.db.flush()
        self.db.commit()
        return deleted

    # ------------------------------------------------------------------
    # Orphan scanner
    # ------------------------------------------------------------------

    def scan_orphans(
        self,
        *,
        prefix: str = f"{OBJECT_KEY_PREFIX}/",
        max_keys: int = 200,
        now: datetime | None = None,
        grace_sec: int | None = None,
    ) -> int:
        """Bounded orphan scanner. Prefers storage leakage over deleting live data.

        Deletes only when all gates pass (Plan 06 §10):
        - object age strictly greater than validated orphan grace;
        - no assistant_run_artifact row references exact bucket/key/digest;
        - key's Run does not exist or is terminal (no nonterminal may lose object);
        - no live lease and no current/prepared/started Checkpoint unit can commit it;
        - final metadata read still matches scanned key/size/digest;
        - GC outbox path converges through one idempotent delete identity.
        """
        grace = int(grace_sec if grace_sec is not None else validated_orphan_grace_sec(self.settings))
        clock = now or utcnow()
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)

        scanned = self.backend.list_keys(
            bucket=self.bucket_name, prefix=prefix, max_keys=max_keys
        )
        deleted = 0
        for st in scanned:
            if not self._orphan_age_ok(st, clock=clock, grace_sec=grace):
                continue
            if not self._orphan_gates_pass(st):
                continue
            # Final metadata re-read immediately before deletion.
            final = self.backend.stat(bucket=st.bucket_name, object_key=st.object_key)
            if final is None:
                continue
            if final.size != st.size:
                continue
            if (
                st.content_sha256
                and final.content_sha256
                and st.content_sha256 != final.content_sha256
            ):
                continue
            if not self._orphan_age_ok(final, clock=clock, grace_sec=grace):
                continue
            if not self._orphan_gates_pass(final):
                continue
            digest = final.content_sha256 or st.content_sha256 or ("0" * 64)
            # Converge with outbox: enqueue then delete through same identity.
            self._enqueue_gc_row(
                bucket_name=final.bucket_name,
                object_key=final.object_key,
                content_sha256=digest if _SHA256_RE.fullmatch(digest) else ("0" * 64),
            )
            if self.backend.delete(bucket=final.bucket_name, object_key=final.object_key):
                self._mark_gc_deleted(
                    bucket_name=final.bucket_name,
                    object_key=final.object_key,
                    content_sha256=digest if _SHA256_RE.fullmatch(digest) else ("0" * 64),
                )
                deleted += 1
        self.db.commit()
        return deleted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _assert_run_budget(self, run_id: UUID, *, additional: int) -> None:
        used = self.db.scalar(
            select(func.coalesce(func.sum(AssistantRunArtifact.byte_size), 0)).where(
                AssistantRunArtifact.run_id == run_id
            )
        )
        total = int(used or 0) + int(additional)
        if total > self.limits.run_max_bytes:
            raise ArtifactStorageError(
                ARTIFACT_RUN_BUDGET_EXCEEDED, "run cumulative artifact budget exceeded"
            )

    def _find_by_content(
        self, run_id: UUID, content_sha256: str, byte_size: int
    ) -> AssistantRunArtifact | None:
        return self.db.scalar(
            select(AssistantRunArtifact).where(
                AssistantRunArtifact.run_id == run_id,
                AssistantRunArtifact.content_sha256 == content_sha256,
                AssistantRunArtifact.byte_size == byte_size,
            )
        )

    def _load_row(self, *, run_id: UUID, artifact_id: UUID) -> AssistantRunArtifact:
        row = self.db.get(AssistantRunArtifact, artifact_id)
        if row is None:
            raise ArtifactStorageError(ARTIFACT_NOT_FOUND, "artifact not found")
        if row.run_id != run_id:
            # Cross-Run denial — do not leak existence details.
            raise ArtifactStorageError(ARTIFACT_CROSS_RUN_DENIED, "artifact not in run")
        return row

    def _to_view(self, row: AssistantRunArtifact) -> StoredArtifactView:
        return StoredArtifactView(
            artifact_id=row.id,
            run_id=row.run_id,
            kind=str(row.kind),
            media_type=str(row.media_type),
            display_label=row.display_label,
            storage_kind="inline" if row.storage_kind == "inline" else "object",
            byte_size=int(row.byte_size),
            content_sha256=str(row.content_sha256),
            inline_bytes=bytes(row.inline_bytes) if row.inline_bytes is not None else None,
        )

    def _enqueue_gc_row(
        self, *, bucket_name: str, object_key: str, content_sha256: str
    ) -> bool:
        digest = str(content_sha256).strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            digest = "0" * 64
        existing = self.db.scalar(
            select(AssistantRunArtifactGc).where(
                AssistantRunArtifactGc.bucket_name == bucket_name,
                AssistantRunArtifactGc.object_key == object_key,
                AssistantRunArtifactGc.content_sha256 == digest,
            )
        )
        if existing is not None:
            if existing.status == "deleted":
                return False
            if existing.status in ("pending", "failed"):
                return False
            return False
        row = AssistantRunArtifactGc(
            bucket_name=bucket_name,
            object_key=object_key,
            content_sha256=digest,
            status="pending",
            attempts=0,
            next_attempt_at=None,
        )
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
        except IntegrityError:
            return False
        return True

    def _mark_gc_deleted(
        self, *, bucket_name: str, object_key: str, content_sha256: str
    ) -> None:
        digest = str(content_sha256).strip().lower()
        row = self.db.scalar(
            select(AssistantRunArtifactGc).where(
                AssistantRunArtifactGc.bucket_name == bucket_name,
                AssistantRunArtifactGc.object_key == object_key,
                AssistantRunArtifactGc.content_sha256 == digest,
            )
        )
        if row is None:
            return
        row.status = "deleted"
        row.deleted_at = utcnow()
        row.next_attempt_at = None
        self.db.flush()

    def _orphan_age_ok(
        self, st: ObjectStat, *, clock: datetime, grace_sec: int
    ) -> bool:
        age = clock - st.last_modified
        return age > timedelta(seconds=grace_sec)

    def _orphan_gates_pass(self, st: ObjectStat) -> bool:
        # Gate: no artifact row references exact bucket/key/(digest if known).
        q: Select[Any] = select(AssistantRunArtifact.id).where(
            AssistantRunArtifact.storage_kind == STORAGE_KIND_OBJECT,
            AssistantRunArtifact.object_key == st.object_key,
        )
        if st.content_sha256:
            q = q.where(AssistantRunArtifact.content_sha256 == st.content_sha256)
        if self.db.scalar(q.limit(1)) is not None:
            return False

        run_id = parse_run_id_from_object_key(st.object_key)
        if run_id is None:
            # Malformed key under our prefix — still require age gate only; allow GC.
            return True

        run = self.db.get(AssistantChatRun, run_id)
        if run is None:
            return True

        status = str(run.status or "")
        if status in NONTERMINAL_RUN_STATUSES:
            return False

        # Live lease gate: even terminal runs with a residual lease should not lose objects
        # until lease expires (belt-and-suspenders; terminal normally clears lease).
        if run.lease_owner and run.lease_expires_at is not None:
            expires = run.lease_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires > utcnow():
                return False

        # Terminal status gate (above) already excludes nonterminal Runs. Once the
        # Run is completed|failed|cancelled, no semantic CAS can still commit a
        # new Artifact for this Run (result commits require status=running).
        # Do NOT require current Checkpoint phase == "terminal": memory finalizer
        # leaves phase at ready_for_memory while status is completed; requiring
        # phase==terminal permanently blocks orphan GC after normal completion
        # (Plan §10 prefers leak over wrong-delete, not infinite retention).
        #
        # Prefer leak only for anomalous dangling current_checkpoint_id on a
        # terminal Run (pointer without row — uncertain storage state).
        current_id = run.current_checkpoint_id
        if current_id is not None:
            current_cp = self.db.get(AssistantRunCheckpoint, current_id)
            if current_cp is None:
                return False
        return True


def enqueue_conversation_artifact_gc(db: Session, conversation_id: UUID) -> int:
    """Module-level helper for conversation deletion path."""
    return DurableArtifactService(db).enqueue_gc_for_conversation(conversation_id)


__all__ = [
    "ARTIFACT_CROSS_RUN_DENIED",
    "ARTIFACT_DIGEST_MISMATCH",
    "ARTIFACT_INVALID_INPUT",
    "ARTIFACT_KEY_REJECTED",
    "ARTIFACT_MEDIA_TYPE_INVALID",
    "ARTIFACT_NEEDS_RECONCILIATION",
    "ARTIFACT_NOT_FOUND",
    "ARTIFACT_OBJECT_MISSING",
    "ARTIFACT_RANGE_INVALID",
    "ARTIFACT_RUN_BUDGET_EXCEEDED",
    "ARTIFACT_STORAGE_ERROR",
    "ARTIFACT_TOO_LARGE",
    "ArtifactLimits",
    "ArtifactObjectBackend",
    "ArtifactStorageError",
    "DurableArtifactService",
    "InMemoryArtifactObjectBackend",
    "META_BYTE_SIZE",
    "META_CONTENT_SHA256",
    "META_RUN_ID",
    "MinioArtifactObjectBackend",
    "NONTERMINAL_RUN_STATUSES",
    "OBJECT_KEY_PREFIX",
    "ObjectStat",
    "PreparedArtifact",
    "STORAGE_KIND_INLINE",
    "STORAGE_KIND_OBJECT",
    "StoredArtifactView",
    "build_object_key",
    "enqueue_conversation_artifact_gc",
    "limits_from_settings",
    "orphan_grace_floor_sec",
    "parse_run_id_from_object_key",
    "sanitize_display_label",
    "sanitize_kind",
    "sanitize_media_type",
    "sanitize_metadata",
    "validated_orphan_grace_sec",
]
