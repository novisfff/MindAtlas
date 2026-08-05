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

# Identity of the real ``@lru_cache`` get_settings. FastAPI ``Depends(get_settings)``
# freezes this callable on each route at import time; restore MUST reinstall the
# same object (not a freshly wrapped lru_cache) or dependency_overrides keyed by
# identity miss and login sees empty canonical_origin from a stale cache entry.
_ORIGINAL_GET_SETTINGS: Any | None = None


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


def _iter_app_get_settings_holders() -> list[Any]:
    """Loaded ``app.*`` modules that currently bind a ``get_settings`` callable.

    Many production modules do ``from app.config import get_settings`` at import
    time, which freezes a function object on the consumer module. Pin/restore
    must rebind every such holder — not only ``app.config`` and the four
    operator_auth modules — or later suites keep reading a stale Settings
    snapshot (build_revision_drift, empty MinIO creds, LightRAG env, …).
    """
    import sys

    holders: list[Any] = []
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not (name == "app.config" or name.startswith("app.")):
            continue
        try:
            gs = getattr(mod, "get_settings", None)
        except Exception:  # noqa: BLE001 — defensive against broken modules
            continue
        if callable(gs):
            holders.append(mod)
    return holders


def _rebind_get_settings_holders(
    target: Any, *, monkeypatch: Any | None = None
) -> None:
    """Point every loaded app.* get_settings holder at ``target``."""
    for mod in _iter_app_get_settings_holders():
        if monkeypatch is not None:
            monkeypatch.setattr(mod, "get_settings", target, raising=False)
        else:
            try:
                setattr(mod, "get_settings", target)
            except Exception:  # noqa: BLE001 — skip extension/frozen modules
                continue


def _clear_settings_derived_caches() -> None:
    """Drop runtime snapshots that may have been built under a pinned Settings."""
    try:
        from app.system_settings.runtime_config_service import clear_runtime_config_caches

        clear_runtime_config_caches()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.common.storage import get_minio_client

        get_minio_client.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.lightrag.manager import reset_lightrag_singletons_for_tests

        reset_lightrag_singletons_for_tests()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.lightrag.service import reset_lightrag_query_state_for_tests

        reset_lightrag_query_state_for_tests()
    except Exception:  # noqa: BLE001
        pass


def _capture_original_get_settings(candidate: Any) -> Any:
    """Remember the process-wide real lru_cache get_settings (stable identity)."""
    global _ORIGINAL_GET_SETTINGS
    if _ORIGINAL_GET_SETTINGS is not None:
        return _ORIGINAL_GET_SETTINGS
    if callable(candidate) and hasattr(candidate, "cache_clear"):
        # Reject our own pin wrappers (they borrow cache_clear from the real fn).
        if getattr(candidate, "_mindatlas_settings_pin", False):
            return _ORIGINAL_GET_SETTINGS
        _ORIGINAL_GET_SETTINGS = candidate
        return candidate
    return _ORIGINAL_GET_SETTINGS


def _rebuild_original_get_settings() -> Any:
    """Last-resort rebuild when the original callable was lost to a bare lambda."""
    global _ORIGINAL_GET_SETTINGS
    from functools import lru_cache

    from app.config import Settings as _Settings

    @lru_cache(maxsize=1)
    def _get_settings() -> _Settings:
        return _Settings()

    _ORIGINAL_GET_SETTINGS = _get_settings
    return _get_settings


def _resolve_real_get_settings() -> Any:
    """Return the stable real get_settings, rebuilding only if identity was lost."""
    import app.config as config_mod

    captured = _capture_original_get_settings(config_mod.get_settings)
    if captured is not None:
        return captured
    current = config_mod.get_settings
    if callable(current) and hasattr(current, "cache_clear"):
        if not getattr(current, "_mindatlas_settings_pin", False):
            return _capture_original_get_settings(current) or current
    return _rebuild_original_get_settings()


def pin_operator_settings(settings: Settings, monkeypatch: Any | None = None) -> Settings:
    """Point get_settings at ``settings`` without destroying the lru_cache wrapper.

    Replacing ``app.config.get_settings`` with a bare lambda permanently strips
    ``cache_clear`` from later imports of the symbol and breaks FastAPI
    ``Depends`` identity. Wrap instead, clear the real cache, and rebind every
    loaded ``app.*`` module that closed over the name (import-time
    ``from app.config import get_settings`` freezes the ref).
    """
    import app.config as config_mod

    real = _resolve_real_get_settings()
    # Ensure config module holds the real callable before we pin over it.
    if config_mod.get_settings is not real and not getattr(
        config_mod.get_settings, "_mindatlas_settings_pin", False
    ):
        # A foreign bare lambda may be installed; put real back first so
        # Depends identity stays coherent after restore.
        config_mod.get_settings = real  # type: ignore[assignment]
    real.cache_clear()

    def _pinned() -> Settings:
        return settings

    # Keep cache_clear available on the module symbol so later suites can reset.
    _pinned.cache_clear = real.cache_clear  # type: ignore[attr-defined]
    _pinned._mindatlas_settings_pin = True  # type: ignore[attr-defined]

    if monkeypatch is not None:
        # Rebind every current holder under monkeypatch so fixture teardown undoes
        # the full surface (not just the four operator_auth modules).
        _rebind_get_settings_holders(_pinned, monkeypatch=monkeypatch)
    else:
        _rebind_get_settings_holders(_pinned, monkeypatch=None)
    # Drop any runtime snapshots taken before the pin so MinIO/LightRAG re-read.
    _clear_settings_derived_caches()
    return settings


def restore_operator_settings() -> None:
    """Restore the real lru_cached get_settings after unittest-style pinning.

    Reinstalls the **same** original callable FastAPI routes closed over at
    import time (identity-stable), rebinds every loaded ``app.*`` holder, and
    clears settings-derived runtime caches so subsequent suites cannot keep a
    pinned build revision, empty MinIO credentials, or LightRAG singleton.
    """
    import app.config as config_mod

    real = _resolve_real_get_settings()
    real.cache_clear()
    config_mod.get_settings = real  # type: ignore[assignment]
    _rebind_get_settings_holders(real, monkeypatch=None)
    _clear_settings_derived_caches()


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
