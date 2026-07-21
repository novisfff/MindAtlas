"""Plan 09 evaluation / publish-gate HTTP surface.

Mounted under the same trusted-dev guard as skill admin. Production/staging
keep this router unmounted until a real principal dependency exists.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.capability_calls.approval import redact_mapping
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
from app.assistant.evaluation.gates import (
    DEFAULT_POLICY_VERSION,
    DEFAULT_RUNTIME_CONTRACT_VERSION,
    PublishGateError,
    PublishGateService,
    current_build_revision,
)
from app.assistant.evaluation.isolation import build_isolation_context, isolation_digest
from app.assistant.evaluation.models import (
    AssistantSkillEvalDataset,
    AssistantSkillEvalDatasetDraft,
    AssistantSkillPublishGate,
)
from app.assistant.evaluation.orchestration import (
    EVAL_POLICY_DIGEST,
    resolve_provider_fixture,
)
from app.assistant.evaluation.repository import (
    TERMINAL_RUN_STATUSES,
    EvaluationRepository,
    EvaluationRepositoryError,
)
from app.assistant.evaluation.schemas import (
    ArtifactSummary,
    CancelEvalRunBody,
    CaseResultSummary,
    CreateDatasetBody,
    CreateEvalRunBody,
    CreateGateBody,
    DatasetDraftSummary,
    DatasetSummary,
    DatasetVersionSummary,
    EvalEventSummary,
    EvalRunSummary,
    PublishDatasetBody,
    PublishGateSummary,
    PutDatasetDraftBody,
    QualifyingEvidenceSummary,
)
from app.assistant.skills.admin_router import (
    get_trusted_operator_principal,
    should_mount_skill_admin_router,
)
from app.assistant.skills.candidate_closure import (
    CandidateClosureError,
    resolve_skill_candidate_closure,
)
from app.assistant.skills.models import AssistantMainAgentProfileVersion
from app.assistant.skills.principal import OperatorPrincipal
from app.common.exceptions import ApiException
from app.common.responses import ApiResponse
from app.database import get_db

PLAN09_EVAL_PREFIX = "/api/assistant-config/skill-eval"

# SSE idle heartbeat interval (seconds). Tests use a short path via immediate
# heartbeat after terminal replay; production uses this poll cadence.
_SSE_POLL_INTERVAL_SEC = 0.25
_SSE_HEARTBEAT_EVERY_IDLE_POLLS = 1

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


def _require_operator(principal: OperatorPrincipal) -> None:
    if not principal.is_operator:
        raise ApiException(
            status_code=403,
            code=40391,
            message="operator role is required for this transition",
        )


def _map_repo_error(exc: EvaluationRepositoryError) -> ApiException:
    code = str(getattr(exc, "code", "") or "")
    message = str(exc)
    if code in {"not_found", "NOT_FOUND"} or "not found" in message.lower():
        return ApiException(status_code=404, code=40493, message=message)
    if code in {"stale_revision", "STALE_REVISION", "conflict", "CONFLICT"}:
        return ApiException(status_code=409, code=40993, message=message)
    if code in {
        "immutable",
        "IMMUTABLE",
        "forbidden_transition",
        "FORBIDDEN_TRANSITION",
    }:
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
        gateEligible=bool(getattr(row, "gate_eligible", False)),
        evidenceProvenance=getattr(row, "evidence_provenance", None),
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


def _dataset_summary(
    row: AssistantSkillEvalDataset, draft: AssistantSkillEvalDatasetDraft | None = None
) -> DatasetSummary:
    return DatasetSummary(
        id=row.id,
        stableKey=row.stable_key,
        displayName=row.display_name,
        ownership=row.ownership,
        aggregateRevision=int(row.aggregate_revision or 0),
        draftVersionId=draft.id if draft is not None else None,
        publishedVersionId=row.current_version_id,
    )


def _draft_summary(draft: AssistantSkillEvalDatasetDraft) -> DatasetDraftSummary:
    return DatasetDraftSummary(
        id=draft.id,
        datasetId=draft.dataset_id,
        draftRevision=int(draft.draft_revision or 0),
        schemaVersion=int(draft.schema_version or 1),
        casesSnapshot=list(draft.cases_snapshot or []),
        draftDigest=draft.draft_digest,
        baseVersionId=draft.base_version_id,
        updatedBy=draft.updated_by,
    )


def _version_summary(version: Any, *, case_count: int = 0) -> DatasetVersionSummary:
    return DatasetVersionSummary(
        id=version.id,
        datasetId=version.dataset_id,
        sequence=int(version.sequence),
        versionName=version.version_name,
        schemaVersion=int(version.schema_version or 1),
        contentDigest=version.content_digest,
        sourceFixtureRevision=version.source_fixture_revision,
        caseCount=case_count,
        createdBy=version.created_by,
        createdAt=version.created_at,
    )


def _redact_event_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(redact_mapping(payload or {}) or {})


def _resolve_profile_version(db: Session, profile_version_id: UUID) -> Any:
    row = db.get(AssistantMainAgentProfileVersion, profile_version_id)
    if row is None:
        raise ApiException(
            status_code=422,
            code=42293,
            message=f"unknown profile version id: {profile_version_id}",
        )
    return row


def _resolve_subject_digests(
    db: Session,
    *,
    subject_kind: str,
    subject_aggregate_id: UUID,
    subject_version_id: UUID,
) -> tuple[str, str]:
    """Server-side subject content/binding digests. No client override."""
    if subject_kind in {"skill_draft", "skill_version"}:
        try:
            closure = resolve_skill_candidate_closure(
                db,
                package_id=subject_aggregate_id,
                version_id=subject_version_id,
                subject_kind=subject_kind,  # type: ignore[arg-type]
            )
        except CandidateClosureError as exc:
            if exc.code in {"skill_package_not_found", "skill_version_not_found"}:
                raise ApiException(
                    status_code=404,
                    code=40491,
                    message=f"skill version not found: {subject_version_id}",
                ) from exc
            if exc.code == "skill_content_digest_drift":
                raise ApiException(
                    status_code=409,
                    code=40993,
                    message="skill content digest drift during candidate closure",
                ) from exc
            raise ApiException(
                status_code=422,
                code=42293,
                message=str(exc),
            ) from exc
        return closure.content_digest, closure.binding_set_digest

    if subject_kind in {"main_agent_profile_draft", "main_agent_profile_version"}:
        version = db.get(AssistantMainAgentProfileVersion, subject_version_id)
        if version is None or version.profile_id != subject_aggregate_id:
            raise ApiException(
                status_code=404,
                code=40491,
                message=f"profile version not found: {subject_version_id}",
            )
        content = str(version.content_digest)
        # Profile has no capability binding set; pin a deterministic placeholder.
        binding = sha256_canonical_json(
            {
                "kind": "main_agent_profile",
                "profile_id": str(subject_aggregate_id),
                "version_id": str(subject_version_id),
                "content_digest": content,
            }
        )
        return content, binding

    if subject_kind == "legacy_baseline":
        # Deterministic legacy baseline pins — never client-authored.
        content = sha256_canonical_json(
            {
                "kind": "legacy_baseline",
                "aggregate_id": str(subject_aggregate_id),
                "version_id": str(subject_version_id),
            }
        )
        binding = sha256_canonical_json({"kind": "legacy_baseline", "binding": "none"})
        return content, binding

    raise ApiException(
        status_code=422,
        code=42293,
        message=f"unsupported subject_kind: {subject_kind}",
    )


def _resolve_fixture_pins(
    *, mode: str, provider_fixture_revision: str | None
) -> tuple[str | None, str | None]:
    """Resolve fixture pins from the real registry only.

    Fail closed on unknown revisions — never invent digests for opaque strings
    and never promote unknown pins to real_orchestration.
    """
    if mode != "dataset_scripted":
        return None, None
    revision = (provider_fixture_revision or "").strip()
    if not revision:
        raise ApiException(
            status_code=422,
            code=42293,
            message="dataset_scripted requires providerFixtureRevision",
        )
    # Revision may be "script_key@rev" or bare script key for builtins.
    script_key = revision
    rev: str | None = None
    if "@" in revision:
        script_key, rev = revision.split("@", 1)
    try:
        fixture = resolve_provider_fixture(script_key=script_key, revision=rev)
    except KeyError as exc:
        raise ApiException(
            status_code=422,
            code=42293,
            message=(
                "unknown providerFixtureRevision: must resolve from the "
                f"built-in fixture registry ({revision!r})"
            ),
            details={"providerFixtureRevision": revision},
        ) from exc
    digest = sha256_canonical_json(
        {
            "script_key": fixture.script_key,
            "revision": fixture.revision,
            "activates_skills": list(fixture.activates_skills),
            "capability_path": list(fixture.capability_path),
            "completes": bool(fixture.completes),
            "stop_reason": fixture.stop_reason,
            "final_text": fixture.final_text,
        }
    )
    pin_revision = f"{fixture.script_key}@{fixture.revision}"
    return pin_revision, digest


def _resolve_live_model_probe(db: Session, live_model_id: UUID) -> str:
    from app.ai_registry.models import AiModel, AiModelCapabilityProbe

    model = db.get(AiModel, live_model_id)
    if model is None:
        raise ApiException(
            status_code=422,
            code=42293,
            message=f"unknown live model id: {live_model_id}",
        )
    probe_id = getattr(model, "current_capability_probe_id", None)
    if probe_id is None:
        raise ApiException(
            status_code=422,
            code=42293,
            message="live model has no current capability probe pin",
        )
    probe = db.get(AiModelCapabilityProbe, probe_id)
    if probe is None or not probe.probe_digest:
        raise ApiException(
            status_code=422,
            code=42293,
            message="live model capability probe missing",
        )
    return str(probe.probe_digest)


def _admission_isolation_digest(
    *,
    subject_content_digest: str,
    dataset_version_ids: list[UUID],
    isolation_namespace_id: UUID,
) -> str:
    ds = tuple(dataset_version_ids) if dataset_version_ids else (uuid4(),)
    isolation = build_isolation_context(
        namespace_id=isolation_namespace_id,
        subject_digest=subject_content_digest,
        dataset_version_ids=ds,
        memory_mode="empty",
        data_mode="fixture",
    )
    return isolation_digest(isolation)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


@skill_eval_router.get("/datasets")
def list_eval_datasets(
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    rows = list(
        db.execute(
            select(AssistantSkillEvalDataset).order_by(
                AssistantSkillEvalDataset.stable_key.asc()
            )
        ).scalars()
    )
    items = []
    for r in rows:
        draft = repo.get_draft(r.id)
        items.append(_dto(_dataset_summary(r, draft)))
    return ApiResponse.ok({"items": items, "total": len(items)})


@skill_eval_router.post("/datasets")
def create_eval_dataset(
    body: CreateDatasetBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _require_operator(principal)
    if body.ownership == "system" and not principal.is_operator:
        raise ApiException(
            status_code=403,
            code=40391,
            message="operator role is required for system-object transitions",
        )
    repo = EvaluationRepository(db)
    try:
        existing = repo.get_dataset_by_key(body.stable_key)
        if existing is not None:
            raise ApiException(
                status_code=409,
                code=40993,
                message=f"dataset stable_key already exists: {body.stable_key}",
            )
        row = repo.create_dataset(
            stable_key=body.stable_key,
            display_name=body.display_name,
            description=body.description,
            ownership=body.ownership,
            actor=principal.principal_id,
        )
        # Ensure a draft row exists for subsequent GET/PUT.
        draft = repo.get_or_create_draft(
            dataset_id=row.id,
            cases_snapshot=[],
            actor=principal.principal_id,
        )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    return ApiResponse.ok(_dto(_dataset_summary(row, draft)))


@skill_eval_router.get("/datasets/{dataset_id}")
def get_eval_dataset(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    row = repo.get_dataset(dataset_id)
    if row is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    draft = repo.get_draft(dataset_id)
    return ApiResponse.ok(_dto(_dataset_summary(row, draft)))


@skill_eval_router.get("/datasets/{dataset_id}/draft")
def get_eval_dataset_draft(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    if repo.get_dataset(dataset_id) is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    draft = repo.get_draft(dataset_id)
    if draft is None:
        draft = repo.get_or_create_draft(
            dataset_id=dataset_id, cases_snapshot=[], actor=principal.principal_id
        )
        db.commit()
    return ApiResponse.ok(_dto(_draft_summary(draft)))


@skill_eval_router.put("/datasets/{dataset_id}/draft")
def put_eval_dataset_draft(
    dataset_id: UUID,
    body: PutDatasetDraftBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _require_operator(principal)
    repo = EvaluationRepository(db)
    if repo.get_dataset(dataset_id) is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    try:
        if repo.get_draft(dataset_id) is None:
            # First write: create then put when expected revision is 0.
            repo.get_or_create_draft(
                dataset_id=dataset_id,
                cases_snapshot=list(body.cases_snapshot or []),
                actor=principal.principal_id,
            )
            if int(body.expected_draft_revision) != 0:
                raise EvaluationRepositoryError(
                    "stale_revision",
                    f"expected draft revision {body.expected_draft_revision}, got 0",
                )
            # Created at revision 0 with snapshot; if non-empty and revision 0
            # expected, treat as create-only success. For empty->put at rev 0
            # with cases, still advance via put_draft when revision matches.
            draft = repo.get_draft(dataset_id)
            assert draft is not None
            if int(draft.draft_revision) == 0 and list(body.cases_snapshot or []):
                # get_or_create already stored snapshot at rev 0; bump via put
                # only when client expects 0 and wants to overwrite empty create.
                # If create already wrote the same snapshot, return as-is.
                if list(draft.cases_snapshot or []) != list(body.cases_snapshot or []):
                    draft = repo.put_draft(
                        dataset_id=dataset_id,
                        expected_draft_revision=0,
                        cases_snapshot=list(body.cases_snapshot or []),
                        actor=principal.principal_id,
                    )
        else:
            draft = repo.put_draft(
                dataset_id=dataset_id,
                expected_draft_revision=body.expected_draft_revision,
                cases_snapshot=list(body.cases_snapshot or []),
                actor=principal.principal_id,
            )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    return ApiResponse.ok(_dto(_draft_summary(draft)))


@skill_eval_router.post("/datasets/{dataset_id}/publish")
def publish_eval_dataset(
    dataset_id: UUID,
    body: PublishDatasetBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _require_operator(principal)
    repo = EvaluationRepository(db)
    dataset = repo.get_dataset(dataset_id)
    if dataset is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    if dataset.ownership == "system":
        _require_operator(principal)
    try:
        published = repo.publish_dataset_version(
            dataset_id=dataset_id,
            expected_aggregate_revision=body.expected_revision,
            expected_draft_revision=body.expected_draft_revision,
            version_name=body.version_name,
            source_fixture_revision=body.source_fixture_revision,
            actor=principal.principal_id,
        )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    version = repo.get_dataset_version(published.version_id)
    assert version is not None
    return ApiResponse.ok(
        _dto(_version_summary(version, case_count=published.case_count))
    )


@skill_eval_router.get("/datasets/{dataset_id}/versions")
def list_eval_dataset_versions(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    if repo.get_dataset(dataset_id) is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    versions = repo.list_dataset_versions(dataset_id)
    items = [
        _dto(
            _version_summary(
                v, case_count=len(repo.list_cases(v.id))
            )
        )
        for v in versions
    ]
    return ApiResponse.ok({"items": items, "total": len(items)})


@skill_eval_router.get("/datasets/{dataset_id}/versions/{version_id}")
def get_eval_dataset_version(
    dataset_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    if repo.get_dataset(dataset_id) is None:
        raise ApiException(status_code=404, code=40493, message="dataset not found")
    version = repo.get_dataset_version(version_id)
    if version is None or version.dataset_id != dataset_id:
        raise ApiException(
            status_code=404, code=40493, message="dataset version not found"
        )
    cases = repo.list_cases(version_id)
    return ApiResponse.ok(
        {
            "version": _dto(_version_summary(version, case_count=len(cases))),
            "cases": [
                {
                    "id": str(c.id),
                    "caseKey": c.case_key,
                    "ordinal": int(c.ordinal),
                    "locale": c.locale,
                    "caseDigest": c.case_digest,
                    "expectCompletion": bool(c.expect_completion),
                }
                for c in cases
            ],
        }
    )


# ---------------------------------------------------------------------------
# Eval runs
# ---------------------------------------------------------------------------


@skill_eval_router.post("/runs")
def create_eval_run(
    body: CreateEvalRunBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Admit an evaluation run with fully server-resolved digests and pins.

    Client digests/decisions/actor overrides are not accepted. Records
    ``principal.principal_id`` as the actor.
    """
    _require_operator(principal)
    repo = EvaluationRepository(db)

    # Fail closed on unknown fixture pins before subject/dataset resolution so
    # opaque revision strings never invent digests or promote real_orchestration.
    fixture_rev, fixture_dig = _resolve_fixture_pins(
        mode=body.mode,
        provider_fixture_revision=body.provider_fixture_revision,
    )

    # Validate profile version exists (admission pin).
    _resolve_profile_version(db, body.profile_version_id)

    content_digest, binding_digest = _resolve_subject_digests(
        db,
        subject_kind=body.subject_kind,
        subject_aggregate_id=body.subject_aggregate_id,
        subject_version_id=body.subject_version_id,
    )

    dataset_ids = list(body.dataset_version_ids or [])
    if dataset_ids:
        for dv_id in dataset_ids:
            row = repo.get_dataset_version(dv_id)
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

    if body.mode == "dataset_scripted":
        # Only reachable after _resolve_fixture_pins succeeded against the
        # real registry — never promote invented digests.
        evidence_provenance = "real_orchestration"
        provider_evidence_digest = None
    elif body.mode == "dataset_live":
        evidence_provenance = "live_model"
        assert body.live_model_id is not None
        provider_evidence_digest = _resolve_live_model_probe(db, body.live_model_id)
    else:
        # interactive_scripted: structural synthetic by default (not gate-eligible).
        evidence_provenance = "structural_synthetic"
        provider_evidence_digest = None
        # Optional fixture pin when client supplies a revision for scripted interactive.
        if body.provider_fixture_revision:
            fixture_rev, fixture_dig = _resolve_fixture_pins(
                mode="dataset_scripted",
                provider_fixture_revision=body.provider_fixture_revision,
            )
            evidence_provenance = "real_orchestration"

    isolation_namespace_id = uuid4()
    iso_digest = _admission_isolation_digest(
        subject_content_digest=content_digest,
        dataset_version_ids=dataset_ids,
        isolation_namespace_id=isolation_namespace_id,
    )
    build_rev = current_build_revision()
    policy_digest = EVAL_POLICY_DIGEST
    runtime_digest = sha256_canonical_json(
        {
            "runtime_contract_version": DEFAULT_RUNTIME_CONTRACT_VERSION,
            "build_revision": build_rev,
            "profile_version_id": str(body.profile_version_id),
            "locale": body.locale,
            "prompt_digest": sha256_canonical_json({"prompt": body.prompt}),
        }
    )

    try:
        run = repo.create_run(
            subject_kind=body.subject_kind,
            subject_aggregate_id=body.subject_aggregate_id,
            subject_version_id=body.subject_version_id,
            subject_content_digest=content_digest,
            subject_binding_digest=binding_digest,
            dataset_version_ids=dataset_ids,
            threshold_policy_version=str(
                THRESHOLD_POLICY_VERSION or DEFAULT_POLICY_VERSION
            ),
            mode=body.mode,
            isolation_namespace_id=isolation_namespace_id,
            runtime_contract_version=DEFAULT_RUNTIME_CONTRACT_VERSION,
            required_build_revision=build_rev,
            isolation_digest=iso_digest,
            policy_digest=policy_digest,
            runtime_digest=runtime_digest,
            evidence_provenance=evidence_provenance,  # type: ignore[arg-type]
            provider_fixture_revision=fixture_rev,
            provider_fixture_digest=fixture_dig,
            provider_evidence_digest=provider_evidence_digest,
            actor_principal=principal.principal_id,
            request_id=body.request_id,
        )
        # Stash admission prompt/locale/profile for workers via aggregate_metrics.
        run.aggregate_metrics = {
            **dict(run.aggregate_metrics or {}),
            "admission": {
                "prompt": body.prompt[:32_000],
                "locale": body.locale,
                "profileVersionId": str(body.profile_version_id),
                "liveModelId": str(body.live_model_id) if body.live_model_id else None,
            },
        }
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    except ValueError as exc:
        db.rollback()
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
                payload=_redact_event_payload(dict(e.payload or {})),
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


