"""Guarded local CLI for capability reconciliation inspection.

Not mounted as HTTP. Read-only ``inspect`` remains available. Mutation commands
(``decide``, ``issue-success``, ``issue-failure-acceptance``) refuse env-asserted
identity and require an authenticated HTTP Operator session. Never prints secrets
or raw provider payloads.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

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
    decide_p.add_argument("--resolution-request-id", default=None)

    success_p = sub.add_parser(
        "issue-success",
        help="Derive and sign success evidence from a captured result Artifact",
    )
    success_p.add_argument("--call-id", required=True)
    success_p.add_argument("--result-artifact-id", required=True)

    failure_p = sub.add_parser(
        "issue-failure-acceptance",
        help="Record explicit authenticated product acceptance of unresolved failure",
    )
    failure_p.add_argument("--call-id", required=True)
    failure_p.add_argument("--reason", required=True)
    return p


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def main(
    argv: list[str] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    settings: Any | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    owns_session = session_factory is None
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal

    if args.cmd in {"decide", "issue-success", "issue-failure-acceptance"}:
        # Mutations require an authenticated HTTP Operator session (Plan 4).
        # Env-asserted operator IDs are never authorization.
        import sys

        print(
            "authenticated HTTP Operator session is required",
            file=sys.stderr,
        )
        return 2

    db = session_factory()
    try:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
        )

        if settings is None:
            from app.config import get_settings

            settings = get_settings()

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

        _emit({"ok": False, "error": "unsupported_command", "detail": str(args.cmd)})
        return 2
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
