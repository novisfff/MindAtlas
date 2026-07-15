"""Core Provider Agent Loop — multi-round tools, sibling scheduling, waiting/resume.

Plan 03 Tasks 3–5: rebuild tools before every Provider round, assemble one
assistant message via the stream assembler, soft-finalize with tools disabled
on the reserved last round after Tool use, verify classification freshness
before planning/dispatch, schedule all sibling Tool Calls safely, support
portable waiting/resume and cancellation sealing, and never emit provisional
tool-call prose as final text.

Does not implement live OpenAI adapters (later tasks).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityResult,
    ContinuationRef,
)
from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError
from app.assistant.domain.contracts import (
    ModelRef,
    ResolvedRunManifestRevision,
    validate_manifest_child_link,
)
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
    lookup_tool_by_alias,
)
from app.assistant.provider_loop.contracts import (
    CapabilityCallReservationDecision,
    CapabilityCallReservationItem,
    ProviderAdapter,
    ProviderCompletionDisposition,
    ProviderCompletionRequest,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderExecutionScope,
    ProviderGenerationOptions,
    ProviderLoopContinuation,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopResult,
    ProviderLoopResumeRequest,
    ProviderRoundBudgetDeniedError,
    ProviderRoundBudgetGuard,
    ProviderRoundRequest,
    ProviderRoundResult,
    ProviderToolChoice,
    ProviderToolDefinition,
    ProviderToolSurface,
    ProviderUsage,
    ProviderWaitingCallState,
    ProviderWaitingResolution,
    RoundContextResolution,
    SafeProviderError,
    ToolSurfaceResolution,
    aggregate_provider_usage,
    compute_scope_digest,
    project_waiting_resolution_message,
    recompute_continuation_identity,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderCompletionInstructionMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderRuntimeInstructionMessage,
    ProviderToolCall,
    ProviderToolCallRecord,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    digest_provider_message,
    digest_provider_transcript,
    project_tool_result_envelope,
    seal_cancelled_continuation,
    validate_provider_transcript,
)
from app.assistant.provider_loop.scheduler import (
    DEFAULT_MAX_WORKERS,
    BoundedIsolatedSiblingExecutor,
    DispatcherCapabilities,
    SequentialSiblingExecutor,
    merge_parallel_manifests,
    plan_sibling_execution,
)
from app.assistant.provider_loop.streaming import (
    DefaultFinalizationInstructionProvider,
    FinalizationInstructionProvider,
    assemble_provider_round,
    is_finalization_round,
)


# Stable reason codes that AuthorizationEvidenceVerificationError may carry from
# pure policy deny / evidence verification. Unknown codes collapse to
# authorization_evidence_failed so exception text never leaks onto Tool Results.
# Kept local (no policy package import) to preserve the provider_loop boundary.
_AUTH_EVIDENCE_STABLE_REASON_CODES: frozenset[str] = frozenset(
    {
        # Plan 05 §5.4 authorization deny codes (pure policy).
        "scope_mismatch",
        "manifest_surface_mismatch",
        "exposure_missing",
        "exposure_ambiguous",
        "owner_mismatch",
        "principal_unauthenticated",
        "principal_not_allowed",
        "entrypoint_not_allowed",
        "global_policy_denied",
        "owner_capability_not_declared",
        "owner_side_effect_denied",
        "release_gate_denied",
        "target_unavailable",
        "version_or_digest_drift",
        "recursion_denied",
        # Evidence/factory verification codes raised by main_agent.authorization.
        "author_policy_missing",
        "binding_contract_digest_mismatch",
        "binding_not_in_manifest",
        "call_id_mismatch",
        "call_id_replay",
        "capability_key_mismatch",
        "conversation_id_mismatch",
        "decision_not_allowed",
        "dependency_closure_digest_mismatch",
        "empty_granted_side_effects",
        "evidence_already_consumed",
        "evidence_digest_mismatch",
        "evidence_grant_mismatch",
        "grant_derivation_failed",
        "granted_side_effects_mismatch",
        "grant_source_digest_mismatch",
        "interrupt_mode_above_ceiling",
        "issuer_entrypoint_mismatch",
        "missing_grant_source_digest",
        "owner_id_mismatch",
        "owner_kind_mismatch",
        "owner_version_mismatch",
        "owner_version_missing",
        "principal_mismatch",
        "resolution_digest_mismatch",
        "run_id_mismatch",
        "scope_identity_mismatch",
        "side_effect_above_ceiling",
        "unauthenticated_principal",
        "unknown_issuer_entrypoint",
        "unknown_side_effect",
        "verifier_not_found",
        # Generic / gateway-aligned policy deny surface.
        "policy_denied",
        "capability_denied",
        # Budget / completion codes that may surface via auth factory.
        "budget_exhausted_total_calls",
        "budget_exhausted_owner_calls",
        "budget_exhausted_parallel",
        "budget_exhausted_read_signature",
        "budget_exhausted_owner_read_signature",
        "budget_exhausted_deadline",
        "budget_exhausted_provider_rounds",
        "budget_exhausted_completion_tokens",
        "budget_exhausted_prompt_tokens",
        "budget_exhausted_capability_depth",
        "budget_exhausted_agent_depth",
        "budget_exhausted_main_agent_cycles",
        "budget_exhausted_completion_followups",
        "budget_exhausted_active_skills",
        "skill_completion_unsatisfiable",
        "policy_state_protocol_error",
    }
)


def _stable_auth_evidence_reason_code(reason_code: str | None) -> str:
    """Map AuthorizationEvidenceVerificationError.reason_code to a Tool Result safe_code.

    Allowlisted policy/budget/completion/evidence codes are preserved; everything
    else collapses to authorization_evidence_failed (no exception text leakage).
    """
    if not isinstance(reason_code, str):
        return "authorization_evidence_failed"
    code = reason_code.strip()
    if not code or len(code) > 64:
        return "authorization_evidence_failed"
    if any(ch.isspace() for ch in code):
        return "authorization_evidence_failed"
    if code in _AUTH_EVIDENCE_STABLE_REASON_CODES:
        return code
    return "authorization_evidence_failed"


class ProviderLoopError(Exception):
    """Internal loop failure carrying a safe terminal result payload."""

    def __init__(
        self,
        *,
        stop_reason: str,
        error: SafeProviderError,
        messages: tuple[ProviderMessage, ...] = (),
        tool_calls: tuple[ProviderToolCallRecord, ...] = (),
        final_text: str | None = None,
        manifest: ResolvedRunManifestRevision | None = None,
        usage: ProviderUsage | None = None,
        round_count: int = 0,
        status: str = "failed",
    ) -> None:
        super().__init__(error.safe_summary)
        self.stop_reason = stop_reason
        self.error = error
        self.messages = messages
        self.tool_calls = tool_calls
        self.final_text = final_text
        self.manifest = manifest
        self.usage = usage or ProviderUsage()
        self.round_count = round_count
        self.status = status


def run_provider_agent_loop(
    request: ProviderLoopRequest,
    ports: ProviderLoopPorts,
    *,
    finalization_instructions: FinalizationInstructionProvider | None = None,
) -> ProviderLoopResult:
    """Run the provider-neutral agent loop until completion, failure, or cancel."""
    if not isinstance(request, ProviderLoopRequest):
        raise TypeError("request must be a ProviderLoopRequest")
    if not isinstance(ports, ProviderLoopPorts):
        raise TypeError("ports must be a ProviderLoopPorts")

    instruction_provider = finalization_instructions or DefaultFinalizationInstructionProvider()
    messages: list[ProviderMessage] = list(request.initial_messages)
    tool_call_records: list[ProviderToolCallRecord] = []
    current_manifest = request.manifest
    accumulated_usage = ProviderUsage()
    round_count = 0
    prior_tool_call_count = 0
    finalization_instruction_appended = False
    already_applied_skill_version_ids: tuple[UUID, ...] = ()

    try:
        _validate_loop_identity(request=request, provider=ports.provider)
        _emit(
            ports,
            "loop.started",
            {
                "runId": str(request.execution_scope.run_id),
                "scopeDigest": request.execution_scope.scope_digest,
                "manifestRevision": current_manifest.revision,
                "manifestDigest": current_manifest.manifest_digest,
                "maxRounds": request.max_rounds,
            },
        )

        while True:
            if ports.cancellation.is_cancelled():
                raise ProviderLoopError(
                    status="cancelled",
                    stop_reason="cancelled",
                    error=SafeProviderError(
                        semantic_code="cancelled",
                        safe_summary="loop cancelled before provider round",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            if round_count >= request.max_rounds:
                raise ProviderLoopError(
                    stop_reason="max_rounds_hard_stop",
                    error=SafeProviderError(
                        semantic_code="max_rounds_hard_stop",
                        safe_summary="provider round budget exhausted",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            _validate_loop_identity(request=request, provider=ports.provider)
            validate_provider_transcript(tuple(messages))

            round_index = round_count
            finalization = is_finalization_round(
                round_index=round_index,
                max_rounds=request.max_rounds,
                prior_tool_call_count=prior_tool_call_count,
            )

            if finalization:
                # Reserved tools-disabled finalization: empty surface, no tools_provider.
                surface = _empty_finalization_surface(
                    manifest=current_manifest,
                    provider_protocol=ports.provider.provider_protocol,
                    scope=request.execution_scope,
                )
                if not finalization_instruction_appended:
                    instruction = instruction_provider.build(locale=request.locale)
                    if not isinstance(instruction, ProviderRuntimeInstructionMessage):
                        raise ProviderLoopError(
                            stop_reason="protocol_error",
                            error=SafeProviderError(
                                semantic_code="finalization_instruction_invalid",
                                safe_summary="finalization instruction must be a runtime message",
                                retry_disposition="never",
                            ),
                            messages=tuple(messages),
                            tool_calls=tuple(tool_call_records),
                            manifest=current_manifest,
                            usage=accumulated_usage,
                            round_count=round_count,
                        )
                    messages.append(instruction)
                    finalization_instruction_appended = True
                generation = ProviderGenerationOptions(
                    max_output_tokens=request.generation.max_output_tokens,
                    temperature=request.generation.temperature,
                    tool_choice=ProviderToolChoice(mode="none"),
                    request_parallel_tool_calls=request.generation.request_parallel_tool_calls,
                )
                tools_enabled = False
                _emit(
                    ports,
                    "finalization.started",
                    {
                        "roundIndex": round_index,
                        "manifestRevision": current_manifest.revision,
                        "manifestDigest": current_manifest.manifest_digest,
                        "priorToolCallCount": prior_tool_call_count,
                    },
                )
            else:
                try:
                    resolution = ports.tools_provider.resolve(
                        current_manifest,
                        scope=request.execution_scope,
                        locale=request.locale,
                    )
                except ProviderLoopError:
                    raise
                except Exception as exc:  # noqa: BLE001 - map to safe protocol error
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="tools_provider_failed",
                            safe_summary="tools provider failed before provider round",
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    ) from exc

                resolution = _require_tool_surface_resolution(resolution)
                _validate_alias_revision_lineage(
                    previous=current_manifest,
                    next_manifest=resolution.manifest,
                )
                current_manifest = resolution.manifest
                surface = resolution.surface
                generation = request.generation
                tools_enabled = True

                # max_rounds=1 with a nonempty tool surface is illegal.
                if request.max_rounds == 1 and surface.tools:
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="max_rounds_surface_conflict",
                            safe_summary=(
                                "nonempty tool surface requires max_rounds >= 2 "
                                "so a finalization round can be reserved"
                            ),
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    )

                # After Tool surface (and any alias child), resolve protected context.
                already_applied_skill_version_ids = _append_round_context(
                    ports=ports,
                    messages=messages,
                    current_manifest=current_manifest,
                    already_applied_skill_version_ids=already_applied_skill_version_ids,
                    execution_scope=request.execution_scope,
                    locale=request.locale,
                    tool_call_records=tool_call_records,
                    accumulated_usage=accumulated_usage,
                    round_count=round_count,
                )

            round_count += 1

            _emit(
                ports,
                "round.started",
                {
                    "roundIndex": round_index,
                    "manifestRevision": current_manifest.revision,
                    "manifestDigest": current_manifest.manifest_digest,
                    "surfaceDigest": surface.surface_digest,
                    "toolCount": len(surface.tools),
                    "toolsEnabled": tools_enabled,
                    "finalizationRound": finalization,
                },
            )

            round_request = ProviderRoundRequest(
                round_index=round_index,
                messages=tuple(messages),
                tool_surface=surface,
                tools_enabled=tools_enabled,
                finalization_round=finalization,
                model_ref=request.model_ref,
                generation=generation,
            )

            # Plan 05: round budget guard immediately before Provider I/O.
            # Caps max_output_tokens by remaining completion budget when present.
            # Denial blocks the next round without undoing prior completed work.
            try:
                guarded_generation = _apply_round_budget_before(
                    ports=ports,
                    round_request=round_request,
                )
            except ProviderLoopError as budget_err:
                budget_err.messages = tuple(messages)
                budget_err.tool_calls = tuple(tool_call_records)
                budget_err.manifest = current_manifest
                budget_err.usage = accumulated_usage
                budget_err.round_count = round_count
                raise
            if guarded_generation is not generation:
                round_request = ProviderRoundRequest(
                    round_index=round_index,
                    messages=tuple(messages),
                    tool_surface=surface,
                    tools_enabled=tools_enabled,
                    finalization_round=finalization,
                    model_ref=request.model_ref,
                    generation=guarded_generation,
                )

            try:
                events = list(
                    ports.provider.stream_round(
                        round_request,
                        cancellation=ports.cancellation,
                    )
                )
            except ProviderLoopError:
                raise
            except Exception as exc:  # noqa: BLE001 - sanitize provider failures
                stop = "max_rounds_hard_stop" if finalization else "provider_error"
                raise ProviderLoopError(
                    stop_reason=stop,
                    error=SafeProviderError(
                        semantic_code=stop,
                        safe_summary=(
                            "finalization provider round failed"
                            if finalization
                            else "provider round failed"
                        ),
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from exc

            try:
                round_result = assemble_provider_round(
                    events=events,
                    surface=surface,
                    round_index=round_index,
                )
            except ValueError as exc:
                stop = "max_rounds_hard_stop" if finalization else "protocol_error"
                raise ProviderLoopError(
                    stop_reason=stop,
                    error=SafeProviderError(
                        semantic_code=stop if finalization else "protocol_error",
                        safe_summary=str(exc) or "provider stream protocol error",
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from exc

            # Report usage to the round budget guard after assembly (even on later failure).
            _apply_round_budget_after(ports=ports, round_result=round_result)

            assistant = round_result.assistant_message
            accumulated_usage = aggregate_provider_usage(
                accumulated_usage,
                round_result.usage,
            )
            messages.append(assistant)

            _emit(
                ports,
                "round.completed",
                {
                    "roundIndex": round_index,
                    "toolCallCount": len(assistant.tool_calls),
                    "finishReason": round_result.finish_reason,
                    "surfaceDigest": surface.surface_digest,
                    "finalizationRound": finalization,
                },
            )

            if not assistant.tool_calls:
                # Plan 05: present no-Tool candidate to completion guard.
                # Default no-op reproduces Plan 03 natural-completion byte-for-byte.
                completed = _handle_no_tool_completion(
                    ports=ports,
                    messages=messages,
                    tool_call_records=tool_call_records,
                    current_manifest=current_manifest,
                    accumulated_usage=accumulated_usage,
                    round_count=round_count,
                    round_index=round_index,
                    finalization=finalization,
                    final_text=assistant.content,
                )
                if completed is not None:
                    return completed
                # continue: protected instruction appended; next loop iteration.
                continue

            if finalization:
                # Tools-disabled finalization must never execute Tool Calls.
                raise ProviderLoopError(
                    stop_reason="max_rounds_hard_stop",
                    error=SafeProviderError(
                        semantic_code="max_rounds_hard_stop",
                        safe_summary="finalization round returned tool calls",
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            # Tool-call path: provisional prose is retained in history only.
            prior_tool_call_count += len(assistant.tool_calls)
            for call in assistant.tool_calls:
                _emit(
                    ports,
                    "tool_call.requested",
                    {
                        "callId": call.call_id,
                        "callIndex": call.call_index,
                        "providerAlias": call.provider_alias,
                        "domainKey": call.domain_key,
                        "surfaceDigest": call.surface_digest,
                        "bindingContractDigest": call.binding_contract_digest,
                        "descriptorDigest": call.descriptor_digest,
                    },
                )

            # Pre-plan batch: verify every exposed definition before any dispatch.
            try:
                _preplan_verify_all(
                    ports=ports,
                    surface=surface,
                    calls=assistant.tool_calls,
                    scope=request.execution_scope,
                )
            except _ClassificationDrift as drift:
                sealed_messages, sealed_records = _seal_classification_drift(
                    messages=messages,
                    tool_call_records=tool_call_records,
                    calls=assistant.tool_calls,
                    first_stale_index=drift.first_stale_index,
                )
                raise ProviderLoopError(
                    stop_reason="capability_error",
                    error=SafeProviderError(
                        semantic_code="classification_changed",
                        safe_summary="capability classification changed before dispatch",
                        retry_disposition="never",
                    ),
                    messages=sealed_messages,
                    tool_calls=sealed_records,
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from drift

            sibling_outcome = _execute_sibling_calls(
                ports=ports,
                surface=surface,
                assistant=assistant,
                calls=assistant.tool_calls,
                start_index=0,
                messages=messages,
                tool_call_records=tool_call_records,
                current_manifest=current_manifest,
                scope=request.execution_scope,
                model_ref=request.model_ref,
                locale=request.locale,
                max_rounds=request.max_rounds,
                provider_rounds_used=round_count,
                prior_tool_call_count=prior_tool_call_count,
                accumulated_usage=accumulated_usage,
            )
            messages = list(sibling_outcome.messages)
            tool_call_records = list(sibling_outcome.tool_call_records)
            current_manifest = sibling_outcome.current_manifest

            if sibling_outcome.kind == "waiting":
                assert sibling_outcome.continuation is not None
                result = ProviderLoopResult(
                    status="waiting",
                    final_text=None,
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    round_count=round_count,
                    stop_reason="waiting_interrupt",
                    manifest=current_manifest,
                    continuation=sibling_outcome.continuation,
                    usage=accumulated_usage,
                    error=None,
                )
                _emit(
                    ports,
                    "loop.waiting",
                    {
                        "roundCount": round_count,
                        "waitingCallId": sibling_outcome.continuation.waiting_call.call_id,
                        "pendingCount": len(sibling_outcome.continuation.pending_call_ids),
                    },
                )
                return result

            if sibling_outcome.kind == "fatal":
                raise ProviderLoopError(
                    stop_reason=sibling_outcome.stop_reason or "capability_error",
                    error=sibling_outcome.error
                    or SafeProviderError(
                        semantic_code="capability_error",
                        safe_summary="sibling execution failed",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            if sibling_outcome.kind == "cancelled":
                raise ProviderLoopError(
                    status="cancelled",
                    stop_reason="cancelled",
                    error=SafeProviderError(
                        semantic_code="cancelled",
                        safe_summary="loop cancelled during tool dispatch",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            validate_provider_transcript(tuple(messages))
            # Continue to next Provider round with a rebuilt tools surface.
            continue

    except ProviderLoopError as terminal:
        result = _result_from_loop_error(terminal, fallback_manifest=request.manifest)
        if result.status == "cancelled":
            _emit(
                ports,
                "loop.cancelled",
                {
                    "roundCount": result.round_count,
                    "stopReason": result.stop_reason,
                },
            )
        else:
            _emit(
                ports,
                "loop.failed",
                {
                    "roundCount": result.round_count,
                    "stopReason": result.stop_reason,
                    "semanticCode": result.error.semantic_code if result.error else None,
                },
            )
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_finalization_surface(
    *,
    manifest: ResolvedRunManifestRevision,
    provider_protocol: str,
    scope: ProviderExecutionScope,
) -> ProviderToolSurface:
    """Canonical empty Tool surface tied to the current Manifest for finalization."""
    protocol = provider_protocol or OPENAI_CHAT_PROVIDER_PROTOCOL
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=protocol,
        visible=[],
        scope=scope,
    )
    return resolution.surface


class _ClassificationDrift(Exception):
    def __init__(self, first_stale_index: int) -> None:
        super().__init__("classification drift")
        self.first_stale_index = first_stale_index


class _FatalCapability(Exception):
    def __init__(
        self,
        *,
        tool_message: ProviderToolMessage,
        safe_code: str,
        safe_summary: str,
    ) -> None:
        super().__init__(safe_summary)
        self.tool_message = tool_message
        self.safe_code = safe_code
        self.safe_summary = safe_summary


class _WaitingCapability(Exception):
    def __init__(
        self,
        *,
        capability_result: CapabilityResult,
        next_manifest: ResolvedRunManifestRevision,
    ) -> None:
        super().__init__("waiting")
        self.capability_result = capability_result
        self.next_manifest = next_manifest


@dataclass(frozen=True)
class _SiblingOutcome:
    kind: Literal["continue", "waiting", "fatal", "cancelled"]
    messages: tuple[ProviderMessage, ...]
    tool_call_records: tuple[ProviderToolCallRecord, ...]
    current_manifest: ResolvedRunManifestRevision
    continuation: ProviderLoopContinuation | None = None
    stop_reason: str | None = None
    error: SafeProviderError | None = None


@dataclass(frozen=True)
class _CallWorkItem:
    call: ProviderToolCall
    definition: ProviderToolDefinition
    current_manifest: ResolvedRunManifestRevision


@dataclass(frozen=True)
class _CallWorkResult:
    call: ProviderToolCall
    kind: Literal["ok", "fatal", "waiting", "protocol"]
    tool_message: ProviderToolMessage | None
    next_manifest: ResolvedRunManifestRevision | None
    capability_status: str | None
    duration_ms: float | None
    safe_code: str | None = None
    safe_summary: str | None = None
    capability_result: CapabilityResult | None = None


def _validate_loop_identity(
    *,
    request: ProviderLoopRequest,
    provider: ProviderAdapter,
) -> None:
    scope = request.execution_scope
    expected_scope = compute_scope_digest(
        run_id=scope.run_id,
        conversation_id=scope.conversation_id,
        principal=scope.principal,
        tenant_scope_id=scope.tenant_scope_id,
    )
    if expected_scope != scope.scope_digest:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="scope_digest_mismatch",
                safe_summary="execution scope digest mismatch",
                retry_disposition="never",
            ),
        )
    if request.manifest.run_id != scope.run_id:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="run_scope_mismatch",
                safe_summary="manifest run_id does not match execution scope",
                retry_disposition="never",
            ),
        )
    if request.manifest.model is not None:
        if request.manifest.model.model_ref_digest != request.model_ref.model_ref_digest:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="model_ref_mismatch",
                    safe_summary="loop model_ref does not match manifest model",
                    retry_disposition="never",
                ),
            )
    _validate_adapter_identity(model_ref=request.model_ref, provider=provider, manifest=request.manifest)


def _validate_adapter_identity(
    *,
    model_ref: ModelRef,
    provider: ProviderAdapter,
    manifest: ResolvedRunManifestRevision,
) -> None:
    if model_ref.model_config_digest is not None:
        if provider.model_config_digest != model_ref.model_config_digest:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="adapter_config_mismatch",
                    safe_summary="provider adapter model_config_digest mismatch",
                    adapter_key=provider.adapter_key,
                    adapter_revision=provider.adapter_revision,
                    retry_disposition="never",
                ),
            )
    if manifest.provider is not None:
        if (
            manifest.provider.adapter_key is not None
            and provider.adapter_key != manifest.provider.adapter_key
        ):
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="adapter_key_mismatch",
                    safe_summary="provider adapter_key mismatch",
                    adapter_key=provider.adapter_key,
                    adapter_revision=provider.adapter_revision,
                    retry_disposition="never",
                ),
            )
        if (
            manifest.provider.adapter_revision is not None
            and provider.adapter_revision != manifest.provider.adapter_revision
        ):
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="adapter_revision_mismatch",
                    safe_summary="provider adapter_revision mismatch",
                    adapter_key=provider.adapter_key,
                    adapter_revision=provider.adapter_revision,
                    retry_disposition="never",
                ),
            )


def _require_tool_surface_resolution(value: Any) -> ToolSurfaceResolution:
    if not isinstance(value, ToolSurfaceResolution):
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="invalid_surface",
                safe_summary="tools provider returned an invalid surface resolution",
                retry_disposition="never",
            ),
        )
    return value


def _validate_alias_revision_lineage(
    *,
    previous: ResolvedRunManifestRevision,
    next_manifest: ResolvedRunManifestRevision,
) -> None:
    if next_manifest.manifest_digest == previous.manifest_digest:
        if next_manifest.revision != previous.revision:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="invalid_surface",
                    safe_summary="tools provider returned inconsistent manifest identity",
                    retry_disposition="never",
                ),
            )
        return
    if next_manifest.revision == previous.revision + 1:
        try:
            validate_manifest_child_link(parent=previous, child=next_manifest)
        except ValueError as exc:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="invalid_surface",
                    safe_summary="tools provider returned non-lineage alias revision",
                    retry_disposition="never",
                ),
            ) from exc
        return
    if next_manifest.revision < previous.revision:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="invalid_surface",
                safe_summary="tools provider returned ancestor manifest",
                retry_disposition="never",
            ),
        )
    raise ProviderLoopError(
        stop_reason="protocol_error",
        error=SafeProviderError(
            semantic_code="invalid_surface",
            safe_summary="tools provider returned non-contiguous manifest revision",
            retry_disposition="never",
        ),
    )


def _preplan_verify_all(
    *,
    ports: ProviderLoopPorts,
    surface: ProviderToolSurface,
    calls: tuple[ProviderToolCall, ...],
    scope: ProviderExecutionScope,
) -> None:
    for call in calls:
        definition = lookup_tool_by_alias(surface, call.provider_alias)
        try:
            ports.current_descriptors.require_current(
                binding=definition.binding,
                exposed_descriptor=definition.descriptor,
                scope=scope,
            )
        except Exception as exc:  # noqa: BLE001 - any mismatch is fatal drift
            raise _ClassificationDrift(first_stale_index=call.call_index) from exc


def _seal_classification_drift(
    *,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    calls: tuple[ProviderToolCall, ...],
    first_stale_index: int,
) -> tuple[tuple[ProviderMessage, ...], tuple[ProviderToolCallRecord, ...]]:
    """Dispatch none; first stale is blocked; every other unstarted sibling cancelled_before_start."""
    out_messages = list(messages)
    out_records = list(tool_call_records)
    for call in calls:
        if call.call_index == first_stale_index:
            error = CapabilityError(
                error_type="version_drift",
                safe_code="classification_changed",
                safe_message="capability classification changed before planning",
                retry_disposition="never",
                call_id=call.call_id,
            )
            msg = ProviderToolMessage(
                call_id=call.call_id,
                provider_alias=call.provider_alias,
                content=ProviderToolResultEnvelope(
                    status="blocked",
                    domain_key=call.domain_key,
                    user_text=None,
                    structured_output=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=error,
                ),
            )
            status = "blocked"
        else:
            error = CapabilityError(
                error_type="cancelled",
                safe_code="cancelled_before_start",
                safe_message="sibling cancelled before start due to classification drift",
                retry_disposition="never",
                call_id=call.call_id,
            )
            msg = ProviderToolMessage(
                call_id=call.call_id,
                provider_alias=call.provider_alias,
                content=ProviderToolResultEnvelope(
                    status="cancelled_before_start",
                    domain_key=call.domain_key,
                    user_text=None,
                    structured_output=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=error,
                ),
            )
            status = "cancelled_before_start"
        out_messages.append(msg)
        out_records.append(
            ProviderToolCallRecord(
                call=call,
                status=status,  # type: ignore[arg-type]
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=None,
            )
        )
    sealed = tuple(out_messages)
    validate_provider_transcript(sealed)
    return sealed, tuple(out_records)


def _dispatch_one(
    *,
    ports: ProviderLoopPorts,
    call: ProviderToolCall,
    definition: ProviderToolDefinition,
    current_manifest: ResolvedRunManifestRevision,
    scope: ProviderExecutionScope,
) -> tuple[ProviderToolMessage, ResolvedRunManifestRevision, str]:
    """Pre-dispatch verify, issue evidence, dispatch once. Returns message/manifest/status."""

    def _fatal_before_start(
        *,
        error: CapabilityError,
        safe_code: str,
        safe_summary: str,
        cause: BaseException | None = None,
    ) -> _FatalCapability:
        # Reservation already held by scheduler; release before pairing the deny.
        _release_unstarted_via_ports(
            ports=ports,
            call_id=call.call_id,
            reason_code=safe_code,
        )
        msg = ProviderToolMessage(
            call_id=call.call_id,
            provider_alias=call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=call.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        err = _FatalCapability(
            tool_message=msg,
            safe_code=safe_code,
            safe_summary=safe_summary,
        )
        if cause is not None:
            raise err from cause
        raise err

    # Pre-dispatch freshness (dispatcher-side equality is also required by fakes).
    try:
        ports.current_descriptors.require_current(
            binding=definition.binding,
            exposed_descriptor=definition.descriptor,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        _fatal_before_start(
            error=CapabilityError(
                error_type="version_drift",
                safe_code="classification_changed",
                safe_message="capability classification changed before dispatch",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="classification_changed",
            safe_summary="capability classification changed before dispatch",
            cause=exc,
        )

    try:
        authorization = ports.authorization_evidence.issue(
            call=call,
            binding=definition.binding,
            descriptor=definition.descriptor,
            scope=scope,
        )
    except AuthorizationEvidenceVerificationError as exc:
        # Pure policy deny / evidence reject: preserve allowlisted reason codes
        # (Plan 05 §11.3 blocked/<stable code>) instead of collapsing all denials
        # to authorization_evidence_failed.
        safe_code = _stable_auth_evidence_reason_code(getattr(exc, "reason_code", None))
        if safe_code == "authorization_evidence_failed":
            safe_summary = "authorization evidence factory failed"
        else:
            safe_summary = "capability authorization denied"
        _fatal_before_start(
            error=CapabilityError(
                error_type="unauthorized",
                safe_code=safe_code,
                safe_message=safe_summary,
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code=safe_code,
            safe_summary=safe_summary,
            cause=exc,
        )
    except Exception as exc:  # noqa: BLE001
        _fatal_before_start(
            error=CapabilityError(
                error_type="unauthorized",
                safe_code="authorization_evidence_failed",
                safe_message="authorization evidence factory failed",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="authorization_evidence_failed",
            safe_summary="authorization evidence factory failed",
            cause=exc,
        )

    # Reject evidence issued for the wrong scope/call when fields are present.
    if getattr(authorization, "call_id", None) not in {None, call.call_id}:
        _fatal_before_start(
            error=CapabilityError(
                error_type="unauthorized",
                safe_code="authorization_scope_mismatch",
                safe_message="authorization evidence call_id mismatch",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="authorization_scope_mismatch",
            safe_summary="authorization evidence call_id mismatch",
        )
    principal = getattr(authorization, "principal", None)
    if principal is not None and getattr(principal, "principal_id", None) != scope.principal.principal_id:
        _fatal_before_start(
            error=CapabilityError(
                error_type="unauthorized",
                safe_code="authorization_scope_mismatch",
                safe_message="authorization evidence principal mismatch",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="authorization_scope_mismatch",
            safe_summary="authorization evidence principal mismatch",
        )

    dispatch_request = ProviderDispatchRequest(
        call=call,
        binding=definition.binding,
        descriptor=definition.descriptor,
        current_manifest=current_manifest,
        execution_scope=scope,
        authorization=authorization,
    )

    try:
        dispatch_result = ports.tool_dispatcher.dispatch(
            dispatch_request,
            cancellation=ports.cancellation,
        )
    except Exception as exc:  # noqa: BLE001
        # Unexpected dispatcher crash: reservation may still be unstarted.
        _fatal_before_start(
            error=CapabilityError(
                error_type="protocol_error",
                safe_code="dispatcher_error",
                safe_message="tool dispatcher failed",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher failed",
            cause=exc,
        )

    if not isinstance(dispatch_result, ProviderDispatchResult):
        _fatal_before_start(
            error=CapabilityError(
                error_type="protocol_error",
                safe_code="dispatcher_error",
                safe_message="tool dispatcher returned invalid result",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher returned invalid result",
        )

    capability_result = dispatch_result.capability_result
    if not isinstance(capability_result, CapabilityResult):
        _fatal_before_start(
            error=CapabilityError(
                error_type="protocol_error",
                safe_code="dispatcher_error",
                safe_message="tool dispatcher returned invalid capability result",
                retry_disposition="never",
                call_id=call.call_id,
            ),
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher returned invalid capability result",
        )

    if capability_result.status == "waiting":
        # Waiting is only legal for durable interrupt descriptors; handled by caller.
        raise _WaitingCapability(
            capability_result=capability_result,
            next_manifest=dispatch_result.next_manifest,
        )

    # Fatal capability errors stop the loop after pairing; model_may_continue may continue.
    if capability_result.status == "failed" and capability_result.error is not None:
        error = capability_result.error
        if error.retry_disposition != "model_may_continue":
            envelope = project_tool_result_envelope(
                domain_key=call.domain_key,
                result=capability_result,
                status_override="blocked",
            )
            msg = ProviderToolMessage(
                call_id=call.call_id,
                provider_alias=call.provider_alias,
                content=envelope,
            )
            raise _FatalCapability(
                tool_message=msg,
                safe_code=error.safe_code,
                safe_summary=error.safe_message,
            )

    if capability_result.status == "completed":
        envelope = project_tool_result_envelope(
            domain_key=call.domain_key,
            result=capability_result,
        )
        status = "completed"
    elif capability_result.status == "failed":
        envelope = project_tool_result_envelope(
            domain_key=call.domain_key,
            result=capability_result,
        )
        status = "failed"
    elif capability_result.status == "cancelled":
        envelope = project_tool_result_envelope(
            domain_key=call.domain_key,
            result=capability_result,
        )
        status = "cancelled"
    else:  # pragma: no cover
        raise ValueError(f"unsupported capability status {capability_result.status!r}")

    msg = ProviderToolMessage(
        call_id=call.call_id,
        provider_alias=call.provider_alias,
        content=envelope,
    )
    return msg, dispatch_result.next_manifest, status


def _accept_next_manifest(
    *,
    previous: ResolvedRunManifestRevision,
    next_manifest: ResolvedRunManifestRevision,
    exposed_manifest_digest: str,
    exposed_manifest_revision: int,
) -> ResolvedRunManifestRevision:
    """Accept same or append-only descendant of the dispatch current/exposed parent."""
    if not isinstance(next_manifest, ResolvedRunManifestRevision):
        raise ValueError("next_manifest must be a ResolvedRunManifestRevision")
    if next_manifest.run_id != previous.run_id:
        raise ValueError("next_manifest run_id mismatch")
    if previous.model is not None and next_manifest.model is not None:
        if previous.model.model_ref_digest != next_manifest.model.model_ref_digest:
            raise ValueError("next_manifest model_ref changed")
    if previous.provider is not None and next_manifest.provider is not None:
        if previous.provider.provider_ref_digest != next_manifest.provider.provider_ref_digest:
            raise ValueError("next_manifest provider_ref changed")

    if next_manifest.manifest_digest == previous.manifest_digest:
        if next_manifest.revision != previous.revision:
            raise ValueError("next_manifest identity inconsistent")
        return previous

    if next_manifest.revision < previous.revision:
        raise ValueError("next_manifest is an ancestor of current_manifest")

    # Must be a direct child of previous for sequential Task 3 (no skipped parents).
    if next_manifest.revision != previous.revision + 1:
        raise ValueError("next_manifest skipped parent revisions")
    validate_manifest_child_link(parent=previous, child=next_manifest)

    # Existing aliases/bindings must not be rewritten.
    prev_aliases = {
        (item.provider_protocol, item.domain_key): item
        for item in previous.provider_aliases
    }
    for item in next_manifest.provider_aliases:
        key = (item.provider_protocol, item.domain_key)
        prior = prev_aliases.get(key)
        if prior is not None:
            if (
                prior.provider_alias != item.provider_alias
                or prior.binding_contract_digest != item.binding_contract_digest
            ):
                raise ValueError("next_manifest rewrote an existing provider alias")

    prev_caps = {
        (item.capability_type, item.capability_key): item
        for item in previous.capabilities
    }
    for item in next_manifest.capabilities:
        key = (item.capability_type, item.capability_key)
        prior = prev_caps.get(key)
        if prior is not None and prior.model_dump() != item.model_dump():
            raise ValueError("next_manifest rewrote an existing capability binding")

    # Exposed identity is informational for Task 3 sequential path: current must
    # remain equal to exposed or a validated descendant. previous is already that.
    del exposed_manifest_digest, exposed_manifest_revision
    return next_manifest


def _lifecycle_accept(
    ports: ProviderLoopPorts,
    *,
    call_id: str,
    current_manifest: ResolvedRunManifestRevision,
    proposed_manifest: ResolvedRunManifestRevision,
) -> None:
    """Invoke Manifest-effect lifecycle after lineage acceptance (Plan 04)."""
    lifecycle = getattr(ports, "manifest_effect_lifecycle", None)
    if lifecycle is None:
        return
    lifecycle.accept(
        call_id=call_id,
        current_manifest=current_manifest,
        proposed_manifest=proposed_manifest,
    )


def _lifecycle_discard(
    ports: ProviderLoopPorts,
    *,
    call_id: str,
    reason_code: str,
) -> None:
    lifecycle = getattr(ports, "manifest_effect_lifecycle", None)
    if lifecycle is None:
        return
    lifecycle.discard(call_id=call_id, reason_code=reason_code)


def _chunk_final_text(text: str, *, size: int = 32) -> list[str]:
    if size < 1:
        raise ValueError("size must be >= 1")
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


def _append_round_context(
    *,
    ports: ProviderLoopPorts,
    messages: list[ProviderMessage],
    current_manifest: ResolvedRunManifestRevision,
    already_applied_skill_version_ids: tuple[UUID, ...],
    execution_scope: ProviderExecutionScope,
    locale: str,
    tool_call_records: list[ProviderToolCallRecord],
    accumulated_usage: ProviderUsage,
    round_count: int,
) -> tuple[UUID, ...]:
    """Resolve protected context after Tool surface; append not-yet-applied messages.

    Ordering lock: tools surface (and alias child) first, then RoundContextProvider
    with that exact round Manifest. Returns the updated applied skill version IDs.
    """
    try:
        context_resolution = ports.round_context_provider.resolve(
            manifest=current_manifest,
            already_applied_skill_version_ids=already_applied_skill_version_ids,
            execution_scope=execution_scope,
            locale=locale,
        )
    except ProviderLoopError:
        raise
    except Exception as exc:  # noqa: BLE001 - map to safe protocol error
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="round_context_provider_failed",
                safe_summary="round context provider failed before provider round",
                retry_disposition="never",
            ),
            messages=tuple(messages),
            tool_calls=tuple(tool_call_records),
            manifest=current_manifest,
            usage=accumulated_usage,
            round_count=round_count,
        ) from exc

    if not isinstance(context_resolution, RoundContextResolution):
        # Tolerate duck-typed resolutions that re-validate as RoundContextResolution.
        try:
            context_resolution = RoundContextResolution.model_validate(
                context_resolution.model_dump()
                if hasattr(context_resolution, "model_dump")
                else context_resolution
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="round_context_invalid",
                    safe_summary="round context resolution is invalid",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            ) from exc

    if context_resolution.manifest_revision != current_manifest.revision:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="round_context_manifest_mismatch",
                safe_summary="round context revision does not match round manifest",
                retry_disposition="never",
            ),
            messages=tuple(messages),
            tool_calls=tuple(tool_call_records),
            manifest=current_manifest,
            usage=accumulated_usage,
            round_count=round_count,
        )
    if context_resolution.manifest_digest != current_manifest.manifest_digest:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="round_context_manifest_mismatch",
                safe_summary="round context digest does not match round manifest",
                retry_disposition="never",
            ),
            messages=tuple(messages),
            tool_calls=tuple(tool_call_records),
            manifest=current_manifest,
            usage=accumulated_usage,
            round_count=round_count,
        )

    applied_set = set(already_applied_skill_version_ids)
    new_applied: list[UUID] = []
    for skill_version_id in context_resolution.applied_skill_version_ids:
        if skill_version_id not in applied_set:
            new_applied.append(skill_version_id)
            applied_set.add(skill_version_id)

    for message in context_resolution.messages:
        if not isinstance(message, ProviderContextUpdateMessage):
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="round_context_invalid",
                    safe_summary="round context message type is invalid",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        if message.manifest_revision != current_manifest.revision:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="round_context_manifest_mismatch",
                    safe_summary="context message revision does not match round manifest",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        if message.manifest_digest != current_manifest.manifest_digest:
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="round_context_manifest_mismatch",
                    safe_summary="context message digest does not match round manifest",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        messages.append(message)

    if not new_applied and not context_resolution.applied_skill_version_ids:
        return already_applied_skill_version_ids
    if not new_applied:
        # Provider reported already-applied IDs only; keep prior set.
        return already_applied_skill_version_ids
    return already_applied_skill_version_ids + tuple(new_applied)


def _emit(ports: ProviderLoopPorts, event_type: str, payload: dict[str, Any]) -> None:
    try:
        ports.events.emit(event_type, payload)  # type: ignore[arg-type]
    except Exception:
        # Event sink failures must never duplicate Provider/Capability work.
        return


def _resolve_round_budget_guard(ports: ProviderLoopPorts) -> ProviderRoundBudgetGuard | None:
    return getattr(ports, "round_budget_guard", None)


def _apply_round_budget_before(
    *,
    ports: ProviderLoopPorts,
    round_request: ProviderRoundRequest,
) -> ProviderGenerationOptions:
    """Invoke optional round budget guard immediately before Provider I/O.

    Raises ProviderLoopError on denial. Default no-op returns request.generation.
    Does not reset Plan 03 round_count; only caps generation options.
    """
    guard = _resolve_round_budget_guard(ports)
    if guard is None:
        return round_request.generation
    try:
        generation = guard.before_round(round_request)
    except ProviderLoopError:
        raise
    except ProviderRoundBudgetDeniedError as exc:
        raise ProviderLoopError(
            stop_reason="max_rounds_hard_stop"
            if round_request.finalization_round
            else "protocol_error",
            error=SafeProviderError(
                semantic_code=exc.reason_code,
                safe_summary="provider round budget denied before I/O",
                retry_disposition="never",
            ),
            round_count=round_request.round_index + 1,
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="budget_guard_error",
                safe_summary="provider round budget guard failed before I/O",
                retry_disposition="never",
            ),
            round_count=round_request.round_index + 1,
        ) from exc
    if not isinstance(generation, ProviderGenerationOptions):
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="budget_guard_error",
                safe_summary="provider round budget guard returned invalid generation options",
                retry_disposition="never",
            ),
            round_count=round_request.round_index + 1,
        )
    return generation


def _apply_round_budget_after(
    *,
    ports: ProviderLoopPorts,
    round_result: ProviderRoundResult,
) -> None:
    """Report Provider usage after round assembly. Failures are contained."""
    guard = _resolve_round_budget_guard(ports)
    if guard is None:
        return
    try:
        guard.after_round(round_result)
    except Exception:
        # Usage recording must never undo a completed Provider round.
        return


def _evaluate_completion_guard(
    *,
    ports: ProviderLoopPorts,
    current_manifest: ResolvedRunManifestRevision,
    candidate_text: str | None,
    finalization_round: bool,
) -> ProviderCompletionDisposition:
    """Invoke the provider-neutral completion guard on a no-Tool round.

    Default no-op reproduces Plan 03 natural-completion behavior.
    """
    guard = getattr(ports, "completion_guard", None)
    if guard is None:
        from app.assistant.provider_loop.contracts import NoOpProviderCompletionGuard

        guard = NoOpProviderCompletionGuard()
    request = ProviderCompletionRequest(
        manifest_revision=current_manifest.revision,
        manifest_digest=current_manifest.manifest_digest,
        candidate_text=candidate_text,
        finalization_round=finalization_round,
    )
    disposition = guard.evaluate(request)
    if not isinstance(disposition, ProviderCompletionDisposition):
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="completion_guard_error",
                safe_summary="completion guard returned invalid disposition",
                retry_disposition="never",
            ),
        )
    return disposition


def _handle_no_tool_completion(
    *,
    ports: ProviderLoopPorts,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    accumulated_usage: ProviderUsage,
    round_count: int,
    round_index: int,
    finalization: bool,
    final_text: str | None,
) -> ProviderLoopResult | None:
    """Apply completion guard on a no-Tool round.

    Returns:
    - ProviderLoopResult on complete
    - None when action is continue (caller appends instruction and loops)
    Raises ProviderLoopError on fail / invalid wait / empty response.
    """
    disposition = _evaluate_completion_guard(
        ports=ports,
        current_manifest=current_manifest,
        candidate_text=final_text,
        finalization_round=finalization,
    )

    if disposition.action == "complete":
        text = final_text if final_text is not None else ""
        if not str(text).strip():
            # Guard said complete but text empty — treat as empty response.
            stop = "max_rounds_hard_stop" if finalization else "protocol_error"
            raise ProviderLoopError(
                stop_reason=stop,
                error=SafeProviderError(
                    semantic_code="empty_response" if not finalization else stop,
                    safe_summary="provider returned empty assistant content",
                    adapter_key=ports.provider.adapter_key,
                    adapter_revision=ports.provider.adapter_revision,
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        for index, chunk in enumerate(_chunk_final_text(text)):
            _emit(
                ports,
                "final_text.delta",
                {
                    "roundIndex": round_index,
                    "sequence": index,
                    "delta": chunk,
                },
            )
        validate_provider_transcript(tuple(messages))
        stop_reason = (
            "max_rounds_soft_finalized" if finalization else "natural_completion"
        )
        result = ProviderLoopResult(
            status="completed",
            final_text=text,
            messages=tuple(messages),
            tool_calls=tuple(tool_call_records),
            round_count=round_count,
            stop_reason=stop_reason,
            manifest=current_manifest,
            continuation=None,
            usage=accumulated_usage,
            error=None,
        )
        _emit(
            ports,
            "loop.completed",
            {
                "roundCount": round_count,
                "stopReason": stop_reason,
                "toolCallCount": len(tool_call_records),
            },
        )
        return result

    if disposition.action == "continue":
        instruction = disposition.instruction
        if instruction is None or not isinstance(
            instruction, ProviderCompletionInstructionMessage
        ):
            raise ProviderLoopError(
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="completion_guard_error",
                    safe_summary="continue disposition requires completion instruction",
                    adapter_key=ports.provider.adapter_key,
                    adapter_revision=ports.provider.adapter_revision,
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        messages.append(instruction)
        _emit(
            ports,
            "completion.continue",
            {
                "roundIndex": round_index,
                "reasonCode": disposition.reason_code,
                "decisionDigest": disposition.decision_digest,
            },
        )
        return None

    if disposition.action == "wait":
        # wait requires pending approval/input + exact continuation; Plan 05
        # production descriptors cannot produce it. Without a continuation
        # constructed by the caller, this is a protocol error.
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="waiting_without_obligation"
                if disposition.reason_code == "waiting_without_obligation"
                else disposition.reason_code,
                safe_summary=(
                    "completion wait requires pending approval/input obligation "
                    "and an exact continuation"
                ),
                adapter_key=ports.provider.adapter_key,
                adapter_revision=ports.provider.adapter_revision,
                retry_disposition="never",
            ),
            messages=tuple(messages),
            tool_calls=tuple(tool_call_records),
            manifest=current_manifest,
            usage=accumulated_usage,
            round_count=round_count,
        )

    # fail
    reason = disposition.reason_code or "completion_guard_failed"
    # Preserve Plan 03 empty_response semantic for default-guard empty text.
    if reason == "terminal_text_missing" and (
        final_text is None or not str(final_text).strip()
    ):
        stop = "max_rounds_hard_stop" if finalization else "protocol_error"
        semantic = "empty_response" if not finalization else stop
        summary = "provider returned empty assistant content"
    else:
        stop = "max_rounds_hard_stop" if finalization else "protocol_error"
        # Prefer the guard reason as semantic_code for obligation failures.
        semantic = reason
        summary = f"completion guard denied natural completion: {reason}"
    raise ProviderLoopError(
        stop_reason=stop,
        error=SafeProviderError(
            semantic_code=semantic,
            safe_summary=summary,
            adapter_key=ports.provider.adapter_key,
            adapter_revision=ports.provider.adapter_revision,
            retry_disposition="never",
        ),
        messages=tuple(messages),
        tool_calls=tuple(tool_call_records),
        manifest=current_manifest,
        usage=accumulated_usage,
        round_count=round_count,
    )


def _depths_from_frame_port(
    ports: ProviderLoopPorts,
    *,
    capability_type: str,
) -> tuple[int, int]:
    """Capability/agent depths from optional process-local frame stack.

    Mirrors policy.recursion.compute_next_depths without importing policy
    (provider_loop must stay free of policy ledger/frame types).
    """
    port = getattr(ports, "call_frames", None)
    stack: tuple[Any, ...] = ()
    if port is not None:
        try:
            current = port.current()
            if isinstance(current, tuple):
                stack = current
            elif current is not None:
                stack = tuple(current)
        except Exception:
            stack = ()
    capability_depth = len(stack) + 1
    agent_frames = sum(
        1 for frame in stack if getattr(frame, "capability_type", None) == "agent"
    )
    if capability_type == "agent":
        agent_depth = agent_frames + 1
    else:
        agent_depth = max(1, agent_frames)
    return capability_depth, agent_depth


def _build_reservation_item(
    *,
    ports: ProviderLoopPorts,
    call: ProviderToolCall,
    descriptor: Any,
) -> CapabilityCallReservationItem:
    owner_kind, owner_version_id = ports.call_owner_resolver.resolve_owner(
        call=call,
        descriptor=descriptor,
    )
    side_effect = getattr(getattr(descriptor, "behavior", None), "side_effect", "read")
    capability_type = str(getattr(descriptor, "capability_type", None) or "tool")
    capability_depth, agent_depth = _depths_from_frame_port(
        ports, capability_type=capability_type
    )
    return CapabilityCallReservationItem(
        call_id=call.call_id,
        owner_kind=str(owner_kind),
        owner_version_id=owner_version_id,
        domain_key=call.domain_key,
        side_effect=str(side_effect),
        arguments_digest=call.arguments_digest,
        binding_contract_digest=call.binding_contract_digest,
        capability_depth=capability_depth,
        agent_depth=agent_depth,
    )


def _blocked_budget_message(
    call: ProviderToolCall,
    *,
    reason_code: str,
    safe_message: str = "capability call budget denied",
) -> ProviderToolMessage:
    # Map budget deny to blocked envelope with stable budget reason as safe_code.
    # CapabilityError has no budget_exhausted type; use unauthorized + budget code.
    code = reason_code if reason_code and len(reason_code) <= 64 else "budget_exhausted"
    # safe_code cannot contain whitespace; reason codes from ledger are already clean.
    error = CapabilityError(
        error_type="unauthorized",
        safe_code=code.replace(" ", "_")[:64],
        safe_message=safe_message,
        retry_disposition="never",
        call_id=call.call_id,
    )
    return ProviderToolMessage(
        call_id=call.call_id,
        provider_alias=call.provider_alias,
        content=ProviderToolResultEnvelope(
            status="blocked",
            domain_key=call.domain_key,
            user_text=None,
            structured_output=None,
            terminal_output=False,
            needs_followup=False,
            error=error,
        ),
    )


def _reserve_one_call(
    *,
    ports: ProviderLoopPorts,
    call: ProviderToolCall,
    definition: ProviderToolDefinition,
) -> CapabilityCallReservationDecision:
    item = _build_reservation_item(
        ports=ports,
        call=call,
        descriptor=definition.descriptor,
    )
    try:
        return ports.call_reservation.reserve_one(item)
    except Exception:
        return CapabilityCallReservationDecision(
            allowed=False,
            reason_code="budget_reservation_error",
            reserved_call_ids=(),
        )


def _reserve_batch_calls(
    *,
    ports: ProviderLoopPorts,
    group_calls: tuple[ProviderToolCall, ...],
    surface: ProviderToolSurface,
) -> CapabilityCallReservationDecision:
    items: list[CapabilityCallReservationItem] = []
    for call in group_calls:
        definition = lookup_tool_by_alias(surface, call.provider_alias)
        items.append(
            _build_reservation_item(
                ports=ports,
                call=call,
                descriptor=definition.descriptor,
            )
        )
    try:
        return ports.call_reservation.reserve_batch(items)
    except Exception:
        return CapabilityCallReservationDecision(
            allowed=False,
            reason_code="budget_reservation_error",
            reserved_call_ids=(),
        )


def _release_unstarted_via_ports(
    *,
    ports: ProviderLoopPorts,
    call_id: str,
    reason_code: str,
) -> None:
    """Best-effort release of a reserved-but-unstarted call via shared dispatch guard."""
    guard = getattr(ports, "dispatch_guard", None)
    if guard is None:
        # GatewayToolDispatcher may hold the guard when ports.dispatch_guard is unset.
        dispatcher = getattr(ports, "tool_dispatcher", None)
        guard = getattr(dispatcher, "dispatch_guard", None) if dispatcher is not None else None
    if guard is None:
        return
    try:
        guard.release_unstarted(call_id=call_id, reason_code=reason_code)
    except Exception:
        return


def _result_from_loop_error(
    terminal: ProviderLoopError,
    *,
    fallback_manifest: ResolvedRunManifestRevision,
) -> ProviderLoopResult:
    messages = terminal.messages
    if messages:
        validate_provider_transcript(messages)
    status = terminal.status  # type: ignore[assignment]
    if status == "cancelled":
        return ProviderLoopResult(
            status="cancelled",
            final_text=terminal.final_text,
            messages=messages,
            tool_calls=terminal.tool_calls,
            round_count=terminal.round_count,
            stop_reason="cancelled",
            manifest=terminal.manifest or fallback_manifest,
            continuation=None,
            usage=terminal.usage,
            error=None,
        )
    return ProviderLoopResult(
        status="failed",
        final_text=terminal.final_text,
        messages=messages,
        tool_calls=terminal.tool_calls,
        round_count=terminal.round_count,
        stop_reason=terminal.stop_reason,  # type: ignore[arg-type]
        manifest=terminal.manifest or fallback_manifest,
        continuation=None,
        usage=terminal.usage,
        error=terminal.error,
    )


def _cancelled_before_start_message(call: ProviderToolCall, *, safe_message: str) -> ProviderToolMessage:
    return ProviderToolMessage(
        call_id=call.call_id,
        provider_alias=call.provider_alias,
        content=ProviderToolResultEnvelope(
            status="cancelled_before_start",
            domain_key=call.domain_key,
            user_text=None,
            structured_output=None,
            terminal_output=False,
            needs_followup=False,
            error=CapabilityError(
                error_type="cancelled",
                safe_code="cancelled_before_start",
                safe_message=safe_message,
                retry_disposition="never",
                call_id=call.call_id,
            ),
        ),
    )


def _append_cancelled_before_start(
    *,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    calls: SequenceLike,
    safe_message: str,
) -> None:
    for sibling in calls:
        cancel_msg = _cancelled_before_start_message(sibling, safe_message=safe_message)
        messages.append(cancel_msg)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=sibling,
                status="cancelled_before_start",
                result_message_digest=digest_provider_message(cancel_msg),
                safe_duration_ms=None,
            )
        )


# Type alias for readability in helpers.
SequenceLike = tuple[ProviderToolCall, ...] | list[ProviderToolCall]


def _dispatcher_capabilities(ports: ProviderLoopPorts) -> DispatcherCapabilities:
    executor = ports.sibling_executor
    supports = isinstance(executor, BoundedIsolatedSiblingExecutor)
    max_workers = DEFAULT_MAX_WORKERS
    if supports:
        max_workers = max(1, int(getattr(executor, "max_workers", DEFAULT_MAX_WORKERS)))
    # Allow duck-typed executors that advertise isolation support.
    if hasattr(executor, "supports_isolated_parallel"):
        supports = bool(getattr(executor, "supports_isolated_parallel"))
    if hasattr(executor, "max_workers") and not supports:
        # Sequential path may still expose max_workers; keep sequential.
        pass
    return DispatcherCapabilities(
        supports_isolated_parallel=supports,
        max_workers=max_workers,
    )


def _run_dispatch_item(
    *,
    ports: ProviderLoopPorts,
    item: _CallWorkItem,
    scope: ProviderExecutionScope,
) -> _CallWorkResult:
    call = item.call
    started = time.perf_counter()
    _emit(
        ports,
        "tool_call.started",
        {
            "callId": call.call_id,
            "callIndex": call.call_index,
            "domainKey": call.domain_key,
            "providerAlias": call.provider_alias,
        },
    )
    try:
        tool_message, next_manifest, capability_status = _dispatch_one(
            ports=ports,
            call=call,
            definition=item.definition,
            current_manifest=item.current_manifest,
            scope=scope,
        )
    except _WaitingCapability as waiting:
        duration_ms = (time.perf_counter() - started) * 1000.0
        return _CallWorkResult(
            call=call,
            kind="waiting",
            tool_message=None,
            next_manifest=waiting.next_manifest,
            capability_status="waiting",
            duration_ms=duration_ms,
            capability_result=waiting.capability_result,
        )
    except _FatalCapability as fatal:
        duration_ms = (time.perf_counter() - started) * 1000.0
        return _CallWorkResult(
            call=call,
            kind="fatal",
            tool_message=fatal.tool_message,
            next_manifest=None,
            capability_status="blocked",
            duration_ms=duration_ms,
            safe_code=fatal.safe_code,
            safe_summary=fatal.safe_summary,
        )
    except Exception as exc:  # noqa: BLE001 - executor infrastructure failure
        duration_ms = (time.perf_counter() - started) * 1000.0
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="executor_error",
            safe_message="sibling executor infrastructure failed",
            retry_disposition="never",
            call_id=call.call_id,
        )
        msg = ProviderToolMessage(
            call_id=call.call_id,
            provider_alias=call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=call.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        return _CallWorkResult(
            call=call,
            kind="protocol",
            tool_message=msg,
            next_manifest=None,
            capability_status="blocked",
            duration_ms=duration_ms,
            safe_code="executor_error",
            safe_summary="sibling executor infrastructure failed",
        )

    duration_ms = (time.perf_counter() - started) * 1000.0
    return _CallWorkResult(
        call=call,
        kind="ok",
        tool_message=tool_message,
        next_manifest=next_manifest,
        capability_status=capability_status,
        duration_ms=duration_ms,
    )


def _build_waiting_continuation(
    *,
    scope: ProviderExecutionScope,
    model_ref: ModelRef,
    locale: str,
    max_rounds: int,
    provider_rounds_used: int,
    prior_tool_call_count: int,
    accumulated_usage: ProviderUsage,
    current_manifest: ResolvedRunManifestRevision,
    exposed_surface: ProviderToolSurface,
    assistant: ProviderAssistantMessage,
    messages: tuple[ProviderMessage, ...],
    waiting_call: ProviderToolCall,
    pending_calls: tuple[ProviderToolCall, ...],
    completed_call_records: tuple[ProviderToolCallRecord, ...],
    capability_continuation: ContinuationRef,
) -> ProviderLoopContinuation:
    return ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=model_ref,
        locale=locale,
        max_rounds=max_rounds,
        provider_rounds_used=provider_rounds_used,
        prior_tool_call_count=prior_tool_call_count,
        accumulated_usage=accumulated_usage,
        current_manifest_revision=current_manifest.revision,
        current_manifest_digest=current_manifest.manifest_digest,
        exposed_surface=exposed_surface,
        assistant_message_digest=digest_provider_message(assistant),
        transcript_digest=digest_provider_transcript(messages),
        waiting_call=ProviderWaitingCallState(
            call_id=waiting_call.call_id,
            call_index=waiting_call.call_index,
            binding_contract_digest=waiting_call.binding_contract_digest,
            descriptor_digest=waiting_call.descriptor_digest,
            behavior_digest=waiting_call.behavior_digest,
            classification_revision=waiting_call.classification_revision,
            classification_ruleset_digest=waiting_call.classification_ruleset_digest,
            capability_continuation=capability_continuation,
        ),
        next_call_index=waiting_call.call_index + 1,
        pending_call_ids=tuple(call.call_id for call in pending_calls),
        completed_call_records=completed_call_records,
    )


def _execute_sibling_calls(
    *,
    ports: ProviderLoopPorts,
    surface: ProviderToolSurface,
    assistant: ProviderAssistantMessage,
    calls: tuple[ProviderToolCall, ...],
    start_index: int,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    scope: ProviderExecutionScope,
    model_ref: ModelRef,
    locale: str,
    max_rounds: int,
    provider_rounds_used: int,
    prior_tool_call_count: int,
    accumulated_usage: ProviderUsage,
) -> _SiblingOutcome:
    """Execute remaining sibling calls from start_index in Provider order groups."""
    remaining = tuple(call for call in calls if call.call_index >= start_index)
    if not remaining:
        return _SiblingOutcome(
            kind="continue",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
        )

    capabilities = _dispatcher_capabilities(ports)
    # Reindex planner inputs contiguously while preserving real call_index values
    # by planning only remaining calls as their own zero-based slice after remap.
    # plan_sibling_execution requires contiguous 0..n indexes, so we plan using
    # a temporary reindexed view then map back.
    reindexed = tuple(
        call.model_copy(update={"call_index": offset})
        for offset, call in enumerate(remaining)
    )
    planned = plan_sibling_execution(
        reindexed,
        surface=surface,
        dispatcher_capabilities=capabilities,
    )
    # Map planned groups back to original calls via offset.
    groups: list[tuple[str, tuple[ProviderToolCall, ...]]] = []
    for group in planned:
        original = tuple(remaining[index] for index in group.call_indexes)
        groups.append((group.mode, original))

    for mode, group_calls in groups:
        if ports.cancellation.is_cancelled():
            recorded_ids = {record.call.call_id for record in tool_call_records}
            still = tuple(call for call in remaining if call.call_id not in recorded_ids)
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=still,
                safe_message="sibling cancelled before start",
            )
            validate_provider_transcript(tuple(messages))
            return _SiblingOutcome(
                kind="cancelled",
                messages=tuple(messages),
                tool_call_records=tuple(tool_call_records),
                current_manifest=current_manifest,
            )

        if mode == "sequential":
            outcome = _execute_sequential_group(
                ports=ports,
                surface=surface,
                assistant=assistant,
                all_calls=calls,
                group_calls=group_calls,
                messages=messages,
                tool_call_records=tool_call_records,
                current_manifest=current_manifest,
                scope=scope,
                model_ref=model_ref,
                locale=locale,
                max_rounds=max_rounds,
                provider_rounds_used=provider_rounds_used,
                prior_tool_call_count=prior_tool_call_count,
                accumulated_usage=accumulated_usage,
            )
            messages = list(outcome.messages)
            tool_call_records = list(outcome.tool_call_records)
            current_manifest = outcome.current_manifest
            if outcome.kind != "continue":
                return outcome
            continue

        outcome = _execute_parallel_group(
            ports=ports,
            surface=surface,
            assistant=assistant,
            all_calls=calls,
            group_calls=group_calls,
            messages=messages,
            tool_call_records=tool_call_records,
            current_manifest=current_manifest,
            scope=scope,
            model_ref=model_ref,
            locale=locale,
            max_rounds=max_rounds,
            provider_rounds_used=provider_rounds_used,
            prior_tool_call_count=prior_tool_call_count,
            accumulated_usage=accumulated_usage,
            max_workers=capabilities.max_workers,
        )
        messages = list(outcome.messages)
        tool_call_records = list(outcome.tool_call_records)
        current_manifest = outcome.current_manifest
        if outcome.kind != "continue":
            return outcome

    return _SiblingOutcome(
        kind="continue",
        messages=tuple(messages),
        tool_call_records=tuple(tool_call_records),
        current_manifest=current_manifest,
    )


def _execute_sequential_group(
    *,
    ports: ProviderLoopPorts,
    surface: ProviderToolSurface,
    assistant: ProviderAssistantMessage,
    all_calls: tuple[ProviderToolCall, ...],
    group_calls: tuple[ProviderToolCall, ...],
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    scope: ProviderExecutionScope,
    model_ref: ModelRef,
    locale: str,
    max_rounds: int,
    provider_rounds_used: int,
    prior_tool_call_count: int,
    accumulated_usage: ProviderUsage,
) -> _SiblingOutcome:
    for call in group_calls:
        if ports.cancellation.is_cancelled():
            still = tuple(
                item
                for item in all_calls
                if item.call_index >= call.call_index
                and item.call_id not in {record.call.call_id for record in tool_call_records}
            )
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=still,
                safe_message="sibling cancelled before start",
            )
            validate_provider_transcript(tuple(messages))
            return _SiblingOutcome(
                kind="cancelled",
                messages=tuple(messages),
                tool_call_records=tuple(tool_call_records),
                current_manifest=current_manifest,
            )

        definition = lookup_tool_by_alias(surface, call.provider_alias)

        # Plan 05: sequential reserve_one before evidence/dispatch.
        reservation = _reserve_one_call(
            ports=ports, call=call, definition=definition
        )
        if not reservation.allowed:
            blocked_msg = _blocked_budget_message(
                call,
                reason_code=reservation.reason_code,
                safe_message="capability call budget denied before start",
            )
            messages.append(blocked_msg)
            tool_call_records.append(
                ProviderToolCallRecord(
                    call=call,
                    status="blocked",
                    result_message_digest=digest_provider_message(blocked_msg),
                    safe_duration_ms=None,
                )
            )
            remaining = tuple(item for item in all_calls if item.call_index > call.call_index)
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=remaining,
                safe_message="sibling cancelled before start after budget denial",
            )
            validate_provider_transcript(tuple(messages))
            return _SiblingOutcome(
                kind="fatal",
                messages=tuple(messages),
                tool_call_records=tuple(tool_call_records),
                current_manifest=current_manifest,
                stop_reason="capability_error",
                error=SafeProviderError(
                    semantic_code=reservation.reason_code,
                    safe_summary="capability call budget denied before start",
                    retry_disposition="never",
                ),
            )

        work = _run_dispatch_item(
            ports=ports,
            item=_CallWorkItem(
                call=call,
                definition=definition,
                current_manifest=current_manifest,
            ),
            scope=scope,
        )

        if work.kind == "waiting":
            return _handle_waiting_result(
                ports=ports,
                surface=surface,
                assistant=assistant,
                all_calls=all_calls,
                waiting_call=call,
                work=work,
                definition=definition,
                messages=messages,
                tool_call_records=tool_call_records,
                current_manifest=current_manifest,
                scope=scope,
                model_ref=model_ref,
                locale=locale,
                max_rounds=max_rounds,
                provider_rounds_used=provider_rounds_used,
                prior_tool_call_count=prior_tool_call_count,
                accumulated_usage=accumulated_usage,
            )

        if work.kind in {"fatal", "protocol"}:
            assert work.tool_message is not None
            messages.append(work.tool_message)
            tool_call_records.append(
                ProviderToolCallRecord(
                    call=call,
                    status="blocked",
                    result_message_digest=digest_provider_message(work.tool_message),
                    safe_duration_ms=work.duration_ms,
                )
            )
            remaining = tuple(item for item in all_calls if item.call_index > call.call_index)
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=remaining,
                safe_message="sibling cancelled before start after fatal call",
            )
            validate_provider_transcript(tuple(messages))
            stop = "protocol_error" if work.kind == "protocol" else "capability_error"
            return _SiblingOutcome(
                kind="fatal",
                messages=tuple(messages),
                tool_call_records=tuple(tool_call_records),
                current_manifest=current_manifest,
                stop_reason=stop,
                error=SafeProviderError(
                    semantic_code=work.safe_code or "capability_error",
                    safe_summary=work.safe_summary or "capability error",
                    retry_disposition="never",
                ),
            )

        assert work.tool_message is not None
        assert work.next_manifest is not None
        assert work.capability_status is not None
        messages.append(work.tool_message)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=call,
                status=work.capability_status,  # type: ignore[arg-type]
                result_message_digest=digest_provider_message(work.tool_message),
                safe_duration_ms=work.duration_ms,
            )
        )
        try:
            parent_before = current_manifest
            # Validate lineage without committing loop state yet so a lifecycle
            # failure cannot leave the loop advanced while the package is discarded.
            accepted_child = _accept_next_manifest(
                previous=parent_before,
                next_manifest=work.next_manifest,
                exposed_manifest_digest=call.manifest_digest,
                exposed_manifest_revision=call.manifest_revision,
            )
            if work.next_manifest.manifest_digest == parent_before.manifest_digest:
                _lifecycle_discard(
                    ports,
                    call_id=call.call_id,
                    reason_code="manifest_unchanged",
                )
                current_manifest = accepted_child
            else:
                try:
                    _lifecycle_accept(
                        ports,
                        call_id=call.call_id,
                        current_manifest=parent_before,
                        proposed_manifest=work.next_manifest,
                    )
                except Exception as life_exc:
                    # Keep loop Manifest at parent; package discarded.
                    _lifecycle_discard(
                        ports,
                        call_id=call.call_id,
                        reason_code="lifecycle_accept_error",
                    )
                    remaining = tuple(
                        item for item in all_calls if item.call_index > call.call_index
                    )
                    _append_cancelled_before_start(
                        messages=messages,
                        tool_call_records=tool_call_records,
                        calls=remaining,
                        safe_message="sibling cancelled after lifecycle accept error",
                    )
                    validate_provider_transcript(tuple(messages))
                    return _SiblingOutcome(
                        kind="fatal",
                        messages=tuple(messages),
                        tool_call_records=tuple(tool_call_records),
                        current_manifest=parent_before,
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="lifecycle_accept_error",
                            safe_summary=(
                                str(life_exc) or "manifest effect lifecycle accept failed"
                            ),
                            retry_disposition="never",
                        ),
                    )
                current_manifest = accepted_child
        except ValueError as exc:
            _lifecycle_discard(
                ports,
                call_id=call.call_id,
                reason_code="manifest_lineage_error",
            )
            remaining = tuple(item for item in all_calls if item.call_index > call.call_index)
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=remaining,
                safe_message="sibling cancelled after manifest lineage error",
            )
            validate_provider_transcript(tuple(messages))
            return _SiblingOutcome(
                kind="fatal",
                messages=tuple(messages),
                tool_call_records=tuple(tool_call_records),
                current_manifest=current_manifest,
                stop_reason="protocol_error",
                error=SafeProviderError(
                    semantic_code="manifest_lineage_error",
                    safe_summary=str(exc) or "invalid next manifest lineage",
                    retry_disposition="never",
                ),
            )

        event_type = (
            "tool_call.completed"
            if work.capability_status == "completed"
            else "tool_call.failed"
        )
        _emit(
            ports,
            event_type,
            {
                "callId": call.call_id,
                "callIndex": call.call_index,
                "domainKey": call.domain_key,
                "status": work.capability_status,
                "safeDurationMs": work.duration_ms,
            },
        )

    return _SiblingOutcome(
        kind="continue",
        messages=tuple(messages),
        tool_call_records=tuple(tool_call_records),
        current_manifest=current_manifest,
    )


def _handle_waiting_result(
    *,
    ports: ProviderLoopPorts,
    surface: ProviderToolSurface,
    assistant: ProviderAssistantMessage,
    all_calls: tuple[ProviderToolCall, ...],
    waiting_call: ProviderToolCall,
    work: _CallWorkResult,
    definition: ProviderToolDefinition,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    scope: ProviderExecutionScope,
    model_ref: ModelRef,
    locale: str,
    max_rounds: int,
    provider_rounds_used: int,
    prior_tool_call_count: int,
    accumulated_usage: ProviderUsage,
) -> _SiblingOutcome:
    del ports
    assert work.capability_result is not None
    assert work.next_manifest is not None
    interrupt_mode = definition.descriptor.behavior.interrupt_mode
    if interrupt_mode != "durable":
        error = CapabilityError(
            error_type="unsupported_interrupt",
            safe_code="unexpected_waiting",
            safe_message="waiting is accepted only from durable descriptors",
            retry_disposition="never",
            call_id=waiting_call.call_id,
        )
        msg = ProviderToolMessage(
            call_id=waiting_call.call_id,
            provider_alias=waiting_call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=waiting_call.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        messages.append(msg)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=waiting_call,
                status="blocked",
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=work.duration_ms,
            )
        )
        remaining = tuple(item for item in all_calls if item.call_index > waiting_call.call_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=remaining,
            safe_message="sibling cancelled after unsupported waiting",
        )
        validate_provider_transcript(tuple(messages))
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="unexpected_waiting",
                safe_summary="waiting is accepted only from durable descriptors",
                retry_disposition="never",
            ),
        )

    continuation_ref = work.capability_result.continuation
    if continuation_ref is None:
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="missing_continuation",
            safe_message="waiting result requires a portable ContinuationRef",
            retry_disposition="never",
            call_id=waiting_call.call_id,
        )
        msg = ProviderToolMessage(
            call_id=waiting_call.call_id,
            provider_alias=waiting_call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=waiting_call.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        messages.append(msg)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=waiting_call,
                status="blocked",
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=work.duration_ms,
            )
        )
        remaining = tuple(item for item in all_calls if item.call_index > waiting_call.call_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=remaining,
            safe_message="sibling cancelled after invalid waiting result",
        )
        validate_provider_transcript(tuple(messages))
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="missing_continuation",
                safe_summary="waiting result requires a portable ContinuationRef",
                retry_disposition="never",
            ),
        )

    try:
        current_manifest = _accept_next_manifest(
            previous=current_manifest,
            next_manifest=work.next_manifest,
            exposed_manifest_digest=waiting_call.manifest_digest,
            exposed_manifest_revision=waiting_call.manifest_revision,
        )
    except ValueError as exc:
        remaining = tuple(item for item in all_calls if item.call_index >= waiting_call.call_index)
        # Waiting call itself has no tool message yet; seal as blocked protocol.
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="manifest_lineage_error",
            safe_message=str(exc) or "invalid next manifest lineage",
            retry_disposition="never",
            call_id=waiting_call.call_id,
        )
        msg = ProviderToolMessage(
            call_id=waiting_call.call_id,
            provider_alias=waiting_call.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=waiting_call.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        messages.append(msg)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=waiting_call,
                status="blocked",
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=work.duration_ms,
            )
        )
        later = tuple(item for item in all_calls if item.call_index > waiting_call.call_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=later,
            safe_message="sibling cancelled after manifest lineage error",
        )
        validate_provider_transcript(tuple(messages))
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="manifest_lineage_error",
                safe_summary=str(exc) or "invalid next manifest lineage",
                retry_disposition="never",
            ),
        )

    pending = tuple(item for item in all_calls if item.call_index > waiting_call.call_index)
    # Internal deferred records only; no fabricated Provider Tool messages.
    deferred_records = tuple(
        ProviderToolCallRecord(
            call=item,
            status="deferred",
            result_message_digest=None,
            safe_duration_ms=None,
        )
        for item in pending
    )
    waiting_record = ProviderToolCallRecord(
        call=waiting_call,
        status="waiting",
        result_message_digest=None,
        safe_duration_ms=work.duration_ms,
    )
    completed_only = tuple(
        record
        for record in tool_call_records
        if record.status not in {"waiting", "deferred"}
    )
    all_records = completed_only + (waiting_record,) + deferred_records
    continuation = _build_waiting_continuation(
        scope=scope,
        model_ref=model_ref,
        locale=locale,
        max_rounds=max_rounds,
        provider_rounds_used=provider_rounds_used,
        prior_tool_call_count=prior_tool_call_count,
        accumulated_usage=accumulated_usage,
        current_manifest=current_manifest,
        exposed_surface=surface,
        assistant=assistant,
        messages=tuple(messages),
        waiting_call=waiting_call,
        pending_calls=pending,
        completed_call_records=completed_only,
        capability_continuation=continuation_ref,
    )
    validate_provider_transcript(tuple(messages), allowed_open_continuation=continuation)
    return _SiblingOutcome(
        kind="waiting",
        messages=tuple(messages),
        tool_call_records=all_records,
        current_manifest=current_manifest,
        continuation=continuation,
    )


def _execute_parallel_group(
    *,
    ports: ProviderLoopPorts,
    surface: ProviderToolSurface,
    assistant: ProviderAssistantMessage,
    all_calls: tuple[ProviderToolCall, ...],
    group_calls: tuple[ProviderToolCall, ...],
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    scope: ProviderExecutionScope,
    model_ref: ModelRef,
    locale: str,
    max_rounds: int,
    provider_rounds_used: int,
    prior_tool_call_count: int,
    accumulated_usage: ProviderUsage,
    max_workers: int,
) -> _SiblingOutcome:
    # Plan 05: reserve eligible parallel batches all-or-none under one ledger lock.
    # On batch denial, replan the group sequentially in Provider order so every
    # original Tool Call still receives a paired terminal result.
    batch_decision = _reserve_batch_calls(
        ports=ports,
        group_calls=group_calls,
        surface=surface,
    )
    if not batch_decision.allowed:
        return _execute_sequential_group(
            ports=ports,
            surface=surface,
            assistant=assistant,
            all_calls=all_calls,
            group_calls=group_calls,
            messages=messages,
            tool_call_records=tool_call_records,
            current_manifest=current_manifest,
            scope=scope,
            model_ref=model_ref,
            locale=locale,
            max_rounds=max_rounds,
            provider_rounds_used=provider_rounds_used,
            prior_tool_call_count=prior_tool_call_count,
            accumulated_usage=accumulated_usage,
        )

    del assistant, model_ref, locale, max_rounds, provider_rounds_used
    del prior_tool_call_count, accumulated_usage
    parent_manifest = current_manifest
    items = [
        _CallWorkItem(
            call=call,
            definition=lookup_tool_by_alias(surface, call.provider_alias),
            current_manifest=parent_manifest,
        )
        for call in group_calls
    ]

    def worker(item: _CallWorkItem) -> _CallWorkResult:
        return _run_dispatch_item(ports=ports, item=item, scope=scope)

    try:
        results: list[_CallWorkResult] = ports.sibling_executor.map_parallel(
            items,
            worker,
            max_workers=max_workers,
        )
    except Exception as exc:  # noqa: BLE001
        # Infrastructure failure before honest per-call results: block first, cancel rest.
        # Release all batch-reserved calls that never reached mark_started.
        for reserved_call in group_calls:
            _release_unstarted_via_ports(
                ports=ports,
                call_id=reserved_call.call_id,
                reason_code="executor_error",
            )
        first = group_calls[0]
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="executor_error",
            safe_message="sibling executor infrastructure failed",
            retry_disposition="never",
            call_id=first.call_id,
        )
        msg = ProviderToolMessage(
            call_id=first.call_id,
            provider_alias=first.provider_alias,
            content=ProviderToolResultEnvelope(
                status="blocked",
                domain_key=first.domain_key,
                user_text=None,
                structured_output=None,
                terminal_output=False,
                needs_followup=False,
                error=error,
            ),
        )
        messages.append(msg)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=first,
                status="blocked",
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=None,
            )
        )
        remaining = tuple(item for item in all_calls if item.call_index > first.call_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=remaining,
            safe_message="sibling cancelled after executor failure",
        )
        validate_provider_transcript(tuple(messages))
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="executor_error",
                safe_summary="sibling executor infrastructure failed",
                retry_disposition="never",
            ),
        )

    # Collect in Provider order; retain all started results honestly.
    ordered = sorted(results, key=lambda item: item.call.call_index)
    child_manifests: list[ResolvedRunManifestRevision] = []
    fatal: _CallWorkResult | None = None
    unexpected_waiting: _CallWorkResult | None = None

    for work in ordered:
        if work.kind == "waiting":
            unexpected_waiting = work
            # Protocol error: parallel-safe descriptors cannot wait.
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="unexpected_waiting",
                safe_message="parallel worker returned waiting despite interrupt_mode=none",
                retry_disposition="never",
                call_id=work.call.call_id,
            )
            msg = ProviderToolMessage(
                call_id=work.call.call_id,
                provider_alias=work.call.provider_alias,
                content=ProviderToolResultEnvelope(
                    status="blocked",
                    domain_key=work.call.domain_key,
                    user_text=None,
                    structured_output=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=error,
                ),
            )
            messages.append(msg)
            tool_call_records.append(
                ProviderToolCallRecord(
                    call=work.call,
                    status="blocked",
                    result_message_digest=digest_provider_message(msg),
                    safe_duration_ms=work.duration_ms,
                )
            )
            fatal = work
            continue

        if work.kind in {"fatal", "protocol"}:
            assert work.tool_message is not None
            messages.append(work.tool_message)
            tool_call_records.append(
                ProviderToolCallRecord(
                    call=work.call,
                    status="blocked",
                    result_message_digest=digest_provider_message(work.tool_message),
                    safe_duration_ms=work.duration_ms,
                )
            )
            if fatal is None:
                fatal = work
            continue

        assert work.tool_message is not None
        assert work.next_manifest is not None
        assert work.capability_status is not None
        messages.append(work.tool_message)
        tool_call_records.append(
            ProviderToolCallRecord(
                call=work.call,
                status=work.capability_status,  # type: ignore[arg-type]
                result_message_digest=digest_provider_message(work.tool_message),
                safe_duration_ms=work.duration_ms,
            )
        )
        child_manifests.append(work.next_manifest)
        event_type = (
            "tool_call.completed"
            if work.capability_status == "completed"
            else "tool_call.failed"
        )
        _emit(
            ports,
            event_type,
            {
                "callId": work.call.call_id,
                "callIndex": work.call.call_index,
                "domainKey": work.call.domain_key,
                "status": work.capability_status,
                "safeDurationMs": work.duration_ms,
            },
        )

    if fatal is not None or unexpected_waiting is not None:
        last_started_index = max(item.call.call_index for item in ordered)
        remaining = tuple(item for item in all_calls if item.call_index > last_started_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=remaining,
            safe_message="sibling cancelled before start after fatal parallel call",
        )
        validate_provider_transcript(tuple(messages))
        source = fatal or unexpected_waiting
        assert source is not None
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=current_manifest,
            stop_reason="protocol_error"
            if source.kind in {"protocol", "waiting"}
            else "capability_error",
            error=SafeProviderError(
                semantic_code=source.safe_code or "unexpected_waiting",
                safe_summary=source.safe_summary
                or "parallel worker returned waiting despite interrupt_mode=none",
                retry_disposition="never",
            ),
        )

    try:
        current_manifest = merge_parallel_manifests(
            parent=parent_manifest,
            children=child_manifests,
        )
        # Validate each accepted child against parent lineage when different.
        if current_manifest.manifest_digest != parent_manifest.manifest_digest:
            current_manifest = _accept_next_manifest(
                previous=parent_manifest,
                next_manifest=current_manifest,
                exposed_manifest_digest=group_calls[0].manifest_digest,
                exposed_manifest_revision=group_calls[0].manifest_revision,
            )
    except ValueError as exc:
        # All started calls already paired; cancel later groups and stop.
        last_started_index = max(item.call_index for item in group_calls)
        remaining = tuple(item for item in all_calls if item.call_index > last_started_index)
        _append_cancelled_before_start(
            messages=messages,
            tool_call_records=tool_call_records,
            calls=remaining,
            safe_message="sibling cancelled after parallel manifest merge error",
        )
        validate_provider_transcript(tuple(messages))
        return _SiblingOutcome(
            kind="fatal",
            messages=tuple(messages),
            tool_call_records=tuple(tool_call_records),
            current_manifest=parent_manifest,
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="manifest_merge_error",
                safe_summary=str(exc) or "conflicting parallel sibling manifests",
                retry_disposition="never",
            ),
        )

    return _SiblingOutcome(
        kind="continue",
        messages=tuple(messages),
        tool_call_records=tuple(tool_call_records),
        current_manifest=current_manifest,
    )


def _validate_resume_request(
    request: ProviderLoopResumeRequest,
    *,
    ports: ProviderLoopPorts,
) -> tuple[ProviderAssistantMessage, ProviderToolCall, tuple[ProviderToolCall, ...]]:
    cont = request.continuation
    identity = recompute_continuation_identity(cont)
    if identity["scope_digest"] != cont.execution_scope.scope_digest:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="scope_digest_mismatch",
                safe_summary="continuation scope digest mismatch",
                retry_disposition="never",
            ),
        )
    if cont.exposed_surface.surface_digest != identity["surface_digest"]:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="surface_digest_mismatch",
                safe_summary="continuation surface digest mismatch",
                retry_disposition="never",
            ),
        )
    if request.manifest.manifest_digest != cont.current_manifest_digest:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="manifest_digest_mismatch",
                safe_summary="resume manifest digest mismatch",
                retry_disposition="never",
            ),
        )
    if request.manifest.revision != cont.current_manifest_revision:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="manifest_revision_mismatch",
                safe_summary="resume manifest revision mismatch",
                retry_disposition="never",
            ),
        )

    # Locate the open assistant message.
    assistant: ProviderAssistantMessage | None = None
    for message in request.messages:
        if isinstance(message, ProviderAssistantMessage) and message.tool_calls:
            digest = digest_provider_message(message)
            if digest == cont.assistant_message_digest:
                assistant = message
    if assistant is None:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="assistant_message_mismatch",
                safe_summary="resume could not locate the open assistant message",
                retry_disposition="never",
            ),
        )

    waiting_call = next(
        (call for call in assistant.tool_calls if call.call_id == cont.waiting_call.call_id),
        None,
    )
    if waiting_call is None:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="waiting_call_mismatch",
                safe_summary="resume waiting call missing from assistant message",
                retry_disposition="never",
            ),
        )
    pending = tuple(
        call
        for call in assistant.tool_calls
        if call.call_id in cont.pending_call_ids
    )
    if tuple(call.call_id for call in pending) != cont.pending_call_ids:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="pending_order_mismatch",
                safe_summary="resume pending call order mismatch",
                retry_disposition="never",
            ),
        )

    # Model/locale/budget identity.
    if cont.model_ref.model_ref_digest != request.continuation.model_ref.model_ref_digest:
        raise ProviderLoopError(
            stop_reason="protocol_error",
            error=SafeProviderError(
                semantic_code="model_ref_mismatch",
                safe_summary="resume model_ref mismatch",
                retry_disposition="never",
            ),
        )
    _validate_adapter_identity(
        model_ref=cont.model_ref,
        provider=ports.provider,
        manifest=request.manifest,
    )
    return assistant, waiting_call, pending


def resume_provider_agent_loop(
    request: ProviderLoopResumeRequest,
    ports: ProviderLoopPorts,
    *,
    finalization_instructions: FinalizationInstructionProvider | None = None,
) -> ProviderLoopResult:
    """Resume after a trusted durable waiting resolution."""
    if not isinstance(request, ProviderLoopResumeRequest):
        raise TypeError("request must be a ProviderLoopResumeRequest")
    if not isinstance(ports, ProviderLoopPorts):
        raise TypeError("ports must be a ProviderLoopPorts")

    cont = request.continuation
    try:
        assistant, waiting_call, pending = _validate_resume_request(request, ports=ports)
        messages: list[ProviderMessage] = list(request.messages)
        tool_call_records: list[ProviderToolCallRecord] = list(cont.completed_call_records)
        current_manifest = request.manifest
        accumulated_usage = cont.accumulated_usage
        round_count = cont.provider_rounds_used
        prior_tool_call_count = cont.prior_tool_call_count
        surface = cont.exposed_surface

        # Classification freshness for every definition on the open assistant message.
        try:
            _preplan_verify_all(
                ports=ports,
                surface=surface,
                calls=assistant.tool_calls,
                scope=cont.execution_scope,
            )
        except _ClassificationDrift:
            # Insert trusted terminal waiting result, cancel pending suffix, seal.
            tool_message = project_waiting_resolution_message(
                call=waiting_call,
                resolution=request.resolved_waiting,
            )
            messages.append(tool_message)
            tool_call_records.append(
                ProviderToolCallRecord(
                    call=waiting_call,
                    status=tool_message.content.status,  # type: ignore[arg-type]
                    result_message_digest=digest_provider_message(tool_message),
                    safe_duration_ms=None,
                )
            )
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=pending,
                safe_message="sibling cancelled before start due to classification drift on resume",
            )
            validate_provider_transcript(tuple(messages))
            raise ProviderLoopError(
                stop_reason="capability_error",
                error=SafeProviderError(
                    semantic_code="classification_changed",
                    safe_summary="capability classification changed before resume dispatch",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )

        # Append trusted terminal waiting result projected by the loop.
        tool_message = project_waiting_resolution_message(
            call=waiting_call,
            resolution=request.resolved_waiting,
        )
        messages.append(tool_message)
        status = tool_message.content.status
        tool_call_records.append(
            ProviderToolCallRecord(
                call=waiting_call,
                status=status,  # type: ignore[arg-type]
                result_message_digest=digest_provider_message(tool_message),
                safe_duration_ms=None,
            )
        )

        if status in {"blocked", "cancelled"} and status != "completed":
            # Terminal non-success waiting resolution still pairs; continue pending only for completed/failed recoverable.
            pass

        if status == "cancelled":
            _append_cancelled_before_start(
                messages=messages,
                tool_call_records=tool_call_records,
                calls=pending,
                safe_message="sibling cancelled before start after waiting cancellation",
            )
            validate_provider_transcript(tuple(messages))
            return ProviderLoopResult(
                status="cancelled",
                final_text=None,
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                round_count=round_count,
                stop_reason="cancelled",
                manifest=current_manifest,
                continuation=None,
                usage=accumulated_usage,
                error=None,
            )

        # Continue remaining siblings against the original frozen surface.
        sibling_outcome = _execute_sibling_calls(
            ports=ports,
            surface=surface,
            assistant=assistant,
            calls=assistant.tool_calls,
            start_index=cont.next_call_index,
            messages=messages,
            tool_call_records=tool_call_records,
            current_manifest=current_manifest,
            scope=cont.execution_scope,
            model_ref=cont.model_ref,
            locale=cont.locale,
            max_rounds=cont.max_rounds,
            provider_rounds_used=round_count,
            prior_tool_call_count=prior_tool_call_count,
            accumulated_usage=accumulated_usage,
        )
        messages = list(sibling_outcome.messages)
        tool_call_records = list(sibling_outcome.tool_call_records)
        current_manifest = sibling_outcome.current_manifest

        if sibling_outcome.kind == "waiting":
            assert sibling_outcome.continuation is not None
            return ProviderLoopResult(
                status="waiting",
                final_text=None,
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                round_count=round_count,
                stop_reason="waiting_interrupt",
                manifest=current_manifest,
                continuation=sibling_outcome.continuation,
                usage=accumulated_usage,
                error=None,
            )
        if sibling_outcome.kind == "fatal":
            raise ProviderLoopError(
                stop_reason=sibling_outcome.stop_reason or "capability_error",
                error=sibling_outcome.error
                or SafeProviderError(
                    semantic_code="capability_error",
                    safe_summary="sibling execution failed on resume",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )
        if sibling_outcome.kind == "cancelled":
            raise ProviderLoopError(
                status="cancelled",
                stop_reason="cancelled",
                error=SafeProviderError(
                    semantic_code="cancelled",
                    safe_summary="loop cancelled during resume dispatch",
                    retry_disposition="never",
                ),
                messages=tuple(messages),
                tool_calls=tuple(tool_call_records),
                manifest=current_manifest,
                usage=accumulated_usage,
                round_count=round_count,
            )

        validate_provider_transcript(tuple(messages))

        # Continue the shared state machine for remaining Provider rounds.
        resume_request = ProviderLoopRequest(
            manifest=current_manifest,
            initial_messages=tuple(messages),
            model_ref=cont.model_ref,
            execution_scope=cont.execution_scope,
            max_rounds=cont.max_rounds,
            locale=cont.locale,
            generation=ProviderGenerationOptions(),
        )
        return _continue_after_resume(
            request=resume_request,
            ports=ports,
            messages=messages,
            tool_call_records=tool_call_records,
            current_manifest=current_manifest,
            accumulated_usage=accumulated_usage,
            round_count=round_count,
            prior_tool_call_count=prior_tool_call_count,
            finalization_instructions=finalization_instructions,
        )
    except ProviderLoopError as terminal:
        return _result_from_loop_error(terminal, fallback_manifest=request.manifest)


def _continue_after_resume(
    *,
    request: ProviderLoopRequest,
    ports: ProviderLoopPorts,
    messages: list[ProviderMessage],
    tool_call_records: list[ProviderToolCallRecord],
    current_manifest: ResolvedRunManifestRevision,
    accumulated_usage: ProviderUsage,
    round_count: int,
    prior_tool_call_count: int,
    finalization_instructions: FinalizationInstructionProvider | None,
) -> ProviderLoopResult:
    """Shared post-tool-round continuation used by resume after full pairing."""
    instruction_provider = finalization_instructions or DefaultFinalizationInstructionProvider()
    finalization_instruction_appended = any(
        isinstance(message, ProviderRuntimeInstructionMessage) for message in messages
    )
    # Process-local: skills already on the resumed Manifest are treated as applied so
    # resume does not re-inject their protected context. New activations after resume
    # still inject once.
    already_applied_skill_version_ids: tuple[UUID, ...] = tuple(
        skill.version_id for skill in current_manifest.active_skills
    )
    try:
        while True:
            if ports.cancellation.is_cancelled():
                raise ProviderLoopError(
                    status="cancelled",
                    stop_reason="cancelled",
                    error=SafeProviderError(
                        semantic_code="cancelled",
                        safe_summary="loop cancelled before provider round",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            if round_count >= request.max_rounds:
                raise ProviderLoopError(
                    stop_reason="max_rounds_hard_stop",
                    error=SafeProviderError(
                        semantic_code="max_rounds_hard_stop",
                        safe_summary="provider round budget exhausted",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            _validate_loop_identity(request=request, provider=ports.provider)
            validate_provider_transcript(tuple(messages))

            round_index = round_count
            finalization = is_finalization_round(
                round_index=round_index,
                max_rounds=request.max_rounds,
                prior_tool_call_count=prior_tool_call_count,
            )

            if finalization:
                surface = _empty_finalization_surface(
                    manifest=current_manifest,
                    provider_protocol=ports.provider.provider_protocol,
                    scope=request.execution_scope,
                )
                if not finalization_instruction_appended:
                    instruction = instruction_provider.build(locale=request.locale)
                    if not isinstance(instruction, ProviderRuntimeInstructionMessage):
                        raise ProviderLoopError(
                            stop_reason="protocol_error",
                            error=SafeProviderError(
                                semantic_code="finalization_instruction_invalid",
                                safe_summary="finalization instruction must be a runtime message",
                                retry_disposition="never",
                            ),
                            messages=tuple(messages),
                            tool_calls=tuple(tool_call_records),
                            manifest=current_manifest,
                            usage=accumulated_usage,
                            round_count=round_count,
                        )
                    messages.append(instruction)
                    finalization_instruction_appended = True
                generation = ProviderGenerationOptions(
                    max_output_tokens=request.generation.max_output_tokens,
                    temperature=request.generation.temperature,
                    tool_choice=ProviderToolChoice(mode="none"),
                    request_parallel_tool_calls=request.generation.request_parallel_tool_calls,
                )
                tools_enabled = False
            else:
                try:
                    resolution = ports.tools_provider.resolve(
                        current_manifest,
                        scope=request.execution_scope,
                        locale=request.locale,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="tools_provider_failed",
                            safe_summary="tools provider failed before provider round",
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    ) from exc
                resolution = _require_tool_surface_resolution(resolution)
                _validate_alias_revision_lineage(
                    previous=current_manifest,
                    next_manifest=resolution.manifest,
                )
                current_manifest = resolution.manifest
                surface = resolution.surface
                generation = request.generation
                tools_enabled = True
                if request.max_rounds == 1 and surface.tools:
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="max_rounds_surface_conflict",
                            safe_summary=(
                                "nonempty tool surface requires max_rounds >= 2 "
                                "so a finalization round can be reserved"
                            ),
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    )

                already_applied_skill_version_ids = _append_round_context(
                    ports=ports,
                    messages=messages,
                    current_manifest=current_manifest,
                    already_applied_skill_version_ids=already_applied_skill_version_ids,
                    execution_scope=request.execution_scope,
                    locale=request.locale,
                    tool_call_records=tool_call_records,
                    accumulated_usage=accumulated_usage,
                    round_count=round_count,
                )

            round_count += 1
            round_request = ProviderRoundRequest(
                round_index=round_index,
                messages=tuple(messages),
                tool_surface=surface,
                tools_enabled=tools_enabled,
                finalization_round=finalization,
                model_ref=request.model_ref,
                generation=generation,
            )
            try:
                guarded_generation = _apply_round_budget_before(
                    ports=ports,
                    round_request=round_request,
                )
            except ProviderLoopError as budget_err:
                budget_err.messages = tuple(messages)
                budget_err.tool_calls = tuple(tool_call_records)
                budget_err.manifest = current_manifest
                budget_err.usage = accumulated_usage
                budget_err.round_count = round_count
                raise
            if guarded_generation is not generation:
                round_request = ProviderRoundRequest(
                    round_index=round_index,
                    messages=tuple(messages),
                    tool_surface=surface,
                    tools_enabled=tools_enabled,
                    finalization_round=finalization,
                    model_ref=request.model_ref,
                    generation=guarded_generation,
                )
            try:
                events = list(
                    ports.provider.stream_round(
                        round_request,
                        cancellation=ports.cancellation,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                stop = "max_rounds_hard_stop" if finalization else "provider_error"
                raise ProviderLoopError(
                    stop_reason=stop,
                    error=SafeProviderError(
                        semantic_code=stop,
                        safe_summary=(
                            "finalization provider round failed"
                            if finalization
                            else "provider round failed"
                        ),
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from exc

            try:
                round_result = assemble_provider_round(
                    events=events,
                    surface=surface,
                    round_index=round_index,
                )
            except ValueError as exc:
                stop = "max_rounds_hard_stop" if finalization else "protocol_error"
                raise ProviderLoopError(
                    stop_reason=stop,
                    error=SafeProviderError(
                        semantic_code=stop if finalization else "protocol_error",
                        safe_summary=str(exc) or "provider stream protocol error",
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from exc

            _apply_round_budget_after(ports=ports, round_result=round_result)

            assistant = round_result.assistant_message
            accumulated_usage = aggregate_provider_usage(
                accumulated_usage,
                round_result.usage,
            )
            messages.append(assistant)

            if not assistant.tool_calls:
                # Plan 05: present no-Tool candidate to completion guard.
                completed = _handle_no_tool_completion(
                    ports=ports,
                    messages=messages,
                    tool_call_records=tool_call_records,
                    current_manifest=current_manifest,
                    accumulated_usage=accumulated_usage,
                    round_count=round_count,
                    round_index=round_index,
                    finalization=finalization,
                    final_text=assistant.content,
                )
                if completed is not None:
                    return completed
                continue

            if finalization:
                raise ProviderLoopError(
                    stop_reason="max_rounds_hard_stop",
                    error=SafeProviderError(
                        semantic_code="max_rounds_hard_stop",
                        safe_summary="finalization round returned tool calls",
                        adapter_key=ports.provider.adapter_key,
                        adapter_revision=ports.provider.adapter_revision,
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )

            prior_tool_call_count += len(assistant.tool_calls)
            try:
                _preplan_verify_all(
                    ports=ports,
                    surface=surface,
                    calls=assistant.tool_calls,
                    scope=request.execution_scope,
                )
            except _ClassificationDrift as drift:
                sealed_messages, sealed_records = _seal_classification_drift(
                    messages=messages,
                    tool_call_records=tool_call_records,
                    calls=assistant.tool_calls,
                    first_stale_index=drift.first_stale_index,
                )
                raise ProviderLoopError(
                    stop_reason="capability_error",
                    error=SafeProviderError(
                        semantic_code="classification_changed",
                        safe_summary="capability classification changed before dispatch",
                        retry_disposition="never",
                    ),
                    messages=sealed_messages,
                    tool_calls=sealed_records,
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                ) from drift

            sibling_outcome = _execute_sibling_calls(
                ports=ports,
                surface=surface,
                assistant=assistant,
                calls=assistant.tool_calls,
                start_index=0,
                messages=messages,
                tool_call_records=tool_call_records,
                current_manifest=current_manifest,
                scope=request.execution_scope,
                model_ref=request.model_ref,
                locale=request.locale,
                max_rounds=request.max_rounds,
                provider_rounds_used=round_count,
                prior_tool_call_count=prior_tool_call_count,
                accumulated_usage=accumulated_usage,
            )
            messages = list(sibling_outcome.messages)
            tool_call_records = list(sibling_outcome.tool_call_records)
            current_manifest = sibling_outcome.current_manifest
            if sibling_outcome.kind == "waiting":
                assert sibling_outcome.continuation is not None
                return ProviderLoopResult(
                    status="waiting",
                    final_text=None,
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    round_count=round_count,
                    stop_reason="waiting_interrupt",
                    manifest=current_manifest,
                    continuation=sibling_outcome.continuation,
                    usage=accumulated_usage,
                    error=None,
                )
            if sibling_outcome.kind == "fatal":
                raise ProviderLoopError(
                    stop_reason=sibling_outcome.stop_reason or "capability_error",
                    error=sibling_outcome.error
                    or SafeProviderError(
                        semantic_code="capability_error",
                        safe_summary="sibling execution failed",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )
            if sibling_outcome.kind == "cancelled":
                raise ProviderLoopError(
                    status="cancelled",
                    stop_reason="cancelled",
                    error=SafeProviderError(
                        semantic_code="cancelled",
                        safe_summary="loop cancelled during tool dispatch",
                        retry_disposition="never",
                    ),
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    manifest=current_manifest,
                    usage=accumulated_usage,
                    round_count=round_count,
                )
            validate_provider_transcript(tuple(messages))
            continue
    except ProviderLoopError as terminal:
        return _result_from_loop_error(terminal, fallback_manifest=request.manifest)


def seal_waiting_after_cancellation(
    *,
    messages: tuple[ProviderMessage, ...],
    continuation: ProviderLoopContinuation,
    waiting_call: ProviderToolCall,
    pending_calls: tuple[ProviderToolCall, ...],
    tool_call_records: tuple[ProviderToolCallRecord, ...] = (),
    manifest: ResolvedRunManifestRevision,
    round_count: int | None = None,
    usage: ProviderUsage | None = None,
) -> ProviderLoopResult:
    """Seal a waiting transcript after a trusted child-cancellation outcome.

    Protocol-only: does not invoke or claim to cancel the durable child.
    """
    sealed_messages = seal_cancelled_continuation(
        messages,
        waiting_call=waiting_call,
        pending_calls=pending_calls,
    )
    waiting_msg = sealed_messages[len(messages)]
    records = list(tool_call_records) or list(continuation.completed_call_records)
    records.append(
        ProviderToolCallRecord(
            call=waiting_call,
            status="cancelled",
            result_message_digest=digest_provider_message(waiting_msg),
            safe_duration_ms=None,
        )
    )
    for index, call in enumerate(pending_calls):
        msg = sealed_messages[len(messages) + 1 + index]
        records.append(
            ProviderToolCallRecord(
                call=call,
                status="cancelled_before_start",
                result_message_digest=digest_provider_message(msg),
                safe_duration_ms=None,
            )
        )
    validate_provider_transcript(sealed_messages)
    return ProviderLoopResult(
        status="cancelled",
        final_text=None,
        messages=sealed_messages,
        tool_calls=tuple(records),
        round_count=round_count if round_count is not None else continuation.provider_rounds_used,
        stop_reason="cancelled",
        manifest=manifest,
        continuation=None,
        usage=usage or continuation.accumulated_usage,
        error=None,
    )


class ProviderAgentLoop:
    """Object facade sharing one internal state machine for start/resume/seal."""

    def start(
        self,
        request: ProviderLoopRequest,
        *,
        ports: ProviderLoopPorts,
        finalization_instructions: FinalizationInstructionProvider | None = None,
    ) -> ProviderLoopResult:
        return run_provider_agent_loop(
            request,
            ports,
            finalization_instructions=finalization_instructions,
        )

    def resume(
        self,
        request: ProviderLoopResumeRequest,
        *,
        ports: ProviderLoopPorts,
        finalization_instructions: FinalizationInstructionProvider | None = None,
    ) -> ProviderLoopResult:
        return resume_provider_agent_loop(
            request,
            ports,
            finalization_instructions=finalization_instructions,
        )

    def seal_waiting_after_cancellation(
        self,
        *,
        messages: tuple[ProviderMessage, ...],
        continuation: ProviderLoopContinuation,
        waiting_call: ProviderToolCall,
        pending_calls: tuple[ProviderToolCall, ...],
        tool_call_records: tuple[ProviderToolCallRecord, ...] = (),
        manifest: ResolvedRunManifestRevision,
        round_count: int | None = None,
        usage: ProviderUsage | None = None,
    ) -> ProviderLoopResult:
        return seal_waiting_after_cancellation(
            messages=messages,
            continuation=continuation,
            waiting_call=waiting_call,
            pending_calls=pending_calls,
            tool_call_records=tool_call_records,
            manifest=manifest,
            round_count=round_count,
            usage=usage,
        )


__all__ = [
    "ProviderAgentLoop",
    "ProviderLoopError",
    "assemble_provider_round",
    "is_finalization_round",
    "resume_provider_agent_loop",
    "run_provider_agent_loop",
    "seal_waiting_after_cancellation",
]
