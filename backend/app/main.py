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
from app.common.request_context import reset_request_id, set_request_id
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
from app.assistant_config.router import router as assistant_config_router
from app.stats.router import router as stats_router
from app.graph.router import router as graph_router
from app.lightrag.router import router as lightrag_router
from app.report.router import router as report_router
from app.scheduler import setup_scheduler, shutdown_scheduler

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
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
        allow_methods=["*"],
        allow_headers=["*"],
    )

register_exception_handlers(app, debug=settings.debug)


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    logger = logging.getLogger("app.request")
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    token = set_request_id(request_id)
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
        reset_request_id(token)

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
app.include_router(stats_router)
app.include_router(graph_router)
app.include_router(lightrag_router)
app.include_router(report_router)


@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ApiResponse.ok({"status": "ok"})
