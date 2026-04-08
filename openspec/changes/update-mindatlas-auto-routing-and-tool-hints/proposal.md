# Change: Tighten MindAtlas Auto Routing And Tool Hints

## Why
MindAtlas-related requests still miss the intended `mindatlas_*` runtime capabilities too often, even after the plugin and shipped skills are installed. The current behavior is shaped by several gaps acting together:

- the shipped skills do not present a single strong MindAtlas router entrypoint
- sub-skills still describe an abstract `catalog-first` flow instead of the real session-visible `mindatlas_*` tool surface
- plugin-generated tool descriptions are too capability-local and do not reinforce MindAtlas as the primary system path for durable memory, retrieval, recap, relation, and graph work
- shipped backend capability metadata is correct but too neutral to reliably win routing against generic memory or other tools

## What Changes
- Promote `mindatlas-overview` into the main router skill for all durable-memory, historical, recap, relation, graph, and MindAtlas-published workflow or agent tasks.
- Narrow `mindatlas-summary`, `mindatlas-retrieval`, and `mindatlas-auto-capture` into subordinate strategies with explicit session-visible tool checks and fallback behavior.
- Strengthen plugin-generated `mindatlas_*` tool descriptions with routing hints and task keywords, without adding new tools or changing the runtime API.
- Update shipped backend capability metadata copy so the existing 7 system capabilities are easier for OpenClaw to select.
- Update docs and settings copy to explain the session-visible MindAtlas tool model, the current new-session requirement, and the expected explicit failure mode when MindAtlas tools are absent from the current session.

## Impact
- Affected specs:
  - `openclaw-plugin-package`
  - `external-agent-integration`
- Affected code:
  - `integrations/openclaw-mindatlas/skills/*`
  - `integrations/openclaw-mindatlas/src/tools.ts`
  - `integrations/openclaw-mindatlas/tests/plugin.test.ts`
  - `backend/app/openclaw_integration/registry.py`
  - `backend/tests/test_openclaw_integration.py`
  - `docs/openclaw/README.md`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`
