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
3. Reconciliation mutation requires
   `ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED=true` and a server-owned
   `ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID`. The CLI has no actor or
   authorization-proof flags.
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

Every evidence Artifact must belong to the Call's Run, pass byte-size/SHA-256
integrity validation, and be locked while the decision is committed. Once a
reconciliation row references an Artifact, PostgreSQL rejects UPDATE/DELETE of
that Artifact with
`MINDATLAS_PLAN08_RECONCILIATION_EVIDENCE_IMMUTABLE`.

Except for the separately validated normalized success result Artifact,
evidence is an inline `application/json` HMAC envelope:

```json
{
  "contractVersion": 1,
  "claims": {
    "callId": "...",
    "runId": "...",
    "decision": "mark_failed",
    "evidenceType": "capability_call_failure",
    "inputDigest": "<sha256>",
    "idempotencyKeyDigest": "<sha256>",
    "attempt": {
      "attemptId": "...",
      "status": "uncertain",
      "requestDigest": "<sha256>",
      "responseDigest": null,
      "diagnosticArtifactId": null
    },
    "issuedAt": "2026-07-18T12:00:00+00:00",
    "failureDisposition": "explicit_product_acceptance_unresolved"
  },
  "signature": "<lowercase HMAC-SHA256 hex>"
}
```

`signature = HMAC-SHA256(ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET,
canonical_json(claims))`. The Artifact digest is SHA-256 of the complete
canonical envelope. Verification binds the exact Run, Call, input digest,
server-key digest, latest Attempt, and decision; signatures expire. Full
verified claims plus Artifact IDs/digests are copied into the append-only
reconciliation audit row.

- `mark_succeeded` requires one real normalized `capability_call_result` plus a
  server-issued `capability_call_success_attestation` whose result digest
  matches a captured latest Attempt response.
- `mark_failed` requires server-issued `capability_call_failure` evidence with
  either proven-not-occurred disposition or authenticated explicit product
  acceptance of an unresolved outcome.
- `mark_compensated` requires a signed independent compensation completion.
- `retry_same_key` requires signed provider-contract bounds
  (`requestDigest`, `maxAttempts`, `remainingAttempts`, `deadlineAt`). For
  `external_reconcilable`, a trusted collector must prove
  `providerStatus=not_accepted` from the diagnostic Artifact attached to the
  latest Attempt. There is no operator boolean or generic-claim CLI.

### Forbidden

- `retry_same_key` on `local_transactional`, `non_retriable`, `unsupported`
- Changing target, input, owner, approval, authorization, or key
- Starting new external I/O while Run is `cancelling` (settlement only)
- Finalizing Run `cancelled` while a started call is unproven

## Apply (service)

```python
from uuid import uuid4, UUID
from app.config import get_settings
from app.assistant.capability_calls.reconciliation import (
    AuthorizedReconciliationActor,
    CapabilityReconciliationService,
    HmacReconciliationEvidenceVerifier,
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
)
settings = get_settings()
with SessionLocal() as db:
    # This callback represents an authenticated server/operator boundary. In
    # the CLI, identity comes from Settings rather than request arguments.
    svc = CapabilityReconciliationService(
        db,
        operator_authorizer=lambda _request: AuthorizedReconciliationActor(
            actor_admin_id=UUID("..."),
            authorization_method="authenticated_operator_shell",
        ),
        evidence_verifier=HmacReconciliationEvidenceVerifier(
            settings.assistant_capability_reconciliation_evidence_secret
        ),
    )
    result = svc.apply(req)
    db.commit()
    print(result)
```

Duplicate `resolution_request_id` returns the persisted outcome (idempotent).
The uniqueness scope is `(run_id, resolution_request_id)`; reusing an ID for a
different Call in the same Run returns a stable conflict.

Before any mutation, the service requires the current v3 Checkpoint to contain
the exact Call and the current obligation ledger to contain exactly one pending
reconciliation obligation for that Call. Any other pending obligation prevents
wake-up. Terminal decisions append exactly one normalized Tool message and one
durable Provider message row, advance transcript digest/ordinal, and either
queue `ready_for_provider` when a continuation exists or write a terminal
Checkpoint/Run when none exists. `retry_same_key` is never accepted from a
terminal Checkpoint.

## Issue approved evidence

Do not hand-author or edit claim JSON. These CLI commands derive claims from
locked durable state, sign them, and persist the evidence Artifact.

```bash
python -m app.assistant.capability_calls.cli issue-success \
  --call-id "..." \
  --result-artifact-id "..."

python -m app.assistant.capability_calls.cli issue-failure-acceptance \
  --call-id "..." \
  --reason "product accepts unresolved provider outcome"
```

Status lookup/retry evidence has no CLI claim flags. It is issued only through
`ReconciliationEvidenceIssuer.issue_collected_retry_evidence(...)`, using an
injected trusted collector and the diagnostic Artifact ID already frozen on
the latest Attempt.

## Apply a decision

The guarded CLI uses the same verification contract and configured operator
identity:

```bash
python -m app.assistant.capability_calls.cli decide \
  --call-id "..." \
  --expected-call-revision 3 \
  --expected-run-revision 5 \
  --decision mark_failed \
  --reason "provider evidence proves failure" \
  --evidence-artifact-id "..." \
  --resolution-request-id "..."
```

## Export / conflict

- On CAS conflict (`stale_call_revision` / `stale_run_revision`), re-inspect and
  retry with fresh revisions.
- Export only sanitized digests/counts for tickets.

## Rollback limitations

- Reconciliation does not undo a committed local Entry.
- Downgrade of the ledger schema refuses while call/reconciliation history remains
  (see migration `984c07876856` guarded downgrade).
