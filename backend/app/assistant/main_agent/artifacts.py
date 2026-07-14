"""Process-local transient Artifact store and result projection (Plan 04 Task 7)."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.assistant.capabilities.contracts import (
    ArtifactRef,
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    completed_result,
    failed_result,
)
from app.assistant.domain.digests import JsonValue, canonical_json_bytes, sha256_bytes, sha256_canonical_json

DEFAULT_MAX_ARTIFACT_BYTES = 1_048_576
DEFAULT_RUN_MAX_BYTES = 5_242_880
DEFAULT_MAX_ARTIFACTS = 64
DEFAULT_INLINE_RESULT_BYTES = 16_384

ARTIFACT_NOT_FOUND = "artifact_not_found"
ARTIFACT_RANGE_INVALID = "artifact_range_invalid"
RESULT_TOO_LARGE = "result_too_large"
NON_DURABLE_STATE_LOST = "non_durable_state_lost"


TokenFactory = Callable[[], str]


def _default_token() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    media_type: str
    content: bytes
    content_digest: str

    @property
    def byte_size(self) -> int:
        return len(self.content)

    def ref(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            media_type=self.media_type,
            content_digest=self.content_digest,
        )


class TransientArtifactStore:
    """Process-local Run-scoped Artifact store with size/count caps."""

    def __init__(
        self,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
        run_max_bytes: int = DEFAULT_RUN_MAX_BYTES,
        max_artifacts: int = DEFAULT_MAX_ARTIFACTS,
        token_factory: TokenFactory | None = None,
    ) -> None:
        if max_artifact_bytes < 1 or run_max_bytes < 1 or max_artifacts < 1:
            raise ValueError("artifact limits must be positive")
        self._max_artifact_bytes = int(max_artifact_bytes)
        self._run_max_bytes = int(run_max_bytes)
        self._max_artifacts = int(max_artifacts)
        self._token_factory = token_factory or _default_token
        self._lock = threading.RLock()
        self._items: OrderedDict[str, StoredArtifact] = OrderedDict()
        self._total_bytes = 0
        self._referenced: set[str] = set()
        self._cleared = False

    def mark_referenced(self, artifact_id: str) -> None:
        with self._lock:
            self._referenced.add(artifact_id)

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        if not isinstance(content, (bytes, bytearray)):
            raise TypeError("content must be bytes")
        data = bytes(content)
        if len(data) > self._max_artifact_bytes:
            raise ValueError(RESULT_TOO_LARGE)
        with self._lock:
            if self._cleared:
                raise RuntimeError(NON_DURABLE_STATE_LOST)
            if self._total_bytes + len(data) > self._run_max_bytes:
                self._evict_until(needed=len(data))
            if len(self._items) >= self._max_artifacts:
                self._evict_one()
            if self._total_bytes + len(data) > self._run_max_bytes:
                raise ValueError(RESULT_TOO_LARGE)
            artifact_id = self._token_factory()
            if not isinstance(artifact_id, str) or not artifact_id:
                raise ValueError("artifact token invalid")
            digest = sha256_bytes(data)
            stored = StoredArtifact(
                artifact_id=artifact_id,
                media_type=media_type,
                content=data,
                content_digest=digest,
            )
            self._items[artifact_id] = stored
            self._items.move_to_end(artifact_id)
            self._total_bytes += len(data)
            return stored.ref()

    def put_json(self, value: JsonValue, *, media_type: str = "application/json") -> ArtifactRef:
        data = canonical_json_bytes(value)
        return self.put_bytes(data, media_type=media_type)

    def put_text(self, text: str, *, media_type: str = "text/plain; charset=utf-8") -> ArtifactRef:
        return self.put_bytes(text.encode("utf-8"), media_type=media_type)

    def get(self, artifact_id: str) -> StoredArtifact | None:
        with self._lock:
            if self._cleared:
                return None
            item = self._items.get(artifact_id)
            if item is not None:
                self._items.move_to_end(artifact_id)
            return item

    def read_chunk(
        self,
        artifact_id: str,
        *,
        offset: int = 0,
        limit: int = 16_384,
    ) -> dict[str, Any]:
        if offset < 0 or limit < 1:
            raise ValueError(ARTIFACT_RANGE_INVALID)
        item = self.get(artifact_id)
        if item is None:
            raise KeyError(ARTIFACT_NOT_FOUND)
        total = item.byte_size
        if offset > total:
            raise ValueError(ARTIFACT_RANGE_INVALID)
        chunk = item.content[offset : offset + limit]
        # Prefer utf-8 when possible.
        encoding: Literal["utf-8", "base64"]
        try:
            text = chunk.decode("utf-8")
            encoding = "utf-8"
            content: str = text
        except UnicodeDecodeError:
            import base64

            encoding = "base64"
            content = base64.b64encode(chunk).decode("ascii")
        return {
            "artifactId": item.artifact_id,
            "mediaType": item.media_type,
            "totalSize": total,
            "offset": offset,
            "returnedBytes": len(chunk),
            "eof": offset + len(chunk) >= total,
            "contentDigest": item.content_digest,
            "encoding": encoding,
            "content": content,
        }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total_bytes = 0
            self._referenced.clear()
            self._cleared = True

    def _evict_until(self, *, needed: int) -> None:
        while self._items and self._total_bytes + needed > self._run_max_bytes:
            if not self._evict_one():
                break

    def _evict_one(self) -> bool:
        # Deterministic LRU: oldest non-referenced first; never silently drop referenced.
        for artifact_id, item in list(self._items.items()):
            if artifact_id in self._referenced:
                continue
            self._items.pop(artifact_id, None)
            self._total_bytes -= item.byte_size
            return True
        return False

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._items)


def project_capability_result(
    result: CapabilityResult,
    *,
    store: TransientArtifactStore,
    inline_threshold_bytes: int = DEFAULT_INLINE_RESULT_BYTES,
) -> CapabilityResult:
    """Project oversized structured/user content into ArtifactRef summaries."""
    if result.status != "completed":
        return result
    # Preserve already-returned ArtifactRef values.
    existing_refs = list(result.artifact_refs)
    structured = result.structured_output
    user_text = result.user_text

    def _too_large(value: Any) -> bool:
        try:
            if isinstance(value, str):
                return len(value.encode("utf-8")) > inline_threshold_bytes
            if value is None:
                return False
            return len(canonical_json_bytes(value)) > inline_threshold_bytes  # type: ignore[arg-type]
        except Exception:
            return True

    new_refs = list(existing_refs)
    new_structured = structured
    new_user_text = user_text
    summary_bits: list[str] = []

    try:
        if structured is not None and _too_large(structured):
            ref = store.put_json(structured)  # type: ignore[arg-type]
            store.mark_referenced(ref.artifact_id)
            new_refs.append(ref)
            new_structured = {
                "artifactRef": {
                    "artifactId": ref.artifact_id,
                    "mediaType": ref.media_type,
                    "contentDigest": ref.content_digest,
                },
                "summary": "structured_output_artifactized",
            }
            summary_bits.append("structured")
        if user_text is not None and _too_large(user_text):
            ref = store.put_text(user_text)
            store.mark_referenced(ref.artifact_id)
            new_refs.append(ref)
            new_user_text = f"[artifact:{ref.artifact_id}]"
            summary_bits.append("user_text")
    except ValueError as exc:
        if str(exc) == RESULT_TOO_LARGE:
            return failed_result(
                error=CapabilityError(
                    error_type="execution_failed",
                    safe_code=RESULT_TOO_LARGE,
                    safe_message="result exceeds artifact limits",
                    retry_disposition="never",
                    call_id=None,
                ),
                metrics=result.metrics
                or CapabilityMetrics(
                    duration_ms=0.0,
                    adapter_duration_ms=0.0,
                    input_bytes=0,
                    output_bytes=0,
                ),
            )
        raise

    if not summary_bits and new_refs == existing_refs:
        return result
    return result.model_copy(
        update={
            "structured_output": new_structured,
            "user_text": new_user_text,
            "artifact_refs": tuple(new_refs),
        }
    )


def handle_artifact_read(
    *,
    call_id: str,
    validated_input: dict[str, JsonValue],
    store: TransientArtifactStore,
) -> CapabilityResult:
    artifact_id = str(validated_input.get("artifactId") or "")
    offset_raw = validated_input.get("offset", 0)
    limit_raw = validated_input.get("limit", 16_384)
    offset = int(offset_raw) if isinstance(offset_raw, int) and not isinstance(offset_raw, bool) else 0
    limit = int(limit_raw) if isinstance(limit_raw, int) and not isinstance(limit_raw, bool) else 16_384
    try:
        payload = store.read_chunk(artifact_id, offset=offset, limit=limit)
    except KeyError:
        return failed_result(
            error=CapabilityError(
                error_type="not_found",
                safe_code=ARTIFACT_NOT_FOUND,
                safe_message="artifact not found",
                retry_disposition="never",
                call_id=call_id,
            ),
            metrics=CapabilityMetrics(
                duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
            ),
        )
    except ValueError as exc:
        code = str(exc) if str(exc) in {ARTIFACT_RANGE_INVALID, NON_DURABLE_STATE_LOST} else ARTIFACT_RANGE_INVALID
        return failed_result(
            error=CapabilityError(
                error_type="invalid_input" if code == ARTIFACT_RANGE_INVALID else "execution_failed",
                safe_code=code[:64],
                safe_message="artifact range invalid"
                if code == ARTIFACT_RANGE_INVALID
                else "non durable state lost",
                retry_disposition="never",
                call_id=call_id,
            ),
            metrics=CapabilityMetrics(
                duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
            ),
        )
    except RuntimeError as exc:
        if str(exc) == NON_DURABLE_STATE_LOST:
            return failed_result(
                error=CapabilityError(
                    error_type="execution_failed",
                    safe_code=NON_DURABLE_STATE_LOST,
                    safe_message="non durable state lost",
                    retry_disposition="never",
                    call_id=call_id,
                ),
                metrics=CapabilityMetrics(
                    duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
                ),
            )
        raise
    return completed_result(
        user_text=None,
        structured_output=payload,  # type: ignore[arg-type]
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
        terminal_output=False,
        needs_followup=True,
    )


__all__ = [
    "ARTIFACT_NOT_FOUND",
    "ARTIFACT_RANGE_INVALID",
    "DEFAULT_INLINE_RESULT_BYTES",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MAX_ARTIFACTS",
    "DEFAULT_RUN_MAX_BYTES",
    "NON_DURABLE_STATE_LOST",
    "RESULT_TOO_LARGE",
    "StoredArtifact",
    "TransientArtifactStore",
    "handle_artifact_read",
    "project_capability_result",
]
