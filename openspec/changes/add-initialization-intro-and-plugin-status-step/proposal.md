# Change: Add Initialization Intro and Capability Status Step

## Why
The current initialization wizard jumps from language selection straight into provider setup. New users do not get a quick explanation of what MindAtlas is or what the remaining setup will configure, and they also cannot immediately see whether core deployment capabilities are already enabled.

## What Changes
- Insert a read-only `System Introduction` step immediately after the system language step in the initialization wizard.
- Localize the new step in both Chinese and English, and make it switch immediately after the user changes language.
- Show the current `LightRAG` and `Docling` enabled states using the existing initialization defaults runtime config without changing backend APIs or the final submission payload.

## Impact
- Affected specs: `system-settings`
- Affected frontend code:
  - `frontend/src/features/initialization/pages/SystemInitializationPage.tsx`
  - `frontend/src/features/initialization/store.ts`
  - `frontend/src/locales/{zh,en}/common.json`
