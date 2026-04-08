## Context
- Workflow editor already supports direct tool invocation and reusable workflow publishing, but those capabilities are disconnected.
- OpenClaw integration already contains published-workflow contract validation logic for structured input and unique structured output contracts.
- Container subflow runtime already forwards nested node/tool/approval events with scoped node ids, which can be reused for child workflow execution.

## Goals
- Add first-class `workflow_call` nodes without reusing `tool` nodes or `agent.toolNames`.
- Expose only callable child workflows that are enabled, published, structured-input, and have a unique structured output contract.
- Support both `pinned` and `latest` binding modes, with new nodes defaulting to `pinned` against the current latest published version.
- Reuse parent runtime locale, trace callbacks, and human approval lifecycle when a child workflow executes.
- Prevent recursive workflow references, including indirect A -> B -> A chains.

## Non-Goals
- Treat child workflows as ordinary tools inside `agent.toolNames`.
- Stream child workflow content directly to the final end user output.
- Support unpublished child workflow drafts as callable targets.
- Add database schema changes solely for reference tracking.

## Decisions
- Add a dedicated node type `workflow_call` across backend and frontend node enums.
- Extract shared workflow-contract helpers from OpenClaw-oriented logic into a reusable module that derives JSON-schema summaries and callable input/output params from published workflow structure.
- Add a callable-workflow listing API that returns workflow metadata plus callable published versions and their contract-derived params.
- Keep references scan-based:
  - workflow delete protection scans draft graphs and saved version snapshots for `workflow_call` references
  - pinned published-version delete protection scans the same sources for direct version references
- Validate cycles against:
  - the workflow currently being saved/validated
  - persisted draft graphs of other workflows
- Execute child workflows through a dedicated runtime node builder that:
  - resolves `pinned` to the selected published version snapshot
  - resolves `latest` to the target workflow current published version
  - scopes child node ids as `workflowCallNodeId::childNodeId`
  - suppresses child `content_delta` passthrough
  - preserves node/tool/HITL events

## Risks / Trade-offs
- Scan-based reference protection avoids schema migration, but adds extra validation and delete-time queries.
- `latest` mode is intentionally dynamic, so runtime behavior can change after a child workflow republishes.
- Reusing shared contract logic reduces drift, but requires carefully untangling helper functions from existing OpenClaw service code.

## Migration Plan
1. Add OpenSpec change files and validate them.
2. Extract shared callable workflow contract helpers.
3. Add backend schema/API/service support for callable workflow discovery and validation.
4. Add `workflow_call` runtime builder and nested event scoping.
5. Add delete/version protection and cycle detection.
6. Add frontend editor, quick-add, property panel, and variable-reference support.
7. Add tests and run backend/frontend validation.
