from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.exceptions import register_exception_handlers
from app.common.responses import ApiResponse
from app.config import get_settings
from app.entry_type.router import router as entry_type_router
from app.tag.router import router as tag_router
from app.entry.router import router as entry_router
from app.relation.router import router as relation_router, type_router as relation_type_router
from app.attachment.router import router as attachment_router
from app.ai.router import router as ai_router
from app.stats.router import router as stats_router
from app.graph.router import router as graph_router

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
app.include_router(ai_router)
app.include_router(stats_router)
app.include_router(graph_router)


@app.get("/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return ApiResponse.ok({"status": "ok"})
