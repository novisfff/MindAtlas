from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AiProviderCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_encrypt_decrypt_roundtrip(self) -> None:
        from cryptography.fernet import Fernet  # noqa: E402

        key = Fernet.generate_key().decode("utf-8")

        class FakeSettings:
            ai_provider_fernet_key = key

        with patch("app.ai_provider.crypto.get_settings", return_value=FakeSettings()):
            from app.ai_provider.crypto import decrypt_api_key, encrypt_api_key  # noqa: E402

            token = encrypt_api_key("  secret  ")
            self.assertIsInstance(token, str)
            self.assertNotEqual(token, "secret")
            self.assertEqual(decrypt_api_key(token), "secret")

    def test_missing_key_raises(self) -> None:
        class FakeSettings:
            ai_provider_fernet_key = ""

        with patch("app.ai_provider.crypto.get_settings", return_value=FakeSettings()):
            from app.ai_provider.crypto import encrypt_api_key  # noqa: E402

            with self.assertRaises(ValueError):
                encrypt_api_key("x")

    def test_api_key_hint(self) -> None:
        from app.ai_provider.crypto import api_key_hint  # noqa: E402

        self.assertEqual(api_key_hint(""), "****")
        self.assertEqual(api_key_hint("a"), "****a")
        self.assertEqual(api_key_hint("abcd"), "****abcd")
        self.assertEqual(api_key_hint("012345"), "****2345")

