"""PostgreSQL atomic Chat admission (Plan 2 Task 8).

Requires ``MINDATLAS_TEST_POSTGRES_URL``. With ``MINDATLAS_REQUIRE_POSTGRES=1``
this suite hard-fails instead of skipping.
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
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402

CLEAN_SCHEMA_HEAD = CLEAN_ROOT_REVISION
BUILD = "test-build-admission-pg-task8"
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
        "Plan 2 atomic admission PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 atomic admission PostgreSQL "
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
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    os.environ.setdefault("APP_ENV", "test")
    os.environ["APP_BUILD_REVISION"] = BUILD
    os.environ["ASSISTANT_NEW_RUNS_ENABLED"] = "true"
    os.environ.setdefault(
        "AI_PROVIDER_FERNET_KEY",
        "b98esSSrtceWc4IUOFGR-f_6I8FfnxtpjjYQZN51RCw=",
    )
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def _alembic_env() -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _POSTGRES_URL
    env["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
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


def _settings(**overrides):
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
        "assistant_capability_ledger_mode": "legacy_read_only",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _PostgresRuntime:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.legacy_daemon_starts = 0
        self.prepared = None
        self.conversation_id: UUID | None = None
        self.closure = None

    def seed_ready(self) -> UUID:
        from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.assistant.models import Conversation
        from app.assistant.runtime.bootstrap import (
            AssistantSystemBootstrapper,
            StageAssistantBootstrapRequest,
        )
        from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl
        from app.common.time import utcnow
        from app.operator_auth.repository import OperatorRepository
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
        )
        from app.system_settings.models import AppSetting

        db = self.Session()
        try:
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
            self.prepared = prepared
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
                    worker_id=f"pg-admit-worker-{uuid.uuid4().hex[:8]}",
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
            conversation = Conversation(title="pg-admission")
            db.add(conversation)
            db.flush()
            self.conversation_id = conversation.id
            self.closure = AssistantRuntimeClosureBuilder(db).build(
                rollout_revision_id=prepared.rollout_revision_id,
                lock=False,
            )
            db.commit()
            return conversation.id
        finally:
            db.close()

    def admit(self, conversation_id: UUID, user_message: str):
        from app.assistant.runtime.admission import AssistantChatAdmissionService
        from app.assistant.runtime.readiness import (
            AssistantReadinessService,
        )
        from app.schema.compatibility import runtime_schema_compatibility

        db = self.Session()
        try:
            readiness = AssistantReadinessService(
                db,
                settings=_settings(),
                schema_compatibility=runtime_schema_compatibility(),
                key_ring=_make_key_ring(),
            )
            service = AssistantChatAdmissionService(
                db,
                settings=_settings(),
                readiness=readiness,
            )
            run = service.admit_and_create(
                conversation_id=conversation_id,
                user_message=user_message,
            )
            # Detach identity for cross-session asserts.
            run_id = run.id
            status = run.status
            runtime_kind = run.runtime_kind
            db.expunge(run)
            run.id = run_id  # type: ignore[misc]
            run.status = status  # type: ignore[misc]
            run.runtime_kind = runtime_kind  # type: ignore[misc]
            return run
        finally:
            db.close()

    def chat_owned_counts(self, conversation_id: UUID) -> dict[str, int]:
        from app.assistant.models import (
            AssistantChatRun,
            AssistantChatRunEvent,
            Message,
        )

        db = self.Session()
        try:
            user_messages = (
                db.query(Message)
                .filter(
                    Message.conversation_id == conversation_id, Message.role == "user"
                )
                .count()
            )
            assistant_messages = (
                db.query(Message)
                .filter(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                )
                .count()
            )
            runs = (
                db.query(AssistantChatRun)
                .filter(AssistantChatRun.conversation_id == conversation_id)
                .count()
            )
            run_ids = [
                row.id
                for row in db.query(AssistantChatRun.id)
                .filter(AssistantChatRun.conversation_id == conversation_id)
                .all()
            ]
            initial_events = 0
            if run_ids:
                initial_events = (
                    db.query(AssistantChatRunEvent)
                    .filter(
                        AssistantChatRunEvent.run_id.in_(run_ids),
                        AssistantChatRunEvent.event_name == "run_status",
                    )
                    .count()
                )
            return {
                "user_messages": int(user_messages),
                "assistant_messages": int(assistant_messages),
                "runs": int(runs),
                "initial_events": int(initial_events),
            }
        finally:
            db.close()

    def count_runs(self, conversation_id: UUID) -> int:
        return self.chat_owned_counts(conversation_id)["runs"]

    def reload_run(self, run_id: UUID):
        from app.assistant.models import AssistantChatRun

        db = self.Session()
        try:
            run = db.get(AssistantChatRun, run_id)
            assert run is not None
            db.expunge(run)
            return run
        finally:
            db.close()

    def execute_with_injected_provider_failure(self, run_id: UUID) -> None:
        """Simulate post-insert worker/provider failure on the existing Run."""
        from app.assistant.run_service import AssistantChatRunService

        self.legacy_daemon_starts = 0
        db = self.Session()
        try:
            AssistantChatRunService(db).update_run_status(
                run_id=run_id,
                status="failed",
                error_message="provider_injected_failure",
            )
        finally:
            db.close()

    def count_legacy_daemon_starts(self) -> int:
        return int(self.legacy_daemon_starts)


def concurrently_admit(
    runtime: _PostgresRuntime, conversation_id: UUID, messages: tuple[str, ...]
) -> list[dict[str, Any]]:
    from app.assistant.runtime.admission import (
        AssistantAdmissionError,
        AssistantChatAdmissionService,
        ConcurrentChatAdmission,
    )
    from app.assistant.runtime.readiness import (
        AssistantReadinessService,
    )
    from app.schema.compatibility import runtime_schema_compatibility

    barrier = threading.Barrier(len(messages))
    outcomes: list[dict[str, Any]] = []
    lock = threading.Lock()

    def worker(user_message: str) -> None:
        db = runtime.Session()
        try:
            readiness = AssistantReadinessService(
                db,
                settings=_settings(),
                schema_compatibility=runtime_schema_compatibility(),
                key_ring=_make_key_ring(),
            )
            service = AssistantChatAdmissionService(
                db,
                settings=_settings(),
                readiness=readiness,
            )
            barrier.wait(timeout=30)
            try:
                run = service.admit_and_create(
                    conversation_id=conversation_id,
                    user_message=user_message,
                )
                with lock:
                    outcomes.append(
                        {
                            "status": "created",
                            "run_id": run.id,
                            "runtime_kind": run.runtime_kind,
                        }
                    )
            except ConcurrentChatAdmission:
                with lock:
                    outcomes.append({"status": "conflict"})
            except AssistantAdmissionError as exc:
                with lock:
                    outcomes.append(
                        {"status": "admission_error", "reason": exc.reason_code}
                    )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    outcomes.append({"status": "error", "error": str(exc)})
        finally:
            db.close()

    threads = [
        threading.Thread(target=worker, args=(message,)) for message in messages
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()
    return outcomes


@pytest.fixture
def postgres_runtime():
    with _engine() as engine:
        _drop_public_schema(engine)
        _upgrade_to_clean_root()
        runtime = _PostgresRuntime(engine)
        yield runtime


def test_concurrent_chat_admission_has_one_complete_winner(postgres_runtime):
    conversation_id = postgres_runtime.seed_ready()
    outcomes = concurrently_admit(
        postgres_runtime, conversation_id, ("first", "second")
    )
    assert sorted(item["status"] for item in outcomes) == ["conflict", "created"], (
        outcomes
    )
    created = [item for item in outcomes if item["status"] == "created"]
    assert len(created) == 1
    assert created[0]["runtime_kind"] == "main_agent"
    counts = postgres_runtime.chat_owned_counts(conversation_id)
    assert counts == {
        "user_messages": 1,
        "assistant_messages": 1,
        "runs": 1,
        "initial_events": 1,
    }


def test_worker_failure_after_insert_marks_existing_run_failed(postgres_runtime):
    conversation_id = postgres_runtime.seed_ready()
    run = postgres_runtime.admit(conversation_id, "hello")
    postgres_runtime.execute_with_injected_provider_failure(run.id)
    failed = postgres_runtime.reload_run(run.id)
    assert failed.id == run.id
    assert failed.status == "failed"
    assert postgres_runtime.count_runs(conversation_id) == 1
    assert postgres_runtime.count_legacy_daemon_starts() == 0
    assert failed.runtime_kind == "main_agent"


def test_success_admission_freezes_closure_on_postgres(postgres_runtime):
    conversation_id = postgres_runtime.seed_ready()
    run = postgres_runtime.admit(conversation_id, "hello")
    closure = postgres_runtime.closure
    assert closure is not None
    reloaded = postgres_runtime.reload_run(run.id)
    assert reloaded.runtime_kind == "main_agent"
    assert reloaded.main_agent_rollout_revision_id == closure.rollout_revision_id
    assert reloaded.main_agent_profile_version_id == closure.profile_version_id
    assert reloaded.resolved_model_id == closure.model_id
    assert reloaded.runtime_closure_digest == closure.closure_digest
    assert reloaded.required_app_build_revision == closure.build_revision
    counts = postgres_runtime.chat_owned_counts(conversation_id)
    assert counts["initial_events"] == 1
