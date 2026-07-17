# Plan 07 Production Closure Design

**Date:** 2026-07-17

**Status:** Approved for implementation planning

**PR:** `#54` (`worktree-plan-07-durable-workflow-interrupt`)

## 1. Goal

Complete the production execution closure for Plan 07 without enabling durable
interrupt admissions by default. The merged code must be capable of safely
admitting, pausing, resolving, expiring, resuming, and completing a reviewed
durable Workflow or Agent when explicitly enabled, while the default deployment
continues to reject all new durable admissions.

This work closes the remaining gap between the existing durable library proofs
and the real API plus default assistant-worker path. It does not open business
writes, introduce a second Run state machine, or absorb the repository's
unrelated full-backend baseline failures.

## 2. Locked Scope

### 2.1 Included

- Route Checkpoint v2 durable actions through the default assistant worker.
- Reconstruct exact runtime material from immutable published references.
- Consume and commit durable pause effects through the production Run result
  transaction.
- Resume resolved Interrupts and close the original Provider waiting call once.
- Permit new durable dispatch only behind an explicit feature gate and trusted
  policy/admission evidence.
- Periodically scan expired Interrupts from the assistant worker.
- Resume an explicit typed `expired` branch or safely terminate when none exists.
- Prove the full API plus default-worker golden path, including restart.
- Add PostgreSQL race coverage for decision, expiry, stop, pause, and resume.

### 2.2 Excluded

- Changing `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` from its default `false`.
- Enabling a production durable Skill or catalog binding.
- Any business Draft, local write, remote write, or external side effect.
- CapabilityCall ledger work reserved for Plan 08.
- Implementing iteration or loop-body execution in durable runtime v1.
- Fixing the 24 unrelated backend CI failures already present on `main`.
- Adding another worker process, queue, lease protocol, or Run status machine.

## 3. Architectural Decision

Use a worker-owned durable unit router rather than expanding
`MainAgentRunExecutor` into a combined Provider and Workflow state machine.

`AssistantWorker` remains the sole owner of claim, lease, recovery
classification, heartbeat, and cancellation. After classification, a new
router inspects the decoded current Checkpoint and dispatches one execution
unit:

```text
AssistantWorker claim
    -> RecoveryClassifier
    -> DurableRunUnitRouter
       -> Plan 06 Provider action: MainAgentRunExecutor
       -> durable start/continue/resume_child: DurableWorkflowUnitExecutor
       -> interrupt_resume: DurableWorkflowUnitExecutor
       -> unknown or drifted state: needs_reconciliation
```

The router never chooses behavior from ambient process state. It uses the
immutable Run runtime kind, Checkpoint schema and `next_action`, frozen
descriptor/binding extension, and exact persisted revision pointers.

## 4. Component Responsibilities

### 4.1 `DurableRunUnitRouter`

The router has one public operation that receives the claimed lease, recovery
decision, heartbeat callback, and session factory. It loads and strictly
decodes the current Checkpoint, then selects exactly one executor.

It must:

- preserve the current Plan 06 path for no Checkpoint, Checkpoint v1, Provider,
  completion, and memory actions;
- send Checkpoint v2 `continue_child`, `resume_child`, and
  `interrupt_resume` actions to the durable Workflow executor;
- fail closed to `needs_reconciliation` for an unsupported action, codec,
  target, Artifact, plan digest, or continuation;
- avoid Provider, Gateway, Tool, or Workflow construction before all durable
  lineage validation succeeds;
- preserve existing retry, backoff, stop, and lease-loss behavior.

The router does not own persistence semantics. Each selected executor continues
to commit through the inherited Plan 06 repository CAS operations.

### 4.2 `DurableWorkflowUnitExecutor`

This executor adapts the existing Plan 07 library primitives to the production
worker interface. It is responsible for:

- rebuilding `DurableFrameMaterial` for every active frame from exact immutable
  Workflow or Agent versions and the frozen binding extension;
- running at most one prepared durable boundary per worker unit unless the
  existing resume contract explicitly continues bounded child boundaries;
- staging and consuming one exact `DurablePauseProposalV1`;
- calling `consume_and_commit_pause` only after Plan 03 has produced the
  complete Provider waiting continuation;
