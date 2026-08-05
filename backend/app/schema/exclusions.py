"""Exact source allowlist for unpublished Plan 10 schema objects.

This module defines identities only.  It is not a prefix selector and does not
authorize runtime deletion without a definition-locked manifest.
"""

from __future__ import annotations

LEGACY_TABLE_NAMES = (
    "assistant_runtime_migration_item",
    "assistant_runtime_migration_event",
    "assistant_runtime_migration_batch",
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_event",
    "assistant_runtime_rollout_control",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_runtime_shadow_comparison",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
)

PLAN10_IMMUTABLE_TABLES = (
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
    "assistant_runtime_migration_event",
    "assistant_runtime_rollout_event",
)

PLAN10_UPDATE_ONLY_TABLES = ("assistant_runtime_shadow_comparison",)

LEGACY_FUNCTION_KEYS = (
    ("function", "public", "mindatlas_reject_plan10_immutable_mutation", ""),
)


def expected_legacy_object_keys() -> tuple[tuple[str, str, str, str], ...]:
    """Return all 27 exact top-level exclusion identities in stable order."""
    keys = [("table", "public", name, "") for name in LEGACY_TABLE_NAMES]
    keys.extend(LEGACY_FUNCTION_KEYS)
    for name in PLAN10_IMMUTABLE_TABLES:
        keys.append(("trigger", "public", f"trg_{name}_reject_update", name))
        keys.append(("trigger", "public", f"trg_{name}_reject_delete", name))
    for name in PLAN10_UPDATE_ONLY_TABLES:
        keys.append(("trigger", "public", f"trg_{name}_reject_update", name))
    return tuple(sorted(keys))
