"""PostgreSQL claim compatibility (Plan 2 Task 7).

Incompatible workers must never claim a queued Main Agent Run. Two compatible
workers racing for one Run yield exactly one lease generation.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_9 = "9" * 64
FEATURE = "e" * 64  # default matching feature digest for claim tests

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
        "Plan 2 claim compatibility PostgreSQL gate is release-critical and must hard-fail",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason=(
        "MINDATLAS_TEST_POSTGRES_URL not set; Plan 2 claim compatibility "
        "PostgreSQL gate skipped. Set MINDATLAS_REQUIRE_POSTGRES=1 to hard-fail "
        "instead of skip."
    ),
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _as_sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _configure_database_env(url: str, *, build: str) -> None:
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", "1")
    os.environ.setdefault("APP_ENV", "test")
    os.environ["APP_BUILD_REVISION"] = build
    os.environ["ASSISTANT_NEW_RUNS_ENABLED"] = "true"
    reset_caches()
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@contextmanager
def _engine(*, build: str) -> Iterator[Engine]:
    assert _POSTGRES_URL
    url = _as_sqlalchemy_url(_POSTGRES_URL)
    _configure_database_env(url, build=build)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def _session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _ensure_schema(engine: Engine) -> None:
    from app.database import Base

    import app.assistant.models  # noqa: F401
    import app.assistant.durable.models  # noqa: F401
    import app.assistant.runtime.models  # noqa: F401
    import app.assistant.skills.models  # noqa: F401
    import app.ai_registry.models  # noqa: F401
    import app.ai_provider.models  # noqa: F401
    import app.operator_auth.models  # noqa: F401
    import app.system_settings.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def _seed_profile_version(db: Session, *, content_digest: str = DIGEST_A):
    from app.assistant.skills.models import (
        AssistantMainAgentProfile,
        AssistantMainAgentProfileVersion,
    )

    profile = AssistantMainAgentProfile(
        profile_key=f"claim-{uuid.uuid4().hex[:8]}",
        display_name="Main Agent",
        is_default=False,
        migration_state="native",
        runtime_enabled=False,
    )
    db.add(profile)
    db.flush()
    version = AssistantMainAgentProfileVersion(
        profile_id=profile.id,
        sequence_no=1,
        version_name="v1",
        version_source="save",
        origin="api",
        snapshot={"schemaVersion": 2, "content": "test"},
        content_digest=content_digest,
    )
    db.add(version)
    db.flush()
    return profile, version


def _seed_model(db: Session):
    from tests.agent_skill_test_support import create_default_model_binding

    _cred, model, _binding = create_default_model_binding(db)
    return model


def _prepared_revision(db: Session, **overrides: Any):
    from app.assistant.runtime.contracts import (
        AssistantRuntimeSubject,
        PreparedRolloutRevision,
    )
    from app.assistant.runtime.repository import AssistantRuntimeRepository

    _, profile_version = _seed_profile_version(db)
    model = _seed_model(db)
    subject = AssistantRuntimeSubject(
        profile_version_id=overrides.get("profile_version_id", profile_version.id),
        profile_content_digest=overrides.get(
            "profile_content_digest", profile_version.content_digest
        ),
        model_id=overrides.get("model_id", model.id),
        model_identity_digest=overrides.get("model_identity_digest", DIGEST_B),
        package_closure=overrides.get(
            "package_closure", ({"packageId": str(uuid.uuid4()), "digest": DIGEST_C},)
        ),
        package_closure_digest=overrides.get("package_closure_digest", DIGEST_C),
        capability_closure_digest=overrides.get("capability_closure_digest", DIGEST_D),
        seed_manifest_digest=overrides.get("seed_manifest_digest", DIGEST_E),
        build_revision=overrides.get(
            "build_revision", f"claim-prep-{uuid.uuid4().hex[:10]}"
        ),
        runtime_contract_version=overrides.get("runtime_contract_version", 1),
        checkpoint_codec_version=overrides.get("checkpoint_codec_version", 3),
        capability_feature_digest=overrides.get(
            "capability_feature_digest", FEATURE
        ),
    )
    prepared = PreparedRolloutRevision.from_subject(
        subject=subject,
        revision_id=overrides.get("revision_id", uuid.uuid4()),
        prepared_by_operator_id=None,
        prepared_reason="claim-compat-test",
    )
    return AssistantRuntimeRepository(db).create_prepared_revision(prepared)


class _PostgresRuntime:
    """Harness matching the brief's postgres_runtime surface."""

    def __init__(self, engine: Engine, *, build: str) -> None:
        self.engine = engine
        self.build = build
        self._prepared = None
        self._run_id = None

    def queued_run(self, **overrides: Any):
        from app.assistant.models import AssistantChatRun, Conversation

        with _session(self.engine) as s:
            prepared = _prepared_revision(
                s,
                build_revision=overrides.get(
                    "required_app_build_revision", self.build
                ),
                runtime_contract_version=overrides.get("runtime_contract_version", 1),
                checkpoint_codec_version=overrides.get(
                    "required_checkpoint_codec_version", 3
                ),
                capability_feature_digest=overrides.get(
                    "required_capability_feature_digest", FEATURE
                ),
            )
            self._prepared = prepared
            conv = Conversation(title=f"pg-claim-{uuid.uuid4().hex[:10]}")
            s.add(conv)
            s.flush()
            run = AssistantChatRun(
                conversation_id=conv.id,
                status="queued",
                runtime_kind="main_agent",
                main_agent_rollout_revision_id=prepared.id,
                main_agent_profile_version_id=prepared.profile_version_id,
                resolved_model_id=prepared.model_id,
                runtime_closure_digest=DIGEST_9,
                runtime_contract_version=int(
                    overrides.get("runtime_contract_version", 1)
                ),
                required_checkpoint_codec_version=int(
                    overrides.get("required_checkpoint_codec_version", 3)
                ),
                required_capability_feature_digest=str(
                    overrides.get("required_capability_feature_digest", FEATURE)
                ),
                required_app_build_revision=str(
                    overrides.get("required_app_build_revision", self.build)
                ),
                capability_ledger_mode="enforced",
                memory_commit_status="pending",
                state_revision=0,
                last_event_seq=0,
            )
            s.add(run)
            s.commit()
            s.refresh(run)
            self._run_id = run.id
            # Detach a lightweight view for callers.
            return SimpleRun(id=run.id, status=run.status, lease_owner=run.lease_owner)

    def worker_identity_with(self, drift: str | None = None, *, worker_id: str | None = None):
        from app.assistant.durable.worker_registry import WorkerIdentity

        wid = worker_id or f"w-{uuid.uuid4().hex[:10]}"
        kwargs: dict[str, Any] = dict(
            worker_id=wid,
            app_build_revision=self.build,
            runtime_contract_version=1,
            supported_checkpoint_codec_versions=(1, 2, 3),
            capability_feature_digest=FEATURE,
            hostname_label="pg-claim",
        )
        if drift == "build_revision":
            kwargs["app_build_revision"] = f"other-{self.build}"
        elif drift == "runtime_contract_version":
            kwargs["runtime_contract_version"] = 99
        elif drift == "checkpoint_codec_version":
            kwargs["supported_checkpoint_codec_versions"] = (99,)
        elif drift == "capability_feature_digest":
            kwargs["capability_feature_digest"] = "0" * 64
        elif drift is not None:
            raise AssertionError(f"unknown drift: {drift}")
        return WorkerIdentity(**kwargs)

    def claim(self, worker):
        from app.assistant.durable.leases import RunLeaseService

        with _session(self.engine) as s:
            svc = RunLeaseService(
                s,
                identity=worker,
                lease_ttl=timedelta(seconds=30),
            )
            return svc.claim_next()

    def reload(self, run_id):
        from app.assistant.durable.repository import DurableRunRepository

        with _session(self.engine) as s:
            run = DurableRunRepository(s).get_run(run_id)
            assert run is not None
            return SimpleRun(
                id=run.id,
                status=run.status,
                lease_owner=run.lease_owner,
                lease_generation=int(run.lease_generation or 0),
            )


