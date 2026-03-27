# Change: Add Automation Scheduler Toggle in Initialization and Settings

## Why
Users need a clear way to control whether system background automation runs. The backend already resolves an automation runtime config, but the initialization flow does not expose it and settings do not provide a focused page for ongoing management. The scheduler also only applies config at process startup, which makes runtime toggling confusing.

## What Changes
- Expose the scheduler toggle during initialization and in settings, including a dedicated `/settings/automation` page.
- Add runtime scheduler synchronization so the current backend instance starts or stops the scheduler immediately after automation config changes.
- Keep System Setup as an overview page, with Automation linking to the dedicated detail page.
- Update initialization review, settings navigation, and i18n so the automation state is understandable in both Chinese and English.

## Impact
- Affected specs: `system-settings`
- Affected backend code:
  - `backend/app/scheduler.py`
  - `backend/app/system_settings/runtime_config_service.py`
  - `backend/app/main.py`
- Affected frontend code:
  - `frontend/src/features/initialization/`
  - `frontend/src/features/settings/`
  - `frontend/src/features/system-setup/`
  - `frontend/src/app/App.tsx`
  - `frontend/src/locales/{zh,en}/common.json`
