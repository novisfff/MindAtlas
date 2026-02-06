from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AiRegistryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_normalize_openai_compat_base_url_adds_v1(self) -> None:
        from app.ai_registry.runtime import _normalize_openai_compat_base_url  # noqa: E402

        self.assertEqual(
            _normalize_openai_compat_base_url(" https://right.codes/codex "),
            "https://right.codes/codex/v1",
        )
        self.assertEqual(
            _normalize_openai_compat_base_url("https://right.codes/codex/v1/"),
            "https://right.codes/codex/v1",
        )

    def test_resolve_openai_compat_config_normalizes_base_url_and_key(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel  # noqa: E402
        from app.ai_registry.runtime import resolve_openai_compat_config  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            cred = AiCredential(
                name="right-codes",
                base_url=" https://right.codes/codex ",
                api_key_encrypted="enc",
                api_key_hint="****",
            )
            db.add(cred)
            db.commit()
            db.refresh(cred)

            model = AiModel(
                credential_id=cred.id,
                name="gpt-4o-mini",
                model_type="llm",
            )
            db.add(model)
            db.commit()
            db.refresh(model)

            binding = AiComponentBinding(
                component="assistant",
                llm_model_id=model.id,
                embedding_model_id=None,
            )
            db.add(binding)
            db.commit()

            with patch("app.ai_registry.runtime.decrypt_api_key", return_value=" sk-live-key "):
                cfg = resolve_openai_compat_config(db, component="assistant", model_type="llm")

            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.base_url, "https://right.codes/codex/v1")
            self.assertEqual(cfg.api_key, "sk-live-key")
            self.assertEqual(cfg.model, "gpt-4o-mini")
        finally:
            db.close()

