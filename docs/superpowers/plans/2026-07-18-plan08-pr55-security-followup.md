# Plan 08 PR #55 Security and PostgreSQL Follow-up

> **Scope:** Close the independently reproduced Plan 08 gaps reported after commit
> `96300ae`, without widening the golden-write release or enabling external writes.

## Task 1: Make the Attempt lifecycle executable and constrained on PostgreSQL

- Add a PostgreSQL integration test that migrates to head and exercises a real
  Attempt through `claimed -> dispatched -> response_received -> committed`.
- Replace the unconditional Attempt UPDATE rejection with a trigger that rejects
  DELETE, identity rewrites, evidence rewrites, counter changes, and illegal or
  regressive status transitions while allowing the repository-owned monotonic
  lifecycle updates.
- Keep direct-SQL negative tests for immutable fields and illegal transitions.
- Add the execution test to the PostgreSQL CI gate.

## Task 2: Require trusted captured Attempt evidence for cancellation settlement

- Add failing tests for unknown Attempt IDs, cross-call Attempts, mismatched
  evidence digests, uncaptured success, and missing/cross-Run result Artifacts.
- Define one canonical server-side Attempt evidence digest over persisted Attempt
  and result evidence.
- Lock Run, then Call, then Attempt; validate ownership, captured state, response
  digest, result Artifact integrity, and outcome-specific requirements before
  changing Call or Run state.
- Update fault-matrix fixtures to use real captured evidence.

## Task 3: Harden reconciliation locks, authorization, and evidence

- Add tests proving Run-before-Call locking, denial without a trusted operator,
  rejection of missing/cross-Run/malformed evidence, and derivation of
  `not_accepted` from a typed status-lookup Artifact rather than a request boolean.
- Inject a trusted operator authorizer into the reconciliation service. Remove
  caller-supplied actor IDs and status-proof booleans from the mutation contract.
- Configure the guarded CLI with a server-owned operator identity and explicit
  enablement; remove the corresponding self-assertion flags.
- Validate decision-specific Artifact types and bindings before the CAS, then
  persist the verified authorization/evidence summary.

## Task 4: Persist denied enforced calls

- Add aggregate tests asserting a denied Provider call has a canonical input
  Artifact, a `proposed -> denied` ledger row, a durable checkpoint entry, and no
  Attempt or adapter invocation.
- Create-or-verify every sibling proposal before disposition branching and
  transition denied calls through the repository under the Run CAS.
- Make `prepare()` replay the persisted denial deterministically.

## Task 5: Close release, rollback, CI, and evidence gates

- Extend worker capability advertisement/admission so an enforced/golden Run
  requires a fresh worker advertising the Plan 08 ledger contract.
- Require the approved reconciliation operator path before golden admission.
- Add rollback coverage showing new enforced/golden admission stops while workers
  compatible with existing enforced/waiting/reconciliation Runs remain claimable.
- Fix verification-document whitespace and replace nonexistent test references.
- Triage current Backend CI failures, fix Plan08 regressions, and record unrelated
  baseline failures explicitly rather than claiming a green gate.

## Task 6: Verification and delivery

- Run focused SQLite and PostgreSQL tests, migration upgrade/downgrade/upgrade,
  `git diff --check`, and the feasible backend/frontend gates.
- Run the required process-level API/worker kill-and-restart smoke with a scripted
  provider, or leave Plan09 explicitly blocked with the exact missing evidence.
- Review the final diff, commit only scoped changes, and push to the existing PR
  #55 head branch.
