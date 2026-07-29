"""Protected browser control-plane routes for Main-Agent runtime (Plan 2 Task 6).

Mounted under Plan 1 ``protected_browser_router`` so unsafe methods receive
Operator + CSRF plus the generic same-transaction mutation audit. Public
``/ready`` lands in Task 10 and is intentionally not mounted here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.assistant.runtime.activation import AssistantRuntimeActivationService
from app.assistant.runtime.contracts import (
    ActivatedRolloutResult,
    ActivateRolloutRequest,
    PreparedRolloutResult,
    PrepareRolloutRequest,
    RuntimeActivationRejected,
    RuntimeControlConflict,
    RuntimeControlResult,
    RuntimeGateEvidenceMissing,
    RuntimeRequestReuseConflict,
    RolloutNotPrepared,
    SetNewRunsEnabledRequest,
)
from app.assistant.runtime.models import (
    AssistantMainAgentRolloutControl,
    AssistantMainAgentRolloutRevision,
)
from app.assistant.runtime.readiness import (
    AssistantReadinessService,
    project_authenticated_readiness,
)
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db
from app.operator_auth.contracts import OperatorPrincipal
from app.operator_auth.dependencies import (
    require_csrf,
    require_operator_principal,
    require_viewer_principal,
)

_RUNTIME_PREFIX = "/api/assistant-runtime"
_RUNTIME_TAGS = ["assistant-runtime"]

# Stable HTTP status/code mapping for control-plane failures.
_CODE_CONTROL_CONFLICT = 40960
_CODE_REQUEST_REUSE = 40961
_CODE_NOT_PREPARED = 42260
_CODE_ACTIVATION_REJECTED = 50360
_CODE_GATE_EVIDENCE = 50361

REASON_TO_MESSAGE: dict[str, str] = {
    "worker_unavailable": "assistant_worker_unavailable",
    "runtime_closure_drift": "assistant_runtime_closure_drift",
    "rollout_inactive": "assistant_rollout_inactive",
    "new_runs_disabled": "assistant_new_runs_disabled",
    "system_not_initialized": "assistant_system_not_initialized",
    "operator_missing": "assistant_operator_missing",
    "operator_auth_unavailable": "assistant_operator_auth_unavailable",
    "system_seed_invalid": "assistant_system_seed_invalid",
    "profile_unpublished": "assistant_profile_unpublished",
    "model_unbound": "assistant_model_unbound",
    "schema_incompatible": "assistant_schema_incompatible",
    "build_revision_missing": "assistant_build_revision_missing",
    "profile_gate_use_missing": "assistant_gate_evidence_missing",
    "package_gate_use_missing": "assistant_gate_evidence_missing",
    "package_version_missing": "assistant_gate_evidence_missing",
    "gate_evidence_missing": "assistant_gate_evidence_missing",
}


router = APIRouter(prefix=_RUNTIME_PREFIX, tags=_RUNTIME_TAGS)


def _map_activation_error(exc: Exception) -> ApiException:
    if isinstance(exc, RuntimeControlConflict):
        return ApiException(
            status_code=409,
            code=_CODE_CONTROL_CONFLICT,
            message=RuntimeControlConflict.code,
        )
    if isinstance(exc, RuntimeRequestReuseConflict):
        return ApiException(
            status_code=409,
            code=_CODE_REQUEST_REUSE,
            message=RuntimeRequestReuseConflict.code,
        )
    if isinstance(exc, RolloutNotPrepared):
        return ApiException(
            status_code=422,
            code=_CODE_NOT_PREPARED,
            message=RolloutNotPrepared.code,
        )
    if isinstance(exc, RuntimeGateEvidenceMissing):
        return ApiException(
            status_code=503,
            code=_CODE_GATE_EVIDENCE,
            message=REASON_TO_MESSAGE.get(exc.reason_code, RuntimeGateEvidenceMissing.code),
            details={"reasonCode": exc.reason_code},
        )
    if isinstance(exc, RuntimeActivationRejected):
        message = REASON_TO_MESSAGE.get(
            exc.reason_code, f"assistant_{exc.reason_code}"
        )
        return ApiException(
            status_code=503,
            code=_CODE_ACTIVATION_REJECTED,
            message=message,
            details={"reasonCode": exc.reason_code},
        )
    raise exc


def _revision_summary(row: AssistantMainAgentRolloutRevision) -> dict[str, Any]:
    return {
        "rolloutRevisionId": str(row.id),
        "revisionLabel": str(row.revision_label),
        "revisionDigest": str(row.revision_digest),
        "profileVersionId": str(row.profile_version_id),
        "modelId": str(row.model_id),
        "buildRevision": str(row.build_revision),
        "preparedReason": str(row.prepared_reason),
        "preparedByOperatorId": (
            str(row.prepared_by_operator_id)
            if row.prepared_by_operator_id is not None
            else None
        ),
        "createdAt": row.created_at.isoformat() if row.created_at is not None else None,
    }


def _control_summary(control: AssistantMainAgentRolloutControl | None) -> dict[str, Any]:
    if control is None:
        return {
            "activeRolloutRevisionId": None,
            "controlRevision": 0,
            "newRunsEnabled": True,
        }
    return {
        "activeRolloutRevisionId": (
            str(control.active_rollout_revision_id)
            if control.active_rollout_revision_id is not None
            else None
        ),
        "controlRevision": int(control.state_revision),
        "newRunsEnabled": bool(control.new_runs_enabled),
    }


@router.get("/readiness", name="assistant_runtime_readiness")
def get_runtime_readiness(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Authenticated readiness diagnostics (safe IDs + worker list only)."""
    del principal  # auth only
    snapshot = AssistantReadinessService(db).evaluate()
    return ApiResponse.ok(project_authenticated_readiness(snapshot))


