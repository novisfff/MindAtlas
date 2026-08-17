"""Authenticated HTTP contract for call-owned create_entry reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_reconciliation_list_is_viewer_safe_and_mutation_requires_operator_csrf() -> None:
    from app.assistant.capability_calls.reconciliation_router import router
    from app.assistant.capability_calls.models import (
        AssistantCapabilityReconciliation,
    )
    from app.operator_auth.models import OperatorAccount, OperatorAuditEvent, OperatorSession
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )
    from tests.test_capability_call_reconciliation import (
        EVIDENCE_SECRET,
        _evidence_artifact,
        _seed_external_call,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET=EVIDENCE_SECRET,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        run, call, _ = _seed_external_call(db)
        evidence = _evidence_artifact(
            db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        db.commit()

        listed = client.get("/api/capability-calls/reconciliation", headers=headers)
        assert listed.status_code == 200, listed.text
        item = listed.json()["data"]["items"][0]
        assert item["callId"] == str(call.id)
        assert item["status"] == "needs_reconciliation"
        assert item["evidenceRequired"] is True
        assert item["evidenceArtifactIds"] == [str(evidence.id)]
        assert "inputDigest" not in item
        assert "idempotencyKey" not in item
        assert "title" not in item
        assert "content" not in item

        path = f"/api/capability-calls/{call.id}/reconcile"
        body = {
            "expectedCallRevision": int(call.state_revision),
            "expectedRunRevision": int(run.state_revision),
            "decision": "mark_failed",
            "evidenceArtifactIds": [str(evidence.id)],
            "requestId": str(uuid.uuid4()),
            "reason": "signed evidence proves the provider outcome failed",
        }

        missing_csrf = dict(headers)
        missing_csrf.pop("X-MindAtlas-CSRF", None)
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            denied = client.post(path, json=body, headers=missing_csrf)
        assert denied.status_code == 403, denied.text
        db.refresh(call)
        db.refresh(run)
        assert call.status == "needs_reconciliation"
        assert run.status == "needs_reconciliation"
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        extra = dict(body)
        extra["approvalBindingDigest"] = "f" * 64
        shaped = client.post(path, json=extra, headers=headers)
        assert shaped.status_code == 422, shaped.text
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        # Reconciliation is an operator recovery surface, not a new-write
        # admission path.  A blocked proposal guard must not make this exact
        # terminalization unavailable.
        with (
            patch(
                "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
            ),
            patch(
                "app.assistant.capability_calls.write_guard.ProductionWriteGuard.evaluate_new_proposal_locked",
                side_effect=AssertionError("new-write guard must not run during reconciliation"),
            ),
        ):
            resolved = client.post(path, json=body, headers=headers)
        assert resolved.status_code == 200, resolved.text
        payload = resolved.json()["data"]
        assert payload["resultingCallStatus"] == "failed"
        assert payload["created"] is True
        db.refresh(call)
        db.refresh(run)
        assert call.status == "failed"
        assert run.status == "failed"
        assert db.query(AssistantCapabilityReconciliation).count() == 1
        account = db.query(OperatorAccount).one()
        session = db.query(OperatorSession).one()
        audit = (
            db.query(OperatorAuditEvent)
            .filter_by(event_type="capability_reconciliation_committed")
            .order_by(OperatorAuditEvent.occurred_at.desc())
            .first()
        )
        assert audit is not None
        assert audit.operator_id == account.id
        assert audit.session_id == session.id

        body_drift = dict(body)
        body_drift["reason"] = "same request id with changed body"
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            drifted_body = client.post(path, json=body_drift, headers=headers)
        assert drifted_body.status_code == 409, drifted_body.text
        assert db.query(AssistantCapabilityReconciliation).count() == 1

        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            replayed = client.post(path, json=body, headers=headers)
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["data"]["created"] is False
        assert (
            replayed.json()["data"]["reconciliationId"]
            == payload["reconciliationId"]
        )

        drifted_request = dict(body)
        drifted_request["expectedRunRevision"] = int(run.state_revision) + 1
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            drift = client.post(path, json=drifted_request, headers=headers)
        assert drift.status_code == 409, drift.text
        assert db.query(AssistantCapabilityReconciliation).count() == 1
    finally:
        restore_operator_settings()
        db.close()


def test_reconciliation_route_rejects_forged_or_missing_session_without_mutation() -> None:
    from app.assistant.capability_calls.reconciliation_router import router
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET="e" * 32,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        forged = dict(headers)
        forged["X-MindAtlas-Operator-ID"] = str(uuid.uuid4())
        forged["X-MindAtlas-Role"] = "operator"
        response = client.get(
            "/api/capability-calls/reconciliation",
            headers=forged,
        )
        assert response.status_code == 200, response.text

        bare = TestClient(client.app)
        response = bare.get("/api/capability-calls/reconciliation")
        assert response.status_code == 401, response.text
    finally:
        restore_operator_settings()
        db.close()


def test_reconciliation_route_negative_auth_and_unsigned_evidence_fail_closed() -> None:
    from app.assistant.capability_calls.models import AssistantCapabilityReconciliation
    from app.assistant.capability_calls.reconciliation_router import router
    from app.operator_auth.models import OperatorAccount, OperatorSession
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        login_operator_session,
        operator_test_settings,
        restore_operator_settings,
    )
    from tests.test_capability_call_reconciliation import (
        EVIDENCE_SECRET,
        _evidence_artifact,
        _seed_external_call,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET=EVIDENCE_SECRET,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        run, call, _ = _seed_external_call(db)
        unsigned = _evidence_artifact(
            db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
            signed=False,
        )
        db.commit()
        path = f"/api/capability-calls/{call.id}/reconcile"
        body = {
            "expectedCallRevision": int(call.state_revision),
            "expectedRunRevision": int(run.state_revision),
            "decision": "mark_failed",
            "evidenceArtifactIds": [str(unsigned.id)],
            "requestId": str(uuid.uuid4()),
            "reason": "bounded negative-path test",
        }

        account = db.query(OperatorAccount).one()
        account.role = "viewer"
        db.commit()
        viewer_denied = client.post(path, json=body, headers=headers)
        assert viewer_denied.status_code == 403, viewer_denied.text
        db.refresh(call)
        db.refresh(run)
        assert call.status == "needs_reconciliation"
        assert run.status == "needs_reconciliation"
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        account.role = "operator"
        db.commit()
        wrong_csrf = dict(headers)
        wrong_csrf["X-MindAtlas-CSRF"] = "wrong-csrf"
        csrf_denied = client.post(path, json=body, headers=wrong_csrf)
        assert csrf_denied.status_code == 403, csrf_denied.text
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        sessions = db.query(OperatorSession).all()
        for session in sessions:
            expired_at = session.created_at + timedelta(microseconds=1)
            session.absolute_expires_at = expired_at
            session.idle_expires_at = expired_at
        db.commit()
        future_db_now = max(session.created_at for session in sessions) + timedelta(days=1)
        with patch(
            "app.operator_auth.repository.OperatorRepository.database_now",
            return_value=future_db_now,
        ):
            expired = client.post(path, json=body, headers=headers)
        assert expired.status_code == 401, expired.text
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        # Login creates a fresh authenticated Session; the unsigned envelope is
        # then rejected by the evidence verifier without changing the Call.
        fresh_headers = login_operator_session(client)
        unsigned_response = client.post(path, json=body, headers=fresh_headers)
        assert unsigned_response.status_code == 409, unsigned_response.text
        assert "signature" not in unsigned_response.text.lower()
        db.refresh(call)
        db.refresh(run)
        assert call.status == "needs_reconciliation"
        assert run.status == "needs_reconciliation"
        assert db.query(AssistantCapabilityReconciliation).count() == 0
    finally:
        restore_operator_settings()
        db.close()


def test_reconciliation_route_rejects_fresh_stale_and_bound_evidence_requests() -> None:
    """Fresh request ids cannot bypass CAS or signed Call/Run evidence binding."""
    from app.assistant.capability_calls.models import AssistantCapabilityReconciliation
    from app.assistant.capability_calls.reconciliation_router import router
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )
    from tests.test_capability_call_reconciliation import (
        EVIDENCE_SECRET,
        _evidence_artifact,
        _seed_external_call,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET=EVIDENCE_SECRET,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        run, call, _ = _seed_external_call(db)
        valid_evidence = _evidence_artifact(
            db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        expired_evidence = _evidence_artifact(
            db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
            metadata={
                "issuedAt": (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat()
            },
        )
        donor_run, donor_call, _ = _seed_external_call(db)
        wrong_call_evidence = _evidence_artifact(
            db,
            run_id=donor_run.id,
            call_id=donor_call.id,
            evidence_type="capability_call_failure",
        )
        db.commit()
        path = f"/api/capability-calls/{call.id}/reconcile"

        stale_body = {
            "expectedCallRevision": int(call.state_revision) + 1,
            "expectedRunRevision": int(run.state_revision),
            "decision": "mark_failed",
            "evidenceArtifactIds": [str(valid_evidence.id)],
            "requestId": str(uuid.uuid4()),
            "reason": "fresh request with stale call CAS",
        }
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            stale = client.post(path, json=stale_body, headers=headers)
        assert stale.status_code == 409, stale.text
        assert stale.json()["data"]["reasonCode"] == "stale_call_revision"
        db.refresh(call)
        db.refresh(run)
        assert call.status == "needs_reconciliation"
        assert run.status == "needs_reconciliation"
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        expired_body = dict(stale_body)
        expired_body.update(
            {
                "expectedCallRevision": int(call.state_revision),
                "expectedRunRevision": int(run.state_revision),
                "evidenceArtifactIds": [str(expired_evidence.id)],
                "requestId": str(uuid.uuid4()),
                "reason": "expired signed envelope",
            }
        )
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            expired = client.post(path, json=expired_body, headers=headers)
        assert expired.status_code == 409, expired.text
        assert expired.json()["data"]["reasonCode"] == "invalid_call_transition"
        assert "signature" not in expired.text.lower()
        assert db.query(AssistantCapabilityReconciliation).count() == 0

        wrong_call_body = dict(expired_body)
        wrong_call_body.update(
            {
                "evidenceArtifactIds": [str(wrong_call_evidence.id)],
                "requestId": str(uuid.uuid4()),
                "reason": "evidence belongs to another call",
            }
        )
        with patch(
            "app.assistant.capability_calls.write_guard.acquire_write_safety_advisory_lock"
        ):
            wrong_call = client.post(path, json=wrong_call_body, headers=headers)
        assert wrong_call.status_code == 409, wrong_call.text
        assert wrong_call.json()["data"]["reasonCode"] == "invalid_call_transition"
        assert "belongs to another" not in wrong_call.text.lower()
        assert db.query(AssistantCapabilityReconciliation).count() == 0
        db.refresh(call)
        db.refresh(run)
        assert call.status == "needs_reconciliation"
        assert run.status == "needs_reconciliation"
    finally:
        restore_operator_settings()
        db.close()


def test_operator_can_obtain_server_signed_failure_evidence_without_client_signature() -> None:
    from app.assistant.durable.models import AssistantRunArtifact
    from app.assistant.capability_calls.reconciliation_router import router
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )
    from tests.test_capability_call_reconciliation import (
        EVIDENCE_SECRET,
        _seed_external_call,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET=EVIDENCE_SECRET,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        run, call, _ = _seed_external_call(db)
        db.commit()
        response = client.post(
            f"/api/capability-calls/{call.id}/reconciliation-evidence/failure",
            json={
                "expectedCallRevision": int(call.state_revision),
                "expectedRunRevision": int(run.state_revision),
                "reason": "operator accepted the captured failure",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        artifact_id = response.json()["data"]["evidenceArtifactId"]
        artifact = db.get(AssistantRunArtifact, uuid.UUID(artifact_id))
        assert artifact is not None
        assert artifact.kind == "capability_call_evidence"
        assert artifact.metadata_json["serverIssued"] is True
        assert b'"signature"' in bytes(artifact.inline_bytes)
        assert b"operator accepted" not in bytes(artifact.inline_bytes)
    finally:
        restore_operator_settings()
        db.close()


def test_operator_can_obtain_server_signed_success_evidence_for_locked_result() -> None:
    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt
    from app.assistant.capability_calls.reconciliation_router import router
    from app.assistant.capability_calls.result_codec import encode_capability_result
    from app.assistant.durable.models import AssistantRunArtifact
    from app.assistant.provider_loop.contracts import ProviderDispatchResult
    from app.assistant.domain.digests import sha256_bytes
    from tests.test_agent_policy_runtime import _base_manifest
    from tests._db import make_session
    from tests.operator_session_helpers import (
        build_authenticated_skill_client,
        operator_test_settings,
        restore_operator_settings,
    )
    from tests.test_capability_call_reconciliation import (
        EVIDENCE_SECRET,
        _seed_external_call,
    )

    db = make_session()
    try:
        settings = operator_test_settings(
            ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=True,
            ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET=EVIDENCE_SECRET,
        )
        client, headers, _ = build_authenticated_skill_client(
            db=db,
            include_routers=[router],
            settings=settings,
        )
        run, call, _ = _seed_external_call(db)
        manifest, _surface = _base_manifest()
        result = ProviderDispatchResult(
            capability_result=completed_result(
                structured_output={"ok": True},
                metrics=CapabilityMetrics(duration_ms=1, input_bytes=0, output_bytes=0),
            ),
            next_manifest=manifest,
        )
        encoded = encode_capability_result(
            call_id=str(call.provider_tool_call_id),
            binding_contract_digest=str(call.authorization_digest),
            descriptor_digest=str(call.descriptor_digest),
            result=result,
        )
        artifact = AssistantRunArtifact(
            run_id=run.id,
            kind="capability_call_result",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(encoded.payload),
            content_sha256=encoded.digest,
            inline_bytes=encoded.payload,
            metadata_json={"contractVersion": 1},
        )
        db.add(artifact)
        attempt = (
            db.query(AssistantCapabilityCallAttempt)
            .filter_by(call_id=call.id)
            .one()
        )
        attempt.status = "committed"
        attempt.response_digest = encoded.digest
        db.commit()
        response = client.post(
            f"/api/capability-calls/{call.id}/reconciliation-evidence/success",
            json={
                "expectedCallRevision": int(call.state_revision),
                "expectedRunRevision": int(run.state_revision),
                "resultArtifactId": str(artifact.id),
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        evidence_id = uuid.UUID(response.json()["data"]["evidenceArtifactId"])
        evidence = db.get(AssistantRunArtifact, evidence_id)
        assert evidence is not None
        assert evidence.metadata_json["serverIssued"] is True
        assert b'"evidenceType":"capability_call_success_attestation"' in bytes(
            evidence.inline_bytes
        )
    finally:
        restore_operator_settings()
        db.close()
