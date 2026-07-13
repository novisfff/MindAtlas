"""Provider transport adapters (Plan 03).

Only adapter modules may import Provider SDKs. Core loop remains SDK-free.
"""

from __future__ import annotations

from app.assistant.provider_loop.adapters.openai_chat import (
    ADAPTER_KEY,
    DEFAULT_ADAPTER_REVISION,
    ExactOpenAIChatRuntimeConfig,
    OpenAIChatAdapterError,
    OpenAIChatClientFactory,
    OpenAIChatCompletionsAdapter,
    encode_openai_chat_messages,
    encode_openai_chat_tools,
    secret_free_endpoint_identity,
)

__all__ = [
    "ADAPTER_KEY",
    "DEFAULT_ADAPTER_REVISION",
    "ExactOpenAIChatRuntimeConfig",
    "OpenAIChatAdapterError",
    "OpenAIChatClientFactory",
    "OpenAIChatCompletionsAdapter",
    "encode_openai_chat_messages",
    "encode_openai_chat_tools",
    "secret_free_endpoint_identity",
]
