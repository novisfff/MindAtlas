# Main Agent Bootstrap and Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every fresh MindAtlas installation deterministically seed, prepare, activate, admit, and execute only the durable Main Agent, with truthful readiness, immutable runtime closure, compatible-Worker enforcement, and no Legacy selector or fallback.

**Architecture:** A new `app.assistant.runtime` package owns the trusted system seed, immutable rollout revisions, singleton rollout control, closure calculation, readiness, activation, and atomic Chat admission. Initialization stages a digest-locked built-in Skill, a published Profile V2, and a prepared rollout in the existing coordinator-owned transaction; a separate authenticated CAS activation points the singleton control at that revision after a compatible Worker is observed. `/health` remains process-only while `/ready` and admission share the same locked readiness/closure evaluator, so a pre-insert failure leaves no Message or Run residue and every admitted Run freezes one exact Main-Agent-only closure.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 15, SHA-256 canonical JSON, React 18, TypeScript, TanStack Query, Vitest, Testing Library, Docker Compose.

## Global Constraints

- Implement from the approved production-closure design commit `ca925eeba569357ddb2c5c3aa63554b391efd21b` plus completed Plan 1 commit `98accdb`. If HEAD differs, review every intervening change and refresh file references before editing.
- The only supported runtime for every newly admitted Chat Run is `main_agent`; no configuration, rollout state, missing dependency, exception, timeout, or Worker failure may select `legacy`.
- Do not reintroduce IntentRouter, SkillRouter, Supervisor, Legacy AssistantAgent, blocking Legacy HITL, Legacy generation, or a second fallback Run.
- The durable singleton `assistant_main_agent_rollout_control` row is the only source of the active rollout fact.
- `ASSISTANT_NEW_RUNS_ENABLED` is a process-level emergency ceiling only. Admission requires both that setting and durable `new_runs_enabled`; changing either never mutates an existing Run.
- `ASSISTANT_MAIN_AGENT_WRITE_MODE` remains `off` by default. The trusted package may bind `create_entry`, but Plan 2 must not enable production writes.
- `update_entry`, `merge_entry`, `create_relation`, and relation-followup capabilities are absent from the trusted Skill. An unsupported requested write is not transformed into `create_entry`.
- Fresh initialization consumes only the build-owned, digest-verified system seed and only while the system, Operator, published Profile, and active rollout are absent.
- Initialization creates a prepared rollout but never activates it. Activation is a separate Operator-plus-CSRF mutation after the initialization commit.
- Initialization, Profile publication, Skill publication, prepared rollout creation, initialization marker, bootstrap gate use, and audit event commit atomically or not at all.
- The initial browser Session is issued only after that initialization transaction commits.
- Profile schema V2 has no fallback policy. Profile V1 is parseable only for historical read-only display and cannot be drafted, published, prepared, activated, or admitted.
- Rollout revisions are immutable. Rollout events are append-only. Activation and kill-switch changes use expected control revision plus request-id idempotency.
- Activation revalidates the build, runtime contract, checkpoint codec, Capability feature digest, Profile identity, deterministic Model identity, Package/Capability closure, seed manifest, gate use, and one fresh non-draining compatible Worker under database locks.
- Model identity used for release/runtime closure is deterministic from the bound credential/provider/model metadata. Optional paid live capability probes remain diagnostics and never determine deterministic readiness.
- `/health` performs no database access and reports process liveness only.
- Public `/ready` exposes only `ready` and stable reason codes. Authenticated `/api/assistant-runtime/readiness` may expose safe IDs and Worker diagnostics.
- Compose bootstrap and Web dependencies use `/health`; Chat admission and deployment acceptance use `/ready`. Do not create a dependency cycle that prevents initialization or activation.
- Readiness and Chat admission share one evaluator. Admission locks control, revalidates closure and Worker compatibility, and inserts user Message, empty assistant Message, one Main Agent Run, and the initial event in one transaction.
- A failure before Run insertion leaves no user Message, assistant placeholder, Run, or event. A failure after Run insertion remains on that exact durable Run.
- The additive Plan 2 revision is `b6e2d4f8a901` with `down_revision = "9f3c1a7e2b40"`.
- The migration rejects any existing `runtime_kind != 'main_agent'` as `legacy_upgrade_not_supported`; it rejects any other non-empty `assistant_chat_run` as `schema_incompatible`. It does not convert, delete, or relabel existing data.
- Plan 3 will archive the old 58 revisions plus Plan 1 and Plan 2 and replace them with `pre_ga_v1_0001`; Plan 2 must not create or edit that clean root.
- Evidence contains no password, Setup/Session/CSRF token, AI/provider credential, Prompt, Provider payload, Entry content, Artifact content, raw IP/User-Agent, or raw idempotency key.
- Every Task follows red-green-refactor, ends with one independently reviewable commit, and leaves focused tests green.
- If deterministic seed identity, transaction ownership, append-only/CAS behavior, closure equality, Worker compatibility, or pre-insert atomicity cannot be proven, stop. Do not weaken the gate with a warning, skip, environment bypass, or Legacy path.

---

## Prerequisites and Stable Interfaces

### Required checkpoint

Run from the repository root:

```bash
git status --short
git rev-parse HEAD
git log -2 --format='%H %s'
rg -n '9f3c1a7e2b40|b6e2d4f8a901|pre_ga_v1_0001|pre_ga_v1_0002' backend
cd backend
.venv/bin/alembic heads
```

Expected:

- `git status --short` prints nothing.
- HEAD is `98accdb` or a reviewed descendant containing the Plan 1 document and implementation prerequisites.
- the revision scan finds no implementation collision before this plan starts;
- before Plan 1 implementation, Alembic reports `3bd7bc4257c9 (head)`; after Plan 1 implementation it reports exactly `9f3c1a7e2b40 (head)`;
- implementation must stop if there is more than one live Alembic head.

The execution branch must first contain the implemented and verified Plan 1 interfaces below. The Plan 1 document alone is not a substitute for those interfaces.

### Consumed Operator and initialization contracts

`backend/app/operator_auth/contracts.py`:

```python
@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: Literal["viewer", "operator"]
    session_id: UUID
    authentication_method: Literal["password_session"] = "password_session"

    @property
    def principal_id(self) -> str: ...

    @property
    def is_operator(self) -> bool: ...

    def audit_actor(self) -> str: ...


@dataclass(frozen=True)
class OperatorAuthAvailability:
    available: bool
    reason_codes: tuple[str, ...]
```

`backend/app/operator_auth/dependencies.py`:

```python
def require_viewer_principal(...) -> OperatorPrincipal: ...
def require_operator_principal(...) -> OperatorPrincipal: ...
def require_csrf(...) -> None: ...
```

`backend/app/operator_auth/constants.py`:

```python
OPERATOR_AUTH_CONTRACT_VERSION = "operator-auth-v1"
```

`backend/app/operator_auth/service.py`:

```python
class OperatorAuthService:
    def availability(self) -> OperatorAuthAvailability: ...
```

`backend/app/system_settings/initialization_service.py`:

```python
@dataclass(frozen=True)
class CoreInitializationResult:
    locale: Literal["zh", "en"]
    credential_id: UUID
    llm_model_id: UUID


class SystemInitializationService:
    def stage_core_initialization(
        self, request: InitializeSystemRequest
    ) -> CoreInitializationResult: ...

    def stage_initialization_marker(
        self, *, locale: Literal["zh", "en"], source: Literal["user"]
    ) -> None: ...
```

Plan 2 inserts trusted bootstrap after `stage_core_initialization()` and before `stage_initialization_marker()`. `InitializationCoordinator` remains the sole commit owner.

### Produced Profile V2 contract

`backend/app/assistant/skills/schemas.py` exports:

```python
class MainAgentRuntimePolicyV2(FrozenContract):
    runtime_kind: Literal["main_agent"] = "main_agent"
    recovery_scope: Literal["same_run_only"] = "same_run_only"


class MainAgentProfileSnapshotV2(FrozenContract):
    schema_version: Literal[2]
    base_prompt: str
    response_style: dict[str, str]
    supported_entrypoints: tuple[MainAgentEntrypoint, ...]
    model_requirements: ModelRequirementsV1
    control_capability_keys: tuple[str, ...]
    skill_catalog_scope: SkillCatalogScopeV1
    context_budget: ContextBudgetV1
    output_budget: OutputBudgetV1
    global_safety_policy: GlobalSafetyPolicyV1
    runtime_policy: MainAgentRuntimePolicyV2
```

V2 intentionally has no `fallback_policy`. All production write methods accept `MainAgentProfileSnapshotV2`; read methods return a discriminated V1-or-V2 detail.

### Produced runtime closure and readiness contracts

`backend/app/assistant/runtime/contracts.py` exports:

```python
class AssistantRuntimeSubject(FrozenContract):
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure: tuple[dict[str, JsonValue], ...]
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str


class AssistantRuntimeClosure(FrozenContract):
    schema_version: Literal[1] = 1
    rollout_revision_id: UUID
    rollout_revision_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    closure_digest: str


class AssistantReadinessSnapshot(FrozenContract):
    ready: bool
    reason_codes: tuple[str, ...]
    active_rollout_revision_id: UUID | None
    profile_version_id: UUID | None
    model_id: UUID | None
    compatible_worker_ids: tuple[str, ...]
    build_revision: str
```

Stable Plan 2 reason codes are exactly:

```text
system_not_initialized
operator_missing
operator_auth_unavailable
system_seed_invalid
profile_unpublished
model_unbound
rollout_inactive
runtime_closure_drift
worker_unavailable
schema_incompatible
new_runs_disabled
```

Plan 4 adds `pre_ga_launch_unapproved` without renaming these values.

### Produced activation and admission contracts

```python
class ActivateRolloutRequest(CamelModel):
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class PrepareRolloutRequest(CamelModel):
    profile_version_id: UUID
    model_id: UUID
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class SetNewRunsEnabledRequest(CamelModel):
    enabled: bool
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class NewChatAdmission:
    closure: AssistantRuntimeClosure
    compatible_worker_ids: tuple[str, ...]
    deadline_at: datetime | None
```

HTTP endpoints:

| Method and path | Policy | Success |
|---|---|---|
| `GET /health` | public | 200 process-only `{"status":"ok"}` |
| `GET /ready` | public | 200 when ready, otherwise 503; safe reason codes only |
| `GET /api/assistant-runtime/readiness` | viewer session | 200 authenticated diagnostics |
| `GET /api/assistant-runtime/rollouts` | viewer session | 200 immutable revision/control summaries |
| `POST /api/assistant-runtime/rollouts/prepare` | Operator plus CSRF | 201 immutable prepared revision or idempotent replay |
| `POST /api/assistant-runtime/rollouts/{revision_id}/activate` | Operator plus CSRF | 200 activated or idempotently replayed result |
| `POST /api/assistant-runtime/new-runs` | Operator plus CSRF | 200 durable kill-switch result |

Stable runtime HTTP failures:

- 409 `assistant_rollout_control_conflict` for a stale expected control revision;
- 409 `assistant_request_reuse_conflict` for one request ID with a different digest;
- 422 `assistant_rollout_not_prepared` for an unknown revision;
- 503 `assistant_rollout_inactive`, `assistant_runtime_closure_drift`, `assistant_worker_unavailable`, `assistant_new_runs_disabled`, or the mapped safe readiness reason;
- no response includes seed content, Profile prompt, credential data, or raw request identity.

### Fixed migration sequence

```text
3bd7bc4257c9
  -> 9f3c1a7e2b40
  -> b6e2d4f8a901
  -> archive old 60-revision lineage in Plan 3
  -> pre_ga_v1_0001
  -> pre_ga_v1_0002 in Plan 4
```

---

## File Structure

### New runtime package

| Path | Responsibility |
|---|---|
| `backend/app/assistant/runtime/__init__.py` | Export only stable runtime contracts and services. |
| `backend/app/assistant/runtime/contracts.py` | Frozen closure/readiness, activation commands/results, stable reason codes, digest validators. |
| `backend/app/assistant/runtime/models.py` | Immutable rollout revision, singleton control, append-only event ORM models. |
| `backend/app/assistant/runtime/repository.py` | Row locks, immutable creation, request replay lookup, CAS control updates, append-only events. |
| `backend/app/assistant/runtime/seed.py` | Parse and verify the embedded system seed and build-owned expected digests. |
| `backend/app/assistant/runtime/bootstrap.py` | Stage built-in Skill/Profile/prepared rollout and bootstrap evidence without committing. |
| `backend/app/assistant/runtime/closure.py` | Deterministic bound Model identity and canonical runtime closure calculation/revalidation. |
| `backend/app/assistant/runtime/readiness.py` | Shared public/authenticated/locked readiness evaluation. |
| `backend/app/assistant/runtime/activation.py` | Operator CAS/idempotent activation and durable new-Run switch service. |
| `backend/app/assistant/runtime/admission.py` | Locked atomic Chat admission plan and stable failure mapping. |
| `backend/app/assistant/runtime/router.py` | Readiness, rollout listing, activation, and new-Run HTTP routes. |

### Trusted seed

| Path | Responsibility |
|---|---|
| `backend/app/assistant/runtime/system_seed/main-agent-profile.v2.json` | Exact conservative Profile V2 source. |
| `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/SKILL.md` | Built-in universal Skill instructions. |
| `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/mindatlas.yaml` | Exact read capabilities plus bound-but-disabled `create_entry`. |
| `backend/app/assistant/runtime/system_seed/manifest.v1.json` | Generated canonical seed manifest and digest. |
| `backend/app/assistant/runtime/system_seed/expected.py` | Generated build-owned literal manifest/contract digests. |
| `backend/scripts/build_assistant_system_seed.py` | Deterministically generate or check manifest and expected digest module. |

### Database and existing backend integration

| Path | Change |
|---|---|
| `backend/alembic/versions/b6e2d4f8a901_main_agent_bootstrap_readiness.py` | Add rollout state, freeze Run closure, remove Legacy shape, preflight non-empty data, append-only trigger. |
| `backend/alembic/env.py` | Register new runtime ORM metadata while the pre-squash chain remains live. |
| `backend/app/config.py` | Remove runtime selector/label; add `ASSISTANT_NEW_RUNS_ENABLED`. |
| `backend/app/main.py` | Mount runtime router, remove Legacy startup validation, keep DB-free `/health`. |
| `backend/app/assistant/models.py` | Main-Agent-only Run columns, relationships, and constraints. |
| `backend/app/assistant/run_service.py` | Require all frozen closure fields; remove default runtime kind. |
| `backend/app/assistant/service.py` | Delegate Message/Run creation to atomic admission; remove conditional Legacy behavior. |
| `backend/app/assistant/skills/schemas.py` | Add Profile V2/runtime policy and V1 read-only discrimination. |
| `backend/app/assistant/skills/service.py` | Reject V1 production mutations and preserve V1 historical reads. |
| `backend/app/assistant/main_agent/service.py` | Split deterministic bound Model identity from optional live probe and remove fallback fields. |
| `backend/app/assistant/main_agent/prompt_builder.py` | Remove Legacy fallback prompt material. |
| `backend/app/assistant/durable/worker_registry.py` | Canonical Worker compatibility query and safe diagnostics. |
| `backend/app/assistant/durable/codec.py` | Export the one current checkpoint codec version frozen into rollout and Run closure. |
| `backend/app/assistant/durable/leases.py` | Match every claim to the Run's frozen build/contract/codec/feature requirements. |
| `backend/app/assistant/worker.py` | Register canonical identity and refuse schema-incompatible claims. |
| `backend/app/system_settings/initialization_coordinator.py` | Retain sole transaction ownership and insert trusted bootstrap between core staging and marker. |
| `backend/app/system_settings/initialization_service.py` | Retain core/marker staging seams consumed by coordinator. |
| `backend/app/system_settings/router.py` | Return safe bootstrap status after coordinated initialization. |
| `backend/app/system_settings/schemas.py` | Add prepared rollout/control revision/bootstrap response fields. |

### Frontend, deployment, tests, and evidence

| Path | Responsibility |
|---|---|
| `frontend/src/features/assistant-runtime/api/runtime.ts` | Public and authenticated readiness plus activation/switch clients. |
| `frontend/src/features/assistant-runtime/queries.ts` | Query keys, polling, and invalidation. |
| `frontend/src/features/assistant-runtime/components/AssistantRuntimeActivationCard.tsx` | Explain safe reasons and require explicit activation. |
| `frontend/src/features/assistant-runtime/components/AssistantReadinessGate.tsx` | Disable new Chat while `/ready` is false without blocking setup. |
| `frontend/src/features/assistant-runtime/index.ts` | Public feature exports. |
| `frontend/src/features/initialization/pages/SystemInitializationPage.tsx` | Continue from committed setup to pending-Worker/activation state. |
| `frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.tsx` | Edit/publish Profile V2 without fallback controls. |
| `frontend/src/features/assistant/AssistantPage.tsx` | Consume readiness gate before Chat admission. |
| `frontend/src/locales/en/common.json` | English readiness/activation copy. |
| `frontend/src/locales/zh/common.json` | Chinese readiness/activation copy. |
| `deploy/docker-compose.yml` | Remove selector variables, add new-Run ceiling, preserve `/health` dependencies. |
| `backend/.env.example` | Document only the new-Run ceiling and independent write mode. |
| `.github/workflows/ci.yml` | Run seed check, PostgreSQL rollout/readiness tests, frontend tests, and smoke gate. |
| `backend/scripts/smoke_main_agent_bootstrap.py` | Fixed fresh-install initialization-to-Chat smoke with sanitized output. |
| `deploy/compose.main-agent-smoke.yml` | Disposable provider stub, API, Web, and compatible Worker smoke overlay. |
| `backend/tests/support/openai_stub_server.py` | Deterministic OpenAI-compatible test server used only by smoke. |
| `docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json` | Safe generated execution evidence; created by Task 12, not hand-authored. |

The focused test files are introduced in the Tasks that own them. Existing tests that assert selectable Legacy runtime are explicitly retired or rewritten in Task 9.

---

### Task 1: Remove Runtime Selection and Introduce Profile V2

**Files:**

- Create: `backend/tests/test_assistant_runtime_config.py`
- Create: `backend/tests/test_main_agent_profile_v2.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/assistant/skills/schemas.py`
- Modify: `backend/app/assistant/skills/service.py`
- Modify: `backend/app/assistant/main_agent/prompt_builder.py`
- Modify: `backend/.env.example`

**Interfaces:**

- Consumes: existing Profile V1 nested budget/model/catalog contracts and `FrozenContract`.
- Produces: `MainAgentRuntimePolicyV2`, `MainAgentProfileSnapshotV2`, `parse_main_agent_profile_snapshot_for_read()`, `require_production_profile_v2()`, and `Settings.assistant_new_runs_enabled: bool`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_default_configuration_has_no_runtime_selector(monkeypatch):
    monkeypatch.delenv("ASSISTANT_NEW_RUNS_ENABLED", raising=False)
    monkeypatch.delenv("ASSISTANT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("ASSISTANT_RUNTIME_ROLLOUT_REVISION", raising=False)
    settings = Settings(_env_file=None)
    assert settings.assistant_new_runs_enabled is True
    assert not hasattr(settings, "assistant_runtime_mode")
    assert not hasattr(settings, "assistant_runtime_rollout_revision")


@pytest.mark.parametrize(
    "name",
    ["ASSISTANT_RUNTIME_MODE", "ASSISTANT_RUNTIME_ROLLOUT_REVISION"],
)
def test_removed_runtime_selector_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "legacy")
    with pytest.raises(ValueError, match="removed runtime selector"):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run the configuration tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_assistant_runtime_config.py -q
