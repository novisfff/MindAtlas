from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

if (os.environ.get("MINDATLAS_FAULTHANDLER") or "").strip().lower() in {"1", "true", "yes", "on"}:
    import faulthandler
    import signal

    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except Exception:
        pass

from app.assistant.runtime.readiness import (
    AssistantReadinessService,
    project_public_readiness,
)
from app.common.exceptions import register_exception_handlers
from app.common.request_context import (
    normalize_request_id,
    reset_request_id,
    reset_request_locale,
    set_request_id,
    set_request_locale,
)
from app.common.responses import ApiResponse, ok_json_content
from app.config import get_settings
from app.database import get_db
from app.entry_type.router import router as entry_type_router
from app.tag.router import router as tag_router
from app.entry.router import router as entry_router
from app.relation.router import router as relation_router, type_router as relation_type_router
from app.attachment.router import router as attachment_router
from app.ai_provider.router import router as ai_provider_router
from app.ai_registry.router import credential_router, model_router, binding_router
from app.ai.router import router as ai_router
from app.assistant.router import router as assistant_router
from app.assistant.evaluation.router import skill_eval_router
from app.assistant.skills.admin_router import skill_admin_parent_router
from app.assistant.runtime.router import router as assistant_runtime_router
from app.assistant.skills.router import (
    main_agent_profile_router,
    skill_package_router,
)
from app.assistant_config.bootstrap import warm_assistant_config_system_catalog
from app.assistant_config.router import router as assistant_config_router
from app.stats.router import router as stats_router
from app.graph.router import router as graph_router
from app.lightrag.router import router as lightrag_router
from app.openclaw_integration.router import (
    runtime_router as openclaw_integration_runtime_router,
    settings_router as openclaw_integration_settings_router,
)
from app.report.router import router as report_router
from app.scheduler import setup_scheduler, shutdown_scheduler
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
    setup_router,
)
from app.system_settings.router import (
    protected_system_settings_router,
    public_system_settings_router,
    setup_system_settings_router,
)
from app.system_settings.service import normalize_system_locale

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events.

    Plan 2 Task 9: no runtime selector validation. Invalid system seed logs a
    bounded reason code and continues serving process liveness; Chat/bootstrap
    fail closed via readiness. Never print seed/Profile content.
    """
    logger = logging.getLogger("app.lifespan")
    try:
        from app.assistant.runtime.seed import (
            SystemSeedInvalid,
            load_verified_assistant_system_seed,
        )

        load_verified_assistant_system_seed()
    except SystemSeedInvalid as exc:
        logger.error(
            "assistant_system_seed_invalid reason=%s",
            getattr(exc, "code", None) or str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — startup must not crash on seed
        reason = getattr(exc, "code", None) or type(exc).__name__
        logger.error("assistant_system_seed_invalid reason=%s", reason)
    warm_assistant_config_system_catalog()
    setup_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

cors_origins = settings.cors_origins_list()
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-MindAtlas-CSRF",
            "X-MindAtlas-Locale",
            "X-Request-ID",
        ],
    )

register_exception_handlers(app, debug=settings.debug)


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    logger = logging.getLogger("app.request")
    request_id = normalize_request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    locale = normalize_system_locale(request.headers.get("x-mindatlas-locale"))
    request.state.locale = locale
    request_id_token = set_request_id(request_id)
    request_locale_token = set_request_locale(locale)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.exception(
            "request_failed request_id=%s method=%s path=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise
    finally:
        reset_request_locale(request_locale_token)
        reset_request_id(request_id_token)

    duration_ms = (time.perf_counter() - start) * 1000.0
    response.headers["x-request-id"] = request_id
    log_fn = logger.info
    if response.status_code >= 500:
        log_fn = logger.error
    elif response.status_code >= 400:
        log_fn = logger.warning
    log_fn(
        "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Route policy parents — every application route has exactly one marker.
# ---------------------------------------------------------------------------

_public = public_router()
_credential_exchange = credential_exchange_router()
_setup = setup_router()
_protected_browser = protected_browser_router()
_machine = machine_router()

# Public: process liveness (no database) + assistant admission readiness.
@_public.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    """Process-only liveness. Never opens a database or evaluates readiness."""
    return ApiResponse.ok({"status": "ok"})


@_public.get("/ready", name="public_assistant_ready")
def public_ready(db: Session = Depends(get_db)) -> JSONResponse:
    """Public assistant readiness: ready + stable reason codes only.

    Chat admission and deployment acceptance consume this route. Compose
    bootstrap and Web dependencies must keep using /health so initialization
    and activation remain reachable when the system is not yet ready.
    """
    snapshot = AssistantReadinessService(db).evaluate()
    body = ok_json_content(project_public_readiness(snapshot))
    return JSONResponse(
        status_code=200 if snapshot.ready else 503,
        content=body,
    )


_public.include_router(public_system_settings_router)
_public.include_router(session_probe_router)

# Credential exchange: password login only.
_credential_exchange.include_router(login_router)

# Setup: clean-only initialization under Setup token (handler-enforced).
_setup.include_router(setup_system_settings_router)

# Protected browser: all operator control-plane data + settings surfaces.
_protected_browser.include_router(protected_operator_auth_router)
_protected_browser.include_router(entry_type_router)
_protected_browser.include_router(tag_router)
_protected_browser.include_router(entry_router)
_protected_browser.include_router(relation_type_router)
_protected_browser.include_router(relation_router)
_protected_browser.include_router(attachment_router)
_protected_browser.include_router(ai_provider_router)
_protected_browser.include_router(credential_router)
_protected_browser.include_router(model_router)
_protected_browser.include_router(binding_router)
_protected_browser.include_router(ai_router)
_protected_browser.include_router(assistant_router)
_protected_browser.include_router(assistant_config_router)
_protected_browser.include_router(skill_package_router)
_protected_browser.include_router(main_agent_profile_router)
# Plan 2 Task 6: prepared rollout / activation / durable kill-switch CAS.
_protected_browser.include_router(assistant_runtime_router)
# Plan 09 admin/eval: always mounted; protected_browser enforces real session.
_protected_browser.include_router(skill_admin_parent_router)
_protected_browser.include_router(skill_eval_router)
_protected_browser.include_router(stats_router)
_protected_browser.include_router(graph_router)
_protected_browser.include_router(lightrag_router)
_protected_browser.include_router(report_router)
_protected_browser.include_router(protected_system_settings_router)
_protected_browser.include_router(openclaw_integration_settings_router)

# Authenticated machine: OpenClaw runtime Bearer only.
_machine.include_router(openclaw_integration_runtime_router)

app.include_router(_public)
app.include_router(_credential_exchange)
app.include_router(_setup)
app.include_router(_protected_browser)
app.include_router(_machine)
