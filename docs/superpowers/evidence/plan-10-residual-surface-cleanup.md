# Plan 10 — Residual surface cleanup (2026-07-23)

**Head commit:** residual after `2d6f044`  
**Alembic head:** unchanged `d3a9fcac15c7`

## Removed / stopped
- Startup `LegacySkillShadowAdapter.sync_all` bootstrap mirror
- Config service shadow-sync helpers and call sites
- `skill_catalog/converters.py` (DB skill → SkillDefinition)
- Backend schemas: `AssistantSkillCreate/Update/Response`
- FE `AssistantSkill` type surface; `api/skills.ts` only exports `SkillTargetType`
- `WorkflowReadonlyPreview` skill-graph path; workflow-only preview remains
- `deserializeFromSkill` removed

## Reclassified
- `skill_catalog/` ownership → `shared_capability` (Workflow DAG types only)

## Still retained on purpose
- `skill_catalog/base.py` + `definitions.py` + `defaults_loader.py` for engine/OpenClaw/system assets
- Fail-closed `human_approval_runtime` shell
- 410 legacy `/skills*` routes (OpenAPI tombstones)
- Historical migration package helpers (empty live skill select)

## Tests
107 passed / 79 skipped focused residual suite.
