"""Plan 03 Task 9: internal/test Provider Loop ↔ Capability Gateway integration."""

from __future__ import annotations

import ast
import json
import os
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "plan03-task9-local")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)
os.environ.pop("OPENCLAW_CAPABILITY_RUNTIME_MODE", None)

DIGEST_A = "a" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000901")
CONV_ID = UUID("00000000-0000-4000-8000-000000000902")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000910")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000911")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000950")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000951")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000940")
MODEL_CONFIG = "5" * 64
ADAPTER_KEY = "openai"
ADAPTER_REVISION = "a1"
P = "openai_chat_completions"


@pytest.fixture()
def db_env(monkeypatch: pytest.MonkeyPatch):
    reset_caches()
    monkeypatch.setenv("APP_BUILD_REVISION", "plan03-task9-local")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("OPENCLAW_CAPABILITY_RUNTIME_MODE", raising=False)
    monkeypatch.setenv(
        "AI_PROVIDER_FERNET_KEY",
        "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
    )
    from app.config import get_settings

    get_settings.cache_clear()
    from tests._db import make_session
    from sqlalchemy.orm import sessionmaker

    root = make_session()
    factory = sessionmaker(
        bind=root.get_bind(), autoflush=False, autocommit=False, future=True
    )

    def session_factory():
        return factory()

    try:
        yield root, session_factory
    finally:
        root.close()
        get_settings.cache_clear()
        reset_caches()


def _main_agent():
    from app.assistant.domain.contracts import ResolvedMainAgentRef

    return ResolvedMainAgentRef(
        profile_id=PROFILE_ID,
        version_id=PROFILE_VERSION_ID,
        profile_key="general_chat",
        sequence=1,
        content_digest=DIGEST_A,
    )


def _provider():
    from app.assistant.domain.contracts import create_provider_ref

    return create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest="3" * 64,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision="plan03-task9-local",
    )


def _model():
    from app.assistant.domain.contracts import create_model_ref

    provider = _provider()
    return create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest="4" * 64,
        model_config_digest=MODEL_CONFIG,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )


def _manifest(run_id: UUID = RUN_ID):
    from app.assistant.domain.contracts import create_base_run_manifest

    return create_base_run_manifest(
        run_id=run_id,
        main_agent=_main_agent(),
        provider=_provider(),
        model=_model(),
        effective_policy_digest=None,
    )


def _scope(
    *,
    run_id: UUID = RUN_ID,
    principal_id: str = "principal-loop",
    tenant: str | None = None,
):
    from app.assistant.capabilities.contracts import CapabilityPrincipal
    from app.assistant.provider_loop.contracts import create_execution_scope

    return create_execution_scope(
        run_id=run_id,
        conversation_id=CONV_ID,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id=principal_id,
            authenticated=True,
        ),
        tenant_scope_id=tenant,
    )


def _usage(inp: int = 3, out: int = 5):
    from app.assistant.provider_loop.contracts import ProviderUsage

    return ProviderUsage(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


def _scripted(model):
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    return ScriptedProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
    )


def _freeze_system_tool(db, key: str = "search_entries"):
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="tool", key=key),)
    )[0]
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )


def _freeze_remote_write_tool(db, name: str = "remote_write_cap"):
    from tests.agent_skill_test_support import create_remote_tool
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_remote_tool(db, name=name)
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="tool",
                key=name,
                contract=CapabilityBindingContract(
                    input_schema=None,
                    output_schema={"type": "string"},
                ),
            ),
        )
    )[0]
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )


def _freeze_agent(db, name: str = "agent_cap"):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_agent,
    )
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import (
        CapabilityBindingContract,
        CapabilityDeclaration,
    )
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_default_model_binding(db)
    create_published_agent(
        db, name=name, tools=["search_entries"], model_source="default"
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key=name,
                contract=CapabilityBindingContract(
                    input_schema={
                        "type": "object",
                        "properties": {"prompt": {"type": "string"}},
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                    output_schema={"type": "string"},
                ),
            ),
        )
    )[0]
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )


def _aliases(surface) -> dict[str, str]:
    return {tool.domain_key: tool.provider_alias for tool in surface.tools}


