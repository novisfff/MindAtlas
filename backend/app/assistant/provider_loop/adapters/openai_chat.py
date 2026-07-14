"""OpenAI-compatible Chat Completions one-round Provider adapter (Plan 03 Task 6).

Transport-only: encodes one ``ProviderRoundRequest``, streams Chat Completions
chunks through the OpenAI SDK, and emits normalized ``ProviderStreamEvent``s.
Does not loop, execute Tools, resolve aliases, or expose SDK objects.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    OpenAI,
)

from app.assistant.domain.contracts import ModelRef
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.provider_loop.aliases import OPENAI_CHAT_PROVIDER_PROTOCOL
from app.assistant.provider_loop.contracts import (
    ProviderGenerationOptions,
    ProviderRoundRequest,
    ProviderRoundTerminal,
    ProviderStreamEvent,
    ProviderTextDelta,
    ProviderToolCallDelta,
    ProviderToolChoice,
    ProviderToolSurface,
    ProviderUsage,
    ProviderUsageSnapshot,
    SafeProviderError,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderContextUpdateMessage,
    ProviderMessage,
    ProviderRuntimeInstructionMessage,
    ProviderSystemMessage,
    ProviderToolMessage,
    ProviderUserMessage,
    provider_message_payload,
)
from app.assistant.provider_loop.streaming import is_safe_request_id
from app.common.ssrf import SSRFError, normalize_openai_base_url, validate_url_ssrf

logger = logging.getLogger(__name__)

ADAPTER_KEY = "openai_chat_completions"
DEFAULT_ADAPTER_REVISION = "1"
PROVIDER_PROTOCOL = OPENAI_CHAT_PROVIDER_PROTOCOL

# Explicit byte bound for Tool Result envelopes on the wire.
TOOL_RESULT_ENVELOPE_BYTE_LIMIT = 64 * 1024

# Optional parameters eligible for one pre-stream negotiation drop.
_OPTIONAL_NEGOTIABLE_PARAMS = frozenset({"stream_options"})

_UNSUPPORTED_PARAM_RE = re.compile(
    r"(?i)(?:unsupported|unknown|unexpected|not\s+supported|unrecognized)"
    r".{0,80}?(?:parameter|param|field|argument|option)?\s*['\"`]?"
    r"(?P<name>stream_options)['\"`]?"
    r"|"
    r"['\"`]?(?P<name2>stream_options)['\"`]?"
    r".{0,40}?(?:is\s+not\s+supported|unsupported|unknown|not\s+allowed)"
)

_SECRET_LIKE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._~+/=-]+|"
    r"authorization\s*[:=]|api[_-]?key\s*[:=]|password\s*[:=])"
)


class OpenAIChatAdapterError(Exception):
    """Adapter-local failure carrying a sanitized ``SafeProviderError``."""

    def __init__(self, error: SafeProviderError) -> None:
        if not isinstance(error, SafeProviderError):
            raise TypeError("error must be a SafeProviderError")
        super().__init__(error.safe_summary)
        self.error = error


@dataclass(frozen=True)
class ExactOpenAIChatRuntimeConfig:
    """Ephemeral exact runtime config used to build one OpenAI client.

    Never store this object in frozen contracts or continuation state. The
    ``api_key`` field is secret and must not appear in logs or digests.
    """

    model_id: UUID
    model_name: str
    model_type: str
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    model_config_digest: str
    adapter_key: str
    adapter_revision: str
    app_build_revision: str
    base_url: str
    api_key: str
    # Secret-free endpoint identity already validated for digest purposes.
    endpoint_identity: dict[str, Any]
    # Production path always re-runs SSRF. Tests inject an explicit transport
    # marker so loopback fake servers do not require weakening production SSRF.
    allow_test_transport: bool = False
    http_client: httpx.Client | None = field(default=None, repr=False, compare=False)
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 30.0
    total_stream_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.adapter_key != ADAPTER_KEY:
            raise ValueError("adapter_key mismatch")
        if not self.model_name or not str(self.model_name).strip():
            raise ValueError("model_name must be non-empty")
        if not self.api_key or not str(self.api_key).strip():
            raise ValueError("api_key must be non-empty")
        if not self.base_url or not str(self.base_url).strip():
            raise ValueError("base_url must be non-empty")
        if not self.model_config_digest or len(self.model_config_digest) != 64:
            raise ValueError("model_config_digest must be a 64-hex digest")
        for name in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "write_timeout_seconds",
            "total_stream_timeout_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive number")


def secret_free_endpoint_identity(base_url: str) -> dict[str, Any]:
    """Derive a secret-free endpoint identity for digests and safe errors.

    Rejects URL user-info, query, and fragment. Does not include credentials.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL is empty",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    raw = base_url.strip()
    parsed = urlsplit(raw)
    if parsed.username is not None or parsed.password is not None:
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL must not include user-info",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    if parsed.query:
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL must not include a query string",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    if parsed.fragment:
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL must not include a fragment",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    if parsed.scheme not in {"http", "https"}:
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL scheme must be http or https",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    if not parsed.hostname:
        raise OpenAIChatAdapterError(
            SafeProviderError(
                semantic_code="invalid_endpoint",
                safe_summary="provider base URL is missing a hostname",
                adapter_key=ADAPTER_KEY,
                adapter_revision=DEFAULT_ADAPTER_REVISION,
                retry_disposition="never",
            )
        )
    normalized = normalize_openai_base_url(raw)
    parts = urlsplit(normalized)
    return {
        "schemaVersion": 1,
        "scheme": parts.scheme or None,
        "host": (parts.hostname or "").lower() or None,
        "port": parts.port,
        "path": parts.path or None,
    }


