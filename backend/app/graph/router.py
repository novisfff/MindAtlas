from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.graph.schemas import GraphData
from app.graph.service import GraphService

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("", response_model=ApiResponse)
def get_graph_data(db: Session = Depends(get_db)) -> ApiResponse:
    service = GraphService(db)
    graph_data = service.get_graph_data()
    return ApiResponse.ok(graph_data.model_dump(by_alias=True))
