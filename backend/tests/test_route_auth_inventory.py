"""Exhaustive FastAPI route-policy inventory and authority-separation proofs.

Every non-framework application route must carry exactly one ``__route_policy__``
marker via the dependency graph (never path-name inference alone).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker
from starlette.routing import Mount

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.operator_auth.models import OperatorAuditEvent  # noqa: E402
from app.operator_auth.route_policy import (  # noqa: E402
    POLICY_AUTHENTICATED_MACHINE,
    POLICY_CREDENTIAL_EXCHANGE,
    POLICY_PROTECTED_BROWSER,
    POLICY_PUBLIC,
    POLICY_SETUP_INITIALIZATION,
)

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_FRAMEWORK_PATH_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
)


@dataclass(frozen=True)
class InventoryRoute:
    """Version-stable route view for policy inventory.

    FastAPI <0.140 flattens included routers into top-level ``APIRoute`` objects.
    FastAPI ≥0.140 keeps ``_IncludedRouter`` branches and exposes effective routes
    via ``effective_candidates()``. Parent-router policy dependencies only appear
    on the effective graph, so inventory must read those fields rather than the
    raw child ``APIRoute`` alone.
    """

    path: str
    methods: set[str]
    name: str
    dependencies: list[Any]
    dependant: Any


def _is_framework_path(path: str) -> bool:
    if path.startswith(_FRAMEWORK_PATH_PREFIXES) or path in {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
    }:
        return True
    return False


def _inventory_from_api_route(route: APIRoute) -> InventoryRoute | None:
    path = route.path or ""
    if _is_framework_path(path):
        return None
    return InventoryRoute(
        path=path,
        methods=set(route.methods or set()),
        name=route.name or "",
        dependencies=list(route.dependencies or []),
        dependant=route.dependant,
    )


def _inventory_from_effective(candidate: Any) -> InventoryRoute | None:
    """Build an inventory row from FastAPI ≥0.140 ``_EffectiveRouteContext``."""
    path = str(getattr(candidate, "path", None) or "")
    if not path or _is_framework_path(path):
        return None
    methods = getattr(candidate, "methods", None) or set()
    name = str(getattr(candidate, "name", None) or "")
    dependencies = list(getattr(candidate, "dependencies", None) or [])
    dependant = getattr(candidate, "dependant", None)
    # Fall back to the original APIRoute when effective fields are sparse.
    original = getattr(candidate, "original_route", None)
    if isinstance(original, APIRoute):
        if not dependencies:
            dependencies = list(original.dependencies or [])
        if dependant is None:
            dependant = original.dependant
        if not name:
            name = original.name or ""
        if not methods:
            methods = set(original.methods or set())
    return InventoryRoute(
        path=path,
        methods=set(methods),
        name=name,
        dependencies=dependencies,
        dependant=dependant,
    )


def _expand_router_nodes(nodes: list[Any]) -> list[InventoryRoute]:
    """Walk top-level app routes across FastAPI flat and nested layouts."""
    out: list[InventoryRoute] = []
    stack: list[Any] = list(nodes)
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None:
            continue
        ident = id(node)
        if ident in seen:
            continue
        seen.add(ident)

        if isinstance(node, APIRoute):
            item = _inventory_from_api_route(node)
            if item is not None:
                out.append(item)
            continue

        if isinstance(node, Mount):
            # Legacy nested mounts (if any) — descend without path rewrite here;
            # production mounts application routes via include_router, not Mount.
            mount_routes = getattr(node, "routes", None) or []
            stack.extend(list(mount_routes))
            continue

        # FastAPI ≥0.140: _IncludedRouter exposes effective_candidates().
        effective = getattr(node, "effective_candidates", None)
        if callable(effective):
            for candidate in effective() or []:
                # Nested included routers appear as further _IncludedRouter nodes.
                nested_effective = getattr(candidate, "effective_candidates", None)
                if callable(nested_effective):
                    stack.append(candidate)
                    continue
                item = _inventory_from_effective(candidate)
                if item is not None:
                    out.append(item)
            continue

        # Unknown node types (plain Starlette Route, WebSocketRoute, …) skipped.
    return out


def application_routes(app) -> list[InventoryRoute]:
    """Return non-framework HTTP routes registered on ``app``.

    Compatible with both pre-0.140 flattened ``APIRoute`` lists and FastAPI
    0.140+ ``_IncludedRouter`` effective-candidate graphs. Parent policy
    dependencies are visible on the returned rows in both cases.
    """
    return _expand_router_nodes(list(app.routes))


def route_identity(route: InventoryRoute) -> str:
    methods = ",".join(sorted(m for m in (route.methods or set()) if m != "HEAD"))
    return f"{methods} {route.path} name={route.name}"


def _walk_dependant(dependant: Any) -> Iterator[Any]:
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


def policy_markers(route: InventoryRoute) -> set[str]:
    """Collect ``__route_policy__`` markers from the route dependency graph."""
    markers: set[str] = set()
    # Router-level dependencies are attached to APIRoute.dependencies and also
    # merged into route.dependant — walk both for resilience. On FastAPI ≥0.140
    # these fields come from the effective candidate (parent markers included).
    for dep in route.dependencies or []:
        call = getattr(dep, "dependency", None)
        marker = getattr(call, "__route_policy__", None) if call is not None else None
        if isinstance(marker, str):
            markers.add(marker)
    for node in _walk_dependant(route.dependant):
        call = getattr(node, "call", None)
        marker = getattr(call, "__route_policy__", None) if call is not None else None
        if isinstance(marker, str):
            markers.add(marker)
    return markers


def openclaw_machine_routes(app) -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for route in application_routes(app):
        if not (route.path or "").startswith("/api/integrations/openclaw"):
            continue
        for method in (route.methods or set()) & UNSAFE:
            markers = policy_markers(route)
            marker = next(iter(markers)) if len(markers) == 1 else None
            found.add((method, route.path, marker or "missing"))
    return found


def unsafe_non_session_routes(app) -> set[tuple[str, str, str]]:
    """Unsafe routes whose policy does not require an existing browser session."""
    found: set[tuple[str, str, str]] = set()
    for route in application_routes(app):
        markers = policy_markers(route)
        marker = next(iter(markers)) if len(markers) == 1 else None
        if marker == POLICY_PROTECTED_BROWSER:
            continue
        for method in (route.methods or set()) & UNSAFE:
            found.add((method, route.path, marker or "missing"))
    return found


@pytest.fixture
def app():
    """Production FastAPI app — import lazily and never mutate its overrides/routes.

    Earlier modules that pin ``get_settings`` or set ``dependency_overrides`` on a
    shared app must not empty inventory results. We re-import ``app.main`` each
    collection and clear any leftover overrides without touching ``app.routes``.
    """
    from app.main import app as production_app

    # Inventory is read-only; clear overrides left by other suites (if any).
    production_app.dependency_overrides.clear()
    try:
        yield production_app
    finally:
        production_app.dependency_overrides.clear()


def test_every_application_route_has_exact_policy(app) -> None:
    routes = application_routes(app)
    assert routes, "production app must expose application routes for inventory"
    for route in routes:
        markers = policy_markers(route)
        assert len(markers) == 1, route_identity(route)
        if route.methods & UNSAFE:
            assert next(iter(markers)) in {
                POLICY_CREDENTIAL_EXCHANGE,
                POLICY_SETUP_INITIALIZATION,
                POLICY_PROTECTED_BROWSER,
                POLICY_AUTHENTICATED_MACHINE,
            }, route_identity(route)


def test_only_setup_and_login_are_unsafe_without_existing_session(app) -> None:
    exemptions = {
        ("POST", "/api/system-settings/initialize", POLICY_SETUP_INITIALIZATION),
        ("POST", "/api/operator-auth/login", POLICY_CREDENTIAL_EXCHANGE),
    }
    assert unsafe_non_session_routes(app) == exemptions | openclaw_machine_routes(app)


def test_openclaw_runtime_is_authenticated_machine(app) -> None:
    runtime_routes = [
        route
        for route in application_routes(app)
        if (route.path or "").startswith("/api/integrations/openclaw")
    ]
    assert runtime_routes, "expected OpenClaw runtime routes to be mounted"
    for route in runtime_routes:
        assert policy_markers(route) == {POLICY_AUTHENTICATED_MACHINE}, route_identity(
            route
        )


def test_openclaw_settings_are_protected_browser(app) -> None:
    settings_routes = [
        route
        for route in application_routes(app)
        if (route.path or "").startswith("/api/system-settings/openclaw-integration")
    ]
    assert settings_routes, "expected OpenClaw settings routes to be mounted"
    for route in settings_routes:
        assert policy_markers(route) == {POLICY_PROTECTED_BROWSER}, route_identity(route)


def test_public_routes_are_exactly_liveness_and_init_status(app) -> None:
    public = {
        (next(iter(policy_markers(route))), route.path, frozenset(route.methods or ()))
        for route in application_routes(app)
        if policy_markers(route) == {POLICY_PUBLIC}
    }
    # HEAD is auto-added for GET routes by FastAPI.
    expected_paths = {
        "/health",
        "/ready",
        "/api/system-settings/initialization-status",
        "/api/system-settings/initialization-defaults",
        "/api/operator-auth/session",
    }
    assert {path for _, path, _ in public} == expected_paths


def test_skill_admin_and_eval_mounted_under_protected_browser(app) -> None:
    admin = [
        route
        for route in application_routes(app)
        if (route.path or "").startswith("/api/assistant-config/skill-admin")
    ]
    eval_routes = [
        route
        for route in application_routes(app)
        if (route.path or "").startswith("/api/assistant-config/skill-eval")
    ]
    assert admin, "skill admin must be mounted in every environment"
    assert eval_routes, "skill eval must be mounted in every environment"
    for route in admin + eval_routes:
        assert policy_markers(route) == {POLICY_PROTECTED_BROWSER}, route_identity(route)


# ---------------------------------------------------------------------------
# OpenClaw authority separation
# ---------------------------------------------------------------------------


_POSTGRES_URL = os.environ.get("MINDATLAS_TEST_POSTGRES_URL", "").strip()
_ORIGIN = "http://localhost:5173"


def _csrf_headers(client, **extra: str) -> dict[str, str]:
    from app.operator_auth.constants import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

    csrf = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf, "expected CSRF cookie"
    headers = {
        "Origin": _ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        CSRF_HEADER_NAME: csrf,
    }
    headers.update(extra)
    return headers


@pytest.fixture
def authority_client(tmp_path, monkeypatch):
    """Minimal app with OpenClaw settings + runtime behind real policy parents."""
    import base64
    import json
    import tempfile
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event as sa_event

    from app.common.exceptions import register_exception_handlers
    from app.common.request_context import reset_request_id, set_request_id
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.openclaw_integration.router import runtime_router, settings_router
    from app.operator_auth.constants import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
    from app.operator_auth.models import (
        OperatorAccount,
        OperatorAuditEvent,
        OperatorSession,
    )
    from app.operator_auth.repository import OperatorRepository
    from app.operator_auth.router import (
        login_router,
        protected_operator_auth_router,
        session_probe_router,
    )
    from app.operator_auth.route_policy import (
        credential_exchange_router,
        machine_router,
        protected_browser_router,
        public_router,
    )
    from app.system_settings.models import AppSetting
    from app.system_settings.router import (
        protected_system_settings_router,
        public_system_settings_router,
        setup_system_settings_router,
    )

    reset_caches()
    key_id = "k1"
    key_bytes = bytes([31]) * 32
    encoded = json.dumps({key_id: base64.b64encode(key_bytes).decode("ascii")})
    password = "correct horse battery"
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN=_ORIGIN,
        CORS_ORIGINS=_ORIGIN,
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID=key_id,
        MINDATLAS_SESSION_HMAC_KEYS=encoded,
    )
    # Rebind every import-frozen app.* holder under monkeypatch (identity-stable
    # restore on teardown). Bare four-module setattr left Depends(get_settings)
    # and skill_resolution holders on stale callables after earlier pins.
    from tests.operator_session_helpers import pin_operator_settings

    pin_operator_settings(settings, monkeypatch=monkeypatch)

    tmp = tempfile.NamedTemporaryFile(
        prefix="mindatlas-route-auth-", suffix=".sqlite", delete=False
    )
    tmp_path_file = Path(tmp.name)
    tmp.close()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @sa_event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Resolve Relation→Entry before any OperatorAccount construction (init service
    # import chain registers Relation with an Entry relationship).
    import app.entry.models  # noqa: F401

    # Only the tables this surface needs — full Base.metadata pulls PG-only CHECKs.
    tables = [
        OperatorAccount.__table__,
        OperatorSession.__table__,
        OperatorAuditEvent.__table__,
        AppSetting.__table__,
    ]
    from tests._db import create_sqlite_schema

    create_sqlite_schema(engine, tables=tables)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    db = factory()
    try:
        OperatorRepository(db).seed_account(password=password, role="operator")
        db.commit()
    finally:
        db.close()

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
        session = factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_settings] = lambda: settings

    public = public_router()
    public.include_router(public_system_settings_router)
    public.include_router(session_probe_router)
    cred = credential_exchange_router()
    cred.include_router(login_router)
    setup = __import__(
        "app.operator_auth.route_policy", fromlist=["setup_router"]
    ).setup_router()
    setup.include_router(setup_system_settings_router)
    protected = protected_browser_router()
    protected.include_router(protected_system_settings_router)
    protected.include_router(protected_operator_auth_router)
    protected.include_router(settings_router)
    machine = machine_router()
    machine.include_router(runtime_router)

    application.include_router(public)
    application.include_router(cred)
    application.include_router(setup)
    application.include_router(protected)
    application.include_router(machine)

    client = TestClient(application, raise_server_exceptions=False)
    try:
        yield {
            "client": client,
            "factory": factory,
            "password": password,
            "settings": settings,
            "SESSION_COOKIE_NAME": SESSION_COOKIE_NAME,
            "CSRF_COOKIE_NAME": CSRF_COOKIE_NAME,
        }
    finally:
        application.dependency_overrides.clear()
        client.close()
        engine.dispose()
        try:
            tmp_path_file.unlink(missing_ok=True)  # type: ignore[call-arg]
        except TypeError:
            if tmp_path_file.exists():
                tmp_path_file.unlink()


def test_openclaw_bearer_without_session_succeeds_on_machine(
    authority_client, monkeypatch
) -> None:
    """Valid Bearer + no session succeeds on machine endpoints."""
    from app.openclaw_integration.service import OpenClawRuntimeAuditContext

    client = authority_client["client"]

    def _fake_authorize(self, request):  # noqa: ANN001
        return OpenClawRuntimeAuditContext(source="test")

    monkeypatch.setattr(
        "app.openclaw_integration.service.OpenClawIntegrationService.authorize_runtime_request",
        _fake_authorize,
    )
    monkeypatch.setattr(
        "app.openclaw_integration.service.OpenClawIntegrationService.get_runtime_catalog",
        lambda self, preferred_locale=None: type(
            "R",
            (),
            {
                "model_dump": lambda *a, **k: {
                    "integrationName": "MindAtlas",
                    "capabilities": [],
                }
            },
        )(),
    )

    # No session cookies — only Bearer.
    client.cookies.clear()
    response = client.get(
        "/api/integrations/openclaw/capabilities",
        headers={"Authorization": "Bearer test-secret-value-not-real"},
    )
    assert response.status_code == 200, response.text


def test_openclaw_session_without_bearer_fails_machine(authority_client) -> None:
    client = authority_client["client"]
    password = authority_client["password"]
    login = client.post(
        "/api/operator-auth/login",
        json={"password": password},
        headers={
            "Origin": _ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    assert login.status_code == 200, login.text
    # Session present, no Bearer.
    response = client.get("/api/integrations/openclaw/capabilities")
    assert response.status_code in {401, 403}
    # Must not succeed via Operator session alone.
    assert response.status_code != 200


def test_openclaw_bearer_fails_browser_settings(authority_client, monkeypatch) -> None:
    client = authority_client["client"]
    client.cookies.clear()
    response = client.get(
        "/api/system-settings/openclaw-integration",
        headers={"Authorization": "Bearer test-secret-value-not-real"},
    )
    assert response.status_code == 401
    assert response.json()["message"] == "invalid_session"


def test_openclaw_operator_session_succeeds_on_browser_settings(
    authority_client, monkeypatch
) -> None:
    from app.openclaw_integration.schemas import OpenClawIntegrationSettingsResponse

    client = authority_client["client"]
    password = authority_client["password"]
    login = client.post(
        "/api/operator-auth/login",
        json={"password": password},
        headers={
            "Origin": _ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    assert login.status_code == 200, login.text

    class _FakeSettings:
        def model_dump(self, *a, **k):  # noqa: ANN001
            return {
                "enabled": False,
                "secretConfigured": False,
                "secretHint": None,
                "catalogItems": [],
            }

    monkeypatch.setattr(
        "app.openclaw_integration.service.OpenClawIntegrationService.get_settings_response",
        lambda self, preferred_locale=None: _FakeSettings(),
    )
    response = client.get("/api/system-settings/openclaw-integration")
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Audit atomicity
# ---------------------------------------------------------------------------


def test_service_exception_leaves_no_generic_success_audit(
    authority_client, monkeypatch
) -> None:
    """If the endpoint raises before commit, staged success audit must roll back."""
    client = authority_client["client"]
    factory = authority_client["factory"]
    password = authority_client["password"]

    login = client.post(
        "/api/operator-auth/login",
        json={"password": password},
        headers={
            "Origin": _ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    assert login.status_code == 200

    def _boom(*args, **kwargs):  # noqa: ANN001
        raise RuntimeError("simulated domain failure")

    monkeypatch.setattr(
        "app.system_settings.service.SystemSettingsService.set_locale",
        _boom,
    )
    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 500

    db = factory()
    try:
        rows = list(
            db.execute(
                select(OperatorAuditEvent).where(
                    OperatorAuditEvent.event_type
                    == "control_plane_mutation_committed"
                )
            ).scalars()
        )
        assert rows == []
    finally:
        db.close()


def test_mutation_success_stages_control_plane_audit(
    authority_client, monkeypatch
) -> None:
    client = authority_client["client"]
    factory = authority_client["factory"]
    password = authority_client["password"]

    login = client.post(
        "/api/operator-auth/login",
        json={"password": password},
        headers={
            "Origin": _ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
        },
    )
    assert login.status_code == 200

    response = client.put(
        "/api/system-settings/locale",
        json={"locale": "en"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    db = factory()
    try:
        rows = list(
            db.execute(
                select(OperatorAuditEvent).where(
                    OperatorAuditEvent.event_type
                    == "control_plane_mutation_committed"
                )
            ).scalars()
        )
        assert len(rows) >= 1
        meta = rows[-1].metadata_json or {}
        assert meta.get("method") == "PUT"
        assert isinstance(meta.get("routeName"), str) and meta["routeName"]
        # No raw path params / secrets in metadata.
        assert "locale" not in meta
        dumped = str(meta)
        assert password not in dumped
    finally:
        db.close()


@pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="MINDATLAS_TEST_POSTGRES_URL not set; audit INSERT failure needs PostgreSQL",
)
def test_audit_insert_failure_rolls_back_domain_mutation(monkeypatch) -> None:
    """PostgreSQL: if the staged audit INSERT fails, the domain row must not commit."""
    import base64
    import json
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from app.common.exceptions import register_exception_handlers
    from app.common.request_context import reset_request_id, set_request_id
    from app.config import Settings, get_settings
    from app.database import get_db
    from app.operator_auth.models import (
        OperatorAccount,
        OperatorAuditEvent,
        OperatorSession,
    )
    from app.operator_auth.repository import OperatorRepository
    from app.operator_auth.router import (
        login_router,
        protected_operator_auth_router,
        session_probe_router,
    )
    from app.operator_auth.route_policy import (
        credential_exchange_router,
        protected_browser_router,
        public_router,
    )
    from app.system_settings.models import AppSetting
    from app.system_settings.router import (
        protected_system_settings_router,
        public_system_settings_router,
    )
    from app.system_settings.service import SYSTEM_LOCALE_KEY

    reset_caches()
    key_id = "k1"
    key_bytes = bytes([42]) * 32
    encoded = json.dumps({key_id: base64.b64encode(key_bytes).decode("ascii")})
    password = "correct horse battery"
    settings = Settings(
        APP_ENV="development",
        MINDATLAS_CANONICAL_ORIGIN=_ORIGIN,
        CORS_ORIGINS=_ORIGIN,
        MINDATLAS_SESSION_HMAC_ACTIVE_KEY_ID=key_id,
        MINDATLAS_SESSION_HMAC_KEYS=encoded,
    )
    get_settings.cache_clear()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.dependencies.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.router.get_settings", lambda: settings)
    monkeypatch.setattr("app.operator_auth.route_policy.get_settings", lambda: settings)

    pg_url = _POSTGRES_URL
    if pg_url.startswith("postgresql://") and "+psycopg2" not in pg_url:
        pg_url = pg_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(pg_url, future=True, pool_pre_ping=True)
    # Ensure operator-auth tables exist (migration may already have run).
    from app.database import Base

    Base.metadata.create_all(
        engine,
        tables=[
            OperatorAccount.__table__,
            OperatorSession.__table__,
            OperatorAuditEvent.__table__,
            AppSetting.__table__,
        ],
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )

    def _truncate_operator_tables(session: Session) -> None:
        # TRUNCATE bypasses the append-only row trigger (DELETE does not).
        session.execute(
            text(
                "TRUNCATE operator_audit_event, operator_session, "
                "operator_account RESTART IDENTITY CASCADE"
            )
        )
        session.execute(
            text("DELETE FROM app_setting WHERE key = :k"),
            {"k": SYSTEM_LOCALE_KEY},
        )
        session.commit()

    db = factory()
    try:
        _truncate_operator_tables(db)
        OperatorRepository(db).seed_account(password=password, role="operator")
        db.commit()
    finally:
        db.close()

    application = FastAPI()
    register_exception_handlers(application)

    @application.middleware("http")
    async def _request_id_middleware(request, call_next):  # noqa: ANN001
        request_id = request.headers.get("x-request-id") or uuid4().hex
        request.state.request_id = request_id
        token = set_request_id(request_id)
        try:
            return await call_next(request)
        finally:
            reset_request_id(token)

    def _override_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_db
    application.dependency_overrides[get_settings] = lambda: settings

    public = public_router()
    public.include_router(public_system_settings_router)
    public.include_router(session_probe_router)
    cred = credential_exchange_router()
    cred.include_router(login_router)
    protected = protected_browser_router()
    protected.include_router(protected_system_settings_router)
    protected.include_router(protected_operator_auth_router)
    application.include_router(public)
    application.include_router(cred)
    application.include_router(protected)

    from app.operator_auth import audit as audit_mod

    original_append = audit_mod.OperatorAuditRepository.append

    def _failing_control_plane_append(self, *args, **kwargs):  # noqa: ANN001
        """Fail only the generic mutation audit — leave login/session audits intact."""
        event_type = kwargs.get("event_type")
        if args:
            # positional not used by production call sites
            pass
        if event_type == "control_plane_mutation_committed":
            original_append(self, *args, **kwargs)
            # Break the shared transaction so a later domain commit cannot land alone.
            self.db.execute(text("SELECT 1 FROM operator_audit_event_does_not_exist"))
            return None
        return original_append(self, *args, **kwargs)

    client = TestClient(application, raise_server_exceptions=False)
    try:
        login = client.post(
            "/api/operator-auth/login",
            json={"password": password},
            headers={
                "Origin": _ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/json",
            },
        )
        assert login.status_code == 200, login.text

        # Install the failing append only after login so session issuance succeeds.
        monkeypatch.setattr(
            audit_mod.OperatorAuditRepository,
            "append",
            _failing_control_plane_append,
        )

        response = client.put(
            "/api/system-settings/locale",
            json={"locale": "en"},
            headers=_csrf_headers(client),
        )
        assert response.status_code >= 400

        verify = factory()
        try:
            setting = (
                verify.execute(
                    select(AppSetting).where(AppSetting.key == SYSTEM_LOCALE_KEY)
                )
                .scalars()
                .first()
            )
            # Domain must not have committed a locale change from the failed request.
            if setting is not None:
                assert (setting.value_json or {}).get("locale") != "en"
            audits = list(
                verify.execute(
                    select(OperatorAuditEvent).where(
                        OperatorAuditEvent.event_type
                        == "control_plane_mutation_committed"
                    )
                ).scalars()
            )
            assert audits == []
        finally:
            verify.close()
    finally:
        application.dependency_overrides.clear()
        client.close()
        cleanup = factory()
        try:
            _truncate_operator_tables(cleanup)
        finally:
            cleanup.close()
        engine.dispose()