```

Expected: FAIL because `assistant_runtime_mode` still exists and `assistant_new_runs_enabled` does not.

- [ ] **Step 3: Replace the selector with one emergency ceiling**

Add the field and a before-model validator that detects removed environment/dotenv aliases:

```python
class Settings(BaseSettings):
    assistant_new_runs_enabled: bool = Field(
        default=True,
        alias="ASSISTANT_NEW_RUNS_ENABLED",
    )

    removed_assistant_runtime_mode: str | None = Field(
        default=None,
        alias="ASSISTANT_RUNTIME_MODE",
        exclude=True,
        repr=False,
    )
    removed_assistant_runtime_rollout_revision: str | None = Field(
        default=None,
        alias="ASSISTANT_RUNTIME_ROLLOUT_REVISION",
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def reject_removed_runtime_selectors(self) -> "Settings":
        if self.removed_assistant_runtime_mode is not None:
            raise ValueError("removed runtime selector: ASSISTANT_RUNTIME_MODE")
        if self.removed_assistant_runtime_rollout_revision is not None:
            raise ValueError(
                "removed runtime selector: ASSISTANT_RUNTIME_ROLLOUT_REVISION"
            )
        return self
```

Remove `AssistantRuntimeMode`, `assistant_runtime_mode`, and `assistant_runtime_rollout_revision`. In `backend/.env.example`, replace the two removed variables with:

```dotenv
# Emergency process ceiling. Durable rollout control must also allow new Runs.
ASSISTANT_NEW_RUNS_ENABLED=true
# Writes remain disabled until Plan 4 release qualification.
ASSISTANT_MAIN_AGENT_WRITE_MODE=off
```

- [ ] **Step 4: Write failing Profile V2 tests**

```python
def test_profile_v2_has_main_agent_only_runtime_policy():
    snapshot = default_main_agent_profile_snapshot_v2()
    assert snapshot.schema_version == 2
    assert snapshot.runtime_policy.runtime_kind == "main_agent"
    assert snapshot.runtime_policy.recovery_scope == "same_run_only"
    assert "fallbackPolicy" not in snapshot.normalized_payload()


def test_profile_v1_is_readable_but_not_publishable(profile_v1_payload):
    historical = parse_main_agent_profile_snapshot_for_read(profile_v1_payload)
    assert historical.schema_version == 1
    with pytest.raises(ProfileSchemaNotPublishable) as exc:
        require_production_profile_v2(historical)
    assert exc.value.reason_code == "profile_schema_unsupported"


def test_profile_v2_rejects_legacy_runtime_policy(profile_v2_payload):
    profile_v2_payload["runtimePolicy"]["runtimeKind"] = "legacy"
    with pytest.raises(ValidationError):
        MainAgentProfileSnapshotV2.model_validate(profile_v2_payload)
```

- [ ] **Step 5: Run the Profile tests and verify red**

Run:

```bash
.venv/bin/python -m pytest tests/test_main_agent_profile_v2.py -q
```

Expected: FAIL because the V2 types and production guard do not exist.

- [ ] **Step 6: Add the exact V2 schema and production guard**

```python
class MainAgentRuntimePolicyV2(FrozenContract):
    runtime_kind: Literal["main_agent"] = "main_agent"
    recovery_scope: Literal["same_run_only"] = "same_run_only"


class MainAgentProfileSnapshotV2(FrozenContract):
    schema_version: Literal[2]
    base_prompt: str
    response_style: dict[str, str] = Field(default_factory=dict)
    supported_entrypoints: tuple[MainAgentEntrypoint, ...]
    model_requirements: ModelRequirementsV1
    control_capability_keys: tuple[str, ...] = ()
    skill_catalog_scope: SkillCatalogScopeV1 = Field(
        default_factory=SkillCatalogScopeV1
    )
    context_budget: ContextBudgetV1
    output_budget: OutputBudgetV1
    global_safety_policy: GlobalSafetyPolicyV1
    runtime_policy: MainAgentRuntimePolicyV2 = Field(
        default_factory=MainAgentRuntimePolicyV2
    )

    def normalized_payload(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")

    def content_digest(self) -> str:
        return sha256_canonical_json(self.normalized_payload())


ReadableMainAgentProfileSnapshot = (
    MainAgentProfileSnapshotV1 | MainAgentProfileSnapshotV2
)


class ProfileSchemaNotPublishable(ValueError):
    reason_code = "profile_schema_unsupported"


def parse_main_agent_profile_snapshot_for_read(
    payload: Mapping[str, Any],
) -> ReadableMainAgentProfileSnapshot:
    version = payload.get("schemaVersion", payload.get("schema_version"))
    if version == 1 and type(version) is int:
        return MainAgentProfileSnapshotV1.model_validate(payload)
    if version == 2 and type(version) is int:
        return MainAgentProfileSnapshotV2.model_validate(payload)
    raise ValueError("unsupported Main Agent Profile schema version")


def require_production_profile_v2(
    snapshot: ReadableMainAgentProfileSnapshot,
) -> MainAgentProfileSnapshotV2:
    if not isinstance(snapshot, MainAgentProfileSnapshotV2):
        raise ProfileSchemaNotPublishable(
            "Profile schema V2 is required for production operations"
        )
    return snapshot
```

Reuse V1 field validators by extracting the base-prompt, response-style, entrypoint, and control-key validators into shared functions, then invoking them from both schema classes. Do not subclass V1 because that would retain `fallback_policy`.

- [ ] **Step 7: Make every Profile mutation V2-only**

Change draft and publish command types and insert an explicit check at the service boundary:

```python
class SaveMainAgentProfileDraftCommand(CamelModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    snapshot: MainAgentProfileSnapshotV2
    expected_aggregate_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    version_name: str | None = None
    origin: Literal["api", "system_bootstrap"] = "api"
    source_ref: dict[str, Any] | None = None


def _production_snapshot_from_version(
    version: AssistantMainAgentProfileVersion,
) -> MainAgentProfileSnapshotV2:
    parsed = parse_main_agent_profile_snapshot_for_read(
        dict(version.snapshot_json)
    )
    return require_production_profile_v2(parsed)
```

The Profile detail endpoint may return V1 for historical display. Draft, publish, runtime preparation, activation, and admission call `_production_snapshot_from_version()` and fail with `profile_schema_unsupported`.

- [ ] **Step 8: Remove fallback text from prompt construction**

Replace the fallback-policy section with the immutable runtime policy:

```python
runtime_line = (
    "runtime_kind=main_agent "
    "recovery_scope=same_run_only "
    "cross_runtime_fallback=false"
)
```

Assert the rendered base prompt contains `cross_runtime_fallback=false` and contains no Profile-derived Legacy permission.

- [ ] **Step 9: Run focused tests and inspect removed configuration**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_config.py \
  tests/test_main_agent_profile_v2.py \
  tests/test_main_agent_profile_service.py \
  tests/test_main_agent_prompt_builder.py -q
rg -n 'ASSISTANT_RUNTIME_MODE|ASSISTANT_RUNTIME_ROLLOUT_REVISION' \
  app/config.py .env.example
```

Expected: tests PASS; the scan prints only rejection aliases/tests in `app/config.py` and no active/default selector.

- [ ] **Step 10: Commit**

```bash
git add \
  backend/app/config.py \
  backend/app/assistant/skills/schemas.py \
  backend/app/assistant/skills/service.py \
  backend/app/assistant/main_agent/prompt_builder.py \
  backend/.env.example \
  backend/tests/test_assistant_runtime_config.py \
  backend/tests/test_main_agent_profile_v2.py
git commit -m "refactor(runtime): make main agent profile v2 exclusive"
```

---

### Task 2: Add Immutable Rollout State and Main-Agent-Only Run Shape

**Files:**

- Create: `backend/app/assistant/runtime/__init__.py`
- Create: `backend/app/assistant/runtime/contracts.py`
- Create: `backend/app/assistant/runtime/models.py`
- Create: `backend/app/assistant/runtime/repository.py`
- Create: `backend/alembic/versions/b6e2d4f8a901_main_agent_bootstrap_readiness.py`
- Create: `backend/tests/test_assistant_runtime_models.py`
- Create: `backend/tests/test_assistant_runtime_repository.py`
- Create: `backend/tests/test_assistant_runtime_migration_postgres.py`
- Modify: `backend/app/assistant/models.py`
- Modify: `backend/app/assistant/run_service.py`
- Modify: `backend/alembic/env.py`

**Interfaces:**

- Consumes: Plan 1 revision `9f3c1a7e2b40`, `OperatorPrincipal.operator_id`, current Profile/Model foreign keys, current Run/Event status machinery.
- Produces: `AssistantMainAgentRolloutRevision`, `AssistantMainAgentRolloutControl`, `AssistantMainAgentRolloutEvent`, `AssistantRuntimeRepository`, `AssistantRuntimeClosure`, `AssistantReadinessSnapshot`, and required frozen Run fields.

- [ ] **Step 1: Write failing ORM and repository tests**

```python
def test_control_is_singleton_and_revision_is_immutable(db):
    repo = AssistantRuntimeRepository(db)
    prepared = repo.create_prepared_revision(prepared_revision_fixture())
    control = repo.get_or_create_control_for_update()
    assert control.control_key == "main_agent"
    assert control.active_rollout_revision_id is None
    assert control.state_revision == 0
    assert control.new_runs_enabled is True

    prepared.revision_digest = "0" * 64
    with pytest.raises(IntegrityError):
        db.commit()


def test_request_id_replay_requires_same_digest(db):
    repo = AssistantRuntimeRepository(db)
    event = repo.append_control_event(event_fixture(request_id=FIXED_REQUEST_ID))
    assert repo.find_request_event(FIXED_REQUEST_ID).id == event.id
    with pytest.raises(RuntimeRequestReuseConflict):
        repo.assert_request_replay(
            request_id=FIXED_REQUEST_ID,
            request_digest="f" * 64,
        )
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_models.py \
  tests/test_assistant_runtime_repository.py -q
```

Expected: collection FAIL because `app.assistant.runtime` does not exist.

- [ ] **Step 3: Define frozen contracts with strict digests**

In `contracts.py`, implement a reusable lowercase SHA-256 validator and the stable contracts:

```python
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lowercase hex characters")
    return value


class AssistantRuntimeSubject(FrozenContract):
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure: tuple[dict[str, JsonValue], ...]
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str


class AssistantRuntimeClosure(FrozenContract):
    schema_version: Literal[1] = 1
    rollout_revision_id: UUID
    rollout_revision_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str
    closure_digest: str

    @field_validator(
        "rollout_revision_digest",
        "profile_content_digest",
        "model_identity_digest",
        "package_closure_digest",
        "capability_closure_digest",
        "seed_manifest_digest",
        "capability_feature_digest",
        "closure_digest",
    )
    @classmethod
    def validate_digest(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field_name=info.field_name)


class AssistantReadinessSnapshot(FrozenContract):
    ready: bool
    reason_codes: tuple[str, ...]
    active_rollout_revision_id: UUID | None
    profile_version_id: UUID | None
    model_id: UUID | None
    compatible_worker_ids: tuple[str, ...]
    build_revision: str
```

Apply the same digest validator to the Subject fields. Also define the activation requests/results from the stable interface section and `RUNTIME_READINESS_REASON_CODES` as the exact ordered tuple listed there. The Subject deliberately excludes rollout ID/digest and closure digest, preventing a circular digest calculation.

- [ ] **Step 4: Add the three rollout ORM models**

Use these exact persistent fields:

```python
class AssistantMainAgentRolloutRevision(Base):
    __tablename__ = "assistant_main_agent_rollout_revision"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    revision_label = Column(String(128), nullable=False, unique=True)
    profile_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_profile_version.id"),
        nullable=False,
    )
    profile_content_digest = Column(String(64), nullable=False)
    model_id = Column(UUID(as_uuid=True), ForeignKey("ai_model.id"), nullable=False)
    model_identity_digest = Column(String(64), nullable=False)
    package_closure_json = Column(JSON, nullable=False)
    package_closure_digest = Column(String(64), nullable=False)
    capability_closure_digest = Column(String(64), nullable=False)
    seed_manifest_digest = Column(String(64), nullable=False)
    build_revision = Column(String(128), nullable=False)
    runtime_contract_version = Column(Integer, nullable=False)
    checkpoint_codec_version = Column(Integer, nullable=False)
    capability_feature_digest = Column(String(64), nullable=False)
    revision_digest = Column(String(64), nullable=False, unique=True)
    prepared_by_operator_id = Column(
        UUID(as_uuid=True),
        ForeignKey("operator_account.id"),
        nullable=True,
    )
    prepared_reason = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AssistantMainAgentRolloutControl(Base):
    __tablename__ = "assistant_main_agent_rollout_control"

    control_key = Column(String(32), primary_key=True)
    active_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    state_revision = Column(Integer, nullable=False, server_default=text("0"))
    new_runs_enabled = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AssistantMainAgentRolloutEvent(Base):
    __tablename__ = "assistant_main_agent_rollout_event"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    from_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    to_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_main_agent_rollout_revision.id"),
        nullable=True,
    )
    action = Column(String(32), nullable=False)
    control_revision = Column(Integer, nullable=False)
    request_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    request_digest = Column(String(64), nullable=False)
    operator_id = Column(
        UUID(as_uuid=True), ForeignKey("operator_account.id"), nullable=True
    )
    operator_session_id = Column(
        UUID(as_uuid=True), ForeignKey("operator_session.id"), nullable=True
    )
    reason = Column(String(500), nullable=False)
    evidence_digest = Column(String(64), nullable=False)
    result_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
```

Add check constraints for singleton key `main_agent`, non-negative control revision, positive contract/codec, action in `prepared`, `activated`, `superseded`, `new_runs_enabled`, `new_runs_disabled`, and lowercase 64-hex digest shapes.

- [ ] **Step 5: Freeze the exact closure on every Run**

Modify `AssistantChatRun`:

```python
runtime_kind = Column(
    String(32), nullable=False, default="main_agent", server_default="main_agent"
)
main_agent_rollout_revision_id = Column(
    UUID(as_uuid=True),
    ForeignKey("assistant_main_agent_rollout_revision.id"),
    nullable=False,
)
main_agent_profile_version_id = Column(
    UUID(as_uuid=True),
    ForeignKey("assistant_main_agent_profile_version.id"),
    nullable=False,
)
resolved_model_id = Column(
    UUID(as_uuid=True), ForeignKey("ai_model.id"), nullable=False
)
runtime_closure_digest = Column(String(64), nullable=False)
runtime_contract_version = Column(Integer, nullable=False)
required_checkpoint_codec_version = Column(Integer, nullable=False)
required_capability_feature_digest = Column(String(64), nullable=False)
required_app_build_revision = Column(String(128), nullable=False)
```

Replace the runtime checks with:

```python
CheckConstraint(
    "runtime_kind = 'main_agent'",
    name="ck_assistant_chat_run_main_agent_only",
),
CheckConstraint(
    "runtime_contract_version > 0 AND required_checkpoint_codec_version > 0",
    name="ck_assistant_chat_run_positive_runtime_contract",
),
CheckConstraint(
    "runtime_closure_digest ~ '^[0-9a-f]{64}$' "
    "AND required_capability_feature_digest ~ '^[0-9a-f]{64}$'",
    name="ck_assistant_chat_run_runtime_digests",
),
```

For SQLite model tests, use ORM validation for digest shape and keep PostgreSQL regex constraints in the migration.

Extend the existing PostgreSQL Run immutability trigger so UPDATE cannot change `runtime_kind`, `main_agent_rollout_revision_id`, `main_agent_profile_version_id`, `resolved_model_id`, `runtime_closure_digest`, `runtime_contract_version`, `required_checkpoint_codec_version`, `required_capability_feature_digest`, `required_app_build_revision`, or frozen `capability_ledger_mode`. Status, lease, recovery, checkpoint, and terminal fields retain their existing controlled state-machine updates.

- [ ] **Step 6: Make Run creation explicit and impossible to default to Legacy**

```python
def create_run(
    self,
    *,
    conversation: Conversation,
    user_message: Message,
    assistant_message: Message,
    main_agent_rollout_revision_id: UUID,
    main_agent_profile_version_id: UUID,
    resolved_model_id: UUID,
    runtime_closure_digest: str,
    runtime_contract_version: int,
    required_checkpoint_codec_version: int,
    required_capability_feature_digest: str,
    required_app_build_revision: str,
    capability_ledger_mode: str,
    deadline_at: datetime | None = None,
    commit: bool = False,
) -> AssistantChatRun:
    run = AssistantChatRun(
        conversation_id=conversation.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        runtime_kind="main_agent",
        main_agent_rollout_revision_id=main_agent_rollout_revision_id,
        main_agent_profile_version_id=main_agent_profile_version_id,
        resolved_model_id=resolved_model_id,
        runtime_closure_digest=require_sha256(
            runtime_closure_digest, field_name="runtime_closure_digest"
        ),
        runtime_contract_version=runtime_contract_version,
        required_checkpoint_codec_version=required_checkpoint_codec_version,
        required_capability_feature_digest=require_sha256(
            required_capability_feature_digest,
            field_name="required_capability_feature_digest",
        ),
        required_app_build_revision=required_app_build_revision,
        capability_ledger_mode=capability_ledger_mode,
        memory_commit_status="pending",
        deadline_at=deadline_at,
    )
    self.db.add(run)
    self.db.flush()
    if commit:
        self.db.commit()
        self.db.refresh(run)
    return run
```

No call site passes `runtime_kind`.

- [ ] **Step 7: Implement locking, immutability, replay, and CAS repository methods**

The repository surface is exact:

```python
class AssistantRuntimeRepository:
    def get_or_create_control_for_update(
        self,
    ) -> AssistantMainAgentRolloutControl: ...

    def get_active_revision_for_update(
        self,
    ) -> AssistantMainAgentRolloutRevision | None: ...

    def create_prepared_revision(
        self, data: PreparedRolloutRevision
    ) -> AssistantMainAgentRolloutRevision: ...

    def find_request_event(
        self, request_id: UUID
    ) -> AssistantMainAgentRolloutEvent | None: ...

    def append_control_event(
        self, event: NewRolloutEvent
    ) -> AssistantMainAgentRolloutEvent: ...

    def compare_and_set_control(
        self,
        *,
        expected_state_revision: int,
        active_rollout_revision_id: UUID | None,
        new_runs_enabled: bool,
    ) -> AssistantMainAgentRolloutControl: ...
```

Use `SELECT ... FOR UPDATE` for control and target revision. `compare_and_set_control()` issues one `UPDATE ... WHERE state_revision = expected` that increments `state_revision`; zero affected rows raises `RuntimeControlConflict`. Repository methods flush but never commit.

Construct revision identity from a precomputed non-circular Subject:

```python
@classmethod
def from_subject(
    cls,
    *,
    subject: AssistantRuntimeSubject,
    revision_id: UUID,
    prepared_by_operator_id: UUID | None,
    prepared_reason: str,
) -> "PreparedRolloutRevision":
    identity = {
        "schemaVersion": 1,
        "rolloutRevisionId": str(revision_id),
        **subject.model_dump(mode="json", by_alias=True),
    }
    revision_digest = sha256_canonical_json(identity)
    return cls(
        id=revision_id,
        revision_label=f"main-agent-{revision_digest[:24]}",
        revision_digest=revision_digest,
        prepared_by_operator_id=prepared_by_operator_id,
        prepared_reason=prepared_reason,
        **subject_to_persistent_fields(subject),
    )
```

The revision UUID is UUIDv5 of the request ID in the runtime rollout namespace. The revision digest includes that UUID and every runtime Subject field, but excludes actor, reason, timestamps, and itself. Closure construction can then include both revision UUID/digest without circular input.

- [ ] **Step 8: Write the PostgreSQL migration preflight tests**

```python
def test_upgrade_rejects_legacy_run(postgres_at_plan1_head):
    insert_minimal_chat_run(postgres_at_plan1_head, runtime_kind="legacy")
    result = run_alembic_upgrade(postgres_at_plan1_head, "b6e2d4f8a901")
    assert result.returncode != 0
    assert "legacy_upgrade_not_supported" in result.stderr


def test_upgrade_rejects_nonempty_main_agent_run(postgres_at_plan1_head):
    insert_minimal_chat_run(postgres_at_plan1_head, runtime_kind="main_agent")
    result = run_alembic_upgrade(postgres_at_plan1_head, "b6e2d4f8a901")
    assert result.returncode != 0
    assert "schema_incompatible" in result.stderr


def test_fresh_upgrade_has_main_agent_only_shape(postgres_at_plan1_head):
    run_alembic_upgrade_checked(postgres_at_plan1_head, "b6e2d4f8a901")
    assert current_heads(postgres_at_plan1_head) == {"b6e2d4f8a901"}
    assert check_constraint_contains(
        postgres_at_plan1_head,
        "assistant_chat_run",
        "ck_assistant_chat_run_main_agent_only",
        "runtime_kind = 'main_agent'",
    )


def test_run_runtime_closure_columns_are_immutable(postgres_at_plan2_head):
    run_id = insert_complete_main_agent_run(postgres_at_plan2_head)
    with pytest.raises(DatabaseError, match="runtime identity is immutable"):
        postgres_at_plan2_head.execute(
            text(
                "UPDATE assistant_chat_run "
                "SET runtime_closure_digest = :digest WHERE id = :run_id"
            ),
            {"digest": "f" * 64, "run_id": run_id},
        )
```

- [ ] **Step 9: Implement revision `b6e2d4f8a901`**

At the very start of `upgrade()`:

```python
bind = op.get_bind()
legacy_count = bind.scalar(
    sa.text(
        "SELECT count(*) FROM assistant_chat_run "
        "WHERE runtime_kind <> 'main_agent'"
    )
)
if int(legacy_count or 0) > 0:
    raise RuntimeError(
        "legacy_upgrade_not_supported: reset this pre-GA database"
    )
run_count = bind.scalar(sa.text("SELECT count(*) FROM assistant_chat_run"))
if int(run_count or 0) > 0:
    raise RuntimeError(
        "schema_incompatible: non-empty Run history requires pre-GA reset"
    )
```

Then create the three rollout tables, add/fill the singleton control row only when initialization later requests it, add the Run columns, make them non-null after the empty-table preflight, replace old constraints, and create immutability triggers:

```sql
CREATE FUNCTION mindatlas_reject_rollout_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'assistant rollout revision is immutable';
END;
$$;

CREATE TRIGGER trg_assistant_rollout_revision_immutable
BEFORE UPDATE OR DELETE ON assistant_main_agent_rollout_revision
FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_rollout_revision_mutation();

CREATE TRIGGER trg_assistant_rollout_event_append_only
BEFORE UPDATE OR DELETE ON assistant_main_agent_rollout_event
FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_rollout_revision_mutation();
```

Replace the existing Run identity trigger function in the same revision so it compares every frozen field listed in Step 5 and raises `assistant Run runtime identity is immutable` before any UPDATE persists.

The downgrade is allowed only when all three rollout tables and `assistant_chat_run` are empty and `APP_ENV=test`; otherwise raise `schema_incompatible`. It never reconstructs Legacy rows.

- [ ] **Step 10: Run focused and PostgreSQL tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_models.py \
  tests/test_assistant_runtime_repository.py \
  tests/test_assistant_chat_run_service.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_runtime_migration_postgres.py -q
```

Expected: all tests PASS; PostgreSQL test summary has zero skipped tests.

- [ ] **Step 11: Inspect the migration and commit**

```bash
.venv/bin/alembic heads
rg -n 'revision = "b6e2d4f8a901"|down_revision = "9f3c1a7e2b40"' \
  alembic/versions/b6e2d4f8a901_main_agent_bootstrap_readiness.py
git diff --check
git add \
  app/assistant/runtime \
  app/assistant/models.py \
  app/assistant/run_service.py \
  alembic/env.py \
  alembic/versions/b6e2d4f8a901_main_agent_bootstrap_readiness.py \
  tests/test_assistant_runtime_models.py \
  tests/test_assistant_runtime_repository.py \
  tests/test_assistant_runtime_migration_postgres.py
git commit -m "feat(runtime): persist immutable main agent rollout"
```

Expected: Alembic reports one head, `b6e2d4f8a901`; both revision lines appear once; `git diff --check` prints nothing.

---

### Task 3: Build the Deterministic Digest-Locked System Seed

**Files:**

- Create: `backend/app/assistant/runtime/seed.py`
- Create: `backend/app/assistant/runtime/system_seed/main-agent-profile.v2.json`
- Create: `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/SKILL.md`
- Create: `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/mindatlas.yaml`
- Create: `backend/app/assistant/runtime/system_seed/manifest.v1.json`
- Create: `backend/app/assistant/runtime/system_seed/expected.py`
- Create: `backend/scripts/build_assistant_system_seed.py`
- Create: `backend/tests/test_assistant_system_seed.py`
- Create: `backend/tests/test_assistant_system_seed_builder.py`
- Modify: `backend/app/assistant/runtime/__init__.py`
- Modify: `backend/app/assistant/durable/codec.py`

**Interfaces:**

- Consumes: Profile V2 parser, Skill package parser/canonicalizer, system Tool definitions, `RUNTIME_CONTRACT_VERSION`, supported checkpoint codec versions, and current Capability feature digest.
- Produces: `AssistantSystemSeedManifest`, `VerifiedAssistantSystemSeed`, `load_verified_assistant_system_seed()`, a canonical generated manifest, and build-owned literal `SEED_MANIFEST_DIGEST`/`SEED_CONTRACT_DIGEST`.

- [ ] **Step 1: Write failing deterministic-seed tests**

```python
def test_embedded_seed_verifies_every_artifact():
    seed = load_verified_assistant_system_seed()
    assert seed.manifest.schema_version == 1
    assert seed.profile.schema_version == 2
    assert seed.profile.control_capability_keys == (
        "skill.search",
        "skill.inject",
        "skill.read_resource",
        "artifact.read",
    )
    assert tuple(binding.key for binding in seed.capability_bindings) == (
        "create_entry",
        "get_entry_detail",
        "search_entries",
    )
    assert seed.manifest.manifest_digest == SEED_MANIFEST_DIGEST
    assert seed.manifest.seed_contract_digest == SEED_CONTRACT_DIGEST


def test_manifest_digest_excludes_only_its_own_field():
    payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    claimed = payload.pop("manifestDigest")
    assert sha256_canonical_json(payload) == claimed


def test_seed_contains_no_unsupported_write():
    seed = load_verified_assistant_system_seed()
    keys = {item.key for item in seed.capability_bindings}
    assert "update_entry" not in keys
    assert "merge_entry" not in keys
    assert "create_relation" not in keys
    assert "relation_followup" not in keys
```

- [ ] **Step 2: Run seed tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_assistant_system_seed.py \
  tests/test_assistant_system_seed_builder.py -q
```

Expected: collection FAIL because seed artifacts and loader do not exist.

- [ ] **Step 3: Add the exact Profile V2 source**

Write `main-agent-profile.v2.json` as canonical source data:

```json
{
  "schemaVersion": 2,
  "basePrompt": "You are the MindAtlas main assistant. Answer directly when no specialized Skill is required. Use published Skills and bound capabilities only. Treat unsupported writes as unsupported; never reinterpret them as create_entry. Recovery stays on the same durable Run.",
  "responseStyle": {
    "grounding": "Prefer the user's MindAtlas knowledge when relevant.",
    "unsupportedWrite": "State that the requested write is not supported."
  },
  "supportedEntrypoints": [
    "assistant_chat"
  ],
  "modelRequirements": {
    "jsonSchema": true,
    "multiToolCalls": true,
    "streaming": true,
    "toolCalling": true
  },
  "controlCapabilityKeys": [
    "skill.search",
    "skill.inject",
    "skill.read_resource",
    "artifact.read"
  ],
  "skillCatalogScope": {
    "mode": "all_published",
    "packageIds": []
  },
  "contextBudget": {
    "maxActiveSkills": 4,
    "maxHistoryCharacters": 24000,
    "maxPromptCharacters": 72000,
    "maxResourceBytesPerCall": 65536,
    "maxSingleSkillInstructionCharacters": 12000,
    "maxSkillInstructionCharacters": 24000,
    "maxToolSummaryCharacters": 24000
  },
  "outputBudget": {
    "maxAgentDepth": 2,
    "maxCapabilityDepth": 4,
    "maxCompletionFollowupRounds": 2,
    "maxCompletionTokens": 4096,
    "maxOuterAgentRounds": 8,
    "maxParallelCalls": 4,
    "maxProviderRounds": 8,
    "maxSameReadSignature": 3,
    "maxTotalCapabilityCalls": 16,
    "maxWallTimeMs": 120000
  },
  "globalSafetyPolicy": {
    "denyByDefault": true
  },
  "runtimePolicy": {
    "recoveryScope": "same_run_only",
    "runtimeKind": "main_agent"
  }
}
```

Validate the camel-case keys against `MainAgentProfileSnapshotV2`; do not duplicate a looser seed-only Profile schema.

- [ ] **Step 4: Add the exact built-in Skill sources**

`SKILL.md`:

```markdown
---
name: mindatlas-universal
description: Search and read MindAtlas knowledge, and propose a new entry only when the user explicitly asks to save new knowledge.
---

# MindAtlas universal

Use `search_entries` before claiming that stored knowledge exists. Use
`get_entry_detail` only for an entry returned by the active context or search.

`create_entry` is the sole supported write. It is available only when the
server's write gate permits it and requires the durable approval flow.

Requests to update, merge, relate, or otherwise mutate existing knowledge are
unsupported. Explain the limitation. Do not translate those requests into
`create_entry`.
```

`mindatlas.yaml`:

```yaml
version: 1
display_name: MindAtlas Universal
legacy_aliases: []

routing:
  include_examples:
    - Search my MindAtlas notes for this topic.
    - Save this as a new MindAtlas entry.
  exclude_examples:
    - Update an existing entry.
    - Merge two entries.
    - Add a relation between entries.
  conflict_rules: []

capabilities:
  - type: tool
    key: search_entries
  - type: tool
    key: get_entry_detail
  - type: tool
    key: create_entry

policy:
  allowed_side_effects:
    - read
    - write
  max_skill_calls: 16
  max_same_read_calls: 3
  requires_terminal_output: true
  terminal_text_allowed: true

provider_aliases: {}
metadata:
  system_seed: "true"
```

The policy declares that the immutable package can propose its sole write. Plan 2 still freezes server write mode `off`, so the declaration alone cannot execute a write.

- [ ] **Step 5: Fix the release codec and define the strict manifest schema**

First make the release codec choice explicit in `durable/codec.py`:

```python
CURRENT_CHECKPOINT_CODEC_VERSION: Final[int] = 3

if CURRENT_CHECKPOINT_CODEC_VERSION not in SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS:
    raise RuntimeError("current checkpoint codec must be supported")
```

Export the constant and test that it is exactly `3`, the schema required by durable CapabilityCall settlement and reconciliation. Do not derive the release value from process configuration.

```python
class SeedArtifact(FrozenContract):
    relative_path: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str


class SeedCapabilityBinding(FrozenContract):
    type: Literal["tool"]
    key: Literal["create_entry", "get_entry_detail", "search_entries"]
    target_contract_digest: str


class SeedBuildCompatibility(FrozenContract):
    runtime_contract_version: int = Field(gt=0)
    checkpoint_codec_version: int = Field(gt=0)
    capability_feature_digest: str


class AssistantSystemSeedManifest(FrozenContract):
    schema_version: Literal[1]
    profile_artifact: SeedArtifact
    skill_artifacts: tuple[SeedArtifact, ...]
    capability_bindings: tuple[SeedCapabilityBinding, ...]
    model_binding_slots: tuple[Literal["assistant"], ...]
    build_compatibility: SeedBuildCompatibility
    seed_contract_digest: str
    manifest_digest: str


@dataclass(frozen=True)
class VerifiedAssistantSystemSeed:
    manifest: AssistantSystemSeedManifest
    profile: MainAgentProfileSnapshotV2
    parsed_skill: ParsedSkillPackage
    capability_bindings: tuple[SeedCapabilityBinding, ...]
```

Reject absolute paths, `..`, symlinks, duplicate normalized paths, unsorted artifact/binding lists, unknown keys, uppercase digests, and any manifest outside `system_seed`.

- [ ] **Step 6: Implement the generator using real parsers and real digests**

The fixed generator has only `--write` and `--check`:

```python
def read_seed_skill_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemSeedInvalid("seed_symlink_forbidden")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files[relative] = path.read_bytes()
    return files


def system_tool_seed_contract_digest(
    *,
    key: str,
    target_identity: str,
    input_schema_digest: str,
    output_schema_digest: str,
    system_tool_contract_set_digest: str,
) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "type": "tool",
            "key": key,
            "targetIdentity": target_identity,
            "inputSchemaDigest": input_schema_digest,
            "outputSchemaDigest": output_schema_digest,
            "systemToolContractSetDigest": (
                system_tool_contract_set_digest
            ),
        }
    )


def resolve_system_tool_contract_digests(
    keys: tuple[str, ...],
) -> list[dict[str, str]]:
    definitions = {
        item.name: item for item in ToolRegistry.list_system_tool_definitions()
    }
    contract_set_digest = compute_system_tool_contract_set_digest()
    bindings: list[dict[str, str]] = []
    for key in sorted(keys):
        if key not in definitions:
            raise SystemSeedInvalid("system_tool_missing")
        input_schema, output_schema = system_tool_schemas(key)
        bindings.append(
            {
                "type": "tool",
                "key": key,
                "targetContractDigest": system_tool_seed_contract_digest(
                    key=key,
                    target_identity=f"system-tool:{key}",
                    input_schema_digest=binding_schema_digest(input_schema),
                    output_schema_digest=binding_schema_digest(output_schema),
                    system_tool_contract_set_digest=contract_set_digest,
                ),
            }
        )
    return bindings


def build_seed_payload() -> tuple[dict[str, object], str, str]:
    profile_bytes = PROFILE_PATH.read_bytes()
    profile = MainAgentProfileSnapshotV2.model_validate_json(profile_bytes)
    parsed_skill = parse_skill_directory_files(
        read_seed_skill_files(SKILL_DIRECTORY),
        expected_root_name=None,
    )
    binding_contracts = resolve_system_tool_contract_digests(
        ("create_entry", "get_entry_detail", "search_entries")
    )
    artifacts = canonical_seed_artifacts(PROFILE_PATH, SKILL_DIRECTORY)
    contract_payload = {
        "schemaVersion": 1,
        "profileContentDigest": profile.content_digest(),
        "skillContentDigest": parsed_skill.content_digest,
        "skillManifestDigest": parsed_skill.manifest_digest,
        "skillResourceIndexDigest": parsed_skill.resource_index_digest,
        "capabilityBindings": binding_contracts,
        "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
        "checkpointCodecVersion": CURRENT_CHECKPOINT_CODEC_VERSION,
        "capabilityFeatureDigest": default_capability_feature_digest(),
    }
    seed_contract_digest = sha256_canonical_json(contract_payload)
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "profileArtifact": artifact_for(PROFILE_PATH),
        "skillArtifacts": artifacts,
        "capabilityBindings": binding_contracts,
        "modelBindingSlots": ["assistant"],
        "buildCompatibility": {
            "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
            "checkpointCodecVersion": CURRENT_CHECKPOINT_CODEC_VERSION,
            "capabilityFeatureDigest": default_capability_feature_digest(),
        },
        "seedContractDigest": seed_contract_digest,
    }
    manifest_digest = sha256_canonical_json(payload)
    payload["manifestDigest"] = manifest_digest
    return payload, manifest_digest, seed_contract_digest
```

Generate `expected.py` from computed values, never from manually supplied strings:

```python
def render_expected_module(
    manifest_digest: str, seed_contract_digest: str
) -> str:
    return (
        '"""Generated build-owned Assistant seed identity."""\n\n'
        f"SEED_MANIFEST_DIGEST = {manifest_digest!r}\n"
        f"SEED_CONTRACT_DIGEST = {seed_contract_digest!r}\n"
    )
```

`--check` rebuilds both outputs in memory, byte-compares them to committed files, prints `assistant system seed: OK`, and exits nonzero on any drift. Writes use a same-directory temporary file plus `os.replace`.

- [ ] **Step 7: Generate the actual manifest and expected module**

Run:

```bash
.venv/bin/python scripts/build_assistant_system_seed.py --write
.venv/bin/python scripts/build_assistant_system_seed.py --check
git diff -- \
  app/assistant/runtime/system_seed/manifest.v1.json \
  app/assistant/runtime/system_seed/expected.py
```

Expected: `--check` prints `assistant system seed: OK`; generated files contain lowercase 64-hex literal digests derived from the checked-in sources. Review the diff; do not accept all-zero, repeated-character, or example digests.

- [ ] **Step 8: Implement fail-closed embedded loading**

```python
@lru_cache(maxsize=1)
def load_verified_assistant_system_seed() -> VerifiedAssistantSystemSeed:
    manifest_payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    manifest = AssistantSystemSeedManifest.model_validate(manifest_payload)
    without_self = dict(manifest_payload)
    claimed_manifest_digest = without_self.pop("manifestDigest")
    if sha256_canonical_json(without_self) != claimed_manifest_digest:
        raise SystemSeedInvalid("manifest_digest_mismatch")
    if claimed_manifest_digest != SEED_MANIFEST_DIGEST:
        raise SystemSeedInvalid("build_manifest_digest_mismatch")
    verify_every_artifact(manifest)
    profile = MainAgentProfileSnapshotV2.model_validate_json(
        PROFILE_PATH.read_bytes()
    )
    parsed_skill = parse_skill_directory_files(
        read_seed_skill_files(SKILL_DIRECTORY),
        expected_root_name=None,
    )
    computed_contract = compute_seed_contract_digest(
        manifest=manifest,
        profile=profile,
        parsed_skill=parsed_skill,
    )
    if computed_contract != manifest.seed_contract_digest:
        raise SystemSeedInvalid("seed_contract_digest_mismatch")
    if computed_contract != SEED_CONTRACT_DIGEST:
        raise SystemSeedInvalid("build_seed_contract_digest_mismatch")
    return VerifiedAssistantSystemSeed(
        manifest=manifest,
        profile=profile,
        parsed_skill=parsed_skill,
        capability_bindings=manifest.capability_bindings,
    )
```

The loader accepts no path, URL, environment override, request payload, or fallback content.

- [ ] **Step 9: Prove drift detection and generator idempotency**

```python
def test_check_detects_profile_drift(tmp_seed_tree):
    mutate_json_field(
        tmp_seed_tree / "main-agent-profile.v2.json",
        "basePrompt",
        "changed",
    )
    result = run_builder("--check", seed_root=tmp_seed_tree)
    assert result.returncode == 1
    assert "seed output drift" in result.stderr


def test_write_is_byte_idempotent(tmp_seed_tree):
    run_builder_checked("--write", seed_root=tmp_seed_tree)
    first = snapshot_tree_bytes(tmp_seed_tree)
    run_builder_checked("--write", seed_root=tmp_seed_tree)
    assert snapshot_tree_bytes(tmp_seed_tree) == first
```

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_system_seed.py \
  tests/test_assistant_system_seed_builder.py -q
.venv/bin/python scripts/build_assistant_system_seed.py --check
```

Expected: tests PASS and seed check prints `assistant system seed: OK`.

- [ ] **Step 10: Commit**

```bash
git add \
  backend/app/assistant/runtime/seed.py \
  backend/app/assistant/runtime/__init__.py \
  backend/app/assistant/durable/codec.py \
  backend/app/assistant/runtime/system_seed \
  backend/scripts/build_assistant_system_seed.py \
  backend/tests/test_assistant_system_seed.py \
  backend/tests/test_assistant_system_seed_builder.py
git commit -m "feat(runtime): add digest locked assistant system seed"
```

---

### Task 4: Integrate Trusted Bootstrap into the Initialization Transaction

**Files:**

- Create: `backend/app/assistant/runtime/bootstrap.py`
- Create: `backend/tests/test_assistant_system_bootstrap.py`
- Create: `backend/tests/test_assistant_initialization_atomicity_postgres.py`
- Modify: `backend/app/assistant/runtime/contracts.py`
- Modify: `backend/app/assistant/runtime/repository.py`
- Modify: `backend/app/system_settings/initialization_service.py`
- Modify: `backend/app/system_settings/schemas.py`
- Modify: `backend/app/system_settings/router.py`
- Modify: `backend/app/system_settings/initialization_coordinator.py`
- Modify: `backend/tests/test_system_initialization_service.py`
- Modify: `backend/tests/test_system_initialization_concurrency_postgres.py`

**Interfaces:**

- Consumes: `InitializationCoordinator`, `CoreInitializationResult`, staged Operator result, Plan 1 `OperatorAuditRepository.append()`, normal catalog/Profile/Skill repositories, verified seed loader, and runtime repository.
- Produces: `AssistantSystemBootstrapper.stage_bootstrap() -> PreparedAssistantBootstrap`, initialized published system assets, prepared rollout, bootstrap gate-use/audit rows, and safe initialization response fields.

- [ ] **Step 1: Write failing bootstrap-precondition tests**

```python
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("initialized", "system_already_initialized"),
        ("operator_exists", "operator_already_exists"),
        ("published_profile_exists", "profile_already_published"),
        ("active_rollout_exists", "rollout_already_active"),
    ],
)
def test_system_bootstrap_rejects_nonfresh_state(db, state, reason):
    arrange_nonfresh_state(db, state)
    bootstrapper = AssistantSystemBootstrapper(db)
    with pytest.raises(AssistantBootstrapRejected) as exc:
        bootstrapper.lock_and_verify_fresh_preconditions()
    assert exc.value.reason_code == reason
    assert count_seed_owned_rows(db) == 0


def test_stage_bootstrap_never_commits(db, commit_spy):
    permit = AssistantSystemBootstrapper(
        db
    ).lock_and_verify_fresh_preconditions()
    operator, core = stage_operator_and_core_fixture(db)
    AssistantSystemBootstrapper(db).stage_bootstrap(
        bootstrap_request_fixture(
            operator_id=operator.id,
            model_id=core.llm_model_id,
            fresh_permit=permit,
        )
    )
    commit_spy.assert_not_called()
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_assistant_system_bootstrap.py -q
```

Expected: collection FAIL because `AssistantSystemBootstrapper` does not exist.

- [ ] **Step 3: Define the staged bootstrap command/result**

```python
@dataclass(frozen=True)
class StageAssistantBootstrapRequest:
    operator_id: UUID
    operator_session_id: UUID | None
    model_id: UUID
    build_revision: str
    fresh_permit: AssistantBootstrapFreshPermit


@dataclass(frozen=True)
class PreparedAssistantBootstrap:
    skill_package_id: UUID
    skill_version_id: UUID
    profile_id: UUID
    profile_version_id: UUID
    rollout_revision_id: UUID
    rollout_revision_digest: str
    rollout_control_revision: int
    seed_manifest_digest: str
```

`AssistantBootstrapFreshPermit` is an in-process, non-serializable object returned by `lock_and_verify_fresh_preconditions()`. That method runs while initialization is locked and before the Operator is staged; it verifies that the system is uninitialized and no Operator, published Profile, or active rollout exists, then loads the verified build-owned seed. `stage_bootstrap()` requires that exact permit in the same transaction and verifies that the newly staged Operator matches `request.operator_id`. The initialization path passes `operator_session_id=None` because the first Session does not exist until after commit.

- [ ] **Step 4: Implement fresh-state locking and trusted asset staging**

`stage_bootstrap()` performs these steps without a commit:

```python
def stage_bootstrap(
    self, request: StageAssistantBootstrapRequest
) -> PreparedAssistantBootstrap:
    self._require_locked_fresh_permit(request.fresh_permit)
    seed = request.fresh_permit.seed
    self._assert_build_compatible(seed, request.build_revision)
    bindings = self._resolve_exact_system_bindings(seed.capability_bindings)
    package, version = self._stage_system_skill(seed, bindings)
    profile, profile_version = self._stage_system_profile(
        seed.profile, package_id=package.id
    )
    subject = self.closure_builder.build_subject(
        profile_version=profile_version,
        model_id=request.model_id,
        seed=seed,
        bindings=bindings,
        build_revision=request.build_revision,
    )
    bootstrap_request_id = uuid5(
        ASSISTANT_ROLLOUT_NAMESPACE,
        (
            f"system-bootstrap:{seed.manifest.manifest_digest}:"
            f"{request.operator_id}"
        ),
    )
    revision_id = uuid5(
        ASSISTANT_ROLLOUT_NAMESPACE,
        f"revision:{bootstrap_request_id}",
    )
    rollout = self.runtime_repo.create_prepared_revision(
        PreparedRolloutRevision.from_subject(
            subject=subject,
            revision_id=revision_id,
            prepared_by_operator_id=request.operator_id,
            prepared_reason="system_bootstrap",
        )
    )
    closure = self.closure_builder.build(
        rollout_revision_id=rollout.id,
        lock=True,
    )
    control = self.runtime_repo.get_or_create_control_for_update()
    event = self.runtime_repo.append_control_event(
        NewRolloutEvent.prepared_from_bootstrap(
            rollout=rollout,
            control_revision=control.state_revision,
            request_id=bootstrap_request_id,
            seed_manifest_digest=seed.manifest.manifest_digest,
        )
    )
    self._stage_bootstrap_gate_use(event=event, closure=closure)
    return PreparedAssistantBootstrap(
        skill_package_id=package.id,
        skill_version_id=version.id,
        profile_id=profile.id,
        profile_version_id=profile_version.id,
        rollout_revision_id=rollout.id,
        rollout_revision_digest=rollout.revision_digest,
        rollout_control_revision=control.state_revision,
        seed_manifest_digest=seed.manifest.manifest_digest,
    )
```

Use the normal Skill parser and immutable version/resource tables. Set system ownership, published pointers, `catalog_enabled=True`, Profile `runtime_enabled=True`, and origin `system_bootstrap` only through repository/service staging methods that accept no arbitrary HTTP content. `runtime_enabled` means publish eligibility; it is not the active rollout pointer.

- [ ] **Step 5: Resolve bindings and Model identity deterministically**

Resolve the parsed Skill declarations through the existing immutable binding resolver:

```python
declarations = tuple(seed.parsed_skill.manifest.capabilities)
resolved = CapabilityReferenceResolver(self.db).resolve_many(declarations)
expected_by_key = {
    item.key: item.target_contract_digest
    for item in seed.capability_bindings
}
for binding in resolved:
    seed_contract_digest = system_tool_seed_contract_digest(
        key=binding.capability_key,
        target_identity=binding.target_identity,
        input_schema_digest=binding.input_schema_digest,
        output_schema_digest=binding.output_schema_digest,
        system_tool_contract_set_digest=(
            binding.resolution_snapshot["systemToolContractSetDigest"]
        ),
    )
    if expected_by_key.get(binding.capability_key) != seed_contract_digest:
        raise AssistantBootstrapRejected("system_binding_digest_mismatch")
```

The generator computes the same seed contract digest from code-native ToolRegistry schemas and `compute_system_tool_contract_set_digest()` without a database or build revision. The normal resolver additionally freezes its actual `target_identity`, schema digests, binding contract digest, dependency closure, and executable `APP_BUILD_REVISION` into the published Skill binding rows.

Resolve the `assistant` `AiComponentBinding` to `request.model_id`; require the same active model row created by core initialization and hash only canonical non-secret identity fields. Never decrypt or probe the credential while constructing release identity.

- [ ] **Step 6: Insert bootstrap evidence without sensitive content**

The `system_bootstrap` gate-use and Operator audit payloads contain only:

```python
safe_evidence = {
    "seedManifestDigest": seed.manifest.manifest_digest,
    "seedContractDigest": seed.manifest.seed_contract_digest,
    "profileVersionId": str(profile_version.id),
    "profileContentDigest": profile_version.content_digest,
    "skillVersionId": str(version.id),
    "skillVersionDigest": version.version_digest,
    "rolloutRevisionId": str(rollout.id),
    "rolloutRevisionDigest": rollout.revision_digest,
    "modelId": str(request.model_id),
    "modelIdentityDigest": closure.model_identity_digest,
    "buildRevision": request.build_revision,
}
```

The request ID for bootstrap is server-derived as UUIDv5 from the seed digest plus newly locked initialization identity, never caller supplied.

- [ ] **Step 7: Insert bootstrap in the coordinator-owned transaction**

The coordinator order is exact:

```python
def initialize(
    self,
    request: InitializeSystemRequest,
    *,
    setup_authorization: SetupAuthorization,
    request_context: RequestSecurityContext,
) -> InitializationCommitResult:
    with self.db.begin():
        self._lock_and_assert_fresh(setup_authorization)
        fresh_permit = (
            self.assistant_bootstrapper.lock_and_verify_fresh_preconditions()
        )
        operator = self.operator_service.stage_initial_operator(
            exact_password=request.operator_password,
            request_context=request_context,
        )
        core = self.system_service.stage_core_initialization(request)
        assistant = self.assistant_bootstrapper.stage_bootstrap(
            StageAssistantBootstrapRequest(
                operator_id=operator.operator_id,
                operator_session_id=None,
                model_id=core.llm_model_id,
                build_revision=self.settings.app_build_revision,
                fresh_permit=fresh_permit,
            )
        )
        self.system_service.stage_initialization_marker(
            locale=core.locale,
            source="user",
        )
        self.audit.append(
            event_type="operator_account_initialized",
            outcome="succeeded",
            context=request_context,
            operator_id=operator.operator_id,
            session_id=None,
            metadata={
                "assistantBootstrap": "prepared",
                "rolloutRevisionDigest": (
                    assistant.rollout_revision_digest
                ),
            },
        )
    issued_session = self.operator_service.issue_initial_session_after_commit(
        operator_id=operator.operator_id,
        request_context=request_context,
    )
    return InitializationCommitResult(
        locale=core.locale,
        operator=operator,
        assistant=assistant,
        issued_session=issued_session,
    )
```

Plan 1's reviewed order is preserved: initialization lock and fresh assistant preflight, initial Operator, `stage_core_initialization()` (locale, credential/model, and system catalogs), trusted Skill, published Profile V2, prepared rollout, marker/evidence, one context-manager commit, then initial Session issuance. No nested service calls `commit()`.

- [ ] **Step 8: Return safe pending-activation state**

Extend the response:

```python
class InitializationCompletionResponse(CamelModel):
    initialized: Literal[True]
    locale: Literal["zh", "en"]
    assistant_bootstrap: Literal["pending_worker"] = "pending_worker"
    prepared_rollout_revision_id: UUID
    rollout_control_revision: int
```

Do not return a manifest body, Profile prompt, package resources, credential identity, or digest closure. The router sets Session/CSRF cookies from `issued_session` after coordinator success and does not activate.

- [ ] **Step 9: Prove atomic rollback and post-commit session order in PostgreSQL**

```python
@pytest.mark.parametrize(
    "failure_point",
    [
        "after_operator",
        "after_model",
        "after_skill",
        "after_profile",
        "after_rollout",
        "before_marker",
    ],
)
def test_initialization_failure_rolls_back_every_owned_row(
    postgres_db, failure_point
):
    coordinator = coordinator_with_failure(postgres_db, failure_point)
    with pytest.raises(InjectedInitializationFailure):
        coordinator.initialize(**valid_initialization_arguments())
    assert initialization_owned_row_counts(postgres_db) == EMPTY_COUNTS


def test_session_is_issued_only_after_commit(postgres_db, timeline):
    coordinator = instrumented_coordinator(postgres_db, timeline)
    coordinator.initialize(**valid_initialization_arguments())
    assert timeline.index("commit") < timeline.index("issue_session")
```

Also retain Plan 1's concurrent setup test: exactly one request commits and the loser returns 409 without creating a second seed/Profile/rollout.

- [ ] **Step 10: Run initialization suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_system_bootstrap.py \
  tests/test_system_initialization_service.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_initialization_atomicity_postgres.py \
  tests/test_system_initialization_concurrency_postgres.py -q
```

Expected: all tests PASS; PostgreSQL test summary has zero skipped tests; failure injection leaves every owned table empty.

- [ ] **Step 11: Commit**

```bash
git add \
  backend/app/assistant/runtime/bootstrap.py \
  backend/app/assistant/runtime/contracts.py \
  backend/app/assistant/runtime/repository.py \
  backend/app/system_settings/initialization_service.py \
  backend/app/system_settings/schemas.py \
  backend/app/system_settings/router.py \
  backend/app/system_settings/initialization_coordinator.py \
  backend/tests/test_assistant_system_bootstrap.py \
  backend/tests/test_assistant_initialization_atomicity_postgres.py \
  backend/tests/test_system_initialization_service.py \
  backend/tests/test_system_initialization_concurrency_postgres.py
git commit -m "feat(runtime): bootstrap prepared rollout atomically"
```

---

### Task 5: Build Canonical Runtime Closure and Shared Readiness

**Files:**

- Create: `backend/app/assistant/runtime/closure.py`
- Create: `backend/app/assistant/runtime/readiness.py`
- Create: `backend/tests/test_assistant_runtime_closure.py`
- Create: `backend/tests/test_assistant_runtime_readiness.py`
- Create: `backend/tests/test_assistant_runtime_readiness_postgres.py`
- Modify: `backend/app/assistant/main_agent/service.py`
- Modify: `backend/app/assistant/main_agent/model_eligibility.py`
- Modify: `backend/app/assistant/runtime/contracts.py`
- Modify: `backend/app/assistant/runtime/repository.py`
- Modify: `backend/app/assistant/runtime/bootstrap.py`

**Interfaces:**

- Consumes: rollout state, published Profile/Skill/Tool rows, deterministic bound Model metadata, seed expected digests, Plan 1 auth availability, Worker registry, process build/new-Run settings, and interim Alembic-head compatibility.
- Produces: `BoundAssistantModelIdentity`, `AssistantRuntimeClosureBuilder.build_subject()`/`build()`/`revalidate()`, `AssistantReadinessService.evaluate()`/`evaluate_locked()`, stable reason ordering, and safe public/authenticated projections.

- [ ] **Step 1: Write failing deterministic Model identity tests**

```python
def test_bound_model_identity_does_not_call_provider(db, provider_call_spy):
    identity = resolve_bound_assistant_model_identity(
        db,
        model_id=BOUND_MODEL_ID,
        app_build_revision="test-build",
    )
    provider_call_spy.assert_not_called()
    assert identity.model_id == BOUND_MODEL_ID
    assert len(identity.identity_digest) == 64


def test_paid_probe_is_diagnostic_only(db, failed_probe):
    before = resolve_bound_assistant_model_identity(
        db,
        model_id=BOUND_MODEL_ID,
        app_build_revision="test-build",
    )
    run_optional_model_probe(db, model_id=BOUND_MODEL_ID)
    after = resolve_bound_assistant_model_identity(
        db,
        model_id=BOUND_MODEL_ID,
        app_build_revision="test-build",
    )
    assert before.identity_digest == after.identity_digest
```

- [ ] **Step 2: Split deterministic identity from optional probe**

```python
@dataclass(frozen=True)
class BoundAssistantModelIdentity:
    model_id: UUID
    model_name: str
    model_type: Literal["llm"]
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    credential_config_digest: str
    model_config_digest: str
    provider_ref_digest: str
    identity_digest: str


def resolve_bound_assistant_model_identity(
    db: Session, *, model_id: UUID, app_build_revision: str
) -> BoundAssistantModelIdentity:
    model, credential = lock_active_model_and_credential(db, model_id=model_id)
    if model.model_type != "llm":
        raise ModelIdentityUnavailable("model_type_unsupported")
    endpoint = build_endpoint_identity(str(credential.base_url))
    provider_ref = create_provider_ref(
        provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
        provider_config_id=credential.id,
        provider_runtime_revision=int(credential.runtime_revision),
        provider_config_digest=_credential_config_digest(
            base_url=str(credential.base_url),
            runtime_revision=int(credential.runtime_revision),
        ),
        adapter_key=OPENAI_ADAPTER_KEY,
        adapter_revision=DEFAULT_ADAPTER_REVISION,
        protocol_revision="1",
        app_build_revision=app_build_revision,
    )
    payload = {
        "modelId": str(model.id),
        "modelName": model.name,
        "modelType": "llm",
        "modelRuntimeRevision": int(model.runtime_revision),
        "credentialId": str(credential.id),
        "credentialRuntimeRevision": int(credential.runtime_revision),
        "credentialConfigDigest": provider_ref.provider_config_digest,
        "modelConfigDigest": build_model_config_digest(
            model_id=model.id,
            model_name=model.name,
            model_type="llm",
            model_runtime_revision=int(model.runtime_revision),
            credential_id=credential.id,
            credential_runtime_revision=int(credential.runtime_revision),
            endpoint_identity=endpoint,
            adapter_key=OPENAI_ADAPTER_KEY,
            adapter_revision=DEFAULT_ADAPTER_REVISION,
            app_build_revision=app_build_revision,
            provider_protocol=OPENAI_CHAT_PROVIDER_PROTOCOL,
            probe_contract_version=PROBE_CONTRACT_VERSION,
        ),
        "providerRefDigest": provider_ref.provider_ref_digest,
    }
    return BoundAssistantModelIdentity(
        model_id=model.id,
        model_name=model.name,
        model_type="llm",
        model_runtime_revision=int(model.runtime_revision),
        credential_id=credential.id,
        credential_runtime_revision=int(credential.runtime_revision),
        credential_config_digest=payload["credentialConfigDigest"],
        model_config_digest=payload["modelConfigDigest"],
        provider_ref_digest=payload["providerRefDigest"],
        identity_digest=sha256_canonical_json(payload),
    )
```

Exclude encrypted credential bytes, credential hints, mutable probe IDs/digests/timestamps/results, and transient availability. Model/credential runtime revisions ensure any execution-sensitive configuration update changes identity even when row IDs stay fixed. Rename the current live-call function to `run_optional_assistant_model_probe()` and keep it behind explicit cost acknowledgement; its diagnostics never enter the deterministic identity or readiness decision.

Make the existing frozen execution identity and decrypt-time recheck match that contract:

```python
class FrozenModelIdentity(FrozenContract):
    model_id: UUID
    model_name: str
    model_type: Literal["llm", "embedding"]
    model_runtime_revision: int
    credential_id: UUID
    credential_runtime_revision: int
    credential_config_digest: str
    model_config_digest: str
    provider_ref_digest: str | None = None
    capability_probe_id: UUID | None = None
    capability_probe_digest: str | None = None
```

`recheck_identity_before_decrypt()` always compares model revision, credential revision, model config digest, and credential config digest. It compares probe identity only when the frozen identity contains an optional diagnostic probe. Fresh bootstrap uses both probe fields as `None`, and `construct_openai_adapter_after_identity_recheck()` does not require a probe. Model capability probe results remain visible diagnostics; they are not an activation/readiness prerequisite.

- [ ] **Step 3: Write failing closure tests**

```python
def test_closure_digest_covers_every_identity(db, prepared_rollout):
    closure = AssistantRuntimeClosureBuilder(db).build(
        rollout_revision_id=prepared_rollout.id
    )
    payload = closure.model_dump(
        mode="json", by_alias=True, exclude={"closure_digest"}
    )
    assert sha256_canonical_json(payload) == closure.closure_digest


@pytest.mark.parametrize(
    "mutation",
    [
        "profile_pointer",
        "model_identity",
        "package_version",
        "tool_binding",
        "seed_digest",
        "build_revision",
        "runtime_contract",
        "checkpoint_codec",
        "feature_digest",
    ],
)
def test_revalidation_rejects_any_closure_drift(db, prepared_rollout, mutation):
    closure = AssistantRuntimeClosureBuilder(db).build(
        rollout_revision_id=prepared_rollout.id
    )
    mutate_runtime_subject(db, mutation)
    with pytest.raises(RuntimeClosureDrift):
        AssistantRuntimeClosureBuilder(db).revalidate(closure)
```

- [ ] **Step 4: Build the closure from canonical sorted payloads**

The closure builder locks the rollout/Profile/Model/Package binding rows and computes:

```python
package_closure = tuple(
    sorted(
        (
            {
                "packageId": str(package.id),
                "versionId": str(version.id),
                "versionDigest": version.version_digest,
                "contentDigest": version.content_digest,
                "resourceMerkleRoot": version.resource_merkle_root,
            }
            for package, version in enabled_published_packages
        ),
        key=lambda item: (item["packageId"], item["versionId"]),
    )
)
capability_closure = tuple(
    sorted(
        (
            {
                "type": binding.capability_type,
                "key": binding.capability_key,
                "targetVersionId": str(binding.target_version_id),
                "targetContractDigest": binding.target_contract_digest,
            }
            for binding in resolved_bindings
        ),
        key=lambda item: (
            item["type"],
            item["key"],
            item["targetVersionId"],
        ),
    )
)
```

`build_subject()` returns an `AssistantRuntimeSubject` from those canonical payloads, deterministic Model identity, expected seed digest, build, runtime contract, current codec `3`, and feature digest. Preparation allocates the UUIDv5 revision ID and computes/persists its revision digest from the Subject as defined in Task 2.

`build()` then loads that immutable row, recomputes the Subject, and constructs the non-circular closure:

```python
closure_payload = {
    "schemaVersion": 1,
    "rolloutRevisionId": str(rollout.id),
    "rolloutRevisionDigest": rollout.revision_digest,
    "profileVersionId": str(subject.profile_version_id),
    "profileContentDigest": subject.profile_content_digest,
    "modelId": str(subject.model_id),
    "modelIdentityDigest": subject.model_identity_digest,
    "packageClosureDigest": subject.package_closure_digest,
    "capabilityClosureDigest": subject.capability_closure_digest,
    "seedManifestDigest": subject.seed_manifest_digest,
    "buildRevision": subject.build_revision,
    "runtimeContractVersion": subject.runtime_contract_version,
    "checkpointCodecVersion": subject.checkpoint_codec_version,
    "capabilityFeatureDigest": subject.capability_feature_digest,
}
closure = AssistantRuntimeClosure(
    **closure_payload,
    closureDigest=sha256_canonical_json(closure_payload),
)
```

`build()` raises `RuntimeClosureDrift(reason_code)` if any recomputed Subject field differs from the immutable rollout row. `revalidate()` also compares the caller's complete closure to this newly built closure.

- [ ] **Step 5: Define an interim schema compatibility port**

Plan 2 must work before Plan 3 replaces the migration chain:

```python
class RuntimeSchemaCompatibility(Protocol):
    def is_compatible(self, db: Session) -> bool: ...


class Plan2AlembicHeadCompatibility:
    expected_head = "b6e2d4f8a901"

    def is_compatible(self, db: Session) -> bool:
        return read_single_alembic_version(db) == self.expected_head
```

`AssistantReadinessService` receives this port. Plan 3 replaces the implementation with `pre_ga_v1` identity verification without changing readiness signatures or reason codes.

- [ ] **Step 6: Write the complete readiness matrix before implementation**

```python
@pytest.mark.parametrize(
    ("arrangement", "expected_reason"),
    [
        ("uninitialized", "system_not_initialized"),
        ("operator_missing", "operator_missing"),
        ("auth_unavailable", "operator_auth_unavailable"),
        ("seed_drift", "system_seed_invalid"),
        ("profile_missing", "profile_unpublished"),
        ("model_missing", "model_unbound"),
        ("no_active_rollout", "rollout_inactive"),
        ("closure_drift", "runtime_closure_drift"),
        ("worker_missing", "worker_unavailable"),
        ("wrong_schema", "schema_incompatible"),
        ("process_switch_off", "new_runs_disabled"),
        ("durable_switch_off", "new_runs_disabled"),
    ],
)
def test_readiness_reason_matrix(runtime_state, arrangement, expected_reason):
    runtime_state.arrange(arrangement)
    snapshot = runtime_state.readiness.evaluate()
    assert snapshot.ready is False
    assert expected_reason in snapshot.reason_codes
```

Also test that multiple reasons use the fixed reason tuple order and never database/query iteration order.

- [ ] **Step 7: Implement one locked evaluator with dependency-aware reasons**

```python
class AssistantReadinessService:
    def evaluate(self) -> AssistantReadinessSnapshot:
        with self.db.begin_nested():
            control = self.repo.get_control()
            return self._evaluate(control=control, lock=False)

    def evaluate_locked(
        self,
        *,
        control: AssistantMainAgentRolloutControl,
    ) -> AssistantReadinessSnapshot:
        return self._evaluate(control=control, lock=True)

    def _evaluate(
        self,
        *,
        control: AssistantMainAgentRolloutControl | None,
        lock: bool,
    ) -> AssistantReadinessSnapshot:
        if not self.schema_compatibility.is_compatible(self.db):
            return self._blocked("schema_incompatible")
        if not self.initialization_probe.is_initialized(self.db):
            return self._blocked("system_not_initialized")
        if not self.operator_probe.operator_exists(self.db):
            return self._blocked("operator_missing")
        availability = OperatorAuthService(self.db).availability()
        if not availability.available:
            return self._blocked("operator_auth_unavailable")
        if not self.seed_probe.is_valid():
            return self._blocked("system_seed_invalid")
        if not self.profile_probe.has_published_v2(self.db):
            return self._blocked("profile_unpublished")
        if not self.model_probe.has_active_assistant_binding(self.db):
            return self._blocked("model_unbound")
        if control is None or control.active_rollout_revision_id is None:
            return self._blocked("rollout_inactive")
        try:
            closure = self.closure_builder.build(
                rollout_revision_id=control.active_rollout_revision_id,
                lock=lock,
            )
        except RuntimeClosureDrift:
            return self._blocked(
                "runtime_closure_drift",
                active_rollout_revision_id=(
                    control.active_rollout_revision_id
                ),
            )
        reasons: set[str] = set()
        if not self.settings.assistant_new_runs_enabled:
            reasons.add("new_runs_disabled")
        if not control.new_runs_enabled:
            reasons.add("new_runs_disabled")
        workers = self._compatible_workers(closure)
        if not workers:
            reasons.add("worker_unavailable")
        ordered = tuple(
            code for code in RUNTIME_READINESS_REASON_CODES if code in reasons
        )
        return AssistantReadinessSnapshot(
            ready=not ordered,
            reason_codes=ordered,
            active_rollout_revision_id=closure.rollout_revision_id,
            profile_version_id=closure.profile_version_id,
            model_id=closure.model_id,
            compatible_worker_ids=tuple(row.worker_id for row in workers),
            build_revision=self.settings.app_build_revision,
        )
```

`_blocked()` builds the same snapshot with one structural reason and no fabricated IDs. Structural prerequisites are dependency-ordered so a fresh database reports exactly `system_not_initialized`, not every downstream consequence. Once the closure exists, independent `new_runs_disabled` and `worker_unavailable` reasons may coexist and are ordered by `RUNTIME_READINESS_REASON_CODES`. `get_control()` is a read-only repository lookup; it never creates the singleton.

- [ ] **Step 8: Ensure readiness is observational and admission can lock**

`evaluate()` performs no writes, no implicit singleton creation, no Provider call, no activation, and no Worker registration. `evaluate_locked()` requires a control row already selected `FOR UPDATE` by admission/activation, so it cannot observe a different active pointer within that transaction.

Add SQL-capture tests:

```python
def test_readiness_performs_no_dml(db, sql_capture):
    AssistantReadinessService(db, settings=test_settings()).evaluate()
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in sql_capture.statements
    )
```

- [ ] **Step 9: Run closure/readiness suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_closure.py \
  tests/test_assistant_runtime_readiness.py \
  tests/test_ai_model_capability_probe_api.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_runtime_readiness_postgres.py -q
```

Expected: all tests PASS; no optional live Provider call is made; PostgreSQL suite has zero skips.

- [ ] **Step 10: Commit**

```bash
git add \
  backend/app/assistant/runtime/closure.py \
  backend/app/assistant/runtime/readiness.py \
  backend/app/assistant/runtime/contracts.py \
  backend/app/assistant/runtime/repository.py \
  backend/app/assistant/runtime/bootstrap.py \
  backend/app/assistant/main_agent/service.py \
  backend/app/assistant/main_agent/model_eligibility.py \
  backend/tests/test_assistant_runtime_closure.py \
  backend/tests/test_assistant_runtime_readiness.py \
  backend/tests/test_assistant_runtime_readiness_postgres.py
git commit -m "feat(runtime): compute canonical assistant readiness"
```

---

### Task 6: Add Prepared Rollout, Activation, and Durable Kill-Switch CAS

**Files:**

- Create: `backend/app/assistant/runtime/activation.py`
- Create: `backend/app/assistant/runtime/router.py`
- Create: `backend/tests/test_assistant_runtime_activation.py`
- Create: `backend/tests/test_assistant_runtime_activation_api.py`
- Create: `backend/tests/test_assistant_runtime_activation_postgres.py`
- Modify: `backend/app/assistant/runtime/contracts.py`
- Modify: `backend/app/assistant/runtime/repository.py`
- Modify: `backend/app/assistant/runtime/closure.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/operator_auth/route_policy.py`

**Interfaces:**

- Consumes: `OperatorPrincipal`, CSRF dependency, published Profile V2, deterministic closure builder, publish/system-bootstrap gate-use evidence, Worker compatibility, append-only events, and runtime control locks.
- Produces: `AssistantRuntimeActivationService.prepare()`, `.activate()`, `.set_new_runs_enabled()`, runtime control-plane HTTP endpoints, and request-replay-safe canonical results.

- [ ] **Step 1: Write failing prepare/activation service tests**

```python
def test_prepare_recomputes_server_owned_subject(db, operator):
    result = activation_service(db).prepare(
        PrepareRolloutRequest(
            profile_version_id=PUBLISHED_PROFILE_VERSION_ID,
            model_id=BOUND_MODEL_ID,
            request_id=REQUEST_ID,
            reason="qualify reviewed profile",
        ),
        principal=operator,
    )
    row = db.get(AssistantMainAgentRolloutRevision, result.revision_id)
    assert row.revision_digest == result.revision_digest
    assert row.prepared_by_operator_id == operator.operator_id
    assert row.package_closure_json == server_recomputed_package_closure(db)


def test_first_activation_sets_pointer_and_enables_new_runs(db, operator):
    prepared = prepared_rollout(db)
    result = activation_service(db).activate(
        prepared.id,
        ActivateRolloutRequest(
            expected_control_revision=0,
            request_id=REQUEST_ID,
            reason="activate initial runtime",
        ),
        principal=operator,
    )
    assert result.active_rollout_revision_id == prepared.id
    assert result.control_revision == 1
    assert result.new_runs_enabled is True
```

- [ ] **Step 2: Write failing idempotency and CAS tests**

```python
def test_identical_activation_retry_replays_exact_result(db, operator):
    request = activate_request(expected=0, request_id=REQUEST_ID)
    first = activation_service(db).activate(
        PREPARED_ID, request, principal=operator
    )
    second = activation_service(db).activate(
        PREPARED_ID, request, principal=operator
    )
    assert second == first
    assert count_events(db, request_id=REQUEST_ID) == 1


def test_request_id_reuse_with_different_body_conflicts(db, operator):
    service = activation_service(db)
    service.activate(
        PREPARED_ID,
        activate_request(expected=0, request_id=REQUEST_ID, reason="first"),
        principal=operator,
    )
    with pytest.raises(RuntimeRequestReuseConflict):
        service.activate(
            PREPARED_ID,
            activate_request(
                expected=0, request_id=REQUEST_ID, reason="changed"
            ),
            principal=operator,
        )


def test_competing_activation_has_one_cas_winner(postgres_runtime, operator):
    outcomes = concurrently_activate_two_revisions(postgres_runtime, operator)
    assert sorted(item.status for item in outcomes) == ["activated", "conflict"]
    assert postgres_runtime.control().state_revision == 1
```

- [ ] **Step 3: Run service tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_assistant_runtime_activation.py -q
```

Expected: collection FAIL because `AssistantRuntimeActivationService` does not exist.

- [ ] **Step 4: Implement server-derived rollout preparation**

Add the exact repository operations consumed by this Task:

```python
def get_control(
    self,
) -> AssistantMainAgentRolloutControl | None: ...

def get_control_for_update(
    self,
) -> AssistantMainAgentRolloutControl | None: ...

def get_revision_for_update(
    self, revision_id: UUID
) -> AssistantMainAgentRolloutRevision | None: ...

def replay_or_conflict(
    self, *, request_id: UUID, request_digest: str
) -> AssistantMainAgentRolloutEvent | None: ...

def lock_request_id(self, request_id: UUID) -> None: ...

def append_activation_events(
    self,
    *,
    previous_revision_id: UUID | None,
    target_revision_id: UUID,
    request: ActivateRolloutRequest,
    request_digest: str,
    principal: OperatorPrincipal,
    result: ActivatedRolloutResult,
    evidence_digest: str,
) -> tuple[AssistantMainAgentRolloutEvent, ...]: ...
```

`get_control()` is read-only. The `*_for_update` methods use `SELECT ... FOR UPDATE`. In PostgreSQL, `lock_request_id()` takes a transaction-scoped advisory lock over `hashtextextended('assistant-runtime:' || request_id, 0)` before replay lookup; SQLite unit tests use a process-local test lock. `replay_or_conflict()` then returns the one identical request event or raises on digest mismatch.

```python
class AssistantRuntimeActivationService:
    def prepare(
        self,
        request: PrepareRolloutRequest,
        *,
        principal: OperatorPrincipal,
    ) -> PreparedRolloutResult:
        request_digest = sha256_canonical_json(
            {
                "action": "prepared",
                "profileVersionId": str(request.profile_version_id),
                "modelId": str(request.model_id),
                "requestId": str(request.request_id),
                "reason": request.reason,
            }
        )
        self.repo.lock_request_id(request.request_id)
        replay = self.repo.replay_or_conflict(
            request_id=request.request_id,
            request_digest=request_digest,
        )
        if replay is not None:
            return PreparedRolloutResult.model_validate(replay.result_json)
        profile_version = self._lock_published_profile_v2(
            request.profile_version_id
        )
        self._require_current_publish_gate_use(profile_version)
        subject = self.closure_builder.build_subject(
            profile_version_id=profile_version.id,
            model_id=request.model_id,
            build_revision=self.settings.app_build_revision,
        )
        self._require_package_gate_uses(subject)
        revision_id = uuid5(
            ASSISTANT_ROLLOUT_NAMESPACE,
            f"revision:{request.request_id}",
        )
        revision = self.repo.create_prepared_revision(
            PreparedRolloutRevision.from_subject(
                subject=subject,
                revision_id=revision_id,
                prepared_by_operator_id=principal.operator_id,
                prepared_reason=request.reason,
            )
        )
        closure = self.closure_builder.build(
            rollout_revision_id=revision.id,
            lock=True,
        )
        control = self.repo.get_or_create_control_for_update()
        result = PreparedRolloutResult.from_rows(revision, control)
        self.repo.append_control_event(
            NewRolloutEvent(
                action="prepared",
                from_rollout_revision_id=control.active_rollout_revision_id,
                to_rollout_revision_id=revision.id,
                control_revision=control.state_revision,
                request_id=request.request_id,
                request_digest=request_digest,
                operator_id=principal.operator_id,
                operator_session_id=principal.session_id,
                reason=request.reason,
                evidence_digest=closure.closure_digest,
                result_json=result.model_dump(mode="json", by_alias=True),
            )
        )
        self.db.commit()
        return result
```

Normal preparation accepts only an already-published Profile V2 and current server-side gate-use records. The trusted initialization bootstrap remains the only path allowed to use `system_bootstrap` evidence.

- [ ] **Step 5: Implement activation revalidation under locks**

The activation sequence is fixed:

```python
def activate(
    self,
    revision_id: UUID,
    request: ActivateRolloutRequest,
    *,
    principal: OperatorPrincipal,
) -> ActivatedRolloutResult:
    request_digest = digest_activation_request(revision_id, request)
    self.repo.lock_request_id(request.request_id)
    replay = self.repo.replay_or_conflict(
        request_id=request.request_id,
        request_digest=request_digest,
    )
    if replay is not None:
        return ActivatedRolloutResult.model_validate(replay.result_json)
    control = self.repo.get_or_create_control_for_update()
    if control.state_revision != request.expected_control_revision:
        raise RuntimeControlConflict
    target = self.repo.get_revision_for_update(revision_id)
    if target is None:
        raise RolloutNotPrepared
    closure = self.closure_builder.build(
        rollout_revision_id=target.id,
        lock=True,
    )
    self._require_current_gate_evidence(target, closure)
    snapshot = self.readiness.evaluate_activation_candidate_locked(
        control=control,
        candidate=closure,
    )
    if "worker_unavailable" in snapshot.reason_codes:
        raise RuntimeActivationRejected("worker_unavailable")
    if snapshot.reason_codes:
        raise RuntimeActivationRejected(snapshot.reason_codes[0])
    first_activation = control.active_rollout_revision_id is None
    effective_new_runs = (
        True if first_activation else bool(control.new_runs_enabled)
    )
    previous_id = control.active_rollout_revision_id
    updated = self.repo.compare_and_set_control(
        expected_state_revision=request.expected_control_revision,
        active_rollout_revision_id=target.id,
        new_runs_enabled=effective_new_runs,
    )
    result = ActivatedRolloutResult.from_rows(updated, target)
    self.repo.append_activation_events(
        previous_revision_id=previous_id,
        target_revision_id=target.id,
        request=request,
        request_digest=request_digest,
        principal=principal,
        result=result,
        evidence_digest=closure.closure_digest,
    )
    self.db.commit()
    return result
```

`evaluate_activation_candidate_locked()` evaluates the supplied candidate closure instead of the active pointer and therefore ignores `rollout_inactive`. It also ignores `new_runs_disabled`: an Operator must be able to switch to a known-good immutable revision while both emergency ceilings remain closed. It still requires initialization, Operator/auth, valid seed, Profile, Model, schema, exact closure/gate evidence, and a compatible Worker. First activation sets durable `new_runs_enabled=true`; a false process ceiling still keeps `/ready` false. Later activation preserves the current durable switch exactly.

When a previous revision exists, `append_activation_events()` writes the request-owned `activated` event and one `superseded` event whose internal request ID is UUIDv5 of the original request ID plus the literal `superseded`. The active request remains uniquely replayable; the derived event records the old revision transition without reusing the caller's unique request ID.

- [ ] **Step 6: Preserve a disabled durable switch across later rollout changes**

```python
def test_later_activation_preserves_disabled_new_runs(db, operator):
    control = active_control(db, new_runs_enabled=False, state_revision=7)
    result = activation_service(db).activate(
        SECOND_PREPARED_ID,
        activate_request(expected=7),
        principal=operator,
    )
    assert result.active_rollout_revision_id == SECOND_PREPARED_ID
    assert result.new_runs_enabled is False
    assert result.control_revision == 8
```

Do not add an activation request field that can override this rule.

- [ ] **Step 7: Implement the durable new-Run switch**

```python
def set_new_runs_enabled(
    self,
    request: SetNewRunsEnabledRequest,
    *,
    principal: OperatorPrincipal,
) -> RuntimeControlResult:
    request_digest = digest_new_runs_request(request)
    self.repo.lock_request_id(request.request_id)
    replay = self.repo.replay_or_conflict(
        request_id=request.request_id,
        request_digest=request_digest,
    )
    if replay is not None:
        return RuntimeControlResult.model_validate(replay.result_json)
    control = self.repo.get_or_create_control_for_update()
    if control.state_revision != request.expected_control_revision:
        raise RuntimeControlConflict
    updated = self.repo.compare_and_set_control(
        expected_state_revision=request.expected_control_revision,
        active_rollout_revision_id=control.active_rollout_revision_id,
        new_runs_enabled=request.enabled,
    )
    result = RuntimeControlResult.from_row(updated)
    self.repo.append_control_event(
        NewRolloutEvent.for_new_runs_switch(
            previous=control,
            updated=updated,
            request=request,
            request_digest=request_digest,
            principal=principal,
            result=result,
        )
    )
    self.db.commit()
    return result
```

The event action is `new_runs_enabled` or `new_runs_disabled`. Existing Run rows are not updated.

- [ ] **Step 8: Add protected routes and exact failure mapping**

```python
@router.post("/rollouts/prepare", status_code=201)
def prepare_rollout(
    body: PrepareRolloutRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    result = AssistantRuntimeActivationService(db).prepare(
        body, principal=principal
    )
    return ApiResponse.ok(result.model_dump(mode="json", by_alias=True))


@router.post("/rollouts/{revision_id}/activate")
def activate_rollout(
    revision_id: UUID,
    body: ActivateRolloutRequest,
    principal: OperatorPrincipal = Depends(require_operator_principal),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ApiResponse:
    result = AssistantRuntimeActivationService(db).activate(
        revision_id, body, principal=principal
    )
    return ApiResponse.ok(result.model_dump(mode="json", by_alias=True))
```

Add the listing and new-Run switch routes with viewer and Operator-plus-CSRF policies respectively. Mount the entire runtime API router under Plan 1's `protected_browser_router`, so the generic same-transaction mutation audit is staged before each POST. The append-only rollout event supplies the action-specific revision/evidence result. Mount only the separate public readiness router directly. Add every route to Plan 1's exhaustive policy inventory.

- [ ] **Step 9: Prove HTTP auth, CSRF, replay, and safe payload behavior**

```python
def test_activation_requires_operator_and_csrf(client, viewer_session):
    response = client.post(
        f"/api/assistant-runtime/rollouts/{PREPARED_ID}/activate",
        cookies=viewer_session.cookies,
        headers=viewer_session.csrf_header,
        json=activate_body(),
    )
    assert response.status_code == 403


def test_activation_response_excludes_sensitive_closure(
    operator_client, prepared_rollout
):
    response = operator_client.post(
        f"/api/assistant-runtime/rollouts/{prepared_rollout.id}/activate",
        json=activate_body(),
    )
    assert response.status_code == 200
    serialized = response.text.lower()
    for fragment in ("prompt", "credential", "api_key", "packageclosurejson"):
        assert fragment not in serialized
```

- [ ] **Step 10: Run activation suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_activation.py \
  tests/test_assistant_runtime_activation_api.py \
  tests/test_route_auth_inventory.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_runtime_activation_postgres.py -q
```

Expected: all tests PASS; concurrent PostgreSQL activation produces one winner; no test is skipped.

- [ ] **Step 11: Commit**

```bash
git add \
  backend/app/assistant/runtime/activation.py \
  backend/app/assistant/runtime/router.py \
  backend/app/assistant/runtime/contracts.py \
  backend/app/assistant/runtime/repository.py \
  backend/app/assistant/runtime/closure.py \
  backend/app/main.py \
  backend/app/operator_auth/route_policy.py \
  backend/tests/test_assistant_runtime_activation.py \
  backend/tests/test_assistant_runtime_activation_api.py \
  backend/tests/test_assistant_runtime_activation_postgres.py
git commit -m "feat(runtime): activate main agent rollout with cas"
```

---

### Task 7: Align Worker Readiness and Claim Compatibility

**Files:**

- Create: `backend/tests/test_assistant_worker_runtime_compatibility.py`
- Create: `backend/tests/test_assistant_worker_claim_compatibility_postgres.py`
- Modify: `backend/app/assistant/durable/worker_registry.py`
- Modify: `backend/app/assistant/durable/leases.py`
- Modify: `backend/app/assistant/durable/repository.py`
- Modify: `backend/app/assistant/worker.py`
- Modify: `backend/app/assistant/runtime/readiness.py`
- Modify: `backend/tests/test_durable_worker_registry.py`
- Modify: `backend/tests/test_durable_worker_lease_postgres.py`

**Interfaces:**

- Consumes: existing `RUNTIME_CONTRACT_VERSION`, supported checkpoint codec tuple, Capability feature digest, Worker registrations, Run-frozen requirements, and schema compatibility port.
- Produces: one canonical `WorkerCompatibility` construction path used by readiness, activation, worker health, and lease claim; safe compatible Worker IDs/ages.

- [ ] **Step 1: Write the failing compatibility matrix**

```python
@pytest.mark.parametrize(
    "drift",
    [
        "build_revision",
        "runtime_contract_version",
        "checkpoint_codec_version",
        "capability_feature_digest",
        "stale_heartbeat",
        "draining",
    ],
)
def test_readiness_rejects_incompatible_worker(runtime_state, drift):
    runtime_state.register_worker()
    runtime_state.drift_worker(drift)
    snapshot = runtime_state.readiness.evaluate()
    assert snapshot.ready is False
    assert "worker_unavailable" in snapshot.reason_codes


def test_two_compatible_workers_are_sorted_and_safe(runtime_state):
    runtime_state.register_worker(worker_id="worker-b:boot")
    runtime_state.register_worker(worker_id="worker-a:boot")
    snapshot = runtime_state.readiness.evaluate()
    assert snapshot.compatible_worker_ids == (
        "worker-a:boot",
        "worker-b:boot",
    )
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_assistant_worker_runtime_compatibility.py -q
```

Expected: FAIL where readiness and claims construct different compatibility requirements or return nondeterministic order.

- [ ] **Step 3: Make `WorkerCompatibility` the sole matcher**

```python
@dataclass(frozen=True)
class WorkerCompatibility:
    app_build_revision: str
    runtime_contract_version: int
    required_checkpoint_codec_version: int
    required_capability_feature_digest: str

    @classmethod
    def from_closure(
        cls, closure: AssistantRuntimeClosure
    ) -> "WorkerCompatibility":
        return cls(
            app_build_revision=closure.build_revision,
            runtime_contract_version=closure.runtime_contract_version,
            required_checkpoint_codec_version=closure.checkpoint_codec_version,
            required_capability_feature_digest=(
                closure.capability_feature_digest
            ),
        )

    @classmethod
    def from_run(
        cls, run: AssistantChatRun
    ) -> "WorkerCompatibility":
        return cls(
            app_build_revision=run.required_app_build_revision,
            runtime_contract_version=run.runtime_contract_version,
            required_checkpoint_codec_version=(
                run.required_checkpoint_codec_version
            ),
            required_capability_feature_digest=(
                run.required_capability_feature_digest
            ),
        )
```

The feature digest is no longer optional for a production Main Agent Run.

- [ ] **Step 4: Return deterministic compatible registrations**

Change the registry API to:

```python
def find_compatible_workers(
    self,
    compatibility: WorkerCompatibility,
    *,
    registration_ttl: timedelta | None = None,
    limit: int = 50,
) -> list[AssistantWorkerRegistration]:
    ...
```

Filter by database-time heartbeat cutoff, `draining_at IS NULL`, exact build/contract/feature digest, then require codec membership. Order by `worker_id ASC` after compatibility filtering. `has_compatible_worker()` delegates with `limit=1`. No readiness call mutates registration state.

- [ ] **Step 5: Make claim checks consume the Run itself**

Before a lease update:

```python
compatibility = WorkerCompatibility.from_run(candidate)
if not compatibility.matches(self.identity):
    self.db.rollback()
    return None
if candidate.runtime_kind != "main_agent":
    raise RuntimeInvariantViolation("non-main-agent Run in live schema")
```

The PostgreSQL claim query locks candidates with `FOR UPDATE SKIP LOCKED`, rechecks compatibility on the locked row, and updates lease owner/generation/status only when requirements match. It never rewrites the Run's requirements to match a Worker.

- [ ] **Step 6: Refuse registration/claim on an incompatible schema**

At Worker startup:

```python
if not runtime_schema_compatibility().is_compatible(db):
    logger.error("assistant_worker_schema_incompatible")
    return WORKER_SCHEMA_INCOMPATIBLE_EXIT
registry.register(identity)
```

Recheck schema compatibility before each claim loop. Plan 3 swaps in family-bound schema identity. Worker health reports only stable reason `schema_incompatible`, never a raw SQL/Alembic error.

- [ ] **Step 7: Prove no incompatible Worker can claim**

```python
@pytest.mark.parametrize(
    "drift",
    [
        "build_revision",
        "runtime_contract_version",
        "checkpoint_codec_version",
        "capability_feature_digest",
    ],
)
def test_incompatible_worker_cannot_claim_run(postgres_runtime, drift):
    run = postgres_runtime.queued_run()
    worker = postgres_runtime.worker_identity_with(drift)
    claimed = postgres_runtime.claim(worker)
    assert claimed is None
    assert postgres_runtime.reload(run.id).status == "queued"
    assert postgres_runtime.reload(run.id).lease_owner is None
```

Also prove two compatible Workers race for one Run and exactly one lease generation increments.

- [ ] **Step 8: Run Worker and PostgreSQL claim suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_worker_runtime_compatibility.py \
  tests/test_durable_worker_registry.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_worker_claim_compatibility_postgres.py \
  tests/test_durable_worker_lease_postgres.py -q
```

Expected: all tests PASS with zero PostgreSQL skips; incompatible workers leave queued Runs unchanged.

- [ ] **Step 9: Commit**

```bash
git add \
  backend/app/assistant/durable/worker_registry.py \
  backend/app/assistant/durable/leases.py \
  backend/app/assistant/durable/repository.py \
  backend/app/assistant/worker.py \
  backend/app/assistant/runtime/readiness.py \
  backend/tests/test_assistant_worker_runtime_compatibility.py \
  backend/tests/test_assistant_worker_claim_compatibility_postgres.py \
  backend/tests/test_durable_worker_registry.py \
  backend/tests/test_durable_worker_lease_postgres.py
git commit -m "feat(runtime): enforce worker closure compatibility"
```

---

### Task 8: Make Chat Admission Atomic and Main-Agent-Only

**Files:**

- Create: `backend/app/assistant/runtime/admission.py`
- Create: `backend/tests/test_assistant_atomic_admission.py`
- Create: `backend/tests/test_assistant_atomic_admission_postgres.py`
- Modify: `backend/app/assistant/service.py`
- Modify: `backend/app/assistant/run_service.py`
- Modify: `backend/app/assistant/runtime/readiness.py`
- Modify: `backend/app/assistant/runtime/closure.py`
- Modify: `backend/tests/test_assistant_service.py`
- Modify: `backend/tests/test_assistant_chat_run_service.py`

**Interfaces:**

- Consumes: control row lock, shared locked readiness, closure builder, Worker snapshot, Profile V2 output budget, explicit Run creation fields, current conversation/message models, and initial Run event format.
- Produces: `AssistantChatAdmissionService.admit_and_create() -> AssistantChatRun`, `NewChatAdmission`, stable `AssistantAdmissionError`, one-commit Message/Run/event creation, and no-residue pre-insert failures.

- [ ] **Step 1: Write failing no-residue tests for every pre-insert gate**

```python
@pytest.mark.parametrize(
    ("arrangement", "reason"),
    [
        ("rollout_inactive", "rollout_inactive"),
        ("closure_drift", "runtime_closure_drift"),
        ("worker_missing", "worker_unavailable"),
        ("process_switch_off", "new_runs_disabled"),
        ("durable_switch_off", "new_runs_disabled"),
        ("schema_incompatible", "schema_incompatible"),
    ],
)
def test_preinsert_admission_failure_has_no_residue(
    db, conversation, arrangement, reason
):
    arrange_runtime_state(db, arrangement)
    before = chat_owned_counts(db, conversation.id)
    with pytest.raises(AssistantAdmissionError) as exc:
        AssistantChatAdmissionService(db).admit_and_create(
            conversation_id=conversation.id,
            user_message="hello",
        )
    assert exc.value.reason_code == reason
    assert chat_owned_counts(db, conversation.id) == before
```

- [ ] **Step 2: Write the successful frozen-closure test**

```python
def test_success_freezes_active_closure_on_one_main_agent_run(
    db, ready_runtime, conversation
):
    run = AssistantChatAdmissionService(db).admit_and_create(
        conversation_id=conversation.id,
        user_message="hello",
    )
    closure = ready_runtime.closure
    assert run.runtime_kind == "main_agent"
    assert run.main_agent_rollout_revision_id == closure.rollout_revision_id
    assert run.main_agent_profile_version_id == closure.profile_version_id
    assert run.resolved_model_id == closure.model_id
    assert run.runtime_closure_digest == closure.closure_digest
    assert run.required_app_build_revision == closure.build_revision
    assert count_initial_events(db, run.id) == 1
```

- [ ] **Step 3: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_assistant_atomic_admission.py -q
```

Expected: collection FAIL because the atomic admission service does not exist.

- [ ] **Step 4: Define stable admission error/result types**

```python
class AssistantAdmissionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        if reason_code not in RUNTIME_READINESS_REASON_CODES:
            reason_code = "runtime_closure_drift"
        super().__init__(reason_code)
        self.reason_code = reason_code


ADMISSION_HTTP_REASON: dict[str, str] = {
    "rollout_inactive": "assistant_rollout_inactive",
    "runtime_closure_drift": "assistant_runtime_closure_drift",
    "worker_unavailable": "assistant_worker_unavailable",
    "new_runs_disabled": "assistant_new_runs_disabled",
}


@dataclass(frozen=True)
class NewChatAdmission:
    closure: AssistantRuntimeClosure
    compatible_worker_ids: tuple[str, ...]
    deadline_at: datetime | None
```

The exception carries no underlying SQL, Profile, Model, Package, or Worker payload.

- [ ] **Step 5: Implement the fixed lock-and-insert order**

```python
def admit_and_create(
    self,
    *,
    conversation_id: UUID,
    user_message: str,
) -> AssistantChatRun:
    try:
        control = self.runtime_repo.get_control_for_update()
        if control is None:
            raise AssistantAdmissionError("rollout_inactive")
        readiness = self.readiness.evaluate_locked(control=control)
        if not readiness.ready:
            raise AssistantAdmissionError(readiness.reason_codes[0])
        rollout_revision_id = readiness.active_rollout_revision_id
        if rollout_revision_id is None:
            raise AssistantAdmissionError("rollout_inactive")
        closure = self.closure_builder.build(
            rollout_revision_id=rollout_revision_id,
            lock=True,
        )
        workers = self.worker_registry.find_compatible_workers(
            WorkerCompatibility.from_closure(closure)
        )
        if not workers:
            raise AssistantAdmissionError("worker_unavailable")
        conversation = self._lock_conversation(conversation_id)
        self._assert_no_active_run(conversation.id)
        admission = NewChatAdmission(
            closure=closure,
            compatible_worker_ids=tuple(row.worker_id for row in workers),
            deadline_at=self._deadline_from_profile(
                closure.profile_version_id
            ),
        )
        user = Message(
            conversation_id=conversation.id,
            role="user",
            content=user_message,
        )
        assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
        )
        self.db.add_all((user, assistant))
        conversation.last_message_at = utcnow()
        self.db.flush()
        run = self.run_service.create_run(
            conversation=conversation,
            user_message=user,
            assistant_message=assistant,
            main_agent_rollout_revision_id=closure.rollout_revision_id,
            main_agent_profile_version_id=closure.profile_version_id,
            resolved_model_id=closure.model_id,
            runtime_closure_digest=closure.closure_digest,
            runtime_contract_version=closure.runtime_contract_version,
            required_checkpoint_codec_version=(
                closure.checkpoint_codec_version
            ),
            required_capability_feature_digest=(
                closure.capability_feature_digest
            ),
            required_app_build_revision=closure.build_revision,
            capability_ledger_mode=self._frozen_ledger_mode(),
            deadline_at=admission.deadline_at,
            commit=False,
        )
        self.run_service.append_event(
            run_id=run.id,
            event_name="run_status",
            event_key=f"run.status:queued:{run.id}",
            payload={"status": "queued", "runtimeKind": "main_agent"},
            commit=False,
        )
        self.db.commit()
        self.db.refresh(run)
        return run
    except AssistantAdmissionError:
        self.db.rollback()
        raise
    except IntegrityError as exc:
        self.db.rollback()
        raise ConcurrentChatAdmission from exc
