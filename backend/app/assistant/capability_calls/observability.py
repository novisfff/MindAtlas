"""Safe, bounded capability/write metrics.

The adapter has no API for arbitrary labels.  Callers provide only the
allowlisted branch/entrypoint/reason enums and unknown values collapse to
``other``.
"""

from __future__ import annotations

from collections import Counter
from threading import RLock
from typing import Mapping


CAPABILITY_METRIC_NAMES = frozenset(
    {
        "mindatlas_agent_unsupported_write_total",
        "mindatlas_create_entry_write_guard_rejection_total",
        "mindatlas_capability_unresolved",
    }
)
_LABELS = {
    "mindatlas_agent_unsupported_write_total": {
        "branch": frozenset({"update_entry", "merge_entry", "create_relation", "relation_followup", "other"}),
        "entrypoint": frozenset({"direct_agent_boundary", "capability_registry", "provider_surface", "openclaw_boundary", "other"}),
    },
    "mindatlas_create_entry_write_guard_rejection_total": {
        "reason_code": frozenset({"pre_ga_launch_unapproved", "reconciliation_required", "write_safety_blocked", "create_entry_not_enabled", "capability_not_supported", "other"}),
        "phase": frozenset({"proposal", "post_approval", "other"}),
    },
    "mindatlas_capability_unresolved": {
        "status": frozenset({"unknown", "needs_reconciliation", "other"}),
    },
}
_LOCK = RLock()
_COUNTS: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()


def record_capability_metric(name: str, labels: Mapping[str, str]) -> None:
    if name not in CAPABILITY_METRIC_NAMES or set(labels) != set(_LABELS[name]):
        raise ValueError("capability_metric_labels_invalid")
    safe = tuple(
        (key, str(labels[key]) if str(labels[key]) in values else "other")
        for key, values in _LABELS[name].items()
    )
    with _LOCK:
        _COUNTS[(name, safe)] += 1


def snapshot_capability_metrics() -> dict[tuple[str, tuple[tuple[str, str], ...]], int]:
    with _LOCK:
        return dict(_COUNTS)


def clear_capability_metrics_for_tests() -> None:
    with _LOCK:
        _COUNTS.clear()


__all__ = [
    "CAPABILITY_METRIC_NAMES",
    "clear_capability_metrics_for_tests",
    "record_capability_metric",
    "snapshot_capability_metrics",
]
