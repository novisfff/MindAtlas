from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.responses import ApiResponse
from app.database import get_db
from app.report.schemas import MonthlyReportResponse, WeeklyReportResponse
from app.report.service import MonthlyReportService, WeeklyReportService

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/weekly", response_model=ApiResponse)
def list_weekly_reports(
    page: int = 0,
    size: int = 10,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = WeeklyReportService(db)
    result = service.list_reports(page=page, size=size)
    return ApiResponse.ok(result.model_dump(by_alias=True))


@router.get("/weekly/latest", response_model=ApiResponse)
def get_latest_weekly_report(db: Session = Depends(get_db)) -> ApiResponse:
    service = WeeklyReportService(db)
    report = service.get_latest()
    if not report:
        return ApiResponse.ok(None)
    data = WeeklyReportResponse.model_validate(report)
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.post("/weekly/generate", response_model=ApiResponse)
def generate_weekly_report(db: Session = Depends(get_db)) -> ApiResponse:
    service = WeeklyReportService(db)
    week_start = service.get_last_monday()
    report = service.get_or_create_for_week(week_start)

    # Generate AI content if not completed
    if report.status != "completed":
        report = service.generate_report(report)

    data = WeeklyReportResponse.model_validate(report)
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.get("/monthly", response_model=ApiResponse)
def list_monthly_reports(
    page: int = 0,
    size: int = 10,
    db: Session = Depends(get_db),
) -> ApiResponse:
    service = MonthlyReportService(db)
    result = service.list_reports(page=page, size=size)
    return ApiResponse.ok(result.model_dump(by_alias=True))


@router.get("/monthly/latest", response_model=ApiResponse)
def get_latest_monthly_report(db: Session = Depends(get_db)) -> ApiResponse:
    service = MonthlyReportService(db)
    report = service.get_latest()
    if not report:
        return ApiResponse.ok(None)
    data = MonthlyReportResponse.model_validate(report)
    return ApiResponse.ok(data.model_dump(by_alias=True))


@router.post("/monthly/generate", response_model=ApiResponse)
def generate_monthly_report(db: Session = Depends(get_db)) -> ApiResponse:
    service = MonthlyReportService(db)
    month_start = service.get_last_month_start()
    report = service.get_or_create_for_month(month_start)

    if report.status != "completed":
        report = service.generate_report(report)

    data = MonthlyReportResponse.model_validate(report)
    return ApiResponse.ok(data.model_dump(by_alias=True))
