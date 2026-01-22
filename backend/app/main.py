from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from app.common.exceptions import register_exception_handlers
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

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
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

register_exception_handlers(app)

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


@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ApiResponse.ok({"status": "ok"})
