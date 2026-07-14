# Plan 05 Task 0 Baseline

**Recorded at (UTC):** 2026-07-14T09:40:33Z  
**Branch:** `worktree-plan-05-policy-guardrails`  
**Base commit (main):** `c967f8585cdf4965e6d2265b31aaef663e58a243`  
**Worktree:** `.claude/worktrees/plan-05-policy-guardrails`

## Environment

| Item | Value |
|---|---|
| Python | 3.12.3 (local venv; production target remains 3.11) |
| pydantic | 2.13.4 |
| sqlalchemy | 2.0.51 |
| jsonschema | 4.26.0 |
| Alembic heads | sole `9ed6f561a381` |
| Working tree | clean product code at Task 0 freeze |
| `ASSISTANT_MAIN_AGENT_MODE` default | `off` |

## Prerequisite evidence

| Prerequisite | Status | Reference |
|---|---|---|
| Plan 01 | merged on main (`2d47173` / #48) | agent skill contracts + immutable versioning |
| Plan 02A | `PLAN_02A_READY=yes` | `docs/superpowers/evidence/plan-02a-readiness.md` |
| Plan 02B | `complete` (non-blocking) | `plan-02b-observation.md`, `plan-02b-final.md` → `FULL_PLAN_02_COMPLETE=yes` |
| Plan 03 | `PLAN_03_READY=yes` | `docs/superpowers/evidence/plan-03-readiness.md` |
| Plan 04 | merged on main (`718122a` / #50); readiness file still records residual risks as `PLAN_04_READY=no` | verified by Task 0 focused suites on current main |

Plan 02B coordination status for Plan 05: **`complete`** (non-blocking track).

## Contract import paths (verified importable)

| Symbol | Module |
|---|---|
| `ResolvedRunManifestRevision` (+ `effective_policy_digest`, `provider_aliases`) | `app.assistant.domain.contracts` |
| `SkillConflictRuleV1`, `SkillPolicyContract` | `app.assistant.skills.contracts` (re-export `app.assistant.skills`) |
| `FrozenCapabilityBinding`, `FrozenBindingProvenance`, `CapabilityAuthorizationEvidence` | `app.assistant.capabilities.contracts` |
| `CapabilityGateway` | `app.assistant.capabilities.gateway` |
| `grant_source_digest_for_ceiling` | `app.assistant.capabilities.policy` |
| `MAIN_AGENT_READ_ONLY_EFFECT_CEILING`, `LOCAL_ASSISTANT_PRINCIPAL`, `SkillPolicyAuthorizationEvidenceVerifier`, `compute_main_agent_grant_source_digest` | `app.assistant.main_agent.authorization` |
| `PendingSkillActivationPackage`, `build_domain_key_ownership_map` | `app.assistant.main_agent.manifest_runtime` |
| `ManifestEffectLifecyclePort` (`accept`/`discard`) | `app.assistant.provider_loop.contracts` |
| `ProviderAgentLoop`, provider messages, `arguments_digest` on `ProviderToolCall` | `app.assistant.provider_loop.*` |
| Profile `output_budget` defaults (rounds/calls/parallel/depth/followups/wall) | `app.assistant.skills.schemas.default_main_agent_profile_snapshot` |

## Plan 05-relevant fixed values

### Effect ceiling

```text
MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects = ("none", "compute", "read")
MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_interrupt_modes = ("none",)
revision = plan04-v1
```

### Principal

```text
principal_type=service principal_id=local-assistant authenticated=True
```

### SkillConflictRuleV1 (byte-compatible with Plan 05 Section 4.4)

```text
kind: Literal["excludes", "requires", "exclusive_group"]
target_skill: str | None
group: str | None
```

Canonical fixed vectors present in Plan 01 package I/O tests (`test_conflict_rules_structure_and_limit`).

### SkillPolicyContract

```text
allowed_side_effects, max_skill_calls=16, max_same_read_calls=3,
requires_terminal_output=false, terminal_text_allowed=false
```

### Profile output_budget defaults (align with Plan 05 RunBudgetLimits)

| Field | Default |
|---|---:|
| max_provider_rounds | 8 |
| max_total_capability_calls | 16 |
| max_parallel_calls | 4 |
| max_capability_depth | 4 |
| max_agent_depth | 2 |
| max_same_read_signature | 3 |
| max_completion_tokens | 4096 |
| max_completion_followup_rounds | 2 |
| max_wall_time_ms | 120000 |

### Operator settings present today

- `ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS` (hard max 8) — Plan 05 reuses this; adds the other numeric ceilings in Task 1/config.

## Stop-condition checks

| Gate | Result |
|---|---|
| Plan 01 `SkillConflictRuleV1` exact shape + fixed vectors | **pass** |
| Terminal policy fields present on Skill policy contract | **pass** (publication structural satisfiability already constrained by Plan 01 package rules; Plan 05 adds runtime path) |
| Independently sourced `allowed_side_effects` + `grant_source_digest` | **pass** (Plan 04 path) |
| Post-lineage pending-package activation (`PendingSkillActivationPackage` + lifecycle port) | **pass** |
| Domain Key exclusivity map across base/active/same-batch | **pass** (`build_domain_key_ownership_map`) |
| Only `none\|read\|compute`, `interrupt_mode=none` | **pass** |
| Canonical Provider `arguments_digest` without Gateway coercion | **pass** (`ProviderToolCall.arguments_digest`) |
| Provider-neutral completion port additive-capable | **pass** — `ProviderCompletionGuard` absent today; Plan 05 owns additive extension; provider_loop does not import `app.assistant.policy` |
| Sole Alembic head unchanged for Plan 05 | **pass** (`9ed6f561a381`) |
| Catalog currently rejects any `conflict_rules` | **expected** — Plan 05 Task 1/6 replaces reject-all with structured evaluation |

## Focused suite results (Task 0 command + expansions)

```text
# Plan-required Task 0 suite
backend/tests/test_main_agent_authorization.py
backend/tests/test_main_agent_skill_injection.py
backend/tests/test_main_agent_runtime.py
backend/tests/test_main_agent_evaluation.py
backend/tests/test_provider_multi_tool_calls.py
backend/tests/test_openclaw_shared_capability_runtime.py
→ 109 passed (84 main/provider + 25 openclaw) with valid AI_PROVIDER_FERNET_KEY

# Expanded Plan 01–03 contracts
backend/tests/test_resolved_run_manifest.py
backend/tests/test_agent_skill_publish.py
backend/tests/test_agent_skill_spec_conformance.py
backend/tests/test_capability_policy.py
backend/tests/test_capability_gateway.py
backend/tests/test_provider_agent_loop.py
backend/tests/test_provider_loop_contracts.py
backend/tests/test_provider_messages.py
→ 223 passed
```

## Notes for Plan 05 implementers

1. Do **not** change `ResolvedRunManifestRevision` v1 fields; derive `ManifestExposureIndex` / `EffectiveRunPolicySnapshot` and pin digests via existing `effective_policy_digest`.
2. Import `SkillConflictRuleV1` from Plan 01 — no second parser/dialect.
3. Reuse `MAIN_AGENT_READ_ONLY_EFFECT_CEILING` lattice prefix; do not invent a competing platform ceiling.
4. Add `ProviderCompletionGuard` / completion message only in provider-loop contracts (no policy ledger types there).
5. Catalog today returns `skill_policy_unsupported` for any conflict rules; Task 6 must flip that to structured evaluation after Task 1 contracts land.
6. No migration; final verification must keep Alembic head `9ed6f561a381`.

## Conclusion

```text
PLAN_05_TASK0_READY=yes
PLAN_02B_STATUS=complete
ALEMBIC_HEAD=9ed6f561a381
```

Task 1 may begin: freeze source-aware agent policy contracts (exposure index, conflicts, budget limits, effective policy snapshot).
