"""Plan 10 AI runtime migration tooling (Deploy-A inventory + evidence).

Task 0: read-only inventory contracts/CLI. Task 1: additive evidence schema,
repository, discovered-only backfill, and prepare/apply/resume CLI for inventory.
Runtime selection remains legacy; traffic routing is later.
"""

from __future__ import annotations

from app.assistant.migration.contracts import (
    InventoryItem,
    InventorySnapshot,
    MetricDefinition,
    MigrationBatchResult,
    SafeInventoryReport,
    UpstreamGateEntry,
)
from app.assistant.migration.metrics import METRIC_DICTIONARY

__all__ = (
    "InventoryItem",
    "InventorySnapshot",
    "METRIC_DICTIONARY",
    "MetricDefinition",
    "MigrationBatchResult",
    "SafeInventoryReport",
    "UpstreamGateEntry",
)
