# Plan 08 / PR #55 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the original Plan 08 exit criteria on PR #55 by wiring ledger admission, call-owned approval, atomic golden writes, replay, cancellation settlement, reconciliation, and PostgreSQL verification into the production durable Main Agent path.

**Architecture:** Add a durable capability-ledger aggregate port at the Provider Loop boundary. Enforced Runs use a ledger dispatcher backed by that port; legacy Runs retain the gateway dispatcher. The aggregate owns Run-first CAS, call/Attempt state, result Artifacts, Checkpoints, Tool Result pairing, Interrupts, budget/obligation revisions, and events; the golden Entry mutation joins the same SQL transaction.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 15, pytest/unittest, the existing durable Run and Capability Gateway contracts.

## Global Constraints

- Preserve Plan 05 v1 bytes/digests and Plan 06/07 Checkpoint readers.
- Keep deploy defaults `legacy_read_only` and write mode `off`.
- Permit only the exact frozen `smart-capture-golden-create -> create_entry` binding.
- Approval never creates or widens a grant.
- Lock Run before Interrupt/call rows; never reverse this order.
- A local write commits Entry, outbox, result, ledger state, Checkpoint, events, budget/obligation revisions, and Run revision together or rolls all back.
- Do not automatically retry after an external effect may have started.
- Do not mix unrelated `origin/main` failures into Plan 08 product changes.

---

### Task 1: Install a fail-closed enforced dispatcher in production

**Files:**
- Modify: `backend/app/assistant/provider_loop/contracts.py`
- Modify: `backend/app/assistant/capability_calls/dispatcher.py`
- Modify: `backend/app/assistant/main_agent/policy_runtime.py`
- Modify: `backend/app/assistant/durable/runner.py`
- Create: `backend/tests/test_capability_call_production_wiring.py`
- Modify: `backend/tests/test_capability_call_dispatcher.py`

**Interfaces:**
- Produces `CapabilityLedgerAggregatePort.prepare(request) -> LedgerPrepareOutcome`.
- Produces optional `ProviderLoopPorts.capability_ledger`; enforced production composition requires it.

- [ ] **Step 1: Write failing production wiring tests**

```python
def test_enforced_composition_installs_ledger_dispatcher(compose_args):
    compose_args["capability_ledger_mode"] = "enforced"
    compose_args["capability_ledger"] = RecordingAggregate()
    _, ports = compose_main_agent_policy_runtime(**compose_args)
    assert isinstance(ports.tool_dispatcher, LedgerDispatcher)


def test_enforced_missing_decision_never_calls_gateway(enforced_dispatcher):
    with pytest.raises(CapabilityCallConflict, match="ledger_decision_required"):
        enforced_dispatcher.dispatch(make_provider_request(), cancellation=NeverCancelled())
    assert enforced_dispatcher.inner.dispatch_calls == []
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_production_wiring.py backend/tests/test_capability_call_dispatcher.py -q
```

Expected: production composition still installs `MainAgentGatewayToolDispatcher`; missing disposition can dispatch.

- [ ] **Step 3: Define the port before its consumers**

Add to `provider_loop/contracts.py`:

```python
class LedgerPrepareOutcome(FrozenContract):
    kind: Literal["dispatch", "deny", "pause", "replay"]
    call_id: UUID
    call_revision: int
    attempt_id: UUID | None = None
    provider_result: ProviderDispatchResult | None = None
    pause_proposal: dict[str, JsonValue] | None = None
    reason_code: str | None = None


@runtime_checkable
class CapabilityLedgerAggregatePort(Protocol):
    def prepare(self, request: ProviderDispatchRequest) -> LedgerPrepareOutcome: ...
    def commit_result(self, outcome: LedgerPrepareOutcome, result: ProviderDispatchResult) -> ProviderDispatchResult: ...
    def record_failure(self, outcome: LedgerPrepareOutcome, reason_code: str) -> None: ...
```

- [ ] **Step 4: Make enforced dispatch non-optional**

Remove `dispatch_disposition=None` from `LedgerDispatchRequest`. `LedgerDispatcher` accepts `inner` and `aggregate`; it calls `inner` only for `kind="dispatch"`. `deny`, `pause`, and `replay` never reach the gateway. Delete independent approval/result commits from the dispatcher.

- [ ] **Step 5: Compose from frozen Run mode**

Extend `compose_main_agent_policy_runtime` with `capability_ledger_mode="legacy_read_only"` and `capability_ledger=None`. Wrap the gateway dispatcher only for enforced mode and fail construction if its aggregate is missing. `MainAgentRunExecutor` supplies the frozen Run mode and claimed-lease aggregate on fresh and resume paths.

