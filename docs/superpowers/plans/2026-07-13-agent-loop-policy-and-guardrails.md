# MindAtlas Source-Aware Agent Policy, Fixed Budgets, and Completion Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Start only after full Plan 01, the reviewed Plan 02A readiness record with `PLAN_02A_READY=yes`, full Plan 03, and full Plan 04 are merged; Plan 04's read-only golden path/fixed evaluation and the explicit start-state regressions below must pass. Plan 02B observation/OpenClaw cleanup is a non-blocking coordination track.

**Goal:** Attribute every Main Agent Capability call to one immutable Main Agent/Skill owner, authorize that exact exposure against Principal/entrypoint/global/owner policy, keep a fixed Run budget that Skill injection cannot amplify, reserve call/round/repeat/depth budgets atomically, enforce structured Skill conflicts, and prevent `completed` while blocking obligations remain.

**Architecture:** Add `app.assistant.policy` as a pure contract/evaluation layer plus process-local revisioned ledgers owned by `MainAgentRunState`. A `ManifestExposureIndex` is derived from Plan 01 Manifest refs and Plan 02 frozen binding provenance; it does not change `ResolvedRunManifestRevision` v1. A composable verifier replaces Plan 04's minimum `skill_policy` evaluator while preserving Plan 04's independent ceiling/grant-source evidence transport and the OpenClaw verifier. The Plan 03 scheduler reserves fixed budgets before dispatch, the Plan 02 Gateway marks a reservation started immediately before adapter invocation through an injected guard, and a new provider-neutral default-permissive completion port lets a Main Agent adapter consult its Obligation Ledger before accepting natural text. Skill injection extends Plan 04's pending package and commits policy/budget/obligation deltas only through the existing post-lineage `ManifestEffectLifecyclePort.accept`. Plan 06 later persists the same snapshots/revisions without changing their meaning.

**Prerequisites:** Full Plan 01 with the locked conflict/satisfiability contracts below; approved Plan 02A Gateway/classification/independent-grant transport; full Plan 03 same-message scheduling and soft-finalization semantics; and full Plan 04 protected context, post-lineage pending-package activation, Domain Key exclusivity, independent Main Agent ceiling, and Run-state integration. Record Plan 02B as `pending|observing|complete`, but do not wait for its production observation or legacy OpenClaw deletion.

---

## 1. Plan Position and Non-Negotiable Boundary

This is Plan 05 of 10 and completes milestone M2.

Implemented here:

- One immutable authorization owner and exposure digest per visible Capability.
- Structured conflict-rule enforcement during atomic Skill activation.
- Frozen Principal, entrypoint, global, Main Agent, and Skill policy snapshots.
- Per-call pure authorization decisions and safe evidence digests.
- Fixed Run, Main Agent owner, Skill owner, Provider round, call, repeat, concurrency, wall-time, and depth budgets.
- Atomic single/batch reservation with honest started/unstarted accounting.
- Revisioned Obligation Ledger and a Provider Loop completion guard.
- Agent/Capability call-frame guards with production recursion remaining fail-closed where the exact Plan 02 adapter cannot support it safely.
- Read-only/compute release gate, adversarial evaluation, metrics, and reversible `off` rollback.

Not implemented here:

- No database migration, persisted budget/obligation/frame ledger, lease, Checkpoint, or restart recovery. Plan 06 owns persistence/CAS.
- No approval/input persistence. Reserved obligation types are not treated as a durable interrupt until Plan 07.
- No CapabilityCall database ledger, side-effect-started persistence, idempotency, unknown reconciliation, or write enablement. Plan 08 owns those.
- No policy/admin UI. Plan 09 owns authoring/productization.
- No new authentication/tenant model. The current Assistant entry remains an explicit local service Principal because the repository has no user auth dependency.
- No change to OpenClaw's exact request-scoped grant semantics.

Hard rules:

1. `ResolvedRunManifestRevision` v1 stays unchanged. Ownership/policy/exposure bodies are derived snapshots whose digest is carried by the existing `effective_policy_digest` field.
2. A Run freezes one policy contract version, hard/entrypoint/operator/Profile ceilings, Principal, and ledger limits. Config/deploy changes affect only later Runs.
3. Skill activation may add owner buckets/obligations but cannot increase any Run hard limit.
4. All Main Agent controls count as Capability calls; there is no name-based free-call path.
5. The Gateway remains the final execution authorization boundary. Scheduler preflight cannot replace Gateway verification.
6. `draft|write_local|write_external|unknown` stay denied even if every author layer claims otherwise.
7. Provider natural text, Tool output, and soft finalization are candidates for completion, not proof of completion.

---

## 2. Required Plan 04 Start State

Task 0 must verify the merged code rather than assume this document's provisional type/module names.

Plan 05 requires:

- a base Manifest containing Main Agent-owned control bindings and exact Profile/model/provider refs;
- Skill bindings whose `FrozenBindingProvenance` names their exact published owner version;
- a Plan 04 per-Run Catalog/Prompt/Artifact/control state and call-scoped `PendingSkillActivationPackage` whose active state commits only through `ManifestEffectLifecyclePort.accept` after Plan 03 lineage validation;
- `ProviderContextUpdateMessage` / `RoundContextProvider` so obligation summaries can enter the protected layer;
- one server-created local Assistant Principal and request-scoped `issuer=skill_policy` evidence path carrying independently derived `allowed_side_effects` plus `grant_source_digest`;
- pre-staging Domain Key exclusivity across Main Agent base controls, all active bindings, and every candidate in one injection batch;
- a Gateway-backed dispatcher and isolated Session factory for parallel-safe calls;
- final text buffering and safe public/internal Run events;
- fixed read-only evaluation fixtures and thresholds.

Stop and amend the owning prerequisite before Plan 05 if:

- Main Agent control provenance is still reported as ambiguous `system` state rather than exact Profile version ownership;
- a Skill binding can be materialized without its exact published Skill Version provenance;
- Provider call arguments are coerced after Plan 03's canonical `arguments_digest`; budget signatures require validation without transformation;
- Gateway success, `take_manifest_effect`, or an unvalidated Manifest child can make a Skill active, expose resources/Tools/context, or emit activation success before Plan 03 lineage acceptance and `ManifestEffectLifecyclePort.accept`;
- Plan 04 Domain Key conflict checks omit any base control, active binding, or same-batch candidate;
- Plan 01 stored `conflict_rules` without the structured v1 semantics below;
- Plan 01 permits a terminal-output policy that has no structural satisfaction path;
- Plan 04's minimum policy/evidence path bypasses Plan 02 Gateway/OpenClaw delegation, omits `grant_source_digest`, or derives `allowed_side_effects` from descriptor behavior;
- Plan 03/provider-loop contracts cannot accept an additive no-op completion port and protected message member without importing `app.assistant.policy` or forking the loop state machine;
- internal events are still streamed to clients with arbitrary metadata.

---

## 3. Runtime Topology and Decision Order

~~~mermaid
flowchart LR
    SURFACE["Frozen round surface"] --> EXP["ManifestExposureIndex"]
    CALL["Provider Tool Call"] --> PRE["Pure source-policy preflight"]
    EXP --> PRE
    PRE --> RES["Budget reservation"]
    RES --> EVID["Fresh skill_policy evidence"]
    EVID --> GW["Plan 02 Gateway"]
    GW --> VERIFY["Pure source-policy verification"]
    VERIFY --> START["DispatchGuard mark_started"]
    START --> CAP["Exact Capability adapter"]
    CAP --> RESULT["CapabilityResult"]
    RESULT --> OBL["Obligation transitions"]
    OBL --> COMPLETE["CompletionGuard"]
    COMPLETE -->|continue| LOOP["Plan 03 next/finalization round"]
    COMPLETE -->|complete/fail| RUN["Main Agent Run result"]
~~~

Locked call order:

1. Validate round scope/Manifest/surface/call/binding/descriptor identity.
2. Resolve exactly one exposure owner from the frozen Run policy snapshot.
3. Pure policy preflight (no evidence issue, no budget mutation).
4. Check cancellation, wall/depth/concurrency/repeat/call allowance.
5. Atomically reserve one call or an eligible sibling batch.
6. Issue fresh call-ID evidence from the same frozen snapshot.
7. Gateway repeats exact evidence/policy/availability/input validation.
8. Gateway guard verifies validated-input digest and marks the reservation started immediately before adapter invocation.
9. Execute exactly one adapter; finalize/release the reservation honestly.
10. Apply result-owned obligation transitions.
11. Pair every Provider call result in original order.
12. Consult completion guard before another Provider request or `completed`.

Authorization happens before reservation. Reservation happens before dispatch. Input Schema validation remains authoritative in the Gateway; because Plan 02 does not coerce values, the Gateway recomputes and verifies the canonical input digest against Plan 03's frozen arguments digest before `mark_started`.

---

## 4. Immutable Exposure and Policy Snapshot

