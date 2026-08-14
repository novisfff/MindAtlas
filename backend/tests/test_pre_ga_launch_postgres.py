from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.test_pre_ga_launch_service import _fixture


POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL is required for PostgreSQL launch tests",
)


def _principal(db, operator_id: UUID):
    from app.common.time import utcnow
    from app.operator_auth.contracts import OperatorPrincipal
    from app.operator_auth.models import OperatorAccount

    account = (
        db.query(OperatorAccount)
        .filter(OperatorAccount.singleton_key == "operator")
        .one_or_none()
    )
    if account is None:
        db.add(
            OperatorAccount(
                id=operator_id,
                singleton_key="operator",
                role="operator",
                password_hash="test-password-hash",
                password_changed_at=utcnow(),
            )
        )
        db.flush()
        account = db.get(OperatorAccount, operator_id)
    assert account is not None
    return OperatorPrincipal(
        operator_id=account.id,
        role="operator",
        session_id=uuid4(),
    )


def test_same_request_is_serialized_and_replayed(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import CreatePreGaLaunchCandidateRequest
    from app.pre_ga_launch.models import PreGaLaunchControl
    from app.pre_ga_launch.service import PreGaLaunchService

    engine = create_engine(POSTGRES_URL, future=True, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    operator_id = uuid4()
    request_id = uuid4()
    target, store, trust_set, refs = _fixture(tmp_path)
    with factory() as setup:
        if setup.get(PreGaLaunchControl, "pre_ga_launch") is None:
            setup.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
        _principal(setup, operator_id)
        setup.commit()

    request = CreatePreGaLaunchCandidateRequest(
        automated_evidence_ref=refs[0],
        rehearsal_evidence_ref=refs[1],
        request_id=request_id,
        reason="postgres serialization",
    )

    def run_once():
        with factory() as db:
            principal = _principal(db, operator_id)
            db.commit()
            service = PreGaLaunchService(
                db,
                evidence_store=store,
                trust_set=trust_set,
                target_provider=lambda: target,
            )
            result = service.create_candidate(request, principal=principal)
            return result.candidate.id, result.replayed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _item: run_once(), (0, 1)))
    assert results[0][0] == results[1][0]
    assert sorted(item[1] for item in results) == [False, True]
    with engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT count(*) FROM pre_ga_launch_candidate "
                "WHERE creation_request_id = :request_id"
            ),
            {"request_id": str(request_id)},
        ).scalar_one()
    assert count == 1
    engine.dispose()


def test_database_trigger_rejects_candidate_update_and_delete(tmp_path: Path) -> None:
    from app.pre_ga_launch.contracts import CreatePreGaLaunchCandidateRequest
    from app.pre_ga_launch.models import PreGaLaunchControl
    from app.pre_ga_launch.service import PreGaLaunchService

    engine = create_engine(POSTGRES_URL, future=True, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    operator_id = uuid4()
    target, store, trust_set, refs = _fixture(tmp_path)
    with factory() as db:
        if db.get(PreGaLaunchControl, "pre_ga_launch") is None:
            db.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
        principal = _principal(db, operator_id)
        db.commit()
        service = PreGaLaunchService(
            db,
            evidence_store=store,
            trust_set=trust_set,
            target_provider=lambda: target,
        )
        result = service.create_candidate(
            CreatePreGaLaunchCandidateRequest(
                automated_evidence_ref=refs[0],
                rehearsal_evidence_ref=refs[1],
                request_id=uuid4(),
                reason="immutability",
            ),
            principal=principal,
        )
        candidate_id = result.candidate.id
        result.candidate.reason = "tampered"
        with pytest.raises(RuntimeError):
            db.flush()
        db.rollback()
        with pytest.raises(Exception):
            db.execute(
                text("DELETE FROM pre_ga_launch_candidate WHERE id = :candidate_id"),
                {"candidate_id": str(candidate_id)},
            )
        db.rollback()
    engine.dispose()
