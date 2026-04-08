## Context
- `add-workflow-call-node` introduced dedicated child-workflow invocation, but callable filtering still excludes text-mode workflows.
- Workflow test run memory is client-round-tripped and currently has no dedicated nested workflow scope.
- Assistant chat already has parent L1/L2 memory persistence that can be extended with a child-scope table without rewriting existing parent memory rows.

## Goals
- Allow callable child workflows with text or structured start contracts and text or structured output contracts, as long as the output contract is uniquely resolvable.
- Normalize text contracts so frontend, validation, and runtime all use one canonical shape.
- Preserve parent memory while letting each `workflow_call` node accumulate child-specific memory across repeated invocations.
- Make child memory immediately visible within the same top-level run, including repeated calls inside loop and iteration containers.
- Keep workflow test runs stateless on the server while still round-tripping nested child memory through the client session payload.

## Non-Goals
- Move child workflows into `agent.toolNames`.
- Add per-node memory configuration to `workflow_call`.
- Backfill existing parent L1/L2 rows into the new child-memory table.
- Persist child memory for runtimes without stable conversation or session scope.

## Decisions
- Extend `WorkflowContractSnapshot` with `input_mode` and `output_mode`.
- Normalize text contracts:
  - start input -> single required `user_input`
  - output -> canonical schema `{ response: string }`
- Treat multiple text output nodes as the same callable contract, but reject:
  - mixed text and structured outputs
  - multiple distinct structured output schemas
- Runtime input mapping branches on child contract input mode:
  - text child workflow reads `user_input`
  - structured child workflow reads `structured_input`
- Runtime output semantics stay parent-oriented:
  - parent `workflow_call` node always exposes `response`
  - structured child workflows additionally expose declared structured fields
- Add stable workflow identity on runtime skill/state metadata via `workflow_id` and `workflow_version_id`.
- Add assistant chat child-memory persistence keyed by:
  - `conversation_id`
  - `source_workflow_id`
  - `source_node_scope`
  - `target_workflow_id`
- Define `source_node_scope` as:
  - top-level `nodeId`
  - container `containerNodeId::bodyNodeId`
- Child memory merge model:
  - L0 inherits only parent recent dialogue window
  - L1 merges parent summary with child-scope summary
  - L2 merges parent facts with child-scope facts and de-duplicates
- Workflow test run uses `sessionMemory.workflowCallScopes` as the non-persistent transport for child-scope memory and always passes a mutable session-memory payload into runtime so newly created scopes can round-trip back.

## Risks / Trade-offs
- `latest` workflow binding remains dynamic, so a republished child workflow can change runtime behavior without parent draft edits.
- Child memory is aggregated per call node, not globally per child workflow, which keeps scopes isolated but increases stored rows.
- Session-memory round-trip for workflow tests hides nested scopes from the UI, which is intentional for v1 but makes debugging more dependent on trace/raw payload inspection.

## Migration Plan
1. Extend callable workflow contract parsing and API payloads for text/structured modes.
2. Add runtime identity plumbing plus child-memory storage and merge logic.
3. Update workflow test session-memory normalization and round-trip behavior.
4. Update frontend callable workflow types, workflow test payload handling, and workflow-call output hints.
5. Add backend tests for text callable workflows and nested child-memory round-trip.
6. Validate with backend tests, backend compile, frontend `tsc`, frontend build, and `openspec validate`.
