"""Safe Main Agent event projections (Plan 04 Task 8 / §14.2).

Public payloads contain IDs/digests/counts/status/reason codes only. Internal
diagnostic rows mark ``_visibility="internal"`` so stream_run can advance the
cursor without yielding them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import UUID

from app.assistant.domain.digests import JsonValue

EventVisibility = Literal["public", "internal"]

VISIBILITY_INTERNAL = "internal"
VISIBILITY_PUBLIC = "public"

# Public event names
RUNTIME_SELECTED = "runtime_selected"
SKILL_SEARCH = "skill_search"
SKILL_ACTIVATION_END = "skill_activation_end"
MANIFEST_REVISION = "manifest_revision"
CONTENT_DELTA = "content_delta"
RUN_STATUS = "run_status"
MESSAGE_END = "message_end"
FALLBACK_SELECTED = "fallback_selected"

# Internal-only event names
SKILL_ACTIVATION_START = "skill_activation_start"
INTERNAL_DIAGNOSTIC = "main_agent_diagnostic"
# Plan 05 Task 8 allowlisted internal digests/counts/reasons
AUTHORIZATION_DECISION = "authorization_decision"
BUDGET_RESERVED = "budget_reserved"
BUDGET_STARTED = "budget_started"
BUDGET_RELEASED = "budget_released"
BUDGET_DENIED = "budget_denied"
OBLIGATION_CREATED = "obligation_created"
OBLIGATION_RESOLVED = "obligation_resolved"
COMPLETION_DECISION = "completion_decision"
POLICY_SNAPSHOT = "policy_snapshot"

PLAN05_INTERNAL_EVENTS = frozenset(
    {
        AUTHORIZATION_DECISION,
        BUDGET_RESERVED,
        BUDGET_STARTED,
        BUDGET_RELEASED,
        BUDGET_DENIED,
        OBLIGATION_CREATED,
        OBLIGATION_RESOLVED,
        COMPLETION_DECISION,
        POLICY_SNAPSHOT,
        SKILL_ACTIVATION_START,
        INTERNAL_DIAGNOSTIC,
    }
)


def mark_internal(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a shallow-copied payload tagged as internal-only."""
    out: dict[str, Any] = dict(payload or {})
    out["_visibility"] = VISIBILITY_INTERNAL
    return out


