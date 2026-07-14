# MindAtlas Main Agent, Protected Prompt Layers, and Read-Only Skill Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Start only after full Plan 01 is merged, the reviewed Plan 02A readiness record says `PLAN_02A_READY=yes`, full Plan 03 is merged with its exit criteria passing, and the post-Plan-03 repository has exactly one Alembic head. Plan 02B observation/OpenClaw cleanup is a non-blocking coordination track for this plan.

**Goal:** Put one published Main Agent Profile behind the existing Assistant Chat Run boundary, let the Plan 03 Provider Loop discover and activate one or more published read-only Skills, append protected Skill instructions before the next Provider round, expose only frozen Capabilities owned by active Skills, read immutable resources and transient Artifacts with strict bounds, and prove one reversible read-only golden path against a checked-in Legacy baseline.

**Architecture:** Add `app.assistant.main_agent` as an application layer over the contracts delivered by Plans 01, 02A, and 03. It creates the base `ResolvedRunManifestRevision`, builds protected Provider messages, owns one process-local `MainAgentRunState`, and supplies the Plan 03 loop with four injected ports: dynamic Tool surfaces, protected Manifest-context updates, a Gateway-backed dispatcher, and a generic Manifest-effect lifecycle port. Four code-native Tool Capabilities (`skill.search`, `skill.inject`, `skill.read_resource`, and `artifact.read`) are frozen into the base Manifest and owned by the published Main Agent Profile. `skill.inject` executes through the Plan 02 Gateway and stages one call-scoped activation package; the package's child Manifest is returned through Plan 03's existing `ProviderDispatchResult.next_manifest`, but activation state and public success events become visible only after Plan 03 accepts lineage and invokes the lifecycle port. No second Provider or Tool loop is introduced.

**Prerequisites:**

- Plan 01 immutable Skill/Profile versions, exact binding snapshots, `ResolvedRunManifestRevision` v1, canonical digests, package/resource services, and disabled shadow migration.
- Approved Plan 02A Capability Registry/Gateway, exact binding materialization, versioned side-effect classification, independent grant-source contract, safe results/errors, and `docs/superpowers/evidence/plan-02a-readiness.md` with `PLAN_02A_READY=yes` for the consumed revision.
- Full Plan 03 Provider Loop, alias revisions, frozen Tool surfaces, complete multi-call pairing, current-descriptor verification, model probe history/current pointer, OpenAI Chat adapter, and production-neutral dispatcher contracts.

Plan 02B status (`pending|observing|complete`) is recorded in the implementation handoff only. Production observation, deletion of the legacy OpenClaw dispatcher, and removal of the temporary OpenClaw selector are not start gates and must not be imported or inferred by Plan 04.

---

## 1. Plan Position and Non-Negotiable Boundary

This is Plan 04 of 10 and the first half of milestone M2.

Implemented here:

- Production consumption of one enabled, published default Main Agent Profile.
- A deterministic protected Prompt Builder and a bounded per-Run Catalog snapshot.
- Initial Catalog recall plus `skill.search`, `skill.inject`, `skill.read_resource`, and `artifact.read`.
- Append-only multi-Skill activation, dynamic protected context, and next-round Tool-surface refresh.
- A production `issuer=skill_policy` evidence factory with the minimum M2 read-only grant required by Plan 02.
- A process-local transient Artifact store and oversized-result projection.
- Assistant Chat feature-mode selection, safe event projection, one promoted read-only Skill, and fixed offline evaluation.
- Additive enablement of the Plan 01 aggregate flags; immutable version rows remain immutable.

Not implemented here:

- No `draft`, `write_local`, `write_external`, or `unknown` Capability on the new path.
- No full source-composed policy evaluator, owner budget, repeat ledger, recursion ledger, or Obligation Ledger; Plan 04 implements only the mandatory independent read-only platform/author ceiling, and Plan 05 replaces that minimum evaluator without changing this plan's transport contracts.
- No persistent Manifest, Provider transcript, Artifact, budget, Checkpoint, lease, or recovery. Plan 06 owns durability.
- No approval/input interrupt and no claim that Plan 03's portable continuation survives process loss. Plans 06–07 own that path.
- No L2 read or write for new-runtime Runs. Stable Skill-ID memory migration begins in Plans 06 and 10.
- No public runtime selector, Skill editor, or paid model-probe trigger.
- No removal or semantic rewrite of `SkillRouter`, Supervisor, Legacy Skills, the existing Agent engine, or OpenClaw.

Hard rules:

1. Every Provider Tool Call, including the four Main Agent controls, reaches the Plan 02 Gateway exactly once.
2. Plan 03 remains the only Provider Loop. This plan may add injected provider-neutral round-context and Manifest-effect lifecycle ports; it may not fork the state machine.
3. `ResolvedRunManifestRevision` v1 is not extended or redefined. New ownership and prompt-build views are derived from existing frozen refs/bindings.
4. Production execution uses only published Profile/Skill/Workflow/Agent/Tool references. No Draft-first or current/latest fallback is allowed.
5. The new path remains read/compute-only even if a malformed Profile or Skill declares a broader grant.
6. `off` remains the deployment default, and changing it affects only Runs admitted after the change.

---

## 2. Verified Repository Baseline and Start Conditions

At plan-writing time the repository still has the Legacy implementation, so Task 0 must repeat this audit after full Plan 01, approved Plan 02A, and full Plan 03 merge instead of copying provisional names blindly.

Current concrete anchors:

- `backend/app/assistant/service.py::_run_chat_background` owns Run status, event append, Message persistence, final L1/L2 updates, and the background thread.
- `backend/app/assistant/service.py::_generate_response` constructs `AssistantAgent`, which still runs `SkillRouter -> Supervisor`.
- `backend/app/assistant/run_service.py` has `queued|running|waiting_approval|cancelling|completed|failed|cancelled`; Plan 04 introduces no new status.
- `backend/app/assistant/orchestration/chat_events.py` currently exposes Tool arguments/results directly. The new path must use a separate safe adapter and must not reuse those raw payload methods for internal events.
- `backend/app/assistant/models.py::Message` already has JSON fields for Tool/Skill summaries, and `AssistantChatRunEvent.payload` can carry safe additive event payloads without a schema change.
- Assistant routes currently have no user-authentication dependency. Production Main Agent evidence must therefore use a server-created, non-user-supplied local service Principal; it must not invent a user or tenant identity.
- `backend/app/assistant/orchestration/memory_context.py::build_l0_window` and `AssistantMemoryService.get_l1_summary` are reusable. Legacy L2 is keyed by mutable `skill_name` and is intentionally unavailable to the new path.
- `quick_stats` is backed by a published Workflow that currently uses LLM/read Tool nodes (`get_statistics`, `analyze_activity`, `get_tag_statistics`) and is the preferred golden candidate only if the final Plan 02 recursive descriptor still proves `read|compute` and `interrupt_mode=none`.

Task 0 must first verify the exact Plan 01, approved Plan 02A, and full Plan 03 revisions. It records Plan 02B status separately and must not fail merely because production observation or OpenClaw legacy cleanup is incomplete. Stop and amend the relevant prerequisite plan before implementation if any of these post-merge contracts are missing:

- Plan 01 Profile snapshot cannot express the locked context/output budget keys used below.
- Plan 01 `ResolvedMainAgentRef`, `ResolvedSkillRef`, base/append helpers, or fixed Manifest vectors do not exist.
- Plan 02 cannot materialize a code-native frozen Tool binding owned by a Main Agent Profile without mutable Tool lookup.
- Plan 02 Gateway cannot accept a server-created `skill_policy` verifier while preserving the OpenClaw verifier.
- Plan 03 cannot add a protected round-context message without breaking transcript pairing or existing fixed vectors.
- Plan 03 probe evidence cannot prove the exact current model/config/adapter/build combination.

Do not hide a prerequisite failure behind an adapter that reconstructs missing data from mutable rows.

---

## 3. Runtime Topology and Dependency Direction

~~~mermaid
flowchart LR
    CHAT["Assistant Chat Run"] --> SELECT["Runtime admission\noff | shadow | read_only"]
    SELECT -->|legacy| LEGACY["SkillRouter + Supervisor"]
    SELECT -->|new| APP["MainAgentService"]
    APP --> PROFILE["Published Main Agent Profile"]
    APP --> MANIFEST["Base Manifest + process-local Run state"]
    APP --> PROMPT["Protected Prompt Builder"]
    APP --> LOOP["Plan 03 ProviderAgentLoop"]
    LOOP --> CTX["RoundContextProvider"]
    LOOP --> TOOLS["Plan 03 ToolsProvider"]
    LOOP --> DISP["MainAgentToolDispatcher"]
    LOOP --> LIFE["ManifestEffectLifecyclePort"]
    DISP --> GW["Plan 02 CapabilityGateway"]
    GW --> CTRL["Main Agent control Tool adapter"]
    GW --> BUSINESS["Tool / Workflow / Agent adapters"]
    CTRL --> EFFECT["Pending activation package"]
    EFFECT --> DISP
    DISP -->|"proposed child"| LOOP
    LOOP -->|"lineage accepted: commit<br/>otherwise: discard"| LIFE
    LIFE --> EFFECT
    DISP --> LOOP
~~~

Dependency rules:

