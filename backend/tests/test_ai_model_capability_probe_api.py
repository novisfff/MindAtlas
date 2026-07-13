"""API contract tests for Plan 03 live capability probe endpoints."""

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.ai_provider.crypto import encrypt_api_key  # noqa: E402
from app.ai_registry.models import AiCredential, AiModel, AiModelCapabilityProbe  # noqa: E402
from app.ai_registry.router import model_router  # noqa: E402
from app.ai_registry.service import (  # noqa: E402
    AiModelCapabilityProbeService,
    LiveProbeResult,
    _PROBE_FLIGHT_LOCK,
    _PROBE_IN_FLIGHT,
    _ProbeConfigSnapshot,
)
from app.assistant.provider_loop.adapters.openai_chat import ADAPTER_KEY  # noqa: E402
from app.assistant.provider_loop.probe import (  # noqa: E402
    CapabilityObservation,
    ModelCapabilityObservations,
    ModelCapabilityProbeEvidence,
    compute_probe_digest,
)
from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.database import get_db  # noqa: E402
from app.common.ssrf import normalize_openai_base_url  # noqa: E402
from app.assistant.provider_loop.probe import (  # noqa: E402
    build_endpoint_identity,
    build_model_config_digest,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
APP_BUILD = "plan03-task8-local"
ADAPTER_REVISION = "1"
API_KEY = "sk-live-secret-DO-NOT-LEAK"
BASE_URL = "https://api.example.com/v1"


def _real_snap(model, cred):
    endpoint = build_endpoint_identity(normalize_openai_base_url(cred.base_url))
    digest = build_model_config_digest(
        model_id=model.id,
        model_name=model.name,
        model_type=model.model_type,
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=cred.id,
        credential_runtime_revision=int(cred.runtime_revision or 1),
        endpoint_identity=endpoint,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    )
    return _ProbeConfigSnapshot(
        model_id=model.id,
        model_name=model.name,
        model_type=model.model_type,
        model_runtime_revision=int(model.runtime_revision or 1),
        credential_id=cred.id,
        credential_runtime_revision=int(cred.runtime_revision or 1),
        base_url=normalize_openai_base_url(cred.base_url),
        model_config_digest=digest,
        endpoint_identity=dict(endpoint),
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        app_build_revision=APP_BUILD,
    ), digest


def _caps_passed() -> ModelCapabilityObservations:
    item = CapabilityObservation(observation="passed")
    return ModelCapabilityObservations(
        streaming=item,
        tool_calling=item,
        json_schema_args=item,
        stable_tool_call_ids=item,
        multi_tool_calls=item,
        tool_result_continuation=item,
        tools_disabled_finalization=item,
    )


def _evidence(status: str = "passed", digest: str | None = None) -> ModelCapabilityProbeEvidence:
    if digest is None:
        raise ValueError("digest required")
    caps = _caps_passed()
    if status == "failed":
        caps = ModelCapabilityObservations(
            streaming=CapabilityObservation(observation="not_observed"),
            tool_calling=CapabilityObservation(observation="not_observed"),
            json_schema_args=CapabilityObservation(observation="not_observed"),
            stable_tool_call_ids=CapabilityObservation(observation="not_observed"),
            multi_tool_calls=CapabilityObservation(observation="not_observed"),
            tool_result_continuation=CapabilityObservation(observation="not_observed"),
            tools_disabled_finalization=CapabilityObservation(observation="not_observed"),
        )
    elif status == "partial":
        caps = ModelCapabilityObservations(
            streaming=CapabilityObservation(observation="passed"),
            tool_calling=CapabilityObservation(observation="failed", safe_reason_code="no_tool"),
            json_schema_args=CapabilityObservation(observation="not_observed"),
            stable_tool_call_ids=CapabilityObservation(observation="not_observed"),
            multi_tool_calls=CapabilityObservation(observation="not_observed"),
            tool_result_continuation=CapabilityObservation(observation="not_observed"),
            tools_disabled_finalization=CapabilityObservation(observation="not_observed"),
        )
    code = "provider_error" if status == "failed" else None
    summary = "provider probe failed" if status == "failed" else None
    probe_digest = compute_probe_digest(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        status=status,  # type: ignore[arg-type]
        capabilities=caps,
        compatibility_warnings=(),
        safe_error_code=code,
        safe_error_summary=summary,
    )
    return ModelCapabilityProbeEvidence(
        probe_contract_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=digest,
        status=status,  # type: ignore[arg-type]
        capabilities=caps,
        probe_digest=probe_digest,
        safe_error_code=code,
        safe_error_summary=summary,
    )


class CapabilityProbeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        with _PROBE_FLIGHT_LOCK:
            _PROBE_IN_FLIGHT.clear()
        self.db = make_session()
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(model_router)

        def _override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_get_db
        self.app = app
        self.client = TestClient(app)
        self.cred = AiCredential(
            name=f"cred-{uuid4().hex[:8]}",
            base_url=BASE_URL,
            api_key_encrypted=encrypt_api_key(API_KEY),
            api_key_hint="****",
            runtime_revision=1,
        )
        self.db.add(self.cred)
        self.db.flush()
        self.model = AiModel(
            credential_id=self.cred.id,
            name="gpt-test-probe",
            model_type="llm",
            runtime_revision=1,
        )
        self.db.add(self.model)
        self.db.commit()
        self.db.refresh(self.model)

    def tearDown(self) -> None:
        with _PROBE_FLIGHT_LOCK:
            _PROBE_IN_FLIGHT.clear()
        self.db.close()

    def _post(self, body: dict, model_id=None):
        mid = model_id or self.model.id
        return self.client.post(f"/api/ai-models/{mid}/capability-probe", json=body)

    def _get(self, **params):
        return self.client.get(
            f"/api/ai-models/{self.model.id}/capability-probes", params=params
        )

    def test_feature_gate_disabled_before_decrypt_provider_flight(self) -> None:
        decrypt_calls: list[str] = []
        runner_calls: list[int] = []

        def boom_decrypt(*_a, **_k):
            decrypt_calls.append("x")
            raise AssertionError("decrypt must not run when disabled")

        def boom_runner(**_k):
            runner_calls.append(1)
            raise AssertionError("provider must not run when disabled")

        with (
            patch("app.ai_registry.service.decrypt_api_key", side_effect=boom_decrypt),
            patch.object(
                AiModelCapabilityProbeService,
                "run_live_probe",
                wraps=AiModelCapabilityProbeService(
                    self.db, enabled=False, provider_runner=boom_runner
                ).run_live_probe,
            ),
        ):
            # Route constructs service from settings default (disabled).
            resp = self._post(
                {
                    "adapterKey": ADAPTER_KEY,
                    "confirmProviderCall": True,
                    "promote": True,
                }
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], 40380)
        self.assertEqual(decrypt_calls, [])
        self.assertEqual(runner_calls, [])

    def test_missing_confirmation_rejected(self) -> None:
        with patch(
            "app.ai_registry.service.get_settings"
        ) as gs:
            settings = gs.return_value
            settings.ai_model_capability_probe_enabled = True
            settings.app_build_revision = APP_BUILD
            resp = self._post(
                {
                    "adapterKey": ADAPTER_KEY,
                    "confirmProviderCall": False,
                    "promote": True,
                }
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 40080)

    def test_unknown_model_404(self) -> None:
        with patch("app.ai_registry.service.get_settings") as gs:
            gs.return_value.ai_model_capability_probe_enabled = True
            gs.return_value.app_build_revision = APP_BUILD
            resp = self._post(
                {
                    "adapterKey": ADAPTER_KEY,
                    "confirmProviderCall": True,
                },
                model_id=uuid4(),
            )
        self.assertEqual(resp.status_code, 404)

    def test_non_llm_model_rejected(self) -> None:
        emb = AiModel(
            credential_id=self.cred.id,
            name="embed",
            model_type="embedding",
            runtime_revision=1,
        )
        self.db.add(emb)
        self.db.commit()
        with patch("app.ai_registry.service.get_settings") as gs:
            gs.return_value.ai_model_capability_probe_enabled = True
            gs.return_value.app_build_revision = APP_BUILD
            resp = self._post(
                {"adapterKey": ADAPTER_KEY, "confirmProviderCall": True},
                model_id=emb.id,
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 40082)

    def test_adapter_key_validation(self) -> None:
        with patch("app.ai_registry.service.get_settings") as gs:
            gs.return_value.ai_model_capability_probe_enabled = True
            gs.return_value.app_build_revision = APP_BUILD
            resp = self._post(
                {"adapterKey": "not_supported", "confirmProviderCall": True}
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 40081)

    def test_passed_partial_failed_and_promote(self) -> None:
        for status, promote, expected_outcome in (
            ("passed", True, "promoted"),
            ("partial", True, "promoted"),
            ("failed", True, "promoted"),
            ("passed", False, "not_requested"),
        ):
            with _PROBE_FLIGHT_LOCK:
                _PROBE_IN_FLIGHT.clear()
            # Reset pointer between iterations for clean promote assertions.
            self.model.current_capability_probe_id = None
            self.db.commit()
            snap, digest = _real_snap(self.model, self.cred)
            calls = {"n": 0}

            def runner(**_k):
                calls["n"] += 1
                return _evidence(status=status, digest=digest)

            with patch("app.ai_registry.service.get_settings") as gs:
                gs.return_value.ai_model_capability_probe_enabled = True
                gs.return_value.app_build_revision = APP_BUILD
                original_init = AiModelCapabilityProbeService.__init__

                def patched_init(self, db, **kwargs):
                    kwargs.setdefault("enabled", True)
                    kwargs.setdefault("provider_runner", runner)
                    kwargs.setdefault("app_build_revision", APP_BUILD)
                    kwargs.setdefault("adapter_revision", ADAPTER_REVISION)
                    return original_init(self, db, **kwargs)

                with patch.object(AiModelCapabilityProbeService, "__init__", patched_init):
                    with patch.object(
                        AiModelCapabilityProbeService,
                        "_snapshot_locked_config",
                        return_value=snap,
                    ), patch(
                        "app.ai_registry.service.validate_url_ssrf",
                        return_value=None,
                    ):
                        resp = self._post(
                            {
                                "adapterKey": ADAPTER_KEY,
                                "confirmProviderCall": True,
                                "promote": promote,
                            }
                        )
            self.assertEqual(resp.status_code, 200, resp.text)
            data = resp.json()["data"]
            self.assertEqual(data["status"], status)
            self.assertEqual(data["promotionOutcome"], expected_outcome)
            self.assertEqual(calls["n"], 1)
            blob = json.dumps(data)
            self.assertNotIn(API_KEY, blob)
            self.assertNotIn("api.example.com", blob)
            self.assertNotIn("nonce", blob.lower())
            self.assertNotIn("prompt", blob.lower())

    def test_provider_called_once_per_request(self) -> None:
        snap, digest = _real_snap(self.model, self.cred)
        calls = {"n": 0}

        def runner(**_k):
            calls["n"] += 1
            return _evidence(digest=digest)

        with patch("app.ai_registry.service.get_settings") as gs:
            gs.return_value.ai_model_capability_probe_enabled = True
            gs.return_value.app_build_revision = APP_BUILD
            original_init = AiModelCapabilityProbeService.__init__

            def patched_init(self, db, **kwargs):
                kwargs.update(
                    enabled=True,
                    provider_runner=runner,
                    app_build_revision=APP_BUILD,
                    adapter_revision=ADAPTER_REVISION,
                )
                return original_init(self, db, **kwargs)

            with patch.object(AiModelCapabilityProbeService, "__init__", patched_init), patch.object(
                AiModelCapabilityProbeService,
                "_snapshot_locked_config",
                return_value=snap,
            ), patch("app.ai_registry.service.validate_url_ssrf", return_value=None):
                r1 = self._post({"adapterKey": ADAPTER_KEY, "confirmProviderCall": True})
                r2 = self._post({"adapterKey": ADAPTER_KEY, "confirmProviderCall": True})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(calls["n"], 2)
        self.assertNotEqual(r1.json()["data"]["id"], r2.json()["data"]["id"])

    def test_concurrent_same_process_rejected(self) -> None:
        snap, digest = _real_snap(self.model, self.cred)
        started = threading.Event()
        release = threading.Event()
        results: list[int] = []
        calls = {"n": 0}
        lock = threading.Lock()

        def runner(**_k):
            with lock:
                calls["n"] += 1
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("release not signaled")
            return _evidence(digest=digest)

        def do_post():
            try:
                svc = AiModelCapabilityProbeService(
                    self.db,
                    enabled=True,
                    provider_runner=runner,
                    app_build_revision=APP_BUILD,
                    adapter_revision=ADAPTER_REVISION,
                )
                with patch.object(
                    AiModelCapabilityProbeService,
                    "_snapshot_locked_config",
                    return_value=snap,
                ), patch("app.ai_registry.service.validate_url_ssrf", return_value=None):
                    svc.run_live_probe(
                        self.model.id,
                        adapter_key=ADAPTER_KEY,
                        confirm_provider_call=True,
                        promote=True,
                    )
                results.append(200)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "status_code", 500)
                results.append(int(code))

        t1 = threading.Thread(target=do_post)
        t1.start()
        self.assertTrue(started.wait(timeout=5))
        # Second call while first is in flight should be rejected without second provider call.
        do_post()
        release.set()
        t1.join(timeout=10)
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(calls["n"], 1)

    def test_url_userinfo_rejected_before_decrypt(self) -> None:
        self.cred.base_url = "https://user:pass@api.example.com/v1"
        self.db.commit()
        decrypt_calls: list[int] = []

        def track_decrypt(*_a, **_k):
            decrypt_calls.append(1)
            return API_KEY

        with patch("app.ai_registry.service.get_settings") as gs:
            gs.return_value.ai_model_capability_probe_enabled = True
            gs.return_value.app_build_revision = APP_BUILD
            with patch("app.ai_registry.service.decrypt_api_key", side_effect=track_decrypt):
                original_init = AiModelCapabilityProbeService.__init__

                def patched_init(self, db, **kwargs):
                    kwargs.update(enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION)
                    return original_init(self, db, **kwargs)

                with patch.object(AiModelCapabilityProbeService, "__init__", patched_init):
                    resp = self._post(
                        {"adapterKey": ADAPTER_KEY, "confirmProviderCall": True}
                    )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], 40083)
        self.assertEqual(decrypt_calls, [])

    def test_get_history_bounds_markers_and_safe_fields(self) -> None:
        svc = AiModelCapabilityProbeService(
            self.db, enabled=True, app_build_revision=APP_BUILD, adapter_revision=ADAPTER_REVISION
        )
        snap, digest = _real_snap(self.model, self.cred)
        r1 = svc._persist_evidence(
            model_id=self.model.id,
            original_snapshot=snap,
            evidence=_evidence(digest=digest),
            promote=True,
        )
        r2 = svc._persist_evidence(
            model_id=self.model.id,
            original_snapshot=snap,
            evidence=_evidence(digest=digest),
            promote=False,
        )
        resp = self._get(limit=1, offset=0)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], str(r2.probe.id))
        self.assertFalse(data[0]["isCurrent"])
        self.assertIn("isStaleForCurrentConfig", data[0])
        self.assertIn("probeDigest", data[0])
        blob = json.dumps(data)
        self.assertNotIn(API_KEY, blob)
        self.assertNotIn(BASE_URL, blob)

        bad = self._get(limit=0)
        self.assertEqual(bad.status_code, 422)

        missing = self.client.get(f"/api/ai-models/{uuid4()}/capability-probes")
        self.assertEqual(missing.status_code, 404)

        all_resp = self._get(limit=10, offset=0)
        ids = [item["id"] for item in all_resp.json()["data"]]
        self.assertEqual(ids, [str(r2.probe.id), str(r1.probe.id)])
        self.assertTrue(all_resp.json()["data"][1]["isCurrent"])


if __name__ == "__main__":
    unittest.main()
