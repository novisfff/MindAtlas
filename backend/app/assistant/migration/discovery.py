"""Discovered-only inventory backfill into migration evidence tables.

Exact source IDs/digests create idempotent ``discovered`` rows. Digest drift
appends a blocker event and never silently remaps/marks verified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.migration.contracts import InventoryItem, InventorySnapshot
from app.assistant.migration.repository import (
    DiscoveryBackfillResult,
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)


def _source_type_for(item: InventoryItem) -> str:
    return f"legacy_{item.subject_kind}"


def backfill_discovered_from_snapshot(
    session: Session,
    snapshot: InventorySnapshot,
    *,
    request_id: str,
    actor_principal: str | None = None,
    dry_run: bool = True,
    batch_size: int = 100,
    configuration_digest: str | None = None,
    apply_limit: int | None = None,
) -> DiscoveryBackfillResult:
    """Backfill discovered items from an inventory snapshot.

    When ``dry_run`` is True, no durable rows are written; the report digest still
    reflects the projected create/unchanged/drift counts against current DB state.
    """
    repo = RuntimeMigrationRepository(session)
    config_digest = configuration_digest or sha256_canonical_json(
        {
            "command": "inventory.backfill",
            "batchSize": int(batch_size),
            "snapshotDigest": snapshot.snapshot_digest,
        }
    )
    created = 0
    unchanged = 0
    drifted = 0
    processed = 0
    items: Sequence[InventoryItem] = snapshot.items
    if apply_limit is not None:
        items = items[: max(0, int(apply_limit))]

    batch = None
    if not dry_run:
        batch = repo.prepare_batch(
            command_kind="inventory",
            source_snapshot_digest=snapshot.snapshot_digest,
            configuration_digest=config_digest,
            build_revision=snapshot.build_revision,
            schema_revision=snapshot.schema_head,
            environment=snapshot.environment,
            database_fingerprint=snapshot.database_fingerprint,
            request_id=request_id,
            batch_size=batch_size,
            dry_run_digest=None,
            started_by=actor_principal,
        )
        if str(batch.status) == "prepared":
            batch = repo.transition_batch(
                batch_id=batch.id,
                expected_revision=int(batch.state_revision),
                to_status="running",
            )

    for item in items:
        processed += 1
        if dry_run:
            existing = repo.get_item_by_source(
                subject_kind=item.subject_kind,
                source_type=_source_type_for(item),
                source_id=item.source_id,
            )
            if existing is None:
                created += 1
            elif str(existing.source_digest) == item.source_digest:
                unchanged += 1
            else:
                drifted += 1
            continue

        _row, outcome = repo.upsert_discovered_item(
            subject_kind=item.subject_kind,
            source_type=_source_type_for(item),
            source_id=item.source_id,
            source_name=item.source_name,
            source_name_normalized=item.source_name_normalized,
            source_digest=item.source_digest,
            evidence_json={
                "enabled": item.enabled,
                "isSystem": item.is_system,
                "reasonCode": item.reason_code,
                "namespaceClass": item.namespace_class,
            },
            actor_principal=actor_principal,
            build_revision=snapshot.build_revision,
            reason_code=item.reason_code if item.state == "blocked" else None,
        )
        if outcome == "created":
            created += 1
        elif outcome == "unchanged":
            unchanged += 1
        else:
            drifted += 1

    report_digest = sha256_canonical_json(
        {
            "created": created,
            "unchanged": unchanged,
            "drifted": drifted,
            "processed": processed,
            "snapshotDigest": snapshot.snapshot_digest,
            "dryRun": bool(dry_run),
            "requestId": request_id,
        }
    )

    if batch is not None and not dry_run:
        final = "completed"
        repo.transition_batch(
            batch_id=batch.id,
            expected_revision=int(batch.state_revision),
            to_status=final,
            processed_delta=processed,
            succeeded_delta=created + unchanged,
            blocked_delta=drifted,
            failed_delta=0,
            report_digest=report_digest,
            completed_by=actor_principal,
            resume_cursor=None if processed >= len(snapshot.items) else str(processed),
        )

    return DiscoveryBackfillResult(
        created=created,
        unchanged=unchanged,
        drifted=drifted,
        batch_id=batch.id if batch is not None else None,
        report_digest=report_digest,
    )


def backfill_discovered_from_records(
    session: Session,
    records: Mapping[str, Any],
    *,
    request_id: str,
    actor_principal: str | None = None,
    dry_run: bool = True,
    batch_size: int = 100,
) -> DiscoveryBackfillResult:
    from app.assistant.migration.inventory import scan_inventory_from_records

    snapshot = scan_inventory_from_records(records)
    return backfill_discovered_from_snapshot(
        session,
        snapshot,
        request_id=request_id,
        actor_principal=actor_principal,
        dry_run=dry_run,
        batch_size=batch_size,
    )


__all__ = (
    "backfill_discovered_from_records",
    "backfill_discovered_from_snapshot",
)
