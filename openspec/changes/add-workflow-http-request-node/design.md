## Context
The workflow engine already supports strongly-typed node builders (`code_executor`, `human_in_loop`, container subflow nodes). We need to add a first-class HTTP node with predictable outputs and validation that aligns with existing architecture.

## Goals / Non-Goals
- Goals:
  - Provide practical HTTP capability inside workflow DAG and container subflows.
  - Preserve safe defaults (SSRF check, SSL verify on by default, bounded timeout/retries/response size).
  - Keep output contract fixed and stable for reference system.
- Non-Goals:
  - No multipart/form-data or binary upload/download in this version.
  - No cURL import or advanced auth schemes beyond `none`/`bearer`/`api_key`.

## Decisions
- Decision: implement transport via `urllib` and reuse SSRF policy (`validate_url_ssrf`) and safe redirect handler.
- Decision: treat HTTP 4xx/5xx as structured node output (`ok=false`) without hard-stop.
- Decision: retry only on transport errors/timeouts and HTTP 5xx; do not retry 4xx.
- Decision: keep fixed output fields and expose `response` as alias of `body`.
- Decision: allow node in container body by extending allowed node type contracts and runtime dispatch.

## Risks / Trade-offs
- Risk: allowing templated URL/body/auth can produce malformed requests at runtime.
  - Mitigation: strict save/compile validators and clear error messages.
- Risk: response truncation due global max-bytes limit may hide full payload.
  - Mitigation: default limit is large enough for practical usage and configurable by environment variable.

## Migration Plan
- Add new node type support as additive change; no migration required for existing workflows.
- Existing workflow behavior remains unchanged unless users opt into `http_request`.
