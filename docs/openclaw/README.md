# OpenClaw Integration Contract

This document defines the MindAtlas side of the `openclaw-mindatlas` integration.

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
          "baseUrl": "http://127.0.0.1:8000",
          "integrationSecret": "paste-the-secret-from-mindatlas",
          "requestTimeoutMs": 15000,
          "catalogRefreshTtlSec": 300
        }
      }
    }
  }
}
```

The plugin bundles the `MindAtlas Overview` skill from `integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md`.

## Positioning

- `OpenClaw`: chat-channel entrypoint, tool orchestration layer, channel adapters.
- `MindAtlas`: capability backend, knowledge system of record, workflow/report execution backend.

OpenClaw should not call MindAtlas frontend APIs directly. It should consume the dedicated OpenClaw integration facade.

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
  - `system_adapter`
  - `tool`
  - `workflow`
  - `agent`

The plugin must not assume a fixed built-in tool list. It should always trust live discovery metadata from MindAtlas.

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
5. If a tool name, exported title, exported description, or input schema changes, reload the OpenClaw Gateway / plugin so tool registration metadata is rebuilt.
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

### `system_adapter`

- Built-in MindAtlas capabilities such as entry capture, search, relation creation, LightRAG query, or weekly and monthly reports.
- These are auto-seeded as system preset catalog items.
- Presets can be disabled and reset, but not deleted.

### `tool`

- Binds to an Assistant Tool, including system tools and user-created tools.
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
  "title": "Read OpenClaw integration plan",
  "summary": "整理接入边界",
  "content": "OpenClaw handles chat entry. MindAtlas handles execution.",
  "entryType": "KNOWLEDGE",
  "tagNames": ["OpenClaw", "Integration"]
}
```

MindAtlas returns:

```json
{
  "success": true,
  "code": 0,
  "message": "OK",
  "data": {
    "capabilityKey": "capture_entry",
    "toolName": "mindatlas_capture_entry",
    "result": {
      "...": "..."
    }
  }
}
```

The request body is always validated against the catalog item’s current `inputSchema`.

Execution dispatch depends on the catalog item source:

- `system_adapter`: run the built-in MindAtlas adapter
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
- System preset items may exist alongside administrator-created custom items.
- Tool names are catalog-driven. A user-created workflow or agent can appear as a first-class OpenClaw tool if an admin publishes it through the catalog.

## Error Semantics

Runtime errors always use the standard MindAtlas API envelope:

- Unauthorized or missing secret: `40161`
- Integration disabled: `40361`
- Capability disabled or not exposed: `40362`
- Unknown capability key: `40461`

Capability-specific validation errors may also return business-level `400xx` or `409xx` codes with `data` details.

## Plugin Guidance

The shipped `openclaw-mindatlas` plugin:

1. Read the exposed catalog from MindAtlas.
2. Register matching OpenClaw tools using each catalog item’s `toolName`.
3. Preserve `capabilityKey` as the execute target behind each OpenClaw tool.
4. Forward tool arguments directly to MindAtlas.
5. Translate MindAtlas API errors into short user-friendly OpenClaw tool failures.
6. Bundle the `MindAtlas Overview` skill from [mindatlas-overview-skill.md](./mindatlas-overview-skill.md).
