"""OpenClaw shared-only Capability Runtime bridge characterization (Plan 02B Task 10)."""

from __future__ import annotations

import inspect
import json
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches

os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)
os.environ.setdefault("APP_BUILD_REVISION", "plan02-task9-local")
os.environ.setdefault("APP_ENV", "test")
# Ensure a leftover dual-mode env cannot pin tests to a selector that no longer exists.
os.environ.pop("OPENCLAW_CAPABILITY_RUNTIME_MODE", None)

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assistant.capabilities.classification import (  # noqa: E402
    CLASSIFICATION_CONTRACT_REVISION,
    CLASSIFICATION_RULESET_DIGEST,
)
from app.assistant.capabilities.policy import (  # noqa: E402
    OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS,
    OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
    AuthorizationEvidenceVerificationError,
    lattice_prefix_through,
)
from app.common.exceptions import ApiException, register_exception_handlers  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.openclaw_integration import capability_adapter as oc_adapter  # noqa: E402
from app.openclaw_integration.capability_adapter import (  # noqa: E402
    OpenClawAuthenticationProof,
    OpenClawAuthorizationEvidenceVerifier,
    freeze_openclaw_capability_call,
    translate_capability_error,
    _select_effect_ceiling,
)
from app.openclaw_integration.models import OpenClawCapabilityItem  # noqa: E402
from app.openclaw_integration.registry import list_openclaw_system_item_definitions  # noqa: E402
from app.openclaw_integration.router import runtime_router, settings_router  # noqa: E402
from app.openclaw_integration.service import (  # noqa: E402
    OpenClawIntegrationService,
    OpenClawRuntimeAuditContext,
)
from app.system_settings.initialization_service import SystemInitializationService  # noqa: E402
from app.system_settings.schemas import InitializeSystemRequest  # noqa: E402
from tests._db import make_session  # noqa: E402

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "openclaw_runtime_error_contract.json"
)

