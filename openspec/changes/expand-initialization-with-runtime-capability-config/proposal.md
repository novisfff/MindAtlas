# Change: Expand Initialization with Runtime Capability Config

## Why
MindAtlas still relies on many user-facing runtime capabilities being configured through environment variables, which makes first-run setup opaque and leaves users without clear guidance when attachments, knowledge graph, parsing, or automation are unavailable.

## What Changes
- Expand the initialization wizard from a core three-step setup into a core path plus a capability center for runtime modules.
- Add persisted runtime capability configuration groups for storage, knowledge graph, document parsing, and automation, with encrypted secret handling and env fallback resolution.
- Add a reusable `System Setup` settings page so the same capability modules can be reviewed, validated, and updated after initialization.
- Update runtime consumers and frontend empty states so missing capability configuration produces clear “not configured” behavior instead of generic failures.

## Impact
- Affected specs: `system-settings`
- Affected backend code:
  - `backend/app/system_settings/**`
  - `backend/app/common/storage.py`
  - `backend/app/attachment/**`
  - `backend/app/lightrag/**`
  - `backend/app/scheduler.py`
- Affected frontend code:
  - `frontend/src/features/initialization/**`
  - `frontend/src/features/settings/**`
  - `frontend/src/features/attachments/**`
  - `frontend/src/features/graph/**`
  - `frontend/src/app/**`
  - `frontend/src/locales/{zh,en}/common.json`
