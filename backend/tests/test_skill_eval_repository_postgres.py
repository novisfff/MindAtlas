"""Current evaluation repository/trigger checks on the clean schema root."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests.postgres_destructive_guard import reset_disposable_public_schema
from tests.schema_baseline_support import upgrade_clean_root_checked

bootstrap_backend_imports()
reset_caches()

from app.common.time import utcnow  # noqa: E402
from app.schema.contracts import CLEAN_ROOT_REVISION  # noqa: E402


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
        "clean-root evaluation gate must hard-fail",
        pytrace=False,
    )
pytestmark = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for clean-root evaluation proof",
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64
_DIGEST_E = "e" * 64


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


@pytest.fixture()
def clean_root_engine() -> Iterator[Engine]:
    assert _POSTGRES_URL
    engine = create_engine(_sqlalchemy_url(_POSTGRES_URL), future=True)
    reset_disposable_public_schema(engine)
    upgrade_clean_root_checked(
        _POSTGRES_URL,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="test-skill-eval-clean-root",
    )
    try:
        yield engine
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


@pytest.fixture()
def session(clean_root_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(
        bind=clean_root_engine,
        autoflush=True,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    value = factory()
    try:
        yield value
    finally:
        value.rollback()
        value.close()


def _error_text(exc: BaseException) -> str:
    return " ".join(
        str(value)
        for value in (exc, getattr(exc, "orig", None))
        if value is not None
    )


def test_live_head_is_clean_root_and_evaluation_tables_exist(
    clean_root_engine: Engine,
) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == [CLEAN_ROOT_REVISION]
    with clean_root_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == CLEAN_ROOT_REVISION
        for table in (
            "assistant_skill_eval_dataset",
            "assistant_skill_eval_dataset_version",
            "assistant_skill_eval_case",
            "assistant_skill_eval_run",
            "assistant_skill_eval_case_result",
            "assistant_skill_eval_capability_call",
            "assistant_skill_eval_event",
            "assistant_skill_eval_artifact",
            "assistant_skill_publish_gate",
            "assistant_skill_publish_gate_use",
        ):
            assert connection.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:name"
                ),
                {"name": table},
            ).first()


def test_repository_roundtrip_and_immutable_gate_use(
    session: Session,
    clean_root_engine: Engine,
) -> None:
    from app.assistant.evaluation.repository import EvaluationRepository, EvaluationRepositoryError

    repo = EvaluationRepository(session)
    dataset = repo.create_dataset(
        stable_key=f"pg-{uuid.uuid4().hex[:10]}",
        display_name="PG",
        ownership="custom",
    )
    snapshot = [
        {
            "case_key": "c1",
            "ordinal": 0,
            "locale": "en",
            "input_messages": [{"role": "user", "content": "x"}],
            "expected_mode": "golden_skill",
            "case_digest": _DIGEST_A,
            "notes": "n",
        }
    ]
    repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
    published = repo.publish_dataset_version(
        dataset_id=dataset.id,
        expected_aggregate_revision=0,
        expected_draft_revision=0,
        version_name="v1",
    )
    case = repo.list_cases(published.version_id)[0]
    run = repo.create_run(
        subject_kind="skill_version",
        subject_aggregate_id=uuid.uuid4(),
        subject_version_id=uuid.uuid4(),
        subject_content_digest=_DIGEST_A,
        subject_binding_digest=_DIGEST_B,
        dataset_version_ids=[published.version_id],
        threshold_policy_version="t1",
        mode="dataset_scripted",
        isolation_namespace_id=uuid.uuid4(),
        runtime_contract_version=1,
        required_build_revision="test-eval-build",
        isolation_digest=_DIGEST_C,
    )
    repo.append_case_result(
        eval_run_id=run.id,
        eval_case_id=case.id,
        expected_run_revision=0,
        result_state="passed",
    )
    repo.append_event(
        eval_run_id=run.id,
        expected_run_revision=1,
        event_type="test",
        payload={},
    )
    repo.append_capability_call(
        eval_run_id=run.id,
        eval_case_id=case.id,
        expected_run_revision=2,
        logical_call_key="skill.search",
        attempt=1,
        subject_kind="skill_version",
        subject_aggregate_id=run.subject_aggregate_id,
        subject_version_id=run.subject_version_id,
        subject_owner_digest=_DIGEST_A,
        binding_digest=_DIGEST_B,
        input_digest=_DIGEST_C,
        descriptor_digest=_DIGEST_D,
        policy_digest=_DIGEST_E,
        outcome="simulated",
    )
    gate = repo.append_publish_gate(
        subject_kind="skill_version",
        subject_aggregate_id=run.subject_aggregate_id,
        subject_version_id=run.subject_version_id,
        subject_content_digest=_DIGEST_A,
        subject_binding_digest=_DIGEST_B,
        profile_digest=_DIGEST_C,
        catalog_digest=_DIGEST_D,
        dataset_version_ids=[published.version_id],
        qualifying_eval_run_ids=[run.id],
        runtime_contract_version=1,
        policy_version="p1",
        threshold_version="t1",
        build_revision="test-eval-build",
        decision="passed",
        expires_at=utcnow() + timedelta(days=1),
        request_id=f"gate-{uuid.uuid4().hex}",
    )
    use = repo.append_gate_use(
        gate_id=gate.id,
        action="skill_publish",
        aggregate_id=run.subject_aggregate_id,
        resulting_version_id=run.subject_version_id,
        actor_principal="operator",
        request_id=f"use-{uuid.uuid4().hex}",
        aggregate_revision=1,
    )
    session.commit()
    assert repo.is_gate_evidence_pinned(gate) is True
    with pytest.raises(EvaluationRepositoryError) as duplicate:
        repo.append_gate_use(
            gate_id=gate.id,
            action="skill_publish",
            aggregate_id=run.subject_aggregate_id,
            resulting_version_id=run.subject_version_id,
            actor_principal="operator",
            request_id=f"duplicate-{uuid.uuid4().hex}",
            aggregate_revision=1,
        )
    assert duplicate.value.code == "conflict"
    session.rollback()

    with clean_root_engine.begin() as connection:
        with pytest.raises((DBAPIError, IntegrityError)) as exc_info:
            connection.execute(
                text(
                    "UPDATE assistant_skill_publish_gate_use "
                    "SET actor_principal='mutated' WHERE id=:id"
                ),
                {"id": use.id},
            )
        assert "MINDATLAS_PLAN09_EVAL_IMMUTABLE" in _error_text(exc_info.value)


def test_eval_run_rejects_wrong_owner_and_provenance(
    clean_root_engine: Engine,
) -> None:
    values = {
        "id": uuid.uuid4(),
        "aggregate": uuid.uuid4(),
        "version": uuid.uuid4(),
        "namespace": uuid.uuid4(),
        "d1": _DIGEST_A,
        "d2": _DIGEST_B,
        "d3": _DIGEST_C,
    }
    sql = text(
        """
        INSERT INTO assistant_skill_eval_run (
            id, subject_kind, subject_aggregate_id, subject_version_id,
            subject_content_digest, subject_binding_digest, dataset_version_ids,
            threshold_policy_version, mode, status, isolation_namespace_id,
            owner_kind, runtime_contract_version, required_build_revision,
            runner_contract_version, state_revision, lease_generation,
            last_event_seq, attempt_count, isolation_digest, evidence_provenance,
            aggregate_metrics, gate_eligible, created_at, updated_at
        ) VALUES (
            :id, 'skill_version', :aggregate, :version, :d1, :d2, '[]',
            't', 'dataset_scripted', 'queued', :namespace, :owner,
            1, 'test', 1, 0, 0, 0, 0, :d3, :provenance, '{}', false,
            now(), now()
        )
        """
    )
    for owner, provenance in (("main_agent", "structural_synthetic"), ("test", "invalid")):
        with pytest.raises((DBAPIError, IntegrityError)):
            with clean_root_engine.begin() as connection:
                connection.execute(
                    sql,
                    {**values, "owner": owner, "provenance": provenance},
                )
