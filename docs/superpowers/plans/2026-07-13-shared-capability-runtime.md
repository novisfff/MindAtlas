# MindAtlas Shared Capability Runtime and Minimum Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Do not start implementation until the Plan 01 readiness checklist below passes. The default automated delivery scope is Plan 02A (Tasks 0–9); Plan 02B requires real observation evidence and explicit human approval.

**Goal:** Build one provider-neutral Capability Runtime for immutable Tool, Workflow, and Agent references; enforce schema, version, availability, cancellation, timeout, side-effect, and explicit authorization checks in one Gateway; then move OpenClaw onto that Gateway without switching the main assistant.

**Architecture:** Plan 01 owns immutable Skill packages, published bindings, `ResolvedCapabilityRef`, Tool `config_revision`, and `APP_BUILD_REVISION`. Plan 02 consumes those contracts. A `CapabilityRegistry` resolves one frozen reference to an executable descriptor without consulting Draft or “latest” state. A minimum `CapabilityPolicyEngine` evaluates trusted authorization evidence. A `CapabilityGateway` validates the request, invokes exactly one type adapter, validates and normalizes the result, and emits safe events. OpenClaw is the first production consumer through an OpenClaw-owned compatibility bridge; no shared runtime module imports OpenClaw.

**Prerequisite:** [Plan 01](./2026-07-13-agent-skills-contracts-and-versioning.md) is merged with its final class names, table names, migration revision, frozen-reference digests, publication tests, and lossless immutable binding/dependency DTOs. Plan 02 projects those objects directly; it does not derive a missing Schema or dependency from Draft, an Agent aggregate, the current OpenClaw catalog, current AI component binding, or another mutable target. If Plan 01's implementation differs from this document, update this document before writing Plan 02 code. Do not create parallel “temporary” reference types.

### Plan 01 readiness checklist — hard blocker for code, not for this plan-only revision

All boxes must be proven from the merged implementation in Task 0:

- [ ] Published Tool/Workflow/Agent bindings round-trip exact normalized input/output Schema bodies, independent digests, completion, target/executable revision, dependency index, and `binding_contract_digest`.
- [ ] Agent/Workflow closure rows freeze exact nested Tool/Workflow/Agent and concrete default/custom Model/Credential refs; an unbound default produces an unpublished diagnostic shadow rather than a mutable fallback.
- [ ] Project-owned Tool parameter Schemas, `system_tool_contract_set_digest`, `APP_BUILD_REVISION`, Tool `config_revision`, and Model/Credential `runtime_revision` gates pass.
- [ ] Plan 01 exports `ResolvedCapabilityBinding`, `ResolvedCapabilityDependency`, `CurrentCapabilityReference`, `ResolvedCapabilityRef`, and the one Schema/digest implementation used here.
- [ ] Plan 01 exports exactly `MAX_CAPABILITY_CLOSURE_DEPTH=16`, `MAX_CAPABILITY_CLOSURE_REFS=256`, and `MAX_CAPABILITY_CLASSIFIED_NODES=4096`; Plan 02 imports them without aliases or local numbers.
- [ ] PostgreSQL immutability/reference-protection and V1→V2 catalog-warm regressions pass, and exactly one Alembic head exists.

Plan 01 owns immutable execution identity; Plan 02 owns the conservative runtime behavior classification. Plan 01 therefore does **not** persist optimistic `side_effect`, `parallel_safe`, or `timeout_policy` values. Plan 02 derives them only from frozen Plan 01 evidence plus the versioned classification contract defined below.

**Tech Stack:** Python 3.11 production/CI, FastAPI, AnyIO 4, Pydantic 2, SQLAlchemy 2, PostgreSQL 15, `jsonschema` Draft 2020-12 validation, the existing synchronous Workflow/Agent execution engine, and pytest.

---

## 1. Plan Position and Non-Negotiable Boundary

This is Plan 02 of 10.

Implemented here:

- One frozen Capability descriptor and execution contract for Tool, Workflow, and Agent.
- Exact published-version/config-revision resolution using Plan 01 contracts.
- Shared JSON Schema compilation and safe validation errors.
- Conservative side-effect and parallel-safety classification.
- Minimum deny-by-default policy with trusted Principal/entrypoint/owner evidence.
- Tool, Workflow, and Agent adapters.
- Native timeout/cancellation checks where the current implementation can honestly enforce them.
- Safe error/result/event normalization.
- OpenClaw characterization, shared-runtime bridge, bounded cutover, and legacy-branch removal.

Explicitly not implemented here:

- No Provider Agent Loop, Provider alias map, dynamic `tools_provider()`, or multi-call scheduler. Those belong to Plan 03.
- No Main Agent prompt, Skill search/injection, Profile runtime switch, or assistant feature flag. Those belong to Plan 04.
- No multi-Skill owner-policy merge, per-Skill budgets, or obligation ledger. Those belong to Plan 05.
- No durable Run checkpoint, Worker Lease, persistent interrupt, resume, or CapabilityCall ledger. Those belong to Plans 06–08.
- No new write access for the future Main Agent. OpenClaw may retain only its already authenticated and explicitly exposed behavior.
- No frontend work.
- No database migration.
- No deletion or behavior change of the legacy `SkillRouter -> Supervisor` path.
- No speculative LangGraph upgrade.
- No Provider Alias allocation or reverse map. Plan 01 may preserve author hints, but Plan 03 alone validates a concrete Provider alias and appends it to a Run Manifest revision.

Design sequencing clarification: the overall design’s earlier Plan 02 wording mentioned a Provider-alias “foundation”. The approved implementation split deliberately moves the entire Provider-specific alias validation/allocation/reverse-map surface to Plan 03. Plan 02 exposes Domain Keys only; implementing aliases here is scope expansion, not a missing step.

The following are hard failures, not “best effort”:

- Resolving a Workflow from `AssistantWorkflow.graph_snapshot`.
- Resolving an Agent from mutable aggregate fields rather than the exact published version row.
- Executing a Tool after its frozen build/config/schema revision has drifted.
- Treating catalog visibility, publication, or possession of an ID as authorization.
- Defaulting an unclassified target to read-only.
- Catching a started shared dispatch and retrying it through the legacy OpenClaw path.
- Calling a Provider or adding Provider aliases from this plan.
- Building a Workflow/Agent descriptor from a frozen outer target while allowing its engine to resolve nested Tool/Workflow/Agent dependencies by current name or “latest” at dispatch time.

---

## 2. Verified Repository Baseline at Plan-Writing Time

The implementation worker must re-run these checks because Plan 01 will change the repository.

Verified on 2026-07-13:

- Git branch: `main`.
- Git revision: `c25d03f`.
- Current sole Alembic head: `a7b8c9d0e1f2`.
- Production Docker and CI use Python 3.11.
- Local `backend/.venv` uses Python 3.12.7.
- `backend/requirements.txt` pins `langgraph==0.3.34` and `langchain-core>=0.3.0,<1.0`.
- Local `backend/.venv` has `langgraph==1.0.5` and `langchain-core==1.2.7`, so it is useful for local regression only and is not dependency-compatibility evidence.
- `jsonschema==4.26.0` is currently installed transitively. The final Plan 01 contract declares `jsonschema>=4.23,<5` directly because publish-time binding Schema normalization/digests already depend on it; Plan 02 must verify and reuse that exact declaration rather than add a second dependency change.
- The focused characterization command below passes with `82 passed, 2 subtests passed` and one unrelated Pydantic deprecation warning:

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_remote_tool.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py -q
~~~

Current code facts that constrain the implementation:

- `backend/app/openclaw_integration/router.py` authenticates a runtime request before calling `OpenClawIntegrationService.execute_capability(...)`.
- `backend/app/openclaw_integration/service.py` has three private production branches:
  - `_execute_tool_capability`;
  - `_execute_workflow_capability`;
  - `_execute_agent_capability`.
- OpenClaw has private JSON Schema normalization/validation and OpenClaw-specific Tool request/response adapters.
- OpenClaw public runtime characterization starts with these common codes, but Task 0 must enumerate the complete **runtime-reachable** call graph and fixed response vectors before refactoring:
  - `40161` authentication;
  - `40361` integration disabled;
  - `40362` capability disabled;
  - `40461` capability not found;
  - `40062` invalid capability configuration;
  - `42261` schema/input/output validation;
  - `42262` invalid source;
  - `40961` runtime availability/version drift.
- Runtime setup/adapters can also currently surface codes such as `40061`, `40064`, `40965`, and `50038`; admin-only `40462`/`40971–40973` are not automatically part of execute compatibility. Task 0 classifies reachability instead of guessing from numeric blocks.
- `ToolRegistry.resolve_system_tool()` can resolve every declared name in `app.assistant.tools._EXPORTS`, including five `openclaw_*` compatibility Tools omitted from `__all__`, plus internal paths such as `kb_search`. A disabled DB Tool intentionally shadows a same-named system Tool.
- `RemoteTool.invoke(...)` decrypts its credential only while constructing the outgoing request, validates the initial URL and every redirect, and uses a native urllib timeout. Its current HTTP error includes up to 500 characters of response body and therefore must be sanitized at the shared boundary.
- `AssistantWorkflow.graph_snapshot` is Draft-first.
- `AssistantWorkflow.published_version_id` and `AssistantAgentProfile.published_version_id` point to owned version rows.
- `AssistantConfigService._get_workflow_published_input` and `_get_agent_profile_published_draft` already demonstrate exact owned-version lookups, but they are private compatibility helpers and must not become the new public domain API.
- `HumanLoopRuntime` persists an approval record and then polls in-process. It is not a durable interrupt.
- Current Workflow/Agent execution is synchronous and receives cancellation/event callbacks through runtime context.
- `OpenClawIntegrationService.execute_capability(...)` is `async`, but it currently calls all three synchronous Tool/Workflow/Agent execution branches inline. Shared mode must use the bounded awaited worker contract in Section 4.3; blocking the FastAPI event loop is not accepted as compatibility.
- `LangGraphEngine._build_tools(...)` currently resolves Tools by name through `ToolRegistry`, and nested `workflow_call` execution resolves child Tools again by name. A Plan 02 adapter therefore cannot claim frozen execution merely by pre-checking an outer Workflow/Agent version; it must supply the exact Plan 01 dependency closure through an additive pre-resolved Tool/target resolver path.
- `WorkflowNodeBuilderDeps` in `workflow_dag_assembler.py` currently carries no execution scope, while `container_runtime.py` rebuilds nodes for iteration/loop bodies and can invoke `workflow_call` again. Scope propagation must therefore cover root assembly, container bodies, and every dependency-using node builder; changing only `engine.py`/`workflow_call_node.py` is a false freeze.
- The current global Workflow graph cache key covers Skill/DAG/tool names/model name but not binding, Tool config, credential, or dependency digests, while compiled node closures capture Tool objects and model clients. Capability mode must bypass that cache unless it is first refactored into a dependency-free template cache; this plan chooses the bounded bypass.
- `AssistantAgentProfileVersion.snapshot` owns prompt, Tool names, KB, and model selection, but no callable input/output Schema. Native Agent callable Schemas must already be explicit binding-level contracts frozen by Plan 01; the OpenClaw catalog Schema is only an OpenClaw compatibility source.
- Agent `model_source=default` and Workflow nodes without an explicit model currently follow mutable `AiComponentBinding(component="assistant")`; explicit model IDs still resolve mutable `AiModel`/`AiCredential` rows. Plan 01 must turn both forms into exact secret-free model dependency refs/config/revision digests at binding publication. Plan 02 cannot call `resolve_openai_compat_config(...)` and silently accept whatever default is current.
- Code-executor allowlists/time/memory/output limits are live Settings and Plan 01 v1 has no sandbox-profile dependency type or immutable profile digest. Plan 02 v1 classifies every reachable `code_executor` node as `unknown`; it must not invent a frozen sandbox contract in this migration-free plan.
- OpenClaw currently authenticates one integration-wide bearer secret and returns request audit headers, not a durable installation row ID. Its verifier must therefore be request-scoped and close over the successful authentication result; a caller-supplied `principal_id` string is not proof.
- `Settings` is process-cached. The temporary Plan 02A environment selector is a process/deployment switch, not a hot database flag; a production change requires a controlled restart or rolling deployment and affects requests according to the instance that accepts them.

Task 0 records the post-Plan-01 baseline. If the focused suite is red before Plan 02 changes, stop and separate pre-existing failures from Plan 02 work.

---

## 3. Runtime Topology and Dependency Direction

~~~mermaid
flowchart LR
    OC["OpenClaw authenticated request"] --> OCB["OpenClaw compatibility bridge"]
    FUT["Future Provider Loop (Plan 03)"] -. "not wired in this plan" .-> GW
    OCB --> GW["CapabilityGateway"]
    GW --> REG["CapabilityRegistry"]
    GW --> POL["Minimum Policy Engine"]
    GW --> JS["JSON Schema Validator"]
    GW --> AD["Adapter Registry"]
    REG --> DC["Plan 01 frozen dependency closure"]
    AD --> TA["Tool Adapter"]
    AD --> WA["Workflow Adapter"]
    AD --> AA["Agent Adapter"]
    REG --> P1["Plan 01 frozen references"]
    TA --> TR["ToolRegistry / RemoteTool"]
    WA --> WR["Exact published Workflow version"]
    AA --> AR["Exact published Agent version"]
~~~

Dependency rules:

1. `app.assistant.capabilities` may import Plan 01 domain/Skill-resolution contracts, `assistant_config` runtime primitives, and generic common utilities.
2. `app.assistant.capabilities` must not import `openclaw_integration`, Provider Loop, Main Agent, Router, Supervisor, or HTTP router modules.
3. `openclaw_integration.capability_adapter` may import the shared runtime and OpenClaw service/schema/model code.
4. Type adapters do not authorize themselves. They receive a request only after the Gateway has produced an allow decision.
5. Adapters do not resolve “current latest”. The Registry supplies the exact descriptor and owned executable snapshot.
6. A SQLAlchemy `Session`, callback, HTTP client, thread/future, or Provider object never enters a frozen domain contract.
7. A Workflow/Agent adapter receives an ephemeral executable map built only from the binding's Plan 01 dependency-closure rows. The existing engine may consume that map through an additive resolver port, but it may not fall back to `ToolRegistry.resolve(name)` or a latest-version helper when an exact dependency is absent.
8. Plan 02 owns no Provider-visible name. Domain Key remains the only Capability identity at this layer; Plan 03 owns alias allocation and reverse lookup.

The production call order is locked:

~~~text
check cancellation
-> verify binding snapshot digest and project it losslessly
-> resolve and verify frozen root reference plus dependency closure
-> check target availability
-> compile/validate input schema
-> evaluate trusted authorization evidence
-> check cancellation again
-> atomically consume one dispatch permit
-> dispatch exactly one adapter
-> inside that adapter, recheck and activate only its exact credential/model immediately before client/request construction
-> validate/normalize output
-> check cancellation at a cooperative boundary
-> return one CapabilityResult
~~~

Authorization occurs after safe descriptor resolution because policy needs the authoritative side-effect class, but before credential/model activation or target execution. Registry, classifier, Policy, and Gateway never decrypt secrets or construct a model client. Only the selected adapter may activate the exact already-verified slot after consuming the single-use dispatch permit.

---

## 4. Locked Domain Contracts

Use Plan 01’s `FrozenContract`, `JsonValue`, canonical binding/Schema models, dependency refs, and completion contract directly. Names below may be adjusted only to match the merged Plan 01 implementation; semantics may not be weakened. If Plan 01 names the completion model `BindingCompletionContractV1` (or similar), import/re-export that type as `CapabilityCompletionMetadata`; do not define a semantically duplicate Plan 02 model that could digest differently.

### 4.1 Identity and descriptor

~~~python
SideEffectClass = Literal[
    "none",
    "compute",
    "read",
    "draft",
    "write_local",
    "write_external",
    "unknown",
]


class ClassificationContractRef(FrozenContract):
    schema_version: Literal[1] = 1
    revision: str
    ruleset_digest: str


class CapabilityAvailability(FrozenContract):
    status: Literal[
        "available",
        "disabled",
        "missing",
        "version_drift",
        "unsupported",
    ]
    reason_code: str | None = None
    compatibility_only: bool = False


class CapabilityTimeoutPolicy(FrozenContract):
    mode: Literal["native", "cooperative", "none"]
    timeout_seconds: float | None
    cancellation_supported: bool


CapabilityCompletionMetadata = CapabilityCompletionContract


class CapabilityBehavior(FrozenContract):
    classification: ClassificationContractRef
    side_effect: SideEffectClass
    parallel_safe: bool
    interrupt_mode: Literal["none", "legacy_blocking", "durable"]
    timeout_policy: CapabilityTimeoutPolicy
    behavior_digest: str


class CapabilityDescriptor(FrozenContract):
    capability_key: str
    capability_type: Literal["tool", "workflow", "agent"]
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    display_name: str
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    input_schema_digest: str
    output_schema_digest: str
    descriptor_digest: str
    executable_revision: str
    behavior: CapabilityBehavior
    availability: CapabilityAvailability
    completion: CapabilityCompletionMetadata
~~~

The executable reference alone is not enough to carry a callable Schema or an exact nested execution closure. The current Agent Profile model has no intrinsic input/output Schema, and the current Workflow/Agent engines can resolve nested Tools by name. Use one binding surface that is a lossless projection of the Plan 01 published binding contract:

~~~python
class FrozenBindingProvenance(FrozenContract):
    origin: Literal["skill_version", "openclaw_request", "test"]
    binding_row_id: UUID | None
    owner_version_id: UUID | None
    source_snapshot_digest: str


class FrozenCapabilityBinding(FrozenContract):
    provenance: FrozenBindingProvenance
    ref: ResolvedCapabilityRef
    resolved: ResolvedCapabilityBinding
~~~

`ResolvedCapabilityBinding` and its `ResolvedCapabilityDependency` tuple are imported from Plan 01 and remain the only binding/dependency DTOs. Plan 02 does not flatten or rename their fields into a second digestable model. `FrozenCapabilityBinding` adds only provenance and the Manifest-facing `ResolvedCapabilityRef`; its constructor proves those refs equal `resolved` and the canonical `resolution_snapshot`. Convenience accessors may expose `input_schema` or dependency tuples, but serialization and hashing always use the Plan 01 object. A `model` dependency carries the actual model selected at publication (including a resolved default), model/credential IDs, runtime revisions, and normalized secret-free configuration digests inside its validated snapshot. No dependency contains ORM rows, encrypted/decrypted credentials, clients, callbacks, or mutable “latest” locators.

The canonical binding payload is exactly Plan 01's persisted `resolution_snapshot`, not a serialization of `FrozenCapabilityBinding` itself:

~~~json
{
  "schemaVersion": 1,
  "target": {
    "capabilityType": "workflow",
    "targetIdentity": "workflow:<uuid>",
    "targetId": "<uuid>",
    "targetVersionId": "<uuid>",
    "targetRevision": null,
    "resolutionDigest": "sha256"
  },
  "inputSchema": {},
  "outputSchema": {},
  "inputSchemaDigest": "sha256",
  "outputSchemaDigest": "sha256",
  "completion": {},
  "execution": {
    "configDigest": null,
    "executableRevision": "immutable-revision"
  },
  "dependencyClosure": [
    {"path": "root/model:primary", "dependencyDigest": "sha256"}
  ],
  "dependencyClosureDigest": "sha256"
}
~~~

Plan 01 stores `bindingContractDigest` after hashing the payload above with that member omitted. Each closure row is first verified against its full lossless snapshot; the parent digest then uses the ordered `(path, dependencyDigest)` index. `ResolvedCapabilityRef` is derived only after this digest exists and therefore is never embedded wholesale into its own hash input. This ordering is a hard anti-cycle invariant. Provenance IDs are authorization/audit evidence and do not alter the contract digest. Plan 02 must use the same Plan 01 Schema normalization and digest helpers used at publish time; `app.assistant.capabilities.json_schema` adds compilation and value validation, not another normalization dialect.

For a published Skill, all contract fields above are copied directly from Plan 01's one authoritative persisted binding payload and closure representation, whether the merged implementation embeds that closure in the immutable snapshot or normalizes it into immutable child rows. Runtime projection recomputes and verifies every digest but does not fill, normalize differently, or derive any missing field. It is a hard prerequisite failure if the persisted payload cannot round-trip byte-for-byte through the canonical form.

Schema ownership is explicit:

- A native Agent binding declares its callable input and output Schema in the Skill binding contract before publication. `AssistantAgentProfileVersion` owns executable prompt/Tool/KB/model state only and is never a callable-Schema source.
- A canonical host Agent envelope may be referenced by an explicit versioned contract ID, but Plan 01 must materialize its exact Schema bodies and digests into the published binding. No implicit runtime default is allowed.
- Code-native Tool and Workflow binding Schemas are publication-time assertions against the authoritative Tool definition or exact published Workflow start/output contract. Their normalized bodies are still copied into the binding snapshot so runtime never reconstructs them from current state.
- A native top-level remote Tool binding uses target-owned `input_params` for input. Because current `AssistantTool` has no output-Schema column, the Skill binding must explicitly own/freeze an output Schema; publication fails if it is absent. Plan 02 parses a complete JSON response only when that frozen contract requires structured JSON. OpenClaw catalog Schema/`text_field` is not a native remote-Tool contract.
- A remote Tool used only as a nested current Workflow/Agent engine dependency freezes the Plan 01 compatibility string contract so existing engine semantics do not change. A disabled Legacy shadow likewise receives an explicit versioned string contract.
- A disabled Legacy Agent shadow may receive a deterministic migration contract from the Legacy Adapter, but that generated contract is explicit, versioned, frozen, and remains disabled until reviewed.
- OpenClaw is the only compatibility exception: its bridge constructs a transient compatibility binding from the authenticated request's exact catalog item and exact target at request start. That Schema never becomes the Schema of a native Skill binding and cannot be reused by another entrypoint.

For OpenClaw, the compatibility bridge freezes the current catalog item contract and complete target dependency closure at request start using the same canonical binding constructor. It resolves a default model to the actual model/credential slot at that boundary; execution cannot follow a later AI component-binding change. In both origins, Schema and dependency digests participate in `binding_contract_digest` and the outer resolution/evidence digests. If Plan 01 stores only a digest, omits a Schema body, or cannot reconstruct the exact dependency closure, amend Plan 01 before implementing Plan 02; do not query mutable OpenClaw, Draft, Agent aggregate, current Tool Registry, current AI component binding, or “latest published” state as a substitute.

Rules:

- `capability_key` is the Plan 01 Domain Key. Provider-visible names do not appear in this plan.
- `target_identity` and all target reference fields must equal the frozen Plan 01 resolution.
- The normalized binding payload, its persisted closure representation, and runtime projection must have one identical `binding_contract_digest`; Plan 02 must not introduce a second digest recipe.
- Every Capability/model dependency is unique by its stable source locator and role, sorted by the Plan 01 canonical order, and included in `dependency_closure_digest`. A missing, extra, reordered-to-change-semantics, disabled, or drifted dependency makes the descriptor unavailable before engine construction.
- `FrozenContract` is only shallowly frozen for nested Python containers. Constructors must deep-copy caller data, validate canonical JSON, and retain canonical bytes/digests as the comparison source. No component receives the mutable ORM JSON object; mutation-of-source and mutation-of-materialized-copy tests must prove the binding digest and later consumers remain unchanged.
- `behavior_digest` covers the exact derived behavior plus `ClassificationContractRef`. `descriptor_digest` covers the normalized descriptor except display text and mutable availability reason text; it includes root resolution, binding/dependency/model digests, Schemas, execution revision, `behavior_digest`, and completion metadata.
- `CLASSIFICATION_CONTRACT_REVISION="plan02-v1"` and `classification_ruleset_digest` are generated from one canonical declarative ruleset containing the risk lattice, complete system-Tool table, remote default, Workflow-node rules, Agent aggregation rules, interrupt rules, and timeout rules. The classifier consumes that same structure; a checked-in golden digest and test require any rule/table change to bump the revision.
- A classification-contract change does not require republishing a Skill because it is a runtime safety/policy update, but it does create a new descriptor/decision digest. New calls use the new classification. A previously frozen Run/Manifest descriptor must fail closed until a later plan creates an explicit reconciliation/new Manifest revision; it may not silently substitute changed behavior evidence.
- Locale may change display text only. It cannot change any digest, policy decision, or executable target.
- An unavailable descriptor may be returned for catalog display, but the Gateway cannot dispatch it.
- `parallel_safe=true` means “classification-eligible for a later scheduler”; it does not create concurrency in this plan and is insufficient by itself. Plan 03 must also require an independent Gateway/Session, current classification digest, read/compute/none effect, depth allowance, no human interrupt, and no shared mutable execution object.
- `interrupt_mode=durable` is reserved. Plan 02 produces no durable continuation.

### 4.2 Principal, owner, and trusted evidence

~~~python
EvidenceIssuer = Literal["openclaw_bridge", "skill_policy", "system", "test"]
CapabilityEntrypoint = Literal["openclaw", "main_agent", "workflow", "agent", "test"]


class CapabilityPrincipal(FrozenContract):
    principal_type: Literal["openclaw_installation", "user", "service", "test"]
    principal_id: str
    authenticated: bool


class CapabilityOwnerRef(FrozenContract):
    owner_kind: Literal["skill_version", "openclaw_catalog", "system", "test"]
    owner_id: str
    owner_version_id: UUID | None


class CapabilityAuthorizationEvidence(FrozenContract):
    issuer: EvidenceIssuer
    call_id: str
    principal: CapabilityPrincipal
    entrypoint: CapabilityEntrypoint
    owner: CapabilityOwnerRef
    capability_key: str
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    evidence_digest: str
~~~

No FastAPI request model accepts this contract from the network. Trusted entrypoint adapters construct it after authenticating and resolving their own source-of-truth records. `call_id` must equal `CapabilityExecutionContext.call_id`, participates in `evidence_digest`, and prevents an allow decision from being replayed for a sibling or later call.

The minimum Policy does not trust `issuer` as a string by itself. It delegates evidence verification to an injected `AuthorizationEvidenceVerifier`:

~~~python
class AuthorizationEvidenceVerifier(Protocol):
    def verify(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
    ) -> VerifiedAuthorizationEvidence: ...
~~~

The cross-task ephemeral types are locked rather than left to Task 1/7 invention:

~~~python
EvidenceVerifierKey = tuple[EvidenceIssuer, CapabilityEntrypoint]


class SingleUseDispatchPermit(Protocol):
    permit_id: str

    def consume(self, *, call_id: str, descriptor_digest: str) -> None: ...


@dataclass(frozen=True)
class VerifiedAuthorizationEvidence:
    call_id: str
    verifier_key: EvidenceVerifierKey
    verifier_instance_id: str
    principal: CapabilityPrincipal
    entrypoint: CapabilityEntrypoint
    owner: CapabilityOwnerRef
    capability_key: str
    resolution_digest: str
    binding_contract_digest: str
    dependency_closure_digest: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    evidence_digest: str
    verification_digest: str
    dispatch_permit: SingleUseDispatchPermit = field(repr=False, compare=False)


@dataclass(frozen=True)
class CapabilityPolicyDecision:
    allowed: bool
    reason_code: str
    call_id: str
    descriptor_digest: str
    classification_ruleset_digest: str
    evidence_digest: str
    owner: CapabilityOwnerRef
    granted_side_effects: tuple[SideEffectClass, ...]
    grant_source_digest: str
    decision_digest: str
    dispatch_permit: SingleUseDispatchPermit | None = field(
        default=None,
        repr=False,
        compare=False,
    )
~~~

These dataclasses are process-local and have no JSON/Pydantic serializer. Their digests are built from explicit safe fields and exclude the permit object. The concrete verifier and permit each own a `threading.Lock`-guarded consumed flag: concurrent verification of one request/evidence succeeds once, and replay of one allow decision reaches adapter dispatch once. A caller cannot construct an accepted permit from IDs or copy a Pydantic evidence object to bypass either atomic gate.

The OpenClaw verifier is created per authenticated execution request and closes over the expected `call_id`, a request-local authentication proof object, the frozen catalog call, and the selected 02A runtime mode. Immediately before policy it re-reads the referenced catalog item in a short transaction, verifies enabled/exposed state and the frozen snapshot digest, then ends that transaction before adapter/HITL work and consumes its allow decision once. Successful verification is the admission boundary: a catalog disable after it affects future calls, not the already admitted call. No catalog row lock or idle transaction spans target execution. The proof object is ephemeral and cannot be serialized into evidence, logs, events, or results. Since the current repository has one integration-wide secret rather than an installation row, use a stable non-secret principal label plus the request-scoped verifier closure; never pretend a caller-provided principal string proves authentication. The future Skill verifier arrives with Plans 04–05. Until then, `entrypoint=main_agent` has no production verifier and is denied.

### 4.3 Execution request and runtime ports

Serializable/frozen data is separate from ephemeral runtime services:

~~~python
class CapabilityExecutionContext(FrozenContract):
    call_id: str
    run_id: UUID | None = None
    conversation_id: UUID | None = None
    locale: str | None = None
    request_source: str | None = None
    request_channel: str | None = None
    request_session: str | None = None
    request_tool: str | None = None
    nesting_depth: int = 0


class CapabilityExecutionRequest(FrozenContract):
    binding: FrozenCapabilityBinding
    input: dict[str, JsonValue]
    context: CapabilityExecutionContext
    authorization: CapabilityAuthorizationEvidence


class CancellationPort(Protocol):
    def is_cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...


class CapabilityEventSink(Protocol):
    def emit(self, event: CapabilityRuntimeEvent) -> None: ...


@dataclass(frozen=True)
class CapabilityRuntimePorts:
    cancellation: CancellationPort
    events: CapabilityEventSink


@dataclass(frozen=True)
class ExecutableToolTarget:
    target_identity: str
    tool_id: UUID | None
    config_revision: int | None
    config_digest: str | None
    is_system: bool
    tool_object_or_record: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ExecutableWorkflowVersionTarget:
    workflow_id: UUID
    version_id: UUID
    snapshot_digest: str
    parsed_published_input: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ExecutableAgentVersionTarget:
    agent_profile_id: UUID
    version_id: UUID
    snapshot_digest: str
    parsed_snapshot: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedModelTarget:
    source_locator: str
    model_id: UUID
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    model_config_digest: str
    credential_config_digest: str


@dataclass(frozen=True)
class AuthorizedModelRuntimeConfig:
    verified: VerifiedModelTarget
    provider_protocol: str
    model_name: str
    client_or_credential_handle: object = field(repr=False, compare=False)


class FrozenClosureRuntimeResolver(Protocol):
    binding_contract_digest: str
    dependency_closure_digest: str

    def bind_authorized(
        self,
        *,
        decision: CapabilityPolicyDecision,
    ) -> ExactRuntimeDependencyResolver: ...


@dataclass(frozen=True)
class ResolvedCapabilitySurface:
    binding: FrozenCapabilityBinding
    executable: ExecutableToolTarget | ExecutableWorkflowVersionTarget | ExecutableAgentVersionTarget
    execution_closure: FrozenClosureRuntimeResolver = field(repr=False, compare=False)
    display_name: str
    description: str
    availability: CapabilityAvailability


@dataclass(frozen=True)
class ResolvedCapabilityTarget:
    descriptor: CapabilityDescriptor
    binding: FrozenCapabilityBinding
    executable: ExecutableToolTarget | ExecutableWorkflowVersionTarget | ExecutableAgentVersionTarget
    execution_closure: FrozenClosureRuntimeResolver = field(repr=False, compare=False)


@dataclass(frozen=True)
class CapabilityAdapterRequest:
    target: ResolvedCapabilityTarget
    validated_input: dict[str, JsonValue]
    context: CapabilityExecutionContext
    decision: CapabilityPolicyDecision
~~~

Every type from `ExecutableToolTarget` through `CapabilityAdapterRequest` is ephemeral and process-local. Fields holding ORM/domain parser objects, callables, clients, credential handles, or the exact resolver are explicitly `repr=False, compare=False`; none is serialized, persisted, hashed, accepted from HTTP, logged, or exposed to Provider code. The corresponding safe IDs/revisions/digests live in the descriptor/binding and are rechecked before activation. Before policy, `FrozenClosureRuntimeResolver` is a non-activating exact index: it can verify safe identity/revision/digest evidence but cannot decrypt or create clients. Gateway constructs `CapabilityAdapterRequest` only after policy allow and dispatch-permit consumption; the selected adapter then calls `bind_authorized(decision=...)` exactly once and cannot replace its descriptor/binding/closure.

The Gateway API is synchronous to match the current Tool/Workflow/Agent engines:

~~~python
class CapabilityGateway:
    def describe(self, binding: FrozenCapabilityBinding) -> CapabilityDescriptor: ...

    def execute(
        self,
        request: CapabilityExecutionRequest,
        *,
        ports: CapabilityRuntimePorts,
    ) -> CapabilityResult: ...
~~~

`describe()` is an internal preflight/Plan 03 surface, not a replacement for the current OpenClaw catalog route. OpenClaw continues to render its catalog from existing catalog records; its execution bridge resolves/describes only the exact selected item. An unavailable descriptor therefore supports diagnostics/preflight without changing public listing behavior.

Plan 03 may call this Gateway from its synchronous dispatcher. A future async wrapper may use worker infrastructure, but Plan 02 must not hide synchronous work in detached threads.

OpenClaw is the one locked async/sync bridge in Plan 02A:

- The async execute route snapshots the selected mode, bounded immutable request payload, preferred locale, and only the required OpenClaw header strings (including bearer material as an opaque `repr=False` worker argument), then awaits `anyio.to_thread.run_sync(..., abandon_on_cancel=False, limiter=OPENCLAW_CAPABILITY_WORKER_LIMITER)`.
- The awaited worker opens and closes its **own** SQLAlchemy `Session`; inside that thread it runs the existing authentication/integration-enabled check, constructs the non-serializable authentication proof/audit context, performs catalog re-read/freeze, evidence construction, synchronous Gateway execution, external result transformation, and commit/rollback. The async execute route no longer injects/constructs a request Session, and no bearer/secret enters frozen data, logs, errors, events, results, or worker return values.
- `OPENCLAW_CAPABILITY_WORKER_LIMIT=8` is a checked platform constant for v1, not a request value. The limiter is dedicated to this bridge so slow Capability/HITL work cannot exhaust the framework’s default worker pool.
- The worker is not detached and no thread implements a timeout. Cancellation/disconnect may set a thread-safe cooperative cancellation event, but the async endpoint does not report completion/cancellation while a side-effecting worker is still running.
- Both `legacy` and `shared` selected branches use this same awaited worker boundary during 02A, keeping event-loop and Session behavior comparable. Branch selection still occurs exactly once and no request crosses modes.

Tests run a heartbeat coroutine alongside two blocking fake Capabilities, prove the event loop remains responsive and calls overlap up to the limiter, prove the ninth call waits, prove each worker has a distinct Session, and prove cancellation never abandons a continuing side effect. Plan 03 remains free to call the synchronous Gateway directly.

Workflow/Agent engine reuse requires one additive ephemeral port; a preflight check alone is insufficient because current engine code resolves Tools and models again:

~~~python
class ExactRuntimeDependencyResolver(Protocol):
    def require_tool(
        self,
        *,
        source_locator: str,
        tool_name: str,
    ) -> ExecutableToolTarget: ...

    def require_workflow_version(
        self,
        *,
        source_locator: str,
        workflow_id: UUID,
        version_id: UUID,
    ) -> ExecutableWorkflowVersionTarget: ...

    def require_model(
        self,
        *,
        source_locator: str,
        requested_model_id: UUID | None,
    ) -> AuthorizedModelRuntimeConfig: ...


@dataclass(frozen=True)
class WorkflowEngineExecutionScope:
    dependency_resolver: ExactRuntimeDependencyResolver
    binding_contract_digest: str
    dependency_closure_digest: str
    nesting_depth: int
    safe_diagnostics: bool = True
    allow_ambient_memory: bool = False
    allow_global_graph_cache: bool = False
~~~

`ExactRuntimeDependencyResolver` is constructed inside the selected adapter from the already verified non-secret closure and allowed `CapabilityPolicyDecision`; it cannot exist in an activation-capable form before the dispatch permit is consumed. `AuthorizedModelRuntimeConfig` may contain a decrypted credential/client handle only after the resolver rechecks the corresponding Plan 01 `ResolvedCapabilityDependency(dependency_type="model")` immediately before client construction. It never enters a frozen contract, cache, event, error, or log.

The additive engine contract is locked:

- `LangGraphEngine(..., execution_scope=None)` preserves the exact Legacy behavior and current name/component lookup. Existing callers pass nothing.
- Capability Workflow execution always passes a non-null scope built from the already verified binding closure.
- Root Tools, inline Agent Tools, KB Tools, child Workflow versions, child Tools, default models, and explicit node models all call `require_*`; a missing locator/ref raises a safe frozen-dependency error. Capability mode has no Registry/latest/component-default fallback.
- `MAX_CAPABILITY_NESTING_DEPTH=4` is the Plan 02 platform execution ceiling. Root execution starts at `0`; each nested `workflow_call` increments before child construction; iteration/loop bodies and ordinary Tool/model nodes do not increment merely by iterating. Depth `5` is denied before child execution. This is independent from Plan 01’s structural closure-depth limit `16`; Plans 03/05 may freeze a lower per-Run budget but never raise the platform ceiling.
- `allow_ambient_memory=false` prevents automatic L1/L2/workflow-call memory lookup or write merely because a correlation `conversation_id` exists. Authorized memory must arrive through the frozen Capability input contract or a later explicit memory Capability/policy; Plan 02 does not invent one.
- `allow_global_graph_cache=false` is mandatory in Plan 02. The current cache key omits binding/dependency/model revisions and the compiled graph closes over Tool objects/model clients, so Capability execution compiles a request-local graph and never reads/writes the Legacy global graph cache. A later refactor may cache a dependency-free graph template under a stronger contract; this plan does not claim that optimization.
- The resolver preflights the entire reachable closure before engine/model/credential construction. Branches need not execute, so “unused dependency” is not an error; an undeclared dependency requested at runtime is.
- The non-serializable `WorkflowEngineExecutionScope` is an explicit field on `WorkflowNodeBuilderDeps` and an explicit argument to container-body execution. `workflow_dag_assembler.py`, iteration/loop `container_runtime.py`, and nested `workflow_call` propagate the same resolver, safe-diagnostics flag, memory rule, cache rule, and incremented depth; scope is never hidden in serializable `WorkflowState`.
- The Workflow engine module owns only the generic Protocol/scope and never imports `app.assistant.capabilities`. `execution_closure.py` implements the Protocol, preserving dependency direction.
- The Agent adapter invokes the current `run_agent_execution(...)` core with pre-resolved `bound_tools`/`tool_runners` and an exact model client; it does not route through `LangGraphEngine._build_tools(...)`. No change to `agent_execution_core.py` is needed.

### 4.4 Result, errors, and retry disposition

~~~python
class ArtifactRef(FrozenContract):
    artifact_id: str
    media_type: str
    content_digest: str


class ContinuationRef(FrozenContract):
    continuation_type: str
    contract_version: int
    reference_id: str
    payload_digest: str


class CapabilityValidationIssue(FrozenContract):
    instance_pointer: str
    schema_pointer: str
    keyword: str
    safe_message: str


class CapabilityError(FrozenContract):
    error_type: Literal[
        "not_found",
        "unavailable",
        "version_drift",
        "unauthorized",
        "invalid_input",
        "invalid_output",
        "timeout",
        "cancelled",
        "execution_failed",
        "unsupported_interrupt",
        "protocol_error",
    ]
    safe_code: str
    safe_message: str
    retry_disposition: Literal[
        "never",
        "new_run_only",
        "same_call_after_reconciliation",
        "model_may_continue",
    ]
    target_identity: str | None = None
    call_id: str | None = None
    validation_issues: tuple[CapabilityValidationIssue, ...] = ()


class CapabilityMetrics(FrozenContract):
    duration_ms: float
    adapter_duration_ms: float | None = None
    input_bytes: int
    output_bytes: int


class CapabilityResult(FrozenContract):
    status: Literal["completed", "failed", "cancelled", "waiting"]
    user_text: str | None
    structured_output: JsonValue | None
    artifact_refs: tuple[ArtifactRef, ...]
    continuation: ContinuationRef | None
    terminal_output: bool
    needs_followup: bool
    error: CapabilityError | None
    metrics: CapabilityMetrics
~~~

Invariants:

