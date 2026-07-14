"""Plan 05 revisioned budget ledger and reservation protocol.

Process-local immutable-by-revision state under a lock/CAS facade. Plan 05 keeps
state in memory; Plan 06 maps the same transition contract to database CAS.

Hard rules (Plan 05 §6–7):
- Counts consume at ``started``, not at reservation.
- ``read_signature`` only for side_effect ``read``.
- Compatible consumers do not receive allowance; frozen first owner is charged.
- Adding Skills never mutates Run hard limits/usage/deadline.
- Monotonic deadline is fixed at ledger start; wall-clock cannot extend it.
- Serializable state holds digests/counts only — never secrets, args, or results.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import field_validator, model_validator

from app.assistant.capabilities.contracts import SideEffectClass
from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import JsonValue, sha256_bytes, sha256_canonical_json
from app.assistant.policy.contracts import (
    OwnerBudgetLimits,
    PolicyOwnerKind,
    RunBudgetLimits,
    build_run_budget_limits_payload,
)

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ReservationState = Literal["reserved", "started", "finished", "released"]

BUDGET_EVENT_RESERVED = "budget_reserved"
BUDGET_EVENT_STARTED = "budget_started"
BUDGET_EVENT_FINISHED = "budget_finished"
BUDGET_EVENT_RELEASED = "budget_released"
BUDGET_EVENT_DENIED = "budget_denied"
BUDGET_EVENT_PROVIDER_ROUND = "budget_provider_round"
BUDGET_EVENT_TOKEN_USAGE = "budget_token_usage"
BUDGET_EVENT_OWNER_LIMITS_ADDED = "budget_owner_limits_added"
BUDGET_EVENT_MAIN_AGENT_CYCLE = "budget_main_agent_cycle"
BUDGET_EVENT_COMPLETION_FOLLOWUP = "budget_completion_followup"
BUDGET_EVENT_CANCELLED = "budget_cancelled"

# Stable budget denial / protocol reason codes (safe for events/metrics).
REASON_ALLOWED = "allowed"
REASON_DUPLICATE_CALL_ID = "duplicate_call_id"
REASON_RESERVATION_NOT_FOUND = "reservation_not_found"
REASON_RESERVATION_STATE_INVALID = "reservation_state_invalid"
REASON_ARGUMENTS_DIGEST_MISMATCH = "arguments_digest_mismatch"
REASON_OWNER_LIMITS_MISSING = "owner_limits_missing"
REASON_OWNER_LIMITS_DUPLICATE = "owner_limits_duplicate"
REASON_CANCELLED = "cancelled"
REASON_PROTOCOL_ERROR = "policy_state_protocol_error"
REASON_TOTAL_CALLS = "budget_exhausted_total_calls"
REASON_OWNER_CALLS = "budget_exhausted_owner_calls"
REASON_PARALLEL = "budget_exhausted_parallel"
REASON_READ_SIGNATURE = "budget_exhausted_read_signature"
REASON_OWNER_READ_SIGNATURE = "budget_exhausted_owner_read_signature"
REASON_DEADLINE = "budget_exhausted_deadline"
REASON_PROVIDER_ROUNDS = "budget_exhausted_provider_rounds"
REASON_COMPLETION_TOKENS = "budget_exhausted_completion_tokens"
REASON_PROMPT_TOKENS = "budget_exhausted_prompt_tokens"
REASON_CAPABILITY_DEPTH = "budget_exhausted_capability_depth"
REASON_AGENT_DEPTH = "budget_exhausted_agent_depth"
REASON_MAIN_AGENT_CYCLES = "budget_exhausted_main_agent_cycles"
REASON_COMPLETION_FOLLOWUPS = "budget_exhausted_completion_followups"
REASON_ACTIVE_SKILLS = "budget_exhausted_active_skills"
REASON_ACTIVE_SKILLS_AT_LIMIT = "budget_exhausted_active_skills"

_DIGEST_RE_LEN = 64

EventSink = Callable[[Mapping[str, JsonValue]], None]


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class BudgetClock(Protocol):
    """Injectable clock for UTC audit fields and monotonic live deadline."""

    def utc_now(self) -> datetime:
        """Timezone-aware UTC wall clock (portable audit only)."""

    def monotonic_ms(self) -> int:
        """Monotonic milliseconds; live deadline source of truth."""


class SystemBudgetClock:
    """Production clock: real UTC + ``time.monotonic``."""

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic_ms(self) -> int:
        return int(time.monotonic() * 1000)


class DeterministicBudgetClock:
    """Test clock with independent UTC and monotonic controls."""

    def __init__(
        self,
        *,
        utc_start: datetime | None = None,
        monotonic_start_ms: int = 1_000_000,
    ) -> None:
        if utc_start is None:
            utc_start = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
        if utc_start.tzinfo is None:
            raise ValueError("utc_start must be timezone-aware")
        self._utc = utc_start.astimezone(timezone.utc)
        self._mono_ms = int(monotonic_start_ms)

    def utc_now(self) -> datetime:
        return self._utc

    def monotonic_ms(self) -> int:
        return self._mono_ms

    def advance_utc(self, *, seconds: float = 0, milliseconds: int = 0) -> None:
        from datetime import timedelta

        delta = timedelta(seconds=seconds, milliseconds=milliseconds)
        self._utc = self._utc + delta

    def rollback_utc(self, *, seconds: float = 0, milliseconds: int = 0) -> None:
        from datetime import timedelta

        delta = timedelta(seconds=seconds, milliseconds=milliseconds)
        self._utc = self._utc - delta

    def advance_monotonic(self, *, milliseconds: int) -> None:
        self._mono_ms += int(milliseconds)

    def set_utc(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("utc value must be timezone-aware")
        self._utc = value.astimezone(timezone.utc)

    def set_monotonic_ms(self, value: int) -> None:
        self._mono_ms = int(value)


# ---------------------------------------------------------------------------
# Supporting frozen contracts
# ---------------------------------------------------------------------------


def _require_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != _DIGEST_RE_LEN:
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest"
        )
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest"
        )
    return value


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def compute_read_signature(
    *,
    binding_contract_digest: str,
    arguments_digest: str,
) -> str:
    """``sha256(binding_contract_digest + arguments_digest)`` for side_effect ``read``."""
    binding = _require_digest(binding_contract_digest, field_name="binding_contract_digest")
    args = _require_digest(arguments_digest, field_name="arguments_digest")
    return sha256_bytes((binding + args).encode("utf-8"))


def compute_owner_usage_digest(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    calls_started: int,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "owner_usage",
            "ownerKind": owner_kind,
            "ownerVersionId": str(owner_version_id),
            "callsStarted": calls_started,
        }
    )


def compute_signature_usage_digest(*, read_signature: str, count: int) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "signature_usage",
            "readSignature": read_signature,
            "count": count,
        }
    )


def compute_owner_signature_usage_digest(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    read_signature: str,
    count: int,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "owner_signature_usage",
            "ownerKind": owner_kind,
            "ownerVersionId": str(owner_version_id),
            "readSignature": read_signature,
            "count": count,
        }
    )


def compute_reservation_digest(
    *,
    call_id: str,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    domain_key: str,
    side_effect: SideEffectClass,
    arguments_digest: str,
    read_signature: str | None,
    state: ReservationState,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "budget_reservation",
            "callId": call_id,
            "ownerKind": owner_kind,
            "ownerVersionId": str(owner_version_id),
            "domainKey": domain_key,
            "sideEffect": side_effect,
            "argumentsDigest": arguments_digest,
            "readSignature": read_signature,
            "state": state,
        }
    )


class OwnerUsage(FrozenContract):
    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    calls_started: int
    owner_usage_digest: str

    @field_validator("calls_started")
    @classmethod
    def _calls(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="calls_started")

    @field_validator("owner_usage_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="owner_usage_digest")


class SignatureUsage(FrozenContract):
    read_signature: str
    count: int
    signature_usage_digest: str

    @field_validator("read_signature")
    @classmethod
    def _sig(cls, value: str) -> str:
        return _require_digest(value, field_name="read_signature")

    @field_validator("count")
    @classmethod
    def _count(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="count")

    @field_validator("signature_usage_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="signature_usage_digest")


class OwnerSignatureUsage(FrozenContract):
    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    read_signature: str
    count: int
    owner_signature_usage_digest: str

    @field_validator("read_signature")
    @classmethod
    def _sig(cls, value: str) -> str:
        return _require_digest(value, field_name="read_signature")

    @field_validator("count")
    @classmethod
    def _count(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="count")

    @field_validator("owner_signature_usage_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="owner_signature_usage_digest")


class BudgetReservation(FrozenContract):
    call_id: str
    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    domain_key: str
    side_effect: SideEffectClass
    arguments_digest: str
    read_signature: str | None
    state: ReservationState
    reservation_digest: str

    @field_validator("call_id", "domain_key")
    @classmethod
    def _non_empty(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("arguments_digest")
    @classmethod
    def _args_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="arguments_digest")

    @field_validator("read_signature")
    @classmethod
    def _read_sig(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, field_name="read_signature")

    @field_validator("reservation_digest")
    @classmethod
    def _res_digest(cls, value: str) -> str:
        return _require_digest(value, field_name="reservation_digest")

    @model_validator(mode="after")
    def _side_effect_signature(self) -> BudgetReservation:
        if self.side_effect == "read" and self.read_signature is None:
            raise ValueError("read side_effect requires read_signature")
        if self.side_effect != "read" and self.read_signature is not None:
            raise ValueError("read_signature only allowed for side_effect read")
        return self


class BudgetLedgerState(FrozenContract):
    revision: int
    limits: RunBudgetLimits
    owner_limits: tuple[OwnerBudgetLimits, ...]
    provider_rounds_started: int
    main_agent_cycles_started: int
    capability_calls_started: int
    completion_followups_started: int
    prompt_tokens_used: int
    completion_tokens_used: int
    owner_calls_started: tuple[OwnerUsage, ...]
    global_read_signatures: tuple[SignatureUsage, ...]
    owner_read_signatures: tuple[OwnerSignatureUsage, ...]
    reservations: tuple[BudgetReservation, ...]
    denial_count: int
    started_at_utc: datetime
    deadline_at_utc: datetime
    ledger_digest: str

    @field_validator(
        "revision",
        "provider_rounds_started",
        "main_agent_cycles_started",
        "capability_calls_started",
        "completion_followups_started",
        "prompt_tokens_used",
        "completion_tokens_used",
        "denial_count",
    )
    @classmethod
    def _non_neg(cls, value: int, info: Any) -> int:
        return _require_non_negative_int(value, field_name=info.field_name)

    @field_validator("ledger_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="ledger_digest")

    @field_validator("started_at_utc", "deadline_at_utc")
    @classmethod
    def _aware(cls, value: datetime, info: Any) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} must be timezone-aware UTC")
        return value.astimezone(timezone.utc)


class BudgetReserveRequest(FrozenContract):
    """Identity of one call to reserve (no raw arguments)."""

    call_id: str
    owner_kind: PolicyOwnerKind
    owner_version_id: UUID
    domain_key: str
    side_effect: SideEffectClass
    arguments_digest: str
    binding_contract_digest: str
    capability_depth: int = 1
    agent_depth: int = 1

    @field_validator("call_id", "domain_key")
    @classmethod
    def _non_empty(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("arguments_digest", "binding_contract_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("capability_depth", "agent_depth")
    @classmethod
    def _depth(cls, value: int, info: Any) -> int:
        return _require_positive_int(value, field_name=info.field_name)


class BudgetDecision(FrozenContract):
    """Result of a ledger transition attempt."""

    allowed: bool
    reason_code: str
    dimension: str | None = None
    reservation: BudgetReservation | None = None
    reservations: tuple[BudgetReservation, ...] = ()
    ledger_revision: int
    ledger_digest: str
    # Allowlisted internal event payload; not part of serializable ledger state.
    # Typed loosely so recursive JsonValue does not explode Pydantic schema gen.
    event: dict[str, Any] | None = None

    @field_validator("reason_code")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="reason_code")

    @field_validator("ledger_revision")
    @classmethod
    def _rev(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="ledger_revision")

    @field_validator("ledger_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="ledger_digest")


# ---------------------------------------------------------------------------
# Digest / payload builders
# ---------------------------------------------------------------------------


def _owner_limits_payload(item: OwnerBudgetLimits) -> dict[str, JsonValue]:
    return {
        "ownerKind": item.owner_kind,
        "ownerVersionId": str(item.owner_version_id),
        "maxCalls": item.max_calls,
        "maxSameReadSignature": item.max_same_read_signature,
        "ownerBudgetDigest": item.owner_budget_digest,
    }


def _owner_usage_payload(item: OwnerUsage) -> dict[str, JsonValue]:
    return {
        "ownerKind": item.owner_kind,
        "ownerVersionId": str(item.owner_version_id),
        "callsStarted": item.calls_started,
        "ownerUsageDigest": item.owner_usage_digest,
    }


def _signature_usage_payload(item: SignatureUsage) -> dict[str, JsonValue]:
    return {
        "readSignature": item.read_signature,
        "count": item.count,
        "signatureUsageDigest": item.signature_usage_digest,
    }


def _owner_signature_usage_payload(item: OwnerSignatureUsage) -> dict[str, JsonValue]:
    return {
        "ownerKind": item.owner_kind,
        "ownerVersionId": str(item.owner_version_id),
        "readSignature": item.read_signature,
        "count": item.count,
        "ownerSignatureUsageDigest": item.owner_signature_usage_digest,
    }


def _reservation_payload(item: BudgetReservation) -> dict[str, JsonValue]:
    return {
        "callId": item.call_id,
        "ownerKind": item.owner_kind,
        "ownerVersionId": str(item.owner_version_id),
        "domainKey": item.domain_key,
        "sideEffect": item.side_effect,
        "argumentsDigest": item.arguments_digest,
        "readSignature": item.read_signature,
        "state": item.state,
        "reservationDigest": item.reservation_digest,
    }


def build_ledger_digest_payload(
    *,
    revision: int,
    limits: RunBudgetLimits,
    owner_limits: Sequence[OwnerBudgetLimits],
    provider_rounds_started: int,
    main_agent_cycles_started: int,
    capability_calls_started: int,
    completion_followups_started: int,
    prompt_tokens_used: int,
    completion_tokens_used: int,
    owner_calls_started: Sequence[OwnerUsage],
    global_read_signatures: Sequence[SignatureUsage],
    owner_read_signatures: Sequence[OwnerSignatureUsage],
    reservations: Sequence[BudgetReservation],
    denial_count: int,
    started_at_utc: datetime,
    deadline_at_utc: datetime,
) -> dict[str, JsonValue]:
    # Deterministic order: owner limits by kind + version bytes; signatures by digests.
    ordered_owner_limits = tuple(
        sorted(
            owner_limits,
            key=lambda o: (o.owner_kind, o.owner_version_id.bytes),
        )
    )
    ordered_owner_usage = tuple(
        sorted(
            owner_calls_started,
            key=lambda o: (o.owner_kind, o.owner_version_id.bytes),
        )
    )
    ordered_global_sigs = tuple(
        sorted(global_read_signatures, key=lambda s: s.read_signature)
    )
    ordered_owner_sigs = tuple(
        sorted(
            owner_read_signatures,
            key=lambda s: (s.owner_kind, s.owner_version_id.bytes, s.read_signature),
        )
    )
    # Reservations keep insertion order (call lifecycle order is meaningful).
    return {
        "schemaVersion": 1,
        "kind": "budget_ledger_state",
        "revision": revision,
        "limits": build_run_budget_limits_payload(limits),
        "ownerLimits": [_owner_limits_payload(o) for o in ordered_owner_limits],
        "providerRoundsStarted": provider_rounds_started,
        "mainAgentCyclesStarted": main_agent_cycles_started,
        "capabilityCallsStarted": capability_calls_started,
        "completionFollowupsStarted": completion_followups_started,
        "promptTokensUsed": prompt_tokens_used,
        "completionTokensUsed": completion_tokens_used,
        "ownerCallsStarted": [_owner_usage_payload(o) for o in ordered_owner_usage],
        "globalReadSignatures": [
            _signature_usage_payload(s) for s in ordered_global_sigs
        ],
        "ownerReadSignatures": [
            _owner_signature_usage_payload(s) for s in ordered_owner_sigs
        ],
        "reservations": [_reservation_payload(r) for r in reservations],
        "denialCount": denial_count,
        "startedAtUtc": started_at_utc.astimezone(timezone.utc).isoformat(),
        "deadlineAtUtc": deadline_at_utc.astimezone(timezone.utc).isoformat(),
    }


def compute_ledger_digest(
    *,
    revision: int,
    limits: RunBudgetLimits,
    owner_limits: Sequence[OwnerBudgetLimits],
    provider_rounds_started: int,
    main_agent_cycles_started: int,
    capability_calls_started: int,
    completion_followups_started: int,
    prompt_tokens_used: int,
    completion_tokens_used: int,
    owner_calls_started: Sequence[OwnerUsage],
    global_read_signatures: Sequence[SignatureUsage],
    owner_read_signatures: Sequence[OwnerSignatureUsage],
    reservations: Sequence[BudgetReservation],
    denial_count: int,
    started_at_utc: datetime,
    deadline_at_utc: datetime,
) -> str:
    return sha256_canonical_json(
        build_ledger_digest_payload(
            revision=revision,
            limits=limits,
            owner_limits=owner_limits,
            provider_rounds_started=provider_rounds_started,
            main_agent_cycles_started=main_agent_cycles_started,
            capability_calls_started=capability_calls_started,
            completion_followups_started=completion_followups_started,
            prompt_tokens_used=prompt_tokens_used,
            completion_tokens_used=completion_tokens_used,
            owner_calls_started=owner_calls_started,
            global_read_signatures=global_read_signatures,
            owner_read_signatures=owner_read_signatures,
            reservations=reservations,
            denial_count=denial_count,
            started_at_utc=started_at_utc,
            deadline_at_utc=deadline_at_utc,
        )
    )


def build_owner_usage(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    calls_started: int,
) -> OwnerUsage:
    return OwnerUsage(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        calls_started=calls_started,
        owner_usage_digest=compute_owner_usage_digest(
            owner_kind=owner_kind,
            owner_version_id=owner_version_id,
            calls_started=calls_started,
        ),
    )


def build_signature_usage(*, read_signature: str, count: int) -> SignatureUsage:
    return SignatureUsage(
        read_signature=read_signature,
        count=count,
        signature_usage_digest=compute_signature_usage_digest(
            read_signature=read_signature,
            count=count,
        ),
    )


def build_owner_signature_usage(
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    read_signature: str,
    count: int,
) -> OwnerSignatureUsage:
    return OwnerSignatureUsage(
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        read_signature=read_signature,
        count=count,
        owner_signature_usage_digest=compute_owner_signature_usage_digest(
            owner_kind=owner_kind,
            owner_version_id=owner_version_id,
            read_signature=read_signature,
            count=count,
        ),
    )


def build_reservation(
    *,
    call_id: str,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    domain_key: str,
    side_effect: SideEffectClass,
    arguments_digest: str,
    read_signature: str | None,
    state: ReservationState,
) -> BudgetReservation:
    digest = compute_reservation_digest(
        call_id=call_id,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        domain_key=domain_key,
        side_effect=side_effect,
        arguments_digest=arguments_digest,
        read_signature=read_signature,
        state=state,
    )
    return BudgetReservation(
        call_id=call_id,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        domain_key=domain_key,
        side_effect=side_effect,
        arguments_digest=arguments_digest,
        read_signature=read_signature,
        state=state,
        reservation_digest=digest,
    )


def _rebuild_state(
    *,
    revision: int,
    limits: RunBudgetLimits,
    owner_limits: Sequence[OwnerBudgetLimits],
    provider_rounds_started: int,
    main_agent_cycles_started: int,
    capability_calls_started: int,
    completion_followups_started: int,
    prompt_tokens_used: int,
    completion_tokens_used: int,
    owner_calls_started: Sequence[OwnerUsage],
    global_read_signatures: Sequence[SignatureUsage],
    owner_read_signatures: Sequence[OwnerSignatureUsage],
    reservations: Sequence[BudgetReservation],
    denial_count: int,
    started_at_utc: datetime,
    deadline_at_utc: datetime,
) -> BudgetLedgerState:
    ordered_owner_limits = tuple(
        sorted(
            owner_limits,
            key=lambda o: (o.owner_kind, o.owner_version_id.bytes),
        )
    )
    ordered_owner_usage = tuple(
        sorted(
            owner_calls_started,
            key=lambda o: (o.owner_kind, o.owner_version_id.bytes),
        )
    )
    ordered_global_sigs = tuple(
        sorted(global_read_signatures, key=lambda s: s.read_signature)
    )
    ordered_owner_sigs = tuple(
        sorted(
            owner_read_signatures,
            key=lambda s: (s.owner_kind, s.owner_version_id.bytes, s.read_signature),
        )
    )
    reservations_t = tuple(reservations)
    digest = compute_ledger_digest(
        revision=revision,
        limits=limits,
        owner_limits=ordered_owner_limits,
        provider_rounds_started=provider_rounds_started,
        main_agent_cycles_started=main_agent_cycles_started,
        capability_calls_started=capability_calls_started,
        completion_followups_started=completion_followups_started,
        prompt_tokens_used=prompt_tokens_used,
        completion_tokens_used=completion_tokens_used,
        owner_calls_started=ordered_owner_usage,
        global_read_signatures=ordered_global_sigs,
        owner_read_signatures=ordered_owner_sigs,
        reservations=reservations_t,
        denial_count=denial_count,
        started_at_utc=started_at_utc,
        deadline_at_utc=deadline_at_utc,
    )
    return BudgetLedgerState(
        revision=revision,
        limits=limits,
        owner_limits=ordered_owner_limits,
        provider_rounds_started=provider_rounds_started,
        main_agent_cycles_started=main_agent_cycles_started,
        capability_calls_started=capability_calls_started,
        completion_followups_started=completion_followups_started,
        prompt_tokens_used=prompt_tokens_used,
        completion_tokens_used=completion_tokens_used,
        owner_calls_started=ordered_owner_usage,
        global_read_signatures=ordered_global_sigs,
        owner_read_signatures=ordered_owner_sigs,
        reservations=reservations_t,
        denial_count=denial_count,
        started_at_utc=started_at_utc,
        deadline_at_utc=deadline_at_utc,
        ledger_digest=digest,
    )


def create_initial_ledger_state(
    *,
    limits: RunBudgetLimits,
    owner_limits: Sequence[OwnerBudgetLimits] = (),
    started_at_utc: datetime,
    deadline_at_utc: datetime | None = None,
) -> BudgetLedgerState:
    """Create revision-0 ledger state. Deadline defaults to start + wall time."""
    if started_at_utc.tzinfo is None:
        raise ValueError("started_at_utc must be timezone-aware")
    start = started_at_utc.astimezone(timezone.utc)
    if deadline_at_utc is None:
        from datetime import timedelta

        deadline = start + timedelta(milliseconds=limits.max_wall_time_ms)
    else:
        if deadline_at_utc.tzinfo is None:
            raise ValueError("deadline_at_utc must be timezone-aware")
        deadline = deadline_at_utc.astimezone(timezone.utc)
    if deadline < start:
        raise ValueError("deadline_at_utc must be >= started_at_utc")
    return _rebuild_state(
        revision=0,
        limits=limits,
        owner_limits=owner_limits,
        provider_rounds_started=0,
        main_agent_cycles_started=0,
        capability_calls_started=0,
        completion_followups_started=0,
        prompt_tokens_used=0,
        completion_tokens_used=0,
        owner_calls_started=(),
        global_read_signatures=(),
        owner_read_signatures=(),
        reservations=(),
        denial_count=0,
        started_at_utc=start,
        deadline_at_utc=deadline,
    )


# ---------------------------------------------------------------------------
# Pure helpers over state
# ---------------------------------------------------------------------------


def _active_reservations(
    state: BudgetLedgerState,
) -> tuple[BudgetReservation, ...]:
    return tuple(r for r in state.reservations if r.state in ("reserved", "started"))


def _find_reservation(
    state: BudgetLedgerState, call_id: str
) -> BudgetReservation | None:
    for item in state.reservations:
        if item.call_id == call_id:
            return item
    return None


def _owner_limit_for(
    state: BudgetLedgerState,
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
) -> OwnerBudgetLimits | None:
    for item in state.owner_limits:
        if item.owner_kind == owner_kind and item.owner_version_id == owner_version_id:
            return item
    return None


def _owner_calls(
    state: BudgetLedgerState,
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
) -> int:
    for item in state.owner_calls_started:
        if item.owner_kind == owner_kind and item.owner_version_id == owner_version_id:
            return item.calls_started
    return 0


def _global_sig_count(state: BudgetLedgerState, read_signature: str) -> int:
    for item in state.global_read_signatures:
        if item.read_signature == read_signature:
            return item.count
    return 0


def _owner_sig_count(
    state: BudgetLedgerState,
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    read_signature: str,
) -> int:
    for item in state.owner_read_signatures:
        if (
            item.owner_kind == owner_kind
            and item.owner_version_id == owner_version_id
            and item.read_signature == read_signature
        ):
            return item.count
    return 0


def _active_reserved_count(
    state: BudgetLedgerState,
    *,
    owner_kind: PolicyOwnerKind | None = None,
    owner_version_id: UUID | None = None,
    read_signature: str | None = None,
    only_reserved: bool = False,
) -> int:
    """Count active reservations optionally filtered (for projected capacity)."""
    total = 0
    for item in state.reservations:
        if only_reserved:
            if item.state != "reserved":
                continue
        elif item.state not in ("reserved", "started"):
            continue
        if owner_kind is not None and item.owner_kind != owner_kind:
            continue
        if owner_version_id is not None and item.owner_version_id != owner_version_id:
            continue
        if read_signature is not None and item.read_signature != read_signature:
            continue
        total += 1
    return total


def _replace_reservation(
    reservations: Sequence[BudgetReservation],
    updated: BudgetReservation,
) -> tuple[BudgetReservation, ...]:
    out: list[BudgetReservation] = []
    found = False
    for item in reservations:
        if item.call_id == updated.call_id:
            out.append(updated)
            found = True
        else:
            out.append(item)
    if not found:
        raise ValueError(f"reservation {updated.call_id!r} not found")
    return tuple(out)


def _bump_owner_usage(
    usages: Sequence[OwnerUsage],
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    delta: int = 1,
) -> tuple[OwnerUsage, ...]:
    found = False
    out: list[OwnerUsage] = []
    for item in usages:
        if item.owner_kind == owner_kind and item.owner_version_id == owner_version_id:
            out.append(
                build_owner_usage(
                    owner_kind=owner_kind,
                    owner_version_id=owner_version_id,
                    calls_started=item.calls_started + delta,
                )
            )
            found = True
        else:
            out.append(item)
    if not found:
        out.append(
            build_owner_usage(
                owner_kind=owner_kind,
                owner_version_id=owner_version_id,
                calls_started=delta,
            )
        )
    return tuple(out)


def _bump_global_sig(
    usages: Sequence[SignatureUsage],
    *,
    read_signature: str,
    delta: int = 1,
) -> tuple[SignatureUsage, ...]:
    found = False
    out: list[SignatureUsage] = []
    for item in usages:
        if item.read_signature == read_signature:
            out.append(
                build_signature_usage(
                    read_signature=read_signature,
                    count=item.count + delta,
                )
            )
            found = True
        else:
            out.append(item)
    if not found:
        out.append(build_signature_usage(read_signature=read_signature, count=delta))
    return tuple(out)


def _bump_owner_sig(
    usages: Sequence[OwnerSignatureUsage],
    *,
    owner_kind: PolicyOwnerKind,
    owner_version_id: UUID,
    read_signature: str,
    delta: int = 1,
) -> tuple[OwnerSignatureUsage, ...]:
    found = False
    out: list[OwnerSignatureUsage] = []
    for item in usages:
        if (
            item.owner_kind == owner_kind
            and item.owner_version_id == owner_version_id
            and item.read_signature == read_signature
        ):
            out.append(
                build_owner_signature_usage(
                    owner_kind=owner_kind,
                    owner_version_id=owner_version_id,
                    read_signature=read_signature,
                    count=item.count + delta,
                )
            )
            found = True
        else:
            out.append(item)
    if not found:
        out.append(
            build_owner_signature_usage(
                owner_kind=owner_kind,
                owner_version_id=owner_version_id,
                read_signature=read_signature,
                count=delta,
            )
        )
    return tuple(out)


def _safe_event(
    event_name: str,
    *,
    reason_code: str,
    state: BudgetLedgerState,
    call_id: str | None = None,
    owner_kind: str | None = None,
    owner_version_id: UUID | None = None,
    reservation_digest: str | None = None,
    dimension: str | None = None,
    extra: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Allowlisted internal event payload (Plan 05 §12)."""
    payload: dict[str, JsonValue] = {
        "_visibility": "internal",
        "event": event_name,
        "reasonCode": reason_code,
        "ledgerRevision": state.revision,
        "ledgerDigest": state.ledger_digest,
        "capabilityCallsStarted": state.capability_calls_started,
        "providerRoundsStarted": state.provider_rounds_started,
        "denialCount": state.denial_count,
        "activeReservations": len(_active_reservations(state)),
        "promptTokensUsed": state.prompt_tokens_used,
        "completionTokensUsed": state.completion_tokens_used,
    }
    if call_id is not None:
        payload["callId"] = call_id
    if owner_kind is not None:
        payload["ownerKind"] = owner_kind
    if owner_version_id is not None:
        payload["ownerVersionId"] = str(owner_version_id)
    if reservation_digest is not None:
        payload["reservationDigest"] = reservation_digest
    if dimension is not None:
        payload["dimension"] = dimension
    if extra:
        for key, value in extra.items():
            # Never allow raw content keys.
            if key in {
                "arguments",
                "result",
                "prompt",
                "content",
                "exception",
                "traceback",
                "secret",
                "headers",
                "body",
            }:
                continue
            payload[key] = value
    return payload


