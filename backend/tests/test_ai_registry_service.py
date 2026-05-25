from __future__ import annotations

import socket
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AiRegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_delete_model_clears_component_bindings(self) -> None:
        from app.common.exceptions import ApiException  # noqa: E402
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel  # noqa: E402
        from app.ai_registry.service import AiModelService  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            cred = AiCredential(
                name="OpenAI",
                base_url="https://api.example.com/v1",
                api_key_encrypted="enc",
                api_key_hint="****",
            )
            model = AiModel(credential=cred, name="gpt-4o-mini", model_type="llm")
            db.add_all([cred, model])
            db.flush()
            binding = AiComponentBinding(component="assistant", llm_model_id=model.id)
            db.add(binding)
            db.commit()

            with self.assertRaises(ApiException) as ctx:
                AiModelService(db).delete(model.id)
            self.assertEqual(ctx.exception.code, 40911)
            self.assertEqual(ctx.exception.details["action"], "confirm_unbind_then_delete")

            AiModelService(db).delete(model.id, confirm_bound_bindings=True)

            self.assertIsNone(db.query(AiModel).filter(AiModel.id == model.id).first())
            db.refresh(binding)
            self.assertIsNone(binding.llm_model_id)
        finally:
            db.close()

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

    def test_create_allows_hostname_when_dns_returns_fake_ip(self) -> None:
        from app.ai_registry.service import AiCredentialService  # noqa: E402

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        service = AiCredentialService(db)

        with (
            patch(
                "app.common.ssrf.socket.getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.34", 0))],
            ),
            patch("app.ai_registry.service.encrypt_api_key", return_value="enc"),
            patch("app.ai_registry.service.api_key_hint", return_value="****"),
        ):
            cred = service.create("codex-for-me", "https://api-vip.codex-for.me/v1", "sk-test")

        self.assertEqual(cred.name, "codex-for-me")
        self.assertEqual(cred.base_url, "https://api-vip.codex-for.me/v1")
        self.assertEqual(cred.api_key_encrypted, "enc")
        self.assertEqual(cred.api_key_hint, "****")
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(cred)
