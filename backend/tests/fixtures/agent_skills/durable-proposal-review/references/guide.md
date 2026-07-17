# Durable Proposal Review Recovery Guide

## Graph

`start -> llm (compute proposal) -> human_in_loop (editable durable approval) -> output`

## Recovery scenario

1. Main Agent calls the golden Capability.
2. Workflow computes proposal and enters waiting.
3. Kill API/worker; restart compatible versions (or simulate via durable checkpoint recovery).
4. Reload / fetch pending Interrupt / rotate token.
5. Edit/approve or submit fields.
6. Kill worker after decision commit and restart.
7. Resume exact node/frame, return final Artifact/result, complete.
8. Verify one Interrupt decision, one node continuation, one Tool Result, one final Artifact, preserved budgets/obligations, zero business writes.

## Constraints

- parallel_safe=false
- interrupt_mode=durable
- business side effect compute
- zero Entry/Tag/Relation/Draft/HTTP writes
- hidden/evaluation only until rollout evidence passes