# System-tool characterization fixtures with mocked runners.
# Runner payloads use native tool field names (snake_case) that the OpenClaw
# response adapters normalize into public camelCase envelopes.
_TOOL_PARITY_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "capability_key": "search_entries",
        "payload": {"query": "parity"},
        "runner_result": {"total": 0, "items": []},
        "expected_result": {"total": 0, "items": []},
        "needs_lightrag": False,
    },
    {
        "capability_key": "get_entry",
        "payload": {"entryId": "00000000-0000-4000-8000-000000000001"},
        "runner_result": {
            "id": "00000000-0000-4000-8000-000000000001",
            "title": "parity-entry",
            "summary": "s",
            "content": "c",
            "type_code": "KNOWLEDGE",
            "type": "Knowledge",
            "tags": [],
            "time_mode": "NONE",
            "time_at": None,
            "time_from": None,
            "time_to": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
        "expected_result_keys": ("id", "title", "entryTypeCode", "entryTypeName"),
        "needs_lightrag": False,
    },
    {
        "capability_key": "create_relation",
        "payload": {
            "sourceEntryId": "00000000-0000-4000-8000-000000000001",
            "targetEntryId": "00000000-0000-4000-8000-000000000002",
            "relationType": "related_to",
            "description": "parity",
        },
        "runner_result": {
            "id": "00000000-0000-4000-8000-000000000099",
            "source_entry_id": "00000000-0000-4000-8000-000000000001",
            "source_entry_title": "Source",
            "target_entry_id": "00000000-0000-4000-8000-000000000002",
            "target_entry_title": "Target",
            "relation_type_code": "related_to",
            "relation_type_name": "Related",
            "description": "parity",
        },
        "expected_result_keys": (
            "id",
            "sourceEntryId",
            "targetEntryId",
            "relationTypeCode",
        ),
        "needs_lightrag": False,
    },
    {
        "capability_key": "query_knowledge_graph",
        "payload": {"query": "parity graph", "mode": "hybrid", "topK": 5},
        "runner_result": {
            "answer": "ok",
            "sources": [],
            "metadata": {
                "mode": "hybrid",
                "topK": 5,
                "latencyMs": 1,
                "cacheHit": False,
            },
        },
        "expected_result_keys": ("answer", "sources", "metadata"),
        "needs_lightrag": True,
    },
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

    def tearDown(self) -> None:
        self.db.close()
        get_settings.cache_clear()

    def _initialize_system(self, *, locale: str = "zh") -> None:
        SystemInitializationService(self.db).initialize_system(
            InitializeSystemRequest.model_validate(
                {
                    "locale": locale,
                    # Exact Operator password (Task 6 clean-only init); never log/echo.
                    "operatorPassword": "correct horse battery",
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

    def _audit(self) -> OpenClawRuntimeAuditContext:
        return OpenClawRuntimeAuditContext(
            source="unit-test",
            channel="cli",
            session="session-1",
            tool="tool-1",
        )

    def _execute_shared(
        self,
        *,
        capability_key: str,
        payload: dict[str, Any],
        runner_result: Any,
        needs_lightrag: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Run the shared-only worker path with a mocked tool runner."""
        service = OpenClawIntegrationService(self.db)
        calls = {"n": 0}

        def fake_runner(**kwargs):  # noqa: ANN003
            calls["n"] += 1
            return runner_result

        patches = [
            patch(
                "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
                return_value=fake_runner,
            ),
            patch(
                "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
                return_value=fake_runner,
            ),
        ]
        if needs_lightrag:
            patches.append(
                patch(
                    "app.openclaw_integration.service.resolve_runtime_knowledge_graph_config",
                    return_value=type("Cfg", (), {"enabled": True, "configured": True})(),
                )
            )
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            response = service.execute_capability_in_worker(
                capability_key=capability_key,
                raw_payload=payload,
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            )
        return response.model_dump(by_alias=True), calls["n"]

    def test_error_contract_fixture_covers_runtime_auth_and_execute_codes(self) -> None:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        codes = {row["code"] for row in fixture["runtimeAuthAndExecute"]}
        for expected in (40161, 40361, 40362, 40461, 42261, 42262, 40961, 40061, 40062):
            self.assertIn(expected, codes)
        # Shared-only cleanup removes legacy execute branch method names.
        self.assertNotIn("executeBranches", fixture)
        for name in (
            "_execute_tool_capability",
            "_execute_workflow_capability",
            "_execute_agent_capability",
        ):
            self.assertNotIn(name, json.dumps(fixture))

    def test_no_runtime_mode_selector_or_settings_field(self) -> None:
        self.assertFalse(hasattr(oc_adapter, "OpenClawRuntimeModeSelector"))
        self.assertNotIn("openclaw_capability_runtime_mode", Settings.model_fields)
        # Env var is not an accepted Settings surface even if present in the process.
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            AI_PROVIDER_FERNET_KEY=os.environ["AI_PROVIDER_FERNET_KEY"],
            APP_BUILD_REVISION="plan02-task9-local",
            APP_ENV="test",
            OPENCLAW_CAPABILITY_RUNTIME_MODE="shared",
        )
        self.assertFalse(hasattr(settings, "openclaw_capability_runtime_mode"))

    def test_legacy_execute_methods_are_removed(self) -> None:
        for name in (
            "_execute_tool_capability",
            "_execute_workflow_capability",
            "_execute_agent_capability",
        ):
            self.assertFalse(hasattr(OpenClawIntegrationService, name), name)
        # Worker path must always call the shared bridge (no selected_mode parameter).
        sig = inspect.signature(OpenClawIntegrationService.execute_capability_in_worker)
        self.assertNotIn("selected_mode", sig.parameters)

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

    def test_no_double_execution_on_shared_tool_path(self) -> None:
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

    # ------------------------------------------------------------------
    # Shared-only tool characterization (formerly dual-mode parity)
    # ------------------------------------------------------------------

    def test_tool_shared_characterization_matrix(self) -> None:
        """Public envelopes for system tools through the shared-only path."""
        self._initialize_system()
        self._rotate_and_enable()

        for fixture in _TOOL_PARITY_FIXTURES:
            with self.subTest(capability_key=fixture["capability_key"]):
                body, calls = self._execute_shared(
                    capability_key=fixture["capability_key"],
                    payload=fixture["payload"],
                    runner_result=fixture["runner_result"],
                    needs_lightrag=bool(fixture.get("needs_lightrag")),
                )
                self.assertEqual(calls, 1, fixture["capability_key"])
                self.assertEqual(body["capabilityKey"], fixture["capability_key"])
                if "expected_result" in fixture:
                    self.assertEqual(body["result"], fixture["expected_result"])
                else:
                    for key in fixture["expected_result_keys"]:
                        self.assertIn(key, body["result"], f"{fixture['capability_key']}:{key}")

    def test_invalid_input_shared_code(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)

        with self.assertRaises(ApiException) as ctx:
            service.execute_capability_in_worker(
                capability_key="search_entries",
                raw_payload={"limit": "not-a-number"},
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            )
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.code, 42261)

    # ------------------------------------------------------------------
    # Shared-only dispatch guarantees (no mode freeze / no fallback)
    # ------------------------------------------------------------------

    def test_execute_always_uses_shared_bridge(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        calls = {"shared": 0}

        def fake_shared(*args, **kwargs):  # noqa: ANN002, ANN003
            calls["shared"] += 1
            from app.openclaw_integration.schemas import OpenClawCapabilityExecuteResponse

            return OpenClawCapabilityExecuteResponse(
                capability_key="search_entries",
                tool_name="mindatlas_search_entries",
                result={"total": 0, "items": []},
            )

        with patch(
            "app.openclaw_integration.service.execute_shared_capability",
            side_effect=fake_shared,
        ):
            response = service.execute_capability_in_worker(
                capability_key="search_entries",
                raw_payload={"query": "always-shared"},
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            )
        self.assertEqual(response.capability_key, "search_entries")
        self.assertEqual(calls["shared"], 1)

    def test_shared_failure_does_not_fallback_to_legacy(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        legacy_calls = {"n": 0}

        def boom_shared(*args, **kwargs):  # noqa: ANN002, ANN003
            raise ApiException(status_code=409, code=40961, message="shared preflight failed")

        def legacy_runner(**kwargs):  # noqa: ANN003
            legacy_calls["n"] += 1
            return {"total": 0, "items": []}

        with patch(
            "app.openclaw_integration.service.execute_shared_capability",
            side_effect=boom_shared,
        ), patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=legacy_runner,
        ), patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=legacy_runner,
        ):
            with self.assertRaises(ApiException) as ctx:
                service.execute_capability_in_worker(
                    capability_key="search_entries",
                    raw_payload={"query": "no-fallback"},
                    audit_context=self._audit(),
                    preferred_locale="zh",
                    auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
                )
        self.assertEqual(ctx.exception.code, 40961)
        self.assertEqual(legacy_calls["n"], 0)

    def test_shared_output_validation_failure_does_not_retry(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        calls = {"n": 0}

        def bad_runner(**kwargs):  # noqa: ANN003
            calls["n"] += 1
            # Missing required total/items → external output schema fails after success.
            return {"unexpected": True}

        with patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=bad_runner,
        ), patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=bad_runner,
        ):
            with self.assertRaises(ApiException) as ctx:
                service.execute_capability_in_worker(
                    capability_key="search_entries",
                    raw_payload={"query": "bad-output"},
                    audit_context=self._audit(),
                    preferred_locale="zh",
                    auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
                )
        self.assertIn(ctx.exception.code, {42261, 40961})
        self.assertEqual(calls["n"], 1)

    def test_request_cancellation_before_dispatch(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)

        class Cancelled:
            def is_cancelled(self) -> bool:
                return True

            def raise_if_cancelled(self) -> None:
                from app.assistant.capabilities.contracts import CapabilityError

                raise CapabilityError(
                    error_type="cancelled",
                    safe_code="cancelled",
                    safe_message="cancelled",
                    retry_disposition="never",
                )

        with self.assertRaises(ApiException) as ctx:
            service.execute_capability_in_worker(
                capability_key="search_entries",
                raw_payload={"query": "cancel"},
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
                cancellation=Cancelled(),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40961)

    # ------------------------------------------------------------------
    # Availability / version race semantics
    # ------------------------------------------------------------------

    def test_catalog_disable_before_admission_denies_shared(self) -> None:
        """Re-verification before admission detects prior catalog disable."""
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        item = (
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "search_entries")
            .one()
        )
        frozen = freeze_openclaw_capability_call(
            service,
            item=item,
            call_id="call-catalog-disable",
        )
        ceiling = _select_effect_ceiling(item)
        from app.assistant.capabilities.contracts import (
            CapabilityAuthorizationEvidence,
            CapabilityAvailability,
            CapabilityBehavior,
            CapabilityDescriptor,
            CapabilityExecutionContext,
            CapabilityOwnerRef,
            CapabilityPrincipal,
            CapabilityTimeoutPolicy,
            ClassificationContractRef,
        )
        from app.assistant.capabilities.policy import grant_source_digest_for_ceiling
        from app.assistant.domain.contracts import CapabilityCompletionContract
        from app.assistant.domain.json_schema import binding_schema_digest

        grant = grant_source_digest_for_ceiling(
            ceiling, exposure_digest=frozen.catalog_item_revision_digest
        )
        verifier = OpenClawAuthorizationEvidenceVerifier(
            expected_call_id=frozen.call_id,
            frozen_call=frozen,
            auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            ceiling=ceiling,
            grant_source_digest=grant,
            service=service,
            locale="zh",
        )
        # Disable after freeze, before admission.
        item.enabled = False
        self.db.flush()

        in_schema = frozen.binding.resolved.input_schema
        out_schema = frozen.binding.resolved.output_schema
        descriptor = CapabilityDescriptor(
            capability_key=item.capability_key,
            capability_type="tool",
            target_identity=f"system-tool:{item.capability_key}",
            target_id=None,
            target_version_id=None,
            target_revision=None,
            resolution_digest=frozen.binding.resolved.resolution_digest,
            binding_contract_digest=frozen.binding.resolved.binding_contract_digest,
            dependency_closure_digest=frozen.binding.resolved.dependency_closure_digest,
            display_name=item.capability_key,
            description="parity",
            input_schema=in_schema,
            output_schema=out_schema,
            input_schema_digest=binding_schema_digest(in_schema),
            output_schema_digest=binding_schema_digest(out_schema),
            descriptor_digest="a" * 64,
            executable_revision="plan02-task9-local",
            behavior=CapabilityBehavior(
                classification=ClassificationContractRef(
                    schema_version=1,
                    revision=CLASSIFICATION_CONTRACT_REVISION,
                    ruleset_digest=CLASSIFICATION_RULESET_DIGEST,
                ),
                side_effect="read",
                parallel_safe=True,
                interrupt_mode="none",
                timeout_policy=CapabilityTimeoutPolicy(
                    mode="cooperative",
                    timeout_seconds=None,
                    cancellation_supported=True,
                ),
                behavior_digest="b" * 64,
            ),
            availability=CapabilityAvailability(status="available", reason_code=None),
            completion=CapabilityCompletionContract(
                terminal_output=True,
                needs_followup=False,
                followup_hint=None,
            ),
        )
        evidence = CapabilityAuthorizationEvidence(
            issuer="openclaw_bridge",
            call_id=frozen.call_id,
            principal=CapabilityPrincipal(
                principal_type="openclaw_installation",
                principal_id="openclaw",
                authenticated=True,
            ),
            entrypoint="openclaw",
            owner=CapabilityOwnerRef(
                owner_kind="openclaw_catalog",
                owner_id=str(item.id),
                owner_version_id=None,
            ),
            capability_key=item.capability_key,
            resolution_digest=frozen.binding.resolved.resolution_digest,
            binding_contract_digest=frozen.binding.resolved.binding_contract_digest,
            dependency_closure_digest=frozen.binding.resolved.dependency_closure_digest,
            allowed_side_effects=ceiling.allowed_side_effects,
            grant_source_digest=grant,
            evidence_digest=frozen.catalog_evidence_digest,
        )
        context = CapabilityExecutionContext(
            call_id=frozen.call_id,
            locale="zh",
            request_source="unit-test",
            request_channel="cli",
            request_session="session-1",
            request_tool="tool-1",
            nesting_depth=0,
        )
        with self.assertRaises(AuthorizationEvidenceVerificationError) as ctx:
            verifier.verify(descriptor=descriptor, evidence=evidence, context=context)
        self.assertEqual(str(ctx.exception), "catalog_item_not_exposed")

    def test_catalog_disable_after_admission_does_not_replay(self) -> None:
        """Once admitted, a later disable affects future calls only (no cancel/replay)."""
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        calls = {"n": 0}

        def fake_runner(**kwargs):  # noqa: ANN003
            calls["n"] += 1
            # Mutate catalog mid-flight after the target has been invoked.
            item = (
                self.db.query(OpenClawCapabilityItem)
                .filter(OpenClawCapabilityItem.capability_key == "search_entries")
                .one()
            )
            item.enabled = False
            self.db.flush()
            return {"total": 1, "items": []}

        with patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=fake_runner,
        ), patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=fake_runner,
        ):
            response = service.execute_capability_in_worker(
                capability_key="search_entries",
                raw_payload={"query": "admitted"},
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            )
        self.assertEqual(response.result["total"], 1)
        self.assertEqual(calls["n"], 1)

        # Future call sees disable and does not re-invoke target.
        with patch(
            "app.assistant.capabilities.adapters.tool.wrap_tool_with_db",
            return_value=fake_runner,
        ), patch(
            "app.assistant.workflow.engine.runtime_helpers.wrap_tool_with_db",
            return_value=fake_runner,
        ):
            with self.assertRaises(ApiException) as ctx:
                service.execute_capability_in_worker(
                    capability_key="search_entries",
                    raw_payload={"query": "after-disable"},
                    audit_context=self._audit(),
                    preferred_locale="zh",
                    auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
                )
        self.assertEqual(ctx.exception.code, 40362)
        self.assertEqual(calls["n"], 1)

    def test_build_revision_drift_fails_closed_on_shared(self) -> None:
        """Frozen system-tool binding fails closed when APP_BUILD_REVISION drifts."""
        # Pin a known build revision for this case so full-suite ordering cannot
        # leave a different APP_BUILD_REVISION from another module's setdefault.
        os.environ["APP_BUILD_REVISION"] = "plan02-task9-local"
        get_settings.cache_clear()
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)
        item = (
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "search_entries")
            .one()
        )
        # Freeze under the current build revision.
        frozen = freeze_openclaw_capability_call(
            service,
            item=item,
            call_id="call-build-drift",
        )
        original_revision = frozen.binding.resolved.executable_revision
        self.assertTrue(original_revision)
        self.assertNotEqual(original_revision, "drifted-build-revision")

        # Drift process build revision, then re-resolve the frozen binding surface.
        from app.assistant.capabilities.errors import CapabilityDomainError
        from app.assistant.capabilities.registry import CapabilityRegistry

        with patch.dict(os.environ, {"APP_BUILD_REVISION": "drifted-build-revision"}):
            get_settings.cache_clear()
            with self.assertRaises(CapabilityDomainError) as ctx:
                CapabilityRegistry(self.db).resolve_surface(frozen.binding)
        self.assertEqual(ctx.exception.error.error_type, "version_drift")
        self.assertEqual(ctx.exception.error.safe_code, "build_revision_drift")
        get_settings.cache_clear()
        # Restore local pin for subsequent tests in this module.
        os.environ["APP_BUILD_REVISION"] = "plan02-task9-local"

    # ------------------------------------------------------------------
    # System item coverage + ceilings + code_executor
    # ------------------------------------------------------------------

    def test_system_items_match_effect_ceilings_and_parity_table(self) -> None:
        definitions = list_openclaw_system_item_definitions(locale="en")
        definition_keys = {item.key for item in definitions}
        ceiling_keys = set(OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS)
        parity_keys = {row["capability_key"] for row in _TOOL_PARITY_FIXTURES}
        workflow_system_keys = {
            item.key for item in definitions if item.source_type == "workflow"
        }

        self.assertEqual(definition_keys, ceiling_keys)
        # Every tool system item must appear in the shared characterization table.
        tool_keys = {item.key for item in definitions if item.source_type == "tool"}
        self.assertTrue(tool_keys.issubset(parity_keys | definition_keys))
        self.assertEqual(tool_keys, parity_keys)
        # Workflow system items are classified independently; they remain inventory-covered.
        self.assertEqual(
            workflow_system_keys,
            {"submit_context_capture", "generate_periodic_review"},
        )
        for key, ceiling in OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.items():
            self.assertEqual(ceiling.revision, "plan02-v1", key)
            self.assertNotIn("unknown", ceiling.allowed_side_effects)
            self.assertNotIn("durable", ceiling.allowed_interrupt_modes)
            self.assertEqual(len(ceiling.ceiling_digest), 64, key)

    def test_classifier_and_ceiling_are_independent(self) -> None:
        """Ceiling rows are static grants; classifier output is not stored in them."""
        for key, ceiling in OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.items():
            # Ceiling is a lattice prefix, not a single classified value.
            self.assertGreaterEqual(len(ceiling.allowed_side_effects), 1, key)
            # Independent of classifier digest.
            self.assertNotEqual(ceiling.ceiling_digest, CLASSIFICATION_RULESET_DIGEST, key)
        self.assertEqual(CLASSIFICATION_CONTRACT_REVISION, "plan02-v1")
        self.assertEqual(len(CLASSIFICATION_RULESET_DIGEST), 64)

    def test_negative_ceiling_rows(self) -> None:
        # Effect above ceiling
        read_ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS["search_entries"]
        self.assertNotIn("write_local", read_ceiling.allowed_side_effects)
        # Missing ceiling key
        self.assertIsNone(OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.get("not_a_real_item"))
        # Missing custom inventory type
        self.assertIsNone(OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS.get("skill"))
        # Unknown classification never granted
        for ceiling in OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.values():
            self.assertNotIn("unknown", ceiling.allowed_side_effects)
        # Unapproved durable interrupt never granted; tool custom source rejects legacy_blocking
        tool_ceiling = OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS["tool"]
        self.assertNotIn("legacy_blocking", tool_ceiling.allowed_interrupt_modes)
        self.assertNotIn("durable", tool_ceiling.allowed_interrupt_modes)
        # Workflow may allow legacy_blocking but not durable
        workflow_ceiling = OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS["workflow"]
        self.assertIn("legacy_blocking", workflow_ceiling.allowed_interrupt_modes)
        self.assertNotIn("durable", workflow_ceiling.allowed_interrupt_modes)
        # Lattice helper remains strict
        self.assertEqual(lattice_prefix_through("read")[-1], "read")

    def test_code_executor_system_workflows_are_unavailable_in_shared(self) -> None:
        """Plan 02 v1: code_executor closures classify unknown; shared preflight fails.

        Only submit_context_capture carries code_executor nodes among system
        OpenClaw workflows. generate_periodic_review classifies shared-ready
        (read) and is covered by the integration suite with mocked engines.
        """
        self._initialize_system()
        self._rotate_and_enable()
        service = OpenClawIntegrationService(self.db)

        from app.assistant.capabilities.runtime import build_capability_runtime
        from app.openclaw_integration.capability_adapter import freeze_openclaw_capability_call

        item = (
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "submit_context_capture")
            .one()
        )
        frozen = freeze_openclaw_capability_call(
            service,
            item=item,
            call_id="call-code-executor",
        )
        descriptor = build_capability_runtime(
            db=self.db, evidence_verifiers={}, locale="zh"
        ).describe(frozen.binding)
        self.assertEqual(descriptor.behavior.side_effect, "unknown")

        with self.assertRaises(ApiException) as shared_ctx:
            service.execute_capability_in_worker(
                capability_key="submit_context_capture",
                raw_payload={"context": "remember this for parity"},
                audit_context=self._audit(),
                preferred_locale="zh",
                auth_proof=OpenClawAuthenticationProof(principal_id="openclaw"),
            )
        self.assertEqual(shared_ctx.exception.status_code, 409)
        self.assertEqual(shared_ctx.exception.code, 40961)

        # Periodic review has no code_executor; classification is not unknown.
        review_item = (
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "generate_periodic_review")
            .one()
        )
        review_frozen = freeze_openclaw_capability_call(
            service,
            item=review_item,
            call_id="call-review",
        )
        review_desc = build_capability_runtime(
            db=self.db, evidence_verifiers={}, locale="zh"
        ).describe(review_frozen.binding)
        self.assertNotEqual(review_desc.behavior.side_effect, "unknown")

    def test_missing_system_ceiling_raises_invalid_source(self) -> None:
        self._initialize_system()
        self._rotate_and_enable()
        item = (
            self.db.query(OpenClawCapabilityItem)
            .filter(OpenClawCapabilityItem.capability_key == "search_entries")
            .one()
        )
        # Synthesize a system item with an unknown default key.
        item.system_default_key = "brand_new_unclassified_item"
        item.is_system_item = True
        self.db.flush()
        with self.assertRaises(ApiException) as ctx:
            _select_effect_ceiling(item)
        self.assertEqual(ctx.exception.code, 42262)

    def test_router_does_not_snapshot_runtime_mode(self) -> None:
        """HTTP path no longer freezes a dual-mode selector; worker has no mode field."""
        from app.openclaw_integration.runtime_worker import OpenClawCapabilityWorkerRequest

        self._initialize_system()
        secret = self._rotate_and_enable()
        captured: dict[str, Any] = {}

        async def fake_worker(req: OpenClawCapabilityWorkerRequest, **kwargs):  # noqa: ANN003
            captured["has_selected_mode"] = hasattr(req, "selected_mode")
            from app.openclaw_integration.schemas import OpenClawCapabilityExecuteResponse

            return OpenClawCapabilityExecuteResponse(
                capability_key="search_entries",
                tool_name="mindatlas_search_entries",
                result={"total": 0, "items": []},
            )

        with patch(
            "app.openclaw_integration.router.execute_openclaw_capability_in_worker",
            side_effect=fake_worker,
        ):
            response = self.client.post(
                "/api/integrations/openclaw/capabilities/search_entries/execute",
                headers=self._auth_headers(secret),
                json={"query": "router-shared-only"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(captured.get("has_selected_mode", True))


if __name__ == "__main__":
    unittest.main()
