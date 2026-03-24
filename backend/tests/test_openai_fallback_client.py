from __future__ import annotations

import io
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class _FakeResponse:
    def __init__(self, body: str):
        self._body = io.BytesIO(body.encode("utf-8"))
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self._body.readline()
        if not line:
            raise StopIteration
        return line

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(status: int, message: str, body: str) -> HTTPError:
    headers = Message()
    headers["Content-Type"] = "application/json; charset=utf-8"
    return HTTPError(
        url="https://api.example.com/v1/chat/completions",
        code=status,
        msg=message,
        hdrs=headers,
        fp=io.BytesIO(body.encode("utf-8")),
    )


class OpenAiFallbackClientTests(unittest.TestCase):
    def test_call_chat_retries_with_stream_when_provider_requires_stream_true(self) -> None:
        from app.assistant.orchestration.openai_fallback_client import (
            OpenAiFallbackClient,
            OpenAiFallbackConfig,
        )

        client = OpenAiFallbackClient()
        cfg = OpenAiFallbackConfig(api_key="k", base_url="https://api.example.com", model="gpt-test")
        sse_body = "\n".join([
            'data: {"choices":[{"delta":{"content":"hello "}}]}',
            'data: {"choices":[{"delta":{"content":"world"}}]}',
            "data: [DONE]",
            "",
        ])

        with patch(
            "app.assistant.orchestration.openai_fallback_client.urlopen",
            side_effect=[
                _http_error(400, "Bad Request", '{"detail":"Stream must be set to true"}'),
                _FakeResponse(sse_body),
            ],
        ) as mocked_urlopen:
            raw = client.call_chat(cfg, [{"role": "user", "content": "hi"}])

        self.assertEqual(mocked_urlopen.call_count, 2)
        self.assertEqual(client.parse_chat_content(raw), "hello world")

    def test_call_chat_returns_none_for_regular_http_400(self) -> None:
        from app.assistant.orchestration.openai_fallback_client import (
            OpenAiFallbackClient,
            OpenAiFallbackConfig,
        )

        client = OpenAiFallbackClient()
        cfg = OpenAiFallbackConfig(api_key="k", base_url="https://api.example.com", model="gpt-test")

        with patch(
            "app.assistant.orchestration.openai_fallback_client.urlopen",
            side_effect=_http_error(400, "Bad Request", '{"detail":"invalid request"}'),
        ):
            raw = client.call_chat(cfg, [{"role": "user", "content": "hi"}])

        self.assertIsNone(raw)
