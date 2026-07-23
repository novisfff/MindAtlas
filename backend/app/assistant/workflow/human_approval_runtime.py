"""Legacy blocking HumanLoop runtime — fail-closed after Plan 10 B2.

The ``assistant_human_approval`` table is dropped. Durable Main Agent /
Plan 07 interrupts own production HITL. This module retains public symbols
so import graphs and isinstance checks stay stable, but every path that
would persist or wait on a legacy approval raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.common.exceptions import ApiException
from app.common.time import utcnow as _utcnow

ApprovalDecision = Literal["approve", "reject", "submit"]

LEGACY_HITL_GONE_CODE = 41011
LEGACY_HITL_GONE_MESSAGE = (
    "Legacy blocking HumanLoop / assistant_human_approval is removed. "
    "Use durable Main Agent interrupts (Plan 07) or evaluation simulation."
)


class DurableBlockingRuntimeForbidden(RuntimeError):
    """Raised when a durable Main Agent Run accidentally enters Legacy create_and_wait."""


class LegacyHitlRemoved(RuntimeError):
    """Legacy HITL table/runtime is gone."""

    def __init__(self, message: str = LEGACY_HITL_GONE_MESSAGE) -> None:
        super().__init__(message)
        self.code = LEGACY_HITL_GONE_CODE


def _gone(*, action: str = "access") -> None:
    raise ApiException(
        status_code=410,
        code=LEGACY_HITL_GONE_CODE,
        message=f"{LEGACY_HITL_GONE_MESSAGE} (action={action})",
        details={"legacyHitlRemoved": True, "replacement": "durable_interrupt"},
    )


def utcnow() -> datetime:
    return _utcnow()


def _reject_durable_blocking_runtime(session_factory: sessionmaker, run_id: Any) -> None:
    _ = session_factory
    rid = str(run_id or "").strip()
    raise DurableBlockingRuntimeForbidden(
        f"run {rid} cannot use Legacy HumanLoopRuntime.create_and_wait"
    )


def serialize_human_approval(row: Any) -> dict[str, Any]:
    _ = row
    _gone(action="serialize")
    return {}


class HumanLoopCoordinator:
    """In-process waiter registry (no-op after table drop)."""

    def __init__(self) -> None:
        self._waiters: dict[str, Any] = {}

    def register(self, approval_id: str, event: Any) -> None:
        _ = (approval_id, event)

    def notify(self, approval_id: str) -> None:
        _ = approval_id

    def unregister(self, approval_id: str) -> None:
        self._waiters.pop(str(approval_id), None)


GLOBAL_HUMAN_LOOP_COORDINATOR = HumanLoopCoordinator()


@dataclass
class HumanLoopContext:
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    workflow_id: UUID | None = None
    skill_id: UUID | None = None
    channel_type: str = "assistant_chat"
    run_id: str | None = None


class HumanLoopRuntime:
    """Fail-closed stand-in for the removed blocking HITL runtime."""

    def __init__(
        self,
        session_factory: sessionmaker | None = None,
        context: HumanLoopContext | None = None,
        *,
        coordinator: HumanLoopCoordinator | None = None,
        **_kwargs: Any,
    ) -> None:
        self._session_factory = session_factory
        self._context = context or HumanLoopContext()
        self._coordinator = coordinator or GLOBAL_HUMAN_LOOP_COORDINATOR

    def create_and_wait(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        # Preserve durable-run guard semantics for callers that probe first.
        if self._session_factory is not None and self._context.run_id:
            try:
                _reject_durable_blocking_runtime(self._session_factory, self._context.run_id)
            except DurableBlockingRuntimeForbidden:
                raise
        raise LegacyHitlRemoved(f"{LEGACY_HITL_GONE_MESSAGE} (create_and_wait)")


def list_pending_approvals_for_conversation(
    db: Session, conversation_id: UUID
) -> list[dict[str, Any]]:
    _ = (db, conversation_id)
    return []


def submit_human_approval_decision(
    db: Session,
    *,
    approval_id: UUID | str,
    decision: Any = None,
    values: dict[str, Any] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    _ = (db, approval_id, decision, values, comment)
    _gone(action="submit_decision")
    return {}


def cancel_pending_human_approvals_for_run(db: Session, *, run_id: str) -> list[dict[str, Any]]:
    _ = (db, run_id)
    return []
