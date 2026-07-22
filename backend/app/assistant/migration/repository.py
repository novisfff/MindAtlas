"""Runtime migration evidence repository (Plan 10 Task 1).

One repository owns all Plan 10 evidence writes with expected-revision CAS and
append-only enforcement. Production traffic routing is not owned here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.models import (
    ROLLOUT_CONTROL_SINGLETON_ID,
    ROLLOUT_CONTROL_SINGLETON_KEY,
    AssistantLegacyApprovalArchive,
    AssistantRuntimeAdmissionFallbackEvent,
    AssistantRuntimeCleanupGate,
    AssistantRuntimeMigrationBatch,
    AssistantRuntimeMigrationEvent,
    AssistantRuntimeMigrationItem,
    AssistantRuntimeRolloutAssignment,
    AssistantRuntimeRolloutControl,
    AssistantRuntimeRolloutEvent,
    AssistantRuntimeRolloutRevision,
    AssistantRuntimeShadowComparison,
)
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
CODE_PRECONDITION = "precondition_failed"
CODE_DRIFT = "drift"

ALLOWED_ITEM_TRANSITIONS: dict[tuple[str, str], str] = {
    ("discovered", "mapped"): "map",
    ("discovered", "blocked"): "block",
    ("discovered", "archived"): "archive",
    ("mapped", "migrated"): "migrate",
    ("mapped", "blocked"): "block",
    ("mapped", "archived"): "archive",
    ("migrated", "verified"): "verify",
    ("migrated", "blocked"): "block",
    ("migrated", "archived"): "archive",
    ("verified", "archived"): "archive",
    ("blocked", "discovered"): "rediscover",
    ("blocked", "mapped"): "remap",
    ("blocked", "archived"): "archive",
}

ALLOWED_BATCH_TRANSITIONS: dict[tuple[str, str], str] = {
    ("prepared", "running"): "start",
    ("prepared", "cancelled"): "cancel",
    ("running", "completed"): "complete",
    ("running", "failed"): "fail",
    ("running", "cancelled"): "cancel",
}

ITEM_STATES = frozenset(
    {"discovered", "mapped", "migrated", "verified", "blocked", "archived"}
)
SUBJECT_KINDS = frozenset(
    {
        "skill",
        "profile",
        "alias",
        "l2_memory",
        "approval",
        "entrypoint",
        "package",
        "write_branch",
    }
)
COMMAND_KINDS = frozenset({"inventory", "package", "l2", "approval", "verify"})
SAFE_EVIDENCE_MAX_KEYS = 32
SAFE_EVIDENCE_MAX_STR = 512
SAFE_EVIDENCE_MAX_DEPTH = 3


class RuntimeMigrationRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DiscoveryBackfillResult:
    created: int
    unchanged: int
    drifted: int
    batch_id: UUID | None
    report_digest: str


def _require_sha256(value: str, *, field: str) -> str:
    text = (value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT, f"{field} must be 64-char lowercase hex digest"
        )
    return text


def _nullable_sha256(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _require_sha256(text, field=field)


def _bounded_safe_json(
    payload: Mapping[str, Any] | None,
    *,
    field: str = "evidence_json",
    depth: int = 0,
) -> dict[str, Any]:
    """Accept only bounded digests/ids/counts/reason codes — no raw content."""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT, f"{field} must be an object"
        )
    if depth > SAFE_EVIDENCE_MAX_DEPTH:
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT, f"{field} exceeds max depth {SAFE_EVIDENCE_MAX_DEPTH}"
        )
    if len(payload) > SAFE_EVIDENCE_MAX_KEYS:
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT, f"{field} exceeds max keys {SAFE_EVIDENCE_MAX_KEYS}"
        )
    forbidden_markers = (
        "prompt",
        "password",
        "secret",
        "credential",
        "api_key",
        "apikey",
        "token",
        "authorization",
        "raw_",
        "system_prompt",
        "memory_fact",
        "payload_body",
    )
    out: dict[str, Any] = {}
    for key, value in payload.items():
        key_s = str(key)
        if len(key_s) > 64:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"{field} key too long"
            )
        lower = key_s.lower()
        if any(m in lower for m in forbidden_markers):
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"{field} rejects key {key_s!r}"
            )
        if value is None or isinstance(value, (bool, int)):
            out[key_s] = value
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise RuntimeMigrationRepositoryError(
                    CODE_INVALID_INPUT, f"{field}.{key_s} non-finite float"
                )
            out[key_s] = value
        elif isinstance(value, str):
            if len(value) > SAFE_EVIDENCE_MAX_STR:
                raise RuntimeMigrationRepositoryError(
                    CODE_INVALID_INPUT,
                    f"{field}.{key_s} exceeds {SAFE_EVIDENCE_MAX_STR} chars",
                )
            out[key_s] = value
        elif isinstance(value, Mapping):
            out[key_s] = _bounded_safe_json(
                value, field=f"{field}.{key_s}", depth=depth + 1
            )
        elif isinstance(value, (list, tuple)):
            if len(value) > SAFE_EVIDENCE_MAX_KEYS:
                raise RuntimeMigrationRepositoryError(
                    CODE_INVALID_INPUT, f"{field}.{key_s} list too long"
                )
            items: list[Any] = []
            for i, item in enumerate(value):
                if item is None or isinstance(item, (bool, int, float, str)):
                    if isinstance(item, str) and len(item) > SAFE_EVIDENCE_MAX_STR:
                        raise RuntimeMigrationRepositoryError(
                            CODE_INVALID_INPUT,
                            f"{field}.{key_s}[{i}] exceeds max str",
                        )
                    items.append(item)
                elif isinstance(item, Mapping):
                    items.append(
                        _bounded_safe_json(
                            item, field=f"{field}.{key_s}[{i}]", depth=depth + 1
                        )
                    )
                else:
                    raise RuntimeMigrationRepositoryError(
                        CODE_INVALID_INPUT,
                        f"{field}.{key_s}[{i}] unsupported type",
                    )
            out[key_s] = items
        else:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"{field}.{key_s} unsupported type"
            )
    return out


def _digest_of(payload: Mapping[str, Any]) -> str:
    return sha256_canonical_json(dict(payload))


class RuntimeMigrationRepository:
    """Single writer for Plan 10 migration/rollout evidence tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Migration items
    # ------------------------------------------------------------------

    def upsert_discovered_item(
        self,
        *,
        subject_kind: str,
        source_type: str,
        source_id: str,
        source_name: str,
        source_name_normalized: str,
        source_digest: str,
        evidence_json: Mapping[str, Any] | None = None,
        actor_principal: str | None = None,
        build_revision: str | None = None,
        reason_code: str | None = None,
    ) -> tuple[AssistantRuntimeMigrationItem, str]:
        """Idempotent discovered-only upsert.

        Returns ``(item, outcome)`` where outcome is one of
        ``created | unchanged | drifted``.
        Drift appends a blocker event and never silently remaps/verifies.
        """
        if subject_kind not in SUBJECT_KINDS:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid subject_kind: {subject_kind}"
            )
        digest = _require_sha256(source_digest, field="source_digest")
        safe = _bounded_safe_json(evidence_json)
        existing = self.session.execute(
            select(AssistantRuntimeMigrationItem)
            .where(AssistantRuntimeMigrationItem.subject_kind == subject_kind)
            .where(AssistantRuntimeMigrationItem.source_type == source_type)
            .where(AssistantRuntimeMigrationItem.source_id == str(source_id))
            .with_for_update()
        ).scalar_one_or_none()

        if existing is None:
            item = AssistantRuntimeMigrationItem(
                id=uuid4(),
                subject_kind=subject_kind,
                source_type=source_type,
                source_id=str(source_id),
                source_name=str(source_name or "")[:256],
                source_name_normalized=str(source_name_normalized or "")[:256],
                source_digest=digest,
                state="discovered",
                reason_code=reason_code,
                evidence_json=safe,
                source_revision=0,
                target_revision=0,
                attempt_count=0,
                state_revision=0,
                actor_principal=actor_principal,
                build_revision=build_revision,
            )
            self.session.add(item)
            self.session.flush()
            self._append_item_event(
                item=item,
                previous_state=None,
                new_state="discovered",
                evidence_digest=_digest_of(
                    {
                        "sourceDigest": digest,
                        "subjectKind": subject_kind,
                        "sourceId": str(source_id),
                        "outcome": "created",
                    }
                ),
                safe_details={"outcome": "created"},
                actor_principal=actor_principal,
                build_revision=build_revision,
                revision=1,
            )
            item.state_revision = 1
            self.session.flush()
            return item, "created"

        if str(existing.source_digest) == digest:
            return existing, "unchanged"

        # Drift: block and never remap/verify silently.
        prev = str(existing.state)
        existing.state = "blocked"
        existing.reason_code = "source_digest_drift"
        existing.evidence_json = {
            **(existing.evidence_json or {}),
            "previousSourceDigest": str(existing.source_digest),
            "observedSourceDigest": digest,
        }
        existing.source_digest = digest
        existing.source_name = str(source_name or existing.source_name)[:256]
        existing.source_name_normalized = str(
            source_name_normalized or existing.source_name_normalized
        )[:256]
        existing.attempt_count = int(existing.attempt_count) + 1
        new_rev = int(existing.state_revision) + 1
        existing.state_revision = new_rev
        existing.actor_principal = actor_principal
        existing.build_revision = build_revision
        self.session.flush()
        self._append_item_event(
            item=existing,
            previous_state=prev,
            new_state="blocked",
            evidence_digest=_digest_of(
                {
                    "previousSourceDigest": existing.evidence_json.get(
                        "previousSourceDigest"
                    ),
                    "observedSourceDigest": digest,
                    "outcome": "drifted",
                }
            ),
            safe_details={
                "outcome": "drifted",
                "reasonCode": "source_digest_drift",
            },
            actor_principal=actor_principal,
            build_revision=build_revision,
            revision=new_rev,
        )
        self.session.flush()
        return existing, "drifted"

    def get_item(self, item_id: UUID) -> AssistantRuntimeMigrationItem | None:
        return self.session.get(AssistantRuntimeMigrationItem, item_id)

    def get_item_by_source(
        self, *, subject_kind: str, source_type: str, source_id: str
    ) -> AssistantRuntimeMigrationItem | None:
        return self.session.execute(
            select(AssistantRuntimeMigrationItem)
            .where(AssistantRuntimeMigrationItem.subject_kind == subject_kind)
            .where(AssistantRuntimeMigrationItem.source_type == source_type)
            .where(AssistantRuntimeMigrationItem.source_id == str(source_id))
        ).scalar_one_or_none()

    def transition_item(
        self,
        *,
        item_id: UUID,
        expected_revision: int,
        to_state: str,
        reason_code: str | None = None,
        evidence_json: Mapping[str, Any] | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        target_version: str | None = None,
        target_digest: str | None = None,
        actor_principal: str | None = None,
        build_revision: str | None = None,
    ) -> AssistantRuntimeMigrationItem:
        if to_state not in ITEM_STATES:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid to_state: {to_state}"
            )
        item = self.session.execute(
            select(AssistantRuntimeMigrationItem)
            .where(AssistantRuntimeMigrationItem.id == item_id)
            .with_for_update()
        ).scalar_one_or_none()
        if item is None:
            raise RuntimeMigrationRepositoryError(CODE_NOT_FOUND, "migration item not found")
        if int(item.state_revision) != int(expected_revision):
            raise RuntimeMigrationRepositoryError(
                CODE_STALE_REVISION,
                f"expected revision {expected_revision}, got {item.state_revision}",
            )
        prev = str(item.state)
        if prev == to_state:
            return item
        if (prev, to_state) not in ALLOWED_ITEM_TRANSITIONS:
            raise RuntimeMigrationRepositoryError(
                CODE_FORBIDDEN_TRANSITION,
                f"item transition {prev} -> {to_state} not allowed",
            )
        safe = _bounded_safe_json(evidence_json)
        item.state = to_state
        if reason_code is not None:
            item.reason_code = reason_code
        if safe:
            item.evidence_json = {**(item.evidence_json or {}), **safe}
        if target_type is not None:
            item.target_type = target_type
        if target_id is not None:
            item.target_id = target_id
        if target_version is not None:
            item.target_version = target_version
        if target_digest is not None:
            item.target_digest = _require_sha256(target_digest, field="target_digest")
        item.attempt_count = int(item.attempt_count) + 1
        new_rev = int(item.state_revision) + 1
        item.state_revision = new_rev
        item.actor_principal = actor_principal
        item.build_revision = build_revision
        if to_state == "verified":
            item.verified_at = utcnow()
        self.session.flush()
        self._append_item_event(
            item=item,
            previous_state=prev,
            new_state=to_state,
            evidence_digest=_digest_of(
                {
                    "itemId": str(item.id),
                    "previousState": prev,
                    "newState": to_state,
                    "reasonCode": reason_code,
                    "revision": new_rev,
                }
            ),
            safe_details={
                "reasonCode": reason_code,
                "transition": ALLOWED_ITEM_TRANSITIONS[(prev, to_state)],
            },
            actor_principal=actor_principal,
            build_revision=build_revision,
            revision=new_rev,
        )
        self.session.flush()
        return item

    def _append_item_event(
        self,
        *,
        item: AssistantRuntimeMigrationItem,
        previous_state: str | None,
        new_state: str,
        evidence_digest: str,
        safe_details: Mapping[str, Any],
        actor_principal: str | None,
        build_revision: str | None,
        revision: int,
    ) -> AssistantRuntimeMigrationEvent:
        event = AssistantRuntimeMigrationEvent(
            id=uuid4(),
            migration_item_id=item.id,
            revision=int(revision),
            previous_state=previous_state,
            new_state=new_state,
            evidence_digest=_require_sha256(evidence_digest, field="evidence_digest"),
            safe_details=_bounded_safe_json(safe_details, field="safe_details"),
            actor_principal=actor_principal,
            build_revision=build_revision,
            created_at=utcnow(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    def list_item_events(
        self, item_id: UUID
    ) -> list[AssistantRuntimeMigrationEvent]:
        return list(
            self.session.execute(
                select(AssistantRuntimeMigrationEvent)
                .where(AssistantRuntimeMigrationEvent.migration_item_id == item_id)
                .order_by(AssistantRuntimeMigrationEvent.revision.asc())
            )
            .scalars()
            .all()
        )

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------

    def prepare_batch(
        self,
        *,
        command_kind: str,
        source_snapshot_digest: str,
        configuration_digest: str,
        build_revision: str,
        schema_revision: str,
        environment: str,
        database_fingerprint: str,
        request_id: str,
        batch_size: int = 100,
        dry_run_digest: str | None = None,
        started_by: str | None = None,
        batch_id: UUID | None = None,
    ) -> AssistantRuntimeMigrationBatch:
        if command_kind not in COMMAND_KINDS:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid command_kind: {command_kind}"
            )
        if not (1 <= int(batch_size) <= 1000):
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, "batch_size must be 1..1000"
            )
        req = str(request_id or "").strip()
        if not req or len(req) > 128:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, "request_id required (1..128)"
            )
        existing = self.session.execute(
            select(AssistantRuntimeMigrationBatch).where(
                AssistantRuntimeMigrationBatch.request_id == req
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Identical request retry returns the same batch; altered reuse conflicts.
            if (
                str(existing.command_kind) != command_kind
                or str(existing.source_snapshot_digest)
                != _require_sha256(source_snapshot_digest, field="source_snapshot_digest")
                or str(existing.configuration_digest)
                != _require_sha256(configuration_digest, field="configuration_digest")
                or str(existing.build_revision) != str(build_revision)
                or str(existing.schema_revision) != str(schema_revision)
                or str(existing.environment) != str(environment)
                or str(existing.database_fingerprint) != str(database_fingerprint)
            ):
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    "request_id already bound to a different batch fingerprint",
                )
            return existing

        batch = AssistantRuntimeMigrationBatch(
            id=batch_id or uuid4(),
            command_kind=command_kind,
            source_snapshot_digest=_require_sha256(
                source_snapshot_digest, field="source_snapshot_digest"
            ),
            configuration_digest=_require_sha256(
                configuration_digest, field="configuration_digest"
            ),
            build_revision=str(build_revision),
            schema_revision=str(schema_revision),
            environment=str(environment),
            database_fingerprint=str(database_fingerprint),
            status="prepared",
            state_revision=0,
            batch_size=int(batch_size),
            request_id=req,
            started_by=started_by,
            dry_run_digest=_nullable_sha256(dry_run_digest, field="dry_run_digest"),
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    def get_batch(self, batch_id: UUID) -> AssistantRuntimeMigrationBatch | None:
        return self.session.get(AssistantRuntimeMigrationBatch, batch_id)

    def get_batch_by_request_id(
        self, request_id: str
    ) -> AssistantRuntimeMigrationBatch | None:
        return self.session.execute(
            select(AssistantRuntimeMigrationBatch).where(
                AssistantRuntimeMigrationBatch.request_id == str(request_id)
            )
        ).scalar_one_or_none()

    def transition_batch(
        self,
        *,
        batch_id: UUID,
        expected_revision: int,
        to_status: str,
        resume_cursor: str | None = None,
        processed_delta: int = 0,
        succeeded_delta: int = 0,
        blocked_delta: int = 0,
        failed_delta: int = 0,
        report_digest: str | None = None,
        report_artifact_id: UUID | None = None,
        completed_by: str | None = None,
    ) -> AssistantRuntimeMigrationBatch:
        batch = self.session.execute(
            select(AssistantRuntimeMigrationBatch)
            .where(AssistantRuntimeMigrationBatch.id == batch_id)
            .with_for_update()
        ).scalar_one_or_none()
        if batch is None:
            raise RuntimeMigrationRepositoryError(CODE_NOT_FOUND, "batch not found")
        if int(batch.state_revision) != int(expected_revision):
            raise RuntimeMigrationRepositoryError(
                CODE_STALE_REVISION,
                f"expected revision {expected_revision}, got {batch.state_revision}",
            )
        prev = str(batch.status)
        if prev != to_status:
            if (prev, to_status) not in ALLOWED_BATCH_TRANSITIONS:
                raise RuntimeMigrationRepositoryError(
                    CODE_FORBIDDEN_TRANSITION,
                    f"batch transition {prev} -> {to_status} not allowed",
                )
            batch.status = to_status
            if to_status == "running" and batch.started_at is None:
                batch.started_at = utcnow()
            if to_status in {"completed", "failed", "cancelled"}:
                batch.completed_at = utcnow()
                batch.completed_by = completed_by
        if resume_cursor is not None:
            batch.resume_cursor = str(resume_cursor)[:256]
        batch.processed_count = int(batch.processed_count) + max(0, int(processed_delta))
        batch.succeeded_count = int(batch.succeeded_count) + max(0, int(succeeded_delta))
        batch.blocked_count = int(batch.blocked_count) + max(0, int(blocked_delta))
        batch.failed_count = int(batch.failed_count) + max(0, int(failed_delta))
        if report_digest is not None:
            batch.report_digest = _require_sha256(report_digest, field="report_digest")
        if report_artifact_id is not None:
            batch.report_artifact_id = report_artifact_id
        batch.state_revision = int(batch.state_revision) + 1
        self.session.flush()
        return batch

    def resume_batch(
        self,
        *,
        batch_id: UUID,
        expected_revision: int,
        source_snapshot_digest: str,
        configuration_digest: str,
        build_revision: str,
        schema_revision: str,
    ) -> AssistantRuntimeMigrationBatch:
        """Resume requires identical command/source/config/build/schema digests."""
        batch = self.session.execute(
            select(AssistantRuntimeMigrationBatch)
            .where(AssistantRuntimeMigrationBatch.id == batch_id)
            .with_for_update()
        ).scalar_one_or_none()
        if batch is None:
            raise RuntimeMigrationRepositoryError(CODE_NOT_FOUND, "batch not found")
        if int(batch.state_revision) != int(expected_revision):
            raise RuntimeMigrationRepositoryError(
                CODE_STALE_REVISION,
                f"expected revision {expected_revision}, got {batch.state_revision}",
            )
        if (
            str(batch.source_snapshot_digest)
            != _require_sha256(source_snapshot_digest, field="source_snapshot_digest")
            or str(batch.configuration_digest)
            != _require_sha256(configuration_digest, field="configuration_digest")
            or str(batch.build_revision) != str(build_revision)
            or str(batch.schema_revision) != str(schema_revision)
        ):
            raise RuntimeMigrationRepositoryError(
                CODE_DRIFT,
                "batch resume digest drift; create a new batch",
            )
        if str(batch.status) not in {"prepared", "running"}:
            raise RuntimeMigrationRepositoryError(
                CODE_FORBIDDEN_TRANSITION,
                f"cannot resume batch in status {batch.status}",
            )
        return batch

    # ------------------------------------------------------------------
    # Rollout revision / control / assignment
    # ------------------------------------------------------------------

    def prepare_rollout_revision(
        self,
        *,
        revision_label: str,
        runtime_mode: str = "legacy",
        shadow_eligible_scope: str = "none",
        shadow_percent: int = 0,
        read_canary_percent: int = 0,
        write_mode: str = "off",
        write_percent: int = 0,
        eligible_closure_digest: str,
        config_origin: str = "native",
        build_revision: str,
        runtime_contract_version: int = 1,
        policy_contract_version: int = 1,
        worker_contract_version: int = 1,
        cohort_salt_fingerprint: str,
        config_json: Mapping[str, Any] | None = None,
        metric_definition_id: str | None = None,
        metric_window_id: str | None = None,
        approval_artifact_id: UUID | None = None,
        evidence_artifact_id: UUID | None = None,
        actor_principal: str | None = None,
        reason: str | None = None,
        revision_id: UUID | None = None,
    ) -> AssistantRuntimeRolloutRevision:
        label = str(revision_label or "").strip()
        if not label or len(label) > 128:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, "revision_label required (1..128)"
            )
        if runtime_mode not in {"legacy", "main_agent"}:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid runtime_mode: {runtime_mode}"
            )
        if config_origin not in {"native", "plan04_compat"}:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid config_origin: {config_origin}"
            )
        if config_origin == "plan04_compat":
            if (
                int(shadow_percent) != 0
                or int(read_canary_percent) != 0
                or int(write_percent) != 0
                or runtime_mode != "legacy"
            ):
                raise RuntimeMigrationRepositoryError(
                    CODE_INVALID_INPUT,
                    "plan04_compat requires zero shadow/canary/write and legacy mode",
                )
        for name, value in (
            ("shadow_percent", shadow_percent),
            ("read_canary_percent", read_canary_percent),
            ("write_percent", write_percent),
        ):
            if not (0 <= int(value) <= 100):
                raise RuntimeMigrationRepositoryError(
                    CODE_INVALID_INPUT, f"{name} must be 0..100"
                )
        safe_config = _bounded_safe_json(config_json, field="config_json")
        config_digest = _digest_of(
            {
                "revisionLabel": label,
                "runtimeMode": runtime_mode,
                "shadowEligibleScope": shadow_eligible_scope,
                "shadowPercent": int(shadow_percent),
                "readCanaryPercent": int(read_canary_percent),
                "writeMode": write_mode,
                "writePercent": int(write_percent),
                "eligibleClosureDigest": _require_sha256(
                    eligible_closure_digest, field="eligible_closure_digest"
                ),
                "configOrigin": config_origin,
                "buildRevision": build_revision,
                "cohortSaltFingerprint": _require_sha256(
                    cohort_salt_fingerprint, field="cohort_salt_fingerprint"
                ),
                "config": safe_config,
            }
        )
        existing = self.session.execute(
            select(AssistantRuntimeRolloutRevision).where(
                AssistantRuntimeRolloutRevision.revision_label == label
            )
        ).scalar_one_or_none()
        if existing is not None:
            if str(existing.config_digest) != config_digest:
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    "revision_label already exists with different config_digest",
                )
            return existing

        row = AssistantRuntimeRolloutRevision(
            id=revision_id or uuid4(),
            revision_label=label,
            runtime_mode=runtime_mode,
            shadow_eligible_scope=shadow_eligible_scope,
            shadow_percent=int(shadow_percent),
            read_canary_percent=int(read_canary_percent),
            write_mode=write_mode,
            write_percent=int(write_percent),
            eligible_closure_digest=_require_sha256(
                eligible_closure_digest, field="eligible_closure_digest"
            ),
            config_origin=config_origin,
            build_revision=str(build_revision),
            runtime_contract_version=int(runtime_contract_version),
            policy_contract_version=int(policy_contract_version),
            worker_contract_version=int(worker_contract_version),
            cohort_salt_fingerprint=_require_sha256(
                cohort_salt_fingerprint, field="cohort_salt_fingerprint"
            ),
            metric_definition_id=metric_definition_id,
            metric_window_id=metric_window_id,
            approval_artifact_id=approval_artifact_id,
            evidence_artifact_id=evidence_artifact_id,
            config_json=safe_config,
            config_digest=config_digest,
            actor_principal=actor_principal,
            reason=reason,
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        self._append_rollout_event(
            rollout_revision_id=row.id,
            action="prepared",
            previous_active_revision_id=None,
            control_revision=self._next_control_event_revision(),
            evidence_digest=config_digest,
            actor_principal=actor_principal,
            reason=reason,
        )
        return row

    def ensure_rollout_control(self) -> AssistantRuntimeRolloutControl:
        control = self.session.execute(
            select(AssistantRuntimeRolloutControl)
            .where(
                AssistantRuntimeRolloutControl.singleton_key
                == ROLLOUT_CONTROL_SINGLETON_KEY
            )
            .with_for_update()
        ).scalar_one_or_none()
        if control is not None:
            return control
        control = AssistantRuntimeRolloutControl(
            id=UUID(ROLLOUT_CONTROL_SINGLETON_ID),
            singleton_key=ROLLOUT_CONTROL_SINGLETON_KEY,
            active_rollout_revision_id=None,
            state_revision=0,
        )
        self.session.add(control)
        self.session.flush()
        return control

    def activate_rollout_revision(
        self,
        *,
        rollout_revision_id: UUID,
        expected_control_revision: int,
        actor_principal: str | None = None,
        reason: str | None = None,
        evidence_digest: str | None = None,
    ) -> AssistantRuntimeRolloutControl:
        revision = self.session.get(
            AssistantRuntimeRolloutRevision, rollout_revision_id
        )
        if revision is None:
            raise RuntimeMigrationRepositoryError(
                CODE_NOT_FOUND, "rollout revision not found"
            )
        control = self.ensure_rollout_control()
        if int(control.state_revision) != int(expected_control_revision):
            raise RuntimeMigrationRepositoryError(
                CODE_STALE_REVISION,
                f"expected control revision {expected_control_revision}, "
                f"got {control.state_revision}",
            )
        previous = control.active_rollout_revision_id
        if previous is not None and previous == rollout_revision_id:
            return control
        control_rev = max(
            int(control.state_revision) + 1, self._next_control_event_revision()
        )
        control.active_rollout_revision_id = rollout_revision_id
        control.state_revision = control_rev
        self.session.flush()
        dig = evidence_digest or _digest_of(
            {
                "action": "activated",
                "revisionId": str(rollout_revision_id),
                "previousActive": str(previous) if previous else None,
                "controlRevision": control_rev,
            }
        )
        dig = _require_sha256(dig, field="evidence_digest")
        if previous is not None:
            self._append_rollout_event(
                rollout_revision_id=previous,
                action="superseded",
                previous_active_revision_id=previous,
                control_revision=control_rev,
                evidence_digest=dig,
                actor_principal=actor_principal,
                reason=reason,
            )
        self._append_rollout_event(
            rollout_revision_id=rollout_revision_id,
            action="activated",
            previous_active_revision_id=previous,
            control_revision=control_rev,
            evidence_digest=dig,
            actor_principal=actor_principal,
            reason=reason,
        )
        return control

    def get_active_rollout_revision(
        self,
    ) -> AssistantRuntimeRolloutRevision | None:
        control = self.session.execute(
            select(AssistantRuntimeRolloutControl).where(
                AssistantRuntimeRolloutControl.singleton_key
                == ROLLOUT_CONTROL_SINGLETON_KEY
            )
        ).scalar_one_or_none()
        if control is None or control.active_rollout_revision_id is None:
            return None
        return self.session.get(
            AssistantRuntimeRolloutRevision, control.active_rollout_revision_id
        )

    def create_assignment(
        self,
        *,
        conversation_id: UUID,
        rollout_revision_id: UUID,
        assigned_runtime_kind: str,
        assignment_reason: str,
        cohort_key_digest: str,
        assigned_write_mode: str = "off",
        cohort: str = "default",
        principal_scope_digest: str | None = None,
        assignment_id: UUID | None = None,
    ) -> AssistantRuntimeRolloutAssignment:
        if assigned_runtime_kind not in {"legacy", "main_agent"}:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT,
                f"invalid assigned_runtime_kind: {assigned_runtime_kind}",
            )
        if assignment_reason not in {
            "hash",
            "staff",
            "explicit_override",
            "rollback",
        }:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid assignment_reason: {assignment_reason}"
            )
        revision = self.session.get(
            AssistantRuntimeRolloutRevision, rollout_revision_id
        )
        if revision is None:
            raise RuntimeMigrationRepositoryError(
                CODE_NOT_FOUND, "rollout revision not found"
            )
        existing = self.session.execute(
            select(AssistantRuntimeRolloutAssignment)
            .where(AssistantRuntimeRolloutAssignment.conversation_id == conversation_id)
            .where(
                AssistantRuntimeRolloutAssignment.rollout_revision_id
                == rollout_revision_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Immutable: same key returns existing; altered values conflict.
            if (
                str(existing.assigned_runtime_kind) != assigned_runtime_kind
                or str(existing.assigned_write_mode) != assigned_write_mode
                or str(existing.assignment_reason) != assignment_reason
                or str(existing.cohort_key_digest)
                != _require_sha256(cohort_key_digest, field="cohort_key_digest")
            ):
                raise RuntimeMigrationRepositoryError(
                    CODE_IMMUTABLE,
                    "rollout assignment is immutable after insert",
                )
            return existing
        row = AssistantRuntimeRolloutAssignment(
            id=assignment_id or uuid4(),
            conversation_id=conversation_id,
            principal_scope_digest=_nullable_sha256(
                principal_scope_digest, field="principal_scope_digest"
            ),
            rollout_revision_id=rollout_revision_id,
            cohort=str(cohort)[:64],
            assigned_runtime_kind=assigned_runtime_kind,
            assigned_write_mode=assigned_write_mode,
            assignment_reason=assignment_reason,
            cohort_key_digest=_require_sha256(
                cohort_key_digest, field="cohort_key_digest"
            ),
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _next_control_event_revision(self) -> int:
        last = self.session.execute(
            select(AssistantRuntimeRolloutEvent.control_revision)
            .order_by(AssistantRuntimeRolloutEvent.control_revision.desc())
            .limit(1)
        ).scalar_one_or_none()
        return int(last or 0) + 1

    def _append_rollout_event(
        self,
        *,
        rollout_revision_id: UUID,
        action: str,
        previous_active_revision_id: UUID | None,
        control_revision: int,
        evidence_digest: str,
        actor_principal: str | None,
        reason: str | None,
    ) -> AssistantRuntimeRolloutEvent:
        event = AssistantRuntimeRolloutEvent(
            id=uuid4(),
            rollout_revision_id=rollout_revision_id,
            action=action,
            previous_active_revision_id=previous_active_revision_id,
            control_revision=int(control_revision),
            actor_principal=actor_principal,
            reason=reason,
            evidence_digest=_require_sha256(evidence_digest, field="evidence_digest"),
            created_at=utcnow(),
        )
        self.session.add(event)
        self.session.flush()
        return event

    # ------------------------------------------------------------------
    # Admission fallback (schema + repository methods; full wiring later)
    # ------------------------------------------------------------------

    def record_admission_fallback(
        self,
        *,
        request_id: str,
        rollout_revision_id: UUID,
        resulting_legacy_run_id: UUID,
        admission_failure_digest: str,
        assignment_id: UUID | None = None,
        principal_scope_digest: str | None = None,
        build_revision: str | None = None,
        schema_revision: str | None = None,
        runtime_contract_version: int | None = None,
        event_id: UUID | None = None,
    ) -> AssistantRuntimeAdmissionFallbackEvent:
        """Append pre-insert fallback event. Caller must insert Legacy Run in same txn."""
        req = str(request_id or "").strip()
        if not req:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, "request_id required"
            )
        existing = self.session.execute(
            select(AssistantRuntimeAdmissionFallbackEvent).where(
                AssistantRuntimeAdmissionFallbackEvent.request_id == req
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.resulting_legacy_run_id != resulting_legacy_run_id
                or str(existing.admission_failure_digest)
                != _require_sha256(
                    admission_failure_digest, field="admission_failure_digest"
                )
                or existing.rollout_revision_id != rollout_revision_id
            ):
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    "request_id already bound to a different fallback event",
                )
            return existing
        row = AssistantRuntimeAdmissionFallbackEvent(
            id=event_id or uuid4(),
            request_id=req,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
            candidate_runtime_kind="main_agent",
            selected_runtime_kind="legacy",
            reason="preinsert_fallback",
            admission_failure_digest=_require_sha256(
                admission_failure_digest, field="admission_failure_digest"
            ),
            resulting_legacy_run_id=resulting_legacy_run_id,
            principal_scope_digest=_nullable_sha256(
                principal_scope_digest, field="principal_scope_digest"
            ),
            build_revision=build_revision,
            schema_revision=schema_revision,
            runtime_contract_version=runtime_contract_version,
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    # ------------------------------------------------------------------
    # Shadow comparison
    # ------------------------------------------------------------------

    def create_shadow_comparison(
        self,
        *,
        production_run_id: UUID,
        eval_run_id: UUID,
        input_digest: str,
        context_digest: str,
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
        private_input_snapshot_id: UUID | None = None,
        private_input_payload_digest: str | None = None,
        quality_assertion_snapshot: Mapping[str, Any] | None = None,
        comparison_id: UUID | None = None,
    ) -> AssistantRuntimeShadowComparison:
        existing = self.session.execute(
            select(AssistantRuntimeShadowComparison).where(
                AssistantRuntimeShadowComparison.eval_run_id == eval_run_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.production_run_id != production_run_id:
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT, "eval_run already paired with another production run"
                )
            return existing
        row = AssistantRuntimeShadowComparison(
            id=comparison_id or uuid4(),
            production_run_id=production_run_id,
            eval_run_id=eval_run_id,
            rollout_revision_id=rollout_revision_id,
            assignment_id=assignment_id,
            shadow_eligible=bool(shadow_eligible),
            input_digest=_require_sha256(input_digest, field="input_digest"),
            context_digest=_require_sha256(context_digest, field="context_digest"),
            fixture_digest=_nullable_sha256(fixture_digest, field="fixture_digest"),
            catalog_revision=catalog_revision,
            profile_revision=profile_revision,
            model_revision=model_revision,
            runtime_revision=runtime_revision,
            build_revision=build_revision,
            intent_class=intent_class,
            write_simulation_required=bool(write_simulation_required),
            quality_assertion_snapshot=_bounded_safe_json(
                quality_assertion_snapshot, field="quality_assertion_snapshot"
            ),
            private_input_snapshot_id=private_input_snapshot_id,
            private_input_payload_digest=_nullable_sha256(
                private_input_payload_digest, field="private_input_payload_digest"
            ),
            reviewer_state="pending",
            result_state="open",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_shadow_comparison(
        self, comparison_id: UUID
    ) -> AssistantRuntimeShadowComparison | None:
        return self.session.get(AssistantRuntimeShadowComparison, comparison_id)

    # ------------------------------------------------------------------
    # Legacy approval archive
    # ------------------------------------------------------------------

    def archive_legacy_approval(
        self,
        *,
        source_row_id: str,
        safe_payload_digest: str,
        status: str,
        migration_evidence_digest: str,
        source_run_id: str | None = None,
        source_conversation_id: str | None = None,
        decision: str | None = None,
        source_created_at: datetime | None = None,
        source_resolved_at: datetime | None = None,
        actor_principal: str | None = None,
        archive_id: UUID | None = None,
    ) -> AssistantLegacyApprovalArchive:
        sid = str(source_row_id or "").strip()
        if not sid:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, "source_row_id required"
            )
        existing = self.session.execute(
            select(AssistantLegacyApprovalArchive).where(
                AssistantLegacyApprovalArchive.source_row_id == sid
            )
        ).scalar_one_or_none()
        if existing is not None:
            if str(existing.safe_payload_digest) != _require_sha256(
                safe_payload_digest, field="safe_payload_digest"
            ):
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    "source_row_id already archived with different payload digest",
                )
            return existing
        row = AssistantLegacyApprovalArchive(
            id=archive_id or uuid4(),
            source_row_id=sid,
            source_run_id=source_run_id,
            source_conversation_id=source_conversation_id,
            safe_payload_digest=_require_sha256(
                safe_payload_digest, field="safe_payload_digest"
            ),
            status=str(status)[:64],
            decision=decision,
            source_created_at=source_created_at,
            source_resolved_at=source_resolved_at,
            migration_evidence_digest=_require_sha256(
                migration_evidence_digest, field="migration_evidence_digest"
            ),
            actor_principal=actor_principal,
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    # ------------------------------------------------------------------
    # Cleanup gates
    # ------------------------------------------------------------------

    def append_cleanup_gate(
        self,
        *,
        gate_kind: str,
        decision: str,
        schema_revision: str,
        build_revision: str,
        inventory_digest: str,
        evidence_digest: str,
        snapshot_counts: Mapping[str, Any] | None = None,
        runtime_revision: str | None = None,
        actor_principal: str | None = None,
        reason: str | None = None,
        migration_batch_digest: str | None = None,
        rollout_revision_digest: str | None = None,
        metric_window_digest: str | None = None,
        backup_restore_digest: str | None = None,
        legacy_access_window_digest: str | None = None,
        archive_count_digest: str | None = None,
        reconciliation_digest: str | None = None,
        expires_at: datetime | None = None,
        gate_id: UUID | None = None,
    ) -> AssistantRuntimeCleanupGate:
        if gate_kind not in {"deploy_b1", "deploy_b2"}:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid gate_kind: {gate_kind}"
            )
        if decision not in {"passed", "failed"}:
            raise RuntimeMigrationRepositoryError(
                CODE_INVALID_INPUT, f"invalid decision: {decision}"
            )
        row = AssistantRuntimeCleanupGate(
            id=gate_id or uuid4(),
            gate_kind=gate_kind,
            decision=decision,
            schema_revision=str(schema_revision),
            build_revision=str(build_revision),
            runtime_revision=runtime_revision,
            actor_principal=actor_principal,
            reason=reason,
            inventory_digest=_require_sha256(inventory_digest, field="inventory_digest"),
            migration_batch_digest=_nullable_sha256(
                migration_batch_digest, field="migration_batch_digest"
            ),
            rollout_revision_digest=_nullable_sha256(
                rollout_revision_digest, field="rollout_revision_digest"
            ),
            metric_window_digest=_nullable_sha256(
                metric_window_digest, field="metric_window_digest"
            ),
            backup_restore_digest=_nullable_sha256(
                backup_restore_digest, field="backup_restore_digest"
            ),
            legacy_access_window_digest=_nullable_sha256(
                legacy_access_window_digest, field="legacy_access_window_digest"
            ),
            archive_count_digest=_nullable_sha256(
                archive_count_digest, field="archive_count_digest"
            ),
            reconciliation_digest=_nullable_sha256(
                reconciliation_digest, field="reconciliation_digest"
            ),
            evidence_digest=_require_sha256(evidence_digest, field="evidence_digest"),
            snapshot_counts=_bounded_safe_json(
                snapshot_counts, field="snapshot_counts"
            ),
            expires_at=expires_at,
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row


__all__ = (
    "ALLOWED_BATCH_TRANSITIONS",
    "ALLOWED_ITEM_TRANSITIONS",
    "CODE_CONFLICT",
    "CODE_DRIFT",
    "CODE_FORBIDDEN_TRANSITION",
    "CODE_IMMUTABLE",
    "CODE_INVALID_INPUT",
    "CODE_NOT_FOUND",
    "CODE_PRECONDITION",
    "CODE_STALE_REVISION",
    "DiscoveryBackfillResult",
    "RuntimeMigrationRepository",
    "RuntimeMigrationRepositoryError",
)
