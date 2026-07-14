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
    CapabilityRuntimePorts,
    ResolvedCapabilityTarget,
)
from app.assistant.capabilities.registry import CapabilityRegistry
from app.assistant.domain.digests import JsonValue, canonical_json_bytes

logger = logging.getLogger(__name__)

CapabilityType = Literal["tool", "workflow", "agent"]


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
        resolved: ResolvedCapabilityTarget | None = None
        terminal_emitted = False

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

        def fail(
            error: CapabilityError,
            *,
            descriptor: CapabilityDescriptor | None = None,
            output: Any | None = None,
            adapter_ms: float | None = None,
        ) -> CapabilityResult:
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
        ) -> CapabilityResult:
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
                return cancel_result()

            # 2) resolve exact root/closure (binding digests verified on construction)
            try:
                resolved = self._registry.resolve(request.binding)
            except CapabilityDomainError as exc:
                return fail(exc.error)
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="resolve",
                )
                return fail(error)

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
                return fail(error, descriptor=descriptor)
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="input_schema",
                )
                return fail(error, descriptor=descriptor)

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
                return fail(error, descriptor=descriptor)

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
                )

            # 5) cancel again before permit consume / adapter
            if ports.cancellation.is_cancelled():
                return cancel_result(descriptor=descriptor)

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
                )
            except Exception as exc:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="dispatch_permit",
                )
                return fail(error, descriptor=descriptor)

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
                )

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
            return fail(exc.error)
        except Exception as exc:
            from app.common.exceptions import ApiException

            if isinstance(exc, ApiException):
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
            return fail(error)


__all__ = ["CapabilityGateway"]
