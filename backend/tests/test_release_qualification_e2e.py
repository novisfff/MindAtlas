from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "backend" / "scripts" / "run_pre_ga_release.py"


def test_profile_run_missing_infrastructure_is_a_hard_failure(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "profile",
            "run",
            "--run-dir",
            str(tmp_path / "missing-run"),
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "release_run_not_prepared" in result.stdout
    assert "passed=true" not in result.stdout.lower()


def test_evidence_command_does_not_accept_outcome_inputs() -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "evidence", "run", "--kind", "automated_qualification", "--passed", "true"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "passed" not in result.stdout.lower()
