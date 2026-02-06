from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AiRegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_build_openai_compat_headers_trims_api_key(self) -> None:
        from app.ai_registry.service import _build_openai_compat_headers  # noqa: E402

        headers = _build_openai_compat_headers("  sk-test-key\n")

        self.assertEqual(headers["authorization"], "Bearer sk-test-key")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], "MindAtlas/1.0")

    def test_test_connection_uses_openai_compat_headers(self) -> None:
        from app.ai_registry.service import AiCredentialService  # noqa: E402

        db = MagicMock()
        service = AiCredentialService(db)
        cred = SimpleNamespace(id="c1", base_url="https://api.example.com", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=cred)

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self) -> int:
                return 200

        captured: dict[str, str | int] = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            captured["authorization"] = req.get_header("Authorization")
            captured["user_agent"] = req.get_header("User-agent")
            captured["accept"] = req.get_header("Accept")
            return FakeResp()

        with (
            patch("app.ai_registry.service.decrypt_api_key", return_value=" sk-test-key "),
            patch("app.ai_registry.service.urlopen", new=fake_urlopen),
        ):
            ok, status_code, message = service.test_connection(cred.id)

        self.assertTrue(ok)
        self.assertEqual(status_code, 200)
        self.assertEqual(message, "OK")
        self.assertEqual(captured["url"], "https://api.example.com/v1/models")
        self.assertEqual(captured["timeout"], 10)
        self.assertEqual(captured["authorization"], "Bearer sk-test-key")
        self.assertEqual(captured["user_agent"], "MindAtlas/1.0")
        self.assertEqual(captured["accept"], "application/json")

