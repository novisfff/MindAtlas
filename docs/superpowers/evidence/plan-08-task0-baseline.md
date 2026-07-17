# Plan 08 Task 0 Baseline (Plans 01–07 Freeze + Pre-Ledger Write Characterization)

**Recorded at (UTC):** 2026-07-17T08:25:23Z  
**Branch:** `worktree-plan-08-capability-call-ledger`  
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-08-capability-call-ledger`  
**HEAD at freeze:** `7f2c04c2a35cc72945f5bfd66c6c7c595991c6e5`  
**HEAD subject:** `feat(ai): Plan 07 durable workflow interrupt and resume (#54)`  
**Working tree product code at freeze start:** clean (untracked local `backend/.venv` symlink only).

---

## 1. Environment

| Item | Value |
|---|---|
| Python (local venv) | **3.12.3** (production target remains **3.11**; local drift is **not** Plan 08 compatibility evidence) |
| `backend/requirements.txt` pin `langgraph` | `langgraph==0.3.34` |
| Installed `langgraph` (local venv) | **1.2.9** (mismatch vs pin — same class of drift Plans 05–07 recorded) |
| Installed `langchain` / `langchain-core` | 1.3.13 / 1.4.9 |
| pydantic | 2.13.4 |
| sqlalchemy | 2.0.51 |
| jsonschema | 4.26.0 |
| alembic | 1.18.5 |
| fastapi | 0.139.0 |
| httpx | 0.28.1 |
| cryptography | 49.0.0 |
| Sole Alembic head | **`7a3dac0ac2a8`** (`7a3dac0ac2a8_add_durable_run_interrupts.py`; parent `6af373ef040f`) |
| Occupied revision ID named in plan draft | **`a3b4c5d6e7f8`** is already used by `a3b4c5d6e7f8_add_workflow_call_child_memory_table.py` |
| Task 1 migration parent | **`7a3dac0ac2a8`** (sole post-Plan-07 head). Generate a **fresh unique** revision ID; do **not** preselect `a3b4c5d6e7f8`. |
| `MINDATLAS_TEST_POSTGRES_URL` | **unset** (PG two-session suites skipped) |
| `MINDATLAS_TEST_MINIO` | **unset** (MinIO suites skipped) |
| Live Docker compose golden | **not run** |

### Worker / codec / flags (frozen at Plan 07 tip)

| Item | Value |
|---|---|
| Checkpoint `SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS` | **`frozenset({1, 2})`** (`app.assistant.durable.codec`) |
| `RUNTIME_CONTRACT_VERSION` | **1** (`app.assistant.durable.worker_registry`) |
| Worker capability feature digest | `94d7b054b85208d3fcab3279bd60b5292d65d0e890285d4dcf7d0871ee2431c9` |
| `ASSISTANT_MAIN_AGENT_MODE` default | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` default | **`false`** |
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` | **absent** (Plan 08 will add; default must be `off`) |
| `ASSISTANT_CAPABILITY_LEDGER_MODE` | **absent** (Plan 08 will add; default must be `legacy_read_only`) |
| Main Agent effect ceiling revision | **`plan07-v1`** |
| Main Agent read-only ceiling digest | `c67231e1c3372271ba2b56e450779889d38a28d1bd04b09c202e812b12077669` |
| Ceiling allowed side effects | **`("none", "compute", "read")`** |
| Ceiling allowed interrupt modes | **`("none", "durable")`** (Plan 07 extended; still **no** write) |
| Plan 05 hard release gate | **`PLAN05_RELEASE_GATE_SIDE_EFFECTS = ("none", "compute", "read")`** |
| Policy decision type (merged name) | `AuthorizationDecision` in `app.assistant.policy.contracts` (**no** `contract_version` field yet — Plan 08 Task 3 introduces tagged V1\|V2 union) |
| Budget suspension contract | `BudgetSuspensionStateV1` with fields: `contract_version`, `run_id`, `interrupt_id`, `parent_budget_revision_id`, `parent_ledger_revision`, `parent_ledger_digest`, `suspended_at_utc`, `remaining_active_ms`, `human_wait_expires_at_utc`, `suspension_digest` |

### Prior evidence paths

| Evidence | Path |
|---|---|
| Plan 05 Task 0 / Task 9 | `docs/superpowers/evidence/plan-05-task0-baseline.md`, `plan-05-task9-verification.md` |
| Plan 06 Task 0 / Task 10 | `docs/superpowers/evidence/plan-06-task0-baseline.md`, `plan-06-task10-rollout.md` |
| Plan 07 Task 0 / Task 9 / Task 10 | `docs/superpowers/evidence/plan-07-task0-baseline.md`, `plan-07-task9-golden-path.md`, `plan-07-task10-verification.md` |

---

## 2. Hard-gate prerequisite proofs

### 2.1 Plan 05 — independent read-only grants (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Hard release gate denies `draft \| write_local \| write_external \| unknown` | **pass** | `PLAN05_RELEASE_GATE_SIDE_EFFECTS = ("none", "compute", "read")` in `app.assistant.policy.contracts`; evaluator intersects platform ceiling with release gate **before** descriptor inspection (`derive_effective_capability_grant` in `app.assistant.policy.evaluator`) |
| Platform ceiling is read-only | **pass** | `MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects == ("none", "compute", "read")` in `app.assistant.main_agent.authorization` |
| Grant is independently derived (not descriptor-copied) | **pass** | `derive_effective_capability_grant(...)` builds `EffectiveCapabilityGrant` from platform/entrypoint/global/owner/principal intersection; descriptor side-effect is checked **after** grant exists (`evaluate_authorization` step order documents `release_gate_denied` after grant construction) |
| Descriptor `write_local` alone cannot authorize | **pass** | `SYSTEM_TOOL_CLASSIFICATIONS["create_entry"] = ("write_local", False)` exists, but release gate + ceiling exclude `write_local`; `evaluate_authorization` returns deny with `release_gate_denied` / effect-not-in-grant when actual effect ∉ grant |
| No Plan 05 v2 / write release record yet | **pass (expected gap for Plan 08)** | `AuthorizationDecision` has no `contract_version` / `write_release_digest` / `dispatch_disposition`; Plan 08 Task 3 must add tagged V1\|V2 without mutating V1 bytes |

**No Plan 05 stop-condition failure.** Write enablement is correctly absent; Plan 08 policy v2 is an explicit versioned extension, not a repair.

### 2.2 Plan 06 — stop revision / result-source CAS (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Ordinary results cannot commit from `cancelling` | **pass** | `ALLOWED_TRANSITIONS` in `app.assistant.durable.repository` has **no** `(cancelling → completed/failed)` ordinary-result edge; only `(cancelling → cancelled): cancel_finalizer`. Comment at repository ~L1187: "Ordinary results must not overwrite cancelling — enforced by allowed_from." |
| Stop increments `state_revision` | **pass (unit-proven historically)** | `(running\|recovering → cancelling): stop`; high-level stop methods require CAS on expected revision |
| `cancelling → needs_reconciliation` **not** present yet | **pass (expected Plan 08 delta)** | Current table has `(running\|recovering → needs_reconciliation): reconcile` only. Plan 08 Task 2 must add **exactly one** new Run edge: `cancelling → needs_reconciliation` when an already-started call is unproven |
| Cancellation finalizer is the only path to `cancelled` from `cancelling` | **pass** | `ALLOWED_TRANSITIONS[(STATUS_CANCELLING, STATUS_CANCELLED)] = "cancel_finalizer"` |
| Unit tests pin stop/result semantics | **pass** | `backend/tests/test_durable_run_repository.py`, `test_durable_run_streaming.py` contain stop/cancel/result coverage |

**No Plan 06 stop-condition failure.** Plan 08 settlement join is additive, not a rewrite of ordinary-result rules.

### 2.3 Plan 07 — idempotency-first resolve + public request ID + versioned suspension (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Resolve locks Run first | **pass** | `InterruptRepository.resolve_pending` docstring + body: step 1 `_lock_run(run_id)` (`app.assistant.workflow.durable.interrupts`) |
| Idempotency lookup **before** consumed-token / pending validation | **pass** | Step 2: `get_by_resolution_request_id(..., for_update=True)` runs **before** locking the Interrupt and verifying token/revisions/deadline. Comment: "Idempotency first (Plan 07 §10.4 / §11.2)." |
| Public terminal `resolutionRequestId` | **pass** | Column `AssistantRunInterrupt.resolution_request_id`; API serializer `serialize_interrupt_safe(..., include_resolution_request_id=True)` emits camelCase `resolutionRequestId` (`interrupt_api.py`) |
| Versioned budget suspension | **pass** | `BudgetSuspensionStateV1` frozen contract with `suspension_digest`; pause path persists immutable suspension; resume parses via `_parse_suspension` |
| Resume Checkpoint lineage | **pass** | Resume path materializes `interrupt_resume` Checkpoint with suspension + resolution evidence (`resume.py`, pause CAS in `pause.py`) |
| Interrupt identity is Workflow-node-shaped | **pass** | `AssistantRunInterrupt`: `workflow_frame_id` / `node_id` / `node_visit_id` are **non-null**; `capability_call_id` is **nullable and always null in Plan 07** (model comment). Plan 08 Task 1 adds `interrupt_origin` discriminator + XOR profile |
| Checkpoint codec retains v1 and v2 | **pass** | `SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS = {1, 2}`; Plan 08 must retain lossless readers |

**No Plan 07 stop-condition failure.** Call-owned Interrupt profile is an explicit Plan 08 extension of the same pause/resolve CAS path.

### 2.4 Stop-condition summary

| Gate | Result |
|---|---|
| Exact policy evidence reconstructible from immutable state | **pass** |
| Stop vs ordinary result cannot diverge into false success from `cancelling` | **pass** |
| Resolution retry hits idempotency before consumed-token checks | **pass** |
| Budget suspension / resume lineage versioned | **pass** |
| One Alembic head | **pass** (`7a3dac0ac2a8`) |

**Plan 08 Task 1 may begin.** No prerequisite plan amendment required.

### Honest enablement gaps (do **not** auto-stop Plan 08)

1. Production Main Agent mode remains **`off`**; dual-wiring / live `read_only` enablement gaps from Plans 06–07 still apply.
2. PostgreSQL two-session CAS/events/lease suites CI-gated — skipped here (`MINDATLAS_TEST_POSTGRES_URL` unset).
3. MinIO private Artifact live suites CI-gated — skipped.
4. Live compose golden path not run.
5. Local venv `langgraph`/`langchain` drift vs pins (see §3 failures).

These are enablement/ops gaps. Durable CAS/codec/contracts/interrupt infrastructure required by Plan 08 **are present**.

---

## 3. Prerequisite regression runs (this Task 0)

Fernet key generated per-process. PG/MinIO env vars unset.

### 3.1 Primary Task 0 suite

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_policy_runtime.py \
  backend/tests/test_durable_run_events_postgres.py \
  backend/tests/test_durable_run_streaming.py \
  backend/tests/test_durable_interrupt_api.py \
  backend/tests/test_durable_interrupt_repository_postgres.py \
  backend/tests/test_entry_service.py \
  backend/tests/test_entry_tools.py -q
```

| Result | Count |
|---|---|
| passed | **80** |
| skipped | **16** (PG two-session / env-gated) |
| failed | **8** (all in `test_entry_tools.py`) |
| warnings | 1 (Starlette/httpx TestClient deprecation) |
| duration | ~221s |

### 3.2 `test_entry_tools.py` baseline failures (recorded, not Plan 08 regressions)

All failures share one root cause under current local deps:

```
TypeError: 'StructuredTool' object is not callable
```

at `EntryToolsValidationTests._with_db` → `create_entry(...)`.

| Observation | Detail |
|---|---|
| Cause class | `@tool`-decorated `create_entry` is a LangChain `StructuredTool`; tests call it as a plain function. Local `langgraph==1.2.9` / `langchain-core==1.4.9` vs pins `langgraph==0.3.34` / `langchain-core>=0.3.0,<1.0` |
| Same drift class | Plans 05–07 Task 0 baselines already recorded local langgraph/langchain mismatch |
| Product path still clear | `create_entry` body still calls `EntryService(db).create(request)` after `_build_entry_request` (see §4). Characterization of commit semantics does not depend on invoking the StructuredTool wrapper as a bare callable |
| Action for Plan 08 | **Do not** "fix" by making golden path call the decorated tool. Task 6 must extract `create_in_uow` and forbid the golden adapter from calling the decorated `create_entry` / committing `EntryService.create()`. Optional later hygiene: tests may use `.invoke` / unwrap — out of Task 0 scope unless characterization is unpinned |

`test_entry_service.py` portion of the suite is included in the 80 passes (service-level create/commit path remains pinned).

### 3.3 Gate interpretation

- Policy / durable run / interrupt suites: green or correctly skipped.
- Entry **service** commit semantics: green.
- Entry **tool wrapper call-shape** under drifted langchain: red locally; recorded as baseline env drift, not a missing Plan 05–07 semantic.

---

## 4. Current local write characterization (`create_entry`)

### 4.1 Call chain (today)

```
Provider/Workflow tool dispatch
  → app.assistant.tools.entry_tools.create_entry  (@tool StructuredTool)
      → _get_db() / tool context Session
      → _build_entry_request(db, title/summary/content/type_code/tags/time_*)
           • type resolution (blank → default enabled type; invalid → ValueError)
           • tag resolve/create (case-insensitive reuse; max 5 new tags) on same Session
           • time defaulting / validation
      → EntryService(db).create(request)
           1. type_service.find_by_id
           2. tag_service.find_by_ids
           3. Entry(...) + tags assign
           4. db.add(entry); db.flush()
           5. db.add(EntryIndexOutbox(entry_id, op="upsert", entry_updated_at, status="pending"))
           6. db.commit()          ← owns transaction lifecycle
           7. db.refresh(entry)
      → _serialize_entry_tool_result(entry) → JSON string Tool Result
```

Exact anchors:

| Piece | Path |
|---|---|
| Tool wrapper | `backend/app/assistant/tools/entry_tools.py::create_entry` (~L668) |
| Request builder / tag creation | `entry_tools.py::_build_entry_request` (~L545) |
| Committing service | `backend/app/entry/service.py::EntryService.create` (~L94–L131) |
| Entry model | `backend/app/entry/models.py::Entry` |
| Index outbox | `backend/app/lightrag/models.py::EntryIndexOutbox` |
| Classification | `SYSTEM_TOOL_CLASSIFICATIONS["create_entry"] = ("write_local", False)` |

### 4.2 What is **not** present (Plan 08 must add)

| Missing | Implication |
|---|---|
| `source_capability_call_id` on Entry | No business-level link to a ledger call; Task 1/6 add unique nullable column |
| `EntryService.create_in_uow(...)` | No no-commit core; Task 6 extracts it |
| Shared `CapabilityUnitOfWork` | Service owns `commit`/`refresh`; golden adapter cannot share atomic success with call/attempt/Artifact/Checkpoint |
| Ledger / Attempt / idempotency key | Recovery after lost response can re-invoke `create` → **duplicate Entry risk** |
| Gateway/UoW commit spy | No architecture test forbidding commit below ledger-owned UoW |

### 4.3 Other write tools (denied for golden Main Agent surface)

| Tool | Classification | Notes |
|---|---|---|
| `update_entry` | `write_local`, not parallel_safe | Must remain denied |
| `create_relation` | `write_local`, not parallel_safe | Must remain denied |
| `openclaw_capture_entry` / `openclaw_create_relation` | `write_local` | Must remain denied |
| `generate_weekly_report` / `generate_monthly_report` | `write_local` | Must remain denied |

HTTP `EntryService.create` callers (non-tool) keep the committing wrapper after Task 6.

---

## 5. Side-effect classification / graph inventory

### 5.1 System tool registry (`SYSTEM_TOOL_CLASSIFICATIONS`)

| domain_key | side_effect | parallel_safe | Plan 08 release eligibility |
|---|---|---|---|
| `search_entries` | read | true | read/compute ledger only |
| `search_similar_entries` | read | true | read/compute ledger only |
| `get_entry_detail` | read | true | read/compute ledger only |
| `list_entry_types` | read | true | read/compute ledger only |
| `list_tags` | read | true | read/compute ledger only |
| `get_statistics` / `get_entries_by_time_range` / `analyze_activity` / `get_tag_statistics` | read | true | read/compute ledger only |
| `query_knowledge_graph` / `kb_search` / `kb_relation_recommendations` / `openclaw_query_knowledge_graph` | read | false | read ledger; not parallel |
| `openclaw_search_entries` / `openclaw_get_entry` | read | true | read/compute ledger only |
| **`create_entry`** | **write_local** | false | **only** after Plan 08 v2 golden release + call-owned approval + `local_transactional` |
| `update_entry` | write_local | false | **denied** entire Plan 08 |
| `create_relation` | write_local | false | **denied** |
| `openclaw_capture_entry` / `openclaw_create_relation` | write_local | false | **denied** |
| `generate_weekly_report` / `generate_monthly_report` | write_local | false | **denied** |

Main Agent controls (`MAIN_AGENT_CONTROL_CLASSIFICATIONS`): `skill.search` read, `skill.inject` none, `skill.read_resource` read, `artifact.read` read — no write.

**Execution mode:** no `execution_mode` field exists on descriptors yet. Plan 08 Task 1–2 introduce `CapabilityExecutionMode`; missing classification defaults to **`unsupported`**, never inferred from tool name.

### 5.2 Full `smart_capture` asset (denied as golden path)

Path: `backend/app/assistant/workflow/system_assets/workflows/smart_capture.json`

| Metric | Value |
|---|---|
| Nodes / edges | 24 / 27 |
| Node types | start×1, tool×6, llm×5, if_else×4, **human_in_loop×2**, output×4, code_executor×1, **workflow_call×1** |
| Human nodes | `human_triage` ("人工判断新建或合并"), `human_confirm_write` ("确认写入内容") |
| Write tools | `tool_update` → `update_entry`; `tool_create` → `create_entry` |
| Child workflow | `call_relation_followup` → pinned `smart_capture_relation_followup` |
| Why denied | Two human nodes; create **and** update branches; unconditional relation-follow-up child that can create relations; code_executor; not a create-only linear graph; not published as `smart-capture-golden-create` |

### 5.3 `smart_capture_relation_followup` (denied)

| Metric | Value |
|---|---|
| Nodes / edges | 12 / 11 |
| Human nodes | `human_confirm_relations` |
| Tools | `kb_relation_recommendations` (read) + iteration/if_else structure that enables relation creation path |
| Why denied | Relation follow-up / additional human approval; not part of golden create-only closure |

### 5.4 Golden path requirements (Plan 08 Task 8 — not present yet)

Must publish **new** hidden asset `smart-capture-golden-create` (do **not** edit existing `smart_capture*.json`):

```
start → reviewed read/compute preparation → structured create input → create_entry → output
```

- Zero `human_in_loop` Workflow nodes (approval is call-owned Interrupt via LedgerDispatcher).
- Exactly one write binding/version: reviewed `create_entry` / `local_transactional`.
- No update/merge/delete/relation/follow-up/code/HTTP/dynamic edges.

---

## 6. Executable fault matrix (Task 9 ownership)

Stable labels for automated tests. Expected states are post-condition sketches; Task 9 fills exact DB/event assertions.

| Label | Boundary | Expected call / attempt / Run / business | Owning future test file |
|---|---|---|---|
| `F01_propose_before_authz` | crash after propose row, before policy decision | call `proposed` or absent; no Attempt; no Entry | `test_capability_call_dispatcher.py` |
| `F02_grant_before_approval` | v2 golden grant derived, approval not satisfied | call `awaiting_approval`; no Gateway evidence; no Entry | `test_capability_call_write_admission.py` |
| `F03_budget_reserved_before_transition` | budget reserved, call not transitioned | reservation held or released by recovery; no adapter | `test_capability_call_dispatcher.py` |
| `F04_pause_proposal_before_waiting_CAS` | `CapabilityCallPauseProposalV1` staged, Plan 07 CAS not committed | neither Interrupt nor call `awaiting_approval` durable; replay converges | `test_capability_call_dispatcher.py` / interrupt PG tests |
| `F05_approval_before_worker_claim` | approval resolved, worker not claimed | call `authorized`; one resume Checkpoint; at most one later Attempt | `test_durable_interrupt_api.py` |
| `F06_attempt_claimed_before_dispatch` | Attempt claimed, adapter not entered | call `executing`; `side_effect_started_at` null; no Entry | `test_capability_call_dispatcher.py` |
| `F07_local_flush_before_commit` | Entry/outbox flushed inside UoW, commit not yet | crash → zero Entry/outbox/succeeded-call (full rollback) | `test_capability_call_local_transaction.py` |
| `F08_stop_vs_local_atomic` | API stop races local atomic success | zero complete set **or** one Entry+outbox+succeeded-call; never durable local `executing + side_effect_started_at != null` | `test_capability_call_cancellation_postgres.py` |
| `F09_commit_before_response` | atomic commit ok, worker response lost | recovery returns stored result; **no** second `create_in_uow` | `test_capability_call_local_transaction.py` |
| `F10_external_effect_start_before_send` | external mode: `side_effect_started_at` committed, network not sent | call `executing` + effect started; recovery per mode matrix | `test_capability_call_external_uncertainty.py` |
| `F11_stop_after_external_effect_start` | stop after effect started, outcome unproven | call `unknown → needs_reconciliation`; Run `cancelling → needs_reconciliation`; **not** false `cancelled` | `test_capability_call_cancellation_postgres.py` |
| `F12_external_send_before_response` | request may have left process, no response | `unknown` then reconciliation obligation | `test_capability_call_external_uncertainty.py` |
| `F13_late_response_vs_settlement` | late trusted response vs cancel settlement | special settlement may commit honest call terminal from `cancelling`; no new I/O | `test_capability_call_cancellation_postgres.py` |
| `F14_result_artifact_before_checkpoint_CAS` | result Artifact written, Checkpoint CAS fails | no false Tool pairing; recovery reuses or reconciles | `test_capability_call_repository.py` |
| `F15_sibling_wait_write` | multi Tool Call message, one write waits | reads may complete; later writes stay `proposed`; Provider turn waits full pairing | `test_capability_call_dispatcher.py` |
| `F16_duplicate_approval_decision` | two HTTP decisions / workers | one authorization, one resume budget/Checkpoint, ≤1 dispatch | interrupt API + dispatcher |
| `F17_retry_same_key_local` | operator `retry_same_key` on local_transactional | **rejected** by mode matrix | `test_capability_call_reconciliation.py` |
| `F18_post_approval_grant_mint` | attempt to widen grant after approval | fatal policy-integrity; zero adapter start | `test_capability_call_write_admission.py` |
| `F19_full_asset_or_update_path` | invoke full smart_capture / update / relation | absent or denied before adapter; zero second business write | `test_main_agent_golden_create_entry.py` |
| `F20_cancel_finalizer_unsettled_started` | finalizer while started call unproven | refuse `cancelling → cancelled` | `test_capability_call_cancellation_postgres.py` |

Every label above must become an automated test by Task 9. No matrix row remains “manual only” unless a runbook names the external process action and automated postflight.

---

## 7. Operational start safety

| Check | Result |
|---|---|
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` | **absent** / not set |
| `ASSISTANT_CAPABILITY_LEDGER_MODE` | **absent** / not set |
| `ASSISTANT_MAIN_AGENT_MODE` | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` | **`false`** |
| Incompatible nonterminal Runs targeting unreleased ledger codec | **none** in this disposable worktree (no production DB attached) |
| Live worker targeting disposable migration DB | **none** |
| Examples default-off | `backend/.env.example` / `deploy/.env.example` show mode off; no write/ledger flags yet |

Safe to generate additive migrations and land storage-only Task 1 without enabling writes.

---

## 8. Alembic parent for Task 1

| Item | Value |
|---|---|
| Sole head | `7a3dac0ac2a8` — `add_durable_run_interrupts` (Plan 07) |
| Parent of head | `6af373ef040f` — `add_durable_agent_run_foundation` (Plan 06) |
| Plan draft collision | `a3b4c5d6e7f8` **occupied** by workflow child memory migration |
| Task 1 command | `cd backend && .venv/bin/alembic revision -m "add capability call ledger"` then assert unique revision + single head |

---

## 9. File ownership anchors (merged tree)

### Create in Plan 08

- `backend/app/assistant/capability_calls/` (`contracts`, `models`, `repository`, `idempotency`, `dispatcher`, `settlement`, `reconciliation`, `cli`, `router`, `schemas`)
- Generated `backend/alembic/versions/<rev>_add_capability_call_ledger.py`
- `docs/operations/assistant-capability-reconciliation.md`
- Tests listed in plan File Responsibility Map
- New golden Workflow fixture (not overwriting `smart_capture*.json`)

### Modify (confirmed present)

| Area | Merged path |
|---|---|
| Run / interrupt models | `backend/app/assistant/durable/models.py` (`AssistantRunInterrupt.capability_call_id` already nullable) |
| Durable repository / transitions | `backend/app/assistant/durable/repository.py` |
| Policy | `backend/app/assistant/policy/{contracts,evaluator,budgets,obligations}.py` |
| Gateway / classification | `backend/app/assistant/capabilities/{gateway,classification,contracts}.py` |
| Provider loop | `backend/app/assistant/provider_loop/loop.py` |
| Workflow durable | `backend/app/assistant/workflow/durable/{runner,interrupts,pause,resume,contracts,codec}.py` |
| Entry | `backend/app/entry/{service,models}.py`, `backend/app/assistant/tools/entry_tools.py` |
| Config / deploy | `backend/app/config.py`, `backend/.env.example`, `deploy/.env.example`, `deploy/docker-compose.yml` |

### Plan path note

Plan text refers to `AuthorizationDecisionV1`; merged code uses **`AuthorizationDecision`** without a version tag. Task 3 must introduce the tagged union (`AuthorizationDecisionV1 | AuthorizationDecisionV2`) carefully so existing V1 decision digests remain byte-compatible. No plan file edit required for Task 0 beyond this evidence record.

---

## 10. Task 0 exit

| Criterion | Status |
|---|---|
| Repository / runtime baseline recorded | **yes** |
| Plan 05–07 hard gates proven with symbols | **yes — all pass** |
| Prerequisite suite run + baseline failures recorded | **yes** (80 pass / 16 skip / 8 entry_tools drift) |
| `create_entry` write path characterized | **yes** |
| Classification + smart_capture denial inventory | **yes** |
| Executable fault matrix with owners | **yes** (F01–F20) |
| Operational flags off | **yes** |
| Real Alembic parent recorded | **yes** (`7a3dac0ac2a8`) |
| Runtime product code changed | **no** |
| Stop / amend prerequisite plan? | **no** |

**Plan 08 Task 1 may start.**
