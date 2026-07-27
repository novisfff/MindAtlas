"""Side-effect-safe runtime shadow comparison helpers (Plan 10 Task 5).

The user-visible production half remains a single ``AssistantChatRun``. The
Main Agent half is always a Plan 09 Eval Run with purpose ``runtime_shadow``
(gate-ineligible). Shadow failures never create/remap a Chat Run and never
mutate legacy response identity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.contracts import RuntimeShadowInputSnapshot
from app.assistant.migration.models import AssistantRuntimeShadowComparison
from app.assistant.migration.repository import (
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)
from app.common.time import utcnow

logger = logging.getLogger(__name__)

# Default private snapshot retention for staff/fixture shadow paths.
DEFAULT_SNAPSHOT_TTL = timedelta(hours=24)

# Safe evidence field names allowed on comparison rows (no raw content).
_EVIDENCE_SUMMARY_FIELDS = frozenset(
    {
        "intent_class",
        "legacy_skill_selection",
        "new_skill_selection",
        "capability_path_summary",
        "completion_summary",
        "stop_summary",
        "error_summary",
        "rounds_estimate",
        "calls_estimate",
        "tokens_estimate",
        "latency_ms_estimate",
        "cost_estimate_micros",
        "result_state",
        "reviewer_state",
        "write_simulation_required",
        "quality_assertion_snapshot",
    }
)

_FORBIDDEN_RAW_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "content",
        "payload_body",
        "system_prompt",
        "api_key",
        "authorization",
        "cookie",
        "raw_headers",
        "signed_url",
    }
)


class ShadowSchedulingError(RuntimeError):
    """Non-fatal shadow scheduling failure (never remaps production Chat Run)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ShadowScheduleResult:
    """Outcome of attempting to pair a production Run with a runtime_shadow Eval."""

    scheduled: bool
    comparison_id: UUID | None = None
    eval_run_id: UUID | None = None
    snapshot: RuntimeShadowInputSnapshot | None = None
    reason_code: str | None = None
    error: str | None = None


def build_private_input_snapshot(
    *,
    production_run_id: UUID,
    source_user_message_id: UUID | None = None,
    principal_scope_digest: str | None = None,
    message_prefix_digest: str | None = None,
    authorized_context_digest: str | None = None,
    snapshot_policy_digest: str | None = None,
    private_eval_artifact_id: UUID | None = None,
    payload_digest: str | None = None,
    expires_at: str | None = None,
    snapshot_id: UUID | None = None,
) -> RuntimeShadowInputSnapshot:
    """Build a frozen private snapshot identity (digests only; no raw content)."""

    sid = snapshot_id or uuid4()
    principal = principal_scope_digest or _zero_digest("principal_scope")
    message_prefix = message_prefix_digest or _zero_digest("message_prefix")
    context = authorized_context_digest or _zero_digest("authorized_context")
    policy = snapshot_policy_digest or _zero_digest("snapshot_policy")
    payload = payload_digest or sha256_canonical_json(
        {
            "snapshotId": str(sid),
            "sourceProductionRunId": str(production_run_id),
            "sourceUserMessageId": (
                str(source_user_message_id) if source_user_message_id else None
            ),
            "principalScopeDigest": principal,
            "messagePrefixDigest": message_prefix,
            "authorizedContextDigest": context,
            "snapshotPolicyDigest": policy,
        }
    )
    if expires_at is None:
        expires_at = (utcnow() + DEFAULT_SNAPSHOT_TTL).isoformat()
    return RuntimeShadowInputSnapshot(
        snapshot_id=str(sid),
        source_production_run_id=str(production_run_id),
        source_user_message_id=(
            str(source_user_message_id) if source_user_message_id else None
        ),
        principal_scope_digest=principal,
        message_prefix_digest=message_prefix,
        authorized_context_digest=context,
        snapshot_policy_digest=policy,
        private_eval_artifact_id=(
            str(private_eval_artifact_id) if private_eval_artifact_id else None
        ),
        payload_digest=payload,
        expires_at=expires_at,
    )


