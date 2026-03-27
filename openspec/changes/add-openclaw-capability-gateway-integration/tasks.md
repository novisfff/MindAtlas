## 1. Backend
- [x] 1.1 Add OpenClaw integration settings persistence, secret rotation, capability catalog storage, and system preset seeding plus legacy migration helpers.
- [x] 1.2 Add `/api/system-settings/openclaw-integration*` admin endpoints for integration state, source picking, catalog CRUD, preset reset, and `/api/integrations/openclaw/*` runtime endpoints.
- [x] 1.3 Implement runtime execution dispatch for system preset adapters plus catalog-bound Tool, Workflow, and Agent sources with structured schema validation.
- [x] 1.4 Add audit-style execution logging and stable agent-friendly error handling for disabled, unavailable, or unauthorized runtime calls.

## 2. Frontend
- [x] 2.1 Add a dedicated OpenClaw Integration settings route and Settings home entry.
- [x] 2.2 Rebuild the settings UI around integration enablement, secret rotation, system preset management, custom catalog items, source picking, and catalog create/edit/delete flows.
- [x] 2.3 Add complete i18n coverage for the new entry, page, dialogs, source-type flows, and status messaging.

## 3. Documentation And Validation
- [x] 3.1 Add implementation docs for the external `openclaw-mindatlas` plugin contract, dynamic capability catalog semantics, and the `MindAtlas Overview` skill prompt.
- [x] 3.2 Add targeted backend tests for catalog migration, source validation, auth, capability exposure, and capability execution routes.
- [x] 3.3 Run `openspec validate add-openclaw-capability-gateway-integration --strict --no-interactive` plus targeted backend/frontend verification.
