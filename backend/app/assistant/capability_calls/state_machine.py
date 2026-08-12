"""CapabilityCall state machine + Plan 08 Run transition delta.

Call transitions are exhaustive. Run gains exactly one new edge:
``cancelling -> needs_reconciliation`` when an already-started call is unproven.
"""

from __future__ import annotations

from typing import Iterable

from app.assistant.capability_calls.contracts import (
    CAPABILITY_CALL_STATUSES,
    CAPABILITY_CALL_TERMINAL_STATUSES,
    CapabilityCallStatus,
    CapabilityExecutionMode,
)

# (from_status, to_status) -> rule name
ALLOWED_CALL_TRANSITIONS: dict[tuple[str, str], str] = {
    ("proposed", "denied"): "deny",
    ("proposed", "awaiting_approval"): "require_approval",
    ("proposed", "authorized"): "authorize_read_or_compute",
    ("proposed", "cancelled"): "cancel_unstarted",
    ("awaiting_approval", "authorized"): "approval_granted",
    ("awaiting_approval", "rejected"): "approval_rejected",
    ("awaiting_approval", "cancelled"): "cancel_unstarted",
    ("awaiting_approval", "expired"): "approval_expired",
    ("authorized", "executing"): "claim_attempt",
    ("authorized", "failed"): "fail_before_side_effect",
    ("authorized", "cancelled"): "cancel_unstarted",
    ("executing", "succeeded"): "commit_success",
    ("executing", "failed"): "commit_failure",
    ("executing", "cancelled"): "cancel_before_effect",
    ("executing", "unknown"): "classify_uncertain",
    ("unknown", "needs_reconciliation"): "enter_reconciliation",
    ("needs_reconciliation", "succeeded"): "reconcile_succeeded",
    ("needs_reconciliation", "failed"): "reconcile_failed",
    ("needs_reconciliation", "compensated"): "reconcile_compensated",
    ("needs_reconciliation", "authorized"): "retry_same_key",
}

# Plan 08 Run delta (merged into durable.repository.ALLOWED_TRANSITIONS).
PLAN08_RUN_TRANSITION_DELTA: dict[tuple[str, str], str] = {
    ("cancelling", "needs_reconciliation"): "call_settlement_unproven",
}

MODES_ALLOWING_RETRY_SAME_KEY: frozenset[CapabilityExecutionMode] = frozenset(
    {"external_idempotent"}
)
# external_reconcilable may reach authorized only after authoritative not_accepted
# proof — repository enforces evidence; state machine allows the edge only via
# retry_same_key rule with mode checks.


class CallTransitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_terminal_call_status(status: str) -> bool:
    return status in CAPABILITY_CALL_TERMINAL_STATUSES


def allowed_call_targets(from_status: str) -> frozenset[str]:
    return frozenset(to for (frm, to) in ALLOWED_CALL_TRANSITIONS if frm == from_status)


def validate_call_transition(
    *,
    from_status: str,
    to_status: str,
    side_effect_started_at_is_set: bool,
    execution_mode: str,
    has_retry_same_key_authorization: bool = False,
) -> str:
    """Return rule name or raise CallTransitionError."""
    rule = ALLOWED_CALL_TRANSITIONS.get((from_status, to_status))
    if rule is None:
        raise CallTransitionError(
            "invalid_call_transition",
            f"illegal call transition {from_status!r} -> {to_status!r}",
        )

    if is_terminal_call_status(from_status):
        raise CallTransitionError(
            "terminal_immutable",
            f"terminal call status {from_status!r} cannot transition",
        )

    if rule == "cancel_before_effect" and side_effect_started_at_is_set:
        raise CallTransitionError(
            "effect_started_blocks_cancel",
            "executing -> cancelled is illegal after side_effect_started_at is set",
        )

    if rule == "retry_same_key":
        if execution_mode == "local_transactional":
            raise CallTransitionError(
                "retry_same_key_forbidden",
                "local_transactional calls cannot retry_same_key from reconciliation",
            )
        if execution_mode in {"non_retriable", "unsupported", "pure_replayable", "read_replayable"}:
            raise CallTransitionError(
                "retry_same_key_forbidden",
                f"execution_mode {execution_mode!r} cannot retry_same_key from reconciliation",
            )
        if execution_mode == "external_idempotent" and not has_retry_same_key_authorization:
            raise CallTransitionError(
                "retry_same_key_unauthorized",
                "external_idempotent retry_same_key requires explicit operator authorization",
            )
        if execution_mode == "external_reconcilable" and not has_retry_same_key_authorization:
            raise CallTransitionError(
                "retry_same_key_unauthorized",
                "external_reconcilable retry requires authoritative not_accepted proof",
            )

    if (
        execution_mode == "local_transactional"
        and to_status == "executing"
        and side_effect_started_at_is_set
    ):
        raise CallTransitionError(
            "local_effect_start_forbidden",
            "local_transactional cannot persist executing with side_effect_started_at set",
        )

    return rule


def all_status_pairs() -> Iterable[tuple[str, str]]:
    for frm in CAPABILITY_CALL_STATUSES:
        for to in CAPABILITY_CALL_STATUSES:
            yield frm, to


__all__ = [
    "ALLOWED_CALL_TRANSITIONS",
    "MODES_ALLOWING_RETRY_SAME_KEY",
    "PLAN08_RUN_TRANSITION_DELTA",
    "CallTransitionError",
    "all_status_pairs",
    "allowed_call_targets",
    "is_terminal_call_status",
    "validate_call_transition",
]
