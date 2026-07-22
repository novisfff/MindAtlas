"""Guarded local CLI for AI runtime migration (Plan 10).

Not mounted as HTTP. Inventory scan is read-only; inventory prepare/apply/resume
write discovered evidence under --apply with operator principal fail-closed.
Mutation groups for packages/l2/approvals/rollout/cleanup remain stubs until
later tasks.

Safety flags are bindings, not authority. No flag can mint a principal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from app.assistant.migration.contracts import (
    CLI_EXIT_COMPLETED,
    CLI_EXIT_COMPLETED_WITH_BLOCKERS,
    CLI_EXIT_CONFLICT_OR_DRIFT,
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
        description="AI runtime migration inventory and evidence commands.",
    )
    sub = p.add_subparsers(dest="group", required=True)

    inv = sub.add_parser("inventory", help="Inventory commands")
    inv_sub = inv.add_subparsers(dest="cmd", required=True)

    scan_p = inv_sub.add_parser("scan", help="Scan migration inventory (read-only)")
    _add_safety_flags(scan_p)
    scan_p.add_argument(
        "--fixture-json",
        default=None,
        help="Optional sanitized fixture path for local/dev inventory scans",
    )

    prepare_p = inv_sub.add_parser(
        "prepare",
        help="Prepare/dry-run discovered inventory evidence batch",
    )
    _add_safety_flags(prepare_p)
    prepare_p.add_argument("--fixture-json", default=None)
    prepare_p.add_argument(
        "--operator-principal",
        default=None,
        help="Operator principal id (required for --apply; fail-closed if missing)",
    )

    apply_p = inv_sub.add_parser(
        "apply",
        help="Apply discovered inventory evidence (requires operator principal)",
    )
    _add_safety_flags(apply_p)
    apply_p.add_argument("--fixture-json", default=None)
    apply_p.add_argument("--operator-principal", default=None)
    apply_p.add_argument(
        "--prepared-batch-id",
        default=None,
        help="Optional prepared batch id from a prior dry-run",
    )
    apply_p.add_argument(
        "--prepared-batch-digest",
        default=None,
        help="Optional prepared batch report digest binding",
    )

    resume_p = inv_sub.add_parser(
        "resume",
        help="Resume a prepared/running inventory evidence batch",
    )
    _add_safety_flags(resume_p)
    resume_p.add_argument("--batch-id", required=True)
    resume_p.add_argument("--expected-state-revision", type=int, required=True)
    resume_p.add_argument("--operator-principal", default=None)
    resume_p.add_argument("--fixture-json", default=None)

    # Mutation groups — stubs until later tasks.
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


def _resolve_operator_principal(raw: str | None) -> str | None:
    """Fail-closed operator principal for --apply mutations.

    Accepts CLI flag or MINDATLAS_MIGRATION_OPERATOR_PRINCIPAL env. No flag can
    mint a role; presence of a non-empty principal id is the only local allow.
    """
    value = (raw or "").strip() or os.environ.get(
        "MINDATLAS_MIGRATION_OPERATOR_PRINCIPAL", ""
    ).strip()
    return value or None


def _complete_inventory_scan(
    records: Mapping[str, Any],
    *,
    environment: str,
    database_fingerprint: str,
    schema_head: str,
    build_revision: str,
    request_id: str,
    report_json: str,
) -> int:
    """Shared dry-run inventory scan path (fixture or injected loader)."""
    from app.assistant.migration.inventory import (
        build_safe_inventory_report,
        scan_inventory_from_records,
    )

    bound = {
        **dict(records),
        "environment": environment,
        "database_fingerprint": database_fingerprint,
        "schema_head": schema_head,
        "build_revision": build_revision,
    }
    snapshot = scan_inventory_from_records(bound)
    report = build_safe_inventory_report(
        snapshot,
        dry_run=True,
        request_id=request_id,
    )
    payload = report.model_dump(mode="json", by_alias=True)
    _write_report(report_json, payload)
    _emit(
        {
            "ok": True,
            "command": "inventory.scan",
            "snapshotDigest": snapshot.snapshot_digest,
            "blockerCount": snapshot.blocker_count,
            "counts": snapshot.counts,
            "reportJson": str(report_json),
            "dryRun": True,
        }
    )
    return CLI_EXIT_COMPLETED


def _run_inventory_scan(
    args: argparse.Namespace,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> int:
    if not args.dry_run:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "inventory_scan_is_dry_run_only",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    if args.fixture_json:
        records = _load_fixture(args.fixture_json)
    elif records_loader is not None:
        records = dict(records_loader())
    else:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "fixture_json_required_until_db_adapter",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    return _complete_inventory_scan(
        records,
        environment=args.environment,
        database_fingerprint=args.database_fingerprint,
        schema_head=args.expected_schema_head,
        build_revision=args.expected_build_revision,
        request_id=str(args.request_id),
        report_json=str(args.report_json),
    )


def _load_records(
    args: argparse.Namespace,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any] | None:
    if getattr(args, "fixture_json", None):
        return _load_fixture(args.fixture_json)
    if records_loader is not None:
        return dict(records_loader())
    return None


def _run_inventory_prepare_or_apply(
    args: argparse.Namespace,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    """Prepare (dry-run) or apply discovered inventory evidence."""
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.migration.discovery import backfill_discovered_from_snapshot
    from app.assistant.migration.inventory import scan_inventory_from_records
    from app.assistant.migration.repository import RuntimeMigrationRepositoryError

    dry_run = bool(args.dry_run)
    apply = bool(args.apply)
    if args.cmd == "prepare" and apply:
        # prepare is always dry-run projection; --apply is rejected.
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "inventory_prepare_is_dry_run_only",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED
    if args.cmd == "apply" and dry_run:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "inventory_apply_requires_apply_flag",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    operator = _resolve_operator_principal(getattr(args, "operator_principal", None))
    if apply and not operator:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "operator_principal_required_for_apply",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    records = _load_records(args, records_loader=records_loader)
    if records is None:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "fixture_json_required_until_db_adapter",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    bound = {
        **dict(records),
        "environment": args.environment,
        "database_fingerprint": args.database_fingerprint,
        "schema_head": args.expected_schema_head,
        "build_revision": args.expected_build_revision,
    }
    snapshot = scan_inventory_from_records(bound)

    # Binding check: caller-supplied source-snapshot-digest must match scan.
    if str(args.source_snapshot_digest).strip().lower() != snapshot.snapshot_digest:
        # Allow wildcard token for first-time dry-run binding discovery.
        if str(args.source_snapshot_digest).strip().lower() not in {
            "0" * 64,
            "discover",
        }:
            _emit(
                {
                    "ok": False,
                    "error": "conflict_or_drift",
                    "reason": "source_snapshot_digest_mismatch",
                    "expected": str(args.source_snapshot_digest),
                    "observed": snapshot.snapshot_digest,
                }
            )
            return CLI_EXIT_CONFLICT_OR_DRIFT

    if dry_run and session_factory is None:
        # Pure dry-run without DB: project counts only.
        report = {
            "ok": True,
            "command": f"inventory.{args.cmd}",
            "dryRun": True,
            "snapshotDigest": snapshot.snapshot_digest,
            "blockerCount": snapshot.blocker_count,
            "counts": snapshot.counts,
            "projectedCreated": len(snapshot.items),
            "projectedUnchanged": 0,
            "projectedDrifted": 0,
            "requestId": str(args.request_id),
            "environment": args.environment,
            "databaseFingerprint": args.database_fingerprint,
            "schemaHead": args.expected_schema_head,
            "buildRevision": args.expected_build_revision,
        }
        report["reportDigest"] = sha256_canonical_json(report)
        _write_report(args.report_json, report)
        _emit(
            {
                "ok": True,
                "command": f"inventory.{args.cmd}",
                "dryRun": True,
                "snapshotDigest": snapshot.snapshot_digest,
                "reportDigest": report["reportDigest"],
                "reportJson": str(args.report_json),
            }
        )
        return CLI_EXIT_COMPLETED

    if session_factory is None:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "db_session_required_for_evidence_write",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    session = session_factory()
    try:
        result = backfill_discovered_from_snapshot(
            session,
            snapshot,
            request_id=str(args.request_id),
            actor_principal=operator,
            dry_run=dry_run,
            batch_size=int(args.batch_size),
        )
        if not dry_run:
            session.commit()
        else:
            session.rollback()
    except RuntimeMigrationRepositoryError as exc:
        session.rollback()
        code = CLI_EXIT_CONFLICT_OR_DRIFT if exc.code in {
            "conflict",
            "drift",
            "stale_revision",
            "immutable",
        } else CLI_EXIT_PRECONDITION_FAILED
        _emit(
            {
                "ok": False,
                "error": exc.code,
                "reason": exc.message[:200],
            }
        )
        return code
    except Exception:
        session.rollback()
        raise
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    report = {
        "ok": True,
        "command": f"inventory.{args.cmd}",
        "dryRun": dry_run,
        "snapshotDigest": snapshot.snapshot_digest,
        "blockerCount": snapshot.blocker_count,
        "created": result.created,
        "unchanged": result.unchanged,
        "drifted": result.drifted,
        "batchId": str(result.batch_id) if result.batch_id else None,
        "reportDigest": result.report_digest,
        "requestId": str(args.request_id),
        "environment": args.environment,
        "databaseFingerprint": args.database_fingerprint,
        "schemaHead": args.expected_schema_head,
        "buildRevision": args.expected_build_revision,
    }
    _write_report(args.report_json, report)
    _emit(
        {
            "ok": True,
            "command": f"inventory.{args.cmd}",
            "dryRun": dry_run,
            "snapshotDigest": snapshot.snapshot_digest,
            "created": result.created,
            "unchanged": result.unchanged,
            "drifted": result.drifted,
            "batchId": str(result.batch_id) if result.batch_id else None,
            "reportDigest": result.report_digest,
            "reportJson": str(args.report_json),
        }
    )
    if result.drifted > 0 or snapshot.blocker_count > 0:
        return CLI_EXIT_COMPLETED_WITH_BLOCKERS
    return CLI_EXIT_COMPLETED


def _run_inventory_resume(
    args: argparse.Namespace,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    from app.assistant.migration.discovery import backfill_discovered_from_snapshot
    from app.assistant.migration.inventory import scan_inventory_from_records
    from app.assistant.migration.repository import (
        RuntimeMigrationRepository,
        RuntimeMigrationRepositoryError,
    )

    if not args.apply:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "inventory_resume_requires_apply",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    operator = _resolve_operator_principal(getattr(args, "operator_principal", None))
    if not operator:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "operator_principal_required_for_apply",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    if session_factory is None:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "db_session_required_for_evidence_write",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    records = _load_records(args, records_loader=records_loader)
    if records is None:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "fixture_json_required_until_db_adapter",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    bound = {
        **dict(records),
        "environment": args.environment,
        "database_fingerprint": args.database_fingerprint,
        "schema_head": args.expected_schema_head,
        "build_revision": args.expected_build_revision,
    }
    snapshot = scan_inventory_from_records(bound)
    session = session_factory()
    try:
        repo = RuntimeMigrationRepository(session)
        from app.assistant.domain.digests import sha256_canonical_json

        config_digest = sha256_canonical_json(
            {
                "command": "inventory.backfill",
                "batchSize": int(args.batch_size),
                "snapshotDigest": snapshot.snapshot_digest,
            }
        )
        repo.resume_batch(
            batch_id=UUID(str(args.batch_id)),
            expected_revision=int(args.expected_state_revision),
            source_snapshot_digest=snapshot.snapshot_digest,
            configuration_digest=config_digest,
            build_revision=args.expected_build_revision,
            schema_revision=args.expected_schema_head,
        )
        result = backfill_discovered_from_snapshot(
            session,
            snapshot,
            request_id=str(args.request_id),
            actor_principal=operator,
            dry_run=False,
            batch_size=int(args.batch_size),
        )
        session.commit()
    except RuntimeMigrationRepositoryError as exc:
        session.rollback()
        code = (
            CLI_EXIT_CONFLICT_OR_DRIFT
            if exc.code in {"conflict", "drift", "stale_revision"}
            else CLI_EXIT_PRECONDITION_FAILED
        )
        _emit({"ok": False, "error": exc.code, "reason": exc.message[:200]})
        return code
    except Exception:
        session.rollback()
        raise
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    report = {
        "ok": True,
        "command": "inventory.resume",
        "dryRun": False,
        "batchId": str(args.batch_id),
        "created": result.created,
        "unchanged": result.unchanged,
        "drifted": result.drifted,
        "reportDigest": result.report_digest,
        "requestId": str(args.request_id),
    }
    _write_report(args.report_json, report)
    _emit(
        {
            "ok": True,
            "command": "inventory.resume",
            "batchId": str(args.batch_id),
            "reportDigest": result.report_digest,
            "reportJson": str(args.report_json),
        }
    )
    if result.drifted > 0:
        return CLI_EXIT_COMPLETED_WITH_BLOCKERS
    return CLI_EXIT_COMPLETED


def _run_stub(args: argparse.Namespace) -> int:
    _emit(
        {
            "ok": False,
            "error": "precondition_failed",
            "reason": "command_not_implemented",
            "group": getattr(args, "group", None),
            "cmd": getattr(args, "cmd", None),
        }
    )
    return CLI_EXIT_PRECONDITION_FAILED


def main(
    argv: list[str] | None = None,
    *,
    records_loader: Callable[[], Mapping[str, Any]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    """CLI entrypoint. Returns stable exit codes (0/2/3/4/5)."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code or 1)
        return CLI_EXIT_PRECONDITION_FAILED if code != 0 else CLI_EXIT_COMPLETED

    try:
        if args.group == "inventory" and args.cmd == "scan":
            return _run_inventory_scan(args, records_loader=records_loader)
        if args.group == "inventory" and args.cmd in {"prepare", "apply"}:
            return _run_inventory_prepare_or_apply(
                args,
                records_loader=records_loader,
                session_factory=session_factory,
            )
        if args.group == "inventory" and args.cmd == "resume":
            return _run_inventory_resume(
                args,
                records_loader=records_loader,
                session_factory=session_factory,
            )
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
