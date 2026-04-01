# OpenClaw Integration Contract

This document defines the MindAtlas side of the `openclaw-mindatlas` integration.

## Docs Navigation

This directory contains the MindAtlas-side OpenClaw contract, the current phase plan, and lightweight reference pages for the shipped MindAtlas skills.

### Phase Plan

- [Phase 1 Plan: Memory / Retrieval / Summary (ZH-CN)](./phase-1-memory-retrieval-summary-plan.zh-CN.md)
  - First-phase implementation plan focused on three core loops:
    - capture high-value experiences through context submission
    - retrieve past records naturally
    - summarize personal history through reports and topic-oriented synthesis

### Shipped Skills

These are the canonical prompt sources that ship with the plugin package:

- [MindAtlas Overview Skill](../../integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md)
  - High-level positioning for what MindAtlas is and when OpenClaw should prefer it

- [MindAtlas Auto Capture Skill](../../integrations/openclaw-mindatlas/skills/mindatlas-auto-capture/SKILL.md)
  - Capture policy for durable memory and context submission

- [MindAtlas Retrieval Skill](../../integrations/openclaw-mindatlas/skills/mindatlas-retrieval/SKILL.md)
  - Retrieval routing across search, detail lookup, and graph-style recall

- [MindAtlas Summary Skill](../../integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md)
  - Summary routing for weekly, monthly, and topic-oriented reviews

### Skill Reference Pages

These docs pages are lightweight explainers only. They are not the prompt source of truth:

- [Overview Skill Reference](./mindatlas-overview-skill.md)
- [Auto Capture Skill Reference](./mindatlas-auto-capture-skill.md)
- [Retrieval Skill Reference](./mindatlas-retrieval-skill.md)
- [Summary Skill Reference](./mindatlas-summary-skill.md)

## Suggested Reading Order

1. This integration contract
2. `phase-1-memory-retrieval-summary-plan.zh-CN.md`
3. `integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md`
4. `integrations/openclaw-mindatlas/skills/mindatlas-auto-capture/SKILL.md`
5. `integrations/openclaw-mindatlas/skills/mindatlas-retrieval/SKILL.md`
6. `integrations/openclaw-mindatlas/skills/mindatlas-summary/SKILL.md`

---

## Shipped Plugin Package

This repository now ships the OpenClaw-side plugin package at:

- `integrations/openclaw-mindatlas/`

Local install:

```bash
openclaw plugins install ./integrations/openclaw-mindatlas
```

The install step should succeed even before `baseUrl` and `integrationSecret` are filled in. After installation, add the plugin config and then restart the OpenClaw Gateway:

```json
{
  "plugins": {
    "entries": {
      "openclaw-mindatlas": {
        "enabled": true,
        "config": {
          "baseUrl": "http://your-mindatlas-host",
          "integrationSecret": "paste-the-secret-from-mindatlas",
          "requestTimeoutMs": 15000,
          "catalogRefreshTtlSec": 300
        }
      }
    }
  }
}
```

The plugin bundles 4 shipped MindAtlas skills:

- `mindatlas-overview`
- `mindatlas-auto-capture`
- `mindatlas-retrieval`
- `mindatlas-summary`

These skills ship with the plugin package and guide OpenClaw's calling strategy. The actual callable tools still come from the live MindAtlas capability catalog.

Current OpenClaw releases may fail to surface plugin-manifest `skills` into the global skill registry. The MindAtlas plugin now works around that by syncing its 4 shipped skills into the active custom skills directory when the plugin service starts. Existing sessions still may need a new session or Gateway reload before refreshed skills or tools show up in the prompt surface.

## Positioning

- `OpenClaw`: chat-channel entrypoint, tool orchestration layer, channel adapters.
- `MindAtlas`: capability backend, knowledge system of record, workflow/report execution backend.

OpenClaw should not call MindAtlas frontend APIs directly. It should consume the dedicated OpenClaw integration facade.

The current documentation assumes a personal single-user MindAtlas system. Multi-user identity mapping and tenant isolation are intentionally out of scope for this phase.

## Admin Model

MindAtlas exposes OpenClaw through a configurable capability catalog.

- Integration-wide state lives in `Settings -> OpenClaw Integration`.
- A catalog item is the only unit OpenClaw can see or execute.
- Catalog items have their own OpenClaw-facing:
  - `capabilityKey`
  - `toolName`
  - `title`
  - `description`
  - `inputSchema`
  - `outputSchema`
- Each catalog item binds to one MindAtlas source:
  - `tool`
  - `workflow`
  - `agent`
- Shipped defaults are ordinary first-class system items with `isSystemItem = true` and a `systemDefaultKey`.
- System items and custom items share the same catalog model; reset only restores shipped system item defaults.

The plugin must not assume a fixed built-in tool list. It should always trust live discovery metadata from MindAtlas.

## Current Implementation vs Recommended Phase 1 Path

- The current runtime can expose field-level catalog items such as entry creation, search, relation creation, graph query, reports, custom tools, workflows, and agents.
- The default system recording item is now workflow-backed and accepts thin context submission instead of requiring full entry fields from OpenClaw.
- For Phase 1 recording behavior, the recommended evolution is **not** to keep OpenClaw assembling every final entry field itself.
- Instead, OpenClaw should prefer an administrator-exposed high-level recording capability or capture workflow that accepts relevant context, while MindAtlas internally materializes the final entry type, summary, content, tags, relations, and dedupe behavior.
- If a field-level recording capability still exists, treat it as a transitional or manual-entry path rather than the preferred automatic capture interface. MindAtlas keeps the legacy `capture_entry` tool-backed system item for compatibility, but it is disabled by default.
- Automatic capture in this phase is a prompt-driven best-effort behavior, not a guaranteed system hook.

