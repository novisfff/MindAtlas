## 1. Data Model & Migration
- [x] 1.1 Add workflow/agent profile tables and binding columns on `assistant_skill`
- [x] 1.2 Add XOR binding constraint on `assistant_skill`
- [x] 1.3 Backfill existing skills into workflow/agent targets

## 2. Backend API & Runtime
- [x] 2.1 Extend schemas for workflow/agent CRUD and skill target binding
- [x] 2.2 Refactor service layer for binding-aware create/update/reset logic
- [x] 2.3 Add canonical `/workflows` and `/agents` routes
- [x] 2.4 Keep legacy skill workflow routes as compatibility forwarding
- [x] 2.5 Update registry/converters to build `SkillDefinition` from bound targets
- [x] 2.6 Update workflow test-run service with workflow-id entrypoint

## 3. Frontend UX
- [x] 3.1 Extend skill API types with target binding fields
- [x] 3.2 Add workflow/agent API modules and query hooks
- [x] 3.3 Replace skill mode form with single target selector + auto type mapping
- [x] 3.4 Add unified Assistant Targets page (mixed list + reference-aware delete UX)
- [x] 3.5 Add dedicated Agent editor page with prompt + KB + tools config
- [x] 3.6 Migrate workflow editor route/data-loading to workflow-id
- [x] 3.7 Add i18n keys for new settings/cards/form labels
- [x] 3.8 Optimize Assistant Targets list with row expansion details and remove enable/disable toggle
- [x] 3.9 Upgrade Agent editor to dual-pane workspace and integrate draft test-run panel

## 4. Validation
- [x] 4.1 Frontend build (`npm run build`)
- [ ] 4.2 Backend tests (`pytest -q`) in dependency-complete environment
- [x] 4.3 Python syntax compile checks for touched backend modules
- [x] 4.4 OpenSpec strict validation
