"""Typed launch-subject and Operator request contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from pydantic import ConfigDict

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.common.schemas import CamelModel
from app.release.contracts import ContentAddressedEvidenceRef


class PreGaLaunchSubjectV1(FrozenContract):
    schema_version: Literal[1] = 1
    qualification_target_digest: str
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_runtime_identity_digest: str
    deployment_class: Literal["production"]
    operator_auth_contract_version: str
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int = Field(gt=0)
    worker_checkpoint_codec_version: int = Field(gt=0)
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int = Field(gt=0)
    dependency_lock_set_digest: str
    automated_evidence_manifest_digest: str
    rehearsal_evidence_manifest_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int = Field(gt=0)
    runner_identity_digest: str
    evidence_trust_set_digest: str
    subject_digest: str

    @classmethod
    def build(cls, **values: Any) -> "PreGaLaunchSubjectV1":
        values = dict(values)
        values["subject_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["subject_digest"] = sha256_canonical_json(
            {
                "domain": "mindatlas:pre-ga-launch-subject:v1",
                **draft.model_dump(mode="json", by_alias=True, exclude={"subject_digest"}),
            }
        )
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_subject_digest(self) -> "PreGaLaunchSubjectV1":
        payload = self.model_dump(mode="json", by_alias=True, exclude={"subject_digest"})
        expected = sha256_canonical_json(
            {"domain": "mindatlas:pre-ga-launch-subject:v1", **payload}
        )
        if self.subject_digest != expected:
            raise ValueError("subject_digest does not match subject")
        return self


class LaunchOperationalSnapshotV1(FrozenContract):
    schema_version: Literal[1] = 1
    unknown_capability_call_count: int = Field(ge=0)
    needs_reconciliation_count: int = Field(ge=0)
    active_run_count: int = Field(ge=0)
    observed_at: datetime
    snapshot_digest: str

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @classmethod
    def build(cls, **values: Any) -> "LaunchOperationalSnapshotV1":
        values = dict(values)
        values["snapshot_digest"] = "0" * 64
        draft = cls.model_construct(**values)
        values["snapshot_digest"] = sha256_canonical_json(
            {
                "domain": "mindatlas:pre-ga-launch-operational-snapshot:v1",
                **draft.model_dump(mode="json", by_alias=True, exclude={"snapshot_digest"}),
            }
        )
        return cls.model_validate(values)

    @model_validator(mode="after")
    def validate_digest(self) -> "LaunchOperationalSnapshotV1":
        expected = sha256_canonical_json(
            {
                "domain": "mindatlas:pre-ga-launch-operational-snapshot:v1",
                **self.model_dump(mode="json", by_alias=True, exclude={"snapshot_digest"}),
            }
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot_digest does not match snapshot")
        return self


class CreatePreGaLaunchCandidateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    automated_evidence_ref: ContentAddressedEvidenceRef
    rehearsal_evidence_ref: ContentAddressedEvidenceRef
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class ConsumePreGaLaunchCandidateRequest(CamelModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


__all__ = [
    "ConsumePreGaLaunchCandidateRequest",
    "CreatePreGaLaunchCandidateRequest",
    "LaunchOperationalSnapshotV1",
    "PreGaLaunchSubjectV1",
]