- [ ] **Step 6: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_production_wiring.py backend/tests/test_capability_call_dispatcher.py backend/tests/test_provider_agent_loop.py -q
git add backend/app/assistant/provider_loop/contracts.py backend/app/assistant/capability_calls/dispatcher.py backend/app/assistant/main_agent/policy_runtime.py backend/app/assistant/durable/runner.py backend/tests/test_capability_call_production_wiring.py backend/tests/test_capability_call_dispatcher.py
git commit -m "fix(ai): wire enforced capability ledger fail closed"
```

---

### Task 2: Derive v2 admission and approval eligibility from frozen server state

**Files:**
- Create: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/policy/write_admission.py`
- Modify: `backend/app/assistant/main_agent/policy_runtime.py`
- Modify: `backend/tests/test_capability_call_write_admission.py`
- Modify: `backend/tests/test_capability_call_production_wiring.py`

**Interfaces:**
- Consumes Task 1 aggregate port.
- Produces `DurableCapabilityLedgerAggregate.prepare()` and exact approved-call evidence.

- [ ] **Step 1: Write failing bypass tests**

```python
def test_write_cannot_supply_dispatch_itself(aggregate, inner):
    outcome = aggregate.prepare(golden_request(caller_disposition="dispatch"))
    assert outcome.kind == "pause"
    assert inner.dispatch_calls == []


@pytest.mark.parametrize("mutate", APPROVAL_BINDING_MUTATIONS)
def test_approval_binding_drift_fails_closed(aggregate, approved_request, mutate):
    with pytest.raises(CapabilityCallConflict, match="approval_binding_mismatch"):
        aggregate.prepare(mutate(approved_request))
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_write_admission.py backend/tests/test_capability_call_production_wiring.py -q
```

- [ ] **Step 3: Implement server-derived admission**

The aggregate loads frozen Run, Manifest/policy revisions, Principal, owner/version, descriptor, and canonical input Artifact. It calls `evaluate_authorization_v2`; no caller field selects disposition. Map `deny`, `awaiting_call_approval`, and `dispatch` to typed aggregate outcomes.

- [ ] **Step 4: Validate post-approval evidence**

Before `write_local`, require a terminal-approved call-owned Interrupt matching Run, logical key, Principal, owner/version, target/version, descriptor, authorization, canonical input, request revision, and binding digest. Call `issue_post_approval_gateway_evidence` with the original frozen decision/grant.

- [ ] **Step 5: Verify policy compatibility and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_policy_matrix.py backend/tests/test_agent_policy_evidence.py backend/tests/test_agent_policy_runtime.py backend/tests/test_capability_call_write_admission.py backend/tests/test_capability_call_production_wiring.py -q
git add backend/app/assistant/capability_calls/aggregate.py backend/app/assistant/policy/write_admission.py backend/app/assistant/main_agent/policy_runtime.py backend/tests/test_capability_call_write_admission.py backend/tests/test_capability_call_production_wiring.py
git commit -m "fix(ai): derive ledger admission from frozen policy"
```

---

### Task 3: Commit call-owned approval through one Plan 07 waiting CAS

**Files:**
- Modify: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/capability_calls/approval.py`
- Modify: `backend/app/assistant/durable/repository.py`
- Modify: `backend/app/assistant/durable/checkpoints.py`
- Modify: `backend/app/assistant/workflow/durable/interrupts.py`
- Modify: `backend/app/assistant/workflow/durable/interrupt_api.py`
- Create: `backend/tests/test_capability_call_pause_postgres.py`
- Modify: `backend/tests/test_durable_interrupt_api.py`

**Interfaces:**
- Produces `CapabilityCallWaitingBundle` appended by `commit_waiting_pause`.
- Produces idempotent call-owned approval/reject/expire/cancel resolution.

- [ ] **Step 1: Write failing crash/race tests**

```python
def test_crash_before_waiting_cas_leaves_no_orphan_layers(pause_runtime):
    with injected_crash("before_waiting_commit"), pytest.raises(InjectedCrash):
        pause_runtime.run_once()
    assert pause_runtime.counts() == (0, 0, 0, 0, 0)


def test_duplicate_approval_creates_one_resume(waiting_call):
    results = waiting_call.race_two_approvals()
    assert sorted(x.kind for x in results) == ["applied", "replayed"]
    assert waiting_call.resume_checkpoint_count == 1
    assert waiting_call.attempt_count == 0
```

