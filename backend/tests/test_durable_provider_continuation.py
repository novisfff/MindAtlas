"""Plan 06 Task 6: Provider continuation reconstruction and validation.

Covers:
- reconstruct exact protected Provider messages
- validate transcript/continuation before every resumed Provider request
- waiting continuation round-trip through Checkpoint
- fresh authorization evidence after recovery (never replay old evidence)
- grants/frames reconstruction from Checkpoint
"""

from __future__ import annotations

import unittest
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
BUILD = "build-test-1"

def _empty_surface(*, manifest_digest: str = DIGEST_A, manifest_revision: int = 1):
    from app.assistant.provider_loop.contracts import (
        ProviderToolSurface,
        compute_alias_map_digest,
        compute_surface_digest,
    )
    alias_map_digest = compute_alias_map_digest(
        provider_protocol="openai_compat",
        manifest_digest=manifest_digest,
        aliases=(),
    )
    surface_digest = compute_surface_digest(
        provider_protocol="openai_compat",
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        alias_map_digest=alias_map_digest,
        tools=(),
    )
    return ProviderToolSurface(
        provider_protocol="openai_compat",
        manifest_revision=manifest_revision,
        manifest_digest=manifest_digest,
        surface_digest=surface_digest,
        tools=(),
        alias_map_digest=alias_map_digest,
    )



def _session():
    from tests._db import make_session

    return make_session()


def _seed_run_with_transcript(db):
    from app.assistant.durable.materialize import materialize_base_run_state
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken
    from app.assistant.models import Conversation, Message
    from app.assistant.provider_loop.messages import (
        ProviderRuntimeInstructionMessage,
        ProviderUserMessage,
    )
    from tests.assistant_runtime_support import make_main_agent_run

    conv = Conversation(title=f"t-{uuid.uuid4().hex[:6]}")
    db.add(conv)
    db.flush()
    user = Message(conversation_id=conv.id, role="user", content="hi")
    assistant = Message(conversation_id=conv.id, role="assistant", content="")
    db.add_all([user, assistant])
    db.flush()
    run = make_main_agent_run(
        db,
        conversation=conv,
        user_message=user,
        assistant_message=assistant,
        status="queued",
        build_revision=BUILD,
        runtime_contract_version=1,
        memory_commit_status="pending",
        state_revision=0,
    )

    repo = DurableRunRepository(db)
    claimed = repo.claim_queued(
        run_id=run.id,
        expected_revision=0,
        worker_id="worker-cont",
        lease_ttl=timedelta(seconds=30),
    )
    lease = LeaseToken(
        run_id=run.id,
        worker_id="worker-cont",
        lease_generation=int(claimed.run.lease_generation),
    )
    messages = (
        ProviderRuntimeInstructionMessage(
            instruction_type="soft_finalization",
            locale="en",
            content="You are the Main Agent.",
        ),
        ProviderUserMessage(content="hello"),
    )
    mat = materialize_base_run_state(
        db,
        run_id=run.id,
        lease=lease,
        expected_revision=claimed.state_revision,
        manifest_payload={
            "schemaVersion": 1,
            "model": {"credentialId": str(uuid.UUID(int=1))},
        },
        manifest_digest=DIGEST_A,
        policy_payload={"schemaVersion": 1, "grants": []},
        policy_digest=DIGEST_A,
        budget_payload={"schemaVersion": 1, "revision": 0},
        budget_digest=DIGEST_A,
        obligation_payload={"schemaVersion": 1},
        obligation_digest=DIGEST_A,
        provider_messages=messages,
        protection_kinds=("protected", "public"),
        policy_revision_for_messages=True,
    )
    db.refresh(run)
    return run, lease, mat, messages


class ProviderContinuationReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self.db = _session()

    def tearDown(self) -> None:
        self.db.close()

    def test_reconstruct_protected_messages_preserve_discriminator(self) -> None:
        from app.assistant.durable.reconstruction import reconstruct_provider_transcript
        from app.assistant.provider_loop.messages import digest_provider_transcript

        run, lease, mat, original = _seed_run_with_transcript(self.db)
        messages, digest = reconstruct_provider_transcript(self.db, run_id=run.id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "runtime_instruction")
        self.assertEqual(messages[1].role, "user")
        # Must not downcast protected role to system.
        self.assertNotEqual(messages[0].role, "system")
        self.assertEqual(digest, digest_provider_transcript(messages))
        self.assertEqual(digest, digest_provider_transcript(original))

    def test_waiting_phase_checkpoint_requires_continuation(self) -> None:
        """Waiting phase Checkpoint stores exact portable continuation."""
        from app.assistant.durable.checkpoints import commit_unit_result
        from app.assistant.durable.reconstruction import load_current_checkpoint
        from app.assistant.provider_loop.contracts import (
            ProviderLoopContinuation,
            ProviderUsage,
            ProviderWaitingCallState,
            create_execution_scope,
        )
        from app.assistant.provider_loop.messages import digest_provider_transcript
        from app.assistant.domain.contracts import create_model_ref, create_provider_ref
        from app.assistant.provider_loop.contracts import ProviderToolSurface

        run, lease, mat, original = _seed_run_with_transcript(self.db)
        transcript_digest = digest_provider_transcript(original)

        # Minimal valid surface + model for continuation (codec helpers pattern).
        provider = create_provider_ref(
            provider_protocol="openai_compat",
            provider_config_id=uuid.UUID(int=9),
            provider_runtime_revision=1,
            provider_config_digest=DIGEST_A,
            adapter_key="openai",
            adapter_revision="a1",
            protocol_revision="p1",
            app_build_revision=BUILD,
        )
        model = create_model_ref(
            model_id=uuid.UUID(int=10),
            model_name="gpt-test",
            model_type="llm",
            model_runtime_revision=1,
            credential_id=uuid.UUID(int=11),
            credential_runtime_revision=1,
            credential_config_digest=DIGEST_A,
            model_config_digest=DIGEST_B,
            provider_ref_digest=provider.provider_ref_digest,
            capability_probe_id=None,
            capability_probe_digest=None,
        )
        scope = create_execution_scope(
            run_id=run.id,
            conversation_id=run.conversation_id,
            principal=__import__(
                "app.assistant.main_agent.authorization", fromlist=["LOCAL_ASSISTANT_PRINCIPAL"]
            ).LOCAL_ASSISTANT_PRINCIPAL,
            tenant_scope_id=None,
        )
        surface = _empty_surface()
        from app.assistant.capabilities.contracts import ContinuationRef

        waiting_state = ProviderWaitingCallState(
            call_id="wait-1",
            call_index=0,
            binding_contract_digest=DIGEST_A,
            descriptor_digest=DIGEST_B,
            behavior_digest=DIGEST_C,
            classification_revision="plan02-v1",
            classification_ruleset_digest=DIGEST_A,
            capability_continuation=ContinuationRef(
                continuation_type="human_approval",
                contract_version=1,
                reference_id="cont-1",
                payload_digest=DIGEST_B,
            ),
        )
        continuation = ProviderLoopContinuation(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            provider_rounds_used=1,
            prior_tool_call_count=0,
            accumulated_usage=ProviderUsage(
                input_tokens=1, output_tokens=1, total_tokens=2
            ),
            current_manifest_revision=1,
            current_manifest_digest=DIGEST_A,
            exposed_surface=surface,
            assistant_message_digest=DIGEST_C,
            transcript_digest=transcript_digest,
            waiting_call=waiting_state,
            next_call_index=1,
            pending_call_ids=(),
            completed_call_records=(),
        )
        result = commit_unit_result(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=mat.state_revision,
            phase="waiting",
            next_action_kind="wait",
            clear_inflight=True,
            provider_loop_continuation=continuation,
        )
        self.assertEqual(result.status, "running")
        ck = load_current_checkpoint(self.db, run_id=run.id)
        self.assertEqual(ck.phase, "waiting")
        self.assertIsNotNone(ck.provider_loop_continuation)
        self.assertEqual(ck.provider_loop_continuation.waiting_call.call_id, "wait-1")

        from app.assistant.durable.reconstruction import validate_resume_transcript

        validate_resume_transcript(
            self.db,
            run_id=run.id,
            continuation=ck.provider_loop_continuation,
        )

    def test_resume_rejects_transcript_mismatch(self) -> None:
        from app.assistant.durable.reconstruction import validate_resume_transcript
        from app.assistant.provider_loop.contracts import (
            ProviderLoopContinuation,
            ProviderUsage,
            ProviderWaitingCallState,
            ProviderToolSurface,
            create_execution_scope,
        )
        from app.assistant.domain.contracts import create_model_ref, create_provider_ref
        from app.assistant.capabilities.contracts import ContinuationRef
        from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

        run, lease, mat, original = _seed_run_with_transcript(self.db)
        provider = create_provider_ref(
            provider_protocol="openai_compat",
            provider_config_id=uuid.UUID(int=9),
            provider_runtime_revision=1,
            provider_config_digest=DIGEST_A,
            adapter_key="openai",
            adapter_revision="a1",
            protocol_revision="p1",
            app_build_revision=BUILD,
        )
        model = create_model_ref(
            model_id=uuid.UUID(int=10),
            model_name="gpt-test",
            model_type="llm",
            model_runtime_revision=1,
            credential_id=uuid.UUID(int=11),
            credential_runtime_revision=1,
            credential_config_digest=DIGEST_A,
            model_config_digest=DIGEST_B,
            provider_ref_digest=provider.provider_ref_digest,
            capability_probe_id=None,
            capability_probe_digest=None,
        )
        scope = create_execution_scope(
            run_id=run.id,
            conversation_id=run.conversation_id,
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            tenant_scope_id=None,
        )
        surface = _empty_surface()
        waiting_state = ProviderWaitingCallState(
            call_id="wait-1",
            call_index=0,
            binding_contract_digest=DIGEST_A,
            descriptor_digest=DIGEST_B,
            behavior_digest=DIGEST_C,
            classification_revision="plan02-v1",
            classification_ruleset_digest=DIGEST_A,
            capability_continuation=ContinuationRef(
                continuation_type="human_approval",
                contract_version=1,
                reference_id="cont-1",
                payload_digest=DIGEST_B,
            ),
        )
        bad = ProviderLoopContinuation(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            provider_rounds_used=1,
            prior_tool_call_count=0,
            accumulated_usage=ProviderUsage(
                input_tokens=1, output_tokens=1, total_tokens=2
            ),
            current_manifest_revision=1,
            current_manifest_digest=DIGEST_A,
            exposed_surface=surface,
            assistant_message_digest=DIGEST_C,
            transcript_digest=DIGEST_B,  # wrong
            waiting_call=waiting_state,
            next_call_index=1,
            pending_call_ids=(),
            completed_call_records=(),
        )
        with self.assertRaises(ValueError) as ctx:
            validate_resume_transcript(self.db, run_id=run.id, continuation=bad)
        self.assertIn("transcript", str(ctx.exception).lower())

    def test_fresh_authorization_evidence_after_recovery(self) -> None:
        """Recovery must issue fresh evidence; never persist/replay old credentials."""
        from app.assistant.durable.reconstruction import (
            issue_fresh_authorization_evidence,
        )

        old_evidence = {
            "call_id": "call-1",
            "evidence_digest": DIGEST_A,
            "issued_at": "2020-01-01T00:00:00Z",
            "credential_token": "MUST_NOT_PERSIST",
        }
        issued: list[dict[str, Any]] = []

        def factory(*, call_id: str, binding_digest: str) -> dict[str, Any]:
            payload = {
                "call_id": call_id,
                "binding_digest": binding_digest,
                "evidence_digest": DIGEST_B,
                "fresh": True,
            }
            issued.append(payload)
            return payload

        fresh = issue_fresh_authorization_evidence(
            previous_evidence=old_evidence,
            call_id="call-1",
            binding_digest=DIGEST_C,
            factory=factory,
        )
        self.assertTrue(fresh["fresh"])
        self.assertEqual(fresh["evidence_digest"], DIGEST_B)
        self.assertNotEqual(fresh.get("evidence_digest"), old_evidence["evidence_digest"])
        self.assertNotIn("credential_token", fresh)
        self.assertEqual(len(issued), 1)

    def test_grants_and_frames_reconstruct_from_checkpoint(self) -> None:
        from app.assistant.durable.checkpoints import commit_prepared_unit
        from app.assistant.durable.contracts import DurableExecutionUnitV1
        from app.assistant.durable.reconstruction import (
            load_current_checkpoint,
            reconstruct_capability_frames,
        )
        from app.assistant.policy.recursion import build_capability_call_frame

        run, lease, mat, _ = _seed_run_with_transcript(self.db)
        frame = build_capability_call_frame(
            call_id="call-1",
            capability_type="tool",
            domain_key="tools.search",
            target_identity="remote-tool:search",
            target_version_id=None,
            binding_contract_digest=DIGEST_A,
            owner_kind="main_agent",
            owner_version_id=uuid.UUID(int=42),
            capability_depth=1,
            agent_depth=1,
        )
        unit = DurableExecutionUnitV1(
            logical_unit_id="cap:group:1",
            kind="capability_group",
            state="prepared",
            provider_round=0,
            call_ids=("call-1",),
            attempt=1,
            reserved_budget_revision=0,
            started_budget_revision=None,
        )
        commit_prepared_unit(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=mat.state_revision,
            unit=unit,
            phase="dispatching_calls",
            next_action_kind="dispatch_calls",
            capability_frames=(frame,),
        )
        ck = load_current_checkpoint(self.db, run_id=run.id)
        frames = reconstruct_capability_frames(ck)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].call_id, "call-1")
        self.assertEqual(frames[0].binding_contract_digest, DIGEST_A)
        self.assertEqual(frames[0].domain_key, "tools.search")


if __name__ == "__main__":
    unittest.main()
