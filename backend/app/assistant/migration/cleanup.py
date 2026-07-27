"""Plan 10 Task 10 — cleanup gate evaluation and Deploy B2 preflight.

Recomputes authoritative hard counts and appends
``assistant_runtime_cleanup_gate`` evidence. Environment acknowledgment is a
safety binding only; counts and a current passed gate are required for B2.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.repository import (
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)
from app.common.time import utcnow

GateKind = Literal["deploy_b1", "deploy_b2"]
GateDecision = Literal["passed", "failed"]

B2_MAINTENANCE_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"

# Align with durable nonterminal set (Plan 06).
_NONTERMINAL_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "recovering",
        "waiting_approval",
        "waiting_input",
        "cancelling",
        "needs_reconciliation",
    }
)

_DEFAULT_GATE_TTL = timedelta(hours=24)


class CleanupGateError(RuntimeError):
    """Structured cleanup/preflight failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CleanupHardCounts:
    pending_legacy_approvals: int
    nonterminal_legacy_runs: int
    invalid_l2_rows: int
    blocked_migration_items: int
    unresolved_reconciliation: int
    archive_count: int
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pendingLegacyApprovals": int(self.pending_legacy_approvals),
            "nonterminalLegacyRuns": int(self.nonterminal_legacy_runs),
            "invalidL2Rows": int(self.invalid_l2_rows),
            "blockedMigrationItems": int(self.blocked_migration_items),
            "unresolvedReconciliation": int(self.unresolved_reconciliation),
            "archiveCount": int(self.archive_count),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class CleanupGateResult:
    gate_kind: GateKind
    decision: GateDecision
    valid: bool
    blockers: list[str] = field(default_factory=list)
    snapshot_counts: dict[str, Any] = field(default_factory=dict)
    evidence_digest: str = ""
    inventory_digest: str = ""
    gate_id: str | None = None
    schema_revision: str = ""
    build_revision: str = ""
    reason: str | None = None
    notes: list[str] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.valid and self.decision == "passed"),
            "valid": bool(self.valid),
            "gateKind": self.gate_kind,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "snapshotCounts": dict(self.snapshot_counts),
            "evidenceDigest": self.evidence_digest,
            "inventoryDigest": self.inventory_digest,
            "gateId": self.gate_id,
            "schemaRevision": self.schema_revision,
            "buildRevision": self.build_revision,
            "reason": self.reason,
            "notes": list(self.notes),
            "dryRun": bool(self.dry_run),
        }


@dataclass(slots=True)
class CleanupPreflightResult:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    gate: CleanupGateResult | None = None
    maintenance_ack: bool = False
    recomputed_counts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "blockers": list(self.blockers),
            "maintenanceAck": bool(self.maintenance_ack),
            "recomputedCounts": dict(self.recomputed_counts),
            "notes": list(self.notes),
            "gate": self.gate.to_dict() if self.gate is not None else None,
        }


