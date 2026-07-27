"""Pure digest/compare helpers for inventory snapshots and backup manifests."""

from __future__ import annotations

from typing import Any, Mapping

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.contracts import InventorySnapshot, SnapshotComparison
from app.assistant.migration.gates import GATE_MATRIX
from app.assistant.migration.inventory import digest_items_payload
from app.assistant.migration.metrics import METRIC_DICTIONARY
from app.assistant.migration.ownership import OWNERSHIP_RULES


def digest_inventory_snapshot(snapshot: InventorySnapshot) -> str:
    """Recompute the deterministic inventory snapshot digest."""
    return digest_items_payload(snapshot.items)


def compare_inventory_snapshots(
    left: InventorySnapshot,
    right: InventorySnapshot,
) -> SnapshotComparison:
    """Compare two snapshots by source identity and digest."""
    left_map = {
        (i.subject_kind, i.source_id): i.source_digest for i in left.items
    }
    right_map = {
        (i.subject_kind, i.source_id): i.source_digest for i in right.items
    }
    left_keys = set(left_map)
    right_keys = set(right_map)
    added = right_keys - left_keys
    removed = left_keys - right_keys
    shared = left_keys & right_keys
    changed = {k for k in shared if left_map[k] != right_map[k]}
    left_digest = digest_inventory_snapshot(left)
    right_digest = digest_inventory_snapshot(right)
    return SnapshotComparison(
        equal=left_digest == right_digest and not added and not removed and not changed,
        left_digest=left_digest,
        right_digest=right_digest,
        added_count=len(added),
        removed_count=len(removed),
        changed_count=len(changed),
    )


def digest_backup_export_manifest(manifest: Mapping[str, Any]) -> str:
    """Digest a sanitized backup/export manifest (counts + table digests only)."""
    tables = manifest.get("tables") or []
    table_payload = []
    for row in tables:
        if not isinstance(row, Mapping):
            continue
        table_payload.append(
            {
                "name": str(row.get("name") or ""),
                "row_count": int(row.get("row_count") or 0),
                "content_digest": str(row.get("content_digest") or ""),
            }
        )
    table_payload.sort(key=lambda r: r["name"])
    object_store = manifest.get("object_store") or {}
    payload = {
        "schema_version": int(manifest.get("schema_version") or 1),
        "export_kind": str(manifest.get("export_kind") or ""),
        "schema_head": str(manifest.get("schema_head") or ""),
        "environment": str(manifest.get("environment") or ""),
        "tables": table_payload,
        "object_store": {
            "object_count": int(object_store.get("object_count") or 0)
            if isinstance(object_store, Mapping)
            else 0,
            "content_digest": str(object_store.get("content_digest") or "")
            if isinstance(object_store, Mapping)
            else "",
        },
    }
    return sha256_canonical_json(payload)


def compute_task0_source_snapshot_digest() -> str:
    """Freeze inventory/gate/metric/ownership schema digest for Task 1 dry-runs.

    This is a pure function of the committed Task 0 definition sets, not of any
    production inventory IDs.
    """
    metric_payload = [
        {
            "metric_id": m.metric_id,
            "numerator": m.numerator,
            "denominator": m.denominator,
            "eligibility": m.eligibility,
            "window": m.window,
            "confidence_model": m.confidence_model,
            "gate_threshold": m.gate_threshold,
            "stage_applicability": list(m.stage_applicability),
        }
        for m in METRIC_DICTIONARY
    ]
    metric_payload.sort(key=lambda r: r["metric_id"])

    gate_payload = [
        {
            "plan": g.plan,
            "gate_id": g.gate_id,
            "satisfied": g.satisfied,
            "blocks_stages": list(g.blocks_stages),
            "reason_code": g.reason_code,
            "local_tooling_allowed": g.local_tooling_allowed,
        }
        for g in GATE_MATRIX
    ]
    gate_payload.sort(key=lambda r: (r["plan"], r["gate_id"]))

    ownership_payload = [
        {
            "pattern": pattern,
            "owner_class": owner_class,
            "subject_area": subject_area,
        }
        for pattern, owner_class, subject_area, _notes in OWNERSHIP_RULES
    ]

    payload = {
        "kind": "plan10_task0_source_snapshot",
        "schema_version": 1,
        "contracts": "InventoryItem/InventorySnapshot/MetricDefinition/UpstreamGateEntry",
        "metrics": metric_payload,
        "gates": gate_payload,
        "ownership_rules": ownership_payload,
        "runbooks": [
            "docs/runbooks/ai-runtime-migration/deploy-a-rollback.md",
            "docs/runbooks/ai-runtime-migration/deploy-b2-restore.md",
            "docs/runbooks/ai-runtime-migration/backup-export.md",
        ],
        "alembic_head_at_task0": "027869a00a47",
        "git_head_at_task0": "c93a3a62b374344c40bd23f41e9ab877c10a6aa3",
    }
    return sha256_canonical_json(payload)


__all__ = (
    "compare_inventory_snapshots",
    "compute_task0_source_snapshot_digest",
    "digest_backup_export_manifest",
    "digest_inventory_snapshot",
)