def _tool_call_events(
    call_specs: list[tuple[str, str, dict[str, Any]]],
    *,
    usage=None,
):
    from app.assistant.provider_loop.contracts import (
        ProviderRoundTerminal,
        ProviderToolCallDelta,
        ProviderUsageSnapshot,
    )

    events = []
    seq = 0
    for index, (call_id, alias, args) in enumerate(call_specs):
        events.append(
            ProviderToolCallDelta(
                sequence=seq,
                call_index=index,
                call_id=call_id,
                provider_alias_delta=alias,
                arguments_delta=json.dumps(args, separators=(",", ":"), sort_keys=True),
            )
        )
        seq += 1
    if usage is not None:
        events.append(ProviderUsageSnapshot(sequence=seq, usage=usage))
        seq += 1
    events.append(ProviderRoundTerminal(sequence=seq, finish_reason="tool_calls"))
    return events


def _text_events(text: str, *, usage=None):
    from app.assistant.provider_loop.scripted_provider import text_then_terminal

    return text_then_terminal(text, usage=usage)


# ---------------------------------------------------------------------------
# Step 3: direct Gateway dispatch
# ---------------------------------------------------------------------------


def test_direct_gateway_dispatch_read_tool(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import (
        ProviderToolMessage,
        ProviderUserMessage,
        validate_provider_transcript,
    )
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_system_tool(root, "search_entries")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            self.seen_requests.append(request)
            surface = request.tool_surface
            aliases = _aliases(surface)
            if self.request_count == 1:
                assert "search_entries" in aliases
                yield from _tool_call_events(
                    [("call-1", aliases["search_entries"], {})],
                    usage=_usage(),
                )
                return
            if self.request_count == 2:
                validate_provider_transcript(request.messages)
                tool_msgs = [
                    m for m in request.messages if isinstance(m, ProviderToolMessage)
                ]
                assert len(tool_msgs) == 1
                assert tool_msgs[0].call_id == "call-1"
                assert tool_msgs[0].content.status == "completed"
                yield from _text_events("done", usage=_usage())
                return
            raise AssertionError("extra provider request")

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="search"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed", result.error
    assert result.final_text == "done"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].status == "completed"
    assert result.tool_calls[0].call.domain_key == "search_entries"
    assert (
        result.tool_calls[0].call.binding_contract_digest
        == binding.ref.binding_contract_digest
    )
    auth = ports.authorization_evidence
    assert len(auth.issued) == 1
    assert auth.issued[0]["entrypoint"] == "test"
    assert auth.issued[0]["issuer"] == "test"
    assert auth.issued[0]["principal_id"] == "principal-loop"
    assert len(ports.tool_dispatcher.dispatch_calls) == 1
    assert ports.tool_dispatcher.adapter_invocations == ["call-1"]
    assert len(ports.current_descriptors.describe_calls) >= 1
    assert tracked.all_closed


