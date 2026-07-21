# Plan 09 / PR #56 Completion Remediation Design

## Objective

Complete the original Plan 09 contract on PR #56 instead of relabeling the
current implementation as a foundation-only change. The remediated branch must
provide a trustworthy and reproducible evaluation-to-promotion chain, a usable
Universal Skill administration experience, independently deployable Plan 09
slices, and fresh verification evidence before it may claim Plan 09
code-complete.

The target baseline for this remediation is PR #56 commit
`ccacc14749af53fb62e1bacd9c25739464b471c9` on Plan 08 base
`cb5dac353408021fffeb5e3902acd2fc317b91de`. Implementation must begin by
refreshing those pins if the PR head changes.

This remediation preserves the original authorization boundary. It does not
invent or ship a project-wide assistant-config authentication/RBAC foundation.
All Plan 09 routes must use one protected parent boundary and remain absent
from staging and production OpenAPI while a real verified principal dependency
is unavailable. Therefore Plan 09 may become code-complete, but M4 remains not
release-complete and Plan 10 production cutover remains blocked until the real
principal/operator guard exists.

## Confirmed Defects

The remediation treats the following findings as reproduced defects rather
than optional follow-up:

1. `dataset_scripted` derives actual execution fields from expected dataset
   fields, hard-codes completion and zero safety/production counters, and may
   persist gate-eligible evidence without running Main Agent orchestration.
2. The Workbench sends an empty dataset list for dataset modes, has no dataset
   version selector, and exposes an unsupported `dataset_live` mode.
3. The frontend constructs publish-gate closures with empty dataset pins,
   incorrect threshold pins, client-owned environment placeholders, and no
   authoritative server closure.
4. One draft gate is reused for publish and catalog enable even though those
   actions require different subject kinds, version IDs, and fresh evidence.
5. Eval admission falls back from an unresolved draft binding digest to the
   content digest, while publish resolves and seals a distinct binding-set
   digest.
6. Profile draft saves force `runtime_enabled=False`, coupling content editing
   to an implicit live-state transition.
7. The new Profile version-detail read is registered on an always-mounted
   legacy router without the Plan 09 principal/mount boundary.
8. Alias soft-disable trigger support is placed after the evaluation migration,
   so the 09A lifecycle slice is not independently usable or reversible.
9. Draft save CAS and request idempotency can be omitted by API clients.
10. Import preview/apply state is process-local and cannot survive worker
    routing, restart, or rolling deployment.
11. Resource editing, Registry-backed capability selection, Profile lifecycle,
    Workbench inputs/results, SSE replay, and route-level feature/principal
    gating do not meet the original Task 6/7 contract.
12. Task 9 evidence is pinned to stale commits, migration heads, test counts,
    and a false clean `git diff --check` result.

## Delivery Strategy

Implement the remediation as five independently reviewable vertical layers.
Each layer must leave the branch fail-closed and must have its own red-green
test cycle.

1. **Integrity foundation:** repair route ownership, migration slice
   independence, mandatory CAS/idempotency, Profile live-state semantics, and
   shared import-preview persistence.
2. **Real evaluation kernel:** execute the real Main Agent contracts with a
   deterministic scripted Provider and isolated adapters; synthetic structural
   evaluation is permanently promotion-ineligible.
3. **Authoritative gate lifecycle:** share candidate closure resolution between
   evaluation and publish, build closures on the server, and separate publish
   gates from promotion gates.
4. **Complete administration experience:** finish the Universal Skill,
   Profile, dataset, evidence, resource, Registry, SSE, and fail-closed route
   flows.
5. **End-to-end proof:** exercise the complete lifecycle with real process and
   PostgreSQL boundaries, then regenerate Task 9 and Plan 10 handoff evidence.

Backend-only-first and blocker-then-rewrite approaches are rejected because
they leave frontend/backend contracts divergent for too long or create
temporary gate/evaluation contracts that later work would need to preserve.

## Candidate Closure Ownership

### Shared resolver

Introduce one server-owned candidate closure resolver used by both Eval Run
admission and publish. Conceptually it exposes:

```python
resolve_skill_candidate_closure(
    session: Session,
    *,
    package_id: UUID,
    version_id: UUID,
    subject_kind: Literal["skill_draft", "skill_version"],
) -> SkillCandidateClosure
```

`SkillCandidateClosure` contains the immutable content digest, resolved and
ordered binding snapshot, binding-set digest, version digest, capability target
version/config evidence, and exact subject identity. The resolver reads the
stored immutable version bytes, parses declarations with the Plan 01 parser,
uses the existing capability resolver, applies the same durable binding rules
as publish, and hashes the result with the existing canonical digest factories.

