"""Plan 05 runtime adapters binding BudgetLedger to Gateway/Provider Loop ports.

Provider-loop and capability packages never import ledger state types.
These adapters close over a process-local BudgetLedger and project only
provider-neutral decisions / generation options.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityError,
)
from app.assistant.capabilities.errors import CapabilityDomainError
from app.assistant.policy.budgets import (
    BudgetLedger,
    BudgetReserveRequest,
)
from app.assistant.provider_loop.contracts import (
    CapabilityCallReservationDecision,
    CapabilityCallReservationItem,
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundResult,
)


def _safe_code(reason_code: str, *, fallback: str = "budget_exhausted") -> str:
    text = (reason_code or fallback).strip().replace(" ", "_")
    if not text:
        text = fallback
    return text[:64]


@dataclass
class BudgetLedgerDispatchGuard:
    """CapabilityDispatchGuard bound to a BudgetLedger reservation lifecycle."""

    ledger: BudgetLedger
    # Optional frozen Provider arguments digests by call_id for extra mismatch checks.
    expected_arguments_digests: dict[str, str] = field(default_factory=dict)

    def mark_started(
        self,
        *,
        call_id: str,
        validated_arguments_digest: str,
    ) -> None:
        decision = self.ledger.mark_started(
            call_id,
            validated_arguments_digest=validated_arguments_digest,
        )
        if decision.allowed:
            return
        # pure_mark_started already released on cancel/deadline/digest mismatch.
        raise CapabilityDomainError(
            CapabilityError(
                error_type="unauthorized",
                safe_code=_safe_code(decision.reason_code),
                safe_message="capability call budget denied at start",
                retry_disposition="never",
                call_id=call_id,
            )
        )

    def finish(self, *, call_id: str, status: str) -> None:
        del status
        decision = self.ledger.finish(call_id)
        if decision.allowed:
            return
        # Protocol error: reservation missing or not in started/finished.
        # Surface like mark_started so callers/logs see the deny instead of
        # silently leaving a started reservation un-finished.
        raise CapabilityDomainError(
            CapabilityError(
                error_type="unauthorized",
                safe_code=_safe_code(
                    decision.reason_code, fallback="reservation_state_invalid"
                ),
                safe_message="capability call budget denied at finish",
                retry_disposition="never",
                call_id=call_id,
            )
        )

    def release_unstarted(self, *, call_id: str, reason_code: str) -> None:
        del reason_code
        decision = self.ledger.release_unstarted(call_id)
        if decision.allowed:
            return
        # Missing reservation is best-effort on cancel/deny paths; only raise
        # when the reservation exists in an invalid state for release.
        if decision.reason_code == "reservation_not_found":
            return
        raise CapabilityDomainError(
            CapabilityError(
                error_type="unauthorized",
                safe_code=_safe_code(
                    decision.reason_code, fallback="reservation_state_invalid"
                ),
                safe_message="capability call budget denied at release",
                retry_disposition="never",
                call_id=call_id,
            )
        )


@dataclass
class BudgetLedgerRoundGuard:
    """ProviderRoundBudgetGuard bound to a BudgetLedger.

    before_round starts a provider round (consumes even if Provider fails) and
    caps max_output_tokens by remaining completion tokens when a limit exists.
    after_round records Provider-reported usage without resetting Plan 03 counters.
    """

    ledger: BudgetLedger

    def before_round(self, request: ProviderRoundRequest) -> ProviderGenerationOptions:
        decision = self.ledger.start_provider_round(
            is_finalization=request.finalization_round,
            estimated_prompt_tokens=None,  # no fabricated estimator
        )
        if not decision.allowed:
            # Provider-neutral denial; loop maps to ProviderLoopError.
            from app.assistant.provider_loop.contracts import (
                ProviderRoundBudgetDeniedError,
            )

            raise ProviderRoundBudgetDeniedError(
                reason_code=decision.reason_code,
                dimension=decision.dimension,
            )

        generation = request.generation
        remaining = self.ledger.remaining_completion_tokens()
        if remaining is None:
            return generation
        # Always cap generation by remaining completion budget. When remaining
        # is 0, force max_output_tokens=1 so the request cannot run unbounded
        # (start_provider_round already charged this round).
        if remaining < 1:
            capped = 1
        else:
            current = generation.max_output_tokens
            capped = remaining if current is None else min(current, remaining)
            if capped == current:
                return generation
        return ProviderGenerationOptions(
            max_output_tokens=capped,
            temperature=generation.temperature,
            tool_choice=generation.tool_choice,
            request_parallel_tool_calls=generation.request_parallel_tool_calls,
        )

    def after_round(self, result: ProviderRoundResult) -> None:
        usage = result.usage
        if usage is None:
            return
        prompt = int(usage.input_tokens or 0)
        completion = int(usage.output_tokens or 0)
        if prompt == 0 and completion == 0:
            return
        self.ledger.record_token_usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
        )


@dataclass
class BudgetLedgerReservationPort:
    """CapabilityCallReservationPort bound to BudgetLedger reserve_one/batch."""

    ledger: BudgetLedger

    def reserve_one(
        self, item: CapabilityCallReservationItem
    ) -> CapabilityCallReservationDecision:
        request = _to_reserve_request(item)
        decision = self.ledger.reserve_one(request)
        if decision.allowed:
            return CapabilityCallReservationDecision(
                allowed=True,
                reason_code=decision.reason_code or "allowed",
                reserved_call_ids=(item.call_id,),
                dimension=decision.dimension,
            )
        return CapabilityCallReservationDecision(
            allowed=False,
            reason_code=decision.reason_code,
            reserved_call_ids=(),
            dimension=decision.dimension,
        )

    def reserve_batch(
        self, items: list[CapabilityCallReservationItem] | tuple[CapabilityCallReservationItem, ...]
    ) -> CapabilityCallReservationDecision:
        if not items:
            return CapabilityCallReservationDecision(
                allowed=True,
                reason_code="allowed",
                reserved_call_ids=(),
            )
        requests = tuple(_to_reserve_request(item) for item in items)
        decision = self.ledger.reserve_batch(requests)
        if decision.allowed:
            return CapabilityCallReservationDecision(
                allowed=True,
                reason_code=decision.reason_code or "allowed",
                reserved_call_ids=tuple(item.call_id for item in items),
                dimension=decision.dimension,
            )
        return CapabilityCallReservationDecision(
            allowed=False,
            reason_code=decision.reason_code,
            reserved_call_ids=(),
            dimension=decision.dimension,
        )


@dataclass(frozen=True)
class FixedOwnerResolver:
    """Resolve every call to a fixed owner (Main Agent profile or Skill version)."""

    owner_kind: Literal["main_agent", "skill_version"] = "main_agent"
    owner_version_id: UUID = UUID(int=0)

    def resolve_owner(
        self,
        *,
        call: Any,
        descriptor: CapabilityDescriptor,
    ) -> tuple[str, UUID]:
        del call, descriptor
        return self.owner_kind, self.owner_version_id


@dataclass
class DomainKeyOwnerResolver:
    """Map domain_key -> (owner_kind, owner_version_id); default main agent.

    Mutable in place via ``rebind`` so ProviderLoopPorts and MainAgentPolicyRuntime
    can share one instance across skill.inject accept without rebuilding ports.
    """

    owners_by_domain_key: dict[str, tuple[str, UUID]]
    default_owner_kind: str = "main_agent"
    default_owner_version_id: UUID = UUID(int=0)

    def rebind(
        self,
        owners_by_domain_key: Mapping[str, tuple[str, UUID]],
        *,
        default_owner_kind: str | None = None,
        default_owner_version_id: UUID | None = None,
    ) -> None:
        """Replace the ownership map (and optional defaults) in place."""
        self.owners_by_domain_key = dict(owners_by_domain_key)
        if default_owner_kind is not None:
            self.default_owner_kind = default_owner_kind
        if default_owner_version_id is not None:
            self.default_owner_version_id = default_owner_version_id

    def resolve_owner(
        self,
        *,
        call: Any,
        descriptor: CapabilityDescriptor,
    ) -> tuple[str, UUID]:
        del descriptor
        domain_key = getattr(call, "domain_key", None)
        if isinstance(domain_key, str) and domain_key in self.owners_by_domain_key:
            return self.owners_by_domain_key[domain_key]
        return self.default_owner_kind, self.default_owner_version_id


def _to_reserve_request(item: CapabilityCallReservationItem) -> BudgetReserveRequest:
    return BudgetReserveRequest(
        call_id=item.call_id,
        owner_kind=item.owner_kind,  # type: ignore[arg-type]
        owner_version_id=item.owner_version_id,
        domain_key=item.domain_key,
        side_effect=item.side_effect,  # type: ignore[arg-type]
        arguments_digest=item.arguments_digest,
        binding_contract_digest=item.binding_contract_digest,
        capability_depth=item.capability_depth,
        agent_depth=item.agent_depth,
    )


__all__ = [
    "BudgetLedgerDispatchGuard",
    "BudgetLedgerReservationPort",
    "BudgetLedgerRoundGuard",
    "DomainKeyOwnerResolver",
    "FixedOwnerResolver",
]