def schedule_runtime_shadow(
    session: Session,
    *,
    production_run_id: UUID,
    subject_kind: str,
    subject_aggregate_id: UUID,
    subject_version_id: UUID,
    subject_content_digest: str,
    subject_binding_digest: str,
    isolation_digest: str,
    required_build_revision: str,
    input_digest: str | None = None,
    context_digest: str | None = None,
    source_user_message_id: UUID | None = None,
    principal_scope_digest: str | None = None,
    rollout_revision_id: UUID | None = None,
    assignment_id: UUID | None = None,
    shadow_eligible: bool = False,
    fixture_digest: str | None = None,
    catalog_revision: str | None = None,
    profile_revision: str | None = None,
    model_revision: str | None = None,
    runtime_revision: str | None = None,
    build_revision: str | None = None,
    intent_class: str | None = None,
    write_simulation_required: bool = False,
    actor_principal: str | None = None,
    request_id: str | None = None,
    threshold_policy_version: str = "runtime-shadow-staff-v1",
    mode: str = "interactive_scripted",
    runtime_contract_version: int = 1,
    nonblocking: bool = True,
) -> ShadowScheduleResult:
    """Insert a gate-ineligible ``runtime_shadow`` Eval Run + comparison pair.

    Intended to run after production admission. Failures are nonblocking by
    default: they never create a second Chat Run and never remap the production
    Run identity.
    """

    try:
        return _schedule_runtime_shadow_inner(
            session,
            production_run_id=production_run_id,
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
            subject_content_digest=subject_content_digest,
            subject_binding_digest=subject_binding_digest,
            isolation_digest=isolation_digest,
            required_build_revision=required_build_revision,
            input_digest=input_digest,
            context_digest=context_digest,
            source_user_message_id=source_user_message_id,
            principal_scope_digest=principal_scope_digest,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
            shadow_eligible=shadow_eligible,
            fixture_digest=fixture_digest,
            catalog_revision=catalog_revision,
            profile_revision=profile_revision,
            model_revision=model_revision,
            runtime_revision=runtime_revision,
            build_revision=build_revision,
            intent_class=intent_class,
            write_simulation_required=write_simulation_required,
            actor_principal=actor_principal,
            request_id=request_id,
            threshold_policy_version=threshold_policy_version,
            mode=mode,
            runtime_contract_version=runtime_contract_version,
        )
    except Exception as exc:  # noqa: BLE001 — shadow must never break production
        logger.info(
            "runtime shadow schedule failed production_run=%s err=%s",
            production_run_id,
            type(exc).__name__,
        )
        if not nonblocking:
            if isinstance(exc, ShadowSchedulingError):
                raise
            raise ShadowSchedulingError("schedule_failed", str(exc)[:200]) from exc
        return ShadowScheduleResult(
            scheduled=False,
            reason_code="schedule_failed",
            error=f"{type(exc).__name__}:{str(exc)[:160]}",
        )


def record_comparison_evidence(
    session: Session,
    *,
    comparison_id: UUID,
    evidence: Mapping[str, Any],
) -> AssistantRuntimeShadowComparison:
    """Attach typed summary/digest evidence to a comparison row (no raw content)."""

    row = session.get(AssistantRuntimeShadowComparison, comparison_id)
    if row is None:
        raise ShadowSchedulingError("not_found", "shadow comparison not found")
    sanitized = _sanitize_evidence(dict(evidence))
    for key, value in sanitized.items():
        if key not in _EVIDENCE_SUMMARY_FIELDS:
            continue
        setattr(row, key, value)
    session.flush()
    return row


def legacy_response_is_independent(
    *,
    production_run_id: UUID,
    shadow_result: ShadowScheduleResult,
    production_events_before: SequenceLike | None = None,
    production_events_after: SequenceLike | None = None,
) -> bool:
    """Return True when shadow outcome cannot alter production Run identity/events.

    Used by independence tests. A failed or successful shadow never changes the
    production Run ID and must not rewrite production events.
    """

    if shadow_result.eval_run_id is not None and shadow_result.eval_run_id == production_run_id:
        return False
    if shadow_result.comparison_id is not None and shadow_result.comparison_id == production_run_id:
        return False
    if production_events_before is not None and production_events_after is not None:
        return list(production_events_before) == list(production_events_after)
    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


SequenceLike = Any


def _zero_digest(label: str) -> str:
    return sha256_canonical_json({"label": label, "empty": True})


