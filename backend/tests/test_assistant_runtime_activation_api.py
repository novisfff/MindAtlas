"""HTTP API tests for assistant runtime prepare/activate/kill-switch (Task 6)."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
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
os.environ.setdefault("APP_BUILD_REVISION", "test-build-activation-api-task6")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-activation-api-task6"
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


def _csrf_headers(client: TestClient, **extra: str) -> dict[str, str]:
    from app.operator_auth.constants import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf, "expected CSRF cookie after login"
    return _origin_headers(**{CSRF_HEADER_NAME: csrf, **extra})


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


def _bootstrap_prepared(db: Session, *, register_worker: bool = True) -> dict[str, Any]:
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
    if register_worker:
        now = utcnow()
        db.add(
            AssistantWorkerRegistration(
                worker_id=f"api-worker-{uuid4().hex[:8]}",
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
                hostname_label="api-test",
            )
        )
    db.commit()
    return {
        "account": account,
        "model": model,
        "prepared": prepared,
        "revision_id": prepared.rollout_revision_id,
    }


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
    from app.assistant.runtime.router import router as runtime_router
    from app.common.exceptions import register_exception_handlers
    from app.config import get_settings
    from app.database import get_db
    from app.operator_auth.router import login_router, protected_operator_auth_router
    from app.operator_auth.route_policy import (
        credential_exchange_router,
        protected_browser_router,
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

    _cred = credential_exchange_router()
    _cred.include_router(login_router)
    _prot = protected_browser_router()
    _prot.include_router(protected_operator_auth_router)
    _prot.include_router(runtime_router)
    app.include_router(_cred)
    app.include_router(_prot)

    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def operator_client(api_app, db: Session):
    state = _bootstrap_prepared(db)
    client = TestClient(api_app)
    response = client.post(
        "/api/operator-auth/login",
        json={"password": PASSWORD},
        headers=_origin_headers(),
    )
    assert response.status_code == 200, response.text
    client.headers.update(_csrf_headers(client))
    return client, state


def _activate_body(
    *, expected: int = 0, request_id: str | None = None, reason: str = "activate"
) -> dict:
    return {
        "expectedControlRevision": expected,
        "requestId": request_id or str(uuid4()),
        "reason": reason,
    }


def test_activation_requires_operator_and_csrf(api_app, db: Session):
    """Unauthenticated and CSRF-missing mutations are rejected."""
    state = _bootstrap_prepared(db)
    client = TestClient(api_app)
    revision_id = state["revision_id"]

    response = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        headers=_origin_headers(),
        json=_activate_body(),
    )
    assert response.status_code in {401, 403}

    login = client.post(
        "/api/operator-auth/login",
        json={"password": PASSWORD},
        headers=_origin_headers(),
    )
    assert login.status_code == 200, login.text
    # Cookies present but CSRF header absent → 403
    response = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        headers=_origin_headers(),
        json=_activate_body(),
    )
    assert response.status_code == 403


def test_activation_response_excludes_sensitive_closure(operator_client):
    client, state = operator_client
    revision_id = state["revision_id"]
    response = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        json=_activate_body(expected=0, reason="activate initial runtime"),
    )
    assert response.status_code == 200, response.text
    serialized = response.text.lower()
    for fragment in ("prompt", "credential", "api_key", "packageclosurejson"):
        assert fragment not in serialized
    body = response.json()
    data = body.get("data") or body
    assert data["activeRolloutRevisionId"] == str(revision_id)
    assert data["controlRevision"] == 1
    assert data["newRunsEnabled"] is True


def test_activation_http_replay_is_idempotent(operator_client):
    client, state = operator_client
    revision_id = state["revision_id"]
    request_id = str(uuid4())
    body = _activate_body(expected=0, request_id=request_id, reason="activate")
    first = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        json=body,
    )
    second = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        json=body,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


def test_list_rollouts_viewer_ok(operator_client):
    client, state = operator_client
    response = client.get("/api/assistant-runtime/rollouts")
    assert response.status_code == 200
    data = response.json().get("data") or response.json()
    assert "control" in data
    assert "revisions" in data
    assert any(
        item["rolloutRevisionId"] == str(state["revision_id"])
        for item in data["revisions"]
    )
    blob = response.text.lower()
    assert "packageclosurejson" not in blob
    assert "prompt" not in blob


def test_rollout_activation_readiness_is_viewer_safe(operator_client):
    client, state = operator_client
    revision_id = state["revision_id"]

    response = client.get(
        f"/api/assistant-runtime/rollouts/{revision_id}/activation-readiness"
    )

    assert response.status_code == 200, response.text
    data = response.json().get("data") or response.json()
    assert data["rolloutRevisionId"] == str(revision_id)
    assert data["compatibleWorkerIds"]
    assert "activeRolloutRevisionId" not in data
    blob = response.text.lower()
    for fragment in ("prompt", "credential", "api_key", "packageclosurejson"):
        assert fragment not in blob


def test_set_new_runs_via_http(operator_client):
    client, state = operator_client
    revision_id = state["revision_id"]
    act = client.post(
        f"/api/assistant-runtime/rollouts/{revision_id}/activate",
        json=_activate_body(expected=0),
    )
    assert act.status_code == 200
    response = client.post(
        "/api/assistant-runtime/new-runs",
        json={
            "enabled": False,
            "expectedControlRevision": 1,
            "requestId": str(uuid4()),
            "reason": "emergency pause",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json().get("data") or response.json()
    assert data["newRunsEnabled"] is False
    assert data["controlRevision"] == 2
