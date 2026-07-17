"""Fixed-vector tests for CapabilityCall identity and HMAC idempotency keys."""

from __future__ import annotations

import unittest
import uuid

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
SECRET = "x" * 32


class LogicalCallKeyTests(unittest.TestCase):
    def test_provider_key_stable_and_position_sensitive(self) -> None:
        from app.assistant.capability_calls.idempotency import (
            make_provider_logical_call_key,
        )

        k1 = make_provider_logical_call_key(
            provider_round_index=0,
            assistant_message_index=1,
            provider_tool_call_id="tc_1",
        )
        k2 = make_provider_logical_call_key(
            provider_round_index=0,
            assistant_message_index=1,
            provider_tool_call_id="tc_1",
        )
        k3 = make_provider_logical_call_key(
            provider_round_index=1,
            assistant_message_index=1,
            provider_tool_call_id="tc_1",
        )
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertEqual(len(k1), 64)

    def test_workflow_and_nested_keys_differ(self) -> None:
        from app.assistant.capability_calls.idempotency import (
            make_nested_agent_logical_call_key,
            make_workflow_logical_call_key,
        )

        frame = uuid.uuid4()
        parent = uuid.uuid4()
        w = make_workflow_logical_call_key(
            root_continuation_id="root-1",
            frame_id=frame,
            node_visit_id="visit-1",
            invocation_ordinal=0,
        )
        n = make_nested_agent_logical_call_key(
            parent_call_id=parent,
            agent_round_index=0,
            provider_tool_call_id="tc_1",
        )
        self.assertNotEqual(w, n)

    def test_unicode_and_reordered_json_input_digest(self) -> None:
        from app.assistant.capability_calls.idempotency import digest_input_payload

        d1 = digest_input_payload({"b": 1, "a": "你好", "nested": {"z": True, "y": 2}})
        d2 = digest_input_payload({"a": "你好", "nested": {"y": 2, "z": True}, "b": 1})
        self.assertEqual(d1, d2)

    def test_equal_inputs_independent_runs_differ_by_run_id(self) -> None:
        from app.assistant.capability_calls.idempotency import (
            make_provider_logical_call_key,
            make_server_idempotency_key,
        )

        logical = make_provider_logical_call_key(
            provider_round_index=0,
            assistant_message_index=0,
            provider_tool_call_id="same",
        )
        run_a = uuid.uuid4()
        run_b = uuid.uuid4()
        ka = make_server_idempotency_key(
            secret=SECRET,
            run_id=run_a,
            logical_call_key=logical,
            frozen_target_digest=DIGEST_A,
            canonical_input_digest=DIGEST_B,
        )
        kb = make_server_idempotency_key(
            secret=SECRET,
            run_id=run_b,
            logical_call_key=logical,
            frozen_target_digest=DIGEST_A,
            canonical_input_digest=DIGEST_B,
        )
        self.assertNotEqual(ka, kb)

    def test_secret_strength_required(self) -> None:
        from app.assistant.capability_calls.idempotency import (
            make_server_idempotency_key,
            require_idempotency_secret,
        )

        with self.assertRaises(ValueError):
            require_idempotency_secret("short")
        with self.assertRaises(ValueError):
            make_server_idempotency_key(
                secret="short",
                run_id=uuid.uuid4(),
                logical_call_key="k",
                frozen_target_digest=DIGEST_A,
                canonical_input_digest=DIGEST_B,
            )

    def test_fingerprint_is_not_raw_key(self) -> None:
        from app.assistant.capability_calls.idempotency import (
            idempotency_key_fingerprint,
            make_server_idempotency_key,
        )

        key = make_server_idempotency_key(
            secret=SECRET,
            run_id=uuid.uuid4(),
            logical_call_key="k",
            frozen_target_digest=DIGEST_A,
            canonical_input_digest=DIGEST_B,
        )
        fp = idempotency_key_fingerprint(key)
        self.assertNotEqual(fp, key)
        self.assertNotEqual(fp, key[:12])
        self.assertEqual(len(fp), 12)


class StateMachineTests(unittest.TestCase):
    def test_exhaustive_illegal_pairs_rejected(self) -> None:
        from app.assistant.capability_calls.state_machine import (
            ALLOWED_CALL_TRANSITIONS,
            CallTransitionError,
            all_status_pairs,
            validate_call_transition,
        )

        for frm, to in all_status_pairs():
            if (frm, to) in ALLOWED_CALL_TRANSITIONS:
                continue
            with self.assertRaises(CallTransitionError):
                validate_call_transition(
                    from_status=frm,
                    to_status=to,
                    side_effect_started_at_is_set=False,
                    execution_mode="read_replayable",
                )

    def test_cancel_after_effect_start_forbidden(self) -> None:
        from app.assistant.capability_calls.state_machine import (
            CallTransitionError,
            validate_call_transition,
        )

        with self.assertRaises(CallTransitionError) as ctx:
            validate_call_transition(
                from_status="executing",
                to_status="cancelled",
                side_effect_started_at_is_set=True,
                execution_mode="external_idempotent",
            )
        self.assertEqual(ctx.exception.code, "effect_started_blocks_cancel")

    def test_retry_same_key_mode_matrix(self) -> None:
        from app.assistant.capability_calls.state_machine import (
            CallTransitionError,
            validate_call_transition,
        )

        with self.assertRaises(CallTransitionError):
            validate_call_transition(
                from_status="needs_reconciliation",
                to_status="authorized",
                side_effect_started_at_is_set=True,
                execution_mode="local_transactional",
                has_retry_same_key_authorization=True,
            )
        # external_idempotent with authorization ok
        rule = validate_call_transition(
            from_status="needs_reconciliation",
            to_status="authorized",
            side_effect_started_at_is_set=True,
            execution_mode="external_idempotent",
            has_retry_same_key_authorization=True,
        )
        self.assertEqual(rule, "retry_same_key")

    def test_plan08_run_delta_edge(self) -> None:
        from app.assistant.capability_calls.state_machine import PLAN08_RUN_TRANSITION_DELTA
        from app.assistant.capability_calls.settlement import CapabilityCallSettlementRepository  # noqa: F401
        from app.assistant.durable.repository import ALLOWED_TRANSITIONS

        self.assertIn(("cancelling", "needs_reconciliation"), PLAN08_RUN_TRANSITION_DELTA)
        self.assertIn(("cancelling", "needs_reconciliation"), ALLOWED_TRANSITIONS)


if __name__ == "__main__":
    unittest.main()
