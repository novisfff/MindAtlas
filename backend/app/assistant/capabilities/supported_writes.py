"""Closed production Agent write vocabulary and side-effect-free boundaries.

This module deliberately knows nothing about SQLAlchemy sessions, Entries,
Relations, CapabilityCalls, Artifacts, Interrupts, or Provider payloads.  A
removed write branch must terminate here before any of those concerns can be
constructed.
"""

from __future__ import annotations

import logging
from collections import Counter
from threading import RLock
from typing import Final, Literal, NoReturn, cast

from app.assistant.capabilities.contracts import CapabilityError
from app.assistant.capabilities.errors import CapabilityDomainError

SUPPORTED_PRODUCTION_WRITE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"create_entry"}
)
UNSUPPORTED_PRODUCTION_WRITE_BRANCHES: Final[frozenset[str]] = frozenset(
    {"update_entry", "merge_entry", "create_relation", "relation_followup"}
)
_UNSUPPORTED_CAPABILITY_KEY_TO_BRANCH: Final[dict[str, str]] = {
    "update_entry": "update_entry",
    "merge_entry": "merge_entry",
    "create_relation": "create_relation",
    "relation_followup": "relation_followup",
    # Retired OpenClaw compatibility names map to the same rejected operation.
    "openclaw_create_relation": "create_relation",
}
CREATE_ENTRY_WRITE_CONTRACT_VERSION: Final[int] = 1

UnsupportedProductionWriteBranch = Literal[
    "update_entry",
    "merge_entry",
    "create_relation",
    "relation_followup",
]
SafeUnsupportedWriteEntrypoint = Literal[
    "direct_agent_boundary",
    "capability_registry",
    "provider_surface",
    "openclaw_boundary",
]

_SAFE_ENTRYPOINTS: Final[frozenset[str]] = frozenset(
    {
        "direct_agent_boundary",
        "capability_registry",
        "provider_surface",
        "openclaw_boundary",
    }
)
_attempt_lock = RLock()
_unsupported_write_attempts: Counter[tuple[str, str]] = Counter()
logger = logging.getLogger(__name__)


def normalize_unsupported_branch(branch: str) -> UnsupportedProductionWriteBranch:
    """Validate a closed unsupported branch identifier without aliases."""
    normalized = str(branch or "").strip()
    if normalized not in UNSUPPORTED_PRODUCTION_WRITE_BRANCHES:
        raise ValueError("unsupported branch identifier is not allowlisted")
    return cast(UnsupportedProductionWriteBranch, normalized)


def unsupported_branch_for_capability_key(
    capability_key: str,
) -> UnsupportedProductionWriteBranch | None:
    """Map only retired Agent capability names to their closed branch vocabulary."""
    normalized = str(capability_key or "").strip()
    branch = _UNSUPPORTED_CAPABILITY_KEY_TO_BRANCH.get(normalized)
    return normalize_unsupported_branch(branch) if branch is not None else None


def _normalize_safe_entrypoint(entrypoint: str) -> SafeUnsupportedWriteEntrypoint:
    normalized = str(entrypoint or "").strip()
    if normalized not in _SAFE_ENTRYPOINTS:
        raise ValueError("unsupported write entrypoint is not allowlisted")
    return cast(SafeUnsupportedWriteEntrypoint, normalized)


def record_unsupported_write_attempt(
    branch: str,
    safe_entrypoint: str,
) -> None:
    """Record only the closed branch/entrypoint vocabulary.

    The log carries no call IDs, prompts, inputs, Entry/Relation identifiers,
    or other request data.  Task 10 binds this event to the production metrics
    adapter; the local counter gives deterministic boundary tests a read-only
    observation surface in the meantime.
    """
    normalized_branch = normalize_unsupported_branch(branch)
    normalized_entrypoint = _normalize_safe_entrypoint(safe_entrypoint)
    with _attempt_lock:
        _unsupported_write_attempts[(normalized_branch, normalized_entrypoint)] += 1
    from app.assistant.capability_calls.observability import record_capability_metric

    record_capability_metric(
        "mindatlas_agent_unsupported_write_total",
        {"branch": normalized_branch, "entrypoint": normalized_entrypoint},
    )
    logger.info(
        "agent_unsupported_write branch=%s entrypoint=%s",
        normalized_branch,
        normalized_entrypoint,
    )


def unsupported_write_attempt_snapshot() -> dict[tuple[str, str], int]:
    """Return a copy for tests and safe in-process diagnostics."""
    with _attempt_lock:
        return dict(_unsupported_write_attempts)


def clear_unsupported_write_attempts_for_tests() -> None:
    """Clear process-local metric state; never used by production code."""
    with _attempt_lock:
        _unsupported_write_attempts.clear()


class CapabilityNotSupported(CapabilityDomainError):
    """A typed write-surface rejection before a CapabilityCall exists."""

    safe_code: Final[str] = "capability_not_supported"

    def __init__(self, branch: str) -> None:
        normalized_branch = normalize_unsupported_branch(branch)
        self.branch: UnsupportedProductionWriteBranch = normalized_branch
        super().__init__(
            CapabilityError(
                error_type="unsupported",
                safe_code=self.safe_code,
                safe_message="This write capability is not supported.",
                retry_disposition="never",
                target_identity=f"unsupported-write:{normalized_branch}",
            )
        )


def unsupported_write_boundary(
    branch: str,
    safe_entrypoint: str = "direct_agent_boundary",
) -> NoReturn:
    """Record the safe boundary event and raise before any side effect."""
    normalized_branch = normalize_unsupported_branch(branch)
    record_unsupported_write_attempt(normalized_branch, safe_entrypoint)
    raise CapabilityNotSupported(normalized_branch)


def merge_entry(*args: object, **kwargs: object) -> NoReturn:
    """Retained direct boundary for the removed merge write branch."""
    del args, kwargs
    unsupported_write_boundary("merge_entry")


def relation_followup(*args: object, **kwargs: object) -> NoReturn:
    """Retained direct boundary for the removed relation follow-up branch."""
    del args, kwargs
    unsupported_write_boundary("relation_followup")


__all__ = [
    "CREATE_ENTRY_WRITE_CONTRACT_VERSION",
    "CapabilityNotSupported",
    "SUPPORTED_PRODUCTION_WRITE_CAPABILITIES",
    "UNSUPPORTED_PRODUCTION_WRITE_BRANCHES",
    "clear_unsupported_write_attempts_for_tests",
    "merge_entry",
    "normalize_unsupported_branch",
    "record_unsupported_write_attempt",
    "relation_followup",
    "unsupported_write_attempt_snapshot",
    "unsupported_branch_for_capability_key",
    "unsupported_write_boundary",
]