- [ ] **Step 2: Verify RED on PostgreSQL**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_capability_call_pause_postgres.py backend/tests/test_durable_interrupt_api.py -q
```

Expected: the current dispatcher persists `awaiting_approval` before the outer CAS.

- [ ] **Step 3: Make pause staging pure**

The aggregate returns a frozen proposed-call spec, approval binding/card, obligation, suspension input, Artifact payload, and continuation. It performs no flush/commit or call transition.

- [ ] **Step 4: Extend the waiting child bundle**

Add `CapabilityCallWaitingBundle(call, interrupt, approval_artifact, obligation_revision, budget_revision, checkpoint)` to `DurableChildBundle`. Append it only inside `DurableRunRepository._append_children()` after the Run lock, validating call-owned XOR, ownership, revisions, and logical pause uniqueness.

- [ ] **Step 5: Resolve through Plan 07 ordering**

In `resolve_interrupt_http`, branch on `interrupt_origin`. Keep resolution-id lookup first, then lock Run, Interrupt, and call; verify binding; atomically authorize or terminalize the call, resolve suspension/obligation, and append exactly one resume Checkpoint. Non-approved outcomes append a typed Tool Result and no Attempt.

- [ ] **Step 6: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_approval.py backend/tests/test_durable_interrupt_api.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_capability_call_pause_postgres.py backend/tests/test_durable_interrupt_repository_postgres.py -q
git add backend/app/assistant/capability_calls/aggregate.py backend/app/assistant/capability_calls/approval.py backend/app/assistant/durable/repository.py backend/app/assistant/durable/checkpoints.py backend/app/assistant/workflow/durable/interrupts.py backend/app/assistant/workflow/durable/interrupt_api.py backend/tests/test_capability_call_pause_postgres.py backend/tests/test_durable_interrupt_api.py
git commit -m "fix(ai): commit call approval through one waiting CAS"
```

---

### Task 4: Persist complete results and replay without redispatch

**Files:**
- Create: `backend/app/assistant/capability_calls/result_codec.py`
- Modify: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/capability_calls/repository.py`
- Modify: `backend/app/assistant/durable/checkpoints.py`
- Modify: `backend/app/assistant/durable/codec.py`
- Modify: `backend/app/assistant/provider_loop/loop.py`
- Create: `backend/tests/test_capability_call_result_replay.py`

**Interfaces:**
- Produces bounded `CapabilityResultArtifactV1` and Checkpoint schema v3 capability-call state.
- Produces Attempt progression `claimed -> dispatched -> response_received -> committed`.

- [ ] **Step 1: Write failing replay tests**

```python
def test_succeeded_replay_returns_result_without_gateway(runtime):
    first = runtime.invoke_read_then_lose_response()
    replay = runtime.invoke_same_logical_call()
    assert replay == first
    assert runtime.gateway_invocations == 1
    assert runtime.attempt_statuses == ["committed"]
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_result_replay.py backend/tests/test_capability_call_repository.py -q
```

Expected: replay returns `None`, Attempt remains `claimed`, and no output Artifact exists.

- [ ] **Step 3: Implement result codec and Attempt CAS**

Encode `ProviderDispatchResult`, next Manifest identity, schema version, and digest using canonical JSON; limit inline bytes to 262144. Decode validates call, binding/descriptor, Manifest lineage, version, and digest. Add repository `transition_attempt(attempt_id, expected_status, to_status, request_digest, response_digest, ended_at)` without committing.

- [ ] **Step 4: Commit the complete aggregate**

`commit_result()` stages result Artifact, Tool Result, checkpoint-v3, budget/obligation revisions, Attempt committed, call succeeded/output reference, events, and Run revision in one transaction. Failed results use the same aggregate shape. Terminal prepare loads and returns the stored result; corruption fails closed without redispatch.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_result_replay.py backend/tests/test_capability_call_repository.py backend/tests/test_provider_agent_loop.py backend/tests/test_durable_checkpoint_codec.py -q
git add backend/app/assistant/capability_calls/result_codec.py backend/app/assistant/capability_calls/aggregate.py backend/app/assistant/capability_calls/repository.py backend/app/assistant/durable/checkpoints.py backend/app/assistant/durable/codec.py backend/app/assistant/provider_loop/loop.py backend/tests/test_capability_call_result_replay.py
git commit -m "fix(ai): persist and replay capability results durably"
```

---

### Task 5: Join the golden Entry write to the durable result transaction

