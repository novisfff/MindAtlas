"""Exact published Agent capability adapter (Plan 02 Task 6).

Consumes one exact published Agent version, its binding-level callable Schema, and
its frozen model/Tool/KB dependency closure. Invokes the current agent engine via
``run_agent_execution`` with pre-bound tools — never name-based ``_build_tools``
or ``ToolRegistry.resolve`` / ``resolve_openai_compat_config`` after the closure
is built.

OpenClaw localization and response-shaping remain in the OpenClaw bridge.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any, Callable, Literal
from uuid import UUID

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
    ExecutableAgentVersionTarget,
    ExactRuntimeDependencyResolver,
)
from app.assistant.capabilities.safe_execution import (
    make_safe_child_event_forwarder,
    safe_log_exception,
)
from app.assistant.domain.digests import JsonValue, canonical_json_bytes
from app.assistant.openai_compat import build_openai_compat_client_headers
from app.assistant.skill_catalog.base import SkillDefinition, SkillKBConfig
from app.assistant.workflow import execution_copy as _copy
from app.assistant.workflow.engine.agent_execution_core import (
    AgentExecutionHooks,
    AgentExecutionRequest,
    AgentExecutionResult,
    build_internal_kb_tool,
    run_agent_execution,
)
from app.assistant.workflow.engine.runtime_dependency_resolver import (
    MAX_CAPABILITY_NESTING_DEPTH,
)
from app.assistant.workflow.engine.runtime_helpers import (
    AGENT_MAX_ITERATIONS,
    wrap_tool_with_db,
)
from app.database import SessionLocal

logger = __import__("logging").getLogger(__name__)


def build_chat_openai_client(
    *,
    api_key: str,
    base_url: str,
    model: str,
    streaming: bool = True,
    temperature: float | None = 0,
) -> Any:
    """Build a ChatOpenAI client from already-activated credentials.

    Isolated for tests to monkeypatch without importing Provider runtime resolvers.
    """
    from langchain_openai import ChatOpenAI

    default_headers = build_openai_compat_client_headers()
    kwargs: dict[str, Any] = {
        "api_key": (api_key or "").strip(),
        "base_url": (base_url or "").strip(),
        "model": model,
        "streaming": streaming,
        "default_headers": default_headers,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        return ChatOpenAI(**kwargs)
    except TypeError:
        return ChatOpenAI()


def build_agent_runtime_definition(
    *,
    agent_profile_id: UUID,
    version_id: UUID,
    name: str,
    description: str,
    snapshot: dict[str, Any],
) -> SkillDefinition:
    """Build a generic SkillDefinition from an exact published Agent version snapshot.

    Uses only the frozen snapshot fields. Does not read mutable aggregate
    ``system_prompt`` / ``tools`` / ``kb_config``. OpenClaw prompt wrapping stays
    outside this adapter.
    """
    system_prompt = str(snapshot.get("system_prompt") or "").strip()
    tools_raw = snapshot.get("tools") or []
    tools: list[str] = []
    if isinstance(tools_raw, list):
        for item in tools_raw:
            if isinstance(item, str) and item.strip():
                tools.append(item.strip())
    kb_cfg = snapshot.get("kb_config") if isinstance(snapshot.get("kb_config"), dict) else {}
    kb_enabled = bool(kb_cfg.get("enabled", False))
    model_source_raw = str(snapshot.get("model_source") or "default").strip().lower()
    model_source: Literal["default", "custom"] = (
        "custom" if model_source_raw == "custom" else "default"
    )
    raw_model_id = snapshot.get("model_id")
    model_id = str(raw_model_id) if raw_model_id else None
    if model_source == "default":
        model_id = None
    return SkillDefinition(
        name=f"{name}__capability_agent",
        description=description or "",
        intent_examples=[],
        tools=tools,
        mode="langgraph",
        langgraph_pattern="agent_loop",
        model_source=model_source,
        model_id=model_id,
        system_prompt=system_prompt,
        kb=SkillKBConfig(enabled=kb_enabled),
        workflow_nodes=[],
        workflow_edges=[],
    )


def serialize_agent_user_input(validated_input: dict[str, JsonValue]) -> str:
    """Serialize validated agent input into a single user message string.

    Locked Plan 01 compatibility envelope uses ``{"input": ...}``. String values
    are passed through; non-string values are canonical-JSON encoded. A ``prompt``
    key is accepted only as a compatibility alias when ``input`` is absent (does
    not invent a Schema).
    """
    if not isinstance(validated_input, dict):
        return ""
    if "input" in validated_input:
        value = validated_input["input"]
    elif "prompt" in validated_input:
        value = validated_input["prompt"]
    elif "user_input" in validated_input:
        value = validated_input["user_input"]
    else:
        value = validated_input
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return json.dumps(value, ensure_ascii=False)
    try:
        return canonical_json_bytes(value).decode("utf-8")  # type: ignore[arg-type]
    except Exception:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def _require_object_root(schema: Any) -> bool:
    return _schema_root_type(schema) == "object"


def _is_canonical_agent_text_output_schema(schema: Any) -> bool:
    """Plan 01 agent text-mode contract: object with required text:string."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    props = schema.get("properties")
    if not isinstance(props, dict) or set(props.keys()) != {"text"}:
        return False
    text = props.get("text")
    if not isinstance(text, dict) or text.get("type") != "string":
        return False
    required = schema.get("required")
    return isinstance(required, list) and required == ["text"]


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
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
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
        return [_safe_json_copy(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"unsupported JSON value type {type(value)!r} at {path}")


def normalize_agent_output_value(
    raw: Any,
    *,
    output_schema: Any,
) -> JsonValue:
    """Normalize Agent engine text into JSON without silent default=str.

    Characterizes current engine behavior: final text is a plain string. When the
    frozen schema is the Plan 01 canonical agent text-output contract, wrap plain
    text as ``{"text": ...}``. Structured JSON is parsed only as a complete
    document. No OpenClaw-specific mapping and no brace/fence scanning.
    """
    if raw is None:
        raise ValueError("empty agent output")
    if isinstance(raw, (dict, list)):
        return _safe_json_copy(raw)
    if isinstance(raw, (bool, int, float)):
        return _safe_json_copy(raw)
    if not isinstance(raw, str):
        raise TypeError(f"unsupported agent output type {type(raw)!r}")
    text = raw.strip()
    if not text:
        raise ValueError("empty agent output")
    root = _schema_root_type(output_schema)
    if root in {"object", "array"}:
        if text.startswith("```"):
            raise ValueError("fenced content is not a complete JSON document")
        try:
            parsed = json.loads(text)
            return _safe_json_copy(parsed)
        except json.JSONDecodeError:
            if root == "object" and _is_canonical_agent_text_output_schema(output_schema):
                return {"text": text}
            raise ValueError("agent output is not valid JSON")
    return text


def _snapshot_forbids_nesting(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    for key in ("nested_agent", "restart", "main_agent_restart"):
        if snapshot.get(key):
            return True
    return False


class AgentCapabilityAdapter:
    """Execute one already-resolved exact published Agent version."""

    capability_type: Literal["agent"] = "agent"

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

        def _metrics(
            *,
            output: JsonValue | None = None,
            adapter_ms: float | None = None,
        ) -> CapabilityMetrics:
            duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
            return CapabilityMetrics(
                duration_ms=duration_ms,
                adapter_duration_ms=adapter_ms if adapter_ms is not None else duration_ms,
                input_bytes=input_bytes,
                output_bytes=_json_byte_size(output),
            )

        def _emit(
            event_type: str,
            *,
            safe_status: str | None = None,
            metrics: CapabilityMetrics | None = None,
            child_event_type: str | None = None,
            metadata: CapabilityEventMetadata | None = None,
        ) -> None:
            meta = metadata or CapabilityEventMetadata(
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
                    capability_type="agent",
                    safe_status=safe_status,
                    child_event_type=child_event_type,
                    metadata=meta,
                )
            )

        if descriptor.capability_type != "agent":
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="capability_type_mismatch",
                safe_message="agent adapter received non-agent descriptor",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        executable = request.target.executable
        if not isinstance(executable, ExecutableAgentVersionTarget):
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="executable_type_mismatch",
                safe_message="agent adapter received non-agent executable",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        availability = descriptor.availability
        if availability.status != "available":
            error_type = (
                "version_drift"
                if availability.status == "version_drift"
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

        nesting = int(request.context.nesting_depth or 0)
        if nesting < 0 or nesting > MAX_CAPABILITY_NESTING_DEPTH:
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="nesting_depth_exceeded",
                safe_message="capability nesting depth denied",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        snapshot = executable.parsed_snapshot
        if _snapshot_forbids_nesting(snapshot):
            error = CapabilityError(
                error_type="unavailable",
                safe_code="nested_agent_unavailable",
                safe_message="nested or main-agent restart is unavailable",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

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

        try:
            structured = self._invoke_agent(
                request=request,
                executable=executable,
                ports=ports,
            )
        except CapabilityDomainError as exc:
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            # Map cancelled domain errors to cancelled results for consistent status.
            if exc.error.error_type == "cancelled":
                result = cancelled_result(
                    metrics=metrics,
                    call_id=call_id,
                    target_identity=target_identity,
                )
                _emit("capability.cancelled", safe_status="cancelled", metrics=metrics)
                return result
            result = failed_result(error=exc.error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result
        except Exception as exc:
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            error = sanitize_unexpected_exception(
                exc,
                call_id=call_id,
                target_identity=target_identity,
                stage="agent_invoke",
            )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        # Only pure read/compute/none may be rewritten to cancelled after success.
        # Side-effecting agent work (write/draft/unknown) keeps completed.
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
            if isinstance(exc, (ValueError, TypeError)):
                error = CapabilityError(
                    error_type="invalid_output",
                    safe_code="invalid_output",
                    safe_message="agent output failed schema validation",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            else:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="agent_output_validate",
                )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        user_text: str | None = structured if isinstance(structured, str) else None
        if (
            user_text is None
            and isinstance(structured, dict)
            and _is_canonical_agent_text_output_schema(descriptor.output_schema)
        ):
            text_val = structured.get("text")
            if isinstance(text_val, str):
                user_text = text_val
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

    def _invoke_agent(
        self,
        *,
        request: CapabilityAdapterRequest,
        executable: ExecutableAgentVersionTarget,
        ports: CapabilityRuntimePorts,
    ) -> JsonValue:
        descriptor = request.target.descriptor
        call_id = request.context.call_id
        target_identity = descriptor.target_identity

        # Activate exact dependency closure only after allow decision.
        resolver = request.target.execution_closure.bind_authorized(decision=request.decision)

        if ports.cancellation.is_cancelled():
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="cancelled",
                    safe_code="cancelled",
                    safe_message="cancelled",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        snapshot = executable.parsed_snapshot
        if not isinstance(snapshot, dict):
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="unavailable",
                    safe_code="agent_snapshot_missing",
                    safe_message="agent published snapshot missing",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        skill = build_agent_runtime_definition(
            agent_profile_id=executable.agent_profile_id,
            version_id=executable.version_id,
            name=descriptor.display_name or descriptor.capability_key,
            description=descriptor.description or "",
            snapshot=snapshot,
        )

        # Cancellation again immediately before model credential activation.
        if ports.cancellation.is_cancelled():
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="cancelled",
                    safe_code="cancelled",
                    safe_message="cancelled",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        llm = self._activate_llm(
            resolver=resolver,
            dependencies=request.target.binding.dependencies,
            call_id=call_id,
            target_identity=target_identity,
        )

        if ports.cancellation.is_cancelled():
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="cancelled",
                    safe_code="cancelled",
                    safe_message="cancelled",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        bound_tools, tool_runners, knowledge_mode = self._build_bound_tools_from_closure(
            skill=skill,
            resolver=resolver,
            dependencies=request.target.binding.dependencies,
            call_id=call_id,
            target_identity=target_identity,
            locale=request.context.locale,
        )

        system_prompt = _copy.build_agent_system_prompt(
            locale=request.context.locale,
            skill_name=skill.name,
            skill_description=skill.description,
            tool_names=[str(getattr(t, "name", "") or "") for t in bound_tools if getattr(t, "name", None)],
            current_date=date.today(),
            base_prompt=skill.system_prompt or "",
            kb_enabled=bool(getattr(getattr(skill, "kb", None), "enabled", False)),
        )

        user_text = serialize_agent_user_input(dict(request.validated_input or {}))
        conversation_messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_text},
        ]

        callbacks = make_safe_child_event_forwarder(
            emit=ports.events.emit,
            call_id=call_id,
            capability_key=descriptor.capability_key,
            target_identity=target_identity,
            binding_contract_digest=descriptor.binding_contract_digest,
            dependency_closure_digest=descriptor.dependency_closure_digest,
        )
        # Re-tag child events as agent capability type by wrapping emit is not needed —
        # safe_execution currently hardcodes capability_type=workflow on child events.
        # That is intentional compatibility projection; do not invent a second channel.

        metadata: dict[str, Any] = {
            "on_tool_call_start": callbacks.get("on_tool_call_start"),
            "on_tool_call_end": callbacks.get("on_tool_call_end"),
            "on_content_delta": None,
            "on_node_output_delta": None,
        }

        # Round-boundary cancellation: check before entering the engine loop.
        if ports.cancellation.is_cancelled():
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="cancelled",
                    safe_code="cancelled",
                    safe_message="cancelled",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        try:
            engine_result = run_agent_execution(
                AgentExecutionRequest(
                    llm=llm,
                    system_prompt=system_prompt,
                    conversation_messages=conversation_messages,
                    bound_tools=bound_tools,
                    tool_runners=tool_runners,
                    max_iterations=AGENT_MAX_ITERATIONS,
                    stream_output_enabled=False,
                    execution_hooks=AgentExecutionHooks(
                        metadata=metadata,
                        content_passthrough_enabled=False,
                        node_output_delta_enabled=False,
                    ),
                    trace_context={
                        "node_id": "agent",
                        "node_type": "agent",
                        "agent_profile_id": str(executable.agent_profile_id),
                        "agent_version_id": str(executable.version_id),
                        "capability_call_id": call_id,
                    },
                    knowledge_mode=knowledge_mode,  # type: ignore[arg-type]
                    recent_dialogue_injection="none",
                    locale=request.context.locale,
                )
            )
        except CapabilityDomainError:
            raise
        except Exception as exc:
            safe_log_exception(
                stage="agent_engine",
                call_id=call_id,
                target_identity=target_identity,
                exc=exc,
            )
            if type(exc).__name__ in {"AssistantRunCancelled", "CancelledError"}:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="cancelled",
                        safe_code="cancelled",
                        safe_message="cancelled",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    )
                ) from None
            raise CapabilityDomainError(
                sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="agent_engine",
                )
            ) from None

        # Post-success cancel is handled by execute() with side_effect awareness so
        # write/draft/unknown successes are not rewritten to cancelled.
        return self._normalize_engine_result(
            engine_result,
            output_schema=descriptor.output_schema,
            call_id=call_id,
            target_identity=target_identity,
            locale=request.context.locale,
        )

    def _activate_llm(
        self,
        *,
        resolver: ExactRuntimeDependencyResolver,
        dependencies: tuple[Any, ...],
        call_id: str,
        target_identity: str,
    ) -> Any:
        model_deps = [d for d in dependencies if getattr(d, "dependency_type", None) == "model"]
        # Prefer root/model (agent LLM) over root/kb/model (embedding).
        ordered = sorted(
            model_deps,
            key=lambda d: (
                0 if str(getattr(d, "dependency_path", "")).endswith("/model")
                and "/kb/" not in str(getattr(d, "dependency_path", ""))
                else 1,
                str(getattr(d, "dependency_path", "")),
            ),
        )
        last_error: BaseException | None = None
        for dep in ordered:
            path = str(getattr(dep, "dependency_path", "") or "")
            if "/kb/" in path:
                continue
            model_id = getattr(dep, "resolved_model_id", None)
            try:
                activated = resolver.require_model(
                    source_locator=path,
                    requested_model_id=model_id,
                )
            except CapabilityDomainError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                continue
            handle = getattr(activated, "client_or_credential_handle", None)
            if not isinstance(handle, dict):
                continue
            api_key = str(handle.get("api_key") or "")
            base_url = str(handle.get("base_url") or "https://example.invalid/v1")
            model_name = str(
                handle.get("model_name")
                or getattr(activated, "model_name", None)
                or "capability-placeholder"
            )
            return build_chat_openai_client(
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                streaming=True,
                temperature=0,
            )

        if isinstance(last_error, CapabilityDomainError):
            # Preserve domain error; attach call_id if missing.
            err = last_error.error
            if err.call_id is None:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type=err.error_type,
                        safe_code=err.safe_code,
                        safe_message=err.safe_message,
                        retry_disposition=err.retry_disposition,
                        target_identity=err.target_identity or target_identity,
                        call_id=call_id,
                    )
                ) from None
            raise last_error

        raise CapabilityDomainError(
            CapabilityError(
                error_type="unavailable",
                safe_code="model_unavailable",
                safe_message="agent model dependency unavailable",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
        )

    def _build_bound_tools_from_closure(
        self,
        *,
        skill: SkillDefinition,
        resolver: ExactRuntimeDependencyResolver,
        dependencies: tuple[Any, ...],
        call_id: str,
        target_identity: str,
        locale: str | None,
    ) -> tuple[list[Any], dict[str, Callable[..., Any]], str]:
        """Build bound_tools/tool_runners solely from the exact execution closure."""
        tool_names = list(skill.tools or [])
        kb_enabled = bool(getattr(getattr(skill, "kb", None), "enabled", False))
        if kb_enabled and "kb_search" not in tool_names:
            tool_names = [*tool_names, "kb_search"]

        # Index tool dependency paths from the frozen closure.
        tool_deps = [
            d
            for d in dependencies
            if getattr(d, "dependency_type", None) in {"system_tool", "remote_tool"}
        ]
        path_by_name: dict[str, str] = {}
        for dep in tool_deps:
            path = str(getattr(dep, "dependency_path", "") or "")
            # Paths look like root/tool:{name}
            name = path.rsplit("tool:", 1)[-1] if "tool:" in path else ""
            if name:
                path_by_name[name] = path

        bound_tools: list[Any] = []
        tool_runners: dict[str, Callable[..., Any]] = {}
        knowledge_mode = "none"
        kb_bound = False

        # Resolve DB bind once for wrap_tool_with_db.
        bind = getattr(SessionLocal, "bind", None)
        if bind is None:
            from app.database import engine as db_engine

            bind = db_engine

        for name in tool_names:
            locator = path_by_name.get(name)
            if locator is None:
                # Try common Plan 01 agent paths.
                for candidate in (
                    f"root/tool:{name}",
                    f"root/node:tool_0/tool:{name}",
                ):
                    if any(
                        str(getattr(d, "dependency_path", "")) == candidate for d in tool_deps
                    ):
                        locator = candidate
                        break
                if locator is None and name == "kb_search" and kb_enabled:
                    # Plan 01 freezes only root/kb/model for KB-enabled agents, not
                    # kb_search as a tool dep. Resolve via system export later.
                    continue

            if locator is None:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="not_found",
                        safe_code="undeclared_dependency",
                        safe_message="tool not in frozen closure",
                        retry_disposition="never",
                        target_identity=f"tool:{name}",
                        call_id=call_id,
                    )
                )

            try:
                target = resolver.require_tool(source_locator=locator, tool_name=name)
            except CapabilityDomainError as exc:
                err = exc.error
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type=err.error_type,
                        safe_code=err.safe_code,
                        safe_message=err.safe_message,
                        retry_disposition=err.retry_disposition,
                        target_identity=err.target_identity or f"tool:{name}",
                        call_id=call_id,
                    )
                ) from None

            tool_obj = target.tool_object_or_record
            if tool_obj is None:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="unavailable",
                        safe_code="tool_executable_missing",
                        safe_message="tool executable missing",
                        retry_disposition="never",
                        target_identity=target.target_identity,
                        call_id=call_id,
                    )
                )

            if name == "kb_search":
                if kb_bound or not kb_enabled:
                    continue
                wrapped = wrap_tool_with_db(tool_obj, bind)
                kb_tool, kb_runner = build_internal_kb_tool(
                    base_kb_tool=tool_obj,
                    wrapped_kb_tool=wrapped,
                    description=_copy.build_internal_kb_tool_description(locale),
                )
                bound_tools.append(kb_tool)
                tool_runners["kb_search"] = kb_runner
                kb_bound = True
                knowledge_mode = "skill_kb"
                continue

            bound_tools.append(tool_obj)
            tool_runners[name] = wrap_tool_with_db(tool_obj, bind)

        if kb_enabled and not kb_bound:
            # Attempt final kb_search resolution for agents that only freeze the embedding model.
            try:
                from app.assistant_config.registry import ToolRegistry

                # Hard rule: do not use ToolRegistry.resolve(name) for ordinary tools.
                # kb_search is a process-local system export whose contract set is already
                # revision-gated by APP_BUILD_REVISION; only resolve_system_tool is used.
                base_kb = ToolRegistry.resolve_system_tool("kb_search")
            except Exception:
                base_kb = None
            if base_kb is None:
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="unavailable",
                        safe_code="kb_tool_unavailable",
                        safe_message="kb tool unavailable",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    )
                )
            wrapped = wrap_tool_with_db(base_kb, bind)
            kb_tool, kb_runner = build_internal_kb_tool(
                base_kb_tool=base_kb,
                wrapped_kb_tool=wrapped,
                description=_copy.build_internal_kb_tool_description(locale),
            )
            bound_tools.append(kb_tool)
            tool_runners["kb_search"] = kb_runner
            knowledge_mode = "skill_kb"

        return bound_tools, tool_runners, knowledge_mode

    def _normalize_engine_result(
        self,
        engine_result: AgentExecutionResult,
        *,
        output_schema: Any,
        call_id: str,
        target_identity: str,
        locale: str | None,
    ) -> JsonValue:
        stopped = engine_result.stopped_by
        if stopped in {"tool_error", "invalid_tool"}:
            # Never surface provider/tool exception text.
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="execution_failed",
                    safe_code="agent_tool_failed" if stopped == "tool_error" else "agent_invalid_tool",
                    safe_message="agent tool execution failed",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )
        if stopped == "max_iterations":
            # Preserve current engine characterization: exhausted iterations yield a
            # localized empty-final-text message path in the subgraph, but here we
            # surface a safe execution failure rather than inventing user text.
            final_text = _copy.build_agent_iterations_exhausted_message(locale)
            try:
                return normalize_agent_output_value(final_text, output_schema=output_schema)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="execution_failed",
                        safe_code="agent_max_iterations",
                        safe_message="agent execution exceeded max iterations",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    )
                ) from None

        raw_output = engine_result.final_text
        if raw_output is None or (isinstance(raw_output, str) and not raw_output.strip()):
            # Empty model output is a failure for structured contracts.
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="execution_failed",
                    safe_code="agent_empty_output",
                    safe_message="agent execution produced no model output",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        try:
            return normalize_agent_output_value(raw_output, output_schema=output_schema)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="invalid_output",
                    safe_code="invalid_output",
                    safe_message="agent output is not valid JSON for the binding schema",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            ) from None


__all__ = [
    "AgentCapabilityAdapter",
    "build_agent_runtime_definition",
    "build_chat_openai_client",
    "normalize_agent_output_value",
    "serialize_agent_user_input",
]
