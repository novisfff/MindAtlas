# Change: Add Workflow Validation Checklist With Real-Time Error Count

## Why
Workflow editor currently validates only at save/test-run entry points, so users cannot continuously see workflow health while editing. This causes avoidable trial-and-error when wiring edges, filling required configs, or resolving dependency issues.

## What Changes
- Add real-time (debounced) workflow validation in editor using existing backend validation endpoint.
- Add a toolbar validation button with error-only badge count.
- Add a checklist panel that groups errors and warnings, supports manual refresh, and displays backend failure state.
- Add frontend warning-only reachability checks for non-output nodes that do not reach output.
- Add issue-to-node locate behavior for both main graph nodes and container subflow nodes.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/components/workflow/workflowValidation.ts`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowValidationChecklistPanel.tsx`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`