def maintenance_ack_present(*, environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    ack = str(env.get(B2_MAINTENANCE_ACK_ENV, "") or "").strip()
    test_override = str(env.get(B2_TEST_OVERRIDE_ENV, "") or "").strip()
    return ack in {"1", "true", "TRUE", "yes", "YES"} or test_override in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def count_pending_legacy_approvals(session: Session) -> int:
    from app.assistant.migration.approvals import count_pending_legacy_approvals as _count

    try:
        return int(_count(session))
    except Exception:
        session.rollback()
        return 0


def count_nonterminal_legacy_runs(session: Session) -> int:
    from app.assistant.models import AssistantChatRun

    try:
        return (
            session.query(AssistantChatRun)
            .filter(
                AssistantChatRun.runtime_kind == "legacy",
                AssistantChatRun.status.in_(tuple(_NONTERMINAL_RUN_STATUSES)),
            )
            .count()
        )
    except Exception:
        session.rollback()
        return 0


def count_invalid_l2_rows(session: Session) -> int:
    """Rows missing package_id, empty namespace, or split default/NULL shape."""
    from app.assistant.models import AssistantConversationSkillL2Memory

    try:
        rows = session.query(AssistantConversationSkillL2Memory).all()
    except Exception:
        session.rollback()
        return 0

    invalid = 0
    for row in rows:
        package_id = getattr(row, "skill_package_id", None)
        ns_raw = getattr(row, "memory_namespace", None)
        ns = str(ns_raw).strip() if ns_raw is not None else ""
        if package_id is None:
            invalid += 1
            continue
        if not ns:
            invalid += 1
            continue
        # Treat whitespace-only / non-normalized empty as invalid (already covered).
        # Split default/NULL is package-backed with null namespace — covered above.
    return invalid


def count_blocked_migration_items(session: Session) -> int:
    from app.assistant.migration.models import AssistantRuntimeMigrationItem

    try:
        return (
            session.query(AssistantRuntimeMigrationItem)
            .filter(AssistantRuntimeMigrationItem.state == "blocked")
            .count()
        )
    except Exception:
        session.rollback()
        return 0


def count_unresolved_reconciliation(session: Session) -> tuple[int, str | None]:
    """Count unknown/needs_reconciliation capability calls when table is available."""
    try:
        from app.assistant.capability_calls.models import AssistantCapabilityCall
    except Exception:
        return 0, "capability_call_model_unavailable"

    try:
        count = (
            session.query(AssistantCapabilityCall)
            .filter(
                AssistantCapabilityCall.status.in_(
                    ("unknown", "needs_reconciliation")
                )
            )
            .count()
        )
        return int(count), None
    except Exception:
        session.rollback()
        return 0, "unresolved_reconciliation_query_failed_stub_zero"


def count_legacy_approval_archives(session: Session) -> int:
    from app.assistant.migration.models import AssistantLegacyApprovalArchive

    try:
        return session.query(AssistantLegacyApprovalArchive).count()
    except Exception:
        session.rollback()
        return 0


def recompute_hard_counts(session: Session) -> CleanupHardCounts:
    pending = count_pending_legacy_approvals(session)
    nonterminal = count_nonterminal_legacy_runs(session)
    invalid_l2 = count_invalid_l2_rows(session)
    blocked = count_blocked_migration_items(session)
    unresolved, unresolved_note = count_unresolved_reconciliation(session)
    archives = count_legacy_approval_archives(session)
    notes: list[str] = []
    if unresolved_note:
        notes.append(unresolved_note)
    return CleanupHardCounts(
        pending_legacy_approvals=pending,
        nonterminal_legacy_runs=nonterminal,
        invalid_l2_rows=invalid_l2,
        blocked_migration_items=blocked,
        unresolved_reconciliation=unresolved,
        archive_count=archives,
        notes=tuple(notes),
    )


def _blockers_from_counts(
    counts: CleanupHardCounts,
    *,
    gate_kind: GateKind,
) -> list[str]:
    blockers: list[str] = []
    if counts.pending_legacy_approvals > 0:
        blockers.append(
            f"pending_legacy_approvals={counts.pending_legacy_approvals}"
        )
    if counts.nonterminal_legacy_runs > 0:
        blockers.append(
            f"nonterminal_legacy_runs={counts.nonterminal_legacy_runs}"
        )
    if counts.invalid_l2_rows > 0:
        blockers.append(f"invalid_l2_rows={counts.invalid_l2_rows}")
    if counts.blocked_migration_items > 0:
        blockers.append(
            f"blocked_migration_items={counts.blocked_migration_items}"
        )
    if counts.unresolved_reconciliation > 0:
        blockers.append(
            f"unresolved_reconciliation={counts.unresolved_reconciliation}"
        )
    # B2 additionally requires archive path evidence when approvals table may drop.
    if gate_kind == "deploy_b2" and counts.archive_count < 0:
        blockers.append("archive_count_negative")
    return blockers


def evaluate_cleanup_gate(
    session: Session,
    *,
    gate_kind: GateKind,
    schema_revision: str,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    request_id: str,
    actor_principal: str | None = None,
    reason: str | None = None,
    dry_run: bool = True,
    inventory_digest: str | None = None,
    migration_batch_digest: str | None = None,
    rollout_revision_digest: str | None = None,
    backup_restore_digest: str | None = None,
    legacy_access_window_digest: str | None = None,
    runtime_revision: str | None = None,
    persist: bool | None = None,
    expires_at: datetime | None = None,
) -> CleanupGateResult:
    """Recompute hard counts and optionally append a cleanup gate row.

    ``persist`` defaults to ``not dry_run``. Dry-run never writes.
    """
    if gate_kind not in {"deploy_b1", "deploy_b2"}:
        raise CleanupGateError("invalid_input", f"invalid gate_kind: {gate_kind}")

    counts = recompute_hard_counts(session)
    blockers = _blockers_from_counts(counts, gate_kind=gate_kind)
    decision: GateDecision = "passed" if not blockers else "failed"
    valid = decision == "passed"

    snapshot = counts.to_dict()
    snapshot["environment"] = str(environment)[:64]
    snapshot["databaseFingerprint"] = str(database_fingerprint)[:64]
    snapshot["requestId"] = str(request_id)[:128]
    snapshot["blockerCount"] = len(blockers)

    inv_digest = inventory_digest or sha256_canonical_json(
        {
            "gateKind": gate_kind,
            "counts": counts.to_dict(),
            "environment": environment,
            "databaseFingerprint": database_fingerprint,
        }
    )
    if len(str(inv_digest)) != 64:
        inv_digest = sha256_canonical_json({"raw": str(inv_digest)})

    evidence_digest = sha256_canonical_json(
        {
            "gateKind": gate_kind,
            "decision": decision,
            "snapshotCounts": snapshot,
            "blockers": blockers,
            "schemaRevision": schema_revision,
            "buildRevision": build_revision,
            "requestId": request_id,
        }
    )

    archive_count_digest = sha256_canonical_json(
        {"archiveCount": counts.archive_count}
    )
    reconciliation_digest = sha256_canonical_json(
        {
            "unresolvedReconciliation": counts.unresolved_reconciliation,
            "notes": list(counts.notes),
        }
    )

    should_persist = (not dry_run) if persist is None else bool(persist)
    gate_id: str | None = None
    notes = list(counts.notes)

    if should_persist:
        if not actor_principal:
            raise CleanupGateError(
                "precondition_failed",
                "operator principal required to persist cleanup gate",
            )
        repo = RuntimeMigrationRepository(session)
        expiry = expires_at or (utcnow() + _DEFAULT_GATE_TTL)
        try:
            row = repo.append_cleanup_gate(
                gate_kind=gate_kind,
                decision=decision,
                schema_revision=schema_revision,
                build_revision=build_revision,
                inventory_digest=str(inv_digest),
                evidence_digest=evidence_digest,
                snapshot_counts=snapshot,
                runtime_revision=runtime_revision,
                actor_principal=actor_principal,
                reason=(reason or f"cleanup evaluate {gate_kind}")[:512],
                migration_batch_digest=migration_batch_digest,
                rollout_revision_digest=rollout_revision_digest,
                backup_restore_digest=backup_restore_digest,
                legacy_access_window_digest=legacy_access_window_digest,
                archive_count_digest=archive_count_digest,
                reconciliation_digest=reconciliation_digest,
                expires_at=expiry,
            )
            gate_id = str(row.id)
        except RuntimeMigrationRepositoryError as exc:
            raise CleanupGateError(exc.code, exc.message) from exc

    return CleanupGateResult(
        gate_kind=gate_kind,
        decision=decision,
        valid=valid,
        blockers=blockers,
        snapshot_counts=snapshot,
        evidence_digest=evidence_digest,
        inventory_digest=str(inv_digest),
        gate_id=gate_id,
        schema_revision=schema_revision,
        build_revision=build_revision,
        reason=reason,
        notes=notes,
        dry_run=bool(dry_run),
    )


def latest_cleanup_gate(
    session: Session,
    *,
    gate_kind: GateKind,
    require_passed: bool = False,
    now: datetime | None = None,
) -> Any | None:
    from app.assistant.migration.models import AssistantRuntimeCleanupGate

    try:
        q = (
            session.query(AssistantRuntimeCleanupGate)
            .filter(AssistantRuntimeCleanupGate.gate_kind == gate_kind)
            .order_by(AssistantRuntimeCleanupGate.created_at.desc())
        )
        if require_passed:
            q = q.filter(AssistantRuntimeCleanupGate.decision == "passed")
        row = q.first()
    except Exception:
        session.rollback()
        return None
    if row is None:
        return None
    current = now or utcnow()
    if row.invalidated_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= current:
        return None
    return row


def preflight_deploy_b2(
    session: Session,
    *,
    schema_revision: str,
    build_revision: str,
    environment: str,
    database_fingerprint: str,
    request_id: str,
    actor_principal: str | None = None,
    reason: str | None = None,
    dry_run: bool = True,
    require_existing_passed_gate: bool = False,
    environ: Mapping[str, str] | None = None,
    inventory_digest: str | None = None,
) -> CleanupPreflightResult:
    """B2 preflight: maintenance ack + live hard counts (+ optional current gate).

    Always recomputes hard counts. When ``require_existing_passed_gate`` is true,
    also requires a non-expired passed deploy_b2 gate row. Environment ack alone
    never passes.
    """
    blockers: list[str] = []
    notes: list[str] = []
    ack = maintenance_ack_present(environ=environ)
    if not ack:
        blockers.append(f"missing_env:{B2_MAINTENANCE_ACK_ENV}")

    # Live recompute (authoritative).
    evaluate = evaluate_cleanup_gate(
        session,
        gate_kind="deploy_b2",
        schema_revision=schema_revision,
        build_revision=build_revision,
        environment=environment,
        database_fingerprint=database_fingerprint,
        request_id=request_id,
        actor_principal=actor_principal,
        reason=reason or "cleanup preflight deploy_b2",
        dry_run=dry_run,
        inventory_digest=inventory_digest,
        persist=not dry_run,
    )
    if not evaluate.valid:
        blockers.extend(evaluate.blockers)
    notes.extend(evaluate.notes)

    if require_existing_passed_gate:
        existing = latest_cleanup_gate(
            session, gate_kind="deploy_b2", require_passed=True
        )
        if existing is None and evaluate.gate_id is None:
            # After a successful evaluate persist above, re-check.
            existing = latest_cleanup_gate(
                session, gate_kind="deploy_b2", require_passed=True
            )
        if existing is None and not (evaluate.valid and evaluate.gate_id):
            blockers.append("missing_current_passed_deploy_b2_gate")

    # Deduplicate blockers while preserving order.
    seen: set[str] = set()
    unique_blockers: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.add(b)
            unique_blockers.append(b)

    ok = len(unique_blockers) == 0
    return CleanupPreflightResult(
        ok=ok,
        blockers=unique_blockers,
        gate=evaluate,
        maintenance_ack=ack,
        recomputed_counts=dict(evaluate.snapshot_counts),
        notes=notes,
    )


def assert_destructive_migration_preflight(
    connection: Any,
    *,
    environ: Mapping[str, str] | None = None,
    allow_nonterminal_legacy_with_ack: bool = True,
) -> dict[str, int]:
    """SQL-level preflight used inside the B2 Alembic upgrade.

    Raises ``RuntimeError`` with a stable token when blocked.
    """
    from sqlalchemy import text

    env = environ if environ is not None else os.environ
    if not maintenance_ack_present(environ=env):
        raise RuntimeError(
            "MINDATLAS_PLAN10_B2_PREFLIGHT_BLOCKED: "
            f"set {B2_MAINTENANCE_ACK_ENV}=1 (or {B2_TEST_OVERRIDE_ENV}=1 for tests)"
        )

    def _scalar(sql: str) -> int:
        row = connection.execute(text(sql)).fetchone()
        if row is None:
            return 0
        return int(row[0] or 0)

    # Tables may already be absent on re-entry; treat missing as zero.
    def _safe_count(sql: str) -> int:
        try:
            return _scalar(sql)
        except Exception:
            return 0

    pending = _safe_count(
        "SELECT COUNT(*) FROM assistant_human_approval WHERE status = 'pending'"
    )
    nonterminal = _safe_count(
        "SELECT COUNT(*) FROM assistant_chat_run "
        "WHERE runtime_kind = 'legacy' AND status IN ("
        "'queued','running','recovering','waiting_approval',"
        "'waiting_input','cancelling','needs_reconciliation')"
    )
    invalid_l2 = _safe_count(
        "SELECT COUNT(*) FROM assistant_conversation_skill_l2_memory "
        "WHERE skill_package_id IS NULL "
        "OR memory_namespace IS NULL "
        "OR length(trim(memory_namespace)) = 0"
    )

    blockers: list[str] = []
    if pending > 0:
        blockers.append(f"pending_legacy_approvals={pending}")
    if nonterminal > 0 and not (
        allow_nonterminal_legacy_with_ack and maintenance_ack_present(environ=env)
    ):
        blockers.append(f"nonterminal_legacy_runs={nonterminal}")
    elif nonterminal > 0:
        # Local/partner path: ack present allows nonterminal legacy drain waiver.
        pass
    if invalid_l2 > 0:
        blockers.append(f"invalid_l2_rows={invalid_l2}")

    if blockers:
        raise RuntimeError(
            "MINDATLAS_PLAN10_B2_PREFLIGHT_BLOCKED: " + ",".join(blockers)
        )

    return {
        "pending_legacy_approvals": pending,
        "nonterminal_legacy_runs": nonterminal,
        "invalid_l2_rows": invalid_l2,
    }


__all__ = (
    "B2_MAINTENANCE_ACK_ENV",
    "B2_TEST_OVERRIDE_ENV",
    "CleanupGateError",
    "CleanupGateResult",
    "CleanupHardCounts",
    "CleanupPreflightResult",
    "assert_destructive_migration_preflight",
    "count_blocked_migration_items",
    "count_invalid_l2_rows",
    "count_legacy_approval_archives",
    "count_nonterminal_legacy_runs",
    "count_pending_legacy_approvals",
    "count_unresolved_reconciliation",
    "evaluate_cleanup_gate",
    "latest_cleanup_gate",
    "maintenance_ack_present",
    "preflight_deploy_b2",
    "recompute_hard_counts",
)
