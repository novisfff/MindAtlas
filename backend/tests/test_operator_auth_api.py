"""HTTP boundary tests for operator cookie sessions, CSRF, and account ops."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import register_exception_handlers  # noqa: E402
from app.common.request_context import (  # noqa: E402
    reset_request_id,
    set_request_id,
)
from app.config import Settings, get_settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.operator_auth.constants import (  # noqa: E402
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    LOGIN_FAILURE_LIMIT,
    SESSION_COOKIE_NAME,
)
from app.operator_auth.models import OperatorAuditEvent  # noqa: E402
from app.operator_auth.repository import OperatorRepository  # noqa: E402
from app.operator_auth.router import router as operator_auth_router  # noqa: E402
from app.system_settings.router import router as system_settings_router  # noqa: E402


_PASSWORD = "correct horse battery"
_ORIGIN = "http://localhost:5173"
_KEY_ID = "k1"
_KEY_BYTES = bytes([31]) * 32


def _encoded_keys(active: str = _KEY_ID, material: bytes = _KEY_BYTES) -> str:
    return json.dumps({active: base64.b64encode(material).decode("ascii")})


def _origin_headers(**extra: str) -> dict[str, str]:
    headers = {
        "Origin": _ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }
    headers.update(extra)
    return headers


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolated Settings with local HTTP cookie policy + session MAC key."""
    reset_caches()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MINDATLAS_CANONICAL_ORIGIN", _ORIGIN)
    monkeypatch.setenv("CORS_ORIGINS", _ORIGIN)
    monkeypatch.setenv("MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID", _KEY_ID)
    monkeypatch.setenv("MINDATLAS_SESSION_HMAC_KEYS", _encoded_keys())
    # Avoid .env noise: construct explicitly and pin get_settings.
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN=_ORIGIN,
        CORS_ORIGINS=_ORIGIN,
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID=_KEY_ID,
        MINDATLAS_SESSION_HMAC_KEYS=_encoded_keys(),
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.dependencies.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.router.get_settings", lambda: settings)
    return settings


@pytest.fixture
def session_factory() -> Iterator[sessionmaker]:
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine, event

    import tests._db  # noqa: F401 — JSONB→JSON for SQLite
    from app.operator_auth.models import (  # noqa: E402
        OperatorAccount,
        OperatorAuditEvent,
        OperatorSession,
    )
    from app.system_settings.models import AppSetting  # noqa: E402

    tmp = tempfile.NamedTemporaryFile(
        prefix="mindatlas-opauth-api-", suffix=".sqlite", delete=False
    )
    tmp_path = Path(tmp.name)
    tmp.close()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Only the tables this API surface needs — full Base.metadata pulls Entry FKs.
    tables = [
        OperatorAccount.__table__,
        OperatorSession.__table__,
        OperatorAuditEvent.__table__,
        AppSetting.__table__,
    ]
    from app.database import Base  # noqa: E402

    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    try:
        yield factory
    finally:
        engine.dispose()
        try:
            tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if tmp_path.exists():
                tmp_path.unlink()


@pytest.fixture
def initialized_operator(session_factory: sessionmaker) -> None:
    db = session_factory()
    try:
        OperatorRepository(db).seed_account(password=_PASSWORD, role="operator")
        db.commit()
    finally:
        db.close()


@pytest.fixture
def app(
    auth_settings: Settings,
    session_factory: sessionmaker,
    initialized_operator: None,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)

    @application.middleware("http")
    async def _request_id_middleware(request, call_next):  # noqa: ANN001
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["x-request-id"] = request_id
        return response

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_settings] = lambda: auth_settings
    application.include_router(operator_auth_router)
    application.include_router(system_settings_router)
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def origin_headers() -> dict[str, str]:
    return _origin_headers()


@pytest.fixture
def authenticated_client(
    client: TestClient, origin_headers: dict[str, str]
) -> TestClient:
    """Client with session cookies from a successful login (no CSRF header)."""
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert response.status_code == 200, response.text
    # TestClient stores cookies; leave CSRF header unset for pair-missing tests.
    return client


def _csrf_headers(client: TestClient, **extra: str) -> dict[str, str]:
    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf, "expected CSRF cookie after login"
    headers = _origin_headers(**{CSRF_HEADER_NAME: csrf})
    headers.update(extra)
    return headers


def _set_cookies(response: Any) -> list[str]:
    return response.headers.get_list("set-cookie")


# ---------------------------------------------------------------------------
# Step 1 brief cases
# ---------------------------------------------------------------------------