### 4.1 Do not change Manifest v1

The Manifest already freezes Main Agent, active Skill, Capability, model/provider, alias, and `effective_policy_digest` identities. Derive a separate portable snapshot:

~~~python
class CapabilityExposureRef(FrozenContract):
    domain_key: str
    resolved_ref: ResolvedCapabilityRef
    binding_contract_digest: str
    descriptor_digest: str
    owner_kind: Literal["main_agent", "skill_version"]
    owner_id: str
    owner_version_id: UUID
    compatible_consumer_version_ids: tuple[UUID, ...]
    exposure_digest: str


class ManifestExposureIndex(FrozenContract):
    manifest_revision: int
    manifest_digest: str
    exposures: tuple[CapabilityExposureRef, ...]
    exposure_index_digest: str
~~~

Build the index only from:

- the exact Main Agent control bindings frozen into the base Manifest;
- exact binding rows/provenance owned by active `ResolvedSkillRef.version_id` values;
- Plan 02 descriptors that verify against the same binding/ref/digests.

Sort by Domain Key UTF-8 bytes. Reject missing, extra, duplicate-ambiguous, changed-owner, or digest-drifted entries. Do not query mutable current/latest targets to fill a gap.

The Provider alias is never an owner key. The exact round call resolves alias -> Domain Key/binding through Plan 03, then Domain Key/binding -> exposure through this index.

### 4.2 Main Agent and Skill owners

- Plan 04 controls use `owner_kind=main_agent`, stable Profile ID/key, and exact published Profile Version ID.
- A business Capability first exposed by an active Skill uses `owner_kind=skill_version`, stable package ID as owner ID, and exact published Skill Version ID.
- Instruction-only Skills own no Capability exposure but may create completion obligations.
- A published but inactive Skill owns and grants nothing in this Run.

### 4.3 Strict duplicate compatibility

When a candidate Skill declares an already-exposed Domain Key:

1. Existing owner stays frozen.
2. Exact `ResolvedCapabilityRef`, binding-contract digest, descriptor digest, side-effect class, Schema bodies/digests, executable/dependency closure, timeout, interrupt, parallel, and completion metadata must match.
3. Normalized Skill policies must have identical `max_skill_calls`, `max_same_read_calls`, `requires_terminal_output`, and `terminal_text_allowed`; each independently derived owner grant must admit the currently classified side-effect class. This membership test does not make the descriptor an input to either grant.
4. Conflict rules must permit coexistence.
5. If compatible, append the later Skill Version to `compatible_consumer_version_ids` in UUID-byte order. It receives no ownership, alias, or additional allowance for that exposure.
6. Any stricter, broader, missing, or semantically different declaration fails activation with `duplicate_capability_policy_conflict`.

This deliberately avoids implicit “use the strictest” or “pick whichever has budget” behavior.

For candidates activated in the same atomic batch, existing active owners win; otherwise the lowest canonical Skill name/version UUID in canonical batch order becomes owner so caller list order cannot alter policy.

### 4.4 Structured conflict rules v1

Import Plan 01's locked `SkillConflictRuleV1` and its canonical fixed vectors verbatim; the shape is repeated here only to state the enforcement input, not to authorize a second model/parser:

~~~python
class SkillConflictRuleV1(FrozenContract):
    kind: Literal["excludes", "requires", "exclusive_group"]
    target_skill: str | None = None
    group: str | None = None
~~~

Rules:

- `excludes` requires one canonical `target_skill`; activation fails if target is active or in the same batch. Evaluate both existing and candidate rules.
- `requires` requires one canonical target already active or present in the same atomic batch. Do not auto-inject it.
- `exclusive_group` requires one normalized nonempty group; at most one active Skill may claim it.
- Names resolve through the exact Catalog snapshot to canonical identities; aliases are accepted only as author-import input and are stored normalized/canonical by Plan 01.
- Self-target, duplicate, contradictory, unknown-kind, missing target/group, and unresolved target rules fail publication in Plan 01 or activation fail-closed here.
- Conflict evaluation is symmetric and independent of injection order.

Task 0 requires byte-compatible normalized payload/digest vectors from Plan 01. Any field or semantic mismatch is an upstream Plan 01 defect and blocks Plan 05; do not add a translation layer or second conflict dialect here.

### 4.5 Effective policy snapshot

~~~python
class EffectiveRunPolicySnapshot(FrozenContract):
    policy_contract_version: Literal[1] = 1
    app_build_revision: str
    run_id: UUID
    principal: CapabilityPrincipal
    entrypoint: Literal["main_agent"]
    main_agent_profile_version_id: UUID
    main_agent_profile_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    exposure_index: ManifestExposureIndex
    owner_policy_refs: tuple[OwnerPolicyRef, ...]
    grant_source_set_digest: str
    run_budget_limits: RunBudgetLimits
    effective_policy_digest: str
~~~

The digest covers policy contract/build revision, exact Principal/entrypoint/Profile, hard read-only release gate, normalized entrypoint/global/owner policies, the independently derived capability-grant source set, exposure index, conflict state, and resolved Run limits. It excludes prompts, user input, Tool arguments/results, secrets, timestamps, mutable counters, and live availability reason text. Individual `grant_source_digest` values remain independently reproducible without descriptor behavior even though the broader policy snapshot also pins the separately verified exposure/classification state.

Base Manifest uses this digest. Skill activation computes a candidate policy/exposure snapshot and appends a child whose existing `effective_policy_digest` equals the candidate. No in-place update is allowed.

The process-local Run state retains the full snapshot. Plan 06 persists it or a lossless reconstruction body plus digest. Reconstructing from immutable refs/constants must reproduce the digest exactly.

---

## 5. Pure Authorization Contract

### 5.1 Policy layers

A proposed call is policy-allowed only when all are true:

~~~text
exact round Manifest/surface exposes exact binding
AND exposure index resolves exactly one immutable owner
AND server-created Principal is authenticated for assistant_chat
AND independent platform/entrypoint/Profile/owner sources derive a nonempty exact-binding grant
AND the classified descriptor side effect/interrupt mode is a member of that frozen grant
AND hard Plan 05 release gate remains none|read|compute
AND exact target/version/build/dependency remains available
AND recursion/depth policy allows the call
~~~

Budget/concurrency/repeat admission is evaluated immediately after this pure policy decision and never broadens it.

### 5.2 Current Principal boundary

The repository's Assistant routes have no user auth. Use only the Plan 04 server-created:

~~~text
principal_type=service
principal_id=local-assistant
authenticated=true
tenant_scope_id=None under explicit current single-tenant mode
~~~

No HTTP field, model output, Skill content, or Tool argument can override it. This is not documented as per-user authorization. A future authenticated Principal requires a new entrypoint policy/version and tests.

### 5.3 Entrypoint/global/owner semantics

- `assistant_chat` entrypoint ceiling is `none|compute|read`.
- Profile global policy must remain deny-by-default and may further deny named controls/classes.
- Main Agent owner policy contributes only Profile-declared exact controls and immutable reviewed effect ceilings.
- Skill owner policy contributes only exact bindings and the immutable Plan 01 author declaration from that Skill Version. The evaluator later tests the descriptor against the independently derived effective grant.
- An author declaration cannot downgrade Plan 02 classification.
- One Skill's policy never broadens or globally narrows another exposure.
- Compatible consumers are completion-evidence consumers only; they do not participate in authorization.
- Live target unavailability/version/build/config drift is a deny, even though the frozen policy body is unchanged.

Before inspecting `CapabilityDescriptor.behavior`, derive one independent grant for the exact owner/binding:

~~~python
class EffectiveCapabilityGrant(FrozenContract):
    owner_kind: Literal["main_agent", "skill_version"]
    owner_version_id: UUID
    capability_key: str
    binding_contract_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none"], ...]
    platform_ceiling_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    owner_policy_digest: str
    grant_source_digest: str
~~~

Construction rules:

- Reuse Plan 04's checked-in `MAIN_AGENT_READ_ONLY_EFFECT_CEILING`, whose canonical lattice prefix is `("none", "compute", "read")`; do not create a competing platform ceiling.
- Intersect that prefix, in Plan 02 lattice order, with immutable `assistant_chat` entrypoint rules, the hard Plan 05 read-only release rule, the published Profile global policy, and the exact Main Agent/Skill owner declaration. `none` remains available only through the explicit Main Agent entrypoint rule.
- Main Agent grants require exact Profile control exposure. Skill grants require exact active Manifest membership, binding ownership, and the published Skill Version's Plan 01 author policy. One owner is never unioned with another.
- `grant_source_digest` covers the fields above except itself plus the exact immutable source-policy revisions/identity needed to reconstruct them. It excludes descriptor side effect, behavior/descriptor/classification digests, availability, and mutable catalog state.
- Only after the grant is frozen does the evaluator test whether the current classified descriptor effect and interrupt mode are members. A mismatch denies; it never regenerates or widens the grant.

### 5.4 Decisions and evidence

