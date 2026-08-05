"""HTTP split of process /health vs assistant /ready (Plan 2 Task 10)."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-health-ready-task10")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-health-ready-task10"
PASSWORD = "correct horse battery"
_ORIGIN = "http://localhost:5173"
_KEY_ID = "k1"
_KEY_BYTES = bytes([31]) * 32


def _encoded_keys(active: str = _KEY_ID, material: bytes = _KEY_BYTES) -> str:
    return json.dumps({active: base64.b64encode(material).decode("ascii")})


def _origin_headers(**extra: str) -> dict[str, str]:
    headers = {
        "Origin": _ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }
    headers.update(extra)
    return headers


@pytest.fixture
def db() -> Iterator[Session]:
    reset_caches()
    from app.config import get_settings

    get_settings.cache_clear()
    os.environ["ASSISTANT_NEW_RUNS_ENABLED"] = "true"
    os.environ["APP_BUILD_REVISION"] = BUILD
    get_settings.cache_clear()
    session = make_session()
    try:
        yield session
    finally:
        session.close()
        reset_caches()
        get_settings.cache_clear()


def _patch_schema_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.runtime import readiness as readiness_mod

    class _AlwaysCompatible:
        def is_compatible(self, db):  # noqa: ANN001
            return True

    monkeypatch.setattr(
        readiness_mod,
        "Plan2AlembicHeadCompatibility",
        _AlwaysCompatible,
    )


def _bootstrap_ready(db: Session) -> dict[str, Any]:
    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
    from app.assistant.durable.models import AssistantWorkerRegistration
    from app.assistant.durable.worker_registry import (
        RUNTIME_CONTRACT_VERSION,
        default_capability_feature_digest,
    )
    from app.assistant.runtime.bootstrap import (
        AssistantSystemBootstrapper,
        StageAssistantBootstrapRequest,
    )
    from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.common.time import utcnow
    from app.operator_auth.repository import OperatorRepository
    from app.system_settings.initialization_service import (
        SYSTEM_INITIALIZATION_STATE_KEY,
    )
    from app.system_settings.models import AppSetting

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    account = OperatorRepository(db).seed_account(
        password=PASSWORD, role="operator", enabled=True
    )
    _cred, model, _binding = create_default_model_binding(db)
    prepared = bootstrapper.stage_bootstrap(
        StageAssistantBootstrapRequest(
            operator_id=account.id,
            operator_session_id=None,
            model_id=model.id,
            build_revision=BUILD,
            fresh_permit=permit,
        )
    )
    existing = (
        db.query(AppSetting)
        .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
        .one_or_none()
    )
    if existing is None:
        db.add(
            AppSetting(
                key=SYSTEM_INITIALIZATION_STATE_KEY,
                value_json={
                    "initialized": True,
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                    "locale": "en",
                    "version": 1,
                    "source": "test",
                },
            )
        )
    control = db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
    if control is None:
        from app.assistant.runtime.repository import AssistantRuntimeRepository

        control = AssistantRuntimeRepository(db).get_or_create_control_for_update()
    control.active_rollout_revision_id = prepared.rollout_revision_id
    control.state_revision = max(int(control.state_revision or 0), 1)
    control.new_runs_enabled = True
    now = utcnow()
    db.add(
        AssistantWorkerRegistration(
            worker_id=f"health-worker-{uuid4().hex[:8]}",
            app_build_revision=BUILD,
            runtime_contract_version=RUNTIME_CONTRACT_VERSION,
            supported_checkpoint_codec_versions=[
                1,
                2,
                int(CURRENT_CHECKPOINT_CODEC_VERSION),
            ],
            capability_feature_digest=default_capability_feature_digest(),
            started_at=now,
            heartbeat_at=now,
            draining_at=None,
            hostname_label="health-api-test",
        )
    )
    db.commit()
    return {
        "account": account,
        "model": model,
        "prepared": prepared,
        "rollout_id": prepared.rollout_revision_id,
    }


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch):
    from app.config import Settings, get_settings

    reset_caches()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MINDATLAS_CANONICAL_ORIGIN", _ORIGIN)
    monkeypatch.setenv("CORS_ORIGINS", _ORIGIN)
    monkeypatch.setenv("MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID", _KEY_ID)
    monkeypatch.setenv("MINDATLAS_SESSION_HMAC_KEYS", _encoded_keys())
    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    monkeypatch.setenv("ASSISTANT_NEW_RUNS_ENABLED", "true")
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN=_ORIGIN,
        CORS_ORIGINS=_ORIGIN,
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID=_KEY_ID,
        MINDATLAS_SESSION_HMAC_KEYS=_encoded_keys(),
        APP_BUILD_REVISION=BUILD,
        ASSISTANT_NEW_RUNS_ENABLED=True,
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.dependencies.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.router.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.route_policy.get_settings", lambda: settings)
    return settings


@pytest.fixture
def api_app(db: Session, monkeypatch: pytest.MonkeyPatch, auth_settings):
    """Minimal app: public health/ready + credential exchange + runtime router."""
    import app.main as main_mod
    from app.assistant.runtime.router import router as runtime_router
    from app.common.exceptions import register_exception_handlers
    from app.config import get_settings
    from app.database import get_db
    from app.operator_auth.router import login_router, protected_operator_auth_router
    from app.operator_auth.route_policy import (
        credential_exchange_router,
        protected_browser_router,
        public_router,
    )

    _patch_schema_compat(monkeypatch)

    app = FastAPI()
    register_exception_handlers(app, debug=True)

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: auth_settings

    _public = public_router()
    # Bind production handlers. /ready is missing during RED and appears after GREEN.
    health_handler = getattr(main_mod, "health", None)
    ready_handler = getattr(main_mod, "public_ready", None)
    if health_handler is not None:
        _public.add_api_route(
            "/health", health_handler, methods=["GET"], response_model=None
        )
    if ready_handler is not None:
        _public.add_api_route(
            "/ready", ready_handler, methods=["GET"], response_model=None
        )

    _cred = credential_exchange_router()
    _cred.include_router(login_router)
    _prot = protected_browser_router()
    _prot.include_router(protected_operator_auth_router)
    _prot.include_router(runtime_router)
    app.include_router(_public)
    app.include_router(_cred)
    app.include_router(_prot)

    try:
        yield app
    finally:
        app.dependency_overrides.clear()

@pytest.fixture
def client(api_app) -> TestClient:
    return TestClient(api_app)


@pytest.fixture
def session_local_spy(monkeypatch: pytest.MonkeyPatch):
    """Raise if SessionLocal / get_db / readiness construction is touched."""
    from app import database as database_mod
    from app.assistant.runtime import readiness as readiness_mod

    spy = MagicMock(name="SessionLocal")

    def _forbidden_session_local(*_a, **_k):  # noqa: ANN001
        spy(*_a, **_k)
        raise AssertionError("SessionLocal must not be called from /health")

    def _forbidden_get_db():
        spy()
        raise AssertionError("get_db must not be called from /health")
        yield  # pragma: no cover

    def _forbidden_readiness(*_a, **_k):  # noqa: ANN001
        spy(*_a, **_k)
        raise AssertionError("AssistantReadinessService must not be built from /health")

    monkeypatch.setattr(database_mod, "SessionLocal", _forbidden_session_local)
    monkeypatch.setattr(database_mod, "get_db", _forbidden_get_db)
    monkeypatch.setattr(
        readiness_mod, "AssistantReadinessService", _forbidden_readiness
    )
    return spy


@pytest.fixture
def ready_runtime(db: Session, monkeypatch: pytest.MonkeyPatch):
    _patch_schema_compat(monkeypatch)
    return _bootstrap_ready(db)


@pytest.fixture
def viewer_client(api_app, ready_runtime, db: Session) -> TestClient:
    del db  # ensure fixture order seeds runtime first
    client = TestClient(api_app)
    response = client.post(
        "/api/operator-auth/login",
        json={"password": PASSWORD},
        headers=_origin_headers(),
    )
    assert response.status_code == 200, response.text
    return client


def test_health_does_not_open_database(client, session_local_spy):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}
    session_local_spy.assert_not_called()


def test_public_ready_is_safe_when_uninitialized(client):
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["data"] == {
        "ready": False,
        "reasonCodes": ["system_not_initialized"],
    }
    serialized = response.text.lower()
    for fragment in (
        "rolloutrevisionid",
        "profileversionid",
        "modelid",
        "workerid",
        "prompt",
    ):
        assert fragment not in serialized


def test_authenticated_readiness_returns_safe_diagnostics(
    viewer_client, ready_runtime
):
    response = viewer_client.get("/api/assistant-runtime/readiness")
    assert response.status_code == 200
    assert response.json()["data"]["activeRolloutRevisionId"] == str(
        ready_runtime["rollout_id"]
    )


def test_public_ready_is_200_when_runtime_ready(client, ready_runtime):
    del ready_runtime
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body == {"ready": True, "reasonCodes": []}
    serialized = response.text.lower()
    for fragment in (
        "rolloutrevisionid",
        "profileversionid",
        "modelid",
        "compatibleworkerids",
        "prompt",
    ):
        assert fragment not in serialized
