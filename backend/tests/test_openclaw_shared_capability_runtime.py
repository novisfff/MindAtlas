"""OpenClaw shared Capability Runtime bridge characterization (Plan 02 Task 8)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches

os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)
os.environ.setdefault("APP_BUILD_REVISION", "plan02-task8-local")
os.environ.setdefault("APP_ENV", "test")

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.openclaw_integration.capability_adapter import (  # noqa: E402
    OpenClawAuthenticationProof,
    OpenClawRuntimeModeSelector,
    translate_capability_error,
)
from app.openclaw_integration.router import runtime_router, settings_router  # noqa: E402
from app.system_settings.initialization_service import SystemInitializationService  # noqa: E402
from app.system_settings.schemas import InitializeSystemRequest  # noqa: E402
from tests._db import make_session  # noqa: E402

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "openclaw_runtime_error_contract.json"
)


class OpenClawSharedCapabilityRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        get_settings.cache_clear()
        self.db = make_session()

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(settings_router)
        app.include_router(runtime_router)

        def _override_get_db():  # noqa: ANN001
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)
        self.mode = os.environ.get("OPENCLAW_CAPABILITY_RUNTIME_MODE", "legacy")

    def tearDown(self) -> None:
        self.db.close()
        get_settings.cache_clear()

    def _initialize_system(self, *, locale: str = "zh") -> None:
        SystemInitializationService(self.db).initialize_system(
            InitializeSystemRequest.model_validate(
                {
                    "locale": locale,
                    "aiCredential": {
                        "name": "OpenAI",
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKey": "sk-test-1234567890",
                    },
                    "llmModel": {"name": "gpt-4.1-mini"},
                    "entryTypes": [
                        {
                            "code": "KNOWLEDGE",
                            "name": "知识" if locale == "zh" else "Knowledge",
                            "description": "知识点" if locale == "zh" else "Concepts",
                            "color": "#3B82F6",
                            "icon": "book",
                            "graphEnabled": True,
                            "aiEnabled": True,
                            "enabled": True,
                            "origin": "default",
                        }
                    ],
                }
            )
        )

    def _rotate_and_enable(self) -> str:
        secret = self.client.post(
            "/api/system-settings/openclaw-integration/rotate-secret"
        ).json()["data"]["secret"]
        enabled = self.client.put(
            "/api/system-settings/openclaw-integration",
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        return secret

    def _auth_headers(self, secret: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {secret}",
            "X-OpenClaw-Source": "unit-test",
            "X-OpenClaw-Channel": "cli",
            "X-OpenClaw-Session": "session-1",
            "X-OpenClaw-Tool": "tool-1",
        }

    def test_error_contract_fixture_covers_runtime_auth_and_execute_codes(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        codes = {row["code"] for row in fixture["runtimeAuthAndExecute"]}
        for expected in (40161, 40361, 40362, 40461, 42261, 42262, 40961, 40061, 40062):
            self.assertIn(expected, codes)

    def test_runtime_mode_selector_snapshots_literal_only(self) -> None:
        selector = OpenClawRuntimeModeSelector()
        mode = selector.snapshot_mode()
        self.assertIn(mode, {"legacy", "shared"})
        # Injected selector freeze: changing process settings after snapshot is out of band;
        # snapshot_mode reads cached settings, so a second call equals first for process life.
        self.assertEqual(selector.snapshot_mode(), mode)

    def test_auth_and_disabled_integration_codes(self) -> None:
        self._initialize_system()
        secret = self._rotate_and_enable()

        unauthorized = self.client.post(
            "/api/integrations/openclaw/capabilities/search_entries/execute",
            json={"query": "x"},
        )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json()["code"], 40161)
        for key in ("success", "code", "message", "data"):
            self.assertIn(key, unauthorized.json())

        # Disable integration and prove 40361 with a valid bearer.
        disabled_settings = self.client.put(
            "/api/system-settings/openclaw-integration",
            json={"enabled": False},
        )
        self.assertEqual(disabled_settings.status_code, 200, disabled_settings.text)
        disabled = self.client.post(
            "/api/integrations/openclaw/capabilities/search_entries/execute",
            headers=self._auth_headers(secret),
            json={"query": "x"},
        )
        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(disabled.json()["code"], 40361)

    def test_missing_capability_code(self) -> None:
        self._initialize_system()
        secret = self._rotate_and_enable()
        response = self.client.post(
            "/api/integrations/openclaw/capabilities/definitely_missing/execute",
            headers=self._auth_headers(secret),
            json={},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 40461)

    def test_system_tool_search_entries_execute_shape(self) -> None:
        self._initialize_system()
        secret = self._rotate_and_enable()
        calls = {"n": 0}

        def fake_runner(**kwargs):  # noqa: ANN003
            calls["n"] += 1
            return {"total": 0, "items": []}

        with patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=fake_runner,
        ), patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=fake_runner,
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/search_entries/execute",
                headers=self._auth_headers(secret),
                json={"query": "hello"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["capabilityKey"], "search_entries")
        self.assertEqual(body["data"]["toolName"], "mindatlas_search_entries")
        self.assertEqual(body["data"]["result"]["total"], 0)
        self.assertEqual(body["data"]["result"]["items"], [])
        self.assertEqual(calls["n"], 1)

    def test_no_double_execution_on_shared_or_legacy_tool_path(self) -> None:
        self._initialize_system()
        secret = self._rotate_and_enable()
        calls = {"n": 0}

        def fake_runner(**kwargs):  # noqa: ANN003
            calls["n"] += 1
            if calls["n"] > 1:
                raise AssertionError("tool executed more than once for one request")
            return {"total": 1, "items": []}

        with patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=fake_runner,
        ), patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=fake_runner,
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/search_entries/execute",
                headers=self._auth_headers(secret),
                json={"query": "once"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls["n"], 1)

    def test_disabled_capability_code(self) -> None:
        self._initialize_system()
        secret = self._rotate_and_enable()
        settings = self.client.get("/api/system-settings/openclaw-integration").json()["data"]
        search_item = next(
            item for item in settings["catalogItems"] if item["capabilityKey"] == "search_entries"
        )
        update = self.client.put(
            f"/api/system-settings/openclaw-integration/catalog-items/{search_item['id']}",
            json={"enabled": False},
        )
        self.assertEqual(update.status_code, 200, update.text)
        response = self.client.post(
            "/api/integrations/openclaw/capabilities/search_entries/execute",
            headers=self._auth_headers(secret),
            json={"query": "x"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], 40362)

    def test_shared_error_translation_matrix(self) -> None:
        from app.assistant.capabilities.contracts import CapabilityError

        cases = [
            ("not_found", 404, 40461),
            ("unavailable", 409, 40961),
            ("version_drift", 409, 40961),
            ("unsupported_interrupt", 409, 40961),
            ("unauthorized", 403, 40362),
            ("invalid_input", 422, 42261),
            ("invalid_output", 422, 42261),
            ("timeout", 409, 40961),
            ("execution_failed", 409, 40961),
            ("cancelled", 409, 40961),
        ]
        for error_type, http, code in cases:
            err = CapabilityError(
                error_type=error_type,  # type: ignore[arg-type]
                safe_code=error_type,
                safe_message="safe",
                retry_disposition="never",
            )
            exc = translate_capability_error(err)
            self.assertEqual(exc.status_code, http, error_type)
            self.assertEqual(exc.code, code, error_type)

    def test_authentication_proof_is_not_serializable_secret(self) -> None:
        proof = OpenClawAuthenticationProof(principal_id="openclaw")
        rendered = repr(proof)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("secret", rendered.lower())
        self.assertTrue(proof.authenticated)


if __name__ == "__main__":
    unittest.main()