~~~python
class AuthorizationDecision(FrozenContract):
    allowed: bool
    reason_code: str
    principal_digest: str
    entrypoint_policy_digest: str
    global_policy_digest: str
    owner_policy_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...] = ()
    grant_source_digest: str | None = None
    exposure_digest: str
    effective_policy_digest: str
    decision_digest: str
~~~

Stable deny order/codes:

1. `scope_mismatch`
2. `manifest_surface_mismatch`
3. `exposure_missing`
4. `exposure_ambiguous`
5. `owner_mismatch`
6. `principal_unauthenticated`
7. `principal_not_allowed`
8. `entrypoint_not_allowed`
9. `global_policy_denied`
10. `owner_capability_not_declared`
11. `owner_side_effect_denied`
12. `release_gate_denied`
13. `target_unavailable`
14. `version_or_digest_drift`
15. `recursion_denied`
16. `allowed`

The Plan 04 `CapabilityAuthorizationEvidence` transport remains. For an allowed call:

- `issuer=skill_policy`, `entrypoint=main_agent`;
- exact owner/exposure/call/binding/resolution/dependency identities;
- `allowed_side_effects` is copied from the already-frozen `EffectiveCapabilityGrant`, not from `descriptor.behavior.side_effect`;
- `grant_source_digest` is copied unchanged from that grant and participates in the Plan 02 decision/evidence verification;
- evidence digest includes `decision_digest`/effective policy/grant-source evidence through the verifier's canonical calculation;
- evidence is issued immediately before Gateway dispatch and consumed once for that call ID.

For a denial reached before an exact grant can be derived, the decision carries `allowed_side_effects=()` and `grant_source_digest=None`; its decision digest includes those explicit empty values. An allowed decision requires a nonempty valid digest and the independently derived tuple.

The Gateway separately checks `descriptor.behavior.side_effect in allowed_side_effects`, exact current classification/descriptor equality, and interrupt eligibility. A verifier that emits `(descriptor.behavior.side_effect,)`, changes the ceiling after snapshot freeze, omits `grant_source_digest`, or substitutes a classification digest for a grant digest must deny before adapter invocation. Preserve the Plan 02/04 read-to-write, ruleset-bump, ceiling-bump, and copy-descriptor negative vectors.

No decision/evidence includes policy prose, prompts, arguments/results, memory, Artifact/resource content, credentials, URLs, headers, or exception text.

### 5.5 OpenClaw delegation

Use an explicit composite verifier:

| Issuer/entrypoint | Verifier |
|---|---|
| `openclaw_bridge/openclaw` | unchanged Plan 02 request-scoped verifier |
| `skill_policy/main_agent` | this Plan 05 source-aware verifier |
| `test/test` | injected test verifier only |
| any other combination | deny |

OpenClaw does not inherit Skill policy, budgets, or rollback; Main Agent does not inherit OpenClaw catalog grants.

---

## 6. Fixed Run and Owner Budget Contract

### 6.1 Resolved Run limits

~~~python
class RunBudgetLimits(FrozenContract):
    max_provider_rounds: int
    max_main_agent_cycles: int
    max_active_skills: int
    max_total_capability_calls: int
    max_parallel_calls: int
    max_capability_depth: int
    max_agent_depth: int
    max_same_read_signature: int
    max_prompt_tokens: int | None
    max_completion_tokens: int | None
    max_wall_time_ms: int
    max_completion_followup_rounds: int
~~~

Initial `assistant_chat` defaults:

| Limit | Default | Checked-in hard ceiling |
|---|---:|---:|
| Provider rounds, including finalization | 8 | 16 |
| Main Agent start/resume cycles in Plan 05 | 1 | 1 |
| active Skills | 4 | 8 |
| total Capability calls, controls included | 16 | 64 |
| parallel started calls | 4 | 8 |
| Capability depth | 4 | 8 |
| Agent depth | 2 | 4 |
| same read signature | 3 | 10 |
| prompt tokens | unset unless reliable estimator/evidence exists | 1,000,000 |
| completion tokens total | 4,096 | 16,384 |
| wall time | 120,000 ms | 600,000 ms |
| completion follow-up rounds | 2 | 4 |

Resolve each limit once as the minimum of:

1. checked-in hard ceiling;
2. checked-in `assistant_chat` entrypoint default/ceiling;
3. optional operator setting, which may lower only;
4. compatible Profile request where the final Plan 01 `contextBudget`/`outputBudget` exposes that field.

Missing Profile fields use entrypoint defaults. No Skill can supply a Run limit.

`max_main_agent_cycles=1` is explicit because Plan 05 has no durable resume. Plan 06 may persist/recover the same cycle accounting; it must not reset it.

### 6.2 Owner limits

~~~python
class OwnerBudgetLimits(FrozenContract):
    owner_kind: Literal["main_agent", "skill_version"]
    owner_version_id: UUID
    max_calls: int
    max_same_read_signature: int
    owner_budget_digest: str
~~~

- Main Agent control bucket defaults to 8 calls, capped by remaining Run total.
- Each active Skill bucket uses `min(skill.max_skill_calls, Run total)` and `min(skill.max_same_read_calls, Run repeat limit)`.
- Instruction-only Skills still get a zero/declared bucket but cannot spend it without an owned exposure.
- Compatible consumers do not get allowance for the shared exposure; the frozen first owner is charged.
- Adding 1 or 100 Skills leaves the Run total/round/time/depth/token limits byte-identical.
- Removing/disabling a Skill mid-Run is not supported; cancellation ends the Run.

### 6.3 Controls count explicitly

`skill.search`, `skill.inject`, `skill.read_resource`, and `artifact.read` consume both Run total and the Main Agent owner bucket after start. Repeat `skill.search`/resource/Artifact reads are subject to read-signature limits. No control is free merely because its side effect is `none` or `read`.

---

## 7. Revisioned Budget Ledger and Reservation Protocol

### 7.1 Serializable state

~~~python
class BudgetReservation(FrozenContract):
    call_id: str
    owner_kind: Literal["main_agent", "skill_version"]
    owner_version_id: UUID
    domain_key: str
    side_effect: SideEffectClass
    arguments_digest: str
    read_signature: str | None
    state: Literal["reserved", "started", "finished", "released"]
    reservation_digest: str


class BudgetLedgerState(FrozenContract):
    revision: int
    limits: RunBudgetLimits
    owner_limits: tuple[OwnerBudgetLimits, ...]
    provider_rounds_started: int
    main_agent_cycles_started: int
    capability_calls_started: int
    completion_followups_started: int
    prompt_tokens_used: int
    completion_tokens_used: int
    owner_calls_started: tuple[OwnerUsage, ...]
    global_read_signatures: tuple[SignatureUsage, ...]
    owner_read_signatures: tuple[OwnerSignatureUsage, ...]
    reservations: tuple[BudgetReservation, ...]
    denial_count: int
    started_at_utc: datetime
    deadline_at_utc: datetime
    ledger_digest: str
~~~

State is immutable-by-revision under a process-local lock/CAS interface. Plan 05 stores it in memory; Plan 06 maps the same transition contract to database CAS.

Runtime also keeps a monotonic deadline derived at start. UTC fields are portable audit state; wall-clock changes cannot extend the live deadline.

### 7.2 Call and read signatures

- `arguments_digest` is Plan 03's canonical parsed JSON-object digest.
- Gateway recomputes the digest after Plan 02 Schema validation; validation performs no coercion. Mismatch blocks before adapter start.
- `read_signature = sha256(binding_contract_digest + arguments_digest)` only for descriptor side effect `read`.
- Global and owner repeat counters increment when the call becomes `started`, not at reservation.
- `none|compute` calls still consume total/owner counts but not the read-signature counter.
- Provider alias, display name, timestamps, or insertion order never participate.

### 7.3 Single-call lifecycle

1. Pure policy allow.
2. `reserve_one` atomically checks Run total, owner total, active reservations, concurrency, read repeats, depth, cancellation, and deadline.
3. Reservation state becomes `reserved`; no execution/repeat count is consumed yet.
4. Issue fresh evidence and call Gateway with a `CapabilityDispatchGuard` bound to the reservation.
5. After exact input/policy/availability validation and immediately before adapter invocation, Gateway calls `mark_started(call_id, validated_arguments_digest)`.
6. `mark_started` atomically verifies the reservation/digest/deadline, advances call/repeat/concurrency usage, and changes state to `started`.
7. Adapter success/failure/timeout/cancellation after start changes it to `finished`; counts remain consumed.
8. Authorization/input/version/cancellation failure before start changes it to `released`; execution/repeat counts are not consumed.
9. Unexpected Gateway/adapter exceptions use `finally` to finish a started reservation or release an unstarted one without exposing exception text.

A rejected call records a denial metric/reason but does not create allowance or count as execution.

### 7.4 Parallel batch reservation

Before any bounded-parallel sibling starts:

- every call must already be Plan 03 parallel-eligible and pure-policy allowed;
- use the exact shared exposed surface/current Manifest identities;
- call `reserve_batch` once under the ledger lock;
- check total, each owner, each signature, max parallel, deadline, and existing reservations against the whole batch;
- either reserve all calls or none.

If batch reservation cannot be granted as a batch, replan the group sequentially in Provider order. Sequential calls reserve one-by-one until a stable denial/cancellation/obligation stops the suffix. Every original Tool Call still receives a paired terminal result.

Independent worker Sessions/Gateways remain required. The ledger/clock/cancellation ports are thread-safe and shared; business Sessions are not.

### 7.5 Provider round/token/time accounting

- Reserve/start one Provider round immediately before network I/O. A request that started consumes the round even if Provider fails.
- Plan 03's tools-disabled finalization counts as a Provider round and must fit the initial maximum.
- `max_rounds` passed to Plan 03 equals the frozen Run limit and never resets after a completion follow-up.
- Accumulate Provider-reported usage after each round. Cap each next request's output tokens by remaining total.
- Enforce `max_prompt_tokens` before request only when the exact adapter supplies a deterministic estimator compatible with the model. Otherwise leave it `None` and rely on Plan 04 character limits plus actual usage metrics; do not fabricate precision.
- If actual usage crosses an available limit, do not undo the completed request; block the next round/call and enter the exhaustion policy.
- Check monotonic deadline before Provider request, reservation, `mark_started`, and completion follow-up.

### 7.6 Budget exhaustion

On first exhaustion:

1. Pair the denied call with safe `blocked/budget_exhausted` and stop starting unsafe suffix calls; suffix calls get `cancelled_before_start` as required by Plan 03.
2. Record the exact safe budget dimension internally.
3. If one reserved Provider round remains, run exactly one tools-disabled soft finalization with a protected bounded summary of completed/pending work.
4. If obligations become satisfied by that final text, completion may succeed.
5. If blocking obligations remain, return `failed: budget_exhausted_with_obligations`.
6. If no finalization round remains, hard fail with `budget_exhausted_no_finalization_slot`.

Skill injection after exhaustion cannot add budget or escape finalization.

---

## 8. Obligation Ledger and Completion Contract

### 8.1 Contracts

~~~python
class CompletionObligation(FrozenContract):
    obligation_id: str
    owner_kind: Literal["main_agent", "skill_version", "capability_call"]
    owner_id: str
    owner_version_id: UUID | None
    source_call_id: str | None
    obligation_type: Literal[
        "terminal_output",
        "required_followup",
        "required_artifact",
        "approval",
        "user_input",
        "reconciliation",
    ]
    blocking: bool
    requirement_digest: str
    status: Literal["pending", "satisfied", "waived", "failed"]
    evidence_refs: tuple[str, ...]
    created_revision: int
    resolved_revision: int | None


class ObligationEvidenceEdge(FrozenContract):
    obligation_id: str
    evidence_kind: Literal["provider_text", "capability_result", "artifact", "compatible_consumer"]
    source_owner_version_id: UUID | None
    source_call_id: str | None
    evidence_digest: str
    predicate_digest: str


class ObligationLedgerState(FrozenContract):
    revision: int
    obligations: tuple[CompletionObligation, ...]
    evidence_edges: tuple[ObligationEvidenceEdge, ...]
    followup_rounds_started: int
    ledger_digest: str
~~~

IDs are deterministic from Run ID, owner, obligation type, source call, and per-source ordinal. Text/result content is represented only by a digest/ref in ledger state.

Transitions are append/update-by-revision under the Run-state lock:

- `pending -> satisfied|waived|failed` only;
- terminal states never reopen; create a new obligation if later work adds a requirement;
- every resolution has at least one exact evidence edge or a safe explicit cancellation/waiver reason;
- no arbitrary metadata/prose field is allowed.

### 8.2 Obligation creation in Plan 05

Create:

- one Main Agent `terminal_output` obligation at Run start;
- one Skill `terminal_output` obligation when an activated Skill has `requires_terminal_output=true`;
- one `required_followup` obligation after a completed Capability Result with `needs_followup=true`;
- a terminal-output evidence candidate after a completed Result with `terminal_output=true` and a nonempty user/structured/Artifact result.

`required_artifact`, `approval`, `user_input`, and `reconciliation` are reserved and serializable, but current production Plan 05 read-only descriptors do not create them. Fixture tests cover their blocking semantics so Plans 06–08 can persist/use them without redefining completion.

A blocking Skill terminal obligation may be created only when it is satisfiable:

- Plan 01 publication performs the structural check. When `requires_terminal_output=true`, either `terminal_text_allowed=true` or the exact published Skill Version must declare at least one Capability whose frozen completion contract has `terminal_output=true`. An instruction-only Skill with terminal text forbidden is invalid. A capability-only path also requires a positive `max_skill_calls` declaration.
- Plan 05 activation repeats a runtime check against the candidate exposure/policy/budget state. A text path requires terminal text to be allowed and at least one remaining Provider/finalization slot after `skill.inject`. A Capability path requires an exact owned or strictly compatible terminal-output exposure that is available, admitted by its independently derived grant, and has at least one remaining Run/owner call allowance.
- Do not assume a future alias, mutable target, unrelated Skill, or budget increase can satisfy the obligation. If no current path exists, fail the whole activation with `skill_completion_unsatisfiable` before staging the pending package.
- The satisfiability check proves that a path exists; it does not mark the obligation satisfied or reserve that future call. Ordinary budget/concurrency/completion rules still apply.

### 8.3 Satisfaction rules

- Nonempty natural Provider final text satisfies the Main Agent terminal obligation.
- It satisfies a Skill terminal obligation only when that exact Skill policy has `terminal_text_allowed=true`.
- A terminal Capability Result satisfies only its execution owner's matching terminal obligation by default.
- A later natural Provider text after a call satisfies that call's `required_followup` obligation.
- An intermediate Tool Result, Assistant prose accompanying Tool Calls, empty text, failed/cancelled result, or mere Artifact existence satisfies nothing automatically.
- A child Agent/Workflow Result cannot declare the whole Run complete; it may satisfy only exact owner/call obligations.

For a compatible consumer, the owner call may satisfy a consumer obligation only when:

1. the consumer appears in the exact exposure's `compatible_consumer_version_ids`;
2. binding/completion/predicate digests are identical;
3. a separate `compatible_consumer` evidence edge names the exact source call/result predicate and target obligation;
4. no policy, owner, alias, or budget is reattributed.

### 8.4 Completion decisions

~~~python
class CompletionDecision(FrozenContract):
    action: Literal["complete", "continue", "fail", "wait"]
    reason_code: str
    blocking_obligation_ids: tuple[str, ...]
    instruction_digest: str | None
    decision_digest: str


class ProviderCompletionRequest(FrozenContract):
    manifest_revision: int
    manifest_digest: str
    candidate_text: str | None
    finalization_round: bool


class ProviderCompletionInstructionMessage(FrozenContract):
    role: Literal["runtime_completion"] = "runtime_completion"
    locale: str
    manifest_revision: int
    manifest_digest: str
    guard_state_digest: str
    content: str


class ProviderCompletionDisposition(FrozenContract):
    action: Literal["complete", "continue", "fail", "wait"]
    reason_code: str
    instruction: ProviderCompletionInstructionMessage | None
    decision_digest: str


class ProviderCompletionGuard(Protocol):
    def evaluate(
        self,
        request: ProviderCompletionRequest,
    ) -> ProviderCompletionDisposition: ...
~~~

`CompletionDecision` remains an `app.assistant.policy` value and may contain obligation IDs. `ProviderCompletionRequest`, `ProviderCompletionDisposition`, and `ProviderCompletionGuard` live in the provider-loop contract and contain no `BudgetLedgerState`, `ObligationLedgerState`, policy evaluator, callback, lock, or ORM type. The Main Agent adapter closes over its own thread-safe policy/ledger state, evaluates the rich decision internally, and projects only the neutral disposition back to Plan 03.

Add this port to the Plan 03 Provider Loop as a Plan 05 additive extension. The default implementation reproduces Plan 03's existing natural-completion behavior byte-for-byte: a nonempty no-Tool candidate completes, and existing empty/protocol/finalization handling is unchanged. Therefore Plan 05 does not require the port to have been pre-implemented in Plan 03, but Task 0 must stop if it cannot be added without importing `app.assistant.policy`, changing non-Main-Agent vectors, or forking the loop state machine.

Provider Loop behavior:

1. A round with Tool Calls is never natural completion; execute/pair calls and apply result transitions.
2. A no-Tool round creates the neutral request and presents it to the guard.
3. The Main Agent guard adapter applies valid text evidence and checks all blocking obligations through its closed-over state; Plan 03 never reads either ledger.
4. If none remain, return `completed` with exact final text.
5. If obligations remain and normal rounds/tools/budget allow, append one protected bounded completion instruction and continue.
6. Each such continuation consumes both a Provider round and `max_completion_followup_rounds`.
7. On a tools-disabled finalization round, pending non-text-satisfiable obligations cause failure, not another loop.
8. `wait` is valid only with pending approval/input obligation plus an exact continuation. Plan 05 production cannot produce it; an occurrence from a Plan 05 descriptor is a protocol failure.

