## Context
- `smart_capture` is the assistant-first guided capture workflow used mainly by system skills and the in-app AI assistant.
- `context_capture` remains the OpenClaw-facing thin-context workflow and must keep its public contract unchanged.
- The current workflow engine does not support convenient reconvergence of mutually exclusive branches, so the upgraded `smart_capture` should favor explicit lane-based topology instead of trying to generalize DAG branch-merge semantics in the same change.

## Goals
- Let `smart_capture` search for similar entries before field materialization.
- Make merge vs create a human-selected decision.
- Keep a second human write gate before `create_entry` or `update_entry`.
- Add reusable batch relation confirmation via `human_in_loop.checkbox_group`.
- Keep relation follow-up optional and post-persistence.

## Non-Goals
- No changes to `context_capture`, `submit_context_capture`, or OpenClaw public schemas.
- No automatic merge in `smart_capture`.
- No generic DAG reconvergence runtime refactor in this change.

## Decisions
- Add `checkbox_group` as a first-class `human_in_loop` widget for array fields.
- Preserve object options end-to-end for `select`, `radio`, and `checkbox_group`, while keeping legacy string option lists valid.
- Rebuild `smart_capture` as explicit straight-line lanes:
  - direct create lane when no similar candidates exist
  - triaged create lane when candidates exist but the user chooses `create_new`
  - merge lane when candidates exist and the user chooses `merge_existing`
- Keep relation follow-up duplicated per success lane rather than changing workflow engine branch semantics.

## Risks / Trade-offs
- The workflow asset becomes larger because post-write relation follow-up is duplicated per success lane.
- English and Chinese assets must stay topology-identical.
- Relation follow-up depends on relation recommendations returning typed candidates; V1 filters out null relation types instead of asking the user to fill them.
