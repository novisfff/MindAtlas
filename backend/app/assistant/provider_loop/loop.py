"""Core Provider Agent Loop — direct answer and single-call sequential dispatch.

Plan 03 Task 3: rebuild tools before every Provider round, assemble one assistant
message, verify classification freshness before planning/dispatch, pair every
Tool Call, and never emit provisional tool-call prose as final text.

Does not implement parallel sibling scheduling, soft finalization reservation
beyond basic multi-round completion, waiting/resume, or live OpenAI adapters.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityResult,
)
from app.assistant.domain.contracts import (
    ModelRef,
    ResolvedRunManifestRevision,
    validate_manifest_child_link,
)
from app.assistant.provider_loop.aliases import lookup_tool_by_alias
from app.assistant.provider_loop.contracts import (
    ProviderAdapter,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderExecutionScope,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopResult,
    ProviderRoundRequest,
    ProviderRoundResult,
    ProviderRoundTerminal,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolDefinition,
    ProviderToolSurface,
    ProviderUsage,
    ProviderUsageSnapshot,
    SafeProviderError,
    ToolSurfaceResolution,
    aggregate_provider_usage,
    compute_scope_digest,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderMessage,
    ProviderToolCall,
    ProviderToolCallRecord,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    digest_arguments,
    digest_provider_message,
    project_tool_result_envelope,
    validate_provider_transcript,
)


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
) -> ProviderLoopResult:
    """Run the provider-neutral agent loop until completion, failure, or cancel."""
    if not isinstance(request, ProviderLoopRequest):
        raise TypeError("request must be a ProviderLoopRequest")
    if not isinstance(ports, ProviderLoopPorts):
        raise TypeError("ports must be a ProviderLoopPorts")

    messages: list[ProviderMessage] = list(request.initial_messages)
    tool_call_records: list[ProviderToolCallRecord] = []
    current_manifest = request.manifest
    accumulated_usage = ProviderUsage()
    round_count = 0

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

            # Resolve tools immediately before the Provider request.
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

            round_index = round_count
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
                    "toolsEnabled": True,
                },
            )

            round_request = ProviderRoundRequest(
                round_index=round_index,
                messages=tuple(messages),
                tool_surface=surface,
                tools_enabled=True,
                finalization_round=False,
                model_ref=request.model_ref,
                generation=request.generation,
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
                raise ProviderLoopError(
                    stop_reason="provider_error",
                    error=SafeProviderError(
                        semantic_code="provider_error",
                        safe_summary="provider round failed",
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
                raise ProviderLoopError(
                    stop_reason="protocol_error",
                    error=SafeProviderError(
                        semantic_code="protocol_error",
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
                },
            )

            if not assistant.tool_calls:
                final_text = assistant.content
                if final_text is None or not str(final_text).strip():
                    # Discard empty direct answer; seal transcript (already paired).
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="empty_response",
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

                # Replay buffered final text only after successful assembly.
                for index, chunk in enumerate(_chunk_final_text(final_text)):
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
                result = ProviderLoopResult(
                    status="completed",
                    final_text=final_text,
                    messages=tuple(messages),
                    tool_calls=tuple(tool_call_records),
                    round_count=round_count,
                    stop_reason="natural_completion",
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
                        "stopReason": "natural_completion",
                        "toolCallCount": len(tool_call_records),
                    },
                )
                return result

            # Tool-call path: provisional prose is retained in history only.
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

            # Task 3: sequential single-call dispatch (one or more, never drop).
            for call in assistant.tool_calls:
                definition = lookup_tool_by_alias(surface, call.provider_alias)
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
                        definition=definition,
                        current_manifest=current_manifest,
                        scope=request.execution_scope,
                    )
                except _FatalCapability as fatal:
                    duration_ms = (time.perf_counter() - started) * 1000.0
                    blocked_message = fatal.tool_message
                    messages.append(blocked_message)
                    record = ProviderToolCallRecord(
                        call=call,
                        status="blocked",
                        result_message_digest=digest_provider_message(blocked_message),
                        safe_duration_ms=duration_ms,
                    )
                    tool_call_records.append(record)
                    # Seal remaining never-started siblings.
                    remaining = [
                        sibling
                        for sibling in assistant.tool_calls
                        if sibling.call_index > call.call_index
                    ]
                    for sibling in remaining:
                        cancel_msg = ProviderToolMessage(
                            call_id=sibling.call_id,
                            provider_alias=sibling.provider_alias,
                            content=ProviderToolResultEnvelope(
                                status="cancelled_before_start",
                                domain_key=sibling.domain_key,
                                user_text=None,
                                structured_output=None,
                                terminal_output=False,
                                needs_followup=False,
                                error=CapabilityError(
                                    error_type="cancelled",
                                    safe_code="cancelled_before_start",
                                    safe_message="sibling cancelled before start after fatal call",
                                    retry_disposition="never",
                                    call_id=sibling.call_id,
                                ),
                            ),
                        )
                        messages.append(cancel_msg)
                        tool_call_records.append(
                            ProviderToolCallRecord(
                                call=sibling,
                                status="cancelled_before_start",
                                result_message_digest=digest_provider_message(cancel_msg),
                                safe_duration_ms=None,
                            )
                        )
                    validate_provider_transcript(tuple(messages))
                    raise ProviderLoopError(
                        stop_reason="capability_error",
                        error=SafeProviderError(
                            semantic_code=fatal.safe_code,
                            safe_summary=fatal.safe_summary,
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    ) from fatal

                duration_ms = (time.perf_counter() - started) * 1000.0
                messages.append(tool_message)
                record = ProviderToolCallRecord(
                    call=call,
                    status=capability_status,  # type: ignore[arg-type]
                    result_message_digest=digest_provider_message(tool_message),
                    safe_duration_ms=duration_ms,
                )
                tool_call_records.append(record)

                try:
                    current_manifest = _accept_next_manifest(
                        previous=current_manifest,
                        next_manifest=next_manifest,
                        exposed_manifest_digest=call.manifest_digest,
                        exposed_manifest_revision=call.manifest_revision,
                    )
                except ValueError as exc:
                    # Manifest lineage failure after a completed dispatch is fatal;
                    # the call is already paired. Seal remaining siblings if any.
                    remaining = [
                        sibling
                        for sibling in assistant.tool_calls
                        if sibling.call_index > call.call_index
                    ]
                    for sibling in remaining:
                        cancel_msg = ProviderToolMessage(
                            call_id=sibling.call_id,
                            provider_alias=sibling.provider_alias,
                            content=ProviderToolResultEnvelope(
                                status="cancelled_before_start",
                                domain_key=sibling.domain_key,
                                user_text=None,
                                structured_output=None,
                                terminal_output=False,
                                needs_followup=False,
                                error=CapabilityError(
                                    error_type="cancelled",
                                    safe_code="cancelled_before_start",
                                    safe_message="sibling cancelled after manifest lineage error",
                                    retry_disposition="never",
                                    call_id=sibling.call_id,
                                ),
                            ),
                        )
                        messages.append(cancel_msg)
                        tool_call_records.append(
                            ProviderToolCallRecord(
                                call=sibling,
                                status="cancelled_before_start",
                                result_message_digest=digest_provider_message(cancel_msg),
                                safe_duration_ms=None,
                            )
                        )
                    validate_provider_transcript(tuple(messages))
                    raise ProviderLoopError(
                        stop_reason="protocol_error",
                        error=SafeProviderError(
                            semantic_code="manifest_lineage_error",
                            safe_summary=str(exc) or "invalid next manifest lineage",
                            retry_disposition="never",
                        ),
                        messages=tuple(messages),
                        tool_calls=tuple(tool_call_records),
                        manifest=current_manifest,
                        usage=accumulated_usage,
                        round_count=round_count,
                    ) from exc

                event_type = (
                    "tool_call.completed"
                    if capability_status == "completed"
                    else "tool_call.failed"
                )
                _emit(
                    ports,
                    event_type,
                    {
                        "callId": call.call_id,
                        "callIndex": call.call_index,
                        "domainKey": call.domain_key,
                        "status": capability_status,
                        "safeDurationMs": duration_ms,
                    },
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
# Round assembly (Task 3 minimal; Task 4 expands streaming edge cases)
# ---------------------------------------------------------------------------


def assemble_provider_round(
    *,
    events: Sequence[Any],
    surface: ProviderToolSurface,
    round_index: int,
) -> ProviderRoundResult:
    """Assemble one normalized assistant message from contiguous stream events."""
    if not events:
        raise ValueError("provider stream was empty")

    text_parts: list[str] = []
    # call_index -> mutable builder
    builders: dict[int, dict[str, Any]] = {}
    usage: ProviderUsage | None = None
    finish_reason: str | None = None
    saw_terminal = False
    warnings: list[str] = []

    for index, event in enumerate(events):
        if saw_terminal:
            raise ValueError("stream event after terminal")
        if getattr(event, "sequence", None) != index:
            raise ValueError("stream event sequences must be contiguous from zero")

        if isinstance(event, ProviderTextDelta):
            text_parts.append(event.delta)
            continue

        if isinstance(event, ProviderToolCallDelta):
            builder = builders.setdefault(
                event.call_index,
                {
                    "call_id": None,
                    "provider_alias": "",
                    "arguments": "",
                },
            )
            if event.call_id is not None:
                if builder["call_id"] is None:
                    builder["call_id"] = event.call_id
                elif builder["call_id"] != event.call_id:
                    raise ValueError("tool call id changed mid-stream")
            if event.provider_alias_delta:
                builder["provider_alias"] += event.provider_alias_delta
            if event.arguments_delta:
                builder["arguments"] += event.arguments_delta
            continue

        if isinstance(event, ProviderUsageSnapshot):
            usage = event.usage
            continue

        if isinstance(event, ProviderRoundTerminal):
            saw_terminal = True
            finish_reason = event.finish_reason
            continue

        raise ValueError(f"unsupported stream event type {type(event)!r}")

    if not saw_terminal:
        raise ValueError("provider stream missing terminal event")

    content = "".join(text_parts) if text_parts else None
    if content == "":
        content = None

    if not builders:
        return ProviderRoundResult(
            assistant_message=ProviderAssistantMessage(content=content, tool_calls=()),
            finish_reason=finish_reason,
            usage=usage,
            compatibility_warnings=tuple(warnings),
        )

    # Build tool calls in provider order (contiguous indexes from 0).
    indexes = sorted(builders)
    if indexes != list(range(len(indexes))):
        raise ValueError("tool call indexes must be contiguous from zero")

    seen_ids: set[str] = set()
    tool_calls: list[ProviderToolCall] = []
    for call_index in indexes:
        builder = builders[call_index]
        call_id = builder["call_id"]
        if not call_id:
            # Task 3: require provider-stable IDs; synthesis is Task 4/adapter.
            raise ValueError("tool call missing call_id")
        if call_id in seen_ids:
            raise ValueError(f"duplicate tool call_id {call_id!r}")
        seen_ids.add(call_id)

        provider_alias = builder["provider_alias"]
        if not provider_alias:
            raise ValueError("tool call missing provider alias")
        try:
            definition = lookup_tool_by_alias(surface, provider_alias)
        except KeyError as exc:
            raise ValueError(f"unknown provider alias: {provider_alias!r}") from exc

        raw_args = builder["arguments"]
        try:
            parsed = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as exc:
            raise ValueError("tool call arguments are not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("tool call arguments must be a JSON object")

        tool_calls.append(
            ProviderToolCall(
                call_id=call_id,
                call_index=call_index,
                provider_alias=definition.provider_alias,
                domain_key=definition.domain_key,
                arguments=parsed,
                arguments_digest=digest_arguments(parsed),
                binding_contract_digest=definition.binding.ref.binding_contract_digest,
                descriptor_digest=definition.descriptor.descriptor_digest,
                behavior_digest=definition.descriptor.behavior.behavior_digest,
                classification_revision=definition.descriptor.behavior.classification.revision,
                classification_ruleset_digest=(
                    definition.descriptor.behavior.classification.ruleset_digest
                ),
                manifest_revision=surface.manifest_revision,
                manifest_digest=surface.manifest_digest,
                surface_digest=surface.surface_digest,
            )
        )

    # Cross-transcript uniqueness is enforced by validate_provider_transcript later.
    assistant = ProviderAssistantMessage(
        content=content,
        tool_calls=tuple(tool_calls),
    )
    return ProviderRoundResult(
        assistant_message=assistant,
        finish_reason=finish_reason,
        usage=usage,
        compatibility_warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    # Pre-dispatch freshness (dispatcher-side equality is also required by fakes).
    try:
        ports.current_descriptors.require_current(
            binding=definition.binding,
            exposed_descriptor=definition.descriptor,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        error = CapabilityError(
            error_type="version_drift",
            safe_code="classification_changed",
            safe_message="capability classification changed before dispatch",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="classification_changed",
            safe_summary="capability classification changed before dispatch",
        ) from exc

    try:
        authorization = ports.authorization_evidence.issue(
            call=call,
            binding=definition.binding,
            descriptor=definition.descriptor,
            scope=scope,
        )
    except Exception as exc:  # noqa: BLE001
        error = CapabilityError(
            error_type="unauthorized",
            safe_code="authorization_evidence_failed",
            safe_message="authorization evidence factory failed",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="authorization_evidence_failed",
            safe_summary="authorization evidence factory failed",
        ) from exc

    # Reject evidence issued for the wrong scope/call when fields are present.
    if getattr(authorization, "call_id", None) not in {None, call.call_id}:
        error = CapabilityError(
            error_type="unauthorized",
            safe_code="authorization_scope_mismatch",
            safe_message="authorization evidence call_id mismatch",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="authorization_scope_mismatch",
            safe_summary="authorization evidence call_id mismatch",
        )
    principal = getattr(authorization, "principal", None)
    if principal is not None and getattr(principal, "principal_id", None) != scope.principal.principal_id:
        error = CapabilityError(
            error_type="unauthorized",
            safe_code="authorization_scope_mismatch",
            safe_message="authorization evidence principal mismatch",
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
        raise _FatalCapability(
            tool_message=msg,
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
        # Unexpected dispatcher crash is fatal protocol/capability error.
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="dispatcher_error",
            safe_message="tool dispatcher failed",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher failed",
        ) from exc

    if not isinstance(dispatch_result, ProviderDispatchResult):
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="dispatcher_error",
            safe_message="tool dispatcher returned invalid result",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher returned invalid result",
        )

    capability_result = dispatch_result.capability_result
    if not isinstance(capability_result, CapabilityResult):
        error = CapabilityError(
            error_type="protocol_error",
            safe_code="dispatcher_error",
            safe_message="tool dispatcher returned invalid capability result",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="dispatcher_error",
            safe_summary="tool dispatcher returned invalid capability result",
        )

    if capability_result.status == "waiting":
        # Waiting/resume is Task 5+; treat unexpected waiting as protocol error.
        error = CapabilityError(
            error_type="unsupported_interrupt",
            safe_code="unexpected_waiting",
            safe_message="waiting capability results are not supported in this task",
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
        raise _FatalCapability(
            tool_message=msg,
            safe_code="unexpected_waiting",
            safe_summary="waiting capability results are not supported in this task",
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


def _chunk_final_text(text: str, *, size: int = 32) -> list[str]:
    if size < 1:
        raise ValueError("size must be >= 1")
    return [text[i : i + size] for i in range(0, len(text), size)] or [text]


def _emit(ports: ProviderLoopPorts, event_type: str, payload: dict[str, Any]) -> None:
    try:
        ports.events.emit(event_type, payload)  # type: ignore[arg-type]
    except Exception:
        # Event sink failures must never duplicate Provider/Capability work.
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


__all__ = [
    "ProviderLoopError",
    "assemble_provider_round",
    "run_provider_agent_loop",
]