```

The fixed database lock order is rollout control, active rollout closure rows, compatible Worker snapshot, then conversation. Apply the same order to any code path that combines these locks.

- [ ] **Step 6: Delegate `AssistantService.chat_stream()` to admission**

Replace pre-created messages and `admit_and_select_runtime()` with:

```python
def chat_stream(
    self,
    conversation_id: UUID,
    user_message: str,
    *,
    stream_output: bool = True,
) -> Iterator[bytes]:
    try:
        run = AssistantChatAdmissionService(self.db).admit_and_create(
            conversation_id=conversation_id,
            user_message=user_message,
        )
    except AssistantAdmissionError as exc:
        raise ApiException(
            status_code=503,
            code=50310,
            message="Assistant is not ready to accept a new Run.",
            details={
                "admissionReason": ADMISSION_HTTP_REASON.get(
                    exc.reason_code, exc.reason_code
                )
            },
        ) from exc
    except ConcurrentChatAdmission as exc:
        raise ApiException(
            status_code=409,
            code=42260,
            message="Conversation already has an active Run.",
        ) from exc
    yield from self.stream_run(
        conversation_id, run_id=run.id, after_seq=0
    )
```

Do not inspect a runtime selector or spawn a background Legacy daemon.

- [ ] **Step 7: Prove concurrent admission creates one complete unit**

```python
def test_concurrent_chat_admission_has_one_complete_winner(
    postgres_runtime, conversation
):
    outcomes = concurrently_admit(
        postgres_runtime, conversation.id, ("first", "second")
    )
    assert sorted(item.status for item in outcomes) == ["conflict", "created"]
    counts = postgres_runtime.chat_owned_counts(conversation.id)
    assert counts == {
        "user_messages": 1,
        "assistant_messages": 1,
        "runs": 1,
        "initial_events": 1,
    }
