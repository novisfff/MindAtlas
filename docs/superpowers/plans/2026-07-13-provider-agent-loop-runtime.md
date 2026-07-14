# MindAtlas Provider Agent Loop Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Start after Plan 01's fixed Manifest/model contracts pass and the reviewed Plan 02A readiness record says `PLAN_02A_READY=yes`. Plan 03 does not wait for Plan 02B production observation or OpenClaw legacy cleanup.

**Goal:** Add a provider-neutral, dynamic, multi-round Agent Loop that rebuilds its frozen Tool surface before every model round, maps Domain Keys to deterministic provider aliases, dispatches every Tool Call through the Plan 02 Capability Gateway, preserves complete provider protocol pairing, safely schedules sibling calls, supports resumable waiting state and soft finalization, and records real model compatibility evidence.

**Architecture:** Reimplement the useful Culina loop mechanisms in MindAtlas-owned contracts. A `ToolsProvider` returns a complete `ProviderToolSurface` tied to one append-only Run Manifest revision. A `ProviderAdapter` performs exactly one semantic model round and exposes only normalized stream events. A protocol assembler produces one normalized assistant message. Before sibling planning and again immediately before each dispatch, the runtime re-describes the exact frozen binding through Plan 02 and requires the current classification/behavior/descriptor digests to equal the surface that exposed the call. A scheduler then dispatches all calls against that exact surface and appends one Tool Result for every call before another Provider request or a terminal transcript seal. The loop is plain domain code and calls the Plan 02 Gateway through a dispatcher port. Plan 04 later supplies Main Agent prompts and `skill.search/skill.inject`; Plan 03 does not switch the assistant.

**Culina evidence inspected:** `/Users/zyf/IdeaProjects/Culina/backend/app/ai/runtime/types.py`, `/Users/zyf/IdeaProjects/Culina/backend/app/ai/runtime/openai_chat.py`, `/Users/zyf/IdeaProjects/Culina/backend/app/ai/runtime/openai_responses.py`, `/Users/zyf/IdeaProjects/Culina/backend/app/ai/runtime/tool_loop.py`, and `/Users/zyf/IdeaProjects/Culina/backend/app/ai/workflows/orchestrator/agent.py`. Reuse mechanisms and invariants only. Do not import Culina or copy its food-domain policy, prompts, persistence, budgets, drafts, completion rules, exception hierarchy, or Provider configuration.

**Prerequisites:**

- Plan 01 immutable `ResolvedRunManifestRevision` v1, Skill/capability bindings, provider alias hints, canonical digest helpers, and exact Provider/Model refs. The merged v1 contract must already contain `schema_version=1`, `provider_aliases=()`, `ResolvedProviderAliasRef`, and runtime/config/probe slots in `ProviderRef`/`ModelRef`; Plan 03 fills those slots and must not change the v1 Pydantic shape.
- Approved Plan 02A `FrozenCapabilityBinding`, versioned `CapabilityDescriptor`/classification contract, `CapabilityGateway.describe/execute`, error/retry disposition, cancellation/events, side-effect, parallel-safety, and exact authorization evidence. Tasks 0–9 and `docs/superpowers/evidence/plan-02a-readiness.md` must be complete with `PLAN_02A_READY=yes`.

If the merged Plan 01 omitted the alias slot or exact model-runtime fields, stop in Task 0 and amend Plan 01 before this plan. Adding a default field later and silently changing already-tested/stored Manifest digests is forbidden.

Plan 02B is a non-blocking operational coordination track: record whether it is pending, observing, or complete, but do not import OpenClaw, depend on its temporary selector/worker/catalog contracts, or delay this inert Provider Loop on deletion of its legacy branches. Plan 03 remains blocked if the Plan 02A Gateway/classification contract itself is not ready; it is not blocked merely because OpenClaw rollback remains available.

**Tech Stack:** Python 3.11, Pydantic 2, SQLAlchemy 2, PostgreSQL 15, the OpenAI Python SDK through a narrow transport adapter, server-sent streaming fixtures, Alembic, and pytest. The core loop does not depend on LangGraph.

---

## 1. Position and Hard Boundary

This is Plan 03 of 10 and completes milestone M1.

Implemented here:

- Provider-neutral message/round/Tool Call/result contracts.
- Append-only provider alias references tied to Run Manifest revisions.
- Dynamic `tools_provider` evaluated immediately before each Provider round.
- One-round Provider adapter protocol.
- Deterministic streaming assembly.
- Direct answer, Tool-then-answer, multi-round, cancellation, and explicit stop reasons.
- Every-call protocol pairing for one assistant message with multiple Tool Calls.
- Conservative sequential/parallel sibling planning.
- Portable waiting continuation, resume validation, and terminal cancellation sealing.
- Tools-disabled soft finalization.
- OpenAI-compatible Chat Completions adapter only.
- Scripted/fake Provider and fake HTTP server tests.
- Explicit live model capability probe plus immutable evidence history/current pointer.
- Internal/test-only integration with Plan 02 Gateway and dynamic Manifest changes.

Not implemented here:

- No Main Agent system prompt, Prompt Builder, Skill discovery, `skill.inject`, or assistant entrypoint switch.
- No OpenAI Responses adapter. Contracts must permit it later.
- No durable Run/Checkpoint/Worker Lease. Waiting state is portable but not persisted until Plan 06.
- No production human resume endpoint. Plan 07 consumes the continuation contract.
- No CapabilityCall ledger, write idempotency, reconciliation, or write parallelism.
- No multi-Skill owner budget/policy merge.
- No modification of current `run_agent_execution`, Router, Supervisor, or Workflow Agent nodes.
- No automatic model migration or fallback to a different Provider.
- No paid Provider calls in CI.
- No forced LangGraph/LangChain upgrade.
- No public live-probe execution by default. The probe API is additive but server-disabled unless an operator explicitly enables the existing configuration boundary; `confirmProviderCall=true` is cost acknowledgement, not authentication.

Hard failures:

- Binding Tools once before the whole loop.
- Executing only the first Tool Call.
- Making another Provider request with an unpaired prior assistant Tool Call.
- Sealing a terminal transcript with unpaired calls.
- Running a write, draft, unknown, Agent, or interrupt-capable call in parallel.
- Re-resolving aliases or “latest” capability versions on resume.
- Planning or dispatching from a surface whose frozen classification revision/ruleset, behavior digest, or descriptor digest no longer equals `CapabilityGateway.describe(exact_binding)`.
- Emitting a tool-call round’s provisional prose as the final user answer.
- Retrying a model round after Tool dispatch started.
- Sharing one SQLAlchemy Session across worker threads.
- Directly importing or calling the old Agent engine from the new Provider Loop. An Agent Capability may still execute indirectly through the Plan 02 Gateway and its exact-version Agent adapter; it remains non-parallel and cannot recurse into the Main Agent.
- Resuming with only IDs/digests while re-resolving the original round surface, child continuation, model configuration, locale, or execution scope from mutable state.
- Reusing one run's Tool surface, authorization evidence, continuation, Provider client, or database Session in another run/scope.

---

## 2. Verified Starting Point

Verified on 2026-07-13 before Plans 01–02 implementation:

- Current Git revision: `c25d03f`.
- Current sole Alembic head: `a7b8c9d0e1f2`.
- Existing migration ID `c9d0e1f2a3b4` is already used by `drop_legacy_workflow_graph_tables.py`. The old draft filename `c9d0e1f2a3b4_add_ai_model_capability_probes.py` is invalid and must not be used.
- Production Docker/CI use Python 3.11.
- Local `backend/.venv` uses Python 3.12.7.
- Requirements pin `langgraph==0.3.34` and `langchain-core<1.0`.
- Local environment has `langgraph==1.0.5`, `langchain-core==1.2.7`, and `openai==2.15.0`.
- Current `run_agent_execution`:
  - calls `llm.bind_tools(...)` once before the loop;
  - passes `parallel_tool_calls=False`;
  - extracts all returned calls but executes only `tool_calls[0]`;
  - logs and drops later sibling calls.
- `AiModel` currently has only credential ID, name, and `llm|embedding` type.
- `AiCredential` has mutable base URL/encrypted key but no execution revision.
- AI model/credential routes currently share the project’s existing configuration boundary; there is no new per-route auth dependency to reuse automatically.
- The combined pre-plan characterization suite passed with `82 passed, 2 subtests passed`.
- The current CI job uses Python 3.11 but has no PostgreSQL service and no PostgreSQL migration-test fixture. Plan 01 is expected to add one; Task 0 must verify the merged result and add/extend an isolated PostgreSQL 15 test gate if it is still absent.
- `backend/tests/test_ai_registry_service_db.py` does not exist at plan-writing time; create it if DB transaction coverage is kept separate from the existing mock-heavy service test.
- `backend/requirements.txt` does not directly declare `openai`; the local `openai==2.15.0` is transitive through `langchain-openai` and is not a production compatibility contract.
- `normalize_openai_base_url()` currently appends `/v1` but does not itself remove URL user-info, query, or fragment data. Probe/config digests must use a deliberately secret-free endpoint identity and must revalidate SSRF immediately before an explicit live call.
- MindAtlas currently has run/conversation isolation but no first-class tenant table or tenant ID on `Conversation`. This plan must not invent a false database tenancy guarantee; it freezes an authenticated execution-scope digest and tests principal/run/conversation separation so a future tenant identifier can be added without changing loop semantics.

Task 0 must re-run all facts after Plans 01–02. The clean Python 3.11 environment, not the drifted local venv, is the compatibility gate.

---

## 3. Culina Reuse Boundary

Reuse these mechanisms:

- Call a Tool provider before every model round.
- Keep provider transport names separate from Domain Keys.
- Keep Provider encoding/stream parsing inside adapters.
- Preserve the complete assistant Tool Call message.
- Return exactly one Tool Result per Tool Call.
- Use a no-tools finalization round after prior Tool use.
- Treat Provider errors, Tool errors, waiting, and hard stop as different states.

Reimplement or strengthen:

- Multiple sibling call scheduling.
- Append-only alias preservation.
- Portable waiting/resume state.
- Transcript protocol validator.
- Safe cancellation sealing.
- Model capability evidence persistence.
- Provider error sanitization.

Verified Culina limitations that must not cross the repository boundary:

- `OpenAICompatibleChatProvider.generate_with_tools()` refreshes Tools every round and iterates every returned call, but keeps Provider encoding, the multi-round loop, Tool execution, product tracing, and exception policy in one class.
- Approval/input exceptions leave the Culina loop immediately; they are useful state signals, not a portable paired-transcript resume contract.
- Culina sends text deltas to its message handler before knowing whether the same assistant message will later contain Tool Calls. MindAtlas buffers a round until it knows whether text is final-user-visible.
- Culina may retry an empty/failed stream before Tool execution and records arbitrary exception strings in several traces/results. MindAtlas uses `max_retries=0`, permits only explicitly classified unsupported-parameter negotiation before any stream item, and maps all failures to safe errors.
- Culina synthesizes missing call IDs and executes siblings sequentially, but does not provide append-only alias evidence, isolated sibling Sessions, a transcript validator, or portable continuation snapshots.

Do not reuse:

- Culina product/workflow profiles;
- food data, drafts, approval classes, or UI events;
- Culina budget merging;
- Culina Provider fallback/retry rules;
- Culina prompt cache policy;
- cross-repository imports.

No code comment should say “temporary copy from Culina”. MindAtlas owns and tests the new contracts.

---

## 4. Runtime Topology

~~~mermaid
flowchart LR
    L["ProviderAgentLoop"] --> TP["ToolsProvider per round"]
    TP --> MS["Manifest + ProviderToolSurface"]
    L --> PA["ProviderAdapter: one streamed round"]
    PA --> AS["Stream Assembler"]
    AS --> MSG["Normalized Assistant Message"]
    L --> SCH["Sibling Scheduler"]
    SCH --> TD["ToolDispatcher"]
    TD --> GW["Plan 02 CapabilityGateway"]
    GW --> CAP["Tool / Workflow / Agent"]
    SCH --> RES["Ordered Tool Result messages"]
    RES --> L
    L --> FIN["Tools-disabled finalization"]
~~~

Dependency rules:

1. `app.assistant.provider_loop` may import Plan 01 domain contracts and Plan 02 capability contracts.
2. It must not import OpenClaw, Router, Supervisor, Main Agent, or Culina.
3. The core loop must not import OpenAI/LangChain. Only `adapters/openai_chat.py` imports the OpenAI SDK.
4. The OpenAI adapter performs one round; it does not own the multi-round loop or execute Tools.
5. The scheduler sees frozen descriptor metadata and calls a dispatcher port; it does not resolve database targets itself.
6. Provider clients, iterators, Sessions, callbacks, futures, and exceptions are ephemeral ports, never serialized continuation state.
7. `ToolsProvider` and authorization-evidence creation receive an explicit execution scope. No global cache may be keyed only by Domain Key, locale, or model name; any safe immutable cache key includes the scope/Manifest/config digest or is proven tenant-independent.
8. Tool-surface resolution closes its read Session before the Provider stream begins. Parallel dispatch obtains a fresh bounded Session/Gateway context per call and closes it in that call's worker.
9. The loop may execute an Agent Capability only through `CapabilityGateway`; it may not import `run_agent_execution`, construct a legacy Agent directly, or select the Main Agent recursively.
10. A frozen surface preserves what the Provider saw; it is not permanent permission to reuse old behavior classification. Exact current descriptor verification is a separate short-lived port and never mutates/rebuilds the exposed alias, binding, arguments, or surface.

---

## 5. Append-Only Provider Alias Contract

Plan 01 reserves final alias validation for Plan 03 and already owns the following v1 fields. Consume them directly; do not redefine the class, add a field, or fork a second Manifest type here.

~~~python
class ResolvedProviderAliasRef(FrozenContract):
    provider_protocol: str
    domain_key: str
    provider_alias: str
    binding_contract_digest: str


class ResolvedRunManifestRevision(FrozenContract):
    schema_version: Literal[1] = 1
    # all other Plan 01 fields remain unchanged
    provider_aliases: tuple[ResolvedProviderAliasRef, ...] = ()
~~~

Provide:

~~~python
def append_provider_aliases(
    current: ResolvedRunManifestRevision,
    *,
    aliases: tuple[ResolvedProviderAliasRef, ...],
) -> ResolvedRunManifestRevision: ...
~~~

Rules:

- Existing alias refs in a parent revision can never be removed or replaced.
- New aliases can only be added for new frozen capability bindings.
- Reapplying the identical alias set returns the same revision.
- A different alias for an existing Domain Key/binding is a conflict.
- Alias refs participate in `manifest_digest`.
- A Provider round uses one Manifest revision containing the complete alias set for that surface.
- A Skill activation may first create a capability revision; the next `tools_provider` call appends missing aliases and returns the resulting revision before the Provider request.
- Resume uses the exact Manifest revision/digest and never regenerates aliases.

Digest dependency direction is locked:

~~~text
FrozenCapabilityBinding.binding_contract_digest
    -> ResolvedProviderAliasRef
    -> ResolvedRunManifestRevision.manifest_digest
    -> alias_map_digest
    -> ProviderToolSurface.surface_digest
    -> ProviderToolCall / assistant-message digest
    -> transcript digest
~~~

No edge may point backwards:

- `binding_contract_digest` never includes Provider alias, Manifest revision/digest, or surface digest.
- An alias ref contains only protocol, Domain Key, Provider alias, and binding digest; it never contains the Manifest digest that contains it.
- Manifest canonical payload excludes `manifest_digest` itself and all surface/message/transcript digests.
- `alias_map_digest` is calculated only after the Manifest revision exists and is not copied back into that same revision.
- `surface_digest` may include Manifest and alias-map identity, but neither the Manifest nor binding digest includes the surface.

Plan 01 fixed vectors must already prove that an initial `provider_aliases=()` participates in v1 canonicalization. Task 1 adds two cross-plan vectors: the empty-alias v1 digest remains byte-identical, and appending one alias creates one child revision with the expected parent digest. If the empty slot is absent or an implementation omitted it from an undocumented canonical payload, Task 1 fails closed and returns to Plan 01; do not introduce an implicit “omit empty aliases” exception in Plan 03.

The Provider alias is transport-only. Policy, budgets, logs, Gateway requests, and future ledger keys use Domain Key and binding digest.

### 5.1 OpenAI Chat alias algorithm

Valid syntax:

~~~text
^[A-Za-z0-9_-]{1,64}$
~~~