### 8.5 Protected completion instruction

Add a distinct Provider message union member rather than changing Plan 03 soft-finalization fields:

`ProviderCompletionInstructionMessage` above maps to system-level Provider input. `guard_state_digest` is opaque to Plan 03; the Main Agent adapter sets it to the exact Obligation Ledger digest used for its internal decision. The message is bounded/deterministic, summarizes only obligation type/owner identity/safe follow-up hint, and contains no user text, Tool result, Artifact/resource content, policy prose, or secret. It is never emitted as final user text or logged.

### 8.6 Stable completion reason codes

- `all_obligations_satisfied`
- `terminal_text_missing`
- `skill_terminal_output_pending`
- `capability_followup_pending`
- `artifact_pending`
- `approval_pending`
- `user_input_pending`
- `reconciliation_pending`
- `completion_followup_limit`
- `budget_exhausted_with_obligations`
- `obligations_pending_at_finalization`
- `waiting_without_obligation`
- `completion_evidence_invalid`

---

## 9. Atomic Skill Activation Across Policy, Budget, and Obligations

Extend Plan 04's pending-package lifecycle; do not replace it with a pre-lineage commit:

1. Perform Plan 04 Catalog/live pointer/binding/context/count checks and build the complete Domain Key ownership map before staging. Main Agent base-control collisions remain unconditional failures. Existing-active and same-batch business duplicates proceed only to Section 4.3 strict compatibility; they never create a second owner/alias/allowance.
2. Normalize/evaluate all candidate and existing conflict rules.
3. Build the candidate Manifest active Skill/capability refs.
4. Build the candidate exposure index, applying strict duplicate compatibility.
5. Normalize candidate owner policy/grant refs and run the Section 8.2 terminal-obligation satisfiability check.
6. Compute owner-budget-limit additions capped by unchanged Run limits. Do not copy or reset Run usage, reservation, deadline, Provider-round, or token counters.
7. Compute candidate Skill obligations, protected-context additions, activation projection, and buffered safe success events.
8. Compute the candidate effective policy/ledger/package digests.
9. Append one proposed Manifest child whose `effective_policy_digest` matches the candidate policy snapshot.
10. Stage all candidate values in one extended `PendingSkillActivationPackage` under the exact control call ID. At this point the current Manifest, policy/exposure snapshot, owner limits, obligations, context, activation projection, resource/Tool authorization, and public success events remain unchanged.
11. Gateway returns the provisional result; Plan 04 transfers the pending effect to the dispatcher, and Plan 03 validates the proposed child's lineage exactly as defined in Plan 04 Section 5.4.
12. Only `ManifestEffectLifecyclePort.accept` may commit. It first validates the package/call/parent/child/effect digests and that the `skill.inject` reservation reached the expected terminal accounting state. Under the same `MainAgentRunState` lock/CAS boundary, it then advances the Manifest pointer, policy/exposure snapshot, owner limits, obligations, protected-context pending set, and activation projection as one revision; safe success events become post-commit deliverables only after that revision succeeds.
13. Any take, lineage, pre-accept cancellation, package, CAS, or lifecycle failure calls `discard` and removes the entire candidate package. No candidate owner bucket, obligation, context, active membership, resource/Tool exposure, or success event survives.

Reinjection of the exact version is idempotent and adds no package, bucket, consumer, obligation, context message, event, or Manifest revision.

The `skill.inject` control call itself is reserved/started under the Main Agent bucket before candidate construction. Its own accounting follows Section 7 independently: a failure before `mark_started` releases it, while any post-start result—including later lineage/lifecycle rejection—remains charged honestly. Discard rolls back only unaccepted activation candidates; it never rewinds a started Capability call. New owner buckets apply only after lifecycle acceptance and only to later calls.

---

## 10. Capability/Agent Depth and Recursion Guard

### 10.1 Process-local frame contract

~~~python
class CapabilityCallFrame(FrozenContract):
    call_id: str
    capability_type: Literal["tool", "workflow", "agent"]
    domain_key: str
    target_identity: str
    target_version_id: UUID | None
    binding_contract_digest: str
    owner_kind: Literal["main_agent", "skill_version"]
    owner_version_id: UUID
    capability_depth: int
    agent_depth: int
    frame_digest: str


class CapabilityCallFramePort(Protocol):
    def current(self) -> tuple[CapabilityCallFrame, ...]: ...
    def push(self, frame: CapabilityCallFrame) -> ContextManager[None]: ...
~~~

Pass this ephemeral port through `CapabilityRuntimePorts`; do not add callback/lock objects to `CapabilityExecutionContext` or another frozen Provider contract. Plan 06 persists the portable frame values at checkpoints.

### 10.2 Guard rules

- Capability depth is current frame count plus one; reject before reservation if above limit.
- Agent depth counts only `capability_type=agent` frames.
- Reject an exact Agent target version already present in the active Agent frame stack.
- Reject a Main Agent restart from an Agent/Workflow dependency.
- Every nested Gateway call uses the same Run ID, Principal, Manifest/policy snapshot, budget/obligation ledgers, cancellation, deadline, and event sink.
- A child sees only its exact frozen binding/dependency execution scope. It never inherits all parent-visible Provider tools automatically.
- Child result can satisfy only exact child/owner obligations and cannot mark the parent Run complete.

### 10.3 Honest production boundary

Plan 02's current exact Agent adapter reuses `run_agent_execution` with frozen Tool/model dependencies. Bindings that imply nested Agent/Main-Agent restart were classified `unknown`/unavailable. Preserve that fail-closed boundary.

Plan 05 production therefore supports Main Agent -> one eligible published Agent Capability frame and shared parent Run accounting for that call, but does not claim recursive Provider-loop Agents where the adapter cannot route every nested call through the same Gateway/ledgers. Use a fake nested Gateway adapter to prove the generic cycle/depth guard; keep real recursive Agent bindings unavailable until a later explicit contract can satisfy the same invariants.

---

## 11. Provider Scheduler and Gateway Integration

### 11.1 Additive runtime guards

Add generic optional ports with no-op defaults:

~~~python
class CapabilityDispatchGuard(Protocol):
    def mark_started(
        self,
        *,
        call_id: str,
        validated_arguments_digest: str,
    ) -> None: ...

    def finish(self, *, call_id: str, status: str) -> None: ...
    def release_unstarted(self, *, call_id: str, reason_code: str) -> None: ...


class ProviderRoundBudgetGuard(Protocol):
    def before_round(self, request: ProviderRoundRequest) -> ProviderGenerationOptions: ...
    def after_round(self, result: ProviderRoundResult) -> None: ...
~~~

Plan 02 Gateway invokes `mark_started` only after exact input validation, evidence verification, availability/version checks, and final cancellation check, immediately before adapter execution. Existing OpenClaw/non-Main-Agent calls use no-op guards and retain behavior.

Plan 03 invokes the round guard immediately before Provider I/O and reports usage afterward. It invokes `ProviderCompletionGuard` with only the neutral request before terminal success. The Main Agent adapter owns/locks policy ledgers and projects the neutral disposition; Provider Loop code never imports or receives those states. Existing test-only/non-Main-Agent compositions use defaults.

### 11.2 Dispatcher/scheduler responsibility

- Main Agent dispatcher resolves the exact exposure and runs pure policy preflight.
- Scheduler owns single/batch reservation because it knows sibling groups.
- Dispatcher issues evidence and passes the reserved call guard to Gateway.
- Gateway is authoritative and may still deny; dispatcher then releases unstarted reservation.
- Started calls always finish accounting even when Capability result is failed/cancelled.
- Sequential calls re-evaluate cancellation/policy/budget/obligations after every prior result.
- A newly blocking obligation may cancel/defer the unstarted suffix under existing Plan 03 pairing rules.
- Control/Manifest-mutating calls remain sequential.

### 11.3 Failure mapping

Map safe outcomes without arbitrary exception text:

| Condition | Tool result / loop outcome |
|---|---|
| pure policy deny | `blocked/policy_denied` |
| budget/repeat/depth deny | `blocked/<stable budget code>` |
| final Gateway deny/drift | `blocked/capability_denied` and release reservation |
| unsatisfiable candidate Skill completion | `blocked/skill_completion_unsatisfiable`; discard pending activation candidate |
| adapter failed after start | existing safe `failed`, budget consumed |
| cancellation before start | `cancelled_before_start`, reservation released |
| cancellation after start | `cancelled`, budget consumed |
| pending obligations after natural text | protected follow-up or explicit failed stop |
| internal ledger divergence | fatal `policy_state_protocol_error`, no fallback |

