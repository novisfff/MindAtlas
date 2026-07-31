"""PostgreSQL concurrency proof: exactly one initialization commits.

With ``MINDATLAS_REQUIRE_POSTGRES=1`` this suite hard-fails when the disposable
Postgres URL is missing.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import assert_disposable_postgres_target

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-bootstrap-task4")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
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
        "Task 4 initialization concurrency PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Task 4 initialization concurrency "
        "PostgreSQL gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail instead of skip."
    ),
)


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", "1")
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
    from app.database import Base

    import app.ai_registry.models  # noqa: F401
    import app.assistant.models  # noqa: F401
    import app.assistant.runtime.models  # noqa: F401
    import app.assistant.skills.models  # noqa: F401
    import app.assistant_config.models  # noqa: F401
    import app.entry_type.models  # noqa: F401
    import app.operator_auth.models  # noqa: F401
    import app.relation.models  # noqa: F401
    import app.system_settings.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def _truncate_owned(engine) -> None:
    assert_disposable_postgres_target(str(engine.url))
    tables = [
        "assistant_main_agent_rollout_event",
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


def _request_context(tag: str):
    from app.operator_auth.contracts import RequestSecurityContext

    return RequestSecurityContext(
        request_id=f"pg-init-concurrency-{tag}",
        request_digest="a" * 64,
        user_agent_digest="b" * 64,
        network_digest="c" * 64,
    )


def _valid_setup():
    from app.operator_auth.contracts import SetupAuthorization

    return SetupAuthorization(validated=True)


def _make_request():
    from app.system_settings.schemas import InitializeSystemRequest

    return InitializeSystemRequest.model_validate(
        {
            "locale": "en",
            "operatorPassword": "correct horse battery",
            "aiCredential": {
                "name": "OpenAI",
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": "sk-test-bootstrap-concurrency",
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


def test_concurrent_initialization_exactly_one_winner():
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantSkillPackage,
    )
    from app.common.exceptions import ApiException
    from app.operator_auth.models import OperatorAccount
    from app.system_settings.initialization_coordinator import InitializationCoordinator
    from app.system_settings.initialization_service import SYSTEM_INITIALIZATION_STATE_KEY
    from app.system_settings.models import AppSetting

    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2, timeout=30)

    with _engine() as engine:
        _ensure_schema(engine)
        _truncate_owned(engine)

        def worker(tag: str) -> None:
            try:
                with _session(engine) as db:
                    barrier.wait()
                    with patch(
                        "app.system_settings.initialization_service.sync_scheduler"
                    ):
                        InitializationCoordinator(db).initialize(
                            _make_request(),
                            setup_authorization=_valid_setup(),
                            request_context=_request_context(tag),
                        )
                    results.append(f"ok:{tag}")
            except ApiException as exc:
                errors.append(exc)
                results.append(f"err:{tag}:{exc.message}")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                results.append(f"exc:{tag}:{type(exc).__name__}")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        assert not t1.is_alive() and not t2.is_alive()

        ok = [r for r in results if r.startswith("ok:")]
        err = [r for r in results if r.startswith("err:")]
        assert len(ok) == 1, results
        assert len(err) == 1, results
        assert any("system_already_initialized" in r for r in err), results

        with _session(engine) as db:
            assert db.query(OperatorAccount).count() == 1
            assert (
                db.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.is_system.is_(True))
                .count()
                == 1
            )
            assert db.query(AssistantMainAgentProfile).count() == 1
            marker = (
                db.query(AppSetting)
                .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
                .count()
            )
            assert marker == 1