class SimpleRun:
    def __init__(
        self,
        *,
        id,  # noqa: A002
        status: str,
        lease_owner: str | None = None,
        lease_generation: int = 0,
    ) -> None:
        self.id = id
        self.status = status
        self.lease_owner = lease_owner
        self.lease_generation = lease_generation


@pytest.fixture
def postgres_runtime():
    # Unique build per fixture so claim_next never sees leftover rows from prior
    # suites sharing the disposable Postgres database.
    build = f"claim-compat-{uuid.uuid4().hex[:12]}"
    with _engine(build=build) as engine:
        _ensure_schema(engine)
        yield _PostgresRuntime(engine, build=build)


@pytest.mark.parametrize(
    "drift",
    [
        "build_revision",
        "runtime_contract_version",
        "checkpoint_codec_version",
        "capability_feature_digest",
    ],
)
def test_incompatible_worker_cannot_claim_run(postgres_runtime, drift):
    run = postgres_runtime.queued_run()
    worker = postgres_runtime.worker_identity_with(drift)
    claimed = postgres_runtime.claim(worker)
    assert claimed is None
    reloaded = postgres_runtime.reload(run.id)
    assert reloaded.status == "queued"
    assert reloaded.lease_owner is None


def test_two_compatible_workers_race_one_lease_generation(postgres_runtime):
    run = postgres_runtime.queued_run()
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, int | None]] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        identity = postgres_runtime.worker_identity_with(worker_id=name)
        from app.assistant.durable.leases import RunLeaseService

        with _session(postgres_runtime.engine) as s:
            svc = RunLeaseService(
                s,
                identity=identity,
                lease_ttl=timedelta(seconds=30),
            )
            barrier.wait(timeout=10)
            claimed = svc.claim_next()
            with lock:
                if claimed is None:
                    outcomes.append(("none", None))
                else:
                    outcomes.append(("ok", int(claimed.lease.lease_generation)))

    t1 = threading.Thread(target=worker, args=("worker-a",))
    t2 = threading.Thread(target=worker, args=("worker-b",))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    ok = [o for o in outcomes if o[0] == "ok"]
    none = [o for o in outcomes if o[0] == "none"]
    assert len(ok) == 1, outcomes
    assert len(none) == 1, outcomes
    assert ok[0][1] == 1

    reloaded = postgres_runtime.reload(run.id)
    assert reloaded.status == "running"
    assert reloaded.lease_owner in {"worker-a", "worker-b"}
    assert reloaded.lease_generation == 1


