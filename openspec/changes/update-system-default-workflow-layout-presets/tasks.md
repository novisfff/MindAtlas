## 1. Implementation
- [x] 1.1 Update `quick_stats` workflow preset coordinates to optimized horizontal layout.
- [x] 1.2 Update `smart_capture` workflow preset coordinates to optimized horizontal layout.
- [x] 1.3 Update `periodic_review` workflow preset coordinates to optimized horizontal + symmetric branch layout.
- [x] 1.4 Clarify `sync_system_skills`/reset behavior boundary via service comments (new/create and reset apply presets; normal sync does not overwrite existing layouts).

## 2. Testing
- [x] 2.1 Add backend tests validating preset coordinates on first sync creation.
- [x] 2.2 Add backend tests validating reset restores preset coordinates.
- [x] 2.3 Add backend tests validating normal sync does not override existing manual coordinates.

## 3. Specification
- [x] 3.1 Add OpenSpec change files (`proposal/tasks/spec`).
- [x] 3.2 Validate with `openspec validate update-system-default-workflow-layout-presets --strict --no-interactive`.
