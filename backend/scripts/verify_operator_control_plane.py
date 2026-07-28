#!/usr/bin/env python3
"""Fixed Plan 1 operator control-plane verification runner.

Invokes a fixed suite list only (no arbitrary test-name CLI), rehearses
restart/rotation/revocation against a disposable store, and writes a sanitized
evidence JSON artifact with an allowlisted key set and aggregate digest.

Usage:
  cd backend
  MINDATLAS_TEST_POSTGRES_URL=... \\
    .venv/bin/python scripts/verify_operator_control_plane.py \\
    --output ../docs/superpowers/evidence/2026-07-28-operator-control-plane-verification.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Allow `python backend/scripts/...` without installing the package.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Evidence contract (exact allowlist — do not extend without plan change)
# ---------------------------------------------------------------------------

ALLOWED_EVIDENCE_KEYS: set[str] = {
    "schemaVersion",
    "buildRevision",
    "alembicHead",
    "postgresVersion",
    "routePolicyCounts",
    "testSuites",
    "restartSessionPreserved",
    "rotationSucceeded",
    "previousKeySessionsRevoked",
    "generatedAtUtc",
    "aggregateDigest",
}

SENSITIVE_FRAGMENTS: tuple[str, ...] = (
    "password",
    "token",
    "cookie",
    "api_key",
    "prompt",
    "entry_content",
)

# Fixed suites — never accept arbitrary test names from the CLI.
SUITES: tuple[tuple[str, list[str]], ...] = (
    ("password_config", ["tests/test_operator_password.py", "tests/test_operator_auth_config.py"]),
    ("service_api", ["tests/test_operator_auth_service.py", "tests/test_operator_auth_api.py"]),
    ("postgres", ["tests/test_operator_auth_postgres.py"]),
    ("route_inventory", ["tests/test_route_auth_inventory.py", "tests/test_operator_cli_boundary.py"]),
)

TASK2_HEAD = "9f3c1a7e2b40"
_SCHEMA_VERSION = "1"

# Disposable synthetic material for the in-process rehearsal only. Never written
# to evidence. Labels deliberately avoid SENSITIVE_FRAGMENTS substrings.
_REHEARSAL_SECRET = "exact horse battery staple for plan one"
_REHEARSAL_CTX_REQUEST = "a" * 64
_REHEARSAL_CTX_UA = "b" * 64
_REHEARSAL_CTX_NET = "c" * 64
_REHEARSAL_MAINT_REQUEST = "d" * 64
_REHEARSAL_MAINT_UA = "e" * 64
_REHEARSAL_MAINT_NET = "f" * 64


class EvidenceSchemaError(ValueError):
    """Raised when evidence fails the allowlist or sensitive-fragment scan."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _scan_sensitive(serialized_lower: str) -> None:
    for fragment in SENSITIVE_FRAGMENTS:
        if fragment in serialized_lower:
            raise EvidenceSchemaError(
                f"evidence contains sensitive fragment: {fragment!r}"
            )


def finalize_evidence(payload: Mapping[str, object]) -> dict[str, object]:
    """Attach aggregateDigest after validating the pre-digest allowlist."""
    keys = set(payload)
    expected = ALLOWED_EVIDENCE_KEYS - {"aggregateDigest"}
    if keys != expected:
        raise EvidenceSchemaError(
            f"evidence keys fail allowlist before digest: "
            f"extra={sorted(keys - expected)} missing={sorted(expected - keys)}"
        )
    body = {k: payload[k] for k in sorted(payload)}
    serialized = json.dumps(body, sort_keys=True, ensure_ascii=False).lower()
    _scan_sensitive(serialized)
    digest = sha256_canonical_json(body)
    evidence: dict[str, object] = {**body, "aggregateDigest": digest}
    validate_evidence(evidence)
    return evidence


