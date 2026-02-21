## Context
Skill routing metadata and executable configuration were stored together in `assistant_skill`. This couples concerns and blocks reuse. We need a binding model where skills can route to independently managed executables.

## Goals
- Decouple routing (`Skill`) from executable body (`Workflow` / `Agent`).
- Enforce exactly one target binding per skill.
- Allow multiple skills to share the same workflow or agent profile.
- Keep one-version compatibility for existing skill-workflow endpoints.

## Non-Goals
- Target version pinning (real-time propagation only in this phase).
- Removing all legacy columns immediately.
- Introducing separate historical stacks per target.

## Data Model
- New tables:
  - `assistant_workflow`
  - `assistant_workflow_node`
  - `assistant_workflow_edge`
  - `assistant_agent_profile`
- `assistant_skill` now binds executable via:
  - `workflow_id` (nullable FK)
  - `agent_profile_id` (nullable FK)
- Enforced by DB constraint:
  - exactly one of `workflow_id` / `agent_profile_id` must be non-null.

## API Shape
- Canonical endpoints:
  - `/api/assistant-config/workflows` CRUD
  - `/api/assistant-config/workflows/{id}/validate`
  - `/api/assistant-config/workflows/{id}/test-run`
  - `/api/assistant-config/agents` CRUD
- Compatibility endpoints (one transition version):
  - `/api/assistant-config/skills/{id}/workflow*`
  - returns `409` when skill is agent-bound.

## Runtime Conversion
- Registry/converter resolves skill pattern from binding first:
  - `workflow_id` => `workflow_dag`
  - `agent_profile_id` => `agent_loop`
- Workflow nodes/edges are loaded from bound workflow entity.
- Agent prompt/kb/tools are loaded from bound agent profile.

## Deletion Protection
- Workflow or Agent profile cannot be deleted when referenced by any skill.
- Service returns `409` with referencing skill names.
- Frontend disables delete action when reference count > 0.

## Frontend UX Model
- Settings entry is unified as `Assistant Targets` instead of separate workflow/agent cards.
- Target list is mixed and typed (`workflow` / `agent`) with shared actions.
- Target rows are expandable (single-expand mode):
  - Workflow row shows read-only mini DAG preview and editor jump.
  - Agent row shows description, KB state, selected tools, and prompt details.
- Skill binding uses one selector over normalized target options; UI no longer asks users to pick target type first.
- Assistant Targets page auto-normalizes legacy disabled targets to enabled once loaded.
- Selector options are shown without disabled suffix after normalization.
- Agent editing is moved to dedicated page (`/settings/agent-editor/:id`) with:
  - `systemPrompt` (required)
  - `kbConfig.enabled` toggle
  - `tools[]` multi-select merged from system + custom tools

## Agent Draft Test-Run
- Agent editor provides an in-page dual-pane workspace:
  - left pane: configuration form and save action
  - right pane: draft test-run input, stream switch, result, and trace timeline
- Test-run executes against unsaved draft payload instead of persisted profile.
- Runtime isolation:
  - uses `assistant-config` dedicated endpoint
  - does not create `assistant conversations/messages`
- SSE events are lightweight and chat-compatible:
  - `run_start`, `content_delta`, `tool_call_start/end`, `analysis_start/delta/end`, `run_error`, `run_end`
- `content_delta` is merged server-side with thresholds (`120ms` or `96 chars`) to reduce event noise.

## Risks
- Mixed old/new fields during compatibility phase may diverge if external writers bypass service layer.
- Shared-target edits affect all bound skills immediately (intended by design).
