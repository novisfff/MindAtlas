# Change: Update Code Executor Signature and Editing Experience

## Why
The first `code_executor` version allowed flexible `inputBindings` and legacy `main(inputs, context)` scripts, which led to unclear contracts and runtime confusion. The editing experience also needed stronger defaults and reliable Python formatting.

## What Changes
- Treat `arg1`/`arg2` as default seed bindings only for new nodes.
- Allow users to add/remove/rename input binding keys.
- Enforce dynamic signature matching: function parameter names must match `inputBindings` keys.
- Update runtime runners (Python/JavaScript) to pass arguments by parameter name mapping.
- Update code executor editor UX:
  - one-line editable binding rows (add/remove/rename key + value)
  - default Python template using `main(arg1: str, arg2: str)`
  - output list panel shows only declared output fields
  - Python formatting switched to Ruff WASM

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/code_executor.py`
  - `backend/app/assistant/skills/code_executor_runners/python_runner.py`
  - `backend/app/assistant/skills/code_executor_runners/js_runner.mjs`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/components/workflow/nodeFactory.ts`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/CodeExecutorNodeSettings.tsx`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/codeExecutorFormat.ts`
  - `frontend/vite.config.ts`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`
