"""HTTP-facing schemas for skill evaluation workbench (Plan 09 Task 8)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.assistant.evaluation.contracts import (
    CreatePublishGateRequest,
    EvalRunMode,
    EvalSubjectKind,
    PublishGateAction,
    PublishGateDecision,
)
from app.common.schemas import CamelModel


class CreateEvalRunBody(CamelModel):
    """Client admission body — no digests, decisions, or actor overrides.

    Server resolves subject content/binding digests, policy/build/runtime pins,
    isolation digests, and provider fixture / model-probe evidence.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    subject_kind: EvalSubjectKind = Field(alias="subjectKind")
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    prompt: str = Field(min_length=1, max_length=32_000)
    locale: str = Field(min_length=2, max_length=32)
    profile_version_id: UUID = Field(alias="profileVersionId")
    mode: EvalRunMode
    dataset_version_ids: list[UUID] = Field(
        default_factory=list, alias="datasetVersionIds"
    )
    provider_fixture_revision: str | None = Field(
        default=None, alias="providerFixtureRevision", max_length=160
    )
    live_model_id: UUID | None = Field(default=None, alias="liveModelId")

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "CreateEvalRunBody":
        if self.mode == "dataset_scripted":
            if not self.dataset_version_ids or not self.provider_fixture_revision:
                raise ValueError(
                    "dataset_scripted requires dataset and fixture revisions"
                )
            if self.live_model_id is not None:
                raise ValueError("dataset_scripted forbids liveModelId")
        if self.mode == "dataset_live":
            if not self.dataset_version_ids or self.live_model_id is None:
                raise ValueError(
                    "dataset_live requires dataset versions and liveModelId"
                )
            if self.provider_fixture_revision is not None:
                raise ValueError("dataset_live forbids providerFixtureRevision")
        if self.mode == "interactive_scripted":
            if self.live_model_id is not None:
                raise ValueError("interactive_scripted forbids liveModelId")
        return self


class CancelEvalRunBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str | None = Field(
        default=None, alias="requestId", max_length=128
    )
    expected_state_revision: int | None = Field(
        default=None, alias="expectedStateRevision", ge=0
    )