def _deny(
    state: BudgetLedgerState,
    *,
    reason_code: str,
    dimension: str | None,
    call_id: str | None = None,
    owner_kind: str | None = None,
    owner_version_id: UUID | None = None,
    bump_denial: bool = True,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    if bump_denial:
        new_state = _rebuild_state(
            revision=state.revision + 1,
            limits=state.limits,
            owner_limits=state.owner_limits,
            provider_rounds_started=state.provider_rounds_started,
            main_agent_cycles_started=state.main_agent_cycles_started,
            capability_calls_started=state.capability_calls_started,
            completion_followups_started=state.completion_followups_started,
            prompt_tokens_used=state.prompt_tokens_used,
            completion_tokens_used=state.completion_tokens_used,
            owner_calls_started=state.owner_calls_started,
            global_read_signatures=state.global_read_signatures,
            owner_read_signatures=state.owner_read_signatures,
            reservations=state.reservations,
            denial_count=state.denial_count + 1,
            started_at_utc=state.started_at_utc,
            deadline_at_utc=state.deadline_at_utc,
        )
    else:
        new_state = state
    event = _safe_event(
        BUDGET_EVENT_DENIED,
        reason_code=reason_code,
        state=new_state,
        call_id=call_id,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        dimension=dimension,
    )
    decision = BudgetDecision(
        allowed=False,
        reason_code=reason_code,
        dimension=dimension,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )
    return new_state, decision


def _check_deadline_live(*, mono_now_ms: int, mono_deadline_ms: int) -> str | None:
    if mono_now_ms >= mono_deadline_ms:
        return REASON_DEADLINE
    return None


def _projected_capacity_ok(
    state: BudgetLedgerState,
    requests: Sequence[BudgetReserveRequest],
) -> tuple[str | None, str | None]:
    """Return (reason_code, dimension) if batch cannot be reserved; else (None, None)."""
    limits = state.limits
    active = _active_reservations(state)
    active_count = len(active)
    n = len(requests)
    if active_count + n > limits.max_parallel_calls:
        return REASON_PARALLEL, "max_parallel_calls"
    if state.capability_calls_started + active_count + n > limits.max_total_capability_calls:
        return REASON_TOTAL_CALLS, "max_total_capability_calls"

    # Per-request projected owner / signature load within the batch.
    batch_owner_reserved: dict[tuple[str, UUID], int] = {}
    batch_global_sig: dict[str, int] = {}
    batch_owner_sig: dict[tuple[str, UUID, str], int] = {}
    seen_call_ids: set[str] = set()

    existing_ids = {r.call_id for r in state.reservations}

    for req in requests:
        if req.call_id in seen_call_ids or req.call_id in existing_ids:
            return REASON_DUPLICATE_CALL_ID, "call_id"
        seen_call_ids.add(req.call_id)

        if req.capability_depth > limits.max_capability_depth:
            return REASON_CAPABILITY_DEPTH, "max_capability_depth"
        if req.agent_depth > limits.max_agent_depth:
            return REASON_AGENT_DEPTH, "max_agent_depth"

        owner_limit = _owner_limit_for(
            state,
            owner_kind=req.owner_kind,
            owner_version_id=req.owner_version_id,
        )
        if owner_limit is None:
            return REASON_OWNER_LIMITS_MISSING, "owner_limits"

        owner_key = (req.owner_kind, req.owner_version_id)
        owner_started = _owner_calls(
            state,
            owner_kind=req.owner_kind,
            owner_version_id=req.owner_version_id,
        )
        owner_active = _active_reserved_count(
            state,
            owner_kind=req.owner_kind,
            owner_version_id=req.owner_version_id,
        )
        batch_extra = batch_owner_reserved.get(owner_key, 0)
        if owner_started + owner_active + batch_extra + 1 > owner_limit.max_calls:
            return REASON_OWNER_CALLS, "owner_max_calls"
        batch_owner_reserved[owner_key] = batch_extra + 1

        if req.side_effect == "read":
            sig = compute_read_signature(
                binding_contract_digest=req.binding_contract_digest,
                arguments_digest=req.arguments_digest,
            )
            g_started = _global_sig_count(state, sig)
            g_active = _active_reserved_count(state, read_signature=sig)
            g_batch = batch_global_sig.get(sig, 0)
            if g_started + g_active + g_batch + 1 > limits.max_same_read_signature:
                return REASON_READ_SIGNATURE, "max_same_read_signature"
            batch_global_sig[sig] = g_batch + 1

            o_started = _owner_sig_count(
                state,
                owner_kind=req.owner_kind,
                owner_version_id=req.owner_version_id,
                read_signature=sig,
            )
            o_active = _active_reserved_count(
                state,
                owner_kind=req.owner_kind,
                owner_version_id=req.owner_version_id,
                read_signature=sig,
            )
            o_key = (req.owner_kind, req.owner_version_id, sig)
            o_batch = batch_owner_sig.get(o_key, 0)
            if (
                o_started + o_active + o_batch + 1
                > owner_limit.max_same_read_signature
            ):
                return REASON_OWNER_READ_SIGNATURE, "owner_max_same_read_signature"
            batch_owner_sig[o_key] = o_batch + 1

    return None, None


def pure_reserve(
    state: BudgetLedgerState,
    requests: Sequence[BudgetReserveRequest],
    *,
    cancelled: bool,
    mono_now_ms: int,
    mono_deadline_ms: int,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    """All-or-none reserve for one or many calls (pure)."""
    if not requests:
        return _deny(
            state,
            reason_code=REASON_PROTOCOL_ERROR,
            dimension="empty_batch",
            bump_denial=True,
        )

    if cancelled:
        return _deny(
            state,
            reason_code=REASON_CANCELLED,
            dimension="cancellation",
            call_id=requests[0].call_id,
            owner_kind=requests[0].owner_kind,
            owner_version_id=requests[0].owner_version_id,
        )

    deadline_reason = _check_deadline_live(
        mono_now_ms=mono_now_ms, mono_deadline_ms=mono_deadline_ms
    )
    if deadline_reason is not None:
        return _deny(
            state,
            reason_code=deadline_reason,
            dimension="max_wall_time_ms",
            call_id=requests[0].call_id,
        )

    # Completion / prompt token hard stop for subsequent work after overflow.
    if (
        state.limits.max_completion_tokens is not None
        and state.completion_tokens_used >= state.limits.max_completion_tokens
    ):
        return _deny(
            state,
            reason_code=REASON_COMPLETION_TOKENS,
            dimension="max_completion_tokens",
            call_id=requests[0].call_id,
        )

    reason, dimension = _projected_capacity_ok(state, requests)
    if reason is not None:
        return _deny(
            state,
            reason_code=reason,
            dimension=dimension,
            call_id=requests[0].call_id,
            owner_kind=requests[0].owner_kind,
            owner_version_id=requests[0].owner_version_id,
        )

    new_reservations: list[BudgetReservation] = list(state.reservations)
    built: list[BudgetReservation] = []
    for req in requests:
        read_sig: str | None = None
        if req.side_effect == "read":
            read_sig = compute_read_signature(
                binding_contract_digest=req.binding_contract_digest,
                arguments_digest=req.arguments_digest,
            )
        reservation = build_reservation(
            call_id=req.call_id,
            owner_kind=req.owner_kind,
            owner_version_id=req.owner_version_id,
            domain_key=req.domain_key,
            side_effect=req.side_effect,
            arguments_digest=req.arguments_digest,
            read_signature=read_sig,
            state="reserved",
        )
        new_reservations.append(reservation)
        built.append(reservation)

    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=new_reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    primary = built[0]
    event = _safe_event(
        BUDGET_EVENT_RESERVED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        call_id=primary.call_id if len(built) == 1 else None,
        owner_kind=primary.owner_kind if len(built) == 1 else None,
        owner_version_id=primary.owner_version_id if len(built) == 1 else None,
        reservation_digest=primary.reservation_digest if len(built) == 1 else None,
        extra={
            "reservedCount": len(built),
            "reservationDigests": [r.reservation_digest for r in built],
        },
    )
    decision = BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=primary if len(built) == 1 else None,
        reservations=tuple(built),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )
    return new_state, decision


def pure_mark_started(
    state: BudgetLedgerState,
    *,
    call_id: str,
    validated_arguments_digest: str,
    cancelled: bool,
    mono_now_ms: int,
    mono_deadline_ms: int,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    existing = _find_reservation(state, call_id)
    if existing is None:
        return _deny(
            state,
            reason_code=REASON_RESERVATION_NOT_FOUND,
            dimension="reservation",
            call_id=call_id,
        )
    if existing.state != "reserved":
        return _deny(
            state,
            reason_code=REASON_RESERVATION_STATE_INVALID,
            dimension="reservation_state",
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
        )
    if cancelled:
        return _deny(
            state,
            reason_code=REASON_CANCELLED,
            dimension="cancellation",
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
        )
    deadline_reason = _check_deadline_live(
        mono_now_ms=mono_now_ms, mono_deadline_ms=mono_deadline_ms
    )
    if deadline_reason is not None:
        return _deny(
            state,
            reason_code=deadline_reason,
            dimension="max_wall_time_ms",
            call_id=call_id,
        )

    args_digest = _require_digest(
        validated_arguments_digest, field_name="validated_arguments_digest"
    )
    if args_digest != existing.arguments_digest:
        return _deny(
            state,
            reason_code=REASON_ARGUMENTS_DIGEST_MISMATCH,
            dimension="arguments_digest",
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
        )

    started = build_reservation(
        call_id=existing.call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        domain_key=existing.domain_key,
        side_effect=existing.side_effect,
        arguments_digest=existing.arguments_digest,
        read_signature=existing.read_signature,
        state="started",
    )
    reservations = _replace_reservation(state.reservations, started)
    owner_usage = _bump_owner_usage(
        state.owner_calls_started,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
    )
    global_sigs = state.global_read_signatures
    owner_sigs = state.owner_read_signatures
    if existing.read_signature is not None:
        global_sigs = _bump_global_sig(
            global_sigs, read_signature=existing.read_signature
        )
        owner_sigs = _bump_owner_sig(
            owner_sigs,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
            read_signature=existing.read_signature,
        )

    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started + 1,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=owner_usage,
        global_read_signatures=global_sigs,
        owner_read_signatures=owner_sigs,
        reservations=reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_STARTED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        call_id=call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        reservation_digest=started.reservation_digest,
    )
    decision = BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=started,
        reservations=(started,),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )
    return new_state, decision


