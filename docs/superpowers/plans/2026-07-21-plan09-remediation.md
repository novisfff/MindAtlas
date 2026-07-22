# Plan 09 / PR #56 Completion Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the original Plan 09 contract on PR #56 with real reproducible Main Agent evaluation, authoritative two-gate publication, complete protected administration flows, independently deployable migrations, and fresh end-to-end evidence.

**Architecture:** Evaluation admission and publish share one pure server-side candidate-closure resolver. Gate-eligible dataset runs execute the real Main Agent Provider Loop with versioned deterministic Provider scripts and test-owned adapters; assertions derive actual outcomes from observed Eval state. Publish and live enable use separate server-derived gates, while every Plan 09 route remains behind the existing fail-closed parent mount until project-wide RBAC exists.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 15, pytest/unittest, React 18, TypeScript, TanStack Query, Zustand, Vitest, Testing Library, existing Main Agent/Provider Loop/Capability Gateway contracts.

## Global Constraints

- Target PR baseline is `ccacc14749af53fb62e1bacd9c25739464b471c9`; refresh the pin before implementation if PR #56 moves.
- Preserve Plan 01 immutable bytes, parsers, digest factories, alias reservation, and published binding semantics.
- Dataset cases provide inputs and assertions only; expected values never populate actual outcomes.
- Only real Main Agent orchestration with observable isolation evidence may set `gate_eligible=True`.
- `dataset_live` remains optional and promotion-ineligible in this remediation.
- Publish and enable use different action, subject kind, version ID, qualifying runs, request ID, and gate-use row.
- Client requests never author closure digests, decisions, metrics, assertions, policy, threshold, or build pins.
- Every Plan 09 mutation requires `requestId` and the relevant expected revision and advances the revision on success.
- Saving a draft never changes Catalog/Profile live state.
- Plan 09 staging/production routes remain unmounted and absent from OpenAPI until a real project-wide principal/operator guard exists.
- Legacy Skill pages and runtime stay available; no task enables production Catalog/Profile state, enforce mode, or Plan 10 cutover.
- Each task follows red-green-refactor, runs its focused tests, and lands as one independently reviewable commit.

---

### Task 1: Freeze deployment state and repair the 09A/09B migration graph

**Files:**
- Modify: `backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py`
- Modify: `backend/alembic/versions/027869a00a47_add_skill_evaluation_workbench.py`
- Delete when deployment audit is clean: `backend/alembic/versions/24f1e06fdd9e_allow_skill_package_alias_soft_disable.py`
- Modify: `backend/tests/test_agent_skill_admin_postgres_migration.py`
- Modify: `backend/tests/test_skill_eval_repository_postgres.py`
- Modify: `backend/tests/test_capability_call_migration_postgres.py`
- Modify: `backend/tests/test_main_agent_postgres_migration.py`
- Create: `docs/superpowers/evidence/plan-09-remediation-deployment-audit.md`

**Interfaces:**
- Produces a recorded yes/no answer for whether any shared database applied Plan 09 revisions.
- Produces a complete 09A revision whose alias soft-disable works without evaluation tables.
- Produces the sole pre-merge Plan 09 head `027869a00a47` when history is safe to rewrite.

- [ ] **Step 1: Record the deployment audit before editing revision history**

Run against every shared development, staging, and production database configured for this repository:

```bash
cd backend
.venv/bin/alembic current -v
```

Write exact environment names, UTC timestamps, command output, and the decision to `docs/superpowers/evidence/plan-09-remediation-deployment-audit.md`. The clean pre-merge decision requires that no shared database report `403414a62e55`, `027869a00a47`, or `24f1e06fdd9e`. If any does, stop this task and use a forward repair revision; do not delete or rewrite an applied revision.

- [ ] **Step 2: Write failing independent-slice PostgreSQL tests**

Add tests with these assertions:

```python
def test_09a_alias_soft_disable_works_without_eval_schema(pg_migrator):
    pg_migrator.upgrade("403414a62e55")
    alias_id = pg_migrator.insert_custom_alias("review-notes")
    pg_migrator.disable_alias(alias_id, actor="operator:test")
    assert pg_migrator.alias_disabled_by(alias_id) == "operator:test"
    assert not pg_migrator.table_exists("assistant_skill_eval_run")


def test_09a_upgrade_downgrade_upgrade_is_independent(pg_migrator):
    pg_migrator.upgrade("403414a62e55")
    pg_migrator.downgrade("d7e8f9a0b1c3")
    pg_migrator.upgrade("403414a62e55")
    assert pg_migrator.alias_soft_disable_trigger_is_column_aware()
```

- [ ] **Step 3: Verify RED**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_admin_postgres_migration.py -q
```

Expected: alias UPDATE at `403414a62e55` is rejected by the Plan 01 immutable trigger.

- [ ] **Step 4: Move the column-aware alias trigger into 09A**

In `backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py`, after adding `disabled_at/disabled_by`, replace the Plan 01 UPDATE trigger with the column-aware function currently held by `backend/alembic/versions/24f1e06fdd9e_allow_skill_package_alias_soft_disable.py`. In 09A downgrade, restore `mindatlas_reject_immutable_mutation()` before dropping the columns. Keep DELETE fully rejected.

For the clean pre-merge path, delete `backend/alembic/versions/24f1e06fdd9e_allow_skill_package_alias_soft_disable.py`; keep `027869a00a47.down_revision = "403414a62e55"`. Update every exact-head assertion from `24f1e06fdd9e` to `027869a00a47`.

- [ ] **Step 5: Verify both migration cycles**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_admin_postgres_migration.py \
  backend/tests/test_skill_eval_repository_postgres.py \
  backend/tests/test_capability_call_migration_postgres.py \
  backend/tests/test_main_agent_postgres_migration.py -q
cd backend && .venv/bin/alembic heads
```

