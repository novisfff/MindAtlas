"""Exact published Workflow capability adapter (Plan 02 Task 5)."""

from __future__ import annotations

import json
import time
from typing import Any, Literal
from uuid import UUID, uuid4

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
    ExecutableWorkflowVersionTarget,
)
from app.assistant.capabilities.safe_execution import (
    make_safe_child_event_forwarder,
    safe_log_exception,
)
from app.assistant.domain.digests import JsonValue, canonical_json_bytes
from app.assistant.skill_catalog.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.assistant.workflow.engine.runtime_dependency_resolver import (
    MAX_CAPABILITY_NESTING_DEPTH,
    WorkflowEngineExecutionScope,
)
from app.assistant_config.service import AssistantConfigService
from app.assistant_config.workflow_contracts import (
    WorkflowContractError,
    workflow_contract_from_input,
)
from app.database import SessionLocal

logger = __import__("logging").getLogger(__name__)


def build_workflow_runtime_definition(
    *,
    workflow_id: UUID,
    version_id: UUID,
    name: str,
    description: str,
    published_input: Any,
) -> SkillDefinition:
    """Build a generic SkillDefinition from an exact published Workflow version.

    Sets ``workflow_version_id`` from the frozen version, never the aggregate
    ``published_version_id``. OpenClaw naming/prompt translation stays outside.
    """
    workflow_nodes = [
        WorkflowNodeDefinition(
            node_id=node.node_id,
            node_type=node.node_type,
            label=node.label,
            position_x=node.position_x,
            position_y=node.position_y,
            config=node.config or {},
        )
        for node in published_input.nodes
    ]
    workflow_edges: list[WorkflowEdgeDefinition] = []
    for edge in published_input.edges:
        condition_expr = None
        if edge.condition_expr is not None:
            condition_expr = ConditionExpression(
                id=edge.condition_expr.id,
                variable=edge.condition_expr.variable,
                operator=edge.condition_expr.operator,
                value=edge.condition_expr.value,
                handle=edge.condition_expr.handle,
            )
        workflow_edges.append(
            WorkflowEdgeDefinition(
                edge_id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                source_handle=edge.source_handle,
                target_handle=edge.target_handle,
                condition_type=edge.condition_type,
                condition_expr=condition_expr,
                label=edge.label,
            )
        )
    tool_names = sorted(AssistantConfigService._collect_workflow_tool_names(workflow_nodes))  # noqa: SLF001
    return SkillDefinition(
        name=f"{name}__capability_workflow",
        description=description or "",
        intent_examples=[],
        tools=tool_names,
        mode="langgraph",
        langgraph_pattern="workflow_dag",
        kb=SkillKBConfig(enabled=False),
        workflow_id=str(workflow_id),
        workflow_version_id=str(version_id),
        workflow_nodes=workflow_nodes,
        workflow_edges=workflow_edges,
    )


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


def _is_openclaw_compat(request: CapabilityAdapterRequest) -> bool:
    owner_kind = getattr(request.decision.owner, "owner_kind", None)
    if owner_kind == "openclaw_catalog":
        return True
    source = str(request.context.request_source or "").strip().lower()
    channel = str(request.context.request_channel or "").strip().lower()
    if source.startswith("openclaw") or channel.startswith("openclaw"):
        return True
    return False


def _resolve_engine_model_credentials(
    resolver: Any,
    *,
    dependencies: tuple[Any, ...],
) -> tuple[str, str, str]:
    """Activate one exact model credential for LangGraphEngine construction.

    Preference order: first root-level model dependency, then any model dependency.
    Workflows with no model dependency (pure start/output/tool) use inert placeholders
    because the engine constructor currently requires credentials even when unused.
    """
    model_deps = [d for d in dependencies if getattr(d, "dependency_type", None) == "model"]
    ordered = sorted(
        model_deps,
        key=lambda d: (
            0 if str(getattr(d, "dependency_path", "")).startswith("root/node:") else 1,
            str(getattr(d, "dependency_path", "")),
        ),
    )
    for dep in ordered:
        locator = str(getattr(dep, "dependency_path", "") or "")
        model_id = getattr(dep, "resolved_model_id", None)
        try:
            activated = resolver.require_model(
                source_locator=locator,
                requested_model_id=model_id,
            )
        except Exception:
            continue
        handle = getattr(activated, "client_or_credential_handle", None)
        if isinstance(handle, dict):
            api_key = str(handle.get("api_key") or "")
            base_url = str(handle.get("base_url") or "https://example.invalid/v1")
            model_name = str(
                handle.get("model_name")
                or getattr(activated, "model_name", None)
                or "capability-placeholder"
            )
            return api_key, base_url, model_name
    return "capability-no-model", "https://example.invalid/v1", "capability-placeholder"


