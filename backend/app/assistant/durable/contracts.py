"""Plan 06 durable Checkpoint v1 frozen contracts.

Uses exact Plan 03 / Plan 05 types — no reduced durable-only clones.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.policy.contracts import EffectiveCapabilityGrant
from app.assistant.policy.recursion import CapabilityCallFrame
from app.assistant.provider_loop.contracts import ProviderLoopContinuation
from app.assistant.provider_loop.messages import (
    ProviderMessage,
    digest_provider_message,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

DurableExecutionUnitKind = Literal[
    "provider_round",
    "capability_group",
    "completion",
    "memory_commit",
]
DurableExecutionUnitState = Literal["prepared", "started"]
DurableCheckpointPhase = Literal[
    "ready_for_provider",
    "dispatching_calls",
    "waiting",
    "ready_for_completion",
    "ready_for_memory",
    "terminal",
]
DurableNextActionKind = Literal[
    "continue_provider",
    "dispatch_calls",
    "wait",
    "complete",
    "memory",
    "terminal",
    "reconcile",
]
DurableProtectionKind = Literal["public", "protected", "internal"]
ProviderMessageRole = Literal[
    "system",
    "runtime_instruction",
    "runtime_context",
    "runtime_completion",
    "user",
    "assistant",
    "tool",
]

_PROTECTED_ROLES = frozenset(
    {"runtime_instruction", "runtime_context", "runtime_completion"}
)
_DIGEST_LEN = 64


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_digest(value: Any, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_LEN
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return value


# ---------------------------------------------------------------------------
# Execution unit / next action
# ---------------------------------------------------------------------------


class DurableExecutionUnitV1(FrozenContract):
    """Prepared/started logical unit committed before external I/O."""

    logical_unit_id: str
    kind: DurableExecutionUnitKind
    state: DurableExecutionUnitState
    provider_round: int | None
    call_ids: tuple[str, ...]
    attempt: int
    reserved_budget_revision: int
    started_budget_revision: int | None

    @field_validator("logical_unit_id")
    @classmethod
    def _unit_id(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="logical_unit_id")

    @field_validator("attempt")
    @classmethod
    def _attempt(cls, value: int) -> int:
        return _require_positive_int(value, field_name="attempt")

    @field_validator("reserved_budget_revision")
    @classmethod
    def _reserved(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="reserved_budget_revision")

    @field_validator("provider_round")
    @classmethod
    def _round(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _require_non_negative_int(value, field_name="provider_round")

    @field_validator("started_budget_revision")
    @classmethod
    def _started_rev(cls, value: int | None) -> int | None:
        if value is None:
            return None
        return _require_non_negative_int(value, field_name="started_budget_revision")

    @field_validator("call_ids", mode="before")
    @classmethod
    def _call_ids(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("call_ids must be a sequence")
        out: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("call_ids items must be non-empty strings")
            out.append(item)
        if len(set(out)) != len(out):
            raise ValueError("call_ids must be unique")
        return tuple(out)

    @model_validator(mode="after")
    def _state_rules(self) -> DurableExecutionUnitV1:
        if self.state == "prepared" and self.started_budget_revision is not None:
            raise ValueError("prepared unit must have started_budget_revision=None")
        if self.state == "started" and self.started_budget_revision is None:
            raise ValueError("started unit requires started_budget_revision")
        if self.kind == "capability_group" and self.state == "started" and not self.call_ids:
            raise ValueError("started capability_group requires call_ids")
        return self


class DurableNextActionV1(FrozenContract):
    """Worker next-action after reconstructing a Checkpoint.

    Minimal locked set for Plan 06:
    continue_provider | dispatch_calls | wait | complete | memory | terminal | reconcile.
    """

    kind: DurableNextActionKind
    reason_code: str | None = None
    detail: str | None = None

    @field_validator("reason_code", "detail")
    @classmethod
    def _optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("reason_code/detail must be strings when present")
        return value


# ---------------------------------------------------------------------------
# Provider message row envelope (revision linkage)
# ---------------------------------------------------------------------------


class DurableProviderMessageRecordV1(FrozenContract):
    """Portable Provider message plus durable row linkage/protection metadata.

    Role-specific revision linkage mirrors the ORM CHECK contract:
    - public system|user|assistant|tool: policy/obligation optional (typically null)
    - runtime_instruction|runtime_context: protected + policy required, no obligation
    - runtime_completion: protected + policy + obligation required
    - bare system may not be protected
    """

    role: ProviderMessageRole
    protection_kind: DurableProtectionKind
    message: ProviderMessage
    manifest_revision_id: UUID
    policy_revision_id: UUID | None
    obligation_revision_id: UUID | None
    content_digest: str

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="content_digest")

    @model_validator(mode="after")
    def _linkage(self) -> DurableProviderMessageRecordV1:
        msg_role = getattr(self.message, "role", None)
        if msg_role != self.role:
            raise ValueError(
                f"envelope role {self.role!r} does not match message role {msg_role!r}"
            )
        expected = digest_provider_message(self.message)
        if self.content_digest != expected:
            raise ValueError("content_digest does not match message payload")

        if self.role in _PROTECTED_ROLES:
            if self.protection_kind != "protected":
                raise ValueError(f"{self.role} requires protection_kind=protected")
            if self.policy_revision_id is None:
                raise ValueError(f"{self.role} requires policy_revision_id")
            if self.role == "runtime_completion":
                if self.obligation_revision_id is None:
                    raise ValueError("runtime_completion requires obligation_revision_id")
            elif self.obligation_revision_id is not None:
                raise ValueError(
                    f"{self.role} must not carry obligation_revision_id"
                )
        else:
            if self.role == "system" and self.protection_kind == "protected":
                raise ValueError("bare system may not be protected")
            if self.role == "system" and self.protection_kind not in {"public", "internal"}:
                raise ValueError("system protection_kind must be public|internal")
        return self


# ---------------------------------------------------------------------------
# Grant set (complete independent grants)
# ---------------------------------------------------------------------------


class DurableGrantSetV1(FrozenContract):
    """Complete independent grant bodies for a policy revision."""

    grants: tuple[EffectiveCapabilityGrant, ...]

    @field_validator("grants", mode="before")
    @classmethod
    def _grants(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("grants must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _unique_keys(self) -> DurableGrantSetV1:
        keys = [g.capability_key for g in self.grants]
        if len(keys) != len(set(keys)):
            raise ValueError("grant set capability_key values must be unique")
        return self


# ---------------------------------------------------------------------------
# Checkpoint v1
# ---------------------------------------------------------------------------


class DurableAgentCheckpointV1(FrozenContract):
    """Schema-versioned durable Main Agent Checkpoint (Plan 06 §6)."""

    schema_version: Literal[1] = 1
    run_id: UUID
    phase: DurableCheckpointPhase
    manifest_revision_id: UUID
    policy_revision_id: UUID
    budget_revision_id: UUID
    obligation_revision_id: UUID
    provider_message_ordinal: int
    provider_transcript_digest: str
    provider_loop_continuation: ProviderLoopContinuation | None
    inflight_unit: DurableExecutionUnitV1 | None
    capability_frames: tuple[CapabilityCallFrame, ...] = ()
    artifact_ids: tuple[UUID, ...] = ()
    visible_text_artifact_id: UUID | None = None
    next_action: DurableNextActionV1

    @field_validator("provider_message_ordinal")
    @classmethod
    def _ordinal(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="provider_message_ordinal")

    @field_validator("provider_transcript_digest")
    @classmethod
    def _transcript(cls, value: str) -> str:
        return _require_digest(value, field_name="provider_transcript_digest")

    @field_validator("capability_frames", mode="before")
    @classmethod
    def _frames(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("capability_frames must be a sequence")
        return tuple(value)

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def _artifacts(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("artifact_ids must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _phase_invariants(self) -> DurableAgentCheckpointV1:
        if self.phase == "waiting":
            if self.provider_loop_continuation is None:
                raise ValueError("waiting phase requires provider_loop_continuation")
            if self.inflight_unit is not None:
                raise ValueError("waiting phase must not carry inflight_unit")
        if self.phase == "terminal":
            if self.inflight_unit is not None:
                raise ValueError("terminal phase must not carry inflight_unit")
            if self.next_action.kind not in {"terminal", "reconcile"}:
                raise ValueError("terminal phase next_action must be terminal|reconcile")
        if self.provider_loop_continuation is not None and self.phase not in {
            "waiting",
            "dispatching_calls",
            "ready_for_provider",
        }:
            # Continuation is primarily for waiting; allow limited non-waiting presence
            # only when phase is waiting (strict). Non-waiting must be None.
            if self.phase != "waiting":
                # Plan: transcript may be open only when phase=waiting and continuation
                # validates it. Disallow continuation outside waiting.
                raise ValueError(
                    "provider_loop_continuation is only valid when phase=waiting"
                )

        # Frame stack must be consistent with inflight capability unit call_ids.
        unit = self.inflight_unit
        if unit is not None and unit.kind == "capability_group" and unit.call_ids:
            frame_ids = {f.call_id for f in self.capability_frames}
            missing = [cid for cid in unit.call_ids if cid not in frame_ids]
            if missing:
                raise ValueError(
                    "capability_frames inconsistent with inflight unit call_ids: "
                    f"missing {missing!r}"
                )
        return self


__all__ = [
    "DurableAgentCheckpointV1",
    "DurableExecutionUnitKind",
    "DurableExecutionUnitState",
    "DurableExecutionUnitV1",
    "DurableGrantSetV1",
    "DurableNextActionKind",
    "DurableNextActionV1",
    "DurableProtectionKind",
    "DurableProviderMessageRecordV1",
    "ProviderMessageRole",
]
