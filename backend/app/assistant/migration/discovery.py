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
    CODE_CONFLICT,
    CODE_DRIFT,
    CODE_FORBIDDEN_TRANSITION,
    CODE_INVALID_INPUT,
    CODE_NOT_FOUND,
    DiscoveryBackfillResult,
    RuntimeMigrationRepository,
    RuntimeMigrationRepositoryError,
)


def _source_type_for(item: InventoryItem) -> str:
    return f"legacy_{item.subject_kind}"


def _config_digest_for(
    snapshot: InventorySnapshot,
    *,
    batch_size: int,
    configuration_digest: str | None,
) -> str:
    return configuration_digest or sha256_canonical_json(
        {
            "command": "inventory.backfill",
            "batchSize": int(batch_size),
            "snapshotDigest": snapshot.snapshot_digest,
        }
    )


def _project_counts(
    repo: RuntimeMigrationRepository,
    items: Sequence[InventoryItem],
) -> tuple[int, int, int, int]:
    created = 0
    unchanged = 0
    drifted = 0
    processed = 0
    for item in items:
        processed += 1
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
    return created, unchanged, drifted, processed


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
    batch_id: UUID | None = None,
    prepare_only: bool = False,
) -> DiscoveryBackfillResult:
    """Backfill discovered items from an inventory snapshot.

    Modes:
    - ``dry_run=True`` (default): project create/unchanged/drift counts; no durable
      writes unless ``prepare_only=True``.
    - ``prepare_only=True``: write a durable ``prepared`` batch with
      ``dry_run_digest`` bound to the projected report digest; no item upserts.
    - ``dry_run=False``: upsert discovered items. When ``batch_id`` is provided,
      continue that existing prepared/running batch instead of opening a new one
      via ``request_id``.
    """
    if prepare_only and batch_id is not None:
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT,
            "prepare_only cannot continue an existing batch_id",
        )
    if prepare_only and not dry_run:
        raise RuntimeMigrationRepositoryError(
            CODE_INVALID_INPUT,
            "prepare_only requires dry_run=True",
        )

    repo = RuntimeMigrationRepository(session)
    config_digest = _config_digest_for(
        snapshot,
        batch_size=batch_size,
        configuration_digest=configuration_digest,
    )
    items: Sequence[InventoryItem] = snapshot.items
    if apply_limit is not None:
        items = items[: max(0, int(apply_limit))]

    if dry_run:
        created, unchanged, drifted, processed = _project_counts(repo, items)
        report_digest = sha256_canonical_json(
            {
                "created": created,
                "unchanged": unchanged,
                "drifted": drifted,
                "processed": processed,
                "snapshotDigest": snapshot.snapshot_digest,
                "dryRun": True,
                "requestId": request_id,
            }
        )
        prepared_batch_id: UUID | None = None
        if prepare_only:
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
                dry_run_digest=report_digest,
                started_by=actor_principal,
            )
            if str(batch.status) != "prepared":
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    f"prepare_only expected prepared batch, got {batch.status}",
                )
            # Bind dry_run_digest on idempotent request_id retries when first write
            # already stored it; reject drift if a different digest is bound.
            if batch.dry_run_digest and str(batch.dry_run_digest) != report_digest:
                raise RuntimeMigrationRepositoryError(
                    CODE_CONFLICT,
                    "request_id already bound to a different dry_run_digest",
                )
            if batch.dry_run_digest is None:
                batch.dry_run_digest = report_digest
                session.flush()
            prepared_batch_id = batch.id
        return DiscoveryBackfillResult(
            created=created,
            unchanged=unchanged,
            drifted=drifted,
            batch_id=prepared_batch_id,
            report_digest=report_digest,
        )

    # Mutation path: continue existing batch or open a new one.
    batch = None
    if batch_id is not None:
        batch = repo.get_batch(batch_id)
        if batch is None:
            raise RuntimeMigrationRepositoryError(CODE_NOT_FOUND, "batch not found")
        if str(batch.status) not in {"prepared", "running"}:
            raise RuntimeMigrationRepositoryError(
                CODE_FORBIDDEN_TRANSITION,
                f"cannot apply/resume batch in status {batch.status}",
            )
        if (
            str(batch.source_snapshot_digest) != snapshot.snapshot_digest
            or str(batch.configuration_digest) != config_digest
            or str(batch.build_revision) != str(snapshot.build_revision)
            or str(batch.schema_revision) != str(snapshot.schema_head)
        ):
            raise RuntimeMigrationRepositoryError(
                CODE_DRIFT,
                "batch digest drift; create a new batch",
            )
        # Prefer the durable request id bound to the batch row.
        request_id = str(batch.request_id)
    else:
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

    created = 0
    unchanged = 0
    drifted = 0
    processed = 0
    for item in items:
        processed += 1
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
            "dryRun": False,
            "requestId": request_id,
        }
    )

    # On resume of a batch that already recorded counters (partial complete was
    # never written mid-loop today), send full window deltas only when counters
    # are still zero; otherwise complete with zero deltas to avoid double-count.
    already_processed = int(batch.processed_count or 0)
    if already_processed == 0:
        processed_delta = processed
        succeeded_delta = created + unchanged
        blocked_delta = drifted
    else:
        processed_delta = 0
        succeeded_delta = 0
        blocked_delta = 0

    repo.transition_batch(
        batch_id=batch.id,
        expected_revision=int(batch.state_revision),
        to_status="completed",
        processed_delta=processed_delta,
        succeeded_delta=succeeded_delta,
        blocked_delta=blocked_delta,
        failed_delta=0,
        report_digest=report_digest,
        completed_by=actor_principal,
        resume_cursor=None if processed >= len(snapshot.items) else str(processed),
    )

    return DiscoveryBackfillResult(
        created=created,
        unchanged=unchanged,
        drifted=drifted,
        batch_id=batch.id,
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
    batch_id: UUID | None = None,
    prepare_only: bool = False,
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
        batch_id=batch_id,
        prepare_only=prepare_only,
    )


__all__ = (
    "backfill_discovered_from_records",
    "backfill_discovered_from_snapshot",
)
