# Plan 07 Task 9 — Durable Proposal Review Golden Path

**Recorded (UTC):** 2026-07-16  
**Branch:** `worktree-plan-07-durable-workflow-interrupt`  
**Base HEAD:** `c7e8ebe`  
**Canonical name:** `durable-proposal-review`

## Graph

```text
start -> llm (compute proposal) -> human_in_loop (editable durable approval) -> output
```

Private Artifact + bounded user text only. No Entry/Tag/Relation/Draft/HTTP/external writes.

## Publish path

- Workflow: new `AssistantWorkflow` + `AssistantWorkflowVersion` (new-publish only)
- Skill: Plan 01 `AgentSkillService.create_native_package` / `save_draft` / `publish(..., durable_capability_keys=...)`
- Binding extension: `extensions.durableExecutionPlanV1` via `publish_durable_binding_snapshot`
- Registry: `CapabilityRegistry.resolve` uses `classify_for_durable_publish` when plan extension present and re-derived plan digest matches
- Catalog remains `catalog_enabled=false`; no admissions flag flip

## Behavior freeze (descriptor)

| Field | Value |
|---|---|
| `interrupt_mode` | `durable` |
| `side_effect` | `compute` |
| `parallel_safe` | `false` |

## Sample digests from one publish (version-scoped)

Digests that include target/version identity change on each new-publish (expected). Graph `target_digest` is stable for the fixed golden graph bytes.

| Digest | Sample value |
|---|---|
| `target_digest` (graph) | `746d1356bfdb1929500e0ee62cd89b51f3ecfb12b1278882d8b9a92c6645fc18` |
| `plan_digest` | version-scoped (includes `target_version_id`) |
| `binding_contract_digest` | version-scoped (includes durable plan extension) |
| `dependency_closure_digest` | version-scoped (model binding identity) |
| `descriptor_digest` | version-scoped |
| `skill_content_digest` | content-stable for fixed SKILL.md/yaml |
| `skill_version_digest` | version-scoped (`content + binding_set`) |

Captured sample (one SQLite publish run under `APP_BUILD_REVISION=test-build-plan07-t9`):

```text
plan_digest=86eeddf030a389737cd310590df9ff52a06e67d62e05e13d94da2c89c9f49ec4
target_digest=746d1356bfdb1929500e0ee62cd89b51f3ecfb12b1278882d8b9a92c6645fc18
binding_contract_digest=945ba1c88245c47c0ca0df2f3cfe0417150a505d3809acdca0636657ef4cb186
dependency_closure_digest=208b94c1c54dd8ddbaadc9b6f3b1b61c45901f577e92c85bc029596c7a53e971
descriptor_digest=eb909c46a6b82e0cf7b4682aa18717e2d1d748c844d6b6467057ec2e0a24035d
skill_content_digest=3efd2ac0b2935736f416244b343b37009881441942bde593fe30d6421ed420a3
skill_binding_set_digest=3df146a4647cb2e4b6a259fc210b4e4c9294b248c7aab8e21c42614a9373e538
skill_version_digest=698f3805eb5ef8098d6d5730dcd469d13a8fc35ec11cabf69f2f92f3d7ac87d4
```

## Recovery proof (library path)

Suite: `backend/tests/test_durable_proposal_review_golden.py`

```bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_proposal_review_golden.py -q
# 10 passed, 3 skipped
```

Scenario covered:

1. Publish golden package + durable descriptor  
2. Scripted LLM compute → durable pause commit  
3. Kill/restart sim (durable waiting on disk; no in-memory waiter)  
4. Pending fetch + token rotate + edit/approve resolve  
5. Kill/restart sim after decision (queued resume-ready)  
6. `execute_interrupt_resume` → root terminal + structured output / user text  
7. One interrupt decision; one derived resume budget with byte-identical non-time usage  
8. Zero Entry/Tag/Relation/Draft row deltas  

Also: rejection, cancellation, expiry, malformed values, two sequential HITLs, nested child frame.

## Env-gated gaps (honest)

| Gap | Status |
|---|---|
| Postgres dual-session kill/restart | skipped unless `MINDATLAS_TEST_POSTGRES_URL` |
| Live MinIO Artifact store | skipped unless `MINDATLAS_TEST_MINIO` |
| Live Provider I/O | skipped unless `MINDATLAS_TEST_LIVE_PROVIDER`; tests use scripted LLM gateway |
| Full compose API+worker smoke | not run in Task 9 (library recovery preferred) |
| Production catalog/runtime admissions | **not flipped** (evaluation/hidden only) |

## Legacy safety

- Default `classify()` without durable extension remains `legacy_blocking` for human_in_loop graphs  
- Other Legacy blocking Workflows not admitted via golden package  
- `catalog_enabled` stays false on golden package  

## Files

- `backend/app/assistant/workflow/durable/golden_path.py`
- `backend/app/assistant/skills/service.py` (`durable_capability_keys` opt-in)
- `backend/app/assistant/capabilities/registry.py` (extension-aware classify)
- `backend/tests/test_durable_proposal_review_golden.py`
- `backend/tests/fixtures/agent_skills/durable-proposal-review/*`
- `backend/tests/fixtures/workflows/durable-proposal-review.json`
