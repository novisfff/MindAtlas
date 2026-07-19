"""Atomic call-owned waiting approval aggregate tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_call_owned_pause_commits_one_waiting_aggregate() -> None:
    from tests._db import make_session
    from tests.test_capability_call_repository import _make_main_agent_run
    from tests.test_durable_checkpoint_codec import (
        _manifest,
        _surface,
        _tool_call,
        _waiting_continuation,
    )

    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunInterrupt,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.durable.crash import (
        CrashPoint,
        TransactionRollbackInject,
        armed_crash,
    )
    from app.assistant.capabilities.contracts import ContinuationRef
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderUserMessage,
    )
    from app.assistant.policy import (
        create_initial_ledger_state,
        normalize_run_budget_limits,
    )
    from app.assistant.policy.contracts import build_authorization_decision_v2

    db = make_session()
    try:
        run_id = UUID("aaaaaaaa-0000-4000-8000-000000000801")
        run = _make_main_agent_run(
            db,
            id=run_id,
            status="running",
            state_revision=3,
            capability_ledger_mode="enforced",
            lease_owner="worker-1",
            lease_generation=1,
        )
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest="a" * 64,
            schema_version=1,
            payload={},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id, revision=1, policy_digest="b" * 64, payload={}
        )
        start = datetime.now(timezone.utc)
        ledger = create_initial_ledger_state(
            limits=normalize_run_budget_limits(),
            started_at_utc=start,
            deadline_at_utc=start + timedelta(minutes=2),
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=ledger.ledger_digest,
            payload=ledger.model_dump(mode="json", by_alias=True),
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id, revision=1, obligation_digest="c" * 64, payload={}
        )
        db.add_all([manifest, policy, budget, obligation])
        db.flush()
        run.current_manifest_revision_id = manifest.id
        run.current_policy_revision_id = policy.id
        run.current_budget_revision_id = budget.id
        run.current_obligation_revision_id = obligation.id
        run.deadline_at = ledger.deadline_at_utc
        db.commit()

        decision = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="awaiting_call_approval",
            reason_code="approval_required",
            principal_digest="d" * 64,
            entrypoint_policy_digest="e" * 64,
            global_policy_digest="f" * 64,
            owner_policy_digest="1" * 64,
            allowed_side_effects=("none", "compute", "read", "draft", "write_local"),
            grant_source_digest="2" * 64,
            exposure_digest="3" * 64,
            effective_policy_digest="4" * 64,
            write_release_digest="8" * 64,
        )
        factory = SimpleNamespace(decision_for_call=lambda **_kwargs: decision)
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=run.id),
            call=SimpleNamespace(
                call_id="call-1",
                domain_key="create_entry",
                arguments={"title": "golden"},
            ),
            current_manifest=SimpleNamespace(manifest_digest="a" * 64),
            binding=SimpleNamespace(
                ref=SimpleNamespace(
                    binding_contract_digest="5" * 64,
                    resolution_digest="6" * 64,
                )
            ),
            descriptor=SimpleNamespace(
                behavior=SimpleNamespace(side_effect="write_local"),
                capability_type="tool",
                target_id=None,
                target_version_id=None,
                descriptor_digest="7" * 64,
            ),
            authorization=SimpleNamespace(
                owner=SimpleNamespace(
                    owner_kind="main_agent",
                    owner_id="general_chat",
                    owner_version_id=None,
                )
            ),
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            lease=LeaseToken(
                run_id=run.id, worker_id="worker-1", lease_generation=1
            ),
        )

        outcome = aggregate.prepare(request)
        assert outcome.kind == "pause"
        # Pure prepare must leave no orphan durable layer.
        assert db.query(AssistantCapabilityCall).count() == 0
        assert db.query(AssistantRunInterrupt).count() == 0
        assert db.query(AssistantRunCheckpoint).count() == 0

        continuation = _waiting_continuation()
        root_continuation = ContinuationRef(
            continuation_type="capability_call",
            contract_version=1,
            reference_id=outcome.pause_proposal["interruptId"],
            payload_digest=outcome.pause_proposal["proposalDigest"],
        )
        waiting_call = continuation.waiting_call.model_copy(
            update={"capability_continuation": root_continuation}
        )
        from app.assistant.provider_loop.contracts import compute_scope_digest

        resumed_scope = continuation.execution_scope.model_copy(
            update={"run_id": run.id}
        )
        resumed_scope = resumed_scope.model_copy(
            update={
                "scope_digest": compute_scope_digest(
                    run_id=resumed_scope.run_id,
                    conversation_id=resumed_scope.conversation_id,
                    principal=resumed_scope.principal,
                    tenant_scope_id=resumed_scope.tenant_scope_id,
                )
            }
        )
        continuation = continuation.model_copy(
            update={
                "execution_scope": resumed_scope,
                "waiting_call": waiting_call,
            }
        )
        surface = _surface(_manifest())
        tool_call = _tool_call(surface)
        provider_messages = (
            ProviderUserMessage(content="q"),
            ProviderAssistantMessage(content=None, tool_calls=(tool_call,)),
        )
        with armed_crash(
            CrashPoint.AFTER_INTERRUPT_INSERT_BEFORE_OUTER_POINTER_CAS
        ):
            with pytest.raises(TransactionRollbackInject):
                aggregate.commit_pause(continuation, provider_messages)

        db.refresh(run)
        assert run.status == "running"
        assert run.state_revision == 3
        assert db.query(AssistantCapabilityCall).count() == 0
        assert db.query(AssistantRunInterrupt).count() == 0
        assert db.query(AssistantRunCheckpoint).count() == 0
        assert db.query(AssistantRunArtifact).count() == 0

        # The staged proposal remains retryable after the transaction rollback.
        aggregate.commit_pause(continuation, provider_messages)

        db.refresh(run)
        call = db.query(AssistantCapabilityCall).one()
        interrupt = db.query(AssistantRunInterrupt).one()
        assert run.status == "waiting_approval"
        assert run.state_revision == 4
        assert call.status == "awaiting_approval"
        assert call.interrupt_id == interrupt.id
        assert interrupt.interrupt_origin == "capability_call"
        assert interrupt.capability_call_id == call.id
        assert db.query(AssistantRunCheckpoint).count() == 1
        checkpoint = db.query(AssistantRunCheckpoint).one()
        assert checkpoint.schema_version == 3
        from app.assistant.durable.codec import decode_checkpoint

        decoded = decode_checkpoint(checkpoint.state_payload)
        assert [item.provider_tool_call_id for item in decoded.capability_calls] == [
            "call-1"
        ]
        assert decoded.capability_calls[0].status == "awaiting_approval"
        assert decoded.capability_calls[0].interrupt_id == interrupt.id
        assert db.query(AssistantRunArtifact).count() == 2

        # After HTTP approval and the next worker claim, server policy still
        # derives awaiting_call_approval. The stored exact approval must narrow
        # that decision to this call only and must not create a second pause.
        interrupt.status = "approved"
        call.status = "authorized"
        call.state_revision = int(call.state_revision) + 1
        run.status = "running"
        run.state_revision = 5
        run.lease_owner = "worker-2"
        run.lease_generation = 2
        db.commit()
        resumed = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            lease=LeaseToken(
                run_id=run.id, worker_id="worker-2", lease_generation=2
            ),
        ).prepare(request)
        assert resumed.kind == "dispatch_local"
        assert db.query(AssistantCapabilityCall).count() == 1
        assert db.query(AssistantRunInterrupt).count() == 1
    finally:
        db.close()