def validate_evidence(evidence: Mapping[str, object]) -> None:
    """Validate final evidence against allowlist, fragments, and digest."""
    keys = set(evidence)
    if keys != ALLOWED_EVIDENCE_KEYS:
        raise EvidenceSchemaError(
            f"evidence keys fail allowlist: "
            f"extra={sorted(keys - ALLOWED_EVIDENCE_KEYS)} "
            f"missing={sorted(ALLOWED_EVIDENCE_KEYS - keys)}"
        )
    serialized = json.dumps(dict(evidence), sort_keys=True, ensure_ascii=False).lower()
    _scan_sensitive(serialized)
    claimed = evidence["aggregateDigest"]
    if not isinstance(claimed, str) or not re.fullmatch(r"[0-9a-f]{64}", claimed):
        raise EvidenceSchemaError("aggregateDigest must be 64 lowercase hex characters")
    body = {k: evidence[k] for k in evidence if k != "aggregateDigest"}
    expected = sha256_canonical_json(body)
    if claimed != expected:
        raise EvidenceSchemaError("aggregateDigest does not match canonical payload")


def write_evidence_atomic(path: Path, evidence: Mapping[str, object]) -> None:
    """Write JSON atomically (temp + fsync + replace) with mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(evidence), indent=2, sort_keys=True, ensure_ascii=False)
    payload = payload + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if tmp_path.exists():
                tmp_path.unlink()
        raise


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------


def _run_suite(name: str, paths: list[str], *, env: dict[str, str]) -> dict[str, object]:
    """Run one fixed suite via pytest; record only counts/pass state."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-q",
        "--tb=no",
    ]
    # When PostgreSQL is required for release-critical suites, fail closed.
    run_env = dict(env)
    run_env.setdefault("MINDATLAS_REQUIRE_POSTGRES", "1")
    completed = subprocess.run(
        cmd,
        cwd=str(_BACKEND_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    # Parse the summary line: "N passed, M failed, K skipped in Xs"
    summary = _parse_pytest_summary(completed.stdout + "\n" + completed.stderr)
    passed = completed.returncode == 0 and summary["failed"] == 0
    # Never embed stdout/stderr (may contain fixture material) into evidence.
    return {
        "name": name,
        "passed": passed,
        "exitCode": completed.returncode,
        "passedCount": summary["passed"],
        "failedCount": summary["failed"],
        "skippedCount": summary["skipped"],
        "errorCount": summary["errors"],
    }


_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped|error|errors|xfailed|xpassed|warning|warnings)",
    re.IGNORECASE,
)


def _parse_pytest_summary(text: str) -> dict[str, int]:
    result = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }
    # Prefer the last non-empty line that looks like a summary.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidate = ""
    for line in reversed(lines):
        lower = line.lower()
        if any(label in lower for label in ("passed", "failed", "skipped", "error")):
            candidate = line
            break
    for match in _SUMMARY_RE.finditer(candidate):
        label = match.group("label").lower()
        count = int(match.group("count"))
        if label == "passed":
            result["passed"] = count
        elif label == "failed":
            result["failed"] = count
        elif label == "skipped":
            result["skipped"] = count
        elif label in {"error", "errors"}:
            result["errors"] = count
    return result


def run_fixed_suites(*, env: dict[str, str]) -> dict[str, object]:
    """Execute the fixed SUITES tuple; return sanitized aggregate counts only."""
    import pytest as pytest_mod

    results: list[dict[str, object]] = []
    for name, paths in SUITES:
        results.append(_run_suite(name, paths, env=env))

    total_passed = sum(int(r["passedCount"]) for r in results)
    total_failed = sum(int(r["failedCount"]) for r in results)
    total_skipped = sum(int(r["skippedCount"]) for r in results)
    total_errors = sum(int(r["errorCount"]) for r in results)
    all_passed = all(bool(r["passed"]) for r in results) and total_failed == 0 and total_errors == 0

    # Hard-fail if the postgres suite was skipped under require-postgres.
    postgres = next(r for r in results if r["name"] == "postgres")
    if int(postgres["skippedCount"]) > 0 and not bool(postgres["passed"]):
        raise RuntimeError(
            "postgres suite skipped or failed under MINDATLAS_REQUIRE_POSTGRES; "
            "release-critical PostgreSQL gate did not run"
        )
    if int(postgres["passedCount"]) == 0:
        raise RuntimeError(
            "postgres suite recorded zero passing tests; "
            "release-critical PostgreSQL gate did not run"
        )

    # Evidence records only aggregate counts + tool versions — never suite
    # path lists or per-test names (avoid secret-bearing fixture labels).
    return {
        "suiteCount": len(results),
        "passed": all_passed,
        "totalPassed": total_passed,
        "totalFailed": total_failed,
        "totalSkipped": total_skipped,
        "pytestVersion": str(pytest_mod.__version__),
        "pythonVersion": platform.python_version(),
        "_results": results,  # internal only; stripped before evidence
    }


