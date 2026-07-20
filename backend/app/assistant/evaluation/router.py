"""Plan 09 evaluation / publish-gate HTTP surface.

Mounted under the same trusted-dev guard as skill admin. Production/staging
keep this router unmounted until a real principal dependency exists.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.evaluation.gates import PublishGateError, PublishGateService
from app.assistant.evaluation.models import (
    AssistantSkillEvalDataset,
    AssistantSkillPublishGate,
)
from app.assistant.skills.models import AssistantSkillVersion
from app.assistant.evaluation.repository import (
    EvaluationRepository,
    EvaluationRepositoryError,
)
from app.assistant.evaluation.schemas import (
    CancelEvalRunBody,
    CreateEvalRunBody,
    CreateGateBody,
    DatasetSummary,
    EvalEventSummary,
    EvalRunSummary,
    PublishGateSummary,
)
from app.assistant.skills.admin_router import (
    get_trusted_operator_principal,
    should_mount_skill_admin_router,
)
from app.assistant.skills.principal import OperatorPrincipal
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db

PLAN09_EVAL_PREFIX = "/api/assistant-config/skill-eval"

skill_eval_router = APIRouter(
    prefix=PLAN09_EVAL_PREFIX,
    tags=["assistant-skill-eval"],
)


def _dto(model: Any) -> Any:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, mode="json")
    return model


def _map_repo_error(exc: EvaluationRepositoryError) -> ApiException:
    code = str(getattr(exc, "code", "") or "")
    message = str(exc)
    if code in {"not_found", "NOT_FOUND"} or "not found" in message.lower():
        return ApiException(status_code=404, code=40493, message=message)
    if code in {"stale_revision", "STALE_REVISION", "conflict", "CONFLICT"}:
        return ApiException(status_code=409, code=40993, message=message)
    if code in {"immutable", "IMMUTABLE", "forbidden_transition", "FORBIDDEN_TRANSITION"}:
        return ApiException(status_code=409, code=40994, message=message)
    if code in {"invalid_input", "INVALID_INPUT", "ownership", "OWNERSHIP"}:
        return ApiException(status_code=422, code=42293, message=message)
    return ApiException(status_code=400, code=40093, message=message)


def _map_gate_error(exc: PublishGateError) -> ApiException:
    status = int(getattr(exc, "http_status", 400) or 400)
    code = int(getattr(exc, "http_code", 40095) or 40095)
    return ApiException(
        status_code=status,
        code=code,
        message=str(exc),
        details=getattr(exc, "details", None),
    )


def _run_summary(row: Any) -> EvalRunSummary:
    return EvalRunSummary(
        id=row.id,
        subjectKind=row.subject_kind,
        subjectAggregateId=row.subject_aggregate_id,
        subjectVersionId=row.subject_version_id,
        mode=row.mode,
        status=row.status,
        stateRevision=int(row.state_revision or 0),
        lastEventSeq=int(row.last_event_seq or 0),
        failureCode=row.failure_code,
        createdAt=getattr(row, "created_at", None),
        startedAt=row.started_at,
        endedAt=row.ended_at,
    )


def _gate_summary(row: AssistantSkillPublishGate) -> PublishGateSummary:
    return PublishGateSummary(
        id=row.id,
        decision=row.decision,  # type: ignore[arg-type]
        subjectKind=row.subject_kind,
        subjectAggregateId=row.subject_aggregate_id,
        subjectVersionId=row.subject_version_id,
        expiresAt=row.expires_at,
        waiverCodes=list(row.waiver_codes or []),
        requestId=row.request_id,
        createdAt=row.created_at,
    )


@skill_eval_router.get("/datasets")
def list_eval_datasets(
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    rows = list(
        db.execute(
            select(AssistantSkillEvalDataset).order_by(AssistantSkillEvalDataset.stable_key.asc())
        ).scalars()
    )
    items = [
        _dto(
            DatasetSummary(
                id=r.id,
                stableKey=r.stable_key,
                displayName=r.display_name,
                draftVersionId=None,
                publishedVersionId=r.current_version_id,
            )
        )
        for r in rows
    ]
    return ApiResponse.ok({"items": items, "total": len(items)})


@skill_eval_router.post("/runs")
def create_eval_run(
    body: CreateEvalRunBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Admit an evaluation run with server-resolved subject digests.

    Client-supplied content/binding digests are ignored for skill subjects —
    values are recomputed from the immutable version row. Unknown dataset
    version IDs are rejected; interactive runs may omit datasets (empty list)
    and never invent random dataset UUIDs.
    """
    repo = EvaluationRepository(db)
    content_digest = body.subject_content_digest
    binding_digest = body.subject_binding_digest
    dataset_ids = list(body.dataset_version_ids or [])

    # Server-side subject resolution for skill packages.
    if body.subject_kind in {"skill_draft", "skill_version"}:
        version = (
            db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.id == body.subject_version_id,
                AssistantSkillVersion.skill_package_id == body.subject_aggregate_id,
            )
            .one_or_none()
        )
        if version is None:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"skill version not found: {body.subject_version_id}",
            )
        content_digest = str(version.content_digest or "")
        binding_digest = str(
            version.binding_set_digest or version.content_digest or ""
        )
        if len(content_digest) != 64 or len(binding_digest) != 64:
            raise ApiException(
                status_code=422,
                code=42293,
                message="skill version digests are incomplete; re-save the draft",
            )

    if dataset_ids:
        from app.assistant.evaluation.models import AssistantSkillEvalDatasetVersion

        for dv_id in dataset_ids:
            row = db.get(AssistantSkillEvalDatasetVersion, dv_id)
            if row is None:
                raise ApiException(
                    status_code=422,
                    code=42293,
                    message=f"unknown dataset version id: {dv_id}",
                )
    elif body.mode != "interactive_scripted":
        raise ApiException(
            status_code=422,
            code=42293,
            message="dataset_version_ids required for dataset evaluation modes",
        )

    try:
        run = repo.create_run(
            subject_kind=body.subject_kind,
            subject_aggregate_id=body.subject_aggregate_id,
            subject_version_id=body.subject_version_id,
            subject_content_digest=content_digest,
            subject_binding_digest=binding_digest,
            dataset_version_ids=dataset_ids,
            threshold_policy_version=body.threshold_policy_version,
            mode=body.mode,
            isolation_namespace_id=body.isolation_namespace_id or uuid4(),
            runtime_contract_version=body.runtime_contract_version,
            required_build_revision=body.required_build_revision,
            isolation_digest=body.isolation_digest,
            actor_principal=body.actor_principal or principal.principal_id,
            request_id=body.request_id,
        )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    except ValueError as exc:
        db.rollback()
        # repository may still require non-empty dataset list
        raise ApiException(status_code=422, code=42293, message=str(exc)) from exc
    return ApiResponse.ok(_dto(_run_summary(run)))


