# Change: Refactor Assistant Orchestration Module Boundaries

## Why
`app.assistant.skills` mixed three responsibilities: skill catalog, orchestration runtime, and workflow execution/validation. This increased coupling and made maintenance risky for large files.

## What Changes
- Introduce explicit packages:
  - `app.assistant.orchestration`
  - `app.assistant.workflow`
  - `app.assistant.skill_catalog`
- Move workflow runtime and validator to new workflow packages.
- Move skill metadata/defaults/converters to `skill_catalog`.
- Move routing/agent runtime/chat event adapter to `orchestration`.
- Remove stale `SkillExecutor` exposure and drop `app.assistant.skills.*` imports.
- Keep external HTTP API and workflow semantics unchanged.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/orchestration/*`
  - `backend/app/assistant/workflow/*`
  - `backend/app/assistant/skill_catalog/*`
  - `backend/app/assistant/service.py`
  - `backend/app/assistant_config/*` (imports only)
  - `backend/tests/*` (imports and patch paths)