def test_compatible_worker_claims_queued_run(postgres_runtime):
    run = postgres_runtime.queued_run()
    worker = postgres_runtime.worker_identity_with()
    claimed = postgres_runtime.claim(worker)
    assert claimed is not None
    assert str(claimed.run_id) == str(run.id)
    reloaded = postgres_runtime.reload(run.id)
    assert reloaded.status == "running"
    assert reloaded.lease_owner == worker.worker_id


def test_claim_skips_codec_incompatible_earliest_run(postgres_runtime):
    """Earliest eligible Run with an unsupported codec must not starve a later match.

    SQL prefilter matches build/contract/feature only; codec is rechecked on the
    locked row. Claim must scan further SKIP LOCKED candidates instead of
    abandoning the poll after one codec-incompatible lock.
    """
    # Worker supports codecs 1 and 2 only — not 3 (current) or 99.
    worker = postgres_runtime.worker_identity_with()
    # Rebuild identity with a restricted codec set while keeping other fields.
    from app.assistant.durable.worker_registry import WorkerIdentity

    worker = WorkerIdentity(
        worker_id=worker.worker_id,
        app_build_revision=postgres_runtime.build,
        runtime_contract_version=1,
        supported_checkpoint_codec_versions=(1, 2),
        capability_feature_digest=FEATURE,
        hostname_label="pg-claim-codec-scan",
    )

    # Earliest by created_at: requires codec 99 (worker cannot support).
    bad = postgres_runtime.queued_run(required_checkpoint_codec_version=99)
    # Later eligible Run: requires codec 2 (worker supports).
    good = postgres_runtime.queued_run(required_checkpoint_codec_version=2)

    claimed = postgres_runtime.claim(worker)
    assert claimed is not None, "claim_next must advance past codec-incompatible head"
    assert str(claimed.run_id) == str(good.id)

    bad_reloaded = postgres_runtime.reload(bad.id)
    assert bad_reloaded.status == "queued"
    assert bad_reloaded.lease_owner is None

    good_reloaded = postgres_runtime.reload(good.id)
    assert good_reloaded.status == "running"
    assert good_reloaded.lease_owner == worker.worker_id
