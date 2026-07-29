"""Maintenance CLI for previous-key session revocation (Plan 1 global constraint)."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _encoded_keys(*ids: str) -> str:
    payload = {
        key_id: base64.b64encode(bytes([i + 1]) * 32).decode("ascii")
        for i, key_id in enumerate(ids)
    }
    return json.dumps(payload)


def test_run_revoke_invokes_service_and_returns_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from app.operator_auth.tokens import SessionMacKeyRing
    from scripts.revoke_unverifiable_operator_sessions import run_revoke

    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN="http://localhost:5173",
        CORS_ORIGINS="http://localhost:5173",
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID="active",
        MINDATLAS_SESSION_HMAC_KEYS=_encoded_keys("active"),
    )
    ring = SessionMacKeyRing.parse(
        active_key_id="active",
        encoded_json=_encoded_keys("active"),
    )
    service = MagicMock()
    service.revoke_unverifiable_sessions.return_value = 3
    db = MagicMock()

    monkeypatch.setattr(
        "app.operator_auth.dependencies.load_session_mac_key_ring",
        lambda _settings: ring,
    )
    monkeypatch.setattr(
        "app.operator_auth.dependencies.build_operator_auth_service",
        lambda _db, _settings: service,
    )

    summary = run_revoke(settings=settings, db=db)

    assert summary["revokedCount"] == 3
    assert summary["activeKeyId"] == "active"
    assert summary["knownKeyIds"] == ["active"]
    assert summary["requestId"].startswith("maint-revoke-")
    # No secret-bearing keys in the allowlisted summary.
    assert set(summary.keys()) == {
        "revokedCount",
        "knownKeyIds",
        "activeKeyId",
        "requestId",
    }
    service.revoke_unverifiable_sessions.assert_called_once()
    ctx = service.revoke_unverifiable_sessions.call_args.kwargs["context"]
    assert ctx.request_id == summary["requestId"]
    # Digests are present and hex-shaped; never raw IP/UA.
    assert len(ctx.request_digest) == 64
    assert len(ctx.user_agent_digest) == 64
    assert len(ctx.network_digest) == 64


def test_run_revoke_exits_when_key_ring_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from scripts.revoke_unverifiable_operator_sessions import run_revoke

    settings = Settings(APP_ENV="development")
    monkeypatch.setattr(
        "app.operator_auth.dependencies.load_session_mac_key_ring",
        lambda _settings: None,
    )
    with pytest.raises(RuntimeError, match="operator_auth_unavailable"):
        run_revoke(settings=settings, db=MagicMock())


def test_main_json_emits_allowlist_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from scripts import revoke_unverifiable_operator_sessions as cli

    monkeypatch.setattr(
        cli,
        "run_revoke",
        lambda: {
            "revokedCount": 1,
            "knownKeyIds": ["k1"],
            "activeKeyId": "k1",
            "requestId": "maint-revoke-deadbeef",
        },
    )
    code = cli.main(["--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {
        "activeKeyId": "k1",
        "knownKeyIds": ["k1"],
        "requestId": "maint-revoke-deadbeef",
        "revokedCount": 1,
    }
