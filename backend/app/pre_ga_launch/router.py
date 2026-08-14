"""Safe operator surfaces for pre-GA launch qualification state."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.common.schemas import to_camel
from app.database import get_db
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.dependencies import (
    require_csrf,
    require_operator_principal,
    require_viewer_principal,
)
from app.pre_ga_launch.contracts import (
    ConsumePreGaLaunchCandidateRequest,
    CreatePreGaLaunchCandidateRequest,
)
from app.pre_ga_launch.factory import default_pre_ga_launch_service
from app.pre_ga_launch.models import (
    PreGaLaunchCandidate,
    PreGaLaunchControl,
    PreGaLaunchGateUse,
)
from app.pre_ga_launch.service import PreGaLaunchError, PreGaLaunchService


_PREFIX = "/api/pre-ga-launch"
_TAGS = ["pre-ga-launch"]
_CODE_INVALID = 42270
_CODE_NOT_FOUND = 40470
_CODE_CONFLICT = 40970
_CODE_UNAVAILABLE = 50370


router = APIRouter(prefix=_PREFIX, tags=_TAGS)


def get_launch_service(db: Session = Depends(get_db)) -> PreGaLaunchService:
    return default_pre_ga_launch_service(db)


def _error(exc: PreGaLaunchError) -> ApiException:
    if exc.status_code == 409 or exc.safe_code == "launch_control_conflict":
        status = 409
        code = _CODE_CONFLICT
    elif exc.safe_code in {"launch_candidate_missing"}:
        status = 404
        code = _CODE_NOT_FOUND
    elif exc.safe_code in {
        "launch_evidence_invalid",
        "launch_request_reuse_conflict",
    }:
        status = exc.status_code
        code = _CODE_INVALID if status != 409 else _CODE_CONFLICT
    elif exc.safe_code in {
        "launch_target_unavailable",
        "launch_evidence_unavailable",
        "launch_subject_unavailable",
    }:
        status = 503
        code = _CODE_UNAVAILABLE
    else:
        status = exc.status_code
        code = _CODE_INVALID
    return ApiException(status_code=status, code=code, message=exc.safe_code)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _use_for_candidate(db: Session, candidate: PreGaLaunchCandidate) -> PreGaLaunchGateUse | None:
    return (
        db.query(PreGaLaunchGateUse)
        .filter(PreGaLaunchGateUse.candidate_id == candidate.id)
        .order_by(PreGaLaunchGateUse.resulting_control_revision.desc())
        .first()
    )


def candidate_projection(
    db: Session,
    candidate: PreGaLaunchCandidate,
    *,
    control: PreGaLaunchControl | None = None,
) -> dict[str, Any]:
    use = _use_for_candidate(db, candidate)
    active = bool(
        control is not None
        and control.active_candidate_id == candidate.id
        and control.active_subject_digest == candidate.subject_digest
    )
    return {
        "candidateId": str(candidate.id),
        "passed": bool(candidate.passed),
        "failureCodes": list(candidate.safe_failure_codes or ()),
        "qualificationTargetDigest": str(candidate.qualification_target_digest),
        "subjectDigest": str(candidate.subject_digest),
        "buildRevision": str(candidate.build_revision),
        "imageSetDigest": str(candidate.image_set_digest),
        "deployedArtifactSetDigest": str(candidate.deployed_artifact_set_digest),
        "schemaFamily": str(candidate.schema_family),
        "schemaRevision": str(candidate.schema_revision),
        "schemaRuntimeIdentityDigest": str(candidate.schema_runtime_identity_digest),
        "rolloutRevisionId": str(candidate.rollout_revision_id),
        "profileVersionId": str(candidate.profile_version_id),
        "modelId": str(candidate.model_id),
        "runtimeClosureDigest": str(candidate.runtime_closure_digest),
        "automatedEvidenceManifestDigest": str(candidate.automated_evidence_manifest_digest),
        "rehearsalEvidenceManifestDigest": str(candidate.rehearsal_evidence_manifest_digest),
        "operationalSnapshotDigest": str(candidate.operational_snapshot_digest),
        "unknownCallCount": int(candidate.unknown_call_count),
        "needsReconciliationCount": int(candidate.needs_reconciliation_count),
        "activeRunCount": int(candidate.active_run_count),
        "issuedAt": _iso(candidate.issued_at),
        "expiresAt": _iso(candidate.expires_at),
        "usedAt": _iso(use.used_at if use is not None else None),
        "resultingControlRevision": (
            int(use.resulting_control_revision) if use is not None else None
        ),
        "active": active,
    }


_TARGET_SAFE_FIELDS = (
    "schema_version",
    "build_revision",
    "image_set_digest",
    "deployed_artifact_set_digest",
    "schema_family",
    "schema_revision",
    "schema_application_fingerprint",
    "schema_control_fingerprint",
    "schema_identity_contract_version",
    "production_schema_deployment_class",
    "schema_seed_contract_digest",
    "schema_runtime_contract_version",
    "schema_checkpoint_codec_version",
    "schema_capability_feature_digest",
    "production_schema_runtime_identity_digest",
    "schema_contract_material_digest",
    "operator_auth_contract_version",
    "rollout_revision_id",
    "rollout_revision_digest",
    "runtime_closure_digest",
    "profile_version_id",
    "profile_content_digest",
    "model_id",
    "model_identity_digest",
    "package_closure_digest",
    "capability_closure_digest",
    "seed_manifest_digest",
    "worker_runtime_contract_version",
    "worker_checkpoint_codec_version",
    "worker_capability_feature_digest",
    "create_entry_contract_digest",
    "write_policy_digest",
    "write_cohort_digest",
    "reconciliation_contract_version",
    "dependency_lock_set_digest",
    "scenario_set_digest",
    "required_assertion_set_digest",
    "runner_contract_version",
    "runner_identity_digest",
    "evidence_trust_set_digest",
    "qualification_target_digest",
)


def _target_projection(target: Any) -> dict[str, Any]:
    payload = target.model_dump(mode="json", by_alias=True)
    return {
        to_camel(field): payload[to_camel(field)]
        for field in _TARGET_SAFE_FIELDS
        if to_camel(field) in payload
    }


@router.get("/status", name="pre_ga_launch_status")
def get_status(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
    service: PreGaLaunchService = Depends(get_launch_service),
) -> ApiResponse:
    del principal
    try:
        authorization = service.evaluate_current_launch()
    except PreGaLaunchError as exc:
        raise _error(exc) from exc
    control = db.query(PreGaLaunchControl).filter(
        PreGaLaunchControl.singleton_key == "pre_ga_launch"
    ).one_or_none()
    candidate = (
        db.get(PreGaLaunchCandidate, control.active_candidate_id)
        if control is not None and control.active_candidate_id is not None
        else None
    )
    return ApiResponse.ok(
        {
            "launched": bool(authorization.launched),
            "reasonCode": authorization.reason_code,
            "controlRevision": int(authorization.control_revision),
            "activeSubjectDigest": authorization.active_subject_digest,
            "candidate": (
                candidate_projection(db, candidate, control=control)
                if candidate is not None
                else None
            ),
        }
    )


@router.get("/qualification-target", name="pre_ga_launch_qualification_target")
def get_qualification_target(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    service: PreGaLaunchService = Depends(get_launch_service),
) -> ApiResponse:
    del principal
    if service.target_provider is None:
        raise ApiException(status_code=503, code=_CODE_UNAVAILABLE, message="launch_target_unavailable")
    try:
        target = service.target_provider()
    except Exception:
        raise ApiException(
            status_code=503,
            code=_CODE_UNAVAILABLE,
            message="launch_target_unavailable",
        ) from None
    return ApiResponse.ok(_target_projection(target))


@router.get("/candidates", name="pre_ga_launch_candidates")
def list_candidates(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    cursor_issued_at: str | None = Query(default=None, alias="cursorIssuedAt"),
    cursor_id: UUID | None = Query(default=None, alias="cursorId"),
) -> ApiResponse:
    del principal
    query = db.query(PreGaLaunchCandidate)
    if cursor_issued_at is not None and cursor_id is not None:
        try:
            cursor_time = datetime.fromisoformat(cursor_issued_at)
        except ValueError:
            raise ApiException(status_code=422, code=_CODE_INVALID, message="invalid_cursor") from None
        query = query.filter(
            (PreGaLaunchCandidate.issued_at < cursor_time)
            | (
                (PreGaLaunchCandidate.issued_at == cursor_time)
                & (PreGaLaunchCandidate.id < cursor_id)
            )
        )
    rows = (
        query.order_by(
            PreGaLaunchCandidate.issued_at.desc(),
            PreGaLaunchCandidate.id.desc(),
        )
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    control = db.query(PreGaLaunchControl).filter(
        PreGaLaunchControl.singleton_key == "pre_ga_launch"
    ).one_or_none()
    return ApiResponse.ok(
        {
            "items": [candidate_projection(db, row, control=control) for row in rows],
            "nextCursor": (
                {
                    "issuedAt": _iso(rows[-1].issued_at),
                    "id": str(rows[-1].id),
                }
                if has_more and rows
                else None
            ),
        }
    )


@router.post("/candidates", status_code=201, name="pre_ga_launch_create_candidate")
def create_candidate(
    body: CreatePreGaLaunchCandidateRequest,
    response: Response,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    service: PreGaLaunchService = Depends(get_launch_service),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result = service.create_candidate(body, principal=principal)
    except PreGaLaunchError as exc:
        raise _error(exc) from exc
    if result.replayed:
        response.status_code = 200
    return ApiResponse.ok(
        candidate_projection(db, result.candidate),
        message="REPLAY" if result.replayed else "OK",
    )


@router.post(
    "/candidates/{candidate_id}/consume",
    name="pre_ga_launch_consume_candidate",
)
def consume_candidate(
    candidate_id: UUID,
    body: ConsumePreGaLaunchCandidateRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    service: PreGaLaunchService = Depends(get_launch_service),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result = service.consume_candidate(candidate_id, body, principal=principal)
    except PreGaLaunchError as exc:
        raise _error(exc) from exc
    candidate = db.get(PreGaLaunchCandidate, candidate_id)
    return ApiResponse.ok(
        {
            "controlRevision": int(result.control.revision),
            "launchedAt": _iso(result.control.launched_at),
            "gateUseId": str(result.gate_use.id),
            "candidate": (
                candidate_projection(db, candidate, control=result.control)
                if candidate is not None
                else None
            ),
        },
        message="REPLAY" if result.replayed else "OK",
    )


__all__ = ["candidate_projection", "get_launch_service", "router"]
