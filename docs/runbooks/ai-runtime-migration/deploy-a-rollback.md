# Deploy A Rollback Runbook (AI Runtime Migration)

**Status:** template (Task 0)  
**Applies to:** Deploy A — migrate and cut over while legacy remains intact  
**Not for:** Deploy B2 destructive schema restore (see `deploy-b2-restore.md`)

## Preconditions

- Production cutover / canary percentages are **not** active in this environment (system not launched). This runbook is still required for later production use.
- Plan 09 real principal/operator RBAC is still missing → production mutation transports remain disabled. Local dry-run tooling may proceed.
- Sole Alembic head at Task 0 freeze: `027869a00a47`.
- Git HEAD at Task 0 freeze: `c93a3a62b374344c40bd23f41e9ab877c10a6aa3`.

## Goal

Return traffic and configuration to the last known-good **legacy-primary** state without deleting migration evidence rows or universal Skill/Run/Call history.

## Abort criteria (stop and escalate)

- Any nonterminal Main Agent Run cannot drain or cancel cleanly.
- Unresolved `unknown` / `needs_reconciliation` capability calls exist.
- Active rollout control revision cannot be read or CAS-advanced.
- Backup/export digests for the current schema head are missing or mismatched.
- Operator principal cannot be verified (production).

## Procedure

### 1. Stop admission of new Main Agent traffic

1. Freeze new rollout activation (do not edit an active revision in place).
2. Prepare a new rollout revision with:
   - `runtime_mode=legacy` (or equivalent legacy-primary assignment)
   - shadow percent = 0
   - read canary percent = 0
   - write mode = `off`
3. Activate via the rollout control CAS (`expected control revision` required).
4. Record actor, reason, evidence artifact IDs.

### 2. Drain in-flight work

1. Stop scheduling new Main Agent worker claims if needed.
2. Wait for nonterminal Main Agent Runs to reach terminal states, or cancel with call settlement proof.
3. Confirm pending durable interrupts are either resolved or safely cancelled.
4. Confirm no runtime_shadow Eval Runs remain nonterminal if shadow was enabled.

### 3. Verify legacy path

1. Smoke: legacy `assistant_chat` entrypoint creates a legacy Run only.
2. Confirm Router/Supervisor path responds for `general_chat` / system skills.
3. Confirm no dual production nonterminal Runs per conversation.

### 4. Workers / flags

| Flag | Rollback target |
|---|---|
| `ASSISTANT_MAIN_AGENT_MODE` | `off` |
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` | `off` |
| `ASSISTANT_CAPABILITY_LEDGER_MODE` | retain prior safe value; do not weaken enforced mid-flight without evidence |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` | retain prior value unless interrupt path is implicated |
| `ASSISTANT_SKILL_PUBLISH_GATE_MODE` | do not flip as part of traffic rollback |

### 5. L2 / calls / reconciliation

1. Do **not** reverse L2 package/namespace backfill during Deploy A rollback unless a specific data defect requires a controlled repair batch.
2. Inventory L2 blockers (`legacy_null_package` vs `native_default_namespace` vs invalid shape).
3. Reconcile any open capability calls before declaring rollback complete.

### 6. Validation checklist

- [ ] Active rollout revision is legacy-primary with zero canary/shadow.
- [ ] New chat requests create legacy Runs only.
- [ ] Migration batch/item evidence rows remain immutable/auditable.
- [ ] No production inventory IDs committed to git.
- [ ] Safe report digests recorded in operator audit storage (not git).

## Evidence to capture

- Rollout revision ID + control revision before/after
- Assignment cohort digest (non-secret)
- Run counts by `runtime_kind` for the rollback window
- Capability call unresolved count
- Inventory snapshot digest (if re-scanned)

## Notes for this repository state

Live production rollback drill is **skipped** (system not launched). Procedure template + unit-testable export/digest helpers are the Task 0 acceptance for Deploy A rollback readiness.