def _sanitize_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        key_s = str(key)
        if key_s.lower() in _FORBIDDEN_RAW_KEYS:
            continue
        if key_s not in _EVIDENCE_SUMMARY_FIELDS:
            continue
        if isinstance(value, str) and len(value) > 256:
            out[key_s] = value[:256]
        elif isinstance(value, Mapping):
            # quality_assertion_snapshot: only digests/counts/bools.
            safe: dict[str, Any] = {}
            for sk, sv in value.items():
                sk_s = str(sk)
                if sk_s.lower() in _FORBIDDEN_RAW_KEYS:
                    continue
                if isinstance(sv, (int, float, bool)) or sv is None:
                    safe[sk_s] = sv
                elif isinstance(sv, str) and len(sv) <= 128:
                    safe[sk_s] = sv
            out[key_s] = safe
        else:
            out[key_s] = value
    return out


def _schedule_runtime_shadow_inner(
    session: Session,
    *,
    production_run_id: UUID,
    subject_kind: str,
    subject_aggregate_id: UUID,
    subject_version_id: UUID,
    subject_content_digest: str,
    subject_binding_digest: str,
    isolation_digest: str,
    required_build_revision: str,
    input_digest: str | None,
    context_digest: str | None,
    source_user_message_id: UUID | None,
    principal_scope_digest: str | None,
    rollout_revision_id: UUID | None,
    assignment_id: UUID | None,
    shadow_eligible: bool,
    fixture_digest: str | None,
    catalog_revision: str | None,
    profile_revision: str | None,
    model_revision: str | None,
    runtime_revision: str | None,
    build_revision: str | None,
    intent_class: str | None,
    write_simulation_required: bool,
    actor_principal: str | None,
    request_id: str | None,
    threshold_policy_version: str,
    mode: str,
    runtime_contract_version: int,
) -> ShadowScheduleResult:
    from app.assistant.evaluation.repository import EvaluationRepository

    snapshot = build_private_input_snapshot(
        production_run_id=production_run_id,
        source_user_message_id=source_user_message_id,
        principal_scope_digest=principal_scope_digest,
        message_prefix_digest=input_digest,
        authorized_context_digest=context_digest,
    )

    isolation_namespace_id = uuid4()
    eval_repo = EvaluationRepository(session)
    eval_run = eval_repo.create_run(
        subject_kind=subject_kind,
        subject_aggregate_id=subject_aggregate_id,
        subject_version_id=subject_version_id,
        subject_content_digest=subject_content_digest,
        subject_binding_digest=subject_binding_digest,
        dataset_version_ids=[],
        threshold_policy_version=threshold_policy_version,
        mode=mode,
        isolation_namespace_id=isolation_namespace_id,
        runtime_contract_version=runtime_contract_version,
        required_build_revision=required_build_revision,
        isolation_digest=isolation_digest,
        purpose="runtime_shadow",
        actor_principal=actor_principal,
        request_id=request_id,
    )
    if bool(getattr(eval_run, "gate_eligible", False)):
        raise ShadowSchedulingError(
            "gate_eligible_forbidden",
            "runtime_shadow eval runs must be gate-ineligible",
        )

    mig_repo = RuntimeMigrationRepository(session)
    try:
        comparison = mig_repo.create_shadow_comparison(
            production_run_id=production_run_id,
            eval_run_id=eval_run.id,
            input_digest=input_digest or snapshot.message_prefix_digest,
            context_digest=context_digest or snapshot.authorized_context_digest,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
            shadow_eligible=bool(shadow_eligible),
            fixture_digest=fixture_digest,
            catalog_revision=catalog_revision,
            profile_revision=profile_revision,
            model_revision=model_revision,
            runtime_revision=runtime_revision,
            build_revision=build_revision or required_build_revision,
            intent_class=intent_class,
            write_simulation_required=write_simulation_required,
            private_input_snapshot_id=UUID(snapshot.snapshot_id),
            private_input_payload_digest=snapshot.payload_digest,
        )
    except RuntimeMigrationRepositoryError as exc:
        raise ShadowSchedulingError(exc.code, exc.message) from exc

    return ShadowScheduleResult(
        scheduled=True,
        comparison_id=comparison.id,
        eval_run_id=eval_run.id,
        snapshot=snapshot,
        reason_code="scheduled",
    )


__all__ = (
    "ShadowScheduleResult",
    "ShadowSchedulingError",
    "build_private_input_snapshot",
    "legacy_response_is_independent",
    "record_comparison_evidence",
    "schedule_runtime_shadow",
)
