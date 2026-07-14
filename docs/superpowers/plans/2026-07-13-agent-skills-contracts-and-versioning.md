# MindAtlas Agent Skills Contracts and Immutable Versioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. If the execution environment supports isolated workers, `superpowers:subagent-driven-development` may be used, but the task order and review gates in this document remain mandatory. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the portable Agent Skills contract layer, append-only Skill and Main Agent version models, deterministic package import/export, frozen published Capability references, and a shadow Legacy Adapter without changing the current assistant runtime.

**Architecture:** Build a parallel v2 domain under `app.assistant.skills` and `app.assistant.domain`. The current `AssistantSkill -> one Workflow/Agent` tables, routes, system catalog, Router, Supervisor, and runtime remain authoritative throughout this plan. New Skill packages are database-backed, versioned immutable snapshots. A published Capability binding owns a lossless callable contract snapshot and an exact transitive execution-dependency closure; later runtimes must not reconstruct either from mutable target/catalog state. Legacy Skills are mirrored into disabled shadow packages, while `general_chat` seeds a disabled Main Agent Profile instead of becoming a new Skill. Later plans consume these contracts; this plan does not introduce the Main Agent loop or execute new packages.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 15, PyYAML 6, jsonschema 4, pytest.

**Approved design:** `docs/superpowers/specs/2026-07-13-mindatlas-universal-skill-agent-loop-design.md`

