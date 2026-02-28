## 1. Backend
- [x] 1.1 Add reset-only helper flow to resolve/create canonical system workflow/agent targets.
- [x] 1.2 Refactor system skill reset to rebind to system targets and reapply JSON baseline config.
- [x] 1.3 Ensure reset does not mutate user-created targets and handles canonical-name conflicts with explicit 409.
- [x] 1.4 Add reset-specific version pruning to keep only latest reset publish version and align draft/published heads.
- [x] 1.5 Reuse same reset semantics in `reset_all_system_skills` for existing and newly created system skills.

## 2. Frontend
- [x] 2.1 Implement two-step destructive reset dialog with typed `RESET` confirmation.
- [x] 2.2 Replace skill reset/reset-all confirmation flow with the new danger dialog.
- [x] 2.3 Expand reset/reset-all query invalidation to include skills, targets, editor details, and versions.

## 3. i18n
- [x] 3.1 Add missing `settings.skills.resetAll` and reset-warning related localization keys in zh/en.

## 4. Verification
- [x] 4.1 Add/extend backend tests for rebind behavior, user-target immutability, and reset history pruning.
- [x] 4.2 Validate OpenSpec change with `openspec validate update-skill-reset-rebind-system-target --strict --no-interactive`.
