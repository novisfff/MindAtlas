# Deploy B2 Snapshot + Matching-Image Restore Runbook

**Status:** template (Task 0)  
**Applies to:** Deploy B2 — after destructive legacy schema removal  
**Not for:** Deploy A traffic rollback (see `deploy-a-rollback.md`)

## Hard rules

1. Deploy B2 **cannot** be rolled back by re-applying Deploy A binaries against the cleaned schema.
2. Restore requires the **matching pre-B2 snapshot** and the **matching pre-B2 application image/schema**.
3. Downgrade migrations may recreate structural compatibility for local tests only; they must **not** claim restoration of dropped legacy rows without the matching backup.

## Preconditions

- Passing Deploy B2 cleanup gate evidence (inventory, archives, zero blockers).
- Verified backup/export digests for:
  - PostgreSQL logical/physical snapshot
  - Object store prefixes required by Artifacts
  - Migration audit / approval archive exports
- Workers and API processes stopped for the maintenance window.
- Plan 09 operator principal available in production (still a gate; missing today).

## Abort criteria

- Backup digest mismatch vs recorded gate evidence.
- Schema head after restore ≠ expected pre-B2 head.
- Row-count/digest verification fails for any required table.
- Matching image/build revision unavailable.
- Any process still running against the destructive head during restore.

## Procedure

### 1. Stop the world

1. Disable deploy automation that could `alembic upgrade head`.
2. Stop API, `assistant-worker`, evaluation worker, and related consumers.
3. Confirm no open DB sessions from app roles.

### 2. Restore database snapshot

1. Restore the approved pre-B2 snapshot into the target environment (or a disposable verification environment first).
2. Verify:
   - Alembic version table = expected pre-B2 head
   - Table existence for legacy + universal objects
   - Export manifest digests via `digest_backup_export_manifest` helper (or equivalent operator tooling)
3. Do not start app binaries yet.

### 3. Restore object store (if required)

1. Restore Artifact/object prefixes recorded in the export manifest.
2. Verify object count + content digest from the manifest.

### 4. Start matching image set only

1. Deploy the **pre-B2** image digests / build revision recorded with the backup.
2. Confirm `APP_BUILD_REVISION` matches the restore package.
3. Start workers only after API config points at the restored DB.

### 5. Smoke validation

1. Legacy chat path healthy (if restoring pre-cutover).
2. Main Agent path matches the restored rollout revision.
3. L2 read for both legacy-null and package-backed rows as present in snapshot.
4. No Eval Run IDs accepted on production conversation APIs.

### 6. Evidence

- Backup identifiers (store in audit storage — **never commit production IDs to git**)
- Export manifest digest
- Schema head before/after
- Build revision / image digests
- Operator approvals and maintenance acknowledgment

## Task 0 environment skip

Live restore against a production-shaped Postgres/MinIO is **not required** in this worktree when fixtures are unavailable. Task 0 provides:

- this procedure template
- sanitized `backup_export_manifest.json` fixture
- `digest_backup_export_manifest` unit helper

Recorded skip reason: no production launch; `MINDATLAS_TEST_POSTGRES_URL` / MinIO may be unset.
