"""Tool capability adapter and secret-safe remote Tool boundary (Plan 02 Task 4)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capabilities.contracts import (
    CapabilityError,
    CapabilityEventMetadata,
    CapabilityMetrics,
    CapabilityResult,
    CapabilityRuntimeEvent,
    cancelled_result,
    completed_result,
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
from app.assistant.capabilities.ports import (
    CapabilityAdapterRequest,
    CapabilityRuntimePorts,
    ExecutableToolTarget,
    MainAgentControlExecutable,
)
from app.assistant.domain.digests import JsonValue, canonical_json_bytes
from app.assistant.workflow.engine.runtime_helpers import wrap_tool_with_db
from app.assistant_config.models import AssistantTool
from app.assistant_config.remote_tool import RemoteTool, RemoteToolRequestError
from app.database import SessionLocal

logger = logging.getLogger(__name__)


def _schema_root_type(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    raw = schema.get("type")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item != "null":
                return item
    return None


def _schema_requires_structured_json(schema: Any) -> bool:
    root = _schema_root_type(schema)
    return root in {"object", "array"}


def _require_object_root(schema: Any) -> bool:
    return _schema_root_type(schema) == "object"


def _json_byte_size(value: JsonValue | dict[str, Any] | None) -> int:
    if value is None:
        return 0
    try:
        return len(canonical_json_bytes(value))  # type: ignore[arg-type]
    except Exception:
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except Exception:
            return 0


def _safe_json_copy(value: Any, *, path: str = "$") -> JsonValue:
    """Copy a narrow JSON value without default=str or silent coercion."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/Inf
            raise TypeError(f"non-finite float at {path}")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string key at {path}")
            out[key] = _safe_json_copy(item, path=f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [
            _safe_json_copy(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"unsupported JSON value type {type(value)!r} at {path}")


def _parse_complete_json_document(text: str) -> JsonValue:
    """Parse only a complete JSON document. No fence/brace scanning."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty json document")
    # Reject fenced / partial content without guessing.
    if raw.startswith("```"):
        raise ValueError("fenced content is not a complete JSON document")
    value = json.loads(raw)
    # json.loads accepts trailing? no — complete document only.
    return _safe_json_copy(value)


def normalize_tool_result_value(
    raw: Any,
    *,
    output_schema: Any,
) -> JsonValue:
    """Normalize Tool return values into JSON without silent stringification.

    - dict/list/scalar/string preserve JSON type.
    - A complete JSON string is parsed only when the authoritative output schema
      requires structured JSON (object/array root).
    - Plain text is never guessed into an object.
    - Non-JSON-serializable values raise TypeError (caller maps to invalid_output).
    """
    if hasattr(raw, "model_dump") and callable(getattr(raw, "model_dump")):
        try:
            dumped = raw.model_dump(mode="json")
        except Exception as exc:
            raise TypeError("model_dump failed") from exc
        return _safe_json_copy(dumped)

    if isinstance(raw, (dict, list, tuple)):
        return _safe_json_copy(raw)
    if raw is None or isinstance(raw, (bool, int, float)):
        return _safe_json_copy(raw)
    if isinstance(raw, str):
        if _schema_requires_structured_json(output_schema):
            return _parse_complete_json_document(raw)
        return raw
    # bytes/set/datetime/SQLAlchemy row/arbitrary object → invalid_output
    raise TypeError(f"non-serializable tool result type {type(raw)!r}")


class ToolCapabilityAdapter:
    """Execute one already-resolved Tool target into a CapabilityResult."""

    capability_type: Literal["tool"] = "tool"

    def execute(
        self,
        request: CapabilityAdapterRequest,
        *,
        ports: CapabilityRuntimePorts,
    ) -> CapabilityResult:
        started = time.perf_counter()
        descriptor = request.target.descriptor
        call_id = request.context.call_id
        target_identity = descriptor.target_identity
        input_bytes = _json_byte_size(request.validated_input)

        def _metrics(*, output: JsonValue | None = None, adapter_ms: float | None = None) -> CapabilityMetrics:
            duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            return CapabilityMetrics(
                duration_ms=duration_ms,
                adapter_duration_ms=adapter_ms if adapter_ms is not None else duration_ms,
                input_bytes=input_bytes,
                output_bytes=_json_byte_size(output),
            )

        def _emit(event_type: str, *, safe_status: str | None = None, metrics: CapabilityMetrics | None = None) -> None:
            metadata = CapabilityEventMetadata(
                binding_contract_digest=descriptor.binding_contract_digest,
                dependency_closure_digest=descriptor.dependency_closure_digest,
                duration_ms=None if metrics is None else metrics.duration_ms,
                adapter_duration_ms=None if metrics is None else metrics.adapter_duration_ms,
                input_bytes=input_bytes if metrics is not None else None,
                output_bytes=None if metrics is None else metrics.output_bytes,
            )
            ports.events.emit(
                CapabilityRuntimeEvent(
                    event_type=event_type,  # type: ignore[arg-type]
                    call_id=call_id,
                    capability_key=descriptor.capability_key,
                    target_identity=target_identity,
                    capability_type="tool",
                    safe_status=safe_status,
                    metadata=metadata,
                )
            )

        # 1) Descriptor / type match the executable target.
        executable = request.target.executable
        if descriptor.capability_type != "tool":
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="capability_type_mismatch",
                safe_message="tool adapter received non-tool descriptor",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        if not isinstance(executable, (ExecutableToolTarget, MainAgentControlExecutable)):
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="executable_type_mismatch",
                safe_message="tool adapter received non-tool executable",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        if executable.target_identity != descriptor.target_identity:
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="target_identity_mismatch",
                safe_message="tool executable identity mismatch",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        # 2) Availability gate — disabled/drifted never invokes.
        availability = descriptor.availability
        if availability.status != "available":
            error_type = (
                "version_drift"
                if availability.status == "version_drift"
                else "unavailable"
                if availability.status in {"disabled", "missing", "unsupported"}
                else "unavailable"
            )
            error = CapabilityError(
                error_type=error_type,  # type: ignore[arg-type]
                safe_code=availability.reason_code or availability.status,
                safe_message="capability target is not available",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status=availability.status, metrics=result.metrics)
            return result

        # 3) Cancellation before invocation.
        if ports.cancellation.is_cancelled():
            result = cancelled_result(
                metrics=_metrics(),
                call_id=call_id,
                target_identity=target_identity,
            )
            _emit("capability.cancelled", safe_status="cancelled", metrics=result.metrics)
            return result

        _emit("capability.started", safe_status="started")
        invoke_started = time.perf_counter()

        # Main Agent controls return a full CapabilityResult from the control port.
        if isinstance(executable, MainAgentControlExecutable):
            try:
                control_result = executable.control_port.execute(
                    call_id=call_id,
                    capability_key=executable.capability_key,
                    validated_input=dict(request.validated_input),
                )
            except CapabilityDomainError as exc:
                metrics = _metrics(
                    adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0)
                )
                result = failed_result(error=exc.error, metrics=metrics)
                _emit("capability.failed", safe_status="failed", metrics=metrics)
                return result
            except Exception as exc:
                metrics = _metrics(
                    adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0)
                )
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="main_agent_control_invoke",
                )
                result = failed_result(error=error, metrics=metrics)
                _emit("capability.failed", safe_status="failed", metrics=metrics)
                return result
            if not isinstance(control_result, CapabilityResult):
                metrics = _metrics(
                    adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0)
                )
                error = CapabilityError(
                    error_type="protocol_error",
                    safe_code="control_result_invalid",
                    safe_message="main agent control returned invalid result",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
                result = failed_result(error=error, metrics=metrics)
                _emit("capability.failed", safe_status="failed", metrics=metrics)
                return result
            # Attach adapter duration metrics if the control left them empty-ish.
            if control_result.metrics is None or control_result.metrics.adapter_duration_ms is None:
                adapter_ms = max(0.0, (time.perf_counter() - invoke_started) * 1000.0)
                metrics = _metrics(
                    output=control_result.structured_output,
                    adapter_ms=adapter_ms,
                )
                control_result = control_result.model_copy(update={"metrics": metrics})
            status = control_result.status
            if status == "completed":
                _emit("capability.completed", safe_status="completed", metrics=control_result.metrics)
            elif status == "cancelled":
                _emit("capability.cancelled", safe_status="cancelled", metrics=control_result.metrics)
            else:
                _emit("capability.failed", safe_status=status, metrics=control_result.metrics)
            return control_result

        try:
            raw_result = self._invoke_tool(
                request=request,
                executable=executable,
            )
        except CapabilityDomainError as exc:
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            result = failed_result(error=exc.error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result
        except Exception as exc:  # unexpected — never leak str(exc)
            # Preserve already-characterized domain HTTP errors (e.g. missing entry 40400)
            # raised by system tools so entrypoint bridges can keep public envelopes.
            from app.common.exceptions import ApiException

            if isinstance(exc, ApiException):
                raise
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            error = sanitize_unexpected_exception(
                exc,
                call_id=call_id,
                target_identity=target_identity,
                stage="tool_invoke",
            )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        # Cooperative cancellation after pure/read/compute invocation only.
        # Side-effecting success (write/draft/control/unknown) must not be rewritten
        # to cancelled — the effect already landed.
        side_effect = str(getattr(descriptor.behavior, "side_effect", "") or "")
        if ports.cancellation.is_cancelled() and side_effect in {"read", "compute", "none"}:
            result = cancelled_result(
                metrics=_metrics(),
                call_id=call_id,
                target_identity=target_identity,
            )
            _emit("capability.cancelled", safe_status="cancelled", metrics=result.metrics)
            return result

        try:
            structured = normalize_tool_result_value(
                raw_result,
                output_schema=descriptor.output_schema,
            )
            # Plan 01 system-tool binding schemas often omit null unions while tools
            # still emit JSON null for optional fields. Drop nulls so object-root
            # validation matches legacy OpenClaw acceptance of optional blanks.
            if isinstance(structured, dict):
                structured = {
                    key: value for key, value in structured.items() if value is not None
                }
        except (TypeError, ValueError, json.JSONDecodeError):
            metrics = _metrics()
            error = CapabilityError(
                error_type="invalid_output",
                safe_code="invalid_output",
                safe_message="tool output is not valid JSON for the binding schema",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        # Optional pre-validation against frozen binding schema (Gateway is authoritative).
        try:
            compiled = compile_binding_schema(
                descriptor.output_schema,  # type: ignore[arg-type]
                expected_digest=descriptor.output_schema_digest,
                require_object_root=_require_object_root(descriptor.output_schema),
            )
            validate_json_value(compiled, structured, label="output")
        except CapabilitySchemaValidationError as exc:
            metrics = _metrics(output=None)
            result = failed_result(error=exc.error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result
        except Exception as exc:
            metrics = _metrics()
            error = sanitize_unexpected_exception(
                exc,
                call_id=call_id,
                target_identity=target_identity,
                stage="tool_output_validate",
            )
            # Schema digest / compile failures are treated as invalid_output, not leaks.
            if isinstance(exc, (ValueError, TypeError)):
                error = CapabilityError(
                    error_type="invalid_output",
                    safe_code="invalid_output",
                    safe_message="tool output failed schema validation",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        user_text: str | None = structured if isinstance(structured, str) else None
        metrics = _metrics(output=structured)
        result = completed_result(
            user_text=user_text,
            structured_output=structured,
            metrics=metrics,
            terminal_output=bool(descriptor.completion.terminal_output),
            needs_followup=bool(descriptor.completion.needs_followup),
        )
        _emit("capability.completed", safe_status="completed", metrics=metrics)
        return result

    def _invoke_tool(
        self,
        *,
        request: CapabilityAdapterRequest,
        executable: ExecutableToolTarget,
    ) -> Any:
        tool_obj = executable.tool_object_or_record
        if tool_obj is None:
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="protocol_error",
                    safe_code="missing_tool_executable",
                    safe_message="tool executable is missing",
                    retry_disposition="never",
                    target_identity=executable.target_identity,
                    call_id=request.context.call_id,
                )
            )

        # Hard rule: never ToolRegistry.resolve(name) from the adapter.
        if executable.is_system:
            return self._invoke_system_tool(tool_obj, request.validated_input)

        if isinstance(tool_obj, RemoteTool):
            return self._invoke_remote_tool(
                request=request,
                executable=executable,
                remote=tool_obj,
            )

        # Fallback: treat as code-native tool object already resolved.
        return self._invoke_system_tool(tool_obj, request.validated_input)

    def _invoke_system_tool(self, tool_obj: Any, validated_input: dict[str, JsonValue]) -> Any:
        # Fresh DB context via wrap_tool_with_db; closed after success/failure.
        bind = SessionLocal.kw.get("bind") if hasattr(SessionLocal, "kw") else None
        if bind is None:
            # SessionLocal is a sessionmaker bound to engine.
            bind = getattr(SessionLocal, "bind", None)
        if bind is None:
            from app.database import engine as db_engine

            bind = db_engine
        runner = wrap_tool_with_db(tool_obj, bind)
        return runner(**dict(validated_input))

    def _invoke_remote_tool(
        self,
        *,
        request: CapabilityAdapterRequest,
        executable: ExecutableToolTarget,
        remote: RemoteTool,
    ) -> str:
        # Recheck config/credential-slot revision after policy and before decrypt.
        rechecked = self._recheck_remote_tool_revision(executable)
        if rechecked is not None:
            remote = rechecked

        try:
            # RemoteTool.invoke decrypts only while constructing the request.
            return remote.invoke(dict(request.validated_input))
        except RemoteToolRequestError as exc:
            error_type: str
            safe_code: str
            safe_message: str
            if exc.category == "timeout" or exc.is_timeout:
                error_type = "timeout"
                safe_code = "remote_timeout"
                safe_message = "remote tool request timed out"
            elif exc.category == "ssrf":
                error_type = "execution_failed"
                safe_code = "remote_endpoint_rejected"
                safe_message = "remote tool endpoint rejected"
            elif exc.category == "http":
                error_type = "execution_failed"
                safe_code = "remote_http_error"
                safe_message = "remote tool HTTP error"
            elif exc.category == "connection" or exc.is_connection:
                error_type = "execution_failed"
                safe_code = "remote_connection_failed"
                safe_message = "remote tool connection failed"
            elif exc.category == "config":
                error_type = "unavailable"
                safe_code = "remote_tool_config_invalid"
                safe_message = "remote tool configuration invalid"
            else:
                error_type = "execution_failed"
                safe_code = "remote_request_failed"
                safe_message = "remote tool request failed"
            raise CapabilityDomainError(
                CapabilityError(
                    error_type=error_type,  # type: ignore[arg-type]
                    safe_code=safe_code,
                    safe_message=safe_message,
                    retry_disposition="never",
                    target_identity=executable.target_identity,
                    call_id=request.context.call_id,
                )
            ) from None

    def _recheck_remote_tool_revision(
        self,
        executable: ExecutableToolTarget,
    ) -> RemoteTool | None:
        """Recheck config_revision before decrypt; fail without network I/O on drift.

        Returns a fresh RemoteTool built from the exact rechecked row when a
        tool_id is known; otherwise returns None and the caller uses the
        already-resolved executable object.
        """
        tool_id = executable.tool_id
        expected_rev = executable.config_revision
        if tool_id is None:
            return None

        session: Session = SessionLocal()
        try:
            row = (
                session.query(AssistantTool)
                .filter(AssistantTool.id == tool_id)
                .one_or_none()
            )
            if row is None:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="not_found",
                        safe_code="remote_tool_missing",
                        safe_message="remote tool missing",
                        retry_disposition="never",
                        target_identity=executable.target_identity,
                    )
                )
            current_rev = int(row.config_revision or 1)
            if expected_rev is not None and int(expected_rev) != current_rev:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="version_drift",
                        safe_code="config_revision_drift",
                        safe_message="remote tool config revision drift",
                        retry_disposition="never",
                        target_identity=executable.target_identity,
                    )
                )
            if not bool(row.enabled):
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="unavailable",
                        safe_code="tool_disabled",
                        safe_message="remote tool is disabled",
                        retry_disposition="never",
                        target_identity=executable.target_identity,
                    )
                )
            # Build from the exact rechecked row (no ToolRegistry.resolve).
            return RemoteTool.from_model(row)
        finally:
            session.close()


__all__ = [
    "ToolCapabilityAdapter",
    "normalize_tool_result_value",
]