1. `app.assistant.main_agent` may import Plan 01 domain/Skill services, Plan 02 Capability contracts/runtime, Plan 03 Provider Loop contracts, generic Assistant memory/event/run utilities, and `ai_registry` exact-model resolution.
2. `app.assistant.provider_loop` may import only provider/domain contracts and injected Protocols. It must not import Main Agent, Skill ORM/service, Assistant service, or OpenClaw.
3. `app.assistant.capabilities` may receive an additive generic code-native control port but must not import `app.assistant.main_agent`.
4. The Main Agent dispatcher and Manifest-effect lifecycle implementation may compose the Gateway and process-local Run state. They may not execute a business Tool/Workflow/Agent directly.
5. The Legacy runtime does not import Main Agent modules. `off` should return before constructing any Main Agent service, Provider adapter, Catalog, Profile, probe, or transient store.
6. No SQLAlchemy `Session`, Provider client, callback, lock, cursor object, or Artifact bytes enter a frozen domain contract.

---

## 4. Locked Admission, Feature Mode, and Fallback Semantics

### 4.1 Feature modes

`ASSISTANT_MAIN_AGENT_MODE` accepts exactly:

| Mode | Production Assistant Chat | Explicit evaluation entry | User-visible new output |
|---|---|---|---|
| `off` | Legacy only | disabled unless test directly injects the service | no |
| `shadow` | Legacy only | new runtime allowed with fixture/evaluation scope | no |
| `read_only` | new runtime after admission preflight; safe pre-output fallback may use Legacy | allowed | yes |

`shadow` never runs a second model against a live production chat automatically. It is an explicit fixed-dataset or operator test mode, does not write Message/L1/L2/title, and cannot execute a descriptor above `read`/`compute`.

There is no request-body/header query that lets a caller select a runtime. Selection comes from server configuration plus the injected evaluation scope.

### 4.2 Admission preflight

Resolve all of the following before the first new-runtime Provider request:

1. Mode permits the requested execution kind.
2. The server created the exact local Assistant Principal; no Principal fields came from HTTP JSON.
3. The default Main Agent aggregate exists, is `runtime_enabled=true`, has an owned published version, and its snapshot supports `assistant_chat`.
4. The Profile declares all four required controls and no unresolved/unsupported control.
5. The current Assistant component model resolves to one exact Plan 01 `ModelRef`/`ProviderRef`.
6. Its current Plan 03 probe is `passed`, belongs to that model, and matches probe-contract, model/credential revisions, config digest, protocol, adapter revision, and build policy.
7. Every Profile-required capability is `passed`; `failed` and `not_observed` both fail a required feature.
8. Prompt, catalog, request, and configured limits normalize successfully.
9. The checked-in Main Agent ceiling fixed vector passes, and a base Manifest/minimum policy digest covering its exact revision/digest plus the published Profile exposure can be built without mutable/latest refs.

Preflight opens no long transaction, decrypts no credential until adapter construction is authorized, and makes no Provider request.

### 4.3 Fallback state machine

Track a process-local `MainAgentFallbackState`:

~~~python
class MainAgentFallbackState(FrozenContract):
    provider_requests_started: int
    capability_dispatches_started: int
    strongest_started_side_effect: SideEffectClass | None
    pending_interrupt: bool
    uncertain_result: bool
    user_output_started: bool
~~~

Automatic fallback is allowed only when all are true:

- Profile fallback policy allows Legacy before side effects.
- no `draft|write_local|write_external|unknown` call started;
- no pending interrupt, continuation, unknown, or reconciliation state exists;
- no user-visible new-runtime output was emitted;
- the error is in the explicit fallback-safe set (`profile_unavailable`, `model_ineligible`, `adapter_unavailable_before_request`, `catalog_unavailable`, or a retry-safe Provider failure before visible output);
- cancellation was not requested.

Read calls may have completed, but fallback remains disallowed after visible output to avoid mixing two answers. Authorization/protocol/digest/tamper errors are fail-closed, not fallback-safe.

Every selection/fallback event records only Run ID, source/target runtime, stable reason code, counts, and strongest side-effect class. It never records prompt, arguments, results, or exception text.

---

## 5. Base Manifest and Main Agent Control Ownership

### 5.1 Preserve Manifest v1

Use Plan 01's exact `ResolvedRunManifestRevision` and append helpers. Do not add `prompt`, `catalog`, `owner`, or Artifact fields to it.

The base revision freezes:

- one `ResolvedMainAgentRef` for the exact published Profile version/content digest;
- exact current `ProviderRef` and `ModelRef` including current probe evidence;
- four code-native `ResolvedCapabilityRef` values for the required controls;
- an empty `active_skills` tuple;
- an initially allocated or next-child `provider_aliases` tuple through Plan 03 only;
- the minimum Plan 04 effective-policy digest covering the exact published Profile exposure and `MAIN_AGENT_READ_ONLY_EFFECT_CEILING` revision/digest. An activated Skill's immutable version/content/policy identity already enters the child Manifest and is included separately in each call's `grant_source_digest`; do not mutate `effective_policy_digest` in place.

### 5.2 Additive provenance values

Plan 02 intentionally left Main Agent production authorization for a later plan. Extend existing enums additively without changing existing serialized values or binding digests:

~~~python
FrozenBindingProvenance.origin += "main_agent_profile"
CapabilityOwnerRef.owner_kind += "main_agent"
~~~

For every base control binding:

- `origin=main_agent_profile`;
- `owner_version_id=<published profile version id>`;
- owner kind is `main_agent`;
- owner ID is the stable Profile ID/key;
- target identity is code-native and build-revision pinned;
- input/output Schemas, descriptor, executable revision, and binding digest are frozen exactly like other system Tools.

Existing OpenClaw, Skill, system, and test provenance/evidence remain byte-compatible.

### 5.3 Required controls and classification

| Domain Key | Side effect | Parallel safe | Manifest mutating | Owner |
|---|---:|---:|---:|---|
| `skill.search` | `read` | true | no | Main Agent Profile version |
| `skill.inject` | `none` | false | yes | Main Agent Profile version |
| `skill.read_resource` | `read` | true | no | Main Agent Profile version |
| `artifact.read` | `read` | true | no | Main Agent Profile version |

All four calls count as normal Capability calls once Plan 05 installs the ledger. No name-based free-call exception is allowed.

`human.request_input` is not exposed in Plan 04.

### 5.4 Gateway-backed Manifest effects

The Plan 02 Gateway returns `CapabilityResult`, while Plan 03's dispatcher separately returns `next_manifest`. Preserve both contracts and close the activation-visibility gap with a call-scoped pending package plus one generic lifecycle port. The pending package is process-local and non-serializable; it is not a new Manifest v1 field or a durability claim.

~~~python
class PendingManifestEffect(FrozenContract):
    call_id: str
    expected_parent_revision: int
    expected_parent_digest: str
    proposed_manifest: ResolvedRunManifestRevision
    effect_digest: str


class MainAgentControlCallPort(Protocol):
    def execute(
        self,
        *,
        call_id: str,
        capability_key: str,
        validated_input: dict[str, JsonValue],
    ) -> CapabilityResult: ...

    def take_manifest_effect(
        self,
        *,
        call_id: str,
    ) -> PendingManifestEffect | None: ...


class ManifestEffectLifecyclePort(Protocol):
    def accept(
        self,
        *,
        call_id: str,
        current_manifest: ResolvedRunManifestRevision,
        proposed_manifest: ResolvedRunManifestRevision,
    ) -> None: ...

    def discard(self, *, call_id: str, reason_code: str) -> None: ...
~~~

Flow for a control call:

1. Dispatcher binds one call-local control port to the exact `current_manifest` and Run state.
2. Gateway resolves, validates, authorizes, and executes the code-native control Tool.
3. `skill.inject` validates the full batch and stages at most one `PendingSkillActivationPackage` keyed by call ID. That private package contains the `PendingManifestEffect`, exact activation projection, and buffered safe post-commit events. It changes no current Manifest, active-Skill projection, resource authorization, or public event stream.
4. Gateway returns one normalized but provisional `CapabilityResult`. The dispatcher calls `take_manifest_effect` exactly once, transfers lifecycle ownership to the scheduler, and returns `proposed_manifest` as `ProviderDispatchResult.next_manifest`; absent effect means unchanged Manifest.
5. Plan 03 validates the returned child against `request.current_manifest` with its existing append-only lineage rules **before** adopting it, pairing a successful Tool Result, resolving a new Tool surface, or emitting activation success.
6. After lineage acceptance, Plan 03 calls the injected `ManifestEffectLifecyclePort.accept` with the exact call, parent, and child already present in Plan 03 contracts. The Main Agent implementation looks up the transferred package by call ID, recomputes its private `effect_digest`, rechecks its exact parent/child against those arguments, and, under the `MainAgentRunState` lock/CAS boundary, atomically advances the current Manifest and activation projection and moves the buffered event batch to post-commit delivery. No digest field is added to `ProviderDispatchResult`. Only then may the scheduler adopt the child and pair the provisional success result.
7. If Gateway execution, `take_manifest_effect`, lineage validation, pre-accept cancellation, or any other pre-accept scheduler check fails, Plan 03 calls `discard`; the package, activation projection delta, and buffered success events are cleared, and the Provider receives a safe failed/protocol Tool Result rather than provisional success. `accept` performs every fallible comparison before mutation and is failure-atomic. After it succeeds, a later Provider/event/Run failure cannot discard or roll back the accepted Manifest.
8. A lifecycle operation is single-use. A package cannot be accepted twice, replayed under another call ID, accepted from another parent, or discarded after acceptance. A non-Main-Agent caller receives a no-op lifecycle implementation so existing Plan 03 behavior remains byte-compatible; Main Agent admission/composition must reject the no-op implementation so a pending activation can never bypass the commit boundary.

