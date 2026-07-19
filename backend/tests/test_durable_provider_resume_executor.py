from __future__ import annotations

from types import SimpleNamespace

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_resume_waiting_rehydrates_snapshots_and_finalizes_run(monkeypatch) -> None:
    from tests.test_agent_policy_runtime import BUILD, _base_manifest
    from tests.test_durable_interrupt_resume import (
        _make_session,
        _seed_running_with_base,
    )

    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.reconstruction import reconstruct_provider_transcript
    from app.assistant.durable.runner import MainAgentRunExecutor
    from app.assistant.main_agent.policy_runtime import compose_main_agent_policy_runtime
    from app.assistant.provider_loop.messages import ProviderAssistantMessage

    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    db = _make_session()
    try:
        run, lease, revision, _repo = _seed_running_with_base(
            db,
            worker_id="worker-1",
        )
        manifest, _ = _base_manifest(run_id=run.id)
        runtime, _ports = compose_main_agent_policy_runtime(
            db=db,
            run_id=run.id,
            conversation_id=run.conversation_id,
            manifest=manifest,
            profile_key=manifest.main_agent.profile_key,
            profile_version_id=manifest.main_agent.version_id,
            profile_content_digest=manifest.main_agent.content_digest,
            app_build_revision=BUILD,
            provider=SimpleNamespace(),  # type: ignore[arg-type]
        )
        manifest = runtime.manifest
        budget = runtime.budget_ledger.snapshot()
        obligation = runtime.obligation_ledger.snapshot()

        manifest_row = db.get(AssistantRunManifestRevision, run.current_manifest_revision_id)
        policy_row = db.get(AssistantRunPolicyRevision, run.current_policy_revision_id)
        budget_row = db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        obligation_row = db.get(
            AssistantRunObligationRevision,
            run.current_obligation_revision_id,
        )
        assert all((manifest_row, policy_row, budget_row, obligation_row))
        manifest_row.payload = manifest.model_dump(mode="json", by_alias=True)
        manifest_row.manifest_digest = manifest.manifest_digest
        policy_row.payload = runtime.policy_snapshot.model_dump(mode="json", by_alias=True)
        policy_row.policy_digest = runtime.policy_snapshot.effective_policy_digest
        budget_row.payload = budget.model_dump(mode="json", by_alias=True)
        budget_row.budget_digest = budget.ledger_digest
        obligation_row.payload = obligation.model_dump(mode="json", by_alias=True)
        obligation_row.obligation_digest = obligation.ledger_digest
        db.commit()

        messages, transcript_digest = reconstruct_provider_transcript(db, run_id=run.id)
        continuation = SimpleNamespace(transcript_digest=transcript_digest)
        resolution = SimpleNamespace()
        final_message = ProviderAssistantMessage(content="resumed final", tool_calls=())
        loop_result = SimpleNamespace(
            status="completed",
            stop_reason="natural_completion",
            messages=tuple(messages) + (final_message,),
            final_text="resumed final",
        )

        monkeypatch.setattr(
            "app.assistant.main_agent.service.construct_openai_adapter_after_eligibility",
            lambda *_args, **_kwargs: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "app.assistant.workflow.durable.resume.validate_provider_waiting_resume",
            lambda **_kwargs: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "app.assistant.provider_loop.loop.ProviderAgentLoop.resume",
            lambda *_args, **_kwargs: loop_result,
        )

        executor = MainAgentRunExecutor()
        executor.resume_waiting(
            db=db,
            claimed=SimpleNamespace(run_id=run.id, lease=lease),
            continuation=continuation,
            resolution=resolution,
            expected_revision=revision,
            heartbeat=lambda: True,
        )

        db.refresh(run)
        assert run.status == "completed"
        persisted, _ = reconstruct_provider_transcript(db, run_id=run.id)
        assert persisted[-1].content == "resumed final"
    finally:
        db.close()