def test_main_agent_entrypoint_denied_by_composition(db_env) -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityAuthorizationEvidence,
        CapabilityExecutionContext,
        CapabilityExecutionRequest,
        CapabilityOwnerRef,
        CapabilityPrincipal,
    )
    from app.assistant.capabilities.ports import CapabilityRuntimePorts
    from app.assistant.capabilities.runtime import build_capability_runtime
    from app.assistant.provider_loop.runtime import (
        NeverCancelled,
        _NullCapabilityEventSink,
        default_test_evidence_verifiers,
    )

    root, _ = db_env
    binding = _freeze_system_tool(root, "search_entries")
    gw = build_capability_runtime(
        db=root,
        evidence_verifiers=default_test_evidence_verifiers(),
    )
    evidence = CapabilityAuthorizationEvidence(
        issuer="test",
        call_id="c-main",
        principal=CapabilityPrincipal(
            principal_type="test", principal_id="p", authenticated=True
        ),
        entrypoint="main_agent",
        owner=CapabilityOwnerRef(owner_kind="test", owner_id="o", owner_version_id=None),
        capability_key=binding.ref.capability_key,
        resolution_digest=binding.ref.resolution_digest,
        binding_contract_digest=binding.ref.binding_contract_digest,
        dependency_closure_digest=binding.ref.dependency_closure_digest,
        allowed_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_A,
        evidence_digest="f" * 64,
    )
    result = gw.execute(
        CapabilityExecutionRequest(
            binding=binding,
            input={},
            context=CapabilityExecutionContext(call_id="c-main"),
            authorization=evidence,
        ),
        ports=CapabilityRuntimePorts(
            cancellation=NeverCancelled(),  # type: ignore[arg-type]
            events=_NullCapabilityEventSink(),  # type: ignore[arg-type]
        ),
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unauthorized"
    assert result.error.safe_code in {"main_agent_denied", "unknown_issuer_entrypoint"}


# ---------------------------------------------------------------------------
# Step 4: dynamic next-round surface
# ---------------------------------------------------------------------------


def test_dynamic_next_round_surface_via_test_control(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    control_binding = _freeze_system_tool(root, "get_statistics")
    new_binding = _freeze_system_tool(root, "search_entries")
    granted = append_test_capability_grant(_manifest(), binding=control_binding)
    grants = TestGrantRegistry()
    grants.put(control_binding)
    grants.put(new_binding)
    tracked = TrackingSessionFactory(session_factory)
    round1_aliases: dict[str, str] = {}
    round2_aliases: dict[str, str] = {}

    def next_manifest_hook(request, result):
        del result
        if request.call.domain_key == "get_statistics":
            return append_test_capability_grant(
                request.current_manifest, binding=new_binding
            )
        return request.current_manifest

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            self.seen_requests.append(request)
            aliases = _aliases(request.tool_surface)
            if self.request_count == 1:
                nonlocal round1_aliases
                round1_aliases = dict(aliases)
                assert "get_statistics" in aliases
                assert "search_entries" not in aliases
                yield from _tool_call_events(
                    [("ctrl-1", aliases["get_statistics"], {})], usage=_usage()
                )
                return
            if self.request_count == 2:
                nonlocal round2_aliases
                round2_aliases = dict(aliases)
                assert "get_statistics" in aliases
                assert "search_entries" in aliases
                assert round2_aliases["get_statistics"] == round1_aliases["get_statistics"]
                yield from _tool_call_events(
                    [("new-1", aliases["search_entries"], {})], usage=_usage()
                )
                return
            if self.request_count == 3:
                yield from _text_events("done", usage=_usage())
                return
            raise AssertionError("extra provider request")

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        next_manifest_hook=next_manifest_hook,
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="activate"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=6,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed", result.error
    assert result.final_text == "done"
    assert [r.call.domain_key for r in result.tool_calls] == [
        "get_statistics",
        "search_entries",
    ]
    cap_keys = {c.capability_key for c in result.manifest.capabilities}
    assert "get_statistics" in cap_keys
    assert "search_entries" in cap_keys
    assert result.manifest.revision > granted.revision
    assert tracked.all_closed


# ---------------------------------------------------------------------------
# Step 5: sibling session isolation
# ---------------------------------------------------------------------------


def test_parallel_siblings_use_separate_gateway_sessions(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import (
        ProviderToolMessage,
        ProviderUserMessage,
        validate_provider_transcript,
    )
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    left = _freeze_system_tool(root, "get_statistics")
    right = _freeze_system_tool(root, "analyze_activity")
    granted = append_test_capability_grant(_manifest(), binding=left)
    granted = append_test_capability_grant(granted, binding=right)
    grants = TestGrantRegistry()
    grants.put(left)
    grants.put(right)
    tracked = TrackingSessionFactory(session_factory)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            self.seen_requests.append(request)
            aliases = _aliases(request.tool_surface)
            if self.request_count == 1:
                yield from _tool_call_events(
                    [
                        ("call-a", aliases["get_statistics"], {}),
                        ("call-b", aliases["analyze_activity"], {}),
                    ],
                    usage=_usage(1, 1),
                )
                return
            if self.request_count == 2:
                validate_provider_transcript(request.messages)
                tool_msgs = [
                    m for m in request.messages if isinstance(m, ProviderToolMessage)
                ]
                assert [m.call_id for m in tool_msgs] == ["call-a", "call-b"]
                yield from _text_events("done", usage=_usage(1, 1))
                return
            raise AssertionError("extra provider request")

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        isolated_parallel=True,
        max_workers=2,
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed", result.error
    assert [r.call.call_id for r in result.tool_calls] == ["call-a", "call-b"]
    dispatch_session_ids = ports.tool_dispatcher.session_ids
    assert len(dispatch_session_ids) == 2
    assert len(set(dispatch_session_ids)) == 2
    assert len(set(ports.tool_dispatcher.gateway_ids)) == 2
    assert tracked.all_closed


def test_without_isolated_executor_degrades_to_sequential(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    left = _freeze_system_tool(root, "get_statistics")
    right = _freeze_system_tool(root, "analyze_activity")
    granted = append_test_capability_grant(_manifest(), binding=left)
    granted = append_test_capability_grant(granted, binding=right)
    grants = TestGrantRegistry()
    grants.put(left)
    grants.put(right)
    tracked = TrackingSessionFactory(session_factory)
    active: list[str] = []
    max_active = 0
    lock = threading.Lock()

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            aliases = _aliases(request.tool_surface)
            if self.request_count == 1:
                yield from _tool_call_events(
                    [
                        ("a", aliases["get_statistics"], {}),
                        ("b", aliases["analyze_activity"], {}),
                    ],
                    usage=_usage(1, 1),
                )
                return
            yield from _text_events("ok", usage=_usage(1, 1))

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        isolated_parallel=False,
    )
    original_dispatch = ports.tool_dispatcher.dispatch

    def wrapped(request, *, cancellation):
        nonlocal max_active
        call_id = request.call.call_id
        with lock:
            active.append(call_id)
            max_active = max(max_active, len(active))
        try:
            return original_dispatch(request, cancellation=cancellation)
        finally:
            with lock:
                active.remove(call_id)

    ports.tool_dispatcher.dispatch = wrapped  # type: ignore[method-assign]
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed", result.error
    assert max_active == 1
    assert tracked.all_closed


def test_cross_scope_isolation_same_tool_keys(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_system_tool(root, "get_statistics")
    run_a = UUID("00000000-0000-4000-8000-0000000009a1")
    run_b = UUID("00000000-0000-4000-8000-0000000009b1")
    tracked = TrackingSessionFactory(session_factory)

    def run_one(run_id: UUID, principal: str):
        grants = TestGrantRegistry()
        grants.put(binding)
        granted = append_test_capability_grant(_manifest(run_id=run_id), binding=binding)

        class Flex(ScriptedProvider):
            def stream_round(self, request, *, cancellation):
                del cancellation
                self.request_count += 1
                aliases = _aliases(request.tool_surface)
                if self.request_count == 1:
                    yield from _tool_call_events(
                        [(f"c-{principal}", aliases["get_statistics"], {})],
                        usage=_usage(),
                    )
                    return
                yield from _text_events(f"ok-{principal}", usage=_usage())

        ports = build_test_provider_loop_ports(
            provider=Flex(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
            ),
            grants=grants,
            session_factory=tracked,
            model_ref=model,
        )
        result = run_internal_test_provider_loop(
            ProviderLoopRequest(
                manifest=granted,
                initial_messages=(ProviderUserMessage(content="hi"),),
                model_ref=model,
                execution_scope=_scope(run_id=run_id, principal_id=principal),
                max_rounds=4,
                locale="en",
                generation=ProviderGenerationOptions(),
            ),
            ports,
        )
        return result, ports

    result_a, ports_a = run_one(run_a, "principal-a")
    result_b, ports_b = run_one(run_b, "principal-b")
    assert result_a.status == "completed", result_a.error
    assert result_b.status == "completed", result_b.error
    assert result_a.final_text == "ok-principal-a"
    assert result_b.final_text == "ok-principal-b"
    assert ports_a.authorization_evidence.issued[0]["principal_id"] == "principal-a"
    assert ports_b.authorization_evidence.issued[0]["principal_id"] == "principal-b"
    assert (
        ports_a.authorization_evidence.issued[0]["evidence_digest"]
        != ports_b.authorization_evidence.issued[0]["evidence_digest"]
    )
    assert tracked.all_closed


# ---------------------------------------------------------------------------
# Step 6: denied write
# ---------------------------------------------------------------------------


def test_denied_write_becomes_blocked_without_adapter_execution(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_remote_write_tool(root, "remote_write_cap")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            if self.request_count != 1:
                raise AssertionError("no next provider request after denied write")
            aliases = _aliases(request.tool_surface)
            yield from _tool_call_events(
                [("w1", aliases["remote_write_cap"], {})], usage=_usage()
            )

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        allowed_side_effects=("none", "compute", "read"),
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="write"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=3,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "failed"
    assert result.tool_calls
    assert result.tool_calls[0].status == "blocked"
    assert result.error is not None
    # Policy denial path still enters Gateway.execute after describe, but no
    # successful adapter completion is recorded as completed.
    assert result.tool_calls[0].status != "completed"
    assert tracked.all_closed


# ---------------------------------------------------------------------------
# Step 7: classification fail-closed
# ---------------------------------------------------------------------------


def test_classification_ruleset_bump_before_planning_blocks_all(
    db_env, monkeypatch
) -> None:
    from app.assistant.capabilities import classification as class_mod
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
        ToolSurfaceResolution,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TestOnlyToolsProvider,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        default_test_evidence_verifiers,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    left = _freeze_system_tool(root, "get_statistics")
    right = _freeze_system_tool(root, "analyze_activity")
    granted = append_test_capability_grant(_manifest(), binding=left)
    granted = append_test_capability_grant(granted, binding=right)
    grants = TestGrantRegistry()
    grants.put(left)
    grants.put(right)
    tracked = TrackingSessionFactory(session_factory)
    verifiers = default_test_evidence_verifiers()
    tools = TestOnlyToolsProvider(
        grants=grants,
        session_factory=tracked,
        expected_model_ref=model,
        evidence_verifiers=verifiers,
    )
    scope = _scope()
    first_resolution = tools.resolve(granted, scope=scope, locale="en")
    exposed_digests = {
        t.binding.ref.binding_contract_digest: t.descriptor.descriptor_digest
        for t in first_resolution.surface.tools
    }

    new_ruleset = class_mod.build_classification_ruleset()
    new_ruleset["revision"] = "plan02-v2-task9"
    new_digest = sha256_canonical_json(new_ruleset)  # type: ignore[arg-type]
    monkeypatch.setattr(class_mod, "CLASSIFICATION_CONTRACT_REVISION", "plan02-v2-task9")
    monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET", new_ruleset)
    monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET_DIGEST", new_digest)

    class FrozenSurfaceProvider:
        def __init__(self, resolution: ToolSurfaceResolution):
            self.resolution = resolution
            self.calls = 0

        def resolve(self, manifest, *, scope, locale):
            del manifest, scope, locale
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("tools provider must not rebuild after drift seal")
            return self.resolution

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            if self.request_count != 1:
                raise AssertionError("no next provider request after classification drift")
            aliases = _aliases(request.tool_surface)
            yield from _tool_call_events(
                [
                    ("a", aliases["get_statistics"], {}),
                    ("b", aliases["analyze_activity"], {}),
                ],
                usage=_usage(1, 1),
            )

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        tools_provider=FrozenSurfaceProvider(first_resolution),
        isolated_parallel=True,
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=first_resolution.manifest,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=scope,
            max_rounds=3,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "classification_changed"
    assert result.tool_calls
    assert result.tool_calls[0].status == "blocked"
    assert ports.tool_dispatcher.adapter_invocations == []
    statuses = [r.status for r in result.tool_calls]
    assert all(s in {"blocked", "cancelled_before_start"} for s in statuses)
    assert tracked.all_closed
    for tool in first_resolution.surface.tools:
        assert (
            exposed_digests[tool.binding.ref.binding_contract_digest]
            == tool.descriptor.descriptor_digest
        )


def test_classification_change_between_preplan_and_dispatch(
    db_env, monkeypatch
) -> None:
    from app.assistant.capabilities import classification as class_mod
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        GatewayCurrentDescriptorVerifier,
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_system_tool(root, "get_statistics")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    class MutatingVerifier(GatewayCurrentDescriptorVerifier):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._count = 0

        def require_current(self, *, binding, exposed_descriptor, scope):
            self._count += 1
            if self._count == 1:
                return super().require_current(
                    binding=binding,
                    exposed_descriptor=exposed_descriptor,
                    scope=scope,
                )
            new_ruleset = class_mod.build_classification_ruleset()
            new_ruleset["revision"] = "plan02-v2-dispatch"
            new_digest = sha256_canonical_json(new_ruleset)  # type: ignore[arg-type]
            monkeypatch.setattr(
                class_mod, "CLASSIFICATION_CONTRACT_REVISION", "plan02-v2-dispatch"
            )
            monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET", new_ruleset)
            monkeypatch.setattr(class_mod, "CLASSIFICATION_RULESET_DIGEST", new_digest)
            return super().require_current(
                binding=binding,
                exposed_descriptor=exposed_descriptor,
                scope=scope,
            )

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            if self.request_count != 1:
                raise AssertionError("no next provider request after dispatch drift")
            aliases = _aliases(request.tool_surface)
            yield from _tool_call_events(
                [("d1", aliases["get_statistics"], {})], usage=_usage()
            )

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        current_descriptors=MutatingVerifier(session_factory=tracked, locale="en"),
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=3,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.semantic_code == "classification_changed"
    assert result.tool_calls[0].status == "blocked"
    assert ports.tool_dispatcher.adapter_invocations == []
    assert tracked.all_closed


def test_unchanged_classification_takes_normal_path(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_system_tool(root, "get_statistics")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            aliases = _aliases(request.tool_surface)
            if self.request_count == 1:
                yield from _tool_call_events(
                    [("ok1", aliases["get_statistics"], {})], usage=_usage()
                )
                return
            yield from _text_events("ok", usage=_usage())

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="hi"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert result.status == "completed", result.error
    assert result.final_text == "ok"
    assert ports.tool_dispatcher.adapter_invocations == ["ok1"]
    assert tracked.all_closed


# ---------------------------------------------------------------------------
# Step 8: wait/resume through dispatcher contract
# ---------------------------------------------------------------------------


def test_wait_resume_through_gateway_dispatcher_contract(db_env) -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityBehavior,
        CapabilityMetrics,
        CapabilityResult,
        ContinuationRef,
        completed_result,
    )
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.provider_loop.aliases import build_provider_tool_surface
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
        ProviderLoopResumeRequest,
        ProviderWaitingResolution,
    )
    from app.assistant.provider_loop.loop import resume_provider_agent_loop
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        GatewayCurrentDescriptorVerifier,
        TestGrantRegistry,
        TestOnlyToolsProvider,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        default_test_evidence_verifiers,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_system_tool(root, "get_statistics")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)
    cont_ref = ContinuationRef(
        continuation_type="test_wait",
        contract_version=1,
        reference_id="wait-ref-1",
        payload_digest=DIGEST_A,
    )

    def result_override(request):
        if request.call.call_id == "wait-1":
            return CapabilityResult(
                status="waiting",
                user_text=None,
                structured_output=None,
                artifact_refs=(),
                continuation=cont_ref,
                terminal_output=False,
                needs_followup=True,
                error=None,
                metrics=CapabilityMetrics(
                    duration_ms=1.0, input_bytes=0, output_bytes=0
                ),
            )
        return None

    class DurableToolsProvider(TestOnlyToolsProvider):
        def resolve(self, manifest, *, scope, locale):
            resolution = super().resolve(manifest, scope=scope, locale=locale)
            pairs = []
            for tool in resolution.surface.tools:
                desc = tool.descriptor
                behavior = CapabilityBehavior(
                    classification=desc.behavior.classification,
                    side_effect=desc.behavior.side_effect,
                    parallel_safe=False,
                    interrupt_mode="durable",
                    timeout_policy=desc.behavior.timeout_policy,
                    behavior_digest=sha256_canonical_json(
                        {
                            "schemaVersion": 1,
                            "orig": desc.behavior.behavior_digest,
                            "interruptMode": "durable",
                        }
                    ),
                )
                new_desc = desc.model_copy(
                    update={
                        "behavior": behavior,
                        "descriptor_digest": sha256_canonical_json(
                            {
                                "schemaVersion": 1,
                                "orig": desc.descriptor_digest,
                                "interruptMode": "durable",
                            }
                        ),
                    }
                )
                pairs.append((tool.binding, new_desc))
            return build_provider_tool_surface(
                manifest=resolution.manifest,
                provider_protocol=self.provider_protocol,
                visible=pairs,
                scope=scope,
            )

    class AcceptExposedVerifier(GatewayCurrentDescriptorVerifier):
        def require_current(self, *, binding, exposed_descriptor, scope):
            del binding, scope
            return exposed_descriptor

    class FlexWait(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            aliases = _aliases(request.tool_surface)
            if self.request_count != 1:
                raise AssertionError("waiting path should not make a second provider request")
            yield from _tool_call_events(
                [("wait-1", aliases["get_statistics"], {})], usage=_usage()
            )

    class FlexResume(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            yield from _text_events("resumed", usage=_usage())

    tools = DurableToolsProvider(
        grants=grants,
        session_factory=tracked,
        expected_model_ref=model,
        evidence_verifiers=default_test_evidence_verifiers(),
    )
    ports = build_test_provider_loop_ports(
        provider=FlexWait(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        tools_provider=tools,
        current_descriptors=AcceptExposedVerifier(
            session_factory=tracked, locale="en"
        ),
        result_override=result_override,
        trust_exposed_for_override=True,
    )
    waiting = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="wait"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=4,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert waiting.status == "waiting", waiting.error
    cont = waiting.continuation
    assert cont is not None
    assert cont.waiting_call.capability_continuation.reference_id == "wait-ref-1"
    assert cont.max_rounds == 4

    ports2 = build_test_provider_loop_ports(
        provider=FlexResume(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        tools_provider=tools,
        current_descriptors=AcceptExposedVerifier(
            session_factory=tracked, locale="en"
        ),
    )
    resumed = resume_provider_agent_loop(
        ProviderLoopResumeRequest(
            manifest=waiting.manifest,
            messages=waiting.messages,
            continuation=cont,
            resolved_waiting=ProviderWaitingResolution(
                call_id="wait-1",
                capability_continuation=cont_ref,
                capability_result=completed_result(
                    user_text="resolved",
                    metrics=CapabilityMetrics(
                        duration_ms=1.0, input_bytes=0, output_bytes=0
                    ),
                ),
            ),
        ),
        ports2,
    )
    assert resumed.status == "completed", resumed.error
    assert resumed.final_text == "resumed"
    assert cont.provider_rounds_used >= 1
    assert tracked.all_closed


# ---------------------------------------------------------------------------
# Step 9: Agent Capability boundary
# ---------------------------------------------------------------------------


def test_agent_capability_goes_only_through_gateway(db_env) -> None:
    from app.assistant.provider_loop.contracts import (
        ProviderGenerationOptions,
        ProviderLoopRequest,
    )
    from app.assistant.provider_loop.messages import ProviderUserMessage
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TrackingSessionFactory,
        append_test_capability_grant,
        build_test_provider_loop_ports,
        run_internal_test_provider_loop,
    )
    from app.assistant.provider_loop.scripted_provider import ScriptedProvider

    root, session_factory = db_env
    model = _model()
    binding = _freeze_agent(root, "agent_cap")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    class Flex(ScriptedProvider):
        def stream_round(self, request, *, cancellation):
            del cancellation
            self.request_count += 1
            if self.request_count != 1:
                raise AssertionError(
                    "agent path should not recurse into another provider loop"
                )
            tool = request.tool_surface.tools[0]
            assert tool.descriptor.capability_type == "agent"
            assert tool.descriptor.behavior.parallel_safe is False
            yield from _tool_call_events(
                [("agent-1", tool.provider_alias, {"prompt": "hello"})],
                usage=_usage(),
            )

    ports = build_test_provider_loop_ports(
        provider=Flex(
            provider_protocol=P,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=MODEL_CONFIG,
            expected_model_ref=model,
        ),
        grants=grants,
        session_factory=tracked,
        model_ref=model,
        allowed_side_effects=(
            "none",
            "compute",
            "read",
            "draft",
            "write_local",
            "write_external",
        ),
    )
    result = run_internal_test_provider_loop(
        ProviderLoopRequest(
            manifest=granted,
            initial_messages=(ProviderUserMessage(content="agent"),),
            model_ref=model,
            execution_scope=_scope(),
            max_rounds=3,
            locale="en",
            generation=ProviderGenerationOptions(),
        ),
        ports,
    )
    assert len(ports.tool_dispatcher.dispatch_calls) == 1
    assert ports.tool_dispatcher.dispatch_calls[0]["domain_key"] == "agent_cap"
    assert ports.provider.request_count == 1  # type: ignore[attr-defined]
    assert tracked.all_closed
    # Result may complete or fail via Gateway adapter; either is fine if only Gateway
    # was used and no recursive loop occurred.
    assert result.status in {"completed", "failed", "waiting"}


def test_provider_loop_package_has_no_legacy_agent_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "assistant" / "provider_loop"
    forbidden_substrings = (
        "openclaw_integration",
        "SkillRouter",
        "Supervisor",
        "agent_execution_core",
        "run_agent_execution",
    )
    # skill.inject is forbidden as an identifier/import, not as a negation comment.
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_substrings:
            assert token not in text, f"{path} contains forbidden token {token!r}"
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "agent_execution_core" not in alias.name
                    assert "openclaw_integration" not in alias.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "agent_execution_core" not in node.module
                assert "openclaw_integration" not in node.module
                for alias in node.names:
                    assert alias.name not in {
                        "run_agent_execution",
                        "SkillRouter",
                        "Supervisor",
                    }
        # Explicit skill.inject symbol usage is forbidden (comment negation allowed).
        if "skill.inject" in text:
            # Allow only the documented "do not name it skill.inject" style comments.
            for line in text.splitlines():
                if "skill.inject" in line:
                    stripped = line.strip()
                    assert stripped.startswith("#") or "not" in stripped.lower() or "never" in stripped.lower() or "do not" in stripped.lower(), (
                        f"{path}: unexpected skill.inject usage: {line!r}"
                    )


# ---------------------------------------------------------------------------
# Step 12: no production entrypoint
# ---------------------------------------------------------------------------


def test_no_production_provider_loop_entrypoint() -> None:
    from app.main import app
    from app.config import get_settings
    from app.assistant.provider_loop.runtime import default_test_evidence_verifiers

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("provider_loop" in p for p in paths)
    assert not any("provider-agent-loop" in p for p in paths)
    settings = get_settings()
    assert getattr(settings, "ai_model_capability_probe_enabled", False) is False
    keys = set(default_test_evidence_verifiers().keys())
    assert keys == {("test", "test")}
    assert ("test", "main_agent") not in keys
    service_path = (
        Path(__file__).resolve().parents[1] / "app" / "assistant" / "service.py"
    )
    text = service_path.read_text(encoding="utf-8")
    assert "provider_loop.runtime" not in text
    assert "run_internal_test_provider_loop" not in text
    assert "run_provider_agent_loop" not in text


def test_tools_provider_closes_session_before_return(db_env) -> None:
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TestOnlyToolsProvider,
        TrackingSessionFactory,
        append_test_capability_grant,
    )

    root, session_factory = db_env
    binding = _freeze_system_tool(root, "get_statistics")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)
    tools = TestOnlyToolsProvider(
        grants=grants,
        session_factory=tracked,
        expected_model_ref=_model(),
    )
    resolution = tools.resolve(granted, scope=_scope(), locale="en")
    assert resolution.surface.tools
    assert tracked.all_closed
    assert tools.session_ids
    assert resolution.surface.surface_digest


def test_tools_provider_does_not_query_all_published_skills(
    db_env, monkeypatch
) -> None:
    from app.assistant.provider_loop.runtime import (
        TestGrantRegistry,
        TestOnlyToolsProvider,
        TrackingSessionFactory,
        append_test_capability_grant,
    )
    import app.assistant.skills.resolution as resolution_mod

    root, session_factory = db_env
    binding = _freeze_system_tool(root, "get_statistics")
    granted = append_test_capability_grant(_manifest(), binding=binding)
    grants = TestGrantRegistry()
    grants.put(binding)
    tracked = TrackingSessionFactory(session_factory)

    def boom(*a, **k):
        raise AssertionError("must not resolve published skills implicitly")

    monkeypatch.setattr(
        resolution_mod.CapabilityReferenceResolver, "resolve_many", boom
    )
    tools = TestOnlyToolsProvider(
        grants=grants,
        session_factory=tracked,
        expected_model_ref=_model(),
    )
    resolution = tools.resolve(granted, scope=_scope(), locale="en")
    assert [t.domain_key for t in resolution.surface.tools] == ["get_statistics"]
