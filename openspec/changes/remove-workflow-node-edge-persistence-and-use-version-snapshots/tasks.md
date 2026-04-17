## 1. Spec And Model Update
- [x] 1.1 Add OpenSpec delta describing version snapshots as the single workflow graph source
- [x] 1.2 Update ORM models to stop defining workflow/skill node and edge persistence

## 2. Service Refactor
- [x] 2.1 Refactor workflow reads to materialize graph data from draft version snapshots only
- [x] 2.2 Refactor workflow writes, publish, rollback, copy, and system baseline restore to operate only on snapshots
- [x] 2.3 Remove legacy node/edge-based fallback paths and relational child rewrites

## 3. Migration And Verification
- [x] 3.1 Add Alembic migration to backfill missing snapshots if needed and drop workflow/skill node-edge tables
- [x] 3.2 Update backend tests for snapshot-only persistence
- [x] 3.3 Validate the change with targeted backend tests and `openspec validate --strict --no-interactive`