Expected: all tests pass and exactly one head is printed: `027869a00a47 (head)`.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions backend/tests/test_agent_skill_admin_postgres_migration.py backend/tests/test_skill_eval_repository_postgres.py backend/tests/test_capability_call_migration_postgres.py backend/tests/test_main_agent_postgres_migration.py docs/superpowers/evidence/plan-09-remediation-deployment-audit.md
git commit -m "fix(ai): restore independent Plan 09 migration slices"
```

---

### Task 2: Enforce protected route ownership, mandatory CAS, and explicit Profile live state

**Files:**
- Modify: `backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py`
- Modify: `backend/app/assistant/skills/models.py`
- Modify: `backend/app/assistant/skills/router.py`
- Modify: `backend/app/assistant/skills/admin_router.py`
- Modify: `backend/app/assistant/skills/schemas.py`
- Modify: `backend/app/assistant/skills/service.py`
- Modify: `backend/app/assistant/skills/admin_service.py`
- Modify: `backend/tests/test_main_agent_profile_api.py`
- Modify: `backend/tests/test_skill_admin_api.py`
- Modify: `backend/tests/test_skill_draft_cas_and_resources.py`
- Modify: `backend/tests/test_agent_skill_admin_service.py`

**Interfaces:**
- Produces required `request_id: str` and `expected_aggregate_revision: int` on protected mutation commands.
- Produces Profile `aggregate_revision`, `last_admin_request_id`, and `last_admin_request_digest` fields with the same CAS/idempotency ordering as Skill packages.
- Moves Profile version detail to the conditionally mounted Plan 09 router.
- Preserves `runtime_enabled` across Profile draft saves.

- [ ] **Step 1: Write failing OpenAPI, CAS, and Profile-state tests**

```python
def test_production_openapi_omits_profile_version_detail(production_app):
    paths = production_app.openapi()["paths"]
    assert "/api/assistant-config/main-agent-profiles/default/versions/{version_id}" not in paths


def test_skill_draft_save_requires_revision_and_request_id(client, package):
    response = client.put(
        f"/api/assistant-config/skill-packages/{package.id}/draft",
        json={"skillMd": VALID_SKILL_MD},
    )
    assert response.status_code == 422


def test_profile_draft_save_does_not_disable_runtime(profile_service, enabled_profile):
    profile_service.save_draft(enabled_profile.id, make_profile_draft_command())
    profile = profile_service.get_default()
    assert profile.runtime_enabled is True
    assert profile.published_version_id == enabled_profile.published_version_id
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_profile_api.py \
  backend/tests/test_skill_admin_api.py \
  backend/tests/test_skill_draft_cas_and_resources.py \
  backend/tests/test_agent_skill_admin_service.py -q
```

- [ ] **Step 3: Make protected mutation schemas non-optional**

Use required fields on Plan 09 bodies and commands:

```python
class SkillPackageJsonSaveRequest(CamelModel):
    skill_md: str = Field(alias="skillMd")
    mindatlas_yaml: str | None = Field(default=None, alias="mindatlasYaml")
    resources: list[SkillResourceInput] | None = None
    version_name: str | None = Field(default=None, alias="versionName")
    expected_aggregate_revision: int = Field(alias="expectedAggregateRevision", ge=0)
    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
```

Apply the same required pair to archive/unarchive, metadata, alias, restore,
catalog/Profile enable/disable, import apply, publish, and Profile draft/publish.
Keep idempotent request lookup before CAS and increment the aggregate revision
on every successful mutation, including existing-version reuse.

Extend 09A and `AssistantMainAgentProfile` with:

```python
aggregate_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
last_admin_request_id = Column(String(128), nullable=True)
last_admin_request_digest = Column(String(64), nullable=True)
```

Add `aggregate_revision >= 0` and SHA-256-length checks. Profile command bodies
use the same idempotency-first sequence as Skill packages: lock profile, compare
request ID/digest, compare expected revision, apply the mutation, increment the
revision, stamp request evidence, and commit.

- [ ] **Step 4: Move Profile detail and remove implicit demotion**

Delete `get_default_main_agent_version` from the always-mounted router. Add it
to `admin_router.py` under the protected prefix with
`Depends(get_trusted_operator_principal)`. In both `MainAgentProfileService.save_draft`
branches, remove `profile.runtime_enabled = False`; do not replace it with any
other live-state mutation.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_main_agent_profile_api.py \
  backend/tests/test_skill_admin_api.py \
  backend/tests/test_skill_draft_cas_and_resources.py \
  backend/tests/test_agent_skill_admin_service.py -q
git add backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py backend/app/assistant/skills backend/tests/test_main_agent_profile_api.py backend/tests/test_skill_admin_api.py backend/tests/test_skill_draft_cas_and_resources.py backend/tests/test_agent_skill_admin_service.py
git commit -m "fix(ai): enforce Plan 09 admin integrity boundary"
```

---

### Task 3: Persist import previews transactionally across workers

**Files:**
- Modify: `backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py`
- Modify: `backend/app/assistant/skills/models.py`
- Modify: `backend/app/assistant/skills/schemas.py`
- Modify: `backend/app/assistant/skills/import_preview.py`
- Modify: `backend/app/assistant/skills/admin_router.py`
- Modify: `backend/tests/test_agent_skill_import_preview.py`
- Modify: `backend/tests/test_agent_skill_admin_postgres_migration.py`

**Interfaces:**
- Produces `AssistantSkillImportPreview` with bounded archive bytes and durable consume/idempotency state.
- Produces `ImportPreviewService.preview()` and `.apply()` with no module-global store.

- [ ] **Step 1: Write failing cross-session and restart tests**

```python
def test_preview_created_in_one_session_applies_in_another(session_factory, archive):
    with session_factory() as first:
        preview = ImportPreviewService(first).preview(
            archive, mode="create", principal=OPERATOR
        )
    clear_import_preview_store_for_tests()  # proves process memory is irrelevant
    with session_factory() as second:
        result = ImportPreviewService(second).apply(
            preview.preview_id,
            preview_digest=preview.preview_digest,
            request_id="import-1",
            principal=OPERATOR,
        )
    assert result.package.canonical_name == "valid-weekly-review"


def test_expired_preview_drops_archive_but_retains_request_audit(session, preview_row):
    expire_import_previews(session, now=preview_row.expires_at)
    session.refresh(preview_row)
    assert preview_row.archive_bytes is None
    assert preview_row.upload_digest is not None
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_import_preview.py -q
```

- [ ] **Step 3: Add the bounded preview model in 09A**

Define an `assistant_skill_import_preview` table owned by 09A with UUID primary
key, principal/scope, mode, target package/revision, candidate/fork names,
upload/content/preview digests, bounded JSON findings/diff/resource index,
`LargeBinary archive_bytes`, expiry, consumed/applied fields, request ID/digest,
and timestamps. Add checks for digest lengths, append/fork target shape, and
consumed/archive XOR.

- [ ] **Step 4: Replace global dictionaries with row locks**

Implement this service shape:

```python
class ImportPreviewService:
    def _lock_preview(self, preview_id: UUID) -> AssistantSkillImportPreview:
        return (
            self.db.query(AssistantSkillImportPreview)
            .filter(AssistantSkillImportPreview.id == preview_id)
            .with_for_update()
            .one()
        )

    def expire(self, *, now: datetime) -> int:
        rows = list(
            self.db.scalars(
                select(AssistantSkillImportPreview)
                .where(
                    AssistantSkillImportPreview.expires_at <= now,
                    AssistantSkillImportPreview.archive_bytes.is_not(None),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            row.archive_bytes = None
        self.db.commit()
        return len(rows)
```

