"""PostgreSQL CAS concurrency for activation (Plan 2 Task 6).

With ``MINDATLAS_REQUIRE_POSTGRES=1`` this suite hard-fails when the disposable
Postgres URL is missing (release-critical gate, never skip).
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

PLAN2_HEAD = "b6e2d4f8a901"
BUILD = "test-build-activation-pg-task6"
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
        "Plan 2 activation PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 activation PostgreSQL "
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
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))


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


def _seed_runtime(db) -> dict[str, Any]:
    from datetime import timedelta

    from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
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
    from app.assistant.runtime.contracts import PrepareRolloutRequest
    from app.assistant.runtime.activation import AssistantRuntimeActivationService
    from app.assistant.runtime.readiness import (
        AssistantReadinessService,
        Plan2AlembicHeadCompatibility,
    )
    from app.common.time import utcnow
    from app.operator_auth.contracts import OperatorPrincipal
    from app.operator_auth.models import OperatorSession
    from app.operator_auth.repository import OperatorRepository
    from app.system_settings.initialization_service import (
        SYSTEM_INITIALIZATION_STATE_KEY,
    )
    from app.system_settings.models import AppSetting
    from app.assistant.skills.models import AssistantMainAgentProfile

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    account = OperatorRepository(db).seed_account(
        password=PASSWORD, role="operator", enabled=True
    )
    now = utcnow()
    session_row = OperatorSession(
        operator_account_id=account.id,
        token_digest="a" * 64,
        csrf_digest="b" * 64,
        hmac_key_id="k1",
        password_revision=int(account.password_revision or 1),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        absolute_expires_at=now + timedelta(hours=8),
        request_digest="c" * 64,
        user_agent_digest="d" * 64,
        network_digest="e" * 64,
    )
    db.add(session_row)
    db.flush()
    principal = OperatorPrincipal(
        operator_id=account.id,
        role="operator",
        session_id=session_row.id,
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
    db.add(AiComponentBinding(component="assistant", llm_model_id=model.id))
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
    db.add(
        AssistantWorkerRegistration(
            worker_id=f"pg-act-worker-{uuid.uuid4().hex[:8]}",
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
            hostname_label="pg-act",
        )
    )
    db.commit()

    # Prepare a second revision so two concurrent activations can race.
    profile = (
        db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
        .one()
    )
    readiness = AssistantReadinessService(
        db,
        settings=_settings(),
        schema_compatibility=Plan2AlembicHeadCompatibility(),
        key_ring=_make_key_ring(),
    )
    service = AssistantRuntimeActivationService(
        db,
        settings=_settings(),
        readiness=readiness,
        key_ring=_make_key_ring(),
    )
    second = service.prepare(
        PrepareRolloutRequest(
            profile_version_id=profile.published_version_id,
            model_id=model.id,
            request_id=uuid4(),
            reason="second prepared revision",
        ),
        principal=principal,
    )
    return {
        "principal": principal,
        "first_revision_id": prepared.rollout_revision_id,
        "second_revision_id": second.rollout_revision_id,
        "account": account,
        "model": model,
    }


def test_competing_activation_has_one_cas_winner():
    from app.assistant.runtime.activation import AssistantRuntimeActivationService
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        CONTROL_KEY_MAIN_AGENT,
        RuntimeControlConflict,
    )
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.assistant.runtime.readiness import (
        AssistantReadinessService,
        Plan2AlembicHeadCompatibility,
    )

    with _engine() as engine:
        _drop_public_schema(engine)
        _upgrade_to_plan2_head()
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        setup = Session()
        try:
            state = _seed_runtime(setup)
        finally:
            setup.close()

        barrier = threading.Barrier(2)
        outcomes: list[dict[str, Any]] = []
        lock = threading.Lock()

        def worker(revision_id: UUID, request_id: UUID) -> None:
            db = Session()
            try:
                readiness = AssistantReadinessService(
                    db,
                    settings=_settings(),
                    schema_compatibility=Plan2AlembicHeadCompatibility(),
                    key_ring=_make_key_ring(),
                )
                service = AssistantRuntimeActivationService(
                    db,
                    settings=_settings(),
                    readiness=readiness,
                    key_ring=_make_key_ring(),
                )
                barrier.wait(timeout=30)
                try:
                    result = service.activate(
                        revision_id,
                        ActivateRolloutRequest(
                            expected_control_revision=0,
                            request_id=request_id,
                            reason="concurrent activate",
                        ),
                        principal=state["principal"],
                    )
                    with lock:
                        outcomes.append(
                            {
                                "status": "activated",
                                "revision_id": result.active_rollout_revision_id,
                                "control_revision": result.control_revision,
                            }
                        )
                except RuntimeControlConflict:
                    with lock:
                        outcomes.append({"status": "conflict"})
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        outcomes.append({"status": "error", "error": str(exc)})
            finally:
                db.close()

        t1 = threading.Thread(
            target=worker,
            args=(state["first_revision_id"], uuid4()),
        )
        t2 = threading.Thread(
            target=worker,
            args=(state["second_revision_id"], uuid4()),
        )
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        assert not t1.is_alive() and not t2.is_alive()

        statuses = sorted(item["status"] for item in outcomes)
        assert statuses == ["activated", "conflict"], outcomes

        verify = Session()
        try:
            control = verify.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
            assert control is not None
            assert int(control.state_revision) == 1
            assert control.active_rollout_revision_id in {
                state["first_revision_id"],
                state["second_revision_id"],
            }
            assert control.new_runs_enabled is True
        finally:
            verify.close()