`active` has one authoritative v1 meaning: the exact Skill Version occurs in the currently accepted and validated Manifest. A process-local projection, staged package, Tool Result, or event row is never sufficient. `skill.read_resource`, protected context, Tool-surface construction, and authorization all check that Manifest membership. Event delivery happens only after acceptance and never authorizes anything; because Plan 04 is explicitly non-durable, an event-sink failure records a safe diagnostic and cannot roll back or replace the accepted Manifest. Controls are non-parallel when they may mutate Manifest state.

---

## 6. Protected Prompt and Dynamic Context Contract

### 6.1 Why Plan 03 needs one additive port

Plan 03 rebuilds Tool surfaces per round but its initial contract carries static `initial_messages`. A Tool Result cannot safely carry the newly active `SKILL.md` as ordinary Tool data. Add a provider-neutral protected context-update message and injected port; do not modify existing message shapes or soft-finalization vectors.

~~~python
class ProviderContextUpdateMessage(FrozenContract):
    role: Literal["runtime_context"] = "runtime_context"
    context_type: Literal["main_agent_manifest"] = "main_agent_manifest"
    locale: str
    manifest_revision: int
    manifest_digest: str
    prompt_build_digest: str
    content: str


class RoundContextResolution(FrozenContract):
    manifest_revision: int
    manifest_digest: str
    applied_skill_version_ids: tuple[UUID, ...]
    messages: tuple[ProviderContextUpdateMessage, ...]


class RoundContextProvider(Protocol):
    def resolve(
        self,
        *,
        manifest: ResolvedRunManifestRevision,
        already_applied_skill_version_ids: tuple[UUID, ...],
        execution_scope: ProviderExecutionScope,
        locale: str,
    ) -> RoundContextResolution: ...
~~~

Add a no-op default implementation so all Plan 03 tests and non-Main-Agent callers retain existing behavior.

Per Provider request, ordering is locked:

1. resolve the Tool surface and accept any append-only alias child;
2. call `RoundContextProvider` with that exact round Manifest;
3. append only validated, not-yet-applied protected Skill context messages;
4. freeze the round messages and surface;
5. call the Provider adapter.

The OpenAI Chat adapter maps `runtime_context` to a system-level message. It never maps it to user/tool content, exposes it as final text, or logs its content. Existing messages remain append-only; prior Provider rounds are not rewritten.

Reinjection and alias-only Manifest children add no duplicate Skill message. A context-update message covers the exact newly activated Skill versions and a bounded current Manifest/obligation summary. Plan 05 adds obligations through the same protected port.

### 6.2 Logical prompt layers

The Prompt Builder owns labeled `PromptLayer` values with this priority:

1. Platform safety and non-overridable runtime rules.
2. Exact published Main Agent Profile base prompt and response style.
3. Entrypoint, local service Principal, locale, and current minimum Effective Policy summary.
4. Exact active published Skill instructions, each labeled by canonical name, version ID, content digest, and version digest.
5. Current Manifest identity, active Skill identities, task state, and (from Plan 05) pending-obligation summaries.
6. L1 summary and bounded L0 history.
7. Current user message.
8. Provider-paired Capability summaries and Artifact references.

Layers 1–5 are rendered only to `ProviderSystemMessage` or `ProviderContextUpdateMessage`. User content, Skill resources, Artifact content, and Capability results are always delimited data at their own layer and cannot introduce another system/runtime message.

The initial Provider messages are:

1. one protected `ProviderSystemMessage` for layers 1–3 plus bounded Catalog summaries and base Manifest identity;
2. bounded prior `ProviderUserMessage`/`ProviderAssistantMessage` history;
3. the current `ProviderUserMessage`.

Skill bodies enter through protected context updates after activation. They never appear in Catalog records, `skill.inject` Tool Results, public events, logs, or safe build reports.

### 6.3 Exact v1 context budgets

Normalize these keys from the final Plan 01 Profile schema. Missing values use the defaults; values above the hard ceiling are rejected at Profile runtime validation rather than silently enlarged.

| Budget | Default | Hard ceiling | Behavior on overflow |
|---|---:|---:|---|
| platform + Profile chars | 12,000 | 24,000 | Profile ineligible; never truncate safety/Profile silently |
| all active Skill instruction chars | 24,000 | 32,000 | reject activation atomically |
| one Skill instruction chars | 12,000 | 16,000 | Skill ineligible for activation |
| initial Catalog chars | 8,000 | 16,000 | reduce lowest-ranked records, keep at least one if eligible |
| L1 + L0 history chars | 24,000 | 48,000 | trim oldest L0 pairs, then L1 tail by deterministic rule |
| current user chars | 12,000 | 16,000 | reject; current HTTP contract is already stricter |
| one inline Tool/result payload bytes | 16 KiB | 64 KiB | summarize/reference or fail closed |
| Tool summaries + Artifact refs chars | 24,000 | 48,000 | Artifactize full value, retain bounded summary/ref |
| total protected + initial context chars | 72,000 | 96,000 | deterministic reduction; fail if mandatory layers still exceed |
| maximum active Skills | 4 | 8 | reject activation atomically |

Provider/model context evidence may lower these values. If no reliable tokenizer/context-window metadata exists, enforce the character/byte limits and optional Profile output reserve only; do not claim an exact token guarantee. Plan 05 adds actual usage accounting.

Deterministic reduction order:

1. omit lowest-ranked initial Catalog records;
2. remove oldest complete L0 user/assistant pairs;
3. truncate L1 at a deterministic Unicode boundary with an explicit marker;
4. reduce optional bounded Tool summaries while retaining Artifact refs/digests;
5. fail `prompt_budget_exceeded` if mandatory platform/Profile/current-user/active-Skill identity and full active `SKILL.md` bodies still do not fit.

Active Skill bodies are never partially truncated. Resource content is not part of the Skill body and is loaded only through bounded calls.

### 6.4 Prompt build report

Return a safe report containing layer kind, source IDs/digests, included character/byte counts, omitted record counts, truncation reason codes, total size, and `prompt_build_digest`. It contains no prompt text, user text, Skill body, memory text, Tool result, resource bytes, or secret.

The digest covers the exact rendered protected/user message bytes and source digests, but logs/events expose only the digest and counts.

---

## 7. Conversation and Memory Boundary

- Reuse the existing conversation and Run IDs; do not create a second user/assistant Message pair.
- Load prior `user|assistant` messages in stable `(created_at, id)` order, excluding the current placeholder assistant Message and applying `build_l0_window` bounds.
- Read the current L1 summary once at Run admission. A later L1 update cannot alter the active Run.
- Do not read Legacy L2 by `skill_name`, alias, Provider alias, or display name.
- Do not write L2 for a Main Agent Run.
- Do not copy active Skill instructions, resources, raw Capability output, transient Artifact content, failed/waiting state, or model inference into memory.
- Persist final assistant Message content and then schedule the existing L1 update only after a user-visible `completed` result.
- `shadow`, failed, cancelled, fallback-discarded, and process-lost new runs do not update L1/L2 or title from new-runtime output.
- The Legacy path retains its current memory behavior while `off` or after a permitted fallback.

---

## 8. Catalog Snapshot and Recall v1

### 8.1 Eligible Catalog record

At Run start, build one immutable process-local `SkillCatalogSnapshot`. A record is eligible only when all are true:

- package `catalog_enabled=true` at snapshot time;
- package has an owned `published_version_id` whose row is `version_source=publish`;
- Profile catalog scope includes the package;
- published binding/resource indexes reconstruct and verify their Plan 01 digests;
- every bound descriptor is available, `interrupt_mode=none`, and its classified effect is admitted by the Section 13 platform ceiling intersected with that published Skill Version's immutable Plan 01 author declaration;
- `routing.conflict_rules` is empty in Plan 04. Plan 01 deliberately assigns semantic enforcement to Plan 05, so Plan 04 fails closed instead of pretending to understand nonempty rules;
- required standard compatibility/locale/entrypoint constraints pass;
- full Skill instructions fit the per-Skill hard limit, though the body is not loaded into the summary.

Instruction-only published Skills are eligible if all other rules pass.

~~~python
class SkillCatalogRecord(FrozenContract):
    package_id: UUID
    version_id: UUID
    canonical_name: str
    display_name: str | None
    description: str
    locale: str
    aliases: tuple[str, ...]
    include_examples: tuple[str, ...]
    exclude_examples: tuple[str, ...]
    content_digest: str
    version_digest: str
    resource_index_digest: str
    binding_set_digest: str
~~~

Provider-visible projections omit package ID, full aliases when over budget, full Capability lists, policies, bindings, resource contents, and instruction bodies.

### 8.2 Snapshot consistency

- Query package/version summary projections and all required published binding indexes in bounded select-in batches; no resource body or N+1 binding query is allowed.
- Compute `catalog_digest` from sorted eligible records and Profile scope digest.
- Do not retain ORM objects or a Session in the snapshot.
- Plan 04 uses a per-Run snapshot and no cross-Run cache. This avoids invalidation races before a durable catalog revision exists.
- Immediately before activation, re-lock candidate package rows in sorted package-ID order and recheck `catalog_enabled`, current published pointer, ownership, and version digest. If anything changed, fail `catalog_changed`; never silently select a newer version.
- Disabling a package affects new activation and new Runs. An already active exact Skill remains frozen for the current Run; live target availability can still deny execution at the Gateway.

### 8.3 Deterministic lexical recall

Implement one pure scorer:

