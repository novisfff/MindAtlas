# Change: Add Post-Initialization LightRAG and Docling Settings Pages

## Why
After initialization, users still need a clean place to review and adjust LightRAG and Docling runtime settings. The current `System Setup` page is only suitable as an overview, and it does not provide the field-level locking rules needed for initialization-only LightRAG fields.

## What Changes
- Add dedicated settings pages for `LightRAG` and `Docling` under `/settings/lightrag` and `/settings/docling`.
- Keep `System Setup` as an overview page and link its knowledge-graph and document-parsing modules to the new detail pages.
- Add post-initialization readonly protection for `LightRAG summaryLanguage` and `LightRAG embeddingModelName / embeddingModelId`, while still allowing a one-time fill when those values are empty.
- Reuse the existing runtime config APIs and initialization status API, and share frontend validation rules between initialization and settings pages.

## Impact
- Affected specs: `system-settings`
- Affected backend code:
  - `backend/app/system_settings/runtime_config_service.py`
  - `backend/tests/`
- Affected frontend code:
  - `frontend/src/app/App.tsx`
  - `frontend/src/features/settings/`
  - `frontend/src/features/system-setup/`
  - `frontend/src/features/initialization/pages/SystemInitializationPage.tsx`
  - `frontend/src/locales/{zh,en}/common.json`