**External contract:** [Agent Skills specification](https://agentskills.io/specification)

## Plan Position and Hard Boundary

This is Plan 01 of 10. It produces contracts and persisted versions for later plans.

~~~text
Plan 01 (this document)
  Agent Skills package + immutable versions + legacy shadow data
        ↓
Plan 02
  shared Capability Runtime and minimum deny-by-default policy
        ↓
Plan 03
  Provider Agent Loop and provider aliases
        ↓
Plan 04+
  Main Agent, dynamic Skill activation, durable execution, admin, migration
~~~

The following are explicitly **not** implemented here:

- No `skill.inject`, `skill.search`, `skill.read_resource`, or Prompt Builder.
- No dynamic tool rebinding or Provider Tool Loop changes.
- No Capability Gateway execution.
- No new Run/Checkpoint tables and no persistence of `ResolvedRunManifestRevision` on `AssistantChatRun`.
- No L2 memory migration.
- No HITL, Worker Lease, CapabilityCall Ledger, or runtime side-effect idempotency.
- No frontend Skill editor.
- No removal or relaxation of `ck_assistant_skill_single_target_binding`.
- No switch away from the current `SkillRouter -> Supervisor` runtime.

Module vocabulary is fixed for all dependent plans:

| Term | Meaning / module owner |
|---|---|
| legacy Skill | current `app.assistant_config` `AssistantSkill`, still used by `SkillRouter`/`Supervisor` |
| Skill Package catalog | portable/versioned v2 data and services under `app.assistant.skills`; “skill catalog” in later plans refers to a projection of these published rows, not a second `skill_catalog` persistence module |
| shared domain contract | provider-neutral frozen types/digests under `app.assistant.domain` |
| Capability Runtime | Plan 02 execution/gateway layer; it consumes `assistant.skills` rows but does not own package persistence |
| Main Agent / Provider Loop | Plans 03–04 orchestration; it does not replace package or Capability contracts |

---

## Verified Baseline at Plan-Writing Time

- Git branch: `main`.
- Git commit: `c25d03f`.
- Alembic head: `a7b8c9d0e1f2`.
- Migration revision `b8c9d0e1f2a3` is already occupied by `add_assistant_chat_run_tables.py`; it is forbidden for this plan. The implementation must let Alembic generate a new unique revision ID.
- The official Agent Skills specification was rechecked on 2026-07-13 for directory layout, frontmatter fields, naming, compatibility, `allowed-tools`, extra files, progressive disclosure, and local references.
- `backend/requirements.txt` pins `langgraph==0.3.34`.
- The local `backend/.venv` currently contains LangGraph `1.0.5`; this mismatch is irrelevant to the contract-only work here and must not be used as Agent Loop compatibility evidence.
- Focused assistant configuration baseline:

~~~text
79 passed, 1 warning, 12 subtests passed
~~~

Command used:

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py \
  backend/tests/test_assistant_skill_converters.py \
  backend/tests/test_system_skill_workflow_refs.py \
  backend/tests/test_system_agent_baseline_restore.py -q
~~~

At execution time, re-run every baseline check. Do not assume these identifiers are still current.

---

## Global Constraints

- Preserve all unrelated dirty worktree files. At plan-writing time `docs/superpowers/` is untracked.
- Use a project-native branch such as `feature/agent-skills-contracts`; do not implement this plan directly on `main`.
- If `openspec/AGENTS.md` exists at execution time, read it before editing. It does not exist at plan-writing time even though `CLAUDE.md` references it.
- Re-run `alembic heads` immediately before creating the migration. Use `alembic revision -m "add agent skill contract tables"` to generate a unique revision from the one real head; never hand-pick an ID, reuse a provisional filename, or create a fork.
- Add new v2 tables alongside `assistant_skill`. Do not rename, drop, or repurpose legacy columns in this plan.
- Existing `/api/assistant-config/skills` execution/routing and response contracts remain compatible. One intentional administrative compatibility change is required: Workflow/Agent version trim, restore, and delete operations must retain any history referenced by v2 bindings/dependencies and return `40994` instead of deleting it.
- New package APIs use `/api/assistant-config/skill-packages`. Do not overload legacy `/skills` responses with partially populated v2 fields.
- A new package is never executable merely because it is saved or published. `catalog_enabled` remains `false` throughout this plan.
- Legacy shadow packages use `migration_state=shadow` and `catalog_enabled=false`. They do not enter any runtime catalog.
- Native/imported packages use `migration_state=native` and still default to `catalog_enabled=false` until Plan 04.
- `general_chat` remains available to the old runtime, but maps to the default Main Agent Profile in the new domain. Do not create a `general-chat` Skill package.
- Standard Skill names are lowercase ASCII letters, digits, and hyphens, 1–64 characters, with no leading, trailing, or consecutive hyphen, and must match the package directory name.
- Standard `description` is 1–1024 characters and describes both what the Skill does and when it applies.
- Standard optional frontmatter fields are `license`, `compatibility`, `metadata`, and experimental `allowed-tools`.
- `license` and `compatibility` are optional strings; `compatibility`, when provided, is 1–500 characters. `metadata` is a string-to-string map with at most 50 entries. Experimental `allowed-tools`, when provided, is one non-empty space-delimited string matching the standard field shape; a YAML list is invalid.
- Unknown `SKILL.md` frontmatter keys are rejected by MindAtlas v1 so a typo cannot silently change routing expectations. A future standard field requires an explicit parser compatibility update.
- `allowed-tools` is preserved for interoperability only. It never grants MindAtlas authorization and cannot add a Capability binding.
- MindAtlas-only fields live in `mindatlas.yaml`, never as invented `SKILL.md` frontmatter fields.
- Published Skill/Main Agent versions and their child bindings/resources are append-only. Do not expose update or delete methods for version rows.
- PostgreSQL must enforce append-only rows with update/delete rejection triggers; service conventions alone are insufficient for immutable execution history. SQLite tests verify service behavior, while the PostgreSQL migration gate verifies the database boundary.
- A publish creates a distinct immutable `version_source=publish` row, even when its content matches the draft.
- Draft saves with identical `content_digest` return the existing draft version instead of creating unlimited duplicate rows.
- An identical draft save still atomically assigns `aggregate.draft_version_id` to the exact existing owned `save` row returned by that request. The pointer may move back to an older byte-identical draft; callers never infer the selected draft from “latest sequence”.
- Runtime code must never resolve “latest” during a frozen execution. This plan provides version references and digest contracts; later plans attach them to Runs.
- Workflow resolution must select the exact `AssistantWorkflowVersion` identified by `AssistantWorkflow.published_version_id`, verify `workflow_id` ownership, and never call `AssistantWorkflow.graph_snapshot` from the new resolver. The relationship may be used only after the same ID/ownership checks.
- `AssistantAgentProfile` currently has no `published_version` relationship. Agent resolution must query `AssistantAgentProfileVersion` by both `published_version_id` and `agent_profile_id`; it must never read mutable aggregate `system_prompt`, `tools`, or `kb_config` as the published source.
- A published binding must persist normalized input/output Schema bodies, their independent digests, conservative completion metadata, exact executable/config revision metadata, and `binding_contract_digest`. A digest without the corresponding immutable Schema body is not a valid Plan 01 output.
- `resolution_snapshot` is a versioned, secret-free, lossless binding-contract payload. Plan 02 may project it into runtime contracts, but may not query mutable Workflow, Agent, Tool, or OpenClaw catalog state to fill missing fields.
- An Agent Profile version owns executable prompt/model/Tool/KB state but owns no callable input/output Schema. In `mindatlas.yaml` v1, every `type: agent` declaration must explicitly declare its binding contract; that declaration is the only Schema source for the published Agent binding.
- Tool/Workflow bindings take their callable Schema from the exact target definition. A package-level contract, when supplied for either type, is a publish-time equality assertion and never an implicit adapter or Schema override.
- Published Agent and Workflow bindings freeze an ordered, exact dependency closure. Embedded Tool, Agent, and nested Workflow references may not be re-resolved by name to a later version during Plan 02 execution.
- Tool snapshots must contain no plaintext API key, encrypted API key, Authorization header, cookie, token, or credential material.
- Binding Schemas are persisted and later exposed to Providers, so they must not be a covert secret store. Reject `default`, `example`, and `examples` annotations recursively; bound `enum`/`const` values and reject them under secret-like property names. Never redact a Schema after validation because that would silently change callable semantics.
- Remote Tool credentials remain a mutable credential slot. A Tool config revision freezes non-secret execution configuration; credential rotation does not rewrite historical Skill versions.
- If the current Tool config revision no longer matches a published binding, later runtime resolution must fail closed until the Skill is republished; it must not silently drift.
- Republish makes the new revision available only to future Runs/activations. A Run already frozen to the old Tool revision must later enter an explicit reconciliation path; it may never swap versions in place.
- Code-native system Tools are registry definitions and normally have no persistent `AssistantTool` row. Freeze them with `target_identity=system-tool:<name>`, a null target UUID, project-owned normalized parameter/output Schemas, `system_tool_contract_set_digest`, and `APP_BUILD_REVISION`; do not invent a database row or synthetic UUID. Raw LangChain/Pydantic `model_json_schema()` output is compatibility evidence only and is never the persisted/digested source of truth.
- A disabled Capability cannot be newly published. Runtime availability remains a live deny gate and is not converted into permanent authorization by a frozen version.
- Imported `scripts/` are stored with `executable=false` regardless of ZIP mode bits. This plan adds no script execution path.
- Package import is create-only. Importing an existing canonical name or alias returns a conflict; merge/replace arrives with the Plan 09 admin workflow.
- Export is deterministic and strips executable mode bits.
- Use a production parser implemented in this repository. The official `skills-ref` package is demonstration/reference code and may only be used as an external conformance smoke test.
- Add `PyYAML>=6.0,<7.0` as an explicit direct dependency; do not rely on its current transitive installation.
- Add `jsonschema>=4.23,<5.0` as an explicit direct dependency in this plan because publish-time Schema normalization and validation import it. Plan 02 reuses this helper and must not introduce a second normalization algorithm.
- New API errors reserve these currently unused blocks:
  - `40490–40499`: package/profile/version/resource not found.
  - `40990–40999`: name, alias, version, binding, or immutable-reference conflicts.
  - `41390–41399`: package/file/resource limits.
  - `42290–42299`: invalid Agent Skills, extension manifest, publish, or resolution contracts.
- Lock the initial assignments:
  - `40490 package`, `40491 Skill Version`, `40492 resource`, `40493 Main Agent Profile/Version`.
  - `40990 canonical name`, `40991 alias namespace`, `40992 version sequence/concurrency`, `40993 immutable ownership/reference`, `40994 target is referenced`, `40995 create-only import conflict`.
  - `41390 compressed upload/encoded body`, `41391 total decoded bytes`, `41392 entry count/path/file size`.
  - `42290 SKILL.md`, `42291 mindatlas.yaml`, `42292 archive/path/link safety`, `42293 publish resolution`, `42294 Main Agent snapshot`, `42295 frozen-binding drift`.
- New HTTP responses keep the repository envelope exactly as `{success, code, message, data}`. Machine error type and safe structured details live under `data`; request IDs remain in server logs unless the shared envelope is changed separately.
- Every implementation task starts with a failing test and ends with its focused tests passing.
- Report the commands actually executed; do not report planned commands as completed verification.

---

## Locked Contract Decisions

### 1. Standard package and MindAtlas extension

Accepted package:

~~~text
weekly-review/
├── SKILL.md
├── mindatlas.yaml
├── scripts/
├── references/
├── assets/
└── other-safe-paths/
~~~

Minimal `SKILL.md`:

~~~markdown
---
name: weekly-review
description: Review MindAtlas entries over a time range; use for weekly summaries and retrospectives.
---

# Weekly review

Follow the active task context and use the bound review capability.
~~~

`mindatlas.yaml` v1:

~~~yaml
version: 1
display_name: 周度回顾
legacy_aliases:
  - weekly_review

routing:
  include_examples: []
  exclude_examples: []
  conflict_rules: []

capabilities:
  - type: tool
    key: search_entries
  - type: workflow
    key: periodic_review__workflow

policy:
  allowed_side_effects:
    - read
    - compute
  max_skill_calls: 16
  max_same_read_calls: 3
  requires_terminal_output: true
  terminal_text_allowed: true

provider_aliases: {}
metadata: {}
~~~

`routing.conflict_rules` uses one locked v1 dialect; later plans enforce these stored semantics rather than inventing another shape:

~~~python
class SkillConflictRuleV1(FrozenContract):
    kind: Literal["excludes", "requires", "exclusive_group"]
    target_skill: str | None = None
    group: str | None = None
~~~

- `excludes` and `requires` require exactly one nonempty `target_skill` and forbid `group`.
- `exclusive_group` requires exactly one nonempty `group` and forbids `target_skill`.
- Target names use the shared Skill lookup normalizer. Alias input may be accepted while drafting/importing, but publication resolves and stores the canonical package name in the immutable published snapshot.
- Group values are NFKC-normalized, trimmed, case-folded, control-free strings of 1–128 Unicode scalar values and at most 256 UTF-8 bytes.
- Unknown kinds/fields, invalid field combinations, self-targets, duplicate rules, and `excludes`/`requires` contradictions for the same target fail. Publication also fails when a target cannot resolve through the exact package namespace.
- Plan 01 validates/canonicalizes/stores the rules and includes them in content/version digests. Plan 05 evaluates coexistence symmetrically; Plan 01 performs no runtime activation decision.

An Agent Capability must declare its callable binding contract because the current `AssistantAgentProfileVersion.snapshot` contains prompt/Tool/model/KB execution state but no input/output Schema:

~~~yaml
capabilities:
  - type: agent
    key: research_assistant__agent
    contract:
      input_schema:
        type: object
        properties:
          input: {}
        required: [input]
        additionalProperties: false
      output_schema:
        type: object
        properties:
          text:
            type: string
        required: [text]
        additionalProperties: false
      completion:
        terminal_output: false
        needs_followup: true
        followup_hint: null
~~~

Rules:

- `version` must equal `1`.
- `display_name` is optional and at most 128 characters.
- `legacy_aliases` contains unique non-empty strings at most 128 characters. Both `general_chat` and `general-chat` are reserved for the Main Agent bridge and cannot be a package canonical name or alias.
- `routing.include_examples` and `exclude_examples` contain at most 100 items, each at most 1000 characters.
- `routing.conflict_rules` contains at most 50 `SkillConflictRuleV1` values with the exact validation/canonicalization above. Plan 01 stores the canonical form; Plan 05 defines runtime enforcement.
- `capabilities` contains at most 100 unique `(type, key)` pairs and may be empty for an instruction-only standard package.
- `type` is exactly `tool | workflow | agent`.
- `key` is a portable MindAtlas Domain Key or existing canonical target name, 1–128 characters.
- `contract` contains only `input_schema`, `output_schema`, and `completion`.
- `type: agent` requires `contract.input_schema` and `contract.output_schema`; the Skill binding declaration, not the Agent Profile and not an OpenClaw catalog row, owns this callable surface.
- `type: workflow` and code-native `type: tool` may omit `contract`; their exact published/registry target owns both Schemas. If a contract Schema is present, its normalized body and digest must exactly equal the target-owned Schema or publication fails; Plan 01 provides no argument/result transformer.
- A top-level remote Tool declaration takes input from `AssistantTool.input_params` but must explicitly provide `contract.output_schema`, because `AssistantTool` has no authoritative output-Schema column. That output contract is binding-owned. Plan 02 may parse a complete JSON response only when this frozen Schema requires structured JSON; OpenClaw catalog schemas and `text_field` modes are not universal sources.
- Input Schema roots must be JSON objects. Output Schema may accept any JSON value. Each normalized Schema is at most 256 KiB canonical JSON with maximum nesting depth 64. Remote `$ref` is forbidden; bounded local `$defs` references are allowed.
- `completion` defaults to `{terminal_output: false, needs_followup: true, followup_hint: null}`. It is immutable execution metadata, not proof that a business obligation was fulfilled; Plan 05 owns completion enforcement.
- `policy.allowed_side_effects` and budgets are author declarations stored with the Skill Version. They neither classify a target nor grant a Principal permission. Plan 02 supplies conservative target classification and deny-by-default authorization; Plan 05 supplies source-aware budget/completion enforcement.
- `policy.requires_terminal_output` and `terminal_text_allowed` are strict booleans. When terminal output is required, publication must prove at least one structural satisfaction path: either terminal text is allowed or an exact resolved binding has `completion.terminal_output=true`. An instruction-only Skill with terminal text forbidden is invalid; a Capability-only path additionally requires `max_skill_calls >= 1`. Plan 05 rechecks runtime availability/grant/budget satisfiability before activation.
- `policy.max_skill_calls` is the Owner Skill hard-call allocation and `max_same_read_calls` is that Skill’s repeated-read allocation. The Main Agent Profile separately freezes Run-wide Provider rounds, outer Agent rounds, Active Skill count, total Capability calls, Capability depth, and Agent depth. These eight dimensions remain separate fields; activating a Skill never increases the Profile/Run ceilings.
- `provider_aliases` is an optional map of declared Capability Domain Key to non-empty author hint, at most 100 entries and 128 characters per hint. Keys must exist in `capabilities`. Hints are stored only; Plan 03 validates them against a concrete Provider, resolves collisions, and freezes the final mapping in the Run Manifest Revision.
- `metadata` is a string-to-string map with at most 50 entries.
- Extra keys are rejected. Future schema changes require `version: 2` and an explicit migration.
- Database IDs, published version IDs, digests, timestamps, and publication status are generated by MindAtlas and are not accepted as authoritative package input.

The boundary is intentional:

| Concern | Owner in Plan 01 | Not allowed as a substitute |
|---|---|---|
| Portable metadata/instructions/resources | standard `SKILL.md` tree | invented MindAtlas frontmatter |
| Capability declarations, author policy, aliases | `mindatlas.yaml` | `allowed-tools` authorization |
| Tool callable Schema | exact code registry; remote input definition plus explicit binding-owned remote output contract | mutable OpenClaw catalog item |
| Workflow callable Schema | exact published Workflow Version through `workflow_contract_from_input` | aggregate Draft or `graph_snapshot` |
| Agent callable Schema | explicit Skill binding `contract` | mutable Agent aggregate, Agent Version guess, or OpenClaw override |
| Agent/Workflow executable state | exact published target version plus frozen dependency closure | resolving names to current/latest during execution |

A package without `mindatlas.yaml` remains a valid standard package and can be imported, drafted, exported, and even published as instruction-only content. It grants no Capability merely because `allowed-tools` is present.

### 2. Package safety profile

Import accepts a ZIP containing exactly one top-level directory whose name equals `SKILL.md.name`.

Limits:

| Limit | Value |
|---|---:|
| ZIP upload bytes | 32 MiB |
| Encoded JSON create/save body | 36 MiB |
| Total uncompressed bytes | 25 MiB |
| Entries | 200 |
| `SKILL.md` | 256 KiB, UTF-8 |
| `mindatlas.yaml` | 128 KiB, UTF-8 |
| One `scripts/` or `references/` file | 1 MiB |
| One `assets/` file | 10 MiB |
| One other file | 1 MiB |
| Path length inside Skill root | 512 UTF-8 bytes |

Reject:

- Absolute paths, drive prefixes, NUL, backslashes, `.`/`..` path components, and duplicate normalized paths.
- Symlinks, hard links, devices, FIFOs, encrypted ZIP members, and unsupported compression.
- ZIP entries whose declared or streamed size crosses a limit.
- YAML aliases, custom tags, duplicate keys, non-string mapping keys, or multiple documents.
- Invalid UTF-8 in `SKILL.md` or `mindatlas.yaml`.
- Missing/broken relative Markdown links from `SKILL.md`.
- `__MACOSX` and `.DS_Store` packaging noise; return a clear validation error instead of silently changing package contents.

Allow additional safe files/directories because the Agent Skills specification allows them. Classify them as `resource_kind=other`, preserve them in export, and never execute them.

The 32 MiB ZIP bound is intentionally above the 25 MiB decoded-content bound so every valid deterministic `ZIP_STORED` export can be imported again even for incompressible assets. The decoded limit remains the zip-bomb boundary.

### 3. Canonical callable Schema and binding snapshot

Create one reusable `app.assistant.domain.json_schema` implementation in Plan 01. Publication and Plan 02 runtime validation must share these functions:

~~~python
def normalize_binding_schema(
    schema: Mapping[str, JsonValue],
    *,
    require_object_root: bool,
) -> dict[str, JsonValue]: ...


def binding_schema_digest(schema: Mapping[str, JsonValue]) -> str: ...
~~~

Normalization is semantic-preserving and deterministic:

- Deep-copy only JSON values; reject Python objects, non-string mapping keys, NaN, and Infinity.
- Validate the normalized document with `Draft202012Validator.check_schema`.
- Convert OpenAPI `nullable: true` only when the same node has a concrete `type`; add `null` to a unique, deterministically ordered type union and remove `nullable`. Reject ambiguous `nullable` uses rather than guessing.
- Sort and deduplicate `required` arrays and Schema `type` unions. Preserve semantically meaningful array order in `prefixItems`, `allOf`, `anyOf`, `oneOf`, and `enum`.
- Preserve bounded `title`, `description`, `deprecated`, `readOnly`, and `writeOnly` annotations. Reject `default`, `example`, `examples`, unapproved `x-*` annotations, and any annotation/object key whose value is not bounded JSON.
- Preserve `enum` and `const` because they are validation semantics, but cap each enum at 256 values, each string value at 4096 UTF-8 bytes, and one enum’s canonical JSON at 64 KiB. Recursively reject `enum`/`const` beneath property names matching the locked case-insensitive secret tokens `api_key`, `apikey`, `authorization`, `cookie`, `credential`, `password`, `secret`, or `token`. Publication also rejects any Schema value copied from live Tool headers/credentials. Do not redact after normalization.
- Reject absolute/remote `$ref`, external identifiers that imply fetching, over-depth documents, and documents over the canonical byte limit. Permit only local fragment references whose targets exist inside the same document.
- Never fetch a URI and never run a format checker during publication. Plan 02 may treat `format` as annotation but must reuse the same normalized body and digest.

System and remote Tool parameters use one project-owned conversion contract:

~~~python
def tool_params_to_binding_schema(
    params: Sequence[ToolParamContract],
    *,
    require_object_root: bool = True,
) -> dict[str, JsonValue]: ...
~~~

The converter accepts only the explicit MindAtlas parameter grammar already projected by the registry/configuration layer, maps every supported primitive/array/object form deterministically, and fails publication on an unknown or lossy type. For code-native Tools, `SystemToolFullDefinition.input_params` and `output_params` are the authoritative source; LangChain `args_schema.model_json_schema()` is generated separately and checked for compatible required fields/types in tests, never hashed or persisted directly. For remote Tools, `AssistantTool.input_params` is the input source and the Skill declaration remains the binding-owned output source. Checked-in golden vectors pin every system Tool’s normalized input/output Schemas, individual digests, and the ordered `system_tool_contract_set_digest`. Clean-environment CI detects LangChain dependency drift without making that dependency’s incidental Schema formatting part of the immutable contract.

Every resolved binding stores this payload in `resolution_snapshot`:

~~~json
{
  "schemaVersion": 1,
  "target": {
    "capabilityType": "workflow",
    "targetIdentity": "workflow:<uuid>",
    "targetId": "<uuid>",
    "targetVersionId": "<uuid>",
    "targetRevision": null,
    "resolutionDigest": "<sha256>"
  },
  "inputSchema": {"type": "object"},
  "outputSchema": {"type": "object"},
  "inputSchemaDigest": "<sha256>",
  "outputSchemaDigest": "<sha256>",
  "completion": {
    "terminalOutput": false,
    "needsFollowup": true,
    "followupHint": null
  },
  "execution": {
    "configDigest": "<sha256-or-null>",
    "executableRevision": "<version-or-build-revision>"
  },
  "dependencyClosure": [
    {"path": "root/tool:search_entries", "dependencyDigest": "<sha256>"}
  ],
  "dependencyClosureDigest": "<sha256>",
  "bindingContractDigest": "<sha256>"
}
~~~

`bindingContractDigest` is calculated over the canonical payload with the `bindingContractDigest` member omitted, then inserted as the final member and duplicated in its indexed table column. This explicitly avoids a digest cycle. Reconstructing the payload from persisted columns/closure rows must reproduce the same bytes and digest.

The ordered dependency closure is required for Agent/Workflow bindings and is persisted in immutable `assistant_skill_capability_dependency` rows. The parent snapshot contains the ordered path/digest index; loading the parent plus all owned dependency rows is the one lossless reconstruction path:

- Agent: every Tool named by the exact published Agent snapshot.
- Workflow: every Tool node, Agent-node Tool, and pinned nested `workflow_call`, recursively from the exact published snapshot.
- An embedded remote Tool dependency freezes its current native return contract as JSON Schema `{type: string}` because `RemoteTool.invoke` returns text to the existing Agent/Workflow engine. This closure rule does not supply the binding-owned output contract required to expose that remote Tool as a top-level Capability.
- Model: every primary/default/custom LLM or embedding configuration used by the Agent or Workflow closure, including model dependencies of Agent nodes and KB paths that the execution can reach. Resolve a `model_source=default` component binding to its concrete `AiModel.id` at publication; never persist “default” as a future runtime lookup.
- Each dependency freezes its target identity, exact target version/config/build revision, normalized Schema bodies/digests, and secret-free execution metadata.
- Each Model dependency freezes `AiModel.id`, `AiModel.runtime_revision`, `AiCredential.id`, `AiCredential.runtime_revision`, model name/type, normalized secret-free endpoint/config digests, adapter/protocol/build revision slots, and a self-excluding `model_ref_digest`. It never freezes or hashes plaintext/ciphertext API keys.
- A reachable model path that currently resolves only from mutable environment settings or another source without a credential-slot revision cannot be published in v1. Fail with `42293` and a safe source locator; do not hash an environment secret or pretend `APP_BUILD_REVISION` versions credential rotation.
- Dependency paths are canonical structural paths, not display names. Sort by path before hashing and persistence.
- Export shared constants `MAX_CAPABILITY_CLOSURE_DEPTH=16`, `MAX_CAPABILITY_CLOSURE_REFS=256`, and `MAX_CAPABILITY_CLASSIFIED_NODES=4096`. Reject dynamic/latest dependencies, unresolved names, cycles, duplicate paths with conflicting refs, or any bound overflow during publication. Plan 02 must consume these constants and may only apply an equal or stricter runtime bound.
- The closure does not make a nested target independently authorized as a top-level Capability. It is execution-integrity evidence for the already authorized parent binding.
- Plan 02 must build an exact resolver map from these rows and pass it into Agent/Workflow execution. If the current engine cannot avoid a name-based mutable lookup for any reachable dependency, that binding is unavailable; falling back to current/latest is forbidden.

### 4. Deterministic digests

Use SHA-256 lowercase hex.

~~~text
skill_md_digest        = sha256(exact SKILL.md bytes)
manifest_digest        = sha256(exact mindatlas.yaml bytes or empty bytes)
resource sha256        = sha256(exact resource bytes)
resource_index_digest  = sha256(canonical JSON of ordered path/kind/mediaType/size/sha256)
content_digest         = sha256(canonical JSON of schemaVersion=1,
                                skillMdDigest, manifestDigest, resourceIndexDigest)
input_schema_digest    = sha256(canonical normalized input Schema)
output_schema_digest   = sha256(canonical normalized output Schema)
system_tool_contract_set_digest = sha256(canonical ordered system-tool name/inputSchemaDigest/outputSchemaDigest tuples)
target_resolution_digest = sha256(canonical exact target reference and execution revision)
dependency_digest      = sha256(canonical dependency reference, schemas, and execution revision)
dependency_closure_digest = sha256(canonical ordered path/dependencyDigest pairs)
binding_contract_digest = sha256(canonical lossless binding payload without its own digest)
binding_set_digest     = sha256(canonical ordered capabilityType/key/bindingContractDigest tuples; publish only)
version_digest         = sha256(canonical JSON of contentDigest and bindingSetDigest; publish only)
~~~

Canonical JSON uses UTF-8, sorted keys, `ensure_ascii=false`, no insignificant spaces, and rejects NaN/Infinity. Portable content/resource digests exclude database IDs, timestamps, sequence numbers, origin metadata, and ZIP metadata. Execution-reference/dependency/binding digests deliberately include frozen target/version/model/credential IDs and runtime revisions; they exclude only their own storage-row IDs, timestamps, secrets, and mutable audit/display metadata.

`content_digest` is the portable package identity and therefore does not change when MindAtlas targets are republished. `version_digest` is the executable published-version identity and changes when a target, Schema body, completion contract, executable/config revision, or dependency-closure item changes. Draft rows have null `binding_set_digest` and `version_digest`.

### 5. Append-only manifest revisions

`ResolvedRunManifestRevision` is a frozen Pydantic contract in this plan, not a database table yet. Its v1 shape already reserves exact Provider/Model refs and the empty Provider-alias collection so Plan 03 can populate values without changing digest semantics:

~~~python
class ProviderRef(FrozenContract):
    schema_version: Literal[1] = 1
    provider_protocol: str
    provider_config_id: UUID | None
    provider_runtime_revision: int | None
    provider_config_digest: str | None
    adapter_key: str | None
    adapter_revision: str | None
    protocol_revision: str | None
    app_build_revision: str | None
    provider_ref_digest: str


class ModelRef(FrozenContract):
    schema_version: Literal[1] = 1
    model_id: UUID
    model_name: str
    model_type: Literal["llm", "embedding"]
    model_runtime_revision: int | None
    credential_id: UUID
    credential_runtime_revision: int | None
    credential_config_digest: str | None
    model_config_digest: str | None
    provider_ref_digest: str | None
    capability_probe_id: UUID | None
    capability_probe_digest: str | None
    model_ref_digest: str


class ResolvedProviderAliasRef(FrozenContract):
    provider_protocol: str
    domain_key: str
    provider_alias: str
    binding_contract_digest: str


class ResolvedMainAgentRef(FrozenContract):
    profile_id: UUID
    version_id: UUID
    profile_key: str
    sequence: int
    content_digest: str


class ResolvedCapabilityRef(FrozenContract):
    capability_type: Literal["tool", "workflow", "agent"]
    capability_key: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    input_schema_digest: str
    output_schema_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    binding_contract_digest: str


class ResolvedRunManifestRevision(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    revision: int
    parent_digest: str | None
    main_agent: ResolvedMainAgentRef
    active_skills: tuple[ResolvedSkillRef, ...]
    capabilities: tuple[ResolvedCapabilityRef, ...]
    provider: ProviderRef | None
    model: ModelRef | None
    provider_aliases: tuple[ResolvedProviderAliasRef, ...] = ()
    effective_policy_digest: str | None
    manifest_digest: str
~~~

Rules:

- Base revision is `1`.
- Skill activation returns a new instance with `revision + 1`.
- Existing Skill/Capability refs cannot be replaced in a child revision.
- Duplicate activation of the same Skill Version is idempotent.
- Reusing the same canonical Skill with a different version in one Run is rejected unless a future explicit upgrade operation is designed.
- `parent_digest` must equal the previous revision’s `manifest_digest`.
- `schema_version`, exact Provider/Model ref fields, and `provider_aliases` are part of the digest payload from the first fixed vector. An empty tuple is serialized explicitly; omission and empty are not two different encodings.
- `provider_ref_digest` and `model_ref_digest` are calculated from their canonical payloads with their own digest field omitted. Secret/ciphertext values and raw headers never participate.
- `ResolvedMainAgentRef.content_digest` is the canonical digest of the immutable normalized Profile snapshot. The Profile table, DTOs, Manifest, and Task 6 use this one name; `snapshot_digest` is not a second persisted identity.
- Runtime revisions and probe fields are nullable only because Plan 01 defines the cross-plan schema before a Provider loop exists. Plan 03 must reject an executable model ref unless the required revision/config/adapter/probe evidence is complete; it must not extend this v1 model silently.
- Plan 01 parses and stores author alias hints but performs no Provider-specific syntax validation or allocation. Plan 03 appends `ResolvedProviderAliasRef` values through a child Manifest revision; it never mutates an existing revision or redefine the class.
- `manifest_digest` uses an explicit digest payload builder, not `model_dump()` of arbitrary runtime objects. Fixed vectors cover base-empty, one Skill, exact Provider/Model refs, empty aliases, and later alias append compatibility.

### 6. Main Agent Profile v1 snapshot

~~~json
{
  "schemaVersion": 1,
  "basePrompt": "string",
  "responseStyle": {},
  "supportedEntrypoints": ["assistant_chat"],
  "modelRequirements": {
    "toolCalling": true,
    "streaming": true,
    "multiToolCalls": true,
    "jsonSchema": true
  },
  "controlCapabilityKeys": [],
  "skillCatalogScope": {"mode": "all_published", "packageIds": []},
  "contextBudget": {
    "maxPromptCharacters": 72000,
    "maxActiveSkills": 4,
    "maxSkillInstructionCharacters": 24000,
    "maxSingleSkillInstructionCharacters": 12000,
    "maxHistoryCharacters": 24000,
    "maxToolSummaryCharacters": 24000,
    "maxResourceBytesPerCall": 65536
  },
  "outputBudget": {
    "maxCompletionTokens": 4096,
    "maxProviderRounds": 8,
    "maxOuterAgentRounds": 8,
    "maxTotalCapabilityCalls": 16,
    "maxParallelCalls": 4,
    "maxCapabilityDepth": 4,
    "maxAgentDepth": 2,
    "maxSameReadSignature": 3,
    "maxCompletionFollowupRounds": 2,
    "maxWallTimeMs": 120000
  },
  "globalSafetyPolicy": {"denyByDefault": true},
  "fallbackPolicy": {
    "legacyRuntimeAllowed": true,
    "beforeSideEffectsOnly": true
  }
}
~~~

`skillCatalogScope.mode` is exactly `all_published | allowlist`. `all_published` requires an empty `packageIds`; `allowlist` requires 1–1000 unique UUIDs sorted by canonical UUID text. These are selection bounds, not execution authorization. The shown budget values are the conservative bootstrap defaults and v1 hard ceilings; lower positive values are accepted only when `maxSingleSkillInstructionCharacters <= maxSkillInstructionCharacters`, `maxHistoryCharacters + maxToolSummaryCharacters + maxSkillInstructionCharacters <= maxPromptCharacters`, `maxParallelCalls <= maxTotalCapabilityCalls`, `maxSameReadSignature <= maxTotalCapabilityCalls`, and `maxCompletionFollowupRounds < maxProviderRounds`. Later settings may lower them but may not exceed the shown ceilings without a versioned Profile-contract change. Plan 01 persists and versions this snapshot. Plan 04 defines prompt construction and runtime semantics, and Plan 05 enforces the frozen budget values.

### 7. Alias normalization and immutable version selection

Canonical names and aliases share one lookup namespace. Define one pure normalizer and use it in create, import, shadow sync, lookup, and conflict translation:

~~~python
def normalize_skill_lookup_name(value: str) -> str:
    """NFKC, trim, Unicode casefold; reject controls, NUL, slash/backslash, and empty."""
~~~

Rules:

- Canonical package names still use the stricter Agent Skills ASCII hyphen-case contract. Aliases may retain legacy underscore or safe Unicode identifiers, up to 128 Unicode scalar values and 512 UTF-8 bytes before/after normalization.
- Internal whitespace is preserved; it is not silently changed into a different alias. The legacy canonical-name mapper separately converts invalid runs to hyphens.
- Store both original `alias` and computed `normalized_alias`; unique-index only the normalized value. Never rely on database locale-sensitive `lower()` for identity.
- Reserve normalized `general_chat` and `general-chat` before any bootstrap/import.
- Alias rows are append-only. Renaming a native package means creating a new package in a later admin migration, not rewriting the canonical alias.

Later runtime lookup must be implemented as an exact selection transaction, not `alias -> latest` persisted state:

1. Normalize the requested canonical name/alias and resolve exactly one append-only alias row.
2. Load its package and the explicit `published_version_id`; verify the version belongs to that package and is `version_source=publish`.
3. Return `ResolvedSkillRef` containing package ID, immutable version ID, sequence, content/version digests, canonical name, requested normalized alias, and alias-row ID as audit evidence.
4. Persist/freeze that ref in the Manifest. Checkpoint, resume, and Capability resolution use the version ID/digest, never the alias or current package pointer.

Plan 01 provides and tests this lookup service even though no old runtime calls it yet. Disabled/catalog state remains a live deny gate; exact version resolution is identity evidence, not execution authorization.

### 8. Model and credential execution revisions

Plan 01 adds `runtime_revision INTEGER NOT NULL DEFAULT 1` to both `ai_model` and `ai_credential`, because an Agent/Workflow binding cannot be exact while its default/custom model configuration silently drifts.

The new exact-ref contract uses the current `ai_registry` (`AiModel` + `AiCredential`) path. The separate legacy `ai_provider` table/API remains untouched and is not silently treated as the same identity model.

- Credential revision increments exactly once when normalized base URL or credential slot changes; display-name-only changes do not increment.
- Model revision increments exactly once when model name or model type changes. Current service does not move a model to another credential; a future move must increment and join the same guard.
- `AiComponentBinding` changes do not mutate any model/credential revision. At Skill publication, a default component binding is resolved to its concrete model/credential ref and frozen in the dependency closure.
- Service updates lock the row, compare one normalized before/after payload, and advance `runtime_revision=old+1` in the same transaction.
- PostgreSQL guard triggers reject direct execution-sensitive updates that fail to advance exactly one revision and reject revision-only changes. SQLite verifies service behavior; the PostgreSQL gate verifies the database guard.
- Secret-free credential config digest includes credential ID, runtime revision, normalized base URL and adapter/protocol identity. It excludes API key plaintext, ciphertext, hint, and any digest of secret material. The revision is the credential-slot rotation evidence.
- Model config digest includes model ID/name/type/runtime revision, credential ID/revision/config digest, and adapter/protocol/build revision slots.

Plan 03 may add probe history/current pointers and may clear them when these revisions change. It consumes these existing revision columns; it must not add a second revision mechanism.

### 9. Locked domain vocabulary and service DTOs

Use these exact literals across Plan 01; later plans import them from `app.assistant.domain.contracts` instead of redeclaring strings:

~~~python
CapabilityType = Literal["tool", "workflow", "agent"]
BindingResolutionStatus = Literal["unresolved", "resolved"]
SkillPackageMigrationState = Literal["shadow", "native", "cutover"]
MainAgentMigrationState = Literal["bootstrap", "shadow", "native", "cutover"]
VersionSource = Literal["save", "publish"]
DeclaredSideEffect = Literal["read", "compute", "draft", "write", "control"]
AgentLoopStatus = Literal["completed", "waiting_input", "waiting_approval", "needs_reconciliation", "failed", "cancelled"]
CapabilityCallStatus = Literal[
    "pending", "running", "deferred", "blocked", "waiting_approval",
    "waiting_input", "completed", "failed", "cancelled", "unknown",
    "needs_reconciliation",
]
~~~

`AgentLoopStatus` and `CapabilityCallStatus` are reserved interoperability vocabulary only; Plan 01 creates no loop, result object, call ledger, or persistence for them. Plan 02 owns authoritative Capability classification. It must derive `side_effect`, `parallel_safe`, and `timeout_policy` only from Plan 01’s immutable target/version/closure evidence plus a checked-in, versioned classification table, then freeze them in its descriptor digest. It may not query mutable catalog state to fill those slots, and Plan 01 must not invent optimistic values.

The parser/service boundary uses concrete frozen DTOs, not anonymous dictionaries:

~~~python
class ParsedSkillResource(FrozenContract):
    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    content: bytes
    byte_size: int
    sha256: str
    executable: Literal[False] = False


class SkillResourceIndexEntry(FrozenContract):
    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    byte_size: int
    sha256: str


class ParsedSkillPackage(FrozenContract):
    canonical_name: str
    frontmatter: AgentSkillFrontmatter
    manifest: MindAtlasSkillManifestV1 | None
    skill_md_bytes: bytes
    mindatlas_yaml_bytes: bytes | None
    resources: tuple[ParsedSkillResource, ...]
    resource_index: tuple[SkillResourceIndexEntry, ...]
    skill_md_digest: str
    manifest_digest: str
    resource_index_digest: str
    content_digest: str


class StoredSkillResource(FrozenContract):
    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    byte_size: int
    sha256: str
    content: bytes


class ResolvedCapabilityDependency(FrozenContract):
    ordinal: int
    dependency_path: str
    dependency_type: Literal["system_tool", "remote_tool", "workflow", "agent", "model"]
    target_identity: str
    resolved_tool_id: UUID | None
    resolved_workflow_version_id: UUID | None
    resolved_agent_version_id: UUID | None
    resolved_model_id: UUID | None
    target_revision: int | None
    input_schema: dict[str, JsonValue] | None
    output_schema: dict[str, JsonValue] | None
    input_schema_digest: str | None
    output_schema_digest: str | None
    resolution_snapshot: dict[str, JsonValue]
    resolution_digest: str
    dependency_digest: str


class ResolvedCapabilityBinding(FrozenContract):
    capability_type: CapabilityType
    capability_key: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    resolved_tool_id: UUID | None
    resolved_workflow_version_id: UUID | None
    resolved_agent_version_id: UUID | None
    resolved_revision: int | None
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    input_schema_digest: str
    output_schema_digest: str
    completion: CapabilityCompletionContract
    config_digest: str | None
    executable_revision: str | None
    resolution_digest: str
    resolution_snapshot: dict[str, JsonValue]
    dependencies: tuple[ResolvedCapabilityDependency, ...]
    dependency_closure_digest: str
    binding_contract_digest: str


class CurrentCapabilityDependencyReference(FrozenContract):
    dependency_path: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    executable_revision: str | None
    resolution_digest: str
    dependency_digest: str


class CurrentCapabilityReference(FrozenContract):
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    executable_revision: str | None
    system_tool_contract_set_digest: str | None
    input_schema_digest: str
    output_schema_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    dependencies: tuple[CurrentCapabilityDependencyReference, ...]
~~~

`AssistantSkillCapabilityBinding` is the ORM class name; `ResolvedCapabilityBinding` is the pre-persistence domain DTO. Do not introduce an undefined `SkillCapabilityBinding` alias. `StoredSkillResource.content` is loaded explicitly by the export/retrieval repository port and is never present in list/detail DTOs. `CreateSkillPackageCommand`, `SaveSkillDraftCommand`, `PublishSkillVersionCommand`, and `PublishMainAgentProfileCommand` are the only mutation inputs; both publish commands require `draft_version_id: UUID`.

### 10. Resource storage and secret-free remote configuration

Plan 01 stores resource bytes in PostgreSQL through a content-addressed blob table rather than copying `LargeBinary` into every draft row or introducing a second MinIO transaction. This choice keeps the bounded 25 MiB package save/version transaction atomic and deterministic. A later storage migration may replace the repository port without changing package/version contracts.

- `assistant_skill_resource_blob` owns immutable `sha256`, `byte_size`, `content`, and `created_at`; unique `(sha256, byte_size)` deduplicates identical bytes across all drafts/packages. On a uniqueness race, re-read the row and verify its bytes before reuse.
- `assistant_skill_version_resource` references the blob with `blob_id ... ON DELETE RESTRICT`; it keeps path/kind/media type/size/digest metadata for lossless version reconstruction but no copied content column.
- Enforce at most 256 MiB of distinct referenced blob bytes per package in the locked aggregate transaction, in addition to the per-version 25 MiB limit. Tests cover many drafts, the exact boundary, concurrent deduplication, and rollback without an unreferenced blob.
- Normal Plan 01 APIs expose no deletion, purge, or blob-GC operation. Because blob insertion and version references commit in one database transaction, a failed save creates no orphan. Future operator GC must prove zero immutable references before deletion.
- Every remote Tool update compares a canonical execution-sensitive payload. Header **names and values**, endpoint/method/query/body mappings, timeout/wrapper, and authentication shape all participate in deciding whether `config_revision` advances. The frozen snapshot keeps only safe structural metadata and the resulting revision/digest; it never keeps header values, endpoint userinfo/query/fragment, raw request templates, secret hashes, or credential-derived defaults.

---

## Database Schema

### `assistant_skill_package`

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `canonical_name` | String(64), unique |
| `display_name` | String(128) |
| `description` | String(1024) |
| `draft_version_id` | nullable UUID pointer |
| `published_version_id` | nullable UUID pointer |
| `legacy_skill_id` | nullable UUID, unique, references legacy `assistant_skill` with `SET NULL` |
| `migration_state` | `shadow | native | cutover` |
| `legacy_source_digest` | nullable SHA-256 |
| `catalog_enabled` | bool, default false |
| `is_system` | bool |
| timestamps | mutable package metadata only |

### `assistant_skill_package_alias`

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `skill_package_id` | FK restrict; package deletion is not exposed |
| `alias` | original string |
| `normalized_alias` | String(512), NFKC/trim/casefold unique lookup key |
| `alias_type` | `canonical | legacy | custom` |
| `created_at` | timestamp |

Insert the canonical name as an `alias_type=canonical` row. This provides one uniqueness namespace across canonical and legacy names.

### `assistant_skill_version`

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `skill_package_id` | FK restrict; package deletion is not exposed |
| `sequence_no` | monotonic per package |
| `version_name` | String(255) |
| `version_source` | `save | publish` |
| `source_draft_version_id` | nullable self-FK restrict; required for publish and must belong to same package |
| `origin` | `api | import | legacy` |
| `skill_md` | exact text |
| `mindatlas_yaml` | exact text or null |
| `frontmatter` | parsed JSON |
| `extension_manifest` | parsed JSON |
| `resource_index` | canonical JSON |
| `skill_md_digest` | SHA-256 |
| `manifest_digest` | SHA-256 |
| `resource_index_digest` | SHA-256 |
| `content_digest` | SHA-256 |
| `binding_set_digest` | nullable SHA-256; required for publish |
| `version_digest` | nullable SHA-256; required for publish |
| `created_at` | timestamp; no `updated_at` |

Unique `(skill_package_id, sequence_no)`.
Also create a partial unique index on `(skill_package_id, content_digest) WHERE version_source='save'` so concurrent identical draft saves converge. Published rows intentionally have no equivalent uniqueness constraint.

### `assistant_skill_resource_blob` and `assistant_skill_version_resource`

The content-addressed blob table holds:

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `sha256` | SHA-256 |
| `byte_size` | positive integer |
| `content` | LargeBinary, exact bytes |
| `created_at` | timestamp |

Unique `(sha256, byte_size)`. The service verifies the exact bytes before reusing a row; a digest collision fails closed.

The version resource table holds:

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `skill_version_id` | FK restrict; version deletion is not exposed |
| `path` | normalized path relative to Skill root |
| `resource_kind` | `scripts | references | assets | other` |
| `media_type` | server-detected media type |
| `byte_size` | integer |
| `sha256` | digest |
| `blob_id` | FK to `assistant_skill_resource_blob`, RESTRICT |
| `executable` | always false in this plan |
| `created_at` | timestamp |

Unique `(skill_version_id, path)`. A constraint/commit-time check requires `byte_size` and `sha256` to equal the referenced blob metadata.

### `assistant_skill_capability_binding`

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `skill_version_id` | FK restrict; version deletion is not exposed |
| `ordinal` | stable source order |
| `capability_type` | `tool | workflow | agent` |
| `capability_key` | source Domain Key |
| `resolution_status` | `unresolved | resolved` |
| `target_identity` | stable string identity; always present when resolved |
| `resolved_tool_id` | nullable FK to remote `assistant_tool`, RESTRICT; null for code-native system Tools |
| `resolved_workflow_version_id` | nullable FK to exact `assistant_workflow_version`, RESTRICT |
| `resolved_agent_version_id` | nullable FK to exact `assistant_agent_profile_version`, RESTRICT |
| `resolved_revision` | nullable positive integer for Tool config revision |
| `input_schema_digest` | nullable SHA-256; required when resolved |
| `output_schema_digest` | nullable SHA-256; required when resolved |
| `config_digest` | nullable SHA-256 |
| `executable_revision` | nullable String(255) |
| `resolution_digest` | nullable SHA-256 of exact target/execution reference |
| `dependency_closure_digest` | nullable SHA-256; required when resolved, including canonical empty closure |
| `binding_contract_digest` | nullable SHA-256 of the lossless binding payload |
| `resolution_snapshot` | versioned, lossless, secret-free binding payload containing normalized Schema bodies |
| `created_at` | timestamp |

Unique `(skill_version_id, capability_type, capability_key)`.

Draft bindings may be unresolved. Every binding in a published version must be resolved, have a complete snapshot/digest set, and satisfy one target-reference shape:

- code-native Tool: all typed FKs null; `target_identity=system-tool:<name>` and build revision present;
- remote Tool: only `resolved_tool_id` and `resolved_revision` present;
- Workflow: only `resolved_workflow_version_id` present;
- Agent: only `resolved_agent_version_id` present.

The typed version FKs intentionally block the current `delete-orphan`/`CASCADE` target-version deletion paths when a published binding references history. Domain `target_id` and `target_version_id` are projected from the typed row/version owner; do not add an unvalidated polymorphic UUID.

### `assistant_skill_capability_dependency`

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `binding_id` | FK to immutable binding, RESTRICT |
| `ordinal` | deterministic closure order |
| `dependency_path` | canonical structural path, String(512) |
| `dependency_type` | `system_tool | remote_tool | workflow | agent | model` |
| `target_identity` | stable identity |
| `resolved_tool_id` | nullable FK to remote Tool, RESTRICT |
| `resolved_workflow_version_id` | nullable exact Workflow Version FK, RESTRICT |
| `resolved_agent_version_id` | nullable exact Agent Version FK, RESTRICT |
| `resolved_model_id` | nullable FK to `ai_model`, RESTRICT |
| `target_revision` | Tool/model runtime revision when applicable |
| `input_schema_digest` / `output_schema_digest` | nullable independent Schema digests |
| `resolution_digest` | exact target/model ref digest |
| `dependency_digest` | digest of the full dependency payload without its own digest |
| `resolution_snapshot` | versioned, lossless, secret-free exact ref/Schema/execution payload |
| `created_at` | timestamp |

Unique `(binding_id, ordinal)` and `(binding_id, dependency_path)`. A database check enforces the one allowed typed-FK shape for each dependency type. Model rows carry their complete `ModelRef` snapshot, including credential ID/revision/config digest, even though only `resolved_model_id` can be a direct FK. The parent binding’s ordered closure index must exactly match these owned rows; service and PostgreSQL publish validation test this invariant.

### `assistant_main_agent_profile` and `assistant_main_agent_profile_version`

The aggregate table holds:

| Column | Contract |
|---|---|
| `id` | UUID PK |
| `profile_key` | String(64), unique; initial key `default` |
| `display_name` | String(128) |
| `is_default` | bool with one-row partial unique index when true |
| `draft_version_id` | nullable owned-version pointer |
| `published_version_id` | nullable owned-version pointer |
| `migration_state` | `bootstrap | shadow | native | cutover` |
| `legacy_skill_id` | nullable UUID, unique, references legacy `assistant_skill` with `SET NULL` |
| `legacy_source_digest` | nullable SHA-256 |
| `runtime_enabled` | bool, always false in Plan 01 |
| timestamps | mutable aggregate metadata only |

The immutable version table holds:

- `id` and `profile_id` with a restrict foreign key.
- monotonic sequence number and version name.
- `version_source=save|publish` and `origin=bootstrap|api|legacy`.
- nullable `source_draft_version_id` self-FK, required for publish and ownership-checked.
- exact validated `snapshot`.
- `content_digest`.
- optional secret-free `source_ref` for legacy source IDs and digest evidence.
- `created_at` only.

Create a partial unique index on `(profile_id, content_digest) WHERE version_source='save'`. Identical profile drafts converge; identical publishes remain separate audit events.

### Tool, model, and credential revision support

Add to `assistant_tool`:

- `config_revision INTEGER NOT NULL DEFAULT 1`.

Increment only when execution-sensitive non-secret configuration changes. Name/description-only edits do not increment it. A credential replacement increments it without storing the secret in any snapshot.

Add to both `ai_model` and `ai_credential`:

- `runtime_revision INTEGER NOT NULL DEFAULT 1`.

Use the exact revision rules in Locked Decision 8. All three revision columns retain a non-null server default because migrations, bootstrap, and test factories create rows outside one service path.

---

## API Contract

### Skill packages

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/assistant-config/skill-packages` | list package summaries |
| POST | `/api/assistant-config/skill-packages` | create native package and initial draft |
| GET | `/api/assistant-config/skill-packages/{id}` | detail + current draft/published summaries |
| PUT | `/api/assistant-config/skill-packages/{id}/draft` | append/reuse immutable draft |
| POST | `/api/assistant-config/skill-packages/{id}/publish` | publish body-selected owned draft version |
| GET | `/api/assistant-config/skill-packages/{id}/versions` | immutable version summaries |
| GET | `/api/assistant-config/skill-packages/{id}/versions/{version_id}` | immutable version metadata |
| GET | `/api/assistant-config/skill-packages/{id}/versions/{version_id}/resources/{path}` | one authorized resource |
| POST | `/api/assistant-config/skill-packages/import` | create-only ZIP import as draft |
| GET | `/api/assistant-config/skill-packages/{id}/versions/{version_id}/export` | deterministic ZIP export |

JSON create/save accepts:

~~~json
{
  "skillMd": "...",
  "mindatlasYaml": "...",
  "resources": [
    {
      "path": "references/guide.md",
      "contentBase64": "..."
    }
  ],
  "versionName": "initial draft"
}
~~~

The client never supplies authoritative media type. JSON and ZIP ingress call the same byte/path-based detector and store the server result; extension/content spoof cases have identical outcomes. Never return resource bytes from list/detail/version APIs.

Package/version lists use offset pagination: `limit` defaults to `50` and is capped at `200`; `offset` defaults to `0`. Package filters are `migrationState`, `publicationState=unpublished|published`, and `catalogEnabled`; version filters are `versionSource` and `origin`. Stable ordering is `(created_at DESC, id DESC)`. The repository envelope places `{items, total, limit, offset}` in `data`.

Resource retrieval authorizes and loads by the exact ownership join `(package_id, version_id, normalized_path)`, proving that the version belongs to the package before reading its blob. A success response uses the server-detected content type, a sanitized ASCII/UTF-8 `Content-Disposition: attachment` filename, `X-Content-Type-Options: nosniff`, and no inline rendering for HTML/SVG or unknown active content.

### Main Agent Profile

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/assistant-config/main-agent-profiles/default` | profile detail |
| PUT | `/api/assistant-config/main-agent-profiles/default/draft` | append/reuse draft snapshot |
| POST | `/api/assistant-config/main-agent-profiles/default/publish` | publish body-selected owned draft version |
| GET | `/api/assistant-config/main-agent-profiles/default/versions` | version summaries |

The Profile remains `runtime_enabled=false` in this plan.

Router prefix ownership is singular: `skills.router` declares the full `/api/assistant-config/skill-packages` and `/api/assistant-config/main-agent-profiles` prefixes through two child routers, and `app.main` calls `include_router(...)` without adding another prefix. Route decorators are relative to those prefixes. API tests assert every external path above exactly once so `/skill-packages/skill-packages` duplication cannot ship.

---

## File Responsibility Map

### Create

- `backend/app/assistant/domain/__init__.py`
- `backend/app/assistant/domain/contracts.py` — frozen cross-plan contracts and enums.
- `backend/app/assistant/domain/digests.py` — canonical JSON and SHA-256.
- `backend/app/assistant/domain/json_schema.py` — shared bounded Schema normalization, validation, and digest semantics.
- `backend/app/assistant/skills/__init__.py`
- `backend/app/assistant/skills/contracts.py` — Agent Skills frontmatter and `mindatlas.yaml` v1.
- `backend/app/assistant/skills/models.py` — package, alias, version, resource, binding, and Main Agent ORM models.
- `backend/app/assistant/skills/package_io.py` — safe parse, ZIP import, deterministic export.
- `backend/app/assistant/skills/resolution.py` — published Workflow/Agent and Tool revision resolution.
- `backend/app/assistant_config/workflow_references.py` — pure, version-aware Workflow/Agent/Tool/model reference walkers extracted from the legacy service and reused by both legacy protection and v2 closure resolution.
- `backend/app/assistant/skills/legacy_adapter.py` — legacy shadow synchronization and alias resolution.
- `backend/app/assistant/skills/schemas.py` — API request/response schemas.
- `backend/app/assistant/skills/service.py` — append-only package/Profile operations.
- `backend/app/assistant/skills/router.py` — new isolated routes.
- `backend/alembic/versions/<generated_revision>_add_agent_skill_contract_tables.py` — generated by Alembic from the sole execution-time head; never substitute an existing/provisional ID.
- `backend/tests/fixtures/agent_skills/valid-weekly-review/SKILL.md`
- `backend/tests/fixtures/agent_skills/valid-weekly-review/mindatlas.yaml`
- `backend/tests/fixtures/agent_skills/valid-weekly-review/references/guide.md`
- `backend/tests/test_agent_skill_package_io.py`
- `backend/tests/test_agent_skill_models.py`
- `backend/tests/test_agent_skill_service.py`
- `backend/tests/test_agent_skill_publish.py`
- `backend/tests/test_agent_skill_dependency_closure.py`
- `backend/tests/test_main_agent_profile_service.py`
- `backend/tests/test_agent_skill_legacy_adapter.py`
- `backend/tests/test_agent_skill_import_export.py`
- `backend/tests/test_agent_skill_api.py`
- `backend/tests/test_main_agent_profile_api.py`
- `backend/tests/test_agent_skill_spec_conformance.py`
- `backend/tests/test_agent_skill_postgres_migration.py`
- `backend/tests/test_resolved_run_manifest.py`
- `backend/tests/test_binding_json_schema.py`
- `backend/tests/agent_skill_test_support.py` — explicit test-only Credential/Model/component-binding factory; never production bootstrap seed data.

### Modify

- `backend/requirements.txt` — pin direct PyYAML and jsonschema dependencies.
- `backend/app/config.py` — `app_build_revision` sourced from `APP_BUILD_REVISION`.
- `backend/.env.example` — document build revision.
- `deploy/.env.example` — document deployment override.
- `deploy/docker-compose.yml` — pass build revision to API.
- `backend/Dockerfile` — accept/stamp build revision.
- `backend/app/assistant_config/models.py` — `AssistantTool.config_revision` only.
- `backend/app/ai_registry/models.py` — add `runtime_revision` to `AiModel` and `AiCredential`.
- `backend/app/ai_registry/service.py` — exact revision bump rules for model/credential execution changes.
- `backend/app/assistant_config/service.py` — increment Tool config revision; protect referenced target versions/deletes; invoke shadow sync after legacy catalog warm.
- `backend/app/assistant_config/bootstrap.py` — keep ordering explicit and idempotent.
- `backend/app/main.py` — register the new router.
- `backend/alembic/env.py` — register new ORM metadata.
- `backend/tests/_db.py` — import new models for SQLite test metadata.
- `backend/tests/_bootstrap.py` — clear new loader caches if introduced.
- `backend/tests/test_assistant_config_service.py` — Tool revision and legacy behavior regression.
- `backend/tests/test_assistant_config_service_more.py` — target/version reference protection regression.
- `backend/tests/test_ai_registry_service.py` — model/credential revision and reference-protection regression.
- `.github/workflows/ci.yml` — run Alembic against PostgreSQL before tests.

No frontend file changes.

### Mandatory execution checkpoints for large tasks

The numbered Task order stays stable for cross-plan references, but Tasks 3/5/7/10 are not single implementation batches. Execute and review these bounded checkpoints sequentially; each starts with its named failing tests and must be green before the next begins:

- **3A:** ORM enums/aggregates/versions/blob rows; **3B:** revision columns and generated migration DDL; **3C:** ownership/immutability/revision/downgrade triggers plus PostgreSQL smoke.
- **5A:** project-owned Tool Schema/build contract vectors; **5B:** shared Workflow/Agent/model walkers and parity; **5C:** target/closure resolution DTOs; **5D:** Tool/Model/Credential revision rules; **5E:** protected-history helpers and V1→V2 warm regression; **5F:** atomic publish/reconstruction/drift verification.
- **7A:** deterministic rendering and disabled aggregate/draft materialization; **7B:** resolvable publication plus unresolved diagnostics/reconciliation; **7C:** lifecycle hooks and legacy-runtime invariance.
- **10A:** clean dependency/conformance gates; **10B:** PostgreSQL preservation, concurrency, downgrade, blob, and protected-history cases; **10C:** focused/legacy/full suites and hard-boundary review.

Do not parallelize checkpoints that edit the same service/migration file. Record the focused command/result at each checkpoint; the Task-level commits below remain the integration boundaries unless the implementer deliberately makes smaller scoped commits.

---

## Task 0: Establish an Isolated, Reproducible Baseline

**Files:** No product file changes.

**Interfaces:**

- Consumes: the approved overall design and current repository.
- Produces: one isolated branch/worktree, one real Alembic parent, and recorded dependency/test baselines.

- [ ] **Step 1: Inspect repository state**

~~~bash
git status --short
git branch --show-current
git rev-parse --short HEAD
git rev-parse --git-dir
git rev-parse --git-common-dir
~~~

Record every pre-existing dirty path. Do not stage or alter it accidentally.

- [ ] **Step 2: Read authoritative planning constraints**

~~~bash
sed -n '1,1400p' docs/superpowers/specs/2026-07-13-mindatlas-universal-skill-agent-loop-design.md
if test -f openspec/AGENTS.md; then sed -n '1,400p' openspec/AGENTS.md; fi
~~~

Stop if a newly added authoritative spec conflicts with this plan.

- [ ] **Step 3: Create implementation isolation**

Use the environment’s worktree/branch workflow and create `feature/agent-skills-contracts`. Preserve the user’s untracked `docs/superpowers/` files. If this plan is still untracked, a new worktree will not contain it: either create the branch in the current checkout or first make an explicitly scoped documentation commit with user approval. Do not silently copy, delete, or absorb unrelated untracked files.

- [ ] **Step 4: Record the real migration parent and dependencies**

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic history --verbose
.venv/bin/python - <<'PY'
import importlib.metadata
for name in ("pydantic", "SQLAlchemy", "alembic", "PyYAML", "jsonschema", "langgraph"):
    try:
        print(name, importlib.metadata.version(name))
    except importlib.metadata.PackageNotFoundError:
        print(name, "NOT_INSTALLED")
PY
~~~

Expected at plan-writing time: one head `a7b8c9d0e1f2`. Record it as `PRE_PLAN01_HEAD`. Do not choose the Plan 01 revision yet. Confirm `b8c9d0e1f2a3` already exists and is therefore unavailable.

- [ ] **Step 5: Run the focused baseline**

~~~bash
cd ..
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py \
  backend/tests/test_assistant_skill_converters.py \
  backend/tests/test_system_skill_workflow_refs.py \
  backend/tests/test_system_agent_baseline_restore.py -q
~~~

Expected at plan-writing time: `79 passed, 1 warning, 12 subtests passed`. Any new failure must be classified before implementation.

- [ ] **Step 6: Record publication-readiness and cross-plan drift**

Before implementation, record without mutating production-like data:

- whether each system Agent/Workflow reachable from a legacy Skill has an owned published version;
- whether every reachable `model_source=default` component binding currently resolves to a concrete `AiModel` and `AiCredential`;
- current `APP_ENV` and effective `APP_BUILD_REVISION`, redacting credentials;
- the exact helper names/coverage of `_collect_workflow_call_references_from_input`, `_collect_workflow_tool_names`, `_collect_workflow_custom_model_ids`, `_get_*_protected_version_ids`, and `_keep_only_*_version`;
- the Plan 02 closure constants. Plan 02 must import the Plan 01 names `MAX_CAPABILITY_CLOSURE_DEPTH=16`, `MAX_CAPABILITY_CLOSURE_REFS=256`, and `MAX_CAPABILITY_CLASSIFIED_NODES=4096`; do not leave a second `*_DEPENDENCY_REFS=512` contract.

An unbound default model or a non-immutable build revision is an expected **shadow publication diagnostic**, not permission to seed fake production credentials or weaken frozen references. It does not block Tasks 1–6, but it must be represented in Task 7 tests and deployment reports.

- [ ] **Step 7: Commit nothing**

Task 0 is evidence only.

---

## Task 1: Add Canonical Digest and Frozen Runtime Contracts

**Files:**

- Modify: `backend/requirements.txt` — add direct `jsonschema` dependency only.
- Create: `backend/app/assistant/domain/__init__.py`
- Create: `backend/app/assistant/domain/digests.py`
- Create: `backend/app/assistant/domain/contracts.py`
- Create: `backend/app/assistant/domain/json_schema.py`
- Test: `backend/tests/test_resolved_run_manifest.py`
- Test: `backend/tests/test_binding_json_schema.py`

**Interfaces:**

- Consumes: normalized immutable references.
- Produces: deterministic SHA-256 values, one canonical Schema normalizer, exact Provider/Model/Capability refs, and frozen append-only `ResolvedRunManifestRevision` values.
- Does not read the database or resolve “latest”.

- [ ] **Step 1: Write failing digest tests**

Cover:

- Dictionary key order does not change canonical JSON or its digest.
- Tuple/list ordering does change the digest.
- Non-ASCII strings are preserved as UTF-8.
- NaN, positive infinity, and negative infinity are rejected.
- Bytes, sets, datetime objects, and arbitrary Pydantic objects are rejected unless the caller converts them to the declared JSON contract.

~~~python
def test_sha256_canonical_json_is_stable_across_mapping_order() -> None:
    left = {"name": "周度回顾", "version": 1}
    right = {"version": 1, "name": "周度回顾"}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_canonical_json(left) == sha256_canonical_json(right)
~~~

- [ ] **Step 2: Run the tests and confirm the intended failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_resolved_run_manifest.py -q
~~~

Expected: import failure because `app.assistant.domain` does not exist.

- [ ] **Step 3: Implement the minimum digest helpers**

Expose:

~~~python
def canonical_json_bytes(value: JsonValue) -> bytes: ...
def sha256_bytes(value: bytes) -> str: ...
def sha256_canonical_json(value: JsonValue) -> str: ...
~~~

Use `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`. Keep the accepted input contract deliberately narrow.

- [ ] **Step 4: Write failing frozen-contract tests**

Construct minimal Tool, Workflow, Agent, Skill, Provider, Model, and Main Agent references and cover:

- Every contract rejects attribute assignment.
- Base manifest revision must equal `1` and have no parent.
- A child revision must be exactly parent revision plus one.
- Parent digest mismatch is rejected.
- Activating a second Skill appends references and does not replace existing references.
- Re-activating the same Skill Version returns the same semantic revision rather than duplicating it.
- Activating the same canonical Skill name at a different version raises `SkillVersionConflictError`.
- Capability keys are unique and a duplicate with different resolution data is rejected.
- Reordering inputs cannot create nondeterministic manifest output; serialization order is canonical Skill name and Capability key.
- Calculated `manifest_digest` changes if any frozen reference or policy digest changes.
- `schema_version=1` and an explicit empty `provider_aliases` tuple participate in the base fixed vector.
- Exact Provider/Model ref digests change for any provider/model/credential runtime revision, config digest, adapter/protocol/build revision, or probe-evidence change.
- Provider/Model refs reject unknown fields and never accept credential values, headers, or raw endpoint credentials.
- `ResolvedMainAgentRef` contains the exact Profile/version IDs, key, sequence, and `content_digest`; changing any field changes the Manifest digest.
- A later `ResolvedProviderAliasRef` value can be represented by the Plan 01 class without changing the Manifest schema.

Use UUID constants rather than random UUIDs so failures remain readable.

- [ ] **Step 5: Define frozen contracts and append logic**

At minimum define:

~~~python
class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResolvedCapabilityRef(FrozenContract):
    capability_type: Literal["tool", "workflow", "agent"]
    capability_key: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    input_schema_digest: str
    output_schema_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    binding_contract_digest: str


class ResolvedSkillRef(FrozenContract):
    package_id: UUID
    version_id: UUID
    canonical_name: str
    sequence: int
    content_digest: str
    version_digest: str
    requested_name_normalized: str | None
    resolved_via_alias_id: UUID | None


class ResolvedMainAgentRef(FrozenContract):
    profile_id: UUID
    version_id: UUID
    profile_key: str
    sequence: int
    content_digest: str


class ResolvedRunManifestRevision(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    revision: int
    parent_digest: str | None
    main_agent: ResolvedMainAgentRef
    active_skills: tuple[ResolvedSkillRef, ...]
    capabilities: tuple[ResolvedCapabilityRef, ...]
    provider: ProviderRef | None
    model: ModelRef | None
    provider_aliases: tuple[ResolvedProviderAliasRef, ...] = ()
    effective_policy_digest: str | None
    manifest_digest: str
~~~

Also define and export every literal alias in Locked Decision 9. Tests assert exact values, reject unknown values, and confirm no loop/ledger ORM table is introduced by importing the vocabulary.

Provide pure base/append functions:

~~~python
def create_base_run_manifest(
    *,
    run_id: UUID,
    main_agent: ResolvedMainAgentRef,
    provider: ProviderRef | None,
    model: ModelRef | None,
    effective_policy_digest: str | None,
) -> ResolvedRunManifestRevision: ...


def append_skill_activation(
    current: ResolvedRunManifestRevision,
    *,
    skill: ResolvedSkillRef,
    capabilities: tuple[ResolvedCapabilityRef, ...],
) -> ResolvedRunManifestRevision: ...
~~~

The functions derive `revision`, `parent_digest`, and `manifest_digest` from a serialization payload that excludes `manifest_digest` itself; callers cannot supply those values. An identical activation returns `current` unchanged rather than manufacturing an empty child revision.

Implement Provider/Model/ref digest constructors rather than accepting caller-supplied self-digests. The Manifest payload always contains `schemaVersion` and `providerAliases`, including the empty tuple.

- [ ] **Step 6: Write and implement canonical Schema tests**

In `test_binding_json_schema.py`, cover:

- stable canonical mapping/`required`/type-union normalization;
- `nullable: true` conversion and ambiguous-nullable rejection;
- Draft 2020-12 invalid Schema rejection;
- input object-root enforcement and output any-JSON support;
- local `$defs` refs accepted, remote/missing refs rejected without network access;
- byte/depth bounds, non-JSON values, NaN/Infinity, and duplicate `required` members;
- recursive secret-bearing annotation rejection, bounded `enum`/`const`, and sensitive property-name vectors;
- project-owned Tool parameter conversion fixed vectors and unknown/lossy parameter-type rejection;
- system Tool golden Schemas/digests remain stable independently of LangChain/Pydantic Schema formatting, while a compatibility assertion detects a real required-field/type mismatch;
- publish-time and simulated Plan 02 runtime calls produce byte-identical normalized Schema/digest fixed vectors.

Implement only the helpers locked in Decision 3. Do not compile a runtime validator cache or project runtime error details here.

Add `jsonschema>=4.23,<5.0` to `backend/requirements.txt` before implementing the production helper. Verify the import in a clean Python 3.11 environment in Task 10; the current local transitive install is not evidence.

- [ ] **Step 7: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_binding_json_schema.py -q
~~~

- [ ] **Step 8: Commit**

~~~bash
git add backend/requirements.txt backend/app/assistant/domain \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_binding_json_schema.py
git commit -m "feat(ai): add frozen run manifest contracts"
~~~

---

## Task 2: Validate and Parse Agent Skills Packages Safely

**Files:**

- Modify: `backend/requirements.txt`
- Create: `backend/app/assistant/skills/__init__.py`
- Create: `backend/app/assistant/skills/contracts.py`
- Create: `backend/app/assistant/skills/package_io.py`
- Create: `backend/tests/fixtures/agent_skills/valid-weekly-review/SKILL.md`
- Create: `backend/tests/fixtures/agent_skills/valid-weekly-review/mindatlas.yaml`
- Create: `backend/tests/fixtures/agent_skills/valid-weekly-review/references/guide.md`
- Create: `backend/tests/test_agent_skill_package_io.py`

**Interfaces:**

- Consumes: bounded bytes from a ZIP upload or an in-memory file map.
- Produces: `ParsedSkillPackage` with validated metadata, exact bytes, classified resources, and deterministic digests.
- Does not write files to the host, persist rows, resolve Capabilities, or execute scripts.

- [ ] **Step 1: Pin the parser dependency directly**

Add one explicit line without changing unrelated dependency pins. `jsonschema` was already added in Task 1 and must not be duplicated:

~~~text
PyYAML>=6.0,<7.0
~~~

- [ ] **Step 2: Write failing frontmatter and manifest tests**

Cover the locked `SKILL.md` and `mindatlas.yaml` contracts, including:

- Minimal standard package passes.
- Frontmatter is the first document content and has exactly one YAML mapping.
- Required `name` and `description` rules.
- `name` rejects leading/trailing/consecutive hyphens and must match its parent directory exactly.
- Optional standard fields survive parsing; `compatibility`, when present, is non-empty and at most 500 characters.
- `allowed-tools` is exactly one space-delimited string, not a YAML list.
- Unknown `SKILL.md` frontmatter fields fail instead of leaking MindAtlas fields into the portable contract.
- `allowed-tools` is retained as interoperability metadata and never creates bindings.
- `mindatlas.yaml` missing means valid defaults.
- Manifest version must be exactly `1`.
- Extra extension keys fail.
- Capability pair uniqueness, routing limits, metadata limits, and reserved `general_chat` alias.
- `SkillConflictRuleV1` exact kinds/field combinations, target/group normalization, unknown fields, self-target, duplicates, and local contradiction rejection; draft parsing retains canonicalizable target identity while publication owns cross-package resolution.
- Terminal-policy booleans and all parser-visible unsatisfiable cases, including instruction-only `requires_terminal_output=true` with `terminal_text_allowed=false`.
- Both `general_chat` and `general-chat` are reserved across canonical/alias input.
- `normalize_skill_lookup_name` fixed vectors cover NFKC-equivalent Unicode, casefold expansion, preserved internal whitespace, controls/NUL/slash/backslash rejection, both reserved names, and the pre/post UTF-8/scalar limits. Task 2 implements this helper in `contracts.py`; Tasks 4/7/8 consume it rather than defining another normalizer.
- Agent declarations require explicit binding input/output Schemas; Tool/Workflow contracts are optional assertions.
- Contract completion defaults are conservative and Schema normalization uses Task 1 fixed vectors.
- Provider alias hints are bounded, refer only to declared Capability keys, and do not become resolved aliases in Plan 01.
- YAML aliases, merge keys, duplicate mapping keys, custom tags, non-string keys, and multiple documents fail in both YAML inputs.

Implement a strict SafeLoader subclass; do not call unrestricted `yaml.load`.

- [ ] **Step 3: Write failing ZIP safety tests**

Generate malicious archives in test memory rather than checking binary fixtures into Git. Cover:

- More than one top-level directory.
- Root name differs from canonical `name`.
- Absolute, traversal, backslash, Windows drive, NUL, overlong, and normalized-duplicate paths.
- Symlink/device/FIFO mode bits.
- Encrypted flag and unsupported compression.
- Entry count, per-file size, total declared size, total streamed size, and compressed upload limits.
- Invalid UTF-8.
- Packaging noise.
- Broken local Markdown links, including URL-decoded traversal.
- Remote links and same-document anchors remain allowed.
- Script mode bits are discarded and the parsed resource has `executable=False`.

- [ ] **Step 4: Run the focused test and confirm failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_package_io.py -q
~~~

- [ ] **Step 5: Implement strict contracts**

Define immutable Pydantic models for:

- `AgentSkillFrontmatter`
- `MindAtlasSkillManifestV1`
- `SkillRoutingContract`
- `SkillPolicyContract`
- `CapabilityDeclaration`
- `ParsedSkillResource`
- `ParsedSkillPackage`

`CapabilityDeclaration` contains an optional frozen `CapabilityBindingContract`; validation requires it for Agent declarations. Keep target resolution data and dependency closure out of this portable draft contract.

Use explicit field validators for every numeric/string/list bound from this plan. Do not make model defaults act as authorization grants.

- [ ] **Step 6: Implement bounded package parsing**

Expose pure entry points:

~~~python
def parse_skill_directory_files(
    files: Mapping[str, bytes],
    *,
    expected_root_name: str | None = None,
) -> ParsedSkillPackage: ...

def parse_skill_zip(
    content: BinaryIO,
    *,
    compressed_size: int,
) -> ParsedSkillPackage: ...
~~~

Implementation requirements:

- Stream each member in bounded chunks and stop immediately after a limit crosses.
- Validate member metadata before reading content.
- Never use `ZipFile.extract` or `extractall`.
- Normalize paths exactly once, retain normalized relative POSIX paths, and reject ambiguity.
- `files` keys are relative to the Skill root (`SKILL.md`, `references/...`) and never contain the top-level directory. ZIP parsing proves exactly one top-level directory, strips it, and passes that name as `expected_root_name`; JSON file-map parsing passes `None`, so validated `SKILL.md.name` becomes the canonical virtual root. When `expected_root_name` is present it must equal the validated canonical name.
- Store `SKILL.md` and `mindatlas.yaml` separately from resources.
- Determine media types deterministically; unknown types use `application/octet-stream`.
- Ignore client media-type claims because none are accepted. ZIP and JSON file-map parsing call the same server detector; extension spoofing, HTML/SVG active content, empty files, and unknown bytes have fixed-vector results.
- Sort resources by normalized path before calculating the index digest.
- Calculate all four digest layers using Task 1 helpers.
- Parse Markdown links conservatively; a link that looks local but cannot be proven safe is rejected.

- [ ] **Step 7: Add happy-path fixture assertions**

Assert the fixture’s exact:

- canonical name and description;
- extension snapshot;
- ordered resource index;
- `skill_md_digest`, `manifest_digest`, `resource_index_digest`, and `content_digest`;
- non-executable resource contract.

Hard-code the expected digests after independently calculating them once. These become cross-version regression vectors.

- [ ] **Step 8: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_package_io.py -q
~~~

- [ ] **Step 9: Commit**

~~~bash
git add backend/requirements.txt backend/app/assistant/skills \
  backend/tests/fixtures/agent_skills backend/tests/test_agent_skill_package_io.py
git commit -m "feat(ai): parse portable agent skill packages"
~~~

---

## Task 3: Add Append-Only Skill and Main Agent Persistence

**Files:**

- Create: `backend/app/assistant/skills/models.py`
- Modify: `backend/app/assistant_config/models.py`
- Modify: `backend/app/ai_registry/models.py`
- Modify: `backend/alembic/env.py`
- Create: one Alembic-generated `backend/alembic/versions/<generated_revision>_add_agent_skill_contract_tables.py`
- Modify: `backend/tests/_db.py`
- Create: `backend/tests/test_agent_skill_models.py`

**Interfaces:**

- Consumes: parsed packages and profile snapshots in later tasks.
- Produces: normalized package/profile aggregate roots with immutable child versions.
- Does not change legacy `assistant_skill`.

- [ ] **Step 1: Reconfirm the real Alembic head**

~~~bash
cd backend
.venv/bin/alembic heads
cd ..
~~~

Require exactly one head and record it as `PRE_PLAN01_HEAD`. Then generate, do not hand-name, the revision:

~~~bash
cd backend
.venv/bin/alembic revision -m "add agent skill contract tables"
cd ..
~~~

Open the generated file and verify:

- its `revision` does not occur in any other migration;
- its `down_revision` equals the recorded sole `PRE_PLAN01_HEAD`;
- its filename/revision is not the occupied `b8c9d0e1f2a3`;
- `alembic heads` now reports exactly the one generated revision.

If another migration landed between recording and generation, delete only the still-empty generated file, re-read the sole head, and regenerate. Never edit a generated revision into an ID chosen from this document.

- [ ] **Step 2: Write failing ORM contract tests**

Cover:

- All nine v2 tables are present in test metadata, including `assistant_skill_resource_blob` and `assistant_skill_capability_dependency`.
- Package canonical name and alias uniqueness.
- Package versions are unique by package/sequence and package/content-digest/version-source according to the schema section.
- Resource paths and binding keys are unique inside a version.
- Equal resource bytes reuse one immutable content-addressed blob, metadata must match the blob, and a digest/size collision with different bytes fails closed.
- Dependency ordinals/paths are unique inside a binding and typed dependency FKs obey their exact shape.
- Resolved bindings expose independent input/output/resolution/closure/contract digests and a versioned lossless snapshot; unresolved drafts cannot masquerade as complete.
- Every child row belongs to exactly one immutable version.
- Main Agent Profile has at most one default profile.
- Main Agent Profile version sequence uniqueness.
- Published version pointers must reference versions owned by the same aggregate. Enforce this in service code when a portable cross-row database constraint would be fragile.
- `AssistantTool.config_revision` defaults to `1` and is non-null.
- `AiModel.runtime_revision` and `AiCredential.runtime_revision` default to `1` and are non-null.
- SQLite tests do not silently omit PostgreSQL-specific constraints; mark database-only cases for the migration test in Task 10.

- [ ] **Step 3: Define SQLAlchemy models**

Create exactly the tables locked in the schema section:

- `assistant_skill_package`
- `assistant_skill_package_alias`
- `assistant_skill_version`
- `assistant_skill_resource_blob`
- `assistant_skill_version_resource`
- `assistant_skill_capability_binding`
- `assistant_skill_capability_dependency`
- `assistant_main_agent_profile`
- `assistant_main_agent_profile_version`

Use the repository’s `Column(...)` ORM style, UUID primary keys, timezone-aware timestamps, explicit indexes, named constraints, and explicit relationships. Aggregate roots may use `TimestampMixin`; immutable version/resource/binding/alias rows define `created_at` only and must not acquire `updated_at` accidentally. No relationship may expose an ORM cascade that makes immutable history disappear.

Modify only `AssistantTool` in the legacy model:

~~~python
config_revision = Column(
    Integer,
    nullable=False,
    default=1,
    server_default=text("1"),
)
~~~

Modify only execution-revision fields in `app.ai_registry.models`:

~~~python
runtime_revision = Column(
    Integer,
    nullable=False,
    default=1,
    server_default=text("1"),
)
~~~

- [ ] **Step 4: Register metadata imports**

Import the v2 models in both:

- `backend/alembic/env.py` for migration/autogenerate awareness.
- `backend/tests/_db.py` before `Base.metadata.create_all`.

Avoid wildcard imports and import-time service side effects.

- [ ] **Step 5: Write the explicit migration**

The migration must:

- Add `assistant_tool.config_revision` with a safe server default.
- Add `ai_model.runtime_revision` and `ai_credential.runtime_revision` with safe server defaults.
- Create tables and indexes in dependency order.
- Add named checks for positive sequence/size/revision values, 64-character lowercase hex digests, every locked enum value, `catalog_enabled=false` and `runtime_enabled=false` in this plan, resolved/unresolved binding nullability, exact typed target/dependency FK shapes, and paired output Schema body/digest presence inside snapshots. Plan 04 must explicitly replace the disabled-only checks before enabling runtime use.
- Use PostgreSQL-compatible partial unique index semantics for one default Main Agent Profile.
- Create foreign keys for package/profile published pointers after the corresponding version tables exist.
- Add deferred ownership/source guards for package/Profile draft/published pointers and every publish `source_draft_version_id`: the referenced version must belong to the same aggregate, Draft pointers/source refs must name `save`, and Published pointers must name `publish`.
- Add a deferred version-resource/blob guard: each reference row’s `sha256`/`byte_size` must equal its blob and each blob must be referenced by commit; application code additionally verifies exact bytes before reuse.
- Add one PostgreSQL trigger function and per-table triggers that reject `UPDATE` and `DELETE` for Skill aliases, Skill versions, resource blobs, version resources, bindings, and Main Agent Profile versions.
- Include immutable dependency rows in the update/delete rejection triggers.
- Add a deferred PostgreSQL constraint trigger that, at transaction commit, checks the parent snapshot’s closure index has one matching dependency row per `(ordinal,path,dependencyDigest)` and no extras, and that indexed digest fields equal their snapshot fields. Cryptographic recomputation remains in application fixed-vector tests; the database trigger enforces relational completeness without requiring `pgcrypto`.
- Add revision guard triggers for remote Tool, `AiModel`, and `AiCredential` execution-sensitive fields. A changed execution payload must advance exactly one revision; a revision-only or skipped/double increment update is rejected.
- Downgrade in exact reverse dependency order.
- Leave all legacy Skill rows and constraints untouched.

Downgrade performs a read-only preflight before dropping any trigger/table. It refuses when any package has `migration_state IN ('native','cutover')`, any package version has `origin IN ('api','import')`, any Profile has `migration_state IN ('native','cutover')`, or any Profile version has non-derived administrator origin. It raises `RuntimeError("MINDATLAS_PLAN01_DOWNGRADE_BLOCKED_NATIVE_DATA: export or back up native Skill/Main Agent history and remove it with an explicitly audited procedure before downgrade")`. Pure derived `shadow` packages and `bootstrap/shadow` Profile history are replayable and may be discarded. Tests exercise each predicate separately and prove refusal occurs before the first destructive DDL statement. Do not rely on generated Alembic output without reviewing every type, constraint name, trigger, guard, and downgrade.

- [ ] **Step 6: Run model tests**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_models.py -q
~~~

- [ ] **Step 7: Run migration metadata sanity**

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic history -r -3:current
cd ..
~~~

Expected: one head only.

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/skills/models.py \
  backend/app/assistant_config/models.py backend/app/ai_registry/models.py \
  backend/alembic/env.py \
  backend/alembic/versions backend/tests/_db.py \
  backend/tests/test_agent_skill_models.py
git commit -m "feat(ai): persist immutable skill package versions"
~~~

---

## Task 4: Implement Append-Only Skill Package Service

**Files:**

- Create: `backend/app/assistant/skills/schemas.py`
- Create: `backend/app/assistant/skills/service.py`
- Create: `backend/tests/test_agent_skill_service.py`

**Interfaces:**

- Consumes: `ParsedSkillPackage` and optional current-caller audit metadata.
- Produces: aggregate summaries and immutable version records.
- Does not publish executable catalog entries or expose resource bytes in list responses.

- [ ] **Step 1: Write failing aggregate lifecycle tests**

Cover:

- Creating a native package reserves canonical name and aliases atomically.
- Canonical names and aliases share one collision namespace.
- A canonical name cannot later be changed.
- Alias additions are normalized, unique, and append-only in this plan.
- NFKC/casefold aliases collide deterministically, and both Main Agent reserved names are unavailable.
- First save creates draft sequence `1`.
- Saving identical draft content returns the existing draft version.
- Saving identical content atomically points `draft_version_id` at that exact existing owned save row, even when it is older than the current pointer; the response and pointer agree.
- Saving changed content appends the next sequence.
- Draft save cannot mutate or delete older versions/resources/bindings.
- Package list/get returns current draft and published summaries without resource bytes.
- Version detail returns resource metadata, never bytes by default.
- Legacy shadow state cannot be assigned through the native create method.
- Package remains `catalog_enabled=false`.
- Concurrent saves either serialize correctly or surface a retryable version conflict; no duplicate sequence may commit.
- Resource bytes deduplicate through `assistant_skill_resource_blob`; aggregate-locked enforcement rejects a package above 256 MiB of distinct referenced blob bytes and a failed save leaves no unreferenced blob.
- Resolving a canonical/legacy alias returns an exact owned published version ref with alias evidence; moving the aggregate published pointer later does not change that returned ref.

- [ ] **Step 2: Run focused tests and confirm failure**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_service.py -q
~~~

- [ ] **Step 3: Define API-safe schemas**

Separate:

- `SkillPackageSummary`
- `SkillPackageDetail`
- `SkillVersionSummary`
- `SkillVersionDetail`
- `SkillResourceMetadata`
- `CreateSkillPackageCommand`
- `SaveSkillDraftCommand`
- `PublishSkillVersionCommand`
- `PublishMainAgentProfileCommand`

Response schemas expose version IDs, digests, state, and target reference metadata. They never expose internal ORM objects or raw resource bytes.

The JSON API request model contains only `skillMd`, optional `mindatlasYaml`, `resources[{path,contentBase64}]`, and `versionName`; `extra="forbid"` rejects `mediaType`, IDs, digests, and publication fields. The router performs bounded base64 decoding into a root-relative `Mapping[str, bytes]` containing `SKILL.md`, optional `mindatlas.yaml`, and the resource paths, calls `parse_skill_directory_files(..., expected_root_name=None)`, then passes the resulting `ParsedSkillPackage` into the create/save command. Both publish request models contain only `draftVersionId`, mapped to `draft_version_id`; the service never resolves latest implicitly.

- [ ] **Step 4: Implement transactional service methods**

Provide:

~~~python
class AgentSkillService:
    def create_native_package(...): ...
    def save_draft(...): ...
    def list_packages(...): ...
    def get_package(...): ...
    def list_versions(...): ...
    def get_version(...): ...
    def get_resource_bytes(...): ...
    def resolve_published_alias(...): ...
~~~

Rules:

- Flush package/alias reservations before inserting content.
- Lock the package aggregate with `SELECT ... FOR UPDATE`, derive sequence inside that transaction, and translate unique violations into `4099x` domain conflicts. SQLite-only tests are not concurrency proof; add the two-session race to Task 10 PostgreSQL coverage.
- Insert/reuse resource blobs, version-resource references, and unresolved declarations in deterministic order inside the same transaction. Verify exact blob bytes on reuse.
- Never update a version, resource, or binding row after insertion.
- Resource byte retrieval is a separate authorized service method for later `skill.read_resource`; it is not included in ordinary serialization.
- Keep imported/origin metadata informational and exclude it from `content_digest`.
- `resolve_published_alias` verifies alias -> package -> explicit published version ownership/source and builds the frozen ref in one transaction. It never returns only a package key or follows a pointer after returning.
- Any administrator/native edit of a Skill Package in `shadow` first advances it to `native`; automatic legacy synchronization then stops. Package state advances `shadow -> native -> cutover`, never backwards. Separately, a Main Agent Profile edit advances `bootstrap|shadow -> native -> cutover`. Do not add `bootstrap` to the package enum.

- [ ] **Step 5: Add immutability tripwire tests**

Assert no public service update/delete method exists for immutable rows. SQLite service tests must show normal operations never mutate history; Task 10 must issue direct PostgreSQL `UPDATE`/`DELETE` statements and assert the migration triggers reject them.

- [ ] **Step 6: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_service.py -q
~~~

- [ ] **Step 7: Commit**

~~~bash
git add backend/app/assistant/skills/schemas.py \
  backend/app/assistant/skills/service.py \
  backend/tests/test_agent_skill_service.py
git commit -m "feat(ai): add append-only skill package service"
~~~

---

## Task 5: Freeze Published Capability Contracts, Dependency Closure, and Execution Revisions

**Files:**

- Create: `backend/app/assistant/skills/resolution.py`
- Create: `backend/app/assistant_config/workflow_references.py`
- Modify: `backend/app/assistant/skills/service.py`
- Modify: `backend/app/assistant_config/service.py`
- Modify: `backend/app/ai_registry/service.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `deploy/.env.example`
- Modify: `deploy/docker-compose.yml`
- Modify: `backend/Dockerfile`
- Modify: `backend/tests/test_assistant_config_service.py`
- Modify: `backend/tests/test_assistant_config_service_more.py`
- Modify: `backend/tests/test_ai_registry_service.py`
- Create: `backend/tests/test_agent_skill_publish.py`
- Create: `backend/tests/test_agent_skill_dependency_closure.py`
- Create: `backend/tests/agent_skill_test_support.py`

**Interfaces:**

- Consumes: one immutable draft plus current published Workflow/Agent versions and current Tool execution configuration.
- Produces: a distinct immutable publish version whose bindings contain lossless callable surfaces, exact target references, and exact transitive Tool/Workflow/Agent/Model dependency closures.
- Does not call any Capability.

- [ ] **Step 1: Write failing resolution tests**

Cover all target types:

- Missing target returns a structured `4229x` publish error.
- Workflow without `published_version_id` cannot resolve.
- Workflow resolution reads `published_version.snapshot`, never draft `graph_snapshot`.
- Agent without `published_version_id` cannot resolve.
- Agent resolution queries `AssistantAgentProfileVersion` with both published ID and owner ID, then reads that version snapshot; no aggregate `published_version` relationship exists.
- System Tool resolution freezes stable registry identity, project-owned input/output Schemas generated from `SystemToolFullDefinition.input_params`/`output_params`, `system_tool_contract_set_digest`, and `APP_BUILD_REVISION`. Raw `SystemToolFullDefinition.json_schema`/LangChain output is only checked for compatibility and is never the persisted or digested source. It does not invent policy metadata that the current registry does not have.
- Custom remote Tool resolution freezes input Schema from `input_params`, the explicitly declared binding-owned output Schema, non-secret execution structure, and `config_revision`; publication fails if the output contract is absent. OpenClaw catalog output Schema is never consulted.
- Embedded remote Tool closure entries use the current raw-string return Schema and remain distinct from top-level remote binding contracts.
- Workflow resolution derives both Schemas with the existing `workflow_contract_from_input` from the exact published snapshot, then passes them through the shared Plan 01 normalizer.
- Agent resolution requires its Skill declaration’s explicit contract and freezes that normalized body. Missing contract fails before target resolution; an OpenClaw catalog Schema is never read.
- No snapshot or error detail contains credential values.
- Duplicate Capability keys with conflicting targets are rejected.
- Binding resolution is sorted and deterministic.
- Every stored binding snapshot reconstructs byte-for-byte from its row plus owned dependency rows; recomputation catches a changed/missing Schema body, digest, completion value, execution revision, dependency row, or closure order.
- Schema defaults/examples and secret-like enum/const values fail safely; no snapshot contains a remote header value, endpoint query/fragment/userinfo, raw request template, credential-derived default, or secret hash.

Monkeypatch `AssistantWorkflow.graph_snapshot` to raise if accessed. This is the regression tripwire for draft leakage.

- [ ] **Step 2: Write failing dependency-closure tests**

Cover:

- exact Agent Tool list freezes every code-native/remote Tool dependency and current Tool config/build revision;
- exact Workflow traversal freezes Tool nodes, Agent-node Tools, and recursively pinned `workflow_call` versions;
- a default model binding resolves immediately to exact `AiModel`/`AiCredential` IDs/revisions/config digests for every reachable LLM/embedding/KB dependency;
- a custom model ID is owner/type checked and frozen directly;
- changing `AiComponentBinding` after publication cannot alter the closure;
- missing/default-unbound/custom-invalid model, dynamic Tool name, unpinned/latest Workflow call, cycle, depth/ref/node overflow fail publication;
- a reachable environment-only model/credential source without revision evidence fails publication without persisting or hashing its secret;
- two structural paths to the same exact target remain two auditable closure items with the same ref digest; one path with conflicting refs fails;
- closure ordering and digest are stable across database iteration order;
- remote Tool/model/credential drift is detected without reading or hashing an API key;
- target version, Tool, model, and credential deletion is blocked by typed FKs and translated to a safe reference conflict.

The test fixture must include an Agent Version using `model_source=default`, another using `custom`, a Workflow with at least one nested Workflow and model-using node, and a remote Tool credential rotation.

Provide `backend/tests/agent_skill_test_support.py` with one explicit factory that creates an `AiCredential`, `AiModel`, and concrete `AiComponentBinding` for the default-model path. Tests that claim successful publication must opt into this helper; unbound-default tests must not inherit hidden global seed data. Production bootstrap never calls this test helper and never creates fake credentials.

- [ ] **Step 3: Write failing publish tests**

Cover:

- Publishing always creates a new `version_source=publish` row.
- The source draft remains unchanged.
- Publish row contains copied package bytes/digests plus resolved bindings.
- Aggregate `published_version_id` points to the new owned version.
- Publishing identical content twice creates two auditable publish sequences.
- A changed target revision changes `binding_set_digest` and `version_digest` even when portable `content_digest` is unchanged.
- A changed binding Schema/completion/dependency/model/credential revision changes `binding_contract_digest`, `binding_set_digest`, and `version_digest` while leaving `content_digest` unchanged.
- Conflict targets resolve to canonical package identities at publication; unknown targets, alias ambiguity, self-target, duplicates, and `excludes`/`requires` contradictions fail without creating a partial publish row.
- A terminal-required policy publishes only when text is allowed or an exact resolved binding has `completion.terminal_output=true`; instruction-only/text-forbidden, no-terminal-binding, or capability-only `max_skill_calls=0` cases fail with stable `4229x` errors.
- Published `source_draft_version_id` identifies the exact owned draft; a draft from another package is rejected.
- Package remains disabled for runtime catalog use.

- [ ] **Step 4: Add build-revision configuration**

Add the project-style field:

~~~python
app_build_revision: str = Field(
    default="development",
    alias="APP_BUILD_REVISION",
)
~~~

Propagate the variable through the local and deploy examples, Docker build argument, image environment, and Docker Compose. In production it should be an immutable image/git revision, not a timestamp generated at process start.

Permit the literal `development` only when `APP_ENV` is development/test. Publishing any code-native Tool/closure in staging/production fails with `42293` when the build revision is blank, `development`, `unknown`, or otherwise not deployment-immutable. The executable revision payload includes both `APP_BUILD_REVISION` and `system_tool_contract_set_digest`, so dependency/registry contract changes cannot hide behind an unchanged build string. Tests set a fixed revision such as `test-build-c25d03f`; CI image smoke stamps the checked-out Git SHA and checks the golden system Tool contract set.

- [ ] **Step 5: Implement resolver snapshots and closure traversal**

First extract the version-aware logic behind the existing `_collect_workflow_call_references_from_input`, `_collect_workflow_tool_names`, `_collect_workflow_custom_model_ids`, and related Agent-node traversal into pure functions in `assistant_config/workflow_references.py`. Keep the existing service wrappers for compatibility, and make both legacy protection and `CapabilityReferenceResolver` call the shared functions. Add parity tests over the current system Workflow fixtures plus nested Workflow/Agent/custom-model cases. A second independently implemented graph walker is not accepted.

Expose:

~~~python
class CapabilityReferenceResolver:
    def resolve_many(
        self,
        declarations: tuple[CapabilityDeclaration, ...],
    ) -> tuple[ResolvedCapabilityBinding, ...]: ...
~~~

Resolution contracts:

- `workflow`: stable identity + target ID + published version ID + published snapshot digest.
- `agent`: stable identity + target ID + published version ID + published snapshot digest.
- code-native `tool`: `system-tool:<name>` identity + null target ID + project-owned input/output Schema digests + `system_tool_contract_set_digest` + `APP_BUILD_REVISION`.
- remote `tool`: stable identity + target ID + current `config_revision` + target-owned input Schema + explicit binding-owned output Schema + secret-free execution snapshot digest.
- `resolution_digest`: digest of the complete normalized target reference contract, including independent Schema digests and executable/config revision.
- `resolution_snapshot`: the exact v1 binding payload locked above, including normalized Schema bodies, completion, execution metadata, closure index, and self-excluding contract digest.
- `assistant_skill_capability_dependency`: one immutable lossless row for each closure item. The parent snapshot’s `(path, dependencyDigest)` list and closure digest must match these rows exactly.

Do not copy Workflow/Agent executable snapshot bodies into bindings; their typed published-version FK remains the executable source. Do persist callable Schema bodies because they are binding-owned. Do not persist credentials, decrypted or encrypted. For remote Tools, omit every configured header value, endpoint userinfo/query/fragment, raw query/body template, and credential-derived default; persist only safe structural metadata such as normalized header names, method/body/auth shape, and revision. Credential/config rotation is detected by revisions. Reject publication if a safe secret-free reference contract cannot be produced.

Closure traversal is a pure preflight over exact snapshots plus typed resolution ports. It performs no model/provider call and no credential decryption. Resolve all declarations and their complete closures before inserting the publish row so a partial graph never persists.

- [ ] **Step 6: Implement Tool/model/credential revision rules**

Increment `AssistantTool.config_revision` only when execution-sensitive remote Tool fields change:

- canonical executable `name` or `kind`;
- input schema;
- endpoint, method, query/body mapping/template, timeout, payload wrapper, or any configured header name/value;
- authentication shape such as type, header name, or scheme;
- credential slot replacement or credential rotation event.

Do not increment for description-only edits or enabled/disabled availability toggles. Current remote Tools have no output-schema or side-effect-classification field; do not fabricate one in Plan 01. If later plans add execution-sensitive fields, they must join this revision comparison.

Use one shared canonical before/after payload helper so create, update, system catalog sync, header replacement, and credential replacement cannot diverge. The helper compares sensitive values in memory but never logs, returns, hashes into a snapshot, or persists an extra copy of them; any change advances exactly once. Preserve the old API response unless exposing `config_revision` is explicitly backward compatible.

Apply the same pattern in `app.ai_registry.service`:

- `AiCredential.runtime_revision`: normalized base URL or API-key slot replacement only;
- `AiModel.runtime_revision`: name or model type only;
- display name and component-binding pointer changes do not advance these revisions;
- lock the row, calculate before/after once, and commit config plus revision atomically.

Keep existing public Tool/Model/Credential response shapes unchanged unless a separately reviewed backward-compatible field addition is required. Internal publication services read revisions from ORM/domain refs.

The database revision guards created in Task 3 must agree with these helpers. Add cross-tests so a field is never classified differently by service and migration trigger.

- [ ] **Step 7: Protect referenced targets and model dependencies**

Extend legacy configuration services so a published Skill binding prevents:

- deleting the referenced Workflow/Agent/remote Tool;
- deleting or trimming the referenced Workflow/Agent published version;
- renaming a referenced remote Tool without first republishing or explicitly resolving the reference conflict;
- reusing a target identity for incompatible semantics.
- deleting a model or credential referenced by any binding dependency, even if no current component binding points to it.

Return a conflict that identifies the referencing Skill Package and Version IDs, without leaking package content.

This protection must be integrated into the existing history-maintenance helpers, not only the public delete endpoints:

- `_get_workflow_protected_version_ids` unions draft/published/pinned/workflow-call/system-baseline IDs with every `resolved_workflow_version_id` referenced by a binding or dependency.
- `_get_agent_protected_version_ids` unions draft/published/system-baseline IDs with every `resolved_agent_version_id` referenced by a binding or dependency.
- `_keep_only_workflow_version` and `_keep_only_agent_version` preserve **all** protected IDs plus the requested keep ID; they must not issue an unrestricted “delete everything except one” statement.
- Restore/reset/system-catalog warm paths call the same protected-set helpers. If a requested trim conflicts, preserve history and return/log `40994` according to whether the path is synchronous admin work or best-effort bootstrap.

Add a warm regression: publish a shadow binding to system Workflow/Agent V1, advance the system definition to V2, run restore/warm and shadow reconciliation, assert V2 becomes current while V1 and its closure remain readable and digest-valid. Also cover a dependency-only V1 reference, which the parent binding table alone would miss.

- [ ] **Step 8: Implement publication transaction**

In `AgentSkillService.publish(...)`:

1. Lock the package aggregate.
2. Load the requested owned draft.
3. Resolve every declaration, binding Schema, and complete exact dependency closure against current published targets/models.
4. Create a distinct immutable publish version.
5. Copy resources and normalized snapshots deterministically.
6. Insert resolved bindings and their dependency rows in canonical order.
7. Re-read/reconstruct every lossless snapshot, then calculate `binding_set_digest` and `version_digest` from binding contract digests.
8. Atomically set `published_version_id`.

Any resolution error rolls back the entire publish.

- [ ] **Step 9: Add future runtime drift verification**

Add a pure check used by later plans:

~~~python
def verify_resolved_binding_is_current(
    binding: AssistantSkillCapabilityBinding,
    current_target: CurrentCapabilityReference,
) -> None: ...
~~~

For a remote Tool it fails closed when `config_revision` differs; for a code-native Tool it fails when `APP_BUILD_REVISION` or either Schema digest differs. For Workflow/Agent it verifies that the referenced owned version still exists and its digest matches, even if the aggregate now points to a newer published version. It also verifies every closure row, Model/Credential runtime revision/config digest, and the reconstructed `binding_contract_digest`. Plan 01 tests this helper but does not invoke it from the old runtime.

- [ ] **Step 10: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_publish.py \
  backend/tests/test_agent_skill_dependency_closure.py \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py \
  backend/tests/test_ai_registry_service.py -q
~~~

- [ ] **Step 11: Commit**

~~~bash
git add backend/app/assistant/skills backend/app/assistant_config/service.py \
  backend/app/assistant_config/workflow_references.py \
  backend/app/ai_registry/service.py \
  backend/app/config.py backend/.env.example deploy/.env.example \
  deploy/docker-compose.yml backend/Dockerfile \
  backend/tests/test_agent_skill_publish.py \
  backend/tests/test_agent_skill_dependency_closure.py \
  backend/tests/agent_skill_test_support.py \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py \
  backend/tests/test_ai_registry_service.py
git commit -m "feat(ai): freeze published skill capability references"
~~~

---

## Task 6: Add Versioned Main Agent Profile Service

**Files:**

- Modify: `backend/app/assistant/skills/schemas.py`
- Modify: `backend/app/assistant/skills/service.py`
- Create: `backend/tests/test_main_agent_profile_service.py`

**Interfaces:**

- Consumes: Main Agent Profile v1 snapshots.
- Produces: one default profile aggregate with append-only draft/publish versions.
- Does not build prompts, resolve providers, or replace `general_chat` in the current runtime.

- [ ] **Step 1: Write failing snapshot validation tests**

Cover:

- `schemaVersion` must equal `1`.
- `basePrompt` is non-empty and bounded.
- Entrypoints are known, unique, and deterministically ordered.
- Model requirements are booleans with no unknown keys.
- Control Capability keys are unique Domain Keys; they are only declarations in this plan.
- `skillCatalogScope.mode` accepts exactly `all_published | allowlist`, enforces the locked `packageIds` rules, and defaults to `all_published`.
- Context/output budget values are positive, bounded, and internally coherent.
- Global safety policy remains deny-by-default.
- Legacy fallback cannot be enabled after side effects.
- Unknown fields fail.

- [ ] **Step 2: Write failing lifecycle tests**

Cover:

- The first bootstrap creates one default profile.
- Re-running bootstrap is idempotent.
- Saving identical drafts reuses the existing draft.
- Saving changed drafts appends a sequence.
- Publishing creates a distinct immutable version and advances only the owned pointer.
- Published Profile version records the exact owned source draft; cross-profile draft IDs are rejected.
- Concurrent attempts cannot create two default profiles.
- Version history cannot be updated or deleted through the service.
- No action here changes old `general_chat` routing or assistant runtime.

- [ ] **Step 3: Implement snapshot schemas**

Define `MainAgentProfileSnapshotV1` and nested frozen models in `schemas.py`. Compute canonical `content_digest` from the normalized JSON snapshot with Task 1 helpers and persist both snapshot and digest. Do not introduce a second `snapshot_digest` name.

- [ ] **Step 4: Implement profile service methods**

Provide:

~~~python
class MainAgentProfileService:
    def ensure_default(...): ...
    def get_default(...): ...
    def save_draft(...): ...
    def list_versions(...): ...
    def publish(...): ...
~~~

Publication must validate the whole snapshot again inside the transaction. A draft that references an undeclared control Capability may be saved for editing, but publication must fail until every required control key can be resolved or is intentionally empty for Plan 01 bootstrap.

- [ ] **Step 5: Seed a conservative default**

The initial `migration_state=bootstrap` default is a replayable disabled configuration record, not an activated runtime. The first administrator-authored save advances the aggregate to `native` before appending its version:

- `supportedEntrypoints=["assistant_chat"]`;
- required tool-calling and streaming flags;
- empty control Capability keys;
- deny-by-default global policy;
- legacy fallback allowed before side effects only.

Task 7 may replace its draft/published baseline with a version derived from `general_chat`, but never mutates prior versions.

- [ ] **Step 6: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_main_agent_profile_service.py -q
~~~

- [ ] **Step 7: Commit**

~~~bash
git add backend/app/assistant/skills/schemas.py \
  backend/app/assistant/skills/service.py \
  backend/tests/test_main_agent_profile_service.py
git commit -m "feat(ai): version main agent profiles"
~~~

---

## Task 7: Mirror Legacy Skills Through a Disabled Shadow Adapter

**Files:**

- Create: `backend/app/assistant/skills/legacy_adapter.py`
- Modify: `backend/app/assistant_config/bootstrap.py`
- Modify: `backend/app/assistant_config/service.py`
- Create: `backend/tests/test_agent_skill_legacy_adapter.py`
- Modify: `backend/tests/test_system_skill_workflow_refs.py`
- Modify: `backend/tests/test_system_agent_baseline_restore.py`

**Interfaces:**

- Consumes: the current legacy `AssistantSkill` system/user catalog after its existing bootstrap/sync completes.
- Produces: disabled, auditable v2 shadow aggregates/drafts, published shadow versions only for resolvable targets, structured diagnostics for unresolved targets/models/build revisions, and a Main Agent Profile baseline.
- Does not route, execute, or expose shadow packages to the current runtime.

- [ ] **Step 1: Write failing canonical-name mapping tests**

The mapper must be deterministic:

- Lowercase ASCII letters/digits are preserved.
- Underscores and whitespace become one hyphen.
- Invalid runs collapse to one hyphen.
- Leading/trailing hyphens are stripped.
- Empty or non-ASCII-only output becomes `legacy-skill-<stable-uuid-prefix>`.
- Names longer than 64 characters are truncated without trailing hyphen.
- Collisions use a stable UUID-derived suffix, not database iteration order.
- The original legacy name is stored as an alias when valid for the alias contract.
- `general_chat` is reserved and never maps to a package.
- Repository regression vectors map `quick_stats -> quick-stats` and `smart_capture -> smart-capture`, retain the underscore names only as aliases, and map `general_chat` only to the Main Agent bridge.

- [ ] **Step 2: Write failing mirror tests**

For each legacy Skill except `general_chat`, assert:

- Exactly one `migration_state=shadow` package exists.
- `catalog_enabled=false`.
- Exactly one reusable shadow draft exists even when its target/default model/build revision is not currently publishable.
- Generated `SKILL.md` is valid standard Agent Skills content.
- Generated `mindatlas.yaml` contains one Capability declaration matching the legacy Workflow or Agent target.
- Generated Agent declarations include an explicit canonical callable contract; generated remote-Tool declarations, if any are introduced later, include an explicit output contract.
- `policy.allowed_side_effects=[]`, because legacy metadata is insufficient to grant effects safely.
- A resolved published shadow version points to the target’s explicit published version.
- Its published binding contains the same lossless Schema/closure/Model-ref contract as a native publish; shadow status never permits mutable/latest resolution.
- Legacy alias and source identity are recorded.
- Re-running sync with unchanged source digest creates nothing.
- A changed legacy source appends a new publish version; it never edits the old one.
- Once an administrator or later migration changes the package from `shadow` to `native`, automatic shadow sync stops touching it.
- A legacy Skill with a missing/unpublished target reports a sync diagnostic and does not produce a falsely published package.
- A legacy Skill whose reachable `model_source=default` has no concrete `AiComponentBinding`/model/credential reports safe diagnostic `unbound_default_model`, leaves `published_version_id=NULL`, and does not seed or guess a production credential.
- A staging/production sync with invalid `APP_BUILD_REVISION` reports safe diagnostic `non_immutable_build_revision`, leaves the aggregate/draft disabled and unpublished, and old runtime bootstrap still succeeds.
- After the missing binding/model/credential/build evidence becomes valid, reconciliation publishes from the existing exact draft without duplicating the aggregate or byte-identical draft.
- Sync report status is exactly `published | unchanged | draft_unresolved | failed`; each diagnostic includes legacy Skill ID, shadow package ID when created, safe source path, and stable reason code, but no endpoint/header/credential values.

- [ ] **Step 3: Write failing `general_chat` bridge tests**

Assert:

- No `general-chat` package is created.
- The default Main Agent Profile records `general_chat` as its migration source.
- Its profile snapshot is based on the explicitly published bound Agent version, never the Agent draft.
- Repeated sync is idempotent.
- A changed published Agent version appends and publishes a new Main Agent Profile version.
- Existing old-runtime `general_chat` row, system catalog status, route, and behavior remain unchanged.

The v1 bridge mapping is explicit:

- Published Agent `system_prompt` → Main Agent `basePrompt`.
- Published Agent `tools` → `controlCapabilityKeys` after validating that each key exists; no tool is granted merely by copying the name.
- Published Agent source IDs, source snapshot digest, `kb_config`, `model_source`, and `model_id` → secret-free `source_ref` audit metadata, not invented Main Agent runtime fields.
- Every other Main Agent field comes from the conservative v1 defaults in Task 6.

For the legacy Agent Capability package itself, render the binding contract explicitly as an object input with required `input` and an object output with required string `text`, matching the example in Locked Decision 1. This is a generated compatibility contract, not an inference Plan 02 may repeat. The general-chat-to-Main-Agent bridge is separate and does not create such a package.

Plan 04 must make an explicit runtime decision for knowledge-context/model behavior. Plan 01 preserves the legacy source evidence but does not pretend those old fields already have new-loop semantics.

- [ ] **Step 4: Implement deterministic legacy rendering**

Expose pure functions:

~~~python
def legacy_skill_canonical_name(skill: AssistantSkill) -> str: ...
def render_legacy_skill_package(skill: AssistantSkill) -> ParsedSkillPackage: ...
def legacy_source_digest(skill: AssistantSkill, target_ref: ...) -> str: ...
~~~

Generated descriptions must say what the legacy Skill does and when it applies. Do not copy database IDs into portable `SKILL.md` or `mindatlas.yaml`; keep source IDs in origin columns.

- [ ] **Step 5: Implement the shadow sync transaction**

Provide:

~~~python
class LegacySkillShadowAdapter:
    def sync_one(self, session: Session, legacy_skill_id: UUID) -> LegacySyncItem: ...
    def sync_all(self, session: Session) -> LegacySyncReport: ...
~~~

The report contains counts for `published`, `unchanged`, `draft_unresolved`, and `failed` plus structured diagnostics with no secret configuration. `draft_unresolved` is a successful disabled-shadow materialization, not a published version and not an old-runtime failure. Each Skill sync is isolated enough that one corrupt legacy row cannot hide diagnostics for all other rows; choose savepoints or a preflight-then-transaction strategy consistent with current service patterns.

- [ ] **Step 6: Hook sync after legacy bootstrap**

Call shadow sync only after:

1. system Tools, Workflows, and Agents are present;
2. legacy system Skills are restored/synchronized;
3. published target versions are available.

Keep bootstrap order explicit. Do not import the adapter from model modules.

After successful legacy Skill create/update/reset, Workflow publish, Agent publish, default component-model binding change, or affected Model/Credential repair, invoke a bounded best-effort `sync_one`/affected-shadow reconciliation. It must run after the legacy transaction has committed, log a structured diagnostic on failure/unresolved publication, and never turn a successful old-runtime operation into an HTTP failure. Do not add another daemon thread for this. Startup `sync_all` is the authoritative repair pass. A deleted legacy Skill leaves immutable shadow history orphaned, disabled, and unsynchronized; it is not cascade-deleted.

- [ ] **Step 7: Prove legacy runtime invariance**

Run old tests plus new tests and assert:

- Current Skill count, system IDs, target bindings, and default/fallback routing are unchanged.
- No new v2 package is read by `SkillRouter` or `Supervisor`.
- `ck_assistant_skill_single_target_binding` remains.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_legacy_adapter.py \
  backend/tests/test_system_skill_workflow_refs.py \
  backend/tests/test_system_agent_baseline_restore.py \
  backend/tests/test_assistant_skill_converters.py -q
~~~

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/skills/legacy_adapter.py \
  backend/app/assistant_config/bootstrap.py \
  backend/app/assistant_config/service.py \
  backend/tests/test_agent_skill_legacy_adapter.py \
  backend/tests/test_system_skill_workflow_refs.py \
  backend/tests/test_system_agent_baseline_restore.py
git commit -m "feat(ai): mirror legacy skills into shadow packages"
~~~

---

## Task 8: Add Deterministic Create-Only Import and Export

**Files:**

- Modify: `backend/app/assistant/skills/package_io.py`
- Modify: `backend/app/assistant/skills/service.py`
- Create: `backend/tests/test_agent_skill_import_export.py`

**Interfaces:**

- Consumes: a bounded Agent Skills ZIP or one owned immutable Skill Version.
- Produces: a newly created disabled native package or a byte-for-byte deterministic ZIP.
- Does not merge, replace, enable, or publish imported content automatically.

- [ ] **Step 1: Write failing import tests**

Cover:

- Valid package creates a native package with first draft only.
- Import never trusts IDs, timestamps, sequence, origin, publication state, or digest values from package content.
- Existing canonical name conflict.
- Existing alias conflict.
- Alias colliding with another canonical name.
- Invalid package leaves no aggregate, alias, resource, or version residue.
- Imported package remains unpublished and `catalog_enabled=false`.
- Re-upload of the same bytes returns a conflict, not an implicit merge.
- Actor/origin metadata is recorded separately from content digests.

- [ ] **Step 2: Write failing deterministic export tests**

For one immutable version, assert:

- Export contains exactly one top-level canonical directory.
- Entries are sorted by UTF-8 normalized POSIX path.
- `SKILL.md`, optional `mindatlas.yaml`, then all preserved resources have exact stored bytes.
- ZIP timestamps are fixed to `1980-01-01 00:00:00`.
- Permissions are normalized to non-executable regular files.
- Every entry uses `ZIP_STORED`; no host/zlib compression-version output can alter bytes.
- No database metadata sidecar is invented.
- Exporting the same version twice yields identical bytes and SHA-256.
- Importing the export into an empty database recreates the same `content_digest`.
- Exporting a legacy shadow version still emits a portable package without legacy database IDs.

- [ ] **Step 3: Implement deterministic ZIP writer**

Expose:

~~~python
def export_skill_package(
    package_name: str,
    *,
    skill_md: bytes,
    mindatlas_yaml: bytes | None,
    resources: Sequence[StoredSkillResource],
) -> bytes: ...
~~~

Use explicit `ZipInfo` values for timestamp, platform, permissions, compression, and flags. Do not rely on host filesystem metadata or dictionary order.

Use `ZIP_STORED` for every entry. The 32 MiB upload envelope plus 25 MiB decoded limit guarantees an exported valid package remains re-importable.

- [ ] **Step 4: Implement create-only import service**

Expose:

~~~python
def import_package(
    self,
    parsed: ParsedSkillPackage,
    *,
    actor_id: UUID | None,
    origin: str,
) -> SkillPackageDetail: ...

def export_version(
    self,
    *,
    package_id: UUID,
    version_id: UUID,
) -> bytes: ...
~~~

Reserve every canonical/alias name and write the first draft inside one transaction. `export_version` verifies version ownership, loads exact blob bytes as `StoredSkillResource` values through the repository port, and delegates to `export_skill_package`; it never exports the aggregate’s current/latest pointer implicitly.

- [ ] **Step 5: Run focused tests**

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_import_export.py -q
~~~

- [ ] **Step 6: Commit**

~~~bash
git add backend/app/assistant/skills/package_io.py \
  backend/app/assistant/skills/service.py \
  backend/tests/test_agent_skill_import_export.py
git commit -m "feat(ai): add deterministic skill import export"
~~~

---

## Task 9: Expose Separate v2 Package and Main Agent APIs

**Files:**

- Create: `backend/app/assistant/skills/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_agent_skill_api.py`
- Create: `backend/tests/test_main_agent_profile_api.py`

**Interfaces:**

- Consumes: HTTP requests under `/api/assistant-config/skill-packages` and `/api/assistant-config/main-agent-profiles` using the same deployment access boundary as the current assistant-config API.
- Produces: v2 DTOs, deterministic ZIP downloads, and reserved error codes.
- Does not alter legacy `/api/assistant-config/skills`.

- [ ] **Step 1: Write failing package API tests**

Test the API contract already locked in this document:

- List packages with migration state, publication state, and pagination filters.
- List responses use the locked `limit`/`offset` bounds, stable ordering, exact filter vocabulary, and `{items,total,limit,offset}` inside `ApiResponse.data`.
- Get aggregate and version history.
- Create a native package from the locked JSON file-map contract.
- Upload ZIP with a streaming compressed-size bound.
- Reject encoded JSON create/save bodies above 36 MiB before parsing, then enforce decoded package/file limits.
- Save changed draft.
- Publish one owned draft.
- Get version metadata.
- Get one resource through a path-safe route.
- Resource lookup proves exact package/version/path ownership and returns attachment/nosniff headers with the server-detected media type; client-supplied media type is rejected as an unknown JSON field.
- Export one version with stable filename and content type.
- Duplicate names/aliases return `4099x`.
- Oversize requests return `4139x` before unbounded buffering.
- Invalid package/publish references return `4229x`.
- Not found uses `4049x`.
- Responses never include resource bytes, credentials, or ORM internals.
- Existing legacy API response snapshots remain unchanged.

- [ ] **Step 2: Write failing Main Agent API tests**

Cover:

- Get default profile.
- Save draft snapshot.
- List versions.
- Publish owned draft.
- Invalid/unknown snapshot fields.
- No endpoint activates the Main Agent loop.
- No update/delete endpoint exists for immutable versions.

- [ ] **Step 3: Implement package routes**

Use two child routers in the same module and register both without another prefix in `app.main`:

~~~python
skill_package_router = APIRouter(
    prefix="/api/assistant-config/skill-packages",
    tags=["assistant-skill-packages"],
)

main_agent_profile_router = APIRouter(
    prefix="/api/assistant-config/main-agent-profiles",
    tags=["assistant-main-agent-profiles"],
)
~~~

Decorators on `skill_package_router` use these exact **relative** paths:

~~~text
GET    ""
POST   ""
POST   /import
GET    /{package_id}
PUT    /{package_id}/draft
GET    /{package_id}/versions
GET    /{package_id}/versions/{version_id}
POST   /{package_id}/publish
GET    /{package_id}/versions/{version_id}/resources/{path:path}
GET    /{package_id}/versions/{version_id}/export
~~~

The publish request body contains `draftVersionId`. It must identify an owned draft version; the server does not implicitly resolve “latest”.

For upload:

- Reject a declared `Content-Length` above 32 MiB.
- Still count streamed bytes because the header is optional/untrusted.
- Stop after 32 MiB plus one byte.
- Pass a bounded in-memory stream to the parser only after the compressed bound is proven.

JSON create/save must likewise read `request.stream()` through a 36 MiB counter before JSON/Pydantic parsing; do not let the framework buffer an unbounded body first. Declared `Content-Length` is only an early rejection hint in both cases.

Resource retrieval must apply the current assistant-config access boundary, normalize the requested path through the same helper as import, query by exact package/version/path ownership, and load the referenced blob only after authorization. Return `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, and the server-detected content type. Plan 01 does not claim that MindAtlas already has the Principal/Policy authorization introduced by later plans.

- [ ] **Step 4: Implement Main Agent Profile routes**

Decorators on `main_agent_profile_router` use these relative paths:

~~~text
GET    /default
PUT    /default/draft
GET    /default/versions
POST   /default/publish
~~~

The profile publish request body likewise contains `draftVersionId`.

- [ ] **Step 5: Centralize error translation**

Map domain errors to the reserved code blocks while preserving the existing `ApiResponse` envelope exactly: `success`, numeric `code`, `message`, and `data`. Put `{type, details}` under `data` when needed. The current shared exception handler logs request IDs but does not expose them in the response, so Plan 01 must not invent a top-level trace field.

Do not expose stack traces, SQL constraints, filesystem paths, or credential values.

- [ ] **Step 6: Register the router without import cycles**

Modify `backend/app/main.py` using the project’s current router registration pattern. Confirm OpenAPI contains both old and new paths.

- [ ] **Step 7: Run focused API and regression tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_api.py \
  backend/tests/test_main_agent_profile_api.py \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py -q
~~~

The new API tests must also snapshot the pre-existing `/api/assistant-config/skills` OpenAPI/response shape because this repository has no dedicated legacy assistant-config router test at plan-writing time.

- [ ] **Step 8: Commit**

~~~bash
git add backend/app/assistant/skills/router.py backend/app/main.py \
  backend/tests/test_agent_skill_api.py \
  backend/tests/test_main_agent_profile_api.py
git commit -m "feat(ai): expose agent skill package APIs"
~~~

---

## Task 10: Add PostgreSQL Migration Gate, Conformance Smoke, and Final Verification

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `backend/tests/_bootstrap.py` only if parser/config caches were added.
- Create: `backend/tests/test_agent_skill_spec_conformance.py`
- Create: `backend/tests/test_agent_skill_postgres_migration.py`
- Modify: `docs/superpowers/plans/2026-07-13-agent-skills-contracts-and-versioning.md` only to check completed boxes and record deviations during execution.

**Interfaces:**

- Consumes: all Plan 01 implementation.
- Produces: CI evidence that schema, portable contract, old runtime compatibility, and deterministic artifacts hold together.

- [ ] **Step 1: Add a real PostgreSQL migration job**

Add a separate migration job with a PostgreSQL 15 service and health check. Set `MINDATLAS_TEST_POSTGRES_URL` to a disposable CI database and run:

~~~bash
cd backend
alembic upgrade head
alembic downgrade <PRE_PLAN01_HEAD>
alembic upgrade head
alembic current
python -m pytest tests/test_agent_skill_postgres_migration.py -q
~~~

At implementation time replace `<PRE_PLAN01_HEAD>` in the actual CI command with the exact Task 3 recorded parent and record the generated Plan 01 revision in this document. Do not leave a placeholder or assume `a7b8c9d0e1f2`. The command must end on the generated Plan 01 head.

The CI job must use an isolated disposable database, not developer/local credentials.

- [ ] **Step 2: Add migration data-preservation verification**

In a disposable PostgreSQL database:

1. Upgrade to the parent revision.
2. Insert representative legacy Tool, Workflow, Agent, version, and Skill rows satisfying existing constraints.
3. Upgrade to head.
4. Assert every legacy row and binding is unchanged.
5. Assert `assistant_tool.config_revision=1` and existing `ai_model`/`ai_credential.runtime_revision=1`.
6. Run shadow bootstrap once with no default-model component binding: verify every legacy Skill except `general_chat` has a disabled shadow aggregate/draft, affected items are `draft_unresolved`, no false published pointer exists, and old runtime bootstrap succeeds. Add a concrete test Credential/Model/component binding, run reconciliation, and verify the resolvable shadows publish without duplicate drafts.
7. Issue direct `UPDATE` and `DELETE` against each immutable table, including resource blob and dependency rows, and assert the trigger rejects them.
8. Issue direct execution-sensitive and revision-only Tool/Model/Credential updates and assert revision guards reject skipped/double/spurious increments while accepting exactly one valid increment.
9. Insert a resolved binding with Workflow/Agent/Tool/Model dependency FKs and prove direct target/version/model/credential deletion is restricted. Run the V1-reference -> system V2 warm/trim regression and prove every binding/dependency-protected V1 row survives.
10. Attempt a binding/dependency insert whose snapshot index omits, adds, reorders, or changes a dependency digest and assert the deferred constraint trigger rejects commit.
11. Attempt cross-package/profile Draft/Published/source pointers and wrong `version_source` pointers; assert deferred ownership guards reject commit.
12. Run two real PostgreSQL sessions against the same package to prove draft sequence convergence/conflict translation and no duplicate sequence.
13. Confirm downgrade succeeds when v2 data is derived `bootstrap/shadow` only, then upgrade again.
14. Exercise every downgrade refusal predicate separately and assert the exact `MINDATLAS_PLAN01_DOWNGRADE_BLOCKED_NATIVE_DATA` message appears before destructive DDL; remove test-only native data and restore head.
15. Save repeated-resource drafts and assert blob deduplication, exact metadata/byte verification, 256 MiB package quota behavior, two-session convergence, and no orphan blob after rollback.

This catches failures hidden by SQLite `create_all` tests.

- [ ] **Step 3: Add portable specification conformance vectors**

Create project-owned tests for:

- all standard frontmatter fields;
- standard name/description constraints;
- optional directories and safe extra files;
- local-link validation;
- portable round-trip.
- explicit Agent binding contract and remote Tool output-contract requirements.

Optionally run the official `skills-ref` validator against the exported happy-path fixture as a non-authoritative smoke test pinned to commit:

~~~text
38a2ff82958afee88dadf4831509e6f7e9d8ef4e
~~~

Do not import `skills-ref` into production, do not fetch an unpinned main branch in every unit-test run, and do not make MindAtlas accept behavior that violates this approved contract merely because the reference demonstration differs.

- [ ] **Step 4: Clear caches between tests**

If strict YAML loaders, config objects, or bootstrap registries are cached, add explicit reset hooks to `backend/tests/_bootstrap.py`. Do not depend on test order.

- [ ] **Step 5: Prove direct dependencies in a clean Python 3.11 environment**

CI already selects Python 3.11. Add a pre-test import/version check after `pip install -r requirements.txt pytest`:

~~~bash
python - <<'PY'
import importlib.metadata
import yaml
import jsonschema

print("PyYAML", importlib.metadata.version("PyYAML"))
print("jsonschema", importlib.metadata.version("jsonschema"))
assert yaml.__name__ == "yaml"
assert jsonschema.__name__ == "jsonschema"
PY
~~~

No editable local environment, transitive dependency, or Python 3.12 cache may satisfy this gate. The production YAML parser must use the repository’s strict `SafeLoader` subclass and must never construct custom tags.

In the same clean environment, generate the project-owned system Tool contract set and compare its individual Schema/digest vectors plus `system_tool_contract_set_digest` with the checked-in golden file. Separately compare LangChain `args_schema` for field/type compatibility. Incidental titles, `$defs` layout, and Pydantic formatting may differ without changing the frozen MindAtlas digest; a required-field/type mismatch fails CI and requires an explicit contract update/build revision.

- [ ] **Step 6: Run all Plan 01 focused tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_resolved_run_manifest.py \
  backend/tests/test_binding_json_schema.py \
  backend/tests/test_agent_skill_package_io.py \
  backend/tests/test_agent_skill_models.py \
  backend/tests/test_agent_skill_service.py \
  backend/tests/test_agent_skill_publish.py \
  backend/tests/test_agent_skill_dependency_closure.py \
  backend/tests/test_main_agent_profile_service.py \
  backend/tests/test_agent_skill_legacy_adapter.py \
  backend/tests/test_agent_skill_import_export.py \
  backend/tests/test_agent_skill_api.py \
  backend/tests/test_main_agent_profile_api.py \
  backend/tests/test_agent_skill_spec_conformance.py -q
~~~

- [ ] **Step 7: Run legacy runtime regression tests**

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py \
  backend/tests/test_assistant_skill_converters.py \
  backend/tests/test_system_skill_workflow_refs.py \
  backend/tests/test_system_agent_baseline_restore.py \
  backend/tests/test_ai_registry_service.py -q
~~~

Compare with Task 0. Changes in counts are acceptable only in new v2 tables; old runtime assertions must remain semantically unchanged.

- [ ] **Step 8: Run the full backend suite**

~~~bash
backend/.venv/bin/python -m pytest backend/tests -q
~~~

- [ ] **Step 9: Run static and repository checks**

Use only checks already configured by the repository. At minimum:

~~~bash
cd backend
.venv/bin/alembic heads
cd ..
git diff --check
git status --short
~~~

Expected: one Alembic head, no whitespace errors, and only intentional Plan 01 changes.

- [ ] **Step 10: Run a deterministic artifact smoke**

Export the same fixture/package twice in separate processes and compare:

~~~bash
sha256sum /tmp/mindatlas-skill-export-1.zip /tmp/mindatlas-skill-export-2.zip
cmp /tmp/mindatlas-skill-export-1.zip /tmp/mindatlas-skill-export-2.zip
~~~

On macOS, use `shasum -a 256` if `sha256sum` is unavailable.

- [ ] **Step 11: Review the hard boundary**

Use `rg` to prove:

- `SkillRouter` and `Supervisor` do not import `app.assistant.skills`.
- No old runtime path queries `assistant_skill_package`.
- No script execution call was added.
- No package becomes `catalog_enabled=true`.
- No new Run/Checkpoint/HITL tables were introduced.
- No L2 key or current assistant endpoint behavior changed.
- No binding/closure runtime code resolves embedded Tools, Models, or Workflows by current name/latest pointer.
- No OpenClaw catalog Schema is used as a native published binding source.

- [ ] **Step 12: Record deviations and update checkboxes**

If actual filenames, migration IDs, or existing test commands differed, update this plan with the exact final values. Architectural deviations require approval and an update to the overall design, not a silent checkbox.

- [ ] **Step 13: Final commit**

~~~bash
git add .github/workflows/ci.yml backend/tests \
  docs/superpowers/plans/2026-07-13-agent-skills-contracts-and-versioning.md
git commit -m "test(ai): gate agent skill contracts and migrations"
~~~

---

## Deployment, Backfill, and Rollback Runbook

Plan 01 is additive and the old runtime remains authoritative, but database rollback is not always the right recovery action.

### Forward deployment order

1. Build/test on clean Python 3.11 and record `PRE_PLAN01_HEAD`, generated Plan 01 revision, `APP_BUILD_REVISION`, and fixed digest vectors.
2. Back up the PostgreSQL database and verify restore in a disposable environment.
3. Run `alembic upgrade <generated-plan01-revision>` before deploying application code.
4. Deploy the Plan 01 application with all new package/profile runtime flags false.
5. Run idempotent bootstrap/shadow sync. Record counts: legacy Skills, generated shadow packages/drafts, published, unchanged, draft-unresolved, failed, skipped `general_chat`, diagnostics, exact target/model closures, effective build/contract-set revisions, and source digests. An unresolved default model/build revision is repaired operationally and reconciled; never insert a fake credential merely to make the report green.
6. Run read-only API/import/export smoke. Do not set `catalog_enabled` or `runtime_enabled` true.
7. Observe old assistant routing/execution and revision-guard errors. Shadow diagnostics do not alter old runtime success.

Backfill is replay, not an in-place conversion:

- Legacy rows remain untouched.
- Shadow/package/Profile versions are appended from deterministic source digests.
- An unresolved item leaves a disabled aggregate/draft plus diagnostic and no partially published version; a failed item rolls back its partial writes.
- Re-running unchanged input adds no version.
- Changed legacy published target/config appends one new shadow publish with a fresh complete binding/closure; it never rewrites history.

### Rollback matrix

| State reached | Application rollback | Database action | Required evidence |
|---|---|---|---|
| Migration applied, no v2 rows | deploy previous app | downgrade to `PRE_PLAN01_HEAD` is supported | upgrade/downgrade/upgrade gate |
| Only replayable `bootstrap/shadow` rows | deploy previous app; old runtime unaffected | guarded downgrade may discard/replay derived rows | export optional, sync report retained |
| Any `native/cutover` package or administrator-authored Profile history | deploy previous app against the additive schema | **do not downgrade**; guard must refuse | deterministic export/backup and later forward fix |
| Any suspected immutable corruption or revision mismatch | disable new v2 APIs/keep old runtime | no destructive cleanup; restore/repair forward from backup | incident record + digest comparison |

The previous application is expected to ignore the additive tables and revision columns, so application rollback is the default safe response after native data exists. Normal APIs expose no destructive purge. Any future operator-approved irreversible removal must be a separate audited procedure that first exports native packages/Profile history; it is not hidden inside `alembic downgrade`.

### Go/no-go gates before Plan 02

- No unresolved published binding or incomplete dependency closure exists.
- Every legacy Skill except `general_chat` has a disabled shadow aggregate and exact draft. Only items whose target/model/build evidence is resolvable are required to have a published shadow; unresolved items have stable diagnostics and null published pointers.
- Reconstructing every published binding from binding + dependency rows reproduces all Schema/ref/closure/contract/version digests.
- Every default/custom model dependency is an exact model/credential revision, not `default` or a component-binding pointer.
- Alias lookup produces exact immutable version refs; reserved names and collision vectors pass.
- Tool/Model/Credential revision service logic and PostgreSQL guards agree.
- Existing trim/restore/warm paths preserve every binding/dependency-protected Workflow/Agent version; the V1 -> V2 warm regression passes.
- Project-owned system Tool Schema vectors and `system_tool_contract_set_digest` pass in a clean environment; raw LangChain Schema formatting is not a frozen digest source.
- The generated migration is the sole head and its revision is unique.
- Old runtime focused/full tests and the PostgreSQL preservation gate pass.
- Plan 02 has been updated to the actual class/table/column names and consumes, rather than recreates, the Schema/closure contracts.
- Plan 02 imports the exact Plan 01 closure constant names/values and its descriptor classification reads no mutable catalog state.

If any gate fails, Plan 01 may still be deployed only as a disabled migration experiment; Plan 02 implementation must not start.

Plan 04’s first end-to-end golden path has a stricter precondition: the specific shadow/native Skill selected for activation must already have a resolved published version. A diagnostic-only draft is intentionally invisible to the runtime catalog.

---

## Plan 01 Exit Criteria

Plan 01 is complete only when all are true:

- A standards-compatible `SKILL.md` package with strict `mindatlas.yaml` v1 can be parsed without extracting to disk.
- `SkillConflictRuleV1` has one canonical stored/digested dialect, and terminal-required policies without a structural satisfaction path cannot publish.
- Malicious/ambiguous packages fail before persistence with stable error classes.
- Skill packages, resources, Capability bindings, and Main Agent Profiles have append-only immutable versions.
- Published bindings losslessly persist normalized input/output Schema bodies, independent digests, completion/execution metadata, and a verified `binding_contract_digest`.
- Published Agent/Workflow bindings freeze explicit target versions plus complete typed Tool/Workflow/Agent/Model/Credential dependency closures without secrets or mutable-name fallback.
- Remote top-level Tool bindings require an explicit binding-owned output contract; Agent bindings require explicit callable contracts.
- Tool config and Model/Credential runtime revisions are transactionally advanced and database-guarded.
- Import is create-only and disabled; export is deterministic and portable.
- Legacy Skills except `general_chat` have idempotent, disabled shadow aggregates/drafts; each resolvable item has a published shadow version, while an unresolvable item has a stable diagnostic and no false published pointer.
- `general_chat` seeds the default Main Agent Profile without changing current routing.
- Old `AssistantSkill` APIs, table contract, Router, Supervisor, and execution behavior remain authoritative.
- Legacy administrative trim/delete/restore behavior has only the documented reference-protection change: referenced Workflow/Agent history survives and conflicts use `40994`.
- PostgreSQL upgrade/downgrade/upgrade and legacy-row preservation pass.
- The Alembic revision was generated uniquely from the execution-time sole head; no provisional/occupied revision is reused.
- Clean Python 3.11 installs and directly imports strict PyYAML/jsonschema dependencies.
- Focused, legacy regression, and full backend tests pass.
- There is exactly one Alembic head.
- No Plan 02+ runtime behavior was smuggled into this change.

## Handoff to Plan 02

Plan 02 may rely on these stable outputs only:

- package/version/resource lookup by immutable ID;
- canonical `SkillConflictRuleV1` values plus immutable terminal-policy fields and publication-time structural satisfiability evidence for later Plan 05 enforcement;
- content-addressed resource blob lookup behind the version-resource repository port, with exact package/version/path ownership checks;
- canonical JSON plus shared Schema normalization and `content_digest`, Schema, target, closure, binding, and version digest calculation;
- published Capability binding snapshots with lossless Schema bodies/completion/execution metadata;
- immutable typed dependency-closure rows, including exact default/custom Model/Credential refs;
- Tool `config_revision` and application build revision;
- project-owned Tool parameter Schema conversion, checked-in system Tool vectors, and `system_tool_contract_set_digest`;
- Model/Credential `runtime_revision` and secret-free config-digest semantics;
- frozen `ResolvedRunManifestRevision` v1 append semantics, stable `schema_version`, complete Provider/Model ref slots, empty `provider_aliases`, and Plan 01-owned `ResolvedProviderAliasRef` type;
- Main Agent Profile version lookup;
- normalized alias lookup that returns an exact immutable published Skill ref;
- locked `ResolvedMainAgentRef`, `ResolvedCapabilityBinding`, `CurrentCapabilityReference`, mutation command DTOs, domain literal aliases, and closure constant names/values;
- shadow/native migration state and disabled catalog flag.

Plan 02 must still add the Capability Runtime and deny-by-default execution policy. It must project `FrozenCapabilityBinding` and the exact execution resolver map from Plan 01 rows, reuse the Plan 01 Schema normalizer/digests/walkers/constants, and fail closed on incomplete/drifted closure. It may not query mutable target/OpenClaw/default-model state to repair a snapshot. It derives `side_effect`, `parallel_safe`, and `timeout_policy` only from immutable closure evidence plus its checked-in versioned classification table and freezes the result. The existence of a published Skill version or an `allowed-tools` declaration is not execution authorization.

Plan 03 must consume the already stable Provider/Model/Alias fields. It implements Provider-specific alias validation/allocation, complete-model eligibility/probe enforcement, and child-revision alias appends; it must not extend the v1 Manifest payload or duplicate Model/Credential revision columns.
