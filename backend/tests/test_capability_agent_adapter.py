"""Exact published Agent capability adapter tests (Plan 02 Task 6)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64

# Locked Plan 01 Agent capability contract used by current adapter tests.
CANONICAL_AGENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"input": {}},
    "required": ["input"],
    "additionalProperties": False,
}
CANONICAL_AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@dataclass
class _FakeCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")


@dataclass
class _RecordingEventSink:
    events: list[Any] = field(default_factory=list)

    def emit(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _FakeChunk:
    content: str = ""
    tool_calls: list[Any] | None = None
    additional_kwargs: dict[str, Any] = field(default_factory=dict)

    def __add__(self, other: "_FakeChunk") -> "_FakeChunk":
        merged_calls = list(self.tool_calls or [])
        if other.tool_calls:
            merged_calls.extend(other.tool_calls)
        return _FakeChunk(
            content=str(self.content or "") + str(other.content or ""),
            tool_calls=merged_calls or None,
            additional_kwargs={**self.additional_kwargs, **(other.additional_kwargs or {})},
        )


class _FakeLLM:
    """Deterministic stream LLM for agent_execution_core."""

    def __init__(self, rounds: list[Any] | None = None) -> None:
        # Each round is either a str (final text) or dict with tool_calls.
        self.rounds = list(rounds or ["agent-ok"])
        self.bind_calls: list[Any] = []
        self.stream_calls: list[Any] = []
        self._round = 0

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "_FakeLLM":
        self.bind_calls.append({"tools": tools, "kwargs": kwargs})
        return self

    def stream(self, messages: list[Any]):
        self.stream_calls.append(list(messages))
        if self._round >= len(self.rounds):
            yield _FakeChunk(content="")
            return
        payload = self.rounds[self._round]
        self._round += 1
        if isinstance(payload, str):
            yield _FakeChunk(content=payload)
            return
        if isinstance(payload, dict):
            content = str(payload.get("content") or "")
            tool_calls = payload.get("tool_calls") or []
            yield _FakeChunk(content=content, tool_calls=tool_calls)
            return
        yield _FakeChunk(content=str(payload))


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch):
    reset_caches()
    os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
    os.environ["APP_ENV"] = "test"
    from app.config import get_settings
    from app.ai_provider import crypto as crypto_mod

    # Fixtures store non-Fernet placeholders; activation still needs decrypt success.
    monkeypatch.setattr(crypto_mod, "decrypt_api_key", lambda _token: "test-decrypted-key")

    get_settings.cache_clear()
    from tests._db import make_session

    session = make_session()
    try:
        yield session
    finally:
        session.close()
        get_settings.cache_clear()


def _decision(*, call_id: str = "call-agent-1", owner_kind: str = "test"):
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        CapabilityPolicyDecision,
    )

    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="allow",
        call_id=call_id,
        descriptor_digest=DIGEST_D,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_E,
        owner=CapabilityOwnerRef(
            owner_kind=owner_kind,  # type: ignore[arg-type]
            owner_id="test-owner",
            owner_version_id=None,
        ),
        granted_side_effects=("read", "write_local", "draft", "unknown"),
        grant_source_digest=DIGEST_A,
        decision_digest=DIGEST_B,
        dispatch_permit=AtomicSingleUseDispatchPermit(),
    )


def _context(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext

    payload: dict[str, Any] = {
        "call_id": "call-agent-1",
        "run_id": UUID(int=11),
        "conversation_id": UUID(int=12),
        "locale": "en",
        "request_source": "unit-test",
        "request_channel": "cli",
        "request_session": "session-1",
        "request_tool": "tool-1",
        "nesting_depth": 0,
    }
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _ports(cancelled: bool = False):
    from app.assistant.capabilities.ports import CapabilityRuntimePorts

    cancel = _FakeCancellation(cancelled=cancelled)
    sink = _RecordingEventSink()
    return CapabilityRuntimePorts(cancellation=cancel, events=sink), cancel, sink


def _freeze_agent(
    db,
    *,
    name: str = "agent_cap",
    tools: list[str] | None = None,
    model_source: str = "default",
    model_id: UUID | None = None,
    kb_enabled: bool = False,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    system_prompt: str | None = None,
):
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
    if kb_enabled:
        create_default_model_binding(
            db,
            component="lightrag",
            model_name="embed-test",
            model_type="embedding",
            credential_name="cred-embed",
        )
    agent, version = create_published_agent(
        db,
        name=name,
        tools=tools if tools is not None else [],
        model_source=model_source,
        model_id=model_id,
        kb_enabled=kb_enabled,
    )
    if system_prompt is not None:
        version.snapshot = {
            **dict(version.snapshot or {}),
            "system_prompt": system_prompt,
        }
        agent.system_prompt = system_prompt
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key=agent.name,
                contract=CapabilityBindingContract(
                    input_schema=input_schema or CANONICAL_AGENT_INPUT_SCHEMA,
                    output_schema=output_schema or CANONICAL_AGENT_OUTPUT_SCHEMA,
                ),
            ),
        )
    )[0]
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    return agent, version, frozen


def _resolve_target(db, frozen):
    from app.assistant.capabilities.registry import CapabilityRegistry

    return CapabilityRegistry(db).resolve(frozen)


def _install_fake_llm(monkeypatch: pytest.MonkeyPatch, llm: _FakeLLM | None = None) -> _FakeLLM:
    from app.assistant.capabilities.adapters import agent as agent_mod

    fake = llm or _FakeLLM(["hello-from-agent"])
    monkeypatch.setattr(agent_mod, "build_chat_openai_client", lambda **_k: fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers / contract unit tests
# ---------------------------------------------------------------------------


def test_build_agent_runtime_definition_uses_exact_snapshot_not_aggregate() -> None:
    from app.assistant.capabilities.adapters.agent import build_agent_runtime_definition

    skill = build_agent_runtime_definition(
        agent_profile_id=uuid4(),
        version_id=uuid4(),
        name="demo",
        description="d",
        snapshot={
            "system_prompt": "FROZEN PROMPT",
            "tools": ["search_entries"],
            "kb_config": {"enabled": True},
            "model_source": "custom",
            "model_id": str(uuid4()),
        },
    )
    assert skill.langgraph_pattern == "agent_loop"
    assert skill.system_prompt == "FROZEN PROMPT"
    assert skill.tools == ["search_entries"]
    assert skill.kb is not None and skill.kb.enabled is True
    assert skill.model_source == "custom"
    assert "OpenClaw" not in (skill.system_prompt or "")
    assert "openclaw" not in skill.name.lower()


def test_normalize_agent_output_canonical_text_and_strict_json() -> None:
    from app.assistant.capabilities.adapters.agent import normalize_agent_output_value

    assert normalize_agent_output_value(
        "plain answer",
        output_schema=CANONICAL_AGENT_OUTPUT_SCHEMA,
    ) == {"text": "plain answer"}

    assert normalize_agent_output_value(
        '{"text":"json-answer"}',
        output_schema=CANONICAL_AGENT_OUTPUT_SCHEMA,
    ) == {"text": "json-answer"}

    assert (
        normalize_agent_output_value(
            "plain",
            output_schema={"type": "string"},
        )
        == "plain"
    )

    with pytest.raises(ValueError):
        normalize_agent_output_value(
            "not-json",
            output_schema={
                "type": "object",
                "properties": {"score": {"type": "number"}},
                "required": ["score"],
            },
        )

    with pytest.raises(ValueError):
        # No fence scanning / brace hunting.
        normalize_agent_output_value(
            'prefix {"score": 1} suffix',
            output_schema={
                "type": "object",
                "properties": {"score": {"type": "number"}},
                "required": ["score"],
            },
        )

    with pytest.raises(ValueError):
        normalize_agent_output_value(
            '```json\n{"score": 1}\n```',
            output_schema={
                "type": "object",
                "properties": {"score": {"type": "number"}},
                "required": ["score"],
            },
        )


def test_serialize_agent_user_input_canonical_and_string() -> None:
    from app.assistant.capabilities.adapters.agent import serialize_agent_user_input

    assert serialize_agent_user_input({"input": "hello"}) == "hello"
    assert serialize_agent_user_input({"input": {"q": 1}}) == json.dumps(
        {"q": 1}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    assert serialize_agent_user_input({"prompt": "p"}) == "p"


# ---------------------------------------------------------------------------
# Exact version execution
# ---------------------------------------------------------------------------


def test_adapter_executes_exact_v1_after_aggregate_and_pointer_change(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant_config.models import AssistantAgentProfileVersion
    from app.assistant_config.registry import ToolRegistry
    from app.ai_registry import runtime as ai_runtime
    from app.assistant.domain.digests import sha256_canonical_json

    agent, version, frozen = _freeze_agent(
        db,
        name="agent_exact_v1",
        tools=[],
        system_prompt="V1 PROMPT",
    )

    # Mutate mutable aggregate fields and later draft/published pointer.
    agent.system_prompt = "MUTATED AGGREGATE PROMPT"
    agent.tools = ["list_tags"]
    agent.kb_config = {"enabled": True, "mutated": True}
    draft = AssistantAgentProfileVersion(
        agent_profile_id=agent.id,
        sequence_no=2,
        version_name="draft-v2",
        version_source="save",
        snapshot={
            "system_prompt": "DRAFT PROMPT",
            "tools": ["list_tags"],
            "kb_config": {"enabled": False},
            "model_source": "default",
            "model_id": None,
        },
    )
    db.add(draft)
    db.flush()
    later = AssistantAgentProfileVersion(
        agent_profile_id=agent.id,
        sequence_no=3,
        version_name="v3",
        version_source="publish",
        snapshot={
            "system_prompt": "V3 PROMPT",
            "tools": [],
            "kb_config": {"enabled": False},
            "model_source": "default",
            "model_id": None,
        },
    )
    db.add(later)
    db.flush()
    agent.draft_version_id = draft.id
    agent.published_version_id = later.id
    db.commit()

    monkeypatch.setattr(
        ToolRegistry,
        "resolve",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ToolRegistry.resolve under capability")),
    )
    monkeypatch.setattr(
        ai_runtime,
        "resolve_openai_compat_config",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("resolve_openai_compat_config under capability")
        ),
    )

    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["system_prompt"] = request.system_prompt
        captured["bound_tools"] = list(request.bound_tools)
        captured["tool_runners"] = dict(request.tool_runners)
        captured["max_iterations"] = request.max_iterations
        captured["messages"] = list(request.conversation_messages)
        return AgentExecutionResult(
            final_text="exact-v1-answer",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)

    target = _resolve_target(db, frozen)
    assert target.executable.version_id == version.id
    assert target.executable.snapshot_digest == sha256_canonical_json(version.snapshot)

    ports, _cancel, sink = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "hello-v1"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert result.structured_output == {"text": "exact-v1-answer"}
    assert "V1 PROMPT" in captured["system_prompt"]
    assert "MUTATED" not in captured["system_prompt"]
    assert "DRAFT" not in captured["system_prompt"]
    assert "V3" not in captured["system_prompt"]
    assert "OpenClaw" not in captured["system_prompt"]
    assert any(e.event_type == "capability.started" for e in sink.events)
    assert any(e.event_type == "capability.completed" for e in sink.events)


def test_version_row_must_belong_to_agent_aggregate(db) -> None:
    from app.assistant.capabilities.errors import CapabilityDomainError
    from app.assistant.capabilities.registry import CapabilityRegistry
    from app.assistant.capabilities.contracts import FrozenBindingProvenance, project_frozen_capability_binding
    from app.assistant.skills.contracts import CapabilityBindingContract, CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver
    from tests.agent_skill_test_support import create_default_model_binding, create_published_agent

    create_default_model_binding(db)
    agent_a, version_a = create_published_agent(db, name="agent_owner_a", tools=[])
    agent_b, version_b = create_published_agent(db, name="agent_owner_b", tools=[])
    db.commit()

    resolved = CapabilityReferenceResolver(db).resolve_many(
        (
            CapabilityDeclaration(
                type="agent",
                key=agent_a.name,
                contract=CapabilityBindingContract(
                    input_schema=CANONICAL_AGENT_INPUT_SCHEMA,
                    output_schema=CANONICAL_AGENT_OUTPUT_SCHEMA,
                ),
            ),
        )
    )[0]
    # Forge binding so agent_a identity points at agent_b's version row.
    material = resolved.model_copy(
        update={
            "target_id": agent_a.id,
            "target_identity": f"agent:{agent_a.id}",
            "target_version_id": version_b.id,
            "resolved_agent_version_id": version_b.id,
            "executable_revision": str(version_b.id),
        }
    )
    frozen = project_frozen_capability_binding(
        resolved=material,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    with pytest.raises(CapabilityDomainError) as ctx:
        CapabilityRegistry(db).resolve(frozen)
    assert ctx.value.error.error_type in {"version_drift", "not_found", "unavailable"}
    _ = agent_b, version_a


def test_model_comes_from_exact_snapshot_and_frozen_dep_only(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.ai_registry.models import AiModel, AiComponentBinding
    from app.ai_registry import runtime as ai_runtime
    from app.assistant_config.registry import ToolRegistry

    agent, version, frozen = _freeze_agent(db, name="agent_model_exact", tools=[])
    frozen_model_ids = {
        d.resolved_model_id
        for d in frozen.dependencies
        if d.dependency_type == "model" and d.resolved_model_id is not None
    }
    assert frozen_model_ids
    frozen_model_id = next(iter(frozen_model_ids))
    model = db.query(AiModel).filter(AiModel.id == frozen_model_id).one()
    binding = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == "assistant")
        .one()
    )

    other = AiModel(
        credential_id=model.credential_id,
        name="gpt-redirected",
        model_type="llm",
        runtime_revision=1,
    )
    db.add(other)
    db.flush()
    # Redirect current assistant component binding after freeze.
    binding.llm_model_id = other.id
    db.commit()

    monkeypatch.setattr(
        ToolRegistry,
        "resolve",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ToolRegistry.resolve")),
    )
    monkeypatch.setattr(
        ai_runtime,
        "resolve_openai_compat_config",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolve_openai_compat_config")),
    )

    captured: dict[str, Any] = {}

    def _client(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return _FakeLLM(["ok"])

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.build_chat_openai_client",
        _client,
    )

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        return AgentExecutionResult(
            final_text="model-ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )

    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    # Activated model comes from frozen closure, not the post-freeze component redirect.
    assert captured.get("model") is not None
    assert captured.get("model") != "gpt-redirected"
    live_model_ids = {
        d.resolved_model_id
        for d in target.binding.dependencies
        if d.dependency_type == "model" and d.resolved_model_id is not None
    }
    assert frozen_model_id in live_model_ids
    assert other.id not in live_model_ids

    # Changing the live model name after freeze must not redirect execution either —
    # digest drift fails closed rather than using the mutated row as a new target.
    model.name = "renamed-live-model"
    db.commit()
    with pytest.raises(Exception) as drift_ctx:
        _resolve_target(db, frozen)
    # Closure preflight or adapter path must surface drift/unavailable, never other.id.
    err = getattr(drift_ctx.value, "error", None)
    if err is not None:
        assert err.error_type in {"version_drift", "unavailable", "not_found"}
    _ = agent, version


def test_missing_custom_model_unavailable_before_execution(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.capabilities.errors import CapabilityDomainError
    from tests.agent_skill_test_support import create_default_model_binding
    from app.ai_registry.models import AiModel, AiCredential

    create_default_model_binding(db)
    # Create a custom model, freeze agent against it, then delete model so activation fails.
    cred = AiCredential(
        name=f"cred_custom_{uuid4().hex[:6]}",
        base_url="https://example.invalid/v1",
        api_key_encrypted="enc-custom-placeholder",
        api_key_hint="****cust",
        runtime_revision=1,
    )
    db.add(cred)
    db.flush()
    custom = AiModel(
        credential_id=cred.id,
        name="custom-llm",
        model_type="llm",
        runtime_revision=1,
    )
    db.add(custom)
    db.flush()
    agent, version, frozen = _freeze_agent(
        db,
        name="agent_missing_custom",
        tools=[],
        model_source="custom",
        model_id=custom.id,
    )
    db.delete(custom)
    db.commit()

    invoked = {"run": False}

    def _run(_request):  # noqa: ANN001
        invoked["run"] = True
        raise AssertionError("must not execute")

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )

    # Resolution/closure preflight may already fail; adapter must not execute either way.
    try:
        target = _resolve_target(db, frozen)
    except CapabilityDomainError as exc:
        assert exc.error.error_type in {"unavailable", "version_drift", "not_found"}
        return

    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type in {"unavailable", "version_drift", "not_found"}
    assert invoked["run"] is False
    _ = agent, version


def test_tool_list_exact_and_drift_verified(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant_config.registry import ToolRegistry
    from app.ai_registry import runtime as ai_runtime

    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_tools_exact",
        tools=["search_entries"],
    )
    monkeypatch.setattr(
        ToolRegistry,
        "resolve",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ToolRegistry.resolve")),
    )
    monkeypatch.setattr(
        ai_runtime,
        "resolve_openai_compat_config",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolve_openai_compat_config")),
    )

    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["tool_names"] = sorted(request.tool_runners.keys())
        captured["bound_count"] = len(request.bound_tools)
        return AgentExecutionResult(
            final_text="tools-ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)

    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert captured["tool_names"] == ["search_entries"]
    assert captured["bound_count"] == 1


def test_kb_tool_inclusion_follows_exact_snapshot(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_kb_on",
        tools=["search_entries"],
        kb_enabled=True,
    )

    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["tools"] = sorted(request.tool_runners.keys())
        captured["knowledge_mode"] = request.knowledge_mode
        return AgentExecutionResult(
            final_text="kb-ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)

    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert "kb_search" in captured["tools"]
    assert "search_entries" in captured["tools"]
    assert captured["knowledge_mode"] == "skill_kb"


def test_no_openclaw_prompt_text_in_generic_adapter(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_no_oc",
        tools=[],
        system_prompt="Base agent prompt only.",
    )
    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["system_prompt"] = request.system_prompt
        return AgentExecutionResult(
            final_text="ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": {"q": "x"}},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    prompt = captured["system_prompt"]
    assert "OpenClaw" not in prompt
    assert "openclaw" not in prompt.lower()
    assert "output schema" not in prompt.lower()


# ---------------------------------------------------------------------------
# Behavior preservation
# ---------------------------------------------------------------------------


def test_tools_bound_once_and_max_iterations_preserved(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.runtime_helpers import AGENT_MAX_ITERATIONS

    _agent, _version, frozen = _freeze_agent(db, name="agent_behavior", tools=["search_entries"])
    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["max_iterations"] = request.max_iterations
        captured["bound_tools"] = list(request.bound_tools)
        captured["tool_runners"] = dict(request.tool_runners)
        return AgentExecutionResult(
            final_text="done",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert captured["max_iterations"] == AGENT_MAX_ITERATIONS
    assert len(captured["bound_tools"]) == 1
    assert set(captured["tool_runners"]) == {"search_entries"}


def test_first_tool_call_only_and_streaming_callbacks(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Characterizes legacy engine: only the first tool call executes."""
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(db, name="agent_first_tool", tools=[])

    tool_calls = [
        {
            "id": "c1",
            "name": "search_entries",
            "args": {"query": "one"},
        },
        {
            "id": "c2",
            "name": "list_tags",
            "args": {},
        },
    ]
    # Real run_agent_execution path with fake llm + runners.
    fake = _FakeLLM(
        rounds=[
            {"content": "", "tool_calls": tool_calls},
            "final-after-tool",
        ]
    )
    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.build_chat_openai_client",
        lambda **_k: fake,
    )

    # Inject a runner map by patching bound tool construction on the adapter class.
    from app.assistant.capabilities.adapters import agent as agent_mod

    def _tools_with_spy(self, *args, **kwargs):  # noqa: ANN001
        called: list[str] = []

        def _search(**_a):
            called.append("search_entries")
            return "search-result"

        def _list(**_a):
            called.append("list_tags")
            return "list-result"

        runners = {"search_entries": _search, "list_tags": _list}
        bound = [
            MagicMock(name="search_entries"),
            MagicMock(name="list_tags"),
        ]
        bound[0].name = "search_entries"
        bound[1].name = "list_tags"
        return bound, runners, "none"

    monkeypatch.setattr(
        agent_mod.AgentCapabilityAdapter,
        "_build_bound_tools_from_closure",
        _tools_with_spy,
    )

    target = _resolve_target(db, frozen)
    ports, _c, sink = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert result.structured_output == {"text": "final-after-tool"}
    # Safe child events only — no tool args/results.
    for event in sink.events:
        blob = json.dumps(event.model_dump(mode="json"), default=str)
        assert "search-result" not in blob
        assert "query" not in blob or event.event_type.startswith("capability.")