- `completed` has no error and no continuation.
- `failed` has an error and no continuation.
- `cancelled` has a `cancelled` error and no continuation.
- `waiting` requires a portable continuation. Plan 02’s new runtime paths do not produce it; a current blocking OpenClaw Workflow either finishes in compatibility mode or fails `unsupported_interrupt`.
- `user_text` is for user-facing text only; structured output is not stringified into it automatically.
- Artifact/continuation references are portable identifiers and digests only; they never contain file handles, callbacks, Sessions, clients, or execution objects.
- Errors never include request input, Tool output, prompt text, credential material, headers, raw provider/remote response bodies, SQL, stack traces, or arbitrary exception strings.
- `validation_issues` is present only for `invalid_input|invalid_output`, is deterministically sorted and capped at 20, and contains paths/keywords plus generic bounded text only. It never contains the rejected value, Schema descriptions/defaults/examples, regex bodies, enum members, or arbitrary validator exception text.
- `execution_failed` from a normal target failure may be `model_may_continue`. Authorization, version, protocol, invalid output, and unknown-side-effect failures are fatal to the current loop.

### 4.5 Events

Define a small event contract now so Plan 03 does not invent adapter-specific callbacks:

~~~python
CapabilityEventType = Literal[
    "capability.resolved",
    "capability.authorized",
    "capability.started",
    "capability.child_event",
    "capability.completed",
    "capability.failed",
    "capability.cancelled",
]


class CapabilityEventMetadata(FrozenContract):
    binding_contract_digest: str | None = None
    dependency_closure_digest: str | None = None
    duration_ms: float | None = None
    adapter_duration_ms: float | None = None
    input_bytes: int | None = None
    output_bytes: int | None = None
    child_node_id: str | None = None
    child_node_type: str | None = None
    compatibility_only: bool = False


class CapabilityRuntimeEvent(FrozenContract):
    event_type: CapabilityEventType
    call_id: str
    capability_key: str
    target_identity: str
    capability_type: Literal["tool", "workflow", "agent"]
    safe_status: str | None = None
    child_event_type: str | None = None
    metadata: CapabilityEventMetadata
~~~

Events contain only the explicit IDs, digests, status, safe durations/counts, and child node/event fields above. There is no arbitrary metadata escape hatch. They do not include raw input/output. Existing OpenClaw request source/channel/session/tool fields remain in its structured log line and execution context; they are not copied indiscriminately to every event.

---

## 5. JSON Schema Contract

Plan 01 must already declare `jsonschema>=4.23,<5` directly in `backend/requirements.txt` because publish-time contract normalization and Plan 02 runtime validation share it. Task 0 fails if it is still only transitive. Plan 02 does not touch `requirements.txt` merely to re-add or reorder the same dependency.

Use `Draft202012Validator` with these rules:

- Import Plan 01’s `normalize_binding_schema(...)` and `binding_schema_digest(...)` under their original names. Plan 02 exposes only `compile_binding_schema(normalized_body, expected_digest, require_object_root)` plus value validation; it does not wrap normalization behind renamed `root_kind` semantics or create a second dialect.
- Reuse Plan 01's canonical normalization output and fixed digest vectors. Runtime rejects a binding whose body normalizes to a different digest; it never silently rewrites the published contract.
- Compile and schema-check once per `(schema_digest, require_object_root)` using a bounded, thread-safe in-process cache.
- Capability input schema root must describe an object.
- Output schema may describe any JSON value; current OpenClaw compatibility schemas remain object-rooted.
- Normalize the legacy OpenAPI `nullable: true` extension into a type union before digest/compile.
- Do not apply defaults or mutate input/output.
- Do not fetch remote `$ref` values.
- Local `$defs`/`$ref` are allowed only when fully contained in the schema and recursion stays within validator limits.
- Reject custom remote URI references, unknown non-JSON values, invalid regexes, and invalid schema documents before dispatch.
- `format` remains an annotation in this plan; do not introduce stricter date/URI behavior that breaks current contracts without explicit characterization.
- Return at most 20 sorted validation failures.
- Error details expose only JSON Pointer path, schema pointer, validator keyword, and a bounded generic message. Enum values, regex text, defaults, descriptions, and rejected values are not copied into public issues.
- Error ordering is deterministic.
- The cache stores only checked normalized Schema data and validator instances; it never stores request values, output values, Sessions, entrypoint evidence, or mutable caller dictionaries. Tests must exercise concurrent reads because Plan 03 may share this pure cache across isolated Gateway contexts.

The shared validator replaces duplicated generic schema logic. OpenClaw-specific Pydantic input translation remains in the OpenClaw bridge because `entryId -> entry_id` and similar compatibility mappings are not universal Capability behavior.

During 02A, fixed OpenClaw external-schema vectors run through both the legacy private validator and the new shared Draft 2020-12 compiler. Every acceptance/rejection difference is classified before shared mode: either preserve it in an explicit OpenClaw external adapter, migrate/reject the catalog item with a documented `42261`, or block 02B. The bridge never changes Plan 01 internal binding normalization to imitate a legacy private-validator quirk.

---

## 6. Side-Effect, Parallel-Safety, and Interrupt Classification

### 6.1 Risk lattice

Classification is a conservative upper bound:

~~~text
none < compute < read < draft < write_local < write_external < unknown
~~~

This total order is used only for conservative aggregation. It is not a claim that external mutation is always “worse” than local mutation in every business context.

This is a deliberate runtime refinement of the overall design's coarse taxonomy, not a competing vocabulary:

| Overall design term | Plan 02 runtime value |
|---|---|
| `compute` | `compute` |
| `read` | `read` |
| `draft` | `draft` |
| `write` | `write_local` or `write_external` according to the committed system boundary |
| `control` | not a business Capability class in Plan 02; future `skill.inject`/input control remains outside this runtime until its owning plan |
| no effect / cannot prove | `none` / `unknown` respectively |

Persisted policy and evidence use the refined Plan 02 value. UI or migration adapters may render the coarse label, but authorization never downcasts `write_external` or `unknown` into a weaker class.

When a future native Skill policy is projected, its Plan 01 author declaration expands deterministically: `compute->{compute}`, `read->{read}`, `draft->{draft}`, `write->{write_local,write_external}`; `control` is rejected at this Gateway because control Capabilities belong to later plans. `none` is granted only by an explicit system/entrypoint rule. Expansion is an authorization ceiling, never a classifier input. Plan 02 production registers no native Skill verifier yet.

Classification is versioned independently from immutable target publication:

~~~text
CLASSIFICATION_CONTRACT_REVISION = "plan02-v1"
classification_ruleset_digest = sha256(canonical declarative risk/tool/node/agent/interrupt/timeout rules)
behavior_digest = sha256(classification_ruleset_digest + exact target/closure digests + derived behavior)
~~~

The declarative ruleset is the classifier’s input and has a checked-in fixed vector. Changing a table/rule without bumping the revision fails tests. More conservative hot code may still deny immediately, but it cannot emit a descriptor under the old behavior digest.

Definitions:

| Class | Meaning | Minimum-policy behavior |
|---|---|---|
| `none` | Control/formatting without business data access | requires exact grant |
| `compute` | Bounded local computation with no I/O | requires exact grant |
| `read` | Reads MindAtlas/user/system data or sends already authorized data to an existing read service | requires exact grant |
| `draft` | Creates a proposal or pending human decision without committing the requested business mutation | denied to future assistant paths in Plan 02 |
| `write_local` | Commits MindAtlas-owned business data | denied to future assistant paths in Plan 02 |
| `write_external` | May mutate an external system or is a remote Tool whose method semantics cannot be proven | denied to future assistant paths in Plan 02 |
| `unknown` | A safe upper bound cannot be proven | always denied |

Read-only is not public. The Policy still requires an authenticated Principal, exact owner, exact capability, entrypoint, and explicit read grant.

### 6.2 Code-native Tool classification

Create a checked-in `SYSTEM_TOOL_CLASSIFICATIONS` map and an exhaustive test against the new stable `ToolRegistry.list_runtime_system_tool_names()`. That API returns the exact declared `_EXPORTS` manifest plus explicit internal runtime names, including all `openclaw_*` compatibility Tools; it is also the allowlist enforced by `resolve_system_tool`. Arbitrary module attributes and undeclared monkeypatched names are not publishable/resolvable Capabilities. Characterization must prove no legitimate legacy caller depended on such an undeclared attribute before tightening resolution.

Initial classification to verify against implementation behavior:

| Tool | Side effect | Parallel safe |
|---|---:|---:|
| `search_entries` | read | true |
| `search_similar_entries` | read | true |
| `get_entry_detail` | read | true |
| `create_entry` | write_local | false |
| `update_entry` | write_local | false |
| `create_relation` | write_local | false |
| `query_knowledge_graph` | read | false |
| `generate_weekly_report` | write_local | false |
| `generate_monthly_report` | write_local | false |
| `openclaw_capture_entry` | write_local | false |
| `openclaw_search_entries` | read | true |
| `openclaw_get_entry` | read | true |
| `openclaw_create_relation` | write_local | false |
| `openclaw_query_knowledge_graph` | read | false |
| `get_statistics` | read | true |
| `get_entries_by_time_range` | read | true |
| `analyze_activity` | read | true |
| `get_tag_statistics` | read | true |
| `list_entry_types` | read | true |
| `list_tags` | read | true |
| `kb_relation_recommendations` | read | false |
| `kb_search` | read | false |

Before locking the map, inspect each implementation and adjust only toward a more conservative value. Any runtime Tool missing from the map resolves to `unknown` and `parallel_safe=false`. The exhaustive test must fail when a future Tool is exported without an explicit classification review.

Remote Tools default to `write_external`, `parallel_safe=false`, and `interrupt_mode=none` regardless of HTTP method. A later plan may add an immutable reviewed remote-Tool policy field; Plan 02 must not infer safety from a mutable GET label.

### 6.3 Workflow classification

Classify the exact published snapshot recursively:

| Node type | Classification rule |
|---|---|
| `start`, `output`, `if_else`, `variable_assign` | none |
| `llm`, `parameter_extractor` | read; never parallel-safe at Workflow level |
| `knowledge_retrieval` | read |
| `code_executor` | `unknown` in Plan 02 v1 because its sandbox/settings profile is mutable and absent from the Plan 01 closure contract |
| `http_request` | read only for a literal supported `GET`, static SSRF-valid URL, verified TLS, and reviewed retry/auth configuration; POST/PUT/PATCH/DELETE are write_external; templated URL, unsupported/coerced method, disabled TLS verification, file body, or ambiguous config is unknown |
| `tool` | resolve the frozen Tool and use its classification |
| `agent` | classify every frozen Tool in that exact node/profile configuration; unresolved/dynamic Tools are unknown |
| `workflow_call` | recurse into the pinned published Workflow version; latest/dynamic/unowned target is unknown |
| `iteration`, `loop` | maximum of the body only when max iterations and body graph are statically bounded; otherwise unknown |
| `human_in_loop` | draft, `interrupt_mode=legacy_blocking` in current OpenClaw compatibility, never parallel-safe |

Rules:

- Use a visited stack of `(workflow_id, published_version_id)` and return `unknown` on a cycle.
- Import and enforce the Plan 01 constants `MAX_CAPABILITY_CLOSURE_DEPTH=16`, `MAX_CAPABILITY_CLOSURE_REFS=256`, and `MAX_CAPABILITY_CLASSIFIED_NODES=4096`; do not redeclare differently named or looser local limits. Exceeding a limit is `unsupported/unknown`, never partial classification. These are reviewed safety limits, not request-controlled values.
- Classification reads the exact frozen target version even if the aggregate now points to a newer publish.
- Unknown node types, missing node configs, dynamic Tool names, unpinned Workflow calls, invalid nested snapshots, and missing targets produce `unknown`.
- Do not mirror current runtime coercions that turn an unsupported HTTP method into `GET`; classification treats that configuration as `unknown` and unavailable.
- Current code-executor allowlists are environment-controlled. Any Workflow closure containing `code_executor` is unavailable in shared mode and stays on 02A legacy or is disabled before 02B. Granting `compute` later requires an explicit immutable sandbox-profile contract, Plan 01 closure/digest extension, drift checks, and a reviewed plan change; Plan 02 must not synthesize a digest from live Settings.
- A Workflow is `parallel_safe=true` only when every reachable node is `none|compute|read`, every nested target is parallel-safe, the graph has no human/loop/agent/write/external node, and the Workflow adapter explicitly opts in. Default is false.
- The Workflow descriptor’s interrupt mode is the strongest reachable interrupt mode.

### 6.4 Agent classification

Classify the exact published Agent version:

- Start at `read` because executing the Agent invokes the configured model over authorized context.
- Resolve every declared Tool through frozen references and take the maximum side effect.
- If KB is enabled, include `kb_search=read`.
- Missing, disabled, dynamic, or unresolvable Tool references produce `unknown`.
- Current Agent Capabilities always have `parallel_safe=false`.
- Current Agent execution has `interrupt_mode=legacy_blocking` if any reachable Tool/Workflow can block for human input; otherwise `none`.
- Agent recursion/nesting is not enabled here. If the snapshot implies nested Agent/Main Agent restart, classify `unknown` and mark unavailable.

---

## 7. Minimum Policy Contract

The minimum Policy is intentionally small but real.

Decision order:

1. Reject invalid execution context, including `nesting_depth < 0` or `> MAX_CAPABILITY_NESTING_DEPTH`, before target/model construction; then reject unauthenticated Principal.
2. Reject unknown issuer/entrypoint combination.
3. Require evidence `call_id` to equal the execution context and require the verifier to be request-scoped/single-use.
4. Verify evidence through the trusted entrypoint verifier.
5. Require exact equality of owner, capability key, binding-contract digest, dependency-closure digest, and frozen resolution digest.
6. Require the descriptor to be available.
7. Require `descriptor.behavior.side_effect` to be a member of the independently derived `allowed_side_effects` ceiling.
8. Reject `unknown` even if a malformed grant lists it.
9. Apply entrypoint ceiling.
10. Return an immutable allow/deny decision whose digest covers call, principal, entrypoint, owner, binding/resolution/dependency digests, actual side effect, classification ruleset digest, verifier key, `grant_source_digest`, and safe reason.

Entrypoint ceilings in Plan 02:

| Entrypoint | Production issuer | Maximum behavior |
|---|---|---|
| `openclaw` | `openclaw_bridge` | exact capability exposed by the authenticated OpenClaw catalog item; existing read/write behavior may continue |
| `main_agent` | none | always deny |
| `workflow` | none for new shared calls | always deny |
| `agent` | none for new shared calls | always deny |
| `test` | injected test verifier only | exact fixture scope |

The OpenClaw grant source has one locked secret-free shape:

~~~python
class OpenClawEffectCeiling(FrozenContract):
    ceiling_scope: Literal["system_item", "custom_source_type"]
    ceiling_key: str
    revision: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none", "legacy_blocking"], ...]
    ceiling_digest: str
~~~

The digest covers every field except itself. System rows use the exact system item key. Custom rows use only the checked-in source type; exact catalog-item exposure is separate verifier evidence. No ceiling permits `unknown` or `durable`.

Initial maximums to verify against the current six system definitions are locked conservatively:

| System item key | Maximum effect | Allowed interrupt |
|---|---|---|
| `search_entries` | `read` | `none` |
| `get_entry` | `read` | `none` |
| `create_relation` | `write_local` | `none` |
| `query_knowledge_graph` | `read` | `none` |
| `submit_context_capture` | `write_local` | `none` |
| `generate_periodic_review` | `write_local` | `none` |

`OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS` initially gives each custom `tool|workflow|agent` source a compatibility maximum of `write_external`; Tool allows only `none` interrupt, while Workflow/Agent allow `none|legacy_blocking` to preserve the current compatibility surface. The 02A/02B inventory must still identify every actual blocking item and approve/disable it before cleanup. These broad source maxima preserve already exposed compatibility behavior, but they do not grant a different item, native Skill, Main Agent, `unknown`, `durable`, or a missing target. `allowed_side_effects` is the ordered lattice prefix through the maximum, not a one-value tuple copied from the descriptor.

OpenClaw compatibility is not a global bypass:

- Its evidence allows exactly one catalog item and one resolved target.
- The item must still be enabled, not retired, available, and tied to the authenticated integration.
- The evidence digest covers call ID, catalog item ID/key/source binding, item schema digests, tool response mode, enabled state, binding/dependency digests, resolved target digest, and the independent `grant_source_digest`.
- The verifier instance is bound to exactly one authenticated request and call ID, and its allow result is consumed once. Copying the Pydantic evidence object cannot authorize a second call.
- It never grants another catalog item merely because both point at the same Tool.
- It never becomes reusable by the future Main Agent.
- For checked-in system items, `OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS` is exhaustive against `list_openclaw_system_item_definitions()`. Each row declares the maximum allowed refined effect, allowed interrupt mode, ceiling revision, and a canonical row digest.
- For custom catalog items, a checked-in `OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS` table declares only the maximum compatibility envelope per source type (`tool|workflow|agent`). The exact enabled catalog row/source binding is still required, and every enabled item plus its actual classified effect appears in the generated 02A release inventory. Mutable database contents are not copied into a fake static source-of-truth file.
- The grant source is independent of the classifier: evidence obtains `allowed_side_effects` from the applicable ceiling row, never from `descriptor.behavior`. Policy then checks the independently classified actual effect against that ceiling. Missing ceiling, `unknown`, an effect above the ceiling, or an unapproved `legacy_blocking` interrupt denies before dispatch.
- `grant_source_digest` covers the selected ceiling revision/row plus exact catalog exposure evidence. The frozen OpenClaw call and final decision retain that digest so a ceiling change cannot be mistaken for the old authorization.
- The 02A preflight must prove every enabled system/custom item has a ceiling and inventory row. A newly enabled item without both is unavailable in shared mode; it is not silently routed through legacy inside the request.

Policy tests must prove a manually constructed evidence model cannot pass without the matching injected verifier, cannot be replayed under another `call_id`, and cannot be reused after a successful verification. They must also mutate a descriptor from `read` to `write_local` while keeping a read ceiling and prove denial; a verifier that simply copies the descriptor effect into `allowed_side_effects` must fail the test.

---

## 8. Timeout, Cancellation, Credential, and Session Rules

- Check cancellation before resolution, before policy, immediately before adapter invocation, at adapter cooperative checkpoints, and after adapter return.
- Cancellation before a side effect starts returns `cancelled`.
- Plan 02 has no durable ledger and therefore cannot truthfully distinguish all “side effect may have committed” cases after an abrupt failure. It must return a safe non-retryable execution error; Plan 08 adds `unknown/needs_reconciliation` persistence.
- Do not implement timeout with an abandoned thread or future.
- Remote Tool uses its existing native urllib timeout and redirect validation.
- Workflow/Agent use cooperative cancellation at existing engine boundaries. A descriptor that cannot enforce the requested timeout is marked `compatibility_only` for OpenClaw and unavailable to future assistant entrypoints.
- Credential decryption happens only inside the Tool adapter after successful reference, schema, availability, and policy checks.
- No descriptor, event, error, log metadata, or result contains encrypted or decrypted credentials.
- Registry/classifier may read non-secret config and credential-slot revision evidence, but may not decrypt. The adapter must recheck the exact Tool/config revision immediately before decryption and request construction; a mismatch fails before network I/O.
- Unexpected exceptions are untrusted data. Shared-runtime paths log only stable call/target IDs, safe error code, stage, and exception class name. They do not format `str(exc)`, `repr(exc)`, `exc.args`, traceback/`exc_info`, request arguments, child output, or provider/remote bodies. Tests inject fake secrets into exception text and traceback messages and inspect every reachable INFO/WARNING/ERROR record.
- Existing Workflow execution contains log/event paths that can format Tool or model exceptions and raw node payloads. Capability adapters must use an additive safe-execution projection/sanitizer mode, and Task 5 audits every adapter-reachable log/event path. It is not sufficient to sanitize only the final `CapabilityError` after an inner engine already logged the exception.
- The Gateway instance is request/session scoped. It may hold the current SQLAlchemy `Session` outside frozen contracts.
- OpenClaw's FastAPI service is async while the current Tool/Workflow/Agent stack is synchronous. Both 02A branches therefore cross the same bounded awaited worker boundary defined in Section 4.3. The request-side Session is never used in the worker; the worker creates and closes its own Session and request-scoped Gateway. Event-loop heartbeat, limiter saturation, cancellation, and Session-affinity tests are release gates.
- `conversation_id` in execution context is correlation, not ambient-memory authorization. Plan 02 Capability Workflow/Agent execution disables automatic L1/L2/workflow-call memory reads/writes; data needed by the Capability must be present in its validated input or already be read by an explicitly authorized nested Capability.
- With a non-null Capability scope and `allow_ambient_memory=false`, the engine must skip `_load_runtime_memory_overrides`, `_load_l1_summary`, `_load_l2_text`, and Workflow-call memory get/upsert paths. L1/L2/runtime scopes are empty; L0 contains only the explicitly supplied validated input/history. A positive Legacy test with `execution_scope=None` proves the existing memory behavior remains unchanged.
- Plan 03 parallel execution must create independent Gateway/Session contexts; Plan 02 never claims its request Session is thread-safe.
- Request-scoped catalog row locks or optimistic revision checks must end deterministically on every result. Do not hold an idle database transaction while waiting for human input. Task 0 inventories every enabled item with `interrupt_mode`, `legacy_blocking_allowed`, and parity disposition. A characterized OpenClaw compatibility call may retain blocking only when its independent ceiling and inventory row explicitly allow it and all catalog/reference locks have been released before polling; otherwise shared mode returns the characterized unsupported mapping and the item blocks 02B or is disabled. Future Main Agent/Plan 03 callers never receive this compatibility exception.

