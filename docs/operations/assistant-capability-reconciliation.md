# Assistant Capability Reconciliation Runbook (Plan 08)

## Scope

This runbook covers operator recovery for durable CapabilityCall rows in
`needs_reconciliation` / `unknown` after external uncertainty. **No production
external write is enabled in Plan 08**; the contract and CLI exist so later
plans can enable external targets safely.

Golden local `create_entry` is `local_transactional`:

- Never use `retry_same_key` for local transactional calls.
- Query Entry by `source_capability_call_id` and ledger call status instead.

## Preconditions

1. `ASSISTANT_MAIN_AGENT_WRITE_MODE` remains default `off` unless a separate
   golden cohort is intentionally enabled.
2. Reconciliation mutation HTTP is **unmounted / default-disabled**.
3. Use the guarded local CLI or an authenticated operator shell that injects
   a database Session and actor identity.
4. Never paste secrets, provider credentials, or raw provider payloads into
   tickets, logs, or the reason field.

## Inspect

```python
from uuid import UUID
from app.database import SessionLocal
from app.assistant.capability_calls.reconciliation import CapabilityReconciliationService

call_id = UUID("...")
with SessionLocal() as db:
    svc = CapabilityReconciliationService(db)
    call = svc.get_call(call_id)
    print({
        "id": str(call.id) if call else None,
        "status": getattr(call, "status", None),
        "execution_mode": getattr(call, "execution_mode", None),
        "side_effect_started_at": str(getattr(call, "side_effect_started_at", None)),
        "attempt_count": getattr(call, "attempt_count", None),
        "failure_code": getattr(call, "failure_code", None),
        "input_digest": getattr(call, "input_digest", None),
        "authorization_digest": getattr(call, "authorization_digest", None),
    })
```

## Decisions

| Decision | When | Effect |
|---|---|---|
| `mark_succeeded` | Stable business/provider evidence proves success | call to succeeded |
| `mark_failed` | Evidence effect did not occur, or product accepts unresolved failure label | call to failed (must not claim false rollback) |
| `mark_compensated` | Independent compensating action completed | call to compensated |
| `retry_same_key` | Only external_idempotent (or external_reconcilable after authoritative not_accepted lookup) | call to authorized for same-key retry |

### Forbidden

- `retry_same_key` on `local_transactional`, `non_retriable`, `unsupported`
- Changing target, input, owner, approval, authorization, or key
- Starting new external I/O while Run is `cancelling` (settlement only)
- Finalizing Run `cancelled` while a started call is unproven

## Apply (service)

```python
from uuid import uuid4, UUID
from app.assistant.capability_calls.reconciliation import (
    CapabilityReconciliationService,
    ReconciliationDecisionRequest,
)

req = ReconciliationDecisionRequest(
    call_id=UUID("..."),
    expected_call_revision=3,
    expected_run_revision=5,
    decision="mark_failed",
    reason="provider status lookup returned not_found; no side effect",
    evidence_artifact_ids=(UUID("..."),),
    resolution_request_id=uuid4(),
    actor_admin_id=UUID("..."),
)
with SessionLocal() as db:
    svc = CapabilityReconciliationService(db)
    result = svc.apply(req)
    db.commit()
    print(result)
```

Duplicate `resolution_request_id` returns the persisted outcome (idempotent).

## Export / conflict

- On CAS conflict (`stale_call_revision` / `stale_run_revision`), re-inspect and
  retry with fresh revisions.
- Export only sanitized digests/counts for tickets.

## Rollback limitations

- Reconciliation does not undo a committed local Entry.
- Downgrade of the ledger schema refuses while call/reconciliation history remains
  (see migration `984c07876856` guarded downgrade).
