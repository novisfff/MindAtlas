## 1. Package Restructuring
- [x] 1.1 Create `orchestration`, `workflow`, `skill_catalog` packages.
- [x] 1.2 Move runtime modules and update package exports.
- [x] 1.3 Remove stale `app.assistant.skills` code paths.

## 2. Import Migration
- [x] 2.1 Replace backend imports to new package paths.
- [x] 2.2 Replace test imports and patch targets.
- [x] 2.3 Ensure no remaining `app.assistant.skills.*` references.

## 3. Large File Decomposition (Low-risk First Cut)
- [x] 3.1 Extract workflow engine state/reducer types to `workflow/engine/state.py`.
- [x] 3.2 Extract validator constants/regex contracts to `workflow/validation/contracts.py`.

## 4. Validation
- [x] 4.1 Python compile checks for migrated modules.
- [x] 4.2 Backend regression tests in dependency-complete environment.
- [x] 4.3 OpenSpec strict validation.

## 5. Stability Closure
- [x] 5.1 Fix `assistant_config_service` test data to satisfy skill target-binding constraint.
- [x] 5.2 Keep `RemoteTool` constructor backwards compatible for unit tests.
- [x] 5.3 Re-run focused regression (`assistant_config_service`, `remote_tool`, `langgraph_engine_streaming`).

## 6. Second-Cut Decomposition
- [x] 6.1 Add `workflow/engine/node_builders/*` module boundary and route graph assembly through node builder modules.
- [x] 6.2 Add `workflow/engine/templates.py`, `workflow/engine/snapshots.py`, `workflow/engine/container_runtime.py` shared module boundaries.
- [x] 6.3 Add `workflow/validation/rules/*` module boundary and route key validator dispatch through rules modules.
- [x] 6.4 Verify no `app.assistant.skills.*` references remain and full backend test suite passes.

## 7. Third-Cut Decomposition
- [x] 7.1 Remove dynamic symbol bridge patterns (`globals()` injection / runtime symbol refresh) from `workflow/engine/node_builders/*`.
- [x] 7.2 Replace runtime proxy wrappers in `workflow/engine/{templates,snapshots,container_runtime}.py` with explicit static aliases.
- [x] 7.3 Remove dynamic validator bridge patterns from `workflow/validation/rules/*` and use explicit imports.
- [x] 7.4 Preserve monkeypatch compatibility in streaming tests by resolving patch-sensitive helpers via `engine` module attributes where required.
- [x] 7.5 Re-run full backend regression suite and confirm no behavior changes.