```

Prove forced errors after each flush roll back all four row types.

- [ ] **Step 8: Prove post-insert failure stays on the same Run**

```python
def test_worker_failure_after_insert_marks_existing_run_failed(
    postgres_runtime, ready_runtime, conversation
):
    run = postgres_runtime.admit(conversation.id, "hello")
    postgres_runtime.execute_with_injected_provider_failure(run.id)
    failed = postgres_runtime.reload_run(run.id)
    assert failed.id == run.id
    assert failed.status == "failed"
    assert postgres_runtime.count_runs(conversation.id) == 1
    assert postgres_runtime.count_legacy_daemon_starts() == 0
```

- [ ] **Step 9: Run unit, service, and PostgreSQL atomicity suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_assistant_atomic_admission.py \
  tests/test_assistant_service.py \
  tests/test_assistant_chat_run_service.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
  tests/test_assistant_atomic_admission_postgres.py -q
```

Expected: all tests PASS; PostgreSQL suite has zero skips; every successful row has `runtime_kind=main_agent`.

- [ ] **Step 10: Commit**

```bash
git add \
  backend/app/assistant/runtime/admission.py \
  backend/app/assistant/runtime/readiness.py \
  backend/app/assistant/runtime/closure.py \
  backend/app/assistant/service.py \
  backend/app/assistant/run_service.py \
  backend/tests/test_assistant_atomic_admission.py \
  backend/tests/test_assistant_atomic_admission_postgres.py \
  backend/tests/test_assistant_service.py \
  backend/tests/test_assistant_chat_run_service.py
git commit -m "feat(runtime): admit chat atomically to main agent"
```