### 8.1 Frozen-scope coverage matrix

Changing only the root engine and `workflow_call_node.py` is insufficient. Task 0 must turn the following matrix into an executable audit list; any runtime-reachable builder/helper omitted from the repository inventory blocks Task 5 until it is classified and covered.

| Runtime surface | Exact Tool/Workflow target | Exact model target | Ambient memory/cache | Safe diagnostics/events | Required proof |
|---|---|---|---|---|---|
| `engine.py` | root Tool map through scope only | root/default models through scope only | memory off and compiled-graph cache bypassed in Capability mode | no raw exception/payload | Legacy-null scope remains unchanged |
| `workflow_dag_assembler.py` | pass scope into every builder | pass scope into every model-using builder | no hidden global lookup | pass safe flag/event sink | scope is an explicit non-state dependency |
| `container_runtime.py` | rebuild iteration/loop body from the same closure | reuse exact scoped model resolver | no ambient memory re-entry | sanitize body failure/item errors | nested body cannot lose scope |
| `workflow_call_node.py` | exact child version and child Tool closure | exact child default/custom models | skip child memory get/upsert | safe child error/event | nesting depth increments once |
| `tool_node.py`, `agent_tool_node.py`, `knowledge_node.py` | exact scoped Tool/KB target | n/a | no ambient fallback | no `str(exc)`/result logging | Registry lookup is unreachable in Capability mode |
| `llm_node.py`, `param_extractor_node.py`, `dag_agent_node.py` | exact Agent Tool set where applicable | exact frozen model/credential slot | no component-default fallback | no provider/Tool error text | current component binding mutation cannot redirect |
| `workflow_node_llm_resolver.py`, `snapshot_input_resolvers.py`, `agent_subgraph.py` | exact frozen locator/version input | exact root/container custom models | no current-published/component fallback | safe Agent child failures | pre-build helpers cannot escape the scope |
| `iteration_node.py`, `loop_node.py` | container receives same closure | same exact model resolver | loop itself does not increment nesting depth | no raw item/exception projection | nested `workflow_call` does increment |
| `code_executor_node.py`, `http_request_node.py`, `human_in_loop_node.py` | classification/ceiling enforced before entry | n/a | no hidden memory | safe generic failures | code executor is unavailable v1; HITL inventory rule applies |
| `runtime_helpers.py`, `snapshots.py`, `stream_runtime.py` | n/a | n/a | no serialization of scope | no raw input/output/error forwarding | fake-secret log/event sweep at all levels |
| `execution_services.py` | n/a | n/a | no transaction held across HITL wait | approval events use safe projection | blocking compatibility follows inventory/ceiling |

The matrix is about runtime reachability, not just files currently known to contain a fallback. Task 0 enumerates all `node_builders/*.py`, marks each `exact|not-applicable|blocked`, and records the responsible test. `workflow_dag_assembler.py` carries `WorkflowEngineExecutionScope` in `WorkflowNodeBuilderDeps`; `container_runtime.py` receives it as an explicit argument. Neither stores it in `WorkflowState`, graph snapshots, checkpoints, or event metadata.

---

## 9. OpenClaw Compatibility and Rollback Boundary

This semantic plan requires two code merge points because removing the old path before observing the shared path would eliminate the safe rollback mechanism.

### Plan 02A: build and switch-capable release

- Add temporary `OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy|shared`.
- Validate it with a Pydantic `Literal`/field validator.
- Default to `legacy` in the first release.
- Propagate it through `backend/.env.example`, `deploy/.env.example`, and `deploy/docker-compose.yml`.
- Freeze the selected mode in a local value at the start of each authenticated execution request.
- Treat production selection as process-start configuration because `get_settings()` is cached. Document the exact rolling-restart/deployment command and mixed-instance window; do not advertise a hot toggle.
- Never retry through the other mode within that request.
- Exercise `shared` in unit/integration/staging tests.
- Emit one secret-safe terminal diagnostic per request with request/call ID, selected mode, source type, capability key, target/binding/dependency digests, semantic result/error category, invocation-started flag, and duration. Do not emit input/output, catalog Schema bodies, prompts, response bodies, headers, or credentials.

### Operational observation

- Switch only future requests to `shared`.
- Observe a bounded, documented window covering each enabled OpenClaw Tool/Workflow/Agent class.
- Compare status/error category/latency and externally visible Schema against the pre-cutover legacy window plus isolated deterministic parity fixtures. Production requests are not pairwise shadowed, and a real side-effecting request is never executed through both paths.
- Roll back future requests by switching the flag only if no shared request is currently being retried through legacy. Started requests finish/fail on their selected path.
- For a rolling deployment, log an instance/build revision and selected mode so the observation query can separate old/new pods. The window is invalid if traffic mode cannot be attributed to a build.

### Plan 02B: cleanup release

- After the observation gate passes, delete the three legacy execution branches and duplicated generic schema/runtime helpers.
- Remove the temporary mode from Settings, examples, Compose, and tests.
- OpenClaw always calls the shared bridge.
- After this point rollback means deploying the previously verified 02A image/config, not changing a nonexistent flag.
- 02B is a separate cleanup merge/release gate. A PR may prepare it, but it cannot merge merely because unit parity is green; the observation evidence and explicit approval are required.

Do not mark the full Plan 02 complete after 02A alone. If the deployment observation cannot be performed in the implementation environment, stop at the documented 02A handoff and leave 02B unchecked.

Public compatibility requirements:

- Same runtime routes and request bodies.
- Same `ApiResponse` envelope.
- Same `capabilityKey`, `toolName`, and `result` response fields.
- Same locale behavior.
- Same catalog exposure and retirement behavior.
- Same authentication and integration-disable behavior.
- Same OpenClaw request source/channel/session/tool audit fields.
- Same existing OpenClaw system Tool input/output adapters.
- Stable current error HTTP/code mapping where the same condition exists.

No production “shadow execution” is permitted for side-effecting Capabilities.

---

## 10. Stable Internal-to-OpenClaw Error Mapping

The shared runtime owns semantic errors. Task 0 first generates a checked-in characterization fixture for **every runtime-reachable** execute-path condition and public HTTP/application-code pair. It must include the common `40161`, `40361`, `40362`, `40461`, `40062`, `42261`, `42262`, `40961` paths and any currently reachable setup/configuration codes such as `40061`, `40064`, `40965`, or `50038`. Admin-only codes are not pulled into execution merely because they exist elsewhere. Task 8 may implement only a mapping supported by that fixture:

| Shared error | OpenClaw mapping |
|---|---|
| `not_found` | HTTP 404 / `40461` |
| item/integration disabled before Gateway | existing HTTP 403 / `40361` or `40362` |
| `unavailable`, `version_drift`, `unsupported_interrupt` | HTTP 409 / `40961` |
| `unauthorized` after authenticated catalog resolution | HTTP 403 / `40362` and security diagnostic |
| `invalid_input`, `invalid_output`, invalid shared schema | HTTP 422 / `42261` |
| invalid source/reference construction before Gateway | HTTP 422 / `42262` |
| `timeout`, `execution_failed` | preserve the exact current safe runtime failure envelope/status selected by characterization; never expose raw exception text |
| `cancelled` | preserve a characterized current request-cancellation result if one exists; otherwise do not invent a public cancellation code |

Authentication `40161` remains outside the Gateway because the router/service authenticates before constructing evidence.

`cancelled` and `timeout` are internal Capability states, not permission to create new OpenClaw public contracts. Because the awaited worker is not abandoned, the endpoint must not report cancellation while a side effect may still be running; absent an existing cancellation envelope it awaits the worker's honest final result/error. Native remote timeout and existing engine failure paths retain their characterized mappings. Any new public code requires a separate API-contract change, not an implementation guess in Plan 02.

---

## 11. File Responsibility Map

### Create

- `backend/app/assistant/capabilities/__init__.py`
- `backend/app/assistant/capabilities/contracts.py`
- `backend/app/assistant/capabilities/errors.py`
- `backend/app/assistant/capabilities/ports.py`
- `backend/app/assistant/capabilities/json_schema.py`
- `backend/app/assistant/capabilities/classification.py`
- `backend/app/assistant/capabilities/execution_closure.py`
- `backend/app/assistant/capabilities/safe_execution.py`
- `backend/app/assistant/capabilities/registry.py`
- `backend/app/assistant/capabilities/policy.py`
- `backend/app/assistant/capabilities/gateway.py`
- `backend/app/assistant/capabilities/runtime.py`
- `backend/app/assistant/capabilities/adapters/__init__.py`
- `backend/app/assistant/capabilities/adapters/base.py`
- `backend/app/assistant/capabilities/adapters/tool.py`
- `backend/app/assistant/capabilities/adapters/workflow.py`
- `backend/app/assistant/capabilities/adapters/agent.py`
- `backend/app/assistant/workflow/engine/runtime_dependency_resolver.py` — generic optional resolver/safe-scope Protocol only; no Capability import.
- `backend/app/openclaw_integration/capability_adapter.py`
- `backend/app/openclaw_integration/runtime_worker.py` — the one bounded awaited async/sync bridge and worker-owned Session lifecycle.
- `backend/tests/test_capability_contracts.py`
- `backend/tests/test_capability_json_schema.py`
- `backend/tests/test_capability_registry.py`
- `backend/tests/test_capability_classification.py`
- `backend/tests/test_capability_execution_closure.py`
- `backend/tests/test_capability_workflow_engine_scope.py`
- `backend/tests/test_capability_tool_adapter.py`
- `backend/tests/test_capability_workflow_adapter.py`
- `backend/tests/test_capability_agent_adapter.py`
- `backend/tests/test_capability_policy.py`
- `backend/tests/test_capability_gateway.py`
- `backend/tests/test_openclaw_shared_capability_runtime.py`
- `backend/tests/test_openclaw_capability_worker.py`
- `backend/tests/fixtures/openclaw_runtime_error_contract.json`
- `docs/superpowers/evidence/plan-02a-readiness.md` — generated/reviewed Task 9 handoff, not prefilled design evidence.

### Modify during Plan 02A

- `backend/app/assistant/domain/json_schema.py` — reuse only; change only if a verified Plan 01 bug requires a separately reviewed Plan 01-compatible fix. Do not duplicate its normalization/digest rules.
- `backend/app/assistant_config/registry.py` — stable runtime Tool enumeration and exact definition lookup.
- `backend/app/assistant/tools/__init__.py` — expose one stable declared runtime export-name manifest used by Registry; no arbitrary module-attribute resolution.
- `backend/app/assistant_config/remote_tool.py` — safe structured invocation/error boundary only; no storage redesign.
- `backend/app/assistant/workflow/engine/engine.py` — additive optional exact dependency resolver and safe-execution mode; legacy default remains current Registry/log behavior.
- `backend/app/assistant/workflow/engine/workflow_dag_assembler.py` — carry non-serializable execution scope through `WorkflowNodeBuilderDeps`.
- `backend/app/assistant/workflow/engine/container_runtime.py` — preserve scope when rebuilding iteration/loop body nodes.
- `backend/app/assistant/workflow/engine/node_builders/workflow_call_node.py` — propagate the exact child resolver/closure and forbid current/latest fallback only in Capability mode.
- `backend/app/assistant/workflow/engine/node_builders/tool_node.py` — suppress arbitrary exception/result logging in Capability safe mode.
- `backend/app/assistant/workflow/engine/node_builders/agent_tool_node.py` — same safe-mode boundary for inline Agent Tool execution.
- `backend/app/assistant/workflow/engine/node_builders/knowledge_node.py` — same safe-mode boundary for retrieval failures.
- `backend/app/assistant/workflow/engine/node_builders/llm_node.py`
- `backend/app/assistant/workflow/engine/node_builders/param_extractor_node.py`
- `backend/app/assistant/workflow/engine/node_builders/dag_agent_node.py`
- `backend/app/assistant/workflow/engine/node_builders/iteration_node.py`
- `backend/app/assistant/workflow/engine/node_builders/loop_node.py`
- `backend/app/assistant/workflow/engine/node_builders/code_executor_node.py`
- `backend/app/assistant/workflow/engine/node_builders/http_request_node.py`
- `backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py`
- `backend/app/assistant/workflow/engine/runtime_helpers.py`
- `backend/app/assistant/workflow/engine/workflow_node_llm_resolver.py`
- `backend/app/assistant/workflow/engine/snapshot_input_resolvers.py`
- `backend/app/assistant/workflow/engine/agent_subgraph.py`
- `backend/app/assistant/workflow/engine/execution_services.py`
- `backend/app/assistant/workflow/human_approval_runtime.py` — characterize current polling/session behavior; modify only if the explicit compatibility cancellation/session rule cannot be enforced at `execution_services.py`.
- `backend/app/assistant/workflow/engine/snapshots.py`
- `backend/app/assistant/workflow/engine/stream_runtime.py`
- `backend/app/openclaw_integration/service.py` — extract compatibility bridge and select frozen runtime mode.
- `backend/app/openclaw_integration/router.py` — execute route snapshots bounded headers/payload and awaits the worker without a request-side SQLAlchemy Session; other sync routes remain unchanged.
- `backend/app/config.py` — temporary OpenClaw mode.
- `backend/requirements.txt` — declare `anyio>=4,<5` directly because Plan 02 imports its limiter/thread API; retain Plan 01's direct jsonschema range.
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml`
- `backend/tests/test_openclaw_integration.py`
- `backend/tests/test_remote_tool.py`
- `backend/tests/test_workflow_execution_context.py`
- `backend/tests/test_workflow_call_node.py`

### Modify during Plan 02B

- `backend/app/openclaw_integration/service.py` — remove old dispatch/schema branches.
- `backend/app/config.py` — remove temporary selector.
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml`
- OpenClaw/shared tests — remove legacy-mode-only cases while retaining public characterization.

### Must not modify

- Main assistant Router/Supervisor selection.
- `backend/app/assistant/workflow/engine/agent_execution_core.py`.
- Assistant conversation/run persistence.
- Plan 01 immutable version rows or migration, except a narrowly scoped bug fix agreed by updating Plan 01 first.
- Frontend.

---

## 12. Commit and Test Discipline

- Each task begins with a failing test or characterization assertion.
- Run the focused command and record the expected failure before implementation.
- Make the smallest implementation that satisfies that task.
- Re-run focused tests, then relevant regressions.
- Commit only the files listed by the task.
- Do not stage unrelated user changes.
- `git diff --check` is required at every merge checkpoint.
- Plan 02 adds no Alembic revision; nevertheless final verification runs `alembic upgrade head` against disposable PostgreSQL.
- If actual class/file names created by Plan 01 differ, update all commands and file maps in this document before proceeding.

Mandatory review checkpoints keep the large change independently auditable:

1. after Task 1: frozen DTO/schema dialect/digest vectors only;
2. after Task 2: exact root and dependency-closure identity with no decryption/execution;
3. after Task 3: versioned classifier and exhaustive Tool/node rules;
4. during Task 5: review root engine, assembler, container/nested builders, memory/cache, and safe diagnostics as separate commits or clearly separated diffs;
5. during Task 8: review worker boundary, dual-schema bridge, independent grant ceilings, and error mapping separately;
6. after Task 9: approve the generated Plan 02A readiness record before any Plan 03 dependency or production switch;
7. Task 10/11 are Plan 02B operational cleanup and must not be marked complete from test evidence alone.

---

## Task 0: Reconfirm Plan 01, Dependency, and Characterization Baselines

**Files:**

- Read: `backend/app/assistant/domain/contracts.py`
- Read: `backend/app/assistant/domain/digests.py`
- Read: `backend/app/assistant/skills/resolution.py`
- Read: Plan 01 migration and tests.
- Read: `backend/app/openclaw_integration/service.py`
- Read: `backend/app/assistant_config/registry.py`
- Read: `backend/app/assistant_config/remote_tool.py`
- Read: `backend/app/assistant_config/models.py`
- Read: `backend/app/assistant/tools/__init__.py`
- Read: `backend/app/assistant/workflow/engine/engine.py`, `workflow_dag_assembler.py`, `container_runtime.py`, and every `node_builders/*.py`.
- Read: Workflow runtime helper/snapshot/stream logging and memory paths reachable from those builders.
- Create: `backend/tests/fixtures/openclaw_runtime_error_contract.json` from current public behavior.
- Record only: implementation notes/PR description plus characterization fixture; no production code change.

**Interfaces:**

- Consumes the merged Plan 01 contract surface and current repository state.
- Produces a written baseline with exact names, sole Alembic head, Python/dependency versions, and passing characterization counts.
- Does not fix failures, create a migration, or edit runtime code.

- [ ] **Step 1: Verify a clean task boundary**

~~~bash
git status --short
git branch --show-current
git rev-parse --short HEAD
~~~

If the worktree contains user changes, preserve them. Create the implementation branch/worktree according to the project workflow; do not reset or absorb unrelated changes.

- [ ] **Step 2: Verify Plan 01 exit criteria**

Run the exact Plan 01 focused and PostgreSQL migration gates. At minimum prove:

- package parsing/publication tests pass;
- immutable manifest tests pass;
- published binding drift tests pass;
- published binding snapshot round-trips normalized input/output Schema bodies, completion metadata, Capability dependency closure, exact resolved default/custom model refs, and all canonical digests;
- Tool `config_revision` tests pass;
- `APP_BUILD_REVISION` is present and propagated;
- `jsonschema>=4.23,<5` is a direct declared dependency and Plan 01 fixed Schema digest vectors pass;
- one Alembic head exists;
- no Plan 01 runtime catalog was accidentally enabled.

If any Plan 01 exit criterion is incomplete, stop. In particular, if an Agent binding has only a Schema digest but no immutable Schema body, if a binding can only discover nested Tools/models from current state, or if a default model remains only `component=assistant`, amend Plan 01 first. Plan 02 must not compensate with duplicate types or mutable resolution.

- [ ] **Step 3: Record the final Plan 01 names**

Use `rg` to locate the actual:

- `ResolvedCapabilityRef`;
- frozen contract base and `JsonValue`;
- current-target reference and drift verifier;
- immutable Skill capability binding ORM;
- immutable input/output schema bodies or a lossless frozen binding-surface reconstruction path for every Tool/Workflow/Agent binding;
- the one authoritative persisted dependency-closure representation and its canonical ordering/digest;
- exact model dependency refs for Agent and every Workflow model-using node, including the actual model selected from a publication-time default component binding;
- resolver entrypoint for system Tool, remote Tool, Workflow, and Agent;
- final Plan 01 migration revision.

Update this document first if names or semantics differ.

- [ ] **Step 4: Freeze runtime-reachability, interrupt, and public-error inventories**

Before editing runtime code, produce three reviewed artifacts in implementation notes/tests:

- every `node_builders/*.py` row mapped to Section 8.1 (`exact|not-applicable|blocked`), including model/Tool lookup, memory/cache, log/event, nesting, and owning test;
- every enabled OpenClaw item with source type, response mode, external/internal Schema digests, classified effect, independent ceiling row/digest, interrupt mode, `legacy_blocking_allowed`, and 02A/02B disposition;
- `backend/tests/fixtures/openclaw_runtime_error_contract.json` containing every runtime-reachable execution condition and exact current HTTP/application code/public keys, including setup/configuration branches outside Gateway.