def _is_canonical_text_output_schema(schema: Any) -> bool:
    """Plan 01 text-mode Workflow output contract: object with required response:string."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return False
    props = schema.get("properties")
    if not isinstance(props, dict) or set(props.keys()) != {"response"}:
        return False
    response = props.get("response")
    if not isinstance(response, dict) or response.get("type") != "string":
        return False
    required = schema.get("required")
    return isinstance(required, list) and required == ["response"]


def normalize_workflow_output_value(
    raw: Any,
    *,
    output_schema: Any,
) -> JsonValue:
    """Normalize Workflow engine text into JSON without silent default=str.

    Characterizes current engine behavior: stream join yields plain text for
    text-mode output nodes. When the frozen schema is the Plan 01 canonical
    text-output contract, wrap plain text as ``{"response": text}``. Structured
    JSON is parsed only as a complete document. No OpenClaw-specific mapping.
    """
    if raw is None:
        raise ValueError("empty workflow output")
    if isinstance(raw, (dict, list)):
        return _safe_json_copy(raw)
    if isinstance(raw, (bool, int, float)):
        return _safe_json_copy(raw)
    if not isinstance(raw, str):
        raise TypeError(f"unsupported workflow output type {type(raw)!r}")
    text = raw.strip()
    if not text:
        raise ValueError("empty workflow output")
    root = _schema_root_type(output_schema)
    if root in {"object", "array"}:
        if text.startswith("```"):
            raise ValueError("fenced content is not a complete JSON document")
        try:
            parsed = json.loads(text)
            return _safe_json_copy(parsed)
        except json.JSONDecodeError:
            # Engine text-mode streams plain text; wrap into the locked response envelope.
            if root == "object" and _is_canonical_text_output_schema(output_schema):
                return {"response": text}
            raise ValueError("workflow output is not valid JSON")
    # Text / unconstrained schema: preserve string.
    return text


class WorkflowCapabilityAdapter:
    """Execute one already-resolved exact published Workflow version."""

    capability_type: Literal["workflow"] = "workflow"

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
                    capability_type="workflow",
                    safe_status=safe_status,
                    child_event_type=child_event_type,
                    metadata=meta,
                )
            )

        if descriptor.capability_type != "workflow":
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="capability_type_mismatch",
                safe_message="workflow adapter received non-workflow descriptor",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="failed", metrics=result.metrics)
            return result

        executable = request.target.executable
        if not isinstance(executable, ExecutableWorkflowVersionTarget):
            error = CapabilityError(
                error_type="protocol_error",
                safe_code="executable_type_mismatch",
                safe_message="workflow adapter received non-workflow executable",
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

        # Human-in-the-loop: durable interrupt routes to Plan 07 port when present.
        # Plan 10 Task 4: legacy_blocking is never admitted for new work on the
        # shared capability path (including OpenClaw). Entrypoints without an
        # authenticated durable decision channel classify as unsupported_interrupt
        # and must not fall back to blocking HumanLoopRuntime.
        interrupt_mode = str(descriptor.behavior.interrupt_mode or "none")
        if interrupt_mode == "durable":
            if ports.durable_workflow is not None:
                return ports.durable_workflow.execute(request, ports=ports)
            error = CapabilityError(
                error_type="unsupported_interrupt",
                safe_code="unsupported_interrupt",
                safe_message="durable interrupt is not supported",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="unsupported_interrupt", metrics=result.metrics)
            return result
        if interrupt_mode == "legacy_blocking":
            error = CapabilityError(
                error_type="unsupported_interrupt",
                safe_code="unsupported_interrupt",
                safe_message="legacy blocking interrupt is unavailable",
                retry_disposition="never",
                target_identity=target_identity,
                call_id=call_id,
            )
            result = failed_result(error=error, metrics=_metrics())
            _emit("capability.failed", safe_status="unsupported_interrupt", metrics=result.metrics)
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
            structured = self._invoke_workflow(
                request=request,
                executable=executable,
                ports=ports,
            )
        except CapabilityDomainError as exc:
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            result = failed_result(error=exc.error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result
        except Exception as exc:
            metrics = _metrics(adapter_ms=max(0.0, (time.perf_counter() - invoke_started) * 1000.0))
            error = sanitize_unexpected_exception(
                exc,
                call_id=call_id,
                target_identity=target_identity,
                stage="workflow_invoke",
            )
            result = failed_result(error=error, metrics=metrics)
            _emit("capability.failed", safe_status="failed", metrics=metrics)
            return result

        # Only pure read/compute/none may be rewritten to cancelled after success.
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
                    safe_message="workflow output failed schema validation",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            else:
                error = sanitize_unexpected_exception(
                    exc,
                    call_id=call_id,
                    target_identity=target_identity,
                    stage="workflow_output_validate",
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

    def _invoke_workflow(
        self,
        *,
        request: CapabilityAdapterRequest,
        executable: ExecutableWorkflowVersionTarget,
        ports: CapabilityRuntimePorts,
    ) -> JsonValue:
        from app.assistant.workflow.engine.engine import LangGraphEngine

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

        published_input = executable.parsed_published_input
        if published_input is None:
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="unavailable",
                    safe_code="workflow_input_missing",
                    safe_message="workflow published input missing",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            )

        # Equality evidence only: re-derive contract from exact published input.
        try:
            contract = workflow_contract_from_input(published_input)
        except WorkflowContractError as exc:
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="version_drift",
                    safe_code="workflow_contract_invalid",
                    safe_message="workflow contract invalid",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
            ) from None
        _ = contract  # evidence path; frozen schemas remain authoritative

        skill = build_workflow_runtime_definition(
            workflow_id=executable.workflow_id,
            version_id=executable.version_id,
            name=descriptor.display_name or descriptor.capability_key,
            description=descriptor.description or "",
            published_input=published_input,
        )

        api_key, base_url, model_name = _resolve_engine_model_credentials(
            resolver,
            dependencies=request.target.binding.dependencies,
        )

        scope = WorkflowEngineExecutionScope(
            dependency_resolver=resolver,
            binding_contract_digest=descriptor.binding_contract_digest,
            dependency_closure_digest=descriptor.dependency_closure_digest,
            nesting_depth=int(request.context.nesting_depth or 0),
            safe_diagnostics=True,
            allow_ambient_memory=False,
            allow_global_graph_cache=False,
        )

        # Request-local Session for engine DB needs; never use a shared Session across
        # workers. Closed after success/failure.
        session = SessionLocal()
        try:
            engine = LangGraphEngine(
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                db=session,
                execution_scope=scope,
            )

            callbacks = make_safe_child_event_forwarder(
                emit=ports.events.emit,
                call_id=call_id,
                capability_key=descriptor.capability_key,
                target_identity=target_identity,
                binding_contract_digest=descriptor.binding_contract_digest,
                dependency_closure_digest=descriptor.dependency_closure_digest,
            )

            input_mode = str(getattr(contract, "input_mode", "text") or "text").strip().lower()
            validated = dict(request.validated_input or {})
            if input_mode == "structured":
                user_input = ""
                structured_input: dict[str, Any] | None = validated
            else:
                # Plan 01 canonical text envelope: {"user_input": "..."}.
                user_input = str(validated.get("user_input", "") or "")
                structured_input = None

            run_id = request.context.run_id or uuid4()
            conversation_id = request.context.conversation_id
            runtime_context: dict[str, Any] = {
                "stream_output": False,
                "run_id": str(run_id),
                "channel_type": "capability_runtime",
                "workflow_id": str(executable.workflow_id),
                "workflow_version_id": str(executable.version_id),
                "locale": request.context.locale,
                "request_source": request.context.request_source,
                "request_channel": request.context.request_channel,
                "request_session": request.context.request_session,
                "request_tool": request.context.request_tool,
                "capability_call_id": call_id,
                "capability_version_id": str(executable.version_id),
                "capability_snapshot_digest": executable.snapshot_digest,
                "binding_contract_digest": descriptor.binding_contract_digest,
                "dependency_closure_digest": descriptor.dependency_closure_digest,
            }
            if conversation_id is not None:
                runtime_context["conversation_id"] = str(conversation_id)
            if structured_input is not None:
                runtime_context["structured_input"] = structured_input

            # legacy_blocking is rejected above; keep a fail-closed pin so a
            # future classification slip cannot re-enable HumanLoopRuntime here.
            if str(descriptor.behavior.interrupt_mode or "none") == "legacy_blocking":
                error = CapabilityError(
                    error_type="unsupported_interrupt",
                    safe_code="unsupported_interrupt",
                    safe_message="legacy blocking interrupt is unavailable",
                    retry_disposition="never",
                    target_identity=target_identity,
                    call_id=call_id,
                )
                result = failed_result(error=error, metrics=_metrics())
                _emit(
                    "capability.failed",
                    safe_status="unsupported_interrupt",
                    metrics=result.metrics,
                )
                return result

            chunks: list[str] = []
            try:
                for chunk in engine.execute(
                    skill=skill,
                    user_input=user_input,
                    history=[],
                    runtime_context=runtime_context,
                    on_node_start=callbacks["on_node_start"],
                    on_node_end=callbacks["on_node_end"],
                    on_tool_call_start=callbacks["on_tool_call_start"],
                    on_tool_call_end=callbacks["on_tool_call_end"],
                    on_human_approval_requested=callbacks["on_human_approval_requested"],
                    on_human_approval_resolved=callbacks["on_human_approval_resolved"],
                    cancel_checker=ports.cancellation.is_cancelled,
                ):
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
                    if chunk is not None:
                        chunks.append(str(chunk))
            except CapabilityDomainError:
                raise
            except Exception as exc:
                safe_log_exception(
                    stage="workflow_engine",
                    call_id=call_id,
                    target_identity=target_identity,
                    exc=exc,
                )
                # Map known cancellation-ish failures.
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
                        stage="workflow_engine",
                    )
                ) from None

            raw_output = "".join(chunks)
            try:
                return normalize_workflow_output_value(
                    raw_output,
                    output_schema=descriptor.output_schema,
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                raise CapabilityDomainError(
                    CapabilityError(
                        error_type="invalid_output",
                        safe_code="invalid_output",
                        safe_message="workflow output is not valid JSON for the binding schema",
                        retry_disposition="never",
                        target_identity=target_identity,
                        call_id=call_id,
                    )
                ) from None
        finally:
            session.close()


__all__ = [
    "WorkflowCapabilityAdapter",
    "build_workflow_runtime_definition",
    "normalize_workflow_output_value",
]