@router.get("/rollouts", name="assistant_runtime_list_rollouts")
def list_rollouts(
    principal: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Immutable revision summaries + current control pointer (viewer)."""
    del principal
    repo = AssistantRuntimeRepository(db)
    control = repo.get_control()
    revisions = repo.list_revisions(limit=50)
    return ApiResponse.ok(
        {
            "control": _control_summary(control),
            "revisions": [_revision_summary(row) for row in revisions],
        }
    )


@router.post("/rollouts/prepare", status_code=201, name="assistant_runtime_prepare_rollout")
def prepare_rollout(
    body: PrepareRolloutRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result: PreparedRolloutResult = AssistantRuntimeActivationService(db).prepare(
            body, principal=principal
        )
    except Exception as exc:  # noqa: BLE001 — map known domain errors only
        if isinstance(
            exc,
            (
                RuntimeControlConflict,
                RuntimeRequestReuseConflict,
                RolloutNotPrepared,
                RuntimeActivationRejected,
                RuntimeGateEvidenceMissing,
            ),
        ):
            raise _map_activation_error(exc) from exc
        raise
    return ApiResponse.ok(result.model_dump(mode="json", by_alias=True))


@router.post(
    "/rollouts/{revision_id}/activate",
    name="assistant_runtime_activate_rollout",
)
def activate_rollout(
    revision_id: UUID,
    body: ActivateRolloutRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result: ActivatedRolloutResult = AssistantRuntimeActivationService(db).activate(
            revision_id, body, principal=principal
        )
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                RuntimeControlConflict,
                RuntimeRequestReuseConflict,
                RolloutNotPrepared,
                RuntimeActivationRejected,
                RuntimeGateEvidenceMissing,
            ),
        ):
            raise _map_activation_error(exc) from exc
        raise
    return ApiResponse.ok(result.model_dump(mode="json", by_alias=True))


@router.post("/new-runs", name="assistant_runtime_set_new_runs")
def set_new_runs_enabled(
    body: SetNewRunsEnabledRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        result: RuntimeControlResult = AssistantRuntimeActivationService(
            db
        ).set_new_runs_enabled(body, principal=principal)
    except Exception as exc:  # noqa: BLE001
        if isinstance(
            exc,
            (
                RuntimeControlConflict,
                RuntimeRequestReuseConflict,
                RolloutNotPrepared,
                RuntimeActivationRejected,
                RuntimeGateEvidenceMissing,
            ),
        ):
            raise _map_activation_error(exc) from exc
        raise
    return ApiResponse.ok(result.model_dump(mode="json", by_alias=True))
