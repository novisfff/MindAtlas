"""Run explicit backend test-order regressions with bounded evidence output."""

from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = BACKEND_ROOT / "tests"
MODES = {
    "streaming-then-tombstone": (
        "tests/test_durable_run_streaming.py",
        "tests/test_ai_runtime_legacy_cleanup.py",
    ),
    "tombstone-then-streaming": (
        "tests/test_ai_runtime_legacy_cleanup.py",
        "tests/test_durable_run_streaming.py",
    ),
    "isolated": (),
    "seeded": (),
}
_COLLECTED_NODE_RE = re.compile(r"^(tests/[^\s:]+\.py)::")


def _validate_order(paths: Sequence[str]) -> tuple[str, ...]:
    if not paths:
        raise ValueError("test order is empty")
    validated: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_absolute() or path.parts[:1] != ("tests",):
            raise ValueError(f"test path is outside backend/tests: {raw}")
        relative = path.as_posix()
        resolved = (BACKEND_ROOT / path).resolve()
        if TEST_ROOT not in resolved.parents or not resolved.is_file():
            raise ValueError(f"test path is not a file under backend/tests: {raw}")
        if relative in seen:
            raise ValueError(f"duplicate test path: {relative}")
        seen.add(relative)
        validated.append(relative)
    return tuple(validated)


def order_digest(paths: Sequence[str]) -> str:
    order = _validate_order(paths)
    payload = "mindatlas:test-order:v1\n" + "\n".join(order) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_backend_test_files() -> tuple[str, ...]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("pytest collection failed")
    paths: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        match = _COLLECTED_NODE_RE.match(line.strip())
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            paths.append(match.group(1))
    return _validate_order(sorted(paths))


def _run_pytest(paths: Sequence[str]) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q"],
        cwd=BACKEND_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return int(result.returncode)


def _ordered_paths(mode: str, seed: int | None) -> tuple[str, ...]:
    if mode in {"streaming-then-tombstone", "tombstone-then-streaming"}:
        if seed is not None:
            raise ValueError("--seed is only valid with --mode seeded")
        return _validate_order(MODES[mode])
    if mode == "isolated":
        if seed is not None:
            raise ValueError("--seed is only valid with --mode seeded")
        return _validate_order(
            (
                "tests/test_durable_run_streaming.py",
                "tests/test_ai_runtime_legacy_cleanup.py",
            )
        )
    if mode == "seeded":
        if seed is None:
            raise ValueError("--seed is required with --mode seeded")
        paths = list(collect_backend_test_files())
        random.Random(seed).shuffle(paths)
        return _validate_order(paths)
    raise ValueError(f"unknown test-order mode: {mode}")


def run_mode(mode: str, seed: int | None) -> tuple[int, tuple[str, ...], float]:
    paths = _ordered_paths(mode, seed)
    started = time.monotonic()
    if mode == "isolated":
        exit_code = 0
        for path in paths:
            exit_code = max(exit_code, _run_pytest((path,)))
    else:
        exit_code = _run_pytest(paths)
    return exit_code, paths, time.monotonic() - started


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=tuple(MODES))
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    try:
        exit_code, paths, duration = run_mode(args.mode, args.seed)
        print(
            f"mode={args.mode} seed={args.seed if args.seed is not None else '-'} "
            f"file_count={len(paths)} order_digest={order_digest(paths)} "
            f"pytest_exit_code={exit_code} duration_ms={int(duration * 1000)}"
        )
        return exit_code
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
