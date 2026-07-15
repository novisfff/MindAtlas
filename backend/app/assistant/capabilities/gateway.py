"""Capability Gateway — single production dispatch path (Plan 02 Task 7).

Locked order:
  cancel -> resolve exact root/closure -> availability (via descriptor)
  -> input schema -> policy -> cancel -> atomically consume one dispatch permit
  -> exactly one adapter -> adapter-local credential/model activation
  -> output schema -> cancel cooperative -> one terminal result
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any, Literal

from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityEventMetadata,
    CapabilityExecutionRequest,
    CapabilityMetrics,
    CapabilityResult,
    CapabilityRuntimeEvent,
    FrozenCapabilityBinding,
    cancelled_result,
    failed_result,
)
from app.assistant.capabilities.errors import (
    CapabilityDomainError,
    CapabilitySchemaValidationError,
    sanitize_unexpected_exception,
)
from app.assistant.capabilities.json_schema import (
    compile_binding_schema,
    validate_json_value,
)
from app.assistant.capabilities.policy import CapabilityPolicyEngine
from app.assistant.capabilities.ports import (
    CapabilityAdapterRequest,
    CapabilityCallFramePort,
    CapabilityDispatchGuard,
    CapabilityRuntimePorts,
    NoOpCapabilityCallFramePort,
    NoOpCapabilityDispatchGuard,
    ResolvedCapabilityTarget,
)
from app.assistant.capabilities.registry import CapabilityRegistry
from app.assistant.domain.digests import JsonValue, canonical_json_bytes, sha256_canonical_json

logger = logging.getLogger(__name__)

CapabilityType = Literal["tool", "workflow", "agent"]

_NOOP_DISPATCH_GUARD: CapabilityDispatchGuard = NoOpCapabilityDispatchGuard()


def _json_byte_size(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(canonical_json_bytes(value))  # type: ignore[arg-type]
    except Exception:
        return 0


def _schema_root_is_object(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    raw = schema.get("type")
    if isinstance(raw, str):
        return raw == "object"
    if isinstance(raw, list):
        return "object" in raw
    return False


def _resolve_dispatch_guard(ports: CapabilityRuntimePorts | Any) -> CapabilityDispatchGuard:
    """Resolve optional dispatch_guard; tolerate SimpleNamespace/legacy ports."""
    guard = getattr(ports, "dispatch_guard", None)
    if guard is None:
        return _NOOP_DISPATCH_GUARD
    return guard  # type: ignore[return-value]


_NOOP_CALL_FRAMES: CapabilityCallFramePort = NoOpCapabilityCallFramePort()


def _resolve_call_frames(ports: CapabilityRuntimePorts | Any) -> CapabilityCallFramePort:
    """Resolve optional call_frames; tolerate SimpleNamespace/legacy ports."""
    port = getattr(ports, "call_frames", None)
    if port is None:
        return _NOOP_CALL_FRAMES
    return port  # type: ignore[return-value]


def _frame_limits(ports: CapabilityRuntimePorts | Any) -> tuple[int, int]:
    """Optional max depths on ports (Main Agent injects); else Plan 05 defaults."""
    from app.assistant.policy.recursion import (
        DEFAULT_MAX_AGENT_DEPTH,
        DEFAULT_MAX_CAPABILITY_DEPTH,
    )

    max_cap = getattr(ports, "max_capability_depth", None)
    max_agent = getattr(ports, "max_agent_depth", None)
    if max_cap is None:
        max_cap = DEFAULT_MAX_CAPABILITY_DEPTH
    if max_agent is None:
        max_agent = DEFAULT_MAX_AGENT_DEPTH
    return int(max_cap), int(max_agent)


def _owner_fields_for_frame(request: CapabilityExecutionRequest) -> tuple[str, Any]:
    """Map authorization owner into frame owner_kind / owner_version_id."""
    from uuid import UUID

    owner = request.authorization.owner
    kind = owner.owner_kind
    if kind == "main_agent":
        frame_kind = "main_agent"
    elif kind == "skill_version":
        frame_kind = "skill_version"
    else:
        # test/system/openclaw_catalog → treat as main_agent for frame accounting
        frame_kind = "main_agent"
    version_id = owner.owner_version_id
    if version_id is None:
        version_id = UUID(int=0)
    return frame_kind, version_id


def _domain_key_for_frame(
    request: CapabilityExecutionRequest,
    *,
    capability_key: str,
) -> str:
    tool = request.context.request_tool
    if isinstance(tool, str) and tool.strip():
        return tool
    return capability_key


class CapabilityGateway:
    """Request/session-scoped gateway. Does not decrypt credentials or build clients."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        policy: CapabilityPolicyEngine,
        adapters: Mapping[CapabilityType, Any],
    ) -> None:
        # Exactly one adapter per capability type; fail closed on missing/duplicate.
        required: set[CapabilityType] = {"tool", "workflow", "agent"}
        provided = set(adapters.keys())
        if provided != required:
            missing = sorted(required - provided)
            extra = sorted(provided - required)
            raise ValueError(
                f"adapter registry must map tool/workflow/agent exactly; "
                f"missing={missing} extra={extra}"
            )
        # Detect accidental multi-registration of the same type via non-unique values
        # is not an error; duplicate keys cannot exist in a Mapping. Construction
        # callers must not pass two adapters under different aliases.
        self._registry = registry
        self._policy = policy
        self._adapters: dict[CapabilityType, Any] = {
            "tool": adapters["tool"],
            "workflow": adapters["workflow"],
            "agent": adapters["agent"],
        }
        for cap_type, adapter in self._adapters.items():
            declared = getattr(adapter, "capability_type", None)
            if declared is not None and declared != cap_type:
                raise ValueError(
                    f"adapter for {cap_type} declares capability_type={declared!r}"
                )

    def describe(self, binding: FrozenCapabilityBinding) -> CapabilityDescriptor:
        """Internal preflight/Plan 03 surface. Does not authorize or execute."""
        return self._registry.describe(binding)

    def execute(
        self,
        request: CapabilityExecutionRequest,
        *,
        ports: CapabilityRuntimePorts,
    ) -> CapabilityResult:
        started = time.perf_counter()
        call_id = request.context.call_id
        target_identity: str | None = request.binding.resolved.target_identity
        capability_key: str = request.binding.resolved.capability_key
        capability_type: CapabilityType = request.binding.resolved.capability_type  # type: ignore[assignment]
        input_bytes = _json_byte_size(request.input)
        adapter_started = False
        budget_started = False
        reserved_active = True  # assume reserved until release/start; no-op is fine
        resolved: ResolvedCapabilityTarget | None = None
        terminal_emitted = False
        dispatch_guard = _resolve_dispatch_guard(ports)
        call_frames = _resolve_call_frames(ports)

        def metrics(
            *,
            output: Any | None = None,
            adapter_ms: float | None = None,
        ) -> CapabilityMetrics:
            duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            return CapabilityMetrics(
                duration_ms=duration_ms,
                adapter_duration_ms=adapter_ms,
                input_bytes=input_bytes,
                output_bytes=_json_byte_size(output),
            )

        def emit_safe(
            event_type: str,
            *,
            safe_status: str | None = None,
            result_metrics: CapabilityMetrics | None = None,
            descriptor: CapabilityDescriptor | None = None,
        ) -> None:
            nonlocal terminal_emitted
            try:
                meta = CapabilityEventMetadata(
                    binding_contract_digest=(
                        descriptor.binding_contract_digest
                        if descriptor is not None
                        else request.binding.resolved.binding_contract_digest
                    ),
                    dependency_closure_digest=(
                        descriptor.dependency_closure_digest
                        if descriptor is not None
                        else request.binding.resolved.dependency_closure_digest
                    ),
                    duration_ms=None if result_metrics is None else result_metrics.duration_ms,
                    adapter_duration_ms=(
                        None if result_metrics is None else result_metrics.adapter_duration_ms
                    ),
                    input_bytes=input_bytes if result_metrics is not None else None,
                    output_bytes=None if result_metrics is None else result_metrics.output_bytes,
                )
                ports.events.emit(
                    CapabilityRuntimeEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        call_id=call_id,
                        capability_key=(
                            descriptor.capability_key if descriptor is not None else capability_key
                        ),
                        target_identity=(
                            descriptor.target_identity
                            if descriptor is not None
                            else target_identity or "unknown"
                        ),
                        capability_type=(
                            descriptor.capability_type
                            if descriptor is not None
                            else capability_type
                        ),
                        safe_status=safe_status,
                        metadata=meta,
                    )
                )
                if event_type in {
                    "capability.completed",
                    "capability.failed",
                    "capability.cancelled",
                }:
                    terminal_emitted = True
            except Exception as exc:
                # Event sink failure is contained; never causes duplicate dispatch.
                sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="event_sink",
                )

        def release_if_unstarted(reason_code: str) -> None:
            nonlocal reserved_active
            if not reserved_active or budget_started:
                return
            try:
                dispatch_guard.release_unstarted(call_id=call_id, reason_code=reason_code)
            except Exception as exc:
                sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="dispatch_guard_release",
                )
            reserved_active = False

        def finish_if_started(status: str) -> None:
            nonlocal budget_started
            if not budget_started:
                return
            try:
                dispatch_guard.finish(call_id=call_id, status=status)
            except Exception as exc:
                sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="dispatch_guard_finish",
                )
            budget_started = False

        def fail(
            error: CapabilityError,
            *,
            descriptor: CapabilityDescriptor | None = None,
            output: Any | None = None,
            adapter_ms: float | None = None,
            reason_code: str | None = None,
        ) -> CapabilityResult:
            if budget_started:
                finish_if_started(error.error_type)
            else:
                release_if_unstarted(reason_code or error.safe_code or error.error_type)
            result = failed_result(error=error, metrics=metrics(output=output, adapter_ms=adapter_ms))
            if not terminal_emitted:
                emit_safe(
                    "capability.failed",
                    safe_status=error.error_type,
                    result_metrics=result.metrics,
                    descriptor=descriptor,
                )
            return result

        def cancel_result(
            *,
            descriptor: CapabilityDescriptor | None = None,
            adapter_ms: float | None = None,
            reason_code: str = "cancelled",
        ) -> CapabilityResult:
            if budget_started:
                finish_if_started("cancelled")
            else:
                release_if_unstarted(reason_code)
            result = cancelled_result(
                metrics=metrics(adapter_ms=adapter_ms),
                call_id=call_id,
                target_identity=(
                    descriptor.target_identity if descriptor is not None else target_identity
                ),
            )
            if not terminal_emitted:
                emit_safe(
                    "capability.cancelled",
                    safe_status="cancelled",
                    result_metrics=result.metrics,
                    descriptor=descriptor,
                )
            return result

        try:
            # 1) cancel before resolution
            if ports.cancellation.is_cancelled():
                return cancel_result(reason_code="cancelled_before_start")

            # 2) resolve exact root/closure (binding digests verified on construction)
            try:
                resolved = self._registry.resolve(request.binding)
            except CapabilityDomainError as exc:
                return fail(exc.error, reason_code=exc.error.safe_code)
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="resolve",
                )
                return fail(error, reason_code=error.safe_code)

            descriptor = resolved.descriptor
            target_identity = descriptor.target_identity
            capability_key = descriptor.capability_key
            capability_type = descriptor.capability_type  # type: ignore[assignment]

            try:
                emit_safe(
                    "capability.resolved",
                    safe_status=descriptor.availability.status,
                    descriptor=descriptor,
                )
            except Exception:
                pass

            # Availability is also enforced in policy; fail early for clear semantics.
            if descriptor.availability.status != "available":
                status = descriptor.availability.status
                error_type = (
                    "version_drift"
                    if status == "version_drift"
                    else "unavailable"
                )
                return fail(
                    CapabilityError(
                        error_type=error_type,  # type: ignore[arg-type]
                        safe_code=descriptor.availability.reason_code or status,
                        safe_message="capability target is not available",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code=descriptor.availability.reason_code or status,
                )

            # 3) input schema validation
            try:
                compiled_input = compile_binding_schema(
                    descriptor.input_schema,  # type: ignore[arg-type]
                    expected_digest=descriptor.input_schema_digest,
                    require_object_root=_schema_root_is_object(descriptor.input_schema),
                )
                validate_json_value(
                    compiled_input,
                    request.input,  # type: ignore[arg-type]
                    label="input",
                )
            except CapabilitySchemaValidationError as exc:
                error = CapabilityError(
                    error_type="invalid_input",
                    safe_code="invalid_input",
                    safe_message=exc.error.safe_message,
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                    validation_issues=exc.error.validation_issues,
                )
                return fail(error, descriptor=descriptor, reason_code="invalid_input")
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="input_schema",
                )
                return fail(error, descriptor=descriptor, reason_code=error.safe_code)

            validated_input: dict[str, JsonValue] = dict(request.input)  # type: ignore[arg-type]

            # 4) policy (needs authoritative side-effect class from resolved descriptor)
            try:
                decision = self._policy.authorize(
                    descriptor=descriptor,
                    evidence=request.authorization,
                    context=request.context,
                )
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="policy",
                )
                return fail(error, descriptor=descriptor, reason_code=error.safe_code)

            try:
                emit_safe(
                    "capability.authorized",
                    safe_status="allow" if decision.allowed else decision.reason_code,
                    descriptor=descriptor,
                )
            except Exception:
                pass

            if not decision.allowed:
                return fail(
                    CapabilityError(
                        error_type="unauthorized",
                        safe_code=decision.reason_code,
                        safe_message="capability authorization denied",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code=decision.reason_code,
                )

            # 5) cancel again before permit consume / adapter
            if ports.cancellation.is_cancelled():
                return cancel_result(descriptor=descriptor, reason_code="cancelled_before_start")

            # 6) atomically consume one dispatch permit
            permit = decision.dispatch_permit
            if permit is None:
                return fail(
                    CapabilityError(
                        error_type="unauthorized",
                        safe_code="dispatch_permit_missing",
                        safe_message="capability authorization denied",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code="dispatch_permit_missing",
                )
            try:
                permit.consume(
                    call_id=call_id,
                    descriptor_digest=descriptor.descriptor_digest,
                )
            except PermissionError:
                return fail(
                    CapabilityError(
                        error_type="unauthorized",
                        safe_code="dispatch_permit_consumed",
                        safe_message="capability dispatch permit already used",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code="dispatch_permit_consumed",
                )
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="dispatch_permit",
                )
                return fail(error, descriptor=descriptor, reason_code=error.safe_code)

            # 7) exactly one adapter by descriptor type — no fallback
            adapter = self._adapters.get(capability_type)
            if adapter is None:
                return fail(
                    CapabilityError(
                        error_type="protocol_error",
                        safe_code="adapter_missing",
                        safe_message="no adapter registered for capability type",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code="adapter_missing",
                )

            # 7a) Plan 05: depth/cycle guards before mark_started / reservation start.
            # Uses process-local frame stack; no-op port admits depth=1 always.
            from app.assistant.policy.recursion import (
                build_capability_call_frame,
                compute_next_depths,
                evaluate_recursion_guard,
            )

            max_cap_depth, max_agent_depth = _frame_limits(ports)
            deny_reason = evaluate_recursion_guard(
                call_frames.current(),
                capability_type=capability_type,
                target_identity=target_identity or "",
                target_version_id=descriptor.target_version_id,
                domain_key=_domain_key_for_frame(
                    request, capability_key=capability_key
                ),
                max_capability_depth=max_cap_depth,
                max_agent_depth=max_agent_depth,
            )
            if deny_reason is not None:
                return fail(
                    CapabilityError(
                        error_type="unauthorized",
                        safe_code=deny_reason[:64],
                        safe_message="capability recursion or depth denied",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    ),
                    descriptor=descriptor,
                    reason_code=deny_reason,
                )

            cap_depth, agent_depth = compute_next_depths(
                call_frames.current(),
                capability_type=capability_type,
            )
            owner_kind, owner_version_id = _owner_fields_for_frame(request)
            frame = build_capability_call_frame(
                call_id=call_id,
                capability_type=capability_type,  # type: ignore[arg-type]
                domain_key=_domain_key_for_frame(
                    request, capability_key=capability_key
                ),
                target_identity=target_identity or capability_key,
                target_version_id=descriptor.target_version_id,
                binding_contract_digest=descriptor.binding_contract_digest,
                owner_kind=owner_kind,  # type: ignore[arg-type]
                owner_version_id=owner_version_id,
                capability_depth=cap_depth,
                agent_depth=agent_depth,
            )

            # 7b) Plan 05: mark_started only after all validation/evidence/permit
            # and the final pre-adapter cancellation check (step 5 above).
            # Digest of validated input must match the frozen Provider arguments
            # digest reserved earlier. Do not add another is_cancelled() probe here
            # so Plan 02 cancel-check ordinals remain stable for existing tests.
            # Frame is pushed around the entire started adapter lifecycle so nested
            # Gateway calls see this frame and pop on exception/cancellation.
            validated_arguments_digest = sha256_canonical_json(validated_input)  # type: ignore[arg-type]
            with call_frames.push(frame):
                try:
                    dispatch_guard.mark_started(
                        call_id=call_id,
                        validated_arguments_digest=validated_arguments_digest,
                    )
                except CapabilityDomainError as exc:
                    # mark_started deny releases unstarted reservation inside ledger.
                    reserved_active = False
                    return fail(
                        exc.error,
                        descriptor=descriptor,
                        reason_code=exc.error.safe_code,
                    )
                except Exception as exc:
                    reserved_active = False
                    try:
                        dispatch_guard.release_unstarted(
                            call_id=call_id,
                            reason_code="dispatch_guard_error",
                        )
                    except Exception:
                        pass
                    error = sanitize_unexpected_exception(
                        exc,
                        call_id=call_id,
                        target_identity=target_identity,
                        stage="dispatch_guard_start",
                    )
                    return fail(error, descriptor=descriptor, reason_code=error.safe_code)

                budget_started = True
                reserved_active = False

                adapter_request = CapabilityAdapterRequest(
                    target=resolved,
                    validated_input=validated_input,
                    context=request.context,
                    decision=decision,
                )

                adapter_t0 = time.perf_counter()
                adapter_started = True
                try:
                    adapter_result = adapter.execute(adapter_request, ports=ports)
                except CapabilityDomainError as exc:
                    adapter_ms = max(0.0, (time.perf_counter() - adapter_t0) * 1000.0)
                    return fail(exc.error, descriptor=descriptor, adapter_ms=adapter_ms)
                except Exception as exc:
                    # Preserve already-characterized domain HTTP errors raised by system
                    # tools (e.g. missing entry 40400) for entrypoint bridges.
                    from app.common.exceptions import ApiException

                    if isinstance(exc, ApiException):
                        # Still finish budget accounting for a started call.
                        finish_if_started("execution_failed")
                        raise
                    adapter_ms = max(0.0, (time.perf_counter() - adapter_t0) * 1000.0)
                    error = sanitize_unexpected_exception(
                        exc,
                        call_id=call_id,
                        target_identity=target_identity,
                        stage="adapter",
                    )
                    return fail(error, descriptor=descriptor, adapter_ms=adapter_ms)

                adapter_ms = max(0.0, (time.perf_counter() - adapter_t0) * 1000.0)

                # Keep the remainder of post-adapter processing inside the frame
                # so nested depth accounting stays accurate until this call ends.

            # Adapter cannot replace descriptor/ref identity claims.
            # (Adapter results do not carry capability_key; integrity is enforced
            # by only ever dispatching the resolved target once.)

            # 8) output schema validation even if adapter claims success
            if adapter_result.status == "completed":
                try:
                    compiled_output = compile_binding_schema(
                        descriptor.output_schema,  # type: ignore[arg-type]
                        expected_digest=descriptor.output_schema_digest,
                        require_object_root=_schema_root_is_object(
                            descriptor.output_schema
                        ),
                    )
                    validate_json_value(
                        compiled_output,
                        adapter_result.structured_output,  # type: ignore[arg-type]
                        label="output",
                    )
                except CapabilitySchemaValidationError as exc:
                    error = CapabilityError(
                        error_type="invalid_output",
                        safe_code="invalid_output",
                        safe_message=exc.error.safe_message,
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                        validation_issues=exc.error.validation_issues,
                    )
                    # Do not log raw invalid value.
                    return fail(
                        error,
                        descriptor=descriptor,
                        adapter_ms=adapter_ms,
                    )
                except Exception as exc:
                    error = sanitize_unexpected_exception(
                        exc,
                        call_id=call_id,
                        target_identity=target_identity,
                        stage="output_schema",
                    )
                    return fail(error, descriptor=descriptor, adapter_ms=adapter_ms)

            # 9) cooperative cancel after adapter return (do not claim termination
            # of a side-effecting adapter that already completed).
            if ports.cancellation.is_cancelled() and adapter_result.status not in {
                "completed",
                "failed",
                "waiting",
            }:
                return cancel_result(descriptor=descriptor, adapter_ms=adapter_ms)

            # Recompute byte metrics with canonical JSON (preserve adapter status).
            recomputed = metrics(
                output=adapter_result.structured_output,
                adapter_ms=adapter_ms,
            )
            # Rebuild result with gateway metrics (status/error preserved).
            final = CapabilityResult(
                status=adapter_result.status,
                user_text=adapter_result.user_text,
                structured_output=adapter_result.structured_output,
                artifact_refs=adapter_result.artifact_refs,
                continuation=adapter_result.continuation,
                terminal_output=adapter_result.terminal_output,
                needs_followup=adapter_result.needs_followup,
                error=adapter_result.error,
                metrics=recomputed,
            )

            # Finish budget accounting for every started call (success/fail/cancel/wait).
            finish_if_started(final.status)

            # Terminal event: adapters may have already emitted; only emit if missing.
            if not terminal_emitted:
                if final.status == "completed":
                    emit_safe(
                        "capability.completed",
                        safe_status="completed",
                        result_metrics=final.metrics,
                        descriptor=descriptor,
                    )
                elif final.status == "cancelled":
                    emit_safe(
                        "capability.cancelled",
                        safe_status="cancelled",
                        result_metrics=final.metrics,
                        descriptor=descriptor,
                    )
                elif final.status == "waiting":
                    # waiting is terminal for this call surface.
                    emit_safe(
                        "capability.completed",
                        safe_status="waiting",
                        result_metrics=final.metrics,
                        descriptor=descriptor,
                    )
                else:
                    emit_safe(
                        "capability.failed",
                        safe_status=(
                            final.error.error_type if final.error is not None else "failed"
                        ),
                        result_metrics=final.metrics,
                        descriptor=descriptor,
                    )

            return final

        except CapabilityDomainError as exc:
            return fail(exc.error, reason_code=exc.error.safe_code)
        except Exception as exc:
            from app.common.exceptions import ApiException

            if isinstance(exc, ApiException):
                if budget_started:
                    finish_if_started("execution_failed")
                elif reserved_active:
                    release_if_unstarted("execution_failed")
                raise
            # Map unexpected Exception only — do not catch BaseException.
            error = sanitize_unexpected_exception(
                exc,
                call_id=call_id,
                target_identity=target_identity,
                stage="gateway",
            )
            # If adapter already started, still return non-retryable safe failure.
            _ = adapter_started  # retained for future reconciliation diagnostics
            return fail(error, reason_code=error.safe_code)


__all__ = ["CapabilityGateway"]