def _sse_frame(*, event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'), default=str)}")
    return "\n".join(lines) + "\n\n"


def _stream_eval_events(
    *,
    eval_run_id: UUID,
    after_sequence: int,
    request: Request,
) -> Iterator[str]:
    """Bounded SSE: replay after sequence, heartbeat when idle, stop terminal.

    Never holds a database transaction while waiting between polls.
    Resolve SessionLocal at call time so test rebinds and runtime config apply.
    """
    import app.database as app_database

    cursor = int(after_sequence)
    idle_polls = 0
    while True:
        if request is not None:
            # Starlette TestClient may not expose is_disconnected the same way;
            # guard with hasattr.
            try:
                if getattr(request, "is_disconnected", None) is not None:
                    # sync path — TestClient stream doesn't await; skip.
                    pass
            except Exception:
                pass

        db = app_database.SessionLocal()
        try:
            repo = EvaluationRepository(db)
            run = repo.get_run(eval_run_id)
            if run is None:
                yield _sse_frame(
                    event="error",
                    data={"code": "not_found", "message": "eval run not found"},
                )
                return
            status = str(run.status)
            events = repo.list_events_after(
                eval_run_id=eval_run_id,
                after_sequence=cursor,
                limit=100,
            )
            # Materialize rows and close session before any wait/yield delay.
            materialized = [
                (
                    int(e.sequence),
                    str(e.event_type),
                    _redact_event_payload(dict(e.payload or {})),
                )
                for e in events
            ]
            terminal = status in TERMINAL_RUN_STATUSES
        finally:
            db.close()

        if not materialized:
            idle_polls += 1
            if idle_polls >= _SSE_HEARTBEAT_EVERY_IDLE_POLLS:
                yield _sse_frame(
                    event="heartbeat",
                    data={
                        "afterSequence": cursor,
                        "status": status,
                        "ts": time.time(),
                    },
                )
                idle_polls = 0
            if terminal:
                # Terminal with no more rows — end stream after heartbeat.
                return
            time.sleep(_SSE_POLL_INTERVAL_SEC)
            continue

        idle_polls = 0
        for seq, event_type, payload in materialized:
            yield _sse_frame(
                event=event_type or "message",
                event_id=str(seq),
                data={
                    "sequence": seq,
                    "eventType": event_type,
                    "payload": payload,
                },
            )
            cursor = seq

        if terminal:
            # Delivered remaining rows for a terminal run — one heartbeat then stop.
            yield _sse_frame(
                event="heartbeat",
                data={
                    "afterSequence": cursor,
                    "status": status,
                    "ts": time.time(),
                    "terminal": True,
                },
            )
            return


@skill_eval_router.get("/runs/{run_id}/events/stream")
def stream_eval_run_events(
    run_id: UUID,
    request: Request,
    after_sequence: int = Query(0, alias="afterSequence", ge=0),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> StreamingResponse:
    _ = principal
    # Auth + existence check with the injected session, then stream with
    # short-lived sessions so we never hold a transaction while waiting.
    repo = EvaluationRepository(db)
    if repo.get_run(run_id) is None:
        raise ApiException(status_code=404, code=40493, message="eval run not found")
    # Release the request-scoped session before long-lived streaming.
    try:
        db.rollback()
    except Exception:
        pass

    return StreamingResponse(
        _stream_eval_events(
            eval_run_id=run_id,
            after_sequence=after_sequence,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@skill_eval_router.post("/runs/{run_id}/cancel")
def cancel_eval_run(
    run_id: UUID,
    body: CancelEvalRunBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Cancel requires CAS body: requestId + expectedStateRevision.

    Repository cancel CAS is revision-based; requestId is required for client
    idempotency contracts even though cancel does not stamp a durable cancel
    request-id column yet. Identical retries after status becomes cancelling
    are handled as a no-op by the repository when CAS matches.
    """
    _require_operator(principal)
    _ = body.request_id  # required for client contract / future durable cancel CAS
    repo = EvaluationRepository(db)
    try:
        run = repo.request_cancel_run(
            run_id=run_id,
            expected_revision=body.expected_state_revision,
        )
        db.commit()
    except EvaluationRepositoryError as exc:
        db.rollback()
        raise _map_repo_error(exc) from exc
    return ApiResponse.ok(_dto(_run_summary(run)))


@skill_eval_router.get("/runs/{run_id}/case-results")
def list_eval_run_case_results(
    run_id: UUID,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    if repo.get_run(run_id) is None:
        raise ApiException(status_code=404, code=40493, message="eval run not found")
    try:
        rows = repo.list_case_results(eval_run_id=run_id, limit=limit)
    except EvaluationRepositoryError as exc:
        raise _map_repo_error(exc) from exc
    items = [
        _dto(
            CaseResultSummary(
                id=r.id,
                evalRunId=r.eval_run_id,
                evalCaseId=r.eval_case_id,
                resultState=r.result_state,
                assertionDetails=_redact_event_payload(dict(r.assertion_details or {})),
                actualActiveSkills=list(r.actual_active_skills or []),
                stopReason=r.stop_reason,
                outputArtifactIds=list(r.output_artifact_ids or []),
                evidenceArtifactIds=list(r.evidence_artifact_ids or []),
                rounds=r.rounds,
                calls=r.calls,
                tokens=r.tokens,
                latencyMs=r.latency_ms,
                safeError=r.safe_error,
                resultDigest=r.result_digest,
                createdAt=r.created_at,
            )
        )
        for r in rows
    ]
    return ApiResponse.ok({"items": items, "total": len(items)})


@skill_eval_router.get("/runs/{run_id}/evidence")
def list_eval_run_evidence(
    run_id: UUID,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    """Bounded evidence: artifacts + capability calls (metadata only)."""
    _ = principal
    repo = EvaluationRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise ApiException(status_code=404, code=40493, message="eval run not found")
    try:
        artifacts = repo.list_artifacts(eval_run_id=run_id, limit=limit)
        calls = repo.list_capability_calls(eval_run_id=run_id)
    except EvaluationRepositoryError as exc:
        raise _map_repo_error(exc) from exc
    return ApiResponse.ok(
        {
            "runId": str(run_id),
            "gateEligible": bool(run.gate_eligible),
            "evidenceProvenance": run.evidence_provenance,
            "artifacts": [
                _dto(
                    ArtifactSummary(
                        id=a.id,
                        evalRunId=a.eval_run_id,
                        kind=a.kind,
                        mediaType=a.media_type,
                        storageKind=a.storage_kind,
                        contentDigest=a.content_digest,
                        byteSize=a.byte_size,
                        label=a.label,
                        createdAt=a.created_at,
                    )
                )
                for a in artifacts
            ],
            "capabilityCalls": [
                {
                    "id": str(c.id),
                    "evalCallId": str(c.eval_call_id),
                    "evalCaseId": str(c.eval_case_id),
                    "logicalCallKey": c.logical_call_key,
                    "attempt": int(c.attempt),
                    "outcome": c.outcome,
                    "bindingDigest": c.binding_digest,
                    "policyDigest": c.policy_digest,
                }
                for c in calls[:limit]
            ],
        }
    )


@skill_eval_router.get("/qualifying-evidence")
def list_qualifying_evidence(
    subject_kind: str | None = Query(None, alias="subjectKind"),
    subject_aggregate_id: UUID | None = Query(None, alias="subjectAggregateId"),
    subject_version_id: UUID | None = Query(None, alias="subjectVersionId"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _ = principal
    repo = EvaluationRepository(db)
    try:
        rows = repo.list_qualifying_runs_for_subject(
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
            limit=limit,
        )
    except EvaluationRepositoryError as exc:
        raise _map_repo_error(exc) from exc
    items = [
        _dto(
            QualifyingEvidenceSummary(
                evalRunId=r.id,
                mode=r.mode,
                status=r.status,
                gateEligible=bool(r.gate_eligible),
                evidenceProvenance=str(r.evidence_provenance),
                subjectKind=r.subject_kind,
                subjectVersionId=r.subject_version_id,
                providerFixtureRevision=r.provider_fixture_revision,
                providerFixtureDigest=r.provider_fixture_digest,
                aggregateMetrics=_redact_event_payload(dict(r.aggregate_metrics or {})),
            )
        )
        for r in rows
    ]
    return ApiResponse.ok({"items": items, "total": len(items)})


# ---------------------------------------------------------------------------
# Publish gates
# ---------------------------------------------------------------------------


@skill_eval_router.post("/gates")
def create_publish_gate(
    body: CreateGateBody,
    db: Session = Depends(get_db),
    principal: OperatorPrincipal = Depends(get_trusted_operator_principal),
) -> ApiResponse:
    _require_operator(principal)
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
