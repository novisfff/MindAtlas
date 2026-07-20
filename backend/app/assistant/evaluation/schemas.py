"""HTTP-facing schemas for skill evaluation workbench (Plan 09 Task 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.assistant.evaluation.contracts import (
    CreatePublishGateRequest,
    EvalRunMode,
    EvalSubjectKind,
    PublishGateAction,
    PublishGateDecision,
    PublishGateSubject,
)
from app.common.schemas import CamelModel


class CreateEvalRunBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str | None = Field(default=None, alias="requestId", max_length=128)
    subject_kind: EvalSubjectKind = Field(alias="subjectKind")
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    subject_content_digest: str = Field(alias="subjectContentDigest", min_length=64, max_length=64)
    subject_binding_digest: str = Field(alias="subjectBindingDigest", min_length=64, max_length=64)
    dataset_version_ids: list[UUID] = Field(default_factory=list, alias="datasetVersionIds")
    threshold_policy_version: str = Field(default="plan09-policy-v1", alias="thresholdPolicyVersion")
    mode: EvalRunMode = "interactive_scripted"
    isolation_namespace_id: UUID | None = Field(default=None, alias="isolationNamespaceId")
    runtime_contract_version: int = Field(default=1, alias="runtimeContractVersion", ge=1)
    required_build_revision: str = Field(default="development", alias="requiredBuildRevision")
    isolation_digest: str = Field(
        default="0" * 64,
        alias="isolationDigest",
        min_length=64,
        max_length=64,
    )
    actor_principal: str | None = Field(default=None, alias="actorPrincipal")


class CancelEvalRunBody(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str | None = Field(default=None, alias="requestId", max_length=128)
    expected_state_revision: int | None = Field(default=None, alias="expectedStateRevision", ge=0)


class CreateGateBody(CamelModel):
    """Client may only supply subject/evidence refs + optional non-safety waivers."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: UUID = Field(alias="requestId")
    subject: PublishGateSubject
    qualifying_eval_run_ids: list[UUID] = Field(alias="qualifyingEvalRunIds", min_length=1)
    requested_non_safety_waiver_codes: list[str] = Field(
        default_factory=list,
        alias="requestedNonSafetyWaiverCodes",
    )
    waiver_reason: str | None = Field(default=None, alias="waiverReason", max_length=2000)

    def to_service_request(self) -> CreatePublishGateRequest:
        return CreatePublishGateRequest(
            request_id=self.request_id,
            subject=self.subject,
            qualifying_eval_run_ids=tuple(self.qualifying_eval_run_ids),
            requested_non_safety_waiver_codes=tuple(self.requested_non_safety_waiver_codes),
            waiver_reason=self.waiver_reason,
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
    draft_version_id: UUID | None = Field(default=None, alias="draftVersionId")
    published_version_id: UUID | None = Field(default=None, alias="publishedVersionId")


__all__ = [
    "CancelEvalRunBody",
    "CreateEvalRunBody",
    "CreateGateBody",
    "DatasetSummary",
    "EvalEventSummary",
    "EvalRunSummary",
    "PublishGateAction",
    "PublishGateSummary",
]
