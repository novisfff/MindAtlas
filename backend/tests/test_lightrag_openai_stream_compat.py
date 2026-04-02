from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class _FakeStreamRequiredError(Exception):
    def __init__(self) -> None:
        self.status_code = 400
        super().__init__("Error code: 400 - {'detail': 'Stream must be set to true'}")


class _FakeResponseFormatError(Exception):
    def __init__(self) -> None:
        self.status_code = 400
        super().__init__("Error code: 400 - {'detail': 'response_format is not supported'}")


class _FakeStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()

    async def aclose(self) -> None:
        self.closed = True


class _FakeChatCompletions:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(responses))
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class LightRagOpenAiStreamCompatTests(unittest.TestCase):
    def test_normalize_lightrag_response_format_converts_basemodel_class(self) -> None:
        from app.lightrag.manager import _normalize_lightrag_response_format

        class _KeywordFormat(BaseModel):
            high_level_keywords: list[str]
            low_level_keywords: list[str]

        normalized = _normalize_lightrag_response_format(_KeywordFormat)

        self.assertIsInstance(normalized, dict)
        self.assertIn(normalized.get("type"), {"json_schema", "json_object"})

    def test_requires_stream_mode_detection_matches_provider_error(self) -> None:
        from app.lightrag.manager import _lightrag_requires_stream_mode

        self.assertTrue(_lightrag_requires_stream_mode(_FakeStreamRequiredError()))
        self.assertFalse(_lightrag_requires_stream_mode(_FakeResponseFormatError()))

    def test_complete_wrapper_retries_non_stream_call_with_stream_and_aggregates(self) -> None:
        from app.lightrag.manager import _openai_complete_with_stream_compat

        calls: list[dict] = []

        async def _fake_complete(model, prompt, **kwargs):
            calls.append({"model": model, "prompt": prompt, **kwargs})
            if kwargs.get("stream") is True:
                async def _chunks():
                    yield "hello "
                    yield "world"

                return _chunks()
            raise _FakeStreamRequiredError()

        result = asyncio.run(
            _openai_complete_with_stream_compat(
                complete_func=_fake_complete,
                client_factory=lambda **_: None,
                model="gpt-test",
                prompt="hi",
            )
        )

        self.assertEqual(result, "hello world")
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0].get("stream"))
        self.assertTrue(calls[1]["stream"])

    def test_complete_wrapper_uses_keyword_extraction_stream_fallback(self) -> None:
        from app.lightrag.manager import _openai_complete_with_stream_compat

        async def _fake_complete(model, prompt, **kwargs):
            raise _FakeStreamRequiredError()

        with patch(
            "app.lightrag.manager._keyword_extraction_stream_fallback",
            new=AsyncMock(return_value='{"high_level_keywords":[],"low_level_keywords":[]}'),
        ) as mocked_fallback:
            result = asyncio.run(
                _openai_complete_with_stream_compat(
                    complete_func=_fake_complete,
                    client_factory=lambda **_: None,
                    model="gpt-test",
                    prompt="hi",
                    keyword_extraction=True,
                )
            )

        self.assertEqual(result, '{"high_level_keywords":[],"low_level_keywords":[]}')
        mocked_fallback.assert_awaited_once()

    def test_keyword_extraction_stream_fallback_retries_without_response_format(self) -> None:
        from app.lightrag.manager import _keyword_extraction_stream_fallback

        stream = _FakeStream(
            [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"high_level_keywords":['))]),
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='"alpha"],"low_level_keywords":["beta"]}'))]),
            ]
        )
        client = _FakeClient([_FakeResponseFormatError(), stream])
        factory_calls: list[dict] = []

        def _client_factory(**kwargs):
            factory_calls.append(kwargs)
            return client

        result = asyncio.run(
            _keyword_extraction_stream_fallback(
                client_factory=_client_factory,
                model="gpt-test",
                prompt="hi",
                base_url="https://api.example.com/v1",
                api_key="k",
            )
        )

        self.assertEqual(result, '{"high_level_keywords":["alpha"],"low_level_keywords":["beta"]}')
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(client.chat.completions.calls), 2)
        self.assertEqual(client.chat.completions.calls[0]["response_format"], {"type": "json_object"})
        self.assertNotIn("response_format", client.chat.completions.calls[1])
        self.assertTrue(stream.closed)
        self.assertTrue(client.closed)

    def test_keyword_extraction_stream_fallback_normalizes_basemodel_response_format(self) -> None:
        from app.lightrag.manager import _keyword_extraction_stream_fallback

        class _KeywordFormat(BaseModel):
            high_level_keywords: list[str]
            low_level_keywords: list[str]

        stream = _FakeStream(
            [
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"high_level_keywords":[],"low_level_keywords":[]}'))]),
            ]
        )
        client = _FakeClient([stream])

        result = asyncio.run(
            _keyword_extraction_stream_fallback(
                client_factory=lambda **_: client,
                model="gpt-test",
                prompt="hi",
                response_format=_KeywordFormat,
            )
        )

        self.assertEqual(result, '{"high_level_keywords":[],"low_level_keywords":[]}')
        self.assertIsInstance(client.chat.completions.calls[0]["response_format"], dict)
        self.assertNotEqual(client.chat.completions.calls[0]["response_format"], _KeywordFormat)
        self.assertTrue(stream.closed)
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