The inventory must enumerate declared system Tool exports from the stable `_EXPORTS`-backed manifest, including all five `openclaw_*` compatibility Tools. It must also identify every current `str(exc)`, `repr(exc)`, `exc_info`, response-body, raw-node-payload, and memory read/write path reachable in Capability mode. An unclassified reachable path blocks Task 5/8; do not hide it under a final catch-all sanitizer.

- [ ] **Step 5: Reconfirm one Alembic head**

~~~bash
cd backend
.venv/bin/alembic heads
cd ..
~~~

Expected: exactly one head. It will normally be the merged Plan 01 revision, not the plan-writing baseline `a7b8c9d0e1f2`.

- [ ] **Step 6: Record dependency reality**

~~~bash
backend/.venv/bin/python --version
backend/.venv/bin/python - <<'PY'
from importlib.metadata import version
for name in ("anyio", "pydantic", "sqlalchemy", "jsonschema", "langgraph", "langchain-core"):
    print(name, version(name))
PY
rg -n '^(anyio|jsonschema|langgraph|langchain|langchain-core)' backend/requirements.txt
~~~

Expected at plan-writing time: local environment drift from production Python/LangGraph. Do not “fix” it in this task.

- [ ] **Step 7: Run the existing focused characterization**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_remote_tool.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py -q
~~~

Expected at plan-writing time: `82 passed, 2 subtests passed`. After Plan 01, record the new exact count.

- [ ] **Step 8: Record the OpenClaw public compatibility matrix**

For each enabled fixture type, record:

- catalog visibility;
- exact input/output schema;
- response mode `json_schema|text_field`;
- success response;
- not found/disabled/unavailable/schema drift errors;
- locale;
- audit source/channel/session/tool;
- Tool/Workflow/Agent execution count.

This matrix becomes the assertion source in Task 8. Do not invoke real remote or write targets twice.

Also record whether the current async endpoint remains responsive during each synchronous legacy execution class and whether the request Session crosses threads. These measurements become the failing baseline for the awaited-worker tests; blocking the event loop is not accepted as parity.

- [ ] **Step 9: Commit the characterization fixture and any factual plan corrections**

~~~bash
git add backend/tests/fixtures/openclaw_runtime_error_contract.json
git add docs/superpowers/plans/2026-07-13-shared-capability-runtime.md  # only if Task 0 corrected it
git commit -m "docs(ai): refresh capability runtime baseline"
~~~

---

## Task 1: Add Frozen Capability Contracts, Safe Errors, and JSON Schema Validation

**Files:**

- Create: `backend/app/assistant/capabilities/__init__.py`
- Create: `backend/app/assistant/capabilities/contracts.py`
- Create: `backend/app/assistant/capabilities/errors.py`
- Create: `backend/app/assistant/capabilities/ports.py`
- Create: `backend/app/assistant/capabilities/json_schema.py`
- Reuse: `backend/app/assistant/domain/json_schema.py`
- Test: `backend/tests/test_capability_contracts.py`
- Test: `backend/tests/test_capability_json_schema.py`

**Interfaces:**

- Consumes Plan 01 `FrozenContract`, `JsonValue`, canonical digest/Schema helpers, `ResolvedCapabilityRef`, and the authoritative binding/dependency projections.
- Produces pure contracts, schema normalization/compilation/validation, and safe semantic errors.
- Does not access the database, Tool Registry, Workflow engine, OpenClaw, or Provider code.

- [ ] **Step 1: Write failing contract-invariant tests**

Cover:

- every contract is frozen and rejects extra keys;
- status/error/continuation combinations;
- digest format is lowercase 64-character SHA-256;
- input is object-only JSON;
- NaN/infinity/non-JSON values fail;
- negative nesting depth, timeout, byte count, and duration fail;
- empty call/capability/target/principal/owner IDs fail;
- `unknown` cannot be marked parallel-safe;
- `durable` waiting requires a continuation;
- safe error messages reject control characters and are length bounded;
- event metadata rejects unknown keys and raw input/output/error fields;
- serialization round trips deterministically;
- Plan 01 persisted binding payload projects without field substitution and retains the identical `binding_contract_digest`/`dependency_closure_digest`;
- mutating the source ORM JSON or a materialized Schema copy after construction cannot change the binding or later validation;
- callbacks, Session objects, exceptions, and arbitrary Pydantic models cannot enter frozen values.

Example:

~~~python
def test_waiting_result_requires_portable_continuation() -> None:
    with pytest.raises(ValidationError):
        CapabilityResult(
            status="waiting",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=None,
            terminal_output=False,
            needs_followup=True,
            error=None,
            metrics=_metrics(),
        )
~~~

- [ ] **Step 2: Run and confirm the intended import failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_contracts.py -q
~~~

Expected: import failure because `app.assistant.capabilities` does not exist.

- [ ] **Step 3: Implement the minimum contracts**

Implement the Plan 02-owned contracts in Section 4 and import/re-export the Plan 01-owned binding/completion/dependency contracts plus:

- `CapabilityPolicyDecision`;
- `VerifiedAuthorizationEvidence`;
- `CapabilityRuntimeEvent`;
- ephemeral `CapabilityAdapterRequest` containing the resolved root/closure, validated input, context, and verified decision;
- pure factories for completed/failed/cancelled results.

Do not add a second binding DTO. The projection constructor accepts the final Plan 01 persisted binding domain object/snapshot and verifies the canonical digest before returning `FrozenCapabilityBinding`.

Do not add a generic `dict[str, Any]` escape hatch. Use `JsonValue` and explicit runtime ports.

- [ ] **Step 4: Write failing schema-normalization tests**

Cover:

- object input schema;
- arbitrary JSON output schema;
- properties/required/additionalProperties;
- arrays and nested objects;
- enums, const, anyOf/oneOf/allOf;
- numeric/string bounds;
- local `$defs` reference;
- legacy `nullable: true` normalization;
- remote `$ref` rejection;
- invalid regex and invalid schema;
- no default application;
- dictionary order does not change normalized digest;
- schema semantic change does change digest;
- deterministic error ordering;
- maximum 20 errors;
- JSON Pointer escaping;
- error does not echo the invalid value;
- error does not echo enum members, pattern text, defaults, examples, or Schema descriptions;
- `format` does not unexpectedly reject current OpenClaw date strings.

Add fixed digest vectors for one simple input and output schema.

- [ ] **Step 5: Run and confirm missing implementation**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_json_schema.py -q
~~~

Expected: missing schema compiler/validator symbols.

- [ ] **Step 6: Verify the Plan 01 direct dependency and canonical helper**

Assert `backend/requirements.txt` already contains exactly the accepted `jsonschema>=4.23,<5` range and import the Plan 01 normalization/digest helper. If absent, stop and repair Plan 01; do not hide the prerequisite in the Plan 02 commit.

- [ ] **Step 7: Implement bounded Draft 2020-12 validation**

Import Plan 01's `normalize_binding_schema(...)` and `binding_schema_digest(...)` without wrapping or renaming them. Expose only the Plan 02 compiler/value API:

~~~python
def compile_binding_schema(
    normalized_body: Mapping[str, JsonValue],
    *,
    expected_digest: str,
    require_object_root: bool,
) -> CompiledBindingSchema: ...

def validate_json_value(
    compiled: CompiledBindingSchema,
    value: JsonValue,
    *,
    label: Literal["input", "output"],
) -> None: ...
~~~

`compile_binding_schema` verifies `binding_schema_digest(normalized_body) == expected_digest`, validates the Draft 2020-12 document, and applies only the requested root check. Add a bounded thread-safe LRU keyed by `(expected_digest, require_object_root)` for checked compiled validators. Cache compiled Schema only, never values. Plan 01 already rejects remote reference resolution before validator construction. Parity tests import the Plan 01 helper directly and prove publish-time and runtime normalized bytes/digests are identical; they must fail if Plan 02 introduces a second normalization dialect.

- [ ] **Step 8: Add secret/error-safety tests**

Build invalid values containing fake:

- bearer token;
- cookie;
- API key;
- password;
- remote response body.

Assert no fake secret appears in raised domain errors, `repr` of public errors, events, or logs captured at INFO/WARNING/ERROR. Include an unexpected exception whose message and traceback exception line contain the secret; the shared path must not log with `exc_info`.

- [ ] **Step 9: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_json_schema.py -q
~~~

- [ ] **Step 10: Commit**

~~~bash
git add backend/app/assistant/capabilities/__init__.py \
  backend/app/assistant/capabilities/contracts.py \
  backend/app/assistant/capabilities/errors.py \
  backend/app/assistant/capabilities/ports.py \
  backend/app/assistant/capabilities/json_schema.py \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_json_schema.py
git commit -m "feat(ai): define shared capability contracts"
~~~

---

## Task 2: Implement Exact Frozen-Reference Registry Resolution

**Files:**

- Create: `backend/app/assistant/capabilities/registry.py`
- Create: `backend/app/assistant/capabilities/execution_closure.py`
- Create: `backend/app/assistant/workflow/engine/runtime_dependency_resolver.py`
- Modify: `backend/app/assistant_config/registry.py`
- Modify only if required by final Plan 01 API: `backend/app/assistant/skills/resolution.py`
- Test: `backend/tests/test_capability_registry.py`
- Test: `backend/tests/test_capability_execution_closure.py`
- Reuse: Plan 01 publication/drift tests.

**Interfaces:**

- Consumes one `FrozenCapabilityBinding` directly projected from a Plan 01 immutable binding (or an explicitly frozen OpenClaw compatibility binding) and current runtime availability.
- Produces `ResolvedCapabilitySurface`: the exact owned root version/config payload, current safe availability/display projection, and a preflighted non-activating frozen dependency closure. It deliberately does **not** produce `CapabilityDescriptor` before Task 3 supplies behavior classification.
- Does not authorize, decrypt credentials, execute targets, select locale-sensitive identity, or resolve Provider aliases.

Use the single `ResolvedCapabilitySurface` and executable-target union locked in Section 4.3; Task 2 must not redefine a shorter look-alike DTO. `FrozenClosureRuntimeResolver` is ephemeral and contains an immutable locator-to-verified-target index, exact root/binding/dependency digests, and hidden repository/activation handles (`repr=False`, non-serializable). It verifies every non-secret ref/config/revision/digest up front, exposes only exact lookup methods plus post-policy adapter-local activation, and cannot resolve a name/ID absent from the closure. Task 3 combines this surface with one `CapabilityBehavior`, computes the descriptor, and returns the final `ResolvedCapabilityTarget`; there is no placeholder behavior/digest in Task 2.

- [ ] **Step 1: Write failing system Tool resolution tests**

Cover:

- exact `system-tool:<name>` identity;
- equality against the Plan 01 project-owned Tool contract/schema set and its frozen digest (the current Registry definition is drift evidence, not a runtime Schema source);
- build revision equality;
- schema digest equality;
- disabled DB record shadows same-named system Tool;
- missing exported Tool;
- unlisted arbitrary module attribute is not resolvable;
- build revision drift;
- schema drift;
- no DB/credential values in the safe surface/binding projection;
- locale affects display only;
- repeated resolution is deterministic;
- mutating the Plan 01 binding source after projection does not mutate the surface or later descriptor;
- a Schema body/digest supplied by current `ToolRegistry` is drift evidence only and never replaces the frozen binding Schema.

Monkeypatch `APP_BUILD_REVISION` and the registry schema to prove fail-closed behavior.

- [ ] **Step 2: Add stable Tool Registry APIs**

Expose:

~~~python
@classmethod
def list_runtime_system_tool_names(cls) -> tuple[str, ...]: ...

@classmethod
def get_runtime_system_tool_definition(
    cls,
    tool_name: str,
    *,
    locale: str | None = None,
) -> SystemToolFullDefinition | None: ...
~~~

The name list is sorted, includes explicit internal runtime Tools, and is the sole exhaustiveness source for classification tests. Preserve existing list/resolve behavior and disabled DB shadowing.

The stable list comes from one public runtime export-name manifest backed by `app.assistant.tools._EXPORTS`, not `__all__` and not `dir(module)`. It includes all five current `openclaw_*` Tools. `resolve_system_tool` enforces the same allowlist, so an arbitrary module attribute cannot become an executable Capability. Characterize legitimate callers first, then tighten only the undeclared path.

- [ ] **Step 3: Write failing remote Tool resolution tests**

Cover:

- exact target ID and `config_revision`;
- secret-free normalized config digest;
- endpoint/method/body/query/header-name/auth-shape/timeout/payload-wrapper drift;
- API-key ciphertext never appears;
- enabled/disabled availability;
- kind/name mismatch;
- stale revision;
- malformed endpoint/config marked unavailable without decryption.

- [ ] **Step 4: Write failing Workflow resolution tests**

Create an aggregate where Draft and Published snapshots differ. Cover:

- exact target ID and `target_version_id` ownership;
- published version exists and belongs to aggregate;
- published snapshot digest matches frozen resolution;
- aggregate may point to a newer publish without changing the frozen target;
- disabled aggregate is unavailable but the exact version remains identifiable;
- deleted/mismatched version is version drift;
- input/output Schemas derive from the exact published snapshot and equal the frozen Plan 01 publication assertion;
- a newly derived current Workflow contract is drift evidence only and never replaces the frozen binding Schema;
- `AssistantWorkflow.graph_snapshot` is monkeypatched to raise and is never read.

- [ ] **Step 5: Write failing Agent resolution and exact model-closure tests**

Cover the same ownership/version/digest cases for `AssistantAgentProfileVersion`. Mutate aggregate `system_prompt`, `tools`, and KB fields after publication and prove surface/executable data still comes from the frozen version snapshot.

Also cover:

- Agent binding input/output Schema comes only from the persisted binding contract, never Agent Profile or OpenClaw catalog defaults;
- `model_source=default` resolves to the exact Plan 01 model dependency, not the current assistant component binding;
- explicit model ID, model name, credential/base URL config digest, and credential-slot revision drift fail before decryption/model client construction;
- Workflow default/custom node models are all present in the closure;
- changing `AiComponentBinding`, `AiModel`, or `AiCredential` after publication cannot redirect execution;
- no encrypted/decrypted key appears in binding, surface, closure diagnostics, or digest payload.

- [ ] **Step 6: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py -q
~~~

Expected: missing `CapabilityRegistry`, exact closure resolver, and stable Tool Registry APIs.

- [ ] **Step 7: Implement per-type resolvers**

Use one dispatcher:

~~~python
class CapabilityRegistry:
    def resolve_surface(self, binding: FrozenCapabilityBinding) -> ResolvedCapabilitySurface: ...
~~~

Implementation rules:

- Recompute the current secret-free reference using Plan 01’s drift helper.
- Verify the frozen binding contract digest, both Schema digests, dependency-closure digest, and every Capability/model dependency ref before target lookup.
- Compare every frozen identity/version/revision/digest.
- Load exact Workflow/Agent version rows with ownership predicates.
- Parse exact version snapshots through public domain parsing helpers, not private OpenClaw methods.
- Do not follow aggregate `published_version_id` after the frozen `target_version_id` is known.
- Do not follow `AiComponentBinding` after an exact model dependency is frozen. Match the exact model/credential/config/revision and only later decrypt through the verified slot.
- Treat disabled state as availability, not version replacement.
- Return semantic domain errors with safe identifiers. Task 3 adds final `resolve(...)`/`describe(...)` methods only after injecting the real classifier; tests must not use an `unknown` placeholder to fabricate a descriptor.

- [ ] **Step 8: Implement and test the no-fallback execution closure**

Build the generic optional `WorkflowEngineExecutionScope`/resolver Protocol in the Workflow engine layer and implement it in `capabilities/execution_closure.py`.

Cover:

- root Tool map, inline Agent Tools, KB Tool, child Workflow V1, child Tools, Agent model, and Workflow node models;
- lookup by exact source locator plus expected identity/version/revision;
- missing/extra/drifted/duplicate locator fails preflight;
- a runtime request for an undeclared name fails without querying `ToolRegistry`, latest published state, or current component binding;
- credential activation is impossible before a verified policy decision and occurs once afterward;
- Legacy `execution_scope=None` retains current lookup behavior;
- Capability scope never falls back even if a same-named current Tool exists.

- [ ] **Step 9: Add query-count and no-decryption tests**

Use SQLAlchemy query instrumentation or mocks to assert:

- bounded queries per resolution;
- no N+1 closure/preflight read for a single resolved surface;
- `decrypt_api_key` is never called;
- no Tool runner/Workflow engine/Agent engine is built.

- [ ] **Step 10: Run focused plus Plan 01 drift tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py \
  backend/tests/test_agent_skill_publish.py \
  backend/tests/test_resolved_run_manifest.py -q
~~~

Use the actual final Plan 01 filenames if they differ.

- [ ] **Step 11: Commit**

~~~bash
git add backend/app/assistant/capabilities/registry.py \
  backend/app/assistant/capabilities/execution_closure.py \
  backend/app/assistant/workflow/engine/runtime_dependency_resolver.py \
  backend/app/assistant_config/registry.py \
  backend/app/assistant/skills/resolution.py \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py
git commit -m "feat(ai): resolve frozen capability targets"
~~~

Do not include `resolution.py` in the commit if no change was required.

---

## Task 3: Add Conservative Recursive Classification

**Files:**

- Create: `backend/app/assistant/capabilities/classification.py`
- Test: `backend/tests/test_capability_classification.py`
- Modify if descriptor assembly is integrated here: `backend/app/assistant/capabilities/registry.py`

**Interfaces:**

- Consumes a `ResolvedCapabilitySurface` and its already verified Plan 01 Capability/model dependency closure.
- Produces side-effect, parallel-safe, interrupt-mode, timeout, and completion metadata.
- Does not execute nodes, infer from descriptions, authorize a call, or inspect Draft.

- [ ] **Step 1: Write the exhaustive system Tool test**

~~~python
def test_every_runtime_system_tool_has_reviewed_classification() -> None:
    assert set(SYSTEM_TOOL_CLASSIFICATIONS) == set(
        ToolRegistry.list_runtime_system_tool_names()
    )
~~~

Add one test that injects a new Tool name and expects failure. Add direct assertions for every initial value in Section 6.2.

Pin `CLASSIFICATION_CONTRACT_REVISION`, the canonical declarative ruleset bytes, and a golden `classification_ruleset_digest`. Mutating any Tool/node/interrupt/timeout rule under the old revision must fail. A deliberate revision change must produce a new behavior/descriptor/decision digest and make an older frozen Run/Manifest descriptor fail closed rather than silently reclassify.

- [ ] **Step 2: Inspect actual Tool implementations**

Review the code paths for all runtime Tool names. Record why each is read/write and why each parallel-safe value is true or false. If uncertain, use the more conservative class. Update Section 6.2 before implementation if facts differ.

- [ ] **Step 3: Write remote Tool defaults**

Assert GET, POST, missing method, templated URL, and authenticated remote Tools all produce:

~~~text
side_effect=write_external
parallel_safe=false
interrupt_mode=none
~~~

- [ ] **Step 4: Write Workflow node matrix tests**

Build exact published snapshots covering every current `NodeType`:

- pure control;
- LLM/parameter extraction;
- knowledge retrieval;
- read/write Tool;
- code executor under multiple live Settings profiles, all of which remain `unknown` in Plan 02 v1 because none is part of the frozen closure contract;
- HTTP literal safe GET, mutating method, unsupported/coerced method, disabled TLS verification, and dynamic URL;
- bounded/unbounded loop and iteration;
- human-in-loop;
- pinned/unpinned Workflow call;
- nested Agent;
- output.

Assert maximum-risk aggregation, parallel-safety, and interrupt mode.

Also generate an impact list of every enabled OpenClaw Workflow/Agent closure containing `code_executor`. Each stays legacy in 02A or is disabled before 02B; no “approved live profile” test may upgrade it to `compute` in this plan.

Generate the same impact list for templated/ambiguous `http_request`, unbounded loop/iteration, dynamic/unpinned target, and unknown node configurations. Conservative classification may intentionally make them shared-unavailable, but the plan must quantify affected enabled items and assign `legacy-only|disabled|contract-remediated` before claiming cutover compatibility.

- [ ] **Step 5: Write recursion and drift tests**

Cover:

