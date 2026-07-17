# Plan 07 Review Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR #54's merge-dark durable workflow library fail closed and restart-safe without enabling production durable admissions or claiming that the outer worker is wired.

**Architecture:** Preserve the frozen Checkpoint v2 and Interrupt contracts, but remove runtime behavior that silently disagrees with them. Unsupported iteration graphs are rejected at planning time; accepted node boundaries persist portable bags as private artifacts; nested frames move explicit inputs and outputs; human outcomes require exact typed edges; stop closes pending durable interrupts. CI fixtures and evidence are then aligned with the actual Plan 07 head and residual scope.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic v2, Pytest, PostgreSQL 15/Alembic, React 18, TypeScript, Zustand, Vitest.

## Global Constraints

- Keep `ASSISTANT_DURABLE_INTERRUPTS_ENABLED=false`; do not enable catalog/runtime admissions.
- Do not wire `MainAgentRunExecutor` to durable pause/resume in this follow-up.
- Do not add a partial loop-body interpreter; reject durable iteration until the full body state machine is implemented.
- Persist only JSON-portable bag snapshots in private run artifacts; never serialize Legacy `WorkflowState` or secrets.
- Human `rejected`, `cancelled`, and `expired` outcomes must never fall back to an approve/default success edge.
- Preserve Run-first, Interrupt-second lock order.
- Do not commit or push unless the user explicitly requests it.

---

### Task 1: Fail-closed planner contracts

**Files:**
- Modify: `backend/app/assistant/workflow/durable/planner.py`
- Test: `backend/tests/test_durable_execution_planner.py`

**Interfaces:**
- Consumes: `_plan_node(...)`, `_detect_unbounded_cycle(...)`.
- Produces: publication-time `DurablePlanError` with stable `unsupported_node` or `unbounded_cycle` reason codes.

- [ ] Add a test proving a bounded iteration with a non-empty body is rejected instead of published for a runtime that skips the body.
- [ ] Run that test and confirm it fails because the current planner accepts the graph.
- [ ] Reject `_LOOP_TYPES` before emitting `DurableNodePlanV1`; retain dependency validation only when iteration execution is implemented in a later plan.
- [ ] Add a graph test for `loop -> compute -> loop` and confirm it fails against the current broad re-entry skip.
- [ ] Make top-level cycle detection traverse every edge; explicit container bodies are not represented as top-level back-edges and need no blanket skip.
- [ ] Run the complete durable planner test file.

### Task 2: Persist portable bag state at every successful boundary

**Files:**
- Modify: `backend/app/assistant/workflow/durable/runner.py`
- Test: `backend/tests/test_durable_workflow_runner.py`
- Test: `backend/tests/test_durable_checkpoint_v2.py`

**Interfaces:**
- Consumes: `BoundaryResult.bag_snapshot`, `AssistantRunArtifact`, `commit_checkpoint_v2(..., artifact_ids=..., extra_child_rows=...)`.
- Produces: a private `workflow_bag_snapshot.v1` artifact and a frame `node_state_artifact_id` for each committed successful node boundary.

- [ ] Add a regression test that executes and commits a node, discards the runner, loads the latest Checkpoint v2 and artifact, and continues with the original node outputs.
- [ ] Run the regression test and confirm the recovered bag is empty on the current implementation.
- [ ] Extract a shared bag-artifact builder in `runner.py` using deterministic identity from run/frame/node visit and JSON snapshot content.
- [ ] Extend `commit_workflow_boundary_result` to accept the completed `BoundaryResult`, stamp the owning frame, and atomically commit the artifact with the checkpoint.
- [ ] Keep failure, cancellation, and lease-lost boundaries artifact-free.
- [ ] Run runner, checkpoint, pause, resume, and crash-matrix tests.

### Task 3: Preserve nested frame inputs, outputs, and plan identity

**Files:**
- Modify: `backend/app/assistant/workflow/durable/adapters.py`
- Modify: `backend/app/assistant/workflow/durable/runner.py`
- Modify: `backend/app/assistant/workflow/durable/resume.py`
- Test: `backend/tests/test_durable_nested_frames.py`
- Test: `backend/tests/test_durable_interrupt_resume.py`

**Interfaces:**
- Consumes: parent `PortableNodeBag`, child `DurableFrameMaterial.inputs`, child frame `target_version_id`.
- Produces: child bag inputs at push, parent call-node output at pop, and exact frame material selection through `child_materials[str(target_version_id)]`.

- [ ] Add a failing nested workflow test whose child reads a parent-derived input and whose parent reads the child output after pop.
- [ ] Seed the child bag from the frozen child material inputs plus explicit call-node input mapping; copy the child terminal payload into the parent call-node output before dropping the child bag.
- [ ] Add a failing nested HITL test where the child and root have different human-node successors.
- [ ] Select the human frame's material by `target_version_id` before `apply_human_result_once`; return `needs_reconciliation` if the exact child material is absent or its plan digest disagrees.
- [ ] Run nested frame, multiple interrupt, provider waiting, and interrupt resume suites.