def is_internal_event(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    return payload.get("_visibility") == VISIBILITY_INTERNAL


def strip_visibility_marker(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy payload without the internal visibility marker (for public yield)."""
    out: dict[str, Any] = dict(payload or {})
    out.pop("_visibility", None)
    return out


def runtime_selected_payload(
    *,
    run_id: UUID | str,
    source_runtime: str,
    target_runtime: str,
    reason_code: str | None = None,
    mode: str | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "runId": str(run_id),
        "sourceRuntime": str(source_runtime),
        "targetRuntime": str(target_runtime),
    }
    if reason_code:
        payload["reasonCode"] = str(reason_code)[:64]
    if mode:
        payload["mode"] = str(mode)[:32]
    return payload


def skill_search_payload(
    *,
    catalog_digest: str,
    result_count: int,
    excluded_count: int = 0,
    semantic_fallback: bool = False,
    status: str = "completed",
) -> dict[str, JsonValue]:
    return {
        "catalogDigest": str(catalog_digest)[:64],
        "resultCount": int(result_count),
        "excludedCount": int(excluded_count),
        "semanticFallback": bool(semantic_fallback),
        "status": str(status)[:32],
    }


def skill_activation_end_payload(
    *,
    status: Literal["success", "failed", "discarded", "noop"],
    activated_version_ids: tuple[UUID | str, ...] = (),
    noop_version_ids: tuple[UUID | str, ...] = (),
    reason_code: str | None = None,
    manifest_revision: int | None = None,
    manifest_digest: str | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "status": status,
        "activatedVersionIds": [str(item) for item in activated_version_ids],
        "noopVersionIds": [str(item) for item in noop_version_ids],
    }
    if reason_code:
        payload["reasonCode"] = str(reason_code)[:64]
    # Success Manifest identity only when lifecycle has accepted.
    if status == "success":
        if manifest_revision is not None:
            payload["manifestRevision"] = int(manifest_revision)
        if manifest_digest:
            payload["manifestDigest"] = str(manifest_digest)[:64]
    return payload


def skill_activation_start_payload(
    *,
    call_id: str,
    candidate_count: int,
) -> dict[str, JsonValue]:
    """Internal-only staging progress; must never claim the Skill is active."""
    return {
        "callId": str(call_id)[:128],
        "candidateCount": int(candidate_count),
        "status": "staging",
    }


def manifest_revision_payload(
    *,
    revision: int,
    manifest_digest: str,
    parent_digest: str | None = None,
    active_skill_count: int = 0,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "revision": int(revision),
        "manifestDigest": str(manifest_digest)[:64],
        "activeSkillCount": int(active_skill_count),
    }
    if parent_digest:
        payload["parentDigest"] = str(parent_digest)[:64]
    return payload


def fallback_selected_payload(
    *,
    run_id: UUID | str,
    source_runtime: str,
    target_runtime: str,
    reason_code: str,
    provider_requests_started: int = 0,
    capability_dispatches_started: int = 0,
    strongest_side_effect: str | None = None,
    user_output_started: bool = False,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "runId": str(run_id),
        "sourceRuntime": str(source_runtime),
        "targetRuntime": str(target_runtime),
        "reasonCode": str(reason_code)[:64],
        "providerRequestsStarted": int(provider_requests_started),
        "capabilityDispatchesStarted": int(capability_dispatches_started),
        "userOutputStarted": bool(user_output_started),
    }
    if strongest_side_effect:
        payload["strongestSideEffect"] = str(strongest_side_effect)[:32]
    return payload


class MainAgentEventAdapter:
    """Projects Main Agent / Provider-loop events into safe public/internal sinks.

    Does **not** reuse ChatEventAdapter tool-arg/result methods.
    """

    def __init__(
        self,
        emit: Callable[[str, dict[str, Any]], None],
        *,
        include_internal: bool = True,
    ) -> None:
        self._emit = emit
        self._include_internal = include_internal
        self.public_event_count = 0
        self.internal_event_count = 0
        self.user_output_started = False
        self.skill_summaries: list[dict[str, Any]] = []
        self.tool_summaries: list[dict[str, Any]] = []

    def emit_public(self, event_name: str, payload: Mapping[str, Any] | None = None) -> None:
        data = strip_visibility_marker(payload)
        self._emit(event_name, data)
        self.public_event_count += 1
        if event_name == CONTENT_DELTA and data.get("delta"):
            self.user_output_started = True

    def emit_internal(self, event_name: str, payload: Mapping[str, Any] | None = None) -> None:
        if not self._include_internal:
            return
        data = mark_internal(payload)
        self._emit(event_name, data)
        self.internal_event_count += 1

    def runtime_selected(
        self,
        *,
        run_id: UUID | str,
        source_runtime: str,
        target_runtime: str,
        reason_code: str | None = None,
        mode: str | None = None,
    ) -> None:
        self.emit_public(
            RUNTIME_SELECTED,
            runtime_selected_payload(
                run_id=run_id,
                source_runtime=source_runtime,
                target_runtime=target_runtime,
                reason_code=reason_code,
                mode=mode,
            ),
        )

    def skill_search(
        self,
        *,
        catalog_digest: str,
        result_count: int,
        excluded_count: int = 0,
        semantic_fallback: bool = False,
        status: str = "completed",
    ) -> None:
        self.emit_public(
            SKILL_SEARCH,
            skill_search_payload(
                catalog_digest=catalog_digest,
                result_count=result_count,
                excluded_count=excluded_count,
                semantic_fallback=semantic_fallback,
                status=status,
            ),
        )

    def skill_activation_start(self, *, call_id: str, candidate_count: int) -> None:
        self.emit_internal(
            SKILL_ACTIVATION_START,
            skill_activation_start_payload(
                call_id=call_id, candidate_count=candidate_count
            ),
        )

    def skill_activation_end(
        self,
        *,
        status: Literal["success", "failed", "discarded", "noop"],
        activated_version_ids: tuple[UUID | str, ...] = (),
        noop_version_ids: tuple[UUID | str, ...] = (),
        reason_code: str | None = None,
        manifest_revision: int | None = None,
        manifest_digest: str | None = None,
    ) -> None:
        payload = skill_activation_end_payload(
            status=status,
            activated_version_ids=activated_version_ids,
            noop_version_ids=noop_version_ids,
            reason_code=reason_code,
            manifest_revision=manifest_revision,
            manifest_digest=manifest_digest,
        )
        self.emit_public(SKILL_ACTIVATION_END, payload)
        if status == "success" and activated_version_ids:
            for version_id in activated_version_ids:
                self.skill_summaries.append(
                    {
                        "versionId": str(version_id),
                        "status": "activated",
                    }
                )

    def manifest_revision(
        self,
        *,
        revision: int,
        manifest_digest: str,
        parent_digest: str | None = None,
        active_skill_count: int = 0,
    ) -> None:
        self.emit_public(
            MANIFEST_REVISION,
            manifest_revision_payload(
                revision=revision,
                manifest_digest=manifest_digest,
                parent_digest=parent_digest,
                active_skill_count=active_skill_count,
            ),
        )

    def fallback_selected(
        self,
        *,
        run_id: UUID | str,
        source_runtime: str,
        target_runtime: str,
        reason_code: str,
        provider_requests_started: int = 0,
        capability_dispatches_started: int = 0,
        strongest_side_effect: str | None = None,
    ) -> None:
        self.emit_public(
            FALLBACK_SELECTED,
            fallback_selected_payload(
                run_id=run_id,
                source_runtime=source_runtime,
                target_runtime=target_runtime,
                reason_code=reason_code,
                provider_requests_started=provider_requests_started,
                capability_dispatches_started=capability_dispatches_started,
                strongest_side_effect=strongest_side_effect,
                user_output_started=self.user_output_started,
            ),
        )

    def content_delta(self, delta: str) -> None:
        if not delta:
            return
        self.emit_public(CONTENT_DELTA, {"delta": str(delta)})

    def policy_snapshot(
        self,
        *,
        run_id: UUID | str,
        effective_policy_digest: str,
        exposure_index_digest: str | None = None,
        max_total_capability_calls: int | None = None,
        max_provider_rounds: int | None = None,
        max_capability_depth: int | None = None,
        max_agent_depth: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "runId": str(run_id),
            "effectivePolicyDigest": str(effective_policy_digest)[:64],
        }
        if exposure_index_digest:
            payload["exposureIndexDigest"] = str(exposure_index_digest)[:64]
        if max_total_capability_calls is not None:
            payload["maxTotalCapabilityCalls"] = int(max_total_capability_calls)
        if max_provider_rounds is not None:
            payload["maxProviderRounds"] = int(max_provider_rounds)
        if max_capability_depth is not None:
            payload["maxCapabilityDepth"] = int(max_capability_depth)
        if max_agent_depth is not None:
            payload["maxAgentDepth"] = int(max_agent_depth)
        self.emit_internal(POLICY_SNAPSHOT, payload)

    def authorization_decision(
        self,
        *,
        call_id: str,
        allowed: bool,
        reason_code: str,
        decision_digest: str | None = None,
        owner_kind: str | None = None,
        capability_key: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "callId": str(call_id)[:128],
            "allowed": bool(allowed),
            "reasonCode": str(reason_code)[:64],
        }
        if decision_digest:
            payload["decisionDigest"] = str(decision_digest)[:64]
        if owner_kind:
            payload["ownerKind"] = str(owner_kind)[:32]
        if capability_key:
            payload["capabilityKey"] = str(capability_key)[:128]
        self.emit_internal(AUTHORIZATION_DECISION, payload)

    def budget_event(
        self,
        *,
        event_name: str,
        call_id: str | None = None,
        reason_code: str | None = None,
        dimension: str | None = None,
        remaining: int | None = None,
        reserved_count: int | None = None,
    ) -> None:
        name = str(event_name or BUDGET_DENIED)
        if name not in {
            BUDGET_RESERVED,
            BUDGET_STARTED,
            BUDGET_RELEASED,
            BUDGET_DENIED,
        }:
            name = BUDGET_DENIED
        payload: dict[str, Any] = {}
        if call_id:
            payload["callId"] = str(call_id)[:128]
        if reason_code:
            payload["reasonCode"] = str(reason_code)[:64]
        if dimension:
            payload["dimension"] = str(dimension)[:64]
        if remaining is not None:
            payload["remaining"] = int(remaining)
        if reserved_count is not None:
            payload["reservedCount"] = int(reserved_count)
        self.emit_internal(name, payload)

    def completion_decision(
        self,
        *,
        action: str,
        reason_code: str,
        pending_count: int = 0,
        followup_rounds_started: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action": str(action)[:32],
            "reasonCode": str(reason_code)[:64],
            "pendingCount": int(pending_count),
        }
        if followup_rounds_started is not None:
            payload["followupRoundsStarted"] = int(followup_rounds_started)
        self.emit_internal(COMPLETION_DECISION, payload)

    def diagnostic(self, *, code: str, detail: Mapping[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"code": str(code)[:64]}
        if detail:
            # Only accept already-safe scalar detail values.
            safe_detail: dict[str, JsonValue] = {}
            for key, value in detail.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    if isinstance(value, str) and len(value) > 128:
                        continue
                    safe_detail[str(key)[:64]] = value
            if safe_detail:
                payload["detail"] = safe_detail
        self.emit_internal(INTERNAL_DIAGNOSTIC, payload)

    # ProviderLoopEventSink compatibility
    def emit(self, event_type: str, payload: dict[str, JsonValue]) -> None:
        """Bridge Plan 03 event sink; default to internal unless allow-listed."""
        name = str(event_type or "").strip() or INTERNAL_DIAGNOSTIC
        data = dict(payload or {})
        if name in {
            RUNTIME_SELECTED,
            SKILL_SEARCH,
            SKILL_ACTIVATION_END,
            MANIFEST_REVISION,
            CONTENT_DELTA,
            RUN_STATUS,
            MESSAGE_END,
            FALLBACK_SELECTED,
        }:
            self.emit_public(name, data)
            return
        self.emit_internal(name, data)


__all__ = [
    "AUTHORIZATION_DECISION",
    "BUDGET_DENIED",
    "BUDGET_RELEASED",
    "BUDGET_RESERVED",
    "BUDGET_STARTED",
    "COMPLETION_DECISION",
    "CONTENT_DELTA",
    "FALLBACK_SELECTED",
    "INTERNAL_DIAGNOSTIC",
    "MANIFEST_REVISION",
    "MainAgentEventAdapter",
    "MESSAGE_END",
    "OBLIGATION_CREATED",
    "OBLIGATION_RESOLVED",
    "PLAN05_INTERNAL_EVENTS",
    "POLICY_SNAPSHOT",
    "RUN_STATUS",
    "RUNTIME_SELECTED",
    "SKILL_ACTIVATION_END",
    "SKILL_ACTIVATION_START",
    "SKILL_SEARCH",
    "VISIBILITY_INTERNAL",
    "VISIBILITY_PUBLIC",
    "fallback_selected_payload",
    "is_internal_event",
    "manifest_revision_payload",
    "mark_internal",
    "runtime_selected_payload",
    "skill_activation_end_payload",
    "skill_activation_start_payload",
    "skill_search_payload",
    "strip_visibility_marker",
]