**Files:**
- Modify: `backend/app/assistant/capability_calls/local_write.py`
- Modify: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/main_agent/policy_runtime.py`
- Modify: `backend/app/entry/service.py`
- Modify: `backend/tests/test_capability_call_local_transaction.py`
- Modify: `backend/tests/test_main_agent_golden_create_entry.py`

**Interfaces:**
- Produces `stage_create_entry_local(session, request, call_id) -> Entry`; it never owns a transaction.

- [ ] **Step 1: Write failing atomic-set tests**

```python
@pytest.mark.parametrize("point", ["after_entry", "after_outbox", "after_artifact", "after_call_success", "before_commit"])
def test_crash_rolls_back_whole_golden_set(golden_runtime, point):
    with injected_crash(point), pytest.raises(InjectedCrash):
        golden_runtime.invoke_approved_create()
    assert golden_runtime.persisted_counts() == COMPLETE_ZERO_SET
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_local_transaction.py backend/tests/test_main_agent_golden_create_entry.py -q
```

- [ ] **Step 3: Remove transaction ownership from the adapter**

Replace the current helper with:

```python
def stage_create_entry_local(*, session: Session, request: EntryRequest, call_id: UUID) -> Entry:
    return EntryService(session).create_in_uow(request, source_capability_call_id=call_id)
```

It must not create a UoW, commit, refresh, transition a call, or open a Session.

- [ ] **Step 4: Route only the exact approved binding**

The aggregate intercepts only the frozen golden `create_entry` binding after approved evidence, stages Entry/outbox, builds the provider result, and commits it with Task 4 rows. Unique conflicts reload and compare canonical input/output digests; mismatch raises `local_write_idempotency_corruption`.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_entry_service.py backend/tests/test_entry_tools.py backend/tests/test_capability_call_local_transaction.py backend/tests/test_main_agent_golden_create_entry.py -q
git add backend/app/assistant/capability_calls/local_write.py backend/app/assistant/capability_calls/aggregate.py backend/app/assistant/main_agent/policy_runtime.py backend/app/entry/service.py backend/tests/test_capability_call_local_transaction.py backend/tests/test_main_agent_golden_create_entry.py
git commit -m "fix(ai): commit golden entry with durable call result"
```

---

### Task 6: Make cancellation settlement call-aware and CAS-only

**Files:**
- Modify: `backend/app/assistant/capability_calls/settlement.py`
- Modify: `backend/app/assistant/capability_calls/repository.py`
- Modify: `backend/app/assistant/durable/repository.py`
- Modify: `backend/app/assistant/durable/runner.py`
- Create: `backend/tests/test_capability_call_cancellation_postgres.py`

**Interfaces:**
- Produces repository settlement using expected Run/call/Attempt revisions.
- Makes `finalize_cancellation()` refuse unproven started calls inside its lock.

- [ ] **Step 1: Write failing PostgreSQL races**

```python
def test_finalizer_refuses_unproven_started_call(pg_runtime):
    pg_runtime.make_external_call_unproven()
    with pytest.raises(DurableRunConflict, match="unproven_started_call"):
        pg_runtime.finalize_cancellation()
    assert pg_runtime.run_status == "cancelling"
```

- [ ] **Step 2: Verify RED**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_capability_call_cancellation_postgres.py -q
```

- [ ] **Step 3: Replace direct mutation and import side effects**

Move captured-outcome transitions to repository methods requiring expected revisions. `settlement.py` only orchestrates them; it never assigns `.status` or `.state_revision`. Register `cancelling -> needs_reconciliation` in durable transition constants, not on settlement import.

- [ ] **Step 4: Guard finalization in the Run transaction**

After locking the Run, query started unproven calls and raise stable `unproven_started_call` before events/status changes. Prove stop/local success yields either zero complete set or one complete succeeded set.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_crash_matrix.py backend/tests/test_capability_call_fault_matrix.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_capability_call_cancellation_postgres.py backend/tests/test_durable_run_events_postgres.py -q
git add backend/app/assistant/capability_calls/settlement.py backend/app/assistant/capability_calls/repository.py backend/app/assistant/durable/repository.py backend/app/assistant/durable/runner.py backend/tests/test_capability_call_cancellation_postgres.py
git commit -m "fix(ai): settle cancelling capability calls through CAS"
```

---

### Task 7: Make reconciliation executable and rollout default-off

