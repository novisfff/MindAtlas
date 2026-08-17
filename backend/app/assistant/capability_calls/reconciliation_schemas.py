"""Safe HTTP contracts for CapabilityCall reconciliation."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.common.schemas import CamelModel


class ReconcileCapabilityCallRequest(CamelModel):
    """Bounded client input; all identity/evidence claims remain server-owned."""

    model_config = ConfigDict(extra="forbid")

    expected_call_revision: int = Field(..., ge=0)
    expected_run_revision: int = Field(..., ge=0)
    decision: Literal["mark_succeeded", "mark_failed", "mark_compensated"]
    evidence_artifact_ids: list[UUID] = Field(..., min_length=1, max_length=8)
    request_id: UUID
    reason: str = Field(..., min_length=1, max_length=500)


class IssueFailureEvidenceRequest(CamelModel):
    """Bounded operator input for server-issued failure evidence."""

    model_config = ConfigDict(extra="forbid")

    expected_call_revision: int = Field(..., ge=0)
    expected_run_revision: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=500)


class IssueSuccessEvidenceRequest(CamelModel):
    """Bounded operator input for server-issued success evidence."""

    model_config = ConfigDict(extra="forbid")

    expected_call_revision: int = Field(..., ge=0)
    expected_run_revision: int = Field(..., ge=0)
    result_artifact_id: UUID


class ReconciliationCallSummary(CamelModel):
    """Viewer-safe unresolved Call projection with no request/result body."""

    call_id: UUID
    run_id: UUID
    status: str
    state_revision: int
    run_revision: int
    failure_code: str | None = None
    execution_mode: str
    side_effect_started_at: str | None = None
    attempt_count: int
    evidence_required: bool = True
    evidence_artifact_ids: list[UUID] = Field(default_factory=list, max_length=8)


__all__ = [
    "IssueFailureEvidenceRequest",
    "IssueSuccessEvidenceRequest",
    "ReconcileCapabilityCallRequest",
    "ReconciliationCallSummary",
]
