"""Trusted Assistant system bootstrap (Plan 2 Task 4) — unit / SQLite proofs."""

from __future__ import annotations

import os
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-bootstrap-task4")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)


@pytest.fixture
def db():
    reset_caches()
    from app.config import get_settings

    get_settings.cache_clear()
    session = make_session()
    try:
        yield session
    finally:
        session.close()
        reset_caches()


def count_seed_owned_rows(db) -> int:
    from app.assistant.runtime.models import (
        AssistantMainAgentRolloutEvent,
        AssistantMainAgentRolloutRevision,
    )
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
        AssistantSkillPackage,
        AssistantSkillVersion,
    )

    return (
        db.query(AssistantSkillPackage).count()
        + db.query(AssistantSkillVersion).count()
        + db.query(AssistantMainAgentProfile).count()
        + db.query(AssistantMainAgentProfileVersion).count()
        + db.query(AssistantMainAgentRolloutRevision).count()
        + db.query(AssistantMainAgentRolloutEvent).count()
    )


def arrange_nonfresh_state(db, state: str) -> None:
    from datetime import datetime, timezone

    from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
    )
    from app.operator_auth.models import OperatorAccount
    from app.operator_auth.password import PasswordService
    from app.system_settings.initialization_service import SYSTEM_INITIALIZATION_STATE_KEY
    from app.system_settings.models import AppSetting
    from tests.agent_skill_test_support import create_default_model_binding
    from tests.test_assistant_runtime_models import prepared_revision_fixture
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    if state == "initialized":
        db.add(
            AppSetting(
                key=SYSTEM_INITIALIZATION_STATE_KEY,
                value_json={
                    "initialized": True,
                    "completedAt": datetime.now(timezone.utc).isoformat(),
                    "locale": "en",
                    "version": 1,
                    "source": "user",
                },
            )
        )
        db.flush()
        return

    if state == "operator_exists":
        now = datetime.now(timezone.utc)
        db.add(
            OperatorAccount(
                singleton_key="operator",
                role="operator",
                password_hash=PasswordService().hash("correct horse battery"),
                password_revision=1,
                enabled=True,
                failed_login_count=0,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        return

    if state == "published_profile_exists":
        profile = AssistantMainAgentProfile(
            profile_key="default",
            display_name="Default Main Agent",
            is_default=True,
            migration_state="bootstrap",
            runtime_enabled=False,
        )
        db.add(profile)
        db.flush()
        draft = AssistantMainAgentProfileVersion(
            profile_id=profile.id,
            sequence_no=1,
            version_name="draft-1",
            version_source="save",
            origin="bootstrap",
            snapshot={"schemaVersion": 2, "basePrompt": "x" * 20},
            content_digest="a" * 64,
        )
        db.add(draft)
        db.flush()
        version = AssistantMainAgentProfileVersion(
            profile_id=profile.id,
            sequence_no=2,
            version_name="publish-1",
            version_source="publish",
            origin="bootstrap",
            source_draft_version_id=draft.id,
            snapshot={"schemaVersion": 2, "basePrompt": "x" * 20},
            content_digest="a" * 64,
        )
        db.add(version)
        db.flush()
        profile.published_version_id = version.id
        db.flush()
        return

    if state == "active_rollout_exists":
        create_default_model_binding(db)
        repo = AssistantRuntimeRepository(db)
        prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
        control = repo.get_or_create_control_for_update()
        control.active_rollout_revision_id = prepared.id
        control.state_revision = 1
        db.flush()
        assert db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT) is not None
        return

    raise AssertionError(f"unknown nonfresh state: {state}")


def stage_operator_and_core_fixture(db):
    """Stage operator + core AI binding without marker/commit (bootstrap preconditions)."""
    from app.operator_auth.repository import OperatorRepository
    from app.system_settings.initialization_service import SystemInitializationService
    from app.system_settings.schemas import InitializeSystemRequest

    account = OperatorRepository(db).seed_account(
        password="correct horse battery",
        role="operator",
        enabled=True,
    )
    request = InitializeSystemRequest.model_validate(
        {
            "locale": "en",
            "operatorPassword": "correct horse battery",
            "aiCredential": {
                "name": "OpenAI",
                "baseUrl": "https://api.openai.com/v1",
                "apiKey": "sk-test-bootstrap-123456",
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
    # Core path asserts clean marker; operator already staged is fine.
    core = SystemInitializationService(db).stage_core_initialization(request)
    return account, core


def bootstrap_request_fixture(*, operator_id, model_id, fresh_permit, build_revision=None):
    from app.assistant.runtime.bootstrap import StageAssistantBootstrapRequest
    from app.config import get_settings

    return StageAssistantBootstrapRequest(
        operator_id=operator_id,
        operator_session_id=None,
        model_id=model_id,
        build_revision=build_revision or get_settings().app_build_revision,
        fresh_permit=fresh_permit,
    )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("initialized", "system_already_initialized"),
        ("operator_exists", "operator_already_exists"),
        ("published_profile_exists", "profile_already_published"),
        ("active_rollout_exists", "rollout_already_active"),
    ],
)
def test_system_bootstrap_rejects_nonfresh_state(db, state, reason):
    from app.assistant.runtime.bootstrap import (
        AssistantBootstrapRejected,
        AssistantSystemBootstrapper,
    )
    from app.assistant.skills.models import AssistantSkillPackage

    before_packages = db.query(AssistantSkillPackage).count()
    arrange_nonfresh_state(db, state)
    bootstrapper = AssistantSystemBootstrapper(db)
    with pytest.raises(AssistantBootstrapRejected) as exc:
        bootstrapper.lock_and_verify_fresh_preconditions()
    assert exc.value.reason_code == reason
    # Rejection must not stage any system skill package.
    assert (
        db.query(AssistantSkillPackage)
        .filter(AssistantSkillPackage.is_system.is_(True))
        .count()
        == 0
    )
    assert db.query(AssistantSkillPackage).count() == before_packages + (
        0 if state != "published_profile_exists" else 0
    )


def test_stage_bootstrap_never_commits(db):
    from app.assistant.runtime.bootstrap import AssistantSystemBootstrapper

    commit_spy = MagicMock()
    original_commit = db.commit

    def _guarded_commit(*args, **kwargs):
        commit_spy(*args, **kwargs)
        return original_commit(*args, **kwargs)

    db.commit = _guarded_commit  # type: ignore[method-assign]
    try:
        bootstrapper = AssistantSystemBootstrapper(db)
        permit = bootstrapper.lock_and_verify_fresh_preconditions()
        operator, core = stage_operator_and_core_fixture(db)
        # Core may flush but must not commit via bootstrap path.
        commit_spy.reset_mock()
        prepared = bootstrapper.stage_bootstrap(
            bootstrap_request_fixture(
                operator_id=operator.id,
                model_id=core.llm_model_id,
                fresh_permit=permit,
            )
        )
        commit_spy.assert_not_called()
        assert prepared.rollout_revision_id is not None
        assert prepared.rollout_control_revision == 0
        # Still uncommitted — rolling back must wipe bootstrap rows.
        db.rollback()
        assert count_seed_owned_rows(db) == 0
    finally:
        db.commit = original_commit  # type: ignore[method-assign]


def test_stage_bootstrap_prepares_not_activates(db):
    from app.assistant.runtime.bootstrap import AssistantSystemBootstrapper
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantSkillPackage,
    )

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    operator, core = stage_operator_and_core_fixture(db)
    prepared = bootstrapper.stage_bootstrap(
        bootstrap_request_fixture(
            operator_id=operator.id,
            model_id=core.llm_model_id,
            fresh_permit=permit,
        )
    )
    db.flush()

    package = db.get(AssistantSkillPackage, prepared.skill_package_id)
    assert package is not None
    assert package.is_system is True
    assert package.catalog_enabled is True
    assert package.published_version_id == prepared.skill_version_id
    assert package.canonical_name == "mindatlas-universal"

    profile = db.get(AssistantMainAgentProfile, prepared.profile_id)
    assert profile is not None
    assert profile.runtime_enabled is True
    assert profile.published_version_id == prepared.profile_version_id
    assert profile.migration_state == "bootstrap"

    control = db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
    assert control is not None
    assert control.active_rollout_revision_id is None
    assert control.state_revision == 0
    assert prepared.rollout_control_revision == 0


def test_stage_bootstrap_persists_exact_system_bootstrap_gate_use(db):
    from app.assistant.runtime.bootstrap import AssistantSystemBootstrapper
    from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
    from app.assistant.runtime.models import AssistantRuntimeBootstrapGateUse

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    operator, core = stage_operator_and_core_fixture(db)
    prepared = bootstrapper.stage_bootstrap(
        bootstrap_request_fixture(
            operator_id=operator.id,
            model_id=core.llm_model_id,
            fresh_permit=permit,
        )
    )

    gate_use = (
        db.query(AssistantRuntimeBootstrapGateUse)
        .filter(
            AssistantRuntimeBootstrapGateUse.rollout_revision_id
            == prepared.rollout_revision_id
        )
        .one()
    )
    closure = AssistantRuntimeClosureBuilder(db).build(
        rollout_revision_id=prepared.rollout_revision_id,
        lock=True,
    )
    assert gate_use.action == "system_bootstrap"
    assert gate_use.profile_version_id == prepared.profile_version_id
    assert gate_use.skill_version_id == prepared.skill_version_id
    assert gate_use.model_id == core.llm_model_id
    assert gate_use.seed_manifest_digest == prepared.seed_manifest_digest
    assert gate_use.rollout_revision_digest == prepared.rollout_revision_digest
    assert gate_use.closure_digest == closure.closure_digest


def test_stage_bootstrap_requires_matching_operator_and_permit(db):
    from app.assistant.runtime.bootstrap import (
        AssistantBootstrapRejected,
        AssistantSystemBootstrapper,
    )

    bootstrapper = AssistantSystemBootstrapper(db)
    permit = bootstrapper.lock_and_verify_fresh_preconditions()
    operator, core = stage_operator_and_core_fixture(db)
    with pytest.raises(AssistantBootstrapRejected) as exc:
        bootstrapper.stage_bootstrap(
            bootstrap_request_fixture(
                operator_id=uuid4(),
                model_id=core.llm_model_id,
                fresh_permit=permit,
            )
        )
    assert exc.value.reason_code == "operator_mismatch"
    # Successful consume, then reuse of the same permit is rejected.
    prepared = bootstrapper.stage_bootstrap(
        bootstrap_request_fixture(
            operator_id=operator.id,
            model_id=core.llm_model_id,
            fresh_permit=permit,
        )
    )
    assert prepared.rollout_revision_id is not None
    with pytest.raises(AssistantBootstrapRejected) as exc2:
        bootstrapper.stage_bootstrap(
            bootstrap_request_fixture(
                operator_id=operator.id,
                model_id=core.llm_model_id,
                fresh_permit=permit,
            )
        )
    assert exc2.value.reason_code == "fresh_permit_invalid"
