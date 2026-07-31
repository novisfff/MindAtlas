"""PostgreSQL readiness gate (Plan 2 Task 5).

Requires ``MINDATLAS_TEST_POSTGRES_URL``. With ``MINDATLAS_REQUIRE_POSTGRES=1``
this suite hard-fails instead of skipping.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema

bootstrap_backend_imports()
reset_caches()

PLAN2_HEAD = "b6e2d4f8a901"
BUILD = "test-build-readiness-pg-task5"
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
        "Plan 2 readiness PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 readiness PostgreSQL "
        "gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", "1")
    os.environ.setdefault("APP_ENV", "test")
    os.environ["APP_BUILD_REVISION"] = BUILD
    os.environ["ASSISTANT_NEW_RUNS_ENABLED"] = "true"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _alembic_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _POSTGRES_URL
    env["MINDATLAS_PLAN10_B2_TEST_OVERRIDE"] = "1"
    env.setdefault("APP_ENV", "test")
    env["APP_BUILD_REVISION"] = BUILD
    return env


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


def _upgrade_to_plan2_head() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", PLAN2_HEAD],
        cwd=str(_BACKEND_DIR),
        env=_alembic_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic upgrade {PLAN2_HEAD} failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _make_key_ring():
    from app.operator_auth.tokens import SessionMacKeyRing

    return SessionMacKeyRing(active_key_id="k1", keys={"k1": b"k" * 32})


def _settings():
    return SimpleNamespace(
        app_build_revision=BUILD,
        assistant_new_runs_enabled=True,
    )


def test_plan2_schema_compatibility_and_readiness_on_postgres():
    from app.assistant.runtime.readiness import (
        AssistantReadinessService,
        Plan2AlembicHeadCompatibility,
        read_single_alembic_version,
    )
    from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
    from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
    from app.assistant.durable.worker_registry import (
        RUNTIME_CONTRACT_VERSION,
        default_capability_feature_digest,
    )
    from app.assistant.durable.models import AssistantWorkerRegistration
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

    with _engine() as engine:
        _drop_public_schema(engine)
        _upgrade_to_plan2_head()

        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = Session()
        try:
            assert read_single_alembic_version(db) == PLAN2_HEAD
            assert Plan2AlembicHeadCompatibility().is_compatible(db) is True

            # Uninitialized → structural reason only.
            service = AssistantReadinessService(
                db,
                settings=_settings(),
                schema_compatibility=Plan2AlembicHeadCompatibility(),
                key_ring=_make_key_ring(),
            )
            snap = service.evaluate()
            assert snap.ready is False
            assert snap.reason_codes == ("system_not_initialized",)

            # Stage operator + model + bootstrap prepared + activate + worker.
            bootstrapper = AssistantSystemBootstrapper(db)
            permit = bootstrapper.lock_and_verify_fresh_preconditions()
            account = OperatorRepository(db).seed_account(
                password=PASSWORD, role="operator", enabled=True
            )
            cred = AiCredential(
                name=f"cred-{uuid.uuid4().hex[:8]}",
                base_url="https://api.example.com/v1",
                api_key_encrypted="enc-test-key-not-secret",
                api_key_hint="****test",
                runtime_revision=1,
            )
            db.add(cred)
            db.flush()
            model = AiModel(
                credential_id=cred.id,
                name="gpt-test",
                model_type="llm",
                runtime_revision=1,
            )
            db.add(model)
            db.flush()
            binding = AiComponentBinding(component="assistant", llm_model_id=model.id)
            db.add(binding)
            db.flush()

            prepared = bootstrapper.stage_bootstrap(
                StageAssistantBootstrapRequest(
                    operator_id=account.id,
                    operator_session_id=None,
                    model_id=model.id,
                    build_revision=BUILD,
                    fresh_permit=permit,
                )
            )
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
            assert control is not None
            control.active_rollout_revision_id = prepared.rollout_revision_id
            control.state_revision = 1
            control.new_runs_enabled = True
            now = utcnow()
            db.add(
                AssistantWorkerRegistration(
                    worker_id=f"pg-worker-{uuid.uuid4().hex[:8]}",
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
                    hostname_label="pg-test",
                )
            )
            db.commit()

            service = AssistantReadinessService(
                db,
                settings=_settings(),
                schema_compatibility=Plan2AlembicHeadCompatibility(),
                key_ring=_make_key_ring(),
            )
            snap = service.evaluate()
            assert snap.ready is True, snap.reason_codes
            assert snap.reason_codes == ()
            assert snap.active_rollout_revision_id == prepared.rollout_revision_id
            assert snap.model_id == model.id
            assert snap.compatible_worker_ids

            # Observational: evaluate must not create extra control revisions.
            before_rev = int(control.state_revision)
            service.evaluate()
            db.refresh(control)
            assert int(control.state_revision) == before_rev
        finally:
            db.close()