`apply()` locks the row, checks principal/mode/digests/target revision, parses
the stored bytes, applies the package mutation, stamps consumed/request fields,
and nulls `archive_bytes` in the same transaction. Identical retry reloads the
persisted package; altered reuse conflicts.

- [ ] **Step 5: Verify PostgreSQL and service behavior, then commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_import_preview.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_admin_postgres_migration.py -q
git add backend/alembic/versions/403414a62e55_add_skill_package_admin_lifecycle.py backend/app/assistant/skills backend/tests/test_agent_skill_import_preview.py backend/tests/test_agent_skill_admin_postgres_migration.py
git commit -m "fix(ai): persist skill import previews across workers"
```

---

### Task 4: Share one pure candidate-closure resolver between evaluation and publish

**Files:**
- Create: `backend/app/assistant/skills/candidate_closure.py`
- Modify: `backend/app/assistant/skills/service.py`
- Modify: `backend/app/assistant/evaluation/router.py`
- Modify: `backend/app/assistant/evaluation/contracts.py`
- Create: `backend/tests/test_skill_candidate_closure.py`
- Modify: `backend/tests/test_skill_eval_api.py`
- Modify: `backend/tests/test_agent_skill_publish.py`

**Interfaces:**
- Produces `SkillCandidateClosure` and `resolve_skill_candidate_closure(session, package_id, version_id, subject_kind)`.
- Consumers in Tasks 5–7 use its exact digests and frozen binding evidence.

- [ ] **Step 1: Write failing parity and drift tests**

```python
def test_eval_and_publish_resolve_identical_draft_closure(session, package_with_capability):
    closure = resolve_skill_candidate_closure(
        session,
        package_id=package_with_capability.id,
        version_id=package_with_capability.draft_version_id,
        subject_kind="skill_draft",
    )
    admitted = admit_eval_run_for(package_with_capability)
    published = publish_candidate_without_commit(package_with_capability)
    assert admitted.subject_binding_digest == closure.binding_set_digest
    assert published.binding_set_digest == closure.binding_set_digest
    assert published.version_digest == closure.version_digest


def test_target_version_drift_changes_candidate_closure(session, package_with_capability):
    before = resolve_current_closure(session, package_with_capability)
    republish_target_capability(session)
    after = resolve_current_closure(session, package_with_capability)
    assert before.binding_set_digest != after.binding_set_digest
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_candidate_closure.py \
  backend/tests/test_skill_eval_api.py \
  backend/tests/test_agent_skill_publish.py -q
```

- [ ] **Step 3: Define the frozen closure contract**

```python
class SkillCandidateClosure(FrozenContract):
    schema_version: Literal[1] = 1
    subject_kind: Literal["skill_draft", "skill_version"]
    package_id: UUID
    version_id: UUID
    content_digest: str
    binding_set_digest: str
    version_digest: str
    bindings: tuple[dict[str, JsonValue], ...]
    durable_capability_keys: tuple[str, ...] = ()


def resolve_skill_candidate_closure(
    session: Session,
    *,
    package_id: UUID,
    version_id: UUID,
    subject_kind: Literal["skill_draft", "skill_version"],
    durable_capability_keys: tuple[str, ...] = (),
) -> SkillCandidateClosure:
    package = session.get(AssistantSkillPackage, package_id)
    if package is None:
        raise CandidateClosureError("skill_package_not_found")
    version = session.scalar(
        select(AssistantSkillVersion).where(
            AssistantSkillVersion.id == version_id,
            AssistantSkillVersion.skill_package_id == package_id,
        )
    )
    if version is None:
        raise CandidateClosureError("skill_version_not_found")
    files = load_immutable_skill_version_files(session, version)
    parsed = parse_skill_directory_files(
        files,
        expected_root_name=str(package.canonical_name),
    )
    if parsed.content_digest != str(version.content_digest):
        raise CandidateClosureError("skill_content_digest_drift")
    declarations = tuple(parsed.manifest.capabilities) if parsed.manifest else ()
    bindings = sorted(
        CapabilityReferenceResolver(session).resolve_many(declarations),
        key=lambda item: (item.capability_type, item.capability_key),
    )
    bindings = apply_candidate_durable_extensions(
        session,
        bindings=bindings,
        durable_capability_keys=durable_capability_keys,
    )
    binding_digest = binding_set_digest_from_bindings(bindings)
    return SkillCandidateClosure(
        subject_kind=subject_kind,
        package_id=package_id,
        version_id=version_id,
        content_digest=parsed.content_digest,
        binding_set_digest=binding_digest,
        version_digest=version_digest_from_parts(
            content_digest=parsed.content_digest,
            binding_set_digest=binding_digest,
        ),
        bindings=tuple(binding.model_dump(mode="json") for binding in bindings),
        durable_capability_keys=durable_capability_keys,
    )
```

Define `load_immutable_skill_version_files()` and
`apply_candidate_durable_extensions()` in the same module by moving the
existing resource reconstruction and `_apply_durable_plan_extensions` logic
out of `AgentSkillService.publish()`. The implementation uses
`parse_skill_directory_files`, `CapabilityReferenceResolver.resolve_many`,
durable binding extensions, `binding_set_digest_from_bindings`, and
`version_digest_from_parts`. It flushes and commits nothing.

- [ ] **Step 4: Replace both divergent paths**

`evaluation/router.py` ignores client digest fields and admits the resolver's
content/binding closure. `AgentSkillService.publish()` calls the resolver before
inserting the publish row and persists the same binding snapshot/digests. Delete
the `binding_set_digest or content_digest` fallback.

- [ ] **Step 5: Verify GREEN and commit**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_candidate_closure.py \
  backend/tests/test_skill_eval_api.py \
  backend/tests/test_agent_skill_publish.py \
  backend/tests/test_agent_skill_dependency_closure.py -q
git add backend/app/assistant/skills/candidate_closure.py backend/app/assistant/skills/service.py backend/app/assistant/evaluation/router.py backend/app/assistant/evaluation/contracts.py backend/tests/test_skill_candidate_closure.py backend/tests/test_skill_eval_api.py backend/tests/test_agent_skill_publish.py
git commit -m "fix(ai): unify skill evaluation and publish closure"
```

---

### Task 5: Version evaluation provenance and deterministic Provider fixtures

