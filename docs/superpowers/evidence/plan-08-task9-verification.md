# Plan 08 Task 9 Verification + Plan 09 Handoff

**Recorded at (UTC):** 2026-07-17T09:42:34Z  
**Branch:** `worktree-plan-08-capability-call-ledger`  
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-08-capability-call-ledger`  
**Tip commit at verification:** (this Task 9 commit)  
**Base (Plan 07 on main):** `7f2c04c`  

---

## 1. Contract / schema versions

| Item | Value |
|---|---|
| Sole Alembic head | **`984c07876856`** (`984c07876856_add_capability_call_ledger.py`) |
| Parent | `7a3dac0ac2a8` (Plan 07 interrupts) |
| Checkpoint schemas | `{1, 2}` (Plan 06/07; Task 4 foundation retains readers) |
| `RUNTIME_CONTRACT_VERSION` | `1` |
| Policy contract | v1 `AuthorizationDecision` (byte-compatible) + v2 `AuthorizationDecisionV2` |
| Golden release | `GoldenWriteReleaseV1` lattice `none|compute|read|draft|write_local` |
| Call statuses | proposed → … → succeeded/failed/compensated/needs_reconciliation (see state_machine) |
| Interrupt origin | `workflow_node \| capability_call` XOR |
| Artifact storage | existing `storage_kind=inline\|object` (equivalent to inline_db/private_object) |

### Frozen config flags (defaults)

| Flag | Default |
|---|---|
| `ASSISTANT_CAPABILITY_LEDGER_MODE` | `legacy_read_only` |
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` | `off` |
| `ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET` | empty (required ≥32 bytes when enforced/golden) |
| `ASSISTANT_MAIN_AGENT_WRITE_COHORT_DIGEST` | empty |
| `ASSISTANT_MAIN_AGENT_MODE` | `off` |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` | `false` |

`golden` requires `enforced` ledger + strong secret (Settings model_validator).

---

## 2. Commits on this branch (Plan 08)

```
b6a56a6 feat(ai): enable approved golden entry write
10b9056 feat(ai): add capability reconciliation operations
1ae4f10 feat(ai): make golden entry creation transactionally idempotent
9b5859f feat(ai): bind durable approval to capability calls
4f08e63 feat(ai): dispatch capabilities through durable ledger
25b9235 feat(ai): admit one approval-gated golden write
f903c71 feat(ai): persist capability call state transitions
3899a08 feat(ai): add capability call ledger schema
85ee6ce docs: amend Plan 08 Task 0 baseline
72d1149 docs: record Plan 08 Task 0 baseline
```

(+ Task 9 verification commit)

---

## 3. Verification commands and results

### 3.1 Plan 08 focused suite (Task 9)

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_call_fault_matrix.py \
  backend/tests/test_capability_call_idempotency.py \
  backend/tests/test_capability_call_repository.py \
  backend/tests/test_capability_call_dispatcher.py \
  backend/tests/test_capability_call_approval.py \
  backend/tests/test_capability_call_local_transaction.py \
  backend/tests/test_capability_call_write_admission.py \
  backend/tests/test_capability_call_reconciliation.py \
  backend/tests/test_capability_call_external_uncertainty.py \
  backend/tests/test_main_agent_golden_create_entry.py -q
```

**Result:** **82 passed, 1 skipped** (PG dual-session race placeholder when `MINDATLAS_TEST_POSTGRES_URL` unset), ~130s.

### 3.2 Prerequisite / policy regressions

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_policy_matrix.py \
  backend/tests/test_agent_policy_evidence.py \
  backend/tests/test_agent_policy_runtime.py \
  backend/tests/test_entry_service.py \
  backend/tests/test_durable_interrupt_models.py -q