@skill_eval_router.get("/runs/{run_id}")
def get_eval_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise ApiException(status_code=404, code=40493, message="eval run not found")
    return ApiResponse.ok(_dto(_run_summary(run)))


@skill_eval_router.get("/runs/{run_id}/events")
def list_eval_run_events(
    run_id: UUID,
    after_sequence: int = Query(0, alias="afterSequence", ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    if repo.get_run(run_id) is None:
        raise ApiException(status_code=404, code=40493, message="eval run not found")
    try:
        events = repo.list_events_after(
            eval_run_id=run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    except EvaluationRepositoryError as exc:
        raise _map_repo_error(exc) from exc
    items = [
        _dto(
            EvalEventSummary(
                sequence=int(e.sequence),
                eventType=e.event_type,
                payload=dict(e.payload or {}),
                createdAt=e.created_at,
            )
        )
        for e in events
    ]
    return ApiResponse.ok(
        {
            "items": items,
            "afterSequence": after_sequence,
            "nextSequence": items[-1]["sequence"] if items else after_sequence,
        }
    )


@skill_eval_router.post("/runs/{run_id}/cancel")
def cancel_eval_run(
    run_id: UUID,
    body: CancelEvalRunBody | None = None,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    try:
        run = repo.request_cancel_run(
            run_id=run_id,
            expected_revision=None if body is None else body.expected_state_revision,
        )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    return ApiResponse.ok(_dto(_run_summary(run)))


@skill_eval_router.post("/gates")
def create_publish_gate(
    body: CreateGateBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    svc = PublishGateService(db)
    try:
        result = svc.create_gate(
            body.to_service_request(),
            actor_principal=principal.principal_id,
        )
        db.commit()
    except PublishGateError as exc:
        db.rollback()
        raise _map_gate_error(exc) from exc
    except Exception as exc:  # pragma: no cover - validation / integrity
        db.rollback()
        raise ApiException(status_code=422, code=42295, message=str(exc)) from exc
    return ApiResponse.ok(
        {
            "gate": _dto(_gate_summary(result.gate)),
            "decision": result.decision,
            "acceptedWaiverCodes": list(result.accepted_waiver_codes),
            # Never echo assertion/metric snapshots as client-authored fields;
            # include bounded server-derived copies for UI display only.
            "assertionSnapshot": dict(result.assertion_snapshot or {}),
            "metricSnapshot": dict(result.metric_snapshot or {}),
        }
    )


@skill_eval_router.get("/gates/{gate_id}")
def get_publish_gate(
    gate_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    row = db.get(AssistantSkillPublishGate, gate_id)
    if row is None:
        raise ApiException(status_code=404, code=40495, message="publish gate not found")
    return ApiResponse.ok(
        {
            "gate": _dto(_gate_summary(row)),
            "assertionSnapshot": dict(row.assertion_snapshot or {}),
            "metricSnapshot": dict(row.metric_snapshot or {}),
        }
    )


def mount_skill_eval_router(app: Any, *, app_env: str | None = None) -> bool:
    """Conditionally mount Plan 09 evaluation router with the admin trusted guard."""
    if not should_mount_skill_admin_router(app_env=app_env):
        return False
    app.include_router(skill_eval_router)
    return True


__all__ = [
    "PLAN09_EVAL_PREFIX",
    "mount_skill_eval_router",
    "skill_eval_router",
]