- pinned Workflow A -> B;
- A -> B -> C;
- direct and indirect cycles;
- missing child version;
- child aggregate republished after parent publication;
- dynamic/latest child binding;
- nested child with human node;
- nested child with unknown node.
- depth, dependency-count, and classified-node limit boundaries plus one-over-limit failures.

The classifier must use the frozen child version/dependency ref and a bounded visited stack. It never repairs a missing closure entry by querying current state.

- [ ] **Step 6: Write Agent classification tests**

Cover:

- no Tools;
- read-only Tools;
- read plus write Tool;
- remote Tool;
- KB enabled;
- missing Tool;
- disabled Tool;
- mutable aggregate differs from published snapshot;
- nested/restart semantics marked unknown;
- Agent always non-parallel.

- [ ] **Step 7: Confirm intended failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_classification.py -q
~~~

Expected: missing classifier/map.

- [ ] **Step 8: Implement pure classification**

Expose:

~~~python
class CapabilityClassifier:
    def classify(
        self,
        surface: ResolvedCapabilitySurface,
    ) -> CapabilityBehavior: ...
~~~

Use explicit per-node handlers and risk-rank constants. Never use substring matching on node labels, Tool descriptions, endpoint paths, or prompt text.

- [ ] **Step 9: Integrate classification into descriptor assembly**

Registry resolution constructs the executable identity/Schemas and verified dependency closure. Classifier supplies behavior metadata. Compute `descriptor_digest` only after all three are present, and include `binding_contract_digest` plus `dependency_closure_digest`.

Finalize the production Registry surface only here:

~~~python
class CapabilityRegistry:
    def resolve_surface(self, binding: FrozenCapabilityBinding) -> ResolvedCapabilitySurface: ...
    def resolve(self, binding: FrozenCapabilityBinding) -> ResolvedCapabilityTarget: ...
    def describe(self, binding: FrozenCapabilityBinding) -> CapabilityDescriptor: ...
~~~

`resolve()` calls `resolve_surface()`, invokes the injected real classifier exactly once, assembles/digests the descriptor, and returns the locked final target. `describe()` returns that descriptor. There is no default/placeholder classifier and no descriptor cache that omits the classification ruleset digest.

If classification is `unknown`:

- descriptor remains describable;
- availability is `unsupported` for new runtime entrypoints;
- Policy always denies dispatch;
- OpenClaw bridge may not blindly override unknown. It must either preserve an already characterized exact legacy item under an explicit compatibility classification or leave that item on legacy during 02A and resolve the classification before 02B.

- [ ] **Step 10: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_registry.py -q
~~~

- [ ] **Step 11: Commit**

~~~bash
git add backend/app/assistant/capabilities/classification.py \
  backend/app/assistant/capabilities/registry.py \
  backend/tests/test_capability_classification.py
git commit -m "feat(ai): classify capability side effects"
~~~

---

## Task 4: Implement the Tool Capability Adapter and Secret-Safe Remote Boundary

**Files:**

- Create: `backend/app/assistant/capabilities/adapters/__init__.py`
- Create: `backend/app/assistant/capabilities/adapters/base.py`
- Create: `backend/app/assistant/capabilities/adapters/tool.py`
- Modify: `backend/app/assistant_config/remote_tool.py`
- Test: `backend/tests/test_capability_tool_adapter.py`
- Modify regression tests: `backend/tests/test_remote_tool.py`

**Interfaces:**

- Consumes an already resolved, validated, and authorized Tool target.
- Produces one normalized `CapabilityResult`.
- Does not resolve a Tool name again, authorize, choose a Provider alias, or catch-and-retry through another target.

Define the adapter port:

~~~python
class CapabilityAdapter(Protocol):
    capability_type: Literal["tool", "workflow", "agent"]

    def execute(
        self,
        request: CapabilityAdapterRequest,
        *,
        ports: CapabilityRuntimePorts,
    ) -> CapabilityResult: ...
~~~

- [ ] **Step 1: Write failing system Tool adapter tests**

Cover:

- validated args reach exactly the resolved Tool;
- current `coerce_tool_args` behavior remains compatible;
- adapter receives the exact root Tool object/config from `ResolvedCapabilityTarget.executable` and never calls `ToolRegistry.resolve(name)`;
- a fresh DB context is created by `wrap_tool_with_db` and closed after success/failure;
- dict/list/scalar/string results preserve JSON type;
- a complete JSON string is parsed only when the authoritative output schema requires structured JSON;
- plain text is not guessed into an object;
- output schema mismatch becomes `invalid_output`;
- disabled/drifted target never invokes;
- cancellation before invocation never invokes;
- cancellation after a pure/read invocation returns cancelled only at the honest cooperative boundary;
- Tool exception becomes safe `execution_failed` without `str(exc)` leakage;
- Tool exception containing a fake secret produces no `str/repr/exc_info` log leakage;
- start/end events are emitted once.

- [ ] **Step 2: Write failing remote Tool security tests**

Use a local fake HTTP server and fake encrypted credential. Cover:

- no decryption during Registry resolution, schema validation, or policy denial;
- decryption occurs once immediately before request construction;
- config/credential-slot revision is rechecked after policy and before decryption; a concurrent rotation fails without network I/O;
- initial URL and every redirect are SSRF-validated;
- native timeout is passed to urllib;
- response success is preserved;
- native top-level remote Tool uses its binding-owned frozen output Schema; missing Schema could not have published and never falls back to an OpenClaw catalog item;
- a nested Legacy Workflow/Agent remote Tool retains the frozen compatibility string contract;
- a structured contract parses only a complete JSON document, while partial/fenced/brace-scanned content fails safely;
- fake Authorization/cookie/API key never appears in result metadata/log/error;
- a fake secret-bearing 4xx/5xx response body is not returned by the shared adapter;
- connection, timeout, HTTP, malformed output, and redirect failures map to safe codes;
- no automatic retry for POST/PUT/PATCH/DELETE;
- adapter does not spawn a timeout thread.

- [ ] **Step 3: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_remote_tool.py -q
~~~

Expected: missing Tool adapter and current remote error body appears in at least the new safety assertion.

- [ ] **Step 4: Add structured internal remote errors**

Refactor `RemoteTool` narrowly so its request layer raises an internal exception containing only:

- category;
- HTTP status when available;
- timeout/connection boolean;
- safe endpoint host or target identity only if already approved by the project’s logging policy.

Do not include body, headers, credential, full query string, or arbitrary exception text. Existing callers that require a string receive a generic safe compatibility message. Preserve all request rendering and SSRF behavior.

This safety refactor is below the 02A mode branch, so both Legacy and shared OpenClaw calls stop exposing/logging remote bodies and `URLError.reason`. The parity matrix records this as an intentional security correction rather than attempting to preserve secret-bearing text. Capture INFO/WARNING/ERROR logs for both modes with fake body/header/exception secrets.

- [ ] **Step 5: Implement Tool execution**

Rules:

1. Assert request descriptor/type matches the executable target.
2. Check cancellation.
3. Use only `ResolvedCapabilityTarget.executable`; a missing/mismatched root target is fatal and has no name fallback.
4. For code-native Tool, call the already resolved object through current DB wrapper.
5. For remote Tool, recheck and instantiate only from the exact row/config/credential-slot revision already verified by Registry.
6. Normalize the returned JSON value without `default=str`.
7. Let Gateway validate the authoritative frozen binding output Schema; the adapter may pre-normalize only type-safe encodings.
8. Return metrics and completion metadata from the descriptor.

No OpenClaw `text_field` behavior belongs here.

- [ ] **Step 6: Add non-serializable result tests**

Return datetime, bytes, set, SQLAlchemy row, and arbitrary object from fake Tools. Assert a safe `invalid_output`; do not stringify silently.

- [ ] **Step 7: Run focused regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_remote_tool.py \
  backend/tests/test_capability_json_schema.py \
  backend/tests/test_capability_registry.py -q
~~~

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/capabilities/adapters \
  backend/app/assistant_config/remote_tool.py \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_remote_tool.py
git commit -m "feat(ai): adapt tools to capability runtime"
~~~

---

## Task 5: Implement the Exact Published Workflow Capability Adapter

**Files:**

- Create: `backend/app/assistant/capabilities/adapters/workflow.py`
- Create: `backend/app/assistant/capabilities/safe_execution.py`
- Test: `backend/tests/test_capability_workflow_adapter.py`
- Test: `backend/tests/test_capability_workflow_engine_scope.py`
- Modify regression tests: `backend/tests/test_workflow_execution_context.py`
- Modify regression tests: `backend/tests/test_workflow_call_node.py`
- Modify: `backend/app/assistant/workflow/engine/engine.py`
- Modify: `backend/app/assistant/workflow/engine/workflow_dag_assembler.py`
- Modify: `backend/app/assistant/workflow/engine/container_runtime.py`
- Modify: `backend/app/assistant/workflow/engine/node_builders/workflow_call_node.py`
- Modify where exact scope/safe diagnostics require it: every runtime-reachable builder named in Section 8.1, including `tool_node.py`, `agent_tool_node.py`, `knowledge_node.py`, `llm_node.py`, `param_extractor_node.py`, `dag_agent_node.py`, `iteration_node.py`, `loop_node.py`, `code_executor_node.py`, `http_request_node.py`, and `human_in_loop_node.py`.
- Modify where exact pre-build resolution or safe Capability events require it: `backend/app/assistant/workflow/engine/workflow_node_llm_resolver.py`, `snapshot_input_resolvers.py`, `agent_subgraph.py`, `execution_services.py`, `runtime_helpers.py`, `snapshots.py`, and `stream_runtime.py`.
- Read/characterize and modify only if required by the locked session/cancellation rule: `backend/app/assistant/workflow/human_approval_runtime.py`.
- Modify only if a generic public conversion helper is required: `backend/app/assistant_config/service.py`

**Interfaces:**

- Consumes the exact owned `AssistantWorkflowVersion` payload plus the fully verified Capability/model dependency closure resolved in Task 2.
- Produces structured output and forwards safe Workflow child events.
- Does not read Draft, follow the aggregate’s current published pointer, block a future Main Agent indefinitely, or persist a durable continuation.

- [ ] **Step 1: Write failing exact-version execution tests**

Create:

- Workflow aggregate;
- published V1;
- different Draft V2;
- later published V3;
- frozen reference to V1.

Assert the adapter executes V1, never reads Draft or V3, and includes V1 ID/digest in safe events.

Monkeypatch all of these to raise if called:

- `AssistantWorkflow.graph_snapshot`;
- a “get latest published” helper;
- OpenClaw private `_get_workflow_published_input`.
- `ToolRegistry.resolve` and `resolve_openai_compat_config` while Capability scope is active.

- [ ] **Step 2: Write input/output contract tests**

Cover:

- structured start input is passed via `runtime_context["structured_input"]`;
- text-mode Workflow receives the locked canonical text envelope defined by Plan 01;
- exact frozen binding input/output Schemas are enforced and re-derived published contract is equality evidence only;
- empty, malformed, and non-JSON output fails safely;
- current output normalization behavior is characterized;
- no OpenClaw-specific field mapping occurs.

Schema ownership gate:

- Tool Schema must come from the frozen Plan 01 binding/project-owned Tool contract; the current Registry definition is equality/drift evidence only.
- Workflow Schema must be the Plan 01 binding snapshot copied from/asserted against the exact published start/output contract.
- Agent Schema must be an explicit Plan 01 binding contract; Agent Profile is never its source.

If merged Plan 01 cannot supply an immutable Agent schema contract, stop and amend Plan 01 before continuing. Do not use an OpenClaw catalog item as the universal Agent schema source.

- [ ] **Step 3: Write execution-context propagation tests**

Assert the adapter propagates:

- call/run/conversation IDs;
- locale;
- request source/channel/session/tool;
- exact Workflow and version IDs;
- channel type `capability_runtime`, with an OpenClaw compatibility value only at its bridge if required;
- cancellation checker;
- child event sink.
- non-null `WorkflowEngineExecutionScope` with exact binding/dependency digests and safe diagnostics.
- ambient L1/L2/workflow-call memory disabled even when `conversation_id` is a valid UUID; `AssistantMemoryService` read/write methods are never called.
- global Workflow graph cache bypassed; two sequential calls to the same Workflow version with different frozen dependency/model revisions cannot reuse the first call's Tool object, model client, credential, or resolver.
- explicit scope propagation through root `engine.py` -> `WorkflowNodeBuilderDeps` -> every builder -> iteration/loop `container_runtime.py`; the scope object is absent from serializable Workflow state/snapshots/checkpoints.

Assert unrelated metadata/callbacks are not copied into persisted/frozen data.

Also assert all event callbacks receive a safe projection: no node input/output, Tool args/results, prompts, Schema bodies, exception text, HTTP/provider body, or credentials. Inject a fake secret into an inner Tool/model exception and inspect emitted events and captured logs.

- [ ] **Step 4: Write cancellation and human-loop tests**

Cover:

- cancelled before engine creation;
- cancelled between Workflow nodes;
- cancelled while current HumanLoopRuntime polls;
- Workflow with human node is classified `legacy_blocking`;
- non-OpenClaw entrypoint receives `unsupported_interrupt` before blocking;
- explicit OpenClaw compatibility execution retains current blocking behavior in 02A and emits a diagnostic;
- no `waiting` result is fabricated without a portable continuation.

- [ ] **Step 5: Write nested Workflow drift tests**

Use a published parent with pinned child Workflow version. Assert:

- exact child executes;
- child republish does not replace it;
- missing/mutated child fails before parent execution;
- child/root Tools come from the frozen closure and a same-named current Registry Tool is never consulted;
- child default/custom models come from exact model dependency refs and current component/model changes cannot redirect them;
- unpinned/dynamic child is unavailable;
- cycle/unknown classification never dispatches.
- iteration -> nested `workflow_call` -> child Tool/default model never consults Registry/current component bindings and increments capability nesting only for the child call;
- loop/iteration body rebuild preserves the same safe diagnostics, memory rule, cache rule, and closure resolver;
- depth 4 is accepted, depth 5 is denied before child execution, while loop iteration count does not itself consume nesting depth.

- [ ] **Step 6: Confirm intended failure**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_workflow_execution_context.py -q
~~~

Expected: missing adapter/public exact-version conversion path.

- [ ] **Step 7: Extract a generic exact-version builder**

Move or recreate only the generic part of OpenClaw’s `_build_workflow_skill_definition`:

~~~python
def build_workflow_runtime_definition(
    *,
    workflow_id: UUID,
    version_id: UUID,
    name: str,
    description: str,
    published_input: WorkflowInput,
) -> SkillDefinition: ...
~~~

It must set `workflow_version_id` from the frozen version, not aggregate `published_version_id`. Keep OpenClaw naming/prompt/output translation outside this helper.

- [ ] **Step 8: Implement Workflow execution**

Construct the current `LangGraphEngine` only after Gateway authorization, with `execution_scope` set to the Task 2 exact resolver and safe diagnostics enabled. Activate/decrypt only the exact default and node model credential slots at that point, rechecking non-secret config/revision first. Pass exact runtime definition/context and collect output deterministically.

The additive engine behavior is mandatory:

- `execution_scope=None` is the Legacy default and retains current `ToolRegistry`/component lookup and public behavior;
- non-null Capability scope makes `_build_tools`, Workflow Tool/Agent nodes, and nested `workflow_call` require exact closure targets;
- non-null Capability scope with `allow_ambient_memory=false` skips root runtime-memory overrides/L1/L2 reads and all Workflow-call memory reads/upserts; Plan 02 always passes false. `execution_scope=None` has a positive regression proving current reads/writes still occur;
- non-null Capability scope bypasses `_get_or_compile_graph` because the current compiled graph captures request dependencies and its key omits binding/dependency revisions; Legacy scope keeps the current cache unchanged;
- nested Workflow resolution uses the frozen child version input supplied by the resolver, never current `published_version_id`;
- missing closure entry fails before that node can execute and never falls back by name/ID;
- safe mode emits/logs fixed diagnostics rather than arbitrary exception/result text.
- assembler and container paths pass scope explicitly; no builder may recover it from serializable state or silently default to Legacy lookup when its parent scope is non-null.

Forward existing Workflow node/tool/human events as `capability.child_event` with:

- parent call ID;
- child event type;
- node ID/type where safe;
- status and duration;
- no raw node config/input/output.

- [ ] **Step 9: Run focused regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_workflow_call_node.py \
  backend/tests/test_openclaw_integration.py -q
~~~

Use actual existing nested Workflow test filenames discovered in Task 0.

- [ ] **Step 10: Commit**

~~~bash
git add backend/app/assistant/capabilities/adapters/workflow.py \
  backend/app/assistant/capabilities/safe_execution.py \
  backend/app/assistant/workflow/engine/engine.py \
  backend/app/assistant/workflow/engine/workflow_dag_assembler.py \
  backend/app/assistant/workflow/engine/container_runtime.py \
  backend/app/assistant/workflow/engine/node_builders/workflow_call_node.py \
  backend/app/assistant/workflow/engine/node_builders/tool_node.py \
  backend/app/assistant/workflow/engine/node_builders/agent_tool_node.py \
  backend/app/assistant/workflow/engine/node_builders/knowledge_node.py \
  backend/app/assistant/workflow/engine/node_builders/llm_node.py \
  backend/app/assistant/workflow/engine/node_builders/param_extractor_node.py \
  backend/app/assistant/workflow/engine/node_builders/dag_agent_node.py \
  backend/app/assistant/workflow/engine/node_builders/iteration_node.py \
  backend/app/assistant/workflow/engine/node_builders/loop_node.py \
  backend/app/assistant/workflow/engine/node_builders/code_executor_node.py \
  backend/app/assistant/workflow/engine/node_builders/http_request_node.py \
  backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py \
  backend/app/assistant/workflow/engine/workflow_node_llm_resolver.py \
  backend/app/assistant/workflow/engine/snapshot_input_resolvers.py \
  backend/app/assistant/workflow/engine/agent_subgraph.py \
  backend/app/assistant/workflow/engine/execution_services.py \
  backend/app/assistant/workflow/human_approval_runtime.py \
  backend/app/assistant/workflow/engine/runtime_helpers.py \
  backend/app/assistant/workflow/engine/snapshots.py \
  backend/app/assistant/workflow/engine/stream_runtime.py \
  backend/app/assistant_config/service.py \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_workflow_call_node.py
git commit -m "feat(ai): adapt published workflows to capability runtime"
~~~

Do not include `assistant_config/service.py` or `human_approval_runtime.py` if no change was required.

---

## Task 6: Implement the Exact Published Agent Capability Adapter

**Files:**

- Create: `backend/app/assistant/capabilities/adapters/agent.py`
- Test: `backend/tests/test_capability_agent_adapter.py`
- Regress: `backend/tests/test_agent_test_run_stream.py`
- Regress: `backend/tests/test_assistant_openai_compat.py`
- Do not modify: `backend/app/assistant/workflow/engine/agent_execution_core.py`

**Interfaces:**

- Consumes one exact published Agent version, its explicit binding-level callable Schema, and its frozen model/Tool/KB dependency closure.
- Produces a normalized Capability result using the current Agent engine.
- Does not gain dynamic Skills, fix multi-Tool behavior, recurse into Main Agent, or switch existing Agent callers.

- [ ] **Step 1: Lock the canonical Agent call contract**

Before tests, confirm merged Plan 01 freezes Agent input/output schema.

Locked contract: the immutable Plan 01 Capability binding owns the callable input/output surface, while the Agent Profile version owns executable prompt/Tool/model/KB declarations. A native Agent binding must explicitly declare its Schema or an explicit versioned canonical contract ID that Plan 01 materializes into exact bodies/digests. Agent Profile has no callable-Schema defaults and Plan 02 invents none.

If current compatibility requires a canonical envelope, it must be documented and frozen, for example:

~~~json
{
  "input": {
    "type": "object",
    "properties": {
      "input": {}
    },
    "required": ["input"],
    "additionalProperties": false
  },
  "output": {
    "type": "object",
    "properties": {
      "text": {"type": "string"}
    },
    "required": ["text"],
    "additionalProperties": false
  }
}
~~~

Do not silently choose this example if Plan 01 specifies another contract. If no lossless frozen binding contract exists, amend Plan 01 and its digests first.

- [ ] **Step 2: Write failing exact-version tests**

Cover:

- published V1 executes after aggregate Draft/published pointer changes;
- mutable aggregate `system_prompt`/`tools`/`kb_config` are ignored;
- version row must belong to Agent aggregate;
- model source/model ID declaration comes from the exact snapshot and resolves only through the frozen model dependency ref;
- missing custom model is unavailable before execution;
- Tool list is exact and drift-verified;
- changing the current assistant component binding, model name, base URL, credential slot, or credential revision cannot redirect execution;
- `ToolRegistry.resolve` and `resolve_openai_compat_config` are monkeypatched to raise and are never called after the exact closure is built;
- KB Tool inclusion follows exact snapshot;
- no OpenClaw prompt text appears in generic adapter.

- [ ] **Step 3: Write behavior-preservation tests**

Characterize current Agent behavior rather than improving it:

- Tools are bound once inside the legacy Agent engine;
- current max iterations and stopping behavior remain;
- current engine may execute only the first Tool Call;
- streaming callbacks behave as before;
- current Tool failures remain normalized by the adapter;
- fake secret-bearing Tool/model exceptions are absent from Capability errors/events and every captured log record.

These assertions protect existing callers. Plan 03 builds a separate loop and does not mutate this engine in Plan 02.

- [ ] **Step 4: Write output and contract tests**

Cover:

- canonical input serialization;
- text output;
- structured output if the frozen Agent contract requires it;
- strict output validation;
- Markdown-fenced JSON compatibility only if characterized and explicitly normalized;
- no arbitrary “find first braces” parsing;
- safe empty/model/tool failure.

OpenClaw’s external arbitrary JSON schema instruction remains in the OpenClaw bridge. The generic adapter must not receive an untrusted schema as prompt text unless that schema is part of the frozen Agent capability contract.

- [ ] **Step 5: Write nesting/cancellation tests**

Assert:

- nonzero depth is carried;
- depth beyond the Plan 02 hard ceiling fails;
- Agent cannot select/restart Main Agent;
- nested Agent target is unavailable;
- cancellation is checked before exact model credential activation, each current Agent round boundary, and after execution;
- no detached model call continues after a reported timeout.

- [ ] **Step 6: Confirm intended failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_capability_agent_adapter.py -q
~~~

- [ ] **Step 7: Implement exact-version Agent builder and adapter**

Build `SkillDefinition` from the exact published snapshot and explicit binding contract. After authorization, activate the exact frozen model dependency and recheck model/credential config revisions before decryption. Build `bound_tools`/`tool_runners` solely from the exact execution closure and invoke current `run_agent_execution(...)` directly; do not call the current engine's name-based `_build_tools(...)`. Keep all OpenClaw localization and response-shaping in its bridge.

The adapter should return current Agent failures as safe Capability errors. It must not expose Provider exception strings.

- [ ] **Step 8: Run focused regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_agent_adapter.py \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_openclaw_integration.py -q
~~~

- [ ] **Step 9: Commit**

~~~bash
git add backend/app/assistant/capabilities/adapters/agent.py \
  backend/tests/test_capability_agent_adapter.py
git commit -m "feat(ai): adapt published agents to capability runtime"
~~~

Do not stage `agent_execution_core.py`.

---

## Task 7: Implement Deny-by-Default Policy, Gateway, and Runtime Composition

**Files:**

- Create: `backend/app/assistant/capabilities/policy.py`
- Create: `backend/app/assistant/capabilities/gateway.py`
- Create: `backend/app/assistant/capabilities/runtime.py`
- Modify: `backend/app/assistant/capabilities/adapters/__init__.py`
- Test: `backend/tests/test_capability_policy.py`
- Test: `backend/tests/test_capability_gateway.py`

**Interfaces:**

- Consumes frozen request/evidence plus injected Registry, classifier, verifier, adapter registry, cancellation, and event sink.
- Produces one policy decision and one result.
- Is the only public dispatch path for the shared runtime.

- [ ] **Step 1: Write failing Policy matrix tests**

Test every combination needed to prove deny by default:

- unauthenticated Principal;
- unknown issuer;
- issuer/entrypoint mismatch;
- missing verifier;
- forged verifier evidence;
- call ID mismatch, sibling replay, and second use of already consumed evidence;
- owner mismatch;
- capability key mismatch;
- resolution digest mismatch;
- binding-contract or dependency-closure digest mismatch;
- side effect omitted;
- `unknown` explicitly listed;
- unavailable/disabled/drifted target;
- `main_agent` without production verifier;
- exact OpenClaw grant;
- exact test grant.
- nesting depth `0..4` accepted subject to other policy, negative/`5` denied before adapter/child construction.
- independent OpenClaw ceiling: read descriptor/read ceiling allows, write descriptor/read ceiling denies, unknown denies even under a broad custom-source ceiling;
- changing classifier output cannot mutate evidence/grant fields, and changing the ceiling revision invalidates the old `grant_source_digest`.

Assert denials use safe reason codes and never include target input/config.

- [ ] **Step 2: Write failing Gateway order tests**

Use spies for every stage. Prove exact order:

~~~text
cancel -> verify binding -> resolve exact root/closure -> input schema
-> policy -> cancel -> atomically consume one dispatch permit -> adapter
-> adapter-local exact revision recheck/credential-model activation
-> output schema -> cancel -> terminal event/result
~~~

For each failure injection, assert all later stages are untouched. Adapter-local activation spies prove Registry, classifier, verifier, Policy, Gateway, and the OpenClaw bridge never decrypt credentials or construct model clients. Concurrent/replayed decisions prove the permit admits exactly one adapter entry.

- [ ] **Step 3: Write dispatch integrity tests**

Cover:

- descriptor type selects exactly one adapter;
- missing/duplicate adapter fails at runtime construction;
- adapter cannot replace descriptor/ref;
- adapter result metadata cannot claim a different capability;
- no fallback adapter;
- no Registry/latest/component-default fallback from any adapter;
- one execution emits one start and one terminal event;
- terminal event is emitted even on safe failure;
- event sink failure is contained/diagnosed and cannot cause duplicate dispatch;
- unexpected exception text/traceback is never formatted into a log record;
- metrics use monotonic time.

- [ ] **Step 4: Write output-validation tests**

Return invalid structured output from an otherwise successful fake adapter. Assert:

- result becomes `invalid_output`;
- raw invalid value is not logged;
- completion metadata is not marked successful;
- no retry;
- adapter invoked once.

- [ ] **Step 5: Write cancellation-race tests**

Cancel:

- before resolution;
- after resolution;
- after authorization;
- during cooperative adapter work;
- after adapter return but before final result.

The test must distinguish “adapter not started” from “adapter completed before cancellation”. Never claim a side-effecting adapter was terminated when it was not.

- [ ] **Step 6: Confirm intended failures**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py -q
~~~

- [ ] **Step 7: Implement trusted verifier registration**

Runtime construction accepts an explicit mapping:

~~~python
EvidenceVerifierKey = tuple[EvidenceIssuer, CapabilityEntrypoint]

class CapabilityPolicyEngine:
    def __init__(
        self,
        verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier],
    ) -> None: ...
~~~

Production construction in Plan 02 registers only OpenClaw when called by its bridge. Test construction may register test fixtures. Do not place a permissive global verifier in `__init__.py`.

Verifier instances that carry request authentication proof are request-scoped and single-use. The mapping key alone is not authority; the matching verifier must validate/consume the exact `call_id`, binding, owner, and evidence digest.

- [ ] **Step 8: Implement Gateway**

Gateway catches only known domain/adapter exceptions and maps unexpected exceptions to generic `execution_failed` after fixed-field safe diagnostics. It does not catch `BaseException` and does not log or return `str(exc)`, `repr(exc)`, `exc.args`, or `exc_info` for untrusted exceptions.

Validate output after adapter return even if the adapter claims success. Recompute byte metrics using canonical JSON bytes.

- [ ] **Step 9: Add runtime factory**

Expose a request-scoped constructor:

~~~python
def build_capability_runtime(
    *,
    db: Session,
    evidence_verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier],
) -> CapabilityGateway: ...
~~~

It wires exactly one adapter for each type and shares Registry/schema/classifier instances only where thread/session safety is explicit.

The OpenClaw async service does not call this factory with its request-side Session and then move the Gateway across threads. `runtime_worker.py` invokes the factory inside the bounded worker using a worker-owned Session; the Session and Gateway are closed before the worker returns a serializable/safe result to the event loop.

- [ ] **Step 10: Run all shared-runtime tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_json_schema.py \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_capability_agent_adapter.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py -q
~~~

- [ ] **Step 11: Run import-boundary checks**

~~~bash
! rg -n 'openclaw_integration|provider_loop|SkillRouter|Supervisor' \
  backend/app/assistant/capabilities
~~~

Expected: no matches.

- [ ] **Step 12: Commit**

~~~bash
git add backend/app/assistant/capabilities \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py
git commit -m "feat(ai): enforce capability gateway policy"
~~~

---

## Task 8: Characterize and Build the OpenClaw Compatibility Bridge

**Files:**

- Create: `backend/app/openclaw_integration/capability_adapter.py`
- Create: `backend/app/openclaw_integration/runtime_worker.py`
- Modify: `backend/app/openclaw_integration/router.py`
- Modify: `backend/app/openclaw_integration/service.py`
- Modify: `backend/app/config.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Test: `backend/tests/test_openclaw_shared_capability_runtime.py`
- Test: `backend/tests/test_openclaw_capability_worker.py`
- Fixture: `backend/tests/fixtures/openclaw_runtime_error_contract.json`
- Modify: `backend/tests/test_openclaw_integration.py`

**Interfaces:**

- The async worker entry consumes bounded header/payload snapshots and a request-frozen runtime mode; inside its own Session it authenticates and constructs `OpenClawRuntimeAuditContext`. The bridge then consumes that proof/context, the exact exposed catalog item, and raw external payload.
- Produces the unchanged `OpenClawCapabilityExecuteResponse`.
- Owns OpenClaw external schema translation, error mapping, and grant verification.
- Does not add OpenClaw dependencies to the shared runtime.

### Locked bridge sequence

~~~text
async route: snapshot runtime mode + bounded payload/header strings
-> await bounded worker with no request-side Session
worker: authenticate request and check integration (existing semantics)
-> ensure integration/system items (existing)
-> load exact exposed catalog item
-> freeze catalog binding and external schema digests
-> resolve exact current target into FrozenCapabilityBinding and freeze its Capability/model dependency closure
-> validate external input
-> apply OpenClaw-specific input adapter
-> validate the transformed internal input against FrozenCapabilityBinding
-> issue and verify exact OpenClaw evidence
-> await the bounded worker, which opens its own Session and executes shared Gateway
-> validate Gateway canonical internal output
-> apply OpenClaw-specific result adapter
-> validate external output
-> return unchanged public response
~~~

For Workflow/Agent, request-start resolution freezes the exact current published version ID. A republish after that point affects future requests only. For remote/code Tools it freezes the current config/build revision. There is no second “resolve latest” inside the adapter.

- [ ] **Step 1: Expand public characterization tests before refactoring**

Parameterize current legacy behavior for:

- canonical system Tool with input adapter;
- `text_field` Tool;
- JSON-object Tool;
- remote Tool fixture;
- structured Workflow;
- published Agent;
- disabled/retired/missing item;
- unavailable source;
- Workflow/Agent schema drift;
- input/output validation;
- zh/en locale;
- authentication and integration disabled;
- unbound/missing default assistant model and model/credential revision drift;
- a disabled DB Tool row shadowing a same-named code-native system Tool;
- catalog item revision versus Workflow/Agent republish at request-start;
- audit context/log fields.

Assert exact HTTP status, application error code, and public JSON keys. Use deterministic fake Tool/engine results.

- [ ] **Step 2: Add no-double-execution characterization**

Each fake target increments a counter. Assert one OpenClaw request invokes it at most once on success and every failure. Add a side-effect marker fixture to make accidental legacy fallback visible.

- [ ] **Step 3: Confirm legacy characterization still passes**

~~~bash
OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy \
backend/.venv/bin/python -m pytest \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_openclaw_shared_capability_runtime.py -q
~~~

The new file may initially contain legacy-only characterization and fail only for missing shared bridge symbols.

- [ ] **Step 4: Add the awaited bounded worker boundary**

Implement the locked Section 4.3 bridge using one dedicated `anyio.CapacityLimiter(8)` and `anyio.to_thread.run_sync(..., abandon_on_cancel=False)`. Snapshot bounded immutable request data plus an opaque secret-bearing auth carrier (`repr=False`, never frozen/serialized/logged) before crossing. The worker opens/closes its own SQLAlchemy Session, builds a request-scoped Gateway there, and returns only a safe serializable result/error projection.

Because application code imports AnyIO directly, add the compatible direct requirement `anyio>=4,<5` if Task 0 confirms it is still only transitive. Record the resolved clean-image version; do not rely on the local venv alone.

Lock the process-local carrier/wrapper rather than passing `Request`, service, or Session:

~~~python
@dataclass(frozen=True)
class OpenClawCapabilityWorkerRequest:
    request_id: str
    selected_mode: Literal["legacy", "shared"]
    capability_key: str
    preferred_locale: str | None
    payload_canonical_json: bytes = field(repr=False, compare=False)
    authorization_header: str = field(repr=False, compare=False)
    source_header: str | None = field(default=None, repr=False, compare=False)
    channel_header: str | None = field(default=None, repr=False, compare=False)
    session_header: str | None = field(default=None, repr=False, compare=False)
    tool_header: str | None = field(default=None, repr=False, compare=False)


async def execute_openclaw_capability_in_worker(
    request: OpenClawCapabilityWorkerRequest,
    *,
    session_factory: Callable[[], Session],
    cancellation: CancellationPort,
) -> OpenClawCapabilityExecuteResponse: ...
~~~

The async constructor bounds header lengths and canonical payload bytes before scheduling; the worker parses a fresh JSON object and never mutates caller data. `session_factory` and cancellation are injected in tests. The carrier is not a domain/FrozenContract, has no serializer, and its secret-bearing fields are excluded from `repr`/comparison.

Tests prove:

- an event-loop heartbeat progresses during blocking Tool/Workflow/Agent fixtures;
- no more than eight workers enter concurrently and unrelated default-threadpool work is not consumed by this limiter;
- the FastAPI request Session is never touched in a worker and each worker-owned Session is closed on success/failure;
- the execute route no longer declares `db: Session = Depends(get_db)` and does no synchronous authentication/catalog DB work on the event loop; header snapshots are bounded and bearer material is absent from every `repr`/log/error;
- cancellation does not detach the thread or report a false terminal state while a side effect continues;
- both `legacy` and `shared` modes use this same worker boundary, so event-loop blocking is not preserved as compatibility behavior.

Extract one header-value authentication helper in `OpenClawIntegrationService`; the existing `authorize_runtime_request(Request)` delegates to it for synchronous catalog routes, while the execute worker calls it with the bounded header snapshot. Authentication codes/messages and secret comparison stay identical, and there is no second auth implementation.

- [ ] **Step 5: Add the temporary selector**

In Settings:

~~~python
openclaw_capability_runtime_mode: Literal["legacy", "shared"] = Field(
    default="legacy",
    alias="OPENCLAW_CAPABILITY_RUNTIME_MODE",
)
~~~

Document/propagate it in both env examples and Compose. Do not accept aliases such as `new`, `auto`, booleans, or an empty value.

Snapshot it once:

~~~python
selected_mode = self.runtime_selector.snapshot_mode()
~~~

Tests may inject a selector to change the global value after snapshot and prove the active request does not switch.

- [ ] **Step 6: Move OpenClaw-only contract adapters**

Move the current request/response transforms for:

- search entries;
- get entry;
- create relation;
- knowledge graph query;
- any retained system compatibility shape.

They belong in `openclaw_integration/capability_adapter.py`. Both legacy and shared branches use the same adapter during 02A so parity is not obscured by duplicate transformation logic.

- [ ] **Step 7: Implement request-frozen catalog binding**

Define an OpenClaw-owned immutable snapshot:

~~~python
class OpenClawFrozenCapabilityCall(FrozenContract):
    call_id: str
    selected_mode: Literal["legacy", "shared"]
    catalog_item_id: UUID
    capability_key: str
    tool_name: str
    source_type: Literal["tool", "workflow", "agent"]
    source_binding_digest: str
    external_input_schema: dict[str, JsonValue]
    external_output_schema: dict[str, JsonValue]
    external_input_schema_digest: str
    external_output_schema_digest: str
    tool_response_mode: Literal["json_schema", "text_field"]
    binding: FrozenCapabilityBinding
    grant_ceiling_revision: str
    grant_ceiling_digest: str
    catalog_item_revision_digest: str
    catalog_evidence_digest: str
~~~

Construction rules:

- normalize canonical legacy source Tool aliases before resolving;
- freeze exact catalog schemas and item fields;
- resolve exact target plus complete Tool/Workflow/Agent/model dependencies through Plan 01 reference helpers;
- resolve a default model to the actual request-start model/credential slot and freeze its non-secret config/revision; never leave `component=assistant` as the executable reference;
- include binding-level schemas in the resolution/binding digest;
- do not persist this transient snapshot;
- do not include encrypted credentials;
- reject any schema/reference that cannot be reconstructed losslessly.
- select the grant ceiling independently from classification: exact system-item row or custom source-type row. Missing/mismatched ceiling fails before Gateway.

- [ ] **Step 8: Implement OpenClaw evidence verifier**

The verifier is instantiated for one authenticated request/call and:

- accepts only `issuer=openclaw_bridge` and `entrypoint=openclaw`;
- closes over the non-serializable successful authentication proof, expected `call_id`, selected mode, and frozen call; it binds Principal without storing the secret;
- checks item ID/key, enabled, not retired, exact source binding, and evidence digest;
- obtains `allowed_side_effects` only from the frozen independent ceiling row and includes its digest as `grant_source_digest`; it never copies classifier output into the grant;
- verifies `descriptor.behavior.side_effect` is inside that ceiling and the exact request-frozen catalog item/source binding is still enabled/exposed. The generated release inventory is a deployment/02B gate, not a stale runtime authorization database;
- denies `unknown`;
- returns a verified decision for one call only and rejects replay/second use.

The future Main Agent cannot instantiate or register this verifier.

- [ ] **Step 9: Implement external/internal schema boundaries**

There are always two distinct contracts:

- **internal binding Schema**: Plan 01/native or request-frozen compatibility target contract consumed and validated by Gateway;
- **external catalog Schema**: OpenClaw request/response contract validated only by the bridge before/after deterministic transforms.

Validate raw payload against the frozen external catalog input schema, apply the source-specific adapter, then let Gateway validate the transformed internal binding input. After Gateway validates the internal output, transform it and validate the external output. Neither Schema may substitute for the other or share a digest field by coincidence.

For `text_field`, the target's raw/canonical return is first normalized and validated against the internal output Schema. Only then does the bridge wrap it in the configured text field and validate the external object Schema. Wrapping before internal validation or treating the external object Schema as the Tool binding output is forbidden. Add fixed round-trip vectors for system Tool, remote Tool, Workflow, Agent, `json_schema`, and `text_field`, including arbitrary JSON internal outputs where the frozen contract permits them.

For Agent bindings, the generic Agent adapter may render the frozen binding output Schema into its execution prompt because that Schema is immutable authorization evidence. A native Skill Agent receives its Plan 01 binding Schema; an OpenClaw Agent receives the request-frozen compatibility binding Schema. The generic adapter must not query the catalog or accept a mutable Schema absent from `FrozenCapabilityBinding`.

After Gateway success:

- transform canonical result to current OpenClaw response shape;
- apply `text_field` behavior only here;
- validate against frozen external output schema;
- return `OpenClawCapabilityExecuteResponse`.

- [ ] **Step 10: Implement safe error translation**

Load the Task 0 characterization fixture and add table-driven tests for every Section 10 mapping plus every pre-Gateway runtime-reachable setup/configuration path. Authentication/configuration errors retain their current paths. Shared errors use one translation function. Do not manufacture cancellation/timeout codes absent from the fixture.

- [ ] **Step 11: Run bridge tests in both modes**

~~~bash
for mode in legacy shared; do
  OPENCLAW_CAPABILITY_RUNTIME_MODE="$mode" \
  backend/.venv/bin/python -m pytest \
    backend/tests/test_openclaw_integration.py \
    backend/tests/test_openclaw_shared_capability_runtime.py \
    backend/tests/test_openclaw_capability_worker.py \
    backend/tests/test_capability_gateway.py -q || exit 1
done
~~~

- [ ] **Step 12: Run import boundary**

~~~bash
! rg -n 'openclaw_integration' backend/app/assistant/capabilities
~~~

- [ ] **Step 13: Commit**

~~~bash
git add backend/app/openclaw_integration/capability_adapter.py \
  backend/app/openclaw_integration/runtime_worker.py \
  backend/app/openclaw_integration/router.py \
  backend/app/openclaw_integration/service.py \
  backend/app/config.py backend/requirements.txt backend/.env.example \
  deploy/.env.example deploy/docker-compose.yml \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_openclaw_shared_capability_runtime.py \
  backend/tests/test_openclaw_capability_worker.py \
  backend/tests/fixtures/openclaw_runtime_error_contract.json
git commit -m "feat(ai): bridge openclaw to capability runtime"
~~~

---

## Task 9: Complete Plan 02A Parity, Failure, and Rollback Verification

**Files:**

- Modify as required: `backend/tests/test_openclaw_shared_capability_runtime.py`
- Modify as required: shared Capability tests.
- Modify only for safe diagnostics: `backend/app/openclaw_integration/service.py` and bridge.
- Create/update: `docs/superpowers/evidence/plan-02a-readiness.md` from verified commands/inventory; do not pre-check evidence.
- Record the same release evidence in PR/release notes.

**Interfaces:**

- Produces a mergeable switch-capable release with default `legacy`.
- Does not remove legacy branches or claim production observation.

- [ ] **Step 1: Build a deterministic parity matrix**

For each Tool/Workflow/Agent fixture, run legacy and shared in isolated database transactions and compare:

- public response schema and values;
- application error code/category;
- target invocation count;
- audit context;
- locale;
- external input/output validation;
- catalog availability.

Never run both modes against the same non-idempotent real target.

- [ ] **Step 2: Test mode freezing**

Cases:

- starts legacy, global config changes to shared before dispatch;
- starts shared, global config changes to legacy before dispatch;
- shared fails before adapter;
- shared fails during adapter;
- shared succeeds then output validation fails;
- request cancellation.

Assert selected implementation runs once and no branch fallback occurs.

- [ ] **Step 3: Test availability/version races**

Simulate after request snapshot:

- catalog item disabled;
- Tool config revision changes;
- application build revision changes;
- Workflow/Agent republished;
- exact version deleted/ownership changed;
- assistant component default model changes;
- exact `AiModel`/`AiCredential` config or revision changes;
- credential rotated.

The exact race semantics must be documented:

- catalog/evidence re-verification before admission detects prior item mutation; once its short transaction succeeds and ends, a later disable affects future calls and does not cancel/replay the admitted call;
- target version/config drift fails closed;
- a new publish alone does not replace a frozen exact version;
- a component-binding change does not replace a frozen model and model/credential drift fails before client construction;
- credential rotation follows Plan 01 Tool revision policy;
- no legacy retry.

- [ ] **Step 4: Test all current OpenClaw system items**

Use `list_openclaw_system_item_definitions` and require every active definition to appear in the shared parity table. A new system item must fail the test until classified and adapted.

Require the same exhaustive set in `OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS`. The 02A release inventory also lists every enabled custom item, resolved target/binding/dependency digests, reviewed side effect, and whether it is safe to exercise in staging/production.

For every row, assert classifier output and grant ceiling are generated/read independently. Add negative rows for an effect above ceiling, missing ceiling, missing inventory record, unknown classification, and unapproved `legacy_blocking`. Record every `code_executor` closure as legacy-only/disabled; none may pass shared preflight in v1.

- [ ] **Step 5: Run focused 02A suite**

~~~bash
for mode in legacy shared; do
  OPENCLAW_CAPABILITY_RUNTIME_MODE="$mode" \
  backend/.venv/bin/python -m pytest \
    backend/tests/test_openclaw_integration.py \
    backend/tests/test_openclaw_shared_capability_runtime.py \
    backend/tests/test_openclaw_capability_worker.py \
    backend/tests/test_remote_tool.py \
    backend/tests/test_workflow_execution_context.py \
    backend/tests/test_capability_contracts.py \
    backend/tests/test_capability_json_schema.py \
    backend/tests/test_capability_registry.py \
    backend/tests/test_capability_execution_closure.py \
    backend/tests/test_capability_classification.py \
    backend/tests/test_capability_tool_adapter.py \
    backend/tests/test_capability_workflow_adapter.py \
    backend/tests/test_capability_workflow_engine_scope.py \
    backend/tests/test_capability_agent_adapter.py \
    backend/tests/test_capability_policy.py \
    backend/tests/test_capability_gateway.py -q || exit 1
done
~~~

- [ ] **Step 6: Run unchanged Main Assistant and Legacy Workflow regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py \
  backend/tests/test_workflow_call_node.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_workflow_memory_mode_step4.py \
  backend/tests/test_workflow_code_executor_runtime.py \
  backend/tests/test_workflow_http_request_runtime.py \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_assistant_service_l1_summary.py \
  backend/tests/test_assistant_service_l2_memory.py -q
~~~

- [ ] **Step 7: Run the full backend suite**

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
git diff --check
~~~

- [ ] **Step 8: Verify default and deploy propagation**

Assert:

- Settings default is `legacy`;
- invalid values fail startup/config validation;
- Compose passes the selected variable;
- examples explain that selection is process-cached, requires restart/rolling deployment, and applies only to requests accepted by an instance after it starts;
- terminal diagnostics include build/instance revision and selected mode so mixed rolling traffic is attributable;
- no secret is logged.

- [ ] **Step 9: Create the 02A operational handoff**

Record:

- image/git revision;
- dependency versions;
- exact test counts;
- staging commands;
- enabled OpenClaw capability inventory;
- expected shared diagnostics;
- exact log/metric queries and accepted category/latency thresholds;
- rollback value `legacy`;
- restart/rolling rollback procedure and expected mixed-instance interval;
- explicit prohibition on dual-running write calls.
- the complete runtime-reachability matrix, public-error fixture revision, classification ruleset digest, and grant-ceiling revisions;
- every enabled item's external/internal Schema digests, interrupt disposition, and whether it is shared-ready, legacy-only, or disabled;
- an explicit `PLAN_02A_READY=yes|no` conclusion with unresolved blockers. `yes` requires Tasks 0–9, not production observation.

Write this evidence to `docs/superpowers/evidence/plan-02a-readiness.md` using actual command output/digests/counts. The implementation plan itself is not evidence.

- [ ] **Step 10: Commit 02A verification**

~~~bash
git status --short
git diff --name-only
# Add each reviewed Plan 02 path explicitly; do not use `git add backend`.
git add docs/superpowers/evidence/plan-02a-readiness.md
git commit -m "test(ai): verify openclaw capability cutover"
~~~

At this point Plan 02A may merge only when the readiness record says `PLAN_02A_READY=yes`. Plan 03 may begin against that approved shared Gateway contract without waiting for production observation/02B cleanup, but it cannot import OpenClaw, depend on the temporary selector, or claim OpenClaw cutover complete. Full Plan 02 remains incomplete.

---

## Task 10: Observe Shared Mode, Then Remove Legacy OpenClaw Dispatch (Plan 02B)

**Files:**

- Modify: `backend/app/openclaw_integration/service.py`
- Modify: `backend/app/openclaw_integration/capability_adapter.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `backend/tests/test_openclaw_integration.py`
- Modify: `backend/tests/test_openclaw_shared_capability_runtime.py`
- Delete no longer used private generic helpers only after `rg` proves no callers.

