"""Durable Skill activation: process-local stage + lifecycle-accept result CAS.

Plan 06 §7.4 / invariant 15:
- Stage is process-local only (no durable candidate residue).
- Only the post-lineage result transaction acts as ManifestEffectLifecyclePort.accept.
- Discarded/failed lineage leaves zero durable active state.
- Already-accepted children are never appended twice.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.durable.checkpoints import commit_unit_result
from app.assistant.durable.crash import CrashPoint, maybe_crash
from app.assistant.durable.models import AssistantRunManifestRevision
from app.assistant.durable.repository import DurableCommitResult, LeaseToken

logger = logging.getLogger(__name__)


class DurableSkillActivationLifecycle:
    """Process-local pending packages + durable accept transaction.

    Implements the durable half of ``ManifestEffectLifecyclePort.accept``:
    stage → lineage validate → one result CAS that appends child Manifest/policy.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._accepted_digests: set[str] = set()

    def stage(self, *, call_id: str, package: Mapping[str, Any]) -> None:
        cid = str(call_id or "").strip()
        if not cid:
            raise ValueError("call_id must be nonempty")
        with self._lock:
            self._pending[cid] = dict(package)

    def has_pending(self, call_id: str) -> bool:
        with self._lock:
            return str(call_id) in self._pending

    def discard(self, *, call_id: str, reason_code: str) -> None:
        del reason_code
        with self._lock:
            self._pending.pop(str(call_id), None)

    def accept(
        self,
        *,
        call_id: str,
        current_manifest: Any = None,
        proposed_manifest: Any = None,
    ) -> None:
        """Protocol-compatible accept for process-local ports.

        Durable CAS accept is ``accept_into_result``. This method only validates
        that a staged package exists and clears it when used as a pure port
        without a DB transaction (tests). Production runner always uses
        ``accept_into_result``.
        """
        del current_manifest, proposed_manifest
        with self._lock:
            if str(call_id) not in self._pending:
                raise ValueError(f"no staged package for call_id={call_id}")
            # Process-local only — durable accept is accept_into_result.
            self._pending.pop(str(call_id), None)

    def accept_into_result(
        self,
        *,
        db: Session,
        run_id: UUID,
        lease: LeaseToken,
        expected_revision: int,
        call_id: str,
        current_manifest_digest: str,
        policy_payload: Mapping[str, Any],
        policy_digest: str,
        budget_payload: Mapping[str, Any],
        budget_digest: str,
        obligation_payload: Mapping[str, Any],
        obligation_digest: str,
        allow_already_accepted: bool = False,
    ) -> DurableCommitResult:
        """Lineage-validate staged package and commit as one result transaction.

        On any validation failure, discard process-local candidate and raise —
        no durable residue.
        """
        cid = str(call_id)
        with self._lock:
            package = self._pending.get(cid)
            if package is None:
                raise ValueError(f"no staged package for call_id={cid}")
            # Snapshot then remove from pending so concurrent failure paths leave
            # no residue even if CAS fails after this method returns.
            package = dict(package)

        try:
            parent_digest = str(package.get("parent_manifest_digest") or "")
            proposed_digest = str(package.get("proposed_manifest_digest") or "")
            child_payload = package.get("child_payload") or {}
            if not parent_digest or not proposed_digest:
                raise ValueError("staged package missing parent/proposed digests")

            def _already_accepted_result() -> DurableCommitResult:
                """Idempotent no-op: no duplicate child/checkpoint append."""
                from app.assistant.models import AssistantChatRun

                with self._lock:
                    self._pending.pop(cid, None)
                    self._accepted_digests.add(proposed_digest)
                run = db.get(AssistantChatRun, run_id)
                if run is None:
                    raise ValueError(f"run not found: {run_id}")
                return DurableCommitResult(
                    run=run,
                    state_revision=int(run.state_revision),
                    status=str(run.status),
                    events=(),
                    reused_event_keys=(),
                    inserted_event_keys=(),
                )

            # Already-accepted / post-result short-circuit BEFORE lineage reject.
            # After process restart, process-local `_accepted_digests` is empty while
            # the durable pointer may already be the child. Re-stage + accept of the
            # same package must no-op without failing parent-lineage.
            if proposed_digest in self._accepted_digests or (
                allow_already_accepted
                and current_manifest_digest == proposed_digest
            ):
                return _already_accepted_result()

            # Durable already-committed child by digest (survives process restart).
            existing = db.execute(
                select(AssistantRunManifestRevision).where(
                    AssistantRunManifestRevision.run_id == run_id,
                    AssistantRunManifestRevision.manifest_digest == proposed_digest,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _already_accepted_result()

            # Lineage: current durable Manifest must match staged parent.
            # Only reached when the proposed child is not already durable.
            if current_manifest_digest != parent_digest:
                raise ValueError(
                    f"lineage failed: current={current_manifest_digest[:12]}… "
                    f"parent={parent_digest[:12]}…"
                )

            # Kill point 5: after stage/lineage acceptance before lifecycle-accept CAS.
            maybe_crash(CrashPoint.AFTER_SKILL_LINEAGE_BEFORE_ACCEPT_COMMIT)

            result = commit_unit_result(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=expected_revision,
                phase="dispatching_calls",
                next_action_kind="dispatch_calls",
                clear_inflight=True,
                manifest_payload=dict(child_payload),
                manifest_digest=proposed_digest,
                parent_manifest_digest=parent_digest,
                policy_payload=dict(policy_payload),
                policy_digest=policy_digest,
                budget_payload=dict(budget_payload),
                budget_digest=budget_digest,
                obligation_payload=dict(obligation_payload),
                obligation_digest=obligation_digest,
                reason="skill_inject_accepted",
                completed_logical_unit_id=f"skill.inject:{cid}",
            )
            with self._lock:
                self._pending.pop(cid, None)
                self._accepted_digests.add(proposed_digest)
            # Kill point 6: after lifecycle-accept commit before worker observes success.
            maybe_crash(CrashPoint.AFTER_LIFECYCLE_ACCEPT_COMMIT_BEFORE_OBSERVE)
            return result
        except Exception:
            # Discard candidate on any failure (lineage, digest, CAS, cancel…).
            with self._lock:
                self._pending.pop(cid, None)
            raise


__all__ = ["DurableSkillActivationLifecycle"]