1. Normalize query/fields with NFKC, trim, Unicode casefold, and reject control/NUL.
2. Tokenize ASCII letter/digit runs plus CJK unigrams and adjacent bigrams; deduplicate for overlap and retain frequencies for ranking.
3. Score canonical name, aliases, include examples, description, and display name with checked-in weights.
4. Treat an exclude example as a hard exclusion only for exact normalized match or high query-token coverage (`>=0.80`, at least two nontrivial tokens); otherwise apply the checked-in penalty.
5. Sort by score descending, canonical UTF-8 name ascending, then version UUID bytes ascending.
6. Apply exclusion and Profile/entrypoint gates before Top-K.

Lock weights/thresholds in tests; do not rely on database collation, insertion order, Python `hash()`, locale-sensitive lowercasing, or Provider output.

Optional semantic recall is an injected `CatalogSemanticRecallPort`. It may return ranked eligible version IDs only. Merge lexical and semantic rankings with RRF (`k=60`), then apply the same gates and deterministic tie-break. Missing configuration, timeout, invalid IDs, or LightRAG failure falls back to lexical results and emits a safe metric; correctness never depends on it.

Initial Top-K defaults to 8 and is capped at 20. `skill.search` uses the same engine.

### 8.4 Opaque search cursors

Store pagination state in the process-local Run state under a random opaque cursor ID. The state binds catalog digest, query digest, locale, next offset, and expiry/count. Cursor values carry no encoded query/data, are single-Run, capped, and expire at Run end. Inject a deterministic token factory in tests.

`skill.search` is marked parallel-safe only because its bookkeeping is an atomic Run-state operation: union returned version IDs into the disclosed-version set and allocate/store that call's cursor state under the `MainAgentRunState` lock/CAS boundary. The union is idempotent, each call owns a distinct cursor record, and ranking is computed from the immutable per-Run Catalog snapshot. Tests must force overlapping searches and prove that no disclosed ID or cursor is lost and that completion order does not change either call's response. If the implementation cannot provide this atomic boundary, set the frozen descriptor's `parallel_safe` field to false before enabling the runtime.

---

## 9. Control Capability Contracts

All Schemas use Plan 01 normalization and Plan 02 runtime validation, reject unknown keys, and have object roots.

### 9.1 `skill.search`

Input:

~~~json
{
  "query": "string, 1..1000 characters",
  "limit": "integer, 1..20, default Profile Top-K",
  "cursor": "optional opaque string"
}
~~~

Output contains `catalogDigest`, bounded summary records, `nextCursor`, and safe exclusion/fallback counts. It never returns full instructions, Capability Schemas, policies, resource bytes, or database-only audit metadata. Returned version IDs are added to the Run's disclosed-version set.

### 9.2 `skill.inject`

Input supports one atomic batch:

~~~json
{
  "skills": [
    {"versionId": "uuid"},
    {"name": "canonical name or exact alias"}
  ]
}
~~~

Rules:

- 1–4 items per call, no duplicate selector, and at least one selector field per item.
- A version ID must have been disclosed by initial recall or `skill.search` in this Run.
- A name/alias resolves only inside the Run's Catalog snapshot and freezes that snapshot's version ID.
- The whole batch succeeds or fails; no partial activation.
- Reinjecting the same exact version is an idempotent no-op.
- A different version of an already-active canonical package is rejected.
- Total active Skills and aggregate instruction budget are checked before append.

Output contains only activation status, canonical names, exact version/content/version/resource-index digests, bounded resource index metadata, and the proposed Manifest revision/digest. It is provisional inside the dispatcher and enters the Provider transcript only after lifecycle acceptance; a rejected child is paired as a safe failed/protocol result. Full instructions and binding Schemas stay protected.

### 9.3 `skill.read_resource`

Input:

~~~json
{
  "skillVersionId": "uuid",
  "path": "exact normalized resource path",
  "offset": "integer >= 0, default 0",
  "limit": "integer 1..65536, default configured chunk"
}
~~~

Only an active exact Skill Version is readable. Reuse Plan 01 path/media/digest validation. Response contains path, media type, total size, offset, returned bytes, EOF, resource digest, encoding (`utf-8` or `base64`), and bounded content. Invalid UTF-8 uses base64. `scripts/` are returned only as inert bytes/text; the runtime has no execute/import/subprocess path.

### 9.4 `artifact.read`

Input uses Run-scoped opaque Artifact ID plus offset/limit. Response mirrors the resource chunk contract and includes content digest/media type. Cross-Run IDs, missing/evicted IDs, digest mismatch, unsupported media, and limit overflow fail safely.

### 9.5 Stable control errors

Use bounded reason codes, including:

- `catalog_unavailable`
- `catalog_changed`
- `catalog_cursor_invalid`
- `skill_not_disclosed`
- `skill_not_cataloged`
- `skill_version_changed`
- `skill_already_active`
- `skill_version_conflict`
- `skill_policy_unsupported`
- `skill_capability_conflict`
- `skill_context_budget_exceeded`
- `active_skill_limit_exceeded`
- `resource_not_active`
- `resource_not_found`
- `resource_range_invalid`
- `artifact_not_found`
- `artifact_range_invalid`
- `control_effect_protocol_error`

Provider-facing text is generic and localized. Errors/events never include requested resource content, full paths beyond an approved bounded safe path, package instructions, arguments, or arbitrary exceptions.

---

## 10. Atomic Skill Activation and Dynamic Tool Surface

For one `skill.inject` call:

1. Validate input through the Gateway.
2. Resolve selectors only against the Run Catalog snapshot.
3. Sort and lock candidate package aggregates by package UUID.
4. Recheck live aggregate flag/current published pointer and immutable version ownership/digests.
5. Reconstruct binding snapshots/dependency closure and ask the Plan 02 Registry for exact descriptors.
6. Require an available target, `interrupt_mode=none`, compatible Profile/model/entrypoint, and a classified side effect admitted by the independent Main Agent platform/author ceiling defined in Section 13.
7. Reject any nonempty Plan 01 conflict rule until Plan 05.
8. Normalize exact already-active versions to idempotent no-ops, then build one complete Domain Key ownership map before constructing a child. The reserved set is every Capability in `current_manifest.capabilities`, including all Main Agent base controls and every active Skill binding. Add all distinct candidate bindings to a separate batch map. Reject `skill_capability_conflict` if a candidate key collides with a base control, with a binding owned by another active Skill, or with a different candidate Skill in the same batch—even when target or binding digests are byte-identical. A repeated selector is invalid input; an exact already-active version remains the only idempotent exception. Plan 05 introduces any explicit compatible-consumer rule.
9. Perform the ownership-map check, active-Skill count check, and aggregate instruction-budget check across the entire batch before staging a Manifest child, activation projection, result, or event. Any failure leaves all state byte-identical.
10. Append non-no-op candidates with Plan 01 helpers, preserving existing refs/model/provider/policy and canonical ordering. A batch containing only exact reinjections returns the unchanged Manifest and stages no activation package or success event.
11. Do not allocate aliases in the control handler. Plan 03 `ToolsProvider` allocates missing aliases as a later append-only child before the next Provider round.
12. Stage one `PendingSkillActivationPackage` under the call ID and return its proposed child/provisional result. Do not advance `MainAgentRunState`, authorize resources, or emit public activation/Manifest success here.
13. Let Plan 03 validate lineage and invoke the Section 5.4 lifecycle port. Only lifecycle acceptance commits the current Manifest and process-local activation projection; rejection discards the whole package.

Database locks end before the next Provider request. There is no database write other than safe existing Run events and the separately authorized rollout operation.

Next round invariants:

- `RoundContextProvider` appends the exact new Skill instructions once.
- `ToolsProvider` exposes the four controls plus only Profile-declared controls and business bindings belonging to active exact Skill versions.
- Base-control Domain Keys and existing active-binding keys are reserved before every activation; a batch can never report success and defer its first ownership collision to the next `ToolsProvider.resolve` call.
- Every alias maps to one exact binding; inactive/unpublished/current-latest guesses cannot resolve.
- Gateway evidence names the exact Main Agent or Skill owner derived from binding provenance.
- Same-message siblings remain bound to the original surface even if an earlier sequential `skill.inject` advances the current Manifest.

---

## 11. Transient Artifact and Result Projection

Use an injected process-local `TransientArtifactStore` implementing the existing Plan 02 `ArtifactRef` identity:

- random opaque 128-bit-or-stronger ID from an injectable token source;
- exact media type and SHA-256 content digest;
- text, JSON canonical bytes, and bounded binary resource chunks only;
- maximum 1 MiB per Artifact;
- maximum 5 MiB and 64 Artifacts per Run;
- deterministic LRU rejection/eviction policy locked in tests; never evict an Artifact still referenced by the active transcript silently;
- cleared at terminal Run or process loss.

Before a business `CapabilityResult` becomes a Provider Tool Result:

1. Preserve already-returned `ArtifactRef` values.
2. Canonicalize structured JSON once.
3. If inline user/structured content fits the threshold, project it normally.
4. Otherwise store the full safe normalized value, return a bounded summary plus `ArtifactRef`, and require `artifact.read` for chunks.
5. If content exceeds Artifact/run limits, return `result_too_large` without logging or partially embedding the value.

Skill resource rows remain their own immutable source and are not copied into the transient store merely to support chunking.

No Artifact bytes enter Message JSON summaries, L1/L2, events, logs, metrics, Prompt Build reports, or fallback data.

---

## 12. Model Eligibility and Exact Adapter Construction

Resolve the current `AiComponentBinding(component="assistant")` once at admission and freeze its exact model/credential refs. The Profile selects requirements, not a mutable alternate model.

Required evidence for the initial Profile:

