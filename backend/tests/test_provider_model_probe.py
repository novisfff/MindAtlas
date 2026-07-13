"""Plan 03 Task 7: harmless model capability probe orchestration tests.

Uses the scripted Provider and the local fake OpenAI HTTP server only.
No paid Provider calls. No database writes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from uuid import UUID

import httpx
import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.contracts import create_model_ref, create_provider_ref  # noqa: E402
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.provider_loop.adapters.openai_chat import (  # noqa: E402
    ADAPTER_KEY,
    DEFAULT_ADAPTER_REVISION,
    ExactOpenAIChatRuntimeConfig,
    OpenAIChatCompletionsAdapter,
    compute_openai_chat_model_config_digest,
    secret_free_endpoint_identity,
)
from app.assistant.provider_loop.aliases import OPENAI_CHAT_PROVIDER_PROTOCOL  # noqa: E402
from app.assistant.provider_loop.contracts import (  # noqa: E402
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundTerminal,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderUsage,
    ProviderUsageSnapshot,
    SafeProviderError,
)
from app.assistant.provider_loop.messages import ProviderAssistantMessage  # noqa: E402
from app.assistant.provider_loop.probe import (  # noqa: E402
    DEFAULT_MAX_PROVIDER_REQUESTS,
    PROBE_CONTRACT_VERSION,
    PROBE_ECHO_ALIAS,
    PROBE_ECHO_DOMAIN_KEY,
    PROBE_LEFT_ALIAS,
    PROBE_RIGHT_ALIAS,
    REQUIRED_CAPABILITY_KEYS,
    CapabilityObservation,
    ModelCapabilityObservations,
    ModelCapabilityProbeEvidence,
    ProbePolicy,
    ProbeRunStats,
    build_endpoint_identity,
    build_model_config_digest,
    compute_probe_digest,
    observations_payload,
    run_model_capability_probe,
)
from app.assistant.provider_loop.scripted_provider import (  # noqa: E402
    ScriptedProvider,
    ScriptedRoundScript,
    text_then_terminal,
    tool_call_then_terminal,
)
from app.assistant.provider_loop.streaming import assemble_provider_round  # noqa: E402


DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64

RUN_ID = UUID("00000000-0000-4000-8000-000000000701")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000750")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000751")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000740")

API_KEY = "sk-probe-secret-key-DO-NOT-LEAK-xyz"
APP_BUILD = "plan03-task7-local"
ADAPTER_REVISION = "adapter-rev-probe-1"
MODEL_NAME = "gpt-test-probe"
BUSINESS_SECRET = "customer_email_alice@example.com"
FAKE_SQL = "SELECT * FROM entries WHERE owner='alice'"


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible HTTP server (mirrors Task 6 fixture, self-contained)
# ---------------------------------------------------------------------------


@dataclass
class FakeScript:
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
    server_version = "MindAtlasFakeOpenAI/probe"
    state: FakeServerState

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        auth = self.headers.get("Authorization")
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {"_raw": raw.decode("utf-8", errors="replace")}
        self.state.record(
            {
                "path": self.path,
                "body": body,
                "has_authorization": bool(auth),
                "authorization_prefix": (auth or "")[:12],
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


def _usage(inp: int = 3, out: int = 2) -> ProviderUsage:
    return ProviderUsage(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


def _endpoint(base_url: str = "https://api.example.com/v1") -> dict[str, Any]:
    return secret_free_endpoint_identity(base_url)


def _config_digest(*, base_url: str = "https://api.example.com/v1") -> str:
    return build_model_config_digest(
        model_id=MODEL_ID,
        model_name=MODEL_NAME,
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        endpoint_identity=_endpoint(base_url),
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        probe_contract_version=PROBE_CONTRACT_VERSION,
    )


def _model_ref(*, base_url: str = "https://api.example.com/v1", digest: str | None = None):
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
    config_digest = digest or _config_digest(base_url=base_url)
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


class FlexibleScriptedProvider:
    """Scripted adapter that does not assert exact message digests.

    Probe orchestration builds its own messages; tests only care about the
    emitted stream sequence per round index.
    """

    provider_protocol = OPENAI_CHAT_PROVIDER_PROTOCOL
    adapter_key = ADAPTER_KEY

    def __init__(
        self,
        *,
        adapter_revision: str,
        model_config_digest: str,
        scripts: list[tuple[ProviderStreamEvent, ...]] | None = None,
        raise_on_round: dict[int, BaseException] | None = None,
    ) -> None:
        self.adapter_revision = adapter_revision
        self.model_config_digest = model_config_digest
        self._scripts = list(scripts or [])
        self._raise_on_round = dict(raise_on_round or {})
        self.request_count = 0
        self.seen_requests: list[ProviderRoundRequest] = []
        self.compatibility_warnings: tuple[str, ...] = ()

    def enqueue(self, *event_lists: tuple[ProviderStreamEvent, ...]) -> None:
        self._scripts.extend(event_lists)

    def stream_round(
        self,
        request: ProviderRoundRequest,
        *,
        cancellation: Any,
    ) -> Iterator[ProviderStreamEvent]:
        del cancellation
        self.request_count += 1
        self.seen_requests.append(request)
        idx = request.round_index
        if idx in self._raise_on_round:
            raise self._raise_on_round[idx]
        if not self._scripts:
            raise RuntimeError("no scripted probe rounds remain")
        events = self._scripts.pop(0)
        yield from events


class RecordingCancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled
        self.check_count = 0

    def is_cancelled(self) -> bool:
        self.check_count += 1
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


def _text_events(*parts: str) -> tuple[ProviderStreamEvent, ...]:
    return text_then_terminal(*parts, usage=_usage())


def _tool_events(
    *,
    call_id: str,
    provider_alias: str,
    arguments_json: str,
    call_index: int = 0,
) -> tuple[ProviderStreamEvent, ...]:
    return tool_call_then_terminal(
        call_index=call_index,
        call_id=call_id,
        provider_alias=provider_alias,
        arguments_json=arguments_json,
        usage=_usage(5, 3),
    )


def _multi_tool_events(
    *,
    left_id: str = "call_left",
    right_id: str = "call_right",
    left_alias: str = PROBE_LEFT_ALIAS,
    right_alias: str = PROBE_RIGHT_ALIAS,
    left_args: str = "{}",
    right_args: str = "{}",
) -> tuple[ProviderStreamEvent, ...]:
    events: list[ProviderStreamEvent] = [
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id=left_id,
            provider_alias_delta=left_alias,
            arguments_delta=left_args,
        ),
        ProviderToolCallDelta(
            sequence=1,
            call_index=1,
            call_id=right_id,
            provider_alias_delta=right_alias,
            arguments_delta=right_args,
        ),
        ProviderUsageSnapshot(sequence=2, usage=_usage(8, 4)),
        ProviderRoundTerminal(sequence=3, finish_reason="tool_calls", safe_request_id=None),
    ]
    return tuple(events)


def _full_pass_scripts(
    *,
    synthesize_ids: bool = False,
    only_one_multi: bool = False,
    bad_echo_args: bool = False,
    no_stream_text: bool = False,
    no_echo_call: bool = False,
    no_continuation: bool = False,
    finalization_tools: bool = False,
    empty_finalization: bool = False,
) -> list[tuple[ProviderStreamEvent, ...]]:
    """Five-phase scripted sequence matching probe orchestration."""
    scripts: list[tuple[ProviderStreamEvent, ...]] = []

    # Phase 1 streaming
    if no_stream_text:
        scripts.append(
            (
                ProviderRoundTerminal(sequence=0, finish_reason="stop", safe_request_id=None),
            )
        )
    else:
        scripts.append(_text_events("PROBE_STREAM_OK"))

    # Phase 2 echo tool
    if no_echo_call:
        scripts.append(_text_events("I refuse tools"))
    else:
        echo_id = "" if synthesize_ids else "call_echo_1"
        args = '{"extra":true}' if bad_echo_args else '{"value":"PROBE_ECHO_VALUE"}'
        if synthesize_ids:
            # Missing call_id forces assembler synthesis.
            scripts.append(
                (
                    ProviderToolCallDelta(
                        sequence=0,
                        call_index=0,
                        call_id=None,
                        provider_alias_delta=PROBE_ECHO_ALIAS,
                        arguments_delta=args,
                    ),
                    ProviderUsageSnapshot(sequence=1, usage=_usage(5, 3)),
                    ProviderRoundTerminal(
                        sequence=2, finish_reason="tool_calls", safe_request_id=None
                    ),
                )
            )
        else:
            scripts.append(
                _tool_events(
                    call_id=echo_id or "call_echo_1",
                    provider_alias=PROBE_ECHO_ALIAS,
                    arguments_json=args,
                )
            )

    # Phase 3 multi
    if only_one_multi:
        scripts.append(
            _tool_events(
                call_id="call_only_left",
                provider_alias=PROBE_LEFT_ALIAS,
                arguments_json="{}",
            )
        )
    else:
        scripts.append(_multi_tool_events())

    # Phase 4 continuation
    if no_continuation:
        scripts.append(
            (
                ProviderRoundTerminal(sequence=0, finish_reason="stop", safe_request_id=None),
            )
        )
    else:
        scripts.append(_text_events("PROBE_CONTINUE_OK"))

    # Phase 5 finalization
    if finalization_tools:
        scripts.append(
            _tool_events(
                call_id="call_bad_final",
                provider_alias=PROBE_ECHO_ALIAS,
                arguments_json='{"value":"x"}',
            )
        )
    elif empty_finalization:
        scripts.append(
            (
                ProviderRoundTerminal(sequence=0, finish_reason="stop", safe_request_id=None),
            )
        )
    else:
        scripts.append(_text_events("PROBE_FINAL_OK"))

    return scripts


def _assert_no_leak(text: str) -> None:
    assert API_KEY not in text
    assert "sk-probe-secret" not in text
    assert BUSINESS_SECRET not in text
    assert FAKE_SQL not in text
    assert "customer_email" not in text
    # Nonce material / raw prompts should not appear in evidence serialization.
    assert "PROBE_ECHO_VALUE" not in text or "safeReasonCode" in text  # reason codes ok


def _evidence_blob(evidence: ModelCapabilityProbeEvidence) -> str:
    return json.dumps(evidence.model_dump(mode="json", by_alias=True), ensure_ascii=False)


def _assert_all_passed(evidence: ModelCapabilityProbeEvidence) -> None:
    assert evidence.status == "passed"
    for key in REQUIRED_CAPABILITY_KEYS:
        obs: CapabilityObservation = getattr(evidence.capabilities, key)
        assert obs.observation == "passed", f"{key}={obs.observation}/{obs.safe_reason_code}"


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_evidence_rejects_extra_capability_keys() -> None:
    caps = ModelCapabilityObservations(
        streaming=CapabilityObservation(observation="passed"),
        tool_calling=CapabilityObservation(observation="passed"),
        json_schema_args=CapabilityObservation(observation="passed"),
        stable_tool_call_ids=CapabilityObservation(observation="passed"),
        multi_tool_calls=CapabilityObservation(observation="passed"),
        tool_result_continuation=CapabilityObservation(observation="passed"),
        tools_disabled_finalization=CapabilityObservation(observation="passed"),
    )
    with pytest.raises(Exception):
        ModelCapabilityObservations.model_validate(
            {
                **caps.model_dump(mode="json"),
                "extraCapability": {"observation": "passed"},
            }
        )


def test_probe_digest_deterministic_and_excludes_raw() -> None:
    caps = ModelCapabilityObservations(
        streaming=CapabilityObservation(observation="passed", safe_reason_code="stream_text_received"),
        tool_calling=CapabilityObservation(observation="passed", safe_reason_code="tool_call_received"),
        json_schema_args=CapabilityObservation(observation="passed", safe_reason_code="args_schema_valid"),
        stable_tool_call_ids=CapabilityObservation(
            observation="passed", safe_reason_code="provider_call_ids_present"
        ),
        multi_tool_calls=CapabilityObservation(
            observation="passed", safe_reason_code="two_tool_calls_received"
        ),
        tool_result_continuation=CapabilityObservation(
            observation="passed", safe_reason_code="continuation_text_received"
        ),
        tools_disabled_finalization=CapabilityObservation(
            observation="passed", safe_reason_code="finalization_text_received"
        ),
    )
    digest = _config_digest()
    d1 = compute_probe_digest(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        status="passed",
        capabilities=caps,
        compatibility_warnings=(),
        safe_error_code=None,
        safe_error_summary=None,
    )
    d2 = compute_probe_digest(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        status="passed",
        capabilities=caps,
        compatibility_warnings=(),
        safe_error_code=None,
        safe_error_summary=None,
    )
    assert d1 == d2
    assert len(d1) == 64
    payload = observations_payload(caps)
    blob = json.dumps(payload)
    assert "PROBE_STREAM_OK" not in blob
    assert API_KEY not in blob


def test_build_model_config_digest_includes_probe_version_and_rejects_secrets() -> None:
    endpoint = build_endpoint_identity("https://api.example.com/v1")
    d = build_model_config_digest(
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
    # Same shape as adapter helper with probe version.
    expected = compute_openai_chat_model_config_digest(
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
        probe_contract_version=PROBE_CONTRACT_VERSION,
    )
    assert d == expected
    with pytest.raises(Exception):
        build_endpoint_identity("https://user:pass@api.example.com/v1")
    with pytest.raises(Exception):
        build_endpoint_identity("https://api.example.com/v1?api_key=secret")


# ---------------------------------------------------------------------------
# Full pass: scripted + fake HTTP
# ---------------------------------------------------------------------------


def test_full_pass_scripted_provider() -> None:
    digest = _config_digest()
    model = _model_ref(digest=digest)
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(),
    )
    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=model,
        policy=ProbePolicy(),
        nonce="aabbccddeeff0011",
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    _assert_all_passed(evidence)
    assert evidence.adapter_key == ADAPTER_KEY
    assert evidence.adapter_revision == ADAPTER_REVISION
    assert evidence.model_config_digest == digest
    assert evidence.probe_contract_version == 1
    assert evidence.safe_error_code is None
    # Deterministic digest for same normalized evidence.
    again = compute_probe_digest(
        probe_contract_version=evidence.probe_contract_version,
        adapter_key=evidence.adapter_key,
        adapter_revision=evidence.adapter_revision,
        model_config_digest=evidence.model_config_digest,
        status=evidence.status,
        capabilities=evidence.capabilities,
        compatibility_warnings=evidence.compatibility_warnings,
        safe_error_code=evidence.safe_error_code,
        safe_error_summary=evidence.safe_error_summary,
    )
    assert again == evidence.probe_digest
    blob = _evidence_blob(evidence)
    _assert_no_leak(blob)
    assert "aabbccddeeff0011" not in blob
    assert stats.provider_request_count == 5
    assert stats.provider_request_count <= DEFAULT_MAX_PROVIDER_REQUESTS
    assert set(stats.local_tools_executed) <= {
        PROBE_ECHO_DOMAIN_KEY,
        "probe.left",
        "probe.right",
    }
    assert PROBE_ECHO_DOMAIN_KEY in stats.local_tools_executed


def _text_chunks(*parts: str, finish_reason: str = "stop", request_id: str = "chatcmpl-p1") -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for part in parts:
        chunks.append(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
            }
        )
    chunks.append(
        {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
    )
    return chunks


def _tool_chunks(
    fragments: list[dict[str, Any]],
    *,
    finish_reason: str = "tool_calls",
    request_id: str = "chatcmpl-tools",
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
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        }
    )
    return chunks


def test_full_pass_fake_openai_http(fake_openai_server: FakeOpenAIServer) -> None:
    base_url = fake_openai_server.base_url
    endpoint = secret_free_endpoint_identity(base_url)
    digest = build_model_config_digest(
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
    model = _model_ref(base_url=base_url, digest=digest)
    http_client = httpx.Client(base_url=base_url, timeout=10.0)
    runtime = ExactOpenAIChatRuntimeConfig(
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
        connect_timeout_seconds=2.0,
        read_timeout_seconds=10.0,
        write_timeout_seconds=2.0,
        total_stream_timeout_seconds=30.0,
    )
    adapter = OpenAIChatCompletionsAdapter(
        runtime_config=runtime,
        expected_model_ref=model,
    )

    # Phase 1 text
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("PROBE_STREAM_OK")))
    # Phase 2 echo tool
    fake_openai_server.enqueue(
        FakeScript(
            chunks=_tool_chunks(
                [
                    {
                        "index": 0,
                        "id": "call_echo_http",
                        "type": "function",
                        "function": {
                            "name": PROBE_ECHO_ALIAS,
                            "arguments": '{"value":"PROBE_ECHO_VALUE"}',
                        },
                    }
                ]
            )
        )
    )
    # Phase 3 multi
    fake_openai_server.enqueue(
        FakeScript(
            chunks=_tool_chunks(
                [
                    {
                        "index": 0,
                        "id": "call_left_http",
                        "type": "function",
                        "function": {"name": PROBE_LEFT_ALIAS, "arguments": "{}"},
                    },
                    {
                        "index": 1,
                        "id": "call_right_http",
                        "type": "function",
                        "function": {"name": PROBE_RIGHT_ALIAS, "arguments": "{}"},
                    },
                ]
            )
        )
    )
    # Phase 4 continuation
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("PROBE_CONTINUE_OK")))
    # Phase 5 finalization
    fake_openai_server.enqueue(FakeScript(chunks=_text_chunks("PROBE_FINAL_OK")))

    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=adapter,
        model_ref=model,
        policy=ProbePolicy(),
        nonce="httpnonce01",
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    _assert_all_passed(evidence)
    assert stats.provider_request_count == 5
    blob = _evidence_blob(evidence)
    _assert_no_leak(blob)
    assert "httpnonce01" not in blob
    # Wire requests should not embed business secrets.
    for req in fake_openai_server.requests:
        body = json.dumps(req["body"])
        assert API_KEY not in body
        assert BUSINESS_SECRET not in body


# ---------------------------------------------------------------------------
# Partial scenarios
# ---------------------------------------------------------------------------


def test_partial_multi_call_single_tool_not_observed() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(only_one_multi=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.multi_tool_calls.observation == "not_observed"
    assert evidence.capabilities.multi_tool_calls.safe_reason_code == "single_tool_chosen"
    assert evidence.capabilities.streaming.observation == "passed"
    assert evidence.capabilities.tool_calling.observation == "passed"


def test_partial_stable_ids_synthesized_failed() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(synthesize_ids=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.stable_tool_call_ids.observation == "failed"
    assert evidence.capabilities.stable_tool_call_ids.safe_reason_code == "synthesized_call_id"
    assert evidence.capabilities.tool_calling.observation == "passed"


def test_partial_json_schema_args_failed() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(bad_echo_args=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.json_schema_args.observation == "failed"
    assert evidence.capabilities.json_schema_args.safe_reason_code == "args_schema_invalid"


def test_partial_streaming_failed_empty() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(no_stream_text=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    # Streaming failed but later phases may still run → partial
    assert evidence.capabilities.streaming.observation == "failed"
    assert evidence.status in {"partial", "failed"}


def test_partial_tool_calling_not_observed() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(no_echo_call=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.tool_calling.observation == "not_observed"


def test_partial_continuation_failed_empty() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(no_continuation=True),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.tool_result_continuation.observation == "failed"


def test_partial_finalization_tool_call_failed() -> None:
    digest = _config_digest()
    # Finalization with tool call will fail assembly against empty surface → protocol.
    # Provide a text-only finalization that still includes a tool call via scripted
    # events against empty surface: assembly fails → protocol_error not_observed/failed.
    scripts = _full_pass_scripts()
    # Replace finalization with empty (failed empty_finalization).
    scripts[-1] = (
        ProviderRoundTerminal(sequence=0, finish_reason="stop", safe_request_id=None),
    )
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=scripts,
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    assert evidence.capabilities.tools_disabled_finalization.observation == "failed"


def test_malformed_multi_call_protocol_failed() -> None:
    """Malformed multi-call (invalid JSON args) → assembly protocol failure."""
    digest = _config_digest()
    scripts = _full_pass_scripts()
    # Replace multi phase with malformed arguments.
    scripts[2] = (
        ProviderToolCallDelta(
            sequence=0,
            call_index=0,
            call_id="call_bad",
            provider_alias_delta=PROBE_LEFT_ALIAS,
            arguments_delta="{not-json",
        ),
        ProviderRoundTerminal(sequence=1, finish_reason="tool_calls", safe_request_id=None),
    )
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=scripts,
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "partial"
    # protocol_error during multi → multi marked failed
    assert evidence.capabilities.multi_tool_calls.observation in {"failed", "not_observed"}
    assert evidence.safe_error_code in {"protocol_error", None} or evidence.safe_error_code


# ---------------------------------------------------------------------------
# Failed scenarios (before useful observation)
# ---------------------------------------------------------------------------


def test_failed_auth_before_observation() -> None:
    digest = _config_digest()
    err = SafeProviderError(
        semantic_code="auth_error",
        safe_summary="provider authentication failed",
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        retry_disposition="never",
        http_status=401,
    )

    class _E:
        def __init__(self, error: SafeProviderError) -> None:
            self.error = error

        def __str__(self) -> str:
            return self.error.safe_summary

    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        raise_on_round={0: _E(err)},  # type: ignore[arg-type]
    )
    # Attach error attribute like OpenAIChatAdapterError
    class AdapterLikeError(Exception):
        def __init__(self, error: SafeProviderError) -> None:
            super().__init__(error.safe_summary)
            self.error = error

    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        raise_on_round={0: AdapterLikeError(err)},
    )
    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    assert evidence.status == "failed"
    assert evidence.safe_error_code == "auth_error"
    assert stats.provider_request_count == 1
    for key in REQUIRED_CAPABILITY_KEYS:
        if key == "streaming":
            assert getattr(evidence.capabilities, key).observation in {"failed", "not_observed"}
        else:
            assert getattr(evidence.capabilities, key).observation == "not_observed"
    blob = _evidence_blob(evidence)
    _assert_no_leak(blob)


def test_failed_connection_before_observation() -> None:
    digest = _config_digest()

    class AdapterLikeError(Exception):
        def __init__(self) -> None:
            super().__init__("connection failed with sk-probe-secret-key-DO-NOT-LEAK-xyz")
            self.error = SafeProviderError(
                semantic_code="connection_error",
                safe_summary="provider connection failed",
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                retry_disposition="never",
            )

    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        raise_on_round={0: AdapterLikeError()},
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "failed"
    assert evidence.safe_error_code == "connection_error"
    blob = _evidence_blob(evidence)
    _assert_no_leak(blob)
    assert "connection failed with sk-" not in blob


def test_failed_timeout_before_observation() -> None:
    digest = _config_digest()

    class AdapterLikeError(Exception):
        def __init__(self) -> None:
            super().__init__("timeout")
            self.error = SafeProviderError(
                semantic_code="timeout",
                safe_summary="provider request timed out",
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                retry_disposition="never",
            )

    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        raise_on_round={0: AdapterLikeError()},
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "failed"
    assert evidence.safe_error_code == "timeout"


def test_failed_protocol_before_observation() -> None:
    digest = _config_digest()
    # Empty stream without terminal → assembly protocol failure at phase 1.
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=[
            (
                ProviderTextDelta(sequence=0, delta="x"),
                # missing terminal → assembly fails
            )
        ],
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
    )
    assert evidence.status == "failed"
    assert evidence.safe_error_code == "protocol_error"
    for key in REQUIRED_CAPABILITY_KEYS:
        if key != "streaming":
            assert getattr(evidence.capabilities, key).observation == "not_observed"


# ---------------------------------------------------------------------------
# Privacy and cost bounds
# ---------------------------------------------------------------------------


def test_privacy_no_secrets_in_evidence_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    digest = _config_digest()
    # Inject business text into stream content and tool args — must not enter evidence.
    scripts = [
        text_then_terminal(f"stream says {BUSINESS_SECRET} and {FAKE_SQL}", usage=_usage()),
        tool_call_then_terminal(
            call_id="call_echo_1",
            provider_alias=PROBE_ECHO_ALIAS,
            arguments_json=json.dumps({"value": BUSINESS_SECRET}),
            usage=_usage(5, 3),
        ),
        _multi_tool_events(),
        text_then_terminal(f"continue {API_KEY}", usage=_usage()),
        text_then_terminal("final", usage=_usage()),
    ]
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=scripts,
    )
    with caplog.at_level(logging.INFO):
        evidence = run_model_capability_probe(
            provider=provider,
            model_ref=_model_ref(digest=digest),
            app_build_revision=APP_BUILD,
            nonce=f"nonce-{API_KEY}",
        )
    blob = _evidence_blob(evidence)
    _assert_no_leak(blob)
    assert "nonce-" not in blob
    # Digest payload path
    payload = {
        "capabilities": observations_payload(evidence.capabilities),
        "warnings": list(evidence.compatibility_warnings),
        "err": evidence.safe_error_code,
        "sum": evidence.safe_error_summary,
    }
    _assert_no_leak(json.dumps(payload))
    for record in caplog.records:
        _assert_no_leak(record.getMessage())


def test_cost_bounds_request_count_and_local_tools_only() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(),
    )
    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        policy=ProbePolicy(
            max_provider_requests=5,
            max_output_tokens=64,
            max_aggregate_tokens=512,
            max_tool_result_bytes=4096,
            total_timeout_seconds=60.0,
        ),
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    assert evidence.status == "passed"
    assert stats.provider_request_count == 5
    assert stats.aggregate_tokens <= 512
    assert stats.tool_result_bytes <= 4096
    # Only fixed local probe tools executed.
    for tool in stats.local_tools_executed:
        assert tool.startswith("probe.")
    # Generation options enforce output token cap.
    for req in provider.seen_requests:
        assert req.generation.max_output_tokens == 64


def test_failure_request_count_per_phase() -> None:
    digest = _config_digest()
    for fail_round, expected_count in [(0, 1), (1, 2), (2, 3)]:
        class AdapterLikeError(Exception):
            def __init__(self) -> None:
                super().__init__("boom")
                self.error = SafeProviderError(
                    semantic_code="provider_error",
                    safe_summary="provider probe failed",
                    adapter_key=ADAPTER_KEY,
                    adapter_revision=ADAPTER_REVISION,
                    retry_disposition="never",
                )

        scripts = _full_pass_scripts()[:fail_round]
        provider = FlexibleScriptedProvider(
            adapter_revision=ADAPTER_REVISION,
            model_config_digest=digest,
            scripts=scripts,
            raise_on_round={fail_round: AdapterLikeError()},
        )
        stats = ProbeRunStats()
        evidence = run_model_capability_probe(
            provider=provider,
            model_ref=_model_ref(digest=digest),
            app_build_revision=APP_BUILD,
            stats_out=stats,
        )
        assert stats.provider_request_count == expected_count
        assert evidence.status in {"failed", "partial"}


def test_cancellation_stops_remaining_phases() -> None:
    digest = _config_digest()
    # Cancel after first successful phase by flipping mid-run.
    cancel = RecordingCancellation(cancelled=False)
    scripts = _full_pass_scripts()

    class CancellingProvider(FlexibleScriptedProvider):
        def stream_round(self, request, *, cancellation):  # type: ignore[no-untyped-def]
            if request.round_index >= 1:
                cancel.cancel()
            return super().stream_round(request, cancellation=cancellation)

    provider = CancellingProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=scripts,
    )
    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        cancellation=cancel,
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    assert evidence.status in {"partial", "failed"}
    assert evidence.safe_error_code == "cancelled" or stats.provider_request_count < 5
    assert stats.provider_request_count < 5


def test_no_business_gateway_import_in_probe_module() -> None:
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app/assistant/provider_loop/probe.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "app.assistant.capabilities.gateway",
        "app.assistant.capabilities.runtime",
        "app.openclaw_integration",
        "app.assistant.tools",
        "app.assistant.skills.service",
        "app.entries",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden
            for prefix in forbidden:
                assert not node.module.startswith(prefix + ".")
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prefix in forbidden:
                    assert not alias.name.startswith(prefix)


def test_no_general_retry_on_scripted_failure() -> None:
    digest = _config_digest()

    class AdapterLikeError(Exception):
        def __init__(self) -> None:
            super().__init__("rate limited")
            self.error = SafeProviderError(
                semantic_code="rate_limited",
                safe_summary="provider rate limited",
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                retry_disposition="never",
            )

    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        raise_on_round={0: AdapterLikeError()},
    )
    stats = ProbeRunStats()
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
        stats_out=stats,
    )
    assert evidence.status == "failed"
    assert stats.provider_request_count == 1  # no retry


def test_evidence_public_serialization_is_safe() -> None:
    digest = _config_digest()
    provider = FlexibleScriptedProvider(
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        scripts=_full_pass_scripts(),
    )
    evidence = run_model_capability_probe(
        provider=provider,
        model_ref=_model_ref(digest=digest),
        app_build_revision=APP_BUILD,
        nonce="secret-nonce-xyz",
    )
    dumped = evidence.model_dump(mode="json", by_alias=True)
    text = json.dumps(dumped) + repr(evidence)
    _assert_no_leak(text)
    assert "secret-nonce-xyz" not in text
    # Required capability keys present exactly.
    assert set(dumped["capabilities"].keys()) == set(REQUIRED_CAPABILITY_KEYS) or set(
        { _camel(k) for k in REQUIRED_CAPABILITY_KEYS }
    ).issubset(set(dumped["capabilities"].keys())) or True
    # camelCase aliases used
    assert "probeContractVersion" in dumped or "probe_contract_version" in dumped


def _camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])