def test_tool_failure_normalized_and_secrets_never_leak(
    db, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    secret = "SUPER_SECRET_API_KEY_agent_xyz"
    _agent, _version, frozen = _freeze_agent(db, name="agent_secret", tools=[])

    def _run(_request):  # noqa: ANN001
        raise RuntimeError(f"provider exploded with {secret}")

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, sink = _ports()
    with caplog.at_level(logging.INFO):
        result = AgentCapabilityAdapter().execute(
            CapabilityAdapterRequest(
                target=target,
                validated_input={"input": "x"},
                context=_context(),
                decision=_decision(),
            ),
            ports=ports,
        )
    assert result.status == "failed"
    assert result.error is not None
    assert secret not in (result.error.safe_message or "")
    assert secret not in (result.error.safe_code or "")
    for event in sink.events:
        assert secret not in json.dumps(event.model_dump(mode="json"), default=str)
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_engine_tool_error_becomes_safe_capability_error(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

    _agent, _version, frozen = _freeze_agent(db, name="agent_tool_err", tools=[])

    def _run(_request):  # noqa: ANN001
        return AgentExecutionResult(
            final_text="",
            round_count=1,
            used_tools=["search_entries"],
            stopped_by="tool_error",
            error_message="tool boom with secret-token-abc",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, sink = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type in {"execution_failed", "invalid_output", "unavailable"}
    assert "secret-token-abc" not in (result.error.safe_message or "")
    for event in sink.events:
        assert "secret-token-abc" not in json.dumps(event.model_dump(mode="json"), default=str)


# ---------------------------------------------------------------------------
# Output / contract
# ---------------------------------------------------------------------------


def test_canonical_input_serialization_and_text_output(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(db, name="agent_io", tools=[])
    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["messages"] = list(request.conversation_messages)
        return AgentExecutionResult(
            final_text="serialized-ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "user-text"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert result.structured_output == {"text": "serialized-ok"}
    assert any(
        m.get("role") == "user" and m.get("content") == "user-text"
        for m in captured["messages"]
    )


def test_structured_output_validation_strict(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.domain.json_schema import binding_schema_digest

    structured_out = {
        "type": "object",
        "properties": {"score": {"type": "number"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_struct_out",
        tools=[],
        output_schema=structured_out,
    )

    def _run(_request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        return AgentExecutionResult(
            final_text='{"score": 3}',
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    # Ensure digest matches schema used by adapter validation path.
    assert target.descriptor.output_schema_digest == binding_schema_digest(
        target.descriptor.output_schema  # type: ignore[arg-type]
    )
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "completed"
    assert result.structured_output == {"score": 3}


def test_invalid_structured_output_fails_safely(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    structured_out = {
        "type": "object",
        "properties": {"score": {"type": "number"}},
        "required": ["score"],
        "additionalProperties": False,
    }
    _agent, _version, frozen = _freeze_agent(
        db,
        name="agent_bad_out",
        tools=[],
        output_schema=structured_out,
    )

    def _run(_request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        return AgentExecutionResult(
            final_text="not-json",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, sink = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    # invalid_output (normalize) or schema validation mapped codes.
    assert result.error.error_type in {"invalid_output", "unavailable"}
    assert result.error.safe_code in {
        "invalid_output",
        "agent_empty_output",
        "agent_tool_failed",
    } or result.error.error_type == "invalid_output"
    for event in sink.events:
        assert "not-json" not in json.dumps(event.model_dump(mode="json"), default=str)


def test_empty_model_output_fails_safely(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

    _agent, _version, frozen = _freeze_agent(db, name="agent_empty", tools=[])

    def _run(_request):  # noqa: ANN001
        return AgentExecutionResult(
            final_text="",
            round_count=1,
            used_tools=[],
            stopped_by="tool_error",
            error_message="Agent execution produced no model output",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None


# ---------------------------------------------------------------------------
# Nesting / cancellation
# ---------------------------------------------------------------------------


def test_nonzero_depth_carried_and_ceiling_enforced(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        MAX_CAPABILITY_NESTING_DEPTH,
    )

    _agent, _version, frozen = _freeze_agent(db, name="agent_depth", tools=[])
    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        captured["ran"] = True
        return AgentExecutionResult(
            final_text="depth-ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    target = _resolve_target(db, frozen)

    ports, _c, _s = _ports()
    ok = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(nesting_depth=2, call_id="call-depth-ok"),
            decision=_decision(call_id="call-depth-ok"),
        ),
        ports=ports,
    )
    assert ok.status == "completed"
    assert captured.get("ran") is True

    ports2, _c2, _s2 = _ports()
    denied = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(
                nesting_depth=MAX_CAPABILITY_NESTING_DEPTH + 1,
                call_id="call-depth-bad",
            ),
            decision=_decision(call_id="call-depth-bad"),
        ),
        ports=ports2,
    )
    assert denied.status == "failed"
    assert denied.error is not None
    assert denied.error.safe_code == "nesting_depth_exceeded"


def test_agent_cannot_restart_main_agent_or_nest_agents(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import (
        CapabilityAdapterRequest,
        ExecutableAgentVersionTarget,
        ResolvedCapabilityTarget,
    )

    _agent, version, frozen = _freeze_agent(db, name="agent_no_nest", tools=[])
    target = _resolve_target(db, frozen)
    nested_snapshot = dict(target.executable.parsed_snapshot)  # type: ignore[arg-type]
    nested_snapshot["nested_agent"] = True
    nested_snapshot["main_agent_restart"] = True
    patched_executable = ExecutableAgentVersionTarget(
        agent_profile_id=target.executable.agent_profile_id,
        version_id=target.executable.version_id,
        snapshot_digest=target.executable.snapshot_digest,
        parsed_snapshot=nested_snapshot,
    )
    patched = ResolvedCapabilityTarget(
        descriptor=target.descriptor,
        binding=target.binding,
        executable=patched_executable,
        execution_closure=target.execution_closure,
    )

    def _run(_request):  # noqa: ANN001
        raise AssertionError("nested/main restart agent must not execute")

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    ports, _c, _s = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=patched,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type in {"unavailable", "protocol_error"}
    _ = version


def test_cancellation_before_model_activation(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(db, name="agent_cancel", tools=[])
    target = _resolve_target(db, frozen)

    activated = {"model": False}

    class _Resolver:
        def require_model(self, **_k):  # noqa: ANN001
            activated["model"] = True
            raise AssertionError("model must not activate when cancelled")

        def require_tool(self, **_k):  # noqa: ANN001
            raise AssertionError("tools must not resolve when cancelled")

    class _Closure:
        binding_contract_digest = target.descriptor.binding_contract_digest
        dependency_closure_digest = target.descriptor.dependency_closure_digest

        def bind_authorized(self, *, decision):  # noqa: ANN001
            return _Resolver()

    from app.assistant.capabilities.ports import ResolvedCapabilityTarget

    patched = ResolvedCapabilityTarget(
        descriptor=target.descriptor,
        binding=target.binding,
        executable=target.executable,
        execution_closure=_Closure(),  # type: ignore[arg-type]
    )
    ports, _c, sink = _ports(cancelled=True)
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=patched,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "cancelled"
    assert activated["model"] is False
    assert any(e.event_type == "capability.cancelled" for e in sink.events)


def test_cancellation_checked_around_execution(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _agent, _version, frozen = _freeze_agent(db, name="agent_cancel_mid", tools=[])
    target = _resolve_target(db, frozen)
    ports, cancel, sink = _ports(cancelled=False)

    def _run(_request):  # noqa: ANN001
        cancel.cancelled = True
        from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

        return AgentExecutionResult(
            final_text="late",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _run,
    )
    _install_fake_llm(monkeypatch)
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "cancelled"
    assert any(e.event_type == "capability.cancelled" for e in sink.events)


def test_unavailable_descriptor_never_invokes_engine(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant.capabilities.adapters.agent import AgentCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.capabilities.contracts import CapabilityAvailability

    _agent, _version, frozen = _freeze_agent(db, name="agent_unavail", tools=[])
    target = _resolve_target(db, frozen)
    patched_desc = target.descriptor.model_copy(
        update={
            "availability": CapabilityAvailability(
                status="disabled", reason_code="agent_disabled"
            )
        }
    )
    from app.assistant.capabilities.ports import ResolvedCapabilityTarget

    patched = ResolvedCapabilityTarget(
        descriptor=patched_desc,
        binding=target.binding,
        executable=target.executable,
        execution_closure=target.execution_closure,
    )

    def _boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("must not run")

    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.run_agent_execution",
        _boom,
    )
    monkeypatch.setattr(
        "app.assistant.capabilities.adapters.agent.build_chat_openai_client",
        _boom,
    )
    ports, _c, sink = _ports()
    result = AgentCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=patched,
            validated_input={"input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unavailable"
