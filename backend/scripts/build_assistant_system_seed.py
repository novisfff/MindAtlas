#!/usr/bin/env python3
"""Generate or check the digest-locked Assistant system seed.

Modes:
  --write  Rebuild manifest.v1.json and expected.py from source artifacts.
  --check  Rebuild in memory and byte-compare against committed outputs.

Optional ``--seed-root`` is only for offline tests of the generator; the runtime
loader never accepts an override and always uses the embedded package.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.assistant.runtime.seed import (  # noqa: E402
    SystemSeedInvalid,
    build_seed_payload,
)


DEFAULT_SEED_ROOT = (
    BACKEND_ROOT / "app" / "assistant" / "runtime" / "system_seed"
)


def render_expected_module(manifest_digest: str, seed_contract_digest: str) -> str:
    return (
        '"""Generated build-owned Assistant seed identity."""\n\n'
        f"SEED_MANIFEST_DIGEST = {manifest_digest!r}\n"
        f"SEED_CONTRACT_DIGEST = {seed_contract_digest!r}\n"
    )


def render_manifest_json(payload: dict[str, object]) -> str:
    # Stable, human-reviewable encoding. Digests themselves are computed from the
    # canonical JSON form (sort_keys / no whitespace), not this pretty encoding.
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_outputs(seed_root: Path) -> tuple[str, str, str, str]:
    payload, manifest_digest, seed_contract_digest = build_seed_payload(seed_root=seed_root)
    manifest_text = render_manifest_json(payload)
    expected_text = render_expected_module(manifest_digest, seed_contract_digest)
    return manifest_text, expected_text, manifest_digest, seed_contract_digest


def cmd_write(seed_root: Path) -> int:
    manifest_text, expected_text, manifest_digest, seed_contract_digest = build_outputs(
        seed_root
    )
    if len(manifest_digest) != 64 or set(manifest_digest) <= {"0"}:
        print("refusing to write placeholder/zero manifest digest", file=sys.stderr)
        return 2
    if len(seed_contract_digest) != 64 or set(seed_contract_digest) <= {"0"}:
        print("refusing to write placeholder/zero seed contract digest", file=sys.stderr)
        return 2
    atomic_write(seed_root / "manifest.v1.json", manifest_text)
    atomic_write(seed_root / "expected.py", expected_text)
    print(f"wrote {seed_root / 'manifest.v1.json'}")
    print(f"wrote {seed_root / 'expected.py'}")
    print(f"SEED_MANIFEST_DIGEST={manifest_digest}")
    print(f"SEED_CONTRACT_DIGEST={seed_contract_digest}")
    return 0


def cmd_check(seed_root: Path) -> int:
    manifest_path = seed_root / "manifest.v1.json"
    expected_path = seed_root / "expected.py"
    if not manifest_path.is_file() or not expected_path.is_file():
        print("seed output drift: committed outputs missing", file=sys.stderr)
        return 1
    try:
        manifest_text, expected_text, _, _ = build_outputs(seed_root)
    except SystemSeedInvalid as exc:
        print(f"seed output drift: rebuild failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"seed output drift: rebuild failed: {exc}", file=sys.stderr)
        return 1

    committed_manifest = manifest_path.read_text("utf-8")
    committed_expected = expected_path.read_text("utf-8")
    drifted: list[str] = []
    if committed_manifest != manifest_text:
        drifted.append("manifest.v1.json")
    if committed_expected != expected_text:
        drifted.append("expected.py")
    if drifted:
        print(
            f"seed output drift: {', '.join(drifted)} do not match rebuild",
            file=sys.stderr,
        )
        return 1
    print("assistant system seed: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate committed outputs")
    mode.add_argument("--check", action="store_true", help="verify committed outputs")
    parser.add_argument(
        "--seed-root",
        type=Path,
        default=None,
        help="offline builder root (tests only; runtime loader never accepts this)",
    )
    args = parser.parse_args(argv)
    seed_root = (args.seed_root or DEFAULT_SEED_ROOT).resolve()
    if not seed_root.is_dir():
        print(f"seed root missing: {seed_root}", file=sys.stderr)
        return 2
    try:
        if args.write:
            return cmd_write(seed_root)
        return cmd_check(seed_root)
    except SystemSeedInvalid as exc:
        print(f"system seed invalid: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
