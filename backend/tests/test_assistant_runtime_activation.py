"""Service tests for prepared rollout, activation CAS, and durable kill-switch (Task 6)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-activation-task6")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-activation-task6"
PASSWORD = "correct horse battery"
REQUEST_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
REQUEST_ID_2 = UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
REQUEST_ID_3 = UUID("cccccccc-dddd-4eee-8fff-000000000000")


@pytest.fixture
def db():
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


def _make_key_ring():
    from app.operator_auth.tokens import SessionMacKeyRing

    return SessionMacKeyRing(active_key_id="k1", keys={"k1": b"k" * 32})


def _settings(**overrides: Any) -> SimpleNamespace:
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@dataclass
class _RuntimeHarness:
    db: Any
    key_ring: Any = field(default_factory=_make_key_ring)
    settings: Any = field(default_factory=_settings)
    operator_account: Any = None
    model: Any = None
    prepared: Any = None
    principal: Any = None
    workers: list[str] = field(default_factory=list)

    def bootstrap_prepared(self, *, register_worker: bool = True) -> Any:
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
        account = OperatorRepository(self.db).seed_account(
            password=PASSWORD,
            role="operator",
            enabled=True,
        )
        self.operator_account = account

        from datetime import timedelta

        from app.common.time import utcnow
        from app.operator_auth.contracts import OperatorPrincipal
        from app.operator_auth.models import OperatorSession

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
        self.db.add(session_row)
        self.db.flush()
        self.principal = OperatorPrincipal(
            operator_id=account.id,
            role="operator",
            session_id=session_row.id,
        )

        _cred, model, _binding = create_default_model_binding(self.db)
        self.model = model

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

        existing = (
            self.db.query(AppSetting)
            .filter(AppSetting.key == SYSTEM_INITIALIZATION_STATE_KEY)
            .one_or_none()
        )
        if existing is None:
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
            self.db.flush()

        if register_worker:
            self.register_worker()
        self.db.commit()
        return prepared

    def register_worker(self) -> str:
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.common.time import utcnow

        worker_id = f"worker-act-{uuid4().hex[:8]}"
        now = utcnow()
        self.db.add(
            AssistantWorkerRegistration(
                worker_id=worker_id,
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
                hostname_label="test",
            )
        )
        self.db.flush()
        self.workers.append(worker_id)
        return worker_id

    def service(self):
        from app.assistant.runtime.activation import AssistantRuntimeActivationService
        from app.assistant.runtime.readiness import AssistantReadinessService

        class _AlwaysCompatible:
            def is_compatible(self, db):  # noqa: ANN001
                return True

        readiness = AssistantReadinessService(
            self.db,
            settings=self.settings,
            schema_compatibility=_AlwaysCompatible(),
            key_ring=self.key_ring,
        )
        return AssistantRuntimeActivationService(
            self.db,
            settings=self.settings,
            readiness=readiness,
            key_ring=self.key_ring,
        )

    def control(self):
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl

        return self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)

    def count_events(self, *, request_id: UUID | None = None) -> int:
        from app.assistant.runtime.models import AssistantMainAgentRolloutEvent

        q = self.db.query(AssistantMainAgentRolloutEvent)
        if request_id is not None:
            q = q.filter(AssistantMainAgentRolloutEvent.request_id == request_id)
        return q.count()


@pytest.fixture
def harness(db) -> _RuntimeHarness:
    return _RuntimeHarness(db=db)


def test_prepare_recomputes_server_owned_subject(harness):
    from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
    from app.assistant.runtime.contracts import PrepareRolloutRequest
    from app.assistant.runtime.models import AssistantMainAgentRolloutRevision
    from app.assistant.skills.models import AssistantMainAgentProfile

    prepared = harness.bootstrap_prepared()
    profile = (
        harness.db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
        .one()
    )
    assert profile.published_version_id is not None

    request_id = REQUEST_ID
    result = harness.service().prepare(
        PrepareRolloutRequest(
            profile_version_id=profile.published_version_id,
            model_id=harness.model.id,
            request_id=request_id,
            reason="qualify reviewed profile",
        ),
        principal=harness.principal,
    )
    row = harness.db.get(AssistantMainAgentRolloutRevision, result.rollout_revision_id)
    assert row is not None
    assert row.revision_digest == result.revision_digest
    assert row.prepared_by_operator_id == harness.principal.operator_id
    subject = AssistantRuntimeClosureBuilder(harness.db).build_subject(
        profile_version_id=profile.published_version_id,
        model_id=harness.model.id,
        build_revision=BUILD,
    )
    assert row.package_closure_json == [dict(item) for item in subject.package_closure]
    # Bootstrap leaves prepared; operator prepare creates a second revision.
    assert result.rollout_revision_id != prepared.rollout_revision_id
    assert result.control_revision == 0
    assert result.active_rollout_revision_id is None


def test_first_activation_sets_pointer_and_enables_new_runs(harness):
    from app.assistant.runtime.contracts import ActivateRolloutRequest

    prepared = harness.bootstrap_prepared()
    result = harness.service().activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="activate initial runtime",
        ),
        principal=harness.principal,
    )
    assert result.active_rollout_revision_id == prepared.rollout_revision_id
    assert result.control_revision == 1
    assert result.new_runs_enabled is True
    control = harness.control()
    assert control is not None
    assert control.active_rollout_revision_id == prepared.rollout_revision_id
    assert control.state_revision == 1
    assert control.new_runs_enabled is True


def test_identical_activation_retry_replays_exact_result(harness):
    from app.assistant.runtime.contracts import ActivateRolloutRequest

    prepared = harness.bootstrap_prepared()
    request = ActivateRolloutRequest(
        expected_control_revision=0,
        request_id=REQUEST_ID,
        reason="activate initial runtime",
    )
    first = harness.service().activate(
        prepared.rollout_revision_id, request, principal=harness.principal
    )
    second = harness.service().activate(
        prepared.rollout_revision_id, request, principal=harness.principal
    )
    assert second == first
    assert harness.count_events(request_id=REQUEST_ID) == 1
    assert harness.control().state_revision == 1


def test_request_id_reuse_with_different_body_conflicts(harness):
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        RuntimeRequestReuseConflict,
    )

    prepared = harness.bootstrap_prepared()
    service = harness.service()
    service.activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="first",
        ),
        principal=harness.principal,
    )
    with pytest.raises(RuntimeRequestReuseConflict):
        service.activate(
            prepared.rollout_revision_id,
            ActivateRolloutRequest(
                expected_control_revision=0,
                request_id=REQUEST_ID,
                reason="changed",
            ),
            principal=harness.principal,
        )


def test_later_activation_preserves_disabled_new_runs(harness):
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        PrepareRolloutRequest,
        SetNewRunsEnabledRequest,
    )
    from app.assistant.skills.models import AssistantMainAgentProfile

    prepared = harness.bootstrap_prepared()
    service = harness.service()
    first = service.activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="activate initial runtime",
        ),
        principal=harness.principal,
    )
    assert first.new_runs_enabled is True

    # Kill switch off while keeping active pointer.
    switched = service.set_new_runs_enabled(
        SetNewRunsEnabledRequest(
            enabled=False,
            expected_control_revision=1,
            request_id=REQUEST_ID_2,
            reason="emergency pause",
        ),
        principal=harness.principal,
    )
    assert switched.new_runs_enabled is False
    assert switched.control_revision == 2

    profile = (
        harness.db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
        .one()
    )
    second_prepared = service.prepare(
        PrepareRolloutRequest(
            profile_version_id=profile.published_version_id,
            model_id=harness.model.id,
            request_id=REQUEST_ID_3,
            reason="prepare known-good successor",
        ),
        principal=harness.principal,
    )
    result = service.activate(
        second_prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=2,
            request_id=uuid4(),
            reason="switch while paused",
        ),
        principal=harness.principal,
    )
    assert result.active_rollout_revision_id == second_prepared.rollout_revision_id
    assert result.new_runs_enabled is False
    assert result.control_revision == 3
    # Superseded event uses derived request id, not the caller's.
    assert harness.count_events(request_id=REQUEST_ID) == 1


def test_stale_expected_control_revision_conflicts(harness):
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        RuntimeControlConflict,
    )

    prepared = harness.bootstrap_prepared()
    service = harness.service()
    service.activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="first",
        ),
        principal=harness.principal,
    )
    with pytest.raises(RuntimeControlConflict):
        service.activate(
            prepared.rollout_revision_id,
            ActivateRolloutRequest(
                expected_control_revision=0,
                request_id=REQUEST_ID_2,
                reason="stale",
            ),
            principal=harness.principal,
        )


def test_activation_without_worker_is_rejected(harness):
    from app.assistant.runtime.contracts import ActivateRolloutRequest
    from app.assistant.runtime.activation import RuntimeActivationRejected

    prepared = harness.bootstrap_prepared(register_worker=False)
    with pytest.raises(RuntimeActivationRejected) as excinfo:
        harness.service().activate(
            prepared.rollout_revision_id,
            ActivateRolloutRequest(
                expected_control_revision=0,
                request_id=REQUEST_ID,
                reason="no worker",
            ),
            principal=harness.principal,
        )
    assert excinfo.value.reason_code == "worker_unavailable"
    assert harness.control().active_rollout_revision_id is None
    assert harness.control().state_revision == 0


def test_set_new_runs_enabled_is_idempotent(harness):
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        SetNewRunsEnabledRequest,
    )

    prepared = harness.bootstrap_prepared()
    service = harness.service()
    service.activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="activate",
        ),
        principal=harness.principal,
    )
    body = SetNewRunsEnabledRequest(
        enabled=False,
        expected_control_revision=1,
        request_id=REQUEST_ID_2,
        reason="pause",
    )
    first = service.set_new_runs_enabled(body, principal=harness.principal)
    second = service.set_new_runs_enabled(body, principal=harness.principal)
    assert second == first
    assert harness.count_events(request_id=REQUEST_ID_2) == 1
    assert harness.control().new_runs_enabled is False


def test_unknown_revision_not_prepared(harness):
    from app.assistant.runtime.activation import RolloutNotPrepared
    from app.assistant.runtime.contracts import ActivateRolloutRequest

    harness.bootstrap_prepared()
    with pytest.raises(RolloutNotPrepared):
        harness.service().activate(
            uuid4(),
            ActivateRolloutRequest(
                expected_control_revision=0,
                request_id=REQUEST_ID,
                reason="missing",
            ),
            principal=harness.principal,
        )


def test_superseded_event_uses_derived_request_id(harness):
    from app.assistant.runtime.contracts import (
        ActivateRolloutRequest,
        PrepareRolloutRequest,
    )
    from app.assistant.runtime.models import AssistantMainAgentRolloutEvent
    from app.assistant.skills.models import AssistantMainAgentProfile
    from uuid import uuid5

    prepared = harness.bootstrap_prepared()
    service = harness.service()
    service.activate(
        prepared.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="first",
        ),
        principal=harness.principal,
    )
    profile = (
        harness.db.query(AssistantMainAgentProfile)
        .filter(AssistantMainAgentProfile.published_version_id.isnot(None))
        .one()
    )
    second = service.prepare(
        PrepareRolloutRequest(
            profile_version_id=profile.published_version_id,
            model_id=harness.model.id,
            request_id=REQUEST_ID_2,
            reason="second prepare",
        ),
        principal=harness.principal,
    )
    activate_id = REQUEST_ID_3
    service.activate(
        second.rollout_revision_id,
        ActivateRolloutRequest(
            expected_control_revision=1,
            request_id=activate_id,
            reason="second activate",
        ),
        principal=harness.principal,
    )
    derived = uuid5(activate_id, "superseded")
    events = (
        harness.db.query(AssistantMainAgentRolloutEvent)
        .filter(AssistantMainAgentRolloutEvent.request_id == derived)
        .all()
    )
    assert len(events) == 1
    assert events[0].action == "superseded"
    assert events[0].from_rollout_revision_id == prepared.rollout_revision_id
    assert events[0].to_rollout_revision_id == second.rollout_revision_id
