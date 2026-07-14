# Plan 04 Task 0 Baseline

**Recorded at (UTC):** 2026-07-14T04:20:00Z  
**Branch:** `feature/plan-04-main-agent-skill-injection`  
**Base commit (main):** `7b106a70afcf8b00a2752aa605a6e4792e918adf`  
**Worktree:** `.claude/worktrees/plan-04-main-agent-skill-injection`

## Environment

| Item | Value |
|---|---|
| Python | 3.12.7 (local venv; production target remains 3.11) |
| jsonschema | 4.26.0 |
| pydantic | 2.12.5 |
| sqlalchemy | 2.0.45 |
| langgraph (local) | 1.0.5 (requirements pin 0.3.34 — clean-env note) |
| Alembic heads | sole `b666b11a5faa` |
| Alembic current (operator DB) | `b666b11a5faa` |
| Working tree | clean for product code; untracked superpowers docs/plans only |

## Prerequisite evidence

| Prerequisite | Status | Reference |
|---|---|---|
| Plan 01 | merged on main (`2d47173` / #48) | agent skill contracts + immutable versioning |
| Plan 02A | `PLAN_02A_READY=yes` | `docs/superpowers/evidence/plan-02a-readiness.md` |
| Plan 02B | `complete` (local observation conditional-pass + cleanup approved) | `plan-02b-observation.md`, `plan-02b-final.md` → `FULL_PLAN_02_COMPLETE=yes` |
| Plan 03 | `PLAN_03_READY=yes` | `docs/superpowers/evidence/plan-03-readiness.md` |

Plan 02B coordination status for Plan 04: **`complete`** (non-blocking track; OpenClaw is shared-only on main).

## Contract import paths (verified importable)

| Symbol | Module |
|---|---|
| `ResolvedRunManifestRevision`, `ResolvedMainAgentRef`, `ResolvedSkillRef`, `ModelRef`, Manifest append helpers | `app.assistant.domain.contracts` |
| `MainAgentProfileSnapshotV1`, `default_main_agent_profile_snapshot` | `app.assistant.skills.schemas` |
| `MainAgentProfileService`, `AssistantMainAgentProfile*` | `app.assistant.skills.service` / `app.assistant.skills.models` |
| `FrozenCapabilityBinding`, `CapabilityDescriptor`, `FrozenBindingProvenance` | `app.assistant.capabilities.contracts` |
| `CapabilityGateway` | `app.assistant.capabilities.gateway` |
| `ProviderAgentLoop`, provider contracts/messages | `app.assistant.provider_loop.loop` / `contracts` / `messages` |
| `AiModelCapabilityProbeService` | `app.ai_registry.service` |

## Manifest v1 / Profile schema gates

- `ResolvedRunManifestRevision` already has `provider_aliases: tuple[...] = ()` and `active_skills`; fixed vectors use explicit empty aliases. **No Manifest v1 field extension required for Plan 04.**
- Default Profile snapshot includes locked budget keys:
  - `context_budget`: `max_prompt_characters`, `max_active_skills`, `max_skill_instruction_characters`, `max_single_skill_instruction_characters`, `max_history_characters`, `max_tool_summary_characters`, `max_resource_bytes_per_call`
  - `output_budget`: `max_completion_tokens`, `max_provider_rounds`, `max_outer_agent_rounds`, `max_total_capability_calls`, `max_parallel_calls`, `max_capability_depth`, `max_agent_depth`, `max_same_read_signature`, `max_completion_followup_rounds`, `max_wall_time_ms`
  - `supported_entrypoints` includes `assistant_chat`
  - `fallback_policy.legacy_runtime_allowed` / `before_side_effects_only`
- `control_capability_keys` defaults to empty tuple; Plan 04 must populate the four required controls at publish/enable time without mutable Tool lookup (code-native bindings via Plan 02 registry path).

## Legacy baseline suites (Task 0 minimum)

```text
backend/tests/test_skill_router_decision.py
backend/tests/test_skill_router_prompt_format.py
backend/tests/test_assistant_chat_run_stream.py
backend/tests/test_assistant_chat_stop.py
backend/tests/test_assistant_service_no_outer_fallback.py
backend/tests/test_assistant_memory_l0.py
backend/tests/test_assistant_memory_l1_service.py
backend/tests/test_assistant_memory_l2_service.py
```

Result: **29 passed**.

Sample Plan 02/03 focused suites (`gateway`, `policy`, `provider_agent_loop`, `provider_loop_contracts`, `provider_messages`): **120 passed**.

## Golden path candidate

- Preferred: system skill/workflow **`quick_stats`** (`QUICK_STATS_ASSET_KEY`, published workflow asset `quick_stats__workflow`).
- Tool classification anchors (Plan 02 ruleset): `get_statistics`, `analyze_activity`, `get_tag_statistics` are registered as **read** system tools in capability classification (exact digests recorded at Task 8/9 golden freeze after full recursive surface materialization).
- Fallback candidate: `periodic_review` / `periodic_review_core` if recursive workflow classification is not `read|compute` + `interrupt_mode=none` after exact published materialization.
- Evaluation fixtures will be built under `backend/tests/fixtures/main_agent_eval/` in Task 9/10; Legacy Router decisions captured before any production mode switch (`ASSISTANT_MAIN_AGENT_MODE` remains `off` by default).

## Stop conditions check

| Gate | Result |
|---|---|
| Plan 01 Profile budget keys present | **pass** |
| Manifest v1 unchanged / empty aliases | **pass** |
| Plan 02A READY | **pass** |
| Plan 03 READY | **pass** |
| Sole Alembic head | **pass** (`b666b11a5faa`) |
| Code-native control binding path exists (system tool registry) | **pass** (system-tool materialization); Main Agent provenance enum extension is Task 5 |
| Mutable Tool lookup required for controls | **no** — Plan 04 must freeze code-native bindings |

## Conclusion

```text
PLAN_04_TASK0_READY=yes
PLAN_02B_STATUS=complete
```

Task 1 may begin: guarded Main Agent aggregate flags + Settings mode `off` default + migration from sole head `b666b11a5faa`.