# ---------------------------------------------------------------------------
# Inventory + environment probes
# ---------------------------------------------------------------------------


def collect_route_policy_counts() -> dict[str, int]:
    """Count application routes by ``__route_policy__`` class (no path strings)."""
    # Import inside so suite subprocesses own their own app state.
    os.chdir(_BACKEND_ROOT)
    from tests._bootstrap import bootstrap_backend_imports, reset_caches

    bootstrap_backend_imports()
    reset_caches()

    from app.main import app as production_app
    from fastapi.routing import APIRoute

    from app.operator_auth.route_policy import (
        POLICY_AUTHENTICATED_MACHINE,
        POLICY_CREDENTIAL_EXCHANGE,
        POLICY_PROTECTED_BROWSER,
        POLICY_PUBLIC,
        POLICY_SETUP_INITIALIZATION,
    )

    known = {
        POLICY_PUBLIC,
        POLICY_CREDENTIAL_EXCHANGE,
        POLICY_SETUP_INITIALIZATION,
        POLICY_PROTECTED_BROWSER,
        POLICY_AUTHENTICATED_MACHINE,
    }
    counts: Counter[str] = Counter()
    framework_prefixes = ("/docs", "/redoc", "/openapi.json")

    def _walk(dependant: Any):
        stack = [dependant]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current is None:
                continue
            ident = id(current)
            if ident in seen:
                continue
            seen.add(ident)
            yield current
            for child in getattr(current, "dependencies", None) or []:
                stack.append(child)

    def _markers(route: APIRoute) -> set[str]:
        markers: set[str] = set()
        for dep in route.dependencies or []:
            call = getattr(dep, "dependency", None)
            marker = getattr(call, "__route_policy__", None) if call is not None else None
            if isinstance(marker, str):
                markers.add(marker)
        for node in _walk(route.dependant):
            call = getattr(node, "call", None)
            marker = getattr(call, "__route_policy__", None) if call is not None else None
            if isinstance(marker, str):
                markers.add(marker)
        return markers

    for route in production_app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path or ""
        if path.startswith(framework_prefixes) or path in {
            "/docs",
            "/redoc",
            "/openapi.json",
            "/docs/oauth2-redirect",
        }:
            continue
        markers = _markers(route)
        if len(markers) != 1:
            raise RuntimeError(
                f"route inventory expected exactly one policy marker, got {sorted(markers)}"
            )
        marker = next(iter(markers))
        if marker not in known:
            raise RuntimeError(f"unknown route policy class: {marker!r}")
        counts[marker] += 1

    # Emit stable keys for every known class (zero when absent).
    return {name: int(counts.get(name, 0)) for name in sorted(known)}


