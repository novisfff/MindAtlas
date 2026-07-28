"""Shared helpers for operator session + skill principal test construction.

The only production HTTP mint of OperatorPrincipal is a durable password session.
Tests that exercise HTTP admin/eval surfaces should seed an account, login with
same-origin headers, and copy the CSRF cookie into X-MindAtlas-CSRF.

Service-layer tests may construct a principal directly with UUID fields — never
with caller-supplied header strings.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from uuid import UUID, uuid4, uuid5

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.operator_auth.constants import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from app.operator_auth.contracts import OperatorPrincipal, OperatorRole
from app.operator_auth.repository import OperatorRepository
from app.operator_auth.router import login_router
from app.operator_auth.route_policy import (
    credential_exchange_router,
    protected_browser_router,
)


CANONICAL_ORIGIN = "http://localhost:5173"
OPERATOR_PASSWORD = "correct horse battery"
_SESSION_KEY_ID = "k1"
_SESSION_KEY_BYTES = bytes([31]) * 32
_PRINCIPAL_NS = UUID("00000000-0000-4000-8000-00000000a001")


def encoded_session_hmac_keys(
    active: str = _SESSION_KEY_ID, material: bytes = _SESSION_KEY_BYTES
) -> str:
    return json.dumps({active: base64.b64encode(material).decode("ascii")})


def operator_test_settings(**overrides: Any) -> Settings:
    """Local-HTTP cookie policy + session MAC keys for skill/admin API tests."""
    kwargs: dict[str, Any] = {
        "APP_ENV": "development",
        "MINDATLAS_CANONICAL_ORIGIN": CANONICAL_ORIGIN,
        "CORS_ORIGINS": CANONICAL_ORIGIN,
        "MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID": _SESSION_KEY_ID,
        "MINDATLAS_SESSION_HMAC_KEYS": encoded_session_hmac_keys(),
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def pin_operator_settings(settings: Settings, monkeypatch: Any | None = None) -> Settings:
    """Point get_settings at ``settings`` without destroying the lru_cache wrapper.

    Replacing ``app.config.get_settings`` with a bare lambda permanently strips
    ``cache_clear`` from later imports of the symbol. Wrap instead, clear the
    real cache, and rebind only the dependency modules that close over the name.
    """
    import app.config as config_mod
    import app.operator_auth.dependencies as dep_mod
    import app.operator_auth.router as router_mod
    import app.operator_auth.route_policy as policy_mod

    real = config_mod.get_settings
    # Prefer the real cached function; if a prior bare lambda already replaced it,
    # re-import the pristine lru_cache wrapper from the defining module object.
    if not hasattr(real, "cache_clear"):
        # Restore from the original decorated function if still available on the
        # module's __dict__ under a private alias; otherwise re-apply lru_cache.
        from functools import lru_cache

        from app.config import Settings as _Settings

        @lru_cache(maxsize=1)
        def _restored_get_settings() -> _Settings:
            return _Settings()

        config_mod.get_settings = _restored_get_settings  # type: ignore[assignment]
        real = _restored_get_settings

    real.cache_clear()

    def _pinned() -> Settings:
        return settings

    # Keep cache_clear available on the module symbol so later suites can reset.
    _pinned.cache_clear = real.cache_clear  # type: ignore[attr-defined]

    if monkeypatch is not None:
        monkeypatch.setattr("app.config.get_settings", _pinned)
        monkeypatch.setattr("app.operator_auth.dependencies.get_settings", _pinned)
        monkeypatch.setattr("app.operator_auth.router.get_settings", _pinned)
        monkeypatch.setattr("app.operator_auth.route_policy.get_settings", _pinned)
    else:
        config_mod.get_settings = _pinned  # type: ignore[assignment]
        dep_mod.get_settings = _pinned  # type: ignore[assignment]
        router_mod.get_settings = _pinned  # type: ignore[assignment]
        policy_mod.get_settings = _pinned  # type: ignore[assignment]
    return settings


def restore_operator_settings() -> None:
    """Restore the real lru_cached get_settings after unittest-style pinning."""
    from functools import lru_cache

    import app.config as config_mod
    import app.operator_auth.dependencies as dep_mod
    import app.operator_auth.router as router_mod
    import app.operator_auth.route_policy as policy_mod
    from app.config import Settings as _Settings

    @lru_cache(maxsize=1)
    def _get_settings() -> _Settings:
        return _Settings()

    config_mod.get_settings = _get_settings  # type: ignore[assignment]
    dep_mod.get_settings = _get_settings  # type: ignore[assignment]
    router_mod.get_settings = _get_settings  # type: ignore[assignment]
    policy_mod.get_settings = _get_settings  # type: ignore[assignment]


def make_service_principal(
    label: str = "op-1",
    *,
    role: OperatorRole = "operator",
    session_id: UUID | None = None,
) -> OperatorPrincipal:
    """Deterministic UUID-backed principal for direct service calls."""
    return OperatorPrincipal(
        operator_id=uuid5(_PRINCIPAL_NS, label),
        role=role,
        session_id=session_id or uuid4(),
    )


def seed_operator_account(db: Session, *, password: str = OPERATOR_PASSWORD) -> None:
    OperatorRepository(db).seed_account(password=password, role="operator")
    db.commit()


def origin_headers(**extra: str) -> dict[str, str]:
    headers = {
        "Origin": CANONICAL_ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }
    headers.update(extra)
    return headers


def csrf_headers(client: TestClient, **extra: str) -> dict[str, str]:
    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf, "expected CSRF cookie after login"
    return origin_headers(**{CSRF_HEADER_NAME: csrf, **extra})


def login_operator_session(
    client: TestClient, *, password: str = OPERATOR_PASSWORD
) -> dict[str, str]:
    """Login and return origin+CSRF headers suitable for unsafe methods."""
    response = client.post(
        "/api/operator-auth/login",
        json={"password": password},
        headers=origin_headers(),
    )
    assert response.status_code == 200, response.text
    return csrf_headers(client)


def build_authenticated_skill_client(
    *,
    db: Session,
    include_routers: list[Any],
    settings: Settings | None = None,
    password: str = OPERATOR_PASSWORD,
    mount_under_protected_browser: bool = True,
) -> tuple[TestClient, dict[str, str], Settings]:
    """Seed singleton operator, mount routers, login, return (client, headers, settings).

    When ``mount_under_protected_browser`` is True, skill/admin/eval routers sit
    under the real parent policy (session + CSRF on unsafe methods). Login is
    mounted under credential_exchange.
    """
    from app.common.exceptions import register_exception_handlers

    resolved = settings or operator_test_settings()
    pin_operator_settings(resolved)
    seed_operator_account(db, password=password)

    app = FastAPI()
    register_exception_handlers(app)

    def _override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: resolved

    cred = credential_exchange_router()
    cred.include_router(login_router)
    app.include_router(cred)

    if mount_under_protected_browser:
        protected = protected_browser_router()
        for router in include_routers:
            protected.include_router(router)
        app.include_router(protected)
    else:
        for router in include_routers:
            app.include_router(router)

    client = TestClient(app)
    headers = login_operator_session(client, password=password)
    # Note: pin_operator_settings rebinds module-level get_settings. Callers that
    # share a process with suites expecting the real lru_cache wrapper should
    # invoke restore_operator_settings() in teardown (or rely on cache_clear
    # remaining attached to the pinned callable).
    return client, headers, resolved