def compute_openai_chat_model_config_digest(
    *,
    model_id: UUID,
    model_name: str,
    model_type: str,
    model_runtime_revision: int,
    credential_id: UUID,
    credential_runtime_revision: int,
    endpoint_identity: Mapping[str, Any],
    adapter_key: str,
    adapter_revision: str,
    app_build_revision: str,
    provider_protocol: str = PROVIDER_PROTOCOL,
    probe_contract_version: int | None = None,
) -> str:
    """Secret-free exact model/config digest for adapter identity checks."""
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "adapterKey": adapter_key,
        "adapterRevision": adapter_revision,
        "appBuildRevision": app_build_revision,
        "credentialId": str(credential_id),
        "credentialRuntimeRevision": int(credential_runtime_revision),
        "endpointIdentity": dict(endpoint_identity),
        "modelId": str(model_id),
        "modelName": model_name,
        "modelRuntimeRevision": int(model_runtime_revision),
        "modelType": model_type,
        "providerProtocol": provider_protocol,
    }
    if probe_contract_version is not None:
        payload["probeContractVersion"] = int(probe_contract_version)
    return sha256_canonical_json(payload)  # type: ignore[arg-type]


class OpenAIChatClientFactory:
    """Build a one-shot OpenAI client from an exact runtime config."""

    def build(self, config: ExactOpenAIChatRuntimeConfig) -> OpenAI:
        if not isinstance(config, ExactOpenAIChatRuntimeConfig):
            raise TypeError("config must be ExactOpenAIChatRuntimeConfig")

        # Recompute secret-free endpoint identity and re-validate SSRF unless
        # the explicit test transport marker is set.
        endpoint = secret_free_endpoint_identity(config.base_url)
        if endpoint != config.endpoint_identity:
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider endpoint identity drift before client build",
                    adapter_key=config.adapter_key,
                    adapter_revision=config.adapter_revision,
                    retry_disposition="never",
                )
            )

        if not config.allow_test_transport:
            try:
                validate_url_ssrf(normalize_openai_base_url(config.base_url))
            except SSRFError as exc:
                logger.warning(
                    "openai_chat_ssrf_rejected adapter=%s revision=%s",
                    config.adapter_key,
                    config.adapter_revision,
                )
                raise OpenAIChatAdapterError(
                    SafeProviderError(
                        semantic_code="ssrf_rejected",
                        safe_summary="provider base URL failed SSRF validation",
                        adapter_key=config.adapter_key,
                        adapter_revision=config.adapter_revision,
                        retry_disposition="never",
                    )
                ) from exc
        elif config.http_client is None:
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="invalid_test_transport",
                    safe_summary="test transport marker requires an injected http client",
                    adapter_key=config.adapter_key,
                    adapter_revision=config.adapter_revision,
                    retry_disposition="never",
                )
            )

        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.write_timeout_seconds,
            pool=config.connect_timeout_seconds,
        )
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "base_url": normalize_openai_base_url(config.base_url),
            "timeout": timeout,
            "max_retries": 0,
        }
        if config.http_client is not None:
            client_kwargs["http_client"] = config.http_client
        return OpenAI(**client_kwargs)


