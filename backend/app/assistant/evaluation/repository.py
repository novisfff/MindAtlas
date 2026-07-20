"""EvaluationRepository — sole writer for evaluation persistence (Plan 09 Task 3).

Owns Dataset/Draft/Version/Case/EvalRun/CaseResult/EvalCapabilityCall/Event/
Artifact/PublishGate/PublishGateUse tables. Requires expected revisions for CAS
mutations. Append-only children never update/delete through this repository
except retention cleanup of unreferenced high-volume evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, delete, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_bytes, sha256_canonical_json
from app.assistant.evaluation.artifacts import resolve_artifact_storage
from app.assistant.evaluation.contracts import (
    EVAL_OWNER_KIND,
    EvalCapabilityOutcome,
    EvalRunMode,
    EvalRunStatus,
    EvalSubjectKind,
    PublishGateAction,
    PublishGateDecision,
    assert_evaluation_object_key,
    is_evaluation_object_key,
)
from app.assistant.evaluation.models import (
    AssistantSkillEvalArtifact,
    AssistantSkillEvalCapabilityCall,
    AssistantSkillEvalCase,
    AssistantSkillEvalCaseResult,
    AssistantSkillEvalDataset,
    AssistantSkillEvalDatasetDraft,
    AssistantSkillEvalDatasetVersion,
    AssistantSkillEvalEvent,
    AssistantSkillEvalRun,
    AssistantSkillPublishGate,
    AssistantSkillPublishGateUse,
)
from app.assistant.evaluation.snapshots import assert_payload_safe
from app.common.time import utcnow

# ---------------------------------------------------------------------------
# Stable codes
# ---------------------------------------------------------------------------

CODE_NOT_FOUND = "not_found"
CODE_STALE_REVISION = "stale_revision"
CODE_FORBIDDEN_TRANSITION = "forbidden_transition"
CODE_IMMUTABLE = "immutable"
CODE_CONFLICT = "conflict"
CODE_INVALID_INPUT = "invalid_input"
CODE_NAMESPACE = "namespace_violation"
CODE_OWNERSHIP = "ownership_violation"
CODE_RETENTION_PINNED = "retention_pinned"
CODE_DOWNGRADE_BLOCKED = "downgrade_blocked"

DEFAULT_GATE_EVIDENCE_GRACE_DAYS = 30

# Eval Run state machine (plan §Evaluation Run state machine)
ALLOWED_RUN_TRANSITIONS: dict[tuple[str, str], str] = {
    ("queued", "running"): "claim",
    ("running", "completed"): "complete",
    ("running", "failed"): "fail",
    ("running", "cancelled"): "cancel_direct",
    ("running", "queued"): "stale_lease_recovery",
    ("running", "cancelling"): "request_cancel",
    ("queued", "cancelling"): "request_cancel",
    ("cancelling", "cancelled"): "cancel_final",
    ("queued", "cancelled"): "cancel_queued",
    ("queued", "failed"): "fail_admission",
}

TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


class EvaluationRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class PublishedDatasetVersion:
    version_id: UUID
    sequence: int
    content_digest: str
    case_count: int
    case_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RetentionCleanupResult:
    deleted_events: int
    deleted_artifacts: int
    skipped_pinned: int


def _require_sha256(value: str, *, field: str) -> str:
    text = (value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise EvaluationRepositoryError(
            CODE_INVALID_INPUT, f"{field} must be 64-char lowercase hex digest"
        )
    return text


def _as_utc(value: datetime) -> datetime:
    """Normalize naive (SQLite) or aware datetimes to UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_uuid_list(values: Sequence[UUID | str]) -> list[str]:
    return [str(UUID(str(v))) for v in values]


