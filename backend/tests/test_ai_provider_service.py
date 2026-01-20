from __future__ import annotations

import io
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402


class AiProviderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_create_name_duplicate_raises(self) -> None:
        from app.ai_provider.schemas import AiProviderCreateRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        existing = object()
        db.query.return_value.filter.return_value.first.return_value = existing

        service = AiProviderService(db)
        req = AiProviderCreateRequest(
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key="k",
        )
        with self.assertRaises(ApiException) as ctx:
            service.create(req)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_update_commit_integrity_error_raises_409(self) -> None:
        from app.ai_provider.schemas import AiProviderUpdateRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        provider = SimpleNamespace(
            id="p1",
            name="A",
            base_url="u",
            model="m",
            api_key_encrypted="enc",
            api_key_hint="****",
        )
        db.query.return_value.filter.return_value.first.return_value = provider
        db.commit.side_effect = IntegrityError("stmt", "params", Exception("orig"))

        service = AiProviderService(db)
        with self.assertRaises(ApiException) as ctx:
            service.update(provider.id, AiProviderUpdateRequest(base_url="u2"))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40900)
        db.rollback.assert_called()

    def test_update_name_duplicate_raises(self) -> None:
        from app.ai_provider.schemas import AiProviderUpdateRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        provider = SimpleNamespace(
            id="p1",
            name="A",
            base_url="u",
            model="m",
            api_key_encrypted="enc",
            api_key_hint="****",
        )

        # First query for provider by id
        db.query.return_value.filter.return_value.first.side_effect = [provider, object()]

        service = AiProviderService(db)
        with self.assertRaises(ApiException) as ctx:
            service.update(provider.id, AiProviderUpdateRequest(name="B"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40001)

    def test_create_encrypt_failure_raises_50001(self) -> None:
        from app.ai_provider.schemas import AiProviderCreateRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        service = AiProviderService(db)

        req = AiProviderCreateRequest(name="n", base_url="u", model="m", api_key="k")
        with patch("app.ai_provider.service.encrypt_api_key", side_effect=Exception("no key")):
            with self.assertRaises(ApiException) as ctx:
                service.create(req)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertEqual(ctx.exception.code, 50001)

    def test_activate_integrity_error_raises_40901(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", is_active=False)
        service.find_by_id = MagicMock(return_value=provider)

        db.commit.side_effect = IntegrityError("stmt", "params", Exception("orig"))

        with self.assertRaises(ApiException) as ctx:
            service.activate(provider.id)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40901)
        db.rollback.assert_called()

    def test_activate_success_deactivates_others(self) -> None:
        from app.ai_provider.models import AiProvider  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            p1 = AiProvider(
                name="p1",
                base_url="https://x",
                model="m",
                api_key_encrypted="enc",
                api_key_hint="****",
                is_active=True,
            )
            p2 = AiProvider(
                name="p2",
                base_url="https://x",
                model="m",
                api_key_encrypted="enc",
                api_key_hint="****",
                is_active=False,
            )
            db.add_all([p1, p2])
            db.commit()

            svc = AiProviderService(db)
            out = svc.activate(p2.id)
            self.assertTrue(out.is_active)

            db.refresh(p1)
            self.assertFalse(p1.is_active)
        finally:
            db.close()

    def test_test_connection_decrypt_failed(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", base_url="https://x", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=provider)

        with patch("app.ai_provider.service.decrypt_api_key", side_effect=Exception("bad")):
            out = service.test_connection(provider.id)
        self.assertFalse(out.ok)
        self.assertIsNone(out.status_code)
        self.assertEqual(out.message, "Failed to decrypt API key")

    def test_test_connection_http_error(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", base_url="https://x", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=provider)

        err = HTTPError(
            url="https://x/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad"}'),
        )
        with (
            patch("app.ai_provider.service.decrypt_api_key", return_value="k"),
            patch("app.ai_provider.service.urlopen", side_effect=err),
        ):
            out = service.test_connection(provider.id)
        self.assertFalse(out.ok)
        self.assertEqual(out.status_code, 401)
        self.assertIn("HTTP 401", out.message)

    def test_test_connection_ok_2xx(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", base_url="https://x", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=provider)

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def getcode(self) -> int:
                return 200

        with (
            patch("app.ai_provider.service.decrypt_api_key", return_value="k"),
            patch("app.ai_provider.service.urlopen", return_value=FakeResp()),
        ):
            out = service.test_connection(provider.id)
        self.assertTrue(out.ok)
        self.assertEqual(out.status_code, 200)

    def test_test_connection_url_error(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", base_url="https://x", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=provider)

        with (
            patch("app.ai_provider.service.decrypt_api_key", return_value="k"),
            patch("app.ai_provider.service.urlopen", side_effect=URLError("down")),
        ):
            out = service.test_connection(provider.id)
        self.assertFalse(out.ok)
        self.assertIsNone(out.status_code)
        self.assertIn("Connection failed", out.message)

    def test_fetch_models_adds_v1_and_parses_sorted(self) -> None:
        from app.ai_provider.schemas import FetchModelsRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        payload = b'{"data":[{"id":"b"},{"id":"a"}]}'

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return payload

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["timeout"] = timeout
            return FakeResp()

        with patch("app.ai_provider.service.urlopen", new=fake_urlopen):
            out = AiProviderService.fetch_models(
                FetchModelsRequest(base_url="https://api.example.com", api_key="k")
            )

        self.assertTrue(out.ok)
        self.assertEqual(out.models, ["a", "b"])
        self.assertEqual(captured["url"], "https://api.example.com/v1/models")

    def test_fetch_models_http_error(self) -> None:
        from urllib.error import HTTPError  # noqa: E402

        from app.ai_provider.schemas import FetchModelsRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        err = HTTPError(
            url="https://api.example.com/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad"}'),
        )
        with patch("app.ai_provider.service.urlopen", side_effect=err):
            out = AiProviderService.fetch_models(FetchModelsRequest(base_url="https://api.example.com", api_key="k"))
        self.assertFalse(out.ok)
        self.assertIn("HTTP 401", out.message or "")

    def test_fetch_models_url_error(self) -> None:
        from urllib.error import URLError  # noqa: E402

        from app.ai_provider.schemas import FetchModelsRequest  # noqa: E402
        from app.ai_provider.service import AiProviderService  # noqa: E402

        with patch("app.ai_provider.service.urlopen", side_effect=URLError("down")):
            out = AiProviderService.fetch_models(FetchModelsRequest(base_url="https://api.example.com", api_key="k"))
        self.assertFalse(out.ok)
        self.assertIn("Connection failed", out.message or "")

    def test_fetch_models_by_id_decrypt_failure(self) -> None:
        from app.ai_provider.service import AiProviderService  # noqa: E402

        db = MagicMock()
        service = AiProviderService(db)
        provider = SimpleNamespace(id="p1", base_url="https://x", api_key_encrypted="enc")
        service.find_by_id = MagicMock(return_value=provider)

        with patch("app.ai_provider.service.decrypt_api_key", side_effect=Exception("bad")):
            out = service.fetch_models_by_id(provider.id)
        self.assertFalse(out.ok)
        self.assertEqual(out.message, "Failed to decrypt API key")
