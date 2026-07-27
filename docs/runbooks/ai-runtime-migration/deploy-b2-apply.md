# Deploy B2 Apply Preflight (local / maintenance)

**Status:** Plan 10 Task 10  
**Gate:** `deploy_b2`  
**Destructive revision:** `ca6f564ef4bd` (`remove legacy assistant skill runtime`)

## What this release drops

- L2 `skill_name` column and legacy null-package name unique index
- L2 `skill_package_id` + `memory_namespace` become `NOT NULL`
- Full unique index on `(conversation_id, skill_package_id, memory_namespace)`
- Table `assistant_human_approval` (terminal history must already live in
  `assistant_legacy_approval_archive`)

**Residual (not dropped here):** `assistant_skill` table and
`assistant_skill_package.legacy_skill_id` FK — still referenced by inventory,
legacy adapter, package provenance, and several admin/services.

## Hard rules

1. Environment ack alone is **not** authority. Preflight recomputes hard counts.
2. Do not run against production without backups and a passed cleanup gate.
3. Stop API/workers before `alembic upgrade` for B2.
4. After B2, do not redeploy pre-B2 binaries that write `skill_name` or create
   null-package L2 rows.

## Local / partner apply (maintenance ack)

```bash
export MINDATLAS_PLAN10_B2_MAINTENANCE_ACK=1
# optional test-only override used by PG tests:
# export MINDATLAS_PLAN10_B2_TEST_OVERRIDE=1

cd backend
source .venv/bin/activate

# 1) Evaluate / preflight (dry-run then apply with operator principal)
python -m app.assistant.migration.cli cleanup evaluate \
  --gate deploy_b2 \
  --environment local \
  --database-fingerprint "<db-fingerprint>" \
  --source-snapshot-digest "<64-hex>" \
  --expected-schema-head 6417df0243be \
  --expected-build-revision "<build>" \
  --request-id "b2-eval-$(date +%s)" \
  --batch-size 50 \
  --dry-run \
  --report-json /tmp/cleanup-eval.json

MINDATLAS_MIGRATION_OPERATOR_PRINCIPAL=local-op \
python -m app.assistant.migration.cli cleanup preflight \
  --gate deploy_b2 \
  --environment local \
  --database-fingerprint "<db-fingerprint>" \
  --source-snapshot-digest "<64-hex>" \
  --expected-schema-head 6417df0243be \
  --expected-build-revision "<build>" \
  --request-id "b2-pre-$(date +%s)" \
  --batch-size 50 \
  --apply \
  --operator-principal local-op \
  --report-json /tmp/cleanup-preflight.json

# 2) With API/workers stopped:
alembic upgrade head   # applies ca6f564ef4bd when ack + counts pass
```

## Preflight hard counts

Migration upgrade fails with `MINDATLAS_PLAN10_B2_PREFLIGHT_BLOCKED` unless:

- `MINDATLAS_PLAN10_B2_MAINTENANCE_ACK=1` (or test override)
- pending `assistant_human_approval` rows = 0
- invalid L2 rows (null package / empty namespace) = 0
- nonterminal legacy runs: waived only when maintenance ack is set (local path)

Cleanup evaluate also fails the durable gate when blocked migration items or
unresolved capability reconciliation counts are non-zero.

## Downgrade

Structural only. Requires `MINDATLAS_PLAN10_B2_DOWNGRADE_ACK=1` and **does not**
restore dropped approval rows or original skill_name values. Production rollback
uses the matching pre-B2 snapshot + image (see `deploy-b2-restore.md`).