Policy/budget/obligation/ledger protocol errors are never Legacy-fallback-safe.

---

## 12. Safe Events, Metrics, and Rollback Boundary

Persist internal events through Plan 04's `_visibility=internal` path:

- `authorization_decision`
- `budget_reserved`
- `budget_started`
- `budget_released`
- `budget_denied`
- `obligation_created`
- `obligation_resolved`
- `completion_decision`
- `policy_snapshot`

Payload allowlist:

- Run/call/owner IDs where already approved;
- Manifest/exposure/policy/ledger/evidence digests;
- stable reason/status;
- round/call/active/reserved/pending counts;
- remaining numeric limits and safe durations.

Never include prompts, policy/Skill prose, user input, arguments/results, signatures built from raw values, Artifact/resource content, Provider bodies, URLs, headers, credentials, exception strings/tracebacks, or arbitrary metadata.

Rollback:

- no schema exists to downgrade;
- set `ASSISTANT_MAIN_AGENT_MODE=off` for future Runs;
- in-process Runs retain their frozen Plan 05 policy/limits until completion/cancellation; do not hot-swap to Plan 04 semantics;
- deployment rollback may cancel non-durable active Runs, then deploy the last verified Plan 04 image;
- preserve Run/evaluation/event data;
- OpenClaw remains on its Plan 02 verifier throughout.

After Plan 06 durability exists, older images must not resume unknown policy/build snapshots. That compatibility gate belongs to Plan 06.

---

## 13. Configuration and Hard Ceilings

Add operator settings, all defaulting to the v1 values above and allowed to lower only:

~~~text
ASSISTANT_MAIN_AGENT_MAX_PROVIDER_ROUNDS=8
ASSISTANT_MAIN_AGENT_MAX_CAPABILITY_CALLS=16
ASSISTANT_MAIN_AGENT_MAX_PARALLEL_CALLS=4
ASSISTANT_MAIN_AGENT_MAX_CAPABILITY_DEPTH=4
ASSISTANT_MAIN_AGENT_MAX_AGENT_DEPTH=2
ASSISTANT_MAIN_AGENT_MAX_SAME_READ_SIGNATURE=3
ASSISTANT_MAIN_AGENT_MAX_COMPLETION_TOKENS=4096
ASSISTANT_MAIN_AGENT_MAX_WALL_TIME_MS=120000
ASSISTANT_MAIN_AGENT_MAX_COMPLETION_FOLLOWUPS=2
~~~

Reuse Plan 04 `MAX_ACTIVE_SKILLS`. Settings validation rejects zero/negative/above-hard-ceiling values. Production mode remains `off` until release gates pass.

At Run admission copy normalized values into `RunBudgetLimits`; never call `get_settings()` from ledger transitions or active dispatch.

No migration is created in this plan. Final verification must prove the Alembic head is unchanged from Plan 04.

---

## 14. File Responsibility Map

### Create

- `backend/app/assistant/policy/__init__.py`
- `backend/app/assistant/policy/contracts.py`
- `backend/app/assistant/policy/exposures.py`
- `backend/app/assistant/policy/conflicts.py`
- `backend/app/assistant/policy/evaluator.py`
- `backend/app/assistant/policy/evidence.py`
- `backend/app/assistant/policy/budgets.py`
- `backend/app/assistant/policy/obligations.py`
- `backend/app/assistant/policy/completion.py`
- `backend/app/assistant/policy/recursion.py`
- `backend/app/assistant/policy/runtime.py`
- `backend/tests/test_agent_exposure_index.py`
- `backend/tests/test_agent_skill_conflicts.py`
- `backend/tests/test_agent_policy_matrix.py`
- `backend/tests/test_agent_policy_evidence.py`
- `backend/tests/test_agent_budget_ledger.py`
- `backend/tests/test_agent_budget_scheduler.py`
- `backend/tests/test_agent_obligation_ledger.py`
- `backend/tests/test_agent_completion_guard.py`
- `backend/tests/test_agent_recursion_policy.py`
- `backend/tests/test_agent_policy_runtime.py`

### Modify