---

### Task 9: Remove Live Fallback and Legacy Migration Imports

**Files:**

- Create: `backend/app/assistant/memory_migration_state.py`
- Create: `backend/tests/assistant_runtime_support.py`
- Create: `backend/tests/test_main_agent_only_live_imports.py`
- Delete: `backend/app/assistant/durable/admission.py`
- Delete: `backend/app/assistant/main_agent/rollout.py`
- Delete: `backend/tests/test_ai_runtime_fallback_boundary.py`
- Delete: `backend/tests/test_ai_runtime_rollout.py`
- Delete: `backend/tests/test_durable_rollout_task10.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/assistant/durable/__init__.py`
- Modify: `backend/app/assistant/durable/repository.py`
- Modify: `backend/app/assistant/main_agent/service.py`
- Modify: `backend/app/assistant/main_agent/prompt_builder.py`
- Modify: `backend/app/assistant/capability_calls/release_admission.py`
- Modify: `backend/app/assistant/memory_service.py`
- Modify: `backend/app/assistant/service.py`
- Modify: `backend/app/assistant/models.py`
- Modify: `backend/tests/test_agent_policy_runtime.py`
- Modify: `backend/tests/test_ai_runtime_legacy_cleanup.py`
- Modify: `backend/tests/test_ai_runtime_migration_repository_postgres.py`
- Modify: `backend/tests/test_durable_main_agent_runner.py`
- Modify: `backend/tests/test_durable_run_migration_postgres.py`
- Modify: `backend/tests/test_durable_run_models.py`
- Modify: `backend/tests/test_durable_run_repository.py`
- Modify: `backend/tests/test_durable_run_streaming.py`
- Modify: `backend/tests/test_main_agent_golden_create_entry.py`
- Modify: `backend/tests/test_main_agent_profile_service.py`
- Modify: `backend/tests/test_main_agent_runtime.py`
- Modify: `backend/tests/test_assistant_chat_run_stream.py`
- Modify: `backend/tests/test_assistant_chat_stop.py`
- Modify: `backend/tests/test_assistant_service_l1_summary.py`
- Modify: `backend/tests/test_assistant_service_l2_memory.py`
- Modify: `backend/tests/test_durable_audit_fixes.py`
- Modify: `backend/tests/test_skill_eval_worker.py`

