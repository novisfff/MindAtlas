from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.stats.schemas import DashboardStats
from app.stats.service import StatsService

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/dashboard", response_model=ApiResponse)
def get_dashboard_stats(db: Session = Depends(get_db)) -> ApiResponse:
    service = StatsService(db)
    stats = service.get_dashboard_stats()
    return ApiResponse.ok(stats.model_dump(by_alias=True))
