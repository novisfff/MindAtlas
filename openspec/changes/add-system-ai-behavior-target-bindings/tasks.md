## 1. Backend
- [x] 1.1 Add persistence for system AI behavior bindings and canonical system default report workflows.
- [x] 1.2 Add system behavior registry, JSON preset loader, binding validation, and binding CRUD APIs.
- [x] 1.3 Add a unified system behavior runner that executes published workflow/agent targets with fixed report contracts and fallback semantics.
- [x] 1.4 Refactor weekly/monthly report generation to use the new runner instead of direct model calls.
- [x] 1.5 Update workflow/agent serialization and delete semantics for system-behavior references and confirm-rebind deletion.

## 2. Frontend
- [x] 2.1 Add a dedicated System AI Behaviors settings route and Settings home entry.
- [x] 2.2 Add system behavior API hooks, cards, target selection UI, reset actions, and delete-confirm UX for referenced targets.
- [x] 2.3 Add i18n coverage for the new settings page and system-behavior deletion/rebind flows.

## 3. Validation
- [x] 3.1 Add backend tests for binding sync, validation, runner execution/fallback, report integration, and delete semantics.
- [x] 3.2 Run targeted backend tests plus frontend build verification.
