"""Authenticated viewer/Operator reconciliation HTTP boundary."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.assistant.capability_calls.models import (
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
)
from app.assistant.models import AssistantChatRun
from app.assistant.durable.models import AssistantRunArtifact
from app.assistant.capability_calls.reconciliation import (
    CapabilityReconciliationService,
    HmacReconciliationEvidenceVerifier,
    ReconciliationDecisionRequest,
)
from app.assistant.capability_calls.reconciliation_schemas import (
    IssueFailureEvidenceRequest,
    IssueSuccessEvidenceRequest,
    ReconcileCapabilityCallRequest,
)
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db
from app.operator_auth.audit import OperatorAuditRepository
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.dependencies import (
    request_security_context,
    require_csrf,
    require_operator_principal,
    require_viewer_principal,
)
from app.config import Settings, get_settings


router = APIRouter(prefix="/api/capability-calls", tags=["capability-calls"])


def _evidence_secret(settings: Settings) -> str:
    secret = str(
        getattr(settings, "assistant_capability_reconciliation_evidence_secret", "")
        or ""
    )
    if len(secret.encode("utf-8")) < 32:
        raise ApiException(
            status_code=503,
            code=50374,
            message="reconciliation_unavailable",
            details={"reasonCode": "reconciliation_evidence_verifier_unavailable"},
        )
    return secret


def _safe_call_summary(
    call: AssistantCapabilityCall,
    *,
    run_revision: int,
    evidence_artifact_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "callId": str(call.id),
        "runId": str(call.run_id),
        "status": str(call.status),
        "stateRevision": int(call.state_revision),
        "runRevision": int(run_revision),
        "failureCode": str(call.failure_code) if call.failure_code else None,
        "executionMode": str(call.execution_mode),
        "sideEffectStartedAt": (
            call.side_effect_started_at.isoformat()
            if call.side_effect_started_at is not None
            else None
        ),
        "createdAt": call.created_at.isoformat() if call.created_at is not None else None,
        "updatedAt": call.updated_at.isoformat() if call.updated_at is not None else None,
        "attemptCount": int(call.attempt_count or 0),
        "evidenceRequired": True,
        "evidenceArtifactIds": list(evidence_artifact_ids),
    }


def _safe_evidence_artifact_ids(db: Session, call: AssistantCapabilityCall) -> tuple[str, ...]:
    """Return only Artifact identities already bound to this Call.

    The projection intentionally inspects metadata/foreign-key identity only;
    it never reads or serializes Artifact bytes, object keys, or evidence JSON.
    """

    ids: list[str] = []
    diagnostic_ids = (
        db.query(AssistantCapabilityCallAttempt.diagnostic_artifact_id)
        .filter(
            AssistantCapabilityCallAttempt.call_id == call.id,
            AssistantCapabilityCallAttempt.diagnostic_artifact_id.is_not(None),
        )
        .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
        .all()
    )
    ids.extend(str(row[0]) for row in diagnostic_ids if row[0] is not None)

    artifacts = (
        db.query(AssistantRunArtifact)
        .filter(
            AssistantRunArtifact.run_id == call.run_id,
            AssistantRunArtifact.kind == "capability_call_evidence",
        )
        .order_by(AssistantRunArtifact.created_at.desc(), AssistantRunArtifact.id.desc())
        .limit(32)
        .all()
    )
    for artifact in artifacts:
        metadata = artifact.metadata_json
        if isinstance(metadata, dict) and str(metadata.get("callId") or "") == str(call.id):
            ids.append(str(artifact.id))

    return tuple(dict.fromkeys(ids))[:8]


def _map_reconciliation_error(exc: Exception) -> ApiException:
    code = str(getattr(exc, "code", "reconciliation_failed") or "reconciliation_failed")
    if code in {"call_not_found"}:
        status = 404
        numeric = 40474
    elif code in {"stale_call_revision", "stale_run_revision", "invalid_call_transition"}:
        status = 409
        numeric = 40974
    elif code in {"reconciliation_required", "write_safety_blocked"}:
        status = 503
        numeric = 50374
    else:
        status = 422
        numeric = 42274
    # Do not expose exception messages: they may include implementation detail
    # or persisted evidence content. The short reason code is the public contract.
    return ApiException(
        status_code=status,
        code=numeric,
        message=code,
        details={"reasonCode": code},
    )


@router.get("/reconciliation", response_model=ApiResponse)
def list_reconciliation_calls(
    limit: int = Query(50, ge=1, le=100),
    _principal: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
) -> ApiResponse:
    total = db.query(AssistantCapabilityCall).filter(
        AssistantCapabilityCall.status.in_(("unknown", "needs_reconciliation"))
    ).count()
    rows = (
        db.query(AssistantCapabilityCall)
        .filter(AssistantCapabilityCall.status.in_(("unknown", "needs_reconciliation")))
        .order_by(AssistantCapabilityCall.updated_at.asc(), AssistantCapabilityCall.id.asc())
        .limit(limit)
        .all()
    )
    return ApiResponse.ok(
        {
            "items": [
                _safe_call_summary(
                    row,
                    run_revision=int(
                        getattr(db.get(AssistantChatRun, row.run_id), "state_revision", 0)
                    ),
                    evidence_artifact_ids=_safe_evidence_artifact_ids(db, row),
                )
                for row in rows
            ],
            "total": total,
        }
    )


@router.post("/{call_id}/reconcile", status_code=200, response_model=ApiResponse)
def reconcile_capability_call(
    call_id: UUID,
    body: ReconcileCapabilityCallRequest,
    request: Request,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    if not bool(getattr(settings, "assistant_capability_reconciliation_enabled", False)):
        raise ApiException(
            status_code=503,
            code=50374,
            message="reconciliation_unavailable",
            details={"reasonCode": "reconciliation_disabled"},
        )
    service = CapabilityReconciliationService(
        db,
        evidence_verifier=HmacReconciliationEvidenceVerifier(_evidence_secret(settings)),
    )
    decision = ReconciliationDecisionRequest(
        call_id=call_id,
        expected_call_revision=body.expected_call_revision,
        expected_run_revision=body.expected_run_revision,
        decision=body.decision,
        reason=body.reason.strip(),
        evidence_artifact_ids=tuple(body.evidence_artifact_ids),
        resolution_request_id=body.request_id,
    )
    try:
        result = service.apply(decision, actor=principal, commit=False)
        # The generic protected-browser policy stages its own safe mutation audit.
        # This explicit event binds the reconciliation result to the same
        # authenticated Operator Session and transaction as the CAS mutation.
        OperatorAuditRepository(db).append(
            event_type="capability_reconciliation_committed",
            outcome="succeeded",
            context=request_security_context(request, settings),
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            metadata={
                "callId": str(call_id),
                "decision": body.decision,
                "reconciliationId": str(result.reconciliation_id),
                "created": bool(result.created),
            },
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - convert only at API boundary
        db.rollback()
        if isinstance(exc, ApiException):
            raise
        raise _map_reconciliation_error(exc) from exc
    return ApiResponse.ok(
        {
            "callId": str(result.call_id),
            "decision": result.decision,
            "resultingCallStatus": result.resulting_call_status,
            "resultingCallRevision": int(result.resulting_call_revision),
            "resultingRunRevision": int(result.resulting_run_revision),
            "reconciliationId": str(result.reconciliation_id),
            "created": bool(result.created),
        }
    )


def _issuer_for_principal(
    db: Session,
    *,
    settings: Settings,
    principal: OperatorPrincipal,
) -> tuple[object, object]:
    """Construct the server-owned issuer and actor from this Session only."""
    from app.assistant.capability_calls.reconciliation import (
        AuthorizedReconciliationActor,
        ReconciliationEvidenceIssuer,
    )

    actor = AuthorizedReconciliationActor(
        actor_admin_id=principal.operator_id,
        authorization_method=principal.authentication_method,
        session_id=principal.session_id,
    )
    issuer = ReconciliationEvidenceIssuer(
        db,
        signer=HmacReconciliationEvidenceVerifier(_evidence_secret(settings)),
        operator_authorizer=lambda _request: actor,
    )
    return issuer, actor


@router.post(
    "/{call_id}/reconciliation-evidence/failure",
    status_code=200,
    response_model=ApiResponse,
)
def issue_failure_evidence(
    call_id: UUID,
    body: IssueFailureEvidenceRequest,
    request: Request,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    if not bool(getattr(settings, "assistant_capability_reconciliation_enabled", False)):
        raise ApiException(
            status_code=503,
            code=50374,
            message="reconciliation_unavailable",
            details={"reasonCode": "reconciliation_disabled"},
        )
    try:
        issuer, _actor = _issuer_for_principal(
            db, settings=settings, principal=principal
        )
        artifact = issuer.issue_failure_acceptance(
            call_id=call_id,
            reason=body.reason.strip(),
            expected_call_revision=body.expected_call_revision,
            expected_run_revision=body.expected_run_revision,
        )
        OperatorAuditRepository(db).append(
            event_type="capability_reconciliation_committed",
            outcome="succeeded",
            context=request_security_context(request, settings),
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            metadata={
                "callId": str(call_id),
                "evidenceArtifactId": str(artifact.id),
                "evidenceType": "capability_call_failure",
            },
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - API boundary mapping
        db.rollback()
        raise _map_reconciliation_error(exc) from exc
    return ApiResponse.ok(
        {
            "callId": str(call_id),
            "evidenceArtifactId": str(artifact.id),
            "evidenceType": "capability_call_failure",
        }
    )


@router.post(
    "/{call_id}/reconciliation-evidence/success",
    status_code=200,
    response_model=ApiResponse,
)
def issue_success_evidence(
    call_id: UUID,
    body: IssueSuccessEvidenceRequest,
    request: Request,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _csrf: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse:
    if not bool(getattr(settings, "assistant_capability_reconciliation_enabled", False)):
        raise ApiException(
            status_code=503,
            code=50374,
            message="reconciliation_unavailable",
            details={"reasonCode": "reconciliation_disabled"},
        )
    try:
        issuer, _actor = _issuer_for_principal(
            db, settings=settings, principal=principal
        )
        artifact = issuer.issue_success_attestation(
            call_id=call_id,
            result_artifact_id=body.result_artifact_id,
            expected_call_revision=body.expected_call_revision,
            expected_run_revision=body.expected_run_revision,
        )
        OperatorAuditRepository(db).append(
            event_type="capability_reconciliation_committed",
            outcome="succeeded",
            context=request_security_context(request, settings),
            operator_id=principal.operator_id,
            session_id=principal.session_id,
            metadata={
                "callId": str(call_id),
                "evidenceArtifactId": str(artifact.id),
                "evidenceType": "capability_call_success_attestation",
            },
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - API boundary mapping
        db.rollback()
        raise _map_reconciliation_error(exc) from exc
    return ApiResponse.ok(
        {
            "callId": str(call_id),
            "evidenceArtifactId": str(artifact.id),
            "evidenceType": "capability_call_success_attestation",
        }
    )


__all__ = ["router"]
