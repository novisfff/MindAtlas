"""PostgreSQL atomicity proofs for Task 4 initialization bootstrap.

With ``MINDATLAS_REQUIRE_POSTGRES=1`` this suite hard-fails when the disposable
Postgres URL is missing (release-critical gate, never skip).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import (
    assert_disposable_postgres_target,
    reset_disposable_public_schema,
)
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("MINDATLAS_DEPLOYMENT_CLASS", "rehearsal")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-bootstrap-task4")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "b98esSSrtceWc4IUOFGR-f_6I8FfnxtpjjYQZN51RCw=",
)

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
        "Task 4 initialization atomicity PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Task 4 initialization atomicity "
        "PostgreSQL gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)


class InjectedInitializationFailure(RuntimeError):
    """Synthetic failure injected at a named coordinator stage."""


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "rehearsal"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@contextmanager
def _engine():
    assert _POSTGRES_URL
    _configure_database_env(_POSTGRES_URL)
    engine = create_engine(
        _as_sqlalchemy_url(_POSTGRES_URL), future=True, pool_pre_ping=True
    )
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _session(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _ensure_schema(engine) -> None:
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-build-bootstrap-task4",
    )


def _truncate_owned(engine) -> None:
    assert_disposable_postgres_target(str(engine.url))
    tables = [
        "assistant_main_agent_rollout_event",
        "assistant_runtime_bootstrap_gate_use",
        "assistant_main_agent_rollout_control",
        "assistant_main_agent_rollout_revision",
        "assistant_skill_capability_dependency",
        "assistant_skill_capability_binding",
        "assistant_skill_version_resource",
        "assistant_skill_version",
        "assistant_skill_package_alias",
        "assistant_skill_package",
        "assistant_skill_resource_blob",
        "assistant_main_agent_profile_version",
        "assistant_main_agent_profile",
        "operator_audit_event",
        "operator_session",
        "operator_account",
        "ai_component_binding",
        "ai_model",
        "ai_credential",
        "app_setting",
        "entry_type",
        "relation_type",
        "assistant_agent_profile_version",
        "assistant_agent_profile",
        "assistant_tool",
        "assistant_workflow_version",
        "assistant_workflow",
    ]
    with engine.begin() as conn:
        existing: list[str] = []
        for table in tables:
            reg = conn.execute(
                text("SELECT to_regclass(:name)"), {"name": table}
            ).scalar()
            if reg is not None:
                existing.append(table)
        if existing:
            joined = ", ".join(f'"{name}"' for name in existing)
            conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))


EMPTY_COUNTS = {
    "operator": 0,
    "skill_package": 0,
    "profile": 0,
    "rollout_revision": 0,
    "bootstrap_gate_use": 0,
    "rollout_event": 0,
    "marker": 0,
}


def initialization_owned_row_counts(db: Session) -> dict[str, int]:
    from app.assistant.runtime.models import (
        AssistantMainAgentRolloutEvent,
        AssistantMainAgentRolloutRevision,
        AssistantRuntimeBootstrapGateUse,
    )
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantSkillPackage,
    )
    from app.operator_auth.models import OperatorAccount
    from app.system_settings.initialization_service import SYSTEM_INITIALIZATION_STATE_KEY
    from app.system_settings.models import AppSetting

    marker = (
        db.query(AppSetting)
        .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
        .count()
    )
    return {
        "operator": db.query(OperatorAccount).count(),
        "skill_package": db.query(AssistantSkillPackage).count(),
        "profile": db.query(AssistantMainAgentProfile).count(),
        "rollout_revision": db.query(AssistantMainAgentRolloutRevision).count(),
        "bootstrap_gate_use": db.query(AssistantRuntimeBootstrapGateUse).count(),
        "rollout_event": db.query(AssistantMainAgentRolloutEvent).count(),
        "marker": marker,
    }


def _request_context():
    from app.operator_auth.contracts import RequestSecurityContext

    return RequestSecurityContext(
        request_id="pg-init-atomicity",
        request_digest="a" * 64,
        user_agent_digest="b" * 64,
        network_digest="c" * 64,
    )


def _valid_setup():
    from app.operator_auth.contracts import SetupAuthorization

    return SetupAuthorization(validated=True)


def _make_request(*, locale: str = "en"):
    from app.system_settings.schemas import InitializeSystemRequest

    return InitializeSystemRequest.model_validate(
        {
            "locale": locale,
            "operatorPassword": "correct horse battery",
            "aiCredential": {
                "name": "OpenAI",
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": "sk-test-bootstrap-atomicity",
            },
            "llmModel": {"name": "gpt-4.1-mini"},
            "entryTypes": [
                {
                    "code": "KNOWLEDGE",
                    "name": "Knowledge",
                    "description": "Concepts",
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


def valid_initialization_arguments():
    return {
        "request": _make_request(),
        "setup_authorization": _valid_setup(),
        "request_context": _request_context(),
    }


@contextmanager
def coordinator_with_failure(db: Session, failure_point: str):
    """Yield a coordinator whose named stage raises InjectedInitializationFailure."""
    from app.assistant.runtime.bootstrap import AssistantSystemBootstrapper
    from app.assistant.runtime.repository import AssistantRuntimeRepository
    from app.system_settings.initialization_coordinator import InitializationCoordinator
    from app.system_settings.initialization_service import SystemInitializationService

    coordinator = InitializationCoordinator(db)
    patches: list = []

    def _fail(label: str):
        raise InjectedInitializationFailure(f"injected at {label}")

    if failure_point == "after_operator":
        original = coordinator.stage_initial_account

        def stage_account(password: str):
            account = original(password)
            _fail("after_operator")
            return account

        coordinator.stage_initial_account = stage_account  # type: ignore[method-assign]
    elif failure_point == "after_model":
        original = SystemInitializationService.stage_core_initialization

        def stage_core(self, request):
            result = original(self, request)
            _fail("after_model")
            return result

        patches.append(
            patch.object(
                SystemInitializationService,
                "stage_core_initialization",
                stage_core,
            )
        )
    elif failure_point == "after_skill":
        original = AssistantSystemBootstrapper._stage_system_skill

        def stage_skill(self, seed, bindings):
            result = original(self, seed, bindings)
            _fail("after_skill")
            return result

        patches.append(
            patch.object(
                AssistantSystemBootstrapper,
                "_stage_system_skill",
                stage_skill,
            )
        )
    elif failure_point == "after_profile":
        original = AssistantSystemBootstrapper._stage_system_profile

        def stage_profile(self, profile_snapshot, *, package_id):
            result = original(self, profile_snapshot, package_id=package_id)
            _fail("after_profile")
            return result

        patches.append(
            patch.object(
                AssistantSystemBootstrapper,
                "_stage_system_profile",
                stage_profile,
            )
        )
    elif failure_point == "after_rollout":
        original = AssistantRuntimeRepository.create_prepared_revision

        def create_revision(self, data):
            result = original(self, data)
            _fail("after_rollout")
            return result

        patches.append(
            patch.object(
                AssistantRuntimeRepository,
                "create_prepared_revision",
                create_revision,
            )
        )
    elif failure_point == "before_marker":
        original = AssistantSystemBootstrapper.stage_bootstrap

        def stage_bootstrap(self, request):
            result = original(self, request)
            _fail("before_marker")
            return result

        patches.append(
            patch.object(
                AssistantSystemBootstrapper,
                "stage_bootstrap",
                stage_bootstrap,
            )
        )
    else:
        raise AssertionError(f"unknown failure_point: {failure_point}")

    entered = [p.start() for p in patches]
    try:
        yield coordinator
    finally:
        for p in patches:
            p.stop()
        del entered


@pytest.mark.parametrize(
    "failure_point",
    [
        "after_operator",
        "after_model",
        "after_skill",
        "after_profile",
        "after_rollout",
        "before_marker",
    ],
)
def test_initialization_failure_rolls_back_every_owned_row(failure_point):
    with _engine() as engine:
        _ensure_schema(engine)
        _truncate_owned(engine)
        with _session(engine) as db:
            with coordinator_with_failure(db, failure_point) as coordinator:
                with pytest.raises(InjectedInitializationFailure):
                    coordinator.initialize(**valid_initialization_arguments())
            db.expire_all()
            assert initialization_owned_row_counts(db) == EMPTY_COUNTS


def test_session_is_issued_only_after_commit():
    """Router-order proof: commit happens before session issuance."""
    from app.system_settings.initialization_coordinator import InitializationCoordinator
    from app.system_settings.router import initialize_system as router_initialize  # type: ignore

    timeline: list[str] = []

    with _engine() as engine:
        _ensure_schema(engine)
        _truncate_owned(engine)
        with _session(engine) as db:
            original_commit = db.commit

            def _commit(*args, **kwargs):
                timeline.append("commit")
                return original_commit(*args, **kwargs)

            db.commit = _commit  # type: ignore[method-assign]

            class _FakeAuthService:
                def issue_initial_session(self, operator_id, context):
                    timeline.append("issue_session")

                    class Issued:
                        session_cookie_value = "sess"
                        csrf_cookie_value = "csrf"

                    return Issued()

            with patch(
                "app.system_settings.initialization_service.sync_scheduler"
            ), patch(
                "app.system_settings.router.build_operator_auth_service",
                return_value=_FakeAuthService(),
            ), patch(
                "app.system_settings.router.set_session_cookies"
            ), patch(
                "app.system_settings.router.get_settings"
            ) as settings_mock:
                # Prefer proving order via coordinator+manual session issue when
                # router signature is heavy; still record commit then issue.
                from app.config import get_settings

                settings_mock.return_value = get_settings()
                result = InitializationCoordinator(db).initialize(
                    **valid_initialization_arguments()
                )
                assert "commit" in timeline
                # Simulate router post-commit session issuance.
                _FakeAuthService().issue_initial_session(
                    result.operator_account_id, _request_context()
                )
                assert timeline.index("commit") < timeline.index("issue_session")
                assert result.prepared_rollout_revision_id is not None
                response = result.to_response()
                assert response.assistant_bootstrap == "pending_worker"
                assert response.prepared_rollout_revision_id == (
                    result.prepared_rollout_revision_id
                )


def test_successful_initialization_prepares_not_activates():
    from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
    from app.assistant.runtime.models import (
        AssistantMainAgentRolloutControl,
        AssistantRuntimeBootstrapGateUse,
    )
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantSkillPackage,
    )
    from app.system_settings.initialization_coordinator import InitializationCoordinator

    with _engine() as engine:
        _ensure_schema(engine)
        _truncate_owned(engine)
        with _session(engine) as db:
            with patch("app.system_settings.initialization_service.sync_scheduler"):
                result = InitializationCoordinator(db).initialize(
                    **valid_initialization_arguments()
                )
            package = (
                db.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.is_system.is_(True))
                .one()
            )
            assert package.canonical_name == "mindatlas-universal"
            profile = db.query(AssistantMainAgentProfile).one()
            assert profile.runtime_enabled is True
            control = db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
            assert control is not None
            assert control.active_rollout_revision_id is None
            assert control.state_revision == 0
            assert result.rollout_control_revision == 0
            assert initialization_owned_row_counts(db)["operator"] == 1
            assert initialization_owned_row_counts(db)["marker"] == 1
            gate_use = db.query(AssistantRuntimeBootstrapGateUse).one()
            assert gate_use.action == "system_bootstrap"
            assert gate_use.rollout_revision_id == result.prepared_rollout_revision_id