**Interfaces:**

- Consumes real 02A observation evidence.
- Produces one shared-only OpenClaw execution path.
- Does not alter catalog tables or Main Assistant runtime.

- [ ] **Step 1: Pause for the operational observation gate**

Do not simulate this checkbox with unit tests.

Required evidence:

- deployed 02A image revision;
- `shared` enabled for a predeclared bounded duration/request-count window; thresholds and abort conditions were written before the switch;
- at least one successful safe representative of every enabled source type;
- all enabled system items covered by staging or deterministic integration evidence;
- no unexplained increase in 401/403/404/409/422/5xx categories;
- no duplicate target executions;
- latency within the accepted bound;
- audit logs contain expected IDs/status and no secrets;
- no unresolved `unknown` classifications;
- no enabled `code_executor` closure and no unapproved `legacy_blocking` item remains on the shared-only path;
- every enabled custom catalog item is either safely exercised, covered by deterministic staging evidence, or explicitly disabled before 02B;
- each terminal diagnostic is attributable to build/instance/mode, and mixed rolling-deployment traffic is excluded or segmented correctly.

If a capability cannot be safely exercised in production, use a staging clone with disposable data and document the gap.

- [ ] **Step 2: Exercise future-request rollback**

During the observation procedure, verify a controlled `shared -> legacy` restart/rolling deployment affects only requests accepted by the restarted instance. Do not interrupt/replay an active side-effecting call, and do not describe the process-cached environment value as an in-process hot switch.

Restore `shared` and repeat one safe smoke request.

- [ ] **Step 3: Approve cleanup explicitly**

Attach the observation record to the cleanup PR. If the gate is not approved, stop with Plan 02A deployed and leave legacy code/flag intact.

- [ ] **Step 4: Write failing shared-only tests**

Change tests to assert:

- no runtime selector exists;
- OpenClaw always builds the shared bridge;
- no legacy method can be monkeypatched/called;
- public behavior remains characterized.

Run before deletion and confirm they fail because the selector/legacy branches still exist.

- [ ] **Step 5: Delete legacy dispatch branches**

Remove:

- `_execute_tool_capability`;
- `_execute_workflow_capability`;
- `_execute_agent_capability`;
- generic private JSON Schema helpers replaced by shared validation;
- duplicate generic Workflow/Agent builders moved to shared adapters;
- mode branch and fallback scaffolding.

Retain OpenClaw-specific:

- auth;
- catalog/admin behavior;
- external schema/request/response adapters;
- error mapping;
- audit log;
- compatibility bridge.

- [ ] **Step 6: Remove temporary configuration**

Delete `OPENCLAW_CAPABILITY_RUNTIME_MODE` from:

- Settings;
- both env examples;
- Compose;
- tests and documentation that imply it remains available.

Do not leave a shared-only flag with a fake legacy value.

- [ ] **Step 7: Prove dead-code removal**

~~~bash
! rg -n \
  '_execute_(tool|workflow|agent)_capability|OPENCLAW_CAPABILITY_RUNTIME_MODE|openclaw_capability_runtime_mode' \
  backend/app backend/tests backend/.env.example deploy/.env.example deploy/docker-compose.yml
~~~

Also search old helper names identified in Task 0. Any remaining match must have a documented non-runtime purpose.

- [ ] **Step 8: Run shared-only focused suite**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_openclaw_shared_capability_runtime.py \
  backend/tests/test_openclaw_capability_worker.py \
  backend/tests/test_remote_tool.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_json_schema.py \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_capability_agent_adapter.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py -q
~~~

- [ ] **Step 9: Commit cleanup**

~~~bash
git add backend/app/openclaw_integration \
  backend/app/config.py backend/.env.example \
  deploy/.env.example deploy/docker-compose.yml \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_openclaw_shared_capability_runtime.py
git commit -m "refactor(ai): remove legacy openclaw capability dispatch"
~~~

Rollback after this commit is an image rollback to verified 02A, not a configuration change.

---

## Task 11: Plan 02B Final Verification, Clean Runtime Gate, and Handoff

**Files:**

- Modify only if factual implementation details changed: this plan.
- Modify tests only to correct verified gaps.
- No feature expansion.

- [ ] **Step 1: Run all Plan 02 tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_capability_json_schema.py \
  backend/tests/test_capability_registry.py \
  backend/tests/test_capability_execution_closure.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_tool_adapter.py \
  backend/tests/test_capability_workflow_adapter.py \
  backend/tests/test_capability_workflow_engine_scope.py \
  backend/tests/test_capability_agent_adapter.py \
  backend/tests/test_capability_policy.py \
  backend/tests/test_capability_gateway.py \
  backend/tests/test_openclaw_shared_capability_runtime.py \
  backend/tests/test_openclaw_capability_worker.py \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_remote_tool.py \
  backend/tests/test_workflow_execution_context.py -q
~~~

- [ ] **Step 2: Run main-assistant non-cutover regressions**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py \
  backend/tests/test_workflow_call_node.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_workflow_memory_mode_step4.py \
  backend/tests/test_workflow_code_executor_runtime.py \
  backend/tests/test_workflow_http_request_runtime.py \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_assistant_service_l1_summary.py \
  backend/tests/test_assistant_service_l2_memory.py -q
~~~

- [ ] **Step 3: Run the complete backend suite**

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
~~~

- [ ] **Step 4: Verify production Python/dependency build**

Build the existing Python 3.11 runtime image, which installs `backend/requirements.txt` from scratch:

~~~bash
docker build --target runtime -f backend/Dockerfile backend
~~~

If Docker is unavailable, create a clean Python 3.11 venv, install exact requirements, and run all Plan 02 tests. The drifted local venv alone is not final evidence.

- [ ] **Step 5: Run PostgreSQL schema smoke**

Against a disposable PostgreSQL 15 database:

~~~bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic current
.venv/bin/alembic heads
cd ..
~~~

Expected: upgrade succeeds and exactly one head/current revision. Plan 02 creates no migration.

- [ ] **Step 6: Run static boundary searches**

~~~bash
! rg -n 'openclaw_integration' backend/app/assistant/capabilities
! rg -n 'provider_loop|skill\\.inject|SkillRouter|Supervisor' \
  backend/app/assistant/capabilities
! rg -n 'graph_snapshot' backend/app/assistant/capabilities
! rg -n 'resolve_openai_compat_config\(|ToolRegistry\(.*\)\.resolve|published_version_id' \
  backend/app/assistant/capabilities/adapters
! rg -n 'api_key_encrypted|authorization|cookie' \
  backend/app/assistant/capabilities
~~~

Review any false positives rather than blindly suppressing them.

The adapter search is a tripwire, not proof by itself: exact model credential activation may call a dedicated Plan 01 exact-ref helper whose name differs. Review every match and prove it requires the frozen ref/config revision rather than a component default or latest target.

- [ ] **Step 7: Verify no hidden fallback or thread timeout**

~~~bash
rg -n 'legacy|fallback|ThreadPoolExecutor|threading\\.Thread|future\\.result|to_thread' \
  backend/app/assistant/capabilities \
  backend/app/openclaw_integration/capability_adapter.py \
  backend/app/openclaw_integration/runtime_worker.py
~~~

Expected after 02B: no legacy execution/fallback. The only allowed async/sync bridge is the awaited `anyio.to_thread.run_sync` call in `runtime_worker.py` with the dedicated limiter, worker-owned Session, and `abandon_on_cancel=False`; no thread/future-based timeout, detached execution, or default-threadpool call is allowed. Review matches rather than requiring an empty search.

- [ ] **Step 8: Verify repository hygiene**

~~~bash
git diff --check
git status --short
~~~

Review the diff file by file. Confirm no frontend, Main Agent, Provider Loop, or migration file entered the change.

- [ ] **Step 9: Record final evidence**

Record:

- final image/git revision;
- exact Python and dependency versions;
- focused/full test counts;
- sole Alembic head;
- observation window and approval;
- public OpenClaw parity matrix;
- known compatibility-only timeout/interrupt limitations;
- rollback image.

- [ ] **Step 10: Commit factual plan/test corrections**

~~~bash
git status --short
git diff --name-only
# Add each reviewed factual Plan 02 correction explicitly; do not stage the backend tree wholesale.
git commit -m "test(ai): verify shared capability runtime"
~~~

Do not create an empty commit.

---

## Plan 02A Exit Criteria

Plan 02A is ready when all are true:

- the Plan 01 readiness checklist passes and Plan 02 imports its frozen DTOs/constants/helpers directly;
- Tasks 0–9 pass, including exhaustive runtime-node coverage, worker/event-loop tests, dual-schema vectors, independent-ceiling tests, the public-error fixture, and full backend regression;
- the shared Gateway is switch-capable while the deployment default remains `legacy`, with no same-request fallback or double execution;
- every enabled OpenClaw item is inventoried with external/internal Schema digests, actual classification, independent ceiling digest, interrupt disposition, and `shared-ready|legacy-only|disabled` state;
- `docs/superpowers/evidence/plan-02a-readiness.md` is generated from actual evidence, reviewed, and says `PLAN_02A_READY=yes`;
- legacy branches and the rollback selector remain intact, and no production-observation/cleanup claim is made.

An approved Plan 02A contract is sufficient for Plan 03 to consume the Gateway while operational cutover continues. Plan 03 remains independent of OpenClaw bridge/config/cleanup.

## Full Plan 02 / Plan 02B Exit Criteria

Full Plan 02 is complete only when all are true:

- Plan 01 frozen reference and binding-surface contracts are consumed directly.
- `FrozenCapabilityBinding` is a lossless projection of the one Plan 01 persisted binding payload/closure representation; publish/runtime canonical digests are identical and no Schema is derived from mutable state.
- Tool, Workflow, and Agent execute through one Gateway.
- Exact Workflow/Agent owned versions are used; Draft-first `graph_snapshot` is never read.
- Every nested Tool/Workflow/Agent and default/custom model is preflighted from the frozen dependency closure; Capability execution has no name/latest/component-default fallback.
- Agent callable Schema comes from the explicit binding contract, never `AssistantAgentProfile` or a native use of OpenClaw catalog Schema.
- Tool build/config/schema drift fails closed before credential decryption or execution.
- Every execution validates input, authorizes exact evidence, validates output, and returns a normalized safe result.
- Missing classification is `unknown` and denied.
- Classification revision/digest is frozen into behavior/descriptor decisions, while authorization ceilings are independently versioned/digested; evidence never derives its grant from classifier output.
- Read-only still requires exact authenticated authorization.
- The future `main_agent` entrypoint has no production grant and cannot call a Capability yet.
- Remote errors/logs/results do not expose response bodies or credentials.
- Unexpected Tool/Workflow/Agent/model exceptions do not expose arbitrary exception text or traceback through inner engine logs/events.
- Cancellation/timeout claims match what the current runtime can actually enforce.
- OpenClaw public routes, catalog, auth, locale, response contract, and supported behavior remain compatible.
- OpenClaw async endpoints use the one bounded awaited worker with worker-owned Sessions and remain event-loop responsive; no detached side-effect execution is reported cancelled.
- OpenClaw external Schema and internal binding Schema remain distinct and are validated on their respective sides of each adapter, including `text_field` round trips.
- Every runtime-reachable OpenClaw public error retains its characterized HTTP/application-code contract; no cancellation/timeout code was invented.
- No enabled `code_executor` closure, unresolved `unknown`, or unapproved `legacy_blocking` item remains at shared-only cutover.
- OpenClaw calls the shared bridge only; legacy dispatch and temporary selector are removed after observed cutover.
- Main assistant Router/Supervisor and legacy Agent engine behavior remain unchanged.
- No database/frontend change was introduced.
- Tests pass in a clean Python 3.11 environment installed from declared requirements.
- Exactly one Alembic head remains.

If only Plan 02A is merged/deployed, report: “shared runtime implemented and switch-capable; cleanup observation pending.” Do not report full completion.

---

## Handoff to Plan 03

Plan 03 may consume the following only after the reviewed Plan 02A readiness record says `PLAN_02A_READY=yes`; it does not wait for Plan 02B observation/cleanup:

- `FrozenCapabilityBinding` (including binding/dependency/model digests) and `CapabilityDescriptor`;
- `CapabilityGateway`;
- exact per-call authorization evidence;
- stable error/retry disposition;
- cancellation/event ports;
- side-effect, `parallel_safe`, interrupt mode, and completion metadata;
- normalized `CapabilityResult`.

Plan 03 must:

- call the Gateway for every Provider Tool Call;
- create independent request/session runtime contexts for eligible parallel work;
- preserve the frozen binding/Manifest revision that exposed each call;
- preserve the exact binding and dependency/model closure digests that were described to the Provider, and fail if current runtime cannot supply those exact revisions;
- freeze the classification ruleset, behavior, and descriptor digests used when a Provider alias/call surface was exposed; a changed classification contract requires an explicit new Manifest/call-surface revision or fail-closed reconciliation;
- never reimplement Tool/Workflow/Agent execution;
- never import OpenClaw;
- leave current Main Assistant and legacy Agent engine untouched.
- never depend on the temporary OpenClaw runtime selector, compatibility grant ceilings, worker bridge, catalog Schema, or Plan 02B deletion state.
