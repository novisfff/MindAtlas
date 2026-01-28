"""FastAPI router for LightRAG API endpoints (Phase 5)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.lightrag.schemas import LightRagQueryRequest
from app.lightrag.service import LightRagService

router = APIRouter(prefix="/api/lightrag", tags=["lightrag"])


@router.post("/query", response_model=ApiResponse)
async def query(request: LightRagQueryRequest) -> ApiResponse | StreamingResponse:
    """Execute a RAG query against the indexed knowledge graph.

    Supports multiple query modes:
    - naive: Simple vector retrieval
    - local: Local knowledge graph query
    - global: Global knowledge graph query
    - hybrid: Combined vector + knowledge graph (default)
    - mix: Alias of hybrid (upstream LightRAG mode)

    Set `stream=true` for SSE streaming response.
    """
    service = LightRagService()

    if request.stream:
        return StreamingResponse(
            service.query_sse(
                query=request.query,
                mode=request.mode,
                top_k=request.top_k,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await service.query(
        query=request.query,
        mode=request.mode,
        top_k=request.top_k,
    )
    return ApiResponse.ok(result.model_dump(by_alias=True))


@router.get("/entries/{entry_id}/relation-recommendations", response_model=ApiResponse)
async def relation_recommendations(
    entry_id: UUID,
    mode: str = Query(default="hybrid", description="Query mode: naive/local/global/hybrid/mix"),
    limit: int = Query(default=20, ge=1, le=100, description="Max number of recommendations"),
    min_score: float = Query(default=0.1, ge=0.0, le=1.0, description="Minimum similarity score threshold"),
    exclude_existing_relations: bool = Query(default=False, description="Filter out entries with existing relations"),
    include_relation_type: bool = Query(default=True, description="Whether to predict relationType via LLM (slower)"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Recommend related Entries using LightRAG query recall (Phase 5.5).

    Returns items: [{ targetEntryId, relationType?, score }].
    relationType is the predicted RelationType.code (e.g., BELONGS_TO, USES, RELATES_TO).
    """
    service = LightRagService()
    result = await service.recommend_entry_relations(
        db=db,
        entry_id=entry_id,
        mode=mode,
        limit=limit,
        min_score=min_score,
        exclude_existing_relations=exclude_existing_relations,
        include_relation_type=include_relation_type,
    )
    return ApiResponse.ok(result.model_dump(by_alias=True, exclude_none=True))


@router.get("/graph", response_model=ApiResponse)
async def get_lightrag_graph(
    node_label: str = Query(default="*", alias="nodeLabel", description="Node label filter, * for all"),
    max_depth: int = Query(default=3, ge=1, le=10, alias="maxDepth", description="Maximum graph depth"),
    max_nodes: int = Query(default=1000, ge=1, le=5000, alias="maxNodes", description="Maximum nodes to return"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Get LightRAG knowledge graph data.

    Returns graph data in the same format as system graph for unified visualization.
    """
    service = LightRagService()
    graph_data = await service.get_graph_data(
        node_label=node_label,
        max_depth=max_depth,
        max_nodes=max_nodes,
        db=db,
    )
    return ApiResponse.ok(graph_data.model_dump(by_alias=True))
