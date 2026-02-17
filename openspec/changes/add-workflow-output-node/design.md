## Context
Workflow DAG currently uses `llm.isOutput` to decide terminal output. This creates ambiguity when multiple LLM nodes exist and mixes concerns between intermediate generation and final response shaping. Streaming behavior is also tied to node semantics instead of API caller intent.

## Goals / Non-Goals
- Goals:
  - Introduce a single, explicit terminal `output` node.
  - Support text and structured final output modes.
  - Make stream/non-stream behavior caller-controlled.
  - Keep undo/save/load/runtime behavior stable for existing editor flows after migration.
- Non-Goals:
  - Introduce multiple output nodes.
  - Allow output nodes inside iteration/loop container subflows.
  - Redesign history engine or workflow execution model beyond output responsibility split.

## Decisions
- Decision: Enforce exactly one output node per workflow.
  - Rationale: deterministic terminal contract for validator, runtime, and UI.
- Decision: Structured output uses field mapping (`outputFields[*].value`) instead of free-form JSON template.
  - Rationale: stronger schema validation and clearer editor UX.
- Decision: Streaming is controlled by chat caller (`streamOutput`) and mapped to runtime `stream_output`.
  - Rationale: same workflow should support both SSE and aggregate responses.
- Decision: Implement text-mode passthrough optimization.
  - Rationale: if output template is a single LLM reference, use upstream token stream and skip duplicate final emit.

## Streaming Design
- Input switch:
  - `stream_output = true`: allow incremental output.
  - `stream_output = false`: buffer all content deltas and emit once after graph completion.
- Passthrough eligibility (text mode only):
  - exactly one output node
  - output template is a single variable: `{{node.field}}`
  - referenced node type is `llm`
  - field is `response` or `text`
- Execution behavior:
  - Eligible passthrough: LLM node emits token deltas; output node skips duplicate final text emission.
  - Ineligible text template: output node emits one final rendered text chunk.
  - Structured mode: output node emits one JSON chunk after field render + coercion.

## Risks / Trade-offs
- Risk: Existing legacy workflows without output node fail validation.
  - Mitigation: provide migration script and pre-release migration runbook.
- Risk: Structured coercion errors at runtime for invalid mapped values.
  - Mitigation: strict validator checks + explicit runtime error messages containing node/field context.
- Trade-off: passthrough detection supports only simple single-variable templates.
  - Benefit: deterministic behavior and avoids partial template rendering races.

## Migration Plan
1. Deploy code with output-node validator/runtime/editor support.
2. Run `backend/scripts/migrate_workflow_output_nodes.py --apply` in production environment.
3. Review conflict report and manually fix workflows that do not have exactly one legacy `llm.isOutput=true`.
4. Re-validate workflows and release UI changes.

## Open Questions
- Should future versions support multiple named output channels? (out of scope for this change)