**Files:**
- Modify: `backend/alembic/versions/027869a00a47_add_skill_evaluation_workbench.py`
- Modify: `backend/app/assistant/evaluation/models.py`
- Modify: `backend/app/assistant/evaluation/contracts.py`
- Modify: `backend/app/assistant/evaluation/repository.py`
- Modify: `backend/app/assistant/evaluation/datasets.py`
- Modify: `backend/tests/test_skill_eval_models.py`
- Modify: `backend/tests/test_skill_eval_repository_postgres.py`
- Modify: `backend/tests/test_skill_eval_snapshot_policy.py`

**Interfaces:**
- Produces `evidence_provenance: real_orchestration | structural_synthetic | live_model`.
- Produces pinned `provider_fixture_revision` and `provider_fixture_digest` on Eval Runs.
- Produces fixture references that scripted Provider execution can resolve without reading expected assertion fields.

- [ ] **Step 1: Write failing provenance/model tests**

```python
def test_structural_synthetic_run_cannot_be_gate_eligible(eval_repo):
    run = eval_repo.create_run(**run_args(evidence_provenance="structural_synthetic"))
    with pytest.raises(EvaluationRepositoryError, match="synthetic_gate_ineligible"):
        eval_repo.transition_run(
            run_id=run.id,
            expected_revision=run.state_revision,
            to_status="completed",
            gate_eligible=True,
        )


def test_real_run_requires_fixture_digest(eval_repo):
    with pytest.raises(EvaluationRepositoryError, match="provider_fixture_required"):
        eval_repo.create_run(**run_args(
            evidence_provenance="real_orchestration",
            provider_fixture_revision=None,
            provider_fixture_digest=None,
        ))
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_models.py \
  backend/tests/test_skill_eval_snapshot_policy.py -q
```

- [ ] **Step 3: Extend the unmerged evaluation schema**

Add non-null `evidence_provenance` with an explicit enum/check, nullable fixture
revision/digest columns with a shape constraint, and provider fixture evidence
inside the run closure. Backfill existing PR fixtures as
`structural_synthetic`; no backfilled row may remain gate-eligible.

- [ ] **Step 4: Enforce provenance in repository transitions**

Repository creation validates that `real_orchestration` pins a fixture,
`live_model` pins provider/model-probe evidence, and structural runs cannot
become gate-eligible. Dataset fixture refs name Provider scripts separately
from expected Skill/Capability assertions.

- [ ] **Step 5: Verify PostgreSQL constraints and commit**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_skill_eval_models.py backend/tests/test_skill_eval_snapshot_policy.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest backend/tests/test_skill_eval_repository_postgres.py -q
git add backend/alembic/versions/027869a00a47_add_skill_evaluation_workbench.py backend/app/assistant/evaluation backend/tests/test_skill_eval_models.py backend/tests/test_skill_eval_repository_postgres.py backend/tests/test_skill_eval_snapshot_policy.py
git commit -m "fix(ai): version trustworthy evaluation provenance"
```

---

### Task 6: Execute dataset cases through the real Main Agent orchestration

**Files:**
- Create: `backend/app/assistant/evaluation/orchestration.py`
- Create: `backend/app/assistant/evaluation/observations.py`
- Modify: `backend/app/assistant/evaluation/isolation.py`
- Modify: `backend/app/assistant/evaluation/runner.py`
- Modify: `backend/app/assistant/evaluation/worker.py`
- Modify: `backend/app/assistant/provider_loop/scripted_provider.py`
- Modify: `backend/tests/test_skill_eval_runner.py`
- Modify: `backend/tests/test_skill_eval_worker.py`
- Modify: `backend/tests/test_skill_eval_isolation.py`
- Create: `backend/tests/test_skill_eval_real_orchestration.py`

**Interfaces:**
- Produces `EvaluationOrchestrator.execute_case(context, case, fixture) -> ObservedEvalCaseOutcome`.
- Produces actual outcomes exclusively from Eval events, calls, obligations, completion, and adapter probes.
- Structural synthetic execution remains callable only with `gate_eligible=False`.

- [ ] **Step 1: Write the decisive expected-vs-actual negative test**

```python
def test_expected_skill_never_rewrites_actual_skill(real_eval_harness):
    case = real_eval_harness.case(
        expected_mode="golden_skill",
        acceptable_skill_keys=["skill-a"],
        fixture_key="provider-selects-skill-b",
    )
    outcome = real_eval_harness.execute(case)
    assert outcome.actual_active_skills == ("skill-b",)
    assert outcome.assertions.skill_recall is False
    assert outcome.gate_eligible is False


def test_missing_safety_observation_is_not_zero(real_eval_harness):
    outcome = real_eval_harness.execute_without_counter("secret_exposure")
    assert outcome.safety_counters["secret_exposure"] is None
    assert outcome.gate_eligible is False
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_skill_eval_real_orchestration.py -q
```

Expected: the current worker copies acceptable skills into actual skills and reports zero counters.

- [ ] **Step 3: Build isolated production-compatible ports**

`orchestration.py` loads the candidate closure, Profile snapshot, dataset case,
and versioned `ScriptedRoundScript` list, then calls
`compose_main_agent_policy_runtime` and the existing Provider Loop. Inject
test-owned memory, data, event, Artifact, CapabilityCall, and tool-dispatch
ports from `RuntimeIsolationContext`; do not import production repositories in
the orchestrator.

- [ ] **Step 4: Derive observed outcomes**

`observations.py` folds owner-qualified Eval events and test-owned call rows into:

```python
class ObservedEvalCaseOutcome(FrozenContract):
    eval_case_id: UUID
    execution_kind: Literal["direct_answer", "golden_skill", "capability"]
    actual_active_skills: tuple[str, ...]
    capability_path: tuple[str, ...]
    completed: bool
    stop_reason: str
    obligations_pending: int
    production_delta: dict[str, int | None]
    safety_counters: dict[str, int | None]
```

No constructor accepts acceptable Skill keys or expected completion as actual
fields. Compare this observed object with dataset assertions only afterward.

- [ ] **Step 5: Replace the worker's gate path**

For `dataset_scripted`, resolve fixtures and call the orchestrator per case.
Persist per-case observed traces and aggregate assertions. Rename the old
materializer to `_materialize_structural_test_outcomes`, require
`evidence_provenance="structural_synthetic"`, and force its terminal run
`gate_eligible=False`. Keep `dataset_live` explicit and promotion-ineligible.

- [ ] **Step 6: Verify real, negative, isolation, cancel, and recovery paths**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_real_orchestration.py \
  backend/tests/test_skill_eval_runner.py \
  backend/tests/test_skill_eval_worker.py \
  backend/tests/test_skill_eval_isolation.py \
  backend/tests/test_main_agent_evaluation.py \
  backend/tests/test_provider_agent_loop.py -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/assistant/evaluation backend/app/assistant/provider_loop/scripted_provider.py backend/tests/test_skill_eval_real_orchestration.py backend/tests/test_skill_eval_runner.py backend/tests/test_skill_eval_worker.py backend/tests/test_skill_eval_isolation.py
git commit -m "fix(ai): run skill datasets through Main Agent orchestration"
```