- calling `execute_interrupt_resume` for a resume-ready Interrupt unit;
- preserving the stable root `ContinuationRef` across repeated pauses;
- building one `ProviderWaitingResolution` only after the durable root becomes
  terminal;
- returning control to the existing Provider-loop continuation so pending
  sibling calls resume in their original order;
- routing irreconcilable material or lineage drift before external I/O.

The executor reuses the existing Plan 02 Gateway, Plan 03 Provider continuation,
Plan 05 budget and obligation state, and Plan 06 lease/CAS implementation. It
does not create alternate ledgers or status transitions.

### 4.3 Runtime material reconstruction

Material reconstruction must be a standalone, testable service. It consumes:

- the persisted Run Manifest revision;
- the immutable Skill binding and descriptor extension;
- exact Workflow/Agent version identifiers and digests from every frame;
- the frozen durable execution-plan digest;
- exact dependency references and private Artifact references.

It produces a root `DurableFrameMaterial` plus a map keyed by exact child
`target_version_id`. Every target/version/plan digest must match the persisted
frame. Missing or ambiguous material is reconciliation, never a fallback to a
current Draft or latest published version.

### 4.4 Expiry driver

The existing repository scanner becomes a production service driven by the
assistant worker at a bounded interval. The driver uses a monotonic process
clock only to decide when to attempt another database scan; the database clock
remains authoritative for `expires_at`.

The driver:

- runs independently of Run claim and lease ownership;
- scans at most one configured batch per interval;
- rolls back and logs a failed batch without preventing ordinary Run claims;
- uses the existing Run-first lock order for each candidate;
- treats stale or already-terminal candidates as no-ops;
- exposes safe counters for scanned, expired, queued, cancelled, skipped, and
  conflicted candidates.

Expiry resolution must inspect the frozen human-node contract. An explicit
`expired` edge creates one typed human result, derives at most one resume budget
child, appends a resume-ready Checkpoint, and queues the Run. Without an exact
typed edge, expiry terminalizes the Interrupt and safely cancels the durable
child/Run. The scanner never invokes Workflow, Provider, Gateway, or user code
inside its database transaction.

## 5. Admissions and Feature Gates

`ASSISTANT_DURABLE_INTERRUPTS_ENABLED` controls only new durable admissions.
It does not prevent a compatible worker from draining, cancelling, expiring, or
reconciling an already-persisted Checkpoint v2 Run.

A new durable admission requires all of the following:

1. the feature flag is `true`;
2. a stable nonempty token pepper is configured;
3. the trusted entrypoint is Main Agent;
4. the descriptor declares `interrupt_mode=durable`;
5. the immutable binding extension contains a verified durable-plan digest;
6. the business effect ceiling is `none`, `read`, or `compute`;
7. an interrupting descriptor is nonparallel;
8. a fresh registered worker supports Checkpoint schema v2 and the required
   app build/runtime contract;
9. exact runtime material can be reconstructed without reading Draft/current/
   latest mutable state.

Policy must no longer reject durable descriptors unconditionally. It may issue
a permit only when the trusted admission evidence proves every condition above.
The Workflow Adapter may enter the durable path only with that permit; a model
or descriptor alone cannot authorize durable execution.

Closing the flag after admissions were previously enabled rejects new durable
Runs but leaves compatible decoding, worker routing, token verification,
Interrupt APIs, expiry, and Artifact access available until all persisted v2
Runs drain.

## 6. State and Error Semantics

- Checkpoint v1 and ordinary Provider actions retain current Plan 06 behavior.
- Checkpoint v2 durable actions are routed from persisted `next_action` only.
- Deterministic target, build, plan, Checkpoint, continuation, budget, or
  Artifact drift transitions to `needs_reconciliation` before external I/O.
- A stale `state_revision`, lost lease, or stop-first result never replays or
  overwrites a committed result; recovery reclassifies from durable state.
- Transient Provider or Artifact I/O follows existing Plan 06 retry/backoff.
- A second pause creates a new Interrupt while preserving the original root
  continuation and Provider waiting call.
- Root terminal creates exactly one trusted `ProviderWaitingResolution` and
  then resumes the original pending sibling suffix.
