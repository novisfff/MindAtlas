"""Guarded local CLI for AI runtime migration (Plan 10).

Not mounted as HTTP. Inventory scan is read-only; inventory prepare/apply/resume
write discovered evidence under --apply with operator principal fail-closed.
Packages and L2 mutation groups are implemented; approvals/rollout/cleanup
remain stubs until later tasks.

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

# Placeholder tokens allowed only for dry-run prepare/scan digest discovery.
_WILDCARD_SOURCE_DIGESTS = frozenset({"0" * 64, "discover"})


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
        help="Operator principal id (optional for prepare; required for apply)",
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
        required=True,
        help="Prepared batch id from a prior inventory prepare (dry-run) write",
    )
    apply_p.add_argument(
        "--prepared-batch-digest",
        required=True,
        help="Prepared batch dry-run / report digest binding",
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

    # Packages migrate/verify (Task 2).
    pkg = sub.add_parser("packages", help="Skill package / profile migration commands")
    pkg_sub = pkg.add_subparsers(dest="cmd", required=True)
    for cmd, help_text in (
        ("migrate", "Migrate legacy skills to native packages / Main Agent Profile"),
        ("verify", "Independent verify pass for migrated packages/profile"),
    ):
        cmd_p = pkg_sub.add_parser(cmd, help=help_text)
        _add_safety_flags(cmd_p)
        cmd_p.add_argument(
            "--operator-principal",
            default=None,
            help="Operator principal id (required for --apply)",
        )
        cmd_p.add_argument(
            "--skill-id",
            action="append",
            default=None,
            help="Limit to one or more legacy skill UUIDs (repeatable)",
        )
        if cmd == "migrate":
            cmd_p.add_argument(
                "--with-verify",
                action="store_true",
                help="Run independent verify pass after migrate",
            )
            cmd_p.add_argument(
                "--write-branches-json",
                default=None,
                help="Optional JSON file with write_branches inventory records",
            )

    # L2 backfill/verify (Task 3).
    l2 = sub.add_parser("l2", help="L2 skill memory package-ID backfill commands")
    l2_sub = l2.add_subparsers(dest="cmd", required=True)
    for cmd, help_text in (
        ("backfill", "Backfill legacy L2 rows onto package/namespace identity"),
        ("verify", "Independent verify pass for L2 package-ID migration"),
    ):
        cmd_p = l2_sub.add_parser(cmd, help=help_text)
        _add_safety_flags(cmd_p)
        cmd_p.add_argument(
            "--operator-principal",
            default=None,
            help="Operator principal id (required for --apply)",
        )
        if cmd == "backfill":
            cmd_p.add_argument(
                "--resume-cursor",
                default=None,
                help="Optional L2 row UUID cursor to resume after",
            )
            cmd_p.add_argument(
                "--row-id",
                action="append",
                default=None,
                help="Limit to one or more L2 row UUIDs (repeatable)",
            )
            cmd_p.add_argument(
                "--conversation-id",
                action="append",
                default=None,
                help="Limit to one or more conversation UUIDs (repeatable)",
            )
        if cmd == "verify":
            cmd_p.add_argument(
                "--require-zero-legacy",
                action="store_true",
                help="Fail verify when any legacy null-package L2 rows remain",
            )
            cmd_p.add_argument(
                "--stability-scans",
                type=int,
                default=2,
                help="Consecutive invariant scans required for zero-delta stability",
            )

    # Remaining mutation groups — stubs until later tasks.
    for group_name, commands in (
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


def _default_session_factory() -> Callable[[], Any]:
    """Match capability_calls CLI: default to app.database.SessionLocal."""
    from app.database import SessionLocal

    return SessionLocal


def _resolve_session_factory(
    session_factory: Callable[[], Any] | None,
    *,
    require: bool,
) -> Callable[[], Any] | None:
    if session_factory is not None:
        return session_factory
    if not require:
        return None
    return _default_session_factory()


def _is_wildcard_source_digest(value: str) -> bool:
    return str(value).strip().lower() in _WILDCARD_SOURCE_DIGESTS


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


def _bind_source_snapshot_digest(
    args: argparse.Namespace,
    snapshot_digest: str,
    *,
    allow_wildcard: bool,
) -> int | None:
    """Return exit code on mismatch; None if binding is acceptable."""
    supplied = str(args.source_snapshot_digest).strip().lower()
    if supplied == snapshot_digest:
        return None
    if allow_wildcard and _is_wildcard_source_digest(supplied):
        return None
    reason = (
        "wildcard_source_snapshot_digest_not_allowed_on_apply"
        if _is_wildcard_source_digest(supplied) and not allow_wildcard
        else "source_snapshot_digest_mismatch"
    )
    _emit(
        {
            "ok": False,
            "error": "conflict_or_drift",
            "reason": reason,
            "expected": str(args.source_snapshot_digest),
            "observed": snapshot_digest,
        }
    )
    return CLI_EXIT_CONFLICT_OR_DRIFT


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
    from app.assistant.migration.repository import (
        RuntimeMigrationRepository,
        RuntimeMigrationRepositoryError,
    )

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

    bind_code = _bind_source_snapshot_digest(
        args,
        snapshot.snapshot_digest,
        allow_wildcard=dry_run and not apply,
    )
    if bind_code is not None:
        return bind_code

    # Pure dry-run prepare without DB: project counts only (no durable batch).
    # When a session_factory is injected, prepare writes a durable prepared batch
    # for later apply binding.
    if dry_run and session_factory is None:
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

    if dry_run:
        # Durable prepare: requires injected session_factory (no silent DB open).
        session = session_factory()  # type: ignore[misc]
        try:
            result = backfill_discovered_from_snapshot(
                session,
                snapshot,
                request_id=str(args.request_id),
                actor_principal=operator,
                dry_run=True,
                prepare_only=True,
                batch_size=int(args.batch_size),
            )
            session.commit()
        except RuntimeMigrationRepositoryError as exc:
            session.rollback()
            code = (
                CLI_EXIT_CONFLICT_OR_DRIFT
                if exc.code in {"conflict", "drift", "stale_revision", "immutable"}
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
            "command": f"inventory.{args.cmd}",
            "dryRun": True,
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
                "dryRun": True,
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

    # Apply path: default SessionLocal when not injected (capability CLI pattern).
    resolved_factory = _resolve_session_factory(session_factory, require=True)

    # Apply path: require prepared batch id + digest binding.
    prepared_batch_id_raw = getattr(args, "prepared_batch_id", None)
    prepared_batch_digest_raw = getattr(args, "prepared_batch_digest", None)
    if not prepared_batch_id_raw or not prepared_batch_digest_raw:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "prepared_batch_id_and_digest_required_for_apply",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    try:
        prepared_batch_id = UUID(str(prepared_batch_id_raw))
    except (TypeError, ValueError):
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "prepared_batch_id_invalid",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    prepared_batch_digest = str(prepared_batch_digest_raw).strip().lower()
    if len(prepared_batch_digest) != 64 or any(
        ch not in "0123456789abcdef" for ch in prepared_batch_digest
    ):
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "prepared_batch_digest_invalid",
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    assert resolved_factory is not None
    session = resolved_factory()
    try:
        repo = RuntimeMigrationRepository(session)
        batch = repo.get_batch(prepared_batch_id)
        if batch is None:
            _emit(
                {
                    "ok": False,
                    "error": "precondition_failed",
                    "reason": "prepared_batch_not_found",
                }
            )
            return CLI_EXIT_PRECONDITION_FAILED
        if str(batch.status) != "prepared":
            _emit(
                {
                    "ok": False,
                    "error": "conflict_or_drift",
                    "reason": "prepared_batch_not_in_prepared_status",
                    "status": str(batch.status),
                }
            )
            return CLI_EXIT_CONFLICT_OR_DRIFT
        bound_digest = str(batch.dry_run_digest or batch.report_digest or "").lower()
        if bound_digest != prepared_batch_digest:
            _emit(
                {
                    "ok": False,
                    "error": "conflict_or_drift",
                    "reason": "prepared_batch_digest_mismatch",
                    "expected": prepared_batch_digest,
                    "observed": bound_digest or None,
                }
            )
            return CLI_EXIT_CONFLICT_OR_DRIFT
        if str(batch.source_snapshot_digest) != snapshot.snapshot_digest:
            _emit(
                {
                    "ok": False,
                    "error": "conflict_or_drift",
                    "reason": "prepared_batch_source_snapshot_digest_mismatch",
                    "expected": str(batch.source_snapshot_digest),
                    "observed": snapshot.snapshot_digest,
                }
            )
            return CLI_EXIT_CONFLICT_OR_DRIFT

        result = backfill_discovered_from_snapshot(
            session,
            snapshot,
            request_id=str(batch.request_id),
            actor_principal=operator,
            dry_run=False,
            batch_size=int(args.batch_size),
            batch_id=batch.id,
        )
        session.commit()
    except RuntimeMigrationRepositoryError as exc:
        session.rollback()
        code = (
            CLI_EXIT_CONFLICT_OR_DRIFT
            if exc.code in {"conflict", "drift", "stale_revision", "immutable"}
            else CLI_EXIT_PRECONDITION_FAILED
        )
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
        "dryRun": False,
        "snapshotDigest": snapshot.snapshot_digest,
        "blockerCount": snapshot.blocker_count,
        "created": result.created,
        "unchanged": result.unchanged,
        "drifted": result.drifted,
        "batchId": str(result.batch_id) if result.batch_id else None,
        "reportDigest": result.report_digest,
        "requestId": str(args.request_id),
        "preparedBatchId": str(prepared_batch_id),
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
            "dryRun": False,
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
    from app.assistant.domain.digests import sha256_canonical_json
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

    if _is_wildcard_source_digest(str(args.source_snapshot_digest)):
        _emit(
            {
                "ok": False,
                "error": "conflict_or_drift",
                "reason": "wildcard_source_snapshot_digest_not_allowed_on_apply",
                "expected": str(args.source_snapshot_digest),
            }
        )
        return CLI_EXIT_CONFLICT_OR_DRIFT

    resolved_factory = _resolve_session_factory(session_factory, require=True)
    assert resolved_factory is not None

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

    bind_code = _bind_source_snapshot_digest(
        args,
        snapshot.snapshot_digest,
        allow_wildcard=False,
    )
    if bind_code is not None:
        return bind_code

    session = resolved_factory()
    try:
        repo = RuntimeMigrationRepository(session)
        config_digest = sha256_canonical_json(
            {
                "command": "inventory.backfill",
                "batchSize": int(args.batch_size),
                "snapshotDigest": snapshot.snapshot_digest,
            }
        )
        resumed = repo.resume_batch(
            batch_id=UUID(str(args.batch_id)),
            expected_revision=int(args.expected_state_revision),
            source_snapshot_digest=snapshot.snapshot_digest,
            configuration_digest=config_digest,
            build_revision=args.expected_build_revision,
            schema_revision=args.expected_schema_head,
        )
        # Continue the same batch row; CLI --request-id is ignored for identity.
        result = backfill_discovered_from_snapshot(
            session,
            snapshot,
            request_id=str(resumed.request_id),
            actor_principal=operator,
            dry_run=False,
            batch_size=int(args.batch_size),
            batch_id=resumed.id,
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

    completed_batch_id = str(result.batch_id or args.batch_id)
    report = {
        "ok": True,
        "command": "inventory.resume",
        "dryRun": False,
        "batchId": completed_batch_id,
        "created": result.created,
        "unchanged": result.unchanged,
        "drifted": result.drifted,
        "reportDigest": result.report_digest,
        # CLI --request-id is invocation metadata only; batch identity is batchId.
        "requestId": str(args.request_id),
    }
    _write_report(args.report_json, report)
    _emit(
        {
            "ok": True,
            "command": "inventory.resume",
            "batchId": completed_batch_id,
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


def _parse_skill_ids(raw: list[str] | None) -> list[UUID] | None:
    if not raw:
        return None
    out: list[UUID] = []
    for value in raw:
        try:
            out.append(UUID(str(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid skill-id: {value}") from exc
    return out


def _parse_uuid_list(raw: list[str] | None, *, field: str) -> list[UUID] | None:
    if not raw:
        return None
    out: list[UUID] = []
    for value in raw:
        try:
            out.append(UUID(str(value)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field}: {value}") from exc
    return out


def _run_l2_command(
    args: argparse.Namespace,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    """l2 backfill / l2 verify."""
    from app.assistant.migration.l2 import backfill_l2, verify_l2
    from app.assistant.migration.repository import RuntimeMigrationRepositoryError

    dry_run = bool(args.dry_run)
    apply = bool(args.apply)
    if dry_run == apply:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "exactly_one_of_dry_run_or_apply",
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

    if apply and _is_wildcard_source_digest(str(args.source_snapshot_digest)):
        _emit(
            {
                "ok": False,
                "error": "conflict_or_drift",
                "reason": "wildcard_source_snapshot_digest_not_allowed_on_apply",
                "expected": str(args.source_snapshot_digest),
            }
        )
        return CLI_EXIT_CONFLICT_OR_DRIFT

    try:
        row_ids = _parse_uuid_list(getattr(args, "row_id", None), field="row-id")
        conversation_ids = _parse_uuid_list(
            getattr(args, "conversation_id", None), field="conversation-id"
        )
    except ValueError as exc:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "uuid_argument_invalid",
                "message": str(exc)[:200],
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    resolved_factory = _resolve_session_factory(session_factory, require=True)
    assert resolved_factory is not None
    session = resolved_factory()
    try:
        common = dict(
            request_id=str(args.request_id),
            actor_principal=operator,
            build_revision=str(args.expected_build_revision),
            environment=str(args.environment),
            database_fingerprint=str(args.database_fingerprint),
            schema_head=str(args.expected_schema_head),
            dry_run=dry_run,
            batch_size=int(args.batch_size),
            source_snapshot_digest=str(args.source_snapshot_digest),
        )
        if args.cmd == "backfill":
            report = backfill_l2(
                session,
                resume_cursor=getattr(args, "resume_cursor", None),
                row_ids=row_ids,
                conversation_ids=conversation_ids,
                **common,
            )
        else:
            report = verify_l2(
                session,
                require_zero_legacy=bool(getattr(args, "require_zero_legacy", False)),
                stability_scans=int(getattr(args, "stability_scans", 2) or 2),
                **common,
            )
        if not dry_run:
            session.commit()
        else:
            session.rollback()
    except RuntimeMigrationRepositoryError as exc:
        session.rollback()
        code = (
            CLI_EXIT_CONFLICT_OR_DRIFT
            if exc.code in {"conflict", "drift", "stale_revision", "immutable"}
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
            if session_factory is None:
                close()

    payload = report.to_dict()
    payload["environment"] = str(args.environment)
    payload["databaseFingerprint"] = str(args.database_fingerprint)
    payload["schemaHead"] = str(args.expected_schema_head)
    payload["buildRevision"] = str(args.expected_build_revision)
    payload["sourceSnapshotDigest"] = str(args.source_snapshot_digest)
    _write_report(args.report_json, payload)
    _emit(
        {
            "ok": payload.get("ok", True),
            "command": payload.get("command"),
            "dryRun": dry_run,
            "processed": payload.get("processed"),
            "succeeded": payload.get("succeeded"),
            "blocked": payload.get("blocked"),
            "failed": payload.get("failed"),
            "unchanged": payload.get("unchanged"),
            "batchId": payload.get("batchId"),
            "reportDigest": payload.get("reportDigest"),
            "resumeCursor": payload.get("resumeCursor"),
            "consecutiveZeroDelta": payload.get("consecutiveZeroDelta"),
            "reportJson": str(args.report_json),
        }
    )
    if int(payload.get("failed") or 0) > 0:
        return CLI_EXIT_UNEXPECTED_FAILURE
    if int(payload.get("blocked") or 0) > 0:
        return CLI_EXIT_COMPLETED_WITH_BLOCKERS
    return CLI_EXIT_COMPLETED


def _run_packages_command(
    args: argparse.Namespace,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> int:
    """packages migrate / packages verify."""
    from app.assistant.migration.packages import migrate_packages, verify_packages
    from app.assistant.migration.repository import RuntimeMigrationRepositoryError

    dry_run = bool(args.dry_run)
    apply = bool(args.apply)
    if dry_run == apply:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "exactly_one_of_dry_run_or_apply",
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

    if apply and _is_wildcard_source_digest(str(args.source_snapshot_digest)):
        _emit(
            {
                "ok": False,
                "error": "conflict_or_drift",
                "reason": "wildcard_source_snapshot_digest_not_allowed_on_apply",
                "expected": str(args.source_snapshot_digest),
            }
        )
        return CLI_EXIT_CONFLICT_OR_DRIFT

    try:
        skill_ids = _parse_skill_ids(getattr(args, "skill_id", None))
    except ValueError as exc:
        _emit(
            {
                "ok": False,
                "error": "precondition_failed",
                "reason": "skill_id_invalid",
                "message": str(exc)[:200],
            }
        )
        return CLI_EXIT_PRECONDITION_FAILED

    write_branches: list[dict[str, Any]] | None = None
    wb_path = getattr(args, "write_branches_json", None)
    if wb_path:
        loaded = _load_fixture(wb_path)
        if isinstance(loaded, dict) and isinstance(loaded.get("write_branches"), list):
            write_branches = list(loaded["write_branches"])
        elif isinstance(loaded, list):
            write_branches = list(loaded)
        else:
            _emit(
                {
                    "ok": False,
                    "error": "precondition_failed",
                    "reason": "write_branches_json_invalid",
                }
            )
            return CLI_EXIT_PRECONDITION_FAILED

    resolved_factory = _resolve_session_factory(session_factory, require=True)
    assert resolved_factory is not None
    session = resolved_factory()
    try:
        common = dict(
            request_id=str(args.request_id),
            actor_principal=operator,
            build_revision=str(args.expected_build_revision),
            environment=str(args.environment),
            database_fingerprint=str(args.database_fingerprint),
            schema_head=str(args.expected_schema_head),
            dry_run=dry_run,
            skill_ids=skill_ids,
            batch_size=int(args.batch_size),
            source_snapshot_digest=str(args.source_snapshot_digest),
        )
        if args.cmd == "migrate":
            report = migrate_packages(
                session,
                write_branches=write_branches,
                verify=bool(getattr(args, "with_verify", False)) and not dry_run,
                **common,
            )
        else:
            report = verify_packages(session, **common)
        if not dry_run:
            session.commit()
        else:
            session.rollback()
    except RuntimeMigrationRepositoryError as exc:
        session.rollback()
        code = (
            CLI_EXIT_CONFLICT_OR_DRIFT
            if exc.code in {"conflict", "drift", "stale_revision", "immutable"}
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
            # Injected test sessions may be shared; only close default factories.
            if session_factory is None:
                close()

    payload = report.to_dict()
    payload["environment"] = str(args.environment)
    payload["databaseFingerprint"] = str(args.database_fingerprint)
    payload["schemaHead"] = str(args.expected_schema_head)
    payload["buildRevision"] = str(args.expected_build_revision)
    payload["sourceSnapshotDigest"] = str(args.source_snapshot_digest)
    _write_report(args.report_json, payload)
    _emit(
        {
            "ok": payload.get("ok", True),
            "command": payload.get("command"),
            "dryRun": dry_run,
            "processed": payload.get("processed"),
            "succeeded": payload.get("succeeded"),
            "blocked": payload.get("blocked"),
            "failed": payload.get("failed"),
            "archived": payload.get("archived"),
            "batchId": payload.get("batchId"),
            "reportDigest": payload.get("reportDigest"),
            "reportJson": str(args.report_json),
        }
    )
    if int(payload.get("failed") or 0) > 0:
        return CLI_EXIT_UNEXPECTED_FAILURE
    if int(payload.get("blocked") or 0) > 0:
        return CLI_EXIT_COMPLETED_WITH_BLOCKERS
    return CLI_EXIT_COMPLETED


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
        if args.group == "packages" and args.cmd in {"migrate", "verify"}:
            return _run_packages_command(args, session_factory=session_factory)
        if args.group == "l2" and args.cmd in {"backfill", "verify"}:
            return _run_l2_command(args, session_factory=session_factory)
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