**Interfaces:**

- Consumes: new runtime package and Main-Agent-only Run fixture fields.
- Produces: zero live imports from `app.assistant.migration`, no selector/fallback module, a runtime-safe L2 migration-state reader, and a reusable complete Run fixture for surviving tests.

- [ ] **Step 1: Write a failing AST import-boundary test**

```python
LIVE_APP_ROOT = Path(__file__).parents[1] / "app"
ARCHIVED_RUNTIME_PACKAGE = LIVE_APP_ROOT / "assistant" / "migration"


def test_live_application_never_imports_assistant_migration():
    violations: list[str] = []
    for path in sorted(LIVE_APP_ROOT.rglob("*.py")):
        if path.is_relative_to(ARCHIVED_RUNTIME_PACKAGE):
            continue
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            if any(
                name == "app.assistant.migration"
                or name.startswith("app.assistant.migration.")
                for name in modules
            ):
                violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_removed_runtime_selector_symbols_have_no_live_callers():
    forbidden = {
        "admit_and_select_runtime",
        "admit_with_rollout",
        "validate_runtime_rollout_startup",
        "decide_assigned_runtime_kind",
    }
    assert find_live_symbol_references(LIVE_APP_ROOT, forbidden) == []
```

- [ ] **Step 2: Run the boundary test and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_main_agent_only_live_imports.py -q
```

Expected: FAIL listing `app/main.py`, `app/assistant/memory_service.py`, and the old admission/rollout callers.

- [ ] **Step 3: Remove startup selector validation and old modules**

Delete the import/call of `validate_runtime_rollout_startup()` from lifespan. Delete `durable/admission.py`, its `durable.__init__` export, and the obsolete golden runtime rollout module. All production admission now resolves through `app.assistant.runtime.admission`.

The lifespan remains:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        load_verified_assistant_system_seed()
    except SystemSeedInvalid as exc:
        logger.error(
            "assistant_system_seed_invalid reason=%s", exc.reason_code
        )
    warm_assistant_config_system_catalog()
    setup_scheduler()
    yield
    shutdown_scheduler()
```

