"""Local transactional golden create_entry adapter (Plan 08 Task 6).

Architecture rule: this module may call EntryService.create_in_uow only.
It must never call EntryService.create() or the decorated create_entry tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capability_calls.repository import CapabilityCallRepository
from app.assistant.capability_calls.uow import CapabilityUnitOfWork, UnitOfWorkBoundaryError
from app.assistant.durable.repository import LeaseToken
from app.common.time import utcnow
from app.entry.schemas import EntryRequest
from app.entry.service import EntryService


@dataclass(frozen=True, slots=True)
class LocalCreateEntryResult:
    entry_id: UUID
    call_id: UUID
    outbox_entry_id: UUID | None


def create_entry_local_transactional(
    *,
    session: Session,
    request: EntryRequest,
    call_id: UUID,
    expected_call_revision: int,
    expected_run_revision: int,
    lease: LeaseToken | None,
    now: datetime | None = None,
) -> LocalCreateEntryResult:
    """Atomically create Entry+outbox and mark call succeeded.

    Crash before commit leaves neither business mutation nor success.
    Crash after commit leaves both. Recovery reloads by source_capability_call_id.
    """
    ts = now or utcnow()
    uow = CapabilityUnitOfWork(session=session)
    # Forbid commit until the ledger stages success.
    repo = CapabilityCallRepository(session)
    call = repo.get_call(call_id, for_update=True)
    if call is None:
        raise ValueError(f"call {call_id} not found")
    if str(call.status) not in {"authorized", "executing"}:
        # Idempotent success recovery.
        if str(call.status) == "succeeded":
            existing = (
                session.query(__import__("app.entry.models", fromlist=["Entry"]).Entry)
                .filter_by(source_capability_call_id=call_id)
                .one_or_none()
            )
            if existing is not None:
                return LocalCreateEntryResult(
                    entry_id=existing.id,
                    call_id=call_id,
                    outbox_entry_id=existing.id,
                )
        raise ValueError(f"call {call_id} not dispatchable for local write: {call.status}")

    # Ensure executing (claim may have already done this).
    if str(call.status) == "authorized":
        if lease is None:
            raise ValueError("lease required to claim local write attempt")
        call, _attempt = repo.claim_attempt(
            call_id=call_id,
            expected_call_revision=expected_call_revision,
            expected_run_revision=expected_run_revision,
            lease=lease,
            worker_id=lease.worker_id,
            now=ts,
        )
        expected_call_revision = int(call.state_revision)

    # Core no-commit create.
    service = EntryService(session)
    entry = service.create_in_uow(
        request,
        source_capability_call_id=call_id,
    )

    # Stage call success + effect timestamp in same transaction.
    # local_transactional may only set side_effect_started_at on succeeded.
    call = repo.transition_call(
        call_id=call_id,
        expected_call_revision=int(call.state_revision),
        expected_run_revision=expected_run_revision,
        to_status="succeeded",
        lease=lease,
        side_effect_started_at=ts,
        now=ts,
    )

    # Single commit owned by ledger UoW.
    uow.allow_commit()
    uow.commit()
    return LocalCreateEntryResult(
        entry_id=entry.id,
        call_id=call_id,
        outbox_entry_id=entry.id,
    )


def assert_no_committing_create_import() -> None:
    """Static architecture note: golden adapter uses create_in_uow only."""
    import ast
    import app.assistant.capability_calls.local_write as mod

    src = open(mod.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            if mod_name == "app.assistant.tools.entry_tools":
                for alias in node.names:
                    if alias.name == "create_entry":
                        raise UnitOfWorkBoundaryError(
                            "local_write must not import create_entry tool"
                        )
        if isinstance(node, ast.Call):
            func = node.func
            # EntryService(...).create(...) — Attribute name exactly "create"
            if isinstance(func, ast.Attribute) and func.attr == "create":
                raise UnitOfWorkBoundaryError(
                    "local_write must not call EntryService.create"
                )
    if "create_in_uow" not in src:
        raise UnitOfWorkBoundaryError("local_write must call create_in_uow")


__all__ = [
    "LocalCreateEntryResult",
    "assert_no_committing_create_import",
    "create_entry_local_transactional",
]