class EvaluationRepository:
    """Only writer of evaluation tables. Callers must not ORM-write these rows."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Dataset aggregate
    # ------------------------------------------------------------------

    def get_dataset(self, dataset_id: UUID) -> AssistantSkillEvalDataset | None:
        return self.session.get(AssistantSkillEvalDataset, dataset_id)

    def get_dataset_by_key(self, stable_key: str) -> AssistantSkillEvalDataset | None:
        stmt: Select[tuple[AssistantSkillEvalDataset]] = select(
            AssistantSkillEvalDataset
        ).where(AssistantSkillEvalDataset.stable_key == stable_key)
        return self.session.execute(stmt).scalar_one_or_none()

    def create_dataset(
        self,
        *,
        stable_key: str,
        display_name: str,
        description: str = "",
        ownership: str = "system",
        actor: str | None = None,
        dataset_id: UUID | None = None,
    ) -> AssistantSkillEvalDataset:
        if ownership not in ("system", "custom"):
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "invalid ownership")
        key = (stable_key or "").strip()
        if not key or len(key) > 128:
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "stable_key invalid")
        row = AssistantSkillEvalDataset(
            id=dataset_id or uuid4(),
            stable_key=key,
            display_name=display_name.strip() or key,
            description=description or "",
            ownership=ownership,
            aggregate_revision=0,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_or_create_draft(
        self,
        *,
        dataset_id: UUID,
        cases_snapshot: list[dict[str, Any]],
        actor: str | None = None,
    ) -> AssistantSkillEvalDatasetDraft:
        dataset = self._require_dataset(dataset_id)
        existing = self.session.execute(
            select(AssistantSkillEvalDatasetDraft).where(
                AssistantSkillEvalDatasetDraft.dataset_id == dataset_id
            )
        ).scalar_one_or_none()
        digest = sha256_canonical_json(cases_snapshot)
        if existing is not None:
            return existing
        draft = AssistantSkillEvalDatasetDraft(
            id=uuid4(),
            dataset_id=dataset.id,
            draft_revision=0,
            schema_version=1,
            cases_snapshot=cases_snapshot,
            draft_digest=digest,
            base_version_id=dataset.current_version_id,
            updated_by=actor,
        )
        self.session.add(draft)
        self.session.flush()
        return draft

    def put_draft(
        self,
        *,
        dataset_id: UUID,
        expected_draft_revision: int,
        cases_snapshot: list[dict[str, Any]],
        actor: str | None = None,
    ) -> AssistantSkillEvalDatasetDraft:
        draft = self.session.execute(
            select(AssistantSkillEvalDatasetDraft)
            .where(AssistantSkillEvalDatasetDraft.dataset_id == dataset_id)
            .with_for_update()
        ).scalar_one_or_none()
        if draft is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "dataset draft not found")
        if int(draft.draft_revision) != int(expected_draft_revision):
            raise EvaluationRepositoryError(
                CODE_STALE_REVISION,
                f"expected draft revision {expected_draft_revision}, got {draft.draft_revision}",
            )
        draft.cases_snapshot = list(cases_snapshot)
        draft.draft_digest = sha256_canonical_json(cases_snapshot)
        draft.draft_revision = int(draft.draft_revision) + 1
        draft.updated_by = actor
        draft.updated_at = utcnow()
        self.session.flush()
        return draft

    def publish_dataset_version(
        self,
        *,
        dataset_id: UUID,
        expected_aggregate_revision: int,
        expected_draft_revision: int,
        version_name: str,
        source_fixture_revision: str | None = None,
        actor: str | None = None,
        version_id: UUID | None = None,
        fixed_case_ids: bool = False,
        content_digest_override: str | None = None,
    ) -> PublishedDatasetVersion:
        dataset = self.session.execute(
            select(AssistantSkillEvalDataset)
            .where(AssistantSkillEvalDataset.id == dataset_id)
            .with_for_update()
        ).scalar_one_or_none()
        if dataset is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "dataset not found")
        if int(dataset.aggregate_revision) != int(expected_aggregate_revision):
            raise EvaluationRepositoryError(
                CODE_STALE_REVISION,
                f"expected aggregate revision {expected_aggregate_revision}, "
                f"got {dataset.aggregate_revision}",
            )
        draft = self.session.execute(
            select(AssistantSkillEvalDatasetDraft)
            .where(AssistantSkillEvalDatasetDraft.dataset_id == dataset_id)
            .with_for_update()
        ).scalar_one_or_none()
        if draft is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "dataset draft not found")
        if int(draft.draft_revision) != int(expected_draft_revision):
            raise EvaluationRepositoryError(
                CODE_STALE_REVISION,
                f"expected draft revision {expected_draft_revision}, got {draft.draft_revision}",
            )

        snapshot = list(draft.cases_snapshot or [])
        if not snapshot:
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "draft has no cases")

        # Next sequence
        max_seq = self.session.execute(
            select(AssistantSkillEvalDatasetVersion.sequence)
            .where(AssistantSkillEvalDatasetVersion.dataset_id == dataset_id)
            .order_by(AssistantSkillEvalDatasetVersion.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        sequence = int(max_seq or 0) + 1

        # Content digest over normalized case digests (stable order by ordinal),
        # unless a fixture importer supplies a locked override (Plan 04).
        ordered = sorted(snapshot, key=lambda row: int(row.get("ordinal", 0)))
        if content_digest_override is not None:
            content_digest = _require_sha256(
                content_digest_override, field="content_digest_override"
            )
        else:
            content_digest = sha256_canonical_json(
                [row.get("case_digest") for row in ordered]
            )

        # If content already published, return existing (idempotent).
        existing_version = self.session.execute(
            select(AssistantSkillEvalDatasetVersion).where(
                AssistantSkillEvalDatasetVersion.dataset_id == dataset_id,
                AssistantSkillEvalDatasetVersion.content_digest == content_digest,
            )
        ).scalar_one_or_none()
        if existing_version is not None:
            cases = self.list_cases(existing_version.id)
            if dataset.current_version_id != existing_version.id:
                dataset.current_version_id = existing_version.id
                dataset.aggregate_revision = int(dataset.aggregate_revision) + 1
                draft.base_version_id = existing_version.id
                draft.draft_revision = int(draft.draft_revision) + 1
                self.session.flush()
            return PublishedDatasetVersion(
                version_id=existing_version.id,
                sequence=int(existing_version.sequence),
                content_digest=existing_version.content_digest,
                case_count=len(cases),
                case_ids=tuple(c.id for c in cases),
            )

        # Prefer full-case canonical digest when fixture importer stamped case_digest
        # from Plan 04 EvalCase.to_dict(); recompute content from case digests.
        version = AssistantSkillEvalDatasetVersion(
            id=version_id or uuid4(),
            dataset_id=dataset.id,
            sequence=sequence,
            version_name=version_name,
            schema_version=int(draft.schema_version),
            content_digest=content_digest,
            source_fixture_revision=source_fixture_revision,
            created_by=actor,
            created_at=utcnow(),
        )
        self.session.add(version)
        self.session.flush()

        case_ids: list[UUID] = []
        for row in ordered:
            cid = (
                UUID(str(row["id"]))
                if fixed_case_ids and row.get("id")
                else uuid4()
            )
            case = AssistantSkillEvalCase(
                id=cid,
                dataset_version_id=version.id,
                case_key=str(row["case_key"]),
                ordinal=int(row["ordinal"]),
                locale=str(row.get("locale") or "en"),
                input_messages=list(row.get("input_messages") or []),
                fixture_refs=list(row.get("fixture_refs") or []),
                expected_mode=str(row.get("expected_mode") or "unknown"),
                acceptable_skill_keys=list(row.get("acceptable_skill_keys") or []),
                forbidden_skill_keys=list(row.get("forbidden_skill_keys") or []),
                acceptable_capability_paths=list(
                    row.get("acceptable_capability_paths") or []
                ),
                forbidden_side_effect_classes=list(
                    row.get("forbidden_side_effect_classes") or []
                ),
                expect_completion=bool(row.get("expect_completion", True)),
                assertion_json=dict(row.get("assertion_json") or {}),
                ceilings_json=dict(row.get("ceilings_json") or row.get("ceilings") or {}),
                tags=list(row.get("tags") or []),
                notes=str(row.get("notes") or ""),
                case_digest=_require_sha256(
                    str(row.get("case_digest") or sha256_canonical_json(row)),
                    field="case_digest",
                ),
                created_at=utcnow(),
            )
            self.session.add(case)
            case_ids.append(cid)

        dataset.current_version_id = version.id
        dataset.aggregate_revision = int(dataset.aggregate_revision) + 1
        draft.base_version_id = version.id
        draft.draft_revision = int(draft.draft_revision) + 1
        draft.updated_by = actor
        draft.updated_at = utcnow()
        self.session.flush()
        return PublishedDatasetVersion(
            version_id=version.id,
            sequence=sequence,
            content_digest=content_digest,
            case_count=len(case_ids),
            case_ids=tuple(case_ids),
        )

    def get_dataset_version(
        self, version_id: UUID
    ) -> AssistantSkillEvalDatasetVersion | None:
        return self.session.get(AssistantSkillEvalDatasetVersion, version_id)

    def list_cases(
        self, dataset_version_id: UUID
    ) -> list[AssistantSkillEvalCase]:
        stmt = (
            select(AssistantSkillEvalCase)
            .where(AssistantSkillEvalCase.dataset_version_id == dataset_version_id)
            .order_by(AssistantSkillEvalCase.ordinal.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    # ------------------------------------------------------------------
    # Eval Run
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        subject_kind: EvalSubjectKind,
        subject_aggregate_id: UUID,
        subject_version_id: UUID,
        subject_content_digest: str,
        subject_binding_digest: str,
        dataset_version_ids: Sequence[UUID],
        threshold_policy_version: str,
        mode: EvalRunMode,
        isolation_namespace_id: UUID,
        runtime_contract_version: int,
        required_build_revision: str,
        isolation_digest: str,
        owner_kind: str = EVAL_OWNER_KIND,
        runner_contract_version: int = 1,
        policy_digest: str | None = None,
        runtime_digest: str | None = None,
        provider_evidence_digest: str | None = None,
        actor_principal: str | None = None,
        request_id: str | None = None,
        run_id: UUID | None = None,
    ) -> AssistantSkillEvalRun:
        if owner_kind != EVAL_OWNER_KIND:
            raise EvaluationRepositoryError(
                CODE_OWNERSHIP,
                "Eval Run owner_kind must be 'test' (execution namespace)",
            )
        if not dataset_version_ids and mode != "interactive_scripted":
            raise EvaluationRepositoryError(
                CODE_INVALID_INPUT,
                "dataset_version_ids required for dataset evaluation modes",
            )
        row = AssistantSkillEvalRun(
            id=run_id or uuid4(),
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
            subject_content_digest=_require_sha256(
                subject_content_digest, field="subject_content_digest"
            ),
            subject_binding_digest=_require_sha256(
                subject_binding_digest, field="subject_binding_digest"
            ),
            dataset_version_ids=_as_uuid_list(dataset_version_ids),
            threshold_policy_version=threshold_policy_version,
            mode=mode,
            status="queued",
            isolation_namespace_id=isolation_namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            runtime_contract_version=int(runtime_contract_version),
            required_build_revision=required_build_revision,
            runner_contract_version=int(runner_contract_version),
            state_revision=0,
            isolation_digest=_require_sha256(isolation_digest, field="isolation_digest"),
            policy_digest=(
                _require_sha256(policy_digest, field="policy_digest")
                if policy_digest
                else None
            ),
            runtime_digest=(
                _require_sha256(runtime_digest, field="runtime_digest")
                if runtime_digest
                else None
            ),
            provider_evidence_digest=(
                _require_sha256(provider_evidence_digest, field="provider_evidence_digest")
                if provider_evidence_digest
                else None
            ),
            actor_principal=actor_principal,
            request_id=request_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_run(self, run_id: UUID) -> AssistantSkillEvalRun | None:
        return self.session.get(AssistantSkillEvalRun, run_id)

    def transition_run(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        to_status: EvalRunStatus,
        lease_owner: str | None = None,
        lease_generation: int | None = None,
        lease_expires_at: datetime | None = None,
        failure_code: str | None = None,
        gate_eligible: bool | None = None,
        aggregate_metrics: Mapping[str, Any] | None = None,
    ) -> AssistantSkillEvalRun:
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "eval run not found")
        if int(run.state_revision) != int(expected_revision):
            raise EvaluationRepositoryError(
                CODE_STALE_REVISION,
                f"expected revision {expected_revision}, got {run.state_revision}",
            )
        if run.status in TERMINAL_RUN_STATUSES:
            raise EvaluationRepositoryError(
                CODE_IMMUTABLE, f"terminal run status {run.status} is immutable"
            )
        key = (str(run.status), str(to_status))
        if key not in ALLOWED_RUN_TRANSITIONS and str(run.status) != str(to_status):
            raise EvaluationRepositoryError(
                CODE_FORBIDDEN_TRANSITION,
                f"transition {run.status} -> {to_status} not allowed",
            )
        now = utcnow()
        if to_status == "running" and run.started_at is None:
            run.started_at = now
            run.attempt_count = int(run.attempt_count) + 1
        if to_status in TERMINAL_RUN_STATUSES:
            run.ended_at = now
            run.lease_owner = None
            run.lease_expires_at = None
        if to_status == "cancelling":
            run.requested_cancel_at = now
        if lease_owner is not None:
            run.lease_owner = lease_owner
        if lease_generation is not None:
            run.lease_generation = int(lease_generation)
        if lease_expires_at is not None:
            run.lease_expires_at = lease_expires_at
        if failure_code is not None:
            run.failure_code = failure_code
        if gate_eligible is not None:
            run.gate_eligible = bool(gate_eligible)
        if aggregate_metrics is not None:
            run.aggregate_metrics = dict(aggregate_metrics)
        run.status = to_status
        run.state_revision = int(run.state_revision) + 1
        run.heartbeat_at = now
        self.session.flush()
        return run

    def heartbeat_run(
        self,
        *,
        run_id: UUID,
        expected_revision: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AssistantSkillEvalRun:
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "eval run not found")
        if int(run.state_revision) != int(expected_revision):
            raise EvaluationRepositoryError(CODE_STALE_REVISION, "stale revision")
        if run.lease_owner != lease_owner:
            raise EvaluationRepositoryError(CODE_CONFLICT, "lease owner mismatch")
        run.lease_expires_at = lease_expires_at
        run.heartbeat_at = utcnow()
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return run

    # ------------------------------------------------------------------
    # Claim / lease (SKIP LOCKED) — evaluation namespace only
    # ------------------------------------------------------------------

    def claim_next_run(
        self,
        *,
        worker_id: str,
        required_build_revision: str,
        runtime_contract_version: int,
        runner_contract_version: int = 1,
        lease_ttl: timedelta | None = None,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> AssistantSkillEvalRun | None:
        """Claim one compatible queued/stale Eval Run with SKIP LOCKED.

        Never touches production AssistantChatRun rows. Compatibility requires
        matching build revision, runtime contract, and runner contract.
        """
        owner = (worker_id or "").strip()
        if not owner:
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "worker_id required")
        current = now or utcnow()
        ttl = lease_ttl or timedelta(seconds=30)
        expired_lease = or_(
            AssistantSkillEvalRun.lease_expires_at.is_(None),
            AssistantSkillEvalRun.lease_expires_at <= current,
        )
        status_predicate = or_(
            AssistantSkillEvalRun.status == "queued",
            and_(
                AssistantSkillEvalRun.status.in_(("running", "cancelling")),
                expired_lease,
            ),
        )
        attempt_predicate = AssistantSkillEvalRun.attempt_count < int(max_attempts)
        stmt = (
            select(AssistantSkillEvalRun)
            .where(
                AssistantSkillEvalRun.required_build_revision == required_build_revision,
                AssistantSkillEvalRun.runtime_contract_version
                == int(runtime_contract_version),
                AssistantSkillEvalRun.runner_contract_version
                == int(runner_contract_version),
                AssistantSkillEvalRun.owner_kind == EVAL_OWNER_KIND,
                status_predicate,
                attempt_predicate,
            )
            .order_by(
                AssistantSkillEvalRun.created_at.asc(),
            )
            .limit(1)
        )
        try:
            stmt = stmt.with_for_update(skip_locked=True)
            run = self.session.execute(stmt).scalar_one_or_none()
        except Exception:
            # Dialect may not support skip_locked (SQLite).
            self.session.rollback()
            stmt = (
                select(AssistantSkillEvalRun)
                .where(
                    AssistantSkillEvalRun.required_build_revision
                    == required_build_revision,
                    AssistantSkillEvalRun.runtime_contract_version
                    == int(runtime_contract_version),
                    AssistantSkillEvalRun.runner_contract_version
                    == int(runner_contract_version),
                    AssistantSkillEvalRun.owner_kind == EVAL_OWNER_KIND,
                    status_predicate,
                    attempt_predicate,
                )
                .order_by(AssistantSkillEvalRun.created_at.asc())
                .limit(1)
                .with_for_update()
            )
            run = self.session.execute(stmt).scalar_one_or_none()

        if run is None:
            return None

        # Stale running/cancelling with expired lease → requeue then claim, or
        # finalize cancel if cancelling.
        if run.status == "cancelling":
            run.status = "cancelled"
            run.ended_at = current
            run.lease_owner = None
            run.lease_expires_at = None
            run.state_revision = int(run.state_revision) + 1
            run.heartbeat_at = current
            self.session.flush()
            return None

        if run.status == "running":
            # Deterministic stale-lease recovery: return to queued for reclaim.
            run.status = "queued"
            run.lease_owner = None
            run.lease_expires_at = None
            run.state_revision = int(run.state_revision) + 1
            run.heartbeat_at = current
            # Fall through to claim as queued in same transaction.

        if run.status != "queued":
            return None

        run.status = "running"
        if run.started_at is None:
            run.started_at = current
        run.attempt_count = int(run.attempt_count) + 1
        run.lease_owner = owner
        run.lease_generation = int(run.lease_generation) + 1
        run.lease_expires_at = current + ttl
        run.heartbeat_at = current
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return run

    def claim_run(
        self,
        *,
        run_id: UUID,
        worker_id: str,
        required_build_revision: str,
        runtime_contract_version: int,
        runner_contract_version: int = 1,
        lease_ttl: timedelta | None = None,
        now: datetime | None = None,
    ) -> AssistantSkillEvalRun | None:
        """Claim a specific Eval Run if eligible and compatible (test helper)."""
        owner = (worker_id or "").strip()
        if not owner:
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "worker_id required")
        current = now or utcnow()
        ttl = lease_ttl or timedelta(seconds=30)
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            return None
        if str(run.required_build_revision or "") != required_build_revision:
            return None
        if int(run.runtime_contract_version or 0) != int(runtime_contract_version):
            return None
        if int(run.runner_contract_version or 0) != int(runner_contract_version):
            return None
        if str(run.owner_kind) != EVAL_OWNER_KIND:
            return None
        if run.status not in {"queued", "running"}:
            return None
        if run.status == "running":
            expires = run.lease_expires_at
            if expires is not None and _as_utc(expires) > _as_utc(current):
                return None
            # Stale recovery → requeue path equivalent.
        run.status = "running"
        if run.started_at is None:
            run.started_at = current
        run.attempt_count = int(run.attempt_count) + 1
        run.lease_owner = owner
        run.lease_generation = int(run.lease_generation) + 1
        run.lease_expires_at = current + ttl
        run.heartbeat_at = current
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return run

    def request_cancel_run(
        self,
        *,
        run_id: UUID,
        expected_revision: int | None = None,
    ) -> AssistantSkillEvalRun:
        """Request cancellation; worker observes before Provider/Capability boundaries."""
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "eval run not found")
        if expected_revision is not None and int(run.state_revision) != int(
            expected_revision
        ):
            raise EvaluationRepositoryError(CODE_STALE_REVISION, "stale revision")
        if run.status in TERMINAL_RUN_STATUSES:
            raise EvaluationRepositoryError(
                CODE_IMMUTABLE, f"terminal run status {run.status} is immutable"
            )
        if run.status == "cancelling":
            return run
        if run.status not in {"queued", "running"}:
            raise EvaluationRepositoryError(
                CODE_FORBIDDEN_TRANSITION,
                f"cannot request cancel from {run.status}",
            )
        run.status = "cancelling"
        run.requested_cancel_at = utcnow()
        run.state_revision = int(run.state_revision) + 1
        run.heartbeat_at = utcnow()
        self.session.flush()
        return run

    def list_events_after(
        self,
        *,
        eval_run_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[AssistantSkillEvalEvent]:
        """SSE replay: monotonic events after sequence (at-least-once; client dedups)."""
        if limit <= 0 or limit > 1000:
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "limit must be 1..1000")
        stmt = (
            select(AssistantSkillEvalEvent)
            .where(
                AssistantSkillEvalEvent.eval_run_id == eval_run_id,
                AssistantSkillEvalEvent.sequence > int(after_sequence),
            )
            .order_by(AssistantSkillEvalEvent.sequence.asc())
            .limit(int(limit))
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_capability_calls(
        self, *, eval_run_id: UUID, eval_case_id: UUID | None = None
    ) -> list[AssistantSkillEvalCapabilityCall]:
        stmt = select(AssistantSkillEvalCapabilityCall).where(
            AssistantSkillEvalCapabilityCall.eval_run_id == eval_run_id
        )
        if eval_case_id is not None:
            stmt = stmt.where(
                AssistantSkillEvalCapabilityCall.eval_case_id == eval_case_id
            )
        stmt = stmt.order_by(
            AssistantSkillEvalCapabilityCall.child_ordinal.asc(),
            AssistantSkillEvalCapabilityCall.attempt.asc(),
        )
        return list(self.session.execute(stmt).scalars().all())

    def has_capability_call_attempt(
        self,
        *,
        eval_run_id: UUID,
        eval_case_id: UUID,
        logical_call_key: str,
        attempt: int,
    ) -> bool:
        """Recovery guard: prevent double-count of synthetic logical calls."""
        row = self.session.execute(
            select(AssistantSkillEvalCapabilityCall.id).where(
                AssistantSkillEvalCapabilityCall.eval_run_id == eval_run_id,
                AssistantSkillEvalCapabilityCall.eval_case_id == eval_case_id,
                AssistantSkillEvalCapabilityCall.logical_call_key == logical_call_key,
                AssistantSkillEvalCapabilityCall.attempt == int(attempt),
            )
        ).first()
        return row is not None

    def has_event_sequence(
        self,
        *,
        eval_run_id: UUID,
        sequence: int,
    ) -> bool:
        """Recovery guard: skip re-append when (eval_run_id, sequence) already exists."""
        row = self.session.execute(
            select(AssistantSkillEvalEvent.id).where(
                AssistantSkillEvalEvent.eval_run_id == eval_run_id,
                AssistantSkillEvalEvent.sequence == int(sequence),
            )
        ).first()
        return row is not None

    def has_event_digest(
        self,
        *,
        eval_run_id: UUID,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> bool:
        """Optional digest-based recovery guard for events without known sequence."""
        digest = sha256_canonical_json(
            {
                "event_type": str(event_type),
                "payload": dict(payload or {}),
            }
        )
        rows = self.session.execute(
            select(AssistantSkillEvalEvent).where(
                AssistantSkillEvalEvent.eval_run_id == eval_run_id,
                AssistantSkillEvalEvent.event_type == str(event_type),
            )
        ).scalars().all()
        for row in rows:
            existing = sha256_canonical_json(
                {
                    "event_type": str(row.event_type),
                    "payload": dict(row.payload or {}),
                }
            )
            if existing == digest:
                return True
        return False

    # ------------------------------------------------------------------
    # Case results / capability calls / events / artifacts
    # ------------------------------------------------------------------

    def append_case_result(
        self,
        *,
        eval_run_id: UUID,
        eval_case_id: UUID,
        expected_run_revision: int,
        result_state: str,
        assertion_details: Mapping[str, Any] | None = None,
        actual_active_skills: Sequence[str] | None = None,
        visible_capability_aliases: Sequence[Any] | None = None,
        call_trace: Sequence[Any] | None = None,
        stop_reason: str | None = None,
        output_artifact_ids: Sequence[UUID] | None = None,
        evidence_artifact_ids: Sequence[UUID] | None = None,
        rounds: int | None = None,
        calls: int | None = None,
        tokens: int | None = None,
        latency_ms: int | None = None,
        safe_error: str | None = None,
        result_id: UUID | None = None,
    ) -> AssistantSkillEvalCaseResult:
        run = self._lock_run(eval_run_id, expected_run_revision)
        payload = {
            "result_state": result_state,
            "assertion_details": dict(assertion_details or {}),
            "actual_active_skills": list(actual_active_skills or []),
            "call_trace": list(call_trace or []),
            "stop_reason": stop_reason,
        }
        assert_payload_safe(payload.get("assertion_details"), context="assertion_details")
        digest = sha256_canonical_json(payload)
        row = AssistantSkillEvalCaseResult(
            id=result_id or uuid4(),
            eval_run_id=run.id,
            eval_case_id=eval_case_id,
            result_state=result_state,
            assertion_details=dict(assertion_details or {}),
            actual_active_skills=list(actual_active_skills or []),
            visible_capability_aliases=list(visible_capability_aliases or []),
            call_trace=list(call_trace or []),
            stop_reason=stop_reason,
            output_artifact_ids=_as_uuid_list(output_artifact_ids or ()),
            evidence_artifact_ids=_as_uuid_list(evidence_artifact_ids or ()),
            rounds=rounds,
            calls=calls,
            tokens=tokens,
            latency_ms=latency_ms,
            safe_error=safe_error,
            result_digest=digest,
            created_at=utcnow(),
        )
        self._add_unique(row, message="one result per run/case")
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return row

    def append_capability_call(
        self,
        *,
        eval_run_id: UUID,
        eval_case_id: UUID,
        expected_run_revision: int,
        logical_call_key: str,
        attempt: int,
        subject_kind: str,
        subject_aggregate_id: UUID,
        subject_version_id: UUID,
        subject_owner_digest: str,
        binding_digest: str,
        input_digest: str,
        descriptor_digest: str,
        policy_digest: str,
        outcome: EvalCapabilityOutcome,
        decision_json: Mapping[str, Any] | None = None,
        parent_ordinal: int | None = None,
        child_ordinal: int = 0,
        eval_call_id: UUID | None = None,
        owner_kind: str = EVAL_OWNER_KIND,
    ) -> AssistantSkillEvalCapabilityCall:
        if owner_kind != EVAL_OWNER_KIND:
            raise EvaluationRepositoryError(
                CODE_OWNERSHIP,
                "Eval CapabilityCall owner_kind must be 'test'",
            )
        run = self._lock_run(eval_run_id, expected_run_revision)
        call_id = eval_call_id or uuid4()
        row = AssistantSkillEvalCapabilityCall(
            id=uuid4(),
            eval_call_id=call_id,
            eval_run_id=run.id,
            eval_case_id=eval_case_id,
            logical_call_key=logical_call_key,
            parent_ordinal=parent_ordinal,
            child_ordinal=int(child_ordinal),
            attempt=int(attempt),
            owner_kind=EVAL_OWNER_KIND,
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
            subject_owner_digest=_require_sha256(
                subject_owner_digest, field="subject_owner_digest"
            ),
            binding_digest=_require_sha256(binding_digest, field="binding_digest"),
            input_digest=_require_sha256(input_digest, field="input_digest"),
            descriptor_digest=_require_sha256(
                descriptor_digest, field="descriptor_digest"
            ),
            policy_digest=_require_sha256(policy_digest, field="policy_digest"),
            outcome=outcome,
            decision_json=dict(decision_json or {}),
            created_at=utcnow(),
        )
        self._add_unique(row, message="unique eval_run/case/logical_call_key/attempt")
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return row

    def append_event(
        self,
        *,
        eval_run_id: UUID,
        expected_run_revision: int,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        event_id: UUID | None = None,
    ) -> AssistantSkillEvalEvent:
        run = self._lock_run(eval_run_id, expected_run_revision)
        assert_payload_safe(payload, context="eval_event.payload")
        next_seq = int(run.last_event_seq) + 1
        row = AssistantSkillEvalEvent(
            id=event_id or uuid4(),
            eval_run_id=run.id,
            sequence=next_seq,
            event_type=event_type,
            payload=dict(payload or {}),
            created_at=utcnow(),
        )
        self._add_unique(row, message="unique run/sequence")
        run.last_event_seq = next_seq
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return row

    def append_artifact(
        self,
        *,
        eval_run_id: UUID,
        expected_run_revision: int,
        kind: str,
        media_type: str,
        payload: bytes | None = None,
        object_key: str | None = None,
        content_digest: str | None = None,
        byte_size: int | None = None,
        label: str | None = None,
        metadata_json: Mapping[str, Any] | None = None,
        artifact_id: UUID | None = None,
    ) -> AssistantSkillEvalArtifact:
        run = self._lock_run(eval_run_id, expected_run_revision)
        try:
            storage = resolve_artifact_storage(
                eval_run_id=run.id,
                payload=payload,
                object_key=object_key,
            )
        except ValueError as exc:
            msg = str(exc)
            code = CODE_NAMESPACE if "skill-eval" in msg or "evaluation" in msg else CODE_INVALID_INPUT
            raise EvaluationRepositoryError(code, msg) from exc
        if storage["storage_kind"] == "object":
            if content_digest is None or byte_size is None:
                raise EvaluationRepositoryError(
                    CODE_INVALID_INPUT,
                    "object artifacts require content_digest and byte_size",
                )
            digest = _require_sha256(content_digest, field="content_digest")
            size = int(byte_size)
            if size < 0:
                raise EvaluationRepositoryError(CODE_INVALID_INPUT, "byte_size < 0")
            key = storage["object_key"]
            if not is_evaluation_object_key(key):
                raise EvaluationRepositoryError(
                    CODE_NAMESPACE, "object_key must be evaluation-namespace"
                )
            inline = None
            storage_kind = "object"
        else:
            digest = storage["content_digest"]
            size = storage["byte_size"]
            key = None
            inline = storage["inline_payload"]
            storage_kind = "inline"

        row = AssistantSkillEvalArtifact(
            id=artifact_id or uuid4(),
            eval_run_id=run.id,
            kind=kind,
            media_type=media_type,
            label=label,
            byte_size=size,
            content_digest=digest,
            storage_kind=storage_kind,
            inline_payload=inline,
            object_key=key,
            metadata_json=dict(metadata_json or {}),
            created_at=utcnow(),
        )
        self._add_unique(row, message="artifact content uniqueness")
        run.state_revision = int(run.state_revision) + 1
        self.session.flush()
        return row

    # ------------------------------------------------------------------
    # Publish gates / gate uses / retention
    # ------------------------------------------------------------------

    def append_publish_gate(
        self,
        *,
        subject_kind: EvalSubjectKind,
        subject_aggregate_id: UUID,
        subject_version_id: UUID,
        subject_content_digest: str,
        subject_binding_digest: str,
        profile_digest: str,
        catalog_digest: str,
        dataset_version_ids: Sequence[UUID],
        qualifying_eval_run_ids: Sequence[UUID],
        runtime_contract_version: int,
        policy_version: str,
        threshold_version: str,
        build_revision: str,
        decision: PublishGateDecision,
        assertion_snapshot: Mapping[str, Any] | None = None,
        metric_snapshot: Mapping[str, Any] | None = None,
        actor_principal: str | None = None,
        reason: str | None = None,
        waiver_codes: Sequence[str] | None = None,
        expires_at: datetime,
        request_id: str | None = None,
        gate_id: UUID | None = None,
    ) -> AssistantSkillPublishGate:
        # Server-derived decision only — callers (PublishGateService in Task 5)
        # recompute evidence; repository still refuses client-shaped blanks.
        if decision not in ("passed", "failed", "waived_non_safety"):
            raise EvaluationRepositoryError(CODE_INVALID_INPUT, "invalid decision")
        if not qualifying_eval_run_ids:
            raise EvaluationRepositoryError(
                CODE_INVALID_INPUT, "qualifying_eval_run_ids required"
            )
        assert_payload_safe(assertion_snapshot, context="assertion_snapshot")
        row = AssistantSkillPublishGate(
            id=gate_id or uuid4(),
            subject_kind=subject_kind,
            subject_aggregate_id=subject_aggregate_id,
            subject_version_id=subject_version_id,
            subject_content_digest=_require_sha256(
                subject_content_digest, field="subject_content_digest"
            ),
            subject_binding_digest=_require_sha256(
                subject_binding_digest, field="subject_binding_digest"
            ),
            profile_digest=_require_sha256(profile_digest, field="profile_digest"),
            catalog_digest=_require_sha256(catalog_digest, field="catalog_digest"),
            dataset_version_ids=_as_uuid_list(dataset_version_ids),
            qualifying_eval_run_ids=_as_uuid_list(qualifying_eval_run_ids),
            runtime_contract_version=int(runtime_contract_version),
            policy_version=policy_version,
            threshold_version=threshold_version,
            build_revision=build_revision,
            decision=decision,
            assertion_snapshot=dict(assertion_snapshot or {}),
            metric_snapshot=dict(metric_snapshot or {}),
            actor_principal=actor_principal,
            reason=reason,
            waiver_codes=list(waiver_codes or []),
            created_at=utcnow(),
            expires_at=expires_at,
            publication_pin_count=0,
            request_id=request_id,
        )
        self._add_unique(row, message="gate request_id uniqueness")
        return row

    def append_gate_use(
        self,
        *,
        gate_id: UUID,
        action: PublishGateAction,
        aggregate_id: UUID,
        resulting_version_id: UUID,
        actor_principal: str,
        request_id: str,
        aggregate_revision: int,
        use_id: UUID | None = None,
    ) -> AssistantSkillPublishGateUse:
        """Append a publication use row. Does not mutate the immutable gate row.

        Pinning is derived from existence of gate_use rows (not publication_pin_count).
        """
        gate = self.session.execute(
            select(AssistantSkillPublishGate)
            .where(AssistantSkillPublishGate.id == gate_id)
            .with_for_update()
        ).scalar_one_or_none()
        if gate is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "publish gate not found")
        # Idempotent retry: same request_id + action returns existing.
        existing = self.session.execute(
            select(AssistantSkillPublishGateUse).where(
                AssistantSkillPublishGateUse.request_id == request_id,
                AssistantSkillPublishGateUse.action == action,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = AssistantSkillPublishGateUse(
            id=use_id or uuid4(),
            gate_id=gate.id,
            action=action,
            aggregate_id=aggregate_id,
            resulting_version_id=resulting_version_id,
            actor_principal=actor_principal,
            request_id=request_id,
            aggregate_revision=int(aggregate_revision),
            created_at=utcnow(),
        )
        # Insert use only — never UPDATE the immutable gate (including pin count).
        self._add_unique(row, message="gate-use request/action uniqueness")
        return row

    def _gate_has_publication_use(self, gate_id: UUID) -> bool:
        """True when any gate_use row pins this gate (EXISTS, not pin_count column)."""
        return bool(
            self.session.execute(
                select(
                    exists().where(AssistantSkillPublishGateUse.gate_id == gate_id)
                )
            ).scalar()
        )

    def is_gate_evidence_pinned(
        self,
        gate: AssistantSkillPublishGate,
        *,
        now: datetime | None = None,
        grace_days: int = DEFAULT_GATE_EVIDENCE_GRACE_DAYS,
    ) -> bool:
        """Pin when publication-used (gate_use exists) OR within grace after expiry."""
        if self._gate_has_publication_use(gate.id):
            # Publication-used evidence remains pinned even after gate expiry.
            return True
        current = _as_utc(now or utcnow())
        # SQLite may return naive expires_at; normalize before compare.
        grace_end = _as_utc(gate.expires_at) + timedelta(days=int(grace_days))
        return current <= grace_end

    def _count_pinned_referencing_gates(
        self,
        eval_run_id: UUID,
        *,
        now: datetime,
        grace_days: int,
    ) -> int:
        """Scan gates referencing ``eval_run_id``, FOR UPDATE lock them, count pins.

        JSON array membership is checked in Python (portable across SQLite/PG).
        Callers re-invoke immediately before delete so a gate created after the
        initial snapshot is either locked+seen or causes cleanup to skip.
        """
        gates = list(
            self.session.execute(select(AssistantSkillPublishGate)).scalars().all()
        )
        referencing = [
            g
            for g in gates
            if str(eval_run_id)
            in [str(x) for x in (g.qualifying_eval_run_ids or [])]
        ]
        skipped = 0
        for gate in referencing:
            locked = self.session.execute(
                select(AssistantSkillPublishGate)
                .where(AssistantSkillPublishGate.id == gate.id)
                .with_for_update()
            ).scalar_one()
            if self.is_gate_evidence_pinned(
                locked, now=now, grace_days=grace_days
            ):
                skipped += 1
        return skipped

    def _cleanup_after_candidates_hook(self) -> None:
        """Test-only seam: no-op in production.

        Invoked after building the candidate delete set and before the final
        gate re-scan so unit tests can inject a concurrent ``append_publish_gate``
        (SQLite demonstrates recheck logic; full concurrent PG race is skippable).
        """
        return None

    def cleanup_unreferenced_evidence(
        self,
        *,
        eval_run_id: UUID,
        grace_days: int = DEFAULT_GATE_EVIDENCE_GRACE_DAYS,
        now: datetime | None = None,
    ) -> RetentionCleanupResult:
        """Delete only unreferenced high-volume events/non-assertion Artifacts.

        Locks/rechecks gate and publication references in the same transaction.
        Races against gate creation *and* consumption: after the candidate delete
        set is built, gates are re-scanned and FOR UPDATE locked so a concurrent
        ``append_publish_gate`` wins and keeps evidence. Pinning is derived from
        gate_use existence (not publication_pin_count).
        """
        current = now or utcnow()
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == eval_run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "eval run not found")

        # Initial pin check (consumption race: existing gates locked via FOR UPDATE).
        skipped = self._count_pinned_referencing_gates(
            eval_run_id, now=current, grace_days=grace_days
        )
        if skipped:
            return RetentionCleanupResult(
                deleted_events=0, deleted_artifacts=0, skipped_pinned=skipped
            )

        # Only delete high-volume events and non-assertion artifacts when unreferenced.
        events = list(
            self.session.execute(
                select(AssistantSkillEvalEvent).where(
                    AssistantSkillEvalEvent.eval_run_id == eval_run_id
                )
            )
            .scalars()
            .all()
        )
        artifacts = list(
            self.session.execute(
                select(AssistantSkillEvalArtifact).where(
                    AssistantSkillEvalArtifact.eval_run_id == eval_run_id
                )
            )
            .scalars()
            .all()
        )
        # Assertion-referenced artifacts are retained (kind startswith assertion).
        deletable_artifacts = [
            a for a in artifacts if not str(a.kind).startswith("assertion")
        ]

        # Creation race: concurrent append_publish_gate may insert after the
        # initial snapshot. Re-scan/re-lock immediately before delete.
        self._cleanup_after_candidates_hook()
        skipped = self._count_pinned_referencing_gates(
            eval_run_id, now=current, grace_days=grace_days
        )
        if skipped:
            return RetentionCleanupResult(
                deleted_events=0, deleted_artifacts=0, skipped_pinned=skipped
            )

        for event in events:
            self.session.delete(event)
        for artifact in deletable_artifacts:
            self.session.delete(artifact)
        self.session.flush()
        return RetentionCleanupResult(
            deleted_events=len(events),
            deleted_artifacts=len(deletable_artifacts),
            skipped_pinned=0,
        )

    # ------------------------------------------------------------------
    # Guards / helpers
    # ------------------------------------------------------------------

    def _require_dataset(self, dataset_id: UUID) -> AssistantSkillEvalDataset:
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "dataset not found")
        return dataset


    def _add_unique(self, row: object, *, message: str, code: str = CODE_CONFLICT) -> None:
        """Insert row under a savepoint; map IntegrityError to EvaluationRepositoryError."""
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            raise EvaluationRepositoryError(code, message) from exc

    def _lock_run(
        self, run_id: UUID, expected_revision: int
    ) -> AssistantSkillEvalRun:
        run = self.session.execute(
            select(AssistantSkillEvalRun)
            .where(AssistantSkillEvalRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise EvaluationRepositoryError(CODE_NOT_FOUND, "eval run not found")
        if int(run.state_revision) != int(expected_revision):
            raise EvaluationRepositoryError(
                CODE_STALE_REVISION,
                f"expected revision {expected_revision}, got {run.state_revision}",
            )
        return run

    def assert_no_production_ledger_fk(self) -> None:
        """Architecture helper: eval capability call table has no production FKs."""
        table = AssistantSkillEvalCapabilityCall.__table__
        for fk in table.foreign_keys:
            target = f"{fk.column.table.name}.{fk.column.name}"
            if fk.column.table.name in {
                "assistant_capability_call",
                "assistant_chat_run",
                "assistant_run_artifact",
                "assistant_chat_run_event",
            }:
                raise EvaluationRepositoryError(
                    CODE_NAMESPACE,
                    f"eval capability call must not FK production table {target}",
                )


def gate_evidence_grace_days(settings: Any | None = None) -> int:
    """Read ASSISTANT_SKILL_GATE_EVIDENCE_GRACE_DAYS from settings or default."""
    if settings is None:
        try:
            from app.config import get_settings

            settings = get_settings()
        except Exception:
            return DEFAULT_GATE_EVIDENCE_GRACE_DAYS
    value = getattr(settings, "assistant_skill_gate_evidence_grace_days", None)
    if value is None:
        return DEFAULT_GATE_EVIDENCE_GRACE_DAYS
    return max(0, int(value))