def test_login_sets_exact_cookie_contract(
    client: TestClient, initialized_operator: None, origin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert response.status_code == 200
    session = response.cookies.get(SESSION_COOKIE_NAME)
    csrf = response.cookies.get(CSRF_COOKIE_NAME)
    assert session and csrf and session != csrf
    cookies = _set_cookies(response)
    assert any("HttpOnly" in item and SESSION_COOKIE_NAME in item for item in cookies)
    assert all("SameSite=strict" in item for item in cookies)
    assert all("Domain=" not in item for item in cookies)
    assert response.headers.get("cache-control") == "no-store"
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["authenticated"] is True
    assert data["role"] == "operator"
    assert data["idleExpiresAt"]
    assert data["absoluteExpiresAt"]
    # No secret echo — password value must never appear; field names may in schemas.
    dumped = json.dumps(body)
    assert _PASSWORD not in dumped
    assert "correct horse" not in dumped.lower()
    assert session not in dumped
    assert csrf not in dumped


def test_mutation_requires_cookie_header_pair(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_origin_headers(),  # session cookie present, CSRF header absent
    )
    assert response.status_code == 403
    assert response.json()["message"] == "csrf_rejected"


def test_forged_operator_headers_never_authenticate(client: TestClient) -> None:
    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers={
            "X-MindAtlas-Operator-Id": "forged",
            "X-MindAtlas-Operator-Role": "operator",
            **_origin_headers(),
        },
    )
    assert response.status_code == 401
    assert response.json()["message"] == "invalid_session"


def test_invalid_session_cookie_cleared_on_mutation_401(
    client: TestClient,
    origin_headers: dict[str, str],
    session_factory: sessionmaker,
) -> None:
    """Present-but-invalid session cookies must be expired on 401 responses.

    FastAPI exception handlers build a fresh JSONResponse, so clears applied only
    to the dependency Response are dropped unless the handler re-applies them.
    """
    login = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert login.status_code == 200
    session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
    assert session_cookie and csrf_cookie

    # Revoke the durable session server-side while leaving browser cookies intact.
    db = session_factory()
    try:
        repo = OperatorRepository(db)
        account = repo.get_singleton_account(for_update=True)
        assert account is not None
        revoked = repo.revoke_all_sessions_for_account(
            account.id, reason="revoke_all"
        )
        assert revoked >= 1
        db.commit()
    finally:
        db.close()

    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_origin_headers(**{CSRF_HEADER_NAME: csrf_cookie}),
    )
    assert response.status_code == 401
    assert response.json()["message"] == "invalid_session"
    cookies = _set_cookies(response)
    assert any(
        SESSION_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies
    ), cookies
    assert any(
        CSRF_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies
    ), cookies


# ---------------------------------------------------------------------------
# Step 6 negative origin / content-type / lockout / audit
# ---------------------------------------------------------------------------


def test_login_rejects_non_json_content_type(
    client: TestClient, origin_headers: dict[str, str]
) -> None:
    headers = dict(origin_headers)
    headers["Content-Type"] = "text/plain"
    response = client.post(
        "/api/operator-auth/login",
        content=b'{"password":"correct horse battery"}',
        headers=headers,
    )
    assert response.status_code == 415
    assert response.json()["message"] == "json_content_type_required"


def test_login_rejects_missing_origin(client: TestClient) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers={
            "Content-Type": "application/json",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 403
    assert response.json()["message"] == "same_origin_required"


def test_login_rejects_cross_site_fetch(client: TestClient) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=_origin_headers(**{"Sec-Fetch-Site": "cross-site"}),
    )
    assert response.status_code == 403
    assert response.json()["message"] == "same_origin_required"


def test_login_rejects_wrong_origin(client: TestClient) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=_origin_headers(**{"Origin": "https://evil.example"}),
    )
    assert response.status_code == 403
    assert response.json()["message"] == "same_origin_required"


def test_wrong_password_is_generic(
    client: TestClient, origin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": "definitely-not-the-password"},
        headers=origin_headers,
    )
    assert response.status_code == 401
    body = response.json()
    assert body["message"] == "invalid_credentials"
    dumped = json.dumps(body).lower()
    assert "definitely-not-the-password" not in dumped
    assert "correct horse" not in dumped
    # Same message shape for missing account path is covered by service tests;
    # HTTP must not leak which secret component failed.
    assert "data" not in body or body.get("data") in (None, {})


def test_login_lockout_returns_retry_after(
    client: TestClient, origin_headers: dict[str, str]
) -> None:
    for _ in range(LOGIN_FAILURE_LIMIT):
        failed = client.post(
            "/api/operator-auth/login",
            json={"password": "wrong-password-xx"},
            headers=origin_headers,
        )
        assert failed.status_code in {401, 429}
    locked = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert locked.status_code == 429
    body = locked.json()
    assert body["message"] == "login_locked"
    retry = body["data"]["retryAfterSeconds"]
    assert isinstance(retry, int)
    assert 0 < retry <= 15 * 60
    assert locked.headers.get("retry-after") == str(retry)


def test_session_probe_unauthenticated(client: TestClient) -> None:
    response = client.get("/api/operator-auth/session")
    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is False
    assert response.json()["data"]["role"] is None


