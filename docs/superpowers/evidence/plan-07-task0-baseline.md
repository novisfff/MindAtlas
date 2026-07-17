# Plan 07 Task 0 Baseline (Plan 06 Freeze + Legacy Workflow/HITL Characterization)

**Recorded at (UTC):** 2026-07-15T15:41:30Z
**Branch:** `worktree-plan-07-durable-workflow-interrupt`
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-07-durable-workflow-interrupt`
**HEAD at freeze:** `8f253db4dde0d72bc45545ce8c99f6f211a2b8e0`
**HEAD subject:** `feat(ai): Plan 06 durable agent run foundation (merge-dark) (#53)`
**Plan 06 development tip (pre-squash):** `44aee03` (`fix(ai): atomic main_agent create, configured I/O heartbeat, migration gates`)
**Tree equivalence:** `git diff --stat 44aee03 8f253db -- backend/app` is empty — squash merge on `main` carries the full Plan 06 product tree.
**Working tree product code at freeze start:** clean (untracked local `backend/.venv` symlink only; characterization test + this evidence added by Task 0).

---

## 1. Environment

| Item | Value |
|---|---|
| Python (local venv) | **3.12.3** (production target remains **3.11**; local drift is **not** Plan 07 compatibility evidence) |
| `backend/requirements.txt` pin `langgraph` | `langgraph==0.3.34` |
| Installed `langgraph` (local venv) | **1.2.9** (mismatch vs pin — same class of drift Plan 05/06 recorded) |
| Installed `langchain` / `langchain-core` | 1.3.13 / 1.4.9 |
| pydantic | 2.13.4 |
| sqlalchemy | 2.0.51 |
| jsonschema | 4.26.0 |
| alembic | 1.18.5 |
| fastapi | 0.139.0 |
| httpx | 0.28.1 |
| cryptography | 49.0.0 |
| Sole Alembic head | **`6af373ef040f`** (`6af373ef040f_add_durable_agent_run_foundation.py`; parent `9ed6f561a381`) |
| `MINDATLAS_TEST_POSTGRES_URL` | **unset** (PG two-session suites skipped) |
| `MINDATLAS_TEST_MINIO` | **unset** (MinIO suites skipped) |
| Live Docker compose golden | **not run** |

### Plan 06 worker / codec / flags (frozen)

| Item | Value |
|---|---|
| Checkpoint `schema_version` | **1** (`SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS = frozenset({1})`) |
| `RUNTIME_CONTRACT_VERSION` | **1** (`app.assistant.durable.worker_registry`) |
| Worker capability feature digest | `de9af0d91cca357a53e11ac65c614fac5403484ed22bdb3bac55ac4f36b9c63a` |
| `ASSISTANT_MAIN_AGENT_MODE` default | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` | **not present yet** (Plan 07 will add; plan default false) |
| Worker lease TTL / heartbeat / registration TTL | 30s / 5s / 20s |
| Worker poll / retry base / retry max | 500ms / 500ms / 30000ms |
| Max recovery attempts | 5 |
| Artifact orphan grace / scan / clock skew | 900s / 60s / 30s |
| Artifact bucket | `mindatlas-assistant-artifacts` (private; attachments remain public download) |
| Main Agent read-only ceiling digest | `a444b092ef2332ec9d0943c12d2bcbd3ac28d8c23ca0a609f692cc6eac1c6482` |
| Ceiling allowed interrupt modes | **`("none",)` only** |
| Plan 05 entrypoint policy revision / digest | `plan05-v1` / `e2049c4f562fb4281a9774779678cfb805c45d0e18cf2c7b2ff81be34c52099f` |
| Classification ruleset digest | `1b3d2d217c35dd9272dfcb850a7006ef38872aa8434cbd9f9c535c613ffdb711` |

### Prior Plan 06 smoke evidence paths

| Evidence | Path |
|---|---|
| Plan 06 Task 0 baseline | `docs/superpowers/evidence/plan-06-task0-baseline.md` |
| Plan 06 Task 10 rollout / exit | `docs/superpowers/evidence/plan-06-task10-rollout.md` |
| Plan 02B final | `docs/superpowers/evidence/plan-02b-final.md` (`FULL_PLAN_02_COMPLETE=yes`) |
| Plan 02B observation | `docs/superpowers/evidence/plan-02b-observation.md` |

---

## 2. Stop-condition checks (plan §2)

| Gate | Result | Evidence |
|---|---|---|
| Plan 06 did **not** reduce Plan 03 `ProviderLoopContinuation` | **pass** | Continuation contract still at `app.assistant.provider_loop.contracts.ProviderLoopContinuation`; durable codec stores full protected Provider messages + continuation linkage (`test_durable_provider_continuation.py`, `test_durable_checkpoint_codec.py`) |
| `prepared → started → result` boundary present | **pass** | `commit_prepared_unit` requires `unit.state=prepared`; runner drives prepare → started → external I/O → result (`app.assistant.durable.checkpoints`, `MainAgentRunExecutor`) |
| Ordinary result never accepts `cancelling`; only cancel finalizer produces `cancelled` | **pass (unit)** | `test_result_never_overwrites_cancelling`, `test_cancel_finalizer_only_from_cancelling`, `test_stop_running_to_cancelling` in `test_durable_run_repository.py`; repository comment + `ALLOWED_TRANSITIONS` |
| Stop increments `state_revision` | **pass (unit)** | `test_stop_running_to_cancelling` asserts revision 2→3 |
| Protected Provider roles not downcast | **pass** | Roles remain `runtime_instruction \| runtime_context \| runtime_completion` (`ProviderRuntimeInstructionMessage` etc.); codec suite green |
| Staged Skill activation not durable before lifecycle accept | **pass** | `test_main_agent_skill_injection.py` activation vectors green; `ManifestEffectLifecyclePort.accept` / discard residual rules intact |
| Compatible worker + lease/CAS repository | **pass** | `WorkerRegistry`, `DurableRunRepository`, `AssistantWorker` default-wires `MainAgentRunExecutor` |
| Strict Checkpoint v1 codec/migration registry | **pass** | `SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS={1}`, migration registry present |
| Production descriptors still `interrupt_mode=none` | **pass** | Main Agent ceiling `allowed_interrupt_modes=("none",)`; only non-`none` classifier emission is Workflow **node-level** `human_in_loop → legacy_blocking` (Legacy classification, not a production durable Main Agent binding) |
| One Alembic head | **pass** | `6af373ef040f (head)` |

**No §2 stop-condition failure.** Plan 07 Task 1 may begin.

### Honest Plan 06 enablement gaps (do **not** auto-stop Plan 07)

Recorded from Plan 06 Task 10 + reconfirmed:

1. **Production dual-wiring incomplete for live `read_only`.** Default `MainAgentRunExecutor` still uses scripted/single-unit paths and placeholder digests on some fresh-materialize vectors; full Plan 03/05 dual-wiring + admission-frozen Manifest digests for production traffic remain open. Mode default stays **`off`**.
2. **PostgreSQL two-session CAS/events/lease suites CI-gated** — skipped here (`MINDATLAS_TEST_POSTGRES_URL` unset). Unit SQLite CAS proves stop/result semantics; concurrent multi-session proof is **not** re-run live.
3. **MinIO private Artifact live suites CI-gated** — skipped.
4. **Live compose golden path not run** (API + assistant-worker heartbeat + recovery smoke).
5. Plan 06 Task 10 explicitly: do **not** treat Plan 06 as `PLAN_06_READY=yes` for production `read_only` enablement.

These are enablement/ops gaps. Durable CAS/codec/contracts/worker infrastructure required by Plan 07 **are present**.

---

## 3. Test re-runs (this Task 0)

Fernet key generated per-process for suites. PG/MinIO env vars unset.

### 3.1 Primary Task 0 suite (suggested command)

```text
backend/tests/test_workflow_human_in_loop_runtime.py
backend/tests/test_workflow_human_in_loop_node.py
backend/tests/test_workflow_execution_context.py
backend/tests/test_workflow_call_node.py
backend/tests/test_workflow_test_run_stream.py
backend/tests/test_durable_run_recovery.py
backend/tests/test_durable_run_repository.py
backend/tests/test_durable_run_events_postgres.py
backend/tests/test_durable_run_streaming.py
backend/tests/test_durable_checkpoint_codec.py
backend/tests/test_durable_main_agent_runner.py
backend/tests/test_durable_audit_fixes.py
```

| Result | Count |
|---|---|
| Passed | **141** |
| Failed | **1 flaky then green on re-run** (`DurableRunStreamingTests::test_two_concurrent_readers_see_same_committed_events` — timing flake under SQLite; re-run alone **PASSED**) |
| Skipped | **8** (PG two-session / events) |
| Duration | ~326s first pass |

### 3.2 Expanded Plan 06 durable suite

Including recovery, repository, codec, provider continuation, audit fixes, budget/obligation recovery, main agent runner, worker registry, crash matrix, memory commit, rollout task10:

| Result | Count |
|---|---|
| Passed | **155** |
| Skipped | **2** |
| Duration | ~352s |

### 3.3 Activation + protected-message + CAS + PG-gated modules

```text
test_main_agent_skill_injection.py
test_provider_messages.py
test_durable_provider_continuation.py
test_durable_checkpoint_codec.py
test_durable_run_repository.py
test_durable_run_events_postgres.py
test_durable_worker_lease_postgres.py
```

| Result | Count |
|---|---|
| Passed | **121** |
| Skipped | **18** (all PG lease/events two-session) |

### 3.4 WorkflowState serialization characterization (new)

`backend/tests/test_workflow_state_serialization_characterization.py` — **9 passed**.

### 3.5 Frontend

`npm --prefix frontend install` required (no `node_modules` in worktree).

```text
vitest run src/features/shared/hitl src/features/assistant
```

| Result | Notes |
|---|---|
| **31 passed / 9 files** | Vitest path filter also matched `assistant-config` substring; focused assistant-only files: `eventIdentity.test.ts` (9), `chat-store.test.ts` (3) |
| `src/features/shared/hitl/*` | **No `*.test.*` files yet** (components only: Card, FieldForm, ActionBar, StatusBadge) |
| workflow-test-run store approval event test | green (via assistant-config match) |

### 3.6 Alembic

```text
6af373ef040f (head)
```

---

## 4. CAS / stop / result behavior (unit — PG skipped)

From `app.assistant.durable.repository.ALLOWED_TRANSITIONS` + unit tests:

| Vector | Unit result |
|---|---|
| `running → cancelling` via stop bumps `state_revision` | **pass** (`test_stop_running_to_cancelling`) |
| Ordinary complete/fail from `cancelling` rejected | **pass** (`test_result_never_overwrites_cancelling`; `is_transition_allowed("cancelling","completed") is False`) |
| `cancelling → cancelled` only via cancel finalizer | **pass** (`test_cancel_finalizer_only_from_cancelling`) |
| `ready_for_memory` fence blocks stop | **pass** (`test_ready_for_memory_blocks_stop`) |
| Idempotent event package does not bump revision | **pass** (repository suite) |
| Transition table already reserves `running → waiting_approval|waiting_input` and `waiting_* → queued` | **present** (Plan 07 will use; no second state machine) |

**Not re-proven live:** two-Session concurrent stop/result, stop/`ready_for_memory`, stop/memory-finalizer, duplicate cancellation-finalizer under real PostgreSQL (`MINDATLAS_TEST_POSTGRES_URL` unset). Do not invent green PG evidence.

---

## 5. Codec + activation vectors

| Vector | Result |
|---|---|
| Lossless `runtime_instruction \| runtime_context \| runtime_completion` discriminators | **pass** (codec + provider message suites) |
| Revision linkage on durable Provider message records | **pass** |
| `stage → lineage → ManifestEffectLifecyclePort.accept` | **pass** (skill injection suite) |
| Zero durable residue on reject / discard / crash / replay before accept | **pass** (activation vectors in skill injection + crash matrix subset) |

---

## 6. Legacy HumanLoopCoordinator characterization (process loss)

### 6.1 Code anchors

| Anchor | Path |
|---|---|
| Coordinator (in-process Event map) | `backend/app/assistant/workflow/human_approval_runtime.py` — `HumanLoopCoordinator`, `GLOBAL_HUMAN_LOOP_COORDINATOR` |
| Blocking wait | `HumanLoopRuntime.create_and_wait` — DB insert `AssistantHumanApproval(status=pending)`, register waiter, **poll loop `wait_once(..., timeout=0.5)`** + DB refresh fallback |
| Resolve path | HTTP decision → `GLOBAL_HUMAN_LOOP_COORDINATOR.resolve` + DB status update |
| Graph thread | `backend/app/assistant/workflow/engine/stream_runtime.py` — `Thread(target=_run_graph, daemon=True)` |
| Runtime injection | `execution_services.attach_human_loop_runtime` → `metadata["human_loop_runtime"] = HumanLoopRuntime(...)` |
| Node | `engine/node_builders/human_in_loop_node.py` → `runtime.create_and_wait(...)` |
| Routes | conversation-scoped only: `/conversations/{id}/approvals/pending`, `/conversations/{id}/approvals/{approval_id}/decision` (no unscoped `/api/assistant/runs/...`) |

### 6.2 Process-loss behavior (code evidence)

1. Approval row is durable in SQL (`AssistantHumanApproval`), but the **waiter is a process-local `threading.Event`**.
2. Graph execution runs on a **daemon thread**; process exit kills the wait without Workflow resume.
3. After API/worker restart: DB row may remain `pending`, but **no in-memory waiter** exists → Legacy run cannot resume the blocked node; human decision may update the row without a live consumer.
4. Nested containers use `ScopedHumanLoopRuntimeProxy` — still process-local.
5. Characterization tests prove coordinator waiters do not survive a fresh process-equivalent coordinator instance.

**Conclusion:** Legacy HITL is correctly classified `legacy_blocking` and is unsafe as a Plan 07 recovery primitive. Plan 07 must use portable `DurablePauseProposal` + Interrupt rows + CAS waiting transitions, not serialize `HumanLoopRuntime`.

---

## 7. Nonserializable `WorkflowState` inventory

Declared TypedDict keys (verified vs `WorkflowState.__annotations__`):

`messages`, `skill_name`, `workflow_id`, `workflow_version_id`, `user_input`, `kb_enabled`, `memory_mode`, `metadata`, `memory_context`, `node_outputs`, `execution_trace`, `branch_decisions`, `sys_vars`, `workflow_node_types`, `node_llms`, `stream_output_enabled`, `output_stream_source_node_id`, `structured_input`, `env_vars`, `env_specs`.

### Nonportable / non-JSON runtime anchors

| Anchor | Why |
|---|---|
| `metadata["human_loop_runtime"]` | `HumanLoopRuntime` holds `session_factory`, callbacks, cancel checker |
| `metadata["on_*"]` callbacks | Callables from `stream_runtime` |
| `node_llms[*]` | Live `ChatOpenAI` / client objects |
| `messages[*]` | LangChain `BaseMessage` (need custom codec; raw `json.dumps` fails) |
| `GLOBAL_HUMAN_LOOP_COORDINATOR` | Process-local `threading.Event` waiters |
| daemon graph `Thread` + compiled graph | Not on state dict but owns execution |
| SQLAlchemy `Session` | Held indirectly by runtime |

Characterization suite: `backend/tests/test_workflow_state_serialization_characterization.py` (9 tests) — proves naive JSON serialization fails for runtime-bearing state.

**Plan 07 implication (reconfirmed):** do **not** serialize `WorkflowState`; introduce portable `DurableWorkflowStateV1` / frames inside Checkpoint v2.

---

## 8. `smart_capture` unsafe for Plan 07 + golden path intent

### 8.1 `smart_capture` graph (system asset)

Path: `backend/app/assistant/workflow/system_assets/workflows/smart_capture.json` (24 nodes, 27 edges) + nested `smart_capture_relation_followup`.

| Node type | Count / notes |
|---|---|
| tool | 6 — includes **`create_entry`**, **`update_entry`**, `list_entry_types`, `list_tags`, `search_similar_entries`, `get_entry_detail` |
| llm | 5 |
| human_in_loop | **2** (`human_triage`, `human_confirm_write`) → classifier `interrupt_mode=legacy_blocking`, `side_effect=draft` |
| code_executor | 1 → `unknown` classification |
| workflow_call | 1 → nested relation followup (more HITL + writes) |
| if_else / output / start | control |

**Verdict:** full `smart_capture` is **unsafe** for Plan 07 Main Agent enablement (write Tools, code execution, nested write follow-up, dual legacy-blocking humans). Publication/admission for durable must deny this graph wholesale.

### 8.2 Locked golden path intent (plan §15)

Canonical name intent: **`durable-proposal-review`** (adjust only if Plan 01 naming rules require).

```text
start
  -> exact-model structured proposal (compute)
  -> editable durable approval OR structured input
  -> output approved proposal as private Artifact + bounded user text
```

Requirements: complete model/dependency/output Schema closure; `parallel_safe=false`; `interrupt_mode=durable`; business side effect `compute`; hidden/evaluation-only until rollout; **zero** Entry/Tag/Relation/Draft/HTTP/external writes.

---

## 9. Contract import paths (Plan 02/03/05/06)

| Symbol | Module |
|---|---|
| `CapabilityGateway` | `app.assistant.capabilities.gateway` |
| `FrozenCapabilityBinding` | `app.assistant.capabilities.contracts` |
| `ProviderLoopContinuation` / `ManifestEffectLifecyclePort` | `app.assistant.provider_loop.contracts` |
| Protected Provider message roles | `app.assistant.provider_loop.messages` |
| `EffectiveRunPolicySnapshot` | `app.assistant.policy.contracts` |
| `BudgetLedgerState` | `app.assistant.policy.budgets` |
| `ObligationLedgerState` | `app.assistant.policy.obligations` |
| `CapabilityCallFrame` | `app.assistant.policy.recursion` |
| `DurableAgentCheckpointV1` / codec | `app.assistant.durable.contracts` / `codec` |
| `DurableRunRepository` | `app.assistant.durable.repository` |
| `MainAgentRunExecutor` | `app.assistant.durable.runner` |
| `WorkflowState` | `app.assistant.workflow.engine.state` |
| `HumanLoopCoordinator` / `HumanLoopRuntime` | `app.assistant.workflow.human_approval_runtime` |
| HITL node builder | `app.assistant.workflow.engine.node_builders.human_in_loop_node` |
| Frontend shared HITL | `frontend/src/features/shared/hitl/*` |
| Assistant chat store / SSE cursor | `frontend/src/features/assistant/stores/chat-store.ts`, `hooks/useChat.ts` |

§16 plan paths for Plan 07 create/modify targets were rechecked against the post-Plan-06 tree: node builders under `workflow/engine/node_builders/` (not legacy `workflow/nodes/`); durable package under `app/assistant/durable/`; no stale create-path correction required beyond the plan’s own note.

---

## 10. Plan 02B coordination status

| Item | Status |
|---|---|
| Plan 02B coordination | **`complete`** |
| Flags | `PLAN_02B_READY=yes`, `FULL_PLAN_02_COMPLETE=yes` |
| Evidence | `docs/superpowers/evidence/plan-02b-final.md`, `plan-02b-observation.md` |

Non-blocking for Plan 07. Do **not** import OpenClaw observation/cleanup contracts.

---

## 11. §16 path confirmation

Create targets (not yet present — expected pre-Task 1):

- `backend/app/assistant/workflow/durable/*` — absent (to create)
- Durable interrupt migration — absent (to generate)
- `frontend/.../DurableInterruptCard.tsx` — absent (optional)

Modify targets — **all present** at post-Plan-06 paths listed in plan §16 (`durable/contracts|codec|repository|checkpoints|recovery`, `worker.py`, `models/schemas/router/config`, HITL node + human_approval_runtime, frontend assistant + shared hitl, compose/env, CI).

No plan-document path amendment required in Task 0 (plan already records that old draft `workflow/nodes/...` paths are wrong).

---

## 12. Verdict

```text
PLAN_07_TASK0_STATUS=pass
PLAN_06_FREEZE_COMMIT=8f253db4dde0d72bc45545ce8c99f6f211a2b8e0
PLAN_06_DEV_TIP_EQUIVALENT=44aee03
ALEMBIC_HEAD=6af373ef040f
ASSISTANT_MAIN_AGENT_MODE_DEFAULT=off
ASSISTANT_DURABLE_INTERRUPTS_ENABLED=not_yet_present
PLAN_02B_STATUS=complete
STOP_CONDITIONS=none_failed
PG_TWO_SESSION_EVIDENCE=skipped_ci_gated
MINIO_LIVE_EVIDENCE=skipped_ci_gated
COMPOSE_GOLDEN=not_run
PRODUCTION_READ_ONLY_DUAL_WIRING=incomplete_honest_gap
SMART_CAPTURE_PLAN07=unsafe
GOLDEN_PATH_INTENT=durable-proposal-review (start->llm proposal->durable human->artifact output)
CHARACTERIZATION_TEST=backend/tests/test_workflow_state_serialization_characterization.py
```

**Task 1 may start.** Do not invent a second Run state machine, reduced message codec, or parallel activation ledger in Plan 07.
