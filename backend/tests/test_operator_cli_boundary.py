"""Task 8 — no asserted operator identity in production modules or CLI mutations."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _BACKEND_ROOT / "app"


def production_python_sources() -> list[Path]:
    """Return production Python modules under backend/app (exclude tests)."""
    return sorted(p for p in _APP_ROOT.rglob("*.py") if p.is_file())


def find_literals(sources: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                rel = path.relative_to(_BACKEND_ROOT)
                hits.append(f"{rel}: {token}")
    return hits


def run_reconciliation_cli(argv: list[str], *, monkeypatch: pytest.MonkeyPatch):
    """Invoke capability reconciliation CLI; capture exit code + stderr text."""
    from app.assistant.capability_calls import cli as cli_mod

    class _Result:
        def __init__(self) -> None:
            self.exit_code = 0
            self.stderr = ""
            self.stdout = ""

    result = _Result()
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    old_err, old_out = sys.stderr, sys.stdout
    try:
        sys.stderr = err_buf
        sys.stdout = out_buf
        try:
            result.exit_code = int(cli_mod.main(argv))
        except SystemExit as exc:
            result.exit_code = int(exc.code or 0)
    finally:
        sys.stderr = old_err
        sys.stdout = old_out
    # CLI may emit JSON to stdout; surface both streams for assertion flexibility.
    result.stderr = err_buf.getvalue() + out_buf.getvalue()
    result.stdout = out_buf.getvalue()
    return result


def test_no_production_module_reads_operator_identity_headers() -> None:
    sources = production_python_sources()
    forbidden = (
        "X-MindAtlas-Operator-Id",
        "X-MindAtlas-Operator-Role",
        "get_trusted_operator_principal",
    )
    hits = find_literals(sources, forbidden)
    assert not hits, "forbidden asserted-identity literals in production:\n" + "\n".join(
        hits
    )


def test_reconciliation_decide_cannot_use_env_operator_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ASSISTANT_CAPABILITY_RECONCILIATION_OPERATOR_ID", str(uuid4())
    )
    monkeypatch.setenv("ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv(
        "ASSISTANT_CAPABILITY_RECONCILIATION_EVIDENCE_SECRET", "e" * 32
    )
    result = run_reconciliation_cli(
        ["decide", "--call-id", str(uuid4()), "--expected-call-revision", "1",
         "--expected-run-revision", "1", "--decision", "mark_failed",
         "--reason", "should-not-authorize"],
        monkeypatch=monkeypatch,
    )
    assert result.exit_code == 2
    assert "authenticated HTTP Operator session is required" in result.stderr


def test_compatibility_principal_is_canonical_dataclass() -> None:
    from app.assistant.skills.principal import OperatorPrincipal as Compat
    from app.operator_auth.contracts import OperatorPrincipal

    assert Compat is OperatorPrincipal
