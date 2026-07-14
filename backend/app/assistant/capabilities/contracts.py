"""Frozen capability runtime contracts (Plan 02-owned surface).

Imports/re-exports Plan 01 binding/completion/dependency contracts and adds the
runtime descriptor, evidence, result, and event models. No database, Tool
Registry, Workflow engine, OpenClaw, or Provider access.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    FrozenContract,
    ResolvedCapabilityBinding,
    ResolvedCapabilityDependency,
    ResolvedCapabilityRef,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json

# Pydantic cannot materialize the recursive JsonValue alias as a field annotation.
# Domain payloads still carry JSON-compatible dicts; digests/validators enforce the narrow contract.
JsonObject = dict[str, Any]

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_SAFE_MESSAGE_LEN = 256
MAX_SAFE_CODE_LEN = 64
MAX_IDENTITY_LEN = 512
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

# ---------------------------------------------------------------------------
# Plan 01 re-exports / aliases
# ---------------------------------------------------------------------------

CapabilityCompletionMetadata = CapabilityCompletionContract

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

SideEffectClass = Literal[
    "none",
    "compute",
    "read",
    "draft",
    "write_local",
    "write_external",
    "unknown",
]

EvidenceIssuer = Literal["openclaw_bridge", "skill_policy", "system", "test"]
CapabilityEntrypoint = Literal["openclaw", "main_agent", "workflow", "agent", "test"]
EvidenceVerifierKey = tuple[EvidenceIssuer, CapabilityEntrypoint]

CapabilityEventType = Literal[
    "capability.resolved",
    "capability.authorized",
    "capability.started",
    "capability.child_event",
    "capability.completed",
    "capability.failed",
    "capability.cancelled",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must be non-empty")
    if len(cleaned) > MAX_IDENTITY_LEN:
        raise ValueError(f"{field_name} exceeds {MAX_IDENTITY_LEN} characters")
    if _CONTROL_RE.search(cleaned):
        raise ValueError(f"{field_name} must not contain control characters")
    return cleaned


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_safe_message(value: Any, *, field_name: str = "safe_message") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if _CONTROL_RE.search(value):
        raise ValueError(f"{field_name} must not contain control characters")
    if len(value) > MAX_SAFE_MESSAGE_LEN:
        raise ValueError(f"{field_name} exceeds {MAX_SAFE_MESSAGE_LEN} characters")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _require_safe_code(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("safe_code must be a string")
    if not value or len(value) > MAX_SAFE_CODE_LEN:
        raise ValueError(f"safe_code must be 1..{MAX_SAFE_CODE_LEN} characters")
    if _CONTROL_RE.search(value) or any(ch.isspace() for ch in value):
        raise ValueError("safe_code must not contain whitespace or control characters")
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _require_non_negative_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{field_name} must be a finite number")
    if number < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return number


def _json_copy(value: Any, *, path: str = "$") -> JsonValue:
    """Deep-copy and validate a narrow JSON value (no NaN/Inf/bytes/callables)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"NaN/Infinity are not valid JSON values at {path}")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        out: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping keys must be strings at {path}")
            out[key] = _json_copy(item, path=f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_copy(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"unsupported JSON value type {type(value)!r} at {path}")


def _json_object_copy(value: Any, *, path: str = "$") -> dict[str, JsonValue]:
    copied = _json_copy(value, path=path)
    if not isinstance(copied, dict):
        raise ValueError(f"{path} must be a JSON object")
    return copied


# ---------------------------------------------------------------------------
# Identity / descriptor
# ---------------------------------------------------------------------------


class ClassificationContractRef(FrozenContract):
    schema_version: Literal[1] = 1
    revision: str
    ruleset_digest: str

    @field_validator("revision")
    @classmethod
    def _revision_non_empty(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="revision")

    @field_validator("ruleset_digest")
    @classmethod
    def _ruleset_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="ruleset_digest")


class CapabilityAvailability(FrozenContract):
    status: Literal[
        "available",
        "disabled",
        "missing",
        "version_drift",
        "unsupported",
    ]
    reason_code: str | None = None
    compatibility_only: bool = False

    @field_validator("reason_code")
    @classmethod
    def _reason_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name="reason_code")


class CapabilityTimeoutPolicy(FrozenContract):
    mode: Literal["native", "cooperative", "none"]
    timeout_seconds: float | None
    cancellation_supported: bool

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_non_negative_float(value, field_name="timeout_seconds")