def encode_openai_chat_tools(surface: ProviderToolSurface) -> list[dict[str, Any]]:
    """Encode frozen tool definitions as Chat Completions tools list."""
    if not isinstance(surface, ProviderToolSurface):
        raise TypeError("surface must be a ProviderToolSurface")
    tools: list[dict[str, Any]] = []
    for definition in surface.tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": definition.provider_alias,
                    "description": definition.description,
                    "parameters": definition.input_schema,
                },
            }
        )
    return tools


def encode_openai_chat_messages(messages: tuple[ProviderMessage, ...]) -> list[dict[str, Any]]:
    """Encode provider-neutral messages into Chat Completions wire form."""
    if not isinstance(messages, tuple):
        raise TypeError("messages must be a tuple")
    encoded: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, ProviderSystemMessage):
            encoded.append({"role": "system", "content": message.content})
            continue
        if isinstance(message, ProviderRuntimeInstructionMessage):
            # Runtime finalization is internal protocol history. Map to system.
            encoded.append({"role": "system", "content": message.content})
            continue
        if isinstance(message, ProviderContextUpdateMessage):
            # Protected Skill/Main Agent context. Map to system; never user/tool/final.
            # Content is not logged.
            encoded.append({"role": "system", "content": message.content})
            continue
        if isinstance(message, ProviderUserMessage):
            encoded.append({"role": "user", "content": message.content})
            continue
        if isinstance(message, ProviderAssistantMessage):
            payload: dict[str, Any] = {
                "role": "assistant",
                "content": message.content if message.content is not None else None,
            }
            if message.tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.provider_alias,
                            "arguments": json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                                allow_nan=False,
                            ),
                        },
                    }
                    for call in message.tool_calls
                ]
            encoded.append(payload)
            continue
        if isinstance(message, ProviderToolMessage):
            envelope = provider_message_payload(message)["content"]
            body = json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > TOOL_RESULT_ENVELOPE_BYTE_LIMIT:
                raise OpenAIChatAdapterError(
                    SafeProviderError(
                        semantic_code="tool_result_too_large",
                        safe_summary="tool result envelope exceeds wire byte bound",
                        adapter_key=ADAPTER_KEY,
                        adapter_revision=DEFAULT_ADAPTER_REVISION,
                        retry_disposition="never",
                    )
                )
            encoded.append(
                {
                    "role": "tool",
                    "tool_call_id": message.call_id,
                    "content": body,
                }
            )
            continue
        raise TypeError(f"unsupported provider message type {type(message)!r}")
    return encoded


def encode_tool_choice(choice: ProviderToolChoice) -> Any:
    if choice.mode == "auto":
        return "auto"
    if choice.mode == "none":
        return "none"
    if choice.mode == "required":
        return "required"
    if choice.mode == "specific":
        return {
            "type": "function",
            "function": {"name": choice.provider_alias},
        }
    raise ValueError(f"unsupported tool_choice mode {choice.mode!r}")


def build_chat_completion_request(
    request: ProviderRoundRequest,
    *,
    include_stream_options: bool = True,
    include_tools: bool | None = None,
) -> dict[str, Any]:
    """Build the Chat Completions request body for one semantic round."""
    if not isinstance(request, ProviderRoundRequest):
        raise TypeError("request must be a ProviderRoundRequest")

    tools_enabled = bool(request.tools_enabled) and not bool(request.finalization_round)
    if include_tools is not None:
        tools_enabled = bool(include_tools) and tools_enabled

    body: dict[str, Any] = {
        "model": request.model_ref.model_name,
        "messages": encode_openai_chat_messages(tuple(request.messages)),
        "stream": True,
        "n": 1,
    }
    if include_stream_options:
        body["stream_options"] = {"include_usage": True}

    generation = request.generation
    if not isinstance(generation, ProviderGenerationOptions):
        generation = ProviderGenerationOptions()

    if generation.max_output_tokens is not None:
        body["max_tokens"] = int(generation.max_output_tokens)
    if generation.temperature is not None:
        body["temperature"] = float(generation.temperature)

    if tools_enabled:
        tools = encode_openai_chat_tools(request.tool_surface)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = encode_tool_choice(generation.tool_choice)
            if generation.request_parallel_tool_calls is not None:
                body["parallel_tool_calls"] = bool(generation.request_parallel_tool_calls)
    elif request.finalization_round or not request.tools_enabled:
        # Protocol invariant: omit tools. Optionally send tool_choice=none when
        # the generation options request it and finalization is active.
        if (
            request.finalization_round
            and generation.tool_choice.mode == "none"
        ):
            body["tool_choice"] = "none"

    return body


