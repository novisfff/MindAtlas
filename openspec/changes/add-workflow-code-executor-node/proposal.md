# Change: Add Workflow Code Executor Node

## Why
Current workflow DAG can orchestrate LLM, tool, condition, and container nodes, but it cannot run deterministic local code for lightweight transforms, validation, and data shaping. Teams need a safe way to execute short Python/JavaScript logic inside workflows without introducing new HTTP tools for every use case.

## What Changes
- Add new workflow node type: `code_executor`.
- Support two languages: `python` and `javascript`.
- Use strict script contract: `main(inputs, context)` returns an object.
- Enforce strict output schema against declared `outputFields`.
- Fail fast on runtime errors, timeout, memory over-limit, or schema mismatch.
- Support `code_executor` in both main graph and `iteration`/`loop` container body.
- Add static validator checks for:
  - required config fields
  - entrypoint naming
  - import whitelist and dynamic-import blocking
- Add sandbox execution runtime with default limits:
  - timeout: 5 seconds
  - memory: 128MB
- Add editor support for node creation and property panel editing.
- Add i18n keys for node label and code-executor UI.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/base.py`
  - `backend/app/assistant/skills/code_executor.py`
  - `backend/app/assistant/skills/code_executor_runners/python_runner.py`
  - `backend/app/assistant/skills/code_executor_runners/js_runner.mjs`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/config.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`
