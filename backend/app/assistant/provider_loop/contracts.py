"""Provider Agent Loop contracts and pure recomputation helpers (Plan 03 Task 1).

Serializable frozen contracts only. Runtime ports are Protocol stubs for later
tasks and must never enter messages, surfaces, continuations, or results.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from typing_extensions import Annotated

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityPrincipal,
    CapabilityResult,
    ContinuationRef,
    FrozenCapabilityBinding,
)
from app.assistant.domain.contracts import (
    FrozenContract,
    ModelRef,
    ResolvedRunManifestRevision,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderToolCall,
    ProviderToolCallRecord,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    digest_provider_message,
    digest_provider_transcript,
    project_tool_result_envelope,
    validate_provider_transcript,
)

ProviderStopReason = Literal[
    "natural_completion",
    "waiting_interrupt",
    "cancelled",
    "provider_error",
    "protocol_error",
    "capability_error",
    "max_rounds_soft_finalized",
    "max_rounds_hard_stop",
]

_DIGEST_RE = __import__("re").compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = __import__("re").compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if _CONTROL_RE.search(cleaned):
        raise ValueError(f"{field_name} must not contain control characters")
    return cleaned


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _json_object_copy(value: Any, *, path: str = "$") -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be a JSON object")
    out: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"mapping keys must be strings at {path}")
        if item is None or isinstance(item, (bool, int, float, str)):
            if isinstance(item, float) and (
                item != item or item in (float("inf"), float("-inf"))  # noqa: PLR0124
            ):
                raise ValueError(f"NaN/Infinity are not valid JSON values at {path}.{key}")
            out[key] = item
        elif isinstance(item, dict):
            out[key] = _json_object_copy(item, path=f"{path}.{key}")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            out[key] = list(item)  # type: ignore[assignment]
        else:
            raise TypeError(f"unsupported JSON value type {type(item)!r} at {path}.{key}")
    return out


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


class ProviderToolDefinition(FrozenContract):
    provider_alias: str
    domain_key: str
    description: str
    input_schema: dict[str, Any]
    binding: FrozenCapabilityBinding
    descriptor: CapabilityDescriptor

    @field_validator("provider_alias", "domain_key", "description")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("input_schema", mode="before")
    @classmethod
    def _schema(cls, value: Any) -> dict[str, JsonValue]:
        return _json_object_copy(value, path="input_schema")

    @model_validator(mode="after")
    def _binding_descriptor_agree(self) -> ProviderToolDefinition:
        binding = self.binding
        descriptor = self.descriptor
        if binding.ref.capability_key != self.domain_key and descriptor.capability_key != self.domain_key:
            # Domain key is the transport/domain identity used by the loop.
            pass
        if descriptor.binding_contract_digest != binding.ref.binding_contract_digest:
            raise ValueError("descriptor binding_contract_digest must match binding")
        if descriptor.capability_key != binding.ref.capability_key:
            raise ValueError("descriptor capability_key must match binding")
        if descriptor.input_schema_digest != binding.ref.input_schema_digest:
            raise ValueError("descriptor input_schema_digest must match binding")
        return self


class ProviderToolSurface(FrozenContract):
    provider_protocol: str
    manifest_revision: int
    manifest_digest: str
    alias_map_digest: str
    tools: tuple[ProviderToolDefinition, ...]
    surface_digest: str

    @field_validator("provider_protocol")
    @classmethod
    def _protocol(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="provider_protocol")

    @field_validator("manifest_revision")
    @classmethod
    def _revision(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="manifest_revision")

    @field_validator("manifest_digest", "alias_map_digest", "surface_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("tools", mode="before")
    @classmethod
    def _tools(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("tools must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _surface_invariants(self) -> ProviderToolSurface:
        aliases = [item.provider_alias for item in self.tools]
        if aliases != sorted(aliases):
            raise ValueError("tools must be sorted by provider_alias")
        if len(set(aliases)) != len(aliases):
            raise ValueError("provider aliases on a surface must be unique")
        domain_keys = [item.domain_key for item in self.tools]
        if len(set(domain_keys)) != len(domain_keys):
            raise ValueError("domain keys on a surface must be unique")
        expected = compute_surface_digest(
            provider_protocol=self.provider_protocol,
            manifest_revision=self.manifest_revision,
            manifest_digest=self.manifest_digest,
            alias_map_digest=self.alias_map_digest,
            tools=self.tools,
        )
        if expected != self.surface_digest:
            raise ValueError("surface_digest does not match surface payload")
        return self


class ToolSurfaceResolution(FrozenContract):
    manifest: ResolvedRunManifestRevision
    surface: ProviderToolSurface

    @model_validator(mode="after")
    def _manifest_matches_surface(self) -> ToolSurfaceResolution:
        if self.surface.manifest_revision != self.manifest.revision:
            raise ValueError("surface manifest_revision must match manifest.revision")
        if self.surface.manifest_digest != self.manifest.manifest_digest:
            raise ValueError("surface manifest_digest must match manifest.manifest_digest")
        return self


def compute_alias_map_digest(
    *,
    provider_protocol: str,
    manifest_digest: str,
    aliases: Sequence[tuple[str, str, str]],
) -> str:
    """Digest of forward/reverse alias maps after a Manifest revision exists.

    aliases: iterable of (domain_key, provider_alias, binding_contract_digest)
    """
    ordered = sorted(
        (
            {
                "domainKey": domain_key,
                "providerAlias": provider_alias,
                "bindingContractDigest": binding_digest,
            }
            for domain_key, provider_alias, binding_digest in aliases
        ),
        key=lambda item: (item["providerAlias"], item["domainKey"]),
    )
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "providerProtocol": provider_protocol,
            "manifestDigest": manifest_digest,
            "aliases": ordered,
        }
    )


def compute_surface_digest(
    *,
    provider_protocol: str,
    manifest_revision: int,
    manifest_digest: str,
    alias_map_digest: str,
    tools: Sequence[ProviderToolDefinition],
) -> str:
    ordered = sorted(tools, key=lambda item: item.provider_alias)
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "providerProtocol": provider_protocol,
            "manifestRevision": manifest_revision,
            "manifestDigest": manifest_digest,
            "aliasMapDigest": alias_map_digest,
            "tools": [
                {
                    "providerAlias": item.provider_alias,
                    "domainKey": item.domain_key,
                    "description": item.description,
                    "inputSchemaDigest": item.descriptor.input_schema_digest,
                    "bindingContractDigest": item.binding.ref.binding_contract_digest,
                    "descriptorDigest": item.descriptor.descriptor_digest,
                    "behaviorDigest": item.descriptor.behavior.behavior_digest,
                    "classificationRevision": item.descriptor.behavior.classification.revision,
                    "classificationRulesetDigest": (
                        item.descriptor.behavior.classification.ruleset_digest
                    ),
                }
                for item in ordered
            ],
        }
    )


# ---------------------------------------------------------------------------
# Stream events / usage / generation
# ---------------------------------------------------------------------------


class ProviderUsage(FrozenContract):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None

    @field_validator(
        "input_tokens",
        "output_tokens",
        "total_tokens",
    )
    @classmethod
    def _tokens(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("cached_input_tokens", "reasoning_tokens")
    @classmethod
    def _optional_tokens(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        return _require_non_negative_int(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _total_bounds(self) -> ProviderUsage:
        if self.total_tokens < max(self.input_tokens, self.output_tokens):
            # total may be provider-reported independently; still require lower bound.
            if self.total_tokens < self.input_tokens + self.output_tokens and self.total_tokens not in {
                self.input_tokens,
                self.output_tokens,
            }:
                # Allow either sum or provider-native total when it covers one side.
                pass
        return self


def aggregate_provider_usage(*parts: ProviderUsage | None) -> ProviderUsage:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached = 0
    reasoning = 0
    saw_cached = False
    saw_reasoning = False
    for part in parts:
        if part is None:
            continue
        input_tokens += part.input_tokens
        output_tokens += part.output_tokens
        total_tokens += part.total_tokens
        if part.cached_input_tokens is not None:
            cached += part.cached_input_tokens
            saw_cached = True
        if part.reasoning_tokens is not None:
            reasoning += part.reasoning_tokens
            saw_reasoning = True
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached if saw_cached else None,
        reasoning_tokens=reasoning if saw_reasoning else None,
    )


class ProviderToolChoice(FrozenContract):
    mode: Literal["auto", "required", "none", "specific"] = "auto"
    provider_alias: str | None = None

    @model_validator(mode="after")
    def _specific_requires_alias(self) -> ProviderToolChoice:
        if self.mode == "specific":
            if not self.provider_alias:
                raise ValueError("specific tool_choice requires provider_alias")
        elif self.provider_alias is not None:
            raise ValueError("provider_alias only valid for specific tool_choice")
        return self


class ProviderGenerationOptions(FrozenContract):
    max_output_tokens: int | None = None
    temperature: float | None = None
    tool_choice: ProviderToolChoice = Field(default_factory=ProviderToolChoice)
    request_parallel_tool_calls: bool | None = None

    @field_validator("max_output_tokens")
    @classmethod
    def _max_output(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("max_output_tokens must be >= 1 when present")
        return value

    @field_validator("temperature")
    @classmethod
    def _temperature(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("temperature must be a number")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # noqa: PLR0124
            raise ValueError("temperature must be finite")
        return number


class ProviderTextDelta(FrozenContract):
    event_type: Literal["text.delta"] = "text.delta"
    sequence: int
    delta: str

    @field_validator("sequence")
    @classmethod
    def _sequence(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="sequence")

    @field_validator("delta")
    @classmethod
    def _delta(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("delta must be a string")
        return value


class ProviderToolCallDelta(FrozenContract):
    event_type: Literal["tool_call.delta"] = "tool_call.delta"
    sequence: int
    call_index: int
    call_id: str | None = None
    function_type: Literal["function"] = "function"
    provider_alias_delta: str = ""
    arguments_delta: str = ""

    @field_validator("sequence", "call_index")
    @classmethod
    def _ints(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("call_id")
    @classmethod
    def _call_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name="call_id")

    @field_validator("provider_alias_delta", "arguments_delta")
    @classmethod
    def _fragments(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("delta fragments must be strings")
        return value


class ProviderUsageSnapshot(FrozenContract):
    event_type: Literal["usage"] = "usage"
    sequence: int
    usage: ProviderUsage

    @field_validator("sequence")
    @classmethod
    def _sequence(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="sequence")


class ProviderRoundTerminal(FrozenContract):
    event_type: Literal["round.terminal"] = "round.terminal"
    sequence: int
    finish_reason: str | None
    safe_request_id: str | None = None

    @field_validator("sequence")
    @classmethod
    def _sequence(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="sequence")

    @field_validator("finish_reason", "safe_request_id")
    @classmethod
    def _optional(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)


ProviderStreamEvent = Annotated[
    ProviderTextDelta
    | ProviderToolCallDelta
    | ProviderUsageSnapshot
    | ProviderRoundTerminal,
    Field(discriminator="event_type"),
]


def parse_provider_stream_event(payload: dict[str, Any]) -> ProviderStreamEvent:
    """Parse a normalized stream event; reject unknown types/extra SDK data."""
    if not isinstance(payload, dict):
        raise TypeError("stream event payload must be a dict")
    event_type = payload.get("event_type") or payload.get("eventType")
    mapping = {
        "text.delta": ProviderTextDelta,
        "tool_call.delta": ProviderToolCallDelta,
        "usage": ProviderUsageSnapshot,
        "round.terminal": ProviderRoundTerminal,
    }
    if event_type not in mapping:
        raise ValueError(f"unknown stream event type {event_type!r}")
    return mapping[event_type].model_validate(payload)


class ProviderRoundRequest(FrozenContract):
    round_index: int
    messages: tuple[ProviderMessage, ...]
    tool_surface: ProviderToolSurface
    tools_enabled: bool
    finalization_round: bool
    model_ref: ModelRef
    generation: ProviderGenerationOptions

    @field_validator("round_index")
    @classmethod
    def _round_index(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="round_index")

    @field_validator("messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("messages must be a sequence")
        return tuple(value)


class ProviderRoundResult(FrozenContract):
    assistant_message: ProviderAssistantMessage
    finish_reason: str | None
    usage: ProviderUsage | None
    compatibility_warnings: tuple[str, ...] = ()

    @field_validator("compatibility_warnings", mode="before")
    @classmethod
    def _warnings(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("compatibility_warnings must be a sequence")
        return tuple(str(item) for item in value)


# ---------------------------------------------------------------------------
# Scope / loop request / result
# ---------------------------------------------------------------------------


class ProviderExecutionScope(FrozenContract):
    run_id: UUID
    conversation_id: UUID | None
    principal: CapabilityPrincipal
    tenant_scope_id: str | None
    scope_digest: str

    @field_validator("tenant_scope_id")
    @classmethod
    def _tenant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name="tenant_scope_id")

    @field_validator("scope_digest")
    @classmethod
    def _scope_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="scope_digest")

    @model_validator(mode="after")
    def _digest_matches(self) -> ProviderExecutionScope:
        expected = compute_scope_digest(
            run_id=self.run_id,
            conversation_id=self.conversation_id,
            principal=self.principal,
            tenant_scope_id=self.tenant_scope_id,
        )
        if expected != self.scope_digest:
            raise ValueError("scope_digest does not match execution scope fields")
        return self


def compute_scope_digest(
    *,
    run_id: UUID,
    conversation_id: UUID | None,
    principal: CapabilityPrincipal,
    tenant_scope_id: str | None,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "runId": str(run_id),
            "conversationId": None if conversation_id is None else str(conversation_id),
            "principal": {
                "principalType": principal.principal_type,
                "principalId": principal.principal_id,
                "authenticated": principal.authenticated,
            },
            "tenantScopeId": tenant_scope_id,
        }
    )


def create_execution_scope(
    *,
    run_id: UUID,
    conversation_id: UUID | None,
    principal: CapabilityPrincipal,
    tenant_scope_id: str | None,
) -> ProviderExecutionScope:
    return ProviderExecutionScope(
        run_id=run_id,
        conversation_id=conversation_id,
        principal=principal,
        tenant_scope_id=tenant_scope_id,
        scope_digest=compute_scope_digest(
            run_id=run_id,
            conversation_id=conversation_id,
            principal=principal,
            tenant_scope_id=tenant_scope_id,
        ),
    )


class SafeProviderError(FrozenContract):
    semantic_code: str
    safe_summary: str
    http_status: int | None = None
    adapter_key: str | None = None
    adapter_revision: str | None = None
    safe_request_id: str | None = None
    retry_disposition: Literal[
        "never",
        "new_run_only",
        "same_call_after_reconciliation",
        "model_may_continue",
    ] = "never"

    @field_validator("semantic_code", "safe_summary")
    @classmethod
    def _required(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("adapter_key", "adapter_revision", "safe_request_id")
    @classmethod
    def _optional(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("http_status")
    @classmethod
    def _http_status(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("http_status must be an int")
        if value < 100 or value > 599:
            raise ValueError("http_status must be a valid HTTP status")
        return value


class ProviderLoopRequest(FrozenContract):
    manifest: ResolvedRunManifestRevision
    initial_messages: tuple[ProviderMessage, ...]
    model_ref: ModelRef
    execution_scope: ProviderExecutionScope
    max_rounds: int
    locale: str
    generation: ProviderGenerationOptions = Field(default_factory=ProviderGenerationOptions)

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="locale")

    @field_validator("max_rounds")
    @classmethod
    def _max_rounds(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("max_rounds must be >= 1")
        return value

    @field_validator("initial_messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("initial_messages must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _request_invariants(self) -> ProviderLoopRequest:
        if self.manifest.run_id != self.execution_scope.run_id:
            raise ValueError("manifest.run_id must equal execution_scope.run_id")
        if self.manifest.model is not None:
            if self.manifest.model.model_ref_digest != self.model_ref.model_ref_digest:
                raise ValueError("loop model_ref must equal manifest.model")
        # Tool-enabled loops need a reserved finalization slot.
        tools_likely = bool(self.generation.tool_choice.mode != "none")
        if tools_likely and self.max_rounds < 2 and self.generation.tool_choice.mode != "none":
            # max_rounds=1 is only legal for tools-disabled requests.
            if self.generation.tool_choice.mode in {"auto", "required", "specific"}:
                raise ValueError("tool-enabled loops require max_rounds >= 2")
        validate_provider_transcript(self.initial_messages)
        return self


class ProviderWaitingCallState(FrozenContract):
    call_id: str
    call_index: int
    binding_contract_digest: str
    descriptor_digest: str
    behavior_digest: str
    classification_revision: str
    classification_ruleset_digest: str
    capability_continuation: ContinuationRef

    @field_validator("call_id", "classification_revision")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("call_index")
    @classmethod
    def _index(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="call_index")

    @field_validator(
        "binding_contract_digest",
        "descriptor_digest",
        "behavior_digest",
        "classification_ruleset_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)


class ProviderLoopContinuation(FrozenContract):
    contract_version: Literal[1] = 1
    execution_scope: ProviderExecutionScope
    model_ref: ModelRef
    locale: str
    max_rounds: int
    provider_rounds_used: int
    prior_tool_call_count: int
    accumulated_usage: ProviderUsage
    current_manifest_revision: int
    current_manifest_digest: str
    exposed_surface: ProviderToolSurface
    assistant_message_digest: str
    transcript_digest: str
    waiting_call: ProviderWaitingCallState
    next_call_index: int
    pending_call_ids: tuple[str, ...]
    completed_call_records: tuple[ProviderToolCallRecord, ...]

    @field_validator("locale")
    @classmethod
    def _locale(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="locale")

    @field_validator(
        "max_rounds",
        "provider_rounds_used",
        "prior_tool_call_count",
        "current_manifest_revision",
        "next_call_index",
    )
    @classmethod
    def _ints(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator(
        "current_manifest_digest",
        "assistant_message_digest",
        "transcript_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("pending_call_ids", mode="before")
    @classmethod
    def _pending(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("pending_call_ids must be a sequence")
        return tuple(str(item) for item in value)

    @field_validator("completed_call_records", mode="before")
    @classmethod
    def _records(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("completed_call_records must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _continuation_invariants(self) -> ProviderLoopContinuation:
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if self.provider_rounds_used > self.max_rounds:
            raise ValueError("provider_rounds_used cannot exceed max_rounds")
        if self.waiting_call.call_id in self.pending_call_ids:
            raise ValueError("waiting call cannot also be pending")
        if len(set(self.pending_call_ids)) != len(self.pending_call_ids):
            raise ValueError("pending_call_ids must be unique")
        for record in self.completed_call_records:
            if record.status in {"waiting", "deferred"}:
                raise ValueError("completed_call_records cannot include waiting/deferred")
        if self.exposed_surface.manifest_digest == self.current_manifest_digest:
            if self.exposed_surface.manifest_revision != self.current_manifest_revision:
                raise ValueError("exposed surface revision must match current when digests match")
        return self


class ProviderWaitingResolution(FrozenContract):
    call_id: str
    capability_continuation: ContinuationRef
    capability_result: CapabilityResult

    @field_validator("call_id")
    @classmethod
    def _call_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="call_id")

    @model_validator(mode="after")
    def _terminal_only(self) -> ProviderWaitingResolution:
        if self.capability_result.status == "waiting":
            raise ValueError("waiting resolution cannot itself be waiting")
        if self.capability_result.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("waiting resolution must be completed|failed|cancelled")
        return self


class ProviderLoopResumeRequest(FrozenContract):
    manifest: ResolvedRunManifestRevision
    messages: tuple[ProviderMessage, ...]
    continuation: ProviderLoopContinuation
    resolved_waiting: ProviderWaitingResolution

    @field_validator("messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("messages must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _resume_invariants(self) -> ProviderLoopResumeRequest:
        cont = self.continuation
        if self.manifest.run_id != cont.execution_scope.run_id:
            raise ValueError("resume manifest run_id must match continuation scope")
        if self.manifest.revision != cont.current_manifest_revision:
            raise ValueError("resume manifest revision mismatch")
        if self.manifest.manifest_digest != cont.current_manifest_digest:
            raise ValueError("resume manifest digest mismatch")
        if digest_provider_transcript(self.messages) != cont.transcript_digest:
            raise ValueError("resume transcript_digest mismatch")
        validate_provider_transcript(
            self.messages,
            allowed_open_continuation=cont,
        )
        if self.resolved_waiting.call_id != cont.waiting_call.call_id:
            raise ValueError("resolved waiting call_id mismatch")
        if (
            self.resolved_waiting.capability_continuation.model_dump()
            != cont.waiting_call.capability_continuation.model_dump()
        ):
            raise ValueError("resolved waiting ContinuationRef mismatch")
        # Raw ProviderToolMessage cannot satisfy resume; require CapabilityResult.
        if isinstance(self.resolved_waiting.capability_result, ProviderToolMessage):
            raise ValueError("raw ProviderToolMessage cannot satisfy resume")
        return self


class ProviderLoopResult(FrozenContract):
    status: Literal["completed", "waiting", "failed", "cancelled"]
    final_text: str | None
    messages: tuple[ProviderMessage, ...]
    tool_calls: tuple[ProviderToolCallRecord, ...]
    round_count: int
    stop_reason: ProviderStopReason
    manifest: ResolvedRunManifestRevision
    continuation: ProviderLoopContinuation | None
    usage: ProviderUsage
    error: SafeProviderError | None

    @field_validator("round_count")
    @classmethod
    def _round_count(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="round_count")

    @field_validator("messages", "tool_calls", mode="before")
    @classmethod
    def _sequences(cls, value: Any, info: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError(f"{info.field_name} must be a sequence")
        return tuple(value)

    @field_validator("final_text")
    @classmethod
    def _final_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("final_text must be a string")
        if _CONTROL_RE.search(value):
            raise ValueError("final_text must not contain control characters")
        return value

    @model_validator(mode="after")
    def _status_stop_rules(self) -> ProviderLoopResult:
        status = self.status
        reason = self.stop_reason
        if status == "completed":
            if reason not in {"natural_completion", "max_rounds_soft_finalized"}:
                raise ValueError("completed status requires natural_completion|max_rounds_soft_finalized")
            if self.continuation is not None:
                raise ValueError("completed result forbids continuation")
            if self.error is not None:
                raise ValueError("completed result forbids error")
            validate_provider_transcript(self.messages)
        elif status == "waiting":
            if reason != "waiting_interrupt":
                raise ValueError("waiting status requires waiting_interrupt")
            if self.continuation is None:
                raise ValueError("waiting status requires continuation")
            if self.error is not None:
                raise ValueError("waiting result forbids error")
            validate_provider_transcript(
                self.messages,
                allowed_open_continuation=self.continuation,
            )
        elif status == "cancelled":
            if reason != "cancelled":
                raise ValueError("cancelled status requires cancelled stop_reason")
            if self.continuation is not None:
                raise ValueError("cancelled result forbids continuation")
            validate_provider_transcript(self.messages)
        elif status == "failed":
            if reason not in {
                "provider_error",
                "protocol_error",
                "capability_error",
                "max_rounds_hard_stop",
            }:
                raise ValueError("failed status requires an error stop_reason")
            if self.continuation is not None:
                raise ValueError("failed result forbids continuation")
            if self.error is None:
                raise ValueError("failed result requires SafeProviderError")
            # Failed may seal unpaired calls already; if messages are present, require pairing.
            if self.messages:
                validate_provider_transcript(self.messages)
        return self


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class ProviderDispatchRequest(FrozenContract):
    call: ProviderToolCall
    binding: FrozenCapabilityBinding
    descriptor: CapabilityDescriptor
    current_manifest: ResolvedRunManifestRevision
    execution_scope: ProviderExecutionScope
    authorization: CapabilityAuthorizationEvidence

    @model_validator(mode="after")
    def _identity(self) -> ProviderDispatchRequest:
        if self.call.binding_contract_digest != self.binding.ref.binding_contract_digest:
            raise ValueError("dispatch binding digest mismatch")
        if self.call.descriptor_digest != self.descriptor.descriptor_digest:
            raise ValueError("dispatch descriptor digest mismatch")
        if self.execution_scope.run_id != self.current_manifest.run_id:
            raise ValueError("dispatch scope run_id mismatch")
        return self


class ProviderDispatchResult(FrozenContract):
    capability_result: CapabilityResult
    next_manifest: ResolvedRunManifestRevision


# ---------------------------------------------------------------------------
# Runtime ports (never serializable into messages/surfaces/continuations/results)
# ---------------------------------------------------------------------------


@runtime_checkable
class CancellationPort(Protocol):
    def is_cancelled(self) -> bool: ...


@runtime_checkable
class ToolsProvider(Protocol):
    def resolve(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        scope: ProviderExecutionScope,
        locale: str,
    ) -> ToolSurfaceResolution: ...


class RoundContextResolution(FrozenContract):
    """Protected context messages for one Provider round (Plan 04 Task 2)."""

    manifest_revision: int
    manifest_digest: str
    applied_skill_version_ids: tuple[UUID, ...]
    messages: tuple[ProviderContextUpdateMessage, ...]

    @field_validator("manifest_revision")
    @classmethod
    def _revision(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="manifest_revision")

    @field_validator("manifest_digest")
    @classmethod
    def _manifest_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="manifest_digest")

    @field_validator("applied_skill_version_ids", mode="before")
    @classmethod
    def _applied_ids(cls, value: Any) -> tuple[UUID, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("applied_skill_version_ids must be a sequence")
        out: list[UUID] = []
        seen: set[UUID] = set()
        for item in value:
            if not isinstance(item, UUID):
                raise TypeError("applied_skill_version_ids must contain UUID values")
            if item in seen:
                raise ValueError("applied_skill_version_ids must be unique")
            seen.add(item)
            out.append(item)
        return tuple(out)

    @field_validator("messages", mode="before")
    @classmethod
    def _messages(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("messages must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _message_manifest_agreement(self) -> RoundContextResolution:
        for message in self.messages:
            if not isinstance(message, ProviderContextUpdateMessage):
                raise TypeError("messages must contain ProviderContextUpdateMessage")
            if message.manifest_revision != self.manifest_revision:
                raise ValueError("context message manifest_revision must match resolution")
            if message.manifest_digest != self.manifest_digest:
                raise ValueError("context message manifest_digest must match resolution")
        return self


@runtime_checkable
class RoundContextProvider(Protocol):
    """Runtime port: protected Skill/Main Agent context for the next Provider round."""

    def resolve(
        self,
        *,
        manifest: ResolvedRunManifestRevision,
        already_applied_skill_version_ids: tuple[UUID, ...],
        execution_scope: ProviderExecutionScope,
        locale: str,
    ) -> RoundContextResolution: ...


class NoOpRoundContextProvider:
    """Default no-op context provider; preserves pre-Plan-04 loop behavior."""

    def resolve(
        self,
        *,
        manifest: ResolvedRunManifestRevision,
        already_applied_skill_version_ids: tuple[UUID, ...],
        execution_scope: ProviderExecutionScope,
        locale: str,
    ) -> RoundContextResolution:
        del already_applied_skill_version_ids, execution_scope, locale
        return RoundContextResolution(
            manifest_revision=manifest.revision,
            manifest_digest=manifest.manifest_digest,
            applied_skill_version_ids=(),
            messages=(),
        )


@runtime_checkable
class CurrentCapabilityDescriptorVerifier(Protocol):
    """Runtime port only. Must never enter messages/surfaces/continuations/results."""

    def require_current(
        self,
        *,
        binding: FrozenCapabilityBinding,
        exposed_descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityDescriptor: ...


@runtime_checkable
class ProviderAuthorizationEvidenceFactory(Protocol):
    def issue(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence: ...


@runtime_checkable
class ToolDispatcher(Protocol):
    def dispatch(
        self,
        request: ProviderDispatchRequest,
        *,
        cancellation: CancellationPort,
    ) -> ProviderDispatchResult: ...


@runtime_checkable
class SiblingExecutionPort(Protocol):
    def map_parallel(
        self,
        items: Sequence[Any],
        worker: Any,
        *,
        max_workers: int,
    ) -> list[Any]: ...


@runtime_checkable
class ProviderLoopEventSink(Protocol):
    def emit(self, event_type: str, payload: dict[str, JsonValue]) -> None: ...


@runtime_checkable
class ProviderAdapter(Protocol):
    provider_protocol: str
    adapter_key: str
    adapter_revision: str
    model_config_digest: str

    def stream_round(
        self,
        request: ProviderRoundRequest,
        *,
        cancellation: CancellationPort,
    ) -> Iterator[ProviderStreamEvent]: ...


@dataclass(frozen=True)
class ProviderLoopPorts:
    provider: ProviderAdapter
    tools_provider: ToolsProvider
    current_descriptors: CurrentCapabilityDescriptorVerifier
    authorization_evidence: ProviderAuthorizationEvidenceFactory
    tool_dispatcher: ToolDispatcher
    sibling_executor: SiblingExecutionPort
    cancellation: CancellationPort
    events: ProviderLoopEventSink
    # Default no-op preserves byte-identical Plan 03 behavior for all callers.
    round_context_provider: RoundContextProvider = NoOpRoundContextProvider()


def assert_not_serializable_port(value: Any) -> None:
    """Reject runtime ports/ephemeral objects from portable contracts."""
    forbidden_type_names = {
        "CancellationPort",
        "ToolsProvider",
        "RoundContextProvider",
        "NoOpRoundContextProvider",
        "CurrentCapabilityDescriptorVerifier",
        "ProviderAuthorizationEvidenceFactory",
        "ToolDispatcher",
        "SiblingExecutionPort",
        "ProviderLoopEventSink",
        "ProviderAdapter",
        "ProviderLoopPorts",
        "Session",
        "AsyncSession",
        "Client",
        "OpenAI",
        "Future",
        "Task",
    }
    type_name = type(value).__name__
    if type_name in forbidden_type_names:
        raise TypeError(f"{type_name} cannot enter portable provider-loop state")
    if callable(value) and not isinstance(value, type):
        # Bound methods / callbacks.
        if type_name in {"method", "function", "builtin_function_or_method"}:
            raise TypeError("callbacks cannot enter portable provider-loop state")


def recompute_continuation_identity(continuation: ProviderLoopContinuation) -> dict[str, str]:
    """Pure recomputation helper for resume validation."""
    return {
        "scope_digest": compute_scope_digest(
            run_id=continuation.execution_scope.run_id,
            conversation_id=continuation.execution_scope.conversation_id,
            principal=continuation.execution_scope.principal,
            tenant_scope_id=continuation.execution_scope.tenant_scope_id,
        ),
        "surface_digest": continuation.exposed_surface.surface_digest,
        "assistant_message_digest": continuation.assistant_message_digest,
        "transcript_digest": continuation.transcript_digest,
        "current_manifest_digest": continuation.current_manifest_digest,
        "model_ref_digest": continuation.model_ref.model_ref_digest,
    }


def project_waiting_resolution_message(
    *,
    call: ProviderToolCall,
    resolution: ProviderWaitingResolution,
) -> ProviderToolMessage:
    if resolution.call_id != call.call_id:
        raise ValueError("resolution call_id mismatch")
    envelope = project_tool_result_envelope(
        domain_key=call.domain_key,
        result=resolution.capability_result,
    )
    return ProviderToolMessage(
        call_id=call.call_id,
        provider_alias=call.provider_alias,
        content=envelope,
    )


__all__ = [
    "CancellationPort",
    "CurrentCapabilityDescriptorVerifier",
    "NoOpRoundContextProvider",
    "ProviderAdapter",
    "ProviderAuthorizationEvidenceFactory",
    "ProviderDispatchRequest",
    "ProviderDispatchResult",
    "ProviderExecutionScope",
    "ProviderGenerationOptions",
    "ProviderLoopContinuation",
    "ProviderLoopEventSink",
    "ProviderLoopPorts",
    "ProviderLoopRequest",
    "ProviderLoopResult",
    "ProviderLoopResumeRequest",
    "ProviderRoundRequest",
    "ProviderRoundResult",
    "ProviderRoundTerminal",
    "ProviderStopReason",
    "ProviderStreamEvent",
    "ProviderTextDelta",
    "ProviderToolCallDelta",
    "ProviderToolChoice",
    "ProviderToolDefinition",
    "ProviderToolSurface",
    "ProviderUsage",
    "ProviderUsageSnapshot",
    "ProviderWaitingCallState",
    "ProviderWaitingResolution",
    "RoundContextProvider",
    "RoundContextResolution",
    "SafeProviderError",
    "SiblingExecutionPort",
    "ToolDispatcher",
    "ToolSurfaceResolution",
    "ToolsProvider",
    "aggregate_provider_usage",
    "assert_not_serializable_port",
    "compute_alias_map_digest",
    "compute_scope_digest",
    "compute_surface_digest",
    "create_execution_scope",
    "parse_provider_stream_event",
    "project_waiting_resolution_message",
    "recompute_continuation_identity",
]