def test_v3_continue_provider_recovers_after_committed_tool_result(monkeypatch) -> None:
    from tests.test_agent_policy_runtime import BUILD, _base_manifest
    from tests.test_durable_interrupt_resume import _make_session, _seed_running_with_base

    from app.assistant.durable.codec import (
        checkpoint_state_digest,
        decode_checkpoint,
        encode_checkpoint_v3,
        migrate_checkpoint,
    )
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.reconstruction import (
        load_current_checkpoint,
        reconstruct_provider_transcript,
    )
    from app.assistant.durable.runner import MainAgentRunExecutor
    from app.assistant.main_agent.policy_runtime import compose_main_agent_policy_runtime
    from app.assistant.provider_loop.messages import ProviderAssistantMessage

    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    monkeypatch.setenv("ASSISTANT_CAPABILITY_LEDGER_MODE", "enforced")
    monkeypatch.setenv(
        "ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET", "s" * 32
    )
    reset_caches()
    db = _make_session()
    try:
        run, lease, _revision, _repo = _seed_running_with_base(
            db, worker_id="worker-1"
        )
        run.capability_ledger_mode = "enforced"
        manifest, _ = _base_manifest(run_id=run.id)
        runtime, _ports = compose_main_agent_policy_runtime(
            db=db,
            run_id=run.id,
            conversation_id=run.conversation_id,
            manifest=manifest,
            profile_key=manifest.main_agent.profile_key,
            profile_version_id=manifest.main_agent.version_id,
            profile_content_digest=manifest.main_agent.content_digest,
            app_build_revision=BUILD,
            provider=SimpleNamespace(),  # type: ignore[arg-type]
            policy_contract_version=2,
        )
        manifest = runtime.manifest
        budget = runtime.budget_ledger.snapshot()
        obligation = runtime.obligation_ledger.snapshot()
        manifest_row = db.get(
            AssistantRunManifestRevision, run.current_manifest_revision_id
        )
        policy_row = db.get(AssistantRunPolicyRevision, run.current_policy_revision_id)
        budget_row = db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        obligation_row = db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        manifest_row.payload = manifest.model_dump(mode="json", by_alias=True)
        manifest_row.manifest_digest = manifest.manifest_digest
        policy_row.payload = runtime.policy_snapshot.model_dump(mode="json", by_alias=True)
        policy_row.policy_digest = runtime.policy_snapshot.effective_policy_digest
        budget_row.payload = budget.model_dump(mode="json", by_alias=True)
        budget_row.budget_digest = budget.ledger_digest
        obligation_row.payload = obligation.model_dump(mode="json", by_alias=True)
        obligation_row.obligation_digest = obligation.ledger_digest

        checkpoint_row = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        checkpoint_v3 = migrate_checkpoint(checkpoint_row.state_payload).model_copy(
            update={"policy_contract_version": 2}
        )
        checkpoint_row.schema_version = 3
        checkpoint_row.state_payload = encode_checkpoint_v3(checkpoint_v3)
        checkpoint_row.state_digest = checkpoint_state_digest(checkpoint_v3)
        db.commit()

        messages, _ = reconstruct_provider_transcript(db, run_id=run.id)
        final_message = ProviderAssistantMessage(content="recovered final", tool_calls=())
        loop_result = SimpleNamespace(
            status="completed",
            stop_reason="natural_completion",
            messages=tuple(messages) + (final_message,),
            final_text="recovered final",
            manifest=manifest,
        )
        monkeypatch.setattr(
            "app.assistant.main_agent.service.construct_openai_adapter_after_eligibility",
            lambda *_args, **_kwargs: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "app.assistant.provider_loop.loop.ProviderAgentLoop.start",
            lambda *_args, **_kwargs: loop_result,
        )

        executor = MainAgentRunExecutor()
        executor._resume_enforced_checkpoint(  # noqa: SLF001
            db,
            claimed=SimpleNamespace(run_id=run.id, lease=lease),
            checkpoint=load_current_checkpoint(db, run_id=run.id),
            heartbeat=lambda: True,
        )

        db.refresh(run)
        assert run.status == "completed"
        persisted, _ = reconstruct_provider_transcript(db, run_id=run.id)
        assert persisted[-1].content == "recovered final"
        assert decode_checkpoint(checkpoint_row.state_payload).schema_version == 3
    finally:
        db.close()
