#!/usr/bin/env python3
"""Maintenance CLI: durably revoke operator sessions whose hmac_key_id left the ring.

Run this **before** removing a previous session-MAC key from
``MINDATLAS_SESSION_HMAC_KEYS``. Prints only safe counts/JSON — never raw
cookies, IPs, UAs, digests of secrets, or key material.

Usage (from backend/):

  .venv/bin/python scripts/revoke_unverifiable_operator_sessions.py

Exit 0 on success. Exit 2 when the session key ring is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _maintenance_context() -> Any:
    """Build a RequestSecurityContext with synthetic digests (no raw IP/UA)."""
    from app.operator_auth.contracts import RequestSecurityContext

    def _hex(label: str) -> str:
        return hashlib.sha256(f"maintenance:{label}".encode("utf-8")).hexdigest()

    return RequestSecurityContext(
        request_id=f"maint-revoke-{uuid.uuid4().hex[:12]}",
        request_digest=_hex("request"),
        user_agent_digest=_hex("ua"),
        network_digest=_hex("network"),
    )


def run_revoke(*, settings: Any | None = None, db: Any | None = None) -> dict[str, Any]:
    """Revoke unverifiable sessions; return allowlist-only summary.

    ``settings`` / ``db`` are injectable for tests. Production path loads both
    from the process environment and SessionLocal.

    Raises ``RuntimeError`` with an ``operator_auth_unavailable`` message when
    the key ring is missing (callers map that to exit 2).
    """
    from app.config import get_settings
    from app.operator_auth.dependencies import (
        build_operator_auth_service,
        load_session_mac_key_ring,
    )

    resolved = settings if settings is not None else get_settings()
    ring = load_session_mac_key_ring(resolved)
    if ring is None:
        raise RuntimeError(
            "operator_auth_unavailable: session MAC key ring is not configured"
        )

    owns_db = db is None
    if owns_db:
        from app.database import SessionLocal

        db = SessionLocal()

    try:
        service = build_operator_auth_service(db, resolved)
        context = _maintenance_context()
        count = int(service.revoke_unverifiable_sessions(context=context))
        known_key_ids = sorted(ring.keys.keys())
        return {
            "revokedCount": count,
            "knownKeyIds": known_key_ids,
            "activeKeyId": ring.active_key_id,
            "requestId": context.request_id,
        }
    finally:
        if owns_db and db is not None:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Revoke operator sessions whose hmac_key_id is no longer in the "
            "configured session MAC key ring. Safe counts only."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object on stdout (default is human one-liners).",
    )
    args = parser.parse_args(argv)

    try:
        summary = run_revoke()
    except RuntimeError as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        return 2 if "operator_auth_unavailable" in message else 1
    except Exception as exc:  # noqa: BLE001 — surface maintenance failure cleanly
        print(f"revoke_failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        print(f"revokedCount={summary['revokedCount']}")
        print(f"activeKeyId={summary['activeKeyId']}")
        print(f"knownKeyIds={','.join(summary['knownKeyIds'])}")
        print(f"requestId={summary['requestId']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
