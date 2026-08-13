"""Local transactional golden create_entry adapter (Plan 08 Task 6).

Architecture rule: this module may call EntryService.create_in_uow only.
It must never call EntryService.create() or the decorated create_entry tool.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capability_calls.uow import UnitOfWorkBoundaryError
from app.entry.schemas import EntryRequest
from app.entry.service import EntryService


def stage_create_entry_local(
    *, session: Session, request: EntryRequest, call_id: UUID
):
    """Stage the golden Entry/outbox on the ledger-owned Session; never commit."""
    return EntryService(session).create_in_uow(
        request,
        source_capability_call_id=call_id,
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
    "assert_no_committing_create_import",
    "stage_create_entry_local",
]