- streaming transport/text: passed;
- Tool Calling: passed;
- JSON Schema arguments: passed;
- nonempty stable Tool Call IDs: passed;
- Tool Result continuation: passed;
- tools-disabled finalization: passed;
- multi-Tool Call: passed when Profile requires it; otherwise the scheduler remains correct but the Profile cannot claim that feature.

The probe must be the model's current pointer and match current revisions/config/protocol/adapter/build contract. A stale historical passing row is ineligible. `partial`, `failed`, and required `not_observed` are ineligible.

Only after this check may the injected Plan 03 client factory decrypt the exact credential slot and construct the adapter. Recheck model/credential revisions immediately before construction; drift fails before network I/O.

No automatic live probe occurs. Operators use Plan 03's explicit, default-disabled probe API separately.

---

## 13. Minimum Plan 04 Authorization Bridge

Plan 04 must make `entrypoint=main_agent` real without pre-implementing Plan 05.

Freeze one checked-in platform ceiling independent from Capability classification:

~~~python
class MainAgentEffectCeiling(FrozenContract):
    schema_version: Literal[1] = 1
    ceiling_key: Literal["main_agent_read_only"] = "main_agent_read_only"
    revision: Literal["plan04-v1"] = "plan04-v1"
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none"], ...]
    ceiling_digest: str
~~~

`MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects` is exactly the Plan 02 lattice prefix through `read`, in canonical lattice order: `("none", "compute", "read")`. Its interrupt tuple is exactly `("none",)`. The digest covers every field except itself and has a checked-in fixed vector. No descriptor, classifier result, availability value, or mutable catalog row participates in constructing this ceiling.

The per-owner independent grant source is derived as follows:

- A base control uses the platform prefix plus exact exposure in the published Main Agent Profile version and current Manifest.
- An active Skill binding uses the same platform prefix, exact current-Manifest ownership, and the published Skill Version's Plan 01 author declaration. `compute` and `read` are retained only when independently declared by that version; `none` is admitted by the explicit Main Agent entrypoint rule. Author declarations never classify a target and never expand the platform prefix.
- The ordered effective tuple is computed from those independent inputs before looking at `CapabilityDescriptor.behavior`. Missing ownership, missing/invalid author policy, unsupported declaration, or an empty effective grant for the actual call denies.
- `grant_source_digest` covers the platform ceiling revision/digest, exact Profile or Skill Version/content/policy digest, owner reference, capability key, and exact Manifest membership/binding identity. It does **not** cover or copy descriptor side effect, behavior digest, classification digest, or mutable availability. Those are verified separately by Plan 03 and Plan 02 policy.

Create a request-scoped `MainAgentMinimumAuthorizationBridge`:

- server-created `CapabilityPrincipal(principal_type="service", principal_id="local-assistant", authenticated=true)`;
- exact `ProviderExecutionScope` bound to Run/conversation and `tenant_scope_id=None` under the repository's explicit current single-tenant contract;
- `issuer=skill_policy`, `entrypoint=main_agent`;
- owner derived only from frozen control binding provenance or active Skill binding provenance;
- exact call/binding/resolution/dependency/descriptor/Manifest/surface equality;
- one-time call-ID verification through the Plan 02 verifier path;
- `allowed_side_effects` and `grant_source_digest` from the independent ceiling/owner derivation above;
- policy compares the current classified descriptor effect with that tuple and denies `unknown`, any effect outside the tuple, or any interrupt mode outside the ceiling;
- deny missing/inactive/unowned/stale/unknown bindings;
- no global permission union across Skills.

Evidence is issued immediately before dispatch and cannot be replayed for a sibling. It must never synthesize `allowed_side_effects=(descriptor.behavior.side_effect,)`; a classifier change cannot widen the independent grant. Add negative tests that (a) mutate a descriptor from `read` to `write_local` while preserving a read-only grant, (b) flip the classification/ruleset revision, (c) change the ceiling or author-policy revision, and (d) substitute a verifier that copies descriptor behavior. Each case must deny before adapter invocation, and the ceiling/author change must produce a new `grant_source_digest`.

The existing OpenClaw verifier remains selected for `issuer=openclaw_bridge`; neither path is a fallback for the other. Plan 05 replaces the minimum Main Agent evaluator but preserves this evidence transport and OpenClaw delegation.

---

## 14. Assistant Run, SSE, and Persistence Integration

### 14.1 Runtime selection boundary

Refactor `AssistantService._run_chat_background` only enough to inject a runtime runner:

~~~python
class AssistantRuntimeRunner(Protocol):
    def run(self, request: AssistantRuntimeRequest) -> AssistantRuntimeResult: ...
~~~

Legacy runner wraps the existing `_generate_response` path unchanged. Main Agent runner wraps `MainAgentService`. Runtime selection happens once after the existing Run/Message pair exists and before output generation.

### 14.2 User-visible and internal events

Do not send raw Plan 02/03 events through `ChatEventAdapter.on_tool_call_*`, because those methods currently include arguments/results.

Add a safe Main Agent event adapter with explicit payload models. Public events may include:

- `runtime_selected`
- `skill_search`
- `skill_activation_end`
- `manifest_revision`
- existing `content_delta`, `run_status`, and `message_end`

Payloads contain IDs/digests, canonical/display names, count, status, and stable reason code only. `skill_activation_end(status=success)` and its Manifest revision are produced from the same accepted pending package and emitted only after lifecycle acceptance. Pre-accept `skill_activation_start` is internal-only if implemented and cannot say that a Skill is active. A failed/discarded package may emit a bounded failure status, but never a success Manifest digest.

Internal diagnostic events add `_visibility="internal"`. Update `stream_run` so it advances its cursor across internal rows but does not yield them. Existing rows without the marker remain public. Tests cover reconnect, sequence gaps, and terminal exit.

### 14.3 Output and Message persistence

- Provider round text that accompanies Tool Calls is provisional and never forwarded as final user output.
- Buffer the final no-Tool round until the Plan 03 result is terminal; then emit bounded chunks as `content_delta` and persist the final text.
- Persist Tool/Skill summaries using safe projections only. No arguments, full results, prompts, resource/Artifact bytes, or authorization evidence.
- A completed new Run updates Message, title (through the existing bounded title path), conversation timestamp, and L1 in the established order.
- Failure/cancellation persists safe status/error code and never writes L1/L2 from partial new output.
- `off` and permitted fallback call the existing Legacy runner and preserve its events/Message behavior.

### 14.4 Honest non-durability

Plan 04 keeps `MainAgentRunState`, applied-context IDs, cursor state, pending control effects, Provider transcript, and Artifacts in memory. It can report a detected missing/evicted in-process state as `non_durable_state_lost`, but it cannot guarantee recovery or even classification after a whole process dies.

Do not add a multi-worker startup scanner without a lease. Operational documentation must state that Plan 04 read-only mode is not restart-resumable and should remain off unless this limitation is accepted. Plan 06 replaces this boundary.

---

## 15. Database Migration, Configuration, and Rollback

### 15.1 Migration

Plan 01 intentionally adds named checks forcing:

- `assistant_skill_package.catalog_enabled=false`;
- `assistant_main_agent_profile.runtime_enabled=false`.

Generate a real Alembic revision after reading the sole post-Plan-03 head. Do not use a hand-selected/provisional revision ID in this document.

Upgrade:

- drop only the two final Plan 01 disabled-only checks by their actual recorded names;
- retain `NOT NULL`, server defaults false, immutable-version triggers, ownership guards, FK restrictions, and all digest/version checks;
- add no flag to Skill/Profile version rows;
- change no existing row value.

Downgrade:

- refuse while any package has `catalog_enabled=true` or any Profile has `runtime_enabled=true`;
- require the operator disable both aggregate flags first;
- then restore the exact disabled-only checks;
- delete no version/history/evaluation data.

Use the guarded PostgreSQL 15 fixture established by Plans 01, 02A, and 03 for parent -> head -> parent -> head and default/data-preservation tests.

### 15.2 Settings

Add and validate:

~~~text
ASSISTANT_MAIN_AGENT_MODE=off
ASSISTANT_MAIN_AGENT_CATALOG_TOP_K=8
ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS=4
ASSISTANT_MAIN_AGENT_RESOURCE_CHUNK_BYTES=16384
ASSISTANT_MAIN_AGENT_RESOURCE_MAX_BYTES_PER_CALL=65536
ASSISTANT_MAIN_AGENT_ARTIFACT_MAX_BYTES=1048576
ASSISTANT_MAIN_AGENT_ARTIFACT_RUN_MAX_BYTES=5242880
ASSISTANT_MAIN_AGENT_INLINE_RESULT_BYTES=16384
~~~

Settings may lower approved Profile/hard limits; they cannot raise the checked-in hard ceilings. Invalid values fail startup with a safe field name. Production examples remain `off`.

`deploy/docker-compose.yml` must pass the feature mode explicitly from deploy env; optional numeric defaults may remain application defaults if current deploy conventions prefer that. Never place API keys or probe output in examples.

---

## 16. File Responsibility Map

### Create

