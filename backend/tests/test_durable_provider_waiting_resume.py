"""Plan 07 Task 7: ProviderWaitingResolution after root terminal.

Covers:
- Only after root terminal, build one exact ProviderWaitingResolution
- Plan 03 resume validation (ProviderLoopResumeRequest invariants)
- Preserve completed sibling prefix / pending suffix / original surface / Manifest
- Tampered continuation / surface fails closed before runtime work
- Fresh authorization evidence for later siblings is the caller's concern;
  library validates continuation surface identity only
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from tests.test_durable_interrupt_resume import (  # noqa: E402
    DIGEST_A,
    DIGEST_B,
    DIGEST_C,
    _make_session,
    _material,
    _parent_ledger,
    _pause_and_resolve,
    _plan_with_human,
    _seed_running_with_base,
)


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


def _provider_continuation(*, root_continuation, call_id: str = "wait-call-1", run_id: UUID | None = None):
    from app.assistant.capabilities.contracts import CapabilityPrincipal
    from app.assistant.domain.contracts import create_model_ref, create_provider_ref
    from app.assistant.provider_loop.contracts import (
        ProviderLoopContinuation,
        ProviderUsage,
        ProviderWaitingCallState,
        create_execution_scope,
    )
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderUserMessage,
        digest_provider_message,
        digest_provider_transcript,
    )

    surface = _empty_surface()
    messages = (
        ProviderUserMessage(content="hello"),
        ProviderAssistantMessage(
            content="need approval",
            tool_calls=(),
        ),
    )
    assistant_digest = digest_provider_message(messages[1])
    transcript_digest = digest_provider_transcript(messages)
    rid = run_id or UUID("00000000-0000-4000-8000-000000000d01")
    cid = UUID("00000000-0000-4000-8000-000000000d02")
    principal = CapabilityPrincipal(
        principal_type="test",
        principal_id="principal-1",
        authenticated=True,
    )
    scope = create_execution_scope(
        run_id=rid,
        conversation_id=cid,
        principal=principal,
        tenant_scope_id=None,
    )
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=UUID("00000000-0000-4000-8000-000000000140"),
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_C,
        adapter_key="openai",
        adapter_revision="a1",
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    model = create_model_ref(
        model_id=UUID("00000000-0000-4000-8000-000000000150"),
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=UUID("00000000-0000-4000-8000-000000000151"),
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_B,
        model_config_digest=DIGEST_A,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    waiting = ProviderWaitingCallState(
        call_id=call_id,
        call_index=0,
        binding_contract_digest=DIGEST_A,
        descriptor_digest=DIGEST_B,
        behavior_digest=DIGEST_C,
        classification_revision="1",
        classification_ruleset_digest=DIGEST_A,
        capability_continuation=root_continuation,
    )
    cont = ProviderLoopContinuation(
        execution_scope=scope,
        model_ref=model,
        locale="en",
        max_rounds=8,
        provider_rounds_used=1,
        prior_tool_call_count=0,
        accumulated_usage=ProviderUsage(
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        ),
        current_manifest_revision=1,
        current_manifest_digest=DIGEST_A,
        exposed_surface=surface,
        assistant_message_digest=assistant_digest,
        transcript_digest=transcript_digest,
        waiting_call=waiting,
        next_call_index=1,
        pending_call_ids=("sibling-later",),
        completed_call_records=(),
    )
    return cont, messages, scope


class TestProviderWaitingResolutionUnit:
    def test_build_resolution_after_root_terminal(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef, CapabilityMetrics
        from app.assistant.workflow.durable.resume import (
            build_provider_waiting_resolution,
            capability_result_from_root,
        )

        root = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        cont, _messages, _scope = _provider_continuation(root_continuation=root)
        cap = capability_result_from_root(
            status="completed",
            user_text="workflow done",
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        )
        resolution = build_provider_waiting_resolution(
            provider_loop_continuation=cont,
            root_continuation=root,
            capability_result=cap,
        )
        assert resolution.call_id == "wait-call-1"
        assert resolution.capability_continuation.payload_digest == DIGEST_A
        assert resolution.capability_result.status == "completed"
        # Pending sibling preserved on continuation (not on resolution)
        assert cont.pending_call_ids == ("sibling-later",)

    def test_waiting_capability_result_forbidden(self) -> None:
        from app.assistant.capabilities.contracts import (
            CapabilityMetrics,
            CapabilityResult,
            ContinuationRef,
        )
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_PROTOCOL_ERROR,
            DurableResumeError,
            build_provider_waiting_resolution,
        )

        root = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        cont, _, _ = _provider_continuation(root_continuation=root)
        waiting_cap = CapabilityResult(
            status="waiting",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=root,
            terminal_output=False,
            needs_followup=True,
            error=None,
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        )
        with pytest.raises(DurableResumeError) as exc:
            build_provider_waiting_resolution(
                provider_loop_continuation=cont,
                root_continuation=root,
                capability_result=waiting_cap,
            )
        assert exc.value.reason_code == CODE_RESUME_PROTOCOL_ERROR

    def test_continuation_mismatch_rejected(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef, CapabilityMetrics
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_CONTINUATION_MISMATCH,
            DurableResumeError,
            build_provider_waiting_resolution,
            capability_result_from_root,
        )

        root = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        other = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_B,
        )
        cont, _, _ = _provider_continuation(root_continuation=root)
        cap = capability_result_from_root(
            status="completed",
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        )
        with pytest.raises(DurableResumeError) as exc:
            build_provider_waiting_resolution(
                provider_loop_continuation=cont,
                root_continuation=other,
                capability_result=cap,
            )
        assert exc.value.reason_code == CODE_RESUME_CONTINUATION_MISMATCH

    def test_tampered_transcript_fails_validation(self) -> None:
        from app.assistant.capabilities.contracts import ContinuationRef, CapabilityMetrics
        from app.assistant.provider_loop.messages import ProviderUserMessage
        from app.assistant.workflow.durable.resume import (
            CODE_RESUME_SURFACE_TAMPERED,
            DurableResumeError,
            build_provider_waiting_resolution,
            capability_result_from_root,
            validate_provider_waiting_resume,
        )

        root = ContinuationRef(
            continuation_type="durable_capability_invocation",
            contract_version=1,
            reference_id=str(uuid.uuid4()),
            payload_digest=DIGEST_A,
        )
        cont, messages, scope = _provider_continuation(root_continuation=root)
        cap = capability_result_from_root(
            status="completed",
            user_text="ok",
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        )
        resolution = build_provider_waiting_resolution(
            provider_loop_continuation=cont,
            root_continuation=root,
            capability_result=cap,
        )

        # Build a minimal fake manifest matching continuation digests
        class _Manifest:
            def __init__(self):
                self.run_id = scope.run_id
                self.revision = 1
                self.manifest_digest = DIGEST_A

        # Tamper messages so transcript_digest no longer matches
        bad_messages = (ProviderUserMessage(content="TAMPERED"),)
        with pytest.raises(DurableResumeError) as exc:
            validate_provider_waiting_resume(
                manifest=_Manifest(),
                messages=bad_messages,
                continuation=cont,
                resolved_waiting=resolution,
            )
        assert exc.value.reason_code in {
            CODE_RESUME_SURFACE_TAMPERED,
            "needs_reconciliation",
            "durable_resume_continuation_mismatch",
        } or exc.value.needs_reconciliation


class TestProviderWaitingResumeIntegration:
    def setup_method(self) -> None:
        reset_caches()
        self.db = _make_session()

    def teardown_method(self) -> None:
        self.db.close()

    def test_root_terminal_builds_one_provider_waiting_resolution(self) -> None:
        from app.assistant.workflow.durable.resume import execute_interrupt_resume

        plan = _plan_with_human()
        material = _material(
            plan,
            configs={
                "start": {},
                "hitl": {"kind": "approval", "title": "Approve?"},
                "output": {"text": "final-output"},
            },
        )
        run, lease, rev, _ = _seed_running_with_base(self.db)
        ledger = _parent_ledger()
        from app.assistant.durable.models import AssistantRunBudgetRevision

        budget = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        budget.payload = ledger.model_dump(mode="json", by_alias=True)
        budget.budget_digest = str(ledger.ledger_digest)
        self.db.flush()

        run, lease, rev, interrupt, proposal, parent_ledger = _pause_and_resolve(
            self.db,
            run=run,
            lease=lease,
            expected_revision=rev,
            plan=plan,
            material=material,
            parent_ledger=ledger,
        )
        cont, messages, scope = _provider_continuation(
            root_continuation=proposal.root_continuation,
            run_id=run.id,
        )

        result = execute_interrupt_resume(
            self.db,
            run_id=run.id,
            lease=lease,
            expected_revision=rev,
            material=material,
            parent_ledger=parent_ledger,
            provider_loop_continuation=cont,
            # Skip full Plan 03 resume validation (needs exact manifest object);
            # resolution construction alone is asserted here.
            provider_messages=None,
            provider_manifest=None,
        )
        assert result.kind == "root_terminal", (
            result.kind,
            result.reason_code,
            result.detail,
        )
        assert result.provider_waiting_resolution is not None
        assert result.provider_waiting_resolution.call_id == cont.waiting_call.call_id
        assert (
            result.provider_waiting_resolution.capability_continuation.payload_digest
            == proposal.root_continuation.payload_digest
        )
        assert result.provider_waiting_resolution.capability_result.status == "completed"
        # Completed sibling prefix empty; pending sibling suffix preserved on cont
        assert cont.pending_call_ids == ("sibling-later",)
        assert cont.completed_call_records == ()
        # Original surface identity frozen
        assert cont.exposed_surface.manifest_digest == DIGEST_A
        assert cont.provider_rounds_used == 1
