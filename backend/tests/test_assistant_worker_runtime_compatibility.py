"""Worker readiness compatibility matrix (Plan 2 Task 7).

Proves one canonical WorkerCompatibility path drives readiness projections:
build / contract / codec / feature digest / heartbeat / draining drift all
surface as worker_unavailable, and compatible worker IDs are ordered ASC.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
os.environ.setdefault("APP_BUILD_REVISION", "test-build-worker-compat-task7")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY",
    "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=",
)

BUILD = "test-build-worker-compat-task7"
PASSWORD = "correct horse battery"


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
    base = {
        "app_build_revision": BUILD,
        "assistant_new_runs_enabled": True,
        "assistant_worker_registration_ttl_sec": 20,
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
    last_worker_id: str | None = None
    _ready: bool = False

    @property
    def readiness(self):
        from app.assistant.runtime.readiness import (
            AssistantReadinessService,
            Plan2AlembicHeadCompatibility,
        )

        outer = self

        class _Schema(Plan2AlembicHeadCompatibility):
            def is_compatible(self, db):  # noqa: ANN001, ARG002
                return bool(outer.schema_ok)

        class _SeedProbe:
            def is_valid(self) -> bool:
                return bool(outer.seed_ok)

        return AssistantReadinessService(
            self.db,
            settings=self.settings,
            schema_compatibility=_Schema(),
            key_ring=self.key_ring if self.key_ring is not False else None,
            seed_probe=_SeedProbe(),
        )

    def ensure_ready_base(self) -> None:
        if self._ready:
            return
        self._bootstrap_prepared(activate=True)
        self._ready = True

    def register_worker(self, worker_id: str | None = None, **overrides: Any) -> str:
        """Insert a fresh compatible worker registration (no registry commit)."""
        from app.assistant.durable.codec import CURRENT_CHECKPOINT_CODEC_VERSION
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.assistant.durable.worker_registry import (
            RUNTIME_CONTRACT_VERSION,
            default_capability_feature_digest,
        )
        from app.common.time import utcnow

        self.ensure_ready_base()
        wid = worker_id or f"worker-ready-{uuid4().hex[:8]}"
        now = utcnow()
        row = AssistantWorkerRegistration(
            worker_id=wid,
            app_build_revision=str(overrides.get("app_build_revision", BUILD)),
            runtime_contract_version=int(
                overrides.get("runtime_contract_version", RUNTIME_CONTRACT_VERSION)
            ),
            supported_checkpoint_codec_versions=list(
                overrides.get(
                    "supported_checkpoint_codec_versions",
                    [1, 2, int(CURRENT_CHECKPOINT_CODEC_VERSION)],
                )
            ),
            capability_feature_digest=str(
                overrides.get(
                    "capability_feature_digest",
                    default_capability_feature_digest(),
                )
            ),
            started_at=now,
            heartbeat_at=overrides.get("heartbeat_at", now),
            draining_at=overrides.get("draining_at"),
            hostname_label="test",
        )
        self.db.add(row)
        self.db.flush()
        self.workers.append(wid)
        self.last_worker_id = wid
        return wid

    def drift_worker(self, drift: str, *, worker_id: str | None = None) -> None:
        from app.assistant.durable.models import AssistantWorkerRegistration
        from app.common.time import utcnow

        wid = worker_id or self.last_worker_id
        assert wid is not None, "register_worker before drift_worker"
        row = self.db.get(AssistantWorkerRegistration, wid)
        assert row is not None
        if drift == "build_revision":
            row.app_build_revision = f"drifted-{BUILD}"
        elif drift == "runtime_contract_version":
            row.runtime_contract_version = int(row.runtime_contract_version) + 99
        elif drift == "checkpoint_codec_version":
            row.supported_checkpoint_codec_versions = [99]
        elif drift == "capability_feature_digest":
            row.capability_feature_digest = "f" * 64
        elif drift == "stale_heartbeat":
            row.heartbeat_at = utcnow() - timedelta(hours=2)
        elif drift == "draining":
            row.draining_at = utcnow()
        else:
            raise AssertionError(f"unknown drift: {drift}")
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
            pass

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


@pytest.fixture
def runtime_state(db):
    return _RuntimeState(db=db, key_ring=_make_key_ring())


@pytest.mark.parametrize(
    "drift",
    [
        "build_revision",
        "runtime_contract_version",
        "checkpoint_codec_version",
        "capability_feature_digest",
        "stale_heartbeat",
        "draining",
    ],
)
def test_readiness_rejects_incompatible_worker(runtime_state, drift):
    runtime_state.register_worker()
    runtime_state.drift_worker(drift)
    snapshot = runtime_state.readiness.evaluate()
    assert snapshot.ready is False
    assert "worker_unavailable" in snapshot.reason_codes


def test_two_compatible_workers_are_sorted_and_safe(runtime_state):
    runtime_state.register_worker(worker_id="worker-b:boot")
    runtime_state.register_worker(worker_id="worker-a:boot")
    snapshot = runtime_state.readiness.evaluate()
    assert snapshot.ready is True
    assert snapshot.compatible_worker_ids == (
        "worker-a:boot",
        "worker-b:boot",
    )


def test_worker_compatibility_from_closure_and_run():
    from app.assistant.capability_calls.write_guard import (
        CREATE_ENTRY_CONTRACT_DIGEST,
        RECONCILIATION_CONTRACT_VERSION,
        WRITE_COHORT_DIGEST,
        WRITE_POLICY_DIGEST,
    )
    from app.assistant.durable.worker_registry import WorkerCompatibility
    from app.assistant.runtime.contracts import AssistantRuntimeClosure
    from types import SimpleNamespace

    closure = AssistantRuntimeClosure(
        rollout_revision_id=uuid4(),
        rollout_revision_digest="a" * 64,
        profile_version_id=uuid4(),
        profile_content_digest="b" * 64,
        model_id=uuid4(),
        model_identity_digest="c" * 64,
        package_closure_digest="d" * 64,
        capability_closure_digest="e" * 64,
        seed_manifest_digest="f" * 64,
        build_revision="build-x",
        runtime_contract_version=1,
        checkpoint_codec_version=3,
        capability_feature_digest="1" * 64,
        create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
        write_policy_digest=WRITE_POLICY_DIGEST,
        write_cohort_digest=WRITE_COHORT_DIGEST,
        reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
        closure_digest="2" * 64,
    )
    from_closure = WorkerCompatibility.from_closure(closure)
    assert from_closure.app_build_revision == "build-x"
    assert from_closure.runtime_contract_version == 1
    assert from_closure.required_checkpoint_codec_version == 3
    assert from_closure.required_capability_feature_digest == "1" * 64

    run = SimpleNamespace(
        required_app_build_revision="build-x",
        runtime_contract_version=1,
        required_checkpoint_codec_version=3,
        required_capability_feature_digest="1" * 64,
        required_create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
        required_write_policy_digest=WRITE_POLICY_DIGEST,
        required_write_cohort_digest=WRITE_COHORT_DIGEST,
        required_reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
    )
    from_run = WorkerCompatibility.from_run(run)
    assert from_run == from_closure


def test_find_compatible_workers_takes_compatibility_and_orders_by_worker_id(db):
    from app.assistant.durable.models import AssistantWorkerRegistration
    from app.assistant.durable.worker_registry import (
        WorkerCompatibility,
        WorkerRegistry,
        default_capability_feature_digest,
    )
    from app.common.time import utcnow

    digest = default_capability_feature_digest()
    now = utcnow()
    for wid in ("worker-z", "worker-a", "worker-m"):
        db.add(
            AssistantWorkerRegistration(
                worker_id=wid,
                app_build_revision=BUILD,
                runtime_contract_version=1,
                supported_checkpoint_codec_versions=[1, 2, 3],
                capability_feature_digest=digest,
                started_at=now,
                heartbeat_at=now,
                draining_at=None,
                hostname_label="t",
            )
        )
    db.flush()

    compat = WorkerCompatibility(
        app_build_revision=BUILD,
        runtime_contract_version=1,
        required_checkpoint_codec_version=3,
        required_capability_feature_digest=digest,
    )
    rows = WorkerRegistry(db).find_compatible_workers(
        compat, registration_ttl=timedelta(seconds=60)
    )
    assert [r.worker_id for r in rows] == ["worker-a", "worker-m", "worker-z"]
    assert WorkerRegistry(db).has_compatible_worker(
        compat, registration_ttl=timedelta(seconds=60)
    )