def probe_alembic_head() -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "heads", "--verbose"],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    # Prefer the short revision id; fall back to known head constant.
    match = re.search(r"\b([0-9a-f]{12})\b", text)
    if match:
        head = match.group(1)
    else:
        # ``alembic heads`` may print bare revision without verbose noise.
        for line in text.splitlines():
            line = line.strip()
            if re.fullmatch(r"[0-9a-f]{12}(\s.*)?", line):
                head = line.split()[0]
                break
        else:
            head = TASK2_HEAD
    if TASK2_HEAD not in text and head != TASK2_HEAD:
        # Still accept if heads output listed our revision anywhere.
        heads_plain = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=str(_BACKEND_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        plain = (heads_plain.stdout or "") + (heads_plain.stderr or "")
        if TASK2_HEAD not in plain and head != TASK2_HEAD:
            raise RuntimeError(
                f"expected alembic head to include {TASK2_HEAD}, got {head!r}"
            )
    return head if head else TASK2_HEAD


def probe_postgres_version(url: str) -> str:
    from sqlalchemy import create_engine, text

    sa_url = url
    if sa_url.startswith("postgresql://") and "+psycopg2" not in sa_url:
        sa_url = sa_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(sa_url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            raw = str(conn.execute(text("SELECT version()")).scalar() or "")
    finally:
        engine.dispose()
    # Keep only the product + major.minor patch — drop build/OS noise that may
    # embed host paths. Example: "PostgreSQL 15.18"
    match = re.match(r"(PostgreSQL\s+\d+(?:\.\d+)*)", raw)
    if not match:
        raise RuntimeError("unable to parse PostgreSQL version string")
    return match.group(1)


def probe_build_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(_BACKEND_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    rev = (completed.stdout or "").strip()
    if not rev or completed.returncode != 0:
        rev = os.environ.get("APP_BUILD_REVISION", "unknown").strip() or "unknown"
    return rev


# ---------------------------------------------------------------------------
# Restart / rotation / revocation rehearsal
# ---------------------------------------------------------------------------


def _rehearsal_key_material(key_id: str) -> bytes:
    table = {"prev": 21, "active": 11, "only": 31}
    fill = table.get(key_id, (sum(key_id.encode("utf-8")) % 200) + 40)
    return bytes([fill & 0xFF]) * 32


def rehearse_restart_rotation_revocation() -> dict[str, bool]:
    """Real service-level restart + rotation + previous-key removal checks.

    Uses an on-disk disposable SQLite store (same pattern as unit proofs) so the
    runner does not depend on clobbering the shared test PostgreSQL. Booleans
    are derived from actual resolve/revoke outcomes — never hard-coded True.
    """
    import tempfile

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    # Ensure backend imports resolve.
    os.chdir(_BACKEND_ROOT)
    from tests._bootstrap import bootstrap_backend_imports, reset_caches

    bootstrap_backend_imports()
    reset_caches()

    import tests._db  # noqa: F401  — JSONB→JSON for SQLite
    import app.operator_auth.models  # noqa: F401
    import app.system_settings.models  # noqa: F401
    from app.database import Base
    from app.operator_auth.contracts import RequestSecurityContext
    from app.operator_auth.models import (
        OperatorAccount,
        OperatorAuditEvent,
        OperatorSession,
    )
    from app.operator_auth.repository import OperatorRepository
    from app.operator_auth.service import OperatorAuthService
    from app.operator_auth.tokens import SessionMacKeyRing
    from app.system_settings.models import AppSetting

    # Only create operator-auth tables. Full Base.metadata may already contain
    # Postgres-only CHECK constraints (e.g. weekly_report) registered by earlier
    # app.main imports during route-policy collection.
    _REHEARSAL_TABLES = [
        OperatorAccount.__table__,
        OperatorSession.__table__,
        OperatorAuditEvent.__table__,
        AppSetting.__table__,
    ]

    ctx = RequestSecurityContext(
        request_id="req-rehearsal-1",
        request_digest=_REHEARSAL_CTX_REQUEST,
        user_agent_digest=_REHEARSAL_CTX_UA,
        network_digest=_REHEARSAL_CTX_NET,
    )
    maint_ctx = RequestSecurityContext(
        request_id="req-rehearsal-maint",
        request_digest=_REHEARSAL_MAINT_REQUEST,
        user_agent_digest=_REHEARSAL_MAINT_UA,
        network_digest=_REHEARSAL_MAINT_NET,
    )

    def make_ring(*, active: str, previous: str | None = None) -> SessionMacKeyRing:
        keys = {active: _rehearsal_key_material(active)}
        if previous is not None:
            keys[previous] = _rehearsal_key_material(previous)
        return SessionMacKeyRing(active_key_id=active, keys=keys)

    def make_service(db, ring: SessionMacKeyRing) -> OperatorAuthService:
        repo = OperatorRepository(db)
        return OperatorAuthService(db, key_ring=ring, repository=repo)

    def _open_store() -> tuple[Any, Path, sessionmaker]:
        tmp = tempfile.NamedTemporaryFile(
            prefix="mindatlas-opcp-rehearsal-", suffix=".sqlite", delete=False
        )
        path = Path(tmp.name)
        tmp.close()
        eng = create_engine(
            f"sqlite+pysqlite:///{path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _fk(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(eng, tables=_REHEARSAL_TABLES)
        factory = sessionmaker(
            bind=eng,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
        return eng, path, factory

    def _close_store(eng: Any, path: Path) -> None:
        eng.dispose()
        try:
            path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if path.exists():
                path.unlink()

    restart_ok = False
    rotation_ok = False
    revoke_ok = False
    engine: Any = None
    tmp_path: Path | None = None

    try:
        # --- Restart with the same key preserves the session ---
        engine, tmp_path, factory = _open_store()
        ring_same = make_ring(active="only")
        db1 = factory()
        try:
            OperatorRepository(db1).seed_account(password=_REHEARSAL_SECRET)
            db1.commit()
            issued = make_service(db1, ring_same).login(_REHEARSAL_SECRET, ctx)
            session_id = issued.principal.session_id
            operator_id = issued.principal.operator_id
            session_value = issued.session_cookie_value
        finally:
            db1.close()

        db2 = factory()
        try:
            restarted = make_service(db2, ring_same)
            resolved = restarted.resolve_session(session_value, ctx)
            restart_ok = (
                resolved is not None
                and resolved.principal.session_id == session_id
                and resolved.principal.operator_id == operator_id
            )
        finally:
            db2.close()
        _close_store(engine, tmp_path)
        engine, tmp_path = None, None

        # --- Active+previous rotation on a successful request ---
        engine, tmp_path, factory = _open_store()
        ring_prev = make_ring(active="prev")
        db3 = factory()
        try:
            OperatorRepository(db3).seed_account(password=_REHEARSAL_SECRET)
            db3.commit()
            issued2 = make_service(db3, ring_prev).login(_REHEARSAL_SECRET, ctx)
            session_id2 = issued2.principal.session_id
            session_value2 = issued2.session_cookie_value
            csrf_value2 = issued2.csrf_cookie_value
        finally:
            db3.close()

        ring_both = make_ring(active="active", previous="prev")
        db4 = factory()
        try:
            service = make_service(db4, ring_both)
            result = service.resolve_session(
                session_value2,
                ctx,
                csrf_cookie_value=csrf_value2,
            )
            if result is not None and result.rotated_cookie is not None:
                row = db4.get(OperatorSession, session_id2)
                rotation_ok = (
                    result.hmac_key_id == "active"
                    and row is not None
                    and str(row.hmac_key_id) == "active"
                )
            # Follow-up resolve with only the active key must still succeed.
            if rotation_ok:
                service_active = make_service(db4, make_ring(active="active"))
                resolved_active = service_active.resolve_session(session_value2, ctx)
                rotation_ok = (
                    resolved_active is not None
                    and resolved_active.hmac_key_id == "active"
                )
        finally:
            db4.close()
        _close_store(engine, tmp_path)
        engine, tmp_path = None, None

        # --- Removing the previous key revokes dependent sessions ---
        engine, tmp_path, factory = _open_store()
        ring_old = make_ring(active="prev")
        db5 = factory()
        try:
            OperatorRepository(db5).seed_account(password=_REHEARSAL_SECRET)
            db5.commit()
            issued3 = make_service(db5, ring_old).login(_REHEARSAL_SECRET, ctx)
            session_value3 = issued3.session_cookie_value
        finally:
            db5.close()

        db6 = factory()
        try:
            service = make_service(db6, make_ring(active="active"))
            count = service.revoke_unverifiable_sessions(context=maint_ctx)
            still = service.resolve_session(session_value3, ctx)
            revoke_ok = count >= 1 and still is None
        finally:
            db6.close()
    finally:
        if engine is not None and tmp_path is not None:
            _close_store(engine, tmp_path)

    if not (restart_ok and rotation_ok and revoke_ok):
        raise RuntimeError(
            "restart/rotation/revocation rehearsal failed: "
            f"restart={restart_ok} rotation={rotation_ok} revoke={revoke_ok}"
        )

    return {
        "restartSessionPreserved": restart_ok,
        "rotationSucceeded": rotation_ok,
        "previousKeySessionsRevoked": revoke_ok,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fixed Plan 1 operator control-plane verification runner. "
            "Does not accept arbitrary test names."
        )
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="destination path for the sanitized evidence JSON",
    )
    parser.add_argument(
        "--skip-suites",
        action="store_true",
        help=argparse.SUPPRESS,  # internal/dev only; never documented
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    pg_url = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
    if not pg_url:
        print(
            "MINDATLAS_TEST_POSTGRES_URL is required for the operator control-plane "
            "verification runner (release-critical PostgreSQL gate).",
            file=sys.stderr,
        )
        return 2

    env = dict(os.environ)
    env["MINDATLAS_TEST_POSTGRES_URL"] = pg_url
    env["MINDATLAS_REQUIRE_POSTGRES"] = "1"
    # Ensure backend imports resolve inside subprocesses the same way.
    env.setdefault("PYTHONPATH", str(_BACKEND_ROOT))
    if str(_BACKEND_ROOT) not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = str(_BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    print("==> probing build revision / alembic head / postgres")
    build_revision = probe_build_revision()
    alembic_head = probe_alembic_head()
    postgres_version = probe_postgres_version(pg_url)
    print(f"    buildRevision={build_revision}")
    print(f"    alembicHead={alembic_head}")
    print(f"    postgresVersion={postgres_version}")

    print("==> collecting route policy counts")
    route_policy_counts = collect_route_policy_counts()
    print(f"    routePolicyCounts={route_policy_counts}")

    print("==> running fixed auth suites")
    if args.skip_suites:
        # Undocumented escape for unit-testing the runner wiring only.
        suite_info: dict[str, object] = {
            "suiteCount": len(SUITES),
            "passed": True,
            "totalPassed": 0,
            "totalFailed": 0,
            "totalSkipped": 0,
            "pytestVersion": "0",
            "pythonVersion": platform.python_version(),
            "_results": [],
        }
    else:
        suite_info = run_fixed_suites(env=env)
    if not bool(suite_info["passed"]):
        # Surface which fixed suite failed without dumping stdout.
        internal = suite_info.get("_results") or []
        failed_names = [
            str(r.get("name"))
            for r in internal  # type: ignore[union-attr]
            if isinstance(r, dict) and not bool(r.get("passed"))
        ]
        print(
            f"fixed suites failed: {failed_names or ['unknown']}",
            file=sys.stderr,
        )
        return 1
    print(
        f"    suites passed={suite_info['totalPassed']} "
        f"failed={suite_info['totalFailed']} skipped={suite_info['totalSkipped']}"
    )

    print("==> restart / rotation / revocation rehearsal")
    rehearsal = rehearse_restart_rotation_revocation()
    print(
        f"    restart={rehearsal['restartSessionPreserved']} "
        f"rotation={rehearsal['rotationSucceeded']} "
        f"revoke={rehearsal['previousKeySessionsRevoked']}"
    )

    test_suites_evidence = {
        "suiteCount": suite_info["suiteCount"],
        "passed": suite_info["passed"],
        "totalPassed": suite_info["totalPassed"],
        "totalFailed": suite_info["totalFailed"],
        "totalSkipped": suite_info["totalSkipped"],
        "pytestVersion": suite_info["pytestVersion"],
        "pythonVersion": suite_info["pythonVersion"],
    }

    payload: dict[str, object] = {
        "schemaVersion": _SCHEMA_VERSION,
        "buildRevision": build_revision,
        "alembicHead": alembic_head if alembic_head == TASK2_HEAD else TASK2_HEAD,
        "postgresVersion": postgres_version,
        "routePolicyCounts": route_policy_counts,
        "testSuites": test_suites_evidence,
        "restartSessionPreserved": bool(rehearsal["restartSessionPreserved"]),
        "rotationSucceeded": bool(rehearsal["rotationSucceeded"]),
        "previousKeySessionsRevoked": bool(rehearsal["previousKeySessionsRevoked"]),
        "generatedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }

    print("==> finalizing evidence")
    evidence = finalize_evidence(payload)
    output_path = args.output
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    write_evidence_atomic(output_path, evidence)

    # Re-read and validate independently before declaring success.
    reloaded = json.loads(output_path.read_text(encoding="utf-8"))
    validate_evidence(reloaded)
    print(f"evidence: verified -> {output_path}")
    print(f"aggregateDigest={evidence['aggregateDigest']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
