"""Plan 08 Task 7: external uncertainty classification matrix (scripted)."""

from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


class ExternalUncertaintyMatrixTests(unittest.TestCase):
    def test_mode_outcome_matrix_labels(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            ScriptedExternalAdapter,
            ScriptedExternalOutcome,
        )
        from app.assistant.capability_calls.state_machine import (
            CallTransitionError,
            validate_call_transition,
        )

        cases = [
            ("before_send_refusal", "failed"),
            ("accepted_then_timeout", "unknown"),
            ("ambiguous_5xx", "unknown"),
            ("key_echo_success", "succeeded"),
            ("duplicate_key", "succeeded"),
            ("non_retriable_uncertain", "unknown"),
        ]
        adapter = ScriptedExternalAdapter(
            [ScriptedExternalOutcome(kind=k) for k, _ in cases]  # type: ignore[arg-type]
        )
        for kind, expected in cases:
            outcome = adapter.send(idempotency_key="k", payload={"kind": kind})
            self.assertEqual(adapter.classify_for_ledger(outcome), expected)

        # After effect start, cancel is illegal.
        with self.assertRaises(CallTransitionError):
            validate_call_transition(
                from_status="executing",
                to_status="cancelled",
                side_effect_started_at_is_set=True,
                execution_mode="external_idempotent",
            )

        # unknown -> needs_reconciliation is the automatic recovery step.
        rule = validate_call_transition(
            from_status="unknown",
            to_status="needs_reconciliation",
            side_effect_started_at_is_set=True,
            execution_mode="external_idempotent",
        )
        self.assertEqual(rule, "enter_reconciliation")


if __name__ == "__main__":
    unittest.main()