### Task 4: Close human decision and stop-state integrity gaps

**Files:**
- Modify: `backend/app/assistant/workflow/durable/adapters.py`
- Modify: `backend/app/assistant/workflow/durable/resume.py`
- Modify: `backend/app/assistant/workflow/durable/interrupt_api.py`
- Modify: `backend/app/assistant/workflow/durable/interrupts.py`
- Modify: `backend/app/assistant/service.py`
- Test: `backend/tests/test_durable_interrupt_resume.py`
- Test: `backend/tests/test_durable_interrupt_api.py`
- Test: `backend/tests/test_durable_interrupt_security.py`
- Test: `backend/tests/test_durable_run_streaming.py`

**Interfaces:**
- Consumes: exact human edge handles and `DurableInterruptRepository.get_pending_for_run/cancel_interrupt`.
- Produces: exact typed successor selection, terminal cancellation when no typed continuation exists, and no pending Interrupt after stop.

- [ ] Add a failing test proving rejected approval without a `rejected` edge cannot select the sole approve/output edge.
- [ ] Change human successor selection to require an exact typed edge for non-success outcomes; only `approved`/`submitted` may use an explicitly unhandled single success edge.
- [ ] Align HTTP and golden queueing behavior with the selected successor contract.
- [ ] Add a failing service test for stopping a waiting Main Agent Run with one pending durable Interrupt.
- [ ] Cancel the pending durable Interrupt after the Run stop CAS using the established Run-first lock order, and verify idempotent repeated stop.
- [ ] Recheck `expires_at <= db_now` under lock in `expire_interrupt`; return a stable conflict/no-op for a not-yet-expired row.
- [ ] Keep the scanner terminal-cancel behavior and document typed expiry continuation plus production scheduling as residual work.
- [ ] Run interrupt API/security/resume and durable stop suites.

### Task 5: Make frontend resolve actions race-safe

**Files:**
- Modify: `frontend/src/features/assistant/components/MessageItem.tsx`
- Modify: `frontend/src/features/assistant/stores/chat-store.ts`
- Test: `frontend/src/features/assistant/components/MessageItem.test.tsx` or the nearest existing interrupt component test
- Test: `frontend/src/features/assistant/stores/chat-store.test.ts`

**Interfaces:**
- Consumes: current Zustand interrupt state by `(runId, interruptId)`.
- Produces: synchronous in-flight exclusion and monotonic terminal-over-pending upserts.

- [ ] Add a test that starts two submissions synchronously and asserts only one token rotation occurs.
- [ ] Guard with a `useRef<Set<string>>` acquired before the first await and released in `finally`; retain React state only for rendering disabled controls.
- [ ] Add a store test proving a stale pending/token-rotation update cannot overwrite a terminal interrupt delivered by SSE.
- [ ] Make durable interrupt upsert monotonic by status and request/token revision.
- [ ] Run focused Vitest tests, frontend tests, and production build.

### Task 6: Repair CI fixtures, migration expectations, and evidence accuracy

**Files:**
- Modify: `backend/tests/test_durable_interrupt_api.py`
- Modify: `backend/tests/test_durable_interrupt_security.py`
- Modify: `backend/tests/test_main_agent_postgres_migration.py`
- Modify: `backend/tests/test_provider_model_probe_postgres.py`
- Modify: `docs/superpowers/evidence/plan-07-task0-baseline.md`
- Modify: `docs/superpowers/evidence/plan-07-task9-golden-path.md`
- Modify: `docs/superpowers/evidence/plan-07-task10-verification.md`

**Interfaces:**
- Consumes: current DB/test clock and Alembic head `7a3dac0ac2a8`.
- Produces: time-independent interrupt fixtures, descendant-aware migration helpers, clean Markdown, and an evidence matrix that marks production auto-route/scanner as residual rather than pass.

- [ ] Replace absolute budget starts with `datetime.now(timezone.utc)` captured once per fixture, while explicit expiry tests continue to pass deterministic `suspended_at_utc`/`expires_at` values.
- [ ] Run both interrupt files and confirm the previous 25 wall-clock failures disappear.
- [ ] Update Plan 06 migration tests to distinguish the historical Plan 06 revision from current Alembic head; teach provider-probe reset about the Plan 07 descendant.
- [ ] Run PostgreSQL 15 migration gates from a disposable database.
- [ ] Remove trailing whitespace without changing Markdown meaning.
- [ ] Revise Task 9/10 evidence so library golden paths, production admissions, outer executor routing, and expiry scheduling have separate truthful statuses.
- [ ] Run `git diff --check`, conflict-marker scan, `compileall`, targeted backend suites, frontend tests/build, and the repository's feasible full backend gate.
