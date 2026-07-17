"""Guarded local CLI for capability reconciliation (Plan 08 Task 7).

Not mounted as HTTP. Operators run this against an explicit database session
with actor + reason + evidence. Never prints secrets or raw provider payloads.
"""

from __future__ import annotations

import argparse
import json


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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # Real wiring requires an app Session; this CLI is a contract surface.
    print(
        json.dumps(
            {
                "ok": False,
                "error": "cli_requires_injected_session",
                "hint": (
                    "Use CapabilityReconciliationService from an operator shell "
                    "or pass a session factory in a deployment wrapper."
                ),
                "cmd": args.cmd,
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
