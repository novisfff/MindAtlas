"""Plan 10 AI runtime migration tooling (Deploy-A inventory + package + L2 + HITL).

Task 0: read-only inventory contracts/CLI. Task 1: additive evidence schema,
repository, discovered-only backfill, and prepare/apply/resume CLI for inventory.
Task 2: package/Profile migration + cutover locks + independent verify.
Task 3: L2 package-ID backfill + compatibility seam + verify.
Task 4: HITL entrypoint matrix, creation cutoff, archive/verify.
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