- `backend/app/assistant/main_agent/__init__.py`
- `backend/app/assistant/main_agent/contracts.py`
- `backend/app/assistant/main_agent/prompt_builder.py`
- `backend/app/assistant/main_agent/catalog.py`
- `backend/app/assistant/main_agent/model_eligibility.py`
- `backend/app/assistant/main_agent/authorization.py`
- `backend/app/assistant/main_agent/artifacts.py`
- `backend/app/assistant/main_agent/control_runtime.py`
- `backend/app/assistant/main_agent/control_capabilities.py`
- `backend/app/assistant/main_agent/manifest_runtime.py`
- `backend/app/assistant/main_agent/events.py`
- `backend/app/assistant/main_agent/service.py`
- `backend/app/assistant/main_agent/rollout.py`
- `backend/app/assistant/main_agent/evaluation.py`
- `backend/app/assistant/main_agent/golden_path.py`
- `backend/scripts/assistant_main_agent_rollout.py`
- generated `backend/alembic/versions/<revision>_enable_main_agent_catalog_flags.py`
- `backend/tests/fixtures/main_agent_eval/read_only_v1.jsonl`
- `backend/tests/fixtures/main_agent_eval/legacy_read_only_v1.jsonl`
- `backend/tests/test_main_agent_prompt_builder.py`
- `backend/tests/test_main_agent_catalog.py`
- `backend/tests/test_main_agent_controls.py`
- `backend/tests/test_main_agent_skill_injection.py`
- `backend/tests/test_main_agent_resources.py`
- `backend/tests/test_main_agent_artifacts.py`
- `backend/tests/test_main_agent_model_eligibility.py`
- `backend/tests/test_main_agent_authorization.py`
- `backend/tests/test_main_agent_runtime.py`
- `backend/tests/test_main_agent_rollout.py`
- `backend/tests/test_main_agent_evaluation.py`
- `backend/tests/test_main_agent_postgres_migration.py`

### Modify