- Feature disablement is not a recovery error for an existing v2 Run.
- Worker expiry-driver failure is isolated from the claim loop and cannot take
  the worker unhealthy unless the existing health contract independently fails.

## 7. Production Golden Path

The acceptance test must use the real FastAPI routes and default
`AssistantWorker`; it may inject deterministic Provider/model responses and
private Artifact storage, but it must not call `consume_and_commit_pause` or
`execute_interrupt_resume` directly from the test.

The test performs:

1. configure the feature flag, stable test pepper, compatible worker, and one
   hidden reviewed durable binding;
2. create a conversation and Main Agent Run through the API/service admission
   path;
3. let the default worker claim and execute until `waiting_approval` or
   `waiting_input`;
4. destroy the worker instance and create another one;
5. reload the conversation and pending Interrupt through the public API;
6. rotate a token and resolve through the public HTTP endpoint;
7. let the replacement default worker claim `interrupt_resume`;
8. complete the exact frame and original Provider waiting call;
9. finish memory and public terminal events once;
10. assert one logical decision, one Tool Result, one terminal Artifact,
    preserved budgets/obligations, zero business writes, and no external write.

A sibling scenario verifies two sequential Interrupts retain one outer
continuation. Additional scenarios cover explicit typed expiry, terminal expiry,
stop, rejection, nested Workflow, nested Agent, and restart before/after each
durable commit boundary.

## 8. Test Strategy

Implementation follows strict red-green-refactor cycles.

### 8.1 Worker router tests

- default worker currently fails to consume `interrupt_resume`;
- v1 and Provider actions remain on `MainAgentRunExecutor`;
- each supported v2 action selects the durable executor;
- unknown actions and material drift reconcile before I/O;
- feature-off existing v2 Runs still drain.

### 8.2 Admission tests

- flag off rejects a new durable admission;
- flag on plus complete trusted evidence admits it;
- missing pepper, worker v2 support, plan digest, nonparallel declaration, or
  effect ceiling fails closed;
- Legacy and noninterrupting Main Agent paths remain unchanged.

### 8.3 Expiry tests

- explicit `expired` edge queues one typed continuation;
- no explicit edge terminalizes safely;
- two scanners converge;
- scanner versus decision and scanner versus stop converge under PostgreSQL
  dual sessions;
- interval throttling and scan failure do not block claims.

### 8.4 Integration and compatibility tests

- real API plus default-worker golden restart path;
- nested Workflow and nested Agent material reconstruction;
- repeated pause and Provider sibling ordering;
- PostgreSQL stop/pause/resume/root-terminal races;
- focused Plan 07 and broader durable plus Legacy HITL suites;
- frontend tests/build, migration cycle, Compose config, compileall, and
  `git diff --check`.

The full backend suite remains an observed repository gate. Any failures are
compared with the exact base failure set; this scope does not silently modify
unrelated Tool, LightRAG, or JavaScript-executor tests.

## 9. Rollout and Rollback

1. Merge and deploy with durable admissions disabled.
2. Register compatible v2 workers and confirm expiry-driver health/counters.
3. Run the hidden production-path golden binding through API plus worker.
4. Confirm no unresolved v2 Run, token exposure, business write, or duplicate
   Provider result.
5. Enable durable admissions only through deployment configuration in a later
   explicit release decision.

Rollback disables new admissions and removes the durable binding from new
Manifests. Compatible workers, APIs, pepper, Checkpoint v2 codec, expiry driver,
and private Artifacts remain until every existing v2 Run drains, cancels,
expires, or reconciles.

## 10. Completion Criteria

This production-closure work is complete only when:

- the default assistant worker automatically performs initial durable pause and
  resolved Interrupt resume from persisted Checkpoints;
- the public API plus replacement-worker golden path completes without direct
  helper invocation;
- typed and terminal expiry paths run from a periodic production driver;
- feature-off prevents new admissions but does not strand existing v2 Runs;
- all supported paths preserve exact frozen material, budgets, obligations,
  continuation identity, sibling order, and CAS semantics;
- nested Workflow and Agent waits have restart coverage;
- PostgreSQL race tests, focused/broader durable tests, frontend build/tests,
  migration cycle, Compose config, compileall, and diff hygiene pass;
- the default deployment still has durable admissions disabled.
