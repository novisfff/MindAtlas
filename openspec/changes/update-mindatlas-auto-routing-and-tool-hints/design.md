## Context
MindAtlas already exposes the right runtime capability surface for OpenClaw, but the route-selection cues are split across shipped skills, plugin-generated tool descriptions, and backend capability metadata. Each layer individually looks plausible, yet together they still leave too much ambiguity for automatic tool selection.

This change keeps the existing 7 system capabilities and all existing `mindatlas_*` tool names. The work is intentionally limited to copy, routing guidance, and discoverability hints on the MindAtlas side.

## Goals
- Make MindAtlas the stable first-choice path for durable memory, historical lookup, recap, relation, graph, and MindAtlas-published workflow or agent tasks.
- Align skill wording with actual runtime behavior: use the session-visible `mindatlas_*` tool surface first.
- Preserve the existing capability catalog, execution endpoints, schemas, and plugin registration model.

## Non-Goals
- No new router tool, catalog tool, or recap-specific capability
- No OpenClaw upstream hot-refresh fix
- No changes to `/api/integrations/openclaw/capabilities` or execution payload shapes

## Design Decisions

### 1. Promote overview into the single router skill
`mindatlas-overview` becomes the only broad, top-level entrypoint. It explicitly covers:
- remember / save / record / store
- find previous records
- recent activity and time-bounded recap
- relation and graph questions
- published MindAtlas workflows and agents

The overview skill also defines the failure mode when the current session does not expose any `mindatlas_*` tools: the agent should say MindAtlas is not available in this session instead of silently treating generic memory as equivalent.

### 2. Narrow the other three skills
- `mindatlas-summary` handles report-first recap and output shaping
- `mindatlas-retrieval` handles search, detail, graph, and cross-record lookup
- `mindatlas-auto-capture` handles save/store/capture behavior

All three sub-skills now start from session-visible `mindatlas_*` tools instead of an abstract catalog-reading requirement, and they all share the same explicit absent-tool rule.

### 3. Add stronger route hints without expanding the tool surface
The plugin keeps registering exactly one OpenClaw tool per capability. The only change is richer description text composed from capability identity plus existing metadata so the tool surface itself advertises the right classes of tasks:
- capture and durable memory
- previous records and time-bounded lookup
- exact record detail
- relation creation
- graph / cross-record synthesis
- weekly or monthly recap, review, digest, and recent activity

### 4. Reword backend metadata instead of changing contracts
The backend system capability registry keeps the same keys, tool names, implementation types, and schemas. Only the human-facing title / description / input summary / output summary copy changes so those capabilities are easier for OpenClaw to select.
