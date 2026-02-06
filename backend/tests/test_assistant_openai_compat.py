from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AssistantOpenAICompatTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_build_openai_compat_client_headers(self) -> None:
        from app.assistant.openai_compat import build_openai_compat_client_headers  # noqa: E402

        headers = build_openai_compat_client_headers()

        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], "MindAtlas/1.0")

    def test_build_openai_compat_request_headers_trims_key(self) -> None:
        from app.assistant.openai_compat import build_openai_compat_request_headers  # noqa: E402

        headers = build_openai_compat_request_headers("  sk-test-key\n")

        self.assertEqual(headers["authorization"], "Bearer sk-test-key")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], "MindAtlas/1.0")

