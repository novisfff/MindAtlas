"""Deterministic runtime rollout assignment + pre-insert fallback (Plan 10 Task 6).

Implements durable revision prepare/activate wrappers, cohort assignment, and the
pre-insert-only fallback decision type. Production percentage canary ladders are
intentionally out of scope; local/dev may activate a ``main`` revision at 100%.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.contracts import RolloutDecision, RuntimeKind
from app.assistant.migration.models import (
    AssistantRuntimeRolloutAssignment,
    AssistantRuntimeRolloutControl,
    AssistantRuntimeRolloutRevision,
)
from app.assistant.migration.repository import (
    CODE_INVALID_INPUT,
    CODE_NOT_FOUND,
    CODE_PRECONDITION,
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)

logger = logging.getLogger(__name__)

SelectionReason = Literal["assigned", "preinsert_fallback"]
AssignmentReason = Literal["hash", "staff", "explicit_override", "rollback"]

# Environment key holding the private cohort salt. Only the fingerprint is stored
# on the durable revision; the raw salt is never persisted.
COHORT_SALT_ENV = "ASSISTANT_RUNTIME_COHORT_SALT"


class RolloutError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AssignmentComputeResult:
    """Stable cohort assignment for a conversation under one rollout revision."""

    assignment: AssistantRuntimeRolloutAssignment
    bucket: int
    assigned_runtime_kind: RuntimeKind
    assignment_reason: AssignmentReason
    cohort_key_digest: str
    created: bool


@dataclass(frozen=True, slots=True)
class PreInsertFallbackDecision:
    """Decision to fall back to Legacy before any Chat Run insert.

    After a Chat Run row exists, this path is permanently closed.
    """

    allowed: bool
    candidate_runtime_kind: RuntimeKind
    selected_runtime_kind: RuntimeKind
    selection_reason: SelectionReason
    admission_failure_code: str | None
    admission_failure_digest: str | None
    request_id: str | None
    rollout_revision_id: UUID | None
    assignment_id: UUID | None
    resulting_legacy_run_id: UUID | None = None
    fallback_event_id: UUID | None = None

    def to_rollout_decision(self) -> RolloutDecision:
        if not self.allowed or self.rollout_revision_id is None:
            raise RolloutError(
                CODE_PRECONDITION,
                "fallback decision is not allowed / incomplete",
            )
        if self.selection_reason != "preinsert_fallback":
            raise RolloutError(CODE_INVALID_INPUT, "not a preinsert_fallback decision")
        return RolloutDecision(
            rollout_revision_id=str(self.rollout_revision_id),
            assignment_id=str(self.assignment_id) if self.assignment_id else None,
            assigned_runtime_kind="main_agent",
            selected_runtime_kind="legacy",
            write_mode="off",
            bucket=0,
            assignment_reason="hash",
            selection_reason="preinsert_fallback",
            fallback_event_id=(
                str(self.fallback_event_id) if self.fallback_event_id else None
            ),
            admission_failure_digest=self.admission_failure_digest,
        )


def cohort_salt_fingerprint(salt: str | None = None) -> str:
    """Fingerprint of the cohort salt (sha256 of salt bytes). Never stores salt."""
    raw = salt if salt is not None else os.environ.get(COHORT_SALT_ENV, "")
    text = str(raw or "").strip()
    if not text:
        # Deterministic local/dev default fingerprint (not a secret salt value).
        text = "mindatlas-dev-cohort-salt-v1"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_cohort_salt() -> str:
    """Return the private salt used for bucket hashing (env or local/dev default)."""
    text = str(os.environ.get(COHORT_SALT_ENV, "") or "").strip()
    return text or "mindatlas-dev-cohort-salt-v1"


# ---------------------------------------------------------------------------
# Prepare / activate / rollback wrappers
# ---------------------------------------------------------------------------


def prepare_revision(
    session: Session,
    *,
    revision_label: str,
    runtime_mode: str = "legacy",
    eligible_closure_digest: str,
    build_revision: str,
    cohort_salt: str | None = None,
    shadow_eligible_scope: str = "none",
    shadow_percent: int = 0,
    read_canary_percent: int = 0,
    write_mode: str = "off",
    write_percent: int = 0,
    config_origin: str = "native",
    config_json: Mapping[str, Any] | None = None,
    actor_principal: str | None = None,
    reason: str | None = None,
) -> AssistantRuntimeRolloutRevision:
    """Prepare an immutable rollout revision (idempotent on label+digest)."""

    repo = RuntimeMigrationRepository(session)
    fingerprint = cohort_salt_fingerprint(cohort_salt)
    # Local/dev convenience: main mode at 100% is allowed without canary ladder.
    if runtime_mode == "main_agent" and int(read_canary_percent) == 0:
        read_canary_percent = 100
    try:
        return repo.prepare_rollout_revision(
            revision_label=revision_label,
            runtime_mode=runtime_mode,
            shadow_eligible_scope=shadow_eligible_scope,
            shadow_percent=int(shadow_percent),
            read_canary_percent=int(read_canary_percent),
            write_mode=write_mode,
            write_percent=int(write_percent),
            eligible_closure_digest=eligible_closure_digest,
            config_origin=config_origin,
            build_revision=build_revision,
            cohort_salt_fingerprint=fingerprint,
            config_json=config_json,
            actor_principal=actor_principal,
            reason=reason,
        )
    except RuntimeMigrationRepositoryError as exc:
        raise RolloutError(exc.code, exc.message) from exc


def activate_revision(
    session: Session,
    *,
    rollout_revision_id: UUID,
    expected_control_revision: int | None = None,
    actor_principal: str | None = None,
    reason: str | None = None,
) -> AssistantRuntimeRolloutControl:
    """Activate a prepared revision, superseding the prior active pointer."""

    repo = RuntimeMigrationRepository(session)
    if expected_control_revision is None:
        control = repo.ensure_rollout_control()
        expected_control_revision = int(control.state_revision)
    try:
        return repo.activate_rollout_revision(
            rollout_revision_id=rollout_revision_id,
            expected_control_revision=int(expected_control_revision),
            actor_principal=actor_principal,
            reason=reason,
        )
    except RuntimeMigrationRepositoryError as exc:
        raise RolloutError(exc.code, exc.message) from exc


def rollback_to_legacy(
    session: Session,
    *,
    revision_label: str,
    eligible_closure_digest: str,
    build_revision: str,
    actor_principal: str | None = None,
    reason: str | None = None,
    expected_control_revision: int | None = None,
) -> tuple[AssistantRuntimeRolloutRevision, AssistantRuntimeRolloutControl]:
    """Prepare + activate a new legacy-selecting revision for future Runs."""

    rev = prepare_revision(
        session,
        revision_label=revision_label,
        runtime_mode="legacy",
        eligible_closure_digest=eligible_closure_digest,
        build_revision=build_revision,
        read_canary_percent=0,
        actor_principal=actor_principal,
        reason=reason or "rollback_to_legacy",
    )
    control = activate_revision(
        session,
        rollout_revision_id=rev.id,
        expected_control_revision=expected_control_revision,
        actor_principal=actor_principal,
        reason=reason or "rollback_to_legacy",
    )
    return rev, control


def get_revision_by_label(
    session: Session, revision_label: str
) -> AssistantRuntimeRolloutRevision | None:
    label = str(revision_label or "").strip()
    if not label:
        return None
    return session.execute(
        select(AssistantRuntimeRolloutRevision).where(
            AssistantRuntimeRolloutRevision.revision_label == label
        )
    ).scalar_one_or_none()


def validate_runtime_rollout_startup(
    session: Session,
    *,
    settings: Any | None = None,
) -> AssistantRuntimeRolloutRevision | None:
    """Require native environment config to match the durable active revision."""

    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    configured_mode = str(
        getattr(settings, "assistant_runtime_mode", "legacy") or "legacy"
    ).strip().lower()
    configured_label = str(
        getattr(settings, "assistant_runtime_rollout_revision", "") or ""
    ).strip()
    active = RuntimeMigrationRepository(session).get_active_rollout_revision()

    if active is None:
        if configured_mode == "legacy" and not configured_label:
            return None
        raise RolloutError(
            "runtime_rollout_revision_missing",
            "configured runtime requires an active durable rollout revision",
        )

    active_label = str(active.revision_label or "").strip()
    if not configured_label or configured_label != active_label:
        raise RolloutError(
            "runtime_rollout_revision_mismatch",
            "configured rollout revision does not match the durable active revision",
        )
    active_mode = str(active.runtime_mode or "legacy").strip().lower()
    if configured_mode != active_mode:
        raise RolloutError(
            "runtime_rollout_mode_mismatch",
            "configured runtime mode does not match the durable active revision",
        )
    return active


# ---------------------------------------------------------------------------
# Deterministic assignment
# ---------------------------------------------------------------------------


def compute_cohort_bucket(
    *,
    conversation_id: UUID,
    rollout_revision_id: UUID,
    salt: str | None = None,
) -> tuple[int, str]:
    """Return ``(bucket 0..99, cohort_key_digest)`` for a conversation+revision."""

    salt_value = salt if salt is not None else resolve_cohort_salt()
    material = sha256_canonical_json(
        {
            "conversationId": str(conversation_id),
            "rolloutRevisionId": str(rollout_revision_id),
            "salt": str(salt_value),
        }
    )
    # First 8 hex chars → int → bucket 0..99.
    bucket = int(material[:8], 16) % 100
    return bucket, material


def decide_assigned_runtime_kind(
    revision: AssistantRuntimeRolloutRevision,
    *,
    bucket: int,
) -> RuntimeKind:
    """Map revision config + bucket to assigned runtime kind.

    - runtime_mode=legacy → always legacy
    - runtime_mode=main_agent → main_agent when bucket < read_canary_percent
    """

    mode = str(revision.runtime_mode or "legacy").strip().lower()
    if mode != "main_agent":
        return "legacy"
    percent = int(revision.read_canary_percent or 0)
    if percent >= 100:
        return "main_agent"
    if percent <= 0:
        return "legacy"
    return "main_agent" if int(bucket) < percent else "legacy"


def ensure_assignment(
    session: Session,
    *,
    conversation_id: UUID,
    revision: AssistantRuntimeRolloutRevision | None = None,
    rollout_revision_id: UUID | None = None,
    assignment_reason: AssignmentReason = "hash",
    assigned_runtime_kind: RuntimeKind | None = None,
    principal_scope_digest: str | None = None,
    salt: str | None = None,
    staff_force_main: bool = False,
) -> AssignmentComputeResult:
    """Compute/insert the immutable assignment for scope+revision."""

    repo = RuntimeMigrationRepository(session)
    if revision is None:
        if rollout_revision_id is not None:
            revision = session.get(AssistantRuntimeRolloutRevision, rollout_revision_id)
        else:
            revision = repo.get_active_rollout_revision()
    if revision is None:
        raise RolloutError(CODE_NOT_FOUND, "no active/specified rollout revision")

    bucket, cohort_key_digest = compute_cohort_bucket(
        conversation_id=conversation_id,
        rollout_revision_id=revision.id,
        salt=salt,
    )
    reason: AssignmentReason = assignment_reason
    if staff_force_main:
        kind: RuntimeKind = "main_agent"
        reason = "staff"
    elif assigned_runtime_kind is not None:
        kind = assigned_runtime_kind
        if reason == "hash" and assigned_runtime_kind != decide_assigned_runtime_kind(
            revision, bucket=bucket
        ):
            reason = "explicit_override"
    else:
        kind = decide_assigned_runtime_kind(revision, bucket=bucket)

    write_mode = str(revision.write_mode or "off")
    # Write eligibility is independent: only apply when assigned main_agent.
    if kind != "main_agent":
        write_mode = "off"
    elif int(revision.write_percent or 0) < 100 and int(revision.write_percent or 0) > 0:
        # Keep write off unless write bucket also hits (independent of read).
        write_bucket = (bucket + 37) % 100  # independent offset
        if write_bucket >= int(revision.write_percent):
            write_mode = "off"

    existing = session.execute(
        select(AssistantRuntimeRolloutAssignment)
        .where(AssistantRuntimeRolloutAssignment.conversation_id == conversation_id)
        .where(AssistantRuntimeRolloutAssignment.rollout_revision_id == revision.id)
    ).scalar_one_or_none()
    created = existing is None
    try:
        row = repo.create_assignment(
            conversation_id=conversation_id,
            rollout_revision_id=revision.id,
            assigned_runtime_kind=kind,
            assignment_reason=reason,
            cohort_key_digest=cohort_key_digest,
            assigned_write_mode=write_mode if kind == "main_agent" else "off",
            principal_scope_digest=principal_scope_digest,
        )
    except RuntimeMigrationRepositoryError as exc:
        raise RolloutError(exc.code, exc.message) from exc

    return AssignmentComputeResult(
        assignment=row,
        bucket=bucket,
        assigned_runtime_kind=str(row.assigned_runtime_kind),  # type: ignore[arg-type]
        assignment_reason=str(row.assignment_reason),  # type: ignore[arg-type]
        cohort_key_digest=str(row.cohort_key_digest),
        created=created,
    )


def build_assigned_decision(
    *,
    revision: AssistantRuntimeRolloutRevision,
    assignment: AssistantRuntimeRolloutAssignment,
    bucket: int,
) -> RolloutDecision:
    return RolloutDecision(
        rollout_revision_id=str(revision.id),
        assignment_id=str(assignment.id),
        assigned_runtime_kind=str(assignment.assigned_runtime_kind),  # type: ignore[arg-type]
        selected_runtime_kind=str(assignment.assigned_runtime_kind),  # type: ignore[arg-type]
        write_mode=str(assignment.assigned_write_mode or "off"),
        bucket=int(bucket),
        assignment_reason=str(assignment.assignment_reason),  # type: ignore[arg-type]
        selection_reason="assigned",
        fallback_event_id=None,
        admission_failure_digest=None,
    )


# ---------------------------------------------------------------------------
# Pre-insert fallback
# ---------------------------------------------------------------------------


def evaluate_preinsert_fallback(
    *,
    assigned_runtime_kind: RuntimeKind,
    admission_ok: bool,
    admission_failure_code: str | None,
    chat_run_already_inserted: bool,
    request_id: str | None,
    rollout_revision_id: UUID | None,
    assignment_id: UUID | None = None,
) -> PreInsertFallbackDecision:
    """Decide whether automatic Legacy fallback is still open.

    Fallback is allowed only when:
    - assignment candidate is main_agent
    - admission preflight failed
    - no Chat Run row exists yet for this request
    """

    if chat_run_already_inserted:
        return PreInsertFallbackDecision(
            allowed=False,
            candidate_runtime_kind="main_agent",
            selected_runtime_kind="main_agent",
            selection_reason="assigned",
            admission_failure_code=admission_failure_code,
            admission_failure_digest=None,
            request_id=request_id,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
        )
    if assigned_runtime_kind != "main_agent":
        return PreInsertFallbackDecision(
            allowed=False,
            candidate_runtime_kind="legacy",
            selected_runtime_kind="legacy",
            selection_reason="assigned",
            admission_failure_code=None,
            admission_failure_digest=None,
            request_id=request_id,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
        )
    if admission_ok:
        return PreInsertFallbackDecision(
            allowed=False,
            candidate_runtime_kind="main_agent",
            selected_runtime_kind="main_agent",
            selection_reason="assigned",
            admission_failure_code=None,
            admission_failure_digest=None,
            request_id=request_id,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
        )
    failure_code = str(admission_failure_code or "admission_failed")
    failure_digest = sha256_canonical_json(
        {
            "reason": "preinsert_fallback",
            "failureCode": failure_code,
            "requestId": request_id,
            "rolloutRevisionId": str(rollout_revision_id) if rollout_revision_id else None,
        }
    )
    return PreInsertFallbackDecision(
        allowed=True,
        candidate_runtime_kind="main_agent",
        selected_runtime_kind="legacy",
        selection_reason="preinsert_fallback",
        admission_failure_code=failure_code,
        admission_failure_digest=failure_digest,
        request_id=request_id,
        rollout_revision_id=rollout_revision_id,
        assignment_id=assignment_id,
        resulting_legacy_run_id=uuid4(),  # pre-generated for atomic insert
    )


def record_preinsert_fallback(
    session: Session,
    *,
    decision: PreInsertFallbackDecision,
    resulting_legacy_run_id: UUID | None = None,
    build_revision: str | None = None,
    schema_revision: str | None = None,
    principal_scope_digest: str | None = None,
) -> PreInsertFallbackDecision:
    """Persist the fallback event. Caller inserts the Legacy Run in the same txn.

    Order: insert Legacy Run first (FK), then record the event. ``resulting_legacy_run_id``
    should match the pre-generated id on the decision when possible.
    """

    if not decision.allowed:
        raise RolloutError(CODE_PRECONDITION, "fallback not allowed")
    if decision.rollout_revision_id is None or not decision.request_id:
        raise RolloutError(CODE_INVALID_INPUT, "fallback decision incomplete")
    if not decision.admission_failure_digest:
        raise RolloutError(CODE_INVALID_INPUT, "admission_failure_digest required")

    run_id = resulting_legacy_run_id or decision.resulting_legacy_run_id
    if run_id is None:
        raise RolloutError(CODE_INVALID_INPUT, "resulting_legacy_run_id required")

    repo = RuntimeMigrationRepository(session)
    try:
        event = repo.record_admission_fallback(
            request_id=str(decision.request_id),
            rollout_revision_id=decision.rollout_revision_id,
            resulting_legacy_run_id=run_id,
            admission_failure_digest=decision.admission_failure_digest,
            assignment_id=decision.assignment_id,
            principal_scope_digest=principal_scope_digest,
            build_revision=build_revision,
            schema_revision=schema_revision,
        )
    except RuntimeMigrationRepositoryError as exc:
        raise RolloutError(exc.code, exc.message) from exc

    return PreInsertFallbackDecision(
        allowed=True,
        candidate_runtime_kind="main_agent",
        selected_runtime_kind="legacy",
        selection_reason="preinsert_fallback",
        admission_failure_code=decision.admission_failure_code,
        admission_failure_digest=decision.admission_failure_digest,
        request_id=decision.request_id,
        rollout_revision_id=decision.rollout_revision_id,
        assignment_id=decision.assignment_id,
        resulting_legacy_run_id=run_id,
        fallback_event_id=event.id,
    )


def admit_with_rollout(
    session: Session,
    *,
    conversation_id: UUID,
    request_id: str | None = None,
    execution_kind: str = "production",
    app_build_revision: str | None = None,
    require_compatible_worker: bool = True,
    principal_scope_digest: str | None = None,
    chat_run_already_inserted: bool = False,
) -> tuple[str, str | None, dict[str, Any], RolloutDecision | None]:
    """Admission that freezes assignment then selects runtime (pre-insert only).

    Returns ``(runtime_kind, reason_code, create_run_kwargs, decision)``.

    When no active rollout revision exists, delegates to the Plan 06 admission
    path (legacy-default) without writing assignment rows.
    """

    from app.assistant.durable.admission import admit_and_select_runtime

    repo = RuntimeMigrationRepository(session)
    revision = repo.get_active_rollout_revision()
    if revision is None:
        kind, reason, kwargs = admit_and_select_runtime(
            session,
            execution_kind=execution_kind,
            app_build_revision=app_build_revision,
            require_compatible_worker=require_compatible_worker,
        )
        return kind, reason, kwargs, None

    assignment_result = ensure_assignment(
        session,
        conversation_id=conversation_id,
        revision=revision,
        principal_scope_digest=principal_scope_digest,
    )
    assigned = assignment_result.assigned_runtime_kind
    decision = build_assigned_decision(
        revision=revision,
        assignment=assignment_result.assignment,
        bucket=assignment_result.bucket,
    )

    if assigned == "legacy":
        return (
            "legacy",
            "rollout_assigned_legacy",
            {
                "runtime_kind": "legacy",
            },
            decision,
        )

    # Candidate is main_agent — run full preflight (no nested rollout assignment).
    kind, reason, kwargs = admit_and_select_runtime(
        session,
        mode="read_only",  # force Main Agent admission attempt
        execution_kind=execution_kind,
        app_build_revision=app_build_revision,
        require_compatible_worker=require_compatible_worker,
        use_rollout_assignment=False,
    )
    admission_ok = kind == "main_agent"
    if admission_ok:
        kwargs = dict(kwargs)
        kwargs["runtime_kind"] = "main_agent"
        return "main_agent", reason, kwargs, decision

    # Pre-insert fallback path.
    fb = evaluate_preinsert_fallback(
        assigned_runtime_kind="main_agent",
        admission_ok=False,
        admission_failure_code=reason,
        chat_run_already_inserted=chat_run_already_inserted,
        request_id=request_id or f"admission-{uuid4()}",
        rollout_revision_id=revision.id,
        assignment_id=assignment_result.assignment.id,
    )
    if not fb.allowed:
        # Post-insert: stay on main_agent path failure (caller must not fall back).
        return "main_agent", reason or "postinsert_no_fallback", kwargs, decision

    fb_decision = RolloutDecision(
        rollout_revision_id=str(revision.id),
        assignment_id=str(assignment_result.assignment.id),
        assigned_runtime_kind="main_agent",
        selected_runtime_kind="legacy",
        write_mode="off",
        bucket=assignment_result.bucket,
        assignment_reason=assignment_result.assignment_reason,
        selection_reason="preinsert_fallback",
        fallback_event_id=None,
        admission_failure_digest=fb.admission_failure_digest,
    )
    create_kwargs: dict[str, Any] = {
        "runtime_kind": "legacy",
        # Pre-generated id so caller can insert Run then record fallback event.
        "_preinsert_fallback": fb,
        "_rollout_decision": fb_decision,
    }
    return "legacy", reason or "preinsert_fallback", create_kwargs, fb_decision


__all__ = (
    "AssignmentComputeResult",
    "COHORT_SALT_ENV",
    "PreInsertFallbackDecision",
    "RolloutError",
    "activate_revision",
    "admit_with_rollout",
    "build_assigned_decision",
    "cohort_salt_fingerprint",
    "compute_cohort_bucket",
    "decide_assigned_runtime_kind",
    "ensure_assignment",
    "evaluate_preinsert_fallback",
    "get_revision_by_label",
    "prepare_revision",
    "record_preinsert_fallback",
    "resolve_cohort_salt",
    "rollback_to_legacy",
    "validate_runtime_rollout_startup",
)
