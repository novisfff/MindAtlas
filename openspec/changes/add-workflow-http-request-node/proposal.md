# Change: Add Workflow HTTP Request Node

## Why
Current workflow DAG lacks a native HTTP node for direct API calls. Users must rely on external tool registration, which adds setup overhead and weakens subflow portability.

## What Changes
- Add workflow node type `http_request` for main graph and container body (`iteration`/`loop`).
- Add fixed output contract for response fields: `body`, `status_code`, `headers`, `ok`, `error_message`, `response`.
- Add validator coverage for method/body/auth/timeout/retry/SSL options.
- Add runtime behavior for SSRF-safe requests, retry policy, and transport failure handling.
- Add editor support: node palette, property panel, references, preview, i18n.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - Backend workflow runtime, validation, config, schemas, router metadata
  - Frontend workflow types, node registration, property panel, reference transform
  - Tests for validator/runtime behavior