The resolver is pure with respect to publication: it does not insert a publish
version, advance pointers, enable catalog state, or commit a transaction. Eval
admission and publish must produce byte-identical candidate closure fields for
the same draft and environment. Draft rows may continue to store a nullable
sealed binding digest, but no evaluation or gate path may substitute content
digest for a missing binding digest.

### Environment closure

The server owns Profile, Catalog, dataset, runtime contract, policy, threshold,
Provider fixture/model-probe, and build pins. The frontend supplies only:

- gate action;
- subject aggregate/version identity;
- qualifying Eval Run IDs;
- request ID;
- optional requested non-safety waiver codes and reason.

Gate creation loads qualifying runs, derives the authoritative environment
closure, and returns the stored subject snapshot. Publish/enable independently
recompute the closure while holding the aggregate lock. Client-authored digest,
decision, metric, assertion, policy, threshold, or build fields are rejected.

## Real Evaluation Architecture

### Provider and orchestration boundary

The default gate-eligible dataset mode executes the production Main Agent
orchestration contract with a deterministic scripted Provider. The scripted
Provider supplies versioned Provider responses for known prompts/turns, but it
cannot directly supply activated Skill keys, execution kind, completion truth,
Capability paths, safety counters, or assertion results.

The execution path must exercise:

- the Plan 01 parser and immutable candidate bytes;
- Catalog search/injection and alias resolution;
- Profile/prompt layering and locale/model selection;
- Plan 05 policy, budget, obligation, and completion contracts;
- the Provider loop and tool-call parsing;
- CapabilityCall planning and isolated dispatch;
- terminal completion and trace emission.

`dataset_live` uses a configured real Provider and is optional. It remains
promotion-ineligible in this remediation because nondeterministic live evidence
requires a separately versioned qualification policy. The UI must label this
status and must not offer live runs as qualifying gate evidence.

### Isolation and observation

Every dataset case receives an explicit `RuntimeIsolationContext` and separate
test-owned namespace. Replace production data, memory, Artifact, event,
CapabilityCall, object-key, outbox, and side-effect adapters while retaining
the production planner and policy contracts.

Dataset cases contain inputs and assertions only. Actual values are derived
from observed Eval events, test-owned CapabilityCall ledgers, obligations,
completion state, and adapter probes. Production table/object-key deltas are
measured before and after execution. Required safety counters that cannot be
observed remain missing and make the run indeterminate or failed; the system
must never manufacture zero evidence.

The existing expected-output materializer may remain only as an explicitly
named structural-test helper. It must always set `gate_eligible=False`, use a
distinct evidence provenance, and be rejected by gate qualification even for
historical completed rows.

### Dataset and fixture versioning

The deterministic Provider fixture is part of the reproducibility closure. An
Eval Run pins dataset version IDs, fixture revision/digest, candidate closure,
Profile/Catalog digests, runtime contract, policy/threshold versions, and build
revision. Any change invalidates prior evidence for a new gate.

## Publish and Promotion Lifecycle

The Skill lifecycle is strictly two-stage:

```text
draft candidate closure
  -> deterministic real-orchestration dataset runs
  -> skill_publish gate for kind=skill_draft
  -> immutable published version and pointer advance
  -> published-version deterministic dataset runs
  -> skill_catalog_enable gate for kind=skill_version
  -> catalog enable
```

Publish and promotion gates have different actions, subject kinds, subject
version IDs, request IDs, qualifying runs, consumption records, and expiry.
They cannot be reused across actions even when content and binding digests are
unchanged. Publishing clears draft-gate state in the UI. Catalog enable is not
available until a fresh qualifying run and promotion gate exist for the exact
current published version.

Profile publish/runtime-enable follows the same two-stage semantics. Profile
draft save advances only the draft pointer and aggregate revision. It never
changes the published pointer or `runtime_enabled`. Disable remains an
explicit, idempotent operation with actor/request evidence; it does not require
a promotion gate.

Gate service errors must distinguish invalid input, missing evidence, stale or
expired evidence, subject/environment drift, action/subject mismatch, already
consumed gate, hard-safety failure, and non-waivable failure with stable 409/422
contracts.

## Integrity Foundation

### Mandatory mutation concurrency

Every Plan 09 mutation requires a non-empty `requestId` and the relevant
expected aggregate/version/Run revision. Identical retries return the persisted
outcome before stale-revision checks. Reuse with a different request digest
conflicts. Every successful aggregate mutation advances its revision regardless
of which client invoked it.

