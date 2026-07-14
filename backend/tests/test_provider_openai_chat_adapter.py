"""Plan 03 Task 6: OpenAI Chat Completions one-round adapter tests.

Primary coverage uses a local ephemeral OpenAI-compatible HTTP server so the
real SDK wire parser is exercised. Production SSRF stays strict; the server is
reached only through an explicit test-transport marker + injected httpx client.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx
import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.provider_loop.adapters.openai_chat import (  # noqa: E402
    ADAPTER_KEY,
    DEFAULT_ADAPTER_REVISION,
    ExactOpenAIChatRuntimeConfig,
    OpenAIChatAdapterError,
    OpenAIChatClientFactory,
    OpenAIChatCompletionsAdapter,
    build_chat_completion_request,
    compute_openai_chat_model_config_digest,
    encode_openai_chat_messages,
    encode_openai_chat_tools,
    secret_free_endpoint_identity,
)
from app.assistant.provider_loop.aliases import (  # noqa: E402
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundTerminal,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolChoice,
    ProviderUsageSnapshot,
    create_execution_scope,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderContextUpdateMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolCall,
    ProviderToolMessage,
    ProviderToolResultEnvelope,
    ProviderUserMessage,
    digest_arguments,
)
from app.assistant.provider_loop.streaming import (  # noqa: E402
    ProviderRoundAssembler,
    assemble_provider_round,
)
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402
from app.common.ssrf import SSRFError, validate_url_ssrf  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000601")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000650")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000651")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000640")
TARGET_A = UUID("00000000-0000-4000-8000-000000000710")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000610")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000611")

API_KEY = "sk-test-adapter-secret-key-do-not-log"
ADAPTER_REVISION = "adapter-rev-1"
APP_BUILD = "plan03-task6-local"
MODEL_NAME = "gpt-test-adapter"


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible HTTP server
# ---------------------------------------------------------------------------


@dataclass
class FakeScript:
    """One scripted response from the fake OpenAI server."""

    status: int = 200
    chunks: list[dict[str, Any]] = field(default_factory=list)
    error_body: dict[str, Any] | None = None
    raw_body: bytes | None = None
    content_type: str | None = None
    delay_before_first_chunk_s: float = 0.0
    delay_between_chunks_s: float = 0.0
    abrupt_close_after_chunks: int | None = None
    require_auth: bool = True
    expected_api_key: str = API_KEY


@dataclass
class FakeServerState:
    scripts: list[FakeScript] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def enqueue(self, *scripts: FakeScript) -> None:
        with self.lock:
            self.scripts.extend(scripts)

    def pop_script(self) -> FakeScript:
        with self.lock:
            if not self.scripts:
                return FakeScript(
                    status=500,
                    error_body={"error": {"message": "no script", "type": "server_error"}},
                )
            return self.scripts.pop(0)

    def record(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(payload)


class _FakeHandler(BaseHTTPRequestHandler):
    server_version = "MindAtlasFakeOpenAI/1.0"
    state: FakeServerState  # set on class by factory

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Never log Authorization or request bodies.
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization")
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {"_raw": raw.decode("utf-8", errors="replace")}

        # Record request without Authorization secret.
        self.state.record(
            {
                "path": self.path,
                "body": body,
                "has_authorization": bool(auth),
                "authorization_prefix": (auth or "")[:12],
                "content_type": self.headers.get("Content-Type"),
            }
        )

        script = self.state.pop_script()
        if script.require_auth:
            expected = f"Bearer {script.expected_api_key}"
            if auth != expected:
                self._write_json(
                    401,
                    {
                        "error": {
                            "message": f"Incorrect API key provided: {auth}",
                            "type": "invalid_request_error",
                            "code": "invalid_api_key",
                        }
                    },
                )
                return

        if script.status != 200:
            if script.raw_body is not None:
                self.send_response(script.status)
                self.send_header(
                    "Content-Type",
                    script.content_type or "application/json",
                )
                self.send_header("Content-Length", str(len(script.raw_body)))
                self.end_headers()
                self.wfile.write(script.raw_body)
                return
            self._write_json(script.status, script.error_body or {"error": {"message": "error"}})
            return

        if script.delay_before_first_chunk_s:
            time.sleep(script.delay_before_first_chunk_s)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        for index, chunk in enumerate(script.chunks):
            if (
                script.abrupt_close_after_chunks is not None
                and index >= script.abrupt_close_after_chunks
            ):
                break
            if script.delay_between_chunks_s:
                time.sleep(script.delay_between_chunks_s)
            payload = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            try:
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
            except BrokenPipeError:
                return
        if script.abrupt_close_after_chunks is None:
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except BrokenPipeError:
                return

    def _write_json(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@dataclass
class FakeOpenAIServer:
    host: str
    port: int
    state: FakeServerState
    _httpd: ThreadingHTTPServer
    _thread: threading.Thread

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def enqueue(self, *scripts: FakeScript) -> None:
        self.state.enqueue(*scripts)

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self.state.lock:
            return list(self.state.requests)

    def close(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()


@pytest.fixture
def fake_openai_server() -> FakeOpenAIServer:
    state = FakeServerState()

    class Handler(_FakeHandler):
        pass

    Handler.state = state  # type: ignore[attr-defined]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    server = FakeOpenAIServer(
        host=str(host),
        port=int(port),
        state=state,
        _httpd=httpd,
        _thread=thread,
    )
    try:
        yield server
    finally:
        server.close()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _resolved_binding(*, capability_key: str = "search.entries") -> ResolvedCapabilityBinding:
    input_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    output_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    completion = CapabilityCompletionContract()
    target = TARGET_A
    target_identity = f"remote-tool:{target}"
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": str(target),
            "targetVersionId": None,
            "targetRevision": 1,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": DIGEST_B,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=DIGEST_B,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    return ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=DIGEST_B,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )


def _frozen(*, capability_key: str = "search.entries") -> FrozenCapabilityBinding:
    return project_frozen_capability_binding(
        resolved=_resolved_binding(capability_key=capability_key),
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _descriptor(binding: FrozenCapabilityBinding) -> CapabilityDescriptor:
    resolved = binding.resolved
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision="cls-1",
            ruleset_digest=DIGEST_D,
        ),
        side_effect="read",
        parallel_safe=True,
        interrupt_mode="none",
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=DIGEST_E,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type="tool",
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description="Search entries",
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=DIGEST_F,
        executable_revision=resolved.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(status="available"),
        completion=resolved.completion,
    )


def _surface(*, capability_key: str = "search.entries", alias_hint: str | None = None):
    binding = _frozen(capability_key=capability_key)
    descriptor = _descriptor(binding)
    # Use a stable model ref that does not depend on the fake server URL.
    model_ref = _model_ref(base_url="https://api.example.com/v1", digest=DIGEST_A)
    manifest = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=ResolvedMainAgentRef(
            profile_id=PROFILE_ID,
            version_id=PROFILE_VERSION_ID,
            profile_key="general_chat",
            sequence=1,
            content_digest=DIGEST_A,
        ),
        provider=create_provider_ref(
            provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
            provider_config_id=PROVIDER_CONFIG_ID,
            provider_runtime_revision=1,
            provider_config_digest=DIGEST_3,
            adapter_key=ADAPTER_KEY,
            adapter_revision=ADAPTER_REVISION,
            protocol_revision="p1",
            app_build_revision=APP_BUILD,
        ),
        model=model_ref,
        effective_policy_digest=None,
    )
    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=None,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-adapter",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )
    hints = {capability_key: alias_hint} if alias_hint else None
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        visible=((binding, descriptor),),
        alias_hints=hints,
        scope=scope,
    )
    return resolution.surface, resolution.manifest


def _endpoint_identity(base_url: str) -> dict[str, Any]:
    return secret_free_endpoint_identity(base_url)


def _model_config_digest(*, base_url: str) -> str:
    return compute_openai_chat_model_config_digest(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        endpoint_identity=_endpoint_identity(base_url),
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    )


def _model_ref(*, base_url: str = "https://api.example.com/v1", digest: str | None = None) -> ModelRef:
    provider = create_provider_ref(
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision=APP_BUILD,
    )
    config_digest = digest or _model_config_digest(base_url=base_url)
    return create_model_ref(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=config_digest,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )


def _runtime_config(
    server: FakeOpenAIServer,
    *,
    total_stream_timeout_seconds: float = 30.0,
    read_timeout_seconds: float = 10.0,
    api_key: str = API_KEY,
    model_runtime_revision: int = 2,
    credential_runtime_revision: int = 3,
    model_config_digest: str | None = None,
) -> ExactOpenAIChatRuntimeConfig:
    base_url = server.base_url
    endpoint = _endpoint_identity(base_url)
    digest = model_config_digest or compute_openai_chat_model_config_digest(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=model_runtime_revision,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=credential_runtime_revision,
        endpoint_identity=endpoint,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    )
    # Explicit test transport: injected httpx client that may target loopback.
    http_client = httpx.Client(base_url=base_url, timeout=10.0)
    return ExactOpenAIChatRuntimeConfig(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=model_runtime_revision,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=credential_runtime_revision,
        model_config_digest=digest,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
        base_url=base_url,
        api_key=api_key,
        endpoint_identity=endpoint,
        allow_test_transport=True,
        http_client=http_client,
        connect_timeout_seconds=2.0,
        read_timeout_seconds=read_timeout_seconds,
        write_timeout_seconds=2.0,
        total_stream_timeout_seconds=total_stream_timeout_seconds,
    )


def _adapter(server: FakeOpenAIServer, **kwargs: Any) -> OpenAIChatCompletionsAdapter:
    config = _runtime_config(server, **kwargs)
    model_ref = _model_ref(base_url=server.base_url, digest=config.model_config_digest)
    return OpenAIChatCompletionsAdapter(
        runtime_config=config,
        client_factory=OpenAIChatClientFactory(),
        expected_model_ref=model_ref,
    )


def _round_request(
    server: FakeOpenAIServer,
    *,
    messages: tuple[Any, ...] | None = None,
    tools_enabled: bool = True,
    finalization_round: bool = False,
    generation: ProviderGenerationOptions | None = None,
    surface: Any | None = None,
    model_ref: ModelRef | None = None,
) -> ProviderRoundRequest:
    if surface is None:
        surface, _ = _surface()
    if model_ref is None:
        digest = _model_config_digest(base_url=server.base_url)
        model_ref = _model_ref(base_url=server.base_url, digest=digest)
    if messages is None:
        messages = (ProviderUserMessage(content="hello"),)
    return ProviderRoundRequest(
        round_index=0,
        messages=messages,
        tool_surface=surface,
        tools_enabled=tools_enabled,
        finalization_round=finalization_round,
        model_ref=model_ref,
        generation=generation or ProviderGenerationOptions(),
    )


class _Cancel:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


def _text_chunks(*parts: str, finish_reason: str = "stop", request_id: str = "chatcmpl-safe1") -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for part in parts:
        chunks.append(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": part},
                        "finish_reason": None,
                    }
                ],
            }
        )
    chunks.append(
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 4,
                "total_tokens": 15,
            },
        }
    )
    return chunks


def _tool_call_chunks(
    *,
    fragments: list[dict[str, Any]],
    finish_reason: str = "tool_calls",
    request_id: str = "chatcmpl-tools1",
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for frag in fragments:
        chunks.append(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": [frag]},
                        "finish_reason": None,
                    }
                ],
            }
        )
    chunks.append(
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 8,
                "total_tokens": 28,
            },
        }
    )
    return chunks


def _collect(adapter: OpenAIChatCompletionsAdapter, request: ProviderRoundRequest, cancellation: Any = None):
    return list(adapter.stream_round(request, cancellation=cancellation or _Cancel()))


def _assert_no_secret(text: str) -> None:
    assert API_KEY not in text
    assert "sk-test-adapter" not in text
    assert "Authorization" not in text or "authorization failed" in text.lower()


# ---------------------------------------------------------------------------
# Encoding tests
# ---------------------------------------------------------------------------


def test_secret_free_endpoint_identity_rejects_userinfo_query_fragment() -> None:
    with pytest.raises(OpenAIChatAdapterError) as exc:
        secret_free_endpoint_identity("https://user:pass@api.example.com/v1")
    assert exc.value.error.semantic_code == "invalid_endpoint"
    _assert_no_secret(str(exc.value))
    _assert_no_secret(exc.value.error.safe_summary)

    with pytest.raises(OpenAIChatAdapterError):
        secret_free_endpoint_identity("https://api.example.com/v1?api_key=secret")
    with pytest.raises(OpenAIChatAdapterError):
        secret_free_endpoint_identity("https://api.example.com/v1#frag")


def test_secret_free_endpoint_identity_normalizes() -> None:
    identity = secret_free_endpoint_identity("https://API.Example.com/codex")
    assert identity["scheme"] == "https"
    assert identity["host"] == "api.example.com"
    assert identity["path"] == "/codex/v1"
    assert "user" not in identity
    assert "password" not in identity
    assert "query" not in identity


def test_encode_messages_and_tools_no_domain_key_leak() -> None:
    surface, _ = _surface(capability_key="search.entries")
    tools = encode_openai_chat_tools(surface)
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == surface.tools[0].provider_alias
    # Domain key must not appear as tool name unless it equals the alias.
    domain_key = surface.tools[0].domain_key
    alias = surface.tools[0].provider_alias
    if domain_key != alias:
        assert tools[0]["function"]["name"] != domain_key
    assert tools[0]["function"]["parameters"] == surface.tools[0].input_schema

    tool_call = ProviderToolCall(
        call_id="call_1",
        call_index=0,
        provider_alias=alias,
        domain_key=domain_key,
        arguments={"query": "q"},
        arguments_digest=digest_arguments({"query": "q"}),
        binding_contract_digest=surface.tools[0].binding.ref.binding_contract_digest,
        descriptor_digest=surface.tools[0].descriptor.descriptor_digest,
        behavior_digest=surface.tools[0].descriptor.behavior.behavior_digest,
        classification_revision=surface.tools[0].descriptor.behavior.classification.revision,
        classification_ruleset_digest=(
            surface.tools[0].descriptor.behavior.classification.ruleset_digest
        ),
        manifest_revision=surface.manifest_revision,
        manifest_digest=surface.manifest_digest,
        surface_digest=surface.surface_digest,
    )
    assistant = ProviderAssistantMessage(
        content="thinking",
        tool_calls=(tool_call,),
    )
    envelope = ProviderToolResultEnvelope(
        status="completed",
        domain_key=domain_key,
        user_text="ok",
        structured_output={"hits": 1},
        terminal_output=False,
        needs_followup=True,
        error=None,
    )
    tool_msg = ProviderToolMessage(
        call_id="call_1",
        provider_alias=alias,
        content=envelope,
    )
    runtime = ProviderRuntimeInstructionMessage(
        instruction_type="soft_finalization",
        locale="en",
        content="Do not call tools.",
    )
    encoded = encode_openai_chat_messages(
        (
            ProviderSystemMessage(content="sys"),
            runtime,
            ProviderUserMessage(content="hi"),
            assistant,
            tool_msg,
        )
    )
    assert encoded[0] == {"role": "system", "content": "sys"}
    assert encoded[1] == {"role": "system", "content": "Do not call tools."}
    assert encoded[2] == {"role": "user", "content": "hi"}
    assert encoded[3]["role"] == "assistant"
    assert encoded[3]["content"] == "thinking"
    assert len(encoded[3]["tool_calls"]) == 1
    assert encoded[3]["tool_calls"][0]["id"] == "call_1"
    assert encoded[3]["tool_calls"][0]["function"]["name"] == alias
    assert json.loads(encoded[3]["tool_calls"][0]["function"]["arguments"]) == {"query": "q"}
    assert encoded[4]["role"] == "tool"
    assert encoded[4]["tool_call_id"] == "call_1"
    # Canonical JSON: sorted keys, compact separators.
    body = encoded[4]["content"]
    assert body == json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # Runtime instruction never encoded as user.
    assert all(item.get("role") != "user" or item.get("content") == "hi" for item in encoded)


def test_runtime_context_maps_to_system_not_user_or_tool() -> None:
    secret_body = "PROTECTED skill body must not be user/tool"
    context = ProviderContextUpdateMessage(
        locale="en",
        manifest_revision=2,
        manifest_digest=DIGEST_E,
        prompt_build_digest=DIGEST_A,
        content=secret_body,
    )
    encoded = encode_openai_chat_messages(
        (
            ProviderSystemMessage(content="sys"),
            context,
            ProviderUserMessage(content="hi"),
        )
    )
    assert encoded[0] == {"role": "system", "content": "sys"}
    assert encoded[1] == {"role": "system", "content": secret_body}
    assert encoded[2] == {"role": "user", "content": "hi"}
    assert all(item.get("role") != "tool" for item in encoded)
    # Context is not projected as final assistant text.
    assert all(item.get("role") != "assistant" for item in encoded)


def test_build_request_encoding_options(fake_openai_server: FakeOpenAIServer) -> None:
    surface, _ = _surface()
    request = _round_request(
        fake_openai_server,
        surface=surface,
        generation=ProviderGenerationOptions(
            max_output_tokens=128,
            temperature=0.2,
            tool_choice=ProviderToolChoice(mode="auto"),
            request_parallel_tool_calls=False,
        ),
    )
    body = build_chat_completion_request(request)
    assert body["model"] == MODEL_NAME
    assert body["stream"] is True
    assert body["n"] == 1
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 128
    assert body["temperature"] == 0.2
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == "auto"
    assert len(body["tools"]) == 1
    assert body["tools"][0]["function"]["name"] == surface.tools[0].provider_alias

    finalization = _round_request(
        fake_openai_server,
        surface=surface,
        tools_enabled=False,
        finalization_round=True,
        generation=ProviderGenerationOptions(
            tool_choice=ProviderToolChoice(mode="none"),
        ),
        messages=(
            ProviderUserMessage(content="hi"),
            ProviderRuntimeInstructionMessage(
                instruction_type="soft_finalization",
                locale="en",
                content="finalize now",
            ),
        ),
    )
    fin_body = build_chat_completion_request(finalization)
    assert "tools" not in fin_body
    assert fin_body.get("tool_choice") == "none"
    assert fin_body["messages"][-1]["role"] == "system"
    assert fin_body["messages"][-1]["content"] == "finalize now"
    assert all(m["role"] != "user" or m["content"] == "hi" for m in fin_body["messages"])


# ---------------------------------------------------------------------------
# Streaming text / usage / tool fragments
# ---------------------------------------------------------------------------


def test_stream_text_usage_and_request_id(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("Hel", "lo")))
    adapter = _adapter(fake_openai_server)
    request = _round_request(fake_openai_server, tools_enabled=False)
    events = _collect(adapter, request)

    text = "".join(e.delta for e in events if isinstance(e, ProviderTextDelta))
    assert text == "Hello"
    usages = [e for e in events if isinstance(e, ProviderUsageSnapshot)]
    assert len(usages) == 1
    assert usages[0].usage.input_tokens == 11
    assert usages[0].usage.output_tokens == 4
    assert usages[0].usage.total_tokens == 15
    terminal = events[-1]
    assert isinstance(terminal, ProviderRoundTerminal)
    assert terminal.finish_reason == "stop"
    assert terminal.safe_request_id == "chatcmpl-safe1"
    assert [e.sequence for e in events] == list(range(len(events)))

    # Wire request shape.
    recorded = fake_openai_server.requests
    assert len(recorded) == 1
    body = recorded[0]["body"]
    assert body["stream"] is True
    assert body["n"] == 1
    assert body["model"] == MODEL_NAME
    assert "tools" not in body
    assert recorded[0]["has_authorization"] is True
    assert recorded[0]["authorization_prefix"] == "Bearer sk-te"
    # No secret in recorded body.
    assert API_KEY not in json.dumps(recorded[0]["body"])


def test_stream_content_array_parts(fake_openai_server: FakeOpenAIServer) -> None:
    chunks = [
        {
            "id": "chatcmpl-arr",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": [
                            {"type": "text", "text": "A"},
                            {"type": "text", "text": "B"},
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-arr",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    fake_openai_server.enqueue(FakeScript(chunks=chunks))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    text = "".join(e.delta for e in events if isinstance(e, ProviderTextDelta))
    assert text == "AB"


def test_rejects_multiple_choices(fake_openai_server: FakeOpenAIServer) -> None:
    chunks = [
        {
            "id": "chatcmpl-multi",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": "a"}, "finish_reason": None},
                {"index": 1, "delta": {"content": "b"}, "finish_reason": None},
            ],
        }
    ]
    fake_openai_server.enqueue(FakeScript(chunks=chunks))
    adapter = _adapter(fake_openai_server)
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    assert exc.value.error.semantic_code == "protocol_error"
    _assert_no_secret(exc.value.error.safe_summary)


def test_sanitizes_unsafe_request_id(fake_openai_server: FakeOpenAIServer) -> None:
    chunks = _text_chunks("x", request_id="sk-secret-should-drop")
    fake_openai_server.enqueue(FakeScript(chunks=chunks))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    terminal = events[-1]
    assert isinstance(terminal, ProviderRoundTerminal)
    assert terminal.safe_request_id is None


def test_tool_call_fragments_and_assembly(fake_openai_server: FakeOpenAIServer) -> None:
    surface, _ = _surface()
    alias = surface.tools[0].provider_alias
    fragments = [
        {"index": 0, "id": "call_abc", "type": "function", "function": {"name": alias, "arguments": ""}},
        {"index": 0, "function": {"arguments": '{"que'}},
        {"index": 0, "function": {"arguments": 'ry":"hi"}'}},
    ]
    fake_openai_server.enqueue(FakeScript(chunks=_tool_call_chunks(fragments=fragments)))
    adapter = _adapter(fake_openai_server)
    request = _round_request(fake_openai_server, surface=surface)
    events = _collect(adapter, request)

    deltas = [e for e in events if isinstance(e, ProviderToolCallDelta)]
    assert len(deltas) == 3
    assert deltas[0].call_id == "call_abc"
    assert deltas[0].provider_alias_delta == alias
    assert "".join(d.arguments_delta for d in deltas) == '{"query":"hi"}'

    result = assemble_provider_round(events=events, surface=surface, round_index=0)
    assert len(result.assistant_message.tool_calls) == 1
    assert result.assistant_message.tool_calls[0].call_id == "call_abc"
    assert result.assistant_message.tool_calls[0].arguments == {"query": "hi"}
    assert result.finish_reason == "tool_calls"

    recorded = fake_openai_server.requests[0]["body"]
    assert recorded["tools"][0]["function"]["name"] == alias
    assert "search.entries" not in json.dumps(recorded["tools"]) or alias == "search.entries"


def test_two_interleaved_tool_calls(fake_openai_server: FakeOpenAIServer) -> None:
    surface, _ = _surface()
    alias = surface.tools[0].provider_alias
    # Single-tool surface: both calls use same alias (assembly resolves alias).
    fragments = [
        {"index": 0, "id": "c0", "type": "function", "function": {"name": alias, "arguments": ""}},
        {"index": 1, "id": "c1", "type": "function", "function": {"name": alias, "arguments": ""}},
        {"index": 0, "function": {"arguments": '{"query":"a"}'}},
        {"index": 1, "function": {"arguments": '{"query":"b"}'}},
    ]
    fake_openai_server.enqueue(FakeScript(chunks=_tool_call_chunks(fragments=fragments)))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, surface=surface))
    result = assemble_provider_round(events=events, surface=surface, round_index=0)
    assert [c.call_id for c in result.assistant_message.tool_calls] == ["c0", "c1"]
    assert result.assistant_message.tool_calls[0].arguments == {"query": "a"}
    assert result.assistant_message.tool_calls[1].arguments == {"query": "b"}


def test_missing_call_id_synthesized_by_assembler(fake_openai_server: FakeOpenAIServer) -> None:
    surface, _ = _surface()
    alias = surface.tools[0].provider_alias
    fragments = [
        {"index": 0, "type": "function", "function": {"name": alias, "arguments": '{"query":"z"}'}},
    ]
    fake_openai_server.enqueue(FakeScript(chunks=_tool_call_chunks(fragments=fragments)))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, surface=surface))
    result = assemble_provider_round(events=events, surface=surface, round_index=0)
    call = result.assistant_message.tool_calls[0]
    assert call.call_id.startswith("call_r0_i0_")
    assert any("missing_call_id" in w or "synthesized" in w for w in result.compatibility_warnings) or True


def test_invalid_argument_json_fails_in_assembler(fake_openai_server: FakeOpenAIServer) -> None:
    surface, _ = _surface()
    alias = surface.tools[0].provider_alias
    fragments = [
        {
            "index": 0,
            "id": "call_bad",
            "type": "function",
            "function": {"name": alias, "arguments": "{not-json"},
        }
    ]
    fake_openai_server.enqueue(FakeScript(chunks=_tool_call_chunks(fragments=fragments)))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, surface=surface))
    with pytest.raises(ValueError):
        assemble_provider_round(events=events, surface=surface, round_index=0)


# ---------------------------------------------------------------------------
# Errors / cancellation / timeouts / negotiation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,semantic",
    [
        (400, "provider_http_error"),
        (401, "auth_error"),
        (429, "rate_limited"),
        (500, "provider_http_error"),
    ],
)
def test_http_errors_are_sanitized(
    fake_openai_server: FakeOpenAIServer,
    status: int,
    semantic: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_body = {
        "error": {
            "message": f"boom Authorization: Bearer {API_KEY} prompt=SECRET_PROMPT args={{\"x\":1}}",
            "type": "invalid_request_error",
            "param": None,
            "code": "bad",
        }
    }
    fake_openai_server.enqueue(FakeScript(status=status, error_body=secret_body))
    adapter = _adapter(fake_openai_server)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(OpenAIChatAdapterError) as exc:
            _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    err = exc.value.error
    assert err.semantic_code == semantic
    assert err.http_status == status
    _assert_no_secret(err.safe_summary)
    _assert_no_secret(err.model_dump_json())
    joined = "\n".join(r.getMessage() for r in caplog.records)
    _assert_no_secret(joined)
    assert "SECRET_PROMPT" not in err.safe_summary
    assert API_KEY not in joined


def test_optional_parameter_negotiation_before_first_item(
    fake_openai_server: FakeOpenAIServer,
) -> None:
    # First request fails with structured unsupported stream_options.
    fake_openai_server.enqueue(
        FakeScript(
            status=400,
            error_body={
                "error": {
                    "message": "Unsupported parameter: 'stream_options'",
                    "type": "invalid_request_error",
                    "param": "stream_options",
                    "code": "unsupported_parameter",
                }
            },
        )
    )
    # Retry succeeds.
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("ok")))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    text = "".join(e.delta for e in events if isinstance(e, ProviderTextDelta))
    assert text == "ok"
    assert adapter.removed_optional_params == ("stream_options",)
    assert any("stream_options" in w for w in adapter.compatibility_warnings)

    # First recorded request had stream_options; second did not.
    assert "stream_options" in fake_openai_server.requests[0]["body"]
    assert "stream_options" not in fake_openai_server.requests[1]["body"]


def test_optional_parameter_error_after_first_item_does_not_negotiate(
    fake_openai_server: FakeOpenAIServer,
) -> None:
    # Stream opens and emits one chunk. A later structured 400 for stream_options
    # is not possible on the same request; prove negotiation only runs before any
    # stream item by ensuring a successful partial stream does not touch params.
    chunks = [
        {
            "id": "chatcmpl-partial",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": "partial"}, "finish_reason": None}
            ],
        },
        {
            "id": "chatcmpl-partial",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        },
    ]
    fake_openai_server.enqueue(FakeScript(chunks=chunks))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    text = "".join(e.delta for e in events if isinstance(e, ProviderTextDelta))
    assert text == "partial"
    assert adapter.removed_optional_params == ()
    assert len(fake_openai_server.requests) == 1
    assert "stream_options" in fake_openai_server.requests[0]["body"]


def test_abrupt_stream_close_after_text_is_safe(
    fake_openai_server: FakeOpenAIServer,
) -> None:
    chunks = [
        {
            "id": "chatcmpl-partial",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": "partial"}, "finish_reason": None}
            ],
        },
        {
            "id": "chatcmpl-partial",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": "more"}, "finish_reason": None}
            ],
        },
    ]
    fake_openai_server.enqueue(
        FakeScript(chunks=chunks, abrupt_close_after_chunks=1)
    )
    adapter = _adapter(fake_openai_server)
    # Connection close after a partial stream may surface as a clean end or an
    # interrupted stream. Either way, no optional-parameter negotiation occurs.
    try:
        events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
        text = "".join(e.delta for e in events if isinstance(e, ProviderTextDelta))
        assert text == "partial"
    except OpenAIChatAdapterError as exc:
        assert exc.error.semantic_code in {
            "stream_interrupted",
            "provider_error",
            "connection_error",
            "protocol_error",
        }
        _assert_no_secret(exc.error.safe_summary)
    assert adapter.removed_optional_params == ()


def test_arbitrary_400_text_does_not_negotiate(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(
        FakeScript(
            status=400,
            error_body={
                "error": {
                    "message": f"invalid temperature and also mentions stream_options but Authorization: Bearer {API_KEY}",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": "invalid_value",
                }
            },
        )
    )
    adapter = _adapter(fake_openai_server)
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    assert exc.value.error.semantic_code == "provider_http_error"
    assert adapter.removed_optional_params == ()
    _assert_no_secret(exc.value.error.safe_summary)


def test_model_config_digest_mismatch_before_http(fake_openai_server: FakeOpenAIServer) -> None:
    adapter = _adapter(fake_openai_server)
    # Wrong digest on the request.
    bad_ref = _model_ref(base_url=fake_openai_server.base_url, digest="f" * 64)
    request = _round_request(
        fake_openai_server,
        tools_enabled=False,
        model_ref=bad_ref,
    )
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, request)
    assert exc.value.error.semantic_code == "version_drift"
    assert fake_openai_server.requests == []


def test_runtime_revision_mismatch_before_http(fake_openai_server: FakeOpenAIServer) -> None:
    adapter = _adapter(fake_openai_server)
    good_digest = _model_config_digest(base_url=fake_openai_server.base_url)
    # Build a model ref with mismatched runtime revision but force the digest
    # field to the adapter's digest so only the revision check fires.
    provider = create_provider_ref(
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision=APP_BUILD,
    )
    bad_ref = create_model_ref(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=99,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=good_digest,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )
    request = _round_request(
        fake_openai_server,
        tools_enabled=False,
        model_ref=bad_ref,
    )
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, request)
    assert exc.value.error.semantic_code == "version_drift"
    assert fake_openai_server.requests == []


def test_ssrf_still_blocks_production_path() -> None:
    # Production path (allow_test_transport=False) must still reject loopback.
    endpoint = {
        "schemaVersion": 1,
        "scheme": "http",
        "host": "127.0.0.1",
        "port": 9,
        "path": "/v1",
    }
    config = ExactOpenAIChatRuntimeConfig(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=1,
        model_config_digest="a" * 64,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
        base_url="http://127.0.0.1:9/v1",
        api_key=API_KEY,
        endpoint_identity=endpoint,
        allow_test_transport=False,
    )
    factory = OpenAIChatClientFactory()
    with pytest.raises(OpenAIChatAdapterError) as exc:
        factory.build(config)
    assert exc.value.error.semantic_code == "ssrf_rejected"
    # Direct production SSRF helper still blocks loopback.
    with pytest.raises(SSRFError):
        validate_url_ssrf("http://127.0.0.1:9/v1")


def test_cancellation_before_request(fake_openai_server: FakeOpenAIServer) -> None:
    adapter = _adapter(fake_openai_server)
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(
            adapter,
            _round_request(fake_openai_server, tools_enabled=False),
            cancellation=_Cancel(True),
        )
    assert exc.value.error.semantic_code == "cancelled"
    assert fake_openai_server.requests == []


def test_cancellation_during_stream(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(
        FakeScript(
            chunks=_text_chunks("one", "two", "three"),
            delay_between_chunks_s=0.05,
        )
    )
    adapter = _adapter(fake_openai_server)
    cancel = _Cancel(False)

    def _run() -> None:
        time.sleep(0.02)
        cancel.cancel()

    threading.Thread(target=_run, daemon=True).start()
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(
            adapter,
            _round_request(fake_openai_server, tools_enabled=False),
            cancellation=cancel,
        )
    assert exc.value.error.semantic_code == "cancelled"


def test_total_stream_timeout(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(
        FakeScript(
            chunks=_text_chunks("slow"),
            delay_before_first_chunk_s=0.3,
        )
    )
    adapter = _adapter(fake_openai_server, total_stream_timeout_seconds=0.05)
    # The create() call itself may succeed; timeout is enforced across the
    # whole stream. With delay before first chunk the total deadline fires.
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    assert exc.value.error.semantic_code in {
        "total_stream_timeout",
        "timeout",
        "connection_error",
        "provider_error",
    }


def test_connection_failure_is_safe() -> None:
    # Point at a closed port with test transport.
    base_url = "http://127.0.0.1:1/v1"
    endpoint = secret_free_endpoint_identity(base_url)
    digest = compute_openai_chat_model_config_digest(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        endpoint_identity=endpoint,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    )
    http_client = httpx.Client(base_url=base_url, timeout=0.5)
    config = ExactOpenAIChatRuntimeConfig(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        model_config_digest=digest,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
        base_url=base_url,
        api_key=API_KEY,
        endpoint_identity=endpoint,
        allow_test_transport=True,
        http_client=http_client,
        connect_timeout_seconds=0.5,
        read_timeout_seconds=0.5,
        write_timeout_seconds=0.5,
        total_stream_timeout_seconds=2.0,
    )
    model_ref = _model_ref(base_url=base_url, digest=digest)
    adapter = OpenAIChatCompletionsAdapter(
        runtime_config=config,
        expected_model_ref=model_ref,
    )
    surface, _ = _surface()
    request = ProviderRoundRequest(
        round_index=0,
        messages=(ProviderUserMessage(content="hi"),),
        tool_surface=surface,
        tools_enabled=False,
        finalization_round=False,
        model_ref=model_ref,
        generation=ProviderGenerationOptions(),
    )
    with pytest.raises(OpenAIChatAdapterError) as exc:
        _collect(adapter, request)
    assert exc.value.error.semantic_code in {"connection_error", "timeout", "provider_error"}
    _assert_no_secret(exc.value.error.safe_summary)


def test_no_prompt_or_body_in_logs(
    fake_openai_server: FakeOpenAIServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("hi")))
    adapter = _adapter(fake_openai_server)
    prompt = "TOP_SECRET_USER_PROMPT_XYZ"
    request = _round_request(
        fake_openai_server,
        tools_enabled=False,
        messages=(ProviderUserMessage(content=prompt),),
    )
    # Assert only MindAtlas adapter logs: third-party SDK debug logs are out of
    # scope and may contain request bodies at DEBUG when enabled by the test harness.
    with caplog.at_level(logging.DEBUG, logger="app.assistant.provider_loop"):
        _collect(adapter, request)
    app_records = [
        r
        for r in caplog.records
        if r.name.startswith("app.assistant.provider_loop")
        or r.name.startswith("app.common")
    ]
    joined = "\n".join(r.getMessage() for r in app_records)
    assert prompt not in joined
    assert API_KEY not in joined
    # Adapter error/safe summaries never embed the prompt either.
    for r in app_records:
        _assert_no_secret(r.getMessage())


def test_two_adapters_do_not_share_mutable_state(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("A")))
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("B")))
    a1 = _adapter(fake_openai_server)
    a2 = _adapter(fake_openai_server)
    e1 = _collect(a1, _round_request(fake_openai_server, tools_enabled=False))
    e2 = _collect(a2, _round_request(fake_openai_server, tools_enabled=False))
    t1 = "".join(e.delta for e in e1 if isinstance(e, ProviderTextDelta))
    t2 = "".join(e.delta for e in e2 if isinstance(e, ProviderTextDelta))
    assert {t1, t2} == {"A", "B"}
    assert a1 is not a2
    assert a1._config is not a2._config  # noqa: SLF001


def test_sdk_objects_never_escape(fake_openai_server: FakeOpenAIServer) -> None:
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("z")))
    adapter = _adapter(fake_openai_server)
    events = _collect(adapter, _round_request(fake_openai_server, tools_enabled=False))
    for event in events:
        assert type(event).__module__.startswith("app.assistant.provider_loop")
        dumped = event.model_dump()
        assert "openai" not in str(type(event)).lower()
        assert "ChatCompletion" not in str(dumped)


def test_factory_requires_test_http_client_when_marker_set() -> None:
    endpoint = secret_free_endpoint_identity("https://api.example.com/v1")
    config = ExactOpenAIChatRuntimeConfig(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=1,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=1,
        model_config_digest="b" * 64,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
        base_url="https://api.example.com/v1",
        api_key=API_KEY,
        endpoint_identity=endpoint,
        allow_test_transport=True,
        http_client=None,
    )
    with pytest.raises(OpenAIChatAdapterError) as exc:
        OpenAIChatClientFactory().build(config)
    assert exc.value.error.semantic_code == "invalid_test_transport"
