#!/usr/bin/env python3
"""Closed command surface for production qualification/rehearsal runs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fixed MindAtlas pre-GA release profile")
    commands = parser.add_subparsers(dest="command", required=True)
    evidence = commands.add_parser("evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    run = evidence_commands.add_parser("run")
    run.add_argument("--kind", choices=("automated_qualification", "production_rehearsal"), required=True)
    run.add_argument("--profile-url", required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--signing-key-fd", type=int, required=True)
    run.add_argument("--trust-set", type=Path, required=True)
    return parser


def _validate_run(args: argparse.Namespace) -> None:
    parsed = urlparse(args.profile_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("release_profile_url_invalid")
    if args.output_dir.exists():
        if not args.output_dir.is_dir() or any(args.output_dir.iterdir()):
            raise ValueError("release_output_dir_must_be_empty")
    else:
        args.output_dir.mkdir(parents=True)
    if args.signing_key_fd < 0:
        raise ValueError("release_signing_key_fd_invalid")
    try:
        os.fstat(args.signing_key_fd)
    except OSError:
        raise ValueError("release_signing_key_fd_unavailable") from None
    if not args.trust_set.is_file():
        raise ValueError("release_trust_set_missing")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _validate_run(args)
    except ValueError as exc:
        print(str(exc))
        return 2
    # The actual profile orchestration is intentionally a separate server-owned
    # runner.  This wrapper cannot manufacture assertion outcomes or launch.
    print("release_runner_requires_server_profile")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