If legacy Plan 01 callers require an optional-CAS endpoint, that compatibility
surface remains separate from the protected Plan 09 admin contract. The new UI
must never use a bypass-compatible mutation.

### Protected route ownership

Move every Plan 09 read and mutation, including Profile version detail and
resource/evidence reads, under one conditionally mounted protected parent
router. Remove the new Profile detail route from the always-mounted legacy
router and do not keep an unauthenticated alias. Feature flags, hidden
navigation, CORS, origin, loopback, and possession of an ID are not
authorization.

Until a real project-wide verified assistant-config principal/operator
dependency exists:

- staging and production do not mount Plan 09 routes;
- those routes are absent from OpenAPI;
- trusted development/test mounting remains explicit and never counts as
  release evidence;
- service-level privileged transitions require a verified
  `OperatorPrincipal`.

### Shared import preview

Persist preview tokens in PostgreSQL with TTL, principal/scope, mode,
target package/revision, upload digest, parsed content digest, candidate name,
finding/diff metadata, consumed state, applied package ID, request ID, and
request digest. Store the bounded raw ZIP bytes in the same preview row as a
`LargeBinary` payload. This keeps preview/apply ownership, expiry, and consume
state in one transactional store; the existing upload byte limit remains the
hard database payload limit.

Apply reloads the shared record, rechecks ownership, bytes/digests and target
revision, parses from the bound archive, applies atomically, marks the token
consumed, nulls the raw bytes, and leaves only bounded audit metadata. An
expiry cleanup deletes unconsumed raw payloads only after the token TTL and
retains the minimal audit fields required by request-id replay. Cross-worker
routing, restart before apply,
crash-after-commit retry, expiry, stale target revision, and altered request-ID
reuse must have deterministic behavior.

### Migration slice repair

Before editing migration history, record whether any shared environment has
applied Plan 09 revisions.

If no Plan 09 revision has been deployed, fold the alias trigger adjustment
into lifecycle revision `403414a62e55`, remove residual revision
`24f1e06fdd9e`, and let evaluation revision `027869a00a47` descend directly
from the complete lifecycle revision. Refresh all sole-head pins and evidence.

If a Plan 09 revision has been deployed, do not rewrite applied history. Add a
forward repair compatible with the deployed graph and document why 09A cannot
be retroactively isolated there. This fallback may preserve deployed database
safety, but the pre-merge target remains the clean independent graph.

Migration tests must execute Plan 08 -> 09A -> Plan 08 and Plan 08 -> 09A ->
09B -> 09A -> 09B. Alias soft-disable must work at 09A without evaluation
tables, and both downgrade boundaries must preserve their evidence guards.

## Administration Experience

### Workbench and evidence

The Workbench exposes prompt, locale, Profile/version, model or deterministic
fixture, mode, and dataset/version selection. Dataset modes cannot start
without a published dataset version. Unsupported or promotion-ineligible modes
are disabled or explicitly labelled rather than offered as successful paths.

Use bounded SSE replay with `afterSequence`, Eval Run ID + sequence
deduplication, reconnect, heartbeat, cancellation, and terminal reconciliation.
Polling may remain only as a bounded fallback after an explicit SSE failure.

Result views show bounded case summaries, observed active Skills, owner-qualified
Capability traces, completion/obligation status, aggregate metrics, assertion
failures, safety evidence availability, retention/expiry, and gate eligibility.
They never expose raw secrets, credentials, unbounded Provider payloads, or
unsafe resource content.

### Skill and Profile editors

The resource working copy supports add, replace, and remove while preserving
safe preview/download semantics. Save serializes the complete intended
resource snapshot through mandatory CAS; omission cannot accidentally erase
resources.

Capability selection loads published identities from the shared Registry,
shows target/version/risk/resolution evidence, preserves explicit order, and
does not accept arbitrary free-text keys as valid selections. The Skill editor
does not recreate generic Tool/Workflow/Agent editors.

Profile editing covers the Plan 09 snapshot contract and clearly separates
draft, published, promotion-gate, and runtime-enabled state. Draft save,
publish, promotion evaluation, enable, and explicit disable each have distinct
commands and UI feedback.

### Route-level fail-closed behavior

Universal Skill list/editor and Profile administration routes are lazy and
require both the server-reported Plan 09 feature and authenticated
assistant-config principal state. Navigation is absent when either is missing,
and direct URLs resolve to a fail-closed unavailable/not-found route without
issuing protected data requests. Legacy Skill pages remain available until
Plan 10.

## Failure and Compatibility Semantics

