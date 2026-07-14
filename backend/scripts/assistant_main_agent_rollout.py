#!/usr/bin/env python3
"""Operator CLI for Plan 04 Main Agent golden-path rollout.

Usage:
  python backend/scripts/assistant_main_agent_rollout.py plan --dry-run
  python backend/scripts/assistant_main_agent_rollout.py enable
  python backend/scripts/assistant_main_agent_rollout.py disable

Environment:
  DATABASE_URL / app settings as for the backend process.
  APP_BUILD_REVISION should match the deployed build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

# Allow `python backend/scripts/...` without installing the package.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan 04 Main Agent golden-path rollout (plan|enable|disable)"
    )
    parser.add_argument(
        "operation",
        choices=("plan", "enable", "disable"),
        help="rollout operation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute expected digests without flipping aggregate flags",
    )
    parser.add_argument(
        "--no-fixture",
        action="store_true",
        help="do not create the pure-read fixture if quick_stats is unavailable",
    )
    parser.add_argument(
        "--prefer-quick-stats",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prefer quick_stats when classifiable as read-only (default: true)",
    )
    parser.add_argument(
        "--require-probe",
        action="store_true",
        help="require a current matching passed model probe before enable",
    )
    parser.add_argument(
        "--package-id",
        type=str,
        default=None,
        help="optional package UUID for disable (defaults to catalog-enabled packages)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON report only",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from app.database import SessionLocal
    from app.assistant.main_agent.rollout import run_rollout

    package_id = UUID(args.package_id) if args.package_id else None
    db = SessionLocal()
    try:
        report = run_rollout(
            db,
            args.operation,
            dry_run=bool(args.dry_run),
            prefer_quick_stats=bool(args.prefer_quick_stats),
            allow_create_fixture=not bool(args.no_fixture),
            require_probe=bool(args.require_probe),
            package_id=package_id,
        )
    finally:
        db.close()

    if args.json:
        print(report.to_json())
    else:
        print(f"operation={report.operation} dry_run={report.dry_run}")
        print(f"success={report.success} reason={report.reason_code}")
        print(f"message={report.message}")
        if report.expected is not None:
            print("expected:")
            print(json.dumps(report.to_dict().get("expected"), indent=2, default=str))
        if report.steps:
            print("steps:")
            for step in report.steps:
                print(f"  - {step}")
        print(
            "flags: "
            f"catalog_enabled={report.package_catalog_enabled} "
            f"package_migration={report.package_migration_state} "
            f"runtime_enabled={report.profile_runtime_enabled} "
            f"profile_migration={report.profile_migration_state}"
        )
        if report.other_catalog_enabled_packages:
            print(
                "other_catalog_enabled_packages="
                + ",".join(report.other_catalog_enabled_packages)
            )

    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
