"""Capability Unit of Work for local transactional writes (Plan 08 Task 6).

Adapters may use the supplied Session but must not commit/rollback/close it.
The ledger-owned UoW commits once after Entry + outbox + call success staging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.orm import Session


class UnitOfWorkBoundaryError(RuntimeError):
    """Raised when code below the UoW attempts forbidden transaction control."""


@dataclass
class CapabilityUnitOfWork:
    """Caller-owned Session wrapper that forbids nested transaction lifecycle."""

    session: Session
    _committed: bool = False
    _forbid_commit: bool = True

    @property
    def db(self) -> Session:
        return self.session

    def allow_commit(self) -> None:
        """Ledger-owned path may enable a single commit."""
        self._forbid_commit = False

    def commit(self) -> None:
        if self._forbid_commit:
            raise UnitOfWorkBoundaryError(
                "Session.commit() is forbidden below ledger-owned Unit of Work"
            )
        if self._committed:
            raise UnitOfWorkBoundaryError("Unit of Work already committed")
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        # Explicit rollback is allowed for fault injection / outer control only
        # when not yet committed; adapters must not call this.
        if self._forbid_commit:
            raise UnitOfWorkBoundaryError(
                "Session.rollback() is forbidden below ledger-owned Unit of Work"
            )
        self.session.rollback()

    def flush(self) -> None:
        self.session.flush()


def install_commit_spy(session: Session) -> Callable[[], None]:
    """Install a spy that raises if session.commit is invoked.

    Returns a restore function.
    """
    original = session.commit

    def _blocked(*_a: Any, **_k: Any) -> None:
        raise UnitOfWorkBoundaryError(
            "Session.commit() is forbidden below ledger-owned Unit of Work"
        )

    session.commit = _blocked  # type: ignore[method-assign]

    def restore() -> None:
        session.commit = original  # type: ignore[method-assign]

    return restore


__all__ = [
    "CapabilityUnitOfWork",
    "UnitOfWorkBoundaryError",
    "install_commit_spy",
]
