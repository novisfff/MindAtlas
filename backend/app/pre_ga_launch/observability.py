"""Bounded metrics for the pre-GA production control plane.

Only code-owned enum labels are accepted.  IDs, digests, reasons, request
material, and response bodies are intentionally not representable here.
"""

from __future__ import annotations

from collections import Counter
from threading import RLock
from typing import Mapping


PRE_GA_METRIC_NAMES = frozenset(
    {
        "mindatlas_pre_ga_launch_candidate_total",
        "mindatlas_pre_ga_launch_consume_total",
        "mindatlas_pre_ga_launch_state",
        "mindatlas_pre_ga_launch_drift_total",
    }
)
_LABELS = {
    "mindatlas_pre_ga_launch_candidate_total": {"result_code": frozenset({"passed", "failed", "invalid", "other"})},
    "mindatlas_pre_ga_launch_consume_total": {"result_code": frozenset({"succeeded", "replayed", "conflict", "rejected", "other"})},
    "mindatlas_pre_ga_launch_state": {"state": frozenset({"unapproved", "current", "stale", "evidence_unavailable", "other"})},
    "mindatlas_pre_ga_launch_drift_total": {"dimension": frozenset({"target", "evidence", "schema", "runtime", "other"})},
}
_LOCK = RLock()
_COUNTS: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()


def _safe_labels(name: str, labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    allowed = _LABELS[name]
    if set(labels) != set(allowed):
        raise ValueError("pre_ga_metric_labels_invalid")
    result = []
    for key, values in allowed.items():
        value = str(labels[key])
        result.append((key, value if value in values else "other"))
    return tuple(result)


def record_pre_ga_metric(name: str, labels: Mapping[str, str]) -> None:
    if name not in PRE_GA_METRIC_NAMES:
        raise ValueError("pre_ga_metric_name_invalid")
    safe = _safe_labels(name, labels)
    with _LOCK:
        _COUNTS[(name, safe)] += 1


def snapshot_pre_ga_metrics() -> dict[tuple[str, tuple[tuple[str, str], ...]], int]:
    with _LOCK:
        return dict(_COUNTS)


def clear_pre_ga_metrics_for_tests() -> None:
    with _LOCK:
        _COUNTS.clear()


__all__ = [
    "PRE_GA_METRIC_NAMES",
    "clear_pre_ga_metrics_for_tests",
    "record_pre_ga_metric",
    "snapshot_pre_ga_metrics",
]
