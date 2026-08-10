"""PostgreSQL health/ready transition gate (Plan 2 Task 10).

Requires ``MINDATLAS_TEST_POSTGRES_URL``. With ``MINDATLAS_REQUIRE_POSTGRES=1``
this suite hard-fails instead of skipping.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402

CLEAN_SCHEMA_HEAD = CLEAN_ROOT_REVISION
BUILD = "test-build-health-ready-pg-task10"
PASSWORD = "correct horse battery"

_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_REQUIRE_POSTGRES = os.environ.get("MINDATLAS_REQUIRE_POSTGRES", "").strip() in {
    "1",
    "true",
    "TRUE",
    "yes",
    "YES",
}

if not _POSTGRES_URL and _REQUIRE_POSTGRES:
    pytest.fail(
        "MINDATLAS_TEST_POSTGRES_URL not set while MINDATLAS_REQUIRE_POSTGRES=1; "
        "Plan 2 health/readiness PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 health/readiness PostgreSQL "
        "gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)

def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    import base64
    import json

    os.environ["DATABASE_URL"] = url
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    os.environ.setdefault("APP_ENV", "test")
    os.environ["APP_BUILD_REVISION"] = BUILD
    os.environ["ASSISTANT_NEW_RUNS_ENABLED"] = "true"
    # Production /ready loads the session-MAC ring from settings; provide a
    # disposable test ring so operator_auth_unavailable does not mask rollout.
    os.environ.setdefault("MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID", "k1")
    os.environ.setdefault(
        "MINDATLAS_SESSION_HMAC_KEYS",
        json.dumps({"k1": base64.b64encode(b"k" * 32).decode("ascii")}),
    )
    os.environ.setdefault("MINDATLAS_CANONICAL_ORIGIN", "http://localhost:5173")
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@contextmanager
def _engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _drop_public_schema(engine: Engine) -> None:
    reset_disposable_public_schema(engine)


def _upgrade_to_clean_root() -> None:
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision=BUILD,
    )


def _make_key_ring():
    from app.operator_auth.tokens import SessionMacKeyRing

    return SessionMacKeyRing(active_key_id="k1", keys={"k1": b"k" * 32})


def _settings(**overrides: Any):
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _PostgresRuntime:
    """Drive initialize → worker → activate → kill-switch against public /ready."""

    def __init__(self, db: Session, app: FastAPI) -> None:
        self.db = db
        self.client = TestClient(app)
        self.account = None
        self.model = None
        self.prepared = None
        self.key_ring = _make_key_ring()
        self._settings = _settings()

    def public_ready(self) -> tuple[int, tuple[str, ...]]:
        response = self.client.get("/ready")
        data = response.json().get("data") or {}
        reasons = tuple(data.get("reasonCodes") or ())
        return response.status_code, reasons

    def reason_codes(self) -> tuple[str, ...]:
        from app.assistant.runtime.readiness import AssistantReadinessService
        from app.schema.compatibility import runtime_schema_compatibility

        snap = AssistantReadinessService(
            self.db,
            settings=self._settings,
            schema_compatibility=runtime_schema_compatibility(),
            key_ring=self.key_ring,
        ).evaluate()
        return snap.reason_codes

    def initialize(self) -> None:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
        from app.assistant.runtime.bootstrap import (
            AssistantSystemBootstrapper,
            StageAssistantBootstrapRequest,
        )
        from app.operator_auth.repository import OperatorRepository
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
        )
        from app.system_settings.models import AppSetting

        bootstrapper = AssistantSystemBootstrapper(self.db)
        permit = bootstrapper.lock_and_verify_fresh_preconditions()
        self.account = OperatorRepository(self.db).seed_account(
            password=PASSWORD, role="operator", enabled=True
        )
        cred = AiCredential(
            name=f"cred-{uuid.uuid4().hex[:8]}",
            base_url="https://api.example.com/v1",
            api_key_encrypted="enc-test-key-not-secret",
            api_key_hint="****test",
            runtime_revision=1,
        )
        self.db.add(cred)
        self.db.flush()
        self.model = AiModel(
            credential_id=cred.id,
            name="gpt-test",
            model_type="llm",
            runtime_revision=1,
        )
        self.db.add(self.model)
        self.db.flush()
        self.db.add(
            AiComponentBinding(component="assistant", llm_model_id=self.model.id)
        )
        self.db.flush()
        self.prepared = bootstrapper.stage_bootstrap(
            StageAssistantBootstrapRequest(
                operator_id=self.account.id,
                operator_session_id=None,
                model_id=self.model.id,
                build_revision=BUILD,
                fresh_permit=permit,
            )
        )
        self.db.add(
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
        self.db.commit()

    def register_compatible_worker(self) -> None:
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.common.time import utcnow

        now = utcnow()
        self.db.add(
            AssistantWorkerRegistration(
                worker_id=f"pg-ready-worker-{uuid.uuid4().hex[:8]}",
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
                hostname_label="pg-ready-test",
            )
        )
        self.db.commit()

    def activate(self) -> None:
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl
        from app.assistant.runtime.repository import AssistantRuntimeRepository

        assert self.prepared is not None
        control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
        if control is None:
            control = AssistantRuntimeRepository(self.db).get_or_create_control_for_update()
        control.active_rollout_revision_id = self.prepared.rollout_revision_id
        control.state_revision = max(int(control.state_revision or 0), 1)
        control.new_runs_enabled = True
        self.db.commit()

    def disable_new_runs(self) -> None:
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl

        control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
        assert control is not None
        control.new_runs_enabled = False
        self.db.commit()


@pytest.fixture
def postgres_runtime():
    from app.common.exceptions import register_exception_handlers
    from app.database import get_db
    from app.main import public_ready as production_public_ready
    from app.operator_auth.route_policy import public_router

    with _engine() as engine:
        _drop_public_schema(engine)
        _upgrade_to_clean_root()
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = SessionLocal()
        try:
            app = FastAPI()
            register_exception_handlers(app, debug=True)

            def _override_db():
                try:
                    yield db
                finally:
                    pass

            app.dependency_overrides[get_db] = _override_db

            # Production public_ready uses the family-bound compatibility port
            # against the live clean-root marker and revision.
            _public = public_router()
            _public.add_api_route("/ready", production_public_ready, methods=["GET"])
            app.include_router(_public)
            yield _PostgresRuntime(db, app)
        finally:
            db.close()
            app.dependency_overrides.clear()


def test_readiness_transitions(postgres_runtime: _PostgresRuntime):
    assert postgres_runtime.public_ready() == (
        503,
        ("system_not_initialized",),
    )
    postgres_runtime.initialize()
    assert "rollout_inactive" in postgres_runtime.reason_codes()
    postgres_runtime.register_compatible_worker()
    postgres_runtime.activate()
    assert postgres_runtime.public_ready() == (200, ())
    postgres_runtime.disable_new_runs()
    assert postgres_runtime.public_ready() == (
        503,
        ("new_runs_disabled",),
    )


def test_health_stays_ok_while_ready_is_503(postgres_runtime: _PostgresRuntime):
    """Process liveness must not depend on assistant readiness state."""
    from app.common.responses import ApiResponse
    from app.main import health
    from app.operator_auth.route_policy import public_router

    # Mount production health on a throwaway app without DB wiring.
    app = FastAPI()
    public = public_router()
    public.add_api_route("/health", health, methods=["GET"])
    app.include_router(public)
    client = TestClient(app)

    assert postgres_runtime.public_ready()[0] == 503
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}
    # Envelope shape stays ApiResponse.
    parsed = response.json()
    assert parsed["success"] is True
    assert parsed["code"] == 0
    assert ApiResponse.model_validate(parsed).data == {"status": "ok"}
