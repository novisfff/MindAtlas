from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_pre_ga_launch_service import _fixture, _operator_session


@pytest.fixture
def launch_api(tmp_path: Path) -> Iterator[dict[str, object]]:
    from app.common.exceptions import register_exception_handlers
    from app.database import get_db
    from app.operator_auth.dependencies import (
        require_csrf,
        require_operator_principal,
        require_viewer_principal,
    )
    from app.operator_auth.route_policy import (
        protected_browser_router,
        require_browser_route_policy,
    )
    from app.pre_ga_launch.router import get_launch_service, router
    from app.pre_ga_launch.service import PreGaLaunchService
    from tests._db import make_session

    db = make_session()
    principal = _operator_session(db)
    target, store, trust_set, refs = _fixture(tmp_path)
    from app.pre_ga_launch.models import PreGaLaunchControl

    db.add(PreGaLaunchControl(singleton_key="pre_ga_launch", revision=0))
    db.commit()
    service = PreGaLaunchService(
        db,
        evidence_store=store,
        trust_set=trust_set,
        target_provider=lambda: target,
    )

    app = FastAPI()
    register_exception_handlers(app)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_launch_service] = lambda: service
    app.dependency_overrides[require_viewer_principal] = lambda: principal
    app.dependency_overrides[require_operator_principal] = lambda: principal
    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[require_browser_route_policy] = lambda: principal
    protected = protected_browser_router()
    protected.include_router(router)
    app.include_router(protected)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {"client": client, "db": db, "principal": principal, "refs": refs}
    finally:
        client.close()
        app.dependency_overrides.clear()
        db.close()


def test_launch_api_has_safe_reads_and_rejects_client_decision_fields(launch_api) -> None:
    client = launch_api["client"]
    refs = launch_api["refs"]

    status = client.get("/api/pre-ga-launch/status")
    assert status.status_code == 200, status.text
    assert status.json()["data"]["launched"] is False
    assert status.json()["data"]["reasonCode"] == "launch_control_missing"
    assert status.json()["data"]["activeCandidateId"] is None
    assert status.json()["data"]["activeGateUseId"] is None
    assert status.json()["data"]["launchedAt"] is None

    target = client.get("/api/pre-ga-launch/qualification-target")
    assert target.status_code == 200, target.text
    assert "subjectJson" not in target.json()["data"]

    invalid = client.post(
        "/api/pre-ga-launch/candidates",
        json={
            "automatedEvidenceRef": refs[0].model_dump(mode="json", by_alias=True),
            "rehearsalEvidenceRef": refs[1].model_dump(mode="json", by_alias=True),
            "requestId": "00000000-0000-0000-0000-0000000000d0",
            "reason": "qualification",
            "passed": True,
        },
    )
    assert invalid.status_code == 422, invalid.text


def test_launch_api_candidate_create_list_consume_and_replay(launch_api) -> None:
    client = launch_api["client"]
    refs = launch_api["refs"]
    body = {
        "automatedEvidenceRef": refs[0].model_dump(mode="json", by_alias=True),
        "rehearsalEvidenceRef": refs[1].model_dump(mode="json", by_alias=True),
        "requestId": "00000000-0000-0000-0000-0000000000d1",
        "reason": "qualification",
    }
    created = client.post("/api/pre-ga-launch/candidates", json=body)
    assert created.status_code == 201, created.text
    candidate = created.json()["data"]
    assert candidate["passed"] is True
    assert "subjectJson" not in candidate
    assert "creationRequestDigest" not in candidate

    replay = client.post("/api/pre-ga-launch/candidates", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["candidateId"] == candidate["candidateId"]

    listed = client.get("/api/pre-ga-launch/candidates")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["items"][0]["candidateId"] == candidate["candidateId"]

    consumed = client.post(
        f"/api/pre-ga-launch/candidates/{candidate['candidateId']}/consume",
        json={
            "expectedControlRevision": 0,
            "requestId": "00000000-0000-0000-0000-0000000000d2",
            "reason": "launch",
        },
    )
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()["data"]["controlRevision"] == 1

    status = client.get("/api/pre-ga-launch/status")
    assert status.status_code == 200, status.text
    assert status.json()["data"]["launched"] is True
    assert status.json()["data"]["activeCandidateId"] == candidate["candidateId"]
    assert status.json()["data"]["activeGateUseId"] == consumed.json()["data"]["gateUseId"]
    assert status.json()["data"]["launchedAt"] is not None
    assert status.json()["data"]["updatedAt"] is not None
