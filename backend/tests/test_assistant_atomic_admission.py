"""Atomic Main-Agent-only Chat admission (Plan 2 Task 8).

Pre-insert failures leave no Message / Run / event residue. Success freezes the
active runtime closure onto one main_agent Run + one initial public event.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-admission-task8")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-admission-task8"
PASSWORD = "correct horse battery"

# Brief arrangement name → readiness harness arrangement.
_ARRANGEMENT_ALIASES = {
    "rollout_inactive": "no_active_rollout",
    "schema_incompatible": "wrong_schema",
}


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

    material = b"k" * 32
    return SessionMacKeyRing(active_key_id="k1", keys={"k1": material})


def _settings(**overrides):
    from app.config import get_settings

    get_settings.cache_clear()
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
        "assistant_capability_ledger_mode": "legacy_read_only",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@dataclass
class _RuntimeState:
    db: Any
    key_ring: Any
    schema_ok: bool = True
    seed_ok: bool = True
    settings: Any = field(default_factory=_settings)
    model: Any = None
    operator: Any = None
    prepared: Any = None
    workers: list[str] = field(default_factory=list)
    conversation: Any = None
    closure: Any = None

    def readiness(self):
        from app.assistant.runtime.readiness import (
            AssistantReadinessService,
            Plan2AlembicHeadCompatibility,
        )

        state = self

        class _Schema(Plan2AlembicHeadCompatibility):
            def is_compatible(self, db):  # noqa: ANN001, ARG002
                return bool(state.schema_ok)

        class _SeedProbe:
            def is_valid(self) -> bool:
                return bool(state.seed_ok)

        return AssistantReadinessService(
            self.db,
            settings=self.settings,
            schema_compatibility=_Schema(),
            key_ring=self.key_ring if self.key_ring is not False else None,
            seed_probe=_SeedProbe(),
        )

    def admission(self):
        from app.assistant.runtime.admission import AssistantChatAdmissionService

        return AssistantChatAdmissionService(
            self.db,
            settings=self.settings,
            readiness=self.readiness(),
        )

    def arrange(self, arrangement: str) -> None:
        key = _ARRANGEMENT_ALIASES.get(arrangement, arrangement)
        if key == "wrong_schema":
            # Admission locks control first; keep a control row so the shared
            # evaluator can surface schema_incompatible ahead of rollout gates.
            from app.assistant.runtime.repository import AssistantRuntimeRepository

            AssistantRuntimeRepository(self.db).get_or_create_control_for_update()
            self.schema_ok = False
            return
        if key == "uninitialized":
            return
        if key == "no_active_rollout":
            self._bootstrap_prepared(activate=False)
            return
        if key == "closure_drift":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            assert self.model is not None
            self.model.runtime_revision = int(self.model.runtime_revision or 1) + 7
            self.db.flush()
            return
        if key == "worker_missing":
            self._bootstrap_prepared(activate=True)
            return
        if key == "process_switch_off":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            self.settings = _settings(assistant_new_runs_enabled=False)
            return
        if key == "durable_switch_off":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
            from app.assistant.runtime.models import AssistantMainAgentRolloutControl

            control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
            assert control is not None
            control.new_runs_enabled = False
            self.db.flush()
            return
        if key == "ready":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            self._capture_closure()
            return
        raise AssertionError(f"unknown arrangement: {arrangement}")

    def ensure_conversation(self):
        from app.assistant.models import Conversation

        if self.conversation is not None:
            return self.conversation
        conversation = Conversation(title="admission-test")
        self.db.add(conversation)
        self.db.flush()
        self.conversation = conversation
        return conversation

    def chat_owned_counts(self, conversation_id) -> dict[str, int]:
        from app.assistant.models import (
            AssistantChatRun,
            AssistantChatRunEvent,
            Message,
        )

        user_messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id, Message.role == "user")
            .count()
        )
        assistant_messages = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conversation_id, Message.role == "assistant"
            )
            .count()
        )
        runs = (
            self.db.query(AssistantChatRun)
            .filter(AssistantChatRun.conversation_id == conversation_id)
            .count()
        )
        run_ids = [
            row.id
            for row in self.db.query(AssistantChatRun.id)
            .filter(AssistantChatRun.conversation_id == conversation_id)
            .all()
        ]
        initial_events = 0
        if run_ids:
            initial_events = (
                self.db.query(AssistantChatRunEvent)
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

    def count_initial_events(self, run_id) -> int:
        from app.assistant.models import AssistantChatRunEvent

        return (
            self.db.query(AssistantChatRunEvent)
            .filter(
                AssistantChatRunEvent.run_id == run_id,
                AssistantChatRunEvent.event_name == "run_status",
            )
            .count()
        )

    def _capture_closure(self) -> None:
        from app.assistant.runtime.closure import AssistantRuntimeClosureBuilder
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl

        control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
        assert control is not None and control.active_rollout_revision_id is not None
        self.closure = AssistantRuntimeClosureBuilder(self.db).build(
            rollout_revision_id=control.active_rollout_revision_id,
            lock=False,
        )

    def _mark_initialized(self) -> None:
        from app.system_settings.initialization_service import (
            SYSTEM_INITIALIZATION_STATE_KEY,
        )
        from app.system_settings.models import AppSetting

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

    def _bootstrap_prepared(self, *, activate: bool, bind_model: bool = True) -> None:
        from app.assistant.runtime.bootstrap import (
            AssistantSystemBootstrapper,
            StageAssistantBootstrapRequest,
        )
        from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
        from app.assistant.runtime.models import AssistantMainAgentRolloutControl
        from app.config import get_settings
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
        self.operator = account

        _cred, model, _binding = create_default_model_binding(self.db)
        self.model = model
        model_id = model.id
        if not bind_model:
            from app.ai_registry.models import AiComponentBinding

            binding = (
                self.db.query(AiComponentBinding)
                .filter(AiComponentBinding.component == "assistant")
                .one_or_none()
            )
            if binding is not None:
                binding.llm_model_id = None
                self.db.flush()

        prepared = bootstrapper.stage_bootstrap(
            StageAssistantBootstrapRequest(
                operator_id=account.id,
                operator_session_id=None,
                model_id=model_id,
                build_revision=get_settings().app_build_revision or BUILD,
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
            self._mark_initialized()

        if activate:
            control = self.db.get(
                AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT
            )
            if control is None:
                from app.assistant.runtime.repository import AssistantRuntimeRepository

                control = AssistantRuntimeRepository(
                    self.db
                ).get_or_create_control_for_update()
            control.active_rollout_revision_id = prepared.rollout_revision_id
            control.state_revision = max(int(control.state_revision or 0), 1)
            control.new_runs_enabled = True
            self.db.flush()

    def _register_worker(self) -> None:
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.common.time import utcnow

        worker_id = f"worker-admit-{uuid4().hex[:8]}"
        now = utcnow()
        row = AssistantWorkerRegistration(
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
        self.db.add(row)
        self.db.flush()
        self.workers.append(worker_id)


@pytest.fixture
def runtime_state(db):
    return _RuntimeState(db=db, key_ring=_make_key_ring())


@pytest.fixture
def conversation(runtime_state):
    return runtime_state.ensure_conversation()


@pytest.fixture
def ready_runtime(runtime_state):
    runtime_state.arrange("ready")
    return runtime_state


def arrange_runtime_state(runtime_state: _RuntimeState, arrangement: str) -> None:
    runtime_state.arrange(arrangement)


def chat_owned_counts(runtime_state: _RuntimeState, conversation_id) -> dict[str, int]:
    return runtime_state.chat_owned_counts(conversation_id)


def count_initial_events(runtime_state: _RuntimeState, run_id) -> int:
    return runtime_state.count_initial_events(run_id)


@pytest.mark.parametrize(
    ("arrangement", "reason"),
    [
        ("rollout_inactive", "rollout_inactive"),
        ("closure_drift", "runtime_closure_drift"),
        ("worker_missing", "worker_unavailable"),
        ("process_switch_off", "new_runs_disabled"),
        ("durable_switch_off", "new_runs_disabled"),
        ("schema_incompatible", "schema_incompatible"),
    ],
)
def test_preinsert_admission_failure_has_no_residue(
    runtime_state, conversation, arrangement, reason
):
    from app.assistant.runtime.admission import (
        AssistantAdmissionError,
        AssistantChatAdmissionService,
    )

    arrange_runtime_state(runtime_state, arrangement)
    before = chat_owned_counts(runtime_state, conversation.id)
    with pytest.raises(AssistantAdmissionError) as exc:
        AssistantChatAdmissionService(
            runtime_state.db,
            settings=runtime_state.settings,
            readiness=runtime_state.readiness(),
        ).admit_and_create(
            conversation_id=conversation.id,
            user_message="hello",
        )
    assert exc.value.reason_code == reason
    # Residue must be empty even if the outer session still holds unflushed work.
    runtime_state.db.expire_all()
    assert chat_owned_counts(runtime_state, conversation.id) == before


def test_success_freezes_active_closure_on_one_main_agent_run(
    ready_runtime, conversation
):
    from app.assistant.runtime.admission import AssistantChatAdmissionService

    run = AssistantChatAdmissionService(
        ready_runtime.db,
        settings=ready_runtime.settings,
        readiness=ready_runtime.readiness(),
    ).admit_and_create(
        conversation_id=conversation.id,
        user_message="hello",
    )
    closure = ready_runtime.closure
    assert closure is not None
    assert run.runtime_kind == "main_agent"
    assert run.main_agent_rollout_revision_id == closure.rollout_revision_id
    assert run.main_agent_profile_version_id == closure.profile_version_id
    assert run.resolved_model_id == closure.model_id
    assert run.runtime_closure_digest == closure.closure_digest
    assert run.required_app_build_revision == closure.build_revision
    assert count_initial_events(ready_runtime, run.id) == 1
    counts = chat_owned_counts(ready_runtime, conversation.id)
    assert counts == {
        "user_messages": 1,
        "assistant_messages": 1,
        "runs": 1,
        "initial_events": 1,
    }


def test_forced_failure_after_message_flush_rolls_back_all_rows(
    ready_runtime, conversation, monkeypatch
):
    from app.assistant.runtime.admission import (
        AssistantAdmissionError,
        AssistantChatAdmissionService,
    )
    from app.assistant.run_service import AssistantChatRunService

    before = chat_owned_counts(ready_runtime, conversation.id)
    original = AssistantChatRunService.create_run

    def _boom(self, *args, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("inject-create-run-failure")

    monkeypatch.setattr(AssistantChatRunService, "create_run", _boom)
    service = AssistantChatAdmissionService(
        ready_runtime.db,
        settings=ready_runtime.settings,
        readiness=ready_runtime.readiness(),
    )
    with pytest.raises(RuntimeError, match="inject-create-run-failure"):
        service.admit_and_create(
            conversation_id=conversation.id,
            user_message="hello",
        )
    ready_runtime.db.expire_all()
    assert chat_owned_counts(ready_runtime, conversation.id) == before

    # Restore and prove success still works on a clean conversation path.
    monkeypatch.setattr(AssistantChatRunService, "create_run", original)


def test_forced_failure_after_run_insert_rolls_back_all_rows(
    ready_runtime, conversation, monkeypatch
):
    from app.assistant.runtime.admission import AssistantChatAdmissionService
    from app.assistant.run_service import AssistantChatRunService

    before = chat_owned_counts(ready_runtime, conversation.id)

    def _boom(self, *args, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("inject-append-event-failure")

    monkeypatch.setattr(AssistantChatRunService, "append_event", _boom)
    service = AssistantChatAdmissionService(
        ready_runtime.db,
        settings=ready_runtime.settings,
        readiness=ready_runtime.readiness(),
    )
    with pytest.raises(RuntimeError, match="inject-append-event-failure"):
        service.admit_and_create(
            conversation_id=conversation.id,
            user_message="hello",
        )
    ready_runtime.db.expire_all()
    assert chat_owned_counts(ready_runtime, conversation.id) == before


def test_active_run_blocks_second_admission(ready_runtime, conversation):
    from app.assistant.runtime.admission import (
        AssistantChatAdmissionService,
        ConcurrentChatAdmission,
    )

    service = AssistantChatAdmissionService(
        ready_runtime.db,
        settings=ready_runtime.settings,
        readiness=ready_runtime.readiness(),
    )
    first = service.admit_and_create(
        conversation_id=conversation.id,
        user_message="first",
    )
    assert first.runtime_kind == "main_agent"
    with pytest.raises(ConcurrentChatAdmission):
        service.admit_and_create(
            conversation_id=conversation.id,
            user_message="second",
        )
    counts = chat_owned_counts(ready_runtime, conversation.id)
    assert counts["runs"] == 1
    assert counts["user_messages"] == 1
    assert counts["assistant_messages"] == 1


def test_post_insert_failure_stays_on_same_run(ready_runtime, conversation):
    from app.assistant.runtime.admission import AssistantChatAdmissionService
    from app.assistant.run_service import AssistantChatRunService

    service = AssistantChatAdmissionService(
        ready_runtime.db,
        settings=ready_runtime.settings,
        readiness=ready_runtime.readiness(),
    )
    run = service.admit_and_create(
        conversation_id=conversation.id,
        user_message="hello",
    )
    failed = AssistantChatRunService(ready_runtime.db).update_run_status(
        run_id=run.id,
        status="failed",
        error_message="provider_injected_failure",
    )
    assert failed.id == run.id
    assert failed.status == "failed"
    counts = chat_owned_counts(ready_runtime, conversation.id)
    assert counts["runs"] == 1
    # No second admission / legacy path.
    assert failed.runtime_kind == "main_agent"


def test_chat_stream_maps_admission_error_to_503(ready_runtime, conversation, monkeypatch):
    from app.assistant.runtime.admission import AssistantAdmissionError
    from app.assistant.service import AssistantService
    from app.common.exceptions import ApiException

    def _deny(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssistantAdmissionError("worker_unavailable")

    monkeypatch.setattr(
        "app.assistant.runtime.admission.AssistantChatAdmissionService.admit_and_create",
        _deny,
    )
    with pytest.raises(ApiException) as exc:
        list(
            AssistantService(ready_runtime.db).chat_stream(
                conversation.id, "hello", stream_output=False
            )
        )
    assert exc.value.status_code == 503
    assert exc.value.code == 50310
    details = getattr(exc.value, "details", None) or {}
    assert details.get("admissionReason") == "assistant_worker_unavailable"


def test_chat_stream_does_not_call_legacy_selector(
    ready_runtime, conversation, monkeypatch
):
    from app.assistant.models import AssistantChatRun
    from app.assistant.runtime.admission import AssistantChatAdmissionService
    from app.assistant.service import AssistantService

    calls: list[str] = []

    def _track_admit(*args, **kwargs):  # noqa: ANN001, ARG001
        calls.append("legacy_admit")
        raise AssertionError("legacy admit_and_select_runtime must not be called")

    monkeypatch.setattr(
        "app.assistant.durable.admission.admit_and_select_runtime",
        _track_admit,
    )

    # chat_stream constructs AssistantChatAdmissionService(self.db) with default
    # readiness probes. Bridge to the ready harness so SQLite unit tests do not
    # depend on the Plan 2 alembic head row, while still exercising the live
    # chat_stream path (no legacy selector).
    original_admit = AssistantChatAdmissionService.admit_and_create

    def _admit_and_create(self, *, conversation_id, user_message):  # noqa: ANN001
        harness = AssistantChatAdmissionService(
            ready_runtime.db,
            settings=ready_runtime.settings,
            readiness=ready_runtime.readiness(),
        )
        return original_admit(
            harness,
            conversation_id=conversation_id,
            user_message=user_message,
        )

    monkeypatch.setattr(
        AssistantChatAdmissionService,
        "admit_and_create",
        _admit_and_create,
    )

    def _fake_stream(self, conversation_id, *, run_id, after_seq=0):  # noqa: ANN001
        yield b"event: ok\ndata: {}\n\n"

    monkeypatch.setattr(AssistantService, "stream_run", _fake_stream)

    chunks = list(
        AssistantService(ready_runtime.db).chat_stream(
            conversation.id, "hello", stream_output=False
        )
    )
    assert chunks
    assert calls == []
    runs = (
        ready_runtime.db.query(AssistantChatRun)
        .filter(AssistantChatRun.conversation_id == conversation.id)
        .all()
    )
    assert len(runs) == 1
    assert runs[0].runtime_kind == "main_agent"