class CreateGateBody(CamelModel):
    """Client may only supply action, subject identity, evidence refs, and waivers.

    ``extra="forbid"`` rejects client-authored subject closure digests,
    decisions, metrics, assertions, policy, threshold, or build fields.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    action: PublishGateAction
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    qualifying_eval_run_ids: list[UUID] = Field(
        alias="qualifyingEvalRunIds", min_length=1
    )
    requested_non_safety_waiver_codes: list[str] = Field(
        default_factory=list,
        alias="requestedNonSafetyWaiverCodes",
    )
    waiver_reason: str | None = Field(default=None, alias="waiverReason", max_length=2000)

    def to_service_request(self) -> CreatePublishGateRequest:
        return CreatePublishGateRequest(
            request_id=self.request_id,
            action=self.action,
            subject_aggregate_id=self.subject_aggregate_id,
            subject_version_id=self.subject_version_id,
            qualifying_eval_run_ids=tuple(self.qualifying_eval_run_ids),
            requested_non_safety_waiver_codes=tuple(
                self.requested_non_safety_waiver_codes
            ),
            waiver_reason=self.waiver_reason,
        )


class CreateDatasetBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    stable_key: str = Field(alias="stableKey", min_length=1, max_length=128)
    display_name: str = Field(alias="displayName", min_length=1, max_length=256)
    description: str = Field(default="", max_length=2048)
    ownership: Literal["system", "custom"] = "custom"


class PutDatasetDraftBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    expected_draft_revision: int = Field(alias="expectedDraftRevision", ge=0)
    cases_snapshot: list[dict[str, Any]] = Field(
        default_factory=list, alias="casesSnapshot"
    )


class PublishDatasetBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    # Accept both expectedRevision (brief) and expectedAggregateRevision.
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    expected_draft_revision: int = Field(
        default=0, alias="expectedDraftRevision", ge=0
    )
    version_name: str = Field(
        default="published", alias="versionName", min_length=1, max_length=128
    )
    source_fixture_revision: str | None = Field(
        default=None, alias="sourceFixtureRevision", max_length=160
    )


class EvalRunSummary(CamelModel):
    id: UUID
    subject_kind: EvalSubjectKind = Field(alias="subjectKind")
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    mode: EvalRunMode
    status: str
    state_revision: int = Field(alias="stateRevision")
    last_event_seq: int = Field(default=0, alias="lastEventSeq")
    failure_code: str | None = Field(default=None, alias="failureCode")
    gate_eligible: bool = Field(default=False, alias="gateEligible")
    evidence_provenance: str | None = Field(default=None, alias="evidenceProvenance")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    ended_at: datetime | None = Field(default=None, alias="endedAt")


class EvalEventSummary(CamelModel):
    sequence: int
    event_type: str = Field(alias="eventType")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = Field(default=None, alias="createdAt")


class PublishGateSummary(CamelModel):
    id: UUID
    decision: PublishGateDecision
    subject_kind: str = Field(alias="subjectKind")
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    waiver_codes: list[str] = Field(default_factory=list, alias="waiverCodes")
    request_id: str | None = Field(default=None, alias="requestId")
    created_at: datetime | None = Field(default=None, alias="createdAt")


class DatasetSummary(CamelModel):
    id: UUID
    stable_key: str = Field(alias="stableKey")
    display_name: str = Field(alias="displayName")
    ownership: str = "custom"
    aggregate_revision: int = Field(default=0, alias="aggregateRevision")
    draft_version_id: UUID | None = Field(default=None, alias="draftVersionId")
    published_version_id: UUID | None = Field(default=None, alias="publishedVersionId")


class DatasetDraftSummary(CamelModel):
    id: UUID
    dataset_id: UUID = Field(alias="datasetId")
    draft_revision: int = Field(alias="draftRevision")
    schema_version: int = Field(alias="schemaVersion")
    cases_snapshot: list[dict[str, Any]] = Field(
        default_factory=list, alias="casesSnapshot"
    )
    draft_digest: str = Field(alias="draftDigest")
    base_version_id: UUID | None = Field(default=None, alias="baseVersionId")
    updated_by: str | None = Field(default=None, alias="updatedBy")


class DatasetVersionSummary(CamelModel):
    id: UUID
    dataset_id: UUID = Field(alias="datasetId")
    sequence: int
    version_name: str = Field(alias="versionName")
    schema_version: int = Field(alias="schemaVersion")
    content_digest: str = Field(alias="contentDigest")
    source_fixture_revision: str | None = Field(
        default=None, alias="sourceFixtureRevision"
    )
    case_count: int = Field(default=0, alias="caseCount")
    created_by: str | None = Field(default=None, alias="createdBy")
    created_at: datetime | None = Field(default=None, alias="createdAt")


class CaseResultSummary(CamelModel):
    id: UUID
    eval_run_id: UUID = Field(alias="evalRunId")
    eval_case_id: UUID = Field(alias="evalCaseId")
    result_state: str = Field(alias="resultState")
    assertion_details: dict[str, Any] = Field(
        default_factory=dict, alias="assertionDetails"
    )
    actual_active_skills: list[str] = Field(
        default_factory=list, alias="actualActiveSkills"
    )
    stop_reason: str | None = Field(default=None, alias="stopReason")
    output_artifact_ids: list[UUID] = Field(
        default_factory=list, alias="outputArtifactIds"
    )
    evidence_artifact_ids: list[UUID] = Field(
        default_factory=list, alias="evidenceArtifactIds"
    )
    rounds: int | None = None
    calls: int | None = None
    tokens: int | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    safe_error: str | None = Field(default=None, alias="safeError")
    result_digest: str = Field(alias="resultDigest")
    created_at: datetime | None = Field(default=None, alias="createdAt")


class ArtifactSummary(CamelModel):
    id: UUID
    eval_run_id: UUID = Field(alias="evalRunId")
    kind: str
    media_type: str = Field(alias="mediaType")
    storage_kind: str = Field(alias="storageKind")
    content_digest: str | None = Field(default=None, alias="contentDigest")
    byte_size: int | None = Field(default=None, alias="byteSize")
    label: str | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")


class QualifyingEvidenceSummary(CamelModel):
    eval_run_id: UUID = Field(alias="evalRunId")
    mode: str
    status: str
    gate_eligible: bool = Field(alias="gateEligible")
    evidence_provenance: str = Field(alias="evidenceProvenance")
    subject_kind: str = Field(alias="subjectKind")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    provider_fixture_revision: str | None = Field(
        default=None, alias="providerFixtureRevision"
    )
    provider_fixture_digest: str | None = Field(
        default=None, alias="providerFixtureDigest"
    )
    aggregate_metrics: dict[str, Any] = Field(
        default_factory=dict, alias="aggregateMetrics"
    )


__all__ = [
    "ArtifactSummary",
    "CancelEvalRunBody",
    "CaseResultSummary",
    "CreateDatasetBody",
    "CreateEvalRunBody",
    "CreateGateBody",
    "DatasetDraftSummary",
    "DatasetSummary",
    "DatasetVersionSummary",
    "EvalEventSummary",
    "EvalRunSummary",
    "PublishDatasetBody",
    "PublishGateAction",
    "PublishGateSummary",
    "PutDatasetDraftBody",
    "QualifyingEvidenceSummary",
]
