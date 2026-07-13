"""Harmless model capability probe orchestration (Plan 03 Task 7).

Runs a bounded, secret-free sequence against one exact model runtime config and
adapter revision. Produces immutable safe evidence ready for persistence.

Hard rules:
- Fixed local probe Tools only (no business Gateway/Tool/Skill dispatch).
- No database writes, pointer promotion, or live Provider calls from CI.
- Evidence and digests never contain API keys, raw base URL secrets, prompts,
  nonces, stream text, Tool arguments/results, or Provider bodies.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field, field_validator, model_validator

from app.assistant.capabilities.contracts import (
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    project_frozen_capability_binding,
)
from app.assistant.capabilities.json_schema import (
    compile_binding_schema,
    validate_json_value,
)
from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    FrozenContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedRunManifestRevision,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.domain.json_schema import (
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.provider_loop.adapters.openai_chat import (
    ADAPTER_KEY as OPENAI_ADAPTER_KEY,
    compute_openai_chat_model_config_digest,
    secret_free_endpoint_identity,
)
from app.assistant.provider_loop.aliases import (
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (
    CancellationPort,
    ProviderAdapter,
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundResult,
    ProviderToolChoice,
    ProviderToolSurface,
    ProviderUsage,
    SafeProviderError,
    create_execution_scope,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    ProviderUserMessage,
)
from app.assistant.provider_loop.streaming import (
    DefaultFinalizationInstructionProvider,
    assemble_provider_round,
)
from app.assistant.skills.resolution import build_binding_snapshot

logger = logging.getLogger(__name__)

PROBE_CONTRACT_VERSION: Literal[1] = 1

# Fixed local probe Tool Domain Keys and Provider aliases.
PROBE_ECHO_DOMAIN_KEY = "probe.echo"
PROBE_LEFT_DOMAIN_KEY = "probe.left"
PROBE_RIGHT_DOMAIN_KEY = "probe.right"
PROBE_ECHO_ALIAS = "probe_echo"
PROBE_LEFT_ALIAS = "probe_left"
PROBE_RIGHT_ALIAS = "probe_right"

REQUIRED_CAPABILITY_KEYS: tuple[str, ...] = (
    "streaming",
    "tool_calling",
    "json_schema_args",
    "stable_tool_call_ids",
    "multi_tool_calls",
    "tool_result_continuation",
    "tools_disabled_finalization",
)

# Cost / privacy bounds (locked for probe-contract version 1).
DEFAULT_MAX_PROVIDER_REQUESTS = 5
DEFAULT_MAX_OUTPUT_TOKENS = 64
DEFAULT_MAX_AGGREGATE_TOKENS = 512
DEFAULT_MAX_TOOL_RESULT_BYTES = 4 * 1024
DEFAULT_TOTAL_TIMEOUT_SECONDS = 60.0
DEFAULT_NONCE_BYTES = 8
MAX_SAFE_ERROR_SUMMARY_LEN = 200
MAX_SAFE_REASON_CODE_LEN = 64

ProbeObservation = Literal["passed", "failed", "not_observed"]
ProbeStatus = Literal["passed", "partial", "failed"]


class ProbeError(Exception):
    """Probe-local failure that does not leak raw Provider content."""

    def __init__(
        self,
        *,
        safe_code: str,
        safe_summary: str,
        fatal: bool = False,
    ) -> None:
        super().__init__(safe_summary)
        self.safe_code = safe_code
        self.safe_summary = safe_summary
        self.fatal = fatal


class CapabilityObservation(FrozenContract):
    """One capability observation with a secret-free reason code."""

    observation: ProbeObservation
    safe_reason_code: str | None = None

    @field_validator("safe_reason_code")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("safe_reason_code must be a string")
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > MAX_SAFE_REASON_CODE_LEN:
            raise ValueError("safe_reason_code exceeds bound")
        if any(ord(ch) < 32 for ch in cleaned):
            raise ValueError("safe_reason_code must not contain control characters")
        return cleaned


class ModelCapabilityObservations(FrozenContract):
    """Exact capability observation set for probe-contract version 1.

    Extra JSON keys are rejected by FrozenContract (extra='forbid').
    """

    streaming: CapabilityObservation
    tool_calling: CapabilityObservation
    json_schema_args: CapabilityObservation
    stable_tool_call_ids: CapabilityObservation
    multi_tool_calls: CapabilityObservation
    tool_result_continuation: CapabilityObservation
    tools_disabled_finalization: CapabilityObservation


class ModelCapabilityProbeEvidence(FrozenContract):
    """Immutable safe probe evidence ready for persistence (Task 8)."""

    probe_contract_version: Literal[1] = 1
    adapter_key: str
    adapter_revision: str
    model_config_digest: str
    status: ProbeStatus
    capabilities: ModelCapabilityObservations
    probe_digest: str
    safe_error_code: str | None = None
    safe_error_summary: str | None = None
    compatibility_warnings: tuple[str, ...] = ()

    @field_validator("adapter_key", "adapter_revision")
    @classmethod
    def _non_empty(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return value.strip()

    @field_validator("model_config_digest", "probe_digest")
    @classmethod
    def _digest(cls, value: str, info: Any) -> str:
        if not isinstance(value, str) or len(value) != 64 or any(
            ch not in "0123456789abcdef" for ch in value
        ):
            raise ValueError(f"{info.field_name} must be a lowercase 64-hex digest")
        return value

    @field_validator("safe_error_code", "safe_error_summary")
    @classmethod
    def _optional_safe(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            return None
        limit = MAX_SAFE_REASON_CODE_LEN if info.field_name == "safe_error_code" else MAX_SAFE_ERROR_SUMMARY_LEN
        if len(cleaned) > limit:
            raise ValueError(f"{info.field_name} exceeds bound")
        if any(ord(ch) < 32 for ch in cleaned):
            raise ValueError(f"{info.field_name} must not contain control characters")
        return cleaned

    @field_validator("compatibility_warnings", mode="before")
    @classmethod
    def _warnings(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("compatibility_warnings must be a sequence")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("compatibility_warnings items must be non-empty strings")
            cleaned = item.strip()
            if len(cleaned) > MAX_SAFE_REASON_CODE_LEN * 2:
                raise ValueError("compatibility_warnings item exceeds bound")
            out.append(cleaned)
        return tuple(out)

    @model_validator(mode="after")
    def _digest_matches(self) -> ModelCapabilityProbeEvidence:
        expected = compute_probe_digest(
            probe_contract_version=self.probe_contract_version,
            adapter_key=self.adapter_key,
            adapter_revision=self.adapter_revision,
            model_config_digest=self.model_config_digest,
            status=self.status,
            capabilities=self.capabilities,
            compatibility_warnings=self.compatibility_warnings,
            safe_error_code=self.safe_error_code,
            safe_error_summary=self.safe_error_summary,
        )
        if expected != self.probe_digest:
            raise ValueError("probe_digest does not match canonical evidence payload")
        return self


class ProbePolicy(FrozenContract):
    """Explicit bounded probe policy (no ambient defaults from mutable config)."""

    max_provider_requests: int = DEFAULT_MAX_PROVIDER_REQUESTS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_aggregate_tokens: int = DEFAULT_MAX_AGGREGATE_TOKENS
    max_tool_result_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES
    total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS
    locale: str = "en"

    @field_validator(
        "max_provider_requests",
        "max_output_tokens",
        "max_aggregate_tokens",
        "max_tool_result_bytes",
    )
    @classmethod
    def _positive_int(cls, value: int, info: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{info.field_name} must be >= 1")
        return value

    @field_validator("total_timeout_seconds")
    @classmethod
    def _timeout(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("total_timeout_seconds must be a positive number")
        return float(value)

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("locale must be non-empty")
        return value.strip()


@dataclass
class ProbeRunStats:
    """Internal counters for cost-bound assertions (not part of evidence)."""

    provider_request_count: int = 0
    aggregate_tokens: int = 0
    tool_result_bytes: int = 0
    local_tools_executed: list[str] = field(default_factory=list)
    started_monotonic: float = field(default_factory=time.monotonic)


def _passed(reason: str | None = None) -> CapabilityObservation:
    return CapabilityObservation(observation="passed", safe_reason_code=reason)


def _failed(reason: str) -> CapabilityObservation:
    return CapabilityObservation(observation="failed", safe_reason_code=reason)


def _not_observed(reason: str | None = None) -> CapabilityObservation:
    return CapabilityObservation(observation="not_observed", safe_reason_code=reason)


def _all_not_observed(reason: str | None = None) -> ModelCapabilityObservations:
    obs = _not_observed(reason)
    return ModelCapabilityObservations(
        streaming=obs,
        tool_calling=obs,
        json_schema_args=obs,
        stable_tool_call_ids=obs,
        multi_tool_calls=obs,
        tool_result_continuation=obs,
        tools_disabled_finalization=obs,
    )


def observations_payload(capabilities: ModelCapabilityObservations) -> dict[str, JsonValue]:
    """Canonical capability observation payload for digests (no raw content)."""
    out: dict[str, JsonValue] = {}
    for key in REQUIRED_CAPABILITY_KEYS:
        item: CapabilityObservation = getattr(capabilities, key)
        out[key] = {
            "observation": item.observation,
            "safeReasonCode": item.safe_reason_code,
        }
    return out


def compute_probe_digest(
    *,
    probe_contract_version: int,
    adapter_key: str,
    adapter_revision: str,
    model_config_digest: str,
    status: ProbeStatus,
    capabilities: ModelCapabilityObservations,
    compatibility_warnings: Sequence[str] = (),
    safe_error_code: str | None,
    safe_error_summary: str | None,
) -> str:
    """Digest of safe evidence content only.

    Excludes row ID, timestamp, nonce, raw text, and promotion outcome.
    """
    payload: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "probeContractVersion": int(probe_contract_version),
        "adapterKey": adapter_key,
        "adapterRevision": adapter_revision,
        "modelConfigDigest": model_config_digest,
        "status": status,
        "capabilities": observations_payload(capabilities),
        "compatibilityWarnings": list(compatibility_warnings),
        "safeErrorCode": safe_error_code,
        "safeErrorSummary": safe_error_summary,
    }
    return sha256_canonical_json(payload)


def build_model_config_digest(
    *,
    model_id: UUID,
    model_name: str,
    model_type: str,
    model_runtime_revision: int,
    credential_id: UUID,
    credential_runtime_revision: int,
    endpoint_identity: Mapping[str, Any],
    adapter_key: str,
    adapter_revision: str,
    app_build_revision: str,
    provider_protocol: str = OPENAI_CHAT_PROVIDER_PROTOCOL,
    probe_contract_version: int = PROBE_CONTRACT_VERSION,
) -> str:
    """Secret-free model-config digest including probe-contract version.

    Reuses the OpenAI adapter endpoint-identity shape. Callers must pass a
    secret-free ``endpoint_identity`` (no user-info/query/fragment).
    """
    return compute_openai_chat_model_config_digest(
        model_id=model_id,
        model_name=model_name,
        model_type=model_type,
        model_runtime_revision=model_runtime_revision,
        credential_id=credential_id,
        credential_runtime_revision=credential_runtime_revision,
        endpoint_identity=endpoint_identity,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        app_build_revision=app_build_revision,
        provider_protocol=provider_protocol,
        probe_contract_version=probe_contract_version,
    )


def build_endpoint_identity(base_url: str) -> dict[str, Any]:
    """Derive secret-free endpoint identity; rejects user-info/query/fragment."""
    return secret_free_endpoint_identity(base_url)


def _bound_safe_text(value: str, *, limit: int) -> str:
    cleaned = value.strip()
    if len(cleaned) > limit:
        return cleaned[:limit]
    return cleaned


def _safe_error_from_exc(exc: BaseException) -> tuple[str, str]:
    """Map exceptions to safe code/summary without raw content."""
    if isinstance(exc, ProbeError):
        return exc.safe_code, _bound_safe_text(exc.safe_summary, limit=MAX_SAFE_ERROR_SUMMARY_LEN)
    # OpenAI adapter error carries SafeProviderError.
    error = getattr(exc, "error", None)
    if isinstance(error, SafeProviderError):
        return (
            _bound_safe_text(error.semantic_code, limit=MAX_SAFE_REASON_CODE_LEN),
            _bound_safe_text(error.safe_summary, limit=MAX_SAFE_ERROR_SUMMARY_LEN),
        )
    if isinstance(exc, SafeProviderError):
        return (
            _bound_safe_text(exc.semantic_code, limit=MAX_SAFE_REASON_CODE_LEN),
            _bound_safe_text(exc.safe_summary, limit=MAX_SAFE_ERROR_SUMMARY_LEN),
        )
    name = type(exc).__name__
    if name in {"TimeoutError", "APITimeoutError"}:
        return "timeout", "provider request timed out"
    if "Cancel" in name or name == "CancelledError":
        return "cancelled", "probe cancelled"
    # Never include str(exc) — may contain secrets / bodies.
    return "provider_error", f"provider probe failed ({name})"


def _usage_tokens(usage: ProviderUsage | None) -> int:
    if usage is None:
        return 0
    if usage.total_tokens:
        return int(usage.total_tokens)
    return int(usage.input_tokens) + int(usage.output_tokens)


def _is_provider_stable_call_id(call_id: str) -> bool:
    """Adapter-synthesized IDs do not count as Provider stable-ID support."""
    if not call_id or not call_id.strip():
        return False
    # Assembler synthesis: call_r{round}_i{index}_{digest8}
    if call_id.startswith("call_r") and "_i" in call_id:
        return False
    return True


def _echo_input_schema() -> dict[str, JsonValue]:
    return normalize_binding_schema(
        {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                }
            },
            "required": ["value"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )


def _empty_input_schema() -> dict[str, JsonValue]:
    return normalize_binding_schema(
        {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        require_object_root=True,
    )


def _output_schema() -> dict[str, JsonValue]:
    return normalize_binding_schema({"type": "string"}, require_object_root=False)


def _build_probe_binding(
    *,
    capability_key: str,
    input_schema: dict[str, JsonValue],
    target_seed: str,
) -> FrozenCapabilityBinding:
    output_schema = _output_schema()
    completion = CapabilityCompletionContract()
    target = uuid5(UUID("00000000-0000-4000-8000-00000000b0be"), target_seed)
    target_identity = f"probe-local:{capability_key}"
    executable_revision = "probe-1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    config_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "probeTool": capability_key,
            "probeContractVersion": PROBE_CONTRACT_VERSION,
        }
    )
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target),
            "targetVersionId": None,
            "targetRevision": 1,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=sha256_canonical_json(
                {
                    "schemaVersion": 1,
                    "probe": True,
                    "capabilityKey": capability_key,
                }
            ),
        ),
    )


def _build_probe_descriptor(
    binding: FrozenCapabilityBinding,
    *,
    description: str,
) -> CapabilityDescriptor:
    resolved = binding.resolved
    # Deterministic digests derived from binding identity only.
    ruleset = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "probeClassifier": "v1",
            "capabilityKey": resolved.capability_key,
        }
    )
    behavior_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "sideEffect": "none",
            "parallelSafe": True,
            "interruptMode": "none",
            "rulesetDigest": ruleset,
            "capabilityKey": resolved.capability_key,
        }
    )
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="probe-v1",
            ruleset_digest=ruleset,
        ),
        side_effect="none",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=behavior_digest,
    )
    descriptor_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityKey": resolved.capability_key,
            "bindingDigest": resolved.binding_contract_digest,
            "behaviorDigest": behavior_digest,
            "descriptionDigest": sha256_canonical_json({"d": description}),
            "inputSchemaDigest": resolved.input_schema_digest,
            "outputSchemaDigest": resolved.output_schema_digest,
        }
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type="tool",
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description=description,
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=descriptor_digest,
        executable_revision=resolved.executable_revision or "probe-1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status="available",
            reason_code=None,
            compatibility_only=False,
        ),
        completion=resolved.completion,
    )


def _probe_manifest(
    *,
    model_ref: ModelRef,
    provider_protocol: str,
    adapter_key: str,
    adapter_revision: str,
    app_build_revision: str,
    run_id: UUID,
) -> ResolvedRunManifestRevision:
    provider = create_provider_ref(
        provider_protocol=provider_protocol,
        provider_config_id=None,
        provider_runtime_revision=1,
        provider_config_digest=None,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        protocol_revision="probe-1",
        app_build_revision=app_build_revision,
    )
    # Rebuild model_ref with matching provider digest if needed.
    model = model_ref
    if model.provider_ref_digest != provider.provider_ref_digest:
        model = create_model_ref(
            model_id=model_ref.model_id,
            model_name=model_ref.model_name,
            model_type=model_ref.model_type,
            model_runtime_revision=model_ref.model_runtime_revision,
            credential_id=model_ref.credential_id,
            credential_runtime_revision=model_ref.credential_runtime_revision,
            credential_config_digest=model_ref.credential_config_digest,
            model_config_digest=model_ref.model_config_digest,
            provider_ref_digest=provider.provider_ref_digest,
            capability_probe_id=None,
            capability_probe_digest=None,
        )
    main_agent = ResolvedMainAgentRef(
        profile_id=UUID("00000000-0000-4000-8000-00000000b001"),
        version_id=UUID("00000000-0000-4000-8000-00000000b002"),
        profile_key="probe_harness",
        sequence=1,
        content_digest=sha256_canonical_json(
            {"schemaVersion": 1, "probeHarness": True, "version": PROBE_CONTRACT_VERSION}
        ),
    )
    return create_base_run_manifest(
        run_id=run_id,
        main_agent=main_agent,
        provider=provider,
        model=model,
        effective_policy_digest=None,
    )


def _build_surface(
    *,
    manifest: ResolvedRunManifestRevision,
    provider_protocol: str,
    tools: Sequence[tuple[FrozenCapabilityBinding, CapabilityDescriptor, str]],
    scope: Any,
) -> tuple[ProviderToolSurface, ResolvedRunManifestRevision]:
    visible = [(binding, descriptor) for binding, descriptor, _alias in tools]
    hints = {binding.ref.capability_key: alias for binding, _d, alias in tools}
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=provider_protocol,
        visible=visible,
        alias_hints=hints,
        scope=scope,
    )
    return resolution.surface, resolution.manifest


def _empty_surface(
    *,
    manifest: ResolvedRunManifestRevision,
    provider_protocol: str,
    scope: Any,
) -> ProviderToolSurface:
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=provider_protocol,
        visible=[],
        scope=scope,
    )
    return resolution.surface


def _validate_args_against_schema(
    *,
    arguments: Mapping[str, Any],
    input_schema: Mapping[str, JsonValue],
    input_schema_digest: str,
) -> bool:
    try:
        compiled = compile_binding_schema(
            input_schema,  # type: ignore[arg-type]
            expected_digest=input_schema_digest,
            require_object_root=True,
        )
        validate_json_value(compiled, dict(arguments), label="input")
        return True
    except Exception:
        return False


def _local_tool_result(
    call: ProviderToolCall,
    *,
    max_bytes: int,
    stats: ProbeRunStats,
) -> ProviderToolMessage:
    """Execute fixed local probe Tools only. Never imports business Gateway."""
    domain = call.domain_key
    if domain not in {
        PROBE_ECHO_DOMAIN_KEY,
        PROBE_LEFT_DOMAIN_KEY,
        PROBE_RIGHT_DOMAIN_KEY,
    }:
        raise ProbeError(
            safe_code="unexpected_tool",
            safe_summary="probe attempted to execute a non-local tool",
            fatal=True,
        )
    stats.local_tools_executed.append(domain)
    # Fixed local outputs — never echo raw model arguments into evidence.
    if domain == PROBE_ECHO_DOMAIN_KEY:
        user_text = "probe_echo_ok"
        structured: JsonValue = {"status": "ok", "tool": "echo"}
    elif domain == PROBE_LEFT_DOMAIN_KEY:
        user_text = "probe_left_ok"
        structured = {"status": "ok", "tool": "left"}
    else:
        user_text = "probe_right_ok"
        structured = {"status": "ok", "tool": "right"}

    envelope = ProviderToolResultEnvelope(
        status="completed",
        domain_key=domain,
        user_text=user_text,
        structured_output=structured,
        terminal_output=True,
        needs_followup=False,
        error=None,
        artifact_refs=(),
    )
    # Bound tool-result wire size using canonical JSON of the envelope fields we keep.
    approx = len(
        sha256_canonical_json(
            {
                "status": envelope.status,
                "domainKey": envelope.domain_key,
                "userText": envelope.user_text,
                "structuredOutput": envelope.structured_output,
            }
        ).encode("utf-8")
    )
    # sha256 hex is 64 bytes; use structured payload size instead.
    payload_bytes = len(
        str(structured).encode("utf-8")
    ) + len(user_text.encode("utf-8"))
    stats.tool_result_bytes += payload_bytes
    if stats.tool_result_bytes > max_bytes:
        raise ProbeError(
            safe_code="tool_result_budget_exceeded",
            safe_summary="probe tool-result byte budget exceeded",
            fatal=True,
        )
    del approx
    return ProviderToolMessage(
        call_id=call.call_id,
        provider_alias=call.provider_alias,
        content=envelope,
    )


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _check_cancel(cancellation: CancellationPort | None) -> None:
    if cancellation is not None and cancellation.is_cancelled():
        raise ProbeError(
            safe_code="cancelled",
            safe_summary="probe cancelled",
            fatal=True,
        )


def _check_deadline(stats: ProbeRunStats, policy: ProbePolicy) -> None:
    elapsed = time.monotonic() - stats.started_monotonic
    if elapsed > policy.total_timeout_seconds:
        raise ProbeError(
            safe_code="timeout",
            safe_summary="probe total timeout exceeded",
            fatal=True,
        )


def _run_one_round(
    *,
    provider: ProviderAdapter,
    model_ref: ModelRef,
    messages: tuple[ProviderMessage, ...],
    surface: ProviderToolSurface,
    tools_enabled: bool,
    finalization_round: bool,
    generation: ProviderGenerationOptions,
    cancellation: CancellationPort | None,
    policy: ProbePolicy,
    stats: ProbeRunStats,
    round_index: int,
) -> ProviderRoundResult:
    _check_cancel(cancellation)
    _check_deadline(stats, policy)
    if stats.provider_request_count >= policy.max_provider_requests:
        raise ProbeError(
            safe_code="request_budget_exceeded",
            safe_summary="probe provider request budget exceeded",
            fatal=True,
        )
    request = ProviderRoundRequest(
        round_index=round_index,
        messages=messages,
        tool_surface=surface,
        tools_enabled=tools_enabled,
        finalization_round=finalization_round,
        model_ref=model_ref,
        generation=generation,
    )
    stats.provider_request_count += 1
    try:
        events = list(provider.stream_round(request, cancellation=cancellation or _NeverCancelled()))
    except Exception as exc:  # noqa: BLE001 - map to safe probe error
        code, summary = _safe_error_from_exc(exc)
        raise ProbeError(safe_code=code, safe_summary=summary, fatal=True) from None
    _check_cancel(cancellation)
    _check_deadline(stats, policy)
    try:
        result = assemble_provider_round(
            events=events,
            surface=surface,
            round_index=round_index,
        )
    except Exception:
        raise ProbeError(
            safe_code="protocol_error",
            safe_summary="provider stream assembly failed",
            fatal=False,
        ) from None
    tokens = _usage_tokens(result.usage)
    stats.aggregate_tokens += tokens
    if stats.aggregate_tokens > policy.max_aggregate_tokens:
        raise ProbeError(
            safe_code="token_budget_exceeded",
            safe_summary="probe aggregate token budget exceeded",
            fatal=True,
        )
    return result


def _finalize_status(capabilities: ModelCapabilityObservations) -> ProbeStatus:
    values = [getattr(capabilities, key).observation for key in REQUIRED_CAPABILITY_KEYS]
    if all(v == "passed" for v in values):
        return "passed"
    if all(v == "not_observed" for v in values):
        return "failed"
    return "partial"


def _build_evidence(
    *,
    adapter_key: str,
    adapter_revision: str,
    model_config_digest: str,
    capabilities: ModelCapabilityObservations,
    compatibility_warnings: Sequence[str],
    safe_error_code: str | None,
    safe_error_summary: str | None,
    status_override: ProbeStatus | None = None,
) -> ModelCapabilityProbeEvidence:
    status = status_override or _finalize_status(capabilities)
    digest = compute_probe_digest(
        probe_contract_version=PROBE_CONTRACT_VERSION,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        model_config_digest=model_config_digest,
        status=status,
        capabilities=capabilities,
        compatibility_warnings=compatibility_warnings,
        safe_error_code=safe_error_code,
        safe_error_summary=safe_error_summary,
    )
    return ModelCapabilityProbeEvidence(
        probe_contract_version=PROBE_CONTRACT_VERSION,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        model_config_digest=model_config_digest,
        status=status,
        capabilities=capabilities,
        probe_digest=digest,
        safe_error_code=safe_error_code,
        safe_error_summary=safe_error_summary,
        compatibility_warnings=tuple(compatibility_warnings),
    )


def run_model_capability_probe(
    *,
    provider: ProviderAdapter,
    model_ref: ModelRef,
    policy: ProbePolicy | None = None,
    cancellation: CancellationPort | None = None,
    nonce: str | None = None,
    app_build_revision: str | None = None,
    stats_out: ProbeRunStats | None = None,
) -> ModelCapabilityProbeEvidence:
    """Run the bounded harmless probe sequence and return safe evidence.

    Does not write the database, promote a pointer, or dispatch business Tools.
    """
    if not isinstance(model_ref, ModelRef):
        raise TypeError("model_ref must be a ModelRef")
    if model_ref.model_config_digest is None or len(model_ref.model_config_digest) != 64:
        raise ValueError("model_ref.model_config_digest is required for probing")

    policy = policy or ProbePolicy()
    stats = stats_out if stats_out is not None else ProbeRunStats()
    stats.started_monotonic = time.monotonic()

    adapter_key = provider.adapter_key
    adapter_revision = provider.adapter_revision
    model_config_digest = model_ref.model_config_digest
    provider_protocol = provider.provider_protocol
    build_rev = app_build_revision or "unknown"

    # Identity: adapter digest must match model_ref before any I/O.
    if getattr(provider, "model_config_digest", None) not in (None, model_config_digest):
        return _build_evidence(
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            model_config_digest=model_config_digest,
            capabilities=_all_not_observed("adapter_config_mismatch"),
            compatibility_warnings=(),
            safe_error_code="version_drift",
            safe_error_summary="provider adapter model_config_digest mismatch",
            status_override="failed",
        )

    # Nonce is ephemeral — never stored in evidence.
    probe_nonce = nonce if nonce is not None else secrets.token_hex(DEFAULT_NONCE_BYTES)
    if not isinstance(probe_nonce, str) or not probe_nonce or len(probe_nonce) > 64:
        raise ValueError("nonce must be a non-empty string up to 64 characters")

    warnings: list[str] = []
    caps = {
        key: _not_observed("phase_not_reached")
        for key in REQUIRED_CAPABILITY_KEYS
    }

    run_id = uuid4()
    scope = create_execution_scope(
        run_id=run_id,
        conversation_id=None,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id="model-capability-probe",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )
    manifest = _probe_manifest(
        model_ref=model_ref,
        provider_protocol=provider_protocol,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        app_build_revision=build_rev,
        run_id=run_id,
    )
    # Keep model_ref from caller (exact config); only manifest needs provider.
    # If create_base_run_manifest rewrote model via provider mismatch, re-sync.
    if manifest.model is not None and manifest.model.model_config_digest != model_config_digest:
        return _build_evidence(
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            model_config_digest=model_config_digest,
            capabilities=_all_not_observed("manifest_config_mismatch"),
            compatibility_warnings=(),
            safe_error_code="version_drift",
            safe_error_summary="probe manifest model_config_digest mismatch",
            status_override="failed",
        )

    generation_base = ProviderGenerationOptions(
        max_output_tokens=policy.max_output_tokens,
        temperature=0.0,
        tool_choice=ProviderToolChoice(mode="auto"),
        request_parallel_tool_calls=None,
    )

    echo_binding = _build_probe_binding(
        capability_key=PROBE_ECHO_DOMAIN_KEY,
        input_schema=_echo_input_schema(),
        target_seed="probe-echo-v1",
    )
    echo_descriptor = _build_probe_descriptor(
        echo_binding,
        description="Harmless probe echo tool. Call with the provided value field.",
    )
    left_binding = _build_probe_binding(
        capability_key=PROBE_LEFT_DOMAIN_KEY,
        input_schema=_empty_input_schema(),
        target_seed="probe-left-v1",
    )
    left_descriptor = _build_probe_descriptor(
        left_binding,
        description="Harmless probe left tool. Call with an empty object.",
    )
    right_binding = _build_probe_binding(
        capability_key=PROBE_RIGHT_DOMAIN_KEY,
        input_schema=_empty_input_schema(),
        target_seed="probe-right-v1",
    )
    right_descriptor = _build_probe_descriptor(
        right_binding,
        description="Harmless probe right tool. Call with an empty object.",
    )

    messages: list[ProviderMessage] = [
        ProviderSystemMessage(
            content=(
                "You are a capability probe harness. Follow instructions exactly. "
                "Do not invent secrets or business data."
            )
        ),
        ProviderUserMessage(
            content=(
                "Capability probe phase 1: reply with the single token PROBE_STREAM_OK "
                "and nothing else."
            )
        ),
    ]

    meaningful_observation = False

    try:
        _check_cancel(cancellation)

        # ------------------------------------------------------------------
        # Phase 1: streaming transport / text (no Tools)
        # ------------------------------------------------------------------
        empty_surface = _empty_surface(
            manifest=manifest,
            provider_protocol=provider_protocol,
            scope=scope,
        )
        try:
            result = _run_one_round(
                provider=provider,
                model_ref=model_ref,
                messages=tuple(messages),
                surface=empty_surface,
                tools_enabled=False,
                finalization_round=False,
                generation=generation_base,
                cancellation=cancellation,
                policy=policy,
                stats=stats,
                round_index=0,
            )
        except ProbeError as exc:
            if exc.fatal and not meaningful_observation:
                caps = {k: _not_observed("prior_phase_failed") for k in REQUIRED_CAPABILITY_KEYS}
                caps["streaming"] = _failed(exc.safe_code)
                return _build_evidence(
                    adapter_key=adapter_key,
                    adapter_revision=adapter_revision,
                    model_config_digest=model_config_digest,
                    capabilities=ModelCapabilityObservations(**caps),
                    compatibility_warnings=warnings,
                    safe_error_code=exc.safe_code,
                    safe_error_summary=exc.safe_summary,
                    status_override="failed",
                )
            raise

        warnings.extend(result.compatibility_warnings)
        assistant = result.assistant_message
        text = (assistant.content or "").strip()
        if assistant.tool_calls:
            caps["streaming"] = _failed("unexpected_tool_calls")
        elif not text:
            caps["streaming"] = _failed("empty_stream_text")
        else:
            # Do not store or compare against the nonce in evidence; only observe
            # that non-empty text streamed successfully.
            caps["streaming"] = _passed("stream_text_received")
            meaningful_observation = True
        messages.append(assistant)

        # ------------------------------------------------------------------
        # Phase 2: single Tool call (probe_echo) — tool calling / schema / IDs
        # ------------------------------------------------------------------
        _check_cancel(cancellation)
        echo_surface, manifest = _build_surface(
            manifest=manifest,
            provider_protocol=provider_protocol,
            tools=((echo_binding, echo_descriptor, PROBE_ECHO_ALIAS),),
            scope=scope,
        )
        messages.append(
            ProviderUserMessage(
                content=(
                    "Capability probe phase 2: call the probe_echo tool exactly once "
                    "with a JSON object containing required string field value set to "
                    "the token PROBE_ECHO_VALUE."
                )
            )
        )
        gen_echo = ProviderGenerationOptions(
            max_output_tokens=policy.max_output_tokens,
            temperature=0.0,
            tool_choice=ProviderToolChoice(mode="required"),
            request_parallel_tool_calls=False,
        )
        try:
            result = _run_one_round(
                provider=provider,
                model_ref=model_ref,
                messages=tuple(messages),
                surface=echo_surface,
                tools_enabled=True,
                finalization_round=False,
                generation=gen_echo,
                cancellation=cancellation,
                policy=policy,
                stats=stats,
                round_index=1,
            )
        except ProbeError as exc:
            if not meaningful_observation:
                caps = {k: _not_observed("prior_phase_failed") for k in REQUIRED_CAPABILITY_KEYS}
                caps["streaming"] = _failed(exc.safe_code)
                return _build_evidence(
                    adapter_key=adapter_key,
                    adapter_revision=adapter_revision,
                    model_config_digest=model_config_digest,
                    capabilities=ModelCapabilityObservations(**caps),
                    compatibility_warnings=warnings,
                    safe_error_code=exc.safe_code,
                    safe_error_summary=exc.safe_summary,
                    status_override="failed",
                )
            for key in (
                "tool_calling",
                "json_schema_args",
                "stable_tool_call_ids",
                "multi_tool_calls",
                "tool_result_continuation",
                "tools_disabled_finalization",
            ):
                caps[key] = _not_observed(exc.safe_code)
            return _build_evidence(
                adapter_key=adapter_key,
                adapter_revision=adapter_revision,
                model_config_digest=model_config_digest,
                capabilities=ModelCapabilityObservations(**caps),
                compatibility_warnings=warnings,
                safe_error_code=exc.safe_code,
                safe_error_summary=exc.safe_summary,
            )

        warnings.extend(result.compatibility_warnings)
        assistant = result.assistant_message
        messages.append(assistant)
        calls = assistant.tool_calls
        if not calls:
            caps["tool_calling"] = _not_observed("no_tool_call")
            caps["json_schema_args"] = _not_observed("no_tool_call")
            caps["stable_tool_call_ids"] = _not_observed("no_tool_call")
        else:
            meaningful_observation = True
            # Prefer the first call that matches probe_echo; any valid tool call
            # still proves tool_calling.
            echo_calls = [c for c in calls if c.domain_key == PROBE_ECHO_DOMAIN_KEY]
            primary = echo_calls[0] if echo_calls else calls[0]
            if primary.domain_key == PROBE_ECHO_DOMAIN_KEY or primary.provider_alias == PROBE_ECHO_ALIAS:
                caps["tool_calling"] = _passed("tool_call_received")
            else:
                caps["tool_calling"] = _failed("unexpected_tool_alias")

            # JSON Schema argument conformance on the echo call when present.
            schema_target = echo_calls[0] if echo_calls else None
            if schema_target is None:
                caps["json_schema_args"] = _not_observed("echo_tool_not_chosen")
            else:
                ok = _validate_args_against_schema(
                    arguments=schema_target.arguments,
                    input_schema=echo_binding.resolved.input_schema,  # type: ignore[arg-type]
                    input_schema_digest=echo_binding.resolved.input_schema_digest,
                )
                if ok:
                    caps["json_schema_args"] = _passed("args_schema_valid")
                else:
                    caps["json_schema_args"] = _failed("args_schema_invalid")

            # Stable IDs: all nonempty Provider IDs; synthesized IDs fail support.
            if any(not c.call_id for c in calls):
                caps["stable_tool_call_ids"] = _failed("empty_call_id")
            elif all(_is_provider_stable_call_id(c.call_id) for c in calls):
                caps["stable_tool_call_ids"] = _passed("provider_call_ids_present")
            else:
                caps["stable_tool_call_ids"] = _failed("synthesized_call_id")

            # Execute fixed local results for pairing (no business Gateway).
            for call in calls:
                if call.domain_key in {
                    PROBE_ECHO_DOMAIN_KEY,
                    PROBE_LEFT_DOMAIN_KEY,
                    PROBE_RIGHT_DOMAIN_KEY,
                }:
                    messages.append(
                        _local_tool_result(
                            call,
                            max_bytes=policy.max_tool_result_bytes,
                            stats=stats,
                        )
                    )

        # ------------------------------------------------------------------
        # Phase 3: multi-call (probe_left + probe_right)
        # ------------------------------------------------------------------
        _check_cancel(cancellation)
        multi_surface, manifest = _build_surface(
            manifest=manifest,
            provider_protocol=provider_protocol,
            tools=(
                (left_binding, left_descriptor, PROBE_LEFT_ALIAS),
                (right_binding, right_descriptor, PROBE_RIGHT_ALIAS),
            ),
            scope=scope,
        )
        messages.append(
            ProviderUserMessage(
                content=(
                    "Capability probe phase 3: in one assistant message call both "
                    "probe_left and probe_right tools. Each call uses an empty JSON object."
                )
            )
        )
        gen_multi = ProviderGenerationOptions(
            max_output_tokens=policy.max_output_tokens,
            temperature=0.0,
            tool_choice=ProviderToolChoice(mode="required"),
            request_parallel_tool_calls=True,
        )
        try:
            result = _run_one_round(
                provider=provider,
                model_ref=model_ref,
                messages=tuple(messages),
                surface=multi_surface,
                tools_enabled=True,
                finalization_round=False,
                generation=gen_multi,
                cancellation=cancellation,
                policy=policy,
                stats=stats,
                round_index=2,
            )
        except ProbeError as exc:
            caps["multi_tool_calls"] = _failed(exc.safe_code) if exc.safe_code == "protocol_error" else _not_observed(exc.safe_code)
            caps["tool_result_continuation"] = _not_observed(exc.safe_code)
            caps["tools_disabled_finalization"] = _not_observed(exc.safe_code)
            return _build_evidence(
                adapter_key=adapter_key,
                adapter_revision=adapter_revision,
                model_config_digest=model_config_digest,
                capabilities=ModelCapabilityObservations(**caps),
                compatibility_warnings=warnings,
                safe_error_code=exc.safe_code,
                safe_error_summary=exc.safe_summary,
            )

        warnings.extend(result.compatibility_warnings)
        assistant = result.assistant_message
        messages.append(assistant)
        multi_calls = assistant.tool_calls
        multi_keys = {c.domain_key for c in multi_calls}
        multi_aliases = {c.provider_alias for c in multi_calls}

        if len(multi_calls) >= 2 and (
            {PROBE_LEFT_DOMAIN_KEY, PROBE_RIGHT_DOMAIN_KEY}.issubset(multi_keys)
            or {PROBE_LEFT_ALIAS, PROBE_RIGHT_ALIAS}.issubset(multi_aliases)
        ):
            # Detect malformed multi-call only via assembly success + two distinct tools.
            caps["multi_tool_calls"] = _passed("two_tool_calls_received")
            meaningful_observation = True
            # Update stable IDs if still not observed / not failed.
            if caps["stable_tool_call_ids"].observation == "not_observed":
                if all(_is_provider_stable_call_id(c.call_id) for c in multi_calls):
                    caps["stable_tool_call_ids"] = _passed("provider_call_ids_present")
                else:
                    caps["stable_tool_call_ids"] = _failed("synthesized_call_id")
            elif caps["stable_tool_call_ids"].observation == "passed":
                if not all(_is_provider_stable_call_id(c.call_id) for c in multi_calls):
                    caps["stable_tool_call_ids"] = _failed("synthesized_call_id")
        elif len(multi_calls) == 1:
            # Valid but only one Tool chosen — not_observed, not failed.
            caps["multi_tool_calls"] = _not_observed("single_tool_chosen")
        elif len(multi_calls) == 0:
            caps["multi_tool_calls"] = _not_observed("no_tool_call")
        else:
            # Multiple calls but not the expected pair — protocol-ish failure.
            caps["multi_tool_calls"] = _failed("unexpected_multi_tool_set")

        multi_tool_messages: list[ProviderToolMessage] = []
        for call in multi_calls:
            if call.domain_key in {
                PROBE_ECHO_DOMAIN_KEY,
                PROBE_LEFT_DOMAIN_KEY,
                PROBE_RIGHT_DOMAIN_KEY,
            }:
                msg = _local_tool_result(
                    call,
                    max_bytes=policy.max_tool_result_bytes,
                    stats=stats,
                )
                multi_tool_messages.append(msg)
                messages.append(msg)

        # ------------------------------------------------------------------
        # Phase 4: Tool Result continuation
        # ------------------------------------------------------------------
        _check_cancel(cancellation)
        if not multi_tool_messages:
            caps["tool_result_continuation"] = _not_observed("no_tool_results")
        else:
            messages.append(
                ProviderUserMessage(
                    content=(
                        "Capability probe phase 4: using the tool results already "
                        "provided, reply with the single token PROBE_CONTINUE_OK "
                        "and do not call tools."
                    )
                )
            )
            gen_cont = ProviderGenerationOptions(
                max_output_tokens=policy.max_output_tokens,
                temperature=0.0,
                tool_choice=ProviderToolChoice(mode="none"),
                request_parallel_tool_calls=False,
            )
            try:
                result = _run_one_round(
                    provider=provider,
                    model_ref=model_ref,
                    messages=tuple(messages),
                    surface=multi_surface,
                    tools_enabled=True,
                    finalization_round=False,
                    generation=gen_cont,
                    cancellation=cancellation,
                    policy=policy,
                    stats=stats,
                    round_index=3,
                )
            except ProbeError as exc:
                caps["tool_result_continuation"] = _failed(exc.safe_code) if exc.safe_code == "protocol_error" else _not_observed(exc.safe_code)
                caps["tools_disabled_finalization"] = _not_observed(exc.safe_code)
                return _build_evidence(
                    adapter_key=adapter_key,
                    adapter_revision=adapter_revision,
                    model_config_digest=model_config_digest,
                    capabilities=ModelCapabilityObservations(**caps),
                    compatibility_warnings=warnings,
                    safe_error_code=exc.safe_code,
                    safe_error_summary=exc.safe_summary,
                )
            warnings.extend(result.compatibility_warnings)
            assistant = result.assistant_message
            messages.append(assistant)
            cont_text = (assistant.content or "").strip()
            if assistant.tool_calls:
                caps["tool_result_continuation"] = _failed("tool_call_after_results")
            elif cont_text:
                caps["tool_result_continuation"] = _passed("continuation_text_received")
                meaningful_observation = True
            else:
                caps["tool_result_continuation"] = _failed("empty_continuation")

        # ------------------------------------------------------------------
        # Phase 5: tools-disabled finalization
        # ------------------------------------------------------------------
        _check_cancel(cancellation)
        final_surface = _empty_surface(
            manifest=manifest,
            provider_protocol=provider_protocol,
            scope=scope,
        )
        instruction = DefaultFinalizationInstructionProvider().build(locale=policy.locale)
        if not isinstance(instruction, ProviderRuntimeInstructionMessage):
            raise ProbeError(
                safe_code="finalization_instruction_invalid",
                safe_summary="finalization instruction must be a runtime message",
                fatal=True,
            )
        messages.append(instruction)
        gen_final = ProviderGenerationOptions(
            max_output_tokens=policy.max_output_tokens,
            temperature=0.0,
            tool_choice=ProviderToolChoice(mode="none"),
            request_parallel_tool_calls=False,
        )
        try:
            result = _run_one_round(
                provider=provider,
                model_ref=model_ref,
                messages=tuple(messages),
                surface=final_surface,
                tools_enabled=False,
                finalization_round=True,
                generation=gen_final,
                cancellation=cancellation,
                policy=policy,
                stats=stats,
                round_index=4,
            )
        except ProbeError as exc:
            caps["tools_disabled_finalization"] = _failed(exc.safe_code) if exc.safe_code == "protocol_error" else _not_observed(exc.safe_code)
            return _build_evidence(
                adapter_key=adapter_key,
                adapter_revision=adapter_revision,
                model_config_digest=model_config_digest,
                capabilities=ModelCapabilityObservations(**caps),
                compatibility_warnings=warnings,
                safe_error_code=exc.safe_code,
                safe_error_summary=exc.safe_summary,
            )
        warnings.extend(result.compatibility_warnings)
        assistant = result.assistant_message
        if assistant.tool_calls:
            caps["tools_disabled_finalization"] = _failed("tool_call_on_finalization")
        elif (assistant.content or "").strip():
            caps["tools_disabled_finalization"] = _passed("finalization_text_received")
            meaningful_observation = True
        else:
            caps["tools_disabled_finalization"] = _failed("empty_finalization")

        # Collect adapter-level compatibility warnings if exposed.
        adapter_warnings = getattr(provider, "compatibility_warnings", ())
        for w in adapter_warnings or ():
            if isinstance(w, str) and w and w not in warnings:
                warnings.append(w)

        logger.info(
            "model_capability_probe_complete adapter=%s status_pending requests=%s",
            adapter_key,
            stats.provider_request_count,
        )
        return _build_evidence(
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            model_config_digest=model_config_digest,
            capabilities=ModelCapabilityObservations(**caps),
            compatibility_warnings=warnings,
            safe_error_code=None,
            safe_error_summary=None,
        )

    except ProbeError as exc:
        if not meaningful_observation:
            caps = {k: _not_observed("prior_phase_failed") for k in REQUIRED_CAPABILITY_KEYS}
            status: ProbeStatus = "failed"
        else:
            status = "partial"
        return _build_evidence(
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            model_config_digest=model_config_digest,
            capabilities=ModelCapabilityObservations(**caps) if meaningful_observation else ModelCapabilityObservations(
                streaming=caps.get("streaming", _not_observed("prior_phase_failed")),
                tool_calling=caps.get("tool_calling", _not_observed("prior_phase_failed")),
                json_schema_args=caps.get("json_schema_args", _not_observed("prior_phase_failed")),
                stable_tool_call_ids=caps.get("stable_tool_call_ids", _not_observed("prior_phase_failed")),
                multi_tool_calls=caps.get("multi_tool_calls", _not_observed("prior_phase_failed")),
                tool_result_continuation=caps.get("tool_result_continuation", _not_observed("prior_phase_failed")),
                tools_disabled_finalization=caps.get(
                    "tools_disabled_finalization", _not_observed("prior_phase_failed")
                ),
            ),
            compatibility_warnings=warnings,
            safe_error_code=exc.safe_code,
            safe_error_summary=exc.safe_summary,
            status_override=status,
        )
    except Exception as exc:  # noqa: BLE001
        code, summary = _safe_error_from_exc(exc)
        logger.info(
            "model_capability_probe_error adapter=%s code=%s exc_class=%s",
            adapter_key,
            code,
            type(exc).__name__,
        )
        return _build_evidence(
            adapter_key=adapter_key,
            adapter_revision=adapter_revision,
            model_config_digest=model_config_digest,
            capabilities=_all_not_observed("unexpected_error"),
            compatibility_warnings=warnings,
            safe_error_code=code,
            safe_error_summary=summary,
            status_override="failed",
        )


__all__ = [
    "DEFAULT_MAX_AGGREGATE_TOKENS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_PROVIDER_REQUESTS",
    "DEFAULT_MAX_TOOL_RESULT_BYTES",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
    "OPENAI_ADAPTER_KEY",
    "PROBE_CONTRACT_VERSION",
    "PROBE_ECHO_ALIAS",
    "PROBE_ECHO_DOMAIN_KEY",
    "PROBE_LEFT_ALIAS",
    "PROBE_LEFT_DOMAIN_KEY",
    "PROBE_RIGHT_ALIAS",
    "PROBE_RIGHT_DOMAIN_KEY",
    "REQUIRED_CAPABILITY_KEYS",
    "CapabilityObservation",
    "ModelCapabilityObservations",
    "ModelCapabilityProbeEvidence",
    "ProbeError",
    "ProbeObservation",
    "ProbePolicy",
    "ProbeRunStats",
    "ProbeStatus",
    "build_endpoint_identity",
    "build_model_config_digest",
    "compute_probe_digest",
    "observations_payload",
    "run_model_capability_probe",
]