class CapabilityBehavior(FrozenContract):
    classification: ClassificationContractRef
    side_effect: SideEffectClass
    parallel_safe: bool
    interrupt_mode: Literal["none", "legacy_blocking", "durable"]
    timeout_policy: CapabilityTimeoutPolicy
    behavior_digest: str

    @field_validator("behavior_digest")
    @classmethod
    def _behavior_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="behavior_digest")

    @model_validator(mode="after")
    def _unknown_not_parallel_safe(self) -> CapabilityBehavior:
        if self.side_effect == "unknown" and self.parallel_safe:
            raise ValueError("unknown side_effect cannot be marked parallel_safe")
        return self


class CapabilityDescriptor(FrozenContract):
    capability_key: str
    capability_type: Literal["tool", "workflow", "agent"]
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    display_name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    input_schema_digest: str
    output_schema_digest: str
    descriptor_digest: str
    executable_revision: str
    behavior: CapabilityBehavior
    availability: CapabilityAvailability
    completion: CapabilityCompletionMetadata

    @field_validator(
        "capability_key",
        "target_identity",
        "display_name",
        "description",
        "executable_revision",
    )
    @classmethod
    def _non_empty_identity(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator(
        "resolution_digest",
        "binding_contract_digest",
        "dependency_closure_digest",
        "input_schema_digest",
        "output_schema_digest",
        "descriptor_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _schema_objects(cls, value: Any, info: Any) -> JsonObject:
        return _json_object_copy(value, path=info.field_name)

    @field_validator("target_revision")
    @classmethod
    def _target_revision(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("target_revision must be >= 0 when present")
        return value


class FrozenBindingProvenance(FrozenContract):
    # Additive Plan 04 value ``main_agent_profile``; existing serialized values unchanged.
    origin: Literal["skill_version", "openclaw_request", "test", "main_agent_profile"]
    binding_row_id: UUID | None
    owner_version_id: UUID | None
    source_snapshot_digest: str

    @field_validator("source_snapshot_digest")
    @classmethod
    def _source_snapshot_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="source_snapshot_digest")


def _verify_resolved_binding_digests(resolved: ResolvedCapabilityBinding) -> None:
    """Recompute binding digests from the snapshot body; reject stale matching pairs."""
    snapshot = resolved.resolution_snapshot
    if not isinstance(snapshot, Mapping):
        raise ValueError("resolution_snapshot must be a mapping")

    expected_digest = _require_digest(
        resolved.binding_contract_digest,
        field_name="binding_contract_digest",
    )
    stored_digest = snapshot.get("bindingContractDigest")
    if not isinstance(stored_digest, str) or not _DIGEST_RE.fullmatch(stored_digest):
        raise ValueError(
            "resolution_snapshot bindingContractDigest must be a lowercase 64-character SHA-256 hex digest"
        )

    # Canonical digest verification: payload without bindingContractDigest must match.
    payload_for_digest = {
        key: value for key, value in snapshot.items() if key != "bindingContractDigest"
    }
    recomputed = sha256_canonical_json(payload_for_digest)  # type: ignore[arg-type]
    if recomputed != expected_digest:
        raise ValueError("binding_contract_digest does not match resolution_snapshot payload")
    if stored_digest != expected_digest:
        raise ValueError("resolution_snapshot bindingContractDigest mismatch")
    if snapshot.get("dependencyClosureDigest") != resolved.dependency_closure_digest:
        raise ValueError("dependency_closure_digest does not match resolution_snapshot")


class FrozenCapabilityBinding(FrozenContract):
    provenance: FrozenBindingProvenance
    ref: ResolvedCapabilityRef
    resolved: ResolvedCapabilityBinding

    @model_validator(mode="after")
    def _ref_matches_resolved(self) -> FrozenCapabilityBinding:
        resolved = self.resolved
        ref = self.ref
        mismatches: list[str] = []
        checks = (
            ("capability_type", ref.capability_type, resolved.capability_type),
            ("capability_key", ref.capability_key, resolved.capability_key),
            ("target_identity", ref.target_identity, resolved.target_identity),
            ("target_id", ref.target_id, resolved.target_id),
            ("target_version_id", ref.target_version_id, resolved.target_version_id),
            ("target_revision", ref.target_revision, resolved.resolved_revision),
            ("input_schema_digest", ref.input_schema_digest, resolved.input_schema_digest),
            ("output_schema_digest", ref.output_schema_digest, resolved.output_schema_digest),
            ("resolution_digest", ref.resolution_digest, resolved.resolution_digest),
            (
                "dependency_closure_digest",
                ref.dependency_closure_digest,
                resolved.dependency_closure_digest,
            ),
            (
                "binding_contract_digest",
                ref.binding_contract_digest,
                resolved.binding_contract_digest,
            ),
        )
        for name, left, right in checks:
            if left != right:
                mismatches.append(name)
        if mismatches:
            raise ValueError(
                "ResolvedCapabilityRef does not match ResolvedCapabilityBinding: "
                + ", ".join(mismatches)
            )
        _verify_resolved_binding_digests(resolved)
        return self

    @property
    def input_schema(self) -> JsonObject:
        return copy.deepcopy(self.resolved.input_schema)  # type: ignore[arg-type]

    @property
    def output_schema(self) -> JsonObject:
        return copy.deepcopy(self.resolved.output_schema)  # type: ignore[arg-type]

    @property
    def dependencies(self) -> tuple[ResolvedCapabilityDependency, ...]:
        return self.resolved.dependencies


def _deepcopy_resolved_binding(resolved: ResolvedCapabilityBinding) -> ResolvedCapabilityBinding:
    """Materialize a deep-copied ResolvedCapabilityBinding so source mutation is isolated."""
    # model_validate + deep-copied nested JSON keeps digests and bodies stable.
    payload = resolved.model_dump(mode="python")
    payload["input_schema"] = _json_object_copy(payload.get("input_schema"), path="input_schema")
    payload["output_schema"] = _json_object_copy(payload.get("output_schema"), path="output_schema")
    payload["resolution_snapshot"] = _json_object_copy(
        payload.get("resolution_snapshot"),
        path="resolution_snapshot",
    )
    deps = []
    for dep in payload.get("dependencies") or ():
        dep_copy = dict(dep)
        if dep_copy.get("input_schema") is not None:
            dep_copy["input_schema"] = _json_object_copy(
                dep_copy["input_schema"],
                path="dependency.input_schema",
            )
        if dep_copy.get("output_schema") is not None:
            dep_copy["output_schema"] = _json_object_copy(
                dep_copy["output_schema"],
                path="dependency.output_schema",
            )
        if dep_copy.get("resolution_snapshot") is not None:
            dep_copy["resolution_snapshot"] = _json_object_copy(
                dep_copy["resolution_snapshot"],
                path="dependency.resolution_snapshot",
            )
        deps.append(dep_copy)
    payload["dependencies"] = deps
    return ResolvedCapabilityBinding.model_validate(payload)


def project_frozen_capability_binding(
    *,
    resolved: ResolvedCapabilityBinding,
    provenance: FrozenBindingProvenance,
    ref: ResolvedCapabilityRef | None = None,
) -> FrozenCapabilityBinding:
    """Project a Plan 01 resolved binding into the runtime frozen surface.

    Accepts the final Plan 01 domain object/snapshot, deep-copies nested JSON,
    and verifies digests before returning. Does not invent a second binding DTO.
    """
    if not isinstance(resolved, ResolvedCapabilityBinding):
        raise TypeError("resolved must be a ResolvedCapabilityBinding")
    if not isinstance(provenance, FrozenBindingProvenance):
        raise TypeError("provenance must be a FrozenBindingProvenance")

    material = _deepcopy_resolved_binding(resolved)
    # Digest recompute runs in FrozenCapabilityBinding validation (shared path).
    if ref is None:
        ref = ResolvedCapabilityRef(
            capability_type=material.capability_type,
            capability_key=material.capability_key,
            target_identity=material.target_identity,
            target_id=material.target_id,
            target_version_id=material.target_version_id,
            target_revision=material.resolved_revision,
            input_schema_digest=material.input_schema_digest,
            output_schema_digest=material.output_schema_digest,
            resolution_digest=material.resolution_digest,
            dependency_closure_digest=material.dependency_closure_digest,
            binding_contract_digest=material.binding_contract_digest,
        )
    return FrozenCapabilityBinding(provenance=provenance, ref=ref, resolved=material)


# ---------------------------------------------------------------------------
# Principal / owner / evidence
# ---------------------------------------------------------------------------


class CapabilityPrincipal(FrozenContract):
    principal_type: Literal["openclaw_installation", "user", "service", "test"]
    principal_id: str
    authenticated: bool

    @field_validator("principal_id")
    @classmethod
    def _principal_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="principal_id")


class CapabilityOwnerRef(FrozenContract):
    # Additive Plan 04 value ``main_agent``; existing serialized values unchanged.
    owner_kind: Literal["skill_version", "openclaw_catalog", "system", "test", "main_agent"]
    owner_id: str
    owner_version_id: UUID | None

    @field_validator("owner_id")
    @classmethod
    def _owner_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="owner_id")


