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
        MINDATLAS_SESSION_HMAC_KEYS=_encoded_keys("active", "previous"),
    )
    ring = SessionMacKeyRing.parse(
        active_key_id="active",
        encoded_json=_encoded_keys("active", "previous"),
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
    assert summary["knownKeyIds"] == ["active", "previous"]
    assert summary["retiredKeyIds"] == ["previous"]
    assert summary["requestId"].startswith("maint-revoke-")
    # No secret-bearing keys in the allowlisted summary.
    assert set(summary.keys()) == {
        "revokedCount",
        "knownKeyIds",
        "retiredKeyIds",
        "activeKeyId",
        "requestId",
    }
    service.revoke_unverifiable_sessions.assert_called_once()
    kwargs = service.revoke_unverifiable_sessions.call_args.kwargs
    ctx = kwargs["context"]
    assert ctx.request_id == summary["requestId"]
    assert kwargs["retire_key_ids"] == frozenset({"previous"})
    # Digests are present and hex-shaped; never raw IP/UA.
    assert len(ctx.request_digest) == 64
    assert len(ctx.user_agent_digest) == 64
    assert len(ctx.network_digest) == 64


def test_run_revoke_documented_dual_key_sequence_with_real_service(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-mock integration: dual ring present → CLI retires previous-key session."""
    import base64
    import json
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.config import Settings
    from app.database import Base
    from app.operator_auth.models import OperatorSession
    from app.operator_auth.repository import OperatorRepository
    from app.operator_auth.service import OperatorAuthService
    from app.operator_auth.tokens import SessionMacKeyRing
    from app.operator_auth.contracts import RequestSecurityContext
    from scripts.revoke_unverifiable_operator_sessions import run_revoke

    def _hex(n: int) -> str:
        return bytes([n & 0xFF]).hex() * 32

    password = "correct horse battery"
    old_bytes = bytes([21]) * 32
    new_bytes = bytes([11]) * 32
    encoded_dual = json.dumps(
        {
            "new": base64.b64encode(new_bytes).decode("ascii"),
            "old": base64.b64encode(old_bytes).decode("ascii"),
        }
    )
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN="http://localhost:5173",
        CORS_ORIGINS="http://localhost:5173",
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID="new",
        MINDATLAS_SESSION_HMAC_KEYS=encoded_dual,
    )

    tmp = tempfile.NamedTemporaryFile(
        prefix="mindatlas-maint-cli-", suffix=".sqlite", delete=False
    )
    path = Path(tmp.name)
    tmp.close()
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    import app.operator_auth.models  # noqa: F401
    import app.system_settings.models  # noqa: F401

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
    )

    # Issue under old-only ring, then run CLI with dual ring still loaded.
    db = factory()
    try:
        OperatorRepository(db).seed_account(password=password)
        db.commit()
        old_ring = SessionMacKeyRing(
            active_key_id="old", keys={"old": old_bytes}
        )
        issued = OperatorAuthService(db, key_ring=old_ring).login(
            password,
            RequestSecurityContext(
                request_id="req-1",
                request_digest=_hex(1),
                user_agent_digest=_hex(2),
                network_digest=_hex(3),
            ),
        )
        session_id = issued.principal.session_id
        cookie = issued.session_cookie_value
    finally:
        db.close()

    dual_ring = SessionMacKeyRing.parse(
        active_key_id="new", encoded_json=encoded_dual
    )
    monkeypatch.setattr(
        "app.operator_auth.dependencies.load_session_mac_key_ring",
        lambda _settings: dual_ring,
    )

    def _build(db_arg, _settings):
        return OperatorAuthService(db_arg, key_ring=dual_ring)

    monkeypatch.setattr(
        "app.operator_auth.dependencies.build_operator_auth_service",
        _build,
    )

    db2 = factory()
    try:
        summary = run_revoke(settings=settings, db=db2)
        assert summary["revokedCount"] == 1
        assert summary["retiredKeyIds"] == ["old"]
        assert summary["activeKeyId"] == "new"
        row = db2.get(OperatorSession, session_id)
        assert row is not None
        assert row.revoked_at is not None
        service = OperatorAuthService(db2, key_ring=dual_ring)
        assert service.resolve_session(
            cookie,
            RequestSecurityContext(
                request_id="req-2",
                request_digest=_hex(4),
                user_agent_digest=_hex(5),
                network_digest=_hex(6),
            ),
        ) is None
    finally:
        db2.close()
        engine.dispose()
        path.unlink(missing_ok=True)

    # Documented sequence is NOT a no-op when old remains in the ring.
    assert summary["revokedCount"] > 0


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
            "knownKeyIds": ["k1", "k0"],
            "retiredKeyIds": ["k0"],
            "activeKeyId": "k1",
            "requestId": "maint-revoke-deadbeef",
        },
    )
    code = cli.main(["--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload == {
        "activeKeyId": "k1",
        "knownKeyIds": ["k1", "k0"],
        "requestId": "maint-revoke-deadbeef",
        "retiredKeyIds": ["k0"],
        "revokedCount": 1,
    }
