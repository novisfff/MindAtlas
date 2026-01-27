from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.graph.service import GraphService

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("", response_model=ApiResponse)
def get_graph_data(
    time_from: datetime | None = Query(default=None, alias="timeFrom"),
    time_to: datetime | None = Query(default=None, alias="timeTo"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = GraphService(db)
    graph_data = service.get_graph_data(time_from=time_from, time_to=time_to)
    return ApiResponse.ok(graph_data.model_dump(by_alias=True))
