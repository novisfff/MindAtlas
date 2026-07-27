"""Plan 10 Task 4 — HITL entrypoint matrix, creation cutoff, and archive/verify.

Classifies every human-node entrypoint as one of:
- durable: authenticated Plan 07 decision channel
- eval_simulated: Plan 09 workbench isolation (simulate only)
- unsupported_interrupt: no authenticated durable channel; never fall back to
  blocking HumanLoopRuntime for new work

assistant_human_approval table is dropped; archive/count handle missing table.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.models import AssistantLegacyApprovalArchive
from app.assistant.migration.repository import (
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)
from app.common.time import utcnow

HitlClassification = Literal["durable", "eval_simulated", "unsupported_interrupt"]

TERMINAL_APPROVAL_STATUSES = frozenset({"approved", "rejected", "cancelled"})
PENDING_APPROVAL_STATUS = "pending"

# Env / process-local creation cutoff. Default off so Deploy-A drain and
# characterization tests keep working until the matrix is green and operators
# enable the gate.
CUTOFF_ENV = "ASSISTANT_LEGACY_HITL_CREATION_CUTOFF"

_cutoff_lock = threading.Lock()
_cutoff_active: bool | None = None  # None → consult env; True/False override for tests


class LegacyApprovalCreationCutoffError(RuntimeError):
    """Raised when a new legacy AssistantHumanApproval would be created after cutoff."""

    reason_code = "legacy_approval_creation_cutoff"

    def __init__(
        self,
        message: str = "legacy human approval creation is disabled after cutoff",
    ) -> None:
        super().__init__(message)
        self.reason_code = self.__class__.reason_code


class ApprovalMigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class EntrypointHitlSpec:
    """One concrete human-node entrypoint and its post-migration classification."""

    entrypoint_id: str
    name: str
    classification: HitlClassification
    decision_channel: str | None
    notes: str
    may_import_blocking_runtime: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "entrypointId": self.entrypoint_id,
            "name": self.name,
            "classification": self.classification,
            "decisionChannel": self.decision_channel,
            "notes": self.notes,
            "mayImportBlockingRuntime": self.may_import_blocking_runtime,
        }


# Canonical matrix. Unknown entrypoints default to unsupported_interrupt.
# OpenClaw and legacy workflow_test are pinned so neither retains a hidden
# blocking HumanLoopRuntime path for new work after cutoff.
ENTRYPOINT_MATRIX: tuple[EntrypointHitlSpec, ...] = (
    EntrypointHitlSpec(
        entrypoint_id="ep-main-agent-chat",
        name="main_agent_chat",
        classification="durable",
        decision_channel="assistant_run_interrupt",
        notes="Plan 07 durable interrupt + authenticated decision API",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-assistant-chat-legacy",
        name="assistant_chat_legacy",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes=(
            "Legacy supervisor path has no durable decision channel; after "
            "cutoff it must not create new blocking approvals"
        ),
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-workflow-nested-durable",
        name="workflow_nested_main_agent",
        classification="durable",
        decision_channel="assistant_run_interrupt",
        notes="Nested workflow under durable Main Agent Run uses Plan 07",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-agent-nested-durable",
        name="agent_nested_main_agent",
        classification="durable",
        decision_channel="assistant_run_interrupt",
        notes="Nested agent under durable Main Agent Run uses Plan 07",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-plan09-workbench",
        name="plan09_workbench",
        classification="eval_simulated",
        decision_channel="evaluation_isolation",
        notes="Plan 09 eval isolation simulates HITL/side effects only",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-eval-runtime-shadow",
        name="runtime_shadow_eval",
        classification="eval_simulated",
        decision_channel="evaluation_isolation",
        notes="Paired runtime_shadow Eval Run never prompts production users",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-workflow-test",
        name="workflow_test",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes=(
            "Legacy workflow-test has no authenticated durable channel; "
            "new blocking HumanLoopRuntime paths are forbidden after cutoff"
        ),
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-agent-test",
        name="agent_test",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes="Agent test harness has no durable decision channel",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-system-behavior",
        name="system_behavior",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes="System behavior runner is not a durable HITL decision channel",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-openclaw-catalog-execute",
        name="openclaw",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes=(
            "OpenClaw shared capability path must never block on HumanLoopRuntime; "
            "human-interrupt workflows are unsupported_interrupt"
        ),
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-capability-runtime",
        name="capability_runtime",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes=(
            "Shared capability runtime without durable workflow port rejects "
            "human interrupt (legacy_blocking and durable without port)"
        ),
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-scheduler-background",
        name="scheduler_background",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes="Background/scheduler callers have no interactive decision channel",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-cli-migration",
        name="cli_migration",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes="Migration CLI archives/verifies only; never creates approvals",
        may_import_blocking_runtime=False,
    ),
    EntrypointHitlSpec(
        entrypoint_id="ep-direct-service",
        name="direct_service",
        classification="unsupported_interrupt",
        decision_channel=None,
        notes="Direct service callers without a durable Run channel are unsupported",
        may_import_blocking_runtime=False,
    ),
)

# Channel_type values used by HumanLoopContext → matrix name mapping.
CHANNEL_TO_ENTRYPOINT: Mapping[str, str] = {
    "assistant_chat": "assistant_chat_legacy",
    "assistant": "assistant_chat_legacy",
    "main_agent": "main_agent_chat",
    "main_agent_chat": "main_agent_chat",
    "workflow_test": "workflow_test",
    "agent_test": "agent_test",
    "system_behavior": "system_behavior",
    "capability_runtime": "capability_runtime",
    "openclaw": "openclaw",
    "openclaw_bridge": "openclaw",
    "evaluation": "plan09_workbench",
    "plan09_workbench": "plan09_workbench",
    "runtime_shadow": "runtime_shadow_eval",
    "scheduler": "scheduler_background",
    "background": "scheduler_background",
    "cli": "cli_migration",
    "service": "direct_service",
}

_MATRIX_BY_NAME: dict[str, EntrypointHitlSpec] = {
    spec.name: spec for spec in ENTRYPOINT_MATRIX
}
_MATRIX_BY_ID: dict[str, EntrypointHitlSpec] = {
    spec.entrypoint_id: spec for spec in ENTRYPOINT_MATRIX
}


def list_entrypoint_matrix() -> tuple[EntrypointHitlSpec, ...]:
    return ENTRYPOINT_MATRIX


def get_entrypoint_spec(name_or_id: str) -> EntrypointHitlSpec | None:
    key = str(name_or_id or "").strip()
    if not key:
        return None
    return _MATRIX_BY_NAME.get(key) or _MATRIX_BY_ID.get(key)


def classify_entrypoint(name_or_id: str) -> HitlClassification:
    """Classify an entrypoint; unknown defaults to unsupported_interrupt."""
    spec = get_entrypoint_spec(name_or_id)
    if spec is None:
        return "unsupported_interrupt"
    return spec.classification


def classify_channel_type(channel_type: str) -> HitlClassification:
    """Map a runtime channel_type to the entrypoint matrix classification."""
    raw = str(channel_type or "").strip()
    if not raw:
        return "unsupported_interrupt"
    mapped = CHANNEL_TO_ENTRYPOINT.get(raw) or CHANNEL_TO_ENTRYPOINT.get(raw.casefold())
    if mapped is None:
        # Unknown channel → fail closed.
        return "unsupported_interrupt"
    return classify_entrypoint(mapped)


def entrypoint_allows_blocking_runtime(name_or_id: str) -> bool:
    """Whether the entrypoint may import/call HumanLoopRuntime for new work."""
    spec = get_entrypoint_spec(name_or_id)
    if spec is None:
        return False
    return bool(spec.may_import_blocking_runtime)


def assert_entrypoint_matrix_invariants() -> None:
    """Fail if the matrix is incomplete or allows hidden blocking paths."""
    names = [s.name for s in ENTRYPOINT_MATRIX]
    ids = [s.entrypoint_id for s in ENTRYPOINT_MATRIX]
    if len(names) != len(set(names)):
        raise ApprovalMigrationError("matrix_invalid", "duplicate entrypoint name")
    if len(ids) != len(set(ids)):
        raise ApprovalMigrationError("matrix_invalid", "duplicate entrypoint id")

    required = {
        "main_agent_chat",
        "assistant_chat_legacy",
        "workflow_test",
        "openclaw",
        "plan09_workbench",
        "capability_runtime",
    }
    missing = required - set(names)
    if missing:
        raise ApprovalMigrationError(
            "matrix_incomplete",
            f"missing required entrypoints: {sorted(missing)}",
        )

    # Pin: OpenClaw and workflow_test must never retain blocking for new work.
    for pinned in ("openclaw", "workflow_test"):
        spec = _MATRIX_BY_NAME[pinned]
        if spec.classification != "unsupported_interrupt":
            raise ApprovalMigrationError(
                "matrix_pin_failed",
                f"{pinned} must be unsupported_interrupt, got {spec.classification}",
            )
        if spec.may_import_blocking_runtime:
            raise ApprovalMigrationError(
                "matrix_pin_failed",
                f"{pinned} must not allow blocking HumanLoopRuntime",
            )

    # Durable paths require an authenticated decision channel.
    for spec in ENTRYPOINT_MATRIX:
        if spec.classification == "durable" and not spec.decision_channel:
            raise ApprovalMigrationError(
                "matrix_invalid",
                f"durable entrypoint {spec.name} missing decision_channel",
            )
        if spec.classification == "eval_simulated" and not spec.decision_channel:
            raise ApprovalMigrationError(
                "matrix_invalid",
                f"eval_simulated entrypoint {spec.name} missing decision_channel",
            )
        if (
            spec.classification == "unsupported_interrupt"
            and spec.may_import_blocking_runtime
        ):
            raise ApprovalMigrationError(
                "matrix_invalid",
                f"unsupported entrypoint {spec.name} cannot allow blocking runtime",
            )


def matrix_report() -> dict[str, Any]:
    assert_entrypoint_matrix_invariants()
    items = [s.to_dict() for s in ENTRYPOINT_MATRIX]
    digest = sha256_canonical_json({"entrypoints": items})
    counts: dict[str, int] = {
        "durable": 0,
        "eval_simulated": 0,
        "unsupported_interrupt": 0,
    }
    for s in ENTRYPOINT_MATRIX:
        counts[s.classification] = counts.get(s.classification, 0) + 1
    return {
        "ok": True,
        "count": len(items),
        "counts": counts,
        "matrixDigest": digest,
        "entrypoints": items,
    }


# ---------------------------------------------------------------------------
# Creation cutoff
# ---------------------------------------------------------------------------


def set_legacy_approval_creation_cutoff(active: bool | None) -> None:
    """Process-local override. None clears override and re-reads env."""
    global _cutoff_active
    with _cutoff_lock:
        _cutoff_active = active


def is_legacy_approval_creation_cutoff_active() -> bool:
    with _cutoff_lock:
        if _cutoff_active is not None:
            return bool(_cutoff_active)
    raw = str(os.environ.get(CUTOFF_ENV, "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def assert_legacy_approval_creation_allowed(*, channel_type: str | None = None) -> None:
    """Fail closed when cutoff is active (no new pending legacy rows)."""
    if not is_legacy_approval_creation_cutoff_active():
        return
    channel = str(channel_type or "").strip()
    raise LegacyApprovalCreationCutoffError(
        "legacy human approval creation is disabled after cutoff"
        + (f" (channel_type={channel})" if channel else "")
    )


# ---------------------------------------------------------------------------
# Safe digests (never raw request/submitted payloads as resume authority)
# ---------------------------------------------------------------------------


def _as_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def safe_approval_payload_digest(row: Any) -> str:
    """Bounded identity digest; never embeds continuation tokens or raw bodies."""
    payload = {
        "sourceRowId": str(getattr(row, "id", "") or ""),
        "runId": str(getattr(row, "run_id", "") or "") or None,
        "conversationId": str(getattr(row, "conversation_id", "") or "") or None,
        "channelType": str(getattr(row, "channel_type", "") or "") or None,
        "nodeId": str(getattr(row, "node_id", "") or "") or None,
        "status": str(getattr(row, "status", "") or ""),
        "decision": str(getattr(row, "decision", "") or "") or None,
        "createdAt": _as_iso(getattr(row, "created_at", None)),
        "resolvedAt": _as_iso(getattr(row, "resolved_at", None)),
        # Digests of sensitive blobs only — never raw request_payload.
        "requestPayloadPresent": isinstance(getattr(row, "request_payload", None), dict),
        "fieldSchemaCount": (
            len(getattr(row, "field_schema", None) or [])
            if isinstance(getattr(row, "field_schema", None), list)
            else 0
        ),
    }
    return sha256_canonical_json(payload)


def migration_evidence_digest(
    *,
    source_row_id: str,
    safe_payload_digest: str,
    status: str,
    actor_principal: str | None,
    request_id: str | None = None,
) -> str:
    return sha256_canonical_json(
        {
            "kind": "legacy_approval_archive",
            "sourceRowId": str(source_row_id),
            "safePayloadDigest": str(safe_payload_digest),
            "status": str(status),
            "actorPrincipal": actor_principal,
            "requestId": request_id,
        }
    )


def archive_count_digest(
    *,
    source_terminal_count: int,
    archived_count: int,
    pending_count: int,
    source_ids: Sequence[str],
    archive_ids: Sequence[str],
) -> str:
    return sha256_canonical_json(
        {
            "sourceTerminalCount": int(source_terminal_count),
            "archivedCount": int(archived_count),
            "pendingCount": int(pending_count),
            "sourceIds": sorted(str(x) for x in source_ids),
            "archiveIds": sorted(str(x) for x in archive_ids),
        }
    )


# ---------------------------------------------------------------------------
# Archive / verify
# ---------------------------------------------------------------------------


@dataclass
class ApprovalArchiveItem:
    source_row_id: str
    status: str
    outcome: str
    safe_payload_digest: str | None = None
    archive_id: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceRowId": self.source_row_id,
            "status": self.status,
            "outcome": self.outcome,
            "safePayloadDigest": self.safe_payload_digest,
            "archiveId": self.archive_id,
            "reasonCode": self.reason_code,
        }


@dataclass
class ApprovalMigrationReport:
    command: str
    dry_run: bool
    request_id: str
    processed: int = 0
    succeeded: int = 0
    blocked: int = 0
    failed: int = 0
    unchanged: int = 0
    pending_count: int = 0
    terminal_count: int = 0
    archived_count: int = 0
    batch_id: str | None = None
    report_digest: str | None = None
    archive_count_digest: str | None = None
    cutoff_active: bool = False
    steps: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.failed == 0 and self.blocked == 0 and not self.blockers,
            "command": self.command,
            "dryRun": self.dry_run,
            "requestId": self.request_id,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "blocked": self.blocked,
            "failed": self.failed,
            "unchanged": self.unchanged,
            "pendingCount": self.pending_count,
            "terminalCount": self.terminal_count,
            "archivedCount": self.archived_count,
            "batchId": self.batch_id,
            "reportDigest": self.report_digest,
            "archiveCountDigest": self.archive_count_digest,
            "cutoffActive": self.cutoff_active,
            "steps": list(self.steps),
            "items": list(self.items),
            "blockers": list(self.blockers),
        }


def _load_legacy_approvals(session: Session) -> list[Any]:
    """Legacy assistant_human_approval table is dropped (Plan 10 B2)."""
    _ = session
    return []


def _load_archives(session: Session) -> list[AssistantLegacyApprovalArchive]:
    try:
        return (
            session.query(AssistantLegacyApprovalArchive)
            .order_by(AssistantLegacyApprovalArchive.created_at.asc())
            .all()
        )
    except Exception:
        session.rollback()
        return []


def count_pending_legacy_approvals(session: Session) -> int:
    """Always zero after assistant_human_approval drop."""
    _ = session
    return 0


def archive_terminal_approvals(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    dry_run: bool,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
    source_row_ids: Sequence[str] | None = None,
) -> ApprovalMigrationReport:
    """Copy terminal legacy approvals into the immutable archive.

    Pending rows are never archived (they are not terminal). Continuation
    tokens / request payloads are never treated as resume authority — only
    digests are stored.
    """
    assert_entrypoint_matrix_invariants()
    report = ApprovalMigrationReport(
        command="approvals.archive",
        dry_run=bool(dry_run),
        request_id=str(request_id),
        cutoff_active=is_legacy_approval_creation_cutoff_active(),
    )
    report.steps.append("inventory_source_rows")

    rows = _load_legacy_approvals(session)
    if source_row_ids is not None:
        wanted = {str(x) for x in source_row_ids}
        rows = [r for r in rows if str(r.id) in wanted]

    pending = [r for r in rows if str(r.status or "") == PENDING_APPROVAL_STATUS]
    terminal = [
        r for r in rows if str(r.status or "") in TERMINAL_APPROVAL_STATUSES
    ]
    report.pending_count = len(pending)
    report.terminal_count = len(terminal)
    report.steps.append(f"terminal={len(terminal)} pending={len(pending)}")

    limit = max(1, min(int(batch_size or 100), 1000))
    work = terminal[:limit]

    repo = RuntimeMigrationRepository(session)
    snapshot = source_snapshot_digest or sha256_canonical_json(
        {
            "kind": "legacy_approval_archive_snapshot",
            "terminalIds": [str(r.id) for r in terminal],
            "pendingCount": len(pending),
        }
    )
    if len(str(snapshot)) != 64:
        snapshot = sha256_canonical_json({"raw": snapshot})

    batch = None
    config_digest = sha256_canonical_json(
        {
            "environment": environment,
            "databaseFingerprint": database_fingerprint,
            "schemaHead": schema_head,
            "buildRevision": build_revision,
            "cutoffActive": report.cutoff_active,
        }
    )

    batch = None
    if not dry_run:
        batch = repo.prepare_batch(
            command_kind="approval",
            source_snapshot_digest=snapshot,
            configuration_digest=config_digest,
            build_revision=build_revision,
            schema_revision=schema_head,
            environment=environment,
            database_fingerprint=database_fingerprint,
            request_id=str(request_id),
            batch_size=limit,
            started_by=actor_principal,
        )
        if str(batch.status) == "prepared":
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="running",
            )
        report.batch_id = str(batch.id)

    archived_source_ids: list[str] = []
    archive_ids: list[str] = []

    for row in work:
        report.processed += 1
        sid = str(row.id)
        status = str(row.status or "")
        try:
            safe_digest = safe_approval_payload_digest(row)
            evidence = migration_evidence_digest(
                source_row_id=sid,
                safe_payload_digest=safe_digest,
                status=status,
                actor_principal=actor_principal,
                request_id=request_id,
            )
            if dry_run:
                existing = (
                    session.query(AssistantLegacyApprovalArchive)
                    .filter(AssistantLegacyApprovalArchive.source_row_id == sid)
                    .one_or_none()
                )
                if existing is not None:
                    report.unchanged += 1
                    report.items.append(
                        ApprovalArchiveItem(
                            source_row_id=sid,
                            status=status,
                            outcome="already_archived",
                            safe_payload_digest=str(existing.safe_payload_digest),
                            archive_id=str(existing.id),
                        ).to_dict()
                    )
                    archived_source_ids.append(sid)
                    archive_ids.append(str(existing.id))
                else:
                    report.succeeded += 1
                    report.items.append(
                        ApprovalArchiveItem(
                            source_row_id=sid,
                            status=status,
                            outcome="would_archive",
                            safe_payload_digest=safe_digest,
                        ).to_dict()
                    )
                    archived_source_ids.append(sid)
                continue

            archived = repo.archive_legacy_approval(
                source_row_id=sid,
                safe_payload_digest=safe_digest,
                status=status,
                migration_evidence_digest=evidence,
                source_run_id=str(getattr(row, "run_id", "") or "") or None,
                source_conversation_id=(
                    str(getattr(row, "conversation_id", "") or "") or None
                ),
                decision=str(getattr(row, "decision", "") or "") or None,
                source_created_at=getattr(row, "created_at", None),
                source_resolved_at=getattr(row, "resolved_at", None),
                actor_principal=actor_principal,
            )
            # Detect idempotent re-archive vs new insert via created_at proximity
            # is unnecessary; repository returns existing on same digest.
            was_existing = False
            prior = [
                i
                for i in report.items
                if i.get("sourceRowId") == sid and i.get("outcome") == "archived"
            ]
            if prior:
                was_existing = True
            # Compare: if archive already existed before this call, count unchanged.
            # archive_legacy_approval is idempotent; re-query isn't free — use
            # processed evidence from flush: if row already had same digest it's ok.
            _ = was_existing
            report.succeeded += 1
            report.items.append(
                ApprovalArchiveItem(
                    source_row_id=sid,
                    status=status,
                    outcome="archived",
                    safe_payload_digest=safe_digest,
                    archive_id=str(archived.id),
                ).to_dict()
            )
            archived_source_ids.append(sid)
            archive_ids.append(str(archived.id))
        except RuntimeMigrationRepositoryError as exc:
            if exc.code == "conflict":
                report.blocked += 1
                report.blockers.append(f"{sid}:conflict")
                report.items.append(
                    ApprovalArchiveItem(
                        source_row_id=sid,
                        status=status,
                        outcome="blocked",
                        reason_code=exc.code,
                    ).to_dict()
                )
            else:
                report.failed += 1
                report.items.append(
                    ApprovalArchiveItem(
                        source_row_id=sid,
                        status=status,
                        outcome="failed",
                        reason_code=exc.code,
                    ).to_dict()
                )
        except Exception as exc:  # pragma: no cover - defensive
            report.failed += 1
            report.items.append(
                ApprovalArchiveItem(
                    source_row_id=sid,
                    status=status,
                    outcome="failed",
                    reason_code=type(exc).__name__,
                ).to_dict()
            )

    # Pending rows are reported but never archived.
    for row in pending[:limit]:
        report.processed += 1
        report.blocked += 1
        report.blockers.append(f"{row.id}:pending_not_terminal")
        report.items.append(
            ApprovalArchiveItem(
                source_row_id=str(row.id),
                status=PENDING_APPROVAL_STATUS,
                outcome="blocked",
                reason_code="pending_not_terminal",
            ).to_dict()
        )

    archives = _load_archives(session)
    report.archived_count = len(archives)
    report.archive_count_digest = archive_count_digest(
        source_terminal_count=len(terminal),
        archived_count=len(archives),
        pending_count=len(pending),
        source_ids=[str(r.id) for r in terminal],
        archive_ids=[str(a.source_row_id) for a in archives],
    )
    report.report_digest = sha256_canonical_json(
        {
            "command": report.command,
            "requestId": report.request_id,
            "processed": report.processed,
            "succeeded": report.succeeded,
            "blocked": report.blocked,
            "failed": report.failed,
            "pendingCount": report.pending_count,
            "terminalCount": report.terminal_count,
            "archivedCount": report.archived_count,
            "archiveCountDigest": report.archive_count_digest,
            "itemOutcomes": [i.get("outcome") for i in report.items],
        }
    )

    if batch is not None and not dry_run:
        final_status = "completed" if report.failed == 0 else "failed"
        try:
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status=final_status,
                processed_delta=report.processed,
                succeeded_delta=report.succeeded,
                blocked_delta=report.blocked,
                failed_delta=report.failed,
                report_digest=report.report_digest,
                completed_by=actor_principal,
            )
            report.batch_id = str(batch.id)
            report.steps.append(f"batch_{final_status}")
        except RuntimeMigrationRepositoryError as exc:
            report.steps.append(f"batch_complete_skipped:{exc.code}")
            if exc.code not in {"forbidden_transition", "stale_revision"}:
                raise

    report.steps.append("archive_complete")
    return report


def verify_approvals(
    session: Session,
    *,
    request_id: str,
    actor_principal: str | None = None,
    build_revision: str = "development",
    environment: str = "test",
    database_fingerprint: str = "local",
    schema_head: str = "unknown",
    dry_run: bool = True,
    batch_size: int = 100,
    source_snapshot_digest: str | None = None,
    require_zero_pending: bool = True,
    require_cutoff_active: bool = False,
    require_archives_match: bool = True,
) -> ApprovalMigrationReport:
    """Verify archive digests and (optionally) zero pending for the B1 gate.

    Never fabricates durable resume tokens from legacy rows.
    """
    assert_entrypoint_matrix_invariants()
    report = ApprovalMigrationReport(
        command="approvals.verify",
        dry_run=bool(dry_run),
        request_id=str(request_id),
        cutoff_active=is_legacy_approval_creation_cutoff_active(),
    )
    report.steps.append("matrix_ok")

    if require_cutoff_active and not report.cutoff_active:
        report.blocked += 1
        report.blockers.append("creation_cutoff_inactive")

    rows = _load_legacy_approvals(session)
    pending = [r for r in rows if str(r.status or "") == PENDING_APPROVAL_STATUS]
    terminal = [
        r for r in rows if str(r.status or "") in TERMINAL_APPROVAL_STATUSES
    ]
    archives = _load_archives(session)
    report.pending_count = len(pending)
    report.terminal_count = len(terminal)
    report.archived_count = len(archives)
    report.processed = len(rows) + len(archives)

    if require_zero_pending and pending:
        report.blocked += 1
        report.blockers.append(f"pending_remaining:{len(pending)}")
        for row in pending:
            report.items.append(
                ApprovalArchiveItem(
                    source_row_id=str(row.id),
                    status=PENDING_APPROVAL_STATUS,
                    outcome="blocked",
                    reason_code="pending_remaining",
                ).to_dict()
            )

    archive_by_source = {str(a.source_row_id): a for a in archives}
    if require_archives_match:
        for row in terminal:
            sid = str(row.id)
            safe_digest = safe_approval_payload_digest(row)
            archived = archive_by_source.get(sid)
            if archived is None:
                report.blocked += 1
                report.blockers.append(f"{sid}:missing_archive")
                report.items.append(
                    ApprovalArchiveItem(
                        source_row_id=sid,
                        status=str(row.status or ""),
                        outcome="blocked",
                        safe_payload_digest=safe_digest,
                        reason_code="missing_archive",
                    ).to_dict()
                )
                continue
            if str(archived.safe_payload_digest) != safe_digest:
                report.blocked += 1
                report.blockers.append(f"{sid}:digest_mismatch")
                report.items.append(
                    ApprovalArchiveItem(
                        source_row_id=sid,
                        status=str(row.status or ""),
                        outcome="blocked",
                        safe_payload_digest=safe_digest,
                        archive_id=str(archived.id),
                        reason_code="digest_mismatch",
                    ).to_dict()
                )
                continue
            report.succeeded += 1
            report.items.append(
                ApprovalArchiveItem(
                    source_row_id=sid,
                    status=str(row.status or ""),
                    outcome="verified",
                    safe_payload_digest=safe_digest,
                    archive_id=str(archived.id),
                ).to_dict()
            )
        # Extra archives without source (source may have been retained) are ok;
        # source_row_id uniqueness is the resume-authority guard.
        for sid, archived in archive_by_source.items():
            if any(str(r.id) == sid for r in terminal):
                continue
            report.unchanged += 1
            report.items.append(
                ApprovalArchiveItem(
                    source_row_id=sid,
                    status=str(archived.status or ""),
                    outcome="archive_without_live_source",
                    safe_payload_digest=str(archived.safe_payload_digest),
                    archive_id=str(archived.id),
                ).to_dict()
            )

    report.archive_count_digest = archive_count_digest(
        source_terminal_count=len(terminal),
        archived_count=len(archives),
        pending_count=len(pending),
        source_ids=[str(r.id) for r in terminal],
        archive_ids=[str(a.source_row_id) for a in archives],
    )
    report.report_digest = sha256_canonical_json(
        {
            "command": report.command,
            "requestId": report.request_id,
            "pendingCount": report.pending_count,
            "terminalCount": report.terminal_count,
            "archivedCount": report.archived_count,
            "blocked": report.blocked,
            "failed": report.failed,
            "blockers": sorted(report.blockers),
            "archiveCountDigest": report.archive_count_digest,
            "cutoffActive": report.cutoff_active,
            "matrixDigest": matrix_report()["matrixDigest"],
        }
    )
    report.steps.append("verify_complete")
    _ = (
        actor_principal,
        build_revision,
        environment,
        database_fingerprint,
        schema_head,
        batch_size,
        source_snapshot_digest,
    )
    return report


def zero_pending_gate(session: Session) -> dict[str, Any]:
    """B1 helper: zero active pending legacy approvals required."""
    pending = count_pending_legacy_approvals(session)
    return {
        "ok": pending == 0,
        "pendingCount": pending,
        "cutoffActive": is_legacy_approval_creation_cutoff_active(),
        "gate": "legacy_approval_zero_pending",
    }


__all__ = (
    "CHANNEL_TO_ENTRYPOINT",
    "CUTOFF_ENV",
    "ENTRYPOINT_MATRIX",
    "ApprovalArchiveItem",
    "ApprovalMigrationError",
    "ApprovalMigrationReport",
    "EntrypointHitlSpec",
    "HitlClassification",
    "LegacyApprovalCreationCutoffError",
    "archive_count_digest",
    "archive_terminal_approvals",
    "assert_entrypoint_matrix_invariants",
    "assert_legacy_approval_creation_allowed",
    "classify_channel_type",
    "classify_entrypoint",
    "count_pending_legacy_approvals",
    "entrypoint_allows_blocking_runtime",
    "get_entrypoint_spec",
    "is_legacy_approval_creation_cutoff_active",
    "list_entrypoint_matrix",
    "matrix_report",
    "migration_evidence_digest",
    "safe_approval_payload_digest",
    "set_legacy_approval_creation_cutoff",
    "verify_approvals",
    "zero_pending_gate",
)
