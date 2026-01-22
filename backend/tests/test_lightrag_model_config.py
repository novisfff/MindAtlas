"""Unit tests for LightRAG OpenAI-compatible model configuration."""
from __future__ import annotations

import os
import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from app.lightrag.errors import LightRagConfigError  # noqa: E402
from app.lightrag.manager import _resolve_embedding_config, _resolve_llm_config  # noqa: E402


_ENV_KEYS = [
    # LightRAG overrides
    "LIGHTRAG_LLM_MODEL",
    "LIGHTRAG_LLM_HOST",
    "LIGHTRAG_LLM_KEY",
    "LIGHTRAG_EMBEDDING_MODEL",
    "LIGHTRAG_EMBEDDING_HOST",
    "LIGHTRAG_EMBEDDING_KEY",
    "LIGHTRAG_AI_KEY_SOURCE",
    # Global AI settings
    "AI_API_KEY",
    "AI_BASE_URL",
    "AI_MODEL",
    # OpenAI-compatible process env
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "LLM_MODEL",
    "EMBEDDING_MODEL",
]


def _clear_env() -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


class LightRagModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        _clear_env()
        reset_caches()

    def tearDown(self) -> None:
        _clear_env()
        reset_caches()

    def test_llm_model_json_spec_includes_host_key(self) -> None:
        os.environ["LIGHTRAG_LLM_MODEL"] = '{"MODEL":"gpt-4o-mini","HOST":"http://example/v1","KEY":"k1"}'
        reset_caches()

        llm = _resolve_llm_config()
        self.assertEqual(llm.model, "gpt-4o-mini")
        self.assertEqual(llm.base_url, "http://example/v1")
        self.assertEqual(llm.api_key, "k1")

    def test_embedding_defaults_to_llm(self) -> None:
        os.environ["LIGHTRAG_LLM_MODEL"] = '{"MODEL":"m1","HOST":"http://llm/v1","KEY":"k1"}'
        os.environ["LIGHTRAG_EMBEDDING_MODEL"] = "text-embedding-3-small"
        reset_caches()

        llm = _resolve_llm_config()
        embedding = _resolve_embedding_config(llm=llm)
        self.assertEqual(embedding.model, "text-embedding-3-small")
        self.assertEqual(embedding.base_url, llm.base_url)
        self.assertEqual(embedding.api_key, llm.api_key)

    def test_embedding_json_spec_overrides_host_key(self) -> None:
        os.environ["LIGHTRAG_LLM_MODEL"] = '{"MODEL":"m1","HOST":"http://llm/v1","KEY":"k1"}'
        os.environ["LIGHTRAG_EMBEDDING_MODEL"] = '{"MODEL":"e1","HOST":"http://emb/v1","KEY":"k2"}'
        reset_caches()

        llm = _resolve_llm_config()
        embedding = _resolve_embedding_config(llm=llm)
        self.assertEqual(embedding.model, "e1")
        self.assertEqual(embedding.base_url, "http://emb/v1")
        self.assertEqual(embedding.api_key, "k2")

    def test_llm_key_env_overrides_json(self) -> None:
        os.environ["LIGHTRAG_LLM_MODEL"] = '{"MODEL":"m1","HOST":"http://llm/v1","KEY":"k1"}'
        os.environ["LIGHTRAG_LLM_KEY"] = "k-override"
        reset_caches()

        llm = _resolve_llm_config()
        self.assertEqual(llm.api_key, "k-override")

    def test_invalid_json_raises_config_error(self) -> None:
        os.environ["LIGHTRAG_LLM_MODEL"] = "{not-json"
        reset_caches()

        with self.assertRaises(LightRagConfigError):
            _resolve_llm_config()


if __name__ == "__main__":
    unittest.main()

