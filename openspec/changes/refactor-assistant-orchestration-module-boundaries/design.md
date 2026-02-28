## Context
The previous `skills` package contained unrelated concerns and very large files (`langgraph_engine.py`, `workflow_validator.py`).

## Goals
- Enforce clear package boundaries by runtime responsibility.
- Preserve behavior while enabling later incremental splits.
- Remove dead exports and stale import paths.

## Decisions
- No API or schema change.
- One-shot internal import migration to new packages.
- Keep runtime behavior identical and verify via regression tests.
- Start large-file decomposition by extracting shared state/contracts modules.
- Continue large-file decomposition with a second cut:
  - introduce `workflow/engine/node_builders/*` module boundary
  - introduce `workflow/validation/rules/*` module boundary
  - keep `engine.py` / `validator.py` public entrypoints stable while delegating to split modules
  - preserve existing error messages and runtime behavior exactly
- Third cut follow-up for maintainability:
  - remove dynamic symbol bridge patterns used during transitional extraction (no `globals()` injection / runtime symbol refresh)
  - replace runtime proxy wrappers with explicit static aliases/imports
  - keep test monkeypatch compatibility by resolving patch-sensitive helpers through stable `engine` module attributes
- Fourth cut continuation:
  - introduce `workflow/engine/runtime_helpers.py` as an explicit shared helper boundary so most node builders no longer import `engine.py` internals directly
  - introduce `workflow/validation/models.py` to decouple validation result models from validator implementation file
  - migrate selected rule modules (`if_else`, `human_in_loop`, `variable_assign`) to consume `contracts` + `rules/common` instead of `validator.py`
  - add `workflow/validation/helpers.py` and migrate remaining rule helper dependencies (`start/llm/output/template_ref/container/code_executor`)
  - route container node builders (`iteration`/`loop`) through `container_runtime` boundary
  - upgrade `container_runtime.py` from transition alias to concrete implementation, while retaining `engine.py` delegation entrypoints for compatibility
  - delegate `validator.py` helper implementations to `validation/helpers.py` to keep public validator entrypoints stable during shrink-down
  - remove unreachable legacy blocks in `engine.py` / `validator.py` after delegation to reduce maintenance risk and file size
  - delegate remaining `engine.py` helper entrypoints (cfg parsing, start/env parsing, template-node resolution, HITL field coercion, condition eval, container input coercion) into `runtime_helpers.py`
  - remove now-unused legacy helper constants/functions from `engine.py` after delegation to keep compatibility surface minimal and explicit
  - convert `workflow/engine/snapshots.py` from transitional alias to concrete implementation module
  - make `engine.py` snapshot helpers delegate to `snapshots.py` while preserving wrapper entrypoints for compatibility and patch stability
  - move `normalize_config` into `workflow/engine/runtime_helpers.py` to consolidate core helper ownership
  - delegate `engine.py` `_wrap_tool_with_db` to shared helper implementation while retaining engine-level wrapper for monkeypatch compatibility
  - add `workflow/engine/workflow_dag_plan.py` to own DAG preprocessing concerns (node/edge normalization, topological ordering, start-node resolution)
  - reuse DAG node-map extraction helpers in `LangGraphEngine.execute()` to remove duplicate workflow node config/type preparation
  - add `workflow/engine/workflow_dag_assembler.py` to own workflow DAG assembly concerns (node builder dispatch + edge wiring)
  - keep `engine.py` as orchestration entrypoint by delegating assembly to module-level helper while preserving existing conditional branch routing and snapshot wrapping behavior
  - add `workflow/engine/stream_runtime.py` to own runtime event metadata wiring and queue event dispatch rules used by streaming execution
  - keep `LangGraphEngine.execute()` as orchestration entrypoint while delegating callback/event branching to stream runtime helper module
  - add `workflow/engine/execution_plan.py` to own execution bootstrap helpers (initial messages, workflow runtime context derivation, initial state assembly)
  - keep `LangGraphEngine.execute()` as orchestration entrypoint while delegating pre-execution planning logic to execution plan helper module
  - extend `workflow/engine/stream_runtime.py` with graph runner helper (graph-thread driving + queue polling + buffered output flush)
  - keep `LangGraphEngine.execute()` as orchestration entrypoint while delegating stream run-loop mechanics to stream runtime runner helper
  - add `workflow/engine/execution_context.py` to own runtime context parsing and UUID coercion for execute path
  - switch context time derivation away from deprecated `datetime.utcnow()` while preserving existing `sys.datetime` output shape for compatibility
  - add `workflow/engine/workflow_node_llm_resolver.py` to own workflow custom-model binding resolution for top-level and container-body nodes
  - keep `LangGraphEngine._resolve_workflow_node_llms()` as compatibility entrypoint and delegate to resolver module to preserve method-level test hooks
  - add `workflow/engine/workflow_graph_cache.py` to own cache-key and LRU storage logic for compiled graphs
  - keep `engine.py` cache entrypoints (`_make_cache_key`, `_get_or_compile_graph`) as compatibility wrappers delegating to cache module
  - add `workflow/engine/agent_subgraph.py` to own agent-loop `StateGraph` wiring and routing condition
  - keep `build_agent_subgraph` in `engine.py` as compatibility entrypoint while delegating subgraph assembly
  - add `workflow/engine/execution_services.py` to own human-loop runtime attachment/bootstrap in execute path
  - keep `LangGraphEngine.execute()` orchestration-only by delegating human-loop runtime setup to execution services helper
  - keep remaining patch-sensitive/large-surface helper routes stable and defer full migration to follow-up slices
- Validator final decomposition closure:
  - add `workflow/validation/rules/compile_rules.py` for compile-time rule ownership
  - add `workflow/validation/rules/parallel_rules.py` for parallel-branch constraint ownership
  - add `workflow/validation/rules/context_rules.py` for graph-context construction and template-reference checks
  - add `workflow/validation/rules/save_rules.py` for save-time node validation ownership
  - remove empty-shell rule modules (`start/llm/output/template_ref/container`) that only re-exported helpers
  - reduce `workflow/validation/validator.py` to stable public orchestration entrypoints while preserving messages and semantics

## Risks / Trade-offs
- Trade-off: a broad import migration in one batch increases short-term churn.
- Mitigation: mechanical path rewrite + focused regression suites.

## Migration Plan
1. Create new packages and move code.
2. Replace imports and test patch paths.
3. Remove old package files.
4. Run compile + regression tests.
5. Introduce second-cut split modules for node builders and validator rules.
6. Wire assembly paths to split modules without changing public APIs.
7. Re-run full backend regression.
8. Remove transition-time dynamic bridge code and keep delegation explicit.
9. Continue reducing direct `engine.py` / `validator.py` helper imports with incremental, test-guarded slices.
10. Complete validator final split (context/save/compile/parallel modules) and validate with full backend regression.

## Open Questions
- Remaining work is centered on optional `engine.py` helper extraction slices; validator decomposition is now closed in this change set.
