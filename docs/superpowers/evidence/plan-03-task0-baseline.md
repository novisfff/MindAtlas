# Plan 03 Task 0 Baseline

**Date:** 2026-07-14  
**Branch:** `feature/shared-capability-runtime` (PR #49)  
**HEAD:** `0f1f49b`  
**Alembic head:** `acf208493c87` (single)

## Prerequisites
| Check | Result |
|---|---|
| Plan 01 Manifest v1 `schema_version=1`, `provider_aliases=()`, `ResolvedProviderAliasRef` | Pass |
| Plan 02A readiness `PLAN_02A_READY=yes` | Pass (`docs/superpowers/evidence/plan-02a-readiness.md`) |
| Capability Gateway/runtime present | Pass |
| Plan 02B | pending (non-blocking coordination note) |

## Environments
### Project venv
- Python 3.12.7
- langgraph 1.0.5, langchain-core 1.2.7, openai 2.15.0 (local drift)

### Clean Python 3.11 (`/tmp/mindatlas-plan03-py311-baseline.k7ZL12`)
- langgraph 0.3.34, langchain 0.3.30, langchain-core 0.3.86, langchain-openai 0.3.35, openai 2.45.0
- `pip check`: No broken requirements
- **Authoritative for Plan 03 loop compatibility**

## Baselines
| Suite | Project venv | Clean 3.11 |
|---|---|---|
| agent_test_run_stream + openai_compat + ai_registry_runtime + supervisor_graph | 20 passed | 20 passed |
| capability gateway/contracts + resolved_run_manifest sample | 72 passed | (not re-run; Plan 02A already green) |

## Current Agent defect (reason for separate loop)
`backend/app/assistant/workflow/engine/agent_execution_core.py`:
- `llm.bind_tools(..., parallel_tool_calls=False)` once before loop
- `selected_call = tool_calls[0]`; later siblings logged and dropped
- No new-loop authorization to modify this path

## Dependencies
- `openai` not direct in `requirements.txt` (transitive via langchain-openai)
- Clean resolver shows openai 2.45.0 with langchain-openai 0.3.35 — Task 6 must pin explicit compatible direct bound

## Migration
- `c9d0e1f2a3b4` already used by `drop_legacy_workflow_graph_tables.py`
- New probe migration must be Alembic-generated unique ID from head `acf208493c87`

## PostgreSQL gate
- Plan 01 CI job exists for agent-skill migration; Task 8 will extend for probes if needed
