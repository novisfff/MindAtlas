"""Plan 05 completion decisions and Main Agent ProviderCompletionGuard adapter.

``CompletionDecision`` is the rich policy-side value (may name obligation IDs).
``ProviderCompletionGuard`` is provider-neutral and lives in provider_loop contracts;
the Main Agent adapter closes over ObligationLedger (+ optional BudgetLedger) and
projects only ``ProviderCompletionDisposition``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.policy.obligations import (
    REASON_ALL_SATISFIED,
    REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS,
    REASON_COMPLETION_FOLLOWUP_LIMIT,
    REASON_OBLIGATIONS_PENDING_AT_FINALIZATION,
    REASON_TERMINAL_TEXT_MISSING,
    REASON_WAITING_WITHOUT_OBLIGATION,
    CompletionObligation,
    ObligationLedger,
    ObligationLedgerState,
    pending_blocking,
    pure_apply_provider_text_evidence,
    pure_start_completion_followup,
    reason_code_for_pending,
)
from app.assistant.provider_loop.contracts import (
    ProviderCompletionDisposition,
    ProviderCompletionRequest,
)
from app.assistant.provider_loop.messages import ProviderCompletionInstructionMessage

CompletionAction = Literal["complete", "continue", "fail", "wait"]

# Content bound for protected completion instructions (deterministic, no secrets).
COMPLETION_INSTRUCTION_MAX_CHARS = 2_000


class CompletionDecision(FrozenContract):
    """Rich policy-side completion decision (may include obligation IDs)."""

    action: CompletionAction
    reason_code: str
    blocking_obligation_ids: tuple[str, ...]
    instruction_digest: str | None
    decision_digest: str


def compute_decision_digest(
    *,
    action: CompletionAction,
    reason_code: str,
    blocking_obligation_ids: Sequence[str],
    instruction_digest: str | None,
) -> str:
    payload: dict[str, JsonValue] = {
        "action": action,
        "reasonCode": reason_code,
        "blockingObligationIds": list(blocking_obligation_ids),
        "instructionDigest": instruction_digest,
    }
    return sha256_canonical_json(payload)


def build_completion_decision(
    *,
    action: CompletionAction,
    reason_code: str,
    blocking_obligation_ids: Sequence[str] = (),
    instruction_digest: str | None = None,
) -> CompletionDecision:
    ids = tuple(blocking_obligation_ids)
    digest = compute_decision_digest(
        action=action,
        reason_code=reason_code,
        blocking_obligation_ids=ids,
        instruction_digest=instruction_digest,
    )
    return CompletionDecision(
        action=action,
        reason_code=reason_code,
        blocking_obligation_ids=ids,
        instruction_digest=instruction_digest,
        decision_digest=digest,
    )


def _instruction_content_for(
    obligations: Sequence[CompletionObligation],
    *,
    locale: str,
) -> str:
    """Bounded deterministic summary: type/owner only — no user text or secrets.

    Content must not contain control characters (including newlines) so it can
    enter Provider message contracts that reject ``\\x00-\\x1f``.
    """
    parts: list[str] = []
    normalized = (locale or "en").strip().lower()
    zh = normalized.startswith("zh")
    if zh:
        header = "完成义务未满足。请继续完成下列要求后再结束："
    else:
        header = (
            "Completion obligations remain. Continue until the following "
            "requirements are satisfied, then provide a final answer:"
        )
    parts.append(header)
    for item in obligations[:16]:  # hard bound
        owner = item.owner_id
        if len(owner) > 48:
            owner = owner[:45] + "..."
        parts.append(f"{item.obligation_type} owner={item.owner_kind}:{owner}")
    # Use "; " separators — newlines are control chars forbidden by message contracts.
    content = " ".join(parts[:1]) + " " + "; ".join(parts[1:]) if len(parts) > 1 else parts[0]
    if len(content) > COMPLETION_INSTRUCTION_MAX_CHARS:
        content = content[: COMPLETION_INSTRUCTION_MAX_CHARS - 3] + "..."
    return content


def build_completion_instruction(
    *,
    obligations: Sequence[CompletionObligation],
    locale: str,
    manifest_revision: int,
    manifest_digest: str,
    guard_state_digest: str,
) -> ProviderCompletionInstructionMessage:
    content = _instruction_content_for(obligations, locale=locale)
    return ProviderCompletionInstructionMessage(
        locale=(locale or "en").strip().lower()[:16] or "en",
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        guard_state_digest=guard_state_digest,
        content=content,
    )


def evaluate_completion(
    state: ObligationLedgerState,
    *,
    candidate_text: str | None,
    finalization_round: bool,
    can_continue: bool,
    followup_budget_remaining: bool,
    skill_terminal_text_allowed: dict[str, bool] | None = None,
    apply_text: bool = True,
) -> tuple[CompletionDecision, ObligationLedgerState]:
    """Pure evaluation of completion against a ledger snapshot.

    Returns (decision, possibly-updated state after applying text evidence).
    Caller is responsible for CAS-installing the updated state when apply_text.
    """
    working = state
    text = candidate_text if candidate_text is not None else ""
    nonempty = bool(str(text).strip())

    if apply_text and nonempty:
        working, _ = pure_apply_provider_text_evidence(
            working,
            text=text,
            skill_terminal_text_allowed=skill_terminal_text_allowed or {},
        )

    blocking = pending_blocking(working)
    if not blocking:
        if not nonempty:
            # No obligations but empty text — Plan 03 empty_response path.
            return (
                build_completion_decision(
                    action="fail",
                    reason_code=REASON_TERMINAL_TEXT_MISSING,
                    blocking_obligation_ids=(),
                ),
                working,
            )
        return (
            build_completion_decision(
                action="complete",
                reason_code=REASON_ALL_SATISFIED,
                blocking_obligation_ids=(),
            ),
            working,
        )

    ids = tuple(o.obligation_id for o in blocking)
    primary = reason_code_for_pending(blocking)

    # Wait is only valid with pending approval/input.
    wait_types = {o.obligation_type for o in blocking}
    only_wait = wait_types and wait_types.issubset({"approval", "user_input"})
    if only_wait and not finalization_round:
        return (
            build_completion_decision(
                action="wait",
                reason_code=primary,
                blocking_obligation_ids=ids,
            ),
            working,
        )

    def _text_ok(o: CompletionObligation) -> bool:
        if o.obligation_type == "required_followup":
            return True
        if o.obligation_type == "terminal_output":
            if o.owner_kind == "main_agent":
                return True
            if o.owner_kind == "skill_version":
                return bool((skill_terminal_text_allowed or {}).get(o.owner_id, False))
        return False

    non_text = [o for o in blocking if not _text_ok(o)]

    if finalization_round:
        return (
            build_completion_decision(
                action="fail",
                reason_code=REASON_OBLIGATIONS_PENDING_AT_FINALIZATION,
                blocking_obligation_ids=ids,
            ),
            working,
        )

    if not can_continue or not followup_budget_remaining:
        reason = (
            REASON_COMPLETION_FOLLOWUP_LIMIT
            if not followup_budget_remaining
            else REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS
        )
        if non_text and followup_budget_remaining:
            reason = REASON_BUDGET_EXHAUSTED_WITH_OBLIGATIONS
        return (
            build_completion_decision(
                action="fail",
                reason_code=reason,
                blocking_obligation_ids=ids,
            ),
            working,
        )

    instruction_payload: dict[str, JsonValue] = {
        "reasonCode": primary,
        "blockingObligationIds": list(ids),
        "ledgerDigest": working.ledger_digest,
    }
    instruction_digest = sha256_canonical_json(instruction_payload)
    return (
        build_completion_decision(
            action="continue",
            reason_code=primary,
            blocking_obligation_ids=ids,
            instruction_digest=instruction_digest,
        ),
        working,
    )


@dataclass
class ObligationLedgerCompletionGuard:
    """Main Agent adapter: ProviderCompletionGuard over ObligationLedger.

    Closes over thread-safe ledgers. Provider Loop never sees ledger types.
    Optional budget hooks report whether provider/followup budget remains.
    """

    obligation_ledger: ObligationLedger
    locale: str = "en"
    max_completion_followup_rounds: int = 2
    can_continue_fn: Callable[[], bool] | None = None
    budget_start_followup_fn: Callable[[], None] | None = None

    def evaluate(
        self, request: ProviderCompletionRequest
    ) -> ProviderCompletionDisposition:
        with self.obligation_ledger._lock:  # noqa: SLF001 — intentional CAS boundary
            state = self.obligation_ledger._state  # noqa: SLF001
            skill_map = dict(self.obligation_ledger._skill_terminal_text_allowed)  # noqa: SLF001

            followup_remaining = (
                state.followup_rounds_started < self.max_completion_followup_rounds
            )
            can_continue = True
            if self.can_continue_fn is not None:
                can_continue = bool(self.can_continue_fn())

            decision, new_state = evaluate_completion(
                state,
                candidate_text=request.candidate_text,
                finalization_round=request.finalization_round,
                can_continue=can_continue,
                followup_budget_remaining=followup_remaining,
                skill_terminal_text_allowed=skill_map,
                apply_text=True,
            )

            if new_state is not state:
                if new_state.revision < state.revision:
                    raise RuntimeError("obligation ledger revision went backwards")
                self.obligation_ledger._state = new_state  # noqa: SLF001

            instruction = None
            if decision.action == "continue":
                follow_state, follow_decision = pure_start_completion_followup(
                    self.obligation_ledger._state,  # noqa: SLF001
                    max_completion_followup_rounds=self.max_completion_followup_rounds,
                )
                if not follow_decision.allowed:
                    decision = build_completion_decision(
                        action="fail",
                        reason_code=REASON_COMPLETION_FOLLOWUP_LIMIT,
                        blocking_obligation_ids=decision.blocking_obligation_ids,
                    )
                else:
                    self.obligation_ledger._state = follow_state  # noqa: SLF001
                    if self.budget_start_followup_fn is not None:
                        self.budget_start_followup_fn()
                    pre_blocking = [
                        o
                        for o in new_state.obligations
                        if o.obligation_id in decision.blocking_obligation_ids
                    ]
                    instruction = build_completion_instruction(
                        obligations=pre_blocking,
                        locale=self.locale,
                        manifest_revision=request.manifest_revision,
                        manifest_digest=request.manifest_digest,
                        guard_state_digest=self.obligation_ledger._state.ledger_digest,  # noqa: SLF001
                    )

            return ProviderCompletionDisposition(
                action=decision.action,
                reason_code=decision.reason_code,
                instruction=instruction,
                decision_digest=decision.decision_digest,
            )


def project_wait_without_obligation_error() -> ProviderCompletionDisposition:
    """Protocol failure: wait without exact pending approval/input."""
    decision = build_completion_decision(
        action="fail",
        reason_code=REASON_WAITING_WITHOUT_OBLIGATION,
        blocking_obligation_ids=(),
    )
    return ProviderCompletionDisposition(
        action="fail",
        reason_code=REASON_WAITING_WITHOUT_OBLIGATION,
        instruction=None,
        decision_digest=decision.decision_digest,
    )


__all__ = [
    "COMPLETION_INSTRUCTION_MAX_CHARS",
    "CompletionAction",
    "CompletionDecision",
    "ObligationLedgerCompletionGuard",
    "build_completion_decision",
    "build_completion_instruction",
    "compute_decision_digest",
    "evaluate_completion",
    "project_wait_without_obligation_error",
]
