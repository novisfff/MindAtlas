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
        from app.common.ssrf import normalize_openai_base_url  # noqa: E402

        self.assertEqual(
            normalize_openai_base_url(" https://right.codes/codex "),
            "https://right.codes/codex/v1",
        )
        self.assertEqual(
            normalize_openai_base_url("https://right.codes/codex/v1/"),
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

    def test_resolve_openai_compat_config_by_model_id(self) -> None:
        from app.ai_registry.models import AiCredential, AiModel  # noqa: E402
        from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            cred = AiCredential(
                name="custom-runtime",
                base_url=" https://example.com/openai ",
                api_key_encrypted="enc",
                api_key_hint="****",
            )
            db.add(cred)
            db.commit()
            db.refresh(cred)

            llm_model = AiModel(
                credential_id=cred.id,
                name="gpt-4.1-mini",
                model_type="llm",
            )
            db.add(llm_model)
            db.commit()
            db.refresh(llm_model)

            embedding_model = AiModel(
                credential_id=cred.id,
                name="text-embedding-3-small",
                model_type="embedding",
            )
            db.add(embedding_model)
            db.commit()
            db.refresh(embedding_model)

            with patch("app.ai_registry.runtime.decrypt_api_key", return_value=" sk-custom "):
                cfg = resolve_openai_compat_config_by_model_id(
                    db,
                    model_id=str(llm_model.id),
                    model_type="llm",
                )
            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.model_id, llm_model.id)
            self.assertEqual(cfg.model, "gpt-4.1-mini")

            with patch("app.ai_registry.runtime.decrypt_api_key", return_value=" sk-custom "):
                bad_cfg = resolve_openai_compat_config_by_model_id(
                    db,
                    model_id=str(embedding_model.id),
                    model_type="llm",
                )
            self.assertIsNone(bad_cfg)
        finally:
            db.close()

    def test_runtime_revision_helpers_reuse_plan01_payloads(self) -> None:
        from app.ai_registry.runtime import (  # noqa: E402
            credential_runtime_fields_changed,
            invalidate_model_probe_pointers,
            model_runtime_fields_changed,
        )
        from app.ai_registry.models import AiCredential, AiModel  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            cred = AiCredential(
                name="rev-helpers",
                base_url="https://api.example.com/v1",
                api_key_encrypted="enc",
                api_key_hint="****",
                runtime_revision=1,
            )
            db.add(cred)
            db.commit()
            db.refresh(cred)
            model = AiModel(
                credential_id=cred.id,
                name="gpt",
                model_type="llm",
                runtime_revision=1,
                current_capability_probe_id=None,
            )
            db.add(model)
            db.commit()
            db.refresh(model)

            self.assertFalse(
                credential_runtime_fields_changed(
                    {"base_url": cred.base_url, "api_key_encrypted": cred.api_key_encrypted},
                    cred,
                )
            )
            self.assertTrue(
                credential_runtime_fields_changed(
                    {"base_url": "https://other/v1", "api_key_encrypted": "enc"},
                    cred,
                )
            )
            self.assertFalse(model_runtime_fields_changed(model, model))
            self.assertTrue(
                model_runtime_fields_changed(
                    {"name": "other", "model_type": "llm", "credential_id": model.credential_id},
                    model,
                )
            )
            # pointer clear helper
            model.current_capability_probe_id = None
            invalidate_model_probe_pointers([model])
            self.assertIsNone(model.current_capability_probe_id)
        finally:
            db.close()

    def test_resolve_openai_compat_config_supports_workflow_copilot_component(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel  # noqa: E402
        from app.ai_registry.runtime import resolve_openai_compat_config  # noqa: E402
        from tests._db import make_session  # noqa: E402

        db = make_session()
        try:
            cred = AiCredential(
                name="workflow-copilot-runtime",
                base_url="https://copilot.example.com/openai",
                api_key_encrypted="enc",
                api_key_hint="****",
            )
            db.add(cred)
            db.commit()
            db.refresh(cred)

            model = AiModel(
                credential_id=cred.id,
                name="gpt-4.1-mini",
                model_type="llm",
            )
            db.add(model)
            db.commit()
            db.refresh(model)

            binding = AiComponentBinding(
                component="workflow_copilot",
                llm_model_id=model.id,
                embedding_model_id=None,
            )
            db.add(binding)
            db.commit()

            with patch("app.ai_registry.runtime.decrypt_api_key", return_value=" sk-workflow-copilot "):
                cfg = resolve_openai_compat_config(db, component="workflow_copilot", model_type="llm")

            self.assertIsNotNone(cfg)
            assert cfg is not None
            self.assertEqual(cfg.model, "gpt-4.1-mini")
            self.assertEqual(cfg.api_key, "sk-workflow-copilot")
            self.assertEqual(cfg.base_url, "https://copilot.example.com/openai/v1")
        finally:
            db.close()
