"""Main Agent control runtime port (Plan 04 Tasks 5–7).

Implements ``MainAgentControlCallPort`` without being imported by capabilities.
Gateway resolves/authorizes/validates, then this port executes the control body.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    completed_result,
    failed_result,
)
from app.assistant.domain.contracts import ResolvedRunManifestRevision
from app.assistant.domain.digests import JsonValue
from app.assistant.main_agent.catalog import (
    CATALOG_CHANGED,
    CATALOG_CURSOR_INVALID,
    CATALOG_UNAVAILABLE,
    CatalogError,
    CatalogSearchState,
)


@dataclass
class PendingManifestEffect:
    """Process-local pending Manifest child staged by skill.inject (Task 6)."""

    call_id: str
    expected_parent_revision: int
    expected_parent_digest: str
    proposed_manifest: ResolvedRunManifestRevision
    effect_digest: str
    activation_payload: dict[str, JsonValue] = field(default_factory=dict)
    post_commit_events: tuple[dict[str, JsonValue], ...] = ()


InjectHandler = Callable[
    [str, dict[str, JsonValue], ResolvedRunManifestRevision],
    tuple[CapabilityResult, PendingManifestEffect | None],
]
ResourceHandler = Callable[[str, dict[str, JsonValue]], CapabilityResult]
ArtifactHandler = Callable[[str, dict[str, JsonValue]], CapabilityResult]


class MainAgentControlRuntime:
    """Call-local control port bound to one Run's catalog/manifest state."""

    def __init__(
        self,
        *,
        catalog_state: CatalogSearchState | None = None,
        current_manifest: ResolvedRunManifestRevision | None = None,
        default_search_limit: int = 8,
        inject_handler: InjectHandler | None = None,
        resource_handler: ResourceHandler | None = None,
        artifact_handler: ArtifactHandler | None = None,
    ) -> None:
        self._catalog_state = catalog_state
        self._current_manifest = current_manifest
        self._default_search_limit = default_search_limit
        self._inject_handler = inject_handler
        self._resource_handler = resource_handler
        self._artifact_handler = artifact_handler
        self._lock = threading.RLock()
        self._pending_effects: dict[str, PendingManifestEffect] = {}
        self._taken_effects: set[str] = set()

    def bind_manifest(self, manifest: ResolvedRunManifestRevision) -> None:
        with self._lock:
            self._current_manifest = manifest

    def bind_catalog(self, catalog_state: CatalogSearchState) -> None:
        with self._lock:
            self._catalog_state = catalog_state

    def execute(
        self,
        *,
        call_id: str,
        capability_key: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult:
        if capability_key == "skill.search":
            return self._execute_search(call_id=call_id, validated_input=validated_input)
        if capability_key == "skill.inject":
            return self._execute_inject(call_id=call_id, validated_input=validated_input)
        if capability_key == "skill.read_resource":
            return self._execute_read_resource(call_id=call_id, validated_input=validated_input)
        if capability_key == "artifact.read":
            return self._execute_artifact_read(call_id=call_id, validated_input=validated_input)
        return failed_result(
            error=CapabilityError(
                error_type="not_found",
                safe_code="unknown_control",
                safe_message="unknown main agent control",
                retry_disposition="never",
                call_id=call_id,
            ),
            metrics=CapabilityMetrics(duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0),
        )

    def take_manifest_effect(self, *, call_id: str) -> PendingManifestEffect | None:
        with self._lock:
            if call_id in self._taken_effects:
                return None
            effect = self._pending_effects.pop(call_id, None)
            if effect is not None:
                self._taken_effects.add(call_id)
            return effect

    def discard_pending(self, *, call_id: str) -> None:
        with self._lock:
            self._pending_effects.pop(call_id, None)
            self._taken_effects.discard(call_id)

    def has_pending_effect(self, *, call_id: str) -> bool:
        with self._lock:
            return call_id in self._pending_effects

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def _execute_search(
        self,
        *,
        call_id: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult:
        state = self._catalog_state
        if state is None:
            return self._control_error(
                call_id=call_id,
                safe_code=CATALOG_UNAVAILABLE,
                safe_message="skill catalog is unavailable",
            )
        query = str(validated_input.get("query") or "")
        limit_raw = validated_input.get("limit")
        limit = int(limit_raw) if isinstance(limit_raw, int) and not isinstance(limit_raw, bool) else None
        cursor_raw = validated_input.get("cursor")
        cursor = str(cursor_raw) if isinstance(cursor_raw, str) else None
        try:
            result = state.search(query=query, limit=limit, cursor=cursor)
        except CatalogError as exc:
            code = exc.reason_code
            if code == CATALOG_CHANGED:
                msg = "skill catalog changed"
            elif code == CATALOG_CURSOR_INVALID:
                msg = "catalog cursor is invalid"
            else:
                msg = "skill search failed"
            return self._control_error(call_id=call_id, safe_code=code[:64], safe_message=msg)
        records = []
        for hit in result.hits:
            record = state.snapshot.get_by_version_id(hit.version_id)
            if record is None:
                continue
            records.append(
                {
                    "versionId": str(record.version_id),
                    "canonicalName": record.canonical_name,
                    "description": record.description,
                    "contentDigest": record.content_digest,
                    "rank": hit.rank,
                    "score": hit.score,
                }
            )
        payload: dict[str, JsonValue] = {
            "catalogDigest": result.catalog_digest,
            "records": records,  # type: ignore[dict-item]
            "nextCursor": result.next_cursor,
            "excludedCount": result.excluded_count,
            "semanticFallback": result.semantic_fallback,
        }
        return completed_result(
            user_text=None,
            structured_output=payload,
            metrics=CapabilityMetrics(
                duration_ms=0.0,
                adapter_duration_ms=0.0,
                input_bytes=0,
                output_bytes=0,
            ),
            terminal_output=False,
            needs_followup=True,
        )

    def _execute_inject(
        self,
        *,
        call_id: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult:
        handler = self._inject_handler
        manifest = self._current_manifest
        if handler is None or manifest is None:
            return self._control_error(
                call_id=call_id,
                safe_code="control_not_configured",
                safe_message="skill inject is not configured",
            )
        try:
            result, effect = handler(call_id, validated_input, manifest)
        except Exception:
            return self._control_error(
                call_id=call_id,
                safe_code="control_effect_protocol_error",
                safe_message="skill inject failed",
            )
        if result.status != "completed":
            # Failed inject must leave no pending effect.
            with self._lock:
                self._pending_effects.pop(call_id, None)
            return result
        if effect is not None:
            with self._lock:
                self._pending_effects[call_id] = effect
        return result

    def _execute_read_resource(
        self,
        *,
        call_id: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult:
        handler = self._resource_handler
        if handler is None:
            return self._control_error(
                call_id=call_id,
                safe_code="control_not_configured",
                safe_message="skill read resource is not configured",
            )
        try:
            return handler(call_id, validated_input)
        except Exception:
            return self._control_error(
                call_id=call_id,
                safe_code="resource_not_found",
                safe_message="skill resource read failed",
            )

    def _execute_artifact_read(
        self,
        *,
        call_id: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult:
        handler = self._artifact_handler
        if handler is None:
            return self._control_error(
                call_id=call_id,
                safe_code="control_not_configured",
                safe_message="artifact read is not configured",
            )
        try:
            return handler(call_id, validated_input)
        except Exception:
            return self._control_error(
                call_id=call_id,
                safe_code="artifact_not_found",
                safe_message="artifact read failed",
            )

    @staticmethod
    def _control_error(
        *,
        call_id: str,
        safe_code: str,
        safe_message: str,
    ) -> CapabilityResult:
        return failed_result(
            error=CapabilityError(
                error_type="execution_failed",
                safe_code=safe_code[:64],
                safe_message=safe_message[:256],
                retry_disposition="never",
                call_id=call_id,
            ),
            metrics=CapabilityMetrics(
                duration_ms=0.0,
                adapter_duration_ms=0.0,
                input_bytes=0,
                output_bytes=0,
            ),
        )


__all__ = [
    "InjectHandler",
    "MainAgentControlRuntime",
    "PendingManifestEffect",
]
