"""Shared assistant readiness evaluator (Plan 2 Task 5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import event

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session
from tests.agent_skill_test_support import create_default_model_binding

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_BUILD_REVISION", "test-build-readiness-task5")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-readiness-task5"
PASSWORD = "correct horse battery"


@pytest.fixture
def db():
    reset_caches()
    from app.config import get_settings

    get_settings.cache_clear()
    # Force process switch on for baseline readiness.
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
    s = get_settings()
    # Settings is frozen-ish via pydantic; use SimpleNamespace overlay for service.
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
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

    def readiness(self):
        from app.assistant.runtime.readiness import (
            AssistantReadinessService,
            Plan2AlembicHeadCompatibility,
        )

        class _Schema(Plan2AlembicHeadCompatibility):
            def is_compatible(self, db):  # noqa: ANN001
                return bool(_RuntimeState_ref.schema_ok)

        # Bind outer self for closure.
        _RuntimeState_ref = self

        class _SeedProbe:
            def is_valid(self) -> bool:
                return bool(_RuntimeState_ref.seed_ok)

        return AssistantReadinessService(
            self.db,
            settings=self.settings,
            schema_compatibility=_Schema(),
            key_ring=self.key_ring if self.key_ring is not False else None,
            seed_probe=_SeedProbe(),
        )

    def arrange(self, arrangement: str) -> None:
        if arrangement == "wrong_schema":
            self.schema_ok = False
            return
        if arrangement == "uninitialized":
            # Empty DB — nothing to do.
            return
        if arrangement == "operator_missing":
            self._mark_initialized()
            return
        if arrangement == "auth_unavailable":
            self._mark_initialized()
            self._seed_operator()
            self.key_ring = False  # force None key ring
            return
        if arrangement == "seed_drift":
            self._mark_initialized()
            self._seed_operator()
            self.seed_ok = False
            return
        if arrangement == "profile_missing":
            self._mark_initialized()
            self._seed_operator()
            create_default_model_binding(self.db)
            return
        if arrangement == "model_missing":
            self._bootstrap_prepared(activate=False, bind_model=False)
            # Clear assistant binding.
            from app.ai_registry.models import AiComponentBinding

            binding = (
                self.db.query(AiComponentBinding)
                .filter(AiComponentBinding.component == "assistant")
                .one_or_none()
            )
            if binding is not None:
                binding.llm_model_id = None
                self.db.flush()
            return
        if arrangement == "no_active_rollout":
            self._bootstrap_prepared(activate=False)
            return
        if arrangement == "closure_drift":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            # Drift model identity after activation.
            assert self.model is not None
            self.model.runtime_revision = int(self.model.runtime_revision or 1) + 7
            self.db.flush()
            return
        if arrangement == "worker_missing":
            self._bootstrap_prepared(activate=True)
            # No workers registered.
            return
        if arrangement == "process_switch_off":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            self.settings = _settings(assistant_new_runs_enabled=False)
            return
        if arrangement == "durable_switch_off":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
            from app.assistant.runtime.models import AssistantMainAgentRolloutControl

            control = self.db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT)
            assert control is not None
            control.new_runs_enabled = False
            self.db.flush()
            return
        if arrangement == "ready":
            self._bootstrap_prepared(activate=True)
            self._register_worker()
            return
        raise AssertionError(f"unknown arrangement: {arrangement}")

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

    def _seed_operator(self) -> None:
        from app.operator_auth.repository import OperatorRepository

        self.operator = OperatorRepository(self.db).seed_account(
            password=PASSWORD,
            role="operator",
            enabled=True,
        )

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
            SystemInitializationService,
        )
        from app.system_settings.models import AppSetting
        from app.system_settings.schemas import InitializeSystemRequest

        bootstrapper = AssistantSystemBootstrapper(self.db)
        permit = bootstrapper.lock_and_verify_fresh_preconditions()

        account = OperatorRepository(self.db).seed_account(
            password=PASSWORD,
            role="operator",
            enabled=True,
        )
        self.operator = account

        if bind_model:
            _cred, model, _binding = create_default_model_binding(self.db)
            self.model = model
            model_id = model.id
        else:
            # Still need a model row for FK on prepared revision, but unbind later.
            _cred, model, _binding = create_default_model_binding(self.db)
            self.model = model
            model_id = model.id

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

        # Mark system initialized (bootstrap itself does not set the marker).
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
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            WorkerIdentity,
            WorkerRegistry,
            default_capability_feature_digest,
        )
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.common.time import utcnow

        worker_id = f"worker-ready-{uuid4().hex[:8]}"
        # WorkerRegistry.register commits — avoid that in unit tests; insert row.
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


@pytest.mark.parametrize(
    ("arrangement", "expected_reason"),
    [
        ("uninitialized", "system_not_initialized"),
        ("operator_missing", "operator_missing"),
        ("auth_unavailable", "operator_auth_unavailable"),
        ("seed_drift", "system_seed_invalid"),
        ("profile_missing", "profile_unpublished"),
        ("model_missing", "model_unbound"),
        ("no_active_rollout", "rollout_inactive"),
        ("closure_drift", "runtime_closure_drift"),
        ("worker_missing", "worker_unavailable"),
        ("wrong_schema", "schema_incompatible"),
        ("process_switch_off", "new_runs_disabled"),
        ("durable_switch_off", "new_runs_disabled"),
    ],
)
def test_readiness_reason_matrix(runtime_state, arrangement, expected_reason):
    runtime_state.arrange(arrangement)
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.ready is False
    assert expected_reason in snapshot.reason_codes


def test_readiness_ready_path(runtime_state):
    runtime_state.arrange("ready")
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.ready is True
    assert snapshot.reason_codes == ()
    assert snapshot.active_rollout_revision_id is not None
    assert snapshot.profile_version_id is not None
    assert snapshot.model_id is not None
    assert snapshot.compatible_worker_ids
    assert snapshot.build_revision == BUILD


def test_multiple_reasons_use_fixed_order(runtime_state):
    """worker_unavailable before new_runs_disabled regardless of discovery order."""
    from app.assistant.runtime.contracts import RUNTIME_READINESS_REASON_CODES

    runtime_state.arrange("worker_missing")
    # Also disable process switch so two independent post-closure reasons fire.
    runtime_state.settings = _settings(assistant_new_runs_enabled=False)
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.ready is False
    assert "worker_unavailable" in snapshot.reason_codes
    assert "new_runs_disabled" in snapshot.reason_codes
    # Fixed order from the canonical tuple — not set/query order.
    positions = {
        code: RUNTIME_READINESS_REASON_CODES.index(code)
        for code in snapshot.reason_codes
    }
    assert positions["worker_unavailable"] < positions["new_runs_disabled"]
    assert list(snapshot.reason_codes) == sorted(
        snapshot.reason_codes,
        key=lambda c: RUNTIME_READINESS_REASON_CODES.index(c),
    )


def test_structural_reason_is_singular(runtime_state):
    """Fresh DB reports exactly system_not_initialized, not every downstream code."""
    runtime_state.arrange("uninitialized")
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.reason_codes == ("system_not_initialized",)
    assert snapshot.active_rollout_revision_id is None
    assert snapshot.profile_version_id is None
    assert snapshot.model_id is None
    assert snapshot.compatible_worker_ids == ()


def test_prepared_inactive_reports_compatible_workers(runtime_state):
    """Pending first activation still surfaces workers against the prepared revision."""
    runtime_state.arrange("no_active_rollout")
    runtime_state._register_worker()
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.ready is False
    assert snapshot.reason_codes == ("rollout_inactive",)
    assert snapshot.active_rollout_revision_id is None
    assert snapshot.compatible_worker_ids
    assert snapshot.profile_version_id is not None
    assert snapshot.model_id is not None


def test_prepared_inactive_without_worker_reports_both_reasons(runtime_state):
    """Prepared-but-inactive diagnostics co-report worker_unavailable when empty."""
    runtime_state.arrange("no_active_rollout")
    snapshot = runtime_state.readiness().evaluate()
    assert snapshot.ready is False
    assert snapshot.reason_codes == ("rollout_inactive", "worker_unavailable")
    assert snapshot.active_rollout_revision_id is None
    assert snapshot.compatible_worker_ids == ()
    assert snapshot.profile_version_id is not None
    assert snapshot.model_id is not None


def test_readiness_performs_no_dml(db):
    from app.assistant.runtime.readiness import AssistantReadinessService

    statements: list[str] = []

    def _before_cursor(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(str(statement))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", _before_cursor)
    try:
        service = AssistantReadinessService(
            db,
            settings=_settings(),
            schema_compatibility=_AlwaysCompatible(),
            key_ring=_make_key_ring(),
        )
        service.evaluate()
    finally:
        event.remove(bind, "before_cursor_execute", _before_cursor)

    dml = [
        s
        for s in statements
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert dml == [], f"readiness must be observational; saw DML: {dml}"


def test_evaluate_does_not_create_control_singleton(db):
    from app.assistant.runtime.contracts import CONTROL_KEY_MAIN_AGENT
    from app.assistant.runtime.models import AssistantMainAgentRolloutControl
    from app.assistant.runtime.readiness import AssistantReadinessService

    assert db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT) is None
    AssistantReadinessService(
        db,
        settings=_settings(),
        schema_compatibility=_AlwaysCompatible(),
        key_ring=_make_key_ring(),
    ).evaluate()
    assert db.get(AssistantMainAgentRolloutControl, CONTROL_KEY_MAIN_AGENT) is None


def test_public_projection_exposes_only_ready_and_reasons(runtime_state):
    from app.assistant.runtime.readiness import project_public_readiness

    runtime_state.arrange("uninitialized")
    snapshot = runtime_state.readiness().evaluate()
    public = project_public_readiness(snapshot)
    assert set(public.keys()) == {"ready", "reasonCodes"}
    assert public["ready"] is False
    assert public["reasonCodes"] == ["system_not_initialized"]


def test_authenticated_projection_may_expose_safe_ids(runtime_state):
    from app.assistant.runtime.readiness import project_authenticated_readiness

    runtime_state.arrange("ready")
    snapshot = runtime_state.readiness().evaluate()
    auth = project_authenticated_readiness(snapshot)
    assert auth["ready"] is True
    assert auth["reasonCodes"] == []
    assert auth["activeRolloutRevisionId"] == str(snapshot.active_rollout_revision_id)
    assert auth["profileVersionId"] == str(snapshot.profile_version_id)
    assert auth["modelId"] == str(snapshot.model_id)
    assert auth["compatibleWorkerIds"] == list(snapshot.compatible_worker_ids)
    assert auth["buildRevision"] == BUILD
    # No secrets.
    blob = str(auth)
    assert "api_key" not in blob.lower()
    assert "password" not in blob.lower()


class _AlwaysCompatible:
    def is_compatible(self, db) -> bool:  # noqa: ANN001
        return True


def _settings_with_session_mac_ring(**overrides):
    """Real Settings carrying a parseable session-MAC ring (production default path)."""
    import base64
    import json

    from app.config import Settings

    material = base64.b64encode(b"k" * 32).decode("ascii")
    encoded = json.dumps({"k1": material})
    kwargs = {
        "APP_ENV": "test",
        "APP_BUILD_REVISION": BUILD,
        "ASSISTANT_NEW_RUNS_ENABLED": "true",
        "MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID": "k1",
        "MINDATLAS_SESSION_HMAC_KEYS": encoded,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_bare_readiness_defaults_key_ring_from_settings(db):
    """Omitted key_ring loads via load_session_mac_key_ring(settings), like production auth.

    Bare AssistantReadinessService(db, settings=...) must not force
    operator_auth_unavailable solely because no ring was injected.
    """
    from app.assistant.runtime.readiness import AssistantReadinessService
    from app.operator_auth.dependencies import load_session_mac_key_ring

    settings = _settings_with_session_mac_ring()
    expected = load_session_mac_key_ring(settings)
    assert expected is not None

    state = _RuntimeState(db=db, key_ring=_make_key_ring())
    state._mark_initialized()
    state._seed_operator()

    # No key_ring= argument — production default path.
    service = AssistantReadinessService(
        db,
        settings=settings,
        schema_compatibility=_AlwaysCompatible(),
        seed_probe=SimpleNamespace(is_valid=lambda: True),
    )
    assert service.key_ring is not None
    assert service.key_ring.active_key_id == expected.active_key_id
    assert set(service.key_ring.keys) == set(expected.keys)

    snapshot = service.evaluate()
    assert "operator_auth_unavailable" not in snapshot.reason_codes


def test_explicit_none_key_ring_still_forces_auth_unavailable(db):
    """Tests may still inject key_ring=None to force operator_auth_unavailable."""
    from app.assistant.runtime.readiness import AssistantReadinessService

    settings = _settings_with_session_mac_ring()
    state = _RuntimeState(db=db, key_ring=_make_key_ring())
    state._mark_initialized()
    state._seed_operator()

    snapshot = AssistantReadinessService(
        db,
        settings=settings,
        schema_compatibility=_AlwaysCompatible(),
        key_ring=None,
        seed_probe=SimpleNamespace(is_valid=lambda: True),
    ).evaluate()
    assert snapshot.ready is False
    assert snapshot.reason_codes == ("operator_auth_unavailable",)
