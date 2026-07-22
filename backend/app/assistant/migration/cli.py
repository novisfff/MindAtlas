"""Guarded local CLI for AI runtime migration (Plan 10 Task 0 skeleton).

Not mounted as HTTP. Task 0 implements read-only ``inventory scan`` against
sanitized fixtures or an injected records loader. Mutation subcommands exit
``3=precondition_failed`` until later tasks implement them.

Safety flags are bindings, not authority. No flag can mint a principal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.assistant.migration.contracts import (
    CLI_EXIT_COMPLETED,
    CLI_EXIT_COMPLETED_WITH_BLOCKERS,
    CLI_EXIT_PRECONDITION_FAILED,
    CLI_EXIT_UNEXPECTED_FAILURE,
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _add_safety_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--environment", required=True)
    parser.add_argument("--database-fingerprint", required=True)
    parser.add_argument("--source-snapshot-digest", required=True)
    parser.add_argument("--expected-schema-head", required=True)
    parser.add_argument("--expected-build-revision", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--report-json", required=True)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mindatlas-ai-runtime-migration",
        description="AI runtime migration inventory and (later) mutation commands.",
    )
    sub = p.add_subparsers(dest="group", required=True)

    inv = sub.add_parser("inventory", help="Read-only inventory commands")
    inv_sub = inv.add_subparsers(dest="cmd", required=True)
    scan_p = inv_sub.add_parser("scan", help="Scan migration inventory (read-only)")
    _add_safety_flags(scan_p)
    scan_p.add_argument(
        "--fixture-json",
        default=None,
        help="Optional sanitized fixture path for local/dev inventory scans",
    )

    # Mutation groups — stubs until Task 1+.
    for group_name, commands in (
        ("packages", ("migrate", "verify")),
        ("l2", ("backfill", "verify")),
        ("approvals", ("archive", "verify")),
        ("rollout", ("prepare", "activate", "rollback")),
        ("cleanup", ("evaluate", "preflight")),
    ):
        grp = sub.add_parser(group_name, help=f"{group_name} commands (not yet implemented)")
        grp_sub = grp.add_subparsers(dest="cmd", required=True)
        for cmd in commands:
            cmd_p = grp_sub.add_parser(cmd, help=f"{group_name} {cmd} (stub)")
            _add_safety_flags(cmd_p)
            if group_name == "cleanup":
                cmd_p.add_argument(
                    "--gate",
                    choices=["deploy_b1", "deploy_b2"],
                    required=False,
                    default="deploy_b1",
                )

    return p


def _load_fixture(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture JSON must be an object")
    return data


def _write_report(path: str | Path, payload: Mapping[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _run_inventory_scan(args: argparse.Namespace) -> int:
    from app.assistant.migration.inventory import (
        build_safe_inventory_report,
        scan_inventory_from_records,
    )

    if not args.dry_run:
        # Inventory is always non-mutating; --apply is rejected as precondition.
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "inventory_scan_is_dry_run_only",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    if not args.fixture_json:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "fixture_json_required_until_db_adapter",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    records = _load_fixture(args.fixture_json)
    # Bind CLI safety labels into the scan context (do not invent DB truth).
    records = {
        **records,
        "environment": args.environment,
        "database_fingerprint": args.database_fingerprint,
        "schema_head": args.expected_schema_head,
        "build_revision": args.expected_build_revision,
    }
    snapshot = scan_inventory_from_records(records)
    report = build_safe_inventory_report(
        snapshot,
        dry_run=True,
        request_id=str(args.request_id),
    )
    payload = report.model_dump(mode="json", by_alias=True)
    _write_report(args.report_json, payload)
    _emit(
        {
            "ok": True,
            "command": "inventory.scan",
            "snapshotDigest": snapshot.snapshot_digest,
            "blockerCount": snapshot.blocker_count,
            "counts": snapshot.counts,
            "reportJson": str(args.report_json),
            "dryRun": True,
        }
    )
    if snapshot.blocker_count > 0:
        # Inventory scan still "completed"; blockers are reported. Use 0 for
        # dry-run discovery so CI can freeze inventory tooling; mutation
        # commands will use exit 2 when applying with blockers.
        return CLI_EXIT_COMPLETED
    return CLI_EXIT_COMPLETED


def _run_stub(args: argparse.Namespace) -> int:
    _emit(
        {
            "ok": False,
            "error": "precondition_failed",
            "reason": "command_not_implemented_in_task0",
            "group": getattr(args, "group", None),
            "cmd": getattr(args, "cmd", None),
        }
    )
    return CLI_EXIT_PRECONDITION_FAILED


def main(
    argv: list[str] | None = None,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> int:
    """CLI entrypoint. Returns stable exit codes (0/2/3/4/5)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed help/error; normalize to precondition/usage.
        code = int(exc.code or 1)
        return CLI_EXIT_PRECONDITION_FAILED if code != 0 else CLI_EXIT_COMPLETED

    try:
        if args.group == "inventory" and args.cmd == "scan":
            if records_loader is not None and not args.fixture_json:
                # Allow injected loader for tests without fixture path.
                from app.assistant.migration.inventory import (
                    build_safe_inventory_report,
                    scan_inventory_from_records,
                )

                records = dict(records_loader())
                records.update(
                    {
                        "environment": args.environment,
                        "database_fingerprint": args.database_fingerprint,
                        "schema_head": args.expected_schema_head,
                        "build_revision": args.expected_build_revision,
                    }
                )
                snapshot = scan_inventory_from_records(records)
                report = build_safe_inventory_report(
                    snapshot, dry_run=True, request_id=str(args.request_id)
                )
                payload = report.model_dump(mode="json", by_alias=True)
                _write_report(args.report_json, payload)
                _emit(
                    {
                        "ok": True,
                        "command": "inventory.scan",
                        "snapshotDigest": snapshot.snapshot_digest,
                        "blockerCount": snapshot.blocker_count,
                        "counts": snapshot.counts,
                        "reportJson": str(args.report_json),
                        "dryRun": True,
                    }
                )
                return CLI_EXIT_COMPLETED
            return _run_inventory_scan(args)
        return _run_stub(args)
    except Exception as exc:  # pragma: no cover - unexpected path
        _emit(
            {
                "ok": False,
                "error": "unexpected_failure",
                "reason": type(exc).__name__,
                "message": str(exc)[:200],
            }
        )
        return CLI_EXIT_UNEXPECTED_FAILURE


if __name__ == "__main__":
    sys.exit(main())