If seed validation fails at startup, log only `assistant_system_seed_invalid` plus a bounded reason code and continue serving process liveness, login, and diagnostics. `/ready` then returns `system_seed_invalid`, while bootstrap, activation, and Chat fail closed. Never print seed/Profile content.

- [ ] **Step 4: Extract the live L2 state reader from the migration package**

Move only the non-mutating state query used by `memory_service.py`:

```python
@dataclass(frozen=True)
class L2MemoryMigrationState:
    usable: bool
    reason_code: str | None


def read_l2_memory_migration_state(db: Session) -> L2MemoryMigrationState:
    row = db.scalar(
        select(AppSetting).where(
            AppSetting.key == "assistant_l2_memory_migration_state"
        )
    )
    payload = dict(row.value_json) if row and row.value_json else {}
    return L2MemoryMigrationState(
        usable=payload.get("verified") is True,
        reason_code=(
            None
            if payload.get("verified") is True
            else "l2_memory_not_verified"
        ),
    )
```

Import this module from `memory_service.py`. It has no mutation, discovery, Legacy-name, or migration CLI dependency.

- [ ] **Step 5: Remove fallback fields from Main Agent admission**

Change:

```python
@dataclass(frozen=True)
class MainAgentAdmission:
    execution_kind: MainAgentExecutionKind
    profile: AssistantMainAgentProfile
    profile_version: AssistantMainAgentProfileVersion
    snapshot: MainAgentProfileSnapshotV2
    main_agent_ref: ResolvedMainAgentRef
    control_keys: tuple[str, ...]
    frozen_model: FrozenModelIdentity
    provider_ref: ProviderRef
    model_ref: ModelRef
    probe_diagnostics: ModelEligibilityReport | None
    effective_policy_digest: str
```

Remove `legacy_runtime_allowed`, `before_side_effects_only`, fallback decisions, safe pre-side-effect fallback results, and code that catches construction errors to select another runtime. `probe_diagnostics` is observational and never changes admission. Provider/worker errors raise into the existing Run state machine.

- [ ] **Step 6: Make stream/repository code Main-Agent-only**

Replace conditional defaults such as:

```python
runtime_kind = str(getattr(run, "runtime_kind", None) or "legacy")
track_attachment = runtime_kind != "main_agent"
```

with the invariant:

```python
if run.runtime_kind != "main_agent":
    raise RuntimeInvariantViolation("live schema contains non-main-agent Run")
```

Remove Legacy attachment/background-generation branches. Keep historical event decoding only where it is required to display exported evidence outside the live Run table; label that decoder `historical_read_only`.

- [ ] **Step 7: Add one complete Main Agent Run test fixture**

```python
@dataclass(frozen=True)
class FrozenRuntimeFields:
    main_agent_rollout_revision_id: UUID
    main_agent_profile_version_id: UUID
    resolved_model_id: UUID
    runtime_closure_digest: str
    runtime_contract_version: int
    required_checkpoint_codec_version: int
    required_capability_feature_digest: str
    required_app_build_revision: str


def frozen_runtime_fields(
    *,
    rollout_revision_id: UUID,
    profile_version_id: UUID,
    model_id: UUID,
    build_revision: str = "test-build",
) -> FrozenRuntimeFields:
    feature = default_capability_feature_digest()
    closure_digest = sha256_canonical_json(
        {
            "rolloutRevisionId": str(rollout_revision_id),
            "profileVersionId": str(profile_version_id),
            "modelId": str(model_id),
            "buildRevision": build_revision,
            "featureDigest": feature,
        }
    )
    return FrozenRuntimeFields(
        main_agent_rollout_revision_id=rollout_revision_id,
        main_agent_profile_version_id=profile_version_id,
        resolved_model_id=model_id,
        runtime_closure_digest=closure_digest,
        runtime_contract_version=RUNTIME_CONTRACT_VERSION,
        required_checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
        required_capability_feature_digest=feature,
        required_app_build_revision=build_revision,
    )
```

Use `dataclasses.asdict()` to pass the fields into surviving direct `AssistantChatRunService.create_run()` tests.

- [ ] **Step 8: Retire selector assertions and rewrite surviving tests**

Delete the three files that test cohort selection, pre-insert Legacy fallback, or old rollout mode percentages. In the listed surviving test files:

- replace `runtime_kind="legacy"` fixtures with the complete frozen Main Agent fixture;
- change “falls back to Legacy” assertions to a typed admission failure with zero residue;
- change post-insert failure assertions to one durable Run;
- keep V1 Profile samples only in the explicit read-only compatibility tests;
- keep `assistant/migration` tests isolated to that package's historical data/manifest behavior and never import them from live services;
- remove any environment selector setup.

The required inventory command after the rewrite is:

```bash
rg -n \
  'ASSISTANT_RUNTIME_MODE|ASSISTANT_RUNTIME_ROLLOUT_REVISION|admit_and_select_runtime|runtime_kind[[:space:]]*=[[:space:]]*"legacy"' \
  tests
```

Expected: only explicit removed-variable rejection tests, historical migration fixtures, and the V1 read-only parser fixture remain. Every match must be asserted by an allowlist in `test_main_agent_only_live_imports.py`.

- [ ] **Step 9: Prevent future live runtime regressions**

Add source assertions:

```python
def test_live_run_model_is_main_agent_only():
    source = (LIVE_APP_ROOT / "assistant" / "models.py").read_text("utf-8")
    assert "runtime_kind = 'main_agent'" in source
    assert "runtime_kind IN ('legacy','main_agent')" not in source


def test_profile_v2_has_no_fallback_field():
    fields = MainAgentProfileSnapshotV2.model_fields
    assert "fallback_policy" not in fields
    assert MainAgentRuntimePolicyV2().runtime_kind == "main_agent"
```

- [ ] **Step 10: Run the focused boundary and affected suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_main_agent_only_live_imports.py \
  tests/test_main_agent_runtime.py \
  tests/test_durable_main_agent_runner.py \
  tests/test_durable_run_models.py \
  tests/test_durable_run_repository.py \
  tests/test_durable_run_streaming.py \
  tests/test_main_agent_profile_service.py \
  tests/test_assistant_service_l1_summary.py \
  tests/test_assistant_service_l2_memory.py -q
```

Expected: all tests PASS and no live import/symbol violation is reported.

- [ ] **Step 11: Run full backend tests before removing historical files**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full backend suite PASS. If an old test relies on runtime selection, classify it explicitly as retired selector coverage or rewrite it to the Main-Agent-only invariant; do not add a compatibility runtime.

- [ ] **Step 12: Commit**

```bash
git add -A \
  backend/app/main.py \
  backend/app/assistant \
  backend/tests
git commit -m "refactor(runtime): remove live legacy selection"
```

---

### Task 10: Split Process Health from Assistant Readiness and Update Deployment Gates

**Files:**

- Create: `backend/tests/test_health_readiness_api.py`
- Create: `backend/tests/test_health_readiness_postgres.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/assistant/runtime/router.py`
- Modify: `backend/app/assistant/runtime/readiness.py`
- Modify: `backend/app/common/responses.py`
- Modify: `deploy/docker-compose.yml`
- Modify: `backend/.env.example`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Consumes: shared readiness service, public safe projection, viewer dependency, process settings, and Compose service lifecycle.
- Produces: database-free `/health`, truthful public `/ready`, authenticated readiness diagnostics, no bootstrap dependency cycle, and CI seed/readiness gates.

- [ ] **Step 1: Write failing health/readiness API tests**

```python
def test_health_does_not_open_database(client, session_local_spy):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}
    session_local_spy.assert_not_called()


def test_public_ready_is_safe_when_uninitialized(client):
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["data"] == {
        "ready": False,
        "reasonCodes": ["system_not_initialized"],
    }
    serialized = response.text.lower()
    for fragment in (
        "rolloutrevisionid",
        "profileversionid",
        "modelid",
        "workerid",
        "prompt",
    ):
        assert fragment not in serialized


def test_authenticated_readiness_returns_safe_diagnostics(
    viewer_client, ready_runtime
):
    response = viewer_client.get("/api/assistant-runtime/readiness")
    assert response.status_code == 200
    assert response.json()["data"]["activeRolloutRevisionId"] == str(
        ready_runtime.rollout_id
    )
```

- [ ] **Step 2: Run the API tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_health_readiness_api.py -q
```

Expected: FAIL because `/ready` and authenticated diagnostics are not mounted.

- [ ] **Step 3: Keep `/health` process-only**

```python
@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ApiResponse.ok({"status": "ok"})
```

It must not depend on `get_db`, initialization, seed state, Worker registration, active rollout, provider reachability, MinIO, or scheduler state. The test patches `SessionLocal`, `get_db`, and readiness construction to raise if called.

- [ ] **Step 4: Add the public and authenticated readiness projections**

```python
@public_router.get("/ready")
def public_ready(db: Session = Depends(get_db)) -> JSONResponse:
    snapshot = AssistantReadinessService(db).evaluate()
    body = ApiResponse.ok(
        {
            "ready": snapshot.ready,
            "reasonCodes": list(snapshot.reason_codes),
        }
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=200 if snapshot.ready else 503,
        content=body,
    )


@router.get("/readiness")
def authenticated_readiness(
    _: OperatorPrincipal = Depends(require_viewer_principal),
    db: Session = Depends(get_db),
) -> ApiResponse:
    snapshot = AssistantReadinessService(db).evaluate()
    return ApiResponse.ok(
        snapshot.model_dump(mode="json", by_alias=True)
    )
```

Mount `public_router` without authentication and `router` under `/api/assistant-runtime`. Keep exact reason ordering from Task 5.

- [ ] **Step 5: Prove ready/503 transitions in PostgreSQL**

```python
def test_readiness_transitions(postgres_runtime):
    assert postgres_runtime.public_ready() == (
        503,
        ("system_not_initialized",),
    )
    postgres_runtime.initialize()
    assert "rollout_inactive" in postgres_runtime.reason_codes()
    postgres_runtime.register_compatible_worker()
    postgres_runtime.activate()
    assert postgres_runtime.public_ready() == (200, ())
    postgres_runtime.disable_new_runs()
    assert postgres_runtime.public_ready() == (
        503,
        ("new_runs_disabled",),
    )
```

Plan 4 will make the post-activation result 503 `pre_ga_launch_unapproved` for production databases.

- [ ] **Step 6: Remove selector variables from Compose**

For API and Assistant Worker:

```yaml
environment:
  APP_BUILD_REVISION: ${APP_BUILD_REVISION:?Set APP_BUILD_REVISION}
  ASSISTANT_NEW_RUNS_ENABLED: ${ASSISTANT_NEW_RUNS_ENABLED:-true}
  ASSISTANT_MAIN_AGENT_WRITE_MODE: ${ASSISTANT_MAIN_AGENT_WRITE_MODE:-off}
```

Remove `ASSISTANT_RUNTIME_MODE` and `ASSISTANT_RUNTIME_ROLLOUT_REVISION` everywhere. API healthcheck stays `/health`; Web depends on API health so setup remains reachable. Do not make API/Web startup depend on Worker health or `/ready`.

- [ ] **Step 7: Add an explicit deployment acceptance check**

Document and use:

```bash
curl --fail --silent --show-error \
  http://localhost:8000/ready
```

Expected after initialization, compatible Worker registration, and activation in Plan 2: HTTP 200 with `ready=true`. Before those steps, curl exits nonzero due to 503. This command is an acceptance gate, not a Compose `depends_on` condition.

- [ ] **Step 8: Add CI seed and PostgreSQL readiness jobs**

The fixed CI commands are:

```bash
cd backend
python scripts/build_assistant_system_seed.py --check
python -m pytest \
  tests/test_health_readiness_api.py \
  tests/test_assistant_runtime_readiness.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  python -m pytest \
  tests/test_health_readiness_postgres.py \
  tests/test_assistant_runtime_activation_postgres.py \
  tests/test_assistant_atomic_admission_postgres.py -q
```

The PostgreSQL job fails if its URL is absent and contains no release-critical skip marker.

- [ ] **Step 9: Validate Compose and run focused tests**

Run:

```bash
docker compose -f deploy/docker-compose.yml config --quiet
cd backend
.venv/bin/python -m pytest tests/test_health_readiness_api.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_health_readiness_postgres.py -q
cd ..
rg -n 'ASSISTANT_RUNTIME_MODE|ASSISTANT_RUNTIME_ROLLOUT_REVISION' \
  backend/.env.example deploy/docker-compose.yml .github/workflows/ci.yml
```

Expected: Compose config exits 0; tests PASS without skip; the removed-variable scan prints nothing.

- [ ] **Step 10: Commit**

```bash
git add \
  backend/app/main.py \
  backend/app/assistant/runtime/router.py \
  backend/app/assistant/runtime/readiness.py \
  backend/app/common/responses.py \
  backend/tests/test_health_readiness_api.py \
  backend/tests/test_health_readiness_postgres.py \
  backend/.env.example \
  deploy/docker-compose.yml \
  .github/workflows/ci.yml
git commit -m "feat(runtime): expose truthful assistant readiness"
```

---

### Task 11: Add Frontend Bootstrap, Activation, Readiness, and Profile V2 UX

**Files:**

- Create: `frontend/src/features/assistant-runtime/api/runtime.ts`
- Create: `frontend/src/features/assistant-runtime/api/runtime.test.ts`
- Create: `frontend/src/features/assistant-runtime/queries.ts`
- Create: `frontend/src/features/assistant-runtime/components/AssistantRuntimeActivationCard.tsx`
- Create: `frontend/src/features/assistant-runtime/components/AssistantRuntimeActivationCard.test.tsx`
- Create: `frontend/src/features/assistant-runtime/components/AssistantReadinessGate.tsx`
- Create: `frontend/src/features/assistant-runtime/components/AssistantReadinessGate.test.tsx`
- Create: `frontend/src/features/assistant-runtime/index.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/features/initialization/api/systemInitialization.ts`
- Modify: `frontend/src/features/initialization/pages/SystemInitializationPage.tsx`
- Modify: `frontend/src/features/initialization/components/InitializationGate.test.tsx`
- Modify: `frontend/src/features/assistant-config/api/main-agent-profiles.ts`
- Modify: `frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.tsx`
- Modify: `frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.test.tsx`
- Modify: `frontend/src/features/assistant/AssistantPage.tsx`
- Modify: `frontend/src/locales/en/common.json`
- Modify: `frontend/src/locales/zh/common.json`

**Interfaces:**

- Consumes: Plan 1 credentialed client/CSRF handling, public 200/503 readiness, viewer diagnostics, prepared rollout response, activation/new-Run endpoints, and Profile V2.
- Produces: status-aware GET support, typed runtime clients/query keys, explicit activation UX, Chat readiness gate, and V1 read-only/V2 editable Profile UX.

- [ ] **Step 1: Write failing status-aware API tests**

```typescript
it('returns a typed public readiness body for HTTP 503', async () => {
  server.use(
    http.get('/ready', () =>
      HttpResponse.json(
        {
          code: 0,
          message: 'ok',
          data: { ready: false, reasonCodes: ['worker_unavailable'] },
        },
        { status: 503 },
      ),
    ),
  )

  await expect(getPublicAssistantReadiness()).resolves.toEqual({
    ready: false,
    reasonCodes: ['worker_unavailable'],
  })
})

it('sends activation through the cookie and csrf client', async () => {
  const result = await activateAssistantRollout('revision-id', {
    expectedControlRevision: 2,
    requestId: '4f99cdf9-1952-4f2f-9558-cd56f89211af',
    reason: 'activate reviewed runtime',
  })
  expect(result.controlRevision).toBe(3)
  expect(observedRequest.credentials).toBe('include')
  expect(observedRequest.headers.get('X-MindAtlas-CSRF')).toBeTruthy()
})
```

- [ ] **Step 2: Run API tests and verify red**

Run:

```bash
cd frontend
npx vitest run src/features/assistant-runtime/api/runtime.test.ts
```

Expected: FAIL because the runtime client and accepted-503 API method do not exist.

- [ ] **Step 3: Add one status-aware GET method without weakening mutations**

Extend Plan 1's credentialed `ApiClient`:

```typescript
export interface StatusAwareResult<T> {
  status: number
  data: T
}

getAllowingStatuses<T>(
  path: string,
  statuses: readonly number[],
  options: ApiCallOptions = {},
): Promise<StatusAwareResult<T>> {
  return this.request<T>(path, {
    ...options,
    method: 'GET',
    acceptedErrorStatuses: new Set(statuses),
    returnStatus: true,
  })
}
```

Inside `request()`, an accepted non-2xx status must still require a structurally valid `ApiResponse<T>` with `code === 0`; malformed bodies and non-allowlisted statuses throw `ApiError`. Do not add accepted statuses to POST/PUT/PATCH/DELETE. Preserve `credentials: 'include'` and Plan 1's automatic CSRF header for mutations.

- [ ] **Step 4: Define exact runtime API types and clients**

```typescript
export type AssistantReadinessReason =
  | 'system_not_initialized'
  | 'operator_missing'
  | 'operator_auth_unavailable'
  | 'system_seed_invalid'
  | 'profile_unpublished'
  | 'model_unbound'
  | 'rollout_inactive'
  | 'runtime_closure_drift'
  | 'worker_unavailable'
  | 'schema_incompatible'
  | 'new_runs_disabled'

export interface PublicAssistantReadiness {
  ready: boolean
  reasonCodes: AssistantReadinessReason[]
}

export interface AssistantReadinessDiagnostics
  extends PublicAssistantReadiness {
  activeRolloutRevisionId: string | null
  profileVersionId: string | null
  modelId: string | null
  compatibleWorkerIds: string[]
  buildRevision: string
}

export async function getPublicAssistantReadiness() {
  const response =
    await apiClient.getAllowingStatuses<PublicAssistantReadiness>(
      '/ready',
      [503],
    )
  return response.data
}

export function getAssistantReadinessDiagnostics() {
  return apiClient.get<AssistantReadinessDiagnostics>(
    '/api/assistant-runtime/readiness',
  )
}

export function activateAssistantRollout(
  revisionId: string,
  body: {
    expectedControlRevision: number
    requestId: string
    reason: string
  },
) {
  return apiClient.post<ActivatedRolloutResult>(
    `/api/assistant-runtime/rollouts/${revisionId}/activate`,
    { body },
  )
}
```

Add prepare/list/new-Run-switch clients using the exact backend camel-case contracts. No caller-selected identity/role Header is exported.

- [ ] **Step 5: Add readiness query and mutation invalidation**

```typescript
export const assistantRuntimeKeys = {
  all: ['assistant-runtime'] as const,
  publicReadiness: () =>
    [...assistantRuntimeKeys.all, 'public-readiness'] as const,
  diagnostics: () =>
    [...assistantRuntimeKeys.all, 'diagnostics'] as const,
  rollouts: () => [...assistantRuntimeKeys.all, 'rollouts'] as const,
}

export function usePublicAssistantReadinessQuery(enabled = true) {
  return useQuery({
    queryKey: assistantRuntimeKeys.publicReadiness(),
    queryFn: getPublicAssistantReadiness,
    enabled,
    refetchInterval: (query) =>
      query.state.data?.ready === false ? 2_000 : 15_000,
  })
}
```

Successful prepare, activation, and switch mutations invalidate public readiness, diagnostics, and rollout lists.

- [ ] **Step 6: Write activation-card behavior tests**