---

### Task 7: Make gate creation authoritative and enforce a two-gate lifecycle

**Files:**
- Modify: `backend/app/assistant/evaluation/contracts.py`
- Modify: `backend/app/assistant/evaluation/schemas.py`
- Modify: `backend/app/assistant/evaluation/gates.py`
- Modify: `backend/app/assistant/evaluation/router.py`
- Modify: `backend/app/assistant/skills/service.py`
- Modify: `backend/app/assistant/skills/admin_service.py`
- Modify: `backend/tests/test_skill_publish_gate.py`
- Modify: `backend/tests/test_skill_eval_api.py`
- Create: `backend/tests/test_skill_two_gate_lifecycle.py`

**Interfaces:**
- Produces `CreateGateBody(action, subjectAggregateId, subjectVersionId, qualifyingEvalRunIds, requestId, waivers)` with no client closure.
- Produces `PublishGateService.build_authoritative_subject(action, aggregate_id, version_id, qualifying_run_ids) -> PublishGateSubject`.
- Enforces action-specific single consumption.

- [ ] **Step 1: Write failing cross-action and client-closure tests**

```python
def test_gate_api_rejects_client_authored_subject(client, qualifying_run):
    body = gate_request_for(qualifying_run)
    body["subject"] = {"catalogDigest": "0" * 64}
    response = client.post("/api/assistant-config/skill-eval/gates", json=body)
    assert response.status_code == 422


def test_publish_gate_cannot_enable_catalog(two_gate_harness):
    publish_gate = two_gate_harness.create_draft_publish_gate()
    version = two_gate_harness.publish(publish_gate)
    with pytest.raises(PublishGateError, match="gate_action_subject_mismatch"):
        two_gate_harness.enable(version, publish_gate)
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_skill_two_gate_lifecycle.py backend/tests/test_skill_publish_gate.py backend/tests/test_skill_eval_api.py -q
```

- [ ] **Step 3: Narrow the HTTP request contract**

```python
class CreateGateBody(CamelModel):
    request_id: UUID = Field(alias="requestId")
    action: PublishGateAction
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    qualifying_eval_run_ids: list[UUID] = Field(alias="qualifyingEvalRunIds", min_length=1)
    requested_non_safety_waiver_codes: list[str] = Field(default_factory=list, alias="requestedNonSafetyWaiverCodes")
    waiver_reason: str | None = Field(default=None, alias="waiverReason", max_length=2000)
```

Pydantic `extra="forbid"` rejects client closure fields.

- [ ] **Step 4: Build and consume authoritative subjects**

Under the aggregate lock, load the subject version, Task 4 candidate closure,
qualifying real-orchestration runs, exact dataset/fixture pins, current Profile,
Catalog, runtime, policy/threshold, model probe, and build. Verify action maps to
the required subject kind. Store action on the gate and enforce an action-specific
unique gate-use. Existing synthetic-derived gates fail qualification/consume.

- [ ] **Step 5: Enforce publish then fresh promotion**

Skill publish accepts only `skill_publish + skill_draft`; Profile publish accepts
only `profile_publish + profile_draft`. Catalog/Profile enable requires a fresh
`skill_catalog_enable/profile_runtime_enable` gate targeting the exact published
version. Disable stays explicit and ungated. Return stable 409/422 errors for
expired, drifted, reused, wrong-action, wrong-kind, wrong-version, and hard-safety
cases.

- [ ] **Step 6: Verify the full matrix and commit**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_two_gate_lifecycle.py \
  backend/tests/test_skill_publish_gate.py \
  backend/tests/test_skill_eval_api.py \
  backend/tests/test_agent_skill_admin_service.py \
  backend/tests/test_main_agent_profile_service.py -q
git add backend/app/assistant/evaluation backend/app/assistant/skills/service.py backend/app/assistant/skills/admin_service.py backend/tests/test_skill_two_gate_lifecycle.py backend/tests/test_skill_publish_gate.py backend/tests/test_skill_eval_api.py
git commit -m "fix(ai): enforce authoritative publish and promotion gates"
```

---

### Task 8: Complete protected dataset, result, Profile, and SSE APIs

**Files:**
- Modify: `backend/app/assistant/evaluation/schemas.py`
- Modify: `backend/app/assistant/evaluation/router.py`
- Modify: `backend/app/assistant/evaluation/repository.py`
- Modify: `backend/app/assistant/skills/admin_router.py`
- Modify: `backend/tests/test_skill_eval_api.py`
- Modify: `backend/tests/test_skill_admin_api.py`
- Modify: `backend/tests/test_main_agent_profile_api.py`
- Create: `backend/tests/test_skill_eval_sse.py`

**Interfaces:**
- Produces dataset draft/publish/version detail endpoints under the protected parent.
- Produces bounded run case-result/evidence endpoints.
- Produces `GET /runs/{id}/events/stream?afterSequence=N` with replay and heartbeat.

- [ ] **Step 1: Write failing route and SSE replay tests**

```python
def test_sse_replays_after_sequence_and_heartbeats(trusted_client, eval_run):
    append_events(eval_run, count=3)
    frames = read_sse(
        trusted_client,
        f"/api/assistant-config/skill-eval/runs/{eval_run.id}/events/stream?afterSequence=1",
        frame_count=3,
    )
    assert [frame.id for frame in frames[:2]] == ["2", "3"]
    assert frames[2].event == "heartbeat"