def test_session_probe_authenticated(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/api/operator-auth/session")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["authenticated"] is True
    assert data["role"] == "operator"
    assert data["idleExpiresAt"]
    assert data["absoluteExpiresAt"]


def test_logout_requires_csrf_and_clears_cookies(
    authenticated_client: TestClient,
) -> None:
    missing = authenticated_client.post(
        "/api/operator-auth/logout",
        json={},
        headers=_origin_headers(),
    )
    assert missing.status_code == 403
    assert missing.json()["message"] == "csrf_rejected"

    ok = authenticated_client.post(
        "/api/operator-auth/logout",
        json={},
        headers=_csrf_headers(authenticated_client),
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["authenticated"] is False
    cookies = _set_cookies(ok)
    assert any(SESSION_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies)
    assert any(CSRF_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies)

    # Session no longer usable.
    probe = authenticated_client.get("/api/operator-auth/session")
    # Client may still send old cookies; server must reject / clear.
    assert probe.json()["data"]["authenticated"] is False


def test_password_change_clears_cookies_and_revokes(
    authenticated_client: TestClient,
    session_factory: sessionmaker,
) -> None:
    new_password = "new horse battery staple"
    response = authenticated_client.post(
        "/api/operator-auth/password",
        json={
            "currentPassword": _PASSWORD,
            "newPassword": new_password,
        },
        headers=_csrf_headers(authenticated_client),
    )
    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is False
    cookies = _set_cookies(response)
    assert any(SESSION_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies)

    # Old session rejected.
    probe = authenticated_client.get("/api/operator-auth/session")
    assert probe.json()["data"]["authenticated"] is False

    # New password works.
    login = authenticated_client.post(
        "/api/operator-auth/login",
        json={"password": new_password},
        headers=_origin_headers(),
    )
    assert login.status_code == 200


def test_revoke_all_clears_cookies(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/operator-auth/sessions/revoke-all",
        json={"reason": "revoke_all"},
        headers=_csrf_headers(authenticated_client),
    )
    assert response.status_code == 200
    assert response.json()["data"]["authenticated"] is False
    cookies = _set_cookies(response)
    assert any(SESSION_COOKIE_NAME in item and "Max-Age=0" in item for item in cookies)


def test_invalid_csrf_header_rejected(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_origin_headers(**{CSRF_HEADER_NAME: "not-the-real-csrf-token-value!!"}),
    )
    assert response.status_code == 403
    assert response.json()["message"] == "csrf_rejected"


def test_mutation_with_valid_csrf_succeeds(authenticated_client: TestClient) -> None:
    response = authenticated_client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_csrf_headers(authenticated_client),
    )
    assert response.status_code == 200
    assert response.json()["data"]["locale"] == "en"


def test_viewer_role_cannot_mutate(
    client: TestClient,
    session_factory: sessionmaker,
    auth_settings: Settings,
    origin_headers: dict[str, str],
) -> None:
    # Re-seed as viewer (singleton already operator from fixture — replace role).
    db = session_factory()
    try:
        account = OperatorRepository(db).get_singleton_account(for_update=True)
        assert account is not None
        account.role = "viewer"
        db.commit()
    finally:
        db.close()

    login = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert login.status_code == 200
    assert login.json()["data"]["role"] == "viewer"

    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "zh"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["message"] == "operator_role_required"


def test_login_audit_metadata_is_allowlisted(
    client: TestClient,
    origin_headers: dict[str, str],
    session_factory: sessionmaker,
) -> None:
    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers={
            **origin_headers,
            "User-Agent": "MindAtlas-Test-Agent/1.0",
            "X-Request-ID": "req-audit-allowlist-1",
        },
    )
    assert response.status_code == 200

    db = session_factory()
    try:
        rows = list(
            db.execute(
                select(OperatorAuditEvent).order_by(OperatorAuditEvent.occurred_at)
            ).scalars()
        )
        assert rows
        dumped = json.dumps(
            [
                {
                    "type": r.event_type,
                    "meta": r.metadata_json,
                    "req": r.request_id,
                    "rd": r.request_digest,
                    "ua": r.user_agent_digest,
                    "net": r.network_digest,
                }
                for r in rows
            ]
        )
        assert "MindAtlas-Test-Agent" not in dumped
        assert "correct horse" not in dumped
        assert response.cookies.get(SESSION_COOKIE_NAME) not in dumped
        # Digests present, raws absent.
        for row in rows:
            if row.event_type in {"login_succeeded", "session_created"}:
                assert row.request_digest
                assert row.user_agent_digest
                assert row.network_digest
                assert row.request_id == "req-audit-allowlist-1"
    finally:
        db.close()


def test_auth_unavailable_without_key_ring(
    client: TestClient,
    origin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN=_ORIGIN,
        CORS_ORIGINS=_ORIGIN,
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID="",
        MINDATLAS_SESSION_HMAC_KEYS=None,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: broken)
    monkeypatch.setattr("app.operator_auth.dependencies.get_settings", lambda: broken)
    monkeypatch.setattr("app.operator_auth.router.get_settings", lambda: broken)
    client.app.dependency_overrides[get_settings] = lambda: broken  # type: ignore[attr-defined]

    response = client.post(
        "/api/operator-auth/login",
        json={"password": _PASSWORD},
        headers=origin_headers,
    )
    assert response.status_code == 503
    assert response.json()["message"] == "operator_auth_unavailable"
