"""Repository tests for Plan 2 Main-Agent rollout control/CAS/replay (Task 2)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding
from tests.test_assistant_runtime_models import prepared_revision_fixture

bootstrap_backend_imports()
reset_caches()

FIXED_REQUEST_ID = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
DIGEST_A = "a" * 64
DIGEST_F = "f" * 64
DIGEST_E = "e" * 64


@pytest.fixture
def db():
    reset_caches()
    session = make_session()
    try:
        yield session
    finally:
        session.close()


def event_fixture(db, *, request_id: uuid.UUID, request_digest: str = DIGEST_A, **overrides):
    from app.assistant.runtime.contracts import NewRolloutEvent
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
    control = repo.get_or_create_control_for_update()
    return NewRolloutEvent(
        action=overrides.get("action", "prepared"),
        from_rollout_revision_id=overrides.get(
            "from_rollout_revision_id", control.active_rollout_revision_id
        ),
        to_rollout_revision_id=overrides.get("to_rollout_revision_id", prepared.id),
        control_revision=overrides.get("control_revision", control.state_revision),
        request_id=request_id,
        request_digest=request_digest,
        operator_id=overrides.get("operator_id"),
        operator_session_id=overrides.get("operator_session_id"),
        reason=overrides.get("reason", "unit-test-event"),
        evidence_digest=overrides.get("evidence_digest", DIGEST_E),
        result_json=overrides.get(
            "result_json",
            {
                "rolloutRevisionId": str(prepared.id),
                "controlRevision": int(control.state_revision),
            },
        ),
    )


def test_request_id_replay_requires_same_digest(db):
    from app.assistant.runtime.contracts import RuntimeRequestReuseConflict
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    event = repo.append_control_event(event_fixture(db, request_id=FIXED_REQUEST_ID))
    assert repo.find_request_event(FIXED_REQUEST_ID).id == event.id
    with pytest.raises(RuntimeRequestReuseConflict):
        repo.assert_request_replay(
            request_id=FIXED_REQUEST_ID,
            request_digest=DIGEST_F,
        )


def test_assert_request_replay_returns_matching_event(db):
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    event = repo.append_control_event(
        event_fixture(db, request_id=FIXED_REQUEST_ID, request_digest=DIGEST_A)
    )
    replayed = repo.assert_request_replay(
        request_id=FIXED_REQUEST_ID,
        request_digest=DIGEST_A,
    )
    assert replayed is not None
    assert replayed.id == event.id


def test_compare_and_set_control_cas(db):
    from app.assistant.runtime.contracts import RuntimeControlConflict
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
    control = repo.get_or_create_control_for_update()
    assert control.state_revision == 0

    updated = repo.compare_and_set_control(
        expected_state_revision=0,
        active_rollout_revision_id=prepared.id,
        new_runs_enabled=True,
    )
    assert updated.state_revision == 1
    assert updated.active_rollout_revision_id == prepared.id

    with pytest.raises(RuntimeControlConflict):
        repo.compare_and_set_control(
            expected_state_revision=0,
            active_rollout_revision_id=prepared.id,
            new_runs_enabled=False,
        )

    active = repo.get_active_revision_for_update()
    assert active is not None
    assert active.id == prepared.id


def test_repository_methods_flush_never_commit(db):
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture(db))
    # Row is visible in-session after flush, but uncommitted.
    assert prepared.id is not None
    assert db.get(type(prepared), prepared.id) is not None
    # No commit called by repository — rolling back drops the row.
    db.rollback()
    assert db.get(type(prepared), prepared.id) is None


def test_control_singleton_key_enforced(db):
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    repo = AssistantRuntimeRepository(db)
    control = repo.get_or_create_control_for_update()
    assert control.control_key == "main_agent"

    db.add(
        AssistantMainAgentRolloutControl(
            control_key="other",
            state_revision=0,
            new_runs_enabled=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