- `backend/app/config.py`
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml`
- final Plan 01 Profile schema only if Task 0 proves the locked nested budget keys were not reserved; amend Plan 01 tests/fixed vectors before continuing.
- `backend/app/assistant/skills/service.py` and Legacy Adapter module for explicit cutover ownership.
- `backend/app/assistant/capabilities/contracts.py` for additive Main Agent provenance and generic control port.
- `backend/app/assistant/capabilities/classification.py` for exhaustive control classifications.
- `backend/app/assistant/capabilities/registry.py` / Tool adapter composition for exact code-native control bindings.
- `backend/app/assistant/capabilities/policy.py` to delegate `skill_policy` evidence while preserving OpenClaw.
- `backend/app/assistant/capabilities/gateway.py` only as needed for the injected generic control port; public Gateway result remains `CapabilityResult`.
- `backend/app/assistant/provider_loop/contracts.py` for `ProviderContextUpdateMessage`, `RoundContextProvider`, and the generic process-local `ManifestEffectLifecyclePort`.
- `backend/app/assistant/provider_loop/loop.py` for the additive per-round context hook and post-lineage Manifest-effect accept/discard boundary.
- `backend/app/assistant/provider_loop/openai_chat.py` (or final adapter path) for system-level context encoding.
- `backend/app/assistant/service.py`
- `backend/app/assistant/run_service.py` only for reusable safe event helpers if needed; no status/schema change.
- `backend/app/assistant/orchestration/chat_events.py` only to keep Legacy behavior and add no raw new-runtime path.
- `backend/tests/_bootstrap.py` if new settings/parser registries are cached.
- Plan 01–03 focused tests and existing Assistant Run/stream/memory tests for compatibility assertions.
- `.github/workflows/ci.yml` only if the final PostgreSQL job does not include the new migration test.

### Must not modify

- Legacy `SkillRouter` decision behavior or Supervisor graph.
- current Legacy Agent-loop semantics in `workflow/engine/agent_execution_core.py`.
- OpenClaw public API/catalog/auth behavior.
- legacy Skill single-target database constraints.
- `AssistantChatRun` status schema or durable Checkpoint schema.
- frontend Skill management/editor.

---

## 17. Commit and Test Discipline

- Work task-by-task; make tests fail for the intended reason before implementation.
- Each task ends with focused tests and one scoped commit. Do not combine the migration, prompt/loop extension, activation, rollout, and evaluation into one unreviewable commit.
- Run Plan 01 fixed vectors whenever Manifest/binding/provenance code changes.
- Run Plan 02 OpenClaw tests whenever policy/Gateway/control classification changes.
- Run Plan 03 message/transcript/multi-call tests whenever the round-context hook changes.
- Use clean Python 3.11 installed from declared requirements for release evidence. The local unpinned `.venv` is not compatibility proof.
- No test may make a paid Provider call by default.
- Never mark checkboxes complete or replace commands with recorded outputs until execution actually occurs.

---

## Task 0: Reconfirm Plan 01, Approved Plan 02A, and Full Plan 03; Freeze the Legacy Baseline

**Files:** read full Plan 01, the approved Plan 02A contracts/readiness evidence, full Plan 03, plus current Assistant runtime/golden Workflow.

- [ ] Record branch, commit, Python version, dependency lock versions, sole Alembic head, and clean/dirty status.
- [ ] Record the exact consumed Plan 01 revision/fixed vectors, Plan 02A revision plus reviewed `PLAN_02A_READY=yes` evidence, and full Plan 03 revision/exit evidence; run those suites in the declared environment.
- [ ] Record Plan 02B status as `pending|observing|complete` for coordination only. Do not wait for OpenClaw production observation, temporary-selector removal, or legacy-dispatch deletion, and do not claim them complete from Plan 04 evidence.
- [ ] Inspect final contract names and record exact import paths for Profile snapshot, Manifest helpers, frozen bindings/descriptors, Gateway, Provider messages/loop/dispatcher, probe evidence, and PostgreSQL helper.
- [ ] Prove existing fixed vectors contain explicit empty aliases and that no Plan 04 field requires changing Manifest v1.
- [ ] Prove the final Profile schema accepts the locked context/output keys; stop and amend Plan 01 if it does not.
- [ ] Verify the Main Agent control binding can be represented exactly; stop and amend Plan 02 if a mutable Tool lookup would be required.
- [ ] Run current Assistant routing, stream/reconnect/cancel, Message persistence, title, L0/L1/L2, approval, and no-outer-fallback tests.
- [ ] Materialize the `quick-stats` shadow package and recursively classify its exact current published Workflow. Record descriptor/binding/dependency/model digests.
- [ ] If it is not available `read|compute` with `interrupt_mode=none`, inspect `periodic-review`; if neither qualifies, stop and choose/fix a genuinely read-only golden path instead of weakening classification.
- [ ] Build the fixed JSONL evaluation set and record Legacy Router/output decisions before any production switch. Redact/private-data review is mandatory.

Run at minimum:

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_router_decision.py \
  backend/tests/test_skill_router_prompt_format.py \
  backend/tests/test_assistant_chat_run_stream.py \
  backend/tests/test_assistant_chat_stop.py \
  backend/tests/test_assistant_service_no_outer_fallback.py \
  backend/tests/test_assistant_memory_l0.py \
  backend/tests/test_assistant_memory_l1_service.py \
  backend/tests/test_assistant_memory_l2_service.py -q
~~~

Expected: full Plan 01, approved Plan 02A, full Plan 03, and the Legacy baseline pass, or deviations are fixed in the owning prerequisite plan before Task 1. Plan 02B incompleteness by itself is not a deviation.

---

## Task 1: Enable Aggregate Flags and Add Safe Configuration

**Files:** configuration/env/deploy, generated Alembic revision, migration tests.

- [ ] Write failing Settings enum/range/hard-ceiling tests.
- [ ] Write failing PostgreSQL upgrade/default/downgrade-refusal/data-preservation/one-head tests.
- [ ] Generate the revision from the actual sole head and review the whole file.
- [ ] Drop only the two disabled-only checks; preserve every immutable/version/ownership guard.
- [ ] Implement guarded downgrade and explicit operator error.
- [ ] Add Settings and environment examples with production mode `off`.
- [ ] Prove invalid mode/numeric values fail before application startup and contain no secret values.
- [ ] Run parent -> head -> parent -> head in disposable PostgreSQL 15.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_postgres_migration.py \
  backend/tests/test_config.py -q
~~~

Commit: `feat(ai): enable guarded main agent aggregate flags`

---

## Task 2: Add Protected Round Context Without Forking Plan 03

**Files:** final Provider Loop contracts/loop/OpenAI adapter and tests.

- [ ] Write failing frozen-contract tests for `ProviderContextUpdateMessage`, unknown fields, size bounds, and no effect on existing Plan 03 message vectors.
- [ ] Write a scripted two-round scenario: initial controls -> generic Manifest-changing control -> context update and new Tool surface before round 2.
- [ ] Assert Tool-surface resolution happens before context resolution and both use the exact accepted round Manifest.
- [ ] Assert alias-only revisions do not duplicate Skill instructions.
- [ ] Assert context updates are appended once, transcript pairing remains closed, and resume/tamper validation covers their digest.
- [ ] Add a no-op default port and prove every pre-Plan-04 caller produces byte-identical messages/results.
- [ ] Map the new role to a system-level OpenAI Chat message; reject/log no content and never expose it as final text.
- [ ] Run all Plan 03 message, transcript, loop, multi-call, resume, soft-finalization, and adapter tests.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_messages.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_openai_chat_provider_adapter.py -q
~~~

Commit: `feat(ai): add protected provider round context`

---

## Task 3: Implement Prompt Builder, Conversation Context, and Budget Reports

**Files:** Main Agent contracts/prompt builder plus memory/context tests.

- [ ] Write failing layer-order, delimiter, prompt-injection, Unicode, locale, deterministic-output, source-mutation, and digest tests.
- [ ] Write every budget boundary and deterministic reduction-order test.
- [ ] Prove overlarge Profile/current-user/active-Skill mandatory content fails instead of silently disappearing.
- [ ] Load stable L0 history excluding the placeholder Message and read L1 once.
- [ ] Prove no Legacy L2 query/write occurs and no active Skill text enters L1/L2.
- [ ] Produce initial Provider messages, incremental protected Skill messages, and safe build reports.
- [ ] Test raw keys, encrypted values, headers, resource bytes, arbitrary exceptions, Tool inputs/results, and prompt text against reports/logs/events/repr.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_prompt_builder.py \
  backend/tests/test_assistant_memory_l0.py \
  backend/tests/test_assistant_memory_l1_service.py -q
~~~

Commit: `feat(ai): build protected main agent prompts`

---

## Task 4: Build the Immutable Per-Run Catalog and Recall Engine

**Files:** Catalog module, Skill service projections, fixtures/tests.

- [ ] Write eligibility tests for flags, owned published pointer, version source, Profile scope, unavailable/write/unknown/interrupt bindings, nonempty conflict rules, instruction-only Skills, locale, and instruction limits.
- [ ] Write N+1 query-count and 10k-record bounded scale tests without resource-body loads.
- [ ] Lock Unicode tokenizer, weights, exclude threshold, sort/tie behavior, and catalog digest vectors.
- [ ] Implement lexical recall and optional RRF semantic port with deterministic lexical fallback.
- [ ] Implement Run-scoped disclosed-version tracking and opaque cursor state as one lock/CAS-protected, idempotent update per search call.
- [ ] Force overlapping `skill.search` calls and prove no lost disclosed IDs/cursors and response determinism independent of completion order; otherwise freeze the descriptor as non-parallel.
- [ ] Prove a changed flag/published pointer between search and inject yields `catalog_changed`, not current/latest selection.
- [ ] Prove DB/locale/insertion order and cache state do not alter results.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_main_agent_catalog.py -q
~~~

Commit: `feat(ai): add bounded published skill recall`

---

## Task 5: Register Main Agent Controls and Minimum Authorization

**Files:** Capability provenance/classification/registry/policy/Gateway, Main Agent authorization/control modules.

- [ ] Write failing Plan 01/02 fixed-vector compatibility tests for additive provenance enums.
- [ ] Freeze exact code-native bindings/Schemas/descriptors for all four controls.
- [ ] Update the exhaustive Tool-classification test; no unclassified control is allowed.
- [ ] Implement the generic call-local control port without importing Main Agent into Capability Runtime.
- [ ] Implement and fixed-vector-test `MAIN_AGENT_READ_ONLY_EFFECT_CEILING=(none, compute, read)` in Plan 02 lattice order; derive active-Skill grants from its intersection with the immutable Plan 01 author declaration, never from descriptor behavior.
- [ ] Implement server-created local Principal, exact scope, one-time `skill_policy` evidence, and `grant_source_digest` over the independent ceiling plus exact owner/Manifest exposure.
- [ ] Add a table-driven matrix for owner kind, entrypoint, issuer, call replay, wrong Run/conversation, inactive binding, guessed alias, stale Manifest/surface, every side effect, and target unavailability.
- [ ] Mutate read -> write classification under an unchanged read ceiling, bump the ruleset, bump the ceiling/author policy, and inject a verifier that copies `descriptor.behavior.side_effect`; prove every invalid combination denies before adapter invocation and grant/classification digests cannot substitute for each other.
- [ ] Prove `openclaw_bridge` still delegates to its existing verifier and Main Agent evidence cannot authorize OpenClaw or vice versa.
- [ ] Prove denied/failed control calls leave no pending Manifest effect.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_controls.py \
  backend/tests/test_main_agent_authorization.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_openclaw_shared_capability_runtime.py -q
~~~

Commit: `feat(ai): authorize gateway backed main agent controls`

---

## Task 6: Implement Atomic Skill Injection and Dynamic Capabilities

**Files:** Manifest/control runtime, dispatcher/ToolsProvider composition, tests.

- [ ] Write a scripted Provider scenario: base controls -> `skill.inject` -> append-only Skill child -> alias child -> protected instructions/new business tools next round -> exact read Capability -> final text.
- [ ] Cover one/multi selector batches, disclosure rules, alias/name lookup, duplicate selector, idempotent reinjection, different-version rejection, active limit, context limit, catalog race, unavailable descriptor, write/unknown, nonempty conflict rules, and duplicate Domain Key.
- [ ] Build the pre-staging ownership map from all current Manifest capabilities plus the complete candidate batch. Prove collisions with each of the four base controls, another active Skill, and two candidates in the same batch all return `skill_capability_conflict` before any child/projection/event is staged; identical binding digests do not bypass ownership.
- [ ] Lock package rows in UUID order and verify no transaction remains open during Provider/network work.
- [ ] Reconstruct exact bindings/dependencies with Plan 02 and append only through Plan 01 helpers.
- [ ] Add the generic no-op `ManifestEffectLifecyclePort` to Plan 03 composition and prove existing dispatchers/results remain byte-identical; prove Main Agent composition rejects the no-op port before the first Provider request.
- [ ] Stage/transfer the call-ID `PendingSkillActivationPackage` exactly once; test replay, wrong parent/child/effect digest, source-state mutation, `take_manifest_effect` failure, cancellation, and discard.
- [ ] Prove Gateway success alone changes no active state and emits no public success event. Accept only after Plan 03 lineage validation; atomically install the current Manifest plus activation projection, then deliver the package's safe post-commit events. A lineage/lifecycle reject must pair a safe failed result and leave Manifest, projection, resource access, protected context, Tool surface, and events unchanged.
- [ ] Define active membership solely from the accepted current Manifest and prove a staged package, provisional Tool Result, activation projection entry, or forged event cannot authorize `skill.read_resource` or a business binding.
- [ ] Prove aliases are allocated only by Plan 03 and all old aliases/refs stay unchanged.
- [ ] Prove guessed inactive aliases and direct Gateway requests cannot dispatch.
- [ ] Prove same-message siblings retain the surface that exposed them while sequential Manifest changes accumulate.
- [ ] Emit safe activation/Manifest success events only from an accepted package, without instructions, arguments, schemas, or resource bodies; simulate event-sink failure and prove it never creates authorization truth or rewinds the accepted Manifest.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_skill_injection.py \
  backend/tests/test_provider_aliases.py \
  backend/tests/test_provider_multi_tool_calls.py -q
~~~

Commit: `feat(ai): activate immutable skills in the provider loop`

---

## Task 7: Add Resource Reads, Artifacts, and Bounded Tool Projection

**Files:** resources/artifacts/control/result projection and tests.

- [ ] Write active-version ownership, safe path, nonexistent path, offset/EOF, limit, UTF-8/base64, media type, digest, binary, cross-Run, scripts-nonexecution, and mutation tests.
- [ ] Reuse the exact Plan 01 resource service; never read package directories at runtime.
- [ ] Implement the transient store with injected ID/clock, byte/count limits, digest checks, cleanup, and referenced-item eviction safety.
- [ ] Project oversized safe Capability results to bounded summaries plus existing `ArtifactRef`.
- [ ] Prove Artifact/resource content is absent from Message summaries, memory, events, reports, logs, and fallback state.
- [ ] Simulate missing process-local state and return `non_durable_state_lost` without fake resume.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_resources.py \
  backend/tests/test_main_agent_artifacts.py -q
~~~

Commit: `feat(ai): add bounded skill resources and transient artifacts`

---

## Task 8: Integrate Main Agent with Assistant Runs and Safe Events

**Files:** model eligibility, Main Agent service/events, Assistant service/run integration, existing stream tests.

- [ ] Write every admission failure: missing/disabled/unpublished Profile, unsupported entrypoint/control, missing model binding, stale/wrong/partial/failed/not-observed probe, model/config drift, invalid budgets, and adapter failure before/after request.
- [ ] Write `off`, explicit `shadow`, `read_only`, cancellation, final text, Tool-call prose suppression, Message/L1/L2/title, and fallback-state tests.
- [ ] Resolve/decrypt the adapter only after exact eligibility checks.
- [ ] Compose base Manifest, Prompt Builder, round-context port, ToolsProvider, dispatcher, independent Gateway/session factory, cancellation, and safe event sinks.
- [ ] Refactor runtime selection once at Run start; do not call Main Agent constructors in `off`.
- [ ] Buffer provisional Provider text and emit only terminal final text.
- [ ] Add internal-event filtering while preserving replay cursor/terminal semantics.
- [ ] Prove no public `skill_activation_end`/Manifest success event precedes lifecycle acceptance; staging progress, if retained, is internal and cannot claim that a Skill is active.
- [ ] Prove Shadow output is discarded and causes no Message/memory/title/business write.
- [ ] Prove allowed fallback occurs before visible output and disallowed fallback fails in place.
- [ ] Keep new-runtime L2 reads/writes at zero.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_model_eligibility.py \
  backend/tests/test_main_agent_runtime.py \
  backend/tests/test_assistant_chat_run_service.py \
  backend/tests/test_assistant_chat_run_stream.py \
  backend/tests/test_assistant_chat_stop.py \
  backend/tests/test_assistant_service_no_outer_fallback.py \
  backend/tests/test_assistant_service_l1_summary.py \
  backend/tests/test_assistant_service_l2_memory.py -q
~~~

Commit: `feat(ai): gate the read only main agent runtime`

---

## Task 9: Promote One Read-Only Golden Skill and Profile Reversibly

**Files:** rollout/golden path, Legacy Adapter ownership rule, operator script/tests.

- [ ] Implement `plan|enable|disable` operations with dry-run output and expected Profile/Skill version IDs/digests.
- [ ] Lock the golden shadow package, verify canonical/Legacy alias, exact current version, complete read-only descriptor closure, empty conflicts, and evaluation eligibility.
- [ ] Reuse/append an owned draft and create a distinct published cutover version through Plan 01 service APIs; never mutate a shadow published row.
- [ ] Set aggregate `migration_state=cutover`, point to the exact new publish, and enable Catalog only after all checks pass.
- [ ] Update Legacy Adapter sync to skip `cutover` aggregates; leave Legacy `AssistantSkill` enabled and unchanged.
- [ ] Publish a new default Main Agent Profile version containing the reviewed base prompt, required controls, budgets, requirements, Catalog scope, and fallback policy.
- [ ] Require a current matching passing model probe, then set only the Profile aggregate `runtime_enabled=true`.
- [ ] Make rerun idempotent for the same expected digests and fail on drift/concurrent change.
- [ ] `disable` sets both aggregate flags false without deleting/repointing history or changing the Legacy Skill.
- [ ] Prove no other package becomes catalog-visible.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_rollout.py \
  backend/tests/test_agent_skill_legacy_adapter.py \
  backend/tests/test_system_skill_workflow_refs.py -q
~~~

Commit: `feat(ai): promote the read only main agent golden path`

---

## Task 10: Add Fixed Evaluation and Release Gate

**Files:** JSONL datasets, evaluation module/script tests.

Dataset v1 must include at least 100 reviewed cases across zh/en:

- positive golden-Skill cases with paraphrase/time-range variants;
- direct-answer/general-chat negatives;
- write/draft requests forbidden on the new path;
- ambiguous golden-vs-other-Skill cases;
- exclude examples and alias cases;
- multi-Skill cases using instruction-only/read-only fixtures;
- prompt/alias/resource injection attempts;
- expected completion/no-completion notes.

Each row contains stable case ID, locale, prompt, expected execution kind, acceptable/forbidden Skill names, acceptable Capability paths, direct-answer allowance, Legacy recorded decision/success, and manual notes. It contains no production/private data.

Metric definitions:

- `recall_at_8`: positive cases where any acceptable Skill appears in initial/search Top-8 divided by positive cases.
- `false_injection_rate`: cases with any activated Skill outside the acceptable set or inside forbidden set divided by all evaluated cases.
- `direct_answer_accuracy`: direct-answer cases with zero Skill activation and acceptable terminal result divided by direct-answer cases.
- `capability_path_accuracy`: positive execution cases whose ordered calls match at least one acceptable path divided by positive execution cases.
- `completion_success`: cases satisfying the fixture completion contract without pending state divided by all cases; compare the same IDs to recorded Legacy success.
- unauthorized/broader-side-effect count: exact count, must be zero.

Release thresholds:

- recall@8 `>= 0.90`;
- false injection `<= 0.05`;
- direct-answer accuracy `>= 0.90`;
- acceptable Capability path `>= 0.85`;
- new completion success no worse than Legacy by more than `0.02`;
- unauthorized calls, write/unknown exposure, prompt-layer violations, and false completion `= 0`.

- [ ] Build deterministic CI evaluation using real Catalog/Prompt/policy code plus scripted Provider/Gateway fixtures.
- [ ] Store Legacy baseline separately and join by case ID; CI never calls the Legacy model.
- [ ] Add optional explicit live evaluation that records model/probe/Profile/Skill/prompt-build digests, rounds, usage, latency, and safe outcomes; it is never required by default CI.
- [ ] Fail on dataset too small, duplicate IDs, denominator zero, missing manual fields, digest drift without version bump, or any locked threshold miss.
- [ ] Produce a safe JSON/Markdown report artifact with counts/digests only.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_main_agent_evaluation.py -q
backend/.venv/bin/python -m app.assistant.main_agent.evaluation \
  --dataset backend/tests/fixtures/main_agent_eval/read_only_v1.jsonl \
  --legacy backend/tests/fixtures/main_agent_eval/legacy_read_only_v1.jsonl \
  --scripted
~~~

Commit: `test(ai): gate the read only main agent golden path`

---

## Task 11: Final Clean-Environment, Migration, Security, and Rollback Verification

- [ ] Run full Plan 01, approved Plan 02A, full Plan 03, and Plan 04 focused suites plus all existing Assistant/OpenClaw/Workflow/Agent tests; record Plan 02B status separately without turning it into a release prerequisite.
- [ ] Run the full backend suite in clean Python 3.11 installed from declared requirements; run `pip check`.
- [ ] Run guarded PostgreSQL parent -> head -> parent -> head and confirm exactly one Alembic head.
- [ ] Run `off -> shadow evaluation -> read_only -> off` and prove no data deletion or Legacy behavior drift.
- [ ] Prove only the selected golden package is enabled and every new visible descriptor is `none|read|compute`, available, and non-interrupting.
- [ ] Prove full Skill bodies appear only in protected context after activation and never in initial Catalog/Tool Result/events/logs.
- [ ] Prove every control/business Tool Call passes one Gateway and one exact verifier; guessed aliases and stale surfaces fail.
- [ ] Prove Main Agent evidence derives its allowed effects from the checked-in ceiling and immutable author/owner sources, carries `grant_source_digest`, and fails closed when classification or either independent source changes.
- [ ] Prove base-control, active-Skill, and same-batch Domain Key collisions fail before staging, and exact-version reinjection alone remains idempotent.
- [ ] Prove `take_manifest_effect`/lineage/lifecycle failure leaves no accepted child, activation projection, resource access, Tool exposure, protected instructions, or public success event.
- [ ] Prove no L2 read/write, Draft/current-latest resolution, paid probe, automatic Shadow double-run, or durable-resume claim exists.
- [ ] Run a redaction corpus through errors/logs/events/reports/Message summaries and inspect all records.
- [ ] Simulate cancellation, Provider failure, transient state loss, config drift, catalog race, event-sink failure, and safe/unsafe fallback boundaries.
- [ ] Run `git diff --check`, inspect the final diff for unrelated changes, and record generated migration ID/head and evaluation digest/results in the implementation handoff.

Recommended final commands:

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
cd backend && alembic heads
git diff --check
~~~

---

## 18. Release, Enablement, and Rollback Gates

### Gate 04A: merge dark

- Full Plan 01 and Plan 03 exit evidence pass, and the exact consumed Plan 02A readiness record says `PLAN_02A_READY=yes`; Plan 02B status is recorded but non-blocking.
- Code/migration merged with `ASSISTANT_MAIN_AGENT_MODE=off`.
- Both aggregate flags remain false.
- Legacy Assistant and OpenClaw regressions pass.

### Gate 04B: explicit Shadow evaluation

- Publish/enable the reviewed Profile and golden package only in the target environment.
- Keep production mode `shadow`; run only the explicit fixed evaluation entry.
- All locked thresholds and redaction tests pass.

### Gate 04C: read-only golden enablement

- Current model probe is matching/passed.
- PostgreSQL migration and one-head gates pass.
- Operators accept the explicit non-durable process-loss limitation.
- Set mode `read_only` only for the intended environment and monitor safe success/fallback/round/call/latency counts.

### Code/config rollback

1. Set mode `off` for future Runs.
2. Let in-process read-only Runs finish or cancel them; do not change semantics mid-Run.
3. Run rollout `disable` to set Profile/package aggregate flags false if desired.
4. Deploy the last verified Legacy image.
5. Preserve immutable Profile/Skill/evaluation history.

### Database downgrade

Only after both aggregate flag classes are false, downgrade to the exact Plan 03 parent. The downgrade restores disabled-only checks and removes no history. If flags are still true, it must refuse.

---

## Plan 04 Exit Criteria

- One published Main Agent Profile can directly answer or activate one/multiple eligible read-only Skills without a separate Router model inside the new path.
- Skill activation creates append-only Manifest revisions, protected next-round instructions, and dynamic exact Tool surfaces only after Plan 03 lineage and lifecycle acceptance; rejected pending packages leave no active/event/resource-visible residue.
- Only active exact Skill bindings and declared Main Agent controls are visible and dispatchable.
- Every call passes the shared Gateway with exact evidence derived from an independent versioned Main Agent/author ceiling and carries `grant_source_digest`; descriptor classification is checked against rather than copied into that grant, and OpenClaw remains isolated and compatible.
- Base controls, active bindings, and all members of one candidate batch participate in pre-staging Domain Key exclusivity.
- Prompt precedence, budgets, Catalog recall, resources, Artifacts, fallback, and event redaction are deterministic and tested.
- One read-only golden path meets all fixed Legacy-comparison thresholds.
- `off` restores the Legacy production path without deleting new data.
- The plan makes no write/HITL/durability/L2 claims and records process-loss limitations honestly.

## Handoff to Plan 05

Plan 05 receives:

- a working read-only Main Agent with exact Main Agent/Skill binding provenance;
- a derived, ownerable Tool surface without changing Manifest v1;
- a call-ID-safe `skill_policy` authorization bridge with independent platform/author ceilings and `grant_source_digest`;
- fixed Run admission/Profile/Catalog state and dynamic protected context;
- Gateway-backed control execution, pre-staging Domain Key exclusivity, and lineage-accepted pending-package activation through the generic lifecycle port;
- a scripted/fixed evaluation harness.

Plan 05 must replace the minimum grant with source-composed per-call policy, enforce structured conflict rules, freeze full Run/owner budgets, add atomic reservation/repeat/depth ledgers and completion obligations, and preserve all Plan 04 transport, prompt, Manifest, fallback, evaluation, and read-only boundaries.
