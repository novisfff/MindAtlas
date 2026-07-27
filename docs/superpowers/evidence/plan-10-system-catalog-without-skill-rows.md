# Plan 10 — System catalog warm without assistant_skill rows (2026-07-23)

## Problem
`sync_system_skills` was temporarily a no-op after table drop, so
`general_chat__agent` and skill-named system workflows stopped seeding.

## Fix
`sync_system_skills` now creates/restores **workflow/agent baselines only**
using synthetic skill shells for naming helpers (`{name}__workflow` /
`{name}__agent`). No `assistant_skill` rows are written.

## Audit
`_audit_system_target_origins` classifies those baselines as `system_skill`
by naming contract rather than skill-table linkage.

## Also in this cleanup batch
- bootstrap no longer runs shadow package sync
- shadow sync helpers removed from config service
- DB skill converters/schemas removed
- FE AssistantSkill type surface removed
