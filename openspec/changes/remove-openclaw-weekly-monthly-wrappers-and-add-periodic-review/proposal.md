# Change: Remove OpenClaw Weekly/Monthly Wrappers And Add Periodic Review

## Why
OpenClaw currently exposes separate weekly and monthly report wrappers that duplicate recap logic, freeze the external capability surface around two fixed periods, and drift from the chat-side periodic review flow. We need one time-range review capability that reuses the same core workflow everywhere and removes the old compatibility layer instead of preserving it forever.

## What Changes
- **BREAKING** Remove the OpenClaw `generate_weekly_report` and `generate_monthly_report` system capabilities, tool wrappers, compatibility aliases, and request/response schemas.
- Add a standalone hidden system workflow asset `periodic_review_core` that accepts `focus`, `period`, `startDate`, and `endDate`, then returns `{ content: string }`.
- Refactor the chat-side `periodic_review` workflow into a text wrapper that extracts the four fields and calls `periodic_review_core` through `workflow_call`.
- Extend system workflow syncing so workflow-call nodes inside system assets can resolve `targetSystemAssetKey` to the current published workflow id/version at sync time.
- Seed one workflow-backed OpenClaw default capability `generate_periodic_review` with the tool name `mindatlas_generate_periodic_review`, remove the old weekly/monthly system items, and treat custom bindings that still point at the removed report sources as unavailable.
- Update OpenClaw plugin routing hints, shipped skills, and backend/plugin tests to use the unified periodic review capability.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant/workflow/system_assets/*`
  - `backend/app/assistant_config/service.py`
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant/tools/openclaw_tools.py`
  - `backend/app/assistant/tools/__init__.py`
  - `backend/tests/test_assistant_config_service_more.py`
  - `backend/tests/test_openclaw_integration.py`
  - `backend/tests/test_system_defaults_loader.py`
  - `backend/tests/test_system_workflow_layout_presets.py`
  - `integrations/openclaw-mindatlas/*`
