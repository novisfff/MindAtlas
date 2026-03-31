# Change: Make System Workflow / Agent Immutable Baselines With Copy-First Customization

## Why
System workflows and system agent profiles were still behaving like editable targets even though product direction has shifted to treating them as official shipped baselines. That made reset semantics, binding-layer customization, and upgrade reconciliation ambiguous, and it encouraged in-place edits that were hard to reason about once multiple system surfaces started rebinding to the same targets.

## What Changes
- Make all shipped system workflows and system agent profiles read-only baselines that support view, validate/test-run, and copy only.
- Add first-class workflow and agent copy APIs that create editable user-owned duplicates from either the canonical system baseline or the current custom draft.
- Force-reconcile shipped system workflow and agent targets back to their canonical defaults during upgrade/sync, collapsing each to one published baseline version while preserving target IDs.
- Update assistant target editors, version panels, and list pages to show read-only system targets with `Copy As Duplicate` as the primary customization path.
- Keep system skills, system AI behaviors, and OpenClaw system items editable at the binding layer, but surface clear copy-first guidance whenever they point at immutable system workflow/agent targets.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant_config/*`
  - `backend/app/assistant/workflow/engine/runtime_helpers.py`
  - `backend/app/openclaw_integration/*`
  - `backend/alembic/versions/*`
  - `backend/tests/test_assistant_config_service*.py`
  - `backend/tests/test_system_*`
  - `backend/tests/test_openclaw_integration.py`
  - `frontend/src/features/assistant-config/*`
  - `frontend/src/features/settings/pages/OpenClawIntegrationSettings.tsx`
  - `frontend/src/locales/*/common.json`
