from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BUILDER = BACKEND_ROOT / "scripts" / "build_assistant_system_seed.py"
EMBEDDED_SEED = (
    BACKEND_ROOT / "app" / "assistant" / "runtime" / "system_seed"
)


def _copy_seed_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def snapshot_tree_bytes(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(root).as_posix()] = path.read_bytes()
    return out


def mutate_json_field(path: Path, field: str, value: object) -> None:
    payload = json.loads(path.read_text("utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_builder(*args: str, seed_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    cmd = [sys.executable, str(BUILDER), *args]
    if seed_root is not None:
        cmd.extend(["--seed-root", str(seed_root)])
    return subprocess.run(
        cmd,
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_builder_checked(*args: str, seed_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = run_builder(*args, seed_root=seed_root)
    assert result.returncode == 0, (
        f"builder failed: rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


@pytest.fixture
def tmp_seed_tree(tmp_path: Path) -> Path:
    dest = tmp_path / "system_seed"
    _copy_seed_tree(EMBEDDED_SEED, dest)
    # Ensure generated outputs exist so --check has something to compare.
    run_builder_checked("--write", seed_root=dest)
    return dest


def test_check_detects_profile_drift(tmp_seed_tree: Path) -> None:
    mutate_json_field(
        tmp_seed_tree / "main-agent-profile.v2.json",
        "basePrompt",
        "changed",
    )
    result = run_builder("--check", seed_root=tmp_seed_tree)
    assert result.returncode == 1
    assert "seed output drift" in result.stderr


def test_write_is_byte_idempotent(tmp_seed_tree: Path) -> None:
    run_builder_checked("--write", seed_root=tmp_seed_tree)
    first = snapshot_tree_bytes(tmp_seed_tree)
    run_builder_checked("--write", seed_root=tmp_seed_tree)
    assert snapshot_tree_bytes(tmp_seed_tree) == first


def test_check_passes_on_fresh_write(tmp_seed_tree: Path) -> None:
    result = run_builder_checked("--check", seed_root=tmp_seed_tree)
    assert "assistant system seed: OK" in result.stdout


def test_builder_rejects_missing_mode() -> None:
    result = run_builder()
    assert result.returncode != 0
