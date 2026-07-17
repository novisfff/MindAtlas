"""Worker adapter for persisted Plan 07 durable Workflow resume units."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.assistant.durable.codec import decode_checkpoint
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
)
from app.assistant.durable.reconstruction import load_current_checkpoint
from app.assistant.workflow.durable.material import (
    DurableMaterialResolutionError,
    DurableRuntimeMaterialResolver,
)
from app.assistant.workflow.durable.resume import (
    execute_interrupt_resume,
    route_irreconcilable_to_needs_reconciliation,
)

logger = logging.getLogger(__name__)


class DurableWorkflowUnitExecutor:
    """Execute one persisted durable child-resume unit under the worker lease."""

    def __init__(
        self,
        *,
        provider_resume: Callable[..., Any] | None = None,
    ) -> None:
        self.provider_resume = provider_resume

    def execute(
        self,
        *,
        claimed: Any,
        decision: Any,
        heartbeat: Callable[[], bool],
        session_factory: Callable[[], Any],
    ) -> None:
        del decision
        if not heartbeat():
            return
        db = session_factory()
        try:
            checkpoint = load_current_checkpoint(db, run_id=claimed.run_id)
            action = str(checkpoint.next_action.kind)
            if action == "resume_provider_loop":
                from app.assistant.models import AssistantChatRun

                run = db.get(AssistantChatRun, claimed.run_id)
                boundary_id = getattr(run, "current_checkpoint_id", None)
                provider_continuation = self._load_provider_continuation(
                    db,
                    run_id=claimed.run_id,
                    before_checkpoint_id=boundary_id,
                )
                resolution = self._load_provider_waiting_resolution(
                    db,
                    checkpoint=checkpoint,
                )
                if provider_continuation is None or resolution is None:
                    self._reconcile(
                        db,
                        claimed=claimed,
                        expected_revision=int(claimed.state_revision),
                        reason_code="provider_resume_context_missing",
                        detail="persisted Provider resume context is incomplete",
                    )
                    return
                self._invoke_provider_resume(
                    db,
                    claimed=claimed,
                    continuation=provider_continuation,
                    resolution=resolution,
                    expected_revision=int(claimed.state_revision),
                    heartbeat=heartbeat,
                )
                return
            if action not in {"resume_child", "continue_child"}:
                self._reconcile(
                    db,
                    claimed=claimed,
                    expected_revision=int(claimed.state_revision),
                    reason_code="durable_action_not_reconstructable",
                    detail=f"unsupported durable worker action: {action}",
                )
                return
            workflow_state = getattr(checkpoint, "workflow_state", None)
            if workflow_state is None:
                self._reconcile(
                    db,
                    claimed=claimed,
                    expected_revision=int(claimed.state_revision),
                    reason_code="durable_workflow_state_missing",
                    detail="durable resume checkpoint has no workflow_state",
                )
                return
            try:
                root, children = DurableRuntimeMaterialResolver(db).resolve(
                    workflow_state=workflow_state
                )
            except DurableMaterialResolutionError as exc:
                self._reconcile(
                    db,
                    claimed=claimed,
                    expected_revision=int(claimed.state_revision),
                    reason_code=exc.reason_code,
                    detail=str(exc),
                )
                return

            waiting_checkpoint_id = None
            pending_interrupt_id = getattr(checkpoint, "pending_interrupt_id", None)
            if pending_interrupt_id is not None:
                interrupt = db.get(AssistantRunInterrupt, pending_interrupt_id)
                waiting_checkpoint_id = (
                    getattr(interrupt, "checkpoint_id", None)
                    if interrupt is not None
                    else None
                )
            provider_continuation = self._load_provider_continuation(
                db,
                run_id=claimed.run_id,
                before_checkpoint_id=waiting_checkpoint_id,
            )
            result = execute_interrupt_resume(
                db,
                run_id=claimed.run_id,
                lease=claimed.lease,
                expected_revision=int(claimed.state_revision),
                material=root,
                child_materials=children,
                provider_loop_continuation=provider_continuation,
            )
            if result.kind != "root_terminal":
                return
            if (
                result.provider_waiting_resolution is None
                or provider_continuation is None
                or self.provider_resume is None
            ):
                self._reconcile(
                    db,
                    claimed=claimed,
                    expected_revision=int(
                        result.state_revision or claimed.state_revision
                    ),
                    reason_code="provider_resume_context_missing",
                    detail="root completed without reconstructable Provider resume context",
                )
                return
            self._invoke_provider_resume(
                db,
                claimed=claimed,
                continuation=provider_continuation,
                resolution=result.provider_waiting_resolution,
                expected_revision=int(
                    result.state_revision or claimed.state_revision
                ),
                heartbeat=heartbeat,
            )
        finally:
            db.close()

    @staticmethod
    def _load_provider_continuation(
        db: Any,
        *,
        run_id: Any,
        before_checkpoint_id: Any | None = None,
    ) -> Any | None:
        rows = db.scalars(
            select(AssistantRunCheckpoint)
            .where(AssistantRunCheckpoint.run_id == run_id)
            .order_by(AssistantRunCheckpoint.sequence.desc())
        )
        boundary = None
        if before_checkpoint_id is not None:
            boundary = db.get(AssistantRunCheckpoint, before_checkpoint_id)
        for row in rows:
            if boundary is not None and int(row.sequence) >= int(boundary.sequence):
                continue
            try:
                checkpoint = decode_checkpoint(row.state_payload)
            except Exception:
                continue
            continuation = getattr(checkpoint, "provider_loop_continuation", None)
            if continuation is not None:
                return continuation
        return None

    @staticmethod
    def _load_provider_waiting_resolution(
        db: Any,
        *,
        checkpoint: Any,
    ) -> Any | None:
        from app.assistant.provider_loop.contracts import ProviderWaitingResolution

        matches = []
        for artifact_id in getattr(checkpoint, "artifact_ids", ()) or ():
            artifact = db.get(AssistantRunArtifact, artifact_id)
            if (
                artifact is not None
                and str(artifact.kind) == "provider_waiting_resolution"
                and artifact.inline_bytes is not None
            ):
                matches.append(artifact)
        if len(matches) != 1:
            return None
        return ProviderWaitingResolution.model_validate_json(matches[0].inline_bytes)

    def _invoke_provider_resume(
        self,
        db: Any,
        *,
        claimed: Any,
        continuation: Any,
        resolution: Any,
        expected_revision: int,
        heartbeat: Callable[[], bool],
    ) -> None:
        if self.provider_resume is None:
            self._reconcile(
                db,
                claimed=claimed,
                expected_revision=expected_revision,
                reason_code="provider_resume_context_missing",
                detail="Provider resume executor is not configured",
            )
            return
        try:
            self.provider_resume(
                db=db,
                claimed=claimed,
                continuation=continuation,
                resolution=resolution,
                expected_revision=expected_revision,
                heartbeat=heartbeat,
            )
        except Exception as exc:
            rollback = getattr(db, "rollback", None)
            if callable(rollback):
                rollback()
            # Network/timeouts are transient and must follow the worker's
            # normal lease/backoff path; only deterministic protocol failures
            # become needs_reconciliation.
            if isinstance(exc, (TimeoutError, ConnectionError)) or getattr(
                exc, "retry_disposition", None
            ) in {"retry", "backoff"}:
                raise
            self._reconcile(
                db,
                claimed=claimed,
                expected_revision=expected_revision,
                reason_code="provider_resume_failed",
                detail=str(exc),
            )

    @staticmethod
    def _reconcile(
        db: Any,
        *,
        claimed: Any,
        expected_revision: int,
        reason_code: str,
        detail: str,
    ) -> None:
        try:
            route_irreconcilable_to_needs_reconciliation(
                db,
                run_id=claimed.run_id,
                lease=claimed.lease,
                expected_revision=expected_revision,
                reason_code=reason_code,
                detail=detail,
            )
        except Exception:
            logger.info(
                "durable reconciliation CAS lost run_id=%s reason=%s",
                claimed.run_id,
                reason_code,
                exc_info=True,
            )


__all__ = ["DurableWorkflowUnitExecutor"]