**Files:**
- Modify: `backend/app/assistant/capability_calls/cli.py`
- Modify: `backend/app/assistant/capability_calls/reconciliation.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `docs/operations/assistant-capability-reconciliation.md`
- Create: `backend/tests/test_capability_call_cli.py`

**Interfaces:**
- Produces `main(argv, session_factory=SessionLocal) -> int` with safe JSON and stable exits.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_cli_inspect_uses_session_and_redacts(cli_db, capsys):
    code = main(["inspect", "--call-id", str(cli_db.call_id)], session_factory=cli_db.factory)
    body = json.loads(capsys.readouterr().out)
    assert code == 0
    assert body["callId"] == str(cli_db.call_id)
    assert "input" not in body and "idempotencyKey" not in body
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_cli.py -q
```

Expected: current CLI always exits 2.

- [ ] **Step 3: Wire Session/service and validation**

Default `session_factory` to `SessionLocal`. `inspect` returns bounded metadata. `decide` requires actor admin ID, expected revisions, reason, evidence references, and resolution request ID; commit on success, rollback on error, and never print raw inputs, credentials, provider payloads, secrets, or idempotency keys.

- [ ] **Step 4: Add explicit Compose defaults and compatibility checks**

Compose supplies ledger `legacy_read_only`, write `off`, empty secret/cohort. Golden configuration additionally requires enforced ledger, strong secret, durable interrupts, compatible checkpoint worker, and importable CLI reconciliation path.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_cli.py backend/tests/test_capability_call_reconciliation.py backend/tests/test_config.py -q
git add backend/app/assistant/capability_calls/cli.py backend/app/assistant/capability_calls/reconciliation.py backend/app/config.py backend/.env.example deploy/.env.example deploy/docker-compose.yml docs/operations/assistant-capability-reconciliation.md backend/tests/test_capability_call_cli.py
git commit -m "fix(ai): make capability reconciliation operable"
```

---

### Task 8: Persist sibling order and complete Tool pairing

**Files:**
- Modify: `backend/app/assistant/provider_loop/loop.py`
- Modify: `backend/app/assistant/provider_loop/scheduler.py`
- Modify: `backend/app/assistant/durable/codec.py`
- Modify: `backend/tests/test_provider_agent_loop.py`
- Modify: `backend/tests/test_provider_multi_tool_calls.py`
- Modify: `backend/tests/test_durable_checkpoint_codec.py`

**Interfaces:**
- Produces Checkpoint v3 `capabilityCalls` entries containing call ID, provider order, status, Attempt/output IDs, and pause identity.

- [ ] **Step 1: Write failing multi-call tests**

```python
def test_waiting_write_blocks_later_write_and_preserves_order(runtime):
    result = runtime.run([READ_A, GOLDEN_WRITE, WRITE_B])
    assert result.status == "waiting"
    assert runtime.dispatched == [READ_A]
    assert runtime.call_status(WRITE_B) == "proposed"
    assert runtime.persisted_order == [READ_A, GOLDEN_WRITE, WRITE_B]
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_agent_loop.py backend/tests/test_durable_checkpoint_codec.py -q
```

- [ ] **Step 3: Reserve before scheduling and restore from v3**

Prepare valid siblings in Provider order. Parallelize only isolated declared-safe reads/computes; serialize write/external calls and stop at the first pause. Resume validates transcript/order and replays terminal calls so every Tool Call has one Tool Result before the next Provider turn. Preserve v1/v2 readers byte-for-byte.

- [ ] **Step 4: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_agent_loop.py backend/tests/test_provider_multi_tool_calls.py backend/tests/test_capability_call_result_replay.py backend/tests/test_durable_checkpoint_codec.py -q
git add backend/app/assistant/provider_loop/loop.py backend/app/assistant/provider_loop/scheduler.py backend/app/assistant/durable/codec.py backend/tests/test_provider_agent_loop.py backend/tests/test_provider_multi_tool_calls.py backend/tests/test_durable_checkpoint_codec.py
git commit -m "fix(ai): persist and resume capability call siblings"
```

---

### Task 9: Repair migration gates and PR-introduced regressions

**Files:**
- Modify: `backend/tests/test_provider_model_probe_postgres.py`
- Modify: `backend/tests/test_main_agent_postgres_migration.py`
- Modify: `backend/tests/test_system_defaults_loader.py`
- Modify: `docs/superpowers/evidence/plan-08-task9-verification.md`

- [ ] **Step 1: Add exact Plan 08 migration state**

Define `PLAN08_HEAD = "984c07876856"`, include it in known descendants, deliberately satisfy its downgrade guard before resetting older tests, and assert Plan 08 after `upgrade head`. Keep assertions about older guarded boundaries semantically scoped to those revisions.

