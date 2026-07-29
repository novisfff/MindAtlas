"""Live L2 memory migration-state reader (Plan 2 Task 9).

Non-mutating AppSetting query only. Live application code must not import
``app.assistant.migration`` for L2 readiness — use this module instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.system_settings.models import AppSetting

L2_MEMORY_MIGRATION_STATE_KEY = "assistant_l2_memory_migration_state"


@dataclass(frozen=True)
class L2MemoryMigrationState:
    usable: bool
    reason_code: str | None


def read_l2_memory_migration_state(db: Session) -> L2MemoryMigrationState:
    """Return whether L2 memory has been verified for live use.

    Reads the durable AppSetting only. Never mutates, never discovers packages,
    and never imports the archived migration package.
    """
    row = db.scalar(
        select(AppSetting).where(AppSetting.key == L2_MEMORY_MIGRATION_STATE_KEY)
    )
    payload = dict(row.value_json) if row and row.value_json else {}
    return L2MemoryMigrationState(
        usable=payload.get("verified") is True,
        reason_code=(
            None if payload.get("verified") is True else "l2_memory_not_verified"
        ),
    )


__all__ = [
    "L2_MEMORY_MIGRATION_STATE_KEY",
    "L2MemoryMigrationState",
    "read_l2_memory_migration_state",
]