class CapabilityAuthorizationEvidence(FrozenContract):
    issuer: EvidenceIssuer
    call_id: str
    principal: CapabilityPrincipal
    entrypoint: CapabilityEntrypoint
    owner: CapabilityOwnerRef
    capability_key: str
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    evidence_digest: str

    @field_validator("call_id", "capability_key")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator(
        "resolution_digest",
        "binding_contract_digest",
        "dependency_closure_digest",
        "grant_source_digest",
        "evidence_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("allowed_side_effects", mode="before")
    @classmethod
    def _side_effects(cls, value: Any) -> tuple[SideEffectClass, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("allowed_side_effects must be a sequence")
        return tuple(value)


# ---------------------------------------------------------------------------
# Ephemeral policy/evidence (process-local dataclasses; no JSON serializer)
# ---------------------------------------------------------------------------


class _SingleUseDispatchPermitProto:
    """Typing-only stand-in imported by ports; concrete permits live with verifiers."""

    permit_id: str

    def consume(self, *, call_id: str, descriptor_digest: str) -> None:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class VerifiedAuthorizationEvidence:
    call_id: str
    verifier_key: EvidenceVerifierKey
    verifier_instance_id: str
    principal: CapabilityPrincipal
    entrypoint: CapabilityEntrypoint
    owner: CapabilityOwnerRef
    capability_key: str
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    evidence_digest: str
    verification_digest: str
    dispatch_permit: Any = field(repr=False, compare=False)


@dataclass(frozen=True)
class CapabilityPolicyDecision:
    allowed: bool
    reason_code: str
    call_id: str
    descriptor_digest: str
    classification_ruleset_digest: str
    evidence_digest: str
    owner: CapabilityOwnerRef
    granted_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    decision_digest: str
    dispatch_permit: Any | None = field(default=None, repr=False, compare=False)


# ---------------------------------------------------------------------------
# Execution request
# ---------------------------------------------------------------------------


class CapabilityExecutionContext(FrozenContract):
    call_id: str
    run_id: UUID | None = None
    conversation_id: UUID | None = None
    locale: str | None = None
    request_source: str | None = None
    request_channel: str | None = None
    request_session: str | None = None
    request_tool: str | None = None
    nesting_depth: int = 0

    @field_validator("call_id")
    @classmethod
    def _call_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="call_id")

    @field_validator("nesting_depth")
    @classmethod
    def _nesting_depth(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="nesting_depth")

    @field_validator(
        "locale",
        "request_source",
        "request_channel",
        "request_session",
        "request_tool",
    )
    @classmethod
    def _optional_strings(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)


class CapabilityExecutionRequest(FrozenContract):
    binding: FrozenCapabilityBinding
    input: JsonObject
    context: CapabilityExecutionContext
    authorization: CapabilityAuthorizationEvidence

    @field_validator("input", mode="before")
    @classmethod
    def _input_object(cls, value: Any) -> JsonObject:
        return _json_object_copy(value, path="input")


# ---------------------------------------------------------------------------
# Result / errors / events
# ---------------------------------------------------------------------------


class ArtifactRef(FrozenContract):
    artifact_id: str
    media_type: str
    content_digest: str

    @field_validator("artifact_id", "media_type")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("content_digest")
    @classmethod
    def _content_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="content_digest")


