from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def generate_weekly_report_job():
    """Weekly report generation job with idempotency."""
    from app.report.service import WeeklyReportService

    logger.info("Starting weekly report generation job")

    db = SessionLocal()
    try:
        service = WeeklyReportService(db)
        week_start = service.get_last_monday()

        # Use advisory lock for idempotency
        lock_key = int(week_start.strftime("%Y%m%d"))
        db.execute(text(f"SELECT pg_advisory_lock({lock_key})"))

        try:
            report = service.get_or_create_for_week(week_start)
            if report.status == "completed":
                logger.info(f"Report for {week_start} already completed")
                return

            service.generate_report(report)
            logger.info(f"Report for {week_start} generated: {report.status}")
        finally:
            db.execute(text(f"SELECT pg_advisory_unlock({lock_key})"))
    except Exception:
        logger.exception("Failed to generate weekly report")
    finally:
        db.close()


def generate_monthly_report_job():
    """Monthly report generation job with idempotency."""
    from app.report.service import MonthlyReportService

    logger.info("Starting monthly report generation job")

    db = SessionLocal()
    try:
        service = MonthlyReportService(db)
        month_start = service.get_last_month_start()

        # Use advisory lock for idempotency (YYYYMM format)
        lock_key = int(month_start.strftime("%Y%m"))
        db.execute(text(f"SELECT pg_advisory_lock({lock_key})"))

        try:
            report = service.get_or_create_for_month(month_start)
            if report.status == "completed":
                logger.info(f"Monthly report for {month_start} already completed")
                return

            service.generate_report(report)
            logger.info(f"Monthly report for {month_start} generated: {report.status}")
        finally:
            db.execute(text(f"SELECT pg_advisory_unlock({lock_key})"))
    except Exception:
        logger.exception("Failed to generate monthly report")
    finally:
        db.close()


def setup_scheduler():
    """Setup and start the scheduler."""
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled")
        return

    scheduler.add_job(
        generate_weekly_report_job,
        CronTrigger(day_of_week="mon", hour=0, minute=0, timezone="UTC"),
        id="weekly_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        generate_monthly_report_job,
        CronTrigger(day=1, hour=0, minute=10, timezone="UTC"),
        id="monthly_report",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=6 * 3600,
    )
    scheduler.start()
    logger.info("Scheduler started")


def shutdown_scheduler():
    """Shutdown the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