- [ ] **Step 2: Replace raw asset count with exact keys**

Assert the expected workflow behavior-key set includes `smart-capture-golden-create`; do not merely change `8` to `9`.

- [ ] **Step 3: Verify PostgreSQL migration gate**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_postgres_migration.py backend/tests/test_provider_model_probe_postgres.py backend/tests/test_main_agent_postgres_migration.py -q
backend/.venv/bin/python -m pytest backend/tests/test_system_defaults_loader.py backend/tests/test_main_agent_golden_create_entry.py -q
git diff --check
```

Expected: migration selection passes at sole Plan 08 head; asset tests pass; no whitespace errors.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_provider_model_probe_postgres.py backend/tests/test_main_agent_postgres_migration.py backend/tests/test_system_defaults_loader.py docs/superpowers/evidence/plan-08-task9-verification.md
git commit -m "test(ai): repair Plan 08 migration and asset gates"
```

---

### Task 10: Run complete verification and rewrite handoff evidence

**Files:**
- Modify: `docs/superpowers/evidence/plan-08-task9-verification.md`
- Modify owning files only for failures proven to be PR #55 regressions.

- [ ] **Step 1: Run focused and prerequisite suites**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_call_fault_matrix.py backend/tests/test_capability_call_idempotency.py backend/tests/test_capability_call_repository.py backend/tests/test_capability_call_dispatcher.py backend/tests/test_capability_call_production_wiring.py backend/tests/test_capability_call_approval.py backend/tests/test_capability_call_local_transaction.py backend/tests/test_capability_call_result_replay.py backend/tests/test_capability_call_write_admission.py backend/tests/test_capability_call_reconciliation.py backend/tests/test_capability_call_cli.py backend/tests/test_capability_call_external_uncertainty.py backend/tests/test_main_agent_golden_create_entry.py -q
backend/.venv/bin/python -m pytest backend/tests/test_agent_policy_matrix.py backend/tests/test_agent_policy_evidence.py backend/tests/test_agent_policy_runtime.py backend/tests/test_durable_interrupt_api.py backend/tests/test_durable_crash_matrix.py backend/tests/test_provider_agent_loop.py backend/tests/test_entry_service.py -q
```

Expected: zero failures and no placeholder skip for non-PostgreSQL behavior.

- [ ] **Step 2: Run PostgreSQL races/migrations three times**

Run the pause, cancellation, interrupt repository, and three migration test files three consecutive times against PostgreSQL 15. Each run must exit 0 and produce the same legal outcome counts; do not use a placeholder assertion.

- [ ] **Step 3: Run full backend/frontend/config verification**

```bash
cd backend && .venv/bin/python -m pytest -q && .venv/bin/python -m pip check && .venv/bin/alembic heads && cd ..
npm --prefix frontend test
npm --prefix frontend run build
docker compose -f deploy/docker-compose.yml config
git diff --check
```

Expected: all exit 0, one Alembic head `984c07876856`, and rendered defaults keep ledger/write disabled.

- [ ] **Step 4: Execute process-level E2E**

On disposable PostgreSQL/MinIO with a scripted Provider: prove default-off admission; then enable a test enforced/golden cohort, pause, restart, approve, kill after local commit, restart, and observe one Entry/outbox/call/result/terminal response. Run reject, cancel, expire, duplicate decision, full smart-capture, update, and relation negatives with zero business writes.

- [ ] **Step 5: Rewrite evidence from observed output**

Record commands, versions, counts, race iterations, approved/negative paths, CLI proof, default-off proof, and independently reproduced `origin/main` failures. Remove every deferred/partial checkbox. Mark Plan 09 ready only if every original Plan 08 handoff condition passes.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/evidence/plan-08-task9-verification.md
git commit -m "test(ai): complete Plan 08 verification evidence"
```

---

## Plan self-review

- Production wiring/fail-open maps to Tasks 1–2.
- One-CAS approval maps to Task 3.
- output Artifact, Attempt lifecycle, Tool pairing, and replay map to Task 4.
- atomic golden business/result transaction maps to Task 5.
- call-aware cancellation and CAS settlement map to Task 6.
- executable reconciliation and default-off deployment map to Task 7.
- sibling/checkpoint state maps to Task 8.
- migration/asset regressions map to Task 9.
- real PostgreSQL/process evidence and honest Plan 09 handoff map to Task 10.
- No broad write exposure or HTTP reconciliation mutation is introduced.
