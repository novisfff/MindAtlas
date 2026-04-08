## Context
The current OpenClaw smart-capture flow is already workflow-backed, which is the right long-term model, but two seams are still too heavy:

1. the public capture schema exposes fields that are really request metadata or downstream extraction hints
2. duplicate handling ends at "always create"

The new behavior should keep the external surface thin while moving decision-making deeper into MindAtlas.

## Goals
- Make `submit_context_capture` the one thin public write capability with only `context`.
- Ensure all required non-business context still comes from OpenClaw and is visible inside workflow templates through runtime `sys` variables.
- Add conservative auto-merge so repeat captures can update the same durable record when there is a clear single match.
- Keep merge as an internal workflow behavior rather than a new public OpenClaw capability.

## Non-Goals
- No backward-compatible acceptance of the older 8-field capture payload.
- No new public OpenClaw merge capability.
- No probabilistic multi-target merge or relation-creation side path in this change.

## Decisions
### Thin Input Contract
`submit_context_capture` will expose only:
- `context: string` required

OpenClaw must place all business details in that context block. Request metadata remains out-of-band in headers.

### Runtime Metadata Bridge
Workflow execution context will add:
- `openclaw_source`
- `openclaw_channel`
- `openclaw_session`
- `openclaw_tool`

`parse_execution_context()` will project those values into `sys_vars` so workflow templates can reference `{{sys.openclaw_source}}` and related fields without changing general template syntax.

### Merge Strategy
The workflow will:
1. materialize a candidate entry payload from `context`
2. search recent candidate entries using the materialized title and tags
3. ask an LLM node to choose `create` or `merge`, with at most one target entry id
4. default to `create` unless there is one clear match
5. if merging, fetch the target entry detail and rewrite the full final record from old + new content

This keeps merge conservative and deterministic enough for workflow use.

### Internal Update Tool
An internal-only `update_entry` LangChain tool will be added to `app.assistant.tools`. It will be resolvable by workflow tool nodes, but it will not be listed in the visible system tool catalog and will not be exposed as an OpenClaw runtime capability.

The tool will reuse `EntryService.update(...)` and return the same structured shape as `create_entry`, plus `updated_at`, so workflow output branches can stay schema-identical.

## Validation And Rollout
- System workflow preset sync must update the shipped workflow snapshot automatically.
- Runtime catalog tests must verify the new one-field contract.
- Plugin tests must verify the registered tool schema and execution path use `submit_context_capture` with `context`.
- Workflow execution tests must cover both create and merge paths, including header-to-`sys` projection.
