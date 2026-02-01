from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.stats.service import StatsService

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/dashboard", response_model=ApiResponse)
def get_dashboard_stats(db: Session = Depends(get_db)) -> ApiResponse:
    service = StatsService(db)
    stats = service.get_dashboard_stats()
    return ApiResponse.ok(stats.model_dump(by_alias=True))


@router.get("/heatmap", response_model=ApiResponse)
def get_heatmap(
    months: int = Query(default=3, ge=1, le=12),
    type_id: UUID | None = Query(default=None, alias="typeId"),
    start_date: date | None = Query(default=None, alias="startDate"),
    end_date: date | None = Query(default=None, alias="endDate"),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=400, detail="startDate and endDate must be provided together")
    if start_date and end_date:
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="endDate must be >= startDate")
        if (end_date - start_date).days > 400:
            raise HTTPException(status_code=400, detail="date range too large")

    service = StatsService(db)
    heatmap = service.get_heatmap(
        months=months,
        type_id=type_id,
        start_date=start_date,
        end_date=end_date,
    )
    return ApiResponse.ok(heatmap.model_dump(by_alias=True))


@router.get("/day-entries", response_model=ApiResponse)
def get_day_entries(
    day: date = Query(..., alias="date"),
    type_id: UUID | None = Query(default=None, alias="typeId"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = StatsService(db)
    resp = service.get_day_entries(target_date=day, type_id=type_id, limit=limit)
    return ApiResponse.ok(resp.model_dump(by_alias=True))


@router.get("/weekly-metrics", response_model=ApiResponse)
def get_weekly_metrics(db: Session = Depends(get_db)) -> ApiResponse:
    service = StatsService(db)
    metrics = service.get_weekly_metrics()
    return ApiResponse.ok(metrics.model_dump(by_alias=True))


@router.get("/hotness", response_model=ApiResponse)
def get_hotness(db: Session = Depends(get_db)) -> ApiResponse:
    service = StatsService(db)
    hotness = service.get_hotness()
    return ApiResponse.ok(hotness.model_dump(by_alias=True))
