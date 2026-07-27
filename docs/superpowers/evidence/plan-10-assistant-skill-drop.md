# Plan 10 — Drop `assistant_skill` residual (2026-07-23)

**Branch:** `worktree-plan10-runtime-migration`  
**Alembic head:** `5cc5a70095f9` (parent `ca6f564ef4bd`)

## Schema
- Detach FKs from `assistant_skill_package.legacy_skill_id` and `assistant_main_agent_profile.legacy_skill_id` (columns retained as provenance UUIDs, no FK)
- Drop table `assistant_skill`
- Requires `MINDATLAS_PLAN10_B2_MAINTENANCE_ACK=1` (or test override)

## Code
- Removed `AssistantSkill` ORM class and workflow/agent `.skills` relationships
- Config skill CRUD methods fail closed (410) / list returns empty
- `SkillRegistry` DB skill lists empty; resolve is system-only
- L2 mapping no longer joins `AssistantSkill` (alias/system map/canonical only)
- Package migrate `_select_skills` returns empty post-drop
- Legacy shadow adapter `sync_one/all` no-ops without skill rows
- `skill_catalog` types retained for Workflow DAG engine

## Tests
Historical suites that constructed `AssistantSkill` rows are skipped as retired.
Destructive PG tests cover skill table drop after maintenance ack.