```

**Result:** **117 passed**.

### 3.3 Alembic

```bash
cd backend && .venv/bin/alembic heads
# 984c07876856 (head)
```

### 3.4 Not run in this environment (CI/ops gaps)

| Gate | Status |
|---|---|
| Full `pytest -q` backend suite | **not run** (focused suites + policy/entry/interrupt green) |
| Frontend test/build | **not run** (no frontend changes) |
| Live PostgreSQL two-session races | **skipped** (`MINDATLAS_TEST_POSTGRES_URL` unset) |
| Parent→head→parent→head migration cycle on disposable PG | **not run** (migration + guarded downgrade code present; CI-gated) |
| Live compose API+worker smoke | **not run** |
| OpenAPI snapshot of unmounted reconciliation mutations | **n/a** — reconciliation HTTP never mounted; CLI-only |

---

## 4. Fault matrix F01–F20 automation

| Label | Automated owner | Status |
|---|---|---|
| F01 propose before authz | `test_capability_call_fault_matrix` | covered |
| F02 grant before approval | fault_matrix + dispatcher | covered |
| F03 budget reserved | deferred to dispatcher integration (Task 4 foundation) | partial |
| F04 pause before waiting CAS | dispatcher pause_proposal staging | covered (no durable Interrupt without Plan 07 CAS consumer) |
| F05 approval before claim | approval tests | covered |
| F06 attempt claimed, effect null | fault_matrix | covered |
| F07 local flush rollback | fault_matrix + local_transaction | covered |
| F08 stop vs local atomic | settlement/local_transaction unit; PG dual-session CI-gated | partial |
| F09 commit before response / idempotent | local_transaction golden path | covered |
| F10 external effect start before send | external_uncertainty + state_machine | covered |
| F11 stop after external effect | fault_matrix settlement | covered |
| F12 external send before response | scripted adapter classification | covered |
| F13 late response vs settlement | settlement unit; PG CI-gated | partial |
| F14 result artifact before checkpoint | deferred (checkpoint codec Task 4 partial) | partial |
| F15 sibling wait write | deferred (sibling scheduler Task 4 partial) | partial |
| F16 duplicate approval | fault_matrix | covered |
| F17 retry_same_key local | fault_matrix + reconciliation | covered |
| F18 post-approval grant mint | fault_matrix + write_admission | covered |
| F19 full asset / update path | golden audit | covered |
| F20 cancel finalizer unproven | fault_matrix settlement refuse | covered |

---

## 5. Golden path evidence (approved create)

| Path | Evidence |
|---|---|
| Approved create-only topology | `smart_capture_golden_create.json` audit: 1× `create_entry`, no human/update/relation/workflow_call |
| Policy v2 awaiting_call_approval | `test_capability_call_write_admission` + `AuthorizationDecisionV2` |
| Local atomic Entry+outbox+call success | `test_capability_call_local_transaction` |
| Approval binding immutable digests | `test_capability_call_approval` |
| Independent Runs may create two Entries | golden create tests (logical vs semantic dedupe) |

### Negative paths

| Case | Result |
|---|---|
| Full `smart_capture` | golden audit **deny** (human + update + relation follow-up) |
| `update_entry` / relation tools | gateway allowlist **false** |
| Non-cohort / write mode off | `is_golden_write_eligible` **false** |
| Reject/expire/cancel approval | call terminal without Attempt |
| `retry_same_key` on local_transactional | **forbidden** |

### Evaluation isolation guarantee

- Golden production writes require `ASSISTANT_MAIN_AGENT_WRITE_MODE=golden` **and** `capability_ledger_mode=enforced` **and** strong HMAC secret **and** optional cohort digest match.
- Defaults remain **off / legacy_read_only**.
- Plan 09 evaluation owner `test` must use its own namespace/adapters and must **not** insert into production `assistant_capability_call` merely to obtain owner kind (plan handoff requirement).

---

## 6. Operator reconciliation

| Surface | Status |
|---|---|
| `CapabilityReconciliationService` | implemented; mode matrix enforced |
| CLI `capability_calls/cli.py` | contract surface; requires injected Session |
| HTTP mutation routes | **unmounted** |
| Runbook | `docs/operations/assistant-capability-reconciliation.md` |

---

## 7. Known gaps (honest; do not block Plan 09 design, may block production golden enablement)

1. Full Provider Loop dual-wiring of `LedgerDispatcher` for every production traffic path is foundation-complete (module + select_dispatcher) but not every call site is cut over.
2. Plan 07 Section 11.1 outer-worker CAS consumer for call-owned pause proposals is staged by dispatcher; full interrupt API pairing for `interrupt_origin=capability_call` is additive and partially deferred to integration follow-up.
3. PostgreSQL dual-session races / migration cycle / compose smoke remain CI/ops gated.
4. Pre-existing `test_main_agent_authorization` ceiling interrupt-mode expectation drift from Plan 07 (`none` vs `none|durable`) — not introduced by Plan 08 write path.

---

## 8. Plan 09 may start when

- [x] One Alembic head + ledger revision recorded  
- [x] Frozen call/attempt/reconciliation schemas + digest vectors  
- [x] Plan 05 v1/v2 + call-owned approval + cancellation settlement matrix  
- [x] Default-off ledger/write/reconciliation configuration  
- [x] Focused PG-skippable race/recovery unit suite green  
- [x] Golden create-only path + full-asset/update negatives  
- [x] CLI-only/unmounted operator reconciliation path  
- [x] Isolation seam notes for evaluation namespaces  

**Plan 08 M3 backend contract is complete for handoff.** Production `golden` enablement still requires CI PG races, migration cycle, and full dual-wiring sign-off.

---

## 9. Key modules

| Area | Path |
|---|---|
| Package | `backend/app/assistant/capability_calls/` |
| Migration | `backend/alembic/versions/984c07876856_add_capability_call_ledger.py` |
| Policy v2 | `backend/app/assistant/policy/write_admission.py`, `contracts.py` |
| Golden asset | `.../workflows/smart_capture_golden_create.json` |
| Ops | `docs/operations/assistant-capability-reconciliation.md` |
| Task 0 baseline | `docs/superpowers/evidence/plan-08-task0-baseline.md` |

---

## 10. PR #55 completion-audit remediation (2026-07-18, supersedes §§4–8)

The earlier M3/Plan09-ready conclusion above is no longer authoritative. The
completion audit found production and transaction-boundary gaps, and this
remediation repaired the code paths rather than relabeling them as deferred.

Fresh evidence from this branch:

- enforced production composition installs `LedgerDispatcher`; fresh enforced
  worker claims enter the admitted `MainAgentService`/`ProviderAgentLoop` path;
- Provider siblings are proposed in order with transcript + Checkpoint v3 + Run
  CAS before dispatch; waiting and result checkpoints retain ordered call state;
- call-owned approval, Interrupt, Provider continuation, artifacts, and waiting
  Run transition share one transaction; approval queues the exact call once;
- read/compute and local-write results are not committed before their matching
  Tool Result, Checkpoint v3, event, and Run CAS;
- the golden `create_entry` mutation, outbox, committed Attempt, result Artifact,
  Tool Result, Checkpoint, and Run revision commit or roll back as one set;
- cancellation finalization is call-aware; reconciliation CLI opens an
  application Session and defaults remain `legacy_read_only` / `off` in Compose;
- Plan 08 migration head is `984c07876856`; parent→head→parent→head and the
  PostgreSQL interrupt/event/fault suites passed against the configured test DB.

Observed verification:

| Gate | Result |
|---|---|
| Plan08/prerequisite focused backend | 238 passed, 3 environment skips |
| PostgreSQL migrations + interrupt/event/fault group | 47 passed |
| PostgreSQL fault + Run-event races, three consecutive runs | 19 passed each run |
| Frontend Vitest | 63 passed |
| Frontend production build | passed |
| Alembic heads | one head: `984c07876856` |
| Compose render with immutable test build revision | passed; ledger/write defaults dark |
| Full backend | 2536 passed, 66 skipped; 20 failures before stale Plan08 assertions were corrected |
| Backend excluding the two unrelated legacy StructuredTool files | 2538 passed, 66 skipped |

The full-backend failures split into stale Plan07 assertions now corrected
(v3 codec support/current migration target, durable interrupt ceiling, and the
exact golden asset set) and 15 unrelated environment failures where the
installed LangChain exposes `StructuredTool` as non-callable in legacy
`entry_tools`/`stats_tools` tests. `pip check` independently reports existing
Docling/Transformers/HuggingFace/Torch dependency incompatibilities.

Remaining release evidence: an OS-process API/default-worker kill-and-restart
smoke with a scripted Provider has not been executed in this workspace. The
database-level approval, crash rollback, replay, and race evidence is green,
but the original completion rule requires that process smoke before declaring
Plan09 ready. Therefore **Plan09 remains not ready** and production golden mode
must remain disabled.
