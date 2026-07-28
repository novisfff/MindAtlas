from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

if (os.environ.get("MINDATLAS_FAULTHANDLER") or "").strip().lower() in {"1", "true", "yes", "on"}:
    import faulthandler
    import signal

    faulthandler.enable()
    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except Exception:
        pass

from app.common.exceptions import register_exception_handlers
from app.common.request_context import (
    reset_request_id,
    reset_request_locale,
    set_request_id,
    set_request_locale,
)
from app.common.responses import ApiResponse
from app.config import get_settings
from app.entry_type.router import router as entry_type_router
from app.tag.router import router as tag_router
from app.entry.router import router as entry_router
from app.relation.router import router as relation_router, type_router as relation_type_router
from app.attachment.router import router as attachment_router
from app.ai_provider.router import router as ai_provider_router
from app.ai_registry.router import credential_router, model_router, binding_router
from app.ai.router import router as ai_router
from app.assistant.router import router as assistant_router
from app.assistant.evaluation.router import mount_skill_eval_router
from app.assistant.skills.admin_router import mount_skill_admin_router
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
from app.operator_auth.router import router as operator_auth_router
from app.system_settings.router import router as system_settings_router
from app.system_settings.service import normalize_system_locale

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    from app.assistant.migration.rollout import validate_runtime_rollout_startup
    from app.database import SessionLocal

    with SessionLocal() as db:
        validate_runtime_rollout_startup(db, settings=settings)
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
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
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

# Register routers
app.include_router(operator_auth_router)
app.include_router(entry_type_router)
app.include_router(tag_router)
app.include_router(entry_router)
app.include_router(relation_type_router)
app.include_router(relation_router)
app.include_router(attachment_router)
app.include_router(ai_provider_router)
app.include_router(credential_router)
app.include_router(model_router)
app.include_router(binding_router)
app.include_router(ai_router)
app.include_router(assistant_router)
app.include_router(assistant_config_router)
app.include_router(skill_package_router)
app.include_router(main_agent_profile_router)
# Plan 09 admin/eval surfaces: unmounted in staging/production; trusted-dev/test only.
mount_skill_admin_router(app, app_env=get_settings().app_env)
mount_skill_eval_router(app, app_env=get_settings().app_env)
app.include_router(stats_router)
app.include_router(graph_router)
app.include_router(lightrag_router)
app.include_router(report_router)
app.include_router(system_settings_router)
app.include_router(openclaw_integration_settings_router)
app.include_router(openclaw_integration_runtime_router)


@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ApiResponse.ok({"status": "ok"})