def pure_finish(
    state: BudgetLedgerState,
    *,
    call_id: str,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    existing = _find_reservation(state, call_id)
    if existing is None:
        return _deny(
            state,
            reason_code=REASON_RESERVATION_NOT_FOUND,
            dimension="reservation",
            call_id=call_id,
        )
    if existing.state == "finished":
        # Idempotent finish: no revision bump.
        event = _safe_event(
            BUDGET_EVENT_FINISHED,
            reason_code=REASON_ALLOWED,
            state=state,
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
            reservation_digest=existing.reservation_digest,
            extra={"idempotent": True},
        )
        return state, BudgetDecision(
            allowed=True,
            reason_code=REASON_ALLOWED,
            dimension=None,
            reservation=existing,
            reservations=(existing,),
            ledger_revision=state.revision,
            ledger_digest=state.ledger_digest,
            event=event,
        )
    if existing.state != "started":
        return _deny(
            state,
            reason_code=REASON_RESERVATION_STATE_INVALID,
            dimension="reservation_state",
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
            bump_denial=False,
        )

    finished = build_reservation(
        call_id=existing.call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        domain_key=existing.domain_key,
        side_effect=existing.side_effect,
        arguments_digest=existing.arguments_digest,
        read_signature=existing.read_signature,
        state="finished",
    )
    reservations = _replace_reservation(state.reservations, finished)
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_FINISHED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        call_id=call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        reservation_digest=finished.reservation_digest,
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=finished,
        reservations=(finished,),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_release_unstarted(
    state: BudgetLedgerState,
    *,
    call_id: str,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    existing = _find_reservation(state, call_id)
    if existing is None:
        return _deny(
            state,
            reason_code=REASON_RESERVATION_NOT_FOUND,
            dimension="reservation",
            call_id=call_id,
        )
    if existing.state == "released":
        event = _safe_event(
            BUDGET_EVENT_RELEASED,
            reason_code=REASON_ALLOWED,
            state=state,
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
            reservation_digest=existing.reservation_digest,
            extra={"idempotent": True},
        )
        return state, BudgetDecision(
            allowed=True,
            reason_code=REASON_ALLOWED,
            dimension=None,
            reservation=existing,
            reservations=(existing,),
            ledger_revision=state.revision,
            ledger_digest=state.ledger_digest,
            event=event,
        )
    if existing.state != "reserved":
        return _deny(
            state,
            reason_code=REASON_RESERVATION_STATE_INVALID,
            dimension="reservation_state",
            call_id=call_id,
            owner_kind=existing.owner_kind,
            owner_version_id=existing.owner_version_id,
            bump_denial=False,
        )

    released = build_reservation(
        call_id=existing.call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        domain_key=existing.domain_key,
        side_effect=existing.side_effect,
        arguments_digest=existing.arguments_digest,
        read_signature=existing.read_signature,
        state="released",
    )
    reservations = _replace_reservation(state.reservations, released)
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_RELEASED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        call_id=call_id,
        owner_kind=existing.owner_kind,
        owner_version_id=existing.owner_version_id,
        reservation_digest=released.reservation_digest,
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=released,
        reservations=(released,),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_start_provider_round(
    state: BudgetLedgerState,
    *,
    cancelled: bool,
    mono_now_ms: int,
    mono_deadline_ms: int,
    is_finalization: bool = False,
    estimated_prompt_tokens: int | None = None,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    if cancelled:
        return _deny(
            state,
            reason_code=REASON_CANCELLED,
            dimension="cancellation",
        )
    deadline_reason = _check_deadline_live(
        mono_now_ms=mono_now_ms, mono_deadline_ms=mono_deadline_ms
    )
    if deadline_reason is not None:
        return _deny(
            state,
            reason_code=deadline_reason,
            dimension="max_wall_time_ms",
        )
    if state.provider_rounds_started >= state.limits.max_provider_rounds:
        return _deny(
            state,
            reason_code=REASON_PROVIDER_ROUNDS,
            dimension="max_provider_rounds",
        )
    if (
        state.limits.max_completion_tokens is not None
        and state.completion_tokens_used >= state.limits.max_completion_tokens
    ):
        return _deny(
            state,
            reason_code=REASON_COMPLETION_TOKENS,
            dimension="max_completion_tokens",
        )
    # Prompt tokens: only when both limit and estimator are present.
    if (
        state.limits.max_prompt_tokens is not None
        and estimated_prompt_tokens is not None
    ):
        if estimated_prompt_tokens > state.limits.max_prompt_tokens:
            return _deny(
                state,
                reason_code=REASON_PROMPT_TOKENS,
                dimension="max_prompt_tokens",
            )

    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started + 1,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_PROVIDER_ROUND,
        reason_code=REASON_ALLOWED,
        state=new_state,
        extra={
            "isFinalization": bool(is_finalization),
            "providerRoundsStarted": new_state.provider_rounds_started,
        },
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_record_token_usage(
    state: BudgetLedgerState,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    prompt = _require_non_negative_int(prompt_tokens, field_name="prompt_tokens")
    completion = _require_non_negative_int(
        completion_tokens, field_name="completion_tokens"
    )
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used + prompt,
        completion_tokens_used=state.completion_tokens_used + completion,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_TOKEN_USAGE,
        reason_code=REASON_ALLOWED,
        state=new_state,
        extra={
            "promptTokensDelta": prompt,
            "completionTokensDelta": completion,
        },
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_add_owner_limits(
    state: BudgetLedgerState,
    owner_limits: OwnerBudgetLimits,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    """Add a Skill/Main owner bucket without mutating Run limits/usage/deadline."""
    existing = _owner_limit_for(
        state,
        owner_kind=owner_limits.owner_kind,
        owner_version_id=owner_limits.owner_version_id,
    )
    if existing is not None:
        # Exact reinjection / duplicate owner: no-op success without amplification.
        if (
            existing.max_calls == owner_limits.max_calls
            and existing.max_same_read_signature
            == owner_limits.max_same_read_signature
            and existing.owner_budget_digest == owner_limits.owner_budget_digest
        ):
            event = _safe_event(
                BUDGET_EVENT_OWNER_LIMITS_ADDED,
                reason_code=REASON_ALLOWED,
                state=state,
                owner_kind=owner_limits.owner_kind,
                owner_version_id=owner_limits.owner_version_id,
                extra={"noop": True},
            )
            return state, BudgetDecision(
                allowed=True,
                reason_code=REASON_ALLOWED,
                dimension=None,
                reservation=None,
                reservations=(),
                ledger_revision=state.revision,
                ledger_digest=state.ledger_digest,
                event=event,
            )
        return _deny(
            state,
            reason_code=REASON_OWNER_LIMITS_DUPLICATE,
            dimension="owner_limits",
            owner_kind=owner_limits.owner_kind,
            owner_version_id=owner_limits.owner_version_id,
        )

    if owner_limits.owner_kind == "skill_version":
        skill_count = sum(
            1 for o in state.owner_limits if o.owner_kind == "skill_version"
        )
        if skill_count >= state.limits.max_active_skills:
            return _deny(
                state,
                reason_code=REASON_ACTIVE_SKILLS,
                dimension="max_active_skills",
                owner_kind=owner_limits.owner_kind,
                owner_version_id=owner_limits.owner_version_id,
            )

    # Run limits byte-identical; only owner_limits tuple grows.
    new_owner_limits = tuple(state.owner_limits) + (owner_limits,)
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,  # frozen reference identity of values
        owner_limits=new_owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_OWNER_LIMITS_ADDED,
        reason_code=REASON_ALLOWED,
        state=new_state,
        owner_kind=owner_limits.owner_kind,
        owner_version_id=owner_limits.owner_version_id,
        extra={
            "ownerBudgetDigest": owner_limits.owner_budget_digest,
            "maxCalls": owner_limits.max_calls,
            "maxSameReadSignature": owner_limits.max_same_read_signature,
        },
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_start_main_agent_cycle(
    state: BudgetLedgerState,
    *,
    cancelled: bool,
    mono_now_ms: int,
    mono_deadline_ms: int,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    if cancelled:
        return _deny(state, reason_code=REASON_CANCELLED, dimension="cancellation")
    deadline_reason = _check_deadline_live(
        mono_now_ms=mono_now_ms, mono_deadline_ms=mono_deadline_ms
    )
    if deadline_reason is not None:
        return _deny(
            state, reason_code=deadline_reason, dimension="max_wall_time_ms"
        )
    if state.main_agent_cycles_started >= state.limits.max_main_agent_cycles:
        return _deny(
            state,
            reason_code=REASON_MAIN_AGENT_CYCLES,
            dimension="max_main_agent_cycles",
        )
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started + 1,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_MAIN_AGENT_CYCLE,
        reason_code=REASON_ALLOWED,
        state=new_state,
        extra={"mainAgentCyclesStarted": new_state.main_agent_cycles_started},
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_start_completion_followup(
    state: BudgetLedgerState,
    *,
    cancelled: bool,
    mono_now_ms: int,
    mono_deadline_ms: int,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    if cancelled:
        return _deny(state, reason_code=REASON_CANCELLED, dimension="cancellation")
    deadline_reason = _check_deadline_live(
        mono_now_ms=mono_now_ms, mono_deadline_ms=mono_deadline_ms
    )
    if deadline_reason is not None:
        return _deny(
            state, reason_code=deadline_reason, dimension="max_wall_time_ms"
        )
    if (
        state.completion_followups_started
        >= state.limits.max_completion_followup_rounds
    ):
        return _deny(
            state,
            reason_code=REASON_COMPLETION_FOLLOWUPS,
            dimension="max_completion_followup_rounds",
        )
    new_state = _rebuild_state(
        revision=state.revision + 1,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started + 1,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    event = _safe_event(
        BUDGET_EVENT_COMPLETION_FOLLOWUP,
        reason_code=REASON_ALLOWED,
        state=new_state,
        extra={
            "completionFollowupsStarted": new_state.completion_followups_started,
        },
    )
    return new_state, BudgetDecision(
        allowed=True,
        reason_code=REASON_ALLOWED,
        dimension=None,
        reservation=None,
        reservations=(),
        ledger_revision=new_state.revision,
        ledger_digest=new_state.ledger_digest,
        event=event,
    )


def pure_record_denial(
    state: BudgetLedgerState,
    *,
    reason_code: str,
    dimension: str | None = None,
    call_id: str | None = None,
) -> tuple[BudgetLedgerState, BudgetDecision]:
    """Record a pre-reserve policy/input denial metric without creating allowance."""
    return _deny(
        state,
        reason_code=reason_code,
        dimension=dimension,
        call_id=call_id,
        bump_denial=True,
    )


def remaining_completion_tokens(state: BudgetLedgerState) -> int | None:
    if state.limits.max_completion_tokens is None:
        return None
    remaining = state.limits.max_completion_tokens - state.completion_tokens_used
    return max(0, remaining)


def serialize_ledger_state(state: BudgetLedgerState) -> dict[str, Any]:
    """JSON-ready camelCase payload with no runtime objects."""
    return state.model_dump(mode="json", by_alias=True)


def deserialize_ledger_state(payload: Mapping[str, Any]) -> BudgetLedgerState:
    state = BudgetLedgerState.model_validate(payload)
    # Recompute digest to prove integrity / reject tampered payloads that lie.
    expected = compute_ledger_digest(
        revision=state.revision,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    if expected != state.ledger_digest:
        raise ValueError("ledger_digest mismatch on deserialize")
    return state


# ---------------------------------------------------------------------------
# Thread-safe facade
# ---------------------------------------------------------------------------


class BudgetLedger:
    """Process-local revisioned budget ledger with lock/CAS interface.

    Pure transitions live as module functions; this facade serializes access,
    holds the live monotonic deadline, cancellation flag, and optional event sink.
    """

    def __init__(
        self,
        initial_state: BudgetLedgerState,
        *,
        clock: BudgetClock | None = None,
        event_sink: EventSink | None = None,
        mono_started_ms: int | None = None,
        mono_deadline_ms: int | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state = initial_state
        self._clock: BudgetClock = clock or SystemBudgetClock()
        self._event_sink = event_sink
        self._cancelled = False
        start_ms = (
            int(mono_started_ms)
            if mono_started_ms is not None
            else self._clock.monotonic_ms()
        )
        if mono_deadline_ms is not None:
            deadline_ms = int(mono_deadline_ms)
        else:
            deadline_ms = start_ms + initial_state.limits.max_wall_time_ms
        self._mono_started_ms = start_ms
        self._mono_deadline_ms = deadline_ms

    @classmethod
    def create(
        cls,
        *,
        limits: RunBudgetLimits,
        owner_limits: Sequence[OwnerBudgetLimits] = (),
        clock: BudgetClock | None = None,
        event_sink: EventSink | None = None,
    ) -> BudgetLedger:
        clock = clock or SystemBudgetClock()
        started_at = clock.utc_now()
        state = create_initial_ledger_state(
            limits=limits,
            owner_limits=owner_limits,
            started_at_utc=started_at,
        )
        mono = clock.monotonic_ms()
        return cls(
            state,
            clock=clock,
            event_sink=event_sink,
            mono_started_ms=mono,
            mono_deadline_ms=mono + limits.max_wall_time_ms,
        )

    def _emit(self, decision: BudgetDecision) -> None:
        if self._event_sink is not None and decision.event is not None:
            self._event_sink(decision.event)

    def _apply(
        self,
        transition: Callable[
            [BudgetLedgerState], tuple[BudgetLedgerState, BudgetDecision]
        ],
    ) -> BudgetDecision:
        with self._lock:
            new_state, decision = transition(self._state)
            if new_state is not self._state:
                # CAS-style: only accept revision == current or current+1
                if new_state.revision < self._state.revision:
                    raise RuntimeError(REASON_PROTOCOL_ERROR)
                if (
                    new_state.revision != self._state.revision
                    and new_state.revision != self._state.revision + 1
                ):
                    raise RuntimeError(REASON_PROTOCOL_ERROR)
                self._state = new_state
            self._emit(decision)
            return decision

    def snapshot(self) -> BudgetLedgerState:
        with self._lock:
            return self._state

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> BudgetDecision:
        with self._lock:
            self._cancelled = True
            event = _safe_event(
                BUDGET_EVENT_CANCELLED,
                reason_code=REASON_CANCELLED,
                state=self._state,
                dimension="cancellation",
            )
            decision = BudgetDecision(
                allowed=True,
                reason_code=REASON_CANCELLED,
                dimension="cancellation",
                reservation=None,
                reservations=(),
                ledger_revision=self._state.revision,
                ledger_digest=self._state.ledger_digest,
                event=event,
            )
            self._emit(decision)
            return decision

    def mono_deadline_ms(self) -> int:
        with self._lock:
            return self._mono_deadline_ms

    def mono_started_ms(self) -> int:
        with self._lock:
            return self._mono_started_ms

    def remaining_wall_time_ms(self) -> int:
        with self._lock:
            now = self._clock.monotonic_ms()
            return max(0, self._mono_deadline_ms - now)

    def compare_and_swap(
        self,
        expected_revision: int,
        new_state: BudgetLedgerState,
    ) -> bool:
        """CAS for Plan 06 parity: install new_state only if revision matches."""
        with self._lock:
            if self._state.revision != expected_revision:
                return False
            if new_state.revision != expected_revision + 1:
                raise ValueError(
                    "new_state.revision must equal expected_revision + 1"
                )
            # Verify digest coherence.
            expected_digest = compute_ledger_digest(
                revision=new_state.revision,
                limits=new_state.limits,
                owner_limits=new_state.owner_limits,
                provider_rounds_started=new_state.provider_rounds_started,
                main_agent_cycles_started=new_state.main_agent_cycles_started,
                capability_calls_started=new_state.capability_calls_started,
                completion_followups_started=new_state.completion_followups_started,
                prompt_tokens_used=new_state.prompt_tokens_used,
                completion_tokens_used=new_state.completion_tokens_used,
                owner_calls_started=new_state.owner_calls_started,
                global_read_signatures=new_state.global_read_signatures,
                owner_read_signatures=new_state.owner_read_signatures,
                reservations=new_state.reservations,
                denial_count=new_state.denial_count,
                started_at_utc=new_state.started_at_utc,
                deadline_at_utc=new_state.deadline_at_utc,
            )
            if expected_digest != new_state.ledger_digest:
                raise ValueError("new_state.ledger_digest is inconsistent")
            # Run limits must remain frozen for the Run.
            if new_state.limits != self._state.limits:
                raise ValueError("CAS cannot change RunBudgetLimits")
            if (
                new_state.started_at_utc != self._state.started_at_utc
                or new_state.deadline_at_utc != self._state.deadline_at_utc
            ):
                raise ValueError("CAS cannot change started/deadline UTC fields")
            self._state = new_state
            return True

    def reserve_one(self, request: BudgetReserveRequest) -> BudgetDecision:
        return self._apply(
            lambda s: pure_reserve(
                s,
                (request,),
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
            )
        )

    def reserve_batch(
        self, requests: Sequence[BudgetReserveRequest]
    ) -> BudgetDecision:
        return self._apply(
            lambda s: pure_reserve(
                s,
                tuple(requests),
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
            )
        )

    def mark_started(
        self,
        call_id: str,
        validated_arguments_digest: str,
    ) -> BudgetDecision:
        return self._apply(
            lambda s: pure_mark_started(
                s,
                call_id=call_id,
                validated_arguments_digest=validated_arguments_digest,
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
            )
        )

    def finish(self, call_id: str) -> BudgetDecision:
        return self._apply(lambda s: pure_finish(s, call_id=call_id))

    def release_unstarted(self, call_id: str) -> BudgetDecision:
        return self._apply(lambda s: pure_release_unstarted(s, call_id=call_id))

    def finalize_reservation(self, call_id: str) -> BudgetDecision:
        """``finally`` helper: finish if started, else release if reserved."""
        with self._lock:
            existing = _find_reservation(self._state, call_id)
            if existing is None:
                new_state, decision = _deny(
                    self._state,
                    reason_code=REASON_RESERVATION_NOT_FOUND,
                    dimension="reservation",
                    call_id=call_id,
                )
                self._state = new_state
                self._emit(decision)
                return decision
            if existing.state == "started":
                new_state, decision = pure_finish(self._state, call_id=call_id)
            elif existing.state == "reserved":
                new_state, decision = pure_release_unstarted(
                    self._state, call_id=call_id
                )
            elif existing.state in ("finished", "released"):
                decision = BudgetDecision(
                    allowed=True,
                    reason_code=REASON_ALLOWED,
                    dimension=None,
                    reservation=existing,
                    reservations=(existing,),
                    ledger_revision=self._state.revision,
                    ledger_digest=self._state.ledger_digest,
                    event=_safe_event(
                        BUDGET_EVENT_FINISHED
                        if existing.state == "finished"
                        else BUDGET_EVENT_RELEASED,
                        reason_code=REASON_ALLOWED,
                        state=self._state,
                        call_id=call_id,
                        extra={"idempotent": True},
                    ),
                )
                self._emit(decision)
                return decision
            else:
                new_state, decision = _deny(
                    self._state,
                    reason_code=REASON_RESERVATION_STATE_INVALID,
                    dimension="reservation_state",
                    call_id=call_id,
                    bump_denial=False,
                )
            if new_state is not self._state:
                self._state = new_state
            self._emit(decision)
            return decision

    def start_provider_round(
        self,
        *,
        is_finalization: bool = False,
        estimated_prompt_tokens: int | None = None,
    ) -> BudgetDecision:
        return self._apply(
            lambda s: pure_start_provider_round(
                s,
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
                is_finalization=is_finalization,
                estimated_prompt_tokens=estimated_prompt_tokens,
            )
        )

    def record_token_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> BudgetDecision:
        return self._apply(
            lambda s: pure_record_token_usage(
                s,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )

    def remaining_completion_tokens(self) -> int | None:
        with self._lock:
            return remaining_completion_tokens(self._state)

    def add_owner_limits(self, owner_limits: OwnerBudgetLimits) -> BudgetDecision:
        return self._apply(lambda s: pure_add_owner_limits(s, owner_limits))

    def start_main_agent_cycle(self) -> BudgetDecision:
        return self._apply(
            lambda s: pure_start_main_agent_cycle(
                s,
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
            )
        )

    def start_completion_followup(self) -> BudgetDecision:
        return self._apply(
            lambda s: pure_start_completion_followup(
                s,
                cancelled=self._cancelled,
                mono_now_ms=self._clock.monotonic_ms(),
                mono_deadline_ms=self._mono_deadline_ms,
            )
        )

    def record_denial(
        self,
        *,
        reason_code: str,
        dimension: str | None = None,
        call_id: str | None = None,
    ) -> BudgetDecision:
        return self._apply(
            lambda s: pure_record_denial(
                s,
                reason_code=reason_code,
                dimension=dimension,
                call_id=call_id,
            )
        )

    def serialize(self) -> dict[str, Any]:
        with self._lock:
            return serialize_ledger_state(self._state)

    @classmethod
    def deserialize(
        cls,
        payload: Mapping[str, Any],
        *,
        clock: BudgetClock | None = None,
        event_sink: EventSink | None = None,
        mono_started_ms: int | None = None,
        mono_deadline_ms: int | None = None,
        remaining_wall_time_ms: int | None = None,
    ) -> BudgetLedger:
        """Restore ledger. Live monotonic deadline must be supplied or re-derived.

        Prefer ``remaining_wall_time_ms`` or explicit mono fields; never re-extend
        from wall-clock alone after restore.
        """
        state = deserialize_ledger_state(payload)
        clock = clock or SystemBudgetClock()
        now = clock.monotonic_ms()
        if mono_deadline_ms is not None:
            deadline = int(mono_deadline_ms)
            started = (
                int(mono_started_ms)
                if mono_started_ms is not None
                else deadline - state.limits.max_wall_time_ms
            )
        elif remaining_wall_time_ms is not None:
            deadline = now + max(0, int(remaining_wall_time_ms))
            started = now - (
                state.limits.max_wall_time_ms - max(0, int(remaining_wall_time_ms))
            )
        elif mono_started_ms is not None:
            started = int(mono_started_ms)
            deadline = started + state.limits.max_wall_time_ms
        else:
            # Conservative: treat restore as "time already fully elapsed risk" —
            # use full wall budget from now only when no mono metadata is provided
            # (tests / fresh process). Production Plan 06 must pass mono fields.
            started = now
            deadline = now + state.limits.max_wall_time_ms
        return cls(
            state,
            clock=clock,
            event_sink=event_sink,
            mono_started_ms=started,
            mono_deadline_ms=deadline,
        )


__all__ = [
    "BUDGET_EVENT_CANCELLED",
    "BUDGET_EVENT_COMPLETION_FOLLOWUP",
    "BUDGET_EVENT_DENIED",
    "BUDGET_EVENT_FINISHED",
    "BUDGET_EVENT_MAIN_AGENT_CYCLE",
    "BUDGET_EVENT_OWNER_LIMITS_ADDED",
    "BUDGET_EVENT_PROVIDER_ROUND",
    "BUDGET_EVENT_RELEASED",
    "BUDGET_EVENT_RESERVED",
    "BUDGET_EVENT_STARTED",
    "BUDGET_EVENT_TOKEN_USAGE",
    "BudgetClock",
    "BudgetDecision",
    "BudgetLedger",
    "BudgetLedgerState",
    "BudgetReservation",
    "BudgetReserveRequest",
    "DeterministicBudgetClock",
    "OwnerSignatureUsage",
    "OwnerUsage",
    "REASON_ACTIVE_SKILLS",
    "REASON_AGENT_DEPTH",
    "REASON_ALLOWED",
    "REASON_ARGUMENTS_DIGEST_MISMATCH",
    "REASON_CANCELLED",
    "REASON_CAPABILITY_DEPTH",
    "REASON_COMPLETION_FOLLOWUPS",
    "REASON_COMPLETION_TOKENS",
    "REASON_DEADLINE",
    "REASON_DUPLICATE_CALL_ID",
    "REASON_MAIN_AGENT_CYCLES",
    "REASON_OWNER_CALLS",
    "REASON_OWNER_LIMITS_DUPLICATE",
    "REASON_OWNER_LIMITS_MISSING",
    "REASON_OWNER_READ_SIGNATURE",
    "REASON_PARALLEL",
    "REASON_PROMPT_TOKENS",
    "REASON_PROTOCOL_ERROR",
    "REASON_PROVIDER_ROUNDS",
    "REASON_READ_SIGNATURE",
    "REASON_RESERVATION_NOT_FOUND",
    "REASON_RESERVATION_STATE_INVALID",
    "REASON_TOTAL_CALLS",
    "ReservationState",
    "SignatureUsage",
    "SystemBudgetClock",
    "build_ledger_digest_payload",
    "build_owner_signature_usage",
    "build_owner_usage",
    "build_reservation",
    "build_signature_usage",
    "compute_ledger_digest",
    "compute_owner_signature_usage_digest",
    "compute_owner_usage_digest",
    "compute_read_signature",
    "compute_reservation_digest",
    "compute_signature_usage_digest",
    "create_initial_ledger_state",
    "deserialize_ledger_state",
    "pure_add_owner_limits",
    "pure_finish",
    "pure_mark_started",
    "pure_record_denial",
    "pure_record_token_usage",
    "pure_release_unstarted",
    "pure_reserve",
    "pure_start_completion_followup",
    "pure_start_main_agent_cycle",
    "pure_start_provider_round",
    "remaining_completion_tokens",
    "serialize_ledger_state",
]