def _chunk_to_mapping(chunk: Any) -> dict[str, Any]:
    if isinstance(chunk, Mapping):
        return dict(chunk)
    if hasattr(chunk, "model_dump"):
        dumped = chunk.model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raise OpenAIChatAdapterError(
        SafeProviderError(
            semantic_code="protocol_error",
            safe_summary="provider stream chunk was not a mapping",
            adapter_key=ADAPTER_KEY,
            adapter_revision=DEFAULT_ADAPTER_REVISION,
            retry_disposition="never",
        )
    )


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _usage_from_payload(usage: Any) -> ProviderUsage | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, Mapping):
        return None
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    cached = usage.get("prompt_tokens_details")
    cached_input: int | None = None
    if isinstance(cached, Mapping):
        value = cached.get("cached_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            cached_input = value
    reasoning: int | None = None
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping):
        value = details.get("reasoning_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            reasoning = value
    return ProviderUsage(
        input_tokens=max(0, input_tokens),
        output_tokens=max(0, output_tokens),
        total_tokens=max(0, total_tokens),
        cached_input_tokens=cached_input,
        reasoning_tokens=reasoning,
    )


def _classify_optional_param_error(exc: BaseException) -> str | None:
    """Return the optional parameter name if a structured 400 identifies it."""
    status = getattr(exc, "status_code", None)
    if status != 400:
        return None
    # Prefer structured body.param when present.
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            param = error.get("param")
            if isinstance(param, str) and param in _OPTIONAL_NEGOTIABLE_PARAMS:
                code = error.get("code")
                message = str(error.get("message") or "")
                if (
                    code in {"unsupported_parameter", "unknown_parameter", "invalid_request_error"}
                    or "unsupported" in message.lower()
                    or "unknown" in message.lower()
                    or "not supported" in message.lower()
                ):
                    return param
            message = str(error.get("message") or "")
            match = _UNSUPPORTED_PARAM_RE.search(message)
            if match:
                name = match.group("name") or match.group("name2")
                if name in _OPTIONAL_NEGOTIABLE_PARAMS:
                    return name
    message = str(getattr(exc, "message", None) or exc)
    # Never negotiate from arbitrary exception text that embeds secrets.
    if _SECRET_LIKE_RE.search(message):
        return None
    match = _UNSUPPORTED_PARAM_RE.search(message)
    if match:
        name = match.group("name") or match.group("name2")
        if name in _OPTIONAL_NEGOTIABLE_PARAMS:
            return name
    return None


def _safe_error_from_exception(
    exc: BaseException,
    *,
    adapter_key: str,
    adapter_revision: str,
    http_status: int | None = None,
    semantic_code: str | None = None,
    safe_summary: str | None = None,
) -> SafeProviderError:
    status = http_status
    if status is None:
        status = getattr(exc, "status_code", None)
        if not isinstance(status, int):
            status = None

    if semantic_code is None:
        if isinstance(exc, APITimeoutError):
            semantic_code = "timeout"
            safe_summary = safe_summary or "provider request timed out"
        elif isinstance(exc, APIConnectionError):
            semantic_code = "connection_error"
            safe_summary = safe_summary or "provider connection failed"
        elif status == 401:
            semantic_code = "auth_error"
            safe_summary = safe_summary or "provider authentication failed"
        elif status == 403:
            semantic_code = "auth_error"
            safe_summary = safe_summary or "provider authorization failed"
        elif status == 429:
            semantic_code = "rate_limited"
            safe_summary = safe_summary or "provider rate limited the request"
        elif status is not None and 400 <= status < 500:
            semantic_code = "provider_http_error"
            safe_summary = safe_summary or "provider rejected the request"
        elif status is not None and status >= 500:
            semantic_code = "provider_http_error"
            safe_summary = safe_summary or "provider server error"
        elif isinstance(exc, OpenAIChatAdapterError):
            return exc.error
        else:
            semantic_code = "provider_error"
            safe_summary = safe_summary or "provider round failed"

    return SafeProviderError(
        semantic_code=semantic_code,
        safe_summary=safe_summary or "provider round failed",
        http_status=status,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        retry_disposition="never",
    )


class OpenAIChatCompletionsAdapter:
    """One-round OpenAI Chat Completions adapter.

    Emits only normalized stream events. Never exposes SDK chunks, response
    objects, or raw exceptions outside this module.
    """

    provider_protocol: str = PROVIDER_PROTOCOL
    adapter_key: str = ADAPTER_KEY

    def __init__(
        self,
        *,
        runtime_config: ExactOpenAIChatRuntimeConfig,
        client_factory: OpenAIChatClientFactory | None = None,
        expected_model_ref: ModelRef | None = None,
    ) -> None:
        if not isinstance(runtime_config, ExactOpenAIChatRuntimeConfig):
            raise TypeError("runtime_config must be ExactOpenAIChatRuntimeConfig")
        self._config = runtime_config
        self.adapter_revision = runtime_config.adapter_revision
        self.model_config_digest = runtime_config.model_config_digest
        self._factory = client_factory or OpenAIChatClientFactory()
        self._expected_model_ref = expected_model_ref
        self._client: OpenAI | None = None
        self._compatibility_warnings: list[str] = []
        self._removed_optional_params: list[str] = []

    @property
    def compatibility_warnings(self) -> tuple[str, ...]:
        return tuple(self._compatibility_warnings)

    @property
    def removed_optional_params(self) -> tuple[str, ...]:
        return tuple(self._removed_optional_params)

    def _require_identity(self, request: ProviderRoundRequest) -> None:
        if self._expected_model_ref is not None:
            if request.model_ref.model_ref_digest != self._expected_model_ref.model_ref_digest:
                raise OpenAIChatAdapterError(
                    SafeProviderError(
                        semantic_code="version_drift",
                        safe_summary="provider round model_ref digest mismatch",
                        adapter_key=self.adapter_key,
                        adapter_revision=self.adapter_revision,
                        retry_disposition="never",
                    )
                )
        if request.model_ref.model_config_digest is not None:
            if request.model_ref.model_config_digest != self.model_config_digest:
                raise OpenAIChatAdapterError(
                    SafeProviderError(
                        semantic_code="version_drift",
                        safe_summary="provider round model_config_digest mismatch",
                        adapter_key=self.adapter_key,
                        adapter_revision=self.adapter_revision,
                        retry_disposition="never",
                    )
                )
        if request.model_ref.model_id != self._config.model_id:
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider round model_id mismatch",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )
        if request.model_ref.credential_id != self._config.credential_id:
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider round credential_id mismatch",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )
        if (
            request.model_ref.model_runtime_revision is not None
            and request.model_ref.model_runtime_revision != self._config.model_runtime_revision
        ):
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider round model_runtime_revision mismatch",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )
        if (
            request.model_ref.credential_runtime_revision is not None
            and request.model_ref.credential_runtime_revision
            != self._config.credential_runtime_revision
        ):
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider round credential_runtime_revision mismatch",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )
        if request.model_ref.model_name != self._config.model_name:
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="version_drift",
                    safe_summary="provider round model_name mismatch",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = self._factory.build(self._config)
        return self._client

    def stream_round(
        self,
        request: ProviderRoundRequest,
        *,
        cancellation: Any,
    ) -> Iterator[ProviderStreamEvent]:
        if not isinstance(request, ProviderRoundRequest):
            raise TypeError("request must be a ProviderRoundRequest")
        if cancellation is not None and getattr(cancellation, "is_cancelled", lambda: False)():
            raise OpenAIChatAdapterError(
                SafeProviderError(
                    semantic_code="cancelled",
                    safe_summary="provider round cancelled before request",
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                    retry_disposition="never",
                )
            )

        # Identity / revision checks happen before any HTTP I/O.
        self._require_identity(request)

        include_stream_options = True
        negotiated = False
        body = build_chat_completion_request(
            request,
            include_stream_options=include_stream_options,
        )

        client = self._get_client()
        deadline = time.monotonic() + float(self._config.total_stream_timeout_seconds)
        stream: Any = None
        saw_item = False
        sequence = 0
        last_finish_reason: str | None = None
        last_request_id: str | None = None
        cumulative_usage: ProviderUsage | None = None

        try:
            while True:
                try:
                    stream = client.chat.completions.create(**body)
                    break
                except Exception as exc:  # noqa: BLE001 - sanitize all provider failures
                    if (
                        not negotiated
                        and not saw_item
                        and (param := _classify_optional_param_error(exc)) is not None
                        and param in body
                    ):
                        body = dict(body)
                        body.pop(param, None)
                        negotiated = True
                        self._removed_optional_params.append(param)
                        warning = f"optional_parameter_removed:{param}"
                        if warning not in self._compatibility_warnings:
                            self._compatibility_warnings.append(warning)
                        logger.info(
                            "openai_chat_optional_param_removed adapter=%s param=%s",
                            self.adapter_key,
                            param,
                        )
                        continue
                    raise OpenAIChatAdapterError(
                        _safe_error_from_exception(
                            exc,
                            adapter_key=self.adapter_key,
                            adapter_revision=self.adapter_revision,
                        )
                    ) from None

            if stream is None:
                raise OpenAIChatAdapterError(
                    SafeProviderError(
                        semantic_code="provider_error",
                        safe_summary="provider stream was not opened",
                        adapter_key=self.adapter_key,
                        adapter_revision=self.adapter_revision,
                        retry_disposition="never",
                    )
                )

            try:
                for raw_chunk in stream:
                    if time.monotonic() > deadline:
                        raise OpenAIChatAdapterError(
                            SafeProviderError(
                                semantic_code="total_stream_timeout",
                                safe_summary="provider stream exceeded total timeout",
                                adapter_key=self.adapter_key,
                                adapter_revision=self.adapter_revision,
                                retry_disposition="never",
                            )
                        )
                    if cancellation is not None and getattr(
                        cancellation, "is_cancelled", lambda: False
                    )():
                        raise OpenAIChatAdapterError(
                            SafeProviderError(
                                semantic_code="cancelled",
                                safe_summary="provider round cancelled during stream",
                                adapter_key=self.adapter_key,
                                adapter_revision=self.adapter_revision,
                                retry_disposition="never",
                            )
                        )

                    chunk = _chunk_to_mapping(raw_chunk)
                    saw_item = True

                    request_id = chunk.get("id")
                    if isinstance(request_id, str) and is_safe_request_id(request_id):
                        last_request_id = request_id

                    usage_payload = chunk.get("usage")
                    usage = _usage_from_payload(usage_payload)
                    if usage is not None:
                        cumulative_usage = usage
                        yield ProviderUsageSnapshot(sequence=sequence, usage=usage)
                        sequence += 1

                    choices = chunk.get("choices")
                    if choices is None:
                        continue
                    if not isinstance(choices, list):
                        raise OpenAIChatAdapterError(
                            SafeProviderError(
                                semantic_code="protocol_error",
                                safe_summary="provider stream choices were invalid",
                                adapter_key=self.adapter_key,
                                adapter_revision=self.adapter_revision,
                                retry_disposition="never",
                            )
                        )
                    if len(choices) == 0:
                        # Usage-only final chunk is valid for some providers.
                        continue
                    if len(choices) > 1:
                        raise OpenAIChatAdapterError(
                            SafeProviderError(
                                semantic_code="protocol_error",
                                safe_summary="provider returned multiple choices",
                                adapter_key=self.adapter_key,
                                adapter_revision=self.adapter_revision,
                                retry_disposition="never",
                            )
                        )
                    choice = choices[0]
                    if not isinstance(choice, Mapping):
                        if hasattr(choice, "model_dump"):
                            choice = choice.model_dump()
                        else:
                            raise OpenAIChatAdapterError(
                                SafeProviderError(
                                    semantic_code="protocol_error",
                                    safe_summary="provider choice was not a mapping",
                                    adapter_key=self.adapter_key,
                                    adapter_revision=self.adapter_revision,
                                    retry_disposition="never",
                                )
                            )
                    if choice.get("index", 0) not in (0, None):
                        raise OpenAIChatAdapterError(
                            SafeProviderError(
                                semantic_code="protocol_error",
                                safe_summary="provider returned a non-zero choice index",
                                adapter_key=self.adapter_key,
                                adapter_revision=self.adapter_revision,
                                retry_disposition="never",
                            )
                        )

                    finish_reason = choice.get("finish_reason")
                    if isinstance(finish_reason, str) and finish_reason:
                        last_finish_reason = finish_reason

                    delta = choice.get("delta") or {}
                    if not isinstance(delta, Mapping):
                        if hasattr(delta, "model_dump"):
                            delta = delta.model_dump()
                        else:
                            delta = {}

                    text = _content_to_text(delta.get("content"))
                    if text:
                        yield ProviderTextDelta(sequence=sequence, delta=text)
                        sequence += 1

                    tool_calls = delta.get("tool_calls")
                    if isinstance(tool_calls, list):
                        for item in tool_calls:
                            if not isinstance(item, Mapping):
                                if hasattr(item, "model_dump"):
                                    item = item.model_dump()
                                else:
                                    continue
                            call_index = item.get("index")
                            if not isinstance(call_index, int) or isinstance(call_index, bool):
                                raise OpenAIChatAdapterError(
                                    SafeProviderError(
                                        semantic_code="protocol_error",
                                        safe_summary="tool call index was missing",
                                        adapter_key=self.adapter_key,
                                        adapter_revision=self.adapter_revision,
                                        retry_disposition="never",
                                    )
                                )
                            call_id = item.get("id")
                            if call_id is not None and not isinstance(call_id, str):
                                call_id = str(call_id) if call_id else None
                            if isinstance(call_id, str) and not call_id:
                                call_id = None
                            function = item.get("function") or {}
                            if not isinstance(function, Mapping):
                                if hasattr(function, "model_dump"):
                                    function = function.model_dump()
                                else:
                                    function = {}
                            name_delta = function.get("name") or ""
                            if not isinstance(name_delta, str):
                                name_delta = str(name_delta)
                            args_delta = function.get("arguments") or ""
                            if not isinstance(args_delta, str):
                                args_delta = str(args_delta)
                            yield ProviderToolCallDelta(
                                sequence=sequence,
                                call_index=call_index,
                                call_id=call_id,
                                provider_alias_delta=name_delta,
                                arguments_delta=args_delta,
                            )
                            sequence += 1
            except OpenAIChatAdapterError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Abrupt close after partial stream is still a provider error.
                if saw_item:
                    raise OpenAIChatAdapterError(
                        _safe_error_from_exception(
                            exc,
                            adapter_key=self.adapter_key,
                            adapter_revision=self.adapter_revision,
                            semantic_code="stream_interrupted",
                            safe_summary="provider stream closed unexpectedly",
                        )
                    ) from None
                raise OpenAIChatAdapterError(
                    _safe_error_from_exception(
                        exc,
                        adapter_key=self.adapter_key,
                        adapter_revision=self.adapter_revision,
                    )
                ) from None
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass

            # One terminal event; nothing follows it.
            yield ProviderRoundTerminal(
                sequence=sequence,
                finish_reason=last_finish_reason,
                safe_request_id=last_request_id if is_safe_request_id(last_request_id) else None,
            )
        except OpenAIChatAdapterError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise OpenAIChatAdapterError(
                _safe_error_from_exception(
                    exc,
                    adapter_key=self.adapter_key,
                    adapter_revision=self.adapter_revision,
                )
            ) from None


# Structural satisfaction of ProviderAdapter Protocol is verified in tests.
# We intentionally avoid importing ProviderAdapter into a type assignment that
# would force a circular evaluation at import time under some checkers.


__all__ = [
    "ADAPTER_KEY",
    "DEFAULT_ADAPTER_REVISION",
    "ExactOpenAIChatRuntimeConfig",
    "OpenAIChatAdapterError",
    "OpenAIChatClientFactory",
    "OpenAIChatCompletionsAdapter",
    "PROVIDER_PROTOCOL",
    "TOOL_RESULT_ENVELOPE_BYTE_LIMIT",
    "build_chat_completion_request",
    "compute_openai_chat_model_config_digest",
    "encode_openai_chat_messages",
    "encode_openai_chat_tools",
    "encode_tool_choice",
    "secret_free_endpoint_identity",
]
