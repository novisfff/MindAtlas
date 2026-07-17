"""Plan 05 pure policy contracts: exposures, budgets, grants, decisions.

Task 1 freezes the portable contract/evaluation-layer types and pure builders.
Task 2 adds EffectiveCapabilityGrant and AuthorizationDecision plus digests.
Budget ledger and Gateway integration are later tasks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator, model_validator

from app.assistant.capabilities.contracts import CapabilityPrincipal, SideEffectClass
from app.assistant.domain.contracts import FrozenContract, ResolvedCapabilityRef
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.main_agent.authorization import (
    MAIN_AGENT_CEILING_REVISION,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
)

# ---------------------------------------------------------------------------
# Vocabulary / limits
# ---------------------------------------------------------------------------

PolicyOwnerKind = Literal["main_agent", "skill_version"]
PolicyEntrypoint = Literal["main_agent"]

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# Checked-in assistant_chat entrypoint defaults (Plan 05 §6.1).
ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS: dict[str, int | None] = {
    "max_provider_rounds": 8,
    "max_main_agent_cycles": 1,
    "max_active_skills": 4,
    "max_total_capability_calls": 16,
    "max_parallel_calls": 4,
    "max_capability_depth": 4,
    "max_agent_depth": 2,
    "max_same_read_signature": 3,
    "max_prompt_tokens": None,
    "max_completion_tokens": 4096,
    "max_wall_time_ms": 120_000,
    "max_completion_followup_rounds": 2,
}

# Checked-in hard ceilings. Operator/Profile may only lower these.
ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS: dict[str, int | None] = {
    "max_provider_rounds": 16,
    "max_main_agent_cycles": 1,
    "max_active_skills": 8,
    "max_total_capability_calls": 64,
    "max_parallel_calls": 8,
    "max_capability_depth": 8,
    "max_agent_depth": 4,
    "max_same_read_signature": 10,
    "max_prompt_tokens": 1_000_000,
    "max_completion_tokens": 16_384,
    "max_wall_time_ms": 600_000,
    "max_completion_followup_rounds": 4,
}

MAIN_AGENT_OWNER_DEFAULT_MAX_CALLS = 8

# Profile output_budget field → RunBudgetLimits field mapping.
_PROFILE_OUTPUT_BUDGET_FIELDS: tuple[tuple[str, str], ...] = (
    ("max_provider_rounds", "max_provider_rounds"),
    ("max_total_capability_calls", "max_total_capability_calls"),
    ("max_parallel_calls", "max_parallel_calls"),
    ("max_capability_depth", "max_capability_depth"),
    ("max_agent_depth", "max_agent_depth"),
    ("max_same_read_signature", "max_same_read_signature"),
    ("max_completion_tokens", "max_completion_tokens"),
    ("max_wall_time_ms", "max_wall_time_ms"),
    ("max_completion_followup_rounds", "max_completion_followup_rounds"),
)

# Profile context_budget field → RunBudgetLimits field mapping.
# max_active_skills lives on ContextBudgetV1, not OutputBudgetV1 (Plan 05 §6.1).
_PROFILE_CONTEXT_BUDGET_FIELDS: tuple[tuple[str, str], ...] = (
    ("max_active_skills", "max_active_skills"),
)

# Entrypoint/global policy digests for the immutable assistant_chat / Plan 05 gate.
ASSISTANT_CHAT_ENTRYPOINT_POLICY_REVISION: Literal["plan05-v1"] = "plan05-v1"
PLAN05_RELEASE_GATE_SIDE_EFFECTS: tuple[str, ...] = ("none", "compute", "read")


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest"
        )
    return value


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _uuid_bytes_key(value: UUID) -> bytes:
    return value.bytes


def _sorted_uuid_tuple(values: Sequence[UUID]) -> tuple[UUID, ...]:
    return tuple(sorted(values, key=_uuid_bytes_key))


def _uuid_json(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _capability_ref_payload(capability: ResolvedCapabilityRef) -> dict[str, JsonValue]:
    """Canonical ResolvedCapabilityRef payload (byte-compatible with Manifest digests)."""
    return {
        "capabilityType": capability.capability_type,
        "capabilityKey": capability.capability_key,
        "targetIdentity": capability.target_identity,
        "targetId": _uuid_json(capability.target_id),
        "targetVersionId": _uuid_json(capability.target_version_id),
        "targetRevision": capability.target_revision,
        "inputSchemaDigest": capability.input_schema_digest,
        "outputSchemaDigest": capability.output_schema_digest,
        "resolutionDigest": capability.resolution_digest,
        "dependencyClosureDigest": capability.dependency_closure_digest,
        "bindingContractDigest": capability.binding_contract_digest,
    }


# ---------------------------------------------------------------------------
# Owner policy / budget contracts
# ---------------------------------------------------------------------------


class OwnerPolicyRef(FrozenContract):
    """Minimal immutable owner policy identity for grant_source_set digests."""

    owner_kind: PolicyOwnerKind
    owner_id: str
    owner_version_id: UUID
    policy_digest: str

    @field_validator("owner_id")
    @classmethod
    def _owner_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="owner_id")

    @field_validator("policy_digest")
    @classmethod
    def _policy_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="policy_digest")


class OwnerBudgetLimits(FrozenContract):
    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    max_calls: int
    max_same_read_signature: int
    owner_budget_digest: str

    @field_validator("max_calls", "max_same_read_signature")
    @classmethod
    def _non_negative(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("owner_budget_digest")
    @classmethod
    def _owner_budget_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="owner_budget_digest")


class RunBudgetLimits(FrozenContract):
    max_provider_rounds: int
    max_main_agent_cycles: int
    max_active_skills: int
    max_total_capability_calls: int
    max_parallel_calls: int
    max_capability_depth: int
    max_agent_depth: int
    max_same_read_signature: int
    max_prompt_tokens: int | None
    max_completion_tokens: int | None
    max_wall_time_ms: int
    max_completion_followup_rounds: int

    @field_validator(
        "max_provider_rounds",
        "max_main_agent_cycles",
        "max_active_skills",
        "max_total_capability_calls",
        "max_parallel_calls",
        "max_capability_depth",
        "max_agent_depth",
        "max_same_read_signature",
        "max_wall_time_ms",
        "max_completion_followup_rounds",
    )
    @classmethod
    def _positive(cls, value: int, info: Any) -> int:
        return _require_positive_int(value, field_name=info.field_name)

    @field_validator("max_prompt_tokens", "max_completion_tokens")
    @classmethod
    def _optional_positive(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        return _require_positive_int(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _coherence(self) -> RunBudgetLimits:
        if self.max_parallel_calls > self.max_total_capability_calls:
            raise ValueError("max_parallel_calls must be <= max_total_capability_calls")
        if self.max_same_read_signature > self.max_total_capability_calls:
            raise ValueError(
                "max_same_read_signature must be <= max_total_capability_calls"
            )
        if self.max_completion_followup_rounds >= self.max_provider_rounds:
            raise ValueError(
                "max_completion_followup_rounds must be < max_provider_rounds"
            )
        return self


# ---------------------------------------------------------------------------
# Exposure contracts
# ---------------------------------------------------------------------------


class CapabilityExposureRef(FrozenContract):
    domain_key: str
    resolved_ref: ResolvedCapabilityRef
    binding_contract_digest: str
    descriptor_digest: str
    owner_kind: PolicyOwnerKind
    owner_id: str
    owner_version_id: UUID
    compatible_consumer_version_ids: tuple[UUID, ...]
    exposure_digest: str

    @field_validator("domain_key", "owner_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator(
        "binding_contract_digest",
        "descriptor_digest",
        "exposure_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("compatible_consumer_version_ids", mode="before")
    @classmethod
    def _consumers(cls, value: Any) -> tuple[UUID, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("compatible_consumer_version_ids must be a sequence of UUIDs")
        out: list[UUID] = []
        for item in value:
            if isinstance(item, UUID):
                out.append(item)
            elif isinstance(item, str):
                out.append(UUID(item))
            else:
                raise TypeError("compatible_consumer_version_ids items must be UUID")
        return _sorted_uuid_tuple(out)

    @model_validator(mode="after")
    def _binding_matches_ref(self) -> CapabilityExposureRef:
        if self.domain_key != self.resolved_ref.capability_key:
            raise ValueError("domain_key must equal resolved_ref.capability_key")
        if self.binding_contract_digest != self.resolved_ref.binding_contract_digest:
            raise ValueError(
                "binding_contract_digest must equal resolved_ref.binding_contract_digest"
            )
        return self


class ManifestExposureIndex(FrozenContract):
    manifest_revision: int
    manifest_digest: str
    exposures: tuple[CapabilityExposureRef, ...]
    exposure_index_digest: str

    @field_validator("manifest_revision")
    @classmethod
    def _revision(cls, value: int) -> int:
        return _require_positive_int(value, field_name="manifest_revision")

    @field_validator("manifest_digest", "exposure_index_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("exposures", mode="before")
    @classmethod
    def _exposures(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("exposures must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _ordered_unique(self) -> ManifestExposureIndex:
        keys = [item.domain_key for item in self.exposures]
        if len(keys) != len(set(keys)):
            raise ValueError("exposures must have unique domain_key values")
        ordered = sorted(keys, key=lambda k: k.encode("utf-8"))
        if keys != ordered:
            raise ValueError("exposures must be sorted by domain_key UTF-8 bytes")
        return self


class EffectiveRunPolicySnapshot(FrozenContract):
    policy_contract_version: Literal[1] = 1
    app_build_revision: str
    run_id: UUID
    principal: CapabilityPrincipal
    entrypoint: PolicyEntrypoint
    main_agent_profile_version_id: UUID
    main_agent_profile_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    exposure_index: ManifestExposureIndex
    owner_policy_refs: tuple[OwnerPolicyRef, ...]
    grant_source_set_digest: str
    run_budget_limits: RunBudgetLimits
    effective_policy_digest: str

    @field_validator("app_build_revision")
    @classmethod
    def _build(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="app_build_revision")

    @field_validator(
        "main_agent_profile_digest",
        "entrypoint_policy_digest",
        "global_policy_digest",
        "grant_source_set_digest",
        "effective_policy_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("owner_policy_refs", mode="before")
    @classmethod
    def _owner_refs(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("owner_policy_refs must be a sequence")
        return tuple(value)


# ---------------------------------------------------------------------------
# Grant + decision contracts (Plan 05 Task 2)
# ---------------------------------------------------------------------------


class EffectiveCapabilityGrant(FrozenContract):
    """Independent exact-binding grant (never built from descriptor behavior)."""

    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    capability_key: str
    binding_contract_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none", "durable"], ...]
    platform_ceiling_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    owner_policy_digest: str
    grant_source_digest: str

    @field_validator("capability_key")
    @classmethod
    def _capability_key(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="capability_key")

    @field_validator(
        "binding_contract_digest",
        "platform_ceiling_digest",
        "entrypoint_policy_digest",
        "global_policy_digest",
        "owner_policy_digest",
        "grant_source_digest",
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

    @field_validator("allowed_interrupt_modes", mode="before")
    @classmethod
    def _interrupt_modes(cls, value: Any) -> tuple[Literal["none"], ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("allowed_interrupt_modes must be a sequence")
        out = tuple(value)
        for item in out:
            if item not in {"none", "durable"}:
                raise ValueError("unsupported allowed interrupt mode")
        return out  # type: ignore[return-value]

    @model_validator(mode="after")
    def _nonempty_grant(self) -> EffectiveCapabilityGrant:
        if not self.allowed_side_effects:
            raise ValueError("allowed_side_effects must be nonempty for a grant")
        if "unknown" in self.allowed_side_effects:
            raise ValueError("allowed_side_effects must not contain unknown")
        return self


class AuthorizationDecision(FrozenContract):
    """Pure ordered authorization decision (Plan 05 §5.4)."""

    allowed: bool
    reason_code: str
    principal_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    owner_policy_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...] = ()
    grant_source_digest: str | None = None
    exposure_digest: str
    effective_policy_digest: str
    decision_digest: str

    @field_validator("reason_code")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="reason_code")

    @field_validator(
        "principal_digest",
        "entrypoint_policy_digest",
        "global_policy_digest",
        "owner_policy_digest",
        "exposure_digest",
        "effective_policy_digest",
        "decision_digest",
    )
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("grant_source_digest")
    @classmethod
    def _optional_grant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, field_name="grant_source_digest")

    @field_validator("allowed_side_effects", mode="before")
    @classmethod
    def _side_effects(cls, value: Any) -> tuple[SideEffectClass, ...]:
        if value is None:
            return ()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("allowed_side_effects must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _coherence(self) -> AuthorizationDecision:
        if self.allowed:
            if self.reason_code != "allowed":
                raise ValueError("allowed decision must use reason_code='allowed'")
            if not self.allowed_side_effects:
                raise ValueError("allowed decision requires nonempty allowed_side_effects")
            if self.grant_source_digest is None:
                raise ValueError("allowed decision requires grant_source_digest")
        else:
            if self.reason_code == "allowed":
                raise ValueError("denied decision cannot use reason_code='allowed'")
        return self


# Stable ordered reason codes (Plan 05 §5.4).
AUTHORIZATION_REASON_CODES: tuple[str, ...] = (
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
    "allowed",
)

# Explicit empty digests for early denials (no prose/user data).
EMPTY_POLICY_DIGEST = "0" * 64


# ---------------------------------------------------------------------------
# Digest payloads / builders
# ---------------------------------------------------------------------------


def compute_assistant_chat_entrypoint_policy_digest() -> str:
    """Immutable assistant_chat entrypoint policy (Plan 05 §5.3)."""
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "assistant_chat_entrypoint_policy",
            "revision": ASSISTANT_CHAT_ENTRYPOINT_POLICY_REVISION,
            "entrypoint": "assistant_chat",
            "allowedSideEffects": list(PLAN05_RELEASE_GATE_SIDE_EFFECTS),
            "allowedInterruptModes": list(
                MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_interrupt_modes
            ),
            "platformCeilingDigest": MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
        }
    )


def compute_default_global_policy_digest(
    *,
    profile_content_digest: str,
    deny_by_default: bool = True,
) -> str:
    """Deny-by-default Profile global policy digest (identity only)."""
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "main_agent_global_policy",
            "denyByDefault": deny_by_default,
            "profileContentDigest": profile_content_digest,
            "allowedSideEffects": list(PLAN05_RELEASE_GATE_SIDE_EFFECTS),
        }
    )


def compute_owner_policy_digest(
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    content_or_policy_digest: str,
    allowed_side_effects: Sequence[str] = (),
    max_skill_calls: int | None = None,
    max_same_read_calls: int | None = None,
    requires_terminal_output: bool | None = None,
    terminal_text_allowed: bool | None = None,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "owner_policy",
            "ownerKind": owner_kind,
            "ownerId": owner_id,
            "ownerVersionId": str(owner_version_id),
            "contentOrPolicyDigest": content_or_policy_digest,
            "allowedSideEffects": list(allowed_side_effects),
            "maxSkillCalls": max_skill_calls,
            "maxSameReadCalls": max_same_read_calls,
            "requiresTerminalOutput": requires_terminal_output,
            "terminalTextAllowed": terminal_text_allowed,
        }
    )


def build_owner_policy_ref(
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    content_or_policy_digest: str,
    allowed_side_effects: Sequence[str] = (),
    max_skill_calls: int | None = None,
    max_same_read_calls: int | None = None,
    requires_terminal_output: bool | None = None,
    terminal_text_allowed: bool | None = None,
) -> OwnerPolicyRef:
    digest = compute_owner_policy_digest(
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        content_or_policy_digest=content_or_policy_digest,
        allowed_side_effects=allowed_side_effects,
        max_skill_calls=max_skill_calls,
        max_same_read_calls=max_same_read_calls,
        requires_terminal_output=requires_terminal_output,
        terminal_text_allowed=terminal_text_allowed,
    )
    return OwnerPolicyRef(
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        policy_digest=digest,
    )


def compute_principal_digest(principal: CapabilityPrincipal) -> str:
    """Identity-only principal digest (no secrets/prose)."""
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "capability_principal",
            "principalType": principal.principal_type,
            "principalId": principal.principal_id,
            "authenticated": principal.authenticated,
        }
    )


def build_grant_source_digest_payload(
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    capability_key: str,
    binding_contract_digest: str,
    allowed_side_effects: Sequence[SideEffectClass],
    allowed_interrupt_modes: Sequence[str],
    platform_ceiling_digest: str,
    platform_ceiling_revision: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
) -> dict[str, JsonValue]:
    """Payload for grant_source_digest (excludes descriptor behavior)."""
    return {
        "schemaVersion": 1,
        "kind": "effective_capability_grant_source",
        "ownerKind": owner_kind,
        "ownerId": owner_id,
        "ownerVersionId": str(owner_version_id),
        "capabilityKey": capability_key,
        "bindingContractDigest": binding_contract_digest,
        "allowedSideEffects": list(allowed_side_effects),
        "allowedInterruptModes": list(allowed_interrupt_modes),
        "platformCeilingDigest": platform_ceiling_digest,
        "platformCeilingRevision": platform_ceiling_revision,
        "entrypointPolicyDigest": entrypoint_policy_digest,
        "globalPolicyDigest": global_policy_digest,
        "ownerPolicyDigest": owner_policy_digest,
    }


def compute_grant_source_digest(
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    capability_key: str,
    binding_contract_digest: str,
    allowed_side_effects: Sequence[SideEffectClass],
    allowed_interrupt_modes: Sequence[str],
    platform_ceiling_digest: str,
    platform_ceiling_revision: str = MAIN_AGENT_CEILING_REVISION,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
) -> str:
    return sha256_canonical_json(
        build_grant_source_digest_payload(
            owner_kind=owner_kind,
            owner_id=owner_id,
            owner_version_id=owner_version_id,
            capability_key=capability_key,
            binding_contract_digest=binding_contract_digest,
            allowed_side_effects=allowed_side_effects,
            allowed_interrupt_modes=allowed_interrupt_modes,
            platform_ceiling_digest=platform_ceiling_digest,
            platform_ceiling_revision=platform_ceiling_revision,
            entrypoint_policy_digest=entrypoint_policy_digest,
            global_policy_digest=global_policy_digest,
            owner_policy_digest=owner_policy_digest,
        )
    )


def build_effective_capability_grant(
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    capability_key: str,
    binding_contract_digest: str,
    allowed_side_effects: Sequence[SideEffectClass],
    allowed_interrupt_modes: Sequence[str] = ("none",),
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    platform_ceiling_revision: str = MAIN_AGENT_CEILING_REVISION,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
    grant_source_digest: str | None = None,
) -> EffectiveCapabilityGrant:
    """Build a frozen EffectiveCapabilityGrant with recomputed grant_source_digest."""
    effects = tuple(allowed_side_effects)
    modes: tuple[Literal["none", "durable"], ...] = tuple(  # type: ignore[assignment]
        allowed_interrupt_modes  # type: ignore[arg-type]
    )
    digest = grant_source_digest or compute_grant_source_digest(
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        capability_key=capability_key,
        binding_contract_digest=binding_contract_digest,
        allowed_side_effects=effects,
        allowed_interrupt_modes=modes,
        platform_ceiling_digest=platform_ceiling_digest,
        platform_ceiling_revision=platform_ceiling_revision,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner_policy_digest,
    )
    return EffectiveCapabilityGrant(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        capability_key=capability_key,
        binding_contract_digest=binding_contract_digest,
        allowed_side_effects=effects,
        allowed_interrupt_modes=modes,
        platform_ceiling_digest=platform_ceiling_digest,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner_policy_digest,
        grant_source_digest=digest,
    )


def build_authorization_decision_digest_payload(
    *,
    allowed: bool,
    reason_code: str,
    principal_digest: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
    allowed_side_effects: Sequence[SideEffectClass],
    grant_source_digest: str | None,
    exposure_digest: str,
    effective_policy_digest: str,
) -> dict[str, JsonValue]:
    return {
        "schemaVersion": 1,
        "kind": "authorization_decision",
        "allowed": allowed,
        "reasonCode": reason_code,
        "principalDigest": principal_digest,
        "entrypointPolicyDigest": entrypoint_policy_digest,
        "globalPolicyDigest": global_policy_digest,
        "ownerPolicyDigest": owner_policy_digest,
        "allowedSideEffects": list(allowed_side_effects),
        "grantSourceDigest": grant_source_digest,
        "exposureDigest": exposure_digest,
        "effectivePolicyDigest": effective_policy_digest,
    }


def compute_authorization_decision_digest(
    *,
    allowed: bool,
    reason_code: str,
    principal_digest: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
    allowed_side_effects: Sequence[SideEffectClass],
    grant_source_digest: str | None,
    exposure_digest: str,
    effective_policy_digest: str,
) -> str:
    return sha256_canonical_json(
        build_authorization_decision_digest_payload(
            allowed=allowed,
            reason_code=reason_code,
            principal_digest=principal_digest,
            entrypoint_policy_digest=entrypoint_policy_digest,
            global_policy_digest=global_policy_digest,
            owner_policy_digest=owner_policy_digest,
            allowed_side_effects=allowed_side_effects,
            grant_source_digest=grant_source_digest,
            exposure_digest=exposure_digest,
            effective_policy_digest=effective_policy_digest,
        )
    )


def build_authorization_decision(
    *,
    allowed: bool,
    reason_code: str,
    principal_digest: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    owner_policy_digest: str,
    allowed_side_effects: Sequence[SideEffectClass] = (),
    grant_source_digest: str | None = None,
    exposure_digest: str,
    effective_policy_digest: str,
    decision_digest: str | None = None,
) -> AuthorizationDecision:
    effects = tuple(allowed_side_effects)
    digest = decision_digest or compute_authorization_decision_digest(
        allowed=allowed,
        reason_code=reason_code,
        principal_digest=principal_digest,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner_policy_digest,
        allowed_side_effects=effects,
        grant_source_digest=grant_source_digest,
        exposure_digest=exposure_digest,
        effective_policy_digest=effective_policy_digest,
    )
    return AuthorizationDecision(
        allowed=allowed,
        reason_code=reason_code,
        principal_digest=principal_digest,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner_policy_digest,
        allowed_side_effects=effects,
        grant_source_digest=grant_source_digest,
        exposure_digest=exposure_digest,
        effective_policy_digest=effective_policy_digest,
        decision_digest=digest,
    )


def compute_owner_budget_digest(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    max_calls: int,
    max_same_read_signature: int,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "owner_budget_limits",
            "ownerKind": owner_kind,
            "ownerVersionId": str(owner_version_id),
            "maxCalls": max_calls,
            "maxSameReadSignature": max_same_read_signature,
        }
    )


def build_owner_budget_limits(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    max_calls: int,
    max_same_read_signature: int,
) -> OwnerBudgetLimits:
    digest = compute_owner_budget_digest(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        max_calls=max_calls,
        max_same_read_signature=max_same_read_signature,
    )
    return OwnerBudgetLimits(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        max_calls=max_calls,
        max_same_read_signature=max_same_read_signature,
        owner_budget_digest=digest,
    )


def build_exposure_digest_payload(
    *,
    domain_key: str,
    resolved_ref: ResolvedCapabilityRef,
    binding_contract_digest: str,
    descriptor_digest: str,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    compatible_consumer_version_ids: Sequence[UUID],
) -> dict[str, JsonValue]:
    return {
        "schemaVersion": 1,
        "kind": "capability_exposure_ref",
        "domainKey": domain_key,
        "resolvedRef": _capability_ref_payload(resolved_ref),
        "bindingContractDigest": binding_contract_digest,
        "descriptorDigest": descriptor_digest,
        "ownerKind": owner_kind,
        "ownerId": owner_id,
        "ownerVersionId": str(owner_version_id),
        "compatibleConsumerVersionIds": [
            str(item) for item in _sorted_uuid_tuple(compatible_consumer_version_ids)
        ],
    }


def compute_exposure_digest(
    *,
    domain_key: str,
    resolved_ref: ResolvedCapabilityRef,
    binding_contract_digest: str,
    descriptor_digest: str,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    compatible_consumer_version_ids: Sequence[UUID] = (),
) -> str:
    return sha256_canonical_json(
        build_exposure_digest_payload(
            domain_key=domain_key,
            resolved_ref=resolved_ref,
            binding_contract_digest=binding_contract_digest,
            descriptor_digest=descriptor_digest,
            owner_kind=owner_kind,
            owner_id=owner_id,
            owner_version_id=owner_version_id,
            compatible_consumer_version_ids=compatible_consumer_version_ids,
        )
    )


def build_capability_exposure_ref(
    *,
    domain_key: str,
    resolved_ref: ResolvedCapabilityRef,
    binding_contract_digest: str,
    descriptor_digest: str,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
    compatible_consumer_version_ids: Sequence[UUID] = (),
) -> CapabilityExposureRef:
    consumers = _sorted_uuid_tuple(compatible_consumer_version_ids)
    digest = compute_exposure_digest(
        domain_key=domain_key,
        resolved_ref=resolved_ref,
        binding_contract_digest=binding_contract_digest,
        descriptor_digest=descriptor_digest,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        compatible_consumer_version_ids=consumers,
    )
    return CapabilityExposureRef(
        domain_key=domain_key,
        resolved_ref=resolved_ref,
        binding_contract_digest=binding_contract_digest,
        descriptor_digest=descriptor_digest,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_version_id=owner_version_id,
        compatible_consumer_version_ids=consumers,
        exposure_digest=digest,
    )


def build_exposure_index_digest_payload(
    *,
    manifest_revision: int,
    manifest_digest: str,
    exposures: Sequence[CapabilityExposureRef],
) -> dict[str, JsonValue]:
    """Digest payload for ManifestExposureIndex.

    ``manifest_digest`` is intentionally *not* included in the digest payload.
    The index still carries ``manifest_digest`` as an association field, but
    including it in the digest creates a cycle with EffectiveRunPolicySnapshot
    (policy digest → Manifest.effective_policy_digest → Manifest.manifest_digest
    → exposure_index_digest → policy digest). Association is verified separately
    by the evaluator's surface/manifest identity checks.
    """
    del manifest_digest  # association field only; not part of digest identity
    ordered = tuple(sorted(exposures, key=lambda item: item.domain_key.encode("utf-8")))
    return {
        "schemaVersion": 1,
        "kind": "manifest_exposure_index",
        "manifestRevision": manifest_revision,
        "exposures": [
            {
                "domainKey": item.domain_key,
                "exposureDigest": item.exposure_digest,
                "ownerKind": item.owner_kind,
                "ownerId": item.owner_id,
                "ownerVersionId": str(item.owner_version_id),
                "bindingContractDigest": item.binding_contract_digest,
                "descriptorDigest": item.descriptor_digest,
                "compatibleConsumerVersionIds": [
                    str(cid) for cid in item.compatible_consumer_version_ids
                ],
            }
            for item in ordered
        ],
    }


def compute_exposure_index_digest(
    *,
    manifest_revision: int,
    manifest_digest: str,
    exposures: Sequence[CapabilityExposureRef],
) -> str:
    return sha256_canonical_json(
        build_exposure_index_digest_payload(
            manifest_revision=manifest_revision,
            manifest_digest=manifest_digest,
            exposures=exposures,
        )
    )


def build_manifest_exposure_index(
    *,
    manifest_revision: int,
    manifest_digest: str,
    exposures: Sequence[CapabilityExposureRef],
) -> ManifestExposureIndex:
    ordered = tuple(sorted(exposures, key=lambda item: item.domain_key.encode("utf-8")))
    digest = compute_exposure_index_digest(
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        exposures=ordered,
    )
    return ManifestExposureIndex(
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        exposures=ordered,
        exposure_index_digest=digest,
    )


def compute_grant_source_set_digest(
    *,
    owner_policy_refs: Sequence[OwnerPolicyRef],
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    entrypoint_policy_digest: str | None = None,
    global_policy_digest: str | None = None,
) -> str:
    ordered = tuple(
        sorted(
            owner_policy_refs,
            key=lambda item: (
                item.owner_kind,
                item.owner_id,
                item.owner_version_id.bytes,
            ),
        )
    )
    entrypoint = entrypoint_policy_digest or compute_assistant_chat_entrypoint_policy_digest()
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "grant_source_set",
            "platformCeilingDigest": platform_ceiling_digest,
            "entrypointPolicyDigest": entrypoint,
            "globalPolicyDigest": global_policy_digest,
            "ownerPolicies": [
                {
                    "ownerKind": item.owner_kind,
                    "ownerId": item.owner_id,
                    "ownerVersionId": str(item.owner_version_id),
                    "policyDigest": item.policy_digest,
                }
                for item in ordered
            ],
        }
    )


def build_run_budget_limits_payload(limits: RunBudgetLimits) -> dict[str, JsonValue]:
    return {
        "maxProviderRounds": limits.max_provider_rounds,
        "maxMainAgentCycles": limits.max_main_agent_cycles,
        "maxActiveSkills": limits.max_active_skills,
        "maxTotalCapabilityCalls": limits.max_total_capability_calls,
        "maxParallelCalls": limits.max_parallel_calls,
        "maxCapabilityDepth": limits.max_capability_depth,
        "maxAgentDepth": limits.max_agent_depth,
        "maxSameReadSignature": limits.max_same_read_signature,
        "maxPromptTokens": limits.max_prompt_tokens,
        "maxCompletionTokens": limits.max_completion_tokens,
        "maxWallTimeMs": limits.max_wall_time_ms,
        "maxCompletionFollowupRounds": limits.max_completion_followup_rounds,
    }


def build_effective_policy_digest_payload(
    *,
    policy_contract_version: int,
    app_build_revision: str,
    run_id: UUID,
    principal: CapabilityPrincipal,
    entrypoint: PolicyEntrypoint,
    main_agent_profile_version_id: UUID,
    main_agent_profile_digest: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    exposure_index: ManifestExposureIndex,
    owner_policy_refs: Sequence[OwnerPolicyRef],
    grant_source_set_digest: str,
    run_budget_limits: RunBudgetLimits,
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    release_gate_side_effects: Sequence[str] = PLAN05_RELEASE_GATE_SIDE_EFFECTS,
) -> dict[str, JsonValue]:
    ordered_owners = tuple(
        sorted(
            owner_policy_refs,
            key=lambda item: (
                item.owner_kind,
                item.owner_id,
                item.owner_version_id.bytes,
            ),
        )
    )
    return {
        "schemaVersion": 1,
        "kind": "effective_run_policy_snapshot",
        "policyContractVersion": policy_contract_version,
        "appBuildRevision": app_build_revision,
        "runId": str(run_id),
        "principal": {
            "principalType": principal.principal_type,
            "principalId": principal.principal_id,
            "authenticated": principal.authenticated,
        },
        "entrypoint": entrypoint,
        "mainAgentProfileVersionId": str(main_agent_profile_version_id),
        "mainAgentProfileDigest": main_agent_profile_digest,
        "entrypointPolicyDigest": entrypoint_policy_digest,
        "globalPolicyDigest": global_policy_digest,
        "platformCeilingDigest": platform_ceiling_digest,
        "releaseGateSideEffects": list(release_gate_side_effects),
        "exposureIndexDigest": exposure_index.exposure_index_digest,
        "ownerPolicyRefs": [
            {
                "ownerKind": item.owner_kind,
                "ownerId": item.owner_id,
                "ownerVersionId": str(item.owner_version_id),
                "policyDigest": item.policy_digest,
            }
            for item in ordered_owners
        ],
        "grantSourceSetDigest": grant_source_set_digest,
        "runBudgetLimits": build_run_budget_limits_payload(run_budget_limits),
    }


def compute_effective_policy_digest(
    *,
    policy_contract_version: int,
    app_build_revision: str,
    run_id: UUID,
    principal: CapabilityPrincipal,
    entrypoint: PolicyEntrypoint,
    main_agent_profile_version_id: UUID,
    main_agent_profile_digest: str,
    entrypoint_policy_digest: str,
    global_policy_digest: str,
    exposure_index: ManifestExposureIndex,
    owner_policy_refs: Sequence[OwnerPolicyRef],
    grant_source_set_digest: str,
    run_budget_limits: RunBudgetLimits,
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    release_gate_side_effects: Sequence[str] = PLAN05_RELEASE_GATE_SIDE_EFFECTS,
) -> str:
    return sha256_canonical_json(
        build_effective_policy_digest_payload(
            policy_contract_version=policy_contract_version,
            app_build_revision=app_build_revision,
            run_id=run_id,
            principal=principal,
            entrypoint=entrypoint,
            main_agent_profile_version_id=main_agent_profile_version_id,
            main_agent_profile_digest=main_agent_profile_digest,
            entrypoint_policy_digest=entrypoint_policy_digest,
            global_policy_digest=global_policy_digest,
            exposure_index=exposure_index,
            owner_policy_refs=owner_policy_refs,
            grant_source_set_digest=grant_source_set_digest,
            run_budget_limits=run_budget_limits,
            platform_ceiling_digest=platform_ceiling_digest,
            release_gate_side_effects=release_gate_side_effects,
        )
    )


def build_effective_run_policy_snapshot(
    *,
    app_build_revision: str,
    run_id: UUID,
    principal: CapabilityPrincipal,
    main_agent_profile_version_id: UUID,
    main_agent_profile_digest: str,
    entrypoint_policy_digest: str | None = None,
    global_policy_digest: str | None = None,
    exposure_index: ManifestExposureIndex,
    owner_policy_refs: Sequence[OwnerPolicyRef],
    run_budget_limits: RunBudgetLimits,
    grant_source_set_digest: str | None = None,
    entrypoint: PolicyEntrypoint = "main_agent",
    policy_contract_version: Literal[1] = 1,
) -> EffectiveRunPolicySnapshot:
    """Build a frozen EffectiveRunPolicySnapshot with recomputed digests."""
    entrypoint_digest = (
        entrypoint_policy_digest or compute_assistant_chat_entrypoint_policy_digest()
    )
    global_digest = global_policy_digest or compute_default_global_policy_digest(
        profile_content_digest=main_agent_profile_digest
    )
    ordered_owners = tuple(
        sorted(
            owner_policy_refs,
            key=lambda item: (
                item.owner_kind,
                item.owner_id,
                item.owner_version_id.bytes,
            ),
        )
    )
    grant_digest = grant_source_set_digest or compute_grant_source_set_digest(
        owner_policy_refs=ordered_owners,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
    )
    effective_digest = compute_effective_policy_digest(
        policy_contract_version=policy_contract_version,
        app_build_revision=app_build_revision,
        run_id=run_id,
        principal=principal,
        entrypoint=entrypoint,
        main_agent_profile_version_id=main_agent_profile_version_id,
        main_agent_profile_digest=main_agent_profile_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        exposure_index=exposure_index,
        owner_policy_refs=ordered_owners,
        grant_source_set_digest=grant_digest,
        run_budget_limits=run_budget_limits,
    )
    return EffectiveRunPolicySnapshot(
        policy_contract_version=policy_contract_version,
        app_build_revision=app_build_revision,
        run_id=run_id,
        principal=principal,
        entrypoint=entrypoint,
        main_agent_profile_version_id=main_agent_profile_version_id,
        main_agent_profile_digest=main_agent_profile_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        exposure_index=exposure_index,
        owner_policy_refs=ordered_owners,
        grant_source_set_digest=grant_digest,
        run_budget_limits=run_budget_limits,
        effective_policy_digest=effective_digest,
    )


# ---------------------------------------------------------------------------
# Run budget limit normalization
# ---------------------------------------------------------------------------


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("budget values must be integers or None")
    return value


def _min_positive(
    *,
    hard_ceiling: int | None,
    entrypoint_default: int | None,
    operator: int | None,
    profile: int | None,
    field_name: str,
) -> int | None:
    """Resolve one limit as min(hard, entrypoint, optional operator lower-only, profile)."""
    candidates: list[int] = []
    if hard_ceiling is not None:
        candidates.append(hard_ceiling)
    if entrypoint_default is not None:
        candidates.append(entrypoint_default)
    if operator is not None:
        if hard_ceiling is not None and operator > hard_ceiling:
            # Operator may only lower; clamp to hard ceiling rather than raise.
            operator = hard_ceiling
        candidates.append(operator)
    if profile is not None:
        if hard_ceiling is not None and profile > hard_ceiling:
            profile = hard_ceiling
        candidates.append(profile)
    if not candidates:
        return None
    result = min(candidates)
    if result < 1:
        raise ValueError(f"{field_name} resolved below 1")
    return result


def _extract_profile_budget_fields(
    source: Any,
    field_pairs: Sequence[tuple[str, str]],
) -> dict[str, int | None]:
    """Pull known budget fields from a Mapping or FrozenContract-like object."""
    out: dict[str, int | None] = {}
    if source is None:
        return out
    if isinstance(source, Mapping):
        mapping = source
        for profile_field, limit_field in field_pairs:
            raw = mapping.get(profile_field, mapping.get(_to_camel(profile_field)))
            if raw is not None:
                out[limit_field] = _as_optional_int(raw)
        return out
    for profile_field, limit_field in field_pairs:
        raw = getattr(source, profile_field, None)
        if raw is not None:
            out[limit_field] = _as_optional_int(raw)
    return out


def normalize_run_budget_limits(
    *,
    operator_limits: Mapping[str, int | None] | None = None,
    profile_output_budget: Any | None = None,
    profile_context_budget: Any | None = None,
    hard_ceilings: Mapping[str, int | None] | None = None,
    entrypoint_defaults: Mapping[str, int | None] | None = None,
) -> RunBudgetLimits:
    """Normalize hard ceiling / entrypoint / operator / profile into one RunBudgetLimits.

    Resolution per field: min(hard ceiling, entrypoint default, optional operator
    lower-only, optional Profile field). Missing Profile fields use entrypoint defaults.
    Skills never supply Run limits.

    Profile sources:
    - ``profile_output_budget`` (OutputBudgetV1 / mapping): provider/capability/wall-time
      fields listed in ``_PROFILE_OUTPUT_BUDGET_FIELDS``.
    - ``profile_context_budget`` (ContextBudgetV1 / mapping): ``max_active_skills``
      (Plan 05 §6.1 — lives on contextBudget, not outputBudget).
    """
    hard = dict(hard_ceilings or ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS)
    defaults = dict(entrypoint_defaults or ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS)
    operator = dict(operator_limits or {})

    profile_map: dict[str, int | None] = {}
    profile_map.update(
        _extract_profile_budget_fields(
            profile_output_budget, _PROFILE_OUTPUT_BUDGET_FIELDS
        )
    )
    profile_map.update(
        _extract_profile_budget_fields(
            profile_context_budget, _PROFILE_CONTEXT_BUDGET_FIELDS
        )
    )

    def resolve(field: str) -> int | None:
        return _min_positive(
            hard_ceiling=_as_optional_int(hard.get(field)),
            entrypoint_default=_as_optional_int(defaults.get(field)),
            operator=_as_optional_int(operator.get(field)),
            profile=profile_map.get(field),
            field_name=field,
        )

    # max_prompt_tokens may remain unset (None) when no reliable estimator exists.
    max_prompt = resolve("max_prompt_tokens")
    # If only hard ceiling is present and entrypoint default is None, keep None
    # unless operator/profile explicitly set a value.
    if defaults.get("max_prompt_tokens") is None and profile_map.get(
        "max_prompt_tokens"
    ) is None and operator.get("max_prompt_tokens") is None:
        max_prompt = None

    return RunBudgetLimits(
        max_provider_rounds=int(resolve("max_provider_rounds") or 8),
        max_main_agent_cycles=int(resolve("max_main_agent_cycles") or 1),
        max_active_skills=int(resolve("max_active_skills") or 4),
        max_total_capability_calls=int(resolve("max_total_capability_calls") or 16),
        max_parallel_calls=int(resolve("max_parallel_calls") or 4),
        max_capability_depth=int(resolve("max_capability_depth") or 4),
        max_agent_depth=int(resolve("max_agent_depth") or 2),
        max_same_read_signature=int(resolve("max_same_read_signature") or 3),
        max_prompt_tokens=max_prompt,
        max_completion_tokens=resolve("max_completion_tokens"),
        max_wall_time_ms=int(resolve("max_wall_time_ms") or 120_000),
        max_completion_followup_rounds=int(
            resolve("max_completion_followup_rounds") or 2
        ),
    )


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def normalize_owner_budget_limits(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    run_limits: RunBudgetLimits,
    max_skill_calls: int | None = None,
    max_same_read_calls: int | None = None,
    is_instruction_only: bool = False,
) -> OwnerBudgetLimits:
    """Derive OwnerBudgetLimits capped by Run totals (Plan 05 §6.2)."""
    if owner_kind == "main_agent":
        max_calls = min(MAIN_AGENT_OWNER_DEFAULT_MAX_CALLS, run_limits.max_total_capability_calls)
        max_same = run_limits.max_same_read_signature
    else:
        declared_calls = 0 if is_instruction_only and max_skill_calls is None else (
            0 if max_skill_calls is None else max_skill_calls
        )
        declared_same = (
            0
            if is_instruction_only and max_same_read_calls is None
            else (0 if max_same_read_calls is None else max_same_read_calls)
        )
        max_calls = min(declared_calls, run_limits.max_total_capability_calls)
        max_same = min(declared_same, run_limits.max_same_read_signature)
    return build_owner_budget_limits(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        max_calls=max_calls,
        max_same_read_signature=max_same,
    )


# Fixed vectors for tests / Task 0 compatibility.
ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST = compute_assistant_chat_entrypoint_policy_digest()


__all__ = [
    "ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST",
    "ASSISTANT_CHAT_ENTRYPOINT_POLICY_REVISION",
    "ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS",
    "ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS",
    "AUTHORIZATION_REASON_CODES",
    "AuthorizationDecision",
    "CapabilityExposureRef",
    "EMPTY_POLICY_DIGEST",
    "EffectiveCapabilityGrant",
    "EffectiveRunPolicySnapshot",
    "MAIN_AGENT_OWNER_DEFAULT_MAX_CALLS",
    "ManifestExposureIndex",
    "OwnerBudgetLimits",
    "OwnerPolicyRef",
    "PLAN05_RELEASE_GATE_SIDE_EFFECTS",
    "PolicyEntrypoint",
    "PolicyOwnerKind",
    "RunBudgetLimits",
    "build_authorization_decision",
    "build_authorization_decision_digest_payload",
    "build_capability_exposure_ref",
    "build_effective_capability_grant",
    "build_effective_policy_digest_payload",
    "build_effective_run_policy_snapshot",
    "build_exposure_digest_payload",
    "build_exposure_index_digest_payload",
    "build_grant_source_digest_payload",
    "build_manifest_exposure_index",
    "build_owner_budget_limits",
    "build_owner_policy_ref",
    "build_run_budget_limits_payload",
    "compute_assistant_chat_entrypoint_policy_digest",
    "compute_authorization_decision_digest",
    "compute_default_global_policy_digest",
    "compute_effective_policy_digest",
    "compute_exposure_digest",
    "compute_exposure_index_digest",
    "compute_grant_source_digest",
    "compute_grant_source_set_digest",
    "compute_owner_budget_digest",
    "compute_owner_policy_digest",
    "compute_principal_digest",
    "normalize_owner_budget_limits",
    "normalize_run_budget_limits",
]