```typescript
it('waits for a compatible worker and does not auto-activate', async () => {
  renderActivationCard({
    diagnostics: {
      ready: false,
      reasonCodes: ['rollout_inactive', 'worker_unavailable'],
      compatibleWorkerIds: [],
    },
  })
  expect(screen.getByText(/waiting for a compatible worker/i)).toBeVisible()
  expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled()
  expect(activateSpy).not.toHaveBeenCalled()
})

it('requires an explicit click and sends current control revision', async () => {
  const user = userEvent.setup()
  renderActivationCard(compatiblePreparedRuntime())
  await user.click(screen.getByRole('button', { name: /activate/i }))
  expect(activateSpy).toHaveBeenCalledWith(
    PREPARED_ID,
    expect.objectContaining({
      expectedControlRevision: 0,
      reason: 'activate prepared Main Agent runtime',
    }),
  )
})
```

- [ ] **Step 7: Implement explicit post-initialization activation UX**

`AssistantRuntimeActivationCard`:

- shows `pending_worker` until at least one compatible Worker ID is present;
- shows one localized, safe explanation per reason code;
- never renders IDs as editable fields;
- enables activation only for a prepared revision, current control revision, and compatible Worker;
- creates a fresh UUID request ID per user click and reuses it only for the same automatic network retry;
- on 409 refreshes diagnostics/control rather than overwriting;
- does not auto-activate in an effect.

After successful initialization, keep the authenticated Session established by Plan 1, preserve the returned `preparedRolloutRevisionId` and `rolloutControlRevision` in component state only, and render this card. Navigate into the application after activation succeeds; if the user leaves, the Settings runtime card can resume from server rollout listing.

- [ ] **Step 8: Add a Chat readiness gate without blocking setup/settings**

```tsx
export function AssistantReadinessGate({
  children,
}: {
  children: ReactNode
}) {
  const readiness = usePublicAssistantReadinessQuery()
  if (readiness.isLoading) {
    return <AssistantReadinessSkeleton />
  }
  if (!readiness.data?.ready) {
    return (
      <AssistantUnavailablePanel
        reasonCodes={readiness.data?.reasonCodes ?? []}
      />
    )
  }
  return <>{children}</>
}
```

Wrap the Chat composer/window, not the root application, login, initialization, Settings, or activation control. Existing conversation read access may remain visible, but sending a new Chat is disabled while not ready.

- [ ] **Step 9: Make Profile V2 editable and V1 explicitly read-only**

```typescript
export interface MainAgentProfileSnapshotV2 {
  schemaVersion: 2
  basePrompt: string
  responseStyle: Record<string, string>
  supportedEntrypoints: ['assistant_chat']
  modelRequirements: {
    toolCalling: boolean
    streaming: boolean
    multiToolCalls: boolean
    jsonSchema: boolean
  }
  controlCapabilityKeys: string[]
  skillCatalogScope: {
    mode: 'all_published' | 'allowlist'
    packageIds: string[]
  }
  contextBudget: Record<string, number>
  outputBudget: Record<string, number>
  globalSafetyPolicy: { denyByDefault: true }
  runtimePolicy: {
    runtimeKind: 'main_agent'
    recoveryScope: 'same_run_only'
  }
}

export type ReadableMainAgentProfileSnapshot =
  | MainAgentProfileSnapshotV1
  | MainAgentProfileSnapshotV2
```

The editor removes both Legacy fallback controls. For V1, render version content and a “historical Profile V1 — read only” banner; disable draft/publish/prepare. New draft payloads are V2 and include the fixed runtime policy.

- [ ] **Step 10: Add complete localized reason copy**

Add English and Chinese copy for every Plan 2 reason. `system_seed_invalid`, `runtime_closure_drift`, and `schema_incompatible` instruct the Operator to stop and inspect deployment integrity; they never offer a bypass. `new_runs_disabled` points to the explicit Operator switch; `worker_unavailable` explains build/contract/codec compatibility.

- [ ] **Step 11: Run focused frontend tests**

Run:

```bash
npx vitest run \
  src/features/assistant-runtime/api/runtime.test.ts \
  src/features/assistant-runtime/components/AssistantRuntimeActivationCard.test.tsx \
  src/features/assistant-runtime/components/AssistantReadinessGate.test.tsx \
  src/features/initialization/components/InitializationGate.test.tsx \
  src/features/assistant-config/pages/MainAgentProfileEditorPage.test.tsx
npm run build
```

Expected: all focused tests PASS and the TypeScript production build exits 0.

- [ ] **Step 12: Commit**

```bash
git add \
  frontend/src/lib/api/client.ts \
  frontend/src/features/assistant-runtime \
  frontend/src/features/initialization \
  frontend/src/features/assistant-config/api/main-agent-profiles.ts \
  frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.tsx \
  frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.test.tsx \
  frontend/src/features/assistant/AssistantPage.tsx \
  frontend/src/locales/en/common.json \
  frontend/src/locales/zh/common.json
git commit -m "feat(runtime): add assistant activation and readiness ui"
```

---

### Task 12: Verify Fresh Compose Bootstrap-to-Chat and Produce Safe Evidence

**Files:**

- Create: `backend/tests/support/openai_stub_server.py`
- Create: `backend/tests/test_main_agent_bootstrap_smoke_script.py`
- Create: `backend/scripts/smoke_main_agent_bootstrap.py`
- Create: `deploy/compose.main-agent-smoke.yml`
- Create at execution: `docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `deploy/README.md`

**Interfaces:**

- Consumes: fresh migration through `b6e2d4f8a901`, Setup-authorized initialization, Session/CSRF, deterministic provider stub, compatible Worker, activation API, public readiness, atomic Chat admission, and safe evidence digest convention.
- Produces: one fixed disposable Compose smoke workflow and a sanitized JSON proof of initialization-to-Main-Agent-Chat.

- [ ] **Step 1: Write failing evidence allowlist tests**

```python
ALLOWED_EVIDENCE_KEYS = {
    "schemaVersion",
    "verificationKind",
    "buildRevision",
    "alembicHead",
    "seedManifestDigest",
    "healthStatus",
    "readinessTransitions",
    "compatibleWorkerCount",
    "activeRuntimeKind",
    "chatRunCount",
    "chatTerminalStatus",
    "testSuites",
    "generatedAtUtc",
    "aggregateDigest",
}


def test_smoke_evidence_has_only_safe_keys(evidence):
    assert set(evidence) == ALLOWED_EVIDENCE_KEYS
    serialized = json.dumps(evidence).lower()
    for fragment in (
        "password",
        "setup",
        "token",
        "cookie",
        "api_key",
        "prompt",
        "entry",
        "artifact",
        "provider_payload",
    ):
        assert fragment not in serialized
```

- [ ] **Step 2: Add a fixed deterministic OpenAI-compatible stub**

The stub accepts only the smoke model `mindatlas-smoke-model` and returns a fixed no-tool streaming completion:

```python
FIXED_CHUNKS = (
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    },
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"content": "smoke-ok"}, "finish_reason": None}
        ],
    },
    {
        "id": "smoke-completion",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
    },
)
```

Reject any other model, non-stream request, tool call request, or outbound network access. Log only method/path/status/counts.

- [ ] **Step 3: Define the disposable smoke overlay**

The overlay:

- uses a unique Compose project name supplied by the fixed runner;
- creates disposable PostgreSQL/MinIO volumes;
- builds API and Assistant Worker from the same immutable `APP_BUILD_REVISION`;
- starts one compatible Assistant Worker;
- starts the fixed provider stub on the internal network only;
- sets `APP_ENV=test`, `ASSISTANT_NEW_RUNS_ENABLED=true`, write mode `off`, and test-only private-provider allowance;
- retains API `/health` and Worker registration healthchecks;
- exposes no provider stub port to the host;
- never embeds a Setup Token, password, session key, provider key, or reusable Fernet key in the committed YAML.

Representative service:

```yaml
services:
  provider-stub:
    image: mindatlas-api-smoke
    command:
      - python
      - -m
      - tests.support.openai_stub_server
    networks:
      - mindatlas-network
  api:
    environment:
      APP_ENV: test
      ASSISTANT_NEW_RUNS_ENABLED: "true"
      ASSISTANT_MAIN_AGENT_WRITE_MODE: "off"
      MINDATLAS_TEST_PROVIDER_HOST: provider-stub
```

- [ ] **Step 4: Implement the fixed smoke transition sequence**

`smoke_main_agent_bootstrap.py` reads ephemeral secrets from protected files named by environment variables, never command-line values. It performs:

```python
assert get("/health").status_code == 200
assert ready() == (503, ("system_not_initialized",))
completion = initialize_with_setup_authorization(
    provider_base_url="http://provider-stub:8089/v1",
    provider_model="mindatlas-smoke-model",
)
assert completion.assistant_bootstrap == "pending_worker"
assert ready().status_code == 503
wait_for_compatible_worker_via_authenticated_diagnostics()
assert "rollout_inactive" in ready().reason_codes
activate(
    completion.prepared_rollout_revision_id,
    completion.rollout_control_revision,
)
wait_until(lambda: ready().status_code == 200)
conversation_id = create_conversation()
run_id = post_chat_and_capture_run_id(
    conversation_id, "Return the deterministic smoke response."
)
terminal = wait_for_run_terminal(conversation_id, run_id)
assert terminal.runtime_kind == "main_agent"
assert terminal.status == "completed"
assert count_runs(conversation_id) == 1
```

HTTP tracing redacts Authorization, Cookie, Set-Cookie, CSRF, request bodies, and response content. It records only statuses, stable reason codes, counts, and safe digests.

- [ ] **Step 5: Generate evidence atomically**

```python
def finalize_evidence(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != ALLOWED_EVIDENCE_KEYS - {"aggregateDigest"}:
        raise EvidenceSchemaError
    digest = sha256_canonical_json(payload)
    return {**payload, "aggregateDigest": digest}
```

Write with `tempfile.NamedTemporaryFile` in the destination directory, `fsync`, permission `0o600`, then `os.replace`. Validate the final file against the allowlist and digest before exit.

- [ ] **Step 6: Unit-test failure cleanup and redaction**

```python
def test_failure_still_runs_compose_down_and_redacts(tmp_path):
    runner = smoke_runner_that_fails_after_initialization(tmp_path)
    result = runner.run()
    assert result.returncode == 1
    assert runner.compose_down_called_with_volumes is True
    combined = result.stdout + result.stderr
    assert runner.setup_secret not in combined
    assert runner.operator_password not in combined
```

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_main_agent_bootstrap_smoke_script.py -q
```

Expected: tests PASS.

- [ ] **Step 7: Run the fresh Compose smoke**

Use ephemeral secret files with mode 0600 and an environment file outside the repository. The runner removes them in `finally`:

```bash
cd backend
.venv/bin/python scripts/smoke_main_agent_bootstrap.py \
  --compose-file ../deploy/docker-compose.yml \
  --overlay-file ../deploy/compose.main-agent-smoke.yml \
  --output ../docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json
```

Expected:

```text
health: ok
initialization: prepared
worker: compatible
activation: committed
readiness: ready
chat: main_agent completed
evidence: verified
```

The command exits 0, always runs `docker compose down --volumes --remove-orphans`, and leaves one evidence file. No secret appears in terminal output.

- [ ] **Step 8: Validate the evidence independently**

Run:

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from app.assistant.domain.digests import sha256_canonical_json

path = Path("../docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json")
payload = json.loads(path.read_text("utf-8"))
claimed = payload.pop("aggregateDigest")
assert sha256_canonical_json(payload) == claimed
assert payload["activeRuntimeKind"] == "main_agent"
assert payload["chatRunCount"] == 1
assert payload["chatTerminalStatus"] == "completed"
print("main agent bootstrap evidence: OK")
PY
```

Expected: prints `main agent bootstrap evidence: OK`.

- [ ] **Step 9: Add the smoke to CI as a required fixed job**

CI builds once with a non-secret synthetic build revision, invokes the fixed script, uploads the sanitized evidence artifact, and fails on a skipped/absent PostgreSQL, Worker, or smoke service. It never uploads Compose environment files, logs containing request bodies, cookie jars, or database volumes.

- [ ] **Step 10: Run final Plan 2 verification**

```bash
cd backend
.venv/bin/python scripts/build_assistant_system_seed.py --check
.venv/bin/python -m pytest \
  tests/test_assistant_runtime_config.py \
  tests/test_main_agent_profile_v2.py \
  tests/test_assistant_system_seed.py \
  tests/test_assistant_system_bootstrap.py \
  tests/test_assistant_runtime_closure.py \
  tests/test_assistant_runtime_readiness.py \
  tests/test_assistant_runtime_activation.py \
  tests/test_assistant_atomic_admission.py \
  tests/test_main_agent_only_live_imports.py \
  tests/test_health_readiness_api.py \
  tests/test_main_agent_bootstrap_smoke_script.py -q
.venv/bin/python -m pytest -q
cd ../frontend
npm test
npm run build
cd ..
git diff --check
```

Expected: seed check passes; focused and full backend pass; frontend tests/build pass; `git diff --check` prints nothing.

- [ ] **Step 11: Commit**

```bash
git add \
  backend/tests/support/openai_stub_server.py \
  backend/tests/test_main_agent_bootstrap_smoke_script.py \
  backend/scripts/smoke_main_agent_bootstrap.py \
  deploy/compose.main-agent-smoke.yml \
  deploy/README.md \
  .github/workflows/ci.yml \
  docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json
git commit -m "test(runtime): verify fresh main agent bootstrap"
```

---

## Plan 2 Exit Gate

Run from a fresh checkout with Python 3.11, Node/npm matching CI, disposable PostgreSQL, Docker, and no reused application volumes:

```bash
git status --short
cd backend
python3.11 -m venv .venv-plan2
.venv-plan2/bin/python -m pip install --upgrade pip
.venv-plan2/bin/python -m pip install -r requirements.txt pytest
.venv-plan2/bin/python scripts/build_assistant_system_seed.py --check
DATABASE_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv-plan2/bin/alembic upgrade b6e2d4f8a901
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv-plan2/bin/python -m pytest -q
.venv-plan2/bin/python scripts/smoke_main_agent_bootstrap.py \
  --compose-file ../deploy/docker-compose.yml \
  --overlay-file ../deploy/compose.main-agent-smoke.yml \
  --output ../docs/superpowers/evidence/2026-07-28-main-agent-bootstrap-readiness.json
cd ../frontend
npm ci
npm test
npm run build
cd ..
git diff --check
```

Expected:

- one Alembic head exists and is `b6e2d4f8a901`;
- the seed generator check is byte-clean and every embedded digest verifies;
- default configuration contains no active/default Legacy selector or rollout label;
- fresh initialization atomically publishes the trusted Skill/Profile V2 and creates one prepared immutable rollout;
- initialization never activates the rollout and issues the first Session only after commit;
- Profile V1 is historical read-only and cannot enter a production operation;
- deterministic Model identity performs no paid Provider call;
- readiness distinguishes initialization, auth, seed, Profile, Model, rollout, closure, Worker, schema, and both new-Run switches;
- activation is Operator-plus-CSRF, CAS-safe, request-replay-safe, and requires one fresh compatible Worker;
- later activation preserves a disabled durable switch;
- Worker claim matches the Run's exact build, runtime contract, codec, and feature digest;
- every new Run is `main_agent`;
- pre-insert failure leaves no Message/Run/event residue;
- post-insert failure remains on one durable Run;
- no live application module imports `app.assistant.migration`;
- `/health` performs no database access, `/ready` is safe/truthful, and Web/setup are not blocked by readiness;
- full backend and frontend suites pass with no release-critical PostgreSQL skip;
- the fresh Compose smoke reaches one completed Main Agent Run;
- evidence contains only allowlisted safe fields;
- `git diff --check` is clean.

## Rollback Boundary

- Before initialization on a disposable pre-GA database, code may be reverted and the database recreated. The test-only downgrade from `b6e2d4f8a901` requires empty rollout/Run tables and never rebuilds Legacy state.
- After Operator authentication has been enabled, do not roll back to unprotected runtime mutations. Apply a forward security fix.
- To stop new work immediately, set `ASSISTANT_NEW_RUNS_ENABLED=false`; then use the authenticated durable switch to set `new_runs_enabled=false` when the API is available.
- Disabling new Runs never changes, deletes, or forks an existing Run. Existing Runs continue to durable completion, failure, cancellation, or reconciliation.
- Runtime rollback prepares or activates an immutable known-good Main Agent revision. It never selects Legacy and never mutates an old revision.
- Write rollback remains `ASSISTANT_MAIN_AGENT_WRITE_MODE=off`; Plan 2 does not enable writes.
- A drifted seed, Profile, Model, Package closure, schema, or Worker identity makes readiness/admission fail closed. Do not edit a digest row to restore service.
- Plan 3 archives `b6e2d4f8a901` into the clean root. After that archive, rollback uses database recreation or a clean-family backup, not the old chain.

## Implementation Stop Conditions

Stop the plan and preserve exact non-secret diagnostics if any condition occurs:

- Plan 1 implementation contracts or revision `9f3c1a7e2b40` are absent;
- the planned revision ID collides or Alembic has multiple heads;
- an embedded artifact digest cannot be reproduced byte-for-byte;
- seed generation would require a live database, network, Provider call, or caller-supplied digest;
- an initialization-owned service commits independently;
- system bootstrap can run after initialization or accept arbitrary caller content;
- a Profile V1 can be published, prepared, activated, or admitted;
- activation can succeed without current closure/gate evidence and one compatible Worker;
- a control/event mutation cannot be made CAS/idempotency/append-only safe;
- readiness and admission compute different closure or Worker results;
- a pre-insert failure leaves any Message, Run, or event;
- an incompatible Worker can claim a Run;
- a live source import reaches `app.assistant.migration`;
- `/health` needs database or Worker access;
- a release-critical PostgreSQL/Compose test skips;
- any secret, Prompt, Entry content, or raw request payload enters logs/evidence.

## Authoring Self-Review Record

- Spec coverage: Task 1 removes runtime selection and creates Profile V2; Task 2 creates immutable rollout/control/event state and Main-Agent-only Run shape; Task 3 creates the digest-locked trusted seed; Task 4 integrates it atomically into initialization; Task 5 centralizes deterministic closure/readiness; Task 6 implements preparation, activation, CAS/idempotency, and the durable switch; Task 7 aligns Worker registration/readiness/claim; Task 8 makes Chat admission atomic; Task 9 removes live Legacy fallback/imports; Task 10 splits `/health` and `/ready`; Task 11 implements initialization/activation/readiness/Profile V2 UX; Task 12 verifies fresh Compose initialization-to-Chat and safe evidence.
- Stable-interface check: `MainAgentProfileSnapshotV2`, `AssistantRuntimeClosure`, `AssistantReadinessSnapshot`, `PrepareRolloutRequest`, `ActivateRolloutRequest`, `SetNewRunsEnabledRequest`, `WorkerCompatibility`, and `NewChatAdmission` have one canonical spelling and explicit producer/consumer Tasks.
- Initialization ownership check: core staging, Operator/catalog staging, trusted bootstrap, marker/audit, one coordinator commit, and post-commit Session issuance are explicit. Initialization never calls activation.
- Migration check: Plan 2 is exactly `9f3c1a7e2b40 -> b6e2d4f8a901`; it neither creates nor edits `pre_ga_v1_0001` or `pre_ga_v1_0002`.
- Runtime-boundary check: live application imports are AST-gated away from `app.assistant.migration`; historical migration code is not treated as a selectable runtime.
- Evidence check: the Plan 2 artifact has a fixed safe-key allowlist and canonical aggregate digest; secret/request/content fields are forbidden.
- Placeholder scan:

```bash
rg -n 'T[B]D|T[O]DO|F[I]XME|implement[[:space:]]+later|fill[[:space:]]+in|similar[[:space:]]+to[[:space:]]+Task' \
  docs/superpowers/plans/2026-07-28-main-agent-bootstrap-and-readiness.md
```

Expected: no output.

- Stable-name scan:

```bash
rg -n \
  'AssistantRuntimeClosure|AssistantReadinessSnapshot|MainAgentProfileSnapshotV2|PrepareRolloutRequest|ActivateRolloutRequest|SetNewRunsEnabledRequest|NewChatAdmission' \
  docs/superpowers/plans/2026-07-28-main-agent-bootstrap-and-readiness.md
```

Expected: every name uses the exact spelling above; no alternate closure/readiness/activation type appears.

- Formatting check:

```bash
git diff --check -- \
  docs/superpowers/plans/2026-07-28-main-agent-bootstrap-and-readiness.md
```

Expected: no output.
