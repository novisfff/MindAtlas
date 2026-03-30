## 1. Backend
- [x] 1.1 Refactor the OpenClaw catalog model, migration, registry, and runtime execution flow so shipped defaults bind only to real `tool`, `workflow`, or `agent` sources.
- [x] 1.2 Add or wire the shipped OpenClaw wrapper system tools, system item seeding, reset behavior, and admin API compatibility aliases.
- [x] 1.3 Update backend tests to cover migration, system item editing/deletion/reset, and runtime execution without `system_adapter`.

## 2. Frontend And Plugin
- [x] 2.1 Update the OpenClaw settings API/types/UI so system items and custom items share one catalog workflow and reset targets system items.
- [x] 2.2 Update plugin runtime types/tests to accept only real source types while keeping catalog-first discovery unchanged.
- [x] 2.3 Refresh localized copy so “system item” replaces “system preset” as the primary concept.

## 3. Docs And Validation
- [x] 3.1 Update OpenClaw docs and shipped skill guidance to describe first-class system items and real-source dispatch.
- [x] 3.2 Add this OpenSpec change package and spec delta for the refactor.
- [x] 3.3 Run `openspec validate refactor-openclaw-system-items-to-first-class-exposures --strict --no-interactive` plus targeted backend and plugin verification.
