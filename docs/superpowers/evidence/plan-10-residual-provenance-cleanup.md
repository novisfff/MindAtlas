# Plan 10 — Residual provenance & HITL mount cleanup (2026-07-23)

**Alembic head:** `d3a9fcac15c7` (parent `5cc5a70095f9`)

## Schema
- Dropped `assistant_skill_package.legacy_skill_id`
- Dropped `assistant_main_agent_profile.legacy_skill_id`
- Maintenance ack required (`MINDATLAS_PLAN10_B2_MAINTENANCE_ACK=1`)

## Code
- ORM models no longer define `legacy_skill_id`
- `LegacySkillShadowAdapter` class removed; `best_effort_sync_*` are no-ops
- `ai_registry` / config service no longer trigger shadow sync
- Workflow engine `attach_human_loop_runtime` is a no-op (does not mount blocking HITL)
- Package migration no longer reads/writes `legacy_skill_id`

## Retained (intentional)
- `skill_catalog` type helpers for Workflow DAG (`SkillDefinition`, etc.)
- Fail-closed `human_approval_runtime` shell for import/isinstance stability
- Historical migration package code paths (empty skill select post-drop)
- FE type-only `api/skills.ts` for workflow deserialize shapes

## Tests
108 passed / 75 skipped (focused residual suite including PG destructive + repository).
