# Plan 08 / PR #55 Completion Design

## Objective

Complete the original Plan 08 exit criteria on PR #55 rather than relabeling the
current work as a foundation-only change. The completed branch must place the
capability-call ledger on the production Main Agent path, preserve Plan 05–07
contracts, make the single golden local write crash-safe and replayable, provide
an executable reconciliation path, and pass the Plan 08 PostgreSQL and migration
gates while all release flags remain off by default.

This remediation does not broaden write exposure. Only the exact frozen
`smart-capture-golden-create -> create_entry` binding may reach call-owned
approval and local execution. Existing full smart-capture, update, delete,
merge, relation, HTTP, dynamic, and external-write paths remain denied.

## Confirmed Baseline

The existing PR provides useful schema, contracts, state-machine helpers,
repository operations, policy-v2 helpers, a local-write seam, reconciliation
logic, a hidden golden asset, and focused unit tests. Those pieces are retained
where their behavior matches Plan 08.

The production Main Agent composition still installs
`MainAgentGatewayToolDispatcher` directly. `LedgerDispatcher`,
`evaluate_authorization_v2`, post-approval evidence issuance, and
`create_entry_local_transactional` have no production caller. The approval
branch persists `awaiting_approval` before the outer durable waiting CAS,
terminal replay returns no stored provider result, normal success does not
commit the complete durable aggregate, cancellation finalization is not
call-aware, and the reconciliation CLI cannot open an application session.

The focused suite passing is therefore evidence for isolated helpers, not for
the Plan 08 production data flow. The PostgreSQL migration gate also fails
because prior migration tests do not recognize revision `984c07876856`.

## Architecture

### Production dispatch ownership

`compose_main_agent_policy_runtime` will continue to build the compatibility
gateway dispatcher, then wrap it with a production ledger dispatcher when the
Run's frozen `capability_ledger_mode` is `enforced`. Legacy-read-only Runs keep
the compatibility dispatcher unchanged.

The wrapper must conform to the existing Provider Loop dispatcher port. It will
derive the ledger request from the frozen Run/Manifest/policy context and the
`ProviderDispatchRequest`; callers will not be able to omit a disposition or
inject mutable authorization fields. The only execution ownership chain is:

```text
Provider Loop
  -> production ledger dispatcher
  -> frozen policy-v2 admission
  -> durable call/attempt aggregate
  -> compatibility gateway dispatcher
  -> Capability Gateway
  -> adapter
```

Provider Loop and Workflow code must not invoke business adapters directly.
The Capability Gateway remains unaware of ledger repositories.

### Fail-closed authorization

An enforced call has exactly three admitted outcomes derived by the server:
`deny`, `dispatch`, or `awaiting_call_approval`. There is no optional/default
disposition on the enforced path.

Read/compute calls may dispatch only after proposal, immutable identity
verification, frozen policy decision, budget reservation, pre-dispatch durable
state, and Attempt claim. A `write_local` call may never interpret `dispatch`
alone as approval. It additionally requires a terminal-approved call-owned
Interrupt whose binding covers Run, logical call key, Principal, owner and
owner version, target and target version, descriptor, authorization, canonical
input, approval request revision, and binding digest. Gateway evidence is
issued from the original frozen grant plus that exact approval; approval cannot
mint or widen a grant.

### Call-owned waiting transaction

The dispatcher stages a pure `CapabilityCallPauseProposalV1`; it does not
transition the call to `awaiting_approval` by itself. The outer durable worker
extends the Plan 07 waiting commit and, under Run-first locking and one expected
Run revision, atomically persists:

- the call proposal and `awaiting_approval` transition;
- the call-owned Interrupt and approval binding;
- the pending obligation and versioned budget suspension;
- the safe inline approval evidence Artifact;
- the Provider waiting continuation and versioned Checkpoint;
- the Run waiting state and ordered private/public events.

A crash before this commit leaves none of those layers pending. Duplicate pause
or resolution requests return the already-persisted aggregate. Approval
resolution uses Plan 07's idempotency-first ordering and atomically authorizes
the same call, resolves the obligation/suspension, and creates one resume
Checkpoint. Reject, expire, and cancel produce one typed Tool Result without an
Attempt.

### Durable result and replay

The ledger aggregate owns Attempt progression
`claimed -> dispatched -> response_received -> committed`. Successful
read/compute calls store a bounded result Artifact and bind it to the call.
Checkpoint, Tool Result pairing, budget/obligation revisions, call and Attempt
terminal state, events, and Run revision are committed through one aggregate
CAS boundary.

Recovery of a succeeded call reconstructs the exact
`ProviderDispatchResult` from frozen metadata and its stored result Artifact;
it never returns `None` and never invokes the Gateway again. Digest or schema
mismatch fails closed into a stable integrity/reconciliation outcome.

