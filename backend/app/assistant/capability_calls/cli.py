"""Guarded local CLI for capability reconciliation (Plan 08 Task 7).

Not mounted as HTTP. Operators run this against an explicit database session
with actor + reason + evidence. Never prints secrets or raw provider payloads.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindatlas-capability-reconcile",
        description="Inspect and reconcile capability calls (operator-only).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    inspect_p = sub.add_parser("inspect", help="Show safe call status")
    inspect_p.add_argument("--call-id", required=True)

    decide_p = sub.add_parser("decide", help="Apply a reconciliation decision")
    decide_p.add_argument("--call-id", required=True)
    decide_p.add_argument("--expected-call-revision", type=int, required=True)
    decide_p.add_argument("--expected-run-revision", type=int, required=True)
    decide_p.add_argument(
        "--decision",
        required=True,
        choices=["mark_succeeded", "mark_failed", "mark_compensated", "retry_same_key"],
    )
    decide_p.add_argument("--reason", required=True)
    decide_p.add_argument("--evidence-artifact-id", action="append", default=[])
    decide_p.add_argument("--actor-admin-id", required=True)
    decide_p.add_argument("--resolution-request-id", default=None)
    decide_p.add_argument(
        "--status-lookup-not-accepted",
        action="store_true",
        help="Required for external_reconcilable retry_same_key",
    )
    return p


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(
    argv: list[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    owns_session = session_factory is None
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal

    db = session_factory()
    try:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )

        service = CapabilityReconciliationService(db)
        call_id = UUID(args.call_id)
        if args.cmd == "inspect":
            call = service.get_call(call_id)
            if call is None:
                _emit({"ok": False, "error": "call_not_found", "callId": str(call_id)})
                return 3
            _emit(
                {
                    "ok": True,
                    "callId": str(call.id),
                    "runId": str(call.run_id),
                    "status": str(call.status),
                    "stateRevision": int(call.state_revision),
                    "executionMode": str(call.execution_mode),
                    "sideEffectClass": str(call.side_effect_class),
                    "attemptCount": int(call.attempt_count),
                }
            )
            return 0

        resolution_request_id = (
            UUID(args.resolution_request_id)
            if args.resolution_request_id
            else uuid4()
        )
        result = service.apply(
            ReconciliationDecisionRequest(
                call_id=call_id,
                expected_call_revision=args.expected_call_revision,
                expected_run_revision=args.expected_run_revision,
                decision=args.decision,
                reason=args.reason,
                evidence_artifact_ids=tuple(
                    UUID(value) for value in args.evidence_artifact_id
                ),
                resolution_request_id=resolution_request_id,
                actor_admin_id=UUID(args.actor_admin_id),
                status_lookup_proved_not_accepted=args.status_lookup_not_accepted,
            )
        )
        db.commit()
        _emit(
            {
                "ok": True,
                "callId": str(result.call_id),
                "decision": result.decision,
                "status": result.resulting_call_status,
                "callRevision": result.resulting_call_revision,
                "runRevision": result.resulting_run_revision,
                "reconciliationId": str(result.reconciliation_id),
                "created": result.created,
            }
        )
        return 0
    except (ValueError, TypeError) as exc:
        db.rollback()
        _emit({"ok": False, "error": "invalid_arguments", "detail": str(exc)})
        return 2
    except Exception as exc:  # operator surface: stable code, no payloads/secrets
        db.rollback()
        code = getattr(exc, "code", "reconciliation_failed")
        _emit({"ok": False, "error": str(code)})
        return 2
    finally:
        # Injected test/operator shells may own their Session lifetime.
        if owns_session:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
