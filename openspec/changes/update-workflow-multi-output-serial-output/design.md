## Context
The workflow engine currently assumes a single terminal output node for deterministic final response construction and passthrough optimization. Real workflows can terminate on multiple branches and still need to emit branch-specific outputs without opening concurrent chat streams.

## Goals / Non-Goals
- Goals:
  - Support multiple terminal output nodes in one workflow.
  - Keep chat rendering as one assistant message stream.
  - Ensure multi-output content is emitted sequentially, not concurrently.
  - Preserve backward-compatible single-output passthrough behavior.
- Non-Goals:
  - Multiple assistant bubbles per output node.
  - Structured aggregation into array/object envelopes.
  - New editor-level output queue configuration.

## Decisions
- Topology decision:
  - Validation requires at least one output node.
  - Output nodes remain terminal-only.
- Streaming decision:
  - Output node content events include source metadata (`source_node_id`, `source_node_type=output`).
  - Runtime stream dispatcher inserts `\n\n` separator when output source changes.
  - Source-switch order follows event arrival (completion order).
- Compatibility decision:
  - Single-output passthrough optimization remains gated on `len(output_nodes) == 1`.
  - Multi-output structured mode is emitted as plain text JSON segments.

## Risks / Trade-offs
- Risk: Segmentation could be surprising if output chunks are tiny.
  - Mitigation: output node emission remains one-shot; switching is at output-node granularity.
- Trade-off: final JSON parsing may fail in multi-output structured workflows.
  - Accepted: contract explicitly allows `finalJson = null` for multi-output text segmentation.

## Verification Strategy
- Topology validation tests for zero/multiple output nodes.
- Stream runtime tests for source-switch separator insertion.
- Editor behavior validation for creating multiple output nodes.
- Reachability warning update validation for reverse-BFS from all output nodes.
