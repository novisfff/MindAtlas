# Change: Refine LightRAG and Docling Settings Locking

## Why
The first version of the post-initialization LightRAG and Docling settings pages still exposes too many deployment-owned fields and allows edits that do not match the actual runtime lifecycle. Users need clearer startup-state feedback, fewer editable controls, and stronger backend protection for initialization-only or deployment-managed fields.

## What Changes
- Simplify the `LightRAG` settings page to focus on runtime-adjustable values and hide deployment-owned Neo4j and graph-storage fields.
- Treat `LightRAG enabled` and `Docling workerEnabled` as startup-owned state in settings, with clear unavailable messaging when the capability is not started.
- Extend post-initialization LightRAG locking to cover `embeddingHost`, while keeping `embeddingApiKey` editable for credential rotation.
- Reject startup-owned field mutations in backend runtime config updates so the API matches the new UI restrictions.

## Impact
- Affected specs: `system-settings`
- Affected backend code:
  - `backend/app/system_settings/runtime_config_service.py`
  - `backend/tests/test_runtime_config_service.py`
- Affected frontend code:
  - `frontend/src/features/settings/pages/LightRagSettings.tsx`
  - `frontend/src/features/settings/pages/DoclingSettings.tsx`
  - `frontend/src/features/system-setup/runtimeRules.ts`
  - `frontend/src/locales/{zh,en}/common.json`