- `backend/app/config.py`
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml` if current convention explicitly forwards numeric ceilings.
- final Plan 01 `SkillConflictRuleV1` and terminal-policy publication tests only in the prerequisite implementation; Plan 05 imports those contracts and never defines a second parser/dialect.
- `backend/app/assistant/capabilities/contracts.py` for generic dispatch/frame ports only.
- `backend/app/assistant/capabilities/policy.py` for composite verifier delegation.
- `backend/app/assistant/capabilities/gateway.py` for exact `mark_started/finish/release` boundaries.
- `backend/app/assistant/main_agent/authorization.py`
- `backend/app/assistant/main_agent/manifest_runtime.py`
- `backend/app/assistant/main_agent/control_runtime.py`
- `backend/app/assistant/main_agent/control_capabilities.py`
- `backend/app/assistant/main_agent/service.py`
- `backend/app/assistant/main_agent/events.py`
- `backend/app/assistant/provider_loop/contracts.py` for provider-neutral completion/round-budget ports and the protected completion message; no policy ledger type may enter this module.
- `backend/app/assistant/provider_loop/loop.py`
- `backend/app/assistant/provider_loop/scheduler.py`
- final OpenAI Chat adapter for system-level completion-message encoding.
- Plan 04 evaluation fixtures/results with multi-Skill adversarial cases; bump fixture version/digest intentionally.
- `backend/tests/_bootstrap.py` if settings/policy registries are cached.
- existing Plan 01–04, OpenClaw, Provider multi-call, Assistant stream/memory, and Capability tests.

### Must not modify

- database models or Alembic versions;
- Legacy Router/Supervisor behavior;
- Legacy L2 schema/behavior;
- public Assistant/OpenClaw request/response schemas;
- write/HITL/idempotency paths;
- current Agent adapter to launch another Main Agent/Provider Loop.

---

## 15. Commit and Test Discipline

- Implement in the task order below; each task starts with focused failing tests and ends with a scoped commit.
- Run Plan 01 Manifest/binding fixed vectors after exposure/effective-policy changes.
- Run Plan 02 OpenClaw parity after every verifier/Gateway change.
- Run Plan 03 pairing/multi-call/soft-finalization after every scheduler/loop change.
- Run Plan 04 prompt/activation/runtime/evaluation after every policy integration.
- No default test performs a paid Provider call.
- Use deterministic UUIDs, clocks, token/ID factories, and scripted Providers in tests.
- Run release evidence in clean declared Python 3.11, not only the local unpinned `.venv`.

---

## Task 0: Freeze the Plan 04 Read-Only Baseline and Exact Contracts

- [ ] Record branch/commit, clean/dirty status, Python/dependency versions, sole Alembic head, Plan 04 evaluation dataset/result digests, Profile/Skill/model/probe IDs/digests, and enabled flags.
- [ ] Record and run full Plan 01, the exact reviewed Plan 02A readiness revision with `PLAN_02A_READY=yes`, full Plan 03, and full Plan 04 focused suites plus fixed scripted evaluation. Record Plan 02B as `pending|observing|complete` only; do not wait for observation/cleanup.
- [ ] Inspect exact merged import paths/types for Manifest, binding provenance, minimum evidence bridge, control effect, Run state, scheduler, Gateway, prompt context update, and events.
- [ ] Record Profile budget fields, operator settings, golden Skill policy, visible bindings, and Plan 03 stop/finalization behavior.
- [ ] Prove only `none|read|compute`, `interrupt_mode=none` descriptors are enabled.
- [ ] Prove Plan 01 exports the exact `SkillConflictRuleV1`, canonical fixed vectors, cross-package target resolution, and structural terminal-policy satisfiability rules consumed by Sections 4.4/8.2; stop/amend Plan 01 otherwise.
- [ ] Re-run Plan 04's four repaired invariants: approved 02A/non-blocking 02B gate, independently sourced `allowed_side_effects` plus `grant_source_digest`, post-lineage pending-package activation, and Domain Key exclusivity across base/active/same-batch bindings.
- [ ] Prove Gateway success/take/lineage rejection leaves no active/resource/Tool/context/event residue and that `active` is defined only by the accepted current Manifest.
- [ ] Prove the Plan 03 loop can accept a default-compatible provider-neutral completion port/message member without importing `app.assistant.policy` or changing existing fixed vectors; Plan 05 owns this additive extension, so absence alone is not a blocker.
- [ ] Prove canonical Provider arguments are validated without coercion and can be digest-checked at Gateway start.
- [ ] Confirm one Alembic head and record it; Plan 05 must not change it.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_authorization.py \
  backend/tests/test_main_agent_skill_injection.py \
  backend/tests/test_main_agent_runtime.py \
  backend/tests/test_main_agent_evaluation.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_openclaw_shared_capability_runtime.py -q
~~~

---

## Task 1: Add Exposure, Conflict, Policy, and Budget Contracts

**Files:** policy contracts/exposures/conflicts and exact Plan 01 integration.

- [ ] Write forbidden-extra/frozen/source-mutation/round-trip/canonical digest vectors for every new contract.
- [ ] Derive Main Agent/Skill exposures from exact Manifest refs and binding provenance without changing Manifest v1.
- [ ] Test missing/extra/ambiguous owners, alias confusion, stale bindings/descriptors, and canonical ordering.
- [ ] Implement strict duplicate compatibility and deterministic batch owner choice.
- [ ] Import Plan 01 `SkillConflictRuleV1`/fixed vectors and implement symmetric `excludes|requires|exclusive_group` evaluation; reject any second model, translation dialect, or normalization drift.
- [ ] Normalize Profile/entrypoint/operator/hard ceilings into one `RunBudgetLimits` value.
- [ ] Build base/candidate `EffectiveRunPolicySnapshot` and prove the Manifest `effective_policy_digest` changes only for semantic policy/exposure/limit changes.
- [ ] Re-run Plan 01 fixed Manifest vectors and prove existing empty/base values remain compatible.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_exposure_index.py \
  backend/tests/test_agent_skill_conflicts.py \
  backend/tests/test_agent_policy_matrix.py \
  backend/tests/test_run_manifest_contracts.py -q
~~~

Commit: `feat(ai): freeze source aware agent policy contracts`

---

## Task 2: Implement the Pure Evaluator and Composable Evidence Verifier

- [ ] Build the exact ordered reason-code matrix across scope/surface/exposure/Principal/entrypoint/global/owner/release/availability/recursion.
- [ ] Cover Main Agent vs Skill owner, compatible consumer, unrelated Skills, instruction-only Skill, every side effect, disabled/drifted target, and guessed/stale alias.
- [ ] Prove one read-only Skill does not globally restrict another owner and one broad Skill cannot grant another owner.
- [ ] Generate deterministic decision/evidence digests with no prose/user data.
- [ ] Derive `EffectiveCapabilityGrant.allowed_side_effects` from Plan 04's platform ceiling intersected with immutable entrypoint/global/owner sources before reading descriptor behavior; carry the exact `grant_source_digest` through decision and evidence.
- [ ] Preserve Plan 02/04 negative vectors for descriptor read -> write, classification/ruleset drift, ceiling/author-policy revision drift, missing grant digest, and a verifier that copies `(descriptor.behavior.side_effect,)`; all deny before adapter invocation.
- [ ] Replace Plan 04's minimum verifier behind the same `skill_policy` transport.
- [ ] Preserve one-time call-ID verification and exact Run/conversation/scope binding.
- [ ] Implement composite delegation and run all OpenClaw replay/auth/catalog parity tests.
- [ ] Add redaction corpus tests for decisions/evidence/logs/events/repr.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_policy_matrix.py \
  backend/tests/test_agent_policy_evidence.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_openclaw_shared_capability_runtime.py -q
~~~

Commit: `feat(ai): authorize capability calls by immutable owner`

---

## Task 3: Implement the Serializable Budget Ledger

- [ ] Write boundary tests for every default/hard/Profile/operator limit and prove settings only lower.
- [ ] Write immutable revision/digest/source-mutation tests with deterministic clock.
- [ ] Cover reserve/start/finish/release, duplicate call ID, digest mismatch, policy/input denial before start, failure/timeout/cancellation after start, and unexpected exceptions.
- [ ] Cover Run/owner totals, controls counted, Skill activation, compatible consumer, global/owner repeat signatures, and no budget amplification.
- [ ] Cover monotonic deadline vs UTC clock rollback/advance.
- [ ] Cover Provider rounds, finalization reservation, completion tokens, actual usage overflow, and optional token estimator absence.
- [ ] Implement thread-safe lock/CAS abstraction and safe internal events.
- [ ] Prove serializing/deserializing state preserves semantics and contains no runtime object/secret/data content.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_budget_ledger.py -q
~~~

Commit: `feat(ai): add fixed revisioned agent budgets`

---

## Task 4: Integrate Atomic Reservations with Scheduler and Gateway

- [ ] Add no-op default dispatch/round guards and prove byte/behavior compatibility for Plan 02/03 non-Main-Agent callers.
- [ ] Put `mark_started` at the exact final pre-adapter boundary; test every earlier deny/cancel/drift path releases unstarted.
- [ ] Verify Gateway validated input digest equals the frozen Provider arguments digest before start.
- [ ] Reserve eligible parallel batches all-or-none; test same/different owners, duplicate signatures, partial capacity, contention, cancellation, and deadline.
- [ ] Replan failed batches sequentially and preserve original Provider result order/pairing.
- [ ] Use independent Sessions/Gateways per started parallel call and close all contexts.
- [ ] Count failures/cancellation after start and release all never-started suffix calls.
- [ ] Integrate Provider round/output token accounting without resetting Plan 03 round counters.
- [ ] Run exhaustive multi-call/property seeds with budget invariants.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_budget_scheduler.py \
  backend/tests/test_capability_gateway.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_provider_agent_loop.py -q
~~~

Commit: `feat(ai): reserve agent budgets before capability dispatch`

---

## Task 5: Add Obligation Ledger and Provider Completion Guard

- [ ] Write contract/digest/transition/concurrency tests for all obligation/evidence types.
- [ ] Cover base Main Agent terminal obligation, Skill terminal policy, terminal Capability Result, `needs_followup`, compatible consumer edge, and unrelated-owner rejection.
- [ ] Reject structurally/runtime-unsatisfiable terminal policies before activation: instruction-only + text forbidden, no eligible terminal-output binding, capability-only path with zero owner/Run allowance, unavailable/denied satisfier, and text-only path with no remaining finalization route.
- [ ] Cover direct natural answer, empty text, Assistant prose with Tool Calls, intermediate Tool output, failed/cancelled call, missing Artifact, approval/input/reconciliation fixtures, waiver/failure, and duplicate evidence.
- [ ] Add the provider-neutral default-permissive completion port and prove existing Plan 03 callers are byte/behavior unchanged; add an import-boundary test proving provider-loop modules do not import `app.assistant.policy` or name `BudgetLedgerState`/`ObligationLedgerState` in Protocol signatures.
- [ ] Add the protected completion message and OpenAI system-level encoding; prove all pre-Plan-05 message/transcript fixed vectors stay byte-identical and `runtime_completion` remains distinct from soft-finalization `runtime_instruction` and Plan 04 `runtime_context`.
- [ ] Implement bounded follow-up rounds and exact budget interaction.
- [ ] Prove nonempty model text cannot override pending obligations.
- [ ] Prove finalization/budget exhaustion with non-text-satisfiable obligations fails explicitly.
- [ ] Prove `waiting` without exact pending approval/input plus continuation is a protocol error.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_obligation_ledger.py \
  backend/tests/test_agent_completion_guard.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_messages.py -q
~~~

Commit: `feat(ai): guard completion with revisioned obligations`

---

## Task 6: Upgrade Skill Injection to One Post-Lineage Atomic Policy-State Commit

- [ ] Extend Plan 04 injection tests with conflict rules, compatible/incompatible duplicate exposure, deterministic same-batch owner, owner buckets, obligations, and effective policy digest.
- [ ] Preserve unconditional base-control conflicts and full same-batch preflight; prove only Section 4.3-compatible business duplicates become non-owning consumers and every incompatible duplicate fails before staging.
- [ ] Compute every candidate state inside the extended Plan 04 pending package before mutation; inject failures at each pre-accept step and prove zero candidate-state visibility.
- [ ] Stage first, let Plan 03 validate lineage, then commit Manifest/policy/exposure/owner limits/obligations/protected context/activation projection only inside `ManifestEffectLifecyclePort.accept` under one Run-state revision/lock.
- [ ] Cover take failure, lineage rejection, wrong parent/child/effect digest, cancellation, CAS conflict, lifecycle failure, replay, and discard; none may leave a candidate bucket/obligation/context/active/resource/Tool/event residue.
- [ ] Prove a post-start activation rejection still charges the `skill.inject` call exactly once while discarding every candidate activation delta; never restore spent Run/Main Agent allowance.
- [ ] Prove exact reinjection is a no-op across every state component.
- [ ] Prove activating Skills never changes Run limits/usage/deadline/provider counters.
- [ ] Prove the `skill.inject` call is charged to Main Agent before candidate Skill buckets exist.
- [ ] Prove next-round aliases/tools/context and every future authorization use the candidate snapshot/digest.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_skill_injection.py \
  backend/tests/test_agent_exposure_index.py \
  backend/tests/test_agent_skill_conflicts.py \
  backend/tests/test_agent_budget_ledger.py \
  backend/tests/test_agent_obligation_ledger.py -q
~~~

Commit: `feat(ai): activate skills with atomic policy state`

---

## Task 7: Add Capability Frames and Fail-Closed Agent Recursion

- [ ] Implement process-local frame port and exact frame digests.
- [ ] Check capability/Agent depth and target-version cycles before reservation.
- [ ] Push/pop frames around Gateway adapter invocation under exceptions/cancellation.
- [ ] Prove child calls share Run policy/budget/obligation/cancellation/deadline/event ports and do not create a new base Manifest/budget.
- [ ] Prove child results cannot complete parent obligations without explicit evidence.
- [ ] Use a fake nested Gateway adapter to test depth/cycle/sibling isolation and shared accounting.
- [ ] Run real exact Agent Capability tests at supported depth one.
- [ ] Prove published Agent closures implying nested Agent/Main-Agent restart remain unavailable under Plan 02 classification; do not route them into another Provider Loop.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_recursion_policy.py \
  backend/tests/test_capability_agent_adapter.py \
  backend/tests/test_agent_policy_runtime.py -q
~~~

Commit: `feat(ai): enforce shared agent call frame limits`

---

## Task 8: Integrate Main Agent Runtime, Events, and Adversarial Evaluation

- [ ] Compose one frozen policy snapshot, Budget Ledger, Obligation Ledger, frame port, evaluator/evidence, scheduler guards, Gateway guards, completion guard, and Plan 04 Run state per admitted Run.
- [ ] Apply them to every control and business call; no direct/bypass path.
- [ ] Map safe policy/budget/completion failures to paired Tool Results and explicit Run stop reasons.
- [ ] Emit only allowlisted internal digests/counts/reasons; public SSE remains unchanged except existing safe Skill/runtime events.
- [ ] Add multi-Skill fixtures with unrelated policies, compatible/incompatible duplicate Capability, instruction-only Skill, conflict rules, and different owner budgets.
- [ ] Add adversarial guessed aliases, owner forgery, evidence replay, Skill budget amplification, repeated reads, parallel over-reservation, recursive Agent, prompt-requested policy bypass, premature final text, and pending-obligation cases.
- [ ] Require exact zeros for unauthorized calls, budget overruns, Run-limit increases, false completion, write/unknown exposure, and internal-event leakage.
- [ ] Re-run all Plan 04 quality thresholds; fixture version/digest changes must be intentional and reviewed.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_policy_runtime.py \
  backend/tests/test_main_agent_runtime.py \
  backend/tests/test_main_agent_evaluation.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_openclaw_shared_capability_runtime.py \
  backend/tests/test_assistant_chat_run_stream.py -q
~~~

Commit: `feat(ai): enforce policy budgets and completion in main agent`

---

## Task 9: Final Clean-Environment, Security, Compatibility, and Rollback Verification

- [ ] Run full Plan 01, approved Plan 02A, full Plans 03–05 focused suites and the full backend suite in clean declared Python 3.11; run `pip check`. Record Plan 02B coordination status without making it a gate.
- [ ] Confirm exactly one Alembic head, equal to the recorded Plan 04 head, and no migration/schema diff.
- [ ] Prove `off` does not construct/evaluate Plan 05 state and Legacy output remains unchanged.
- [ ] Exercise `read_only -> off`, cancellation, config change during active Run, and deployment rollback boundary without semantic hot-swap.
- [ ] Run deterministic concurrency/property seeds for reservation, obligation, activation, and frame state; record failing seed.
- [ ] Run redaction corpus through every error/log/event/evidence/decision/ledger/report/repr path.
- [ ] Prove all controls count, every started call is charged once, every unstarted call is released, and every Provider call is paired.
- [ ] Prove every active Skill addition leaves Run limits/deadline/usage unchanged.
- [ ] Prove every evidence grant comes from independent frozen sources with `grant_source_digest`; copy-descriptor and classification/grant substitution fail closed.
- [ ] Prove no activation/policy/budget/obligation/context success state commits before Plan 03 lineage acceptance and lifecycle accept, while a started `skill.inject` remains charged on later rejection.
- [ ] Prove no unsatisfiable blocking terminal obligation can be published/activated and Provider Loop contracts remain free of policy ledger types.
- [ ] Prove natural text cannot complete pending blocking obligations and exhaustion stops honestly.
- [ ] Prove OpenClaw still uses only its Plan 02 grant and public parity tests pass.
- [ ] Prove no `draft|write_local|write_external|unknown`, durable wait, database ledger, recursive Main Agent, or L2 behavior was introduced.
- [ ] Run `git diff --check`, inspect the final diff for unrelated/schema changes, and record policy/evaluation digests and clean-environment results.

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
cd backend && alembic heads
git diff --check
~~~

---

## 16. Release and Rollback Gates

### Gate 05A: merge dark

- Full Plan 01, the exact approved Plan 02A readiness record, full Plan 03, and full Plan 04 start-state regressions pass; Plan 02B status is recorded but non-blocking.
- Mode remains `off`.
- Full Plan 04/Legacy/OpenClaw/evaluation regressions pass.
- Alembic head is unchanged.

### Gate 05B: explicit Shadow evaluation

- Run the fixed dataset with Plan 05 policy/ledger/completion enabled.
- Unauthorized calls, overruns, Run-limit amplification, false completion, write exposure, and event leakage are all zero.
- Plan 04 quality thresholds remain green.

### Gate 05C: read-only M2 enablement

- Enable only after Gate 05B and operator review of defaults/limits/non-durable boundary.
- Monitor safe deny/exhaustion/completion/round/call/latency counts by digest/build, not raw data.
- Do not enable writes or durable waiting.

### Rollback

1. Set mode `off` for future Runs.
2. Finish/cancel process-local active Runs under their frozen Plan 05 snapshot; never reinterpret them with Plan 04 semantics.
3. Deploy the last verified Plan 04 image after active Plan 05 Runs are gone.
4. Preserve immutable Profile/Skill versions, Run events, and evaluation results.
5. No database downgrade is required or allowed because Plan 05 adds no schema.

---

## Plan 05 Exit Criteria

- Every visible Main Agent Capability resolves to one immutable Main Agent/Skill owner and exposure digest without changing Manifest v1.
- Source-aware authorization is evaluated per call across exact Principal, entrypoint, global, owner, release, and target layers.
- Evidence allowed effects come from independent versioned platform/entrypoint/global/owner sources with `grant_source_digest`; descriptor classification is checked against and never copied into the grant.
- Unrelated Skills neither broaden nor globally over-restrict one another.
- Compatible duplicate declarations cannot reassign ownership or multiply allowance; incompatible duplicates/conflicts fail activation atomically.
- Run limits are frozen once; adding Skills never increases totals, rounds, time, depth, tokens, repeats, or concurrency.
- Every started Capability/Provider call is charged exactly once and every unstarted reservation is released.
- Pending blocking obligations prevent false completion; follow-up/finalization/exhaustion have explicit bounded outcomes.
- Every blocking terminal obligation has a publication-time structural and activation-time runtime satisfaction path; unsatisfiable Skills fail before staging.
- Skill policy/budget/obligation/context state becomes visible only through the post-lineage Manifest-effect lifecycle commit; discard leaves no candidate residue and never rewinds an already-started control-call charge.
- The completion extension is provider-neutral and default-compatible; Provider Loop contracts contain no policy-ledger types.
- Capability/Agent frames share the same Run policy/ledgers and cycles/depth are fail-closed; unsupported recursive production Agents remain unavailable.
- OpenClaw and Legacy runtime remain compatible, and the new runtime remains `none|read|compute` only.
- All fixed evaluation/security/concurrency/clean-environment gates pass with no schema change.

## Handoff to Plan 06

Plan 06 must persist, with lossless digests and CAS transitions:

- exact Manifest revisions plus `EffectiveRunPolicySnapshot`/exposure index;
- independently derived `EffectiveCapabilityGrant` values and `grant_source_digest` without rebuilding them from descriptor classification;
- Provider messages including protected context/completion messages;
- `BudgetLedgerState`, reservations, actual usage, monotonic/UTC deadline accounting;
- `ObligationLedgerState` and evidence edges;
- portable call frames and transient Artifact references/content storage;
- Run status/events/checkpoints and worker ownership.

The durable activation transaction must preserve Plan 04/05 ordering: stage candidate package, validate expected parent/lineage, then CAS Manifest/policy/exposure/owner-limit/obligation/context/activation state. Recovery must never promote a merely staged package or roll back the accounting of an already-started `skill.inject` call.

The Plan 06 draft's provisional Provider-message role list (`system|user|assistant|tool`) is not sufficient for this handoff. Its storage contract must either add distinct `runtime_context` and `runtime_completion` roles or use a generic protected-runtime-instruction role with a lossless discriminated payload. It must preserve the original message kind, content/digest, Manifest/policy/obligation revision linkage, ordering, and visibility; downcasting either message to an ordinary `system` row and discarding the discriminator is forbidden.

Plan 06 must not reset budgets on takeover, reinterpret owner/duplicate/conflict/completion semantics, rebuild policy from mutable current/latest state, or resume a Run under an incompatible policy/build revision.
