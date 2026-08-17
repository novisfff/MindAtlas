"""Frozen Main-Agent runtime contracts (Plan 2 Task 2).

Stable closure/readiness/activation request shapes plus digest validators and
repository DTO helpers. Seed/bootstrap/activation services land in later tasks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import Field, ValidationInfo, field_validator

from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.common.schemas import CamelModel

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Fixed UUIDv5 namespace for Main-Agent rollout revision identity.
ASSISTANT_ROLLOUT_NAMESPACE = UUID("a11e57a0-6a1a-4a11-9a9e-000000000002")

RUNTIME_READINESS_REASON_CODES: tuple[str, ...] = (
    "system_not_initialized",
    "operator_missing",
    "operator_auth_unavailable",
    "system_seed_invalid",
    "profile_unpublished",
    "model_unbound",
    "rollout_inactive",
    "runtime_closure_drift",
    "pre_ga_launch_unapproved",
    "worker_unavailable",
    "schema_incompatible",
    "new_runs_disabled",
)

ROLLOUT_EVENT_ACTIONS: tuple[str, ...] = (
    "prepared",
    "activated",
    "superseded",
    "new_runs_enabled",
    "new_runs_disabled",
)

CONTROL_KEY_MAIN_AGENT = "main_agent"


def require_sha256(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lowercase hex characters")
    return value


def rollout_revision_id_for_request(request_id: UUID) -> UUID:
    return uuid5(ASSISTANT_ROLLOUT_NAMESPACE, f"revision:{request_id}")


class RuntimeControlConflict(RuntimeError):
    """Raised when durable control CAS expected revision does not match."""

    code = "assistant_rollout_control_conflict"

    def __init__(self, message: str = "assistant rollout control conflict") -> None:
        super().__init__(message)


class RuntimeRequestReuseConflict(RuntimeError):
    """Raised when the same request_id is reused with a different digest."""

    code = "assistant_request_reuse_conflict"

    def __init__(self, message: str = "assistant request reuse conflict") -> None:
        super().__init__(message)


class RolloutNotPrepared(RuntimeError):
    """Raised when activation targets an unknown or unprepared revision."""

    code = "assistant_rollout_not_prepared"

    def __init__(self, message: str = "assistant rollout not prepared") -> None:
        super().__init__(message)


class RuntimeActivationRejected(RuntimeError):
    """Raised when activation revalidation rejects the candidate under locks."""

    code = "assistant_rollout_activation_rejected"

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class RuntimeGateEvidenceMissing(RuntimeError):
    """Raised when prepare/activate cannot find current gate or bootstrap evidence."""

    code = "assistant_gate_evidence_missing"

    def __init__(self, reason_code: str = "gate_evidence_missing") -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def digest_prepare_request(request: "PrepareRolloutRequest") -> str:
    return sha256_canonical_json(
        {
            "action": "prepared",
            "profileVersionId": str(request.profile_version_id),
            "modelId": str(request.model_id),
            "requestId": str(request.request_id),
            "reason": request.reason,
        }
    )


def digest_activation_request(
    revision_id: UUID, request: "ActivateRolloutRequest"
) -> str:
    return sha256_canonical_json(
        {
            "action": "activated",
            "revisionId": str(revision_id),
            "expectedControlRevision": int(request.expected_control_revision),
            "requestId": str(request.request_id),
            "reason": request.reason,
        }
    )


def digest_new_runs_request(request: "SetNewRunsEnabledRequest") -> str:
    action = "new_runs_enabled" if request.enabled else "new_runs_disabled"
    return sha256_canonical_json(
        {
            "action": action,
            "enabled": bool(request.enabled),
            "expectedControlRevision": int(request.expected_control_revision),
            "requestId": str(request.request_id),
            "reason": request.reason,
        }
    )


class AssistantRuntimeSubject(FrozenContract):
    """Non-circular runtime subject used to derive revision digests.

    Deliberately excludes rollout ID/digest and closure digest so revision and
    closure digests can be computed without circular input.
    """

    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    # Use plain dict[str, Any] — recursive JsonValue aliases trip Pydantic schema gen.
    package_closure: tuple[dict[str, Any], ...]
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int = Field(gt=0)

    @field_validator(
        "profile_content_digest",
        "model_identity_digest",
        "package_closure_digest",
        "capability_closure_digest",
        "seed_manifest_digest",
        "capability_feature_digest",
        "create_entry_contract_digest",
        "write_policy_digest",
        "write_cohort_digest",
    )
    @classmethod
    def validate_digest(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field_name=info.field_name)


class AssistantRuntimeClosure(FrozenContract):
    schema_version: Literal[1] = 1
    rollout_revision_id: UUID
    rollout_revision_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int = Field(gt=0)
    closure_digest: str

    @field_validator(
        "rollout_revision_digest",
        "profile_content_digest",
        "model_identity_digest",
        "package_closure_digest",
        "capability_closure_digest",
        "seed_manifest_digest",
        "capability_feature_digest",
        "create_entry_contract_digest",
        "write_policy_digest",
        "write_cohort_digest",
        "closure_digest",
    )
    @classmethod
    def validate_digest(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field_name=info.field_name)


class AssistantReadinessSnapshot(FrozenContract):
    ready: bool
    reason_codes: tuple[str, ...]
    active_rollout_revision_id: UUID | None
    profile_version_id: UUID | None
    model_id: UUID | None
    compatible_worker_ids: tuple[str, ...]
    build_revision: str


class ActivateRolloutRequest(CamelModel):
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class PrepareRolloutRequest(CamelModel):
    profile_version_id: UUID
    model_id: UUID
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class SetNewRunsEnabledRequest(CamelModel):
    enabled: bool
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class PreparedRolloutResult(FrozenContract):
    rollout_revision_id: UUID
    revision_label: str
    revision_digest: str
    control_revision: int
    active_rollout_revision_id: UUID | None
    new_runs_enabled: bool

    @classmethod
    def from_rows(cls, revision: Any, control: Any) -> "PreparedRolloutResult":
        return cls(
            rollout_revision_id=revision.id,
            revision_label=str(revision.revision_label),
            revision_digest=str(revision.revision_digest),
            control_revision=int(control.state_revision),
            active_rollout_revision_id=control.active_rollout_revision_id,
            new_runs_enabled=bool(control.new_runs_enabled),
        )


class ActivatedRolloutResult(FrozenContract):
    active_rollout_revision_id: UUID
    revision_label: str
    revision_digest: str
    control_revision: int
    new_runs_enabled: bool

    @classmethod
    def from_rows(cls, control: Any, revision: Any) -> "ActivatedRolloutResult":
        return cls(
            active_rollout_revision_id=revision.id,
            revision_label=str(revision.revision_label),
            revision_digest=str(revision.revision_digest),
            control_revision=int(control.state_revision),
            new_runs_enabled=bool(control.new_runs_enabled),
        )


class RuntimeControlResult(FrozenContract):
    active_rollout_revision_id: UUID | None
    control_revision: int
    new_runs_enabled: bool

    @classmethod
    def from_row(cls, control: Any) -> "RuntimeControlResult":
        return cls(
            active_rollout_revision_id=control.active_rollout_revision_id,
            control_revision=int(control.state_revision),
            new_runs_enabled=bool(control.new_runs_enabled),
        )


@dataclass(frozen=True)
class NewChatAdmission:
    closure: AssistantRuntimeClosure
    compatible_worker_ids: tuple[str, ...]
    deadline_at: datetime | None


def subject_to_persistent_fields(subject: AssistantRuntimeSubject) -> dict[str, Any]:
    """Map a Subject onto ORM revision column names (excluding identity fields)."""
    return {
        "profile_version_id": subject.profile_version_id,
        "profile_content_digest": subject.profile_content_digest,
        "model_id": subject.model_id,
        "model_identity_digest": subject.model_identity_digest,
        "package_closure_json": [
            dict(item) for item in subject.package_closure
        ],
        "package_closure_digest": subject.package_closure_digest,
        "capability_closure_digest": subject.capability_closure_digest,
        "seed_manifest_digest": subject.seed_manifest_digest,
        "build_revision": subject.build_revision,
        "runtime_contract_version": subject.runtime_contract_version,
        "checkpoint_codec_version": subject.checkpoint_codec_version,
        "capability_feature_digest": subject.capability_feature_digest,
        "required_create_entry_contract_digest": subject.create_entry_contract_digest,
        "required_write_policy_digest": subject.write_policy_digest,
        "required_write_cohort_digest": subject.write_cohort_digest,
        "required_reconciliation_contract_version": (
            subject.reconciliation_contract_version
        ),
    }


@dataclass(frozen=True, slots=True)
class PreparedRolloutRevision:
    """In-memory prepared revision payload ready for repository insert."""

    id: UUID
    revision_label: str
    revision_digest: str
    prepared_by_operator_id: UUID | None
    prepared_reason: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_json: list[dict[str, Any]]
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    required_create_entry_contract_digest: str
    required_write_policy_digest: str
    required_write_cohort_digest: str
    required_reconciliation_contract_version: int

    @classmethod
    def from_subject(
        cls,
        *,
        subject: AssistantRuntimeSubject,
        revision_id: UUID,
        prepared_by_operator_id: UUID | None,
        prepared_reason: str,
    ) -> "PreparedRolloutRevision":
        identity = {
            "schemaVersion": 1,
            "rolloutRevisionId": str(revision_id),
            **subject.model_dump(mode="json", by_alias=True),
        }
        revision_digest = sha256_canonical_json(identity)
        return cls(
            id=revision_id,
            revision_label=f"main-agent-{revision_digest[:24]}",
            revision_digest=revision_digest,
            prepared_by_operator_id=prepared_by_operator_id,
            prepared_reason=prepared_reason,
            **subject_to_persistent_fields(subject),
        )


@dataclass(frozen=True, slots=True)
class NewRolloutEvent:
    """In-memory append-only control event payload."""

    action: str
    from_rollout_revision_id: UUID | None
    to_rollout_revision_id: UUID | None
    control_revision: int
    request_id: UUID
    request_digest: str
    operator_id: UUID | None
    operator_session_id: UUID | None
    reason: str
    evidence_digest: str
    result_json: dict[str, Any]

    def __post_init__(self) -> None:
        if self.action not in ROLLOUT_EVENT_ACTIONS:
            raise ValueError(f"invalid rollout event action: {self.action!r}")
        object.__setattr__(
            self,
            "request_digest",
            require_sha256(self.request_digest, field_name="request_digest"),
        )
        object.__setattr__(
            self,
            "evidence_digest",
            require_sha256(self.evidence_digest, field_name="evidence_digest"),
        )
        if not isinstance(self.result_json, dict):
            raise ValueError("result_json must be an object")
        reason = str(self.reason or "").strip()
        if not reason or len(reason) > 500:
            raise ValueError("reason must be 1..500 characters")
        object.__setattr__(self, "reason", reason)

    @classmethod
    def prepared_from_bootstrap(
        cls,
        *,
        rollout: Any,
        control_revision: int,
        request_id: UUID,
        seed_manifest_digest: str,
        operator_id: UUID | None = None,
        bootstrap_evidence: dict[str, Any] | None = None,
    ) -> "NewRolloutEvent":
        request_digest = sha256_canonical_json(
            {
                "action": "prepared",
                "requestId": str(request_id),
                "rolloutRevisionId": str(rollout.id),
                "seedManifestDigest": seed_manifest_digest,
                "reason": "system_bootstrap",
            }
        )
        result_json: dict[str, Any] = {
            "rolloutRevisionId": str(rollout.id),
            "revisionDigest": str(rollout.revision_digest),
            "controlRevision": int(control_revision),
        }
        if bootstrap_evidence is not None:
            if not isinstance(bootstrap_evidence, dict):
                raise ValueError("bootstrap_evidence must be an object")
            result_json["bootstrapEvidence"] = dict(bootstrap_evidence)
        return cls(
            action="prepared",
            from_rollout_revision_id=None,
            to_rollout_revision_id=rollout.id,
            control_revision=int(control_revision),
            request_id=request_id,
            request_digest=request_digest,
            operator_id=operator_id,
            operator_session_id=None,
            reason="system_bootstrap",
            evidence_digest=require_sha256(
                seed_manifest_digest, field_name="seed_manifest_digest"
            ),
            result_json=result_json,
        )

    @classmethod
    def for_new_runs_switch(
        cls,
        *,
        previous: Any,
        updated: Any,
        request: "SetNewRunsEnabledRequest",
        request_digest: str,
        principal: Any,
        result: "RuntimeControlResult",
    ) -> "NewRolloutEvent":
        action = "new_runs_enabled" if request.enabled else "new_runs_disabled"
        evidence_digest = sha256_canonical_json(
            {
                "action": action,
                "controlRevision": int(updated.state_revision),
                "enabled": bool(request.enabled),
                "activeRolloutRevisionId": (
                    str(updated.active_rollout_revision_id)
                    if updated.active_rollout_revision_id is not None
                    else None
                ),
            }
        )
        return cls(
            action=action,
            from_rollout_revision_id=previous.active_rollout_revision_id,
            to_rollout_revision_id=updated.active_rollout_revision_id,
            control_revision=int(updated.state_revision),
            request_id=request.request_id,
            request_digest=request_digest,
            operator_id=getattr(principal, "operator_id", None),
            operator_session_id=getattr(principal, "session_id", None),
            reason=request.reason,
            evidence_digest=evidence_digest,
            result_json=result.model_dump(mode="json", by_alias=True),
        )