## Runtime Auth

All runtime calls from OpenClaw to MindAtlas use a single app-level bearer secret.

- Header: `Authorization: Bearer <integration_secret>`
- Secret source: generated from `Settings -> OpenClaw Integration`
- Secret storage: encrypted in MindAtlas `app_setting`

If the integration is disabled or the bearer secret is invalid, MindAtlas returns a stable API error body:

```json
{
  "success": false,
  "code": 40161,
  "message": "Invalid OpenClaw integration secret."
}
```

If the integration is enabled without a configured secret, MindAtlas rejects that admin update. OpenClaw runtime calls also fail when the integration is disabled.

## Discovery Flow

The plugin should always discover the exposed catalog first:

```http
GET /api/integrations/openclaw/capabilities
Authorization: Bearer <integration_secret>
```

MindAtlas returns only catalog items with `enabled = true`. Each item includes:

- `capabilityKey`
- `toolName`
- `title`
- `description`
- `sourceType`
- `implementationType`
- `available`
- `availabilityReason`
- `inputSummary`
- `outputSummary`
- `inputSchema`
- `outputSchema`
- `toolResponseMode`

Recommended plugin behavior:

1. Read the capability catalog at startup or on refresh.
2. Register one OpenClaw tool per returned capability.
3. Skip tools marked `available = false`, or register them with a graceful fallback message.
4. Refresh the catalog after administrators change the MindAtlas catalog.
5. If a tool name, exported title, exported description, or input schema changes, start a new OpenClaw session or reload the OpenClaw Gateway / plugin so tool registration metadata is rebuilt.
6. Never hard-code a built-in capability list on the OpenClaw side.

Example discovery item:

```json
{
  "capabilityKey": "project_digest",
  "toolName": "mindatlas_project_digest",
  "title": "Project Digest",
  "description": "Run the published project digest workflow in MindAtlas.",
  "sourceType": "workflow",
  "implementationType": "workflow",
  "available": true,
  "availabilityReason": null,
  "inputSummary": "projectId (string), includeRisks (boolean)",
  "outputSummary": "title (string), summary (string), highlights (array[string])",
  "inputSchema": {
    "type": "object",
    "properties": {
      "projectId": { "type": "string" },
      "includeRisks": { "type": "boolean" }
    },
    "required": ["projectId"],
    "additionalProperties": false
  },
  "outputSchema": {
    "type": "object",
    "properties": {
      "title": { "type": "string" },
      "summary": { "type": "string" },
      "highlights": {
        "type": "array",
        "items": { "type": "string" }
      }
    },
    "required": ["title", "summary", "highlights"],
    "additionalProperties": false
  },
  "toolResponseMode": "json_schema"
}
```

## Catalog Source Semantics

### `tool`

- Binds to an Assistant Tool, including shipped system tools and user-created tools.
- The OpenClaw-facing input and output contract lives on the catalog item itself.
- `toolResponseMode` controls how results are interpreted:
  - `json_schema`: the returned value must validate against `outputSchema`
  - `text_field`: MindAtlas wraps plain text into a single-field object before validation

### `workflow`

- Must bind to an enabled workflow with a published structured input and output contract.
- The catalog item snapshots the published workflow contract for OpenClaw-facing metadata.
- If the workflow is unpublished, disabled, or its structured contract drifts, the catalog item becomes `available = false`.

### `agent`

- Must bind to an enabled agent profile with a published version.
- The catalog item owns the OpenClaw-facing structured input and output schema in v1.
- Runtime still checks the published agent and validates the final JSON result against the catalog schema.

## Runtime Execution Flow

Tool execution uses the per-capability execution endpoint:

```http
POST /api/integrations/openclaw/capabilities/{capabilityKey}/execute
Authorization: Bearer <integration_secret>
Content-Type: application/json
```

The request body should be the tool argument object itself, not an extra wrapper.

Example:

```json
{
  "projectId": "openclaw-integration",
  "includeRisks": true
}
```

MindAtlas returns:

```json
{
  "success": true,
  "code": 0,
  "message": "OK",
  "data": {
    "capabilityKey": "project_digest",
    "toolName": "mindatlas_project_digest",
    "result": {
      "...": "..."
    }
  }
}
```

The request body is always validated against the catalog item’s current `inputSchema`.

Execution dispatch depends on the catalog item source:

- `tool`: run the bound Assistant Tool
- `workflow`: run the bound published workflow
- `agent`: run the bound published agent with catalog-owned structured contract enforcement

## Optional Audit Headers

The plugin should send lightweight execution context headers when available:

- `X-OpenClaw-Source`
- `X-OpenClaw-Channel`
- `X-OpenClaw-Session`
- `X-OpenClaw-Tool`

MindAtlas logs these values together with `requestId`, `capabilityKey`, status, and duration for audit-style observability.

## Plugin Boundaries

The plugin should not invent passthrough routes for arbitrary workflows, skills, or agents outside the MindAtlas catalog.

- `Skill` stays inside MindAtlas and is not exported directly to OpenClaw.
- OpenClaw should register only the currently exposed catalog items.
- System items may exist alongside administrator-created custom items.
- Tool names are catalog-driven. A user-created workflow or agent can appear as a first-class OpenClaw tool if an admin publishes it through the catalog.
- Policy docs and prompts should therefore route by capability category and current catalog metadata, not by hard-coded tool names.

## Error Semantics

- Keep MindAtlas runtime errors stable and machine-readable.
- Return actionable messages for auth failures, disabled capabilities, schema mismatch, or unavailable bindings.
- Prefer explicit unavailability over silent degradation when a catalog item can no longer execute safely.