Algorithm:

1. Reserve adapter/runtime control aliases first.
2. Reserve all existing alias refs from the parent Manifest.
3. Evaluate author hints from Plan 01. A hint is accepted only if syntax-valid, not reserved, and collision-free under ASCII case-folding.
4. If multiple hints collide, none wins by ordering; all colliding declarations fall back to generated aliases.
5. Generated alias: replace every non-ASCII or non-alphanumeric run in Domain Key with `_`, collapse separators, trim, and lowercase.
6. If empty or longer than 48 characters, use a readable prefix plus `_` plus the first 12 lowercase hex characters of SHA-256 over `provider_protocol + NUL + domain_key + NUL + binding_contract_digest`.
7. Resolve remaining collisions deterministically from sorted `(domain_key, binding_digest)` using digest suffixes.
8. Never alter an alias already frozen in the parent Manifest when a new colliding Domain Key appears.
9. Build exact forward/reverse maps and a canonical `alias_map_digest`.

All sorting, validation, case-folding, and hashing operate on UTF-8 bytes and explicit ASCII rules. Never use Python's process-randomized `hash()`, database collation order, insertion order, locale-aware lowercasing, or Provider response order to allocate an alias.

Provider protocols may supply stricter validators later. Domain Keys do not change across providers.

---

## 6. Provider-Neutral Contracts

Use frozen Pydantic data for serializable state and dataclass/Protocol ports for runtime behavior.

### 6.1 Messages and calls

Prefer role-specific contracts over one permissive message model:

~~~python
class ProviderSystemMessage(FrozenContract):
    role: Literal["system"] = "system"
    content: str


class ProviderRuntimeInstructionMessage(FrozenContract):
    role: Literal["runtime_instruction"] = "runtime_instruction"
    instruction_type: Literal["soft_finalization"]
    locale: str
    content: str


class ProviderUserMessage(FrozenContract):
    role: Literal["user"] = "user"
    content: str


class ProviderToolCall(FrozenContract):
    call_id: str
    call_index: int
    provider_alias: str
    domain_key: str
    arguments: dict[str, JsonValue]
    arguments_digest: str
    binding_contract_digest: str
    descriptor_digest: str
    behavior_digest: str
    classification_revision: str
    classification_ruleset_digest: str
    manifest_revision: int
    manifest_digest: str
    surface_digest: str


class ProviderAssistantMessage(FrozenContract):
    role: Literal["assistant"] = "assistant"
    content: str | None
    tool_calls: tuple[ProviderToolCall, ...] = ()


class ProviderToolResultEnvelope(FrozenContract):
    status: Literal[
        "completed",
        "failed",
        "blocked",
        "cancelled",
        "cancelled_before_start",
    ]
    domain_key: str
    user_text: str | None
    structured_output: JsonValue | None
    terminal_output: bool
    needs_followup: bool
    error: CapabilityError | None
    artifact_refs: tuple[ArtifactRef, ...] = ()


class ProviderToolMessage(FrozenContract):
    role: Literal["tool"] = "tool"
    call_id: str
    provider_alias: str
    content: ProviderToolResultEnvelope


ProviderMessage = Annotated[
    ProviderSystemMessage
    | ProviderRuntimeInstructionMessage
    | ProviderUserMessage
    | ProviderAssistantMessage
    | ProviderToolMessage,
    Field(discriminator="role"),
]


class ProviderToolCallRecord(FrozenContract):
    call: ProviderToolCall
    status: Literal[
        "completed",
        "failed",
        "blocked",
        "waiting",
        "deferred",
        "cancelled",
        "cancelled_before_start",
    ]
    result_message_digest: str | None
    safe_duration_ms: float | None
~~~

Invariants:

- Tool Call IDs are unique within an assistant message and across the active transcript.
- Call indexes are contiguous from zero and preserve Provider order.
- Assistant content may coexist with Tool Calls in history, but is not user-visible final text.
- A Tool message references exactly one prior open call.
- A call cannot receive two results.
- Domain Key/alias/binding/Manifest/surface values are frozen at exposure time.
- Descriptor, behavior, classification revision, and classification-ruleset digests are stamped from the same exposed definition and participate in call/message/transcript digests. They record what the Provider saw but must still match current Plan 02 classification before planning/dispatch.
- Arguments are validated JSON objects before dispatch.
- A runtime instruction is internal protocol history, never user-authored input and never emitted as final text. The Chat adapter maps it to a safe system-level message; a future adapter may map it to its native instruction role.
- `ProviderToolResultEnvelope` is a lossless safe projection of `CapabilityResult`; it never stringifies arbitrary structured output or embeds metrics, continuations, raw exceptions, or runtime objects.
- `waiting` and `deferred` are scheduler-record states, not Provider Tool messages. A Tool message is appended only when that call is terminal for the current assistant message.

### 6.2 Tool surface

~~~python
class ProviderToolDefinition(FrozenContract):
    provider_alias: str
    domain_key: str
    description: str
    input_schema: dict[str, JsonValue]
    binding: FrozenCapabilityBinding
    descriptor: CapabilityDescriptor


class ProviderToolSurface(FrozenContract):
    provider_protocol: str
    manifest_revision: int
    manifest_digest: str
    alias_map_digest: str
    tools: tuple[ProviderToolDefinition, ...]
    surface_digest: str


class ToolSurfaceResolution(FrozenContract):
    manifest: ResolvedRunManifestRevision
    surface: ProviderToolSurface


class ToolsProvider(Protocol):
    def resolve(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        scope: "ProviderExecutionScope",
        locale: str,
    ) -> ToolSurfaceResolution: ...


class CurrentCapabilityDescriptorVerifier(Protocol):
    def require_current(
        self,
        *,
        binding: FrozenCapabilityBinding,
        exposed_descriptor: CapabilityDescriptor,
        scope: "ProviderExecutionScope",
    ) -> CapabilityDescriptor: ...
~~~

Surface rules:

- Definitions sort by provider alias for stable Provider payloads.
- Forward/reverse maps are one-to-one.
- Every definition’s binding exists in the same Manifest revision.
- Descriptors are available and already filtered for visibility, but Gateway still authorizes each call.
- A surface may be empty.
- `surface_digest` covers protocol, Manifest identity, aliases, Domain Keys, binding/descriptor/behavior/classification/schema digests, descriptions, and order.
- Descriptions are frozen per surface; locale change mid-Run does not rewrite a resumed surface.
- Binding, descriptor, input schema, Domain Key, and all corresponding digests must agree. The surface builder does not trust a caller-supplied digest.
- `availability.status` must be `available`, and `legacy_blocking` descriptors are excluded from the Provider Loop. Only a future/fixture `interrupt_mode=durable` result may produce portable waiting.
- The surface contains no authorization evidence. Visibility and execution authorization are separate; evidence is freshly issued and verified for every dispatch.
- `CurrentCapabilityDescriptorVerifier` re-runs Plan 02 `CapabilityGateway.describe(exact_binding)` in a short independent resolution context, closes that context, and requires equality of availability, `descriptor_digest`, `behavior.behavior_digest`, `behavior.classification.revision`, and `behavior.classification.ruleset_digest`. Descriptor digest equality also protects the binding/resolution/dependency/Schema/executable fields covered by Plan 02. It never substitutes a newly described binding/descriptor into the old surface.

Classification freshness has two mandatory gates:

1. **pre-plan batch:** after assembling all calls and before starting any sibling, verify every referenced surface definition; if any check fails, dispatch none;
2. **pre-dispatch:** immediately before evidence issuance and `Gateway.execute`, the isolated dispatcher verifies its one exact binding/descriptor again.

A mismatch, unavailable descriptor, or `unknown` current classification is fatal `CapabilityError(error_type="version_drift", safe_code="classification_changed")`, never a downgrade to sequential execution and never a reason to rebuild aliases from latest state. The Provider-visible assistant call is already in history, so failure must be paired/sealed according to Section 8 rather than discarded.

### 6.3 Round adapter

~~~python
class ProviderUsage(FrozenContract):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class ProviderToolChoice(FrozenContract):
    mode: Literal["auto", "required", "none", "specific"] = "auto"
    provider_alias: str | None = None


class ProviderGenerationOptions(FrozenContract):
    max_output_tokens: int | None = None
    temperature: float | None = None
    tool_choice: ProviderToolChoice = ProviderToolChoice()
    request_parallel_tool_calls: bool | None = None


class ProviderRoundRequest(FrozenContract):
    round_index: int
    messages: tuple[ProviderMessage, ...]
    tool_surface: ProviderToolSurface
    tools_enabled: bool
    finalization_round: bool
    model_ref: ModelRef
    generation: ProviderGenerationOptions


class ProviderRoundResult(FrozenContract):
    assistant_message: ProviderAssistantMessage
    finish_reason: str | None
    usage: ProviderUsage | None
    compatibility_warnings: tuple[str, ...]


class ProviderAdapter(Protocol):
    provider_protocol: str
    adapter_key: str
    adapter_revision: str
    model_config_digest: str

    def stream_round(
        self,
        request: ProviderRoundRequest,
        *,
        cancellation: CancellationPort,
    ) -> Iterator[ProviderStreamEvent]: ...
~~~

Use a discriminated normalized stream union rather than SDK chunks:

~~~python
class ProviderTextDelta(FrozenContract):
    event_type: Literal["text.delta"] = "text.delta"
    sequence: int
    delta: str


class ProviderToolCallDelta(FrozenContract):
    event_type: Literal["tool_call.delta"] = "tool_call.delta"
    sequence: int
    call_index: int
    call_id: str | None = None          # whole stable value when observed
    function_type: Literal["function"] = "function"
    provider_alias_delta: str = ""
    arguments_delta: str = ""


class ProviderUsageSnapshot(FrozenContract):
    event_type: Literal["usage"] = "usage"
    sequence: int
    usage: ProviderUsage


class ProviderRoundTerminal(FrozenContract):
    event_type: Literal["round.terminal"] = "round.terminal"
    sequence: int
    finish_reason: str | None
    safe_request_id: str | None = None


ProviderStreamEvent = Annotated[
    ProviderTextDelta
    | ProviderToolCallDelta
    | ProviderUsageSnapshot
    | ProviderRoundTerminal,
    Field(discriminator="event_type"),
]
~~~

Event sequences are contiguous from zero. There is exactly one terminal event and nothing follows it. Tool-call IDs are whole stable values when present; a later different nonempty value is a protocol error. Alias and argument fields are append-only UTF-8 fragments with explicit aggregate byte limits. The assembler parses arguments exactly once after terminal as a JSON object and resolves aliases only through the round surface.

The adapter emits normalized stream events and no Tool execution. The loop/assembler returns one round result. Adapter identity and secret-free `model_config_digest` must equal the exact `ModelRef`; a mismatched adapter fails before network I/O.

### 6.4 Loop request, ports, and result

~~~python
class ProviderExecutionScope(FrozenContract):
    run_id: UUID
    conversation_id: UUID | None
    principal: CapabilityPrincipal
    tenant_scope_id: str | None
    scope_digest: str


class ProviderLoopRequest(FrozenContract):
    manifest: ResolvedRunManifestRevision
    initial_messages: tuple[ProviderMessage, ...]
    model_ref: ModelRef
    execution_scope: ProviderExecutionScope
    max_rounds: int
    locale: str
    generation: ProviderGenerationOptions


