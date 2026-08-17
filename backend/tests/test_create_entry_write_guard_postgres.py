"""Two-session PostgreSQL serialization proof for the global write-safety lock.

Task 2 intentionally creates a throwaway schema from current ``Base.metadata``.
It must not use an Alembic-upgraded ``pre_ga_v1_0001`` schema before Task 7.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for write-guard lock proof",
)


def _url() -> str:
    if _POSTGRES_URL.startswith("postgresql://"):
        return _POSTGRES_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    return _POSTGRES_URL


@contextmanager
def _current_metadata_database():
    from app.database import Base
    from app.model_registry import load_all_live_models

    load_all_live_models()
    schema = f"task2_write_guard_{uuid.uuid4().hex}"
    admin = create_engine(_url(), future=True, pool_pre_ping=True)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        _url(),
        future=True,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _seed_executing_call(factory):  # noqa: ANN001
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits
    from tests.assistant_runtime_support import make_main_agent_run

    db = factory()
    try:
        run = make_main_agent_run(
            db,
            status="running",
            build_revision="task2-postgres-build",
            capability_ledger_mode="enforced",
            lease_owner="worker-1",
            lease_generation=1,
            commit=False,
        )
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest="1" * 64,
            schema_version=1,
            payload={"schemaVersion": 1},
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest="6" * 64,
            payload={},
        )
        started_at = datetime.now(timezone.utc)
        ledger = create_initial_ledger_state(
            limits=normalize_run_budget_limits(),
            started_at_utc=started_at,
            deadline_at_utc=started_at + timedelta(minutes=2),
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=ledger.ledger_digest,
            payload=ledger.model_dump(mode="json", by_alias=True),
        )
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest="7" * 64,
            payload={},
        )
        artifact = AssistantRunArtifact(
            run_id=run.id,
            kind="capability_call_input",
            media_type="application/json",
            storage_kind="inline",
            byte_size=2,
            content_sha256="2" * 64,
            inline_bytes=b"{}",
            metadata_json={"contractVersion": 1},
        )
        db.add_all((manifest, policy, budget, obligation, artifact))
        db.flush()
        run.current_manifest_revision_id = manifest.id
        run.current_policy_revision_id = policy.id
        run.current_budget_revision_id = budget.id
        run.current_obligation_revision_id = obligation.id
        run.deadline_at = ledger.deadline_at_utc
        call = AssistantCapabilityCall(
            run_id=run.id,
            manifest_revision_id=manifest.id,
            provider_tool_call_id="existing-uncertain",
            logical_call_key="provider:existing-uncertain",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="create_entry",
            descriptor_digest="3" * 64,
            authorization_digest="4" * 64,
            input_artifact_id=artifact.id,
            input_digest="2" * 64,
            side_effect_class="write_external",
            execution_mode="external_idempotent",
            idempotency_key="5" * 64,
            status="executing",
            state_revision=0,
            attempt_count=0,
        )
        db.add(call)
        db.commit()
        return run.id, call.id, manifest.id, artifact.id
    finally:
        db.close()


def _guard(db, run):  # noqa: ANN001
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
        ProductionWriteGuard,
        system_tool_input_schema_digest,
        system_tool_output_schema_digest,
    )
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.skills.resolution import (
        build_binding_snapshot,
        compute_system_tool_contract_set_digest,
        system_tool_schemas,
    )
    from app.schema.contracts import DeploymentClass

    closure = SimpleNamespace(
        rollout_revision_id=run.main_agent_rollout_revision_id,
        profile_version_id=run.main_agent_profile_version_id,
        model_id=run.resolved_model_id,
        closure_digest=run.runtime_closure_digest,
        build_revision=run.required_app_build_revision,
        runtime_contract_version=run.runtime_contract_version,
        checkpoint_codec_version=run.required_checkpoint_codec_version,
        capability_feature_digest=run.required_capability_feature_digest,
        create_entry_contract_digest=run.required_create_entry_contract_digest,
        write_policy_digest=run.required_write_policy_digest,
        write_cohort_digest=run.required_write_cohort_digest,
        reconciliation_contract_version=run.required_reconciliation_contract_version,
    )
    input_schema, output_schema = system_tool_schemas("create_entry")
    tool_set_digest = compute_system_tool_contract_set_digest()
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": "system-tool:create_entry",
            "targetId": None,
            "targetVersionId": None,
            "targetRevision": None,
            "inputSchemaDigest": system_tool_input_schema_digest("create_entry"),
            "outputSchemaDigest": system_tool_output_schema_digest("create_entry"),
            "executableRevision": closure.build_revision,
            "configDigest": tool_set_digest,
            "systemToolContractSetDigest": tool_set_digest,
        }
    )
    snapshot, dependency_digest, binding_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity="system-tool:create_entry",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=SimpleNamespace(
            terminal_output=False,
            needs_followup=True,
            followup_hint=None,
        ),
        config_digest=tool_set_digest,
        executable_revision=closure.build_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    binding = SimpleNamespace(
        ref=SimpleNamespace(
            capability_type="tool",
            capability_key="create_entry",
            target_identity="system-tool:create_entry",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            input_schema_digest=system_tool_input_schema_digest("create_entry"),
            output_schema_digest=system_tool_output_schema_digest("create_entry"),
            resolution_digest=resolution_digest,
            dependency_closure_digest=dependency_digest,
            binding_contract_digest=binding_digest,
        ),
        resolved=SimpleNamespace(
            capability_type="tool",
            capability_key="create_entry",
            target_identity="system-tool:create_entry",
            target_id=None,
            target_version_id=None,
            resolved_revision=None,
            input_schema_digest=system_tool_input_schema_digest("create_entry"),
            output_schema_digest=system_tool_output_schema_digest("create_entry"),
            config_digest=tool_set_digest,
            executable_revision=closure.build_revision,
            resolution_digest=resolution_digest,
            resolution_snapshot=snapshot,
            dependency_closure_digest=dependency_digest,
            binding_contract_digest=binding_digest,
        )
    )
    settings = SimpleNamespace(
        assistant_main_agent_write_mode="create_entry",
        assistant_durable_interrupts_enabled=True,
        assistant_interrupt_token_pepper="stable-pepper",
        assistant_capability_reconciliation_enabled=True,
        assistant_capability_reconciliation_evidence_secret="e" * 32,
        assistant_capability_call_idempotency_secret="i" * 32,
    )
    guard = ProductionWriteGuard(
        db,
        settings=settings,
        schema_compatibility=SimpleNamespace(is_compatible=lambda _db: True),
        launch_authorization=SimpleNamespace(
            allows_current_subject=lambda _db, **_kwargs: True
        ),
        deployment_class=DeploymentClass.REHEARSAL,
        closure_revalidator=SimpleNamespace(revalidate=lambda value: value),
        operator_control_available=lambda _db: True,
    )
    assert CREATE_ENTRY_CONTRACT_DIGEST == run.required_create_entry_contract_digest
    assert WRITE_POLICY_DIGEST == run.required_write_policy_digest
    assert WRITE_COHORT_DIGEST == run.required_write_cohort_digest
    assert RECONCILIATION_CONTRACT_VERSION == run.required_reconciliation_contract_version
    return guard, closure, binding


def _aggregate_request(run_id, binding):  # noqa: ANN001, ANN201
    return SimpleNamespace(
        execution_scope=SimpleNamespace(run_id=run_id),
        call=SimpleNamespace(
            call_id="new-local-proposal",
            domain_key="create_entry",
            arguments={"title": "race proof"},
        ),
        descriptor=SimpleNamespace(
            behavior=SimpleNamespace(side_effect="write_local"),
            capability_type="tool",
            target_id=None,
            target_version_id=None,
            descriptor_digest="7" * 64,
        ),
        current_manifest=SimpleNamespace(manifest_digest="1" * 64),
        authorization=SimpleNamespace(
            owner=SimpleNamespace(
                owner_kind="main_agent",
                owner_id=None,
                owner_version_id=None,
            )
        ),
        binding=binding,
    )


def _aggregate(db, run, guard, closure):  # noqa: ANN001, ANN201
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.policy.contracts import build_authorization_decision_v2

    decision = build_authorization_decision_v2(
        policy_allowed=True,
        dispatch_disposition="awaiting_call_approval",
        reason_code="approval_required",
        principal_digest="a" * 64,
        entrypoint_policy_digest="b" * 64,
        global_policy_digest="c" * 64,
        owner_policy_digest="d" * 64,
        allowed_side_effects=("none", "compute", "read", "draft", "write_local"),
        grant_source_digest="e" * 64,
        exposure_digest="f" * 64,
        effective_policy_digest="0" * 64,
        write_release_digest="a" * 64,
    )
    return DurableCapabilityLedgerAggregate(
        db=db,
        authorization_factory=SimpleNamespace(
            decision_for_call=lambda **_kwargs: decision
        ),
        idempotency_secret="i" * 32,
        write_guard=guard,
        lease=LeaseToken(
            run_id=run.id,
            worker_id="worker-1",
            lease_generation=1,
        ),
        runtime_closure_provider=lambda _run: closure,
    )


def test_new_proposal_and_unresolved_transition_serialize_in_both_orders():
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.capability_calls.repository import CapabilityCallRepository
    from app.assistant.capabilities.contracts import ContinuationRef
    from app.assistant.models import AssistantChatRun
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderUserMessage,
        digest_provider_transcript,
    )
    from tests.test_durable_checkpoint_codec import (
        _manifest,
        _surface,
        _tool_call,
        _waiting_continuation,
    )

    class _HoldingGuard:
        def __init__(self, inner, entered, release, snapshots):  # noqa: ANN001
            self.inner = inner
            self.lock_port = inner.lock_port
            self.entered = entered
            self.release = release
            self.snapshots = snapshots
            self.evaluations = 0

        def evaluate_new_proposal_locked(self, **kwargs):  # noqa: ANN201
            snapshot = self.inner.evaluate_new_proposal_locked(**kwargs)
            self.snapshots.append(snapshot)
            self.evaluations += 1
            # ``prepare`` is a pure first check and rolls back.  The second
            # evaluation is the commit-time fence whose advisory lock must
            # cover call/Interrupt/checkpoint persistence.
            if self.evaluations == 2:
                self.entered.set()
                assert self.release.wait(5)
            return snapshot

        def evaluate_post_approval_locked(self, **kwargs):  # noqa: ANN201
            return self.inner.evaluate_post_approval_locked(**kwargs)

    with _current_metadata_database() as engine:
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

        # Order A: the new proposal owns the advisory lock and commits first.
        run_id, call_id, manifest_id, artifact_id = _seed_executing_call(factory)
        proposal_holds = threading.Event()
        transition_started = threading.Event()
        release_proposal = threading.Event()
        order: list[str] = []
        snapshots: list[object] = []
        holding_guards: list[_HoldingGuard] = []

        def proposal_first() -> None:
            db = factory()
            try:
                run = db.get(AssistantChatRun, run_id)
                base_guard, closure, binding = _guard(db, run)
                guard = _HoldingGuard(
                    base_guard,
                    proposal_holds,
                    release_proposal,
                    snapshots,
                )
                holding_guards.append(guard)
                aggregate = _aggregate(db, run, guard, closure)
                outcome = aggregate.prepare(_aggregate_request(run_id, binding))
                assert outcome.kind == "pause"
                surface = _surface(_manifest())
                template = _tool_call(surface)
                existing_tool = template.model_copy(
                    update={"call_id": "existing-uncertain", "call_index": 0}
                )
                proposed_tool = template.model_copy(
                    update={"call_id": "new-local-proposal", "call_index": 1}
                )
                messages = (
                    ProviderUserMessage(content="race proof"),
                    ProviderAssistantMessage(
                        content=None,
                        tool_calls=(existing_tool, proposed_tool),
                    ),
                )
                root = ContinuationRef(
                    continuation_type="capability_call",
                    contract_version=1,
                    reference_id=outcome.pause_proposal["interruptId"],
                    payload_digest=outcome.pause_proposal["proposalDigest"],
                )
                continuation = _waiting_continuation()
                continuation = continuation.model_copy(
                    update={
                        "execution_scope": continuation.execution_scope.model_copy(
                            update={"run_id": run_id}
                        ),
                        "waiting_call": continuation.waiting_call.model_copy(
                            update={
                                "call_id": "new-local-proposal",
                                "capability_continuation": root,
                            }
                        ),
                        "transcript_digest": digest_provider_transcript(messages),
                    }
                )
                aggregate.commit_pause(continuation, messages)
                order.append("proposal_commit")
            finally:
                db.close()

        def unresolved_second() -> None:
            db = factory()
            try:
                transition_started.set()
                call = db.get(AssistantCapabilityCall, call_id)
                CapabilityCallRepository(db).transition_call(
                    call_id=call_id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=1,
                    to_status="unknown",
                    lease=None,
                )
                db.commit()
                order.append("unresolved_commit")
            finally:
                db.close()

        one = threading.Thread(target=proposal_first)
        two = threading.Thread(target=unresolved_second)
        one.start()
        assert proposal_holds.wait(5)
        two.start()
        assert transition_started.wait(5)
        release_proposal.set()
        one.join(10)
        two.join(10)
        assert not one.is_alive() and not two.is_alive()
        assert holding_guards[0].evaluations == 2
        assert snapshots[0].allowed is True
        assert order == ["proposal_commit", "unresolved_commit"]
        check = factory()
        try:
            proposed = (
                check.query(AssistantCapabilityCall)
                .filter_by(provider_tool_call_id="new-local-proposal")
                .one()
            )
            assert proposed.status == "awaiting_approval"
        finally:
            check.close()

        # Fresh current-metadata schema for the reverse serial order.
    with _current_metadata_database() as engine:
        factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
        run_id, call_id, _manifest_id, _artifact_id = _seed_executing_call(factory)
        unresolved_holds = threading.Event()
        proposal_started = threading.Event()
        release_unresolved = threading.Event()
        order = []
        outcomes = {}

        def unresolved_first() -> None:
            db = factory()
            try:
                call = db.get(AssistantCapabilityCall, call_id)
                CapabilityCallRepository(db).transition_call(
                    call_id=call_id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=0,
                    to_status="unknown",
                    lease=None,
                )
                unresolved_holds.set()
                assert release_unresolved.wait(5)
                db.commit()
                order.append("unresolved_commit")
            finally:
                db.close()

        def proposal_second() -> None:
            db = factory()
            try:
                proposal_started.set()
                run = db.get(AssistantChatRun, run_id)
                guard, closure, binding = _guard(db, run)
                aggregate = _aggregate(db, run, guard, closure)
                outcomes["proposal"] = aggregate.prepare(
                    _aggregate_request(run_id, binding)
                )
                order.append("proposal_return")
            finally:
                db.close()

        one = threading.Thread(target=unresolved_first)
        two = threading.Thread(target=proposal_second)
        one.start()
        assert unresolved_holds.wait(5)
        two.start()
        assert proposal_started.wait(5)
        release_unresolved.set()
        one.join(10)
        two.join(10)
        assert not one.is_alive() and not two.is_alive()
        assert outcomes["proposal"].kind == "deny"
        assert outcomes["proposal"].reason_code == "reconciliation_required"
        assert order == ["unresolved_commit", "proposal_return"]