def test_dataset_publish_requires_revision_and_principal(untrusted_client, dataset):
    response = untrusted_client.post(
        f"/api/assistant-config/skill-eval/datasets/{dataset.id}/publish",
        json={"requestId": "ds-1", "expectedRevision": 0},
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Verify RED**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_skill_eval_sse.py backend/tests/test_skill_eval_api.py backend/tests/test_main_agent_profile_api.py -q
```

- [ ] **Step 3: Add the missing protected contracts**

Implement dataset create, draft GET/PUT, publish, version detail/list, bounded
run results, qualifying evidence, and Profile draft/publish/promotion/enable/
disable reads and mutations. Every mutation carries request ID + expected
revision. Every read/mutation uses the protected principal dependency; operator
role is required for live-state and system-object transitions.

Replace the client-digest Eval admission body with:

```python
class CreateEvalRunBody(CamelModel):
    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    subject_kind: EvalSubjectKind = Field(alias="subjectKind")
    subject_aggregate_id: UUID = Field(alias="subjectAggregateId")
    subject_version_id: UUID = Field(alias="subjectVersionId")
    prompt: str = Field(min_length=1, max_length=32_000)
    locale: str = Field(min_length=2, max_length=32)
    profile_version_id: UUID = Field(alias="profileVersionId")
    mode: EvalRunMode
    dataset_version_ids: list[UUID] = Field(alias="datasetVersionIds")
    provider_fixture_revision: str | None = Field(default=None, alias="providerFixtureRevision")
    live_model_id: UUID | None = Field(default=None, alias="liveModelId")

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "CreateEvalRunBody":
        if self.mode == "dataset_scripted":
            if not self.dataset_version_ids or not self.provider_fixture_revision:
                raise ValueError("dataset_scripted requires dataset and fixture revisions")
            if self.live_model_id is not None:
                raise ValueError("dataset_scripted forbids liveModelId")
        if self.mode == "dataset_live":
            if not self.dataset_version_ids or self.live_model_id is None:
                raise ValueError("dataset_live requires dataset versions and liveModelId")
            if self.provider_fixture_revision is not None:
                raise ValueError("dataset_live forbids providerFixtureRevision")
        return self
```

Admission resolves all digests and policy/build/runtime/model-probe pins on the
server and records `principal.principal_id`; it accepts no actor override.

- [ ] **Step 4: Implement bounded SSE**

Use `StreamingResponse` with `text/event-stream`; repeatedly call
`repo.list_events_after(eval_run_id, after_sequence, limit=100)`, emit `id`,
`event`, and redacted JSON `data`, advance only after emission, send a heartbeat
when no rows arrive, and stop on disconnect or terminal run after all rows are
delivered. Never hold a database transaction while waiting.

- [ ] **Step 5: Verify GREEN, OpenAPI boundaries, and commit**

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_sse.py \
  backend/tests/test_skill_eval_api.py \
  backend/tests/test_skill_admin_api.py \
  backend/tests/test_main_agent_profile_api.py -q
git add backend/app/assistant/evaluation backend/app/assistant/skills/admin_router.py backend/tests/test_skill_eval_sse.py backend/tests/test_skill_eval_api.py backend/tests/test_skill_admin_api.py backend/tests/test_main_agent_profile_api.py
git commit -m "feat(ai): complete protected Plan 09 evaluation APIs"
```

---

### Task 9: Build the real Workbench client and evidence views

**Files:**
- Modify: `frontend/src/features/assistant-config/api/skill-evaluations.ts`
- Modify: `frontend/src/features/assistant-config/api/skill-evaluations.test.ts`
- Modify: `frontend/src/features/assistant-config/components/SkillTestWorkbench.tsx`
- Modify: `frontend/src/features/assistant-config/components/SkillEvaluationRun.tsx`
- Modify: `frontend/src/features/assistant-config/stores/skill-test-run-store.ts`
- Modify: `frontend/src/features/assistant-config/stores/skill-test-run-store.test.ts`
- Create: `frontend/src/features/assistant-config/components/SkillTestWorkbench.test.tsx`
- Create: `frontend/src/features/assistant-config/components/SkillEvaluationEvidence.tsx`

**Interfaces:**
- Produces typed Workbench inputs for prompt, locale, Profile/version, fixture/model, mode, and dataset versions.
- Produces SSE replay with polling only as a bounded fallback.
- Produces bounded case, trace, assertion, metric, retention, and eligibility views.

- [ ] **Step 1: Write failing contract and component tests**

```tsx
it('requires a published dataset for dataset_scripted', async () => {
  renderWorkbench({ datasets: [publishedDataset] })
  await user.selectOptions(screen.getByLabelText('Evaluation mode'), 'dataset_scripted')
  expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('Dataset version'), publishedDataset.versionId)
  expect(screen.getByRole('button', { name: 'Start evaluation' })).toBeEnabled()
})

it('deduplicates SSE replay by run and sequence', () => {
  const store = createSkillTestRunStore()
  store.getState().ingestEvents('run-1', [event(2), event(2), event(3)])
  expect(store.getState().events.map((item) => item.sequence)).toEqual([2, 3])
})
```

- [ ] **Step 2: Verify RED**

```bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config/api/skill-evaluations.test.ts \
  src/features/assistant-config/stores/skill-test-run-store.test.ts \
  src/features/assistant-config/components/SkillTestWorkbench.test.tsx
```

- [ ] **Step 3: Replace client-authored run/closure inputs**

Define `CreateEvalRunRequest` without content/binding/environment digests. It
contains subject identity, prompt, locale, Profile version, mode, dataset
version IDs, deterministic fixture revision or live model ID, and request ID.
Load dataset versions and Profiles through TanStack Query and prevent invalid
mode/input combinations before submit.

- [ ] **Step 4: Implement SSE lifecycle**

Create an `EventSource`/fetch-stream client carrying `afterSequence`; the store
keys deduplication by `${runId}:${sequence}`, reconnects from the last accepted
sequence, handles heartbeat separately, sends cancel with expected revision,
and fetches terminal run/results after stream close. Start bounded polling only
after a classified SSE transport failure.

- [ ] **Step 5: Render bounded evidence**

Show actual active Skills, owner-qualified Capability traces, completion and
obligations, aggregate metrics, assertion failures, missing safety evidence,
retention/expiry, and promotion eligibility. Never render raw credentials,
unbounded Provider payloads, or unsafe resource bytes.

- [ ] **Step 6: Verify GREEN and commit**

```bash
npm --prefix frontend run test -- --run src/features/assistant-config
git add frontend/src/features/assistant-config/api/skill-evaluations.ts frontend/src/features/assistant-config/api/skill-evaluations.test.ts frontend/src/features/assistant-config/components/SkillTestWorkbench.tsx frontend/src/features/assistant-config/components/SkillTestWorkbench.test.tsx frontend/src/features/assistant-config/components/SkillEvaluationRun.tsx frontend/src/features/assistant-config/components/SkillEvaluationEvidence.tsx frontend/src/features/assistant-config/stores/skill-test-run-store.ts frontend/src/features/assistant-config/stores/skill-test-run-store.test.ts
git commit -m "feat(ui): complete skill evaluation workbench"
```

---

### Task 10: Complete Skill/Profile editing and route-level fail-closed behavior

**Files:**
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/features/settings/SettingsPage.tsx`
- Modify: `frontend/src/features/assistant-config/api/skill-packages.ts`
- Modify: `frontend/src/features/assistant-config/api/main-agent-profiles.ts`
- Modify: `frontend/src/features/assistant-config/queries.ts`
- Modify: `frontend/src/features/assistant-config/components/UniversalSkillEditor.tsx`
- Modify: `frontend/src/features/assistant-config/components/SkillResourceBrowser.tsx`
- Modify: `frontend/src/features/assistant-config/components/SkillCapabilityEditor.tsx`
- Modify: `frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.tsx`
- Modify: `frontend/src/features/assistant-config/stores/skill-editor-store.ts`
- Modify: `frontend/src/features/assistant-config/stores/skill-editor-store.test.ts`
- Create: `frontend/src/features/assistant-config/components/Plan09RouteGate.tsx`
- Create: `frontend/src/features/assistant-config/components/Plan09RouteGate.test.tsx`
- Modify: `frontend/src/features/assistant-config/components/SkillResourceBrowser.test.tsx`
- Create: `frontend/src/features/assistant-config/components/SkillCapabilityEditor.test.tsx`

**Interfaces:**
- Produces resource working-copy add/replace/remove with mandatory CAS serialization.
- Produces Registry-only ordered capability selection.
- Produces lazy Plan 09 routes gated by feature + principal state.
- Produces distinct Profile draft/publish/promotion/enable/disable commands.

- [ ] **Step 1: Write failing resource, Registry, Profile, and route tests**

```tsx
it('direct URL fails closed before protected package fetch', async () => {
  server.use(featureProbeUnavailable())
  renderAt('/settings/universal-skills/package-1')
  expect(await screen.findByText('Universal Skills unavailable')).toBeVisible()
  expect(protectedPackageRequests()).toHaveLength(0)
})

it('rejects free-text capability keys outside the Registry', async () => {
  renderCapabilityEditor({ registryKeys: ['tool:published'] })
  await user.type(screen.getByRole('combobox'), 'tool:unknown')
  expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
})

it('resource removal is serialized as an explicit replacement snapshot', () => {
  const store = loadedEditorWithResources(['a.txt', 'b.txt'])
  store.getState().removeResource('a.txt')
  expect(store.getState().buildSaveBody().resources.map(r => r.path)).toEqual(['b.txt'])
})
```

- [ ] **Step 2: Verify RED**

```bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config/components/Plan09RouteGate.test.tsx \
  src/features/assistant-config/components/SkillResourceBrowser.test.tsx \
  src/features/assistant-config/components/SkillCapabilityEditor.test.tsx \
  src/features/assistant-config/stores/skill-editor-store.test.ts
```

- [ ] **Step 3: Implement route-level gating**

`Plan09RouteGate` waits for the server feature/principal probe before rendering
lazy route children. Missing either renders the fail-closed unavailable/not-found
surface and never mounts protected queries. Navigation uses the same probe;
legacy `SkillSettings` remains routed.

- [ ] **Step 4: Implement working-copy resources and Registry selection**

Keep safe immutable preview/download behavior, but add file add/replace/remove
actions that update `SkillWorkingCopy.resources`. `buildSaveBody()` always sends
the complete intended resource list with request ID and expected revision.
Load published capability identities from the shared Registry, show target,
version, resolution, and risk metadata, and allow only those identities while
preserving order.

- [ ] **Step 5: Separate Profile lifecycle actions**

Profile draft save, publish, published-version evaluation, promotion gate,
runtime enable, and explicit disable use separate API functions and mutation
states. Saving draft leaves enabled/published UI state unchanged. Disable asks
for an explicit confirmation and request ID but no promotion gate.

- [ ] **Step 6: Verify GREEN, build, and commit**

```bash
npm --prefix frontend run test -- --run src/features/assistant-config
npm --prefix frontend run build
git add frontend/src/app/App.tsx frontend/src/features/settings/SettingsPage.tsx frontend/src/features/assistant-config
git commit -m "feat(ui): complete protected skill and Profile administration"
```

---

### Task 11: Wire the frontend to the authoritative two-gate lifecycle

**Files:**
- Modify: `frontend/src/features/assistant-config/api/skill-evaluations.ts`
- Modify: `frontend/src/features/assistant-config/api/skill-packages.ts`
- Modify: `frontend/src/features/assistant-config/components/SkillPublishGateDialog.tsx`
- Modify: `frontend/src/features/assistant-config/components/SkillPublishGateDialog.test.tsx`
- Modify: `frontend/src/features/assistant-config/pages/UniversalSkillEditorPage.tsx`
- Create: `frontend/src/features/assistant-config/pages/UniversalSkillEditorPage.test.tsx`
- Modify: `frontend/src/features/assistant-config/stores/skill-test-run-store.ts`

**Interfaces:**
- Produces separate `publishGate` and `promotionGate` UI state keyed by action + subject version.
- Sends gate requests containing identities/evidence only.
- Invalidates draft evidence after publish and requires published-version evidence before enable.

- [ ] **Step 1: Write failing lifecycle tests**

```tsx
it('does not reuse the draft gate after publish', async () => {
  renderEditorWithPassingDraftRun()
  await requestGateAndPublish()
  expect(screen.getByRole('button', { name: 'Enable catalog' })).toBeDisabled()
  expect(screen.getByText('Evaluate the published version before enabling')).toBeVisible()
})

it('gate request contains no client-authored closure', async () => {
  await requestDraftPublishGate()
  expect(lastGateRequest()).toEqual({
    requestId: expect.any(String),
    action: 'skill_publish',
    subjectAggregateId: PACKAGE_ID,
    subjectVersionId: DRAFT_ID,
    qualifyingEvalRunIds: [RUN_ID],
    requestedNonSafetyWaiverCodes: [],
    waiverReason: null,
  })
})
```

- [ ] **Step 2: Verify RED**

```bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config/components/SkillPublishGateDialog.test.tsx \
  src/features/assistant-config/pages/UniversalSkillEditorPage.test.tsx
```

- [ ] **Step 3: Remove `buildGateSubject()` and `lastGateId`**

Replace the single gate state with action/subject-specific records. The dialog
selects only completed real-orchestration qualifying runs for the exact current
subject. It sends the Task 7 request shape and displays the server-returned
authoritative closure read-only.

- [ ] **Step 4: Enforce UI lifecycle invalidation**

After publish, clear draft run/gate state, refetch package/version pointers, and
switch Workbench subject to `skill_version + publishedVersionId`. Enable remains
disabled until that subject has a passing run and fresh
`skill_catalog_enable` gate. Any content, binding, Profile, Catalog, dataset,
fixture, runtime, policy, threshold, or build drift clears affected eligibility.

- [ ] **Step 5: Verify GREEN and commit**

```bash
npm --prefix frontend run test -- --run src/features/assistant-config
npm --prefix frontend run build
git add frontend/src/features/assistant-config/api/skill-evaluations.ts frontend/src/features/assistant-config/api/skill-packages.ts frontend/src/features/assistant-config/components/SkillPublishGateDialog.tsx frontend/src/features/assistant-config/components/SkillPublishGateDialog.test.tsx frontend/src/features/assistant-config/pages/UniversalSkillEditorPage.tsx frontend/src/features/assistant-config/pages/UniversalSkillEditorPage.test.tsx frontend/src/features/assistant-config/stores/skill-test-run-store.ts
git commit -m "fix(ui): separate skill publish and promotion gates"
```

---

### Task 12: Prove the complete lifecycle and regenerate Plan 09 evidence

**Files:**
- Create: `backend/tests/test_plan09_lifecycle_e2e.py`
- Create: `backend/tests/test_plan09_lifecycle_postgres.py`
- Create: `frontend/src/features/assistant-config/plan09-lifecycle.e2e.test.tsx`
- Modify: `.github/workflows/ci.yml`
- Rewrite: `docs/superpowers/evidence/plan-09-task9-verification.md`
- Create: `docs/superpowers/evidence/plan-09-remediation-e2e.md`

**Interfaces:**
- Produces fresh evidence for the exact final commit and migration head.
- Produces one positive create-to-enable lifecycle and the required negative paths.
- Leaves M4 release and Plan 10 production cutover explicitly blocked on real RBAC.

- [ ] **Step 1: Write the process-level positive lifecycle test**

The test starts the API and Eval worker against disposable PostgreSQL, uses the
trusted test principal, and executes:

```python
def test_create_real_eval_publish_fresh_eval_promote_enable(plan09_system):
    package = plan09_system.create_package()
    draft = plan09_system.save_draft(package, expected_revision=package.aggregate_revision)
    draft_run = plan09_system.run_real_dataset(draft)
    publish_gate = plan09_system.create_gate("skill_publish", draft, [draft_run])
    published = plan09_system.publish(draft, publish_gate)
    assert not plan09_system.catalog_contains(package.canonical_name)
    promotion_run = plan09_system.run_real_dataset(published)
    promotion_gate = plan09_system.create_gate(
        "skill_catalog_enable", published, [promotion_run]
    )
    plan09_system.enable_catalog(package, promotion_gate)
    assert plan09_system.catalog_contains(package.canonical_name)
```

- [ ] **Step 2: Write the negative lifecycle matrix**

Cover synthetic evidence, empty datasets, wrong action/kind/version, reused or
expired gate, binding/target drift, missing safety observation, production
delta, unauthenticated Profile detail, Profile edit demotion, stale/cross-worker
preview, missing CAS, altered request reuse, and direct URL without feature/
principal. Every failure asserts unchanged pointer, revision, live flag, and
production data.

- [ ] **Step 3: Verify RED before relying on the test**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_plan09_lifecycle_e2e.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  backend/.venv/bin/python -m pytest backend/tests/test_plan09_lifecycle_postgres.py -q
npm --prefix frontend run test -- --run src/features/assistant-config/plan09-lifecycle.e2e.test.tsx
```

Expected before Tasks 1–11: failures at dataset admission, synthetic evidence,
gate closure, and promotion-gate reuse.

- [ ] **Step 4: Add the exact CI gates**

Add the PostgreSQL migration cycles, real-orchestration lifecycle, isolation
delta probes, OpenAPI boundary tests, frontend lifecycle tests, and production
build to `.github/workflows/ci.yml`. Do not mark synthetic structural tests as
release evidence.

- [ ] **Step 5: Run the complete verification set**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_admin_service.py backend/tests/test_agent_skill_import_preview.py backend/tests/test_skill_candidate_closure.py backend/tests/test_skill_eval_api.py backend/tests/test_skill_eval_isolation.py backend/tests/test_skill_eval_models.py backend/tests/test_skill_eval_runner.py backend/tests/test_skill_eval_snapshot_policy.py backend/tests/test_skill_eval_worker.py backend/tests/test_skill_eval_real_orchestration.py backend/tests/test_skill_publish_gate.py backend/tests/test_skill_two_gate_lifecycle.py backend/tests/test_plan09_lifecycle_e2e.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" backend/.venv/bin/python -m pytest backend/tests/test_agent_skill_admin_postgres_migration.py backend/tests/test_skill_eval_repository_postgres.py backend/tests/test_plan09_lifecycle_postgres.py -q
backend/.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run test -- --run
npm --prefix frontend run build
cd backend && .venv/bin/alembic heads
git diff --check cb5dac353408021fffeb5e3902acd2fc317b91de..HEAD
```

Expected: all required suites pass, frontend build succeeds, exactly one
Alembic head is printed, and `git diff --check` prints nothing.

- [ ] **Step 6: Regenerate evidence from command output**

Rewrite `plan-09-task9-verification.md` with the exact final commit, Plan 08
base, migration head, test counts, CI run URLs, OpenAPI result, positive and
negative E2E observations, feature/worker/gate defaults, rollback instructions,
and the real principal/operator release blocker. Do not reuse old counts or
claim a command that was not run.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml backend/tests/test_plan09_lifecycle_e2e.py backend/tests/test_plan09_lifecycle_postgres.py frontend/src/features/assistant-config/plan09-lifecycle.e2e.test.tsx docs/superpowers/evidence/plan-09-task9-verification.md docs/superpowers/evidence/plan-09-remediation-e2e.md
git commit -m "test(ai): prove Plan 09 completion lifecycle"
```

---

## Final Review Gate

Before marking the remediation complete:

1. Map every section of `docs/superpowers/specs/2026-07-21-plan09-remediation-design.md` to a passing Task 1–12 test or recorded process check.
2. Confirm no `structural_synthetic` or `live_model` run can contribute to a promotion gate.
3. Confirm no Plan 09 route, including Profile detail, appears in staging/production OpenAPI.
4. Confirm the PR description and Task 9 evidence say **Plan 09 code-complete** only after all gates pass, but still say **M4 not release-complete** because real RBAC is absent.
5. Do not start Plan 10 production cutover from trusted-mount or synthetic evidence.