class ProviderAuthorizationEvidenceFactory(Protocol):
    def issue(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence: ...


@dataclass(frozen=True)
class ProviderLoopPorts:
    provider: ProviderAdapter
    tools_provider: ToolsProvider
    current_descriptors: CurrentCapabilityDescriptorVerifier
    authorization_evidence: ProviderAuthorizationEvidenceFactory
    tool_dispatcher: ToolDispatcher
    sibling_executor: SiblingExecutionPort
    cancellation: CancellationPort
    events: ProviderLoopEventSink


class ProviderLoopResult(FrozenContract):
    status: Literal["completed", "waiting", "failed", "cancelled"]
    final_text: str | None
    messages: tuple[ProviderMessage, ...]
    tool_calls: tuple[ProviderToolCallRecord, ...]
    round_count: int
    stop_reason: ProviderStopReason
    manifest: ResolvedRunManifestRevision
    continuation: ProviderLoopContinuation | None
    usage: ProviderUsage
    error: SafeProviderError | None
~~~

`max_rounds` counts Provider requests across start plus all resumes, not Tool Calls. It is at least 2 for a tool-enabled loop so a finalization request can be reserved. The request validates:

- `manifest.run_id == execution_scope.run_id`;
- exact Manifest `model`/`provider` refs equal the loop model and adapter identity/config digest;
- the scope digest is recomputed from the portable scope fields;
- no credential, Session, request object, or auth token appears in the request;
- `tenant_scope_id=None` is accepted only for the repository's current explicitly single-tenant/test entrypoint. The loop must not claim tenant isolation from a nullable field alone.

### 6.5 Dispatcher and Manifest changes

~~~python
class ProviderDispatchRequest(FrozenContract):
    call: ProviderToolCall
    binding: FrozenCapabilityBinding
    descriptor: CapabilityDescriptor
    current_manifest: ResolvedRunManifestRevision
    execution_scope: ProviderExecutionScope
    authorization: CapabilityAuthorizationEvidence


class ProviderDispatchResult(FrozenContract):
    capability_result: CapabilityResult
    next_manifest: ResolvedRunManifestRevision


class ToolDispatcher(Protocol):
    def dispatch(
        self,
        request: ProviderDispatchRequest,
        *,
        cancellation: CancellationPort,
    ) -> ProviderDispatchResult: ...
~~~

All sibling calls from one assistant message keep their original exposed binding/descriptor/Manifest/surface identity. `current_manifest` is a separate mutation base: it must equal that exposed Manifest or a validated append-only descendant produced by an earlier sequential sibling. Thus an already exposed call can never be rebound, while two non-parallel Manifest-mutating control calls can append cumulatively instead of both forking the same parent. Immediately before every dispatch, the isolated dispatcher calls `Gateway.describe(exact_binding)` and requires the current availability/classification/behavior/descriptor values to equal the exposed descriptor. Only after equality succeeds does it issue new evidence from the injected trusted factory and call `Gateway.execute` once; never serialize and replay old evidence on resume. The re-described object is verification evidence only and never replaces the surface object used for alias, arguments, transcript, or scheduling identity.

`ProviderDispatchResult.next_manifest` must be either `request.current_manifest` or one validated append-only descendant of it. A returned ancestor, unrelated Run, changed model/provider ref, changed existing alias/binding, skipped parent, or conflicting sibling child is a protocol error. The scheduler carries two identities while waiting: the original round surface that exposed all siblings and the latest validated current Manifest produced by completed siblings.

### 6.6 Waiting and resume

~~~python
class ProviderWaitingCallState(FrozenContract):
    call_id: str
    call_index: int
    binding_contract_digest: str
    descriptor_digest: str
    behavior_digest: str
    classification_revision: str
    classification_ruleset_digest: str
    capability_continuation: ContinuationRef


class ProviderLoopContinuation(FrozenContract):
    contract_version: Literal[1] = 1
    execution_scope: ProviderExecutionScope
    model_ref: ModelRef
    locale: str
    max_rounds: int
    provider_rounds_used: int
    prior_tool_call_count: int
    accumulated_usage: ProviderUsage
    current_manifest_revision: int
    current_manifest_digest: str
    exposed_surface: ProviderToolSurface
    assistant_message_digest: str
    transcript_digest: str
    waiting_call: ProviderWaitingCallState
    next_call_index: int
    pending_call_ids: tuple[str, ...]
    completed_call_records: tuple[ProviderToolCallRecord, ...]


class ProviderWaitingResolution(FrozenContract):
    call_id: str
    capability_continuation: ContinuationRef
    capability_result: CapabilityResult


class ProviderLoopResumeRequest(FrozenContract):
    manifest: ResolvedRunManifestRevision
    messages: tuple[ProviderMessage, ...]
    continuation: ProviderLoopContinuation
    resolved_waiting: ProviderWaitingResolution
~~~

The continuation contains only frozen portable data. The full original `ProviderToolSurface` is intentionally included: a surface digest alone cannot reconstruct the exact binding/descriptor/scheduling facts after deployment or configuration drift. The frozen assistant message and arguments remain in the transcript rather than being duplicated.

Resume validation is all-or-nothing before dispatch or Provider I/O:

1. Recompute continuation, scope, ModelRef, exposed-surface, assistant-message, transcript, and current-Manifest digests.
2. Require the supplied Manifest to equal the latest continuation Manifest and remain an append-only descendant of the exposed round Manifest.
3. Locate the one open assistant message and require completed/waiting/pending IDs and indexes to match exactly.
4. Require the waiting `ContinuationRef` to match byte-for-byte and the trusted resolution to be `completed|failed|cancelled`, never another `waiting` result.
5. Re-describe every definition referenced by the open assistant message through `CurrentCapabilityDescriptorVerifier` and require the current classification/behavior/descriptor digests to equal the continuation's full exposed surface.
6. If classification changed, append the already trusted terminal waiting result honestly, mark every never-started pending sibling `cancelled_before_start`, seal the transcript, and return fatal `version_drift/classification_changed` without evidence issuance, dispatch, or Provider I/O. Do not discard/overwrite a durable child result that already happened, and do not continue under a rebuilt surface.
7. If classification remains current, rebuild one safe `ProviderToolMessage` from the resolved `CapabilityResult`, append it in call order, then reissue authorization and continue pending siblings against the original surface.
8. Preserve original locale, model/config, maximum rounds, rounds used, usage, and prior call count. Resume has no fields that can extend those budgets or switch models.

The `ProviderWaitingResolution` is an internal trusted orchestration input, never a FastAPI request body. Plans 06–07 own durable child-resume authorization and persistence. Plan 03 proves in-memory portability and tamper rejection only.

No Provider client, iterator, Session, callback, future, task, exception, or decrypted credential is serializable continuation state.

---

## 7. Loop State Machine and Protocol Invariants

### 7.1 Start/round algorithm

For Provider round `r`:

1. Check cancellation.
2. Validate the execution scope, exact ModelRef/adapter/config identity, accumulated round count, and transcript pairing before any runtime work.
3. If this is the reserved finalization round, derive a canonical empty Tool surface tied to the current Manifest, set `tool_choice=none`, and append exactly one frozen runtime finalization instruction.
4. Otherwise call `tools_provider.resolve(current_manifest, scope=execution_scope, locale=locale)` and close its resolution context.
5. Accept only a valid append-only alias revision and freeze that Manifest/surface for round `r`.
6. Emit `round.started` with digests/counts only.
7. Invoke one Provider adapter round and accept one contiguous normalized event sequence.
8. Buffer all text/tool-call chunks and accumulate bounded usage.
9. Assemble and protocol-validate one assistant message.
10. Append the complete assistant message to history.
11. If no Tool Calls:
    - reject empty output;
    - replay buffered text as final user-visible deltas;
    - return natural completion or soft-finalized completion.
12. If Tool Calls:
    - never expose the buffered prose as final;
    - reject Tool Calls during a tools-disabled finalization round;
    - resolve each call only through the frozen surface, then re-describe every exact binding and require current availability/classification/behavior/descriptor equality **before** sibling grouping or any dispatch;
    - if that batch check fails, dispatch none, pair the first stale call as `blocked`, pair every other unstarted sibling as `cancelled_before_start` in Provider order, and stop with fatal `version_drift/classification_changed`;
    - otherwise plan all siblings; each isolated dispatcher repeats the exact descriptor check immediately before fresh evidence issuance and its single Gateway execution;
    - append ordered Tool Results;
    - if one durable call is waiting, return its exact child continuation and full exposed surface without another Provider request;
    - otherwise enter next round.

The Provider surface is rebuilt only before Provider requests, not between siblings from one assistant message. `current_manifest` may advance while siblings execute, but the original exposed surface remains the only source for their binding, descriptor, alias, and scheduling facts.

### 7.2 Transcript pairing

Before every Provider request and before terminal sealing:

~~~text
count(open Tool Calls) == 0
and every assistant Tool Call has exactly one later Tool Result
and result order matches assistant call order
~~~

The sole temporary exception is `status=waiting`. Its continuation identifies exactly one open assistant message, one waiting durable child, the already completed prefix, and the pending suffix. Pending siblings are recorded internally as `deferred`; they do not receive a fake Tool Result because that would close the Provider call and make same-call resume impossible. The incomplete transcript must never be sent to a Provider.

Provide pure validators:

~~~python
def validate_provider_transcript(
    messages: tuple[ProviderMessage, ...],
    *,
    allowed_open_continuation: ProviderLoopContinuation | None = None,
) -> None: ...

def seal_cancelled_continuation(...) -> tuple[ProviderMessage, ...]: ...
~~~

`seal_cancelled_continuation` seals Provider protocol only. It does not claim to cancel the underlying durable child. In Plan 03 it is tested with a fake continuation; Plans 06–07 must first obtain a trusted child-cancellation outcome, then seal the waiting call as `cancelled` and the never-started suffix as `cancelled_before_start`.

### 7.3 Stop reasons

~~~python
ProviderStopReason = Literal[
    "natural_completion",
    "waiting_interrupt",
    "cancelled",
    "provider_error",
    "protocol_error",
    "capability_error",
    "max_rounds_soft_finalized",
    "max_rounds_hard_stop",
]
~~~

Rules:

- `completed` uses `natural_completion|max_rounds_soft_finalized`.
- `waiting` uses `waiting_interrupt`.
- `cancelled` uses `cancelled`.
- `failed` uses one of the remaining error reasons.
- Stop reason and status combinations are validator-enforced.

### 7.4 Soft finalization

- Once any Tool Call has occurred, the last allowed Provider request is reserved.
- Reserved request exposes no Tools.
- Append a localized internal instruction to summarize completed and incomplete work without calling Tools.
- If final text is nonempty and no Tool Calls appear: `max_rounds_soft_finalized`.
- Tool Calls, empty output, malformed stream, or protocol violation: `max_rounds_hard_stop`.
- With `max_rounds=1`, a nonempty Tool surface is rejected before calling the Provider.
- A normal round with no Tool Calls terminates the loop, so there is no legal path where earlier no-Tool rounds silently consume the budget and the first Tool Call appears only on the last slot. If a Provider emits a Tool Call on the reserved tools-disabled finalization request, reject it as `max_rounds_hard_stop`; never execute it or allocate an extra round.
- No “extra” Provider request beyond `max_rounds`.
- Waiting/resume preserves `provider_rounds_used` and prior Tool Call count, so a resume cannot buy another finalization round.
- The finalization instruction is a `ProviderRuntimeInstructionMessage`, not a forged user message, and participates in transcript digest/replay.

### 7.5 Streaming

- Buffer round text until the round is known to contain no Tool Calls.
- Tool-call round text remains in assistant history but is not emitted as final text.
- Natural/finalization text is replayed as ordered `final_text.delta` events after successful round assembly.
- Round/tool/progress events may be emitted immediately.
- Provider/network failure discards buffered user-visible text.
- A Provider round is never semantically retried after any Tool dispatch. The OpenAI adapter may perform one narrowly classified unsupported-request-parameter negotiation only before receiving any stream item; it is recorded as compatibility evidence and is not a general model retry.
- Commit-after-assembly means user-visible token latency is one Provider round in this milestone. This is an explicit safety tradeoff: SSE still receives ordered replay deltas, but provisional Tool-call prose is never shown. A later optimization requires a Provider guarantee that Tool Calls cannot follow emitted text.

---

## 8. Sibling Scheduling Contract

Use the descriptor frozen in the surface for deterministic grouping **only after** the pre-plan verifier has proved its current Plan 02 classification/behavior/descriptor digests are still identical. A mismatch is fatal drift, not a reason to recompute groups from a newer descriptor.

Parallel eligibility requires all:

- side effect in `none|compute|read`;
- `parallel_safe=true`;
- `interrupt_mode=none`;
- dispatcher explicitly supports isolated parallel dispatch;
- sibling executor can create an independent Gateway/Session context;
- cancellation has not been requested.

Everything else is sequential in Provider order.

Planning algorithm:

- Re-describe all sibling bindings before inspecting any `parallel_safe` bit. Verification order is Provider call order and no call starts until the whole batch passes.
- If one or more definitions are stale, choose the first stale call in Provider order as `blocked(version_drift/classification_changed)`, mark every other never-started sibling—including earlier indexes—as `cancelled_before_start`, append all Tool messages in Provider order, and stop. This deterministic seal avoids partial side effects under a mixed classification surface.
- Walk calls in Provider order.
- Build maximal contiguous groups with the same safe parallel eligibility.
- A safe group may run bounded-parallel.
- Unsafe groups execute one call at a time.
- Writes/drafts/unknown/Agent/interrupt-capable calls never run in parallel.

`parallel_safe` is permission, not a requirement. If no safe isolated executor is available, execute sequentially and remain correct.

Parallel execution rules:

- Default maximum workers: 4, configurable only through injected executor policy.
- Never share the request SQLAlchemy Session.
- Each call gets an independent dispatcher/Gateway context and fresh Session.
- Each call receives the same immutable execution scope and independently issued authorization evidence; the Gateway verifies both against the exact descriptor.
- Inside each worker, re-describe the exact binding immediately before evidence issuance/Gateway execution. If it no longer equals the exposed classification/behavior/descriptor, return a fatal blocked result without invoking the adapter.
- Do not implement hard timeout by abandoning a future.
- Wait for started safe calls to reach an honest terminal state.
- Collect out-of-order completions, append one honest Tool Result for every **started** call in Provider order, even when one started sibling returns a fatal error. Do not discard a successful sibling merely because its completion was observed after the fatal result.
- One safe-call failure does not cancel already started safe siblings unless cancellation policy explicitly requires it.
- A parallel worker returning `waiting` despite `interrupt_mode=none`, using a different scope, or retaining a Session after completion is a protocol error.
- All workers in one parallel group receive the same current Manifest parent. Unchanged results plus one child, or multiple byte-identical children, converge; different children are rejected unless a future explicit merge contract exists. A descriptor that may mutate the Manifest is never parallel; sequential groups pass each accepted child as the next call's `current_manifest`.

Sequential waiting rules:

- Only a descriptor frozen as `interrupt_mode=durable` may return `waiting`; `none|legacy_blocking` returning it is a protocol error.
- Plan 02A produces no production `durable` descriptor, so Plan 03 exercises this branch only with explicit contract fixtures. The presence of the union member does not expose a production waiting Capability or weaken the `legacy_blocking` exclusion; Plans 06–07 own the first real durable producer.
- If a call returns `waiting`, require a portable `ContinuationRef` and do not start later siblings.
- Preserve already completed prior results.
- Return continuation with waiting and remaining IDs.
- Do not synthesize results merely to make a Provider request; no Provider request occurs.
- On resume, validate and insert the waiting result, then evaluate remaining siblings.
- On terminal cancellation/termination, append `cancelled` for waiting call and `cancelled_before_start` for unstarted calls, in order, then seal.

Scope/session isolation tests must prove:

- a surface resolved for scope A cannot be reused for scope B when visibility differs;
- two runs with the same Domain Keys and model name retain distinct scope/Manifest digests;
- no parent request Session enters a worker;
- every bounded-parallel call opens and closes a distinct Session/Gateway context;
- evidence issued for one principal/run/conversation/tenant scope is rejected for another;
- cancellation polling uses a fresh scoped read or an injected thread-safe port, not the worker's business transaction.

The current repository has no tenant column. These tests use explicit synthetic `tenant_scope_id` values and current principal/run/conversation ownership checks; they are forward-compatibility tests, not a claim that Plan 03 adds tenant persistence.

Fatal call errors:

- Authorization/version/protocol/invalid-output/unknown-side-effect errors become a `blocked` result for the current call.
- Remaining unstarted siblings receive `cancelled_before_start`.
- Seal pairing and stop with `capability_error`.
- A safe normal Tool execution failure may become a `failed` Tool Result and allow the Provider to continue.

For a fatal result inside an already started parallel group, wait for all started siblings to finish honestly, retain their completed/failed/blocked results in Provider order, mark only later not-yet-started groups `cancelled_before_start`, then seal and stop. The fatal call is not forcibly moved ahead of an earlier-index successful result, and a later-index success is not erased. This ordering rule is distinct from pre-plan classification drift, where no sibling has started and the first stale call is the single blocked result.

---

## 9. OpenAI-Compatible Chat Completions Adapter

Use the OpenAI Python SDK behind a narrow transport factory. `backend/requirements.txt` currently has no direct `openai` declaration, so Task 6 must add one. Derive the compatible bound from a fresh Python 3.11 resolver against the merged `langchain-openai`/`langchain-core` constraints, run `pip check`, and lock only the SDK API range exercised by tests. Do not use the local transitive `openai==2.15.0` as evidence and do not copy the illustrative range from an older draft.

Production client:

- normalized base URL from existing AI registry;
- reject URL user-info, query, and fragment for live probe/runtime construction; re-run SSRF validation immediately before the call and derive a secret-free endpoint identity for digests/errors;
- API key decrypted only at adapter construction after runtime/model eligibility checks;
- separate native connect/read/write/total-stream bounds;
- `max_retries=0` for loop rounds;
- `n=1`;
- `stream=true`;
- usage requested when supported;
- `tool_choice`, output-token limit, temperature, and `parallel_tool_calls` are emitted only according to normalized generation options and observed/explicit adapter compatibility;
- no raw request/response body logging.

The adapter factory receives an already authorized exact `ModelRef` plus an ephemeral decrypted runtime config. It recomputes model/credential runtime revisions and the secret-free config digest before client creation. A changed or unavailable revision is `version_drift`; it never falls forward to the latest credential/model. On resume, failure to reconstruct the exact runtime is terminal and safe.

Compatibility negotiation is not a general retry policy. At most once, before any stream item, a structured HTTP 400 that identifies an optional request parameter such as `stream_options` as unsupported may rebuild the same semantic request without only that parameter. Record the removed parameter and warning code. Never trigger fallback from arbitrary response text, 401/403/408/409/429/5xx, an opened/partial stream, or an unknown error.

Stream assembler:

- accept choice index 0 only;
- merge content deltas in order;
- merge fragmented Tool Call fields by Provider call index;
- reject changing IDs/names/types;
- concatenate argument fragments exactly;
- parse arguments once at end as a JSON object;
- reject duplicate IDs and invalid/gapped indexes that cannot be normalized safely;
- preserve Provider order;
- accept a missing call ID only by synthesizing deterministic `call_r{round}_i{index}_{digest8}`, record a compatibility warning, and mark stable IDs unsupported in probes;
- reject an unknown alias before dispatch;
- capture safe finish reason/usage/request ID only;
- accept only bounded request IDs matching a conservative printable identifier pattern; otherwise discard them;
- never expose arbitrary SDK exception strings.

Message encoder:

- assistant history includes the complete Tool Call list;
- each Tool result encodes the exact call ID;
- tool definitions use alias, description, and input JSON Schema;
- no Tools field on finalization round;
- runtime finalization instructions map to a system-level Chat message and never to a user-authored message;
- Tool Result envelopes serialize through canonical JSON with explicit byte bounds;
- `tool_choice=none` accompanies tools-disabled finalization only when supported; omitting `tools` remains the protocol invariant;
- no Domain Key is sent as a Tool name unless it equals the frozen alias by design.

Fake HTTP tests must exercise real SDK wire parsing for:

- fragmented text;
- fragmented name/arguments;
- multiple calls;
- missing ID;
- duplicate ID;
- invalid JSON;
- stream error;
- empty response;
- tool result continuation;
- tools-disabled finalization.
- optional-parameter negotiation before first chunk and refusal after first chunk;
- exact model/config digest mismatch before HTTP I/O;
- URL user-info/query/fragment rejection and fresh SSRF failure;
- two concurrent adapters/scopes do not share mutable request/message state.

No internet is required. The local fake server uses an explicitly injected test transport/config path; production SSRF checks must not be weakened to allow loopback for tests.

---

## 10. Model Capability Probe Contract

### 10.1 Evidence captured

For one exact model configuration, adapter revision, and probe-contract version, observe:

- streaming transport/text;
- Tool Calling;
- JSON Schema argument conformance;
- nonempty stable Tool Call IDs;
- multiple Tool Calls in one assistant message;
- Tool Result continuation;
- tools-disabled finalization;
- protocol compatibility warnings.

Each capability state is `passed|failed|not_observed` with a safe reason code, not raw Provider content.

`failed` means the Provider produced evidence that the capability did not work. `not_observed` means the bounded probe could not prove it—for example, a model chose one Tool instead of two despite a valid required-tool request. The probe must not overstate nondeterministic model behavior as a protocol failure.

Probe status:

- `passed`: all required capabilities passed.
- `partial`: transport worked but one or more required capabilities failed/not observed.
- `failed`: connection/auth/provider/protocol failure prevented meaningful observation.

### 10.2 Harmless live sequence

Use random non-sensitive nonces and fixed local fake Tools:

1. Stream a short nonce echo without Tools.
2. Expose `probe_echo` with a required string field; observe call arguments/ID.
3. Expose `probe_left` and `probe_right` and request both in one assistant message.
4. Return fixed local results and observe final continuation text.
5. Perform a tools-disabled finalization request and verify no Tool Call.

Use provider-neutral `tool_choice=required|specific` where it can reduce nondeterminism without adding Provider-specific prompt logic. The two-call phase may still be `not_observed` if the model emits only one valid call. Stable-ID support passes only for nonempty Provider IDs; adapter-synthesized IDs keep the transcript testable but do not count as Provider support.

Never send MindAtlas user data, Skill content, credentials, or business records. Cap per-request output tokens, request count, aggregate tokens, connect/read/total time, Tool-result bytes, and nonce length. Do not store prompt, raw chunks, request/response body, headers, nonce, Tool arguments/results, or result text.

Live probing is explicit, may cost money, and is never run by CI. CI runs the same orchestration only against the scripted adapter and local fake HTTP server.

### 10.3 Persistence

Create `ai_model_capability_probe`:

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `model_id` | FK to `ai_model`; lifecycle-owned |
| `probe_contract_version` | immutable integer; initially `1` |
| `adapter_key` | initially `openai_chat_completions` |
| `adapter_revision` | immutable application/adapter build revision |
| `model_config_digest` | secret-free digest of exact model/credential runtime refs and endpoint identity |
| `status` | `passed|partial|failed` |
| `capabilities` | validated JSON object of observed states/details |
| `probe_digest` | canonical evidence digest |
| `safe_error_code` | nullable bounded code |
| `safe_error_summary` | nullable generic bounded summary |
| `created_at` | immutable timestamp |

Plan 01 already adds and maintains:

- `ai_model.runtime_revision INTEGER NOT NULL DEFAULT 1`;
- `ai_credential.runtime_revision INTEGER NOT NULL DEFAULT 1`.

Plan 03 adds only `ai_model.current_capability_probe_id`, a nullable current pointer to the new table. Do not add the runtime columns a second time.

Revision rules:

- Preserve Plan 01's revision rules: credential base URL/API-key changes and model name/type changes increment the appropriate runtime revision; display-only/no-op changes do not.
- Extend the same locked mutation transaction so an execution-sensitive credential change clears all associated model pointers and an execution-sensitive model change clears that model's pointer.
- Use one lock order everywhere: credential row first, then affected model rows sorted by ID. Revision increment and pointer clearing commit or roll back together.
- `model_config_digest` includes probe-contract version, model/credential IDs and runtime revisions, normalized model name/type, secret-free endpoint identity digest, Provider protocol, adapter key/revision, and application build revision. It includes no API key, ciphertext, raw base URL query/user-info/fragment, current probe pointer, or timestamp.
- Updating the current pointer must not invalidate the digest.

Probe rows are immutable against UPDATE. The service exposes no delete. Deleting the owning model may cascade its diagnostic history; document this lifecycle exception rather than claiming permanent audit retention.

Every explicit live probe creates a new row, even if its digest matches a prior run, because recency is evidence. `probe_digest` is indexed, not unique.

Current pointer rules:

- Request body has `promote: bool = true`.
- When true, the newly observed probe becomes current even if partial/failed, preventing a stale passing pointer from remaining authoritative.
- When false, record history without changing current.
- If the model/credential revision changes while the live call is in flight, persist the evidence against its original config digest, do not promote it, and return `promotionOutcome=config_changed`. Preserve the actual observations; do not rewrite them into a fake Provider failure.
- Plan 04 requires a current, matching, sufficiently capable probe; a current failed/partial probe makes the model ineligible.
- “Matching” includes probe-contract version, adapter key/revision, application build revision policy, and current model-config digest. An old passing row is history, not current compatibility evidence.

Database details:

- probe rows use `created_at` only, no mutable `updated_at`;
- `capabilities` is PostgreSQL JSONB with an object-type check and is validated by the exact Pydantic schema before insert;
- history order is `(created_at DESC, id DESC)` and pagination is stable;
- `model_id` uses `ON DELETE CASCADE`; `current_capability_probe_id` uses `ON DELETE SET NULL`;
- the ORM declares explicit `foreign_keys` for history and current relationships and no `delete-orphan` on the current pointer;
- a PostgreSQL ownership trigger rejects pointers to another model's probe, and an immutability trigger rejects direct probe UPDATE;
- repeated identical `probe_digest` values are valid independent observations.

### 10.4 API

~~~text
POST /api/ai-models/{model_id}/capability-probe
GET  /api/ai-models/{model_id}/capability-probes?limit=...&offset=...
~~~

POST body:

~~~json
{
  "adapterKey": "openai_chat_completions",
  "confirmProviderCall": true,
  "promote": true
}
~~~

The endpoint inherits the existing AI model configuration boundary. At plan-writing time those routes have no separate auth dependency; do not falsely document one. Because the call incurs cost, `confirmProviderCall=true` is mandatory. Broader admin authentication is outside this plan and should be addressed before exposing configuration routes publicly.

That existing boundary is insufficient by itself for a paid operation. Add `AI_MODEL_CAPABILITY_PROBE_ENABLED=false` (default) and reject live POST before credential decryption while disabled. Enabling it is an operator action and still does not turn the confirmation boolean into authorization. Deployments reachable by untrusted clients must add upstream/admin authentication before enabling. GET history may remain on the existing configuration boundary because it contains safe metadata only.

POST uses a per-model in-process single-flight guard only as a cost safeguard, not as a distributed lock. The database re-lock/config-digest check remains authoritative. Duplicate sequential confirmed requests intentionally create separate evidence rows.

Responses include safe evidence, `isCurrent`, `isStaleForCurrentConfig`, and `promotionOutcome=promoted|not_requested|config_changed`. They never contain prompts, raw Provider bodies/chunks, credentials, raw endpoint URL, headers, nonce, Tool args/results, or decrypted/base64 secret data.

---

## 11. Migration Boundary

Do not hardcode a provisional revision ID.

At implementation time:

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic revision -m "add ai model capability probes"
cd ..
~~~

Use the one actual post-Plan-02 head as `down_revision`. Verify the generated ID is unique across filenames and `revision` declarations. The previously proposed `c9d0e1f2a3b4` is already occupied and forbidden; do not reuse any human-selected hexadecimal pattern.

Migration requirements:

- create probe table, checks, indexes, and FKs;
- add current pointer after probe table;
- use `ON DELETE CASCADE` for owned history and `ON DELETE SET NULL` for the current pointer;
- add PostgreSQL trigger validating current pointer’s probe belongs to the same model;
- add PostgreSQL trigger rejecting direct UPDATE of probe rows;
- allow owned lifecycle deletion explicitly if model deletion cascades;
- create indexes on `(model_id, created_at, id)`, current pointer, status, config digest, and probe digest;
- add named checks for positive probe-contract version, locked statuses, lowercase 64-hex digests, bounded adapter/error fields, and JSON object shape;
- no raw secret migration/backfill;
- existing models start with null current pointer and are ineligible for future Main Agent until probed.

Downgrade:

- clear/drop current pointer and triggers first;
- refuse destructive downgrade while probe rows exist unless the operator explicitly exports/removes diagnostic evidence;
- drop the probe table only after the guard; preserve Plan 01 runtime revision columns;
- restore one sole Alembic head.

SQLite unit tests cover service behavior but do not claim PostgreSQL trigger coverage. A PostgreSQL 15 migration test covers triggers, FK ownership, circular-FK delete order, upgrade/downgrade/upgrade, and existing-row preservation.

At plan-writing time no PostgreSQL test fixture exists. Task 0 must either consume the final Plan 01 isolated fixture/job or create `backend/tests/_postgres.py` plus a CI PostgreSQL 15 service. The helper requires an explicit test-only URL, refuses a database/schema that does not match the test naming guard, creates a unique schema/database per test process, and never targets the default development/production database.

---

## 12. File Responsibility Map

### Create

- `backend/app/assistant/provider_loop/__init__.py`
- `backend/app/assistant/provider_loop/contracts.py`
- `backend/app/assistant/provider_loop/aliases.py`
- `backend/app/assistant/provider_loop/messages.py`
- `backend/app/assistant/provider_loop/streaming.py`
- `backend/app/assistant/provider_loop/scheduler.py`
- `backend/app/assistant/provider_loop/loop.py`
- `backend/app/assistant/provider_loop/scripted_provider.py`
- `backend/app/assistant/provider_loop/adapters/__init__.py`
- `backend/app/assistant/provider_loop/adapters/openai_chat.py`
- `backend/app/assistant/provider_loop/probe.py`
- `backend/app/assistant/provider_loop/runtime.py`
- one generated unique `backend/alembic/versions/<revision>_add_ai_model_capability_probes.py`
- `backend/tests/test_provider_loop_contracts.py`
- `backend/tests/test_provider_aliases.py`
- `backend/tests/test_provider_messages.py`
- `backend/tests/test_provider_streaming.py`
- `backend/tests/test_provider_agent_loop.py`
- `backend/tests/test_provider_multi_tool_calls.py`
- `backend/tests/test_provider_openai_chat_adapter.py`
- `backend/tests/test_provider_model_probe.py`
- `backend/tests/test_provider_model_probe_postgres.py`
- `backend/tests/test_ai_model_capability_probe_api.py`
- `backend/tests/test_provider_loop_gateway_integration.py`
- `backend/tests/test_provider_loop_clean_environment.py`
- `backend/tests/test_ai_registry_service_db.py` — focused revision/pointer transaction coverage if those cases do not fit the existing service test cleanly.

### Modify

- `backend/app/assistant/domain/contracts.py` or the merged public Manifest helper module — append helper/validation only; do not change the Plan 01 v1 schema.
- `backend/tests/test_resolved_run_manifest.py`.
- `backend/app/ai_registry/models.py`
- `backend/app/ai_registry/schemas.py`
- `backend/app/ai_registry/service.py`
- `backend/app/ai_registry/router.py`
- `backend/app/ai_registry/runtime.py`
- `backend/app/config.py`, `backend/.env.example`, and `deploy/.env.example` — default-disabled paid live-probe gate.
- `deploy/docker-compose.yml` — pass the default-disabled live-probe gate to the API container only.
- `backend/alembic/env.py` — register the probe ORM model explicitly.
- `backend/tests/_db.py` only if SQLite metadata registration needs an explicit import; it currently imports the `ai_registry.models` module.
- final Plan 01 PostgreSQL helper/job, or create `backend/tests/_postgres.py` if still absent.
- `backend/tests/test_ai_registry_runtime.py`
- `backend/tests/test_ai_registry_service.py`
- `backend/requirements.txt` — add the clean-resolved direct OpenAI SDK compatible bound.
- `.github/workflows/ci.yml` if the final Plan 01 PostgreSQL/clean Python gate does not run the new migration test.

### Must not modify

- `backend/app/assistant/workflow/engine/agent_execution_core.py`
- Router/Supervisor/Main Assistant service selection.
- OpenClaw runtime.
- Skill search/injection.
- frontend.

---

## 13. Event and Error Safety

Events:

- `loop.started`
- `round.started`
- `round.completed`
- `tool_call.requested`
- `tool_call.started`
- `tool_call.completed`
- `tool_call.failed`
- `loop.waiting`
- `finalization.started`
- `final_text.delta`
- `loop.completed`
- `loop.failed`
- `loop.cancelled`

Events contain round/call indexes, provider alias, Domain Key, Manifest/surface/binding/descriptor/behavior/classification digests, safe status, duration, and usage. They do not contain prompts, full messages, Tool args/results, raw chunks, credentials, or Provider response bodies. Classification-drift events report only expected/current safe digests and `classification_changed`, never descriptor bodies.

Every event includes run ID plus execution-scope digest; it never includes auth tokens or treats Provider alias as an authorization identity. Event sink failures follow Plan 02 policy: contain/diagnose them and never repeat a Provider request or Capability dispatch. Tests inject sink failure before and after side-effect boundaries.

`SafeProviderError` contains:

- semantic code;
- safe summary;
- HTTP status when safe;
- adapter key/revision;
- request ID only when provider-supplied and non-secret;
- retry disposition.

Never persist or return `str(OpenAIError)` directly. SDK exceptions may embed URLs, request bodies, and response bodies.

Run one redaction corpus through Provider errors, Capability errors, probe evidence, logs, events, API responses, and `repr()` values. The corpus includes API keys, Authorization/Cookie headers, URL user-info/query tokens, prompt fragments, Tool arguments/results, fake SQL, and Provider bodies.

---

## 14. Commit and Verification Discipline

- Start every task with failing tests.
- Confirm the intended failure.
- Implement the smallest contract slice.
- Run focused tests.
- Commit only task files.
- Do not stage unrelated user work.
- No live Provider in CI.
- Every migration is tested against PostgreSQL 15.
- The final gate uses a new Python 3.11 environment installed from `backend/requirements.txt`.
- Local drifted venv success is regression evidence only.
- `git diff --check` at each merge checkpoint.
- Use fault injection at every boundary: Tool resolution, alias append, adapter construction, before/after first stream item, assembly, evidence issuance, Gateway start/result, Session close, event sink, probe insert, pointer promotion, and transaction commit.

---

## Task 0: Reconfirm Prerequisites and Build a Clean Python 3.11 Baseline

**Files:**

- Read: final Plan 01/02 contracts and tests.
- Read: `backend/requirements.txt`, `backend/Dockerfile`, `.github/workflows/ci.yml`.
- Read: current Agent/OpenAI/AI Registry code.
- Record only unless this plan needs factual corrections.

**Interfaces:**

- Produces exact post-Plan-02A contract names/readiness revision, the current sole migration parent, and two dependency baselines: project venv and clean Python 3.11.
- Does not change dependencies or code.

- [ ] **Step 1: Verify Plan 01 and approved Plan 02A contracts**

Confirm:

- Plan 01 Manifest v1 already has `schema_version=1`, `provider_aliases=()`, `ResolvedProviderAliasRef`, fixed empty-alias digest vectors, exact ModelRef/ProviderRef runtime/config fields, and `AiModel`/`AiCredential.runtime_revision`;
- the reviewed Plan 02A readiness record exists and says `PLAN_02A_READY=yes` for the exact consumed revision;
- Plan 02 Tasks 0–9 tests pass, including `FrozenCapabilityBinding`, versioned classification/behavior/descriptor contracts, `CapabilityGateway.describe/execute`, safe errors, cancellation, and exact authorization evidence;
- Plan 02 exports classification revision/ruleset, behavior, and descriptor digests and its handoff requires fail-closed reconciliation on drift;
- Plan 03 can construct the Gateway/descriptor verifier without importing OpenClaw or depending on its selector, worker, catalog Schema, grant ceilings, or cleanup state.

Record Plan 02B status (`pending|observing|complete`) as a coordination note only. It is not a Task 0 failure when 02A is approved; OpenClaw legacy removal and production observation remain Plan 02's operational work.

Stop and amend Plan 01 if adding aliases or exact model refs would require changing the v1 Manifest Pydantic shape/canonical payload now. Stop and amend Plan 02 if binding schema bodies, descriptor digests, safe errors, or authorization evidence cannot be reconstructed without mutable state.

- [ ] **Step 2: Record Git and Alembic state**

~~~bash
git status --short
git branch --show-current
git rev-parse --short HEAD
cd backend
.venv/bin/alembic heads
cd ..
~~~

Expected: one head. Record it; do not assume `a7b8c9d0e1f2`.

- [ ] **Step 3: Record current environment**

~~~bash
backend/.venv/bin/python --version
backend/.venv/bin/python - <<'PY'
from importlib.metadata import version, PackageNotFoundError
for name in ("langgraph", "langchain", "langchain-core", "langchain-openai", "openai", "pydantic", "sqlalchemy"):
    try:
        print(name, version(name))
    except PackageNotFoundError:
        print(name, "NOT_INSTALLED")
PY
~~~

- [ ] **Step 4: Create a disposable clean Python 3.11 environment**

Use a directory outside the repository:

~~~bash
clean_env="$(mktemp -d /tmp/mindatlas-plan03-py311-baseline.XXXXXX)"
python3.11 -m venv "$clean_env"
"$clean_env/bin/python" -m pip install --upgrade pip
"$clean_env/bin/python" -m pip install -r backend/requirements.txt
"$clean_env/bin/python" -m pip check
"$clean_env/bin/python" - <<'PY'
from importlib.metadata import version
for name in ("langgraph", "langchain", "langchain-core", "langchain-openai", "openai"):
    print(name, version(name))
PY
~~~

If Python 3.11 or package installation is unavailable, record the blocker. Do not substitute Python 3.12 as final evidence.
Run Step 5 in the same shell or substitute the exact recorded `clean_env` path. If that environment is unavailable, create another `mktemp` environment; never fall back to the project venv silently.

- [ ] **Step 5: Run existing behavior baselines in both environments**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py -q

"$clean_env/bin/python" -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py -q
~~~

Set the same safe test environment variables used by project CI.

- [ ] **Step 6: Inspect exact current Agent defect**

Record line-level evidence that:

- Tool binding occurs once;
- only first Tool Call executes;
- later calls receive no result;
- old stop reasons differ from the new contract.

These are reasons to add a separate loop, not authorization to modify the old one.

- [ ] **Step 7: Inspect dependency ownership**

Confirm `openai` is still transitive-only. Capture the clean resolver's `langchain-openai` requirement on `openai`, the SDK APIs needed by the narrow adapter, and the compatible bound to add in Task 6. Do not update dependencies in Task 0.

- [ ] **Step 8: Verify isolated PostgreSQL 15 test infrastructure**

Inspect the final Plan 01 helper and CI job. Prove it uses an explicit test-only URL/database, reaches PostgreSQL 15, and can run a parent-head -> head -> parent -> head cycle without touching the developer database. If no such gate exists, record creation of `backend/tests/_postgres.py` and the CI service in Task 8.

- [ ] **Step 9: Reconfirm migration ID collision**

~~~bash
rg -n 'revision = "c9d0e1f2a3b4"|revision = .c9d0e1f2a3b4.' \
  backend/alembic/versions
~~~

Expected: the ID is already used. Record that the new migration must be generated.

- [ ] **Step 10: Reconfirm exact model runtime and endpoint safety**

Trace Plan 01 revision bumps through credential/model create/update services and exact ModelRef construction. Verify that no-op/display-only changes behave as documented. Inspect `normalize_openai_base_url`, SSRF validation, and route auth/feature gates; record that live-probe code needs a stricter secret-free endpoint identity and a default-disabled paid-call gate.

- [ ] **Step 11: Commit only factual document corrections**

~~~bash
git add docs/superpowers/plans/2026-07-13-provider-agent-loop-runtime.md
git commit -m "docs(ai): refresh provider loop baseline"
~~~

---

## Task 1: Consume the Reserved Manifest Slots and Define Provider Loop Contracts

**Files:**

- Modify: `backend/app/assistant/domain/contracts.py`
- Modify: manifest digest/helper module.
- Modify: `backend/tests/test_resolved_run_manifest.py`
- Create: `backend/app/assistant/provider_loop/__init__.py`
- Create: `backend/app/assistant/provider_loop/contracts.py`
- Create: `backend/app/assistant/provider_loop/messages.py`
- Test: `backend/tests/test_provider_loop_contracts.py`
- Test: `backend/tests/test_provider_messages.py`

**Interfaces:**

- Consumes final Plan 01 Manifest and Plan 02 Capability contracts.
- Produces the alias-append operation, message/result/execution-scope/continuation contracts, normalized stream events, and pure transcript validation.
- Does not call a Provider, database, Gateway, or Tool.

- [ ] **Step 1: Write failing Manifest alias-revision tests**

Cover:

- Plan 01's fixed v1 base Manifest has `provider_aliases=()` and its pre-Plan-03 digest remains byte-identical;
- appending aliases creates exactly one child revision;
- parent digest/revision chain;
- identical append returns same object/revision;
- existing alias cannot change/disappear;
- same alias cannot map to two bindings under case-folded collision rules;
- same Domain Key at conflicting binding/version is rejected;
- aliases participate in Manifest digest;
- input ordering is canonical;
- a later Skill activation preserves old aliases;
- re-aliasing on resume fails.
- digest dependency vectors prove `binding -> alias ref -> manifest -> alias map -> surface` and reject any reverse/self reference.

- [ ] **Step 2: Confirm expected contract failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_resolved_run_manifest.py -q
~~~

Expected: `ResolvedProviderAliasRef` and the empty v1 slot already exist; only the append helper/Plan03 validation is missing. If the contract type itself is missing, stop and amend Plan 01.

- [ ] **Step 3: Implement alias append without changing Manifest v1**

Use Plan 01’s revision/digest factory. Do not manually mutate/copy `model_dump()` and fill digest fields.

Do not add/redeclare a field, omit empty aliases opportunistically, or introduce a second canonical payload. The v1 empty tuple was reserved by Plan 01 and is part of its fixed payload. Any discrepancy is a prerequisite failure, not a Plan 03 compatibility workaround.

- [ ] **Step 4: Write failing role-specific message tests**

Cover:

- valid system/user/assistant/tool messages;
- Tool Calls only on assistant;
- Tool result only references prior call;
- duplicate/cross-round call IDs;
- contiguous indexes;
- empty/invalid aliases;
- JSON-only arguments/results;
- stable arguments/message/transcript digests;
- descriptor/behavior/classification revision/ruleset fields are copied from the same surface definition, participate in call/message/transcript digests, and reject independent tampering;
- assistant text plus calls retained;
- no arbitrary Provider object/SDK type accepted.
- runtime-instruction messages cannot be constructed as user messages and never become final text;
- Tool Result envelope is a safe lossless projection of each terminal Capability result;
- normalized stream-event union rejects unknown event types/extra SDK data.

- [ ] **Step 5: Write failing result/continuation tests**

Cover every status/stop reason combination, plus:

- waiting requires continuation;
- non-waiting forbids continuation;
- continuation identifies one open assistant message;
- continuation retains the full exposed surface and exact child `ContinuationRef`;
- pending IDs/order match transcript;
- resume transcript/Manifest/surface digest mismatch;
- resume classification revision/ruleset, behavior digest, or descriptor digest mismatch;
- execution scope/model/config/locale/max-round/rounds-used/usage tampering;
- scope A evidence/surface/continuation reused for scope B;
- a raw `ProviderToolMessage` or another `waiting` result cannot satisfy resume;
- result round count/usage bounds;
- `max_rounds` validation;
- no callback/client/Session/future serialization.
- `CurrentCapabilityDescriptorVerifier` is a runtime port only and cannot enter messages/surfaces/continuations/results.

- [ ] **Step 6: Write transcript validator tests**

Scenarios:

- direct text;
- one paired call;
- multiple paired calls;
- missing result;
- duplicate result;
- wrong order;
- Tool result before call;
- nested assistant message before pairing;
- one valid waiting exception;
- terminal cancellation seal.
- deferred sibling status exists only in continuation records and is not encoded as a premature Tool message.

- [ ] **Step 7: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_messages.py -q
~~~

- [ ] **Step 8: Implement contracts and pure validators**

Expose:

~~~python
def digest_provider_message(message: ProviderMessage) -> str: ...
def digest_provider_transcript(messages: tuple[ProviderMessage, ...]) -> str: ...
def validate_provider_transcript(...) -> None: ...
def seal_cancelled_continuation(...) -> tuple[ProviderMessage, ...]: ...
~~~

Use canonical JSON helpers. Cancellation sealing generates only protocol Tool Result envelopes; it does not call or claim to cancel business targets. Add pure recomputation helpers for scope, message, transcript, Tool Result, continuation, and usage aggregation.

- [ ] **Step 9: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_messages.py -q
~~~

- [ ] **Step 10: Commit**

~~~bash
git add backend/app/assistant/domain/contracts.py \
  backend/app/assistant/domain/digests.py \
  backend/app/assistant/provider_loop/__init__.py \
  backend/app/assistant/provider_loop/contracts.py \
  backend/app/assistant/provider_loop/messages.py \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_messages.py
git commit -m "feat(ai): define provider loop state contracts"
~~~

---

## Task 2: Implement Deterministic Alias Mapping and Frozen Tool Surfaces

**Files:**

- Create: `backend/app/assistant/provider_loop/aliases.py`
- Test: `backend/tests/test_provider_aliases.py`
- Extend: `backend/tests/test_provider_loop_contracts.py`

**Interfaces:**

- Consumes current Manifest, frozen visible bindings/descriptors, provider protocol constraints, reserved aliases, and optional author hints.
- Produces an append-only Manifest alias revision and one complete `ProviderToolSurface`.
- Does not authorize execution or mutate existing aliases.

- [ ] **Step 1: Write fixed alias vectors**

Include:

- `skill.inject`;
- `human/request-input`;
- underscores/hyphens/dots/slashes/spaces;
- uppercase;
- Chinese-only Domain Key;
- emoji;
- empty sanitized value;
- 48/49/64/65 lengths;
- two normalized collisions;
- case-only collision;
- digest-prefix collision fixture;
- valid/invalid hints;
- reserved control alias;
- colliding hints;
- same Domain Key with different binding digest.
- same Domain Key under two Provider protocols;
- an attempted alias ref whose binding digest depends on alias/Manifest/surface data.

Hard-code expected aliases/digests.

- [ ] **Step 2: Write append-only growth tests**

Build surface A, then append a new Domain Key whose normalized form would sort before/collide with an existing key. Assert all existing aliases remain byte-identical and only the new alias gains a suffix.

- [ ] **Step 3: Write forward/reverse map tests**

Assert:

- exact one-to-one mapping;
- lookup by unknown alias fails before dispatcher;
- case-fold collision rejected even if exact strings differ;
- reverse lookup returns Domain Key plus binding digest;
- map and surface digest stable across input ordering;
- description/schema/descriptor/behavior/classification revision or ruleset change changes surface digest;
- locale text is frozen for the surface.
- exact scope is passed to ToolsProvider and scope-dependent visibility cannot reuse another scope's surface;
- empty surfaces have deterministic alias-map/surface digests;
- the Plan 01 empty-alias Manifest digest remains unchanged before the first append.

- [ ] **Step 4: Confirm intended failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_aliases.py -q
~~~

- [ ] **Step 5: Implement alias candidates and collision allocation**

Separate:

~~~python
def generated_alias_candidate(...) -> str: ...
def allocate_provider_aliases(...) -> tuple[ResolvedProviderAliasRef, ...]: ...
def build_provider_tool_surface(...) -> ToolSurfaceResolution: ...
~~~

Never derive permission from the hint. Hints affect transport spelling only.

The allocator consumes Plan 01's reserved alias type and revision factory. It never writes a second alias field or recomputes `binding_contract_digest`. Assert the digest dependency DAG before appending.

- [ ] **Step 6: Validate surface completeness**

Fail if:

- binding/descriptor keys differ;
- binding digest differs from Manifest ref;
- unavailable descriptor included;
- duplicate Domain Key/binding;
- missing alias;
- stale alias protocol;
- schema invalid for Provider Tool input.
- descriptor unavailable or `legacy_blocking`;
- descriptor/binding/schema/resolution digest disagreement;
- descriptor behavior digest or classification revision/ruleset disagreement with the stamped Provider call/surface;
- scope/run/model mismatch;
- an open database Session or mutable ORM object appears in the frozen result.

- [ ] **Step 7: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_aliases.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_resolved_run_manifest.py -q
~~~

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/provider_loop/aliases.py \
  backend/tests/test_provider_aliases.py \
  backend/tests/test_provider_loop_contracts.py
git commit -m "feat(ai): freeze provider tool aliases"
~~~

---

## Task 3: Add the Scripted Provider and Core Direct/Single-Call Loop

**Files:**

- Create: `backend/app/assistant/provider_loop/scripted_provider.py`
- Create: `backend/app/assistant/provider_loop/loop.py`
- Test: `backend/tests/test_provider_agent_loop.py`

**Interfaces:**

- Consumes a request and injected ports.
- Produces deterministic direct-answer and one-Tool multi-round results.
- Does not yet implement parallel siblings, live OpenAI, persistence, or Main Agent integration.

- [ ] **Step 1: Define a deterministic scripted adapter**

It accepts a queue of expected round requests and scripted normalized stream events. It asserts:

- exact execution scope, ModelRef, adapter/config digest, and generation options;
- exact messages;
- exact Tool definitions/surface digest;
- tools enabled/disabled;
- finalization flag;
- round index.
- contiguous event sequence and exactly one terminal event.

It records request count and raises readable test-only assertion failures.

- [ ] **Step 2: Write direct-answer scenario**

Assert:

- cancellation checked;
- execution-scope/model/adapter digests validated before any port call;
- `tools_provider` called once with the exact scope/locale immediately before Provider;
- any alias revision becomes current Manifest;
- Provider receives complete surface;
- text is buffered then replayed;
- no dispatcher call;
- no authorization evidence issued;
- status/stop reason/final text/messages/round count/usage/events.

- [ ] **Step 3: Write one-Tool-then-answer scenario**

Round 1 returns one call; dispatcher returns completed Capability result and unchanged Manifest. Round 2 returns final text.

Assert:

- same assistant call message retained;
- one exact Tool Result appended before round 2;
- dispatcher sees Domain Key, binding, call ID, original Manifest/surface;
- fresh evidence is issued for the exact scope/call/descriptor and verified once by the fake Gateway;
- the pre-plan descriptor verifier re-describes the exact binding before the dispatcher is called, and the fake dispatcher independently repeats equality before evidence issuance;
- `tools_provider` called again before round 2;
- final text excludes round-1 provisional prose;
- Tool call record and events complete.

- [ ] **Step 4: Write dynamic next-Manifest scenario**

Dispatcher returns an append-only next Manifest. Before round 2, `tools_provider` returns a new surface. Assert:

- round-1 siblings remain bound to old surface;
- round 2 sees the new Tool;
- existing aliases unchanged;
- no Skill-specific control name is hardcoded.
- returned Manifest lineage is validated before round 2.

- [ ] **Step 5: Write normal Tool failure scenario**

Dispatcher returns safe `execution_failed/model_may_continue`. Assert a `failed` Tool Result reaches round 2 and Provider can answer. No exception string/raw output leaks.

- [ ] **Step 6: Write early failures**

Cover:

- cancellation before round;
- tools provider failure;
- invalid surface;
- scope/run/tenant/model/config mismatch;
- adapter key/revision/config mismatch before a scripted Provider request;
- Provider error;
- empty response;
- unknown alias;
- invalid call arguments;
- duplicate call ID;
- dispatcher fatal capability error.
- classification ruleset/revision bump, behavior digest change, descriptor digest change, availability loss, and `parallel_safe` flip between surface exposure and pre-plan verification; every case dispatches nothing and emits one blocked plus deterministic `cancelled_before_start` pairing as applicable;
- authorization-evidence factory failure or evidence for the wrong scope;
- returned Manifest ancestor/unrelated Run/changed model/existing-binding rewrite.

Fatal errors must pair/seal any call already present before returning terminal.

- [ ] **Step 7: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_agent_loop.py -q
~~~

- [ ] **Step 8: Implement the smallest loop**

Implement start, one-call sequential dispatch, direct completion, classification-freshness verification, and error sealing. Call pure scope/identity/transcript validators before every Provider request and terminal result. Require current descriptor equality before planning, then require the dispatcher to repeat it before issuing authorization immediately before dispatch; do not store authorization in the surface or transcript.

Do not add retries. Do not special-case `skill.inject`.

- [ ] **Step 9: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_messages.py \
  backend/tests/test_provider_aliases.py -q
~~~

- [ ] **Step 10: Commit**

~~~bash
git add backend/app/assistant/provider_loop/scripted_provider.py \
  backend/app/assistant/provider_loop/loop.py \
  backend/tests/test_provider_agent_loop.py
git commit -m "feat(ai): add dynamic provider agent loop"
~~~

---

## Task 4: Implement Streaming Assembly, Event Ordering, and Soft Finalization

**Files:**

- Create: `backend/app/assistant/provider_loop/streaming.py`
- Modify: `backend/app/assistant/provider_loop/loop.py`
- Test: `backend/tests/test_provider_streaming.py`
- Extend: `backend/tests/test_provider_agent_loop.py`

**Interfaces:**

- Consumes normalized Provider stream events for one round.
- Produces one protocol-validated `ProviderRoundResult`.
- Does not dispatch Tools or emit provisional final text.

- [ ] **Step 1: Write stream assembly vectors**

Cover:

- sequence starts at nonzero, gaps, duplicate sequence, and out-of-order sequence;
- one text chunk;
- many text chunks;
- text plus one Tool Call;
- interleaved text/Tool Call fragments;
- two Tool Calls fragmented out of order by call index;
- arguments split across chunks;
- terminal usage;
- decreasing/negative/inconsistent usage snapshots;
- missing finish reason;
- unsafe/oversized request ID discarded;
- duplicate terminal event;
- chunk after terminal;
- Provider error before/after chunks;
- cancellation during stream;
- empty stream.

- [ ] **Step 2: Write malformed Tool Call tests**

Cover:

- changing ID;
- changing name;
- duplicate ID;
- invalid/gapped index;
- non-function type;
- invalid JSON arguments;
- JSON scalar/array arguments;
- unknown alias;
- arguments exceeding a bounded adapter limit;
- missing alias/name.
- identity or argument aggregate byte limit exceeded;

No dispatcher is called on malformed assembly.

- [ ] **Step 3: Write buffered visibility tests**

Assert:

- a natural answer’s chunks are replayed only after successful terminal assembly;
- tool-call-round text is retained in assistant history;
- tool-call-round text emits no `final_text.delta`;
- Provider error/cancel discards buffered text;
- event ordering is deterministic.
- event-sink failure before Provider, during stream, after Tool dispatch, and during replay never duplicates a request/call;
- runtime finalization instructions never appear in `final_text.delta`.

- [ ] **Step 4: Write soft-finalization tests**

Scenarios:

- one or more Tool rounds then natural final round before max;
- Tool use through round `max_rounds-1` then reserved tools-disabled round;
- finalization returns text;
- finalization returns Tool Calls;
- finalization empty;
- finalization Provider error;
- `max_rounds=1` with nonempty surface;
- direct answer with no prior Tools at first round.
- wait at `max_rounds-1`, then resume into the one reserved finalization round without resetting counters;
- finalization instruction is appended once and digested as an internal message.

Assert exact request counts and stop reasons.

- [ ] **Step 5: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py -q
~~~

- [ ] **Step 6: Implement one-round assembler**

Expose:

~~~python
class ProviderRoundAssembler:
    def accept(self, event: ProviderStreamEvent) -> None: ...
    def finish(self) -> ProviderRoundResult: ...
~~~

The assembler receives the exact reverse alias map/surface and stamps resolved Domain Key, binding digest, descriptor digest, Manifest, and surface data into Tool Calls. It must not look up aliases globally or retain SDK chunks after completion.

- [ ] **Step 7: Implement finalization policy**

Provide one pure decision helper:

~~~python
def is_finalization_round(
    *,
    round_index: int,
    max_rounds: int,
    prior_tool_call_count: int,
) -> bool: ...
~~~

The localized prompt is injected through a small `FinalizationInstructionProvider`. Tests lock zh/en content semantics without coupling business prompts.

- [ ] **Step 8: Implement loop event emission**

Emit IDs/digests/counts only. Event sink failure must not duplicate Provider or Tool calls. Decide and test whether sink errors are logged/contained or fail before side effects; use the same policy as Plan 02 events.

- [ ] **Step 9: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_messages.py -q
~~~

- [ ] **Step 10: Commit**

~~~bash
git add backend/app/assistant/provider_loop/streaming.py \
  backend/app/assistant/provider_loop/loop.py \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py
git commit -m "feat(ai): assemble provider streams and finalize softly"
~~~

---

## Task 5: Add Complete Multi-Call Scheduling, Waiting, Resume, and Cancellation Sealing

**Files:**

- Create: `backend/app/assistant/provider_loop/scheduler.py`
- Modify: `backend/app/assistant/provider_loop/loop.py`
- Test: `backend/tests/test_provider_multi_tool_calls.py`
- Extend: `backend/tests/test_provider_loop_contracts.py`
- Extend: `backend/tests/test_provider_messages.py`

**Interfaces:**

- Consumes all Tool Calls from one assistant message and exact frozen descriptor metadata.
- Produces ordered Tool Results, next Manifest, or one portable waiting continuation.
- Does not persist, reopen Provider streams, or parallelize unsafe calls.

- [ ] **Step 1: Write pure scheduling-plan tests**

Call sequences:

- read-safe/read-safe;
- read-safe/write;
- write/read-safe;
- compute-safe/read-safe/write/read-safe;
- Agent;
- draft;
- unknown;
- interrupt-capable;
- `legacy_blocking` versus `durable` interrupt;
- parallel-safe false;
- dispatcher parallel unsupported.

Assert exact group boundaries/modes and Provider order.

Before invoking the pure planner, add a verifier fixture for every call. A classification revision/ruleset bump, behavior change, descriptor change, availability loss, or `parallel_safe` flip must fail before the planner is called; stale `parallel_safe=true` is never consumed as scheduling permission.

- [ ] **Step 2: Write bounded-parallel tests**

Use barriers/events, not sleeps. Prove:

- eligible calls overlap;
- maximum worker bound;
- each dispatcher has a distinct Session/context identity;
- every Session is closed in the worker even on exception/cancellation;
- each call receives newly issued evidence for the same exact scope;
- completion can occur out of order;
- Tool messages append in original order;
- no shared mutable Manifest object;
- started calls finish honestly before terminal result.
- classification remains equal during pre-plan verification but changes before one worker's pre-dispatch check; that worker is blocked without Gateway execution, already started siblings finish honestly, and ordered sealing remains deterministic.

Add a guard dispatcher that fails if used from two threads or with the parent Session.

Run a second guard with two synthetic tenant/principal/run scopes and identical Domain Keys. Assert no surface, evidence, Session, result, or cancellation state crosses scopes.

- [ ] **Step 3: Write sequential unsafe tests**

Prove writes, drafts, Agents, unknown, and interrupt-capable calls never overlap. A fake write records start/end order.

- [ ] **Step 4: Write mixed failure tests**

Cover:

- recoverable failure in safe parallel group;
- fatal policy/version failure;
- exception from executor infrastructure;
- cancellation before group;
- cancellation while safe calls run;
- cancellation between groups;
- one call changes next Manifest;
- two sequential Manifest-mutating calls receive the original exposed surface but append cumulatively through `current_manifest`;
- one worker unexpectedly returns `waiting`;
- one worker returns evidence/result stamped for another scope;
- Session factory/close failure;
- one unchanged result plus one child, identical children, and two conflicting children.
- fatal call index 1 completes first while successful indexes 0 and 2 complete later: all three started results are retained in index order, no success is overwritten, and only later groups that never started are `cancelled_before_start`.

Rules:

- recoverable results are ordered and loop may continue;
- fatal error blocks current/cancels later unstarted calls, seals, stops;
- no started call is falsely marked unstarted;
- Manifest changes merge only through validated append-only lineage; a sequential child becomes the next mutation base, while conflicting parallel sibling children are a protocol error rather than last-writer-wins.

For parallel siblings that both return different child Manifest revisions from the same parent, define a deterministic merge port or reject. Plan 03 default rejects conflicting children; control/Manifest-mutating calls are non-parallel.

- [ ] **Step 5: Write waiting scenario**

Calls: completed read, waiting interrupt, later write, later read.

Assert:

- first result retained;
- waiting call has no fabricated result;
- later calls not started;
- no next Provider request;
- result status/reason/continuation;
- continuation pending IDs and next index;
- exact child `ContinuationRef`, original full exposed surface, latest current Manifest, execution scope, model/config, locale, rounds used, usage, and completed call records;
- waiting result with unchanged Manifest and with one valid append-only child; both preserve the original exposed surface while recording the correct latest current Manifest;
- waiting is accepted only from a `durable` descriptor;
- later siblings are internally `deferred` without Provider Tool messages;
- transcript validator allows exactly this open state.
- classification revision/ruleset and behavior/descriptor digests in the waiting call match the full exposed surface.

- [ ] **Step 6: Write resume scenario**

Supply exact transcript/continuation plus an internal trusted `ProviderWaitingResolution`. Assert:

- all digests validated;
- resolved call ID and exact child `ContinuationRef` match;
- Capability result is terminal and is converted to the original alias/Domain Key by the loop rather than trusted from the caller;
- waiting result inserted in call order;
- remaining calls execute from original frozen assistant message;
- same round surface/bindings used;
- next Provider request occurs only after full pairing;
- next round may use latest returned Manifest/surface.
- fresh authorization evidence is issued for every remaining sibling;
- preserved round/usage counters force finalization at the same boundary as an uninterrupted run.
- unchanged current classification permits resume; a ruleset bump, conservative/permissive behavior change, `parallel_safe` flip, or availability loss after waiting inserts the trusted terminal child result, cancels the pending suffix, seals, and returns fatal classification drift with no new dispatch/Provider request.

- [ ] **Step 7: Write resume tamper tests**

Change one at a time:

- Manifest revision/digest;
- surface digest;
- assistant message;
- transcript;
- waiting ID;
- pending order;
- Tool arguments;
- binding digest;
- descriptor digest/full exposed surface;
- execution scope/principal/tenant/run/conversation;
- ModelRef/config digest/adapter revision/locale;
- max rounds/rounds used/prior-call count/usage;
- child `ContinuationRef` or resolved call ID;
- a second `waiting` Capability result.

Every case fails `protocol_error` with no dispatch/Provider call.

Distinguish tampering from legitimate runtime drift: tampered continuation fields are `protocol_error`; a byte-valid old continuation whose current Plan 02 classification changed is `version_drift/classification_changed` and follows the honest waiting-result sealing rule above.

- [ ] **Step 8: Write terminal cancellation sealing**

For waiting state, first supply a trusted fake child-cancellation outcome, then seal and assert:

- waiting call receives `cancelled`;
- pending calls receive `cancelled_before_start`;
- results in original order;
- transcript fully paired;
- terminal result `cancelled`;
- no business dispatch.

Also assert that calling the protocol sealer alone does not invoke or claim to cancel a child. Durable child cancellation, persistence, and authorization remain Plans 06–07 work.

- [ ] **Step 9: Add bounded seeded invariant sequences**

Generate a fixed number of reproducible scripted assistant-call/result sequences covering completed, recoverable failure, fatal failure, cancellation, one durable wait/resume, safe parallel groups, classification drift at pre-plan/pre-dispatch/resume, and Manifest no-op/child results. For every seed assert: no Provider request sees an open transcript; every terminal transcript is paired; result order matches call order; stale parallel permission is never used; writes/unknown/Agent never overlap; aliases reverse exactly; round budgets never reset. Record the seed on failure and keep the CI case count bounded.

- [ ] **Step 10: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_multi_tool_calls.py -q
~~~

- [ ] **Step 11: Implement pure planner and executors**

Expose:

~~~python
def plan_sibling_execution(
    calls: tuple[ProviderToolCall, ...],
    *,
    dispatcher_capabilities: DispatcherCapabilities,
) -> tuple[SiblingExecutionGroup, ...]: ...


class SequentialSiblingExecutor: ...
class BoundedIsolatedSiblingExecutor: ...
~~~

Production/test-only runtime defaults to sequential unless an isolated dispatcher factory is supplied. Eligibility does not force threads.

The loop performs a complete `CurrentCapabilityDescriptorVerifier` pass before calling `plan_sibling_execution`. The dispatcher factory used by both sequential and bounded executors owns the second `Gateway.describe` check immediately before evidence/Gateway execution. Do not memoize either check across a Provider round, resume boundary, deployment, or worker context.

- [ ] **Step 12: Implement start/resume/seal APIs**

~~~python
class ProviderAgentLoop:
    def start(self, request: ProviderLoopRequest, *, ports: ProviderLoopPorts) -> ProviderLoopResult: ...
    def resume(self, request: ProviderLoopResumeRequest, *, ports: ProviderLoopPorts) -> ProviderLoopResult: ...
    def seal_waiting_after_cancellation(...) -> ProviderLoopResult: ...
~~~

Share one internal state machine; do not duplicate protocol logic across methods.

- [ ] **Step 13: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_messages.py -q
~~~

- [ ] **Step 14: Commit**

~~~bash
git add backend/app/assistant/provider_loop/scheduler.py \
  backend/app/assistant/provider_loop/loop.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_messages.py
git commit -m "feat(ai): schedule provider sibling calls safely"
~~~

---

## Task 6: Implement the OpenAI Chat Completions Round Adapter

**Files:**

- Create: `backend/app/assistant/provider_loop/adapters/__init__.py`
- Create: `backend/app/assistant/provider_loop/adapters/openai_chat.py`
- Test: `backend/tests/test_provider_openai_chat_adapter.py`
- Test: `backend/tests/test_provider_loop_clean_environment.py`
- Modify: `backend/requirements.txt` only for direct compatible OpenAI SDK bound.

**Interfaces:**

- Consumes one `ProviderRoundRequest` and existing AI registry runtime config.
- Produces normalized stream events for one round.
- Does not loop, execute Tools, resolve aliases, retry after dispatch, or expose SDK objects.

- [ ] **Step 1: Lock the direct SDK dependency**

Based on Task 0 clean resolution, add an explicit range only after recording the intersection between merged `langchain-openai` metadata and the SDK APIs covered by the adapter tests. Do not paste a guessed example range. Use the narrowest proven range, recreate the clean environment after editing requirements, run `pip check`, and record resolved versions.

- [ ] **Step 2: Build a fake OpenAI-compatible HTTP server fixture**

Use a local ephemeral server that:

- validates Authorization and JSON request shape without logging secret;
- emits real `text/event-stream` Chat Completions chunks;
- records request messages/Tools;
- scripts HTTP errors, abrupt close, delays, and malformed streams.
- supports a structured optional-parameter 400 and proves the adapter removes only that parameter before the first stream item.

No monkeypatch of private SDK response classes is sufficient for the primary adapter tests.
The server is reached only through an explicit test transport marker/factory so production SSRF validation remains strict.

- [ ] **Step 3: Write request encoding tests**

Assert:

- normalized base URL;
- model name;
- `stream=true`, `n=1`;
- exact output-token/temperature/tool-choice/parallel-call options when supported;
- usage option/fallback behavior;
- complete messages;
- all Tool definitions with frozen aliases/schemas;
- no Tool list when disabled/finalizing;
- complete assistant multi-call history;
- one Tool message per call ID;
- canonical JSON Tool Result envelopes;
- runtime finalization instruction encoded at system level and never as a user message;
- no Domain Key leakage as Tool name unless alias matches;
- no prompt/request body logs.

- [ ] **Step 4: Write streamed text and usage tests**

Cover standard chunks, content arrays/strings if supported, event-sequence normalization, finish reason, cumulative usage, bounded/sanitized request ID, empty choice, and multiple choices rejection.

- [ ] **Step 5: Write Tool Call fragment tests**

Cover:

- one call split across ID/name/argument chunks;
- two interleaved calls;
- missing IDs and deterministic synthesis;
- duplicate IDs;
- changing names/IDs;
- invalid argument JSON;
- provider finish reason mismatch.

Compatibility warnings must be safe and deterministic.

- [ ] **Step 6: Write error/cancellation/timeout tests**

Cover:

- 400/401/429/500 with secret-bearing body;
- connection failure;
- native timeout;
- cancellation before request;
- cancellation during stream;
- abrupt stream close after text/call fragment;
- SDK internal exception.
- exact ModelRef/runtime-revision/config-digest mismatch before HTTP I/O;
- base URL user-info/query/fragment and fresh SSRF failure;
- optional-parameter 400 before first item, the same error after first item, and arbitrary 400 text that must not trigger negotiation;
- total-stream timeout distinct from connect/read timeout.

Assert `SafeProviderError` and captured logs contain no body, key, header, prompt, Tool args, or arbitrary exception string.

- [ ] **Step 7: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_openai_chat_adapter.py -q
~~~

- [ ] **Step 8: Implement transport/client factory**

Use dependency injection:

~~~python
class OpenAIChatClientFactory:
    def build(self, config: ExactOpenAIChatRuntimeConfig) -> OpenAI: ...
~~~

The exact runtime config contains model/credential IDs and revisions, adapter/build/config digest, normalized secret-free endpoint identity, and ephemeral API key. Set native timeout and `max_retries=0`. Revalidate revisions, endpoint, and SSRF before decrypt/build. Do not store the client or secret config in frozen contracts.

- [ ] **Step 9: Implement one-round encoding/streaming**

The adapter converts SDK chunks to provider-neutral events. `ProviderRoundAssembler` owns final assembly/validation.

Do not call `ChatOpenAI.bind_tools`. This isolates the new loop from LangChain version drift.

Emit only the normalized events from Section 6.3. Never pass an SDK chunk, SDK message model, response object, or exception outside the adapter module.

- [ ] **Step 10: Run normal and clean-environment tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py -q

clean_env="$(mktemp -d /tmp/mindatlas-plan03-py311-adapter.XXXXXX)"
python3.11 -m venv "$clean_env"
"$clean_env/bin/python" -m pip install --upgrade pip
"$clean_env/bin/python" -m pip install -r backend/requirements.txt
"$clean_env/bin/python" -m pip check
"$clean_env/bin/python" -m pytest \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py -q
~~~

Record the exact clean environment path and resolved versions. Any later dependency change requires another new environment; do not reuse the Task 0 baseline.

- [ ] **Step 11: Run old OpenAI/Agent regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_agent_test_run_stream.py -q
~~~

- [ ] **Step 12: Commit**

~~~bash
git add backend/requirements.txt \
  backend/app/assistant/provider_loop/adapters/__init__.py \
  backend/app/assistant/provider_loop/adapters/openai_chat.py \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_loop_clean_environment.py
git commit -m "feat(ai): adapt openai chat provider rounds"
~~~

---

## Task 7: Implement Harmless Model Capability Probe Orchestration

**Files:**

- Create: `backend/app/assistant/provider_loop/probe.py`
- Test: `backend/tests/test_provider_model_probe.py`
- Reuse: scripted Provider and OpenAI fake server.

**Interfaces:**

- Consumes an exact model runtime config, adapter revision, and explicit probe policy.
- Produces immutable safe probe evidence ready for persistence.
- Does not write the database, promote a pointer, use business data, or run in CI against a live provider.

- [ ] **Step 1: Define validated evidence contracts**

Create:

~~~python
ProbeObservation = Literal["passed", "failed", "not_observed"]

class ModelCapabilityProbeEvidence(FrozenContract):
    probe_contract_version: Literal[1] = 1
    adapter_key: str
    adapter_revision: str
    model_config_digest: str
    status: Literal["passed", "partial", "failed"]
    capabilities: ModelCapabilityObservations
    probe_digest: str
    safe_error_code: str | None
    safe_error_summary: str | None
~~~

Validate all capability keys explicitly; reject extra JSON. `probe_digest` covers probe-contract version, adapter identity, model-config digest, observations, compatibility warnings/reason codes, and safe error fields; it excludes row ID, timestamp, nonce, raw text, and promotion outcome.

- [ ] **Step 2: Write fully passing scripted scenario**

Assert all required observations pass, final status `passed`, digest deterministic for the same normalized evidence, and no raw content is present.

Run the exact same scenario once through the scripted adapter and once through the local fake OpenAI HTTP server so orchestration and wire encoding cannot drift independently.

- [ ] **Step 3: Write partial scenarios**

Independently fail/not-observe:

- multi-call;
- stable IDs;
- JSON Schema args;
- Tool Result continuation;
- finalization;
- streaming.

Assert status `partial` and safe reason codes.

Distinguish “valid but only one Tool chosen” (`not_observed`) from a malformed multi-call response (`failed`). Assert adapter-synthesized IDs do not pass stable-ID support.

- [ ] **Step 4: Write failed scenarios**

Cover auth/connection/timeout/protocol failure before useful observation. Assert status `failed` and later phases `not_observed`.

- [ ] **Step 5: Write privacy and cost-bound tests**

Inject fake secrets/business text in:

- API key;
- base URL query;
- SDK error/body;
- stream text;
- Tool arguments/results.

Assert evidence, digest payload, logs, and public serialization contain none.

Assert:

- only fixed local Tools execute;
- fixed maximum Provider requests;
- fixed output token cap;
- fixed aggregate token and Tool-result byte caps;
- total native timeout;
- no general retry; at most the Section 9 optional-parameter negotiation before any stream item;
- cancellation stops remaining phases.
- no business Gateway/Tool/Skill is imported or invoked;
- exact request count for full pass and for failure at every phase.

- [ ] **Step 6: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_provider_model_probe.py -q
~~~

- [ ] **Step 7: Implement pure orchestration**

Separate:

~~~python
def build_model_config_digest(...) -> str: ...
def run_model_capability_probe(...) -> ModelCapabilityProbeEvidence: ...
~~~

Build the config digest from the exact Plan 01 model/runtime refs plus a secret-free endpoint identity; reject raw URL user-info/query/fragment. The evidence digest excludes timestamps so content is deterministic; database row creation time supplies recency. Each live run still creates a distinct row.

- [ ] **Step 8: Run focused adapter/probe tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_model_probe.py \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_multi_tool_calls.py -q
~~~

- [ ] **Step 9: Commit**

~~~bash
git add backend/app/assistant/provider_loop/probe.py \
  backend/tests/test_provider_model_probe.py
git commit -m "feat(ai): probe provider model capabilities"
~~~

---

## Task 8: Persist Probe Evidence, Extend Revision Invalidation, and Add an Explicit Live-Probe API

**Files:**

- Create: generated unique Alembic migration.
- Modify: `backend/app/ai_registry/models.py`
- Modify: `backend/app/ai_registry/schemas.py`
- Modify: `backend/app/ai_registry/service.py`
- Modify: `backend/app/ai_registry/router.py`
- Modify: `backend/app/ai_registry/runtime.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `backend/alembic/env.py`
- Test: `backend/tests/test_provider_model_probe.py`
- Create: `backend/tests/test_provider_model_probe_postgres.py`
- Create: `backend/tests/test_ai_model_capability_probe_api.py`
- Modify: `backend/tests/test_ai_registry_runtime.py`
- Modify: `backend/tests/test_ai_registry_service.py`
- Create: `backend/tests/test_ai_registry_service_db.py`
- Create/modify only if absent after Plan 01: `backend/tests/_postgres.py`
- Modify if required: `.github/workflows/ci.yml`

**Interfaces:**

- Consumes safe evidence from Task 7.
- Produces immutable history, current pointer, extension of Plan 01 revision invalidation, default-disabled list/run API, and PostgreSQL evidence.
- Does not enforce Main Agent eligibility yet.

- [ ] **Step 1: Reconfirm and generate the real migration**

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic revision -m "add ai model capability probes"
cd ..
~~~

Verify:

- exactly one parent;
- generated revision ID is unused;
- it is not `c9d0e1f2a3b4`;
- the migration does not add Plan 01 runtime revision columns again;
- ORM metadata imports the new model.

- [ ] **Step 2: Write failing ORM/service tests**

Cover:

- Plan 01 runtime revisions remain present/default correctly and are not recreated;
- create probe row;
- repeated identical evidence creates another row;
- history newest-first with pagination;
- promote true sets pointer for passed/partial/failed;
- promote false leaves pointer unchanged;
- pointer cannot reference another model’s probe;
- probe update has no service method;
- model/credential safe response never exposes revisions unless intentionally added;
- model deletion lifecycle behavior explicit.
- current pointer FK is `SET NULL`, history ownership is `CASCADE`, and ORM relationships use explicit foreign keys;
- probe has no `updated_at`, service update/delete method, or unique constraint on `probe_digest`;
- stable newest-first `(created_at, id)` pagination and config-digest stale marker.

- [ ] **Step 3: Write revision invalidation tests**

Credential update:

- display name only: no increment/invalidation;
- base URL: increment and clear all associated model pointers;
- API key: increment and clear;
- failed transaction: neither revision nor pointer changes.
- two associated models are locked in sorted order and all pointers clear atomically;
- semantically unchanged normalized base URL does not increment; any supplied API-key replacement does.

Model update:

- name: increment/clear;
- type: increment/clear;
- no-op update: no increment;
- component binding change: no model runtime revision.

Probe promotion itself must not increment model runtime revision.

Add concurrency tests with two Sessions: probe in flight versus credential update, probe promotion versus model update, and two promotions. Assert the credential -> sorted model lock order, no deadlock in the bounded test, exact config recheck, and no stale promotion.

- [ ] **Step 4: Write API contract tests**

POST:

- feature gate disabled returns before decrypt/Provider/single-flight acquisition;
- missing/false confirmation rejected before decrypt/Provider call;
- unknown/non-LLM model;
- missing credential;
- decrypt failure;
- passed/partial/failed response;
- promote semantics;
- adapter key validation;
- Provider called once per explicit request.
- concurrent same-process POST for one model is rejected as already running without a second call;
- config change during call persists history, does not promote, and returns `promotionOutcome=config_changed`;
- URL user-info/query/fragment or fresh SSRF rejection occurs before decrypt/call;
- response redaction corpus contains no raw endpoint, nonce, prompt, Tool data, or secret.

GET:

- limit/offset bounds;
- current marker;
- safe fields only;
- 404 model;
- deterministic ordering.
- `isCurrent`, `isStaleForCurrentConfig`, and safe promotion metadata.

- [ ] **Step 5: Write PostgreSQL migration tests before implementation**

Against a parent-revision database with existing credentials/models/bindings:

1. Upgrade new head.
2. Existing rows preserve their Plan 01 revisions and have null pointer.
3. Insert model A/B probes.
4. Valid A pointer succeeds.
5. A pointer to B probe fails in DB.
6. Direct probe UPDATE fails.
7. Required checks/digests/status constraints reject invalid rows.
8. Model delete follows documented cascade behavior.
9. Downgrade with probe rows refuses.
10. Export/remove test rows, downgrade parent, then upgrade head.
11. Existing registry rows/bindings remain intact.
12. Probe JSON must be an object; invalid status/version/digest/error bounds fail named checks.
13. Deleting the current probe directly sets the pointer null; deleting the model cascades owned history without a circular-FK failure.
14. Two models cannot point at each other's rows even under direct SQL.

- [ ] **Step 6: Implement ORM models**

Use explicit relationship foreign keys to avoid the model/probe pointer cycle ambiguity. Do not use `delete-orphan` from the current pointer relationship.

Add named constraints and indexes matching Section 10/11.

- [ ] **Step 7: Extend Plan 01 runtime revision mutations**

Reuse Plan 01 comparison helpers; do not create a second source of revision truth. Extend their locked transaction boundary:

~~~python
def credential_runtime_fields_changed(before, update) -> bool: ...
def model_runtime_fields_changed(before, update) -> bool: ...
def invalidate_model_probe_pointers(...) -> None: ...
~~~

Lock affected rows during mutation. Revision increment and pointer clearing occur in the same transaction.

Use the credential -> sorted model lock order for credential updates and credential -> model for model updates/probe promotion. A no-op/display-only update must neither lock unnecessarily nor clear evidence.

- [ ] **Step 8: Implement migration explicitly**

Review generated Alembic output. Add PostgreSQL functions/triggers with stable names and safe downgrade order. SQLite-specific test metadata must not pretend trigger behavior exists.

- [ ] **Step 9: Implement probe service transaction**

~~~python
class AiModelCapabilityProbeService:
    def run_live_probe(...) -> AiModelCapabilityProbe: ...
    def list_for_model(...) -> list[AiModelCapabilityProbe]: ...
~~~

Sequence:

1. Check feature gate, confirmation, adapter key, model/type, and credential presence without decrypt.
2. Lock credential then model briefly; snapshot exact runtime revisions/config digest and release the transaction.
3. Revalidate the secret-free endpoint/SSRF, decrypt/build the adapter, and run the bounded probe outside a long DB transaction.
4. Re-lock credential then model in the same canonical order.
5. Recompute config digest/revisions.
6. Insert immutable evidence against the original config digest regardless of whether the current config changed.
7. If changed, do not promote and return `promotionOutcome=config_changed`; otherwise optionally update the pointer atomically.

This prevents promoting evidence for a configuration that changed mid-probe.

- [ ] **Step 10: Implement schemas/routes**

Use current CamelModel/ApiResponse patterns. Keep live probe explicit and internal-service composition injectable for tests. Add `AI_MODEL_CAPABILITY_PROBE_ENABLED=false` to settings/examples and pass it to the API container in Compose. Do not return credential base URL if current model response intentionally excludes it.

The POST body is the only network input; no network schema accepts evidence, ModelRef, execution scope, adapter revision, or config digest from the caller. Those are server-derived.

- [ ] **Step 11: Run SQLite/service/API tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_model_probe.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_ai_registry_service.py \
  backend/tests/test_ai_registry_service_db.py \
  backend/tests/test_ai_model_capability_probe_api.py -q
~~~

- [ ] **Step 12: Run PostgreSQL migration gate**

Use the verified Plan 01 disposable PostgreSQL fixture/job. If Task 0 found none, create the guarded helper and PostgreSQL 15 CI service before running this command:

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_model_probe_postgres.py -q
~~~

- [ ] **Step 13: Verify one head**

~~~bash
cd backend
.venv/bin/alembic heads
cd ..
~~~

- [ ] **Step 14: Commit**

~~~bash
migration_file="$(find backend/alembic/versions -maxdepth 1 -type f \
  -name '*_add_ai_model_capability_probes.py' -print)"
test -n "$migration_file"
test "$(printf '%s\n' "$migration_file" | wc -l | tr -d ' ')" = "1"
git add backend/app/ai_registry/models.py \
  backend/app/ai_registry/schemas.py \
  backend/app/ai_registry/service.py \
  backend/app/ai_registry/router.py \
  backend/app/ai_registry/runtime.py \
  backend/app/config.py backend/.env.example \
  deploy/.env.example deploy/docker-compose.yml \
  backend/alembic/env.py "$migration_file" \
  backend/tests/test_provider_model_probe.py \
  backend/tests/test_provider_model_probe_postgres.py \
  backend/tests/test_ai_model_capability_probe_api.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_ai_registry_service.py \
  backend/tests/test_ai_registry_service_db.py
git commit -m "feat(ai): persist model capability probes"
~~~

The guard must find exactly one generated migration. Add `backend/tests/_postgres.py` and `.github/workflows/ci.yml` to this exact command only if Task 0 required them; do not stage either when unchanged.

---

## Task 9: Integrate the Loop with the Capability Gateway Behind an Internal Test Entry

**Files:**

- Create: `backend/app/assistant/provider_loop/runtime.py`
- Create: `backend/tests/test_provider_loop_gateway_integration.py`
- Modify as needed: `backend/app/assistant/provider_loop/loop.py`
- Regress Plan 02/OpenClaw/current Agent tests.

**Interfaces:**

- Consumes an explicit frozen Manifest, model ref, initial messages, and test/runtime factories.
- Produces one Provider Loop result.
- Exposes no public assistant route and registers no real Skill control Capability.

- [ ] **Step 1: Build a test-only ToolsProvider**

Given a Manifest:

- validate run/execution-scope/model/config identity;
- load immutable binding surfaces;
- resolve descriptors through Plan 02;
- stamp descriptor, behavior, classification revision, and classification-ruleset digests from the same `Gateway.describe(exact_binding)` result;
- filter only exact test grants;
- append aliases;
- return complete surface.
- close the resolution Session before returning frozen data.

It must not query “all published Skills” or activate anything implicitly.

- [ ] **Step 2: Build a Gateway dispatcher**

For each call:

- reverse alias is already resolved;
- verify call Manifest/surface/binding;
- call `Gateway.describe(exact_binding)` immediately before evidence issuance and require current availability/classification/behavior/descriptor equality with the exposed descriptor;
- create exact `CapabilityExecutionRequest`;
- use injected authorization-evidence factory;
- derive `CapabilityExecutionContext` from the exact execution scope and call ID;
- call Gateway once;
- return result and append-only next Manifest.

No direct Tool/Workflow/Agent imports. The integration composition registers only `issuer=test` evidence; `entrypoint=main_agent` remains denied because Plans 04–05 have not supplied a production verifier.

The loop-level pre-plan verifier and dispatcher-level pre-execute verifier use separate short-lived resolution/Gateway contexts. Neither returns a replacement surface; both close before the next stage. A mismatch returns safe `version_drift/classification_changed` without authorization issuance or adapter execution.

- [ ] **Step 3: Prove direct Gateway dispatch**

Use one read Tool fixture. Assert:

- Provider call -> dispatcher -> Gateway -> Tool adapter;
- exact binding/ref/evidence;
- exact principal/run/conversation/tenant scope and fresh evidence;
- schema/policy/output checks;
- one invocation;
- result message.
- exposed and current classification/behavior/descriptor digests are equal at both verification gates.

- [ ] **Step 4: Prove dynamic next-round surface**

Use a generic test control dispatcher result that appends a new frozen binding/Manifest revision. Do not name it `skill.inject`.

Assert:

- new capability absent round 1;
- control call uses old surface;
- next `tools_provider` appends alias;
- new capability visible round 2;
- old aliases unchanged;
- Provider can call it;
- all Manifest/surface digests traceable.

- [ ] **Step 5: Prove sibling session isolation**

Run two eligible read calls with isolated executor. Assert separate SQLAlchemy Sessions/Gateway instances and ordered results. Run the same test without isolated factory and assert safe sequential degradation.

Repeat with two simultaneous synthetic scopes and identical Tool keys. Assert no cross-scope visibility, evidence, result, cancellation, or Session reuse. Verify every Session closes on success, failure, and cancellation.

- [ ] **Step 6: Prove denied write/unknown behavior**

Use test evidence omitting write/unknown. Assert Gateway denial becomes paired blocked result and no adapter execution.

- [ ] **Step 7: Prove current-classification fail-closed behavior**

Use the real Plan 02 descriptor builder with a controllable classification ruleset fixture. Cover:

- ruleset/revision bump after the Provider surface is frozen but before sibling planning;
- `parallel_safe=true -> false`, side-effect, interrupt, timeout, availability, and behavior-digest changes;
- a change after pre-plan verification but before an isolated dispatcher's `Gateway.describe`;
- a valid waiting continuation resumed after a classification deployment change.

Assert no stale call is scheduled/authorized/executed, no alias/binding is rebuilt, every open Tool Call receives the locked blocked/cancelled seal in Provider order, and no next Provider request occurs. Also assert unchanged current digests take the normal path. Run both a more-conservative and more-permissive classifier change; either mismatch requires an explicit new surface/Manifest revision rather than silent reuse.

- [ ] **Step 8: Prove wait/resume through dispatcher contract**

Use a fake `durable` Gateway-compatible waiting result with exact `ContinuationRef`, trusted `ProviderWaitingResolution`, pending sibling, and next round. Assert full-surface/config/scope tamper checks, fresh authorization on pending work, preserved round budget, and no promise of persistence or child-resume authorization.

- [ ] **Step 9: Prove Agent Capability boundary and no recursive loop**

Use a fake/exact Plan 02 Agent binding and assert the Provider Loop calls only the Gateway. The Agent descriptor is non-parallel, nested Main Agent selection is unavailable, and no `run_agent_execution` import/call appears under `provider_loop`. This test permits Plan 02's Gateway adapter to own exact-version legacy Agent execution; it forbids direct coupling.

- [ ] **Step 10: Prove existing runtimes unchanged**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_loop_gateway_integration.py \
  backend/tests/test_capability_gateway.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_supervisor_graph_runtime.py -q
~~~

- [ ] **Step 11: Run boundary searches**

~~~bash
! rg -n 'openclaw_integration|SkillRouter|Supervisor|skill\\.inject' \
  backend/app/assistant/provider_loop
! rg -n 'agent_execution_core|run_agent_execution' \
  backend/app/assistant/provider_loop
~~~

- [ ] **Step 12: Prove no production entrypoint**

Inspect `app.main` router registration, Assistant service selection, config flags, and dependency graph. Assert the Provider Loop has no public assistant route, no startup worker, no production `main_agent` evidence verifier, and no fallback invocation from Router/Supervisor. The only new public route is the default-disabled model capability probe.

- [ ] **Step 13: Commit**

~~~bash
git add backend/app/assistant/provider_loop/runtime.py \
  backend/app/assistant/provider_loop/loop.py \
  backend/tests/test_provider_loop_gateway_integration.py
git commit -m "test(ai): integrate provider loop with capability gateway"
~~~

---

## Task 10: Final Clean-Environment, Migration, Compatibility, and Boundary Verification

**Files:**

- Modify only factual plan/test corrections.
- No new feature.

- [ ] **Step 1: Run all Provider Loop tests in project venv**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_aliases.py \
  backend/tests/test_provider_messages.py \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_model_probe.py \
  backend/tests/test_provider_loop_gateway_integration.py \
  backend/tests/test_provider_loop_clean_environment.py -q
~~~

- [ ] **Step 2: Recreate and run the clean Python 3.11 gate**

~~~bash
clean_env="$(mktemp -d /tmp/mindatlas-plan03-py311.XXXXXX)"
python3.11 -m venv "$clean_env"
"$clean_env/bin/python" -m pip install --upgrade pip
"$clean_env/bin/python" -m pip install -r backend/requirements.txt
"$clean_env/bin/python" -m pip check
"$clean_env/bin/python" -m pytest \
  backend/tests/test_provider_loop_contracts.py \
  backend/tests/test_provider_aliases.py \
  backend/tests/test_provider_messages.py \
  backend/tests/test_provider_streaming.py \
  backend/tests/test_provider_agent_loop.py \
  backend/tests/test_provider_multi_tool_calls.py \
  backend/tests/test_provider_openai_chat_adapter.py \
  backend/tests/test_provider_model_probe.py \
  backend/tests/test_provider_loop_gateway_integration.py -q
~~~

Record the disposable environment path and exact resolved versions. Cleanup is optional and must target that exact `mktemp` path only; never use a broad destructive path command.
Keep the recorded path for Step 6 or recreate another clean environment there; a missing shell variable is not permission to use `backend/.venv` as clean evidence.

- [ ] **Step 3: Run PostgreSQL upgrade/downgrade/upgrade**

Against the guarded disposable PostgreSQL 15 fixture only:

~~~bash
test -n "${MINDATLAS_TEST_POSTGRES_URL:-}"
backend/.venv/bin/python -m pytest \
  backend/tests/test_provider_model_probe_postgres.py -q
cd backend
.venv/bin/alembic heads
cd ..
~~~

The PostgreSQL test owns a unique test schema/database, parent upgrade, guarded downgrade preparation, and the full upgrade/downgrade/upgrade cycle. Never point `DATABASE_URL` implicitly at the developer database for this gate.

- [ ] **Step 4: Run Plan 01/02 integration regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py \
  backend/tests/test_openclaw_integration.py -q
~~~

- [ ] **Step 5: Run unchanged legacy assistant regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_supervisor_graph_runtime.py -q
~~~

- [ ] **Step 6: Run full backend suite in both declared and local environments**

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
"$clean_env/bin/python" -m pytest backend/tests -q
~~~

- [ ] **Step 7: Static architecture checks**

~~~bash
if rg -n '/Users/zyf/IdeaProjects/Culina|from app\\.ai\\.runtime' \
  backend/app/assistant/provider_loop; then exit 1; fi
if rg -n 'openclaw_integration|SkillRouter|Supervisor|skill\\.inject' \
  backend/app/assistant/provider_loop; then exit 1; fi
if rg -n 'run_agent_execution|agent_execution_core' \
  backend/app/assistant/provider_loop; then exit 1; fi
if rg -n 'api_key_encrypted|cookie|raw_response|response_body|decrypted_api_key' \
  backend/app/assistant/provider_loop/contracts.py \
  backend/app/assistant/provider_loop/probe.py; then exit 1; fi
~~~

Review false positives. `CapabilityAuthorizationEvidence` is a legitimate typed contract and therefore is not banned by a substring search; raw Authorization/Cookie header values remain forbidden. The OpenAI adapter may build a header internally, but it must not place it in contracts/events/errors.

- [ ] **Step 8: Prove no public cutover**

Search application router registration and assistant service selection. Assert no new Provider Loop route/feature flag is reachable from normal assistant chat and current Router/Supervisor remains selected.

- [ ] **Step 9: Prove protocol invariants with a randomized test**

Run the bounded seeded property/fuzz test added with Task 5 over scripted call/result sequences:

- every next Provider request sees paired transcript;
- terminal transcript paired;
- writes never parallel;
- result order preserved;
- aliases reversible;
- same seed reproducible.

The fixed CI seed/case count is reproducible. Optional larger local runs may vary the seed; never add an unbounded flaky run to CI.

- [ ] **Step 10: Verify one Alembic head and repository hygiene**

~~~bash
cd backend
.venv/bin/alembic heads
cd ..
git diff --check
git status --short
~~~

- [ ] **Step 11: Record final evidence**

Record:

- image/git revision;
- Python 3.11 clean versions;
- project venv drift versions;
- focused/full test counts;
- migration revision/parent and one head;
- exact Plan 02A readiness revision/record and current classification ruleset fixed vector; Plan 02B coordination status is recorded separately and is not claimed complete by Plan 03;
- live probe not run by CI;
- paid probe feature gate default and whether an operator explicitly enabled it outside CI;
- exact direct `openai` bound, `pip check`, adapter key/revision, and probe-contract version;
- no Main Agent cutover;
- known limitation: waiting continuation is portable but not durable and child resume/cancel authorization remains Plans 06–07;
- current repository has no first-class tenant persistence; scope-isolation tests are forward-compatible guards, not a tenancy claim;
- OpenAI Responses adapter deferred.

- [ ] **Step 12: Commit factual corrections**

~~~bash
git diff --name-only
git add docs/superpowers/plans/2026-07-13-provider-agent-loop-runtime.md
git commit -m "test(ai): verify provider agent loop runtime"
~~~

If a factual test correction was necessary, review and add that exact test file separately. Never stage the entire `backend` directory. Do not create an empty commit.

---

## 15. Release, Enablement, and Rollback Gates

### Gate 03A: pure runtime merge

Merge Tasks 1–7 and 9 only after:

- Plan 01 fixed Manifest/ModelRef vectors pass and the reviewed Plan 02A readiness record for the consumed revision says `PLAN_02A_READY=yes`;
- current-classification equality is enforced before sibling planning, again immediately before every Gateway execution, and across resume; drift seals pairing without dispatch;
- scripted, fake HTTP, Gateway, scope/session-isolation, pairing, waiting/resume, and soft-finalization tests pass in clean Python 3.11;
- no normal Assistant route, startup task, production evidence verifier, or legacy-engine modification references the new loop.

03A is inert production code. Its only consumers are tests/internal composition. Rollback is a normal code revert because it creates no schema and receives no user traffic. Plan 02B may still be pending/observing: its OpenClaw selector, worker, catalog, observation record, and legacy deletion are outside this gate and outside Plan 03 imports.

### Gate 03B: additive probe schema and disabled API

Merge Task 8 after the guarded PostgreSQL cycle passes. Deploy with:

~~~text
AI_MODEL_CAPABILITY_PROBE_ENABLED=false
~~~

Verify GET history contains only safe metadata and POST rejects before decrypt/network I/O. The migration is additive: Plan 01 runtime revisions remain, existing models receive a null current pointer, and the normal Assistant still uses Router/Supervisor.

### Optional operator probe enablement

Enabling the paid POST is separate from enabling the Provider Loop. Before setting the flag true:

1. Confirm the configuration routes are protected by trusted network/upstream/admin controls.
2. Confirm the exact Provider account may incur the bounded probe cost.
3. Run one model at a time and inspect safe evidence/promotion outcome.
4. Disable the flag after the intended compatibility inventory if continuous probing is unnecessary.

No model becomes a Main Agent merely because its probe passes; Plan 04 owns eligibility and entrypoint selection.

### Code rollback

1. Set the probe flag false first, preventing new paid calls before deployment rollback.
2. Let a currently running probe finish or terminate the process; it executes no business Capability and cannot be auto-retried.
3. Deploy the previous code. Leave the additive probe table/pointer in place by default; old code ignores them and this preserves diagnostic evidence.
4. Verify Router/Supervisor/legacy Agent behavior and one Alembic head.

Because the Main Assistant was never cut over and no old loop was deleted, code rollback never replays a Provider Tool Call or business side effect.

### Database rollback

Database downgrade is optional and deliberately destructive:

1. Keep the paid endpoint disabled.
2. Export probe history and current-pointer evidence if it must be retained.
3. Clear pointers and remove probe rows through an explicit operator procedure.
4. Run the migration downgrade against a guarded test/staging copy first; the downgrade refuses while rows remain.
5. Drop only Plan 03 probe objects. Do not drop Plan 01 model/credential runtime revisions.
6. Re-run upgrade/downgrade/upgrade evidence and confirm one head.

An inability to delete/export retained evidence is not a reason to force the downgrade; retain the forward-compatible schema and roll back code only.

---

## Plan 03 Exit Criteria

- The consumed Plan 01 fixed vectors and reviewed Plan 02A readiness record pass; Plan 03 neither waits for nor claims Plan 02B OpenClaw observation/cleanup.
- Plan 01 Manifest v1 shape and empty-alias fixed digest remain unchanged; Plan 03 only fills the reserved alias slot through append-only child revisions.
- Binding -> alias ref -> Manifest -> alias map -> surface -> message/transcript digest direction is tested, with no reverse/self dependency.
- Alias refs are append-only in Manifest revisions and existing aliases never change.
- Every Provider round obtains a complete fresh Tool surface immediately before the request.
- Every Tool Call resolves through the exact reverse alias/binding/descriptor/Manifest/surface that exposed it.
- Every exposed call freezes classification revision/ruleset, behavior, and descriptor digests; exact current `Gateway.describe` equality is required before sibling planning, immediately before each dispatch, and after resume. Any drift fails closed and seals pairing without rebuilding the old surface.
- Every call has exactly one Tool Result before the next Provider request or terminal seal.
- Waiting is the only temporarily open transcript state and carries the full exposed surface, exact child `ContinuationRef`, latest current Manifest, exact scope/model/locale, usage/round counters, completed prefix, and pending suffix.
- Resume never regenerates aliases, re-resolves latest versions, changes model/config/scope/locale/budgets, or changes sibling order; all tampering fails before dispatch/Provider I/O.
- Terminal cancellation sealing pairs waiting/unstarted calls explicitly without falsely claiming to cancel the durable child.
- Safe read/compute calls may use bounded isolated parallelism; unsafe calls and writes never do.
- A fatal result inside an already started parallel group preserves every started sibling's honest result in Provider order and cancels only groups that never started; pre-plan classification drift dispatches none and uses the deterministic single-blocked/all-other-cancelled seal.
- Distinct closed SQLAlchemy Sessions/Gateway contexts and freshly verified authorization evidence are used for parallel calls; cross-run/principal/conversation/synthetic-tenant reuse fails.
- Tool-call-round prose is not emitted as final user text.
- Soft finalization reserves a tools-disabled final Provider request and has explicit hard-stop behavior.
- OpenAI Chat adapter owns one semantic round, emits normalized bounded events, uses no general SDK retry, and does not own Tool execution.
- Provider errors/evidence never expose prompts, bodies, arguments/results, headers, or credentials.
- The direct OpenAI SDK bound passes `pip check` and adapter tests in a fresh Python 3.11 environment; local transitive 2.15 is not treated as evidence.
- Model probes are explicit, bounded, harmless, persisted with probe/config/adapter revisions, stale-safe under concurrent config changes, and default-disabled for paid calls.
- The new Alembic revision has the actual sole parent and a unique ID.
- Clean Python 3.11 full/focused tests and guarded PostgreSQL 15 migration tests pass.
- No Culina import, OpenClaw dependency, Main Agent route, Skill injection, direct old-Agent-engine call, or old Agent-loop modification exists. Exact Agent Capabilities remain reachable only indirectly through the Plan 02 Gateway.
- Current main assistant behavior remains unchanged.

---

## Handoff to Plan 04

Plan 04 may provide:

- published Main Agent Profile messages;
- Prompt Builder layers;
- Skill catalog recall;
- `skill.search`, `skill.inject`, and resource control Capabilities;
- append-only Manifest Skill/capability changes;
- dynamic visible Tool surfaces;
- model eligibility checks against current probe evidence;
- legacy-vs-main-agent evaluation entrypoint.

Plan 04 must:

- use this `ProviderAgentLoop`;
- use Plan 02 Gateway for every Tool Call;
- construct one authenticated `ProviderExecutionScope` and a production `skill_policy` authorization-evidence factory; Plan 03's test issuer is not reusable;
- freeze exact model/credential runtime refs and require a current matching probe-contract/config/adapter evidence row before adapter construction;
- append aliases through Manifest revisions;
- keep same-message siblings on the surface that exposed them;
- preserve the exposed classification/behavior/descriptor digests and keep both current-descriptor verification gates; Main Agent policy may narrow grants but may not bypass classification drift;
- preserve Provider transcript pairing;
- keep the first production path read/compute-only and do not claim portable waiting is durable before Plans 06–07;
- not build another model/Tool loop.
