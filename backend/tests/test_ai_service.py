from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class AiServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_generate_no_active_provider(self) -> None:
        from app.ai.schemas import AiGenerateRequest  # noqa: E402
        from app.ai.service import AiService  # noqa: E402

        db = MagicMock()
        svc = AiService(db)
        with patch("app.ai.service.resolve_openai_compat_config", return_value=None):
            out = svc.generate(AiGenerateRequest(type_name="t", title="x", content="c"))
        self.assertIsNone(out.summary)
        self.assertEqual(out.suggested_tags, [])

    def test_generate_config_resolve_failure(self) -> None:
        from app.ai.schemas import AiGenerateRequest  # noqa: E402
        from app.ai.service import AiService  # noqa: E402

        db = MagicMock()
        svc = AiService(db)
        with patch("app.ai.service.resolve_openai_compat_config", side_effect=Exception("bad")):
            out = svc.generate(AiGenerateRequest(type_name="t", title="x", content="c"))
        self.assertIsNone(out.summary)
        self.assertEqual(out.suggested_tags, [])

    def test_build_api_url_adds_v1(self) -> None:
        from app.ai.service import AiService  # noqa: E402

        svc = AiService(MagicMock())
        self.assertEqual(
            svc._build_api_url("https://api.example.com", "/models"),
            "https://api.example.com/v1/models",
        )
        self.assertEqual(
            svc._build_api_url("https://api.example.com/v1", "/models"),
            "https://api.example.com/v1/models",
        )

    def test_parse_json_from_text_extracts_object(self) -> None:
        from app.ai.service import AiService  # noqa: E402

        svc = AiService(MagicMock())
        self.assertEqual(svc._parse_json_from_text('{"a":1}'), {"a": 1})
        self.assertEqual(svc._parse_json_from_text("xxx {\"a\":1} yyy"), {"a": 1})
        self.assertIsNone(svc._parse_json_from_text("no-json"))

    def test_parse_openai_response_tags_and_refined_content(self) -> None:
        from app.ai.service import AiService  # noqa: E402

        svc = AiService(MagicMock())
        content = {"summary": "s", "refined_content": "r", "tags": ["t1", "t2"]}
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]})
        out = svc._parse_openai_response(raw)
        self.assertEqual(out.summary, "s")
        self.assertEqual(out.refined_content, "r")
        self.assertEqual(out.suggested_tags, ["t1", "t2"])

    def test_generate_happy_path(self) -> None:
        from app.ai.schemas import AiGenerateRequest  # noqa: E402
        from app.ai.service import AiService  # noqa: E402

        db = MagicMock()
        db.query.return_value.all.return_value = [SimpleNamespace(name="tag1"), SimpleNamespace(name="tag2")]

        svc = AiService(db)

        content = {"summary": "S", "refined_content": "R", "tags": ["a"]}
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}]})

        with (
            patch(
                "app.ai.service.resolve_openai_compat_config",
                return_value=SimpleNamespace(api_key="k", base_url="https://x", model="m"),
            ),
            patch.object(svc, "_call_openai", return_value=raw),
        ):
            out = svc.generate(AiGenerateRequest(type_name="t", title="x", content="c"))

        self.assertEqual(out.summary, "S")
        self.assertEqual(out.refined_content, "R")
        self.assertEqual(out.suggested_tags, ["a"])
