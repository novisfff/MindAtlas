# Change: Tighten OpenClaw Capability Surface And Smart Entry Capture

## Why
OpenClaw currently exposes two overlapping entry-creation paths: a thin-context workflow and a field-level capture tool. That overlap makes the external capability catalog harder to reason about, and the workflow-backed capture path still needs stronger availability checks and safer baseline resync behavior.

## What Changes
- Remove `capture_entry` from the shipped OpenClaw system capability surface while keeping the underlying tool implementation for non-OpenClaw internal reuse.
- Make `submit_context_capture` the single official OpenClaw entry-creation capability and expand its structured output with the key created-entry fields needed by downstream automation.
- Retire legacy OpenClaw catalog items bound to `openclaw_capture_entry` so they remain visible in settings for inspection or rebinding, but never appear in runtime capability discovery or execution.
- Align availability rules so workflow-backed smart capture becomes unavailable when entry types are unavailable, just like search/get entry capabilities.
- Harden system workflow baseline restoration so repeated sync and list flows cannot reinsert duplicate workflow edges.

## Impact
- Affected specs: `external-agent-integration`
- Affected code:
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant/skill_catalog/system_defaults/workflows/openclaw_context_capture*.json`
  - `backend/app/assistant_config/service.py`
  - `backend/tests/test_openclaw_integration.py`
  - `backend/tests/test_assistant_config_service_more.py`
  - `frontend/src/features/settings/*`
  - `frontend/src/locales/*/common.json`