Missing fixture revisions, unresolved closure fields, binding resolution
failure, isolation breach, unobservable required counters, incompatible
runtime/build, malformed Provider responses, cancellation uncertainty, or
production namespace deltas terminalize the Eval Run with a stable failure code
and `gate_eligible=False`.

Historical synthetic evidence is excluded by provenance/mode contract even if
its stored row says completed and gate-eligible. New gate creation never
retroactively trusts it. Existing gates derived from synthetic evidence cannot
be consumed after the remediation and must be regenerated.

The legacy Skill page and Plan 01 runtime remain compatible and default-on as
before. Plan 09 admin/eval features remain default-off. No remediation step
enables Catalog entries, Profile runtime, gate enforce mode, or Plan 10
cutover in production.

## Verification Strategy

Implementation follows test-driven red-green cycles. Passing test counts are
not sufficient without the following contract evidence.

### Closure and evaluation tests

1. Eval admission and publish resolve identical content, binding-set, version,
   target-version, and config-revision digests for the same draft.
2. Drift of any candidate, binding, Profile, Catalog, dataset, fixture/model
   probe, runtime, build, policy, or threshold pin rejects old evidence.
3. A positive deterministic fixture drives the real Main Agent to the expected
   Skill and Capability outcome.
4. A negative fixture where the dataset expects Skill A but the real scripted
   Provider path selects Skill B fails assertions; expected values never rewrite
   actual outcomes.
5. Missing safety observations remain indeterminate/failing rather than zero.
6. Production tables and object namespaces have measured zero deltas; any
   actual delta produces an isolation failure.

### Gate and state tests

1. A draft publish gate cannot enable catalog; a published-version promotion
   gate cannot publish a draft.
2. Cross-action, cross-kind, cross-version, expired, drifted, reused, and
   synthetic-derived gates are rejected.
3. Concurrent mutation requests produce one applied result and one idempotent
   replay or stale conflict without lost updates.
4. Profile draft save while runtime-enabled leaves runtime enabled and published
   pointer unchanged.
5. Import preview works across independent service sessions/workers, survives a
   process restart, rejects stale targets, and replays crash-after-commit
   requests.
6. Production/staging OpenAPI contains no Plan 09 route, including the Profile
   version-detail path; trusted test mounts enforce principal and operator role.

### Frontend tests

1. Dataset/version, prompt, locale, Profile/model, fixture, and mode inputs map
   to the exact Eval Run contract.
2. SSE reconnect replays after sequence, deduplicates events, handles cancel,
   and reconciles terminal state.
3. Publish invalidates draft-gate UI state; enable remains unavailable until a
   published-version run and fresh promotion gate exist.
4. Resource add/replace/remove produces the intended working-copy snapshot and
   mandatory CAS body.
5. Capability selection uses only Registry-published identities and preserves
   order.
6. Missing feature/principal removes navigation and makes direct URLs fail
   closed without protected requests.
7. Profile draft, publish, promotion, enable, and disable states do not mutate
   one another implicitly.

### Process-level end-to-end proof

Run at least one real API/worker/PostgreSQL/browser-client lifecycle:

```text
create package
  -> save draft with CAS
  -> run deterministic real-orchestration dataset
  -> create skill_publish gate
  -> publish immutable version
  -> run deterministic dataset against published version
  -> create skill_catalog_enable promotion gate
  -> enable catalog
  -> Catalog lookup becomes visible only after enable
```

The companion negative path proves synthetic evidence, empty dataset IDs, old
gates, wrong binding closures, unauthenticated Profile reads, stale preview
records, and cross-worker altered requests all fail without pointer/live-state
changes.

## Evidence and Completion Criteria

Regenerate Task 9 evidence against the final remediation commit. It records:

- exact base/head commits and one Alembic head;
- both independent migration cycles;
- focused and full backend results in the required service environment;
- PostgreSQL concurrency and isolation suites;
- frontend component/store/route tests and production build;
- production/staging and trusted-test OpenAPI snapshots;
- the process-level lifecycle and negative-path evidence;
- `git diff --check` against the Plan 08 base;
- feature, worker, gate-mode, rollback, and compatibility defaults;
- the still-open real principal/operator release blocker.

Plan 09 is code-complete only when every requirement above maps to fresh
automated or process-level evidence. It must not be marked complete because the
routers are default-off, because isolated helpers pass, or because synthetic
fixtures produce threshold metrics.

M4 remains not release-complete until a real server-verified assistant-config
principal/operator guard protects every mounted Plan 09 route. Plan 10 may not
begin production cutover before that guard exists and the Plan 09 evidence is
rerun under the release configuration.
