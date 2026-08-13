# Task 3 Execution Boundary Report

## Scope

Implemented Task 3 brief Steps 1-3 only:

- Provider-facing `create_entry` is a non-writing gateway envelope.
- Direct declaration calls fail closed with a typed safe code and no database
  effects.
- `local_write.py` is a no-commit staging adapter whose only Entry service
  mutation is `EntryService.create_in_uow`.

No migration, approval, reconciliation, or unrelated task work was changed.

## Red-Green Evidence

### Cycle 1: declaration and normalized proposal

Test first: added `backend/tests/test_create_entry_production_postgres.py`.

Red command:

```text
cd backend
.venv/bin/python -m pytest tests/test_create_entry_production_postgres.py \
  tests/test_capability_call_local_transaction.py -q \
  -k 'gateway_required or architecture or verified_gateway'
```

Observed expected red result: `ModuleNotFoundError: No module named
'app.assistant.capability_calls.create_entry_declaration'` for the verified
gateway proposal test.

Green implementation:

- Added `create_entry_declaration.py` with `CapabilityGatewayRequired`, opaque
  `CapabilityGatewayInvocation`, `CreateEntryCapabilityInput`, and
  `CreateEntryProposal`.
- Replaced the old decorated database-writing declaration exported by
  `entry_tools.py` with the non-writing declaration.
- Made `ToolCapabilityAdapter` construct the private invocation marker at the
  resolved system-tool adapter edge.

Green result: `3 passed, 12 deselected` for the focused command.

### Cycle 2: local adapter has no commit call

Test first: AST assertions require no old Provider tool import, no `.create()`,
no `.commit()`, and a `create_in_uow` call in `local_write.py`.

Red command:

```text
cd backend
.venv/bin/python -m pytest tests/test_create_entry_production_postgres.py -q
```

Observed expected red assertion: `assert 'commit' not in {'allow_commit', ...,
'commit', 'create_in_uow', ...}`.

Green implementation:

- Moved the compatibility transaction wrapper into
  `capability_calls/local_settlement.py`.
- Left `local_write.py` as the staging-only adapter and preserved package-level
  `create_entry_local_transactional` export through `__init__.py`.

Green result: focused execution-boundary tests passed.

### Cycle 3: actual LangChain wrapper path

Test first: `test_gateway_injected_marker_survives_tool_argument_validation`
exercises `wrap_tool_with_db(create_entry, ...)` with the trusted marker.

Red result: Pydantic raised `Extra inputs are not permitted` for
`_gateway_invocation` before the declaration ran.

Green implementation:

- `coerce_tool_args()` retains an injected marker only for the tool named
  `create_entry` and only when its runtime type is
  `CapabilityGatewayInvocation`; all other values still hit the public
  Pydantic schema and fail as extras.

Green result: declaration wrapper test passed.

### Cycle 4: marker/schema self-audit

Added tests that prove:

- direct invocation uses `CapabilityGatewayRequired.safe_code ==
  'capability_gateway_required'` and leaves Session/Call/Entry state unchanged;
- `CreateEntryCapabilityInput` and the actual LangChain
  `create_entry.args_schema` omit `_gateway_invocation`;
- Provider JSON containing `_gateway_invocation` is rejected;
- `CapabilityGatewayInvocation(object())` cannot forge a verified marker;
- only the private adapter factory can construct the accepted marker.

The marker relies on identity equality with a module-private object. It is an
in-process trusted runtime boundary, not a security boundary against code that
can arbitrarily import and introspect application modules in the same Python
process.

## Files

Added:

- `backend/app/assistant/capability_calls/create_entry_declaration.py`
- `backend/app/assistant/capability_calls/local_settlement.py`
- `backend/tests/test_create_entry_production_postgres.py`

Modified:

- `backend/app/assistant/capabilities/adapters/tool.py`
- `backend/app/assistant/capability_calls/__init__.py`
- `backend/app/assistant/capability_calls/local_write.py`
- `backend/app/assistant/tools/entry_tools.py`
- `backend/app/assistant/workflow/engine/runtime_helpers.py`
- `backend/app/assistant/runtime/system_seed/manifest.v1.json`
- `backend/app/assistant/runtime/system_seed/expected.py`
- `backend/tests/test_capability_call_local_transaction.py`
- `backend/tests/test_entry_tools.py`

The seed files were regenerated using the repository's deterministic builder
because the Provider tool input/output contract digest intentionally changed.

## Final Verification

Brief-required focused command:

```text
.venv/bin/python -m pytest \
  tests/test_create_entry_production_postgres.py \
  tests/test_capability_call_local_transaction.py \
  -q -k 'gateway_required or architecture'
2 passed, 15 deselected
```

Gateway/schema regression group:

```text
.venv/bin/python -m pytest \
  tests/test_create_entry_production_postgres.py \
  tests/test_capability_tool_adapter.py -q
29 passed
```

Related regression group:

```text
.venv/bin/python -m pytest \
  tests/test_capability_call_local_transaction.py \
  tests/test_entry_tools.py \
  tests/test_capability_contracts.py \
  tests/test_capability_json_schema.py \
  tests/test_production_capability_surface.py \
  tests/test_assistant_system_seed.py \
  tests/test_assistant_system_seed_builder.py -q
64 passed
```

Seed verification:

```text
.venv/bin/python scripts/build_assistant_system_seed.py --check
assistant system seed: OK
```

All pytest commands emitted only pre-existing third-party deprecation warnings
from LangGraph, Starlette TestClient, and Pydantic class-based config.

## Self-Review

- `git diff --check` passed.
- The direct declaration test installs a Session object whose `commit()` raises
  and snapshots CapabilityCall/Entry counts and pending objects before/after.
- The local adapter architecture tests parse real module AST rather than relying
  on text-only checks.
- The injected-argument escape hatch is limited to the exact
  `create_entry` tool and exact marker type, so an arbitrary tool cannot use
  the field to bypass its schema.
- The previous direct write tests were deliberately rewritten to assert the new
  fail-closed boundary. Existing human REST Entry service behavior remains
  outside this Provider tool surface and was not modified.
