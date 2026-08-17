"""Retired Agent relation-write boundary.

Ordinary authenticated product APIs still own human Relation management.  This
module intentionally contains no model, service, session, or Provider imports.
"""

from __future__ import annotations

from typing import NoReturn

from app.assistant.capabilities.supported_writes import unsupported_write_boundary


def create_relation(*args: object, **kwargs: object) -> NoReturn:
    """Reject a stale Agent relation request before any side effect."""
    del args, kwargs
    unsupported_write_boundary("create_relation", "direct_agent_boundary")


__all__ = ["create_relation"]
