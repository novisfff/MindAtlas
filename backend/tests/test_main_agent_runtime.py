"""Main Agent runtime admission + Assistant integration (Plan 04 Task 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.contracts import (  # noqa: E402
    ModelRef,
    ProviderRef,
    ResolvedMainAgentRef,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS  # noqa: E402
from app.assistant.main_agent.events import (  # noqa: E402
    MainAgentEventAdapter,
    SKILL_ACTIVATION_START,
    is_internal_event,
    mark_internal,
    skill_activation_end_payload,
    skill_activation_start_payload,
    strip_visibility_marker,
)
from app.assistant.main_agent.model_eligibility import FrozenModelIdentity  # noqa: E402
from app.assistant.main_agent.service import (  # noqa: E402
    MODE_OFF,
    MODE_SHADOW_PRODUCTION,
    PROFILE_DISABLED,
    PROFILE_UNAVAILABLE,
    AdmissionContext,
    AssistantRuntimeRequest,
    MainAgentAdmissionError,
    MainAgentService,
    build_base_manifest_with_controls,
    compute_main_agent_effective_policy_digest,
    load_default_published_profile,
    select_runtime_for_mode,
    should_construct_main_agent,
    validate_profile_for_assistant_chat,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    NoOpManifestEffectLifecyclePort,
    NoOpRoundContextProvider,
    ProviderLoopPorts,
    ProviderLoopResult,
    ProviderUsage,
    SafeProviderError,
)
from app.assistant.provider_loop.scheduler import SequentialSiblingExecutor  # noqa: E402
from app.assistant.skills.schemas import (  # noqa: E402
    MainAgentProfileSnapshotV1,
    MainAgentProfileSnapshotV2,
    default_main_agent_profile_snapshot,
    default_main_agent_profile_snapshot_v2,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000801")
CONV_ID = UUID("00000000-0000-4000-8000-000000000802")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000810")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000811")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000820")
CRED_ID = UUID("00000000-0000-4000-8000-000000000821")
PROBE_ID = UUID("00000000-0000-4000-8000-000000000822")


def test_select_runtime_off_never_constructs_main_agent() -> None:
    runtime, reason = select_runtime_for_mode(mode="off", execution_kind="production")
    assert runtime is None
    assert reason == MODE_OFF
    assert should_construct_main_agent(mode="off") is False
    assert should_construct_main_agent(mode="off", execution_kind="evaluation") is False


def test_select_runtime_shadow_production_vs_evaluation() -> None:
    runtime, reason = select_runtime_for_mode(mode="shadow", execution_kind="production")
    assert runtime is None
    assert reason == MODE_SHADOW_PRODUCTION
    assert should_construct_main_agent(mode="shadow", execution_kind="production") is False

    # Plan 2 Task 9: shadow never selects Legacy; evaluation shadow also refuses
    # construction via select_runtime_for_mode (execution_kind is observational only).
    runtime, reason = select_runtime_for_mode(mode="shadow", execution_kind="evaluation")
    assert runtime is None
    assert reason == MODE_SHADOW_PRODUCTION
    assert should_construct_main_agent(mode="shadow", execution_kind="evaluation") is False


def test_select_runtime_read_only_admits_main_agent() -> None:
    runtime, reason = select_runtime_for_mode(mode="read_only", execution_kind="production")
    assert runtime == "main_agent"
    assert reason is None
    assert should_construct_main_agent(mode="read_only") is True


def test_validate_profile_controls_and_entrypoint() -> None:
    # Shared-field validation still accepts historical V1 for unit coverage.
    snap = default_main_agent_profile_snapshot()
    with pytest.raises(MainAgentAdmissionError) as exc:
        validate_profile_for_assistant_chat(snap)
    assert exc.value.reason_code in {"control_missing", "entrypoint_unsupported"}

    good_v1 = MainAgentProfileSnapshotV1.model_validate(
        {
            **snap.model_dump(by_alias=True, mode="json"),
            "controlCapabilityKeys": list(MAIN_AGENT_CONTROL_KEYS),
        }
    )
    keys = validate_profile_for_assistant_chat(good_v1)
    assert keys == MAIN_AGENT_CONTROL_KEYS

    # Production path is V2; shared fields validate the same way.
    good_v2 = MainAgentProfileSnapshotV2.model_validate(
        {
            **default_main_agent_profile_snapshot_v2().model_dump(by_alias=True, mode="json"),
            "controlCapabilityKeys": list(MAIN_AGENT_CONTROL_KEYS),
        }
    )
    assert validate_profile_for_assistant_chat(good_v2) == MAIN_AGENT_CONTROL_KEYS


def test_events_internal_visibility_and_safe_activation() -> None:
    public: list[tuple[str, dict]] = []
    adapter = MainAgentEventAdapter(lambda n, p: public.append((n, p)))

    adapter.skill_activation_start(call_id="c1", candidate_count=2)
    adapter.skill_activation_end(
        status="success",
        activated_version_ids=(uuid4(),),
        manifest_revision=2,
        manifest_digest=DIGEST_A,
    )
    adapter.diagnostic(code="staging_progress", detail={"n": 1})

    assert any(name == SKILL_ACTIVATION_START for name, _ in public)
    internal_rows = [p for _, p in public if is_internal_event(p)]
    assert internal_rows
    # Public success payload may include manifest digest; staging start must not.
    start_payload = skill_activation_start_payload(call_id="c1", candidate_count=1)
    assert "active" not in str(start_payload).lower() or start_payload["status"] == "staging"
    success = skill_activation_end_payload(
        status="success",
        activated_version_ids=(),
        manifest_revision=3,
        manifest_digest=DIGEST_B,
    )
    assert success["manifestRevision"] == 3
    failed = skill_activation_end_payload(status="failed", reason_code="x")
    assert "manifestDigest" not in failed

    marked = mark_internal({"foo": 1})
    assert is_internal_event(marked)
    stripped = strip_visibility_marker(marked)
    assert "_visibility" not in stripped
    assert stripped["foo"] == 1


def _provider_ref() -> ProviderRef:
    return create_provider_ref(
        provider_protocol="openai_chat_completions",
        provider_config_id=CRED_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_A,
        adapter_key="openai_chat_completions",
        adapter_revision="1",
        protocol_revision="1",
        app_build_revision="plan04-dev",
    )


def _model_ref(provider: ProviderRef) -> ModelRef:
    return create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CRED_ID,
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_A,
        model_config_digest=DIGEST_B,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_A,
    )


def _snapshot_with_controls() -> MainAgentProfileSnapshotV2:
    base = default_main_agent_profile_snapshot_v2()
    return MainAgentProfileSnapshotV2.model_validate(
        {
            **base.model_dump(by_alias=True, mode="json"),
            "controlCapabilityKeys": list(MAIN_AGENT_CONTROL_KEYS),
        }
    )


@dataclass
class _FakeProvider:
    provider_protocol: str = "openai_chat_completions"
    adapter_key: str = "openai_chat_completions"
    adapter_revision: str = "1"
    model_config_digest: str = DIGEST_B
    request_count: int = 0

    def stream_round(self, request: Any, *, cancellation: Any) -> Iterator[Any]:
        del request, cancellation
        self.request_count += 1
        if False:  # pragma: no cover
            yield None


@dataclass
class _FakeTools:
    def resolve(self, manifest: Any, *, scope: Any, locale: str) -> Any:
        del manifest, scope, locale
        raise AssertionError("tools should not resolve in this unit path")


@dataclass
class _FakeDescriptors:
    def require_current(self, *, binding: Any, exposed_descriptor: Any, scope: Any) -> Any:
        del binding, exposed_descriptor, scope
        raise AssertionError("describe should not run")


@dataclass
class _FakeAuth:
    def issue(self, *, call: Any, binding: Any, descriptor: Any, scope: Any) -> Any:
        del call, binding, descriptor, scope
        raise AssertionError("auth should not issue")


@dataclass
class _FakeDispatcher:
    def dispatch(self, request: Any, *, cancellation: Any) -> Any:
        del request, cancellation
        raise AssertionError("dispatch should not run")


@dataclass
class _FakeLifecycle:
    accepted: list[str] = field(default_factory=list)

    def accept(self, *, call_id: str, current_manifest: Any, proposed_manifest: Any) -> None:
        del current_manifest, proposed_manifest
        self.accepted.append(call_id)

    def discard(self, *, call_id: str, reason_code: str) -> None:
        del call_id, reason_code


@dataclass
class _FakeLoop:
    result: ProviderLoopResult

    def start(self, request: Any, *, ports: Any, finalization_instructions: Any = None) -> ProviderLoopResult:
        del request, ports, finalization_instructions
        return self.result


class _DummyProfile:
    id = PROFILE_ID
    profile_key = "default"
    runtime_enabled = True
    published_version_id = PROFILE_VERSION_ID


class _DummyVersion:
    id = PROFILE_VERSION_ID
    profile_id = PROFILE_ID
    sequence_no = 1
    content_digest = DIGEST_A
    version_source = "publish"
    snapshot: dict = {}


def _admission(*, mode: str = "read_only") -> AdmissionContext:
    provider = _provider_ref()
    model = _model_ref(provider)
    snap = _snapshot_with_controls()
    from app.assistant.main_agent.model_eligibility import ModelEligibilityReport

    frozen = FrozenModelIdentity(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CRED_ID,
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_A,
        model_config_digest=DIGEST_B,
        capability_probe_id=PROBE_ID,
        capability_probe_digest=DIGEST_A,
    )
    return AdmissionContext(
        mode=mode,  # type: ignore[arg-type]
        execution_kind="production",
        profile=_DummyProfile(),  # type: ignore[arg-type]
        profile_version=_DummyVersion(),  # type: ignore[arg-type]
        snapshot=snap,
        main_agent_ref=ResolvedMainAgentRef(
            profile_id=PROFILE_ID,
            version_id=PROFILE_VERSION_ID,
            profile_key="default",
            sequence=1,
            content_digest=DIGEST_A,
        ),
        control_keys=MAIN_AGENT_CONTROL_KEYS,
        frozen_model=frozen,
        provider_ref=provider,
        model_ref=model,
        eligibility=ModelEligibilityReport(
            eligible=True,
            probe_id=PROBE_ID,
            probe_digest=DIGEST_A,
            model_config_digest=DIGEST_B,
            required_capabilities=("streaming",),
        ),
        effective_policy_digest=compute_main_agent_effective_policy_digest(
            profile_content_digest=DIGEST_A
        ),
        probe_diagnostics=None,
    )


def test_build_base_manifest_includes_four_controls() -> None:
    from app.assistant.main_agent.control_capabilities import (
        build_all_main_agent_control_bindings,
    )

    admission = _admission()
    bindings = build_all_main_agent_control_bindings(
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=DIGEST_A,
        app_build_revision="plan04-dev",
    )
    manifest = build_base_manifest_with_controls(
        run_id=RUN_ID,
        main_agent=admission.main_agent_ref,
        provider=admission.provider_ref,
        model=admission.model_ref,
        effective_policy_digest=admission.effective_policy_digest,
        control_bindings=bindings,
    )
    keys = {c.capability_key for c in manifest.capabilities}
    assert set(MAIN_AGENT_CONTROL_KEYS) <= keys
    assert manifest.model is not None
    assert manifest.provider is not None
    assert len(manifest.active_skills) == 0


def test_main_agent_service_happy_path_scripted_loop() -> None:
    events: list[tuple[str, dict]] = []
    adapter = MainAgentEventAdapter(lambda n, p: events.append((n, p)))
    admission = _admission(mode="read_only")
    bindings_manifest = build_base_manifest_with_controls(
        run_id=RUN_ID,
        main_agent=admission.main_agent_ref,
        provider=admission.provider_ref,
        model=admission.model_ref,
        effective_policy_digest=admission.effective_policy_digest,
        control_bindings=__import__(
            "app.assistant.main_agent.control_capabilities", fromlist=["build_all_main_agent_control_bindings"]
        ).build_all_main_agent_control_bindings(
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=DIGEST_A,
            app_build_revision="plan04-dev",
        ),
    )
    loop_result = ProviderLoopResult(
        status="completed",
        final_text="hello from main agent",
        messages=(),
        tool_calls=(),
        round_count=1,
        stop_reason="natural_completion",
        manifest=bindings_manifest,
        continuation=None,
        usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        error=None,
    )
    ports = ProviderLoopPorts(
        provider=_FakeProvider(),
        tools_provider=_FakeTools(),
        current_descriptors=_FakeDescriptors(),
        authorization_evidence=_FakeAuth(),
        tool_dispatcher=_FakeDispatcher(),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=type("C", (), {"is_cancelled": lambda self: False})(),
        events=adapter,
        round_context_provider=NoOpRoundContextProvider(),
        manifest_effect_lifecycle=_FakeLifecycle(),
    )
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=_FakeProvider(),
        ports=ports,
        event_adapter=adapter,
        loop=_FakeLoop(loop_result),  # type: ignore[arg-type]
        app_build_revision="plan04-dev",
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="hi",
            locale="en",
            execution_kind="production",
        )
    )
    assert result.status == "completed"
    assert result.final_text == "hello from main agent"
    assert result.write_l1 is True
    assert result.write_l2 is False
    assert result.write_message is True
    assert any(name == "runtime_selected" for name, _ in events)
    # content_delta is owned by the outer Assistant Run path (single stream);
    # MainAgentService only returns final_text for the outer loop to emit once.
    assert not any(name == "content_delta" for name, _ in events)


def test_owner_mismatch_promotes_stop_reason_and_failed_reason_code() -> None:
    """Pure §5.4 code owner_mismatch must promote onto Run reason_code + stop_reason.

    ProviderLoopResult only allows coarse failed stop reasons (capability_error);
    the stable pure policy code arrives via SafeProviderError.semantic_code and must
    become both MainAgentRunState.stop_reason and AssistantRuntimeResult.reason_code
    instead of collapsing to MAIN_AGENT_FAILED.
    """
    from app.assistant.main_agent.service import MAIN_AGENT_FAILED

    admission = _admission(mode="read_only")
    bindings_manifest = build_base_manifest_with_controls(
        run_id=RUN_ID,
        main_agent=admission.main_agent_ref,
        provider=admission.provider_ref,
        model=admission.model_ref,
        effective_policy_digest=admission.effective_policy_digest,
        control_bindings=__import__(
            "app.assistant.main_agent.control_capabilities",
            fromlist=["build_all_main_agent_control_bindings"],
        ).build_all_main_agent_control_bindings(
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=DIGEST_A,
            app_build_revision="plan04-dev",
        ),
    )
    loop_result = ProviderLoopResult(
        status="failed",
        final_text=None,
        messages=(),
        tool_calls=(),
        round_count=1,
        stop_reason="capability_error",
        manifest=bindings_manifest,
        continuation=None,
        usage=ProviderUsage(input_tokens=1, output_tokens=0, total_tokens=1),
        error=SafeProviderError(
            semantic_code="owner_mismatch",
            safe_summary="policy denied",
            retry_disposition="never",
        ),
    )
    ports = ProviderLoopPorts(
        provider=_FakeProvider(),
        tools_provider=_FakeTools(),
        current_descriptors=_FakeDescriptors(),
        authorization_evidence=_FakeAuth(),
        tool_dispatcher=_FakeDispatcher(),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=type("C", (), {"is_cancelled": lambda self: False})(),
        events=MainAgentEventAdapter(lambda *_a, **_k: None),
        round_context_provider=NoOpRoundContextProvider(),
        manifest_effect_lifecycle=_FakeLifecycle(),
    )
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=_FakeProvider(),
        ports=ports,
        loop=_FakeLoop(loop_result),  # type: ignore[arg-type]
        app_build_revision="plan04-dev",
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="hi",
            locale="en",
            execution_kind="production",
        )
    )
    assert result.status == "failed"
    assert result.reason_code == "owner_mismatch"
    assert result.reason_code != MAIN_AGENT_FAILED
    assert service.state is not None
    assert service.state.stop_reason == "owner_mismatch"


def test_main_agent_service_shadow_evaluation_discards_writes() -> None:
    admission = _admission(mode="shadow")
    # Force evaluation kind so shadow is allowed.
    admission = AdmissionContext(
        **{**admission.__dict__, "execution_kind": "evaluation", "mode": "shadow"}
    )
    bindings_manifest = build_base_manifest_with_controls(
        run_id=RUN_ID,
        main_agent=admission.main_agent_ref,
        provider=admission.provider_ref,
        model=admission.model_ref,
        effective_policy_digest=admission.effective_policy_digest,
        control_bindings=__import__(
            "app.assistant.main_agent.control_capabilities", fromlist=["build_all_main_agent_control_bindings"]
        ).build_all_main_agent_control_bindings(
            owner_version_id=PROFILE_VERSION_ID,
            source_snapshot_digest=DIGEST_A,
            app_build_revision="plan04-dev",
        ),
    )
    loop_result = ProviderLoopResult(
        status="completed",
        final_text="shadow answer",
        messages=(),
        tool_calls=(),
        round_count=1,
        stop_reason="natural_completion",
        manifest=bindings_manifest,
        continuation=None,
        usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        error=None,
    )
    ports = ProviderLoopPorts(
        provider=_FakeProvider(),
        tools_provider=_FakeTools(),
        current_descriptors=_FakeDescriptors(),
        authorization_evidence=_FakeAuth(),
        tool_dispatcher=_FakeDispatcher(),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=type("C", (), {"is_cancelled": lambda self: False})(),
        events=MainAgentEventAdapter(lambda *_a, **_k: None),
        round_context_provider=NoOpRoundContextProvider(),
        manifest_effect_lifecycle=_FakeLifecycle(),
    )
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=_FakeProvider(),
        ports=ports,
        loop=_FakeLoop(loop_result),  # type: ignore[arg-type]
        app_build_revision="plan04-dev",
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="eval prompt",
            locale="en",
            execution_kind="evaluation",
        )
    )
    assert result.status == "completed"
    assert result.final_text == "shadow answer"
    assert result.write_message is False
    assert result.write_l1 is False
    assert result.write_l2 is False
    assert result.write_title is False


def test_main_agent_rejects_noop_lifecycle_and_fails_closed() -> None:
    events: list[tuple[str, dict]] = []
    adapter = MainAgentEventAdapter(lambda n, p: events.append((n, p)))
    admission = _admission(mode="read_only")
    ports = ProviderLoopPorts(
        provider=_FakeProvider(),
        tools_provider=_FakeTools(),
        current_descriptors=_FakeDescriptors(),
        authorization_evidence=_FakeAuth(),
        tool_dispatcher=_FakeDispatcher(),
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=type("C", (), {"is_cancelled": lambda self: False})(),
        events=adapter,
        round_context_provider=NoOpRoundContextProvider(),
        manifest_effect_lifecycle=NoOpManifestEffectLifecyclePort(),
    )
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=_FakeProvider(),
        ports=ports,
        event_adapter=adapter,
        app_build_revision="plan04-dev",
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="hi",
            locale="en",
            execution_kind="production",
        )
    )
    assert result.status == "failed"
    assert result.runtime == "main_agent"
    assert not hasattr(result, "fallback_to_legacy") or getattr(result, "fallback_to_legacy", None) in (None, False)
    assert result.write_message is False


def test_main_agent_service_missing_ports_fails_closed_before_request() -> None:
    admission = _admission(mode="read_only")
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=_FakeProvider(),
        ports=None,
        app_build_revision="plan04-dev",
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="hi",
            locale="en",
            execution_kind="production",
        )
    )
    assert result.status == "failed"
    assert result.runtime == "main_agent"
    assert result.write_l1 is False
    assert result.write_message is False


def test_stream_run_skips_internal_events_but_advances_cursor() -> None:
    """stream_run filters _visibility=internal while advancing seq."""
    from unittest.mock import MagicMock, patch

    from app.assistant.service import AssistantService

    class _Event:
        def __init__(self, seq: int, name: str, payload: dict):
            self.seq = seq
            self.event_name = name
            self.payload = payload

    class _Run:
        def __init__(self):
            self.id = RUN_ID
            self.status = "completed"
            self.last_event_seq = 3
            self.runtime_kind = "main_agent"

    events_seq = [
        _Event(1, "run_status", {"status": "running"}),
        _Event(2, "main_agent_diagnostic", mark_internal({"code": "x"})),
        _Event(3, "message_end", {"finishReason": "stop"}),
    ]

    class _Svc:
        def get_run(self, **_kwargs):
            return _Run()

        def list_events_after(self, **_kwargs):
            after = int(_kwargs.get("after_seq") or 0)
            return [e for e in events_seq if e.seq > after]

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_bind(self):
            return object()

        @property
        def bind(self):
            return object()

    with patch(
        "app.assistant.service.sessionmaker",
        return_value=lambda: _Session(),
    ), patch(
        "app.assistant.service.AssistantChatRunService",
        return_value=_Svc(),
    ):
        service = AssistantService(db=_Session())  # type: ignore[arg-type]
        chunks = list(service.stream_run(CONV_ID, run_id=RUN_ID, after_seq=0))
    decoded = b"".join(chunks).decode("utf-8")
    assert "run_status" in decoded
    assert "message_end" in decoded
    assert "main_agent_diagnostic" not in decoded
    assert "_visibility" not in decoded


def test_off_mode_never_imports_main_agent_service_path_on_construct_check() -> None:
    # Guard: off must not construct MainAgentService in production selection.
    assert should_construct_main_agent(mode="off") is False
    # Admission helper raises if someone forces mode off through admit path.
    # (db-backed admit is covered by mode selection; unit path uses select_runtime)
    runtime, reason = select_runtime_for_mode(mode="off")
    assert runtime is None
    assert reason == MODE_OFF


def _seed_default_published_profile(
    db: Any,
    *,
    snapshot: MainAgentProfileSnapshotV1 | MainAgentProfileSnapshotV2,
    runtime_enabled: bool = True,
) -> tuple[Any, Any]:
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
    )

    profile = AssistantMainAgentProfile(
        profile_key="default",
        display_name="Main Agent",
        is_default=True,
        migration_state="native",
        runtime_enabled=runtime_enabled,
    )
    db.add(profile)
    db.flush()
    payload = snapshot.normalized_payload()
    digest = snapshot.content_digest()
    # publish versions require a non-null source_draft_version_id (shape constraint).
    draft = AssistantMainAgentProfileVersion(
        profile_id=profile.id,
        sequence_no=1,
        version_name="draft-v1",
        version_source="save",
        origin="bootstrap",
        snapshot=payload,
        content_digest=digest,
    )
    db.add(draft)
    db.flush()
    published = AssistantMainAgentProfileVersion(
        profile_id=profile.id,
        sequence_no=2,
        version_name="published-v1",
        version_source="publish",
        origin="bootstrap",
        source_draft_version_id=draft.id,
        snapshot=payload,
        content_digest=digest,
    )
    db.add(published)
    db.flush()
    profile.published_version_id = published.id
    profile.draft_version_id = draft.id
    db.commit()
    db.refresh(profile)
    db.refresh(published)
    return profile, published


def test_load_default_published_profile_accepts_v2() -> None:
    from tests._db import make_session

    db = make_session()
    try:
        snap = _snapshot_with_controls()
        _seed_default_published_profile(db, snapshot=snap)
        profile, version, loaded = load_default_published_profile(db)
        assert profile.is_default is True
        assert version.version_source == "publish"
        assert isinstance(loaded, MainAgentProfileSnapshotV2)
        assert loaded.schema_version == 2
        assert loaded.content_digest() == snap.content_digest()
        assert "fallbackPolicy" not in loaded.normalized_payload()
    finally:
        db.close()


def test_load_default_published_profile_rejects_v1() -> None:
    from tests._db import make_session

    db = make_session()
    try:
        v1 = MainAgentProfileSnapshotV1.model_validate(
            {
                **default_main_agent_profile_snapshot().model_dump(by_alias=True, mode="json"),
                "controlCapabilityKeys": list(MAIN_AGENT_CONTROL_KEYS),
            }
        )
        _seed_default_published_profile(db, snapshot=v1)
        with pytest.raises(MainAgentAdmissionError) as exc:
            load_default_published_profile(db)
        assert exc.value.reason_code == PROFILE_UNAVAILABLE
    finally:
        db.close()