## 8. Fourth-Cut Decomposition (In Progress)
- [x] 8.1 Introduce `workflow/engine/runtime_helpers.py` and migrate reusable builder helpers out of direct `engine.py` imports.
- [x] 8.2 Rewire most `node_builders/*` modules to consume `runtime_helpers` (keep patch-sensitive paths on `engine` where tests rely on monkeypatch targets).
- [x] 8.3 Extract validator result models into `workflow/validation/models.py` and reuse from `validator.py`.
- [x] 8.4 Remove direct `validator.py` imports from key split rules (`if_else_rules.py`, `human_in_loop_rules.py`, `variable_assign_rules.py`) by using `contracts` + `rules/common`.
- [x] 8.5 Re-run focused and full backend regression suites.
- [x] 8.6 Rewire remaining `iteration_node.py` / `loop_node.py` to use `container_runtime` boundary instead of importing `engine.py` directly.
- [x] 8.7 Add `workflow/validation/helpers.py` and migrate remaining rule-module helper imports (`start/llm/output/template_ref/container/code_executor`) off `validator.py`.
- [x] 8.8 Verify no direct `engine.py` imports in `node_builders/*` and no direct `validator.py` imports in `validation/rules/*`.
- [x] 8.9 Convert `container_runtime.py` from alias module to concrete implementation module and keep `engine.py` container entrypoints delegating for compatibility.
- [x] 8.10 Make `validator.py` shared helper functions delegate to `validation/helpers.py` while preserving existing function names and error text behavior.
- [x] 8.11 Remove unreachable legacy container-runtime implementation blocks from `engine.py` after delegation wiring.
- [x] 8.12 Remove unreachable legacy validator helper implementation blocks from `validator.py` after helper delegation wiring.
- [x] 8.13 Delegate remaining config/template/HITL/condition helper entrypoints in `engine.py` to `workflow/engine/runtime_helpers.py` and remove duplicated in-file logic.
- [x] 8.14 Remove dead legacy helper constants/functions left unused after delegation, then re-run full backend regression.
- [x] 8.15 Convert `workflow/engine/snapshots.py` from transitional alias module to concrete implementation module.
- [x] 8.16 Delegate `engine.py` snapshot entrypoints (`_trim/_sanitize/_emit/_build_input/_build_output/_wrap`) to `snapshots.py` while keeping compatibility wrappers.
- [x] 8.17 Move config-normalization helper (`normalize_config`) into `workflow/engine/runtime_helpers.py` and keep `engine.py` wrapper stable.
- [x] 8.18 Delegate `engine.py` DB tool-session helper (`_wrap_tool_with_db`) to `runtime_helpers.py` and re-run full backend regression.
- [x] 8.19 Add `workflow/engine/workflow_dag_plan.py` to own workflow DAG preprocessing (node/edge normalization + topo ordering + start node resolution).
- [x] 8.20 Delegate `build_workflow_dag_subgraph` preprocessing in `engine.py` to the DAG planning module and keep compile/assembly behavior unchanged.
- [x] 8.21 Add reusable workflow node-map extraction helper in `workflow_dag_plan.py` for runtime path (`node_types` + normalized `node_configs`).
- [x] 8.22 Reuse DAG planning helper in `LangGraphEngine.execute()` workflow path to reduce duplicate node map normalization logic.
- [x] 8.23 Add `workflow/engine/workflow_dag_assembler.py` to own DAG graph assembly concerns (node builder dispatch + edge routing wiring).
- [x] 8.24 Delegate `build_workflow_dag_subgraph` node/edge assembly to assembler module while preserving branch routing and snapshot wrapping behavior.
- [x] 8.25 Add `workflow/engine/stream_runtime.py` to own runtime event metadata wiring and event dispatch behavior for streaming execution.
- [x] 8.26 Delegate `LangGraphEngine.execute()` queue-event callback wiring and dispatch branching to `stream_runtime.py` while preserving stream output semantics.
- [x] 8.27 Add `workflow/engine/execution_plan.py` to own message bootstrap, workflow runtime-context derivation, and initial-state assembly helpers.
- [x] 8.28 Delegate `LangGraphEngine.execute()` message bootstrap, workflow runtime context prep, and initial-state creation to execution plan module.
- [x] 8.29 Add stream-runner helper in `workflow/engine/stream_runtime.py` to own graph-thread driving and queue polling loop.
- [x] 8.30 Delegate `LangGraphEngine.execute()` graph-run loop to stream runtime runner helper while preserving event ordering and output buffering semantics.
- [x] 8.31 Add `workflow/engine/execution_context.py` to own runtime context parsing (`stream_output`, `structured_input`, run/channel ids, UUID coercion, `sys_vars`).
- [x] 8.32 Delegate `LangGraphEngine.execute()` context parsing to execution-context helper and replace deprecated `datetime.utcnow()` usage with timezone-aware equivalent.
- [x] 8.33 Add `workflow/engine/workflow_node_llm_resolver.py` to own workflow-node custom model binding resolution (including container body node bindings).
- [x] 8.34 Delegate `LangGraphEngine._resolve_workflow_node_llms()` to resolver module while preserving existing method entrypoint and error semantics.
- [x] 8.35 Add `workflow/engine/workflow_graph_cache.py` to own graph cache key generation and LRU cache storage/lookup behavior.
- [x] 8.36 Delegate `engine.py` cache entrypoints (`_make_cache_key`, `_get_or_compile_graph`) to cache module while preserving patch targets.
- [x] 8.37 Add `workflow/engine/agent_subgraph.py` to own agent-loop subgraph assembly (`StateGraph` wiring + route condition).
- [x] 8.38 Delegate `build_agent_subgraph` in `engine.py` to agent-subgraph module while preserving current node builder hooks.
- [x] 8.39 Add `workflow/engine/execution_services.py` to own human-loop runtime attachment/bootstrap behavior.
- [x] 8.40 Delegate `LangGraphEngine.execute()` human-loop runtime setup to execution-services helper while preserving callback event semantics.

## 9. Validator Final Decomposition (Completed)
- [x] 9.1 Add `workflow/validation/rules/compile_rules.py` and delegate compile-time node checks from `validator.py`.
- [x] 9.2 Add `workflow/validation/rules/parallel_rules.py` and delegate `validate_parallel_branches` logic from `validator.py`.
- [x] 9.3 Add `workflow/validation/rules/context_rules.py` to own graph context construction and template reference validation.
- [x] 9.4 Add `workflow/validation/rules/save_rules.py` to own save-time node config checks (`tool/if_else/output/llm/model/parameter_extractor/container`).
- [x] 9.5 Remove validation rules empty-shell modules (`start/llm/output/template_ref/container`) and keep only behavior-owning rule modules.
- [x] 9.6 Shrink `workflow/validation/validator.py` to orchestration entrypoints only (`validate_workflow*` + `validate_parallel_branches`).
- [x] 9.7 Run full backend regression (`pytest -q`) after validator decomposition and confirm zero behavior drift.