### Atomic golden local write

The golden adapter uses the caller-owned SQLAlchemy Session and only
`EntryService.create_in_uow`. One PostgreSQL transaction commits:

- tags, Entry, and the existing indexing outbox;
- the unique `source_capability_call_id` binding;
- the inline result Artifact and output reference;
- Attempt `committed` and call `succeeded` with effect timestamp;
- Provider Tool Result/Checkpoint, budget/obligation revisions, events, and Run
  revision.

The adapter performs no commit, rollback, Session creation, MinIO, vector, or
network work below the unit-of-work boundary. Stop-first rolls the transaction
back completely; success-first leaves one complete replayable result. A unique
conflict for the same call reloads and verifies the stored Entry/result rather
than dispatching again.

### Cancellation and reconciliation

`DurableRunRepository.finalize_cancellation` will consult the call-aware
settlement guard inside its locking transaction. A started, nonterminal, or
unproven call prevents false `cancelled`. Captured trusted evidence may be
settled without new I/O; uncertain external outcomes move the call and Run to
`needs_reconciliation`.

Settlement operations use repository transition/CAS methods rather than direct
status assignment. The reconciliation CLI will create an application Session
from the configured database URL and use local shell access as the deployment
authorization boundary; every mutation additionally requires an explicit
`actor_admin_id`, expected call/Run revisions, reason, evidence references, and
resolution request ID. It invokes `CapabilityReconciliationService`, emits only
bounded safe JSON, and returns stable exit codes. HTTP mutation routes remain
unmounted. Owner-safe HTTP reads are optional because they are not required to
make the audited operator path usable.

## Checkpoint and sibling behavior

The durable checkpoint schema will gain a versioned capability-call section
containing logical call IDs, Attempt/replay state, sibling provider order,
waiting proposal/resolution identity, and policy contract version. Readers for
Plan 06/07 schemas remain lossless.

All valid sibling calls are proposed in provider order. Eligible read/compute
siblings may run in parallel only through isolated sessions. Write/external
siblings remain serialized, and a waiting write prevents later writes from
starting. Every Provider Tool Call receives exactly one replayable Tool Result
before the next Provider turn.

## Configuration and rollout

Run creation freezes ledger mode and the policy/release version. Deployment
examples and Compose explicitly carry the Plan 08 variables with defaults of
`legacy_read_only` and `off`. Golden mode requires enforced ledger mode, a
secret of at least 32 UTF-8 bytes, compatible durable worker/checkpoint support,
and an available CLI reconciliation path. Existing Runs never change mode when
configuration changes.

The branch remains default-off after merge. No production golden activation is
part of this remediation.

## Testing and evidence

Implementation follows red-green cycles. New integration tests must fail on the
current branch before production code changes. Required evidence includes:

1. Production composition proves enforced Runs use the ledger wrapper and
   legacy Runs do not.
2. Missing/forged disposition and missing/mismatched approval fail before
   Gateway or adapter invocation.
3. Crash before the waiting CAS leaves no orphan call, Interrupt, obligation,
   suspension, or Checkpoint; duplicate decisions produce one resume.
4. Succeeded read/compute replay returns the stored Tool Result without another
   Gateway call.
5. Approval through restart to golden create produces one Entry, one outbox,
   one committed Attempt, one result Artifact, one Tool Result, and one terminal
   response.
6. PostgreSQL two-session races cover stop/local success, duplicate approval,
   late settlement, cancellation-finalizer refusal, and reconciliation CAS.
7. Migration tests support Plan 08 head and execute parent -> head -> parent ->
   head with guarded downgrade behavior.
8. Full-asset/update/relation/non-cohort/reject/cancel/expire/drift paths produce
   no business write.
9. The CLI inspect/decision paths execute against a disposable database and
   redact raw input, credentials, provider payloads, and secrets.
10. `git diff --check`, the Plan 08 focused suites, related Plan 05–07 suites,
    full backend tests with required services, frontend tests/build, Alembic
    sole-head check, and a default-off API/worker smoke are recorded afresh.

Failures already present on `origin/main` are documented separately. PR #55
must fix failures it introduces, including stale migration-head assumptions and
the system-asset registry count. Unrelated baseline failures are not silently
rewritten as Plan 08 product changes, but the required-service CI environment
must be used to determine whether they are genuine baseline failures.

## Completion criteria

PR #55 is complete only when every original Plan 08 exit criterion is mapped to
fresh automated or process-level evidence. The Task 9 handoff must not mark
partial/deferred rows complete, and Plan 09 must not start until PostgreSQL
races, migration cycle, approved golden E2E, negative write paths, executable
operator reconciliation, and evaluation-isolation evidence all pass.
