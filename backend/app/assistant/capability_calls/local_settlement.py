"""Ledger-owned transactional settlement for the local create-entry adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capability_calls.local_write import stage_create_entry_local
from app.assistant.capability_calls.repository import CapabilityCallRepository
from app.assistant.capability_calls.uow import CapabilityUnitOfWork
from app.assistant.durable.repository import LeaseToken
from app.common.time import utcnow
from app.entry.schemas import EntryRequest


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
    """Atomically stage and settle a create_entry call in the ledger UoW."""
    ts = now or utcnow()
    uow = CapabilityUnitOfWork(session=session)
    repo = CapabilityCallRepository(session)
    call = repo.get_call(call_id, for_update=True)
    if call is None:
        raise ValueError(f"call {call_id} not found")
    if str(call.status) not in {"authorized", "executing"}:
        if str(call.status) == "succeeded":
            existing = (
                session.query(__import__("app.entry.models", fromlist=["Entry"]).Entry)
                .filter_by(source_capability_call_id=call_id)
                .one_or_none()
            )
            if existing is not None:
                return LocalCreateEntryResult(existing.id, call_id, existing.id)
        raise ValueError(f"call {call_id} not dispatchable for local write: {call.status}")
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
    entry = stage_create_entry_local(session=session, request=request, call_id=call_id)
    repo.transition_call(
        call_id=call_id,
        expected_call_revision=int(call.state_revision),
        expected_run_revision=expected_run_revision,
        to_status="succeeded",
        lease=lease,
        side_effect_started_at=ts,
        now=ts,
    )
    uow.allow_commit()
    uow.commit()
    return LocalCreateEntryResult(entry.id, call_id, entry.id)


__all__ = ["LocalCreateEntryResult", "create_entry_local_transactional"]
