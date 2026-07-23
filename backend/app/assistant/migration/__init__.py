"""Plan 10 AI runtime migration tooling (Deploy-A inventory + package + L2 + HITL + rollout + cleanup).

Task 0: read-only inventory contracts/CLI. Task 1: additive evidence schema,
repository, discovered-only backfill, and prepare/apply/resume CLI for inventory.
Task 2: package/Profile migration + cutover locks + independent verify.
Task 3: L2 package-ID backfill + compatibility seam + verify.
Task 4: HITL entrypoint matrix, creation cutoff, archive/verify.
Task 5: side-effect-safe shadow comparison helpers (Eval runtime_shadow).
Task 6: deterministic assignment + pre-insert-only fallback + rollout CLI.
Task 9: Deploy B1 legacy code/UI surface removal (tables retained).
Task 10: cleanup preflight + Deploy B2 destructive L2/HITL schema cleanup.
"""

from __future__ import annotations

from app.assistant.migration.contracts import (
    InventoryItem,
    InventorySnapshot,
    MetricDefinition,
    MigrationBatchResult,
    RolloutDecision,
    RuntimeShadowInputSnapshot,
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
    "RolloutDecision",
    "RuntimeShadowInputSnapshot",
    "SafeInventoryReport",
    "UpstreamGateEntry",
)
