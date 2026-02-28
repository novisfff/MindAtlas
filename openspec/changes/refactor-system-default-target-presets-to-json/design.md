## Context
System default targets (workflow DAG presets and agent runtime defaults) are consumed by sync/reset and now by system baseline rollback flows. Hardcoding these defaults in Python creates a high-change-cost path for product tuning and increases accidental divergence risk.

## Goals / Non-Goals
- Goals:
  - Use JSON files in code repository as the single source-of-truth for system default target presets.
  - Preserve current external APIs and DB schema.
  - Preserve compatibility for existing imports from `definitions.py`.
  - Fail fast when default preset files are missing/invalid.
- Non-Goals:
  - Hot-reload defaults at runtime.
  - Add new HTTP APIs.
  - Introduce schema version migration logic beyond current `schemaVersion=1` validation.

## Decisions
- Decision: Add `defaults_loader.py` to parse/validate JSON into `SkillDefinition` objects.
  - Why: Keeps existing runtime contracts intact while changing only source-of-truth.
- Decision: Keep `definitions.py` as compatibility façade exporting `SKILLS`, `get_skill_by_name`, and named constants.
  - Why: Minimize blast radius in runtime/tests/imports.
- Decision: Baseline rollback for system workflow/agent rehydrates baseline snapshots from JSON defaults.
  - Why: Ensures "system baseline" always maps to canonical preset, even if historical earliest publish snapshot is legacy.
- Decision: Load-once cache (`lru_cache`) with explicit fail-fast exceptions.
  - Why: deterministic behavior and lower runtime overhead.

## Risks / Trade-offs
- Risk: Invalid JSON prevents application startup/first-load.
  - Mitigation: strict tests for loader and clear error messages naming broken file.
- Risk: JSON/manifest format drift.
  - Mitigation: pydantic schema validation and minimal schema (`schemaVersion`, `skills[]`, typed preset files).

## Migration Plan
1. Add JSON manifest and preset files mirroring current system default values.
2. Add loader and switch `definitions.py` to load from JSON.
3. Update rollback baseline logic to use JSON canonical presets.
4. Add regression tests for loader and system agent baseline rollback.

## Open Questions
- None for this iteration.