class ContinuationRef(FrozenContract):
    continuation_type: str
    contract_version: int
    reference_id: str
    payload_digest: str

    @field_validator("continuation_type", "reference_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("contract_version")
    @classmethod
    def _contract_version(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("contract_version must be >= 1")
        return value

    @field_validator("payload_digest")
    @classmethod
    def _payload_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="payload_digest")


class CapabilityValidationIssue(FrozenContract):
    instance_pointer: str
    schema_pointer: str
    keyword: str
    safe_message: str

    @field_validator("instance_pointer", "schema_pointer", "keyword")
    @classmethod
    def _non_empty(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("safe_message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        return _require_safe_message(value)


class CapabilityError(FrozenContract):
    error_type: Literal[
        "not_found",
        "unavailable",
        "version_drift",
        "unauthorized",
        "invalid_input",
        "invalid_output",
        "timeout",
        "cancelled",
        "execution_failed",
        "unsupported_interrupt",
        "protocol_error",
    ]
    safe_code: str
    safe_message: str
    retry_disposition: Literal[
        "never",
        "new_run_only",
        "same_call_after_reconciliation",
        "model_may_continue",
    ]
    target_identity: str | None = None
    call_id: str | None = None
    validation_issues: tuple[CapabilityValidationIssue, ...] = ()

    @field_validator("safe_code")
    @classmethod
    def _safe_code(cls, value: str) -> str:
        return _require_safe_code(value)

    @field_validator("safe_message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        return _require_safe_message(value)

    @field_validator("target_identity", "call_id")
    @classmethod
    def _optional_ids(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("validation_issues", mode="before")
    @classmethod
    def _issues(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("validation_issues must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _issue_type_rules(self) -> CapabilityError:
        if self.validation_issues and self.error_type not in {"invalid_input", "invalid_output"}:
            raise ValueError("validation_issues only allowed for invalid_input/invalid_output")
        if len(self.validation_issues) > 20:
            raise ValueError("validation_issues capped at 20")
        return self


class CapabilityMetrics(FrozenContract):
    duration_ms: float
    adapter_duration_ms: float | None = None
    input_bytes: int
    output_bytes: int

    @field_validator("duration_ms")
    @classmethod
    def _duration(cls, value: float) -> float:
        return _require_non_negative_float(value, field_name="duration_ms")

    @field_validator("adapter_duration_ms")
    @classmethod
    def _adapter_duration(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _require_non_negative_float(value, field_name="adapter_duration_ms")

    @field_validator("input_bytes", "output_bytes")
    @classmethod
    def _bytes(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)


class CapabilityResult(FrozenContract):
    status: Literal["completed", "failed", "cancelled", "waiting"]
    user_text: str | None
    structured_output: Any | None
    artifact_refs: tuple[ArtifactRef, ...]
    continuation: ContinuationRef | None
    terminal_output: bool
    needs_followup: bool
    error: CapabilityError | None
    metrics: CapabilityMetrics

    @field_validator("structured_output", mode="before")
    @classmethod
    def _structured_output(cls, value: Any) -> Any | None:
        if value is None:
            return None
        return _json_copy(value, path="structured_output")

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _artifact_refs(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("artifact_refs must be a sequence")
        return tuple(value)

    @field_validator("user_text")
    @classmethod
    def _user_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _CONTROL_RE.search(value):
            raise ValueError("user_text must not contain control characters")
        return value

    @model_validator(mode="after")
    def _status_invariants(self) -> CapabilityResult:
        status = self.status
        if status == "completed":
            if self.error is not None:
                raise ValueError("completed result cannot include an error")
            if self.continuation is not None:
                raise ValueError("completed result cannot include a continuation")
        elif status == "failed":
            if self.error is None:
                raise ValueError("failed result requires an error")
            if self.continuation is not None:
                raise ValueError("failed result cannot include a continuation")
        elif status == "cancelled":
            if self.error is None or self.error.error_type != "cancelled":
                raise ValueError("cancelled result requires a cancelled error")
            if self.continuation is not None:
                raise ValueError("cancelled result cannot include a continuation")
        elif status == "waiting":
            if self.continuation is None:
                raise ValueError("waiting result requires a portable continuation")
            if self.error is not None:
                raise ValueError("waiting result cannot include an error")
        return self


class CapabilityEventMetadata(FrozenContract):
    binding_contract_digest: str | None = None
    dependency_closure_digest: str | None = None
    duration_ms: float | None = None
    adapter_duration_ms: float | None = None
    input_bytes: int | None = None
    output_bytes: int | None = None
    child_node_id: str | None = None
    child_node_type: str | None = None
    compatibility_only: bool = False

    @field_validator("binding_contract_digest", "dependency_closure_digest")
    @classmethod
    def _optional_digests(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_digest(value, field_name=info.field_name)

    @field_validator("duration_ms", "adapter_duration_ms")
    @classmethod
    def _optional_duration(cls, value: float | None, info: Any) -> float | None:
        if value is None:
            return None
        return _require_non_negative_float(value, field_name=info.field_name)

    @field_validator("input_bytes", "output_bytes")
    @classmethod
    def _optional_bytes(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("child_node_id", "child_node_type")
    @classmethod
    def _optional_child(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)


class CapabilityRuntimeEvent(FrozenContract):
    event_type: CapabilityEventType
    call_id: str
    capability_key: str
    target_identity: str
    capability_type: Literal["tool", "workflow", "agent"]
    safe_status: str | None = None
    child_event_type: str | None = None
    metadata: CapabilityEventMetadata

    @field_validator("call_id", "capability_key", "target_identity")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("safe_status", "child_event_type")
    @classmethod
    def _optional_status(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_empty_str(value, field_name=info.field_name)


# ---------------------------------------------------------------------------
# Pure result factories
# ---------------------------------------------------------------------------


def completed_result(
    *,
    user_text: str | None = None,
    structured_output: JsonValue | None = None,
    artifact_refs: tuple[ArtifactRef, ...] = (),
    metrics: CapabilityMetrics,
    terminal_output: bool = True,
    needs_followup: bool = False,
) -> CapabilityResult:
    return CapabilityResult(
        status="completed",
        user_text=user_text,
        structured_output=structured_output,
        artifact_refs=artifact_refs,
        continuation=None,
        terminal_output=terminal_output,
        needs_followup=needs_followup,
        error=None,
        metrics=metrics,
    )


def failed_result(
    *,
    error: CapabilityError,
    metrics: CapabilityMetrics,
    user_text: str | None = None,
    structured_output: JsonValue | None = None,
    artifact_refs: tuple[ArtifactRef, ...] = (),
    terminal_output: bool = False,
    needs_followup: bool = False,
) -> CapabilityResult:
    return CapabilityResult(
        status="failed",
        user_text=user_text,
        structured_output=structured_output,
        artifact_refs=artifact_refs,
        continuation=None,
        terminal_output=terminal_output,
        needs_followup=needs_followup,
        error=error,
        metrics=metrics,
    )


def cancelled_result(
    *,
    metrics: CapabilityMetrics,
    call_id: str | None = None,
    target_identity: str | None = None,
    safe_message: str = "cancelled",
) -> CapabilityResult:
    error = CapabilityError(
        error_type="cancelled",
        safe_code="cancelled",
        safe_message=safe_message,
        retry_disposition="never",
        target_identity=target_identity,
        call_id=call_id,
        validation_issues=(),
    )
    return CapabilityResult(
        status="cancelled",
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        continuation=None,
        terminal_output=False,
        needs_followup=False,
        error=error,
        metrics=metrics,
    )


__all__ = [
    "MAX_SAFE_MESSAGE_LEN",
    "ArtifactRef",
    "CapabilityAuthorizationEvidence",
    "CapabilityAvailability",
    "CapabilityBehavior",
    "CapabilityCompletionMetadata",
    "CapabilityDescriptor",
    "CapabilityEntrypoint",
    "CapabilityError",
    "CapabilityEventMetadata",
    "CapabilityEventType",
    "CapabilityExecutionContext",
    "CapabilityExecutionRequest",
    "CapabilityMetrics",
    "CapabilityOwnerRef",
    "CapabilityPolicyDecision",
    "CapabilityPrincipal",
    "CapabilityResult",
    "CapabilityRuntimeEvent",
    "CapabilityTimeoutPolicy",
    "CapabilityValidationIssue",
    "ClassificationContractRef",
    "ContinuationRef",
    "EvidenceIssuer",
    "EvidenceVerifierKey",
    "FrozenBindingProvenance",
    "FrozenCapabilityBinding",
    "ResolvedCapabilityBinding",
    "ResolvedCapabilityDependency",
    "ResolvedCapabilityRef",
    "SideEffectClass",
    "VerifiedAuthorizationEvidence",
    "cancelled_result",
    "completed_result",
    "failed_result",
    "project_frozen_capability_binding",
]
