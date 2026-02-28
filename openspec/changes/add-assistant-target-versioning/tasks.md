## 1. Backend
- [x] 1.1 Add migration for workflow/agent version tables and draft/published pointers.
- [x] 1.2 Backfill initial publish version for existing workflows and agents.
- [x] 1.3 Extend schemas with publish/list/rollback contracts and pointer fields.
- [x] 1.4 Refactor service save semantics to write draft versions only.
- [x] 1.5 Add publish/list/rollback service methods for workflows and agents.
- [x] 1.6 Add router endpoints for workflow/agent versions and publish/rollback.
- [x] 1.7 Enforce version retention (100) while preserving head-pointer versions.

## 2. Frontend
- [x] 2.1 Add workflow/agent version APIs and payload types.
- [x] 2.2 Add shared versioning UI components (publish dialog + history panel).
- [x] 2.3 Integrate workflow editor actions: save, save&publish, history, rollback.
- [x] 2.4 Integrate agent editor actions: save, save&publish, history, rollback.
- [x] 2.5 Add i18n keys for versioning actions and messages.
- [x] 2.6 Add system-target “System Default” version entry (highlighted and always pinned first).

## 3. Specification
- [x] 3.1 Add OpenSpec change files and requirement scenarios.
- [x] 3.2 Validate with `openspec validate add-assistant-target-versioning --strict --no-interactive`.

## 4. Retention Safety
- [x] 4.1 Preserve earliest `publish` version for system targets during retention trimming.

## 5. Version Operations and Publish Gate
- [x] 5.1 Add workflow/agent version delete APIs with protected-version guard.
- [x] 5.2 Add workflow/agent draft-history clear APIs (clear non-latest `save` versions only).
- [x] 5.3 Add version panel actions (Delete per row + Clear button) and i18n messaging.
- [x] 5.4 Enforce workflow publish backend validation gate and explicit blocked error message.
