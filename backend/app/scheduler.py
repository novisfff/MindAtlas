from __future__ import annotations

import logging
from threading import RLock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from app.database import SessionLocal
from app.system_settings.runtime_config_service import resolve_runtime_automation_config

logger = logging.getLogger(__name__)

_scheduler_lock = RLock()
_scheduler: BackgroundScheduler | None = None


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
            if not service.should_generate_report(report):
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
            if not service.should_generate_report(report):
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


def _build_scheduler() -> BackgroundScheduler:
    return BackgroundScheduler(timezone="UTC")


def _register_jobs(scheduler: BackgroundScheduler) -> None:
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


def _clear_scheduler_locked() -> None:
    global _scheduler

    if _scheduler is None:
        return

    try:
        _scheduler.remove_all_jobs()
    except Exception:
        logger.exception("Failed to clear scheduler jobs")

    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    _scheduler = None


def sync_scheduler() -> bool:
    """Synchronize the current instance scheduler with persisted automation config."""
    automation_config = resolve_runtime_automation_config()

    with _scheduler_lock:
        if not automation_config.scheduler_enabled:
            _clear_scheduler_locked()
            logger.info("Scheduler disabled")
            return False

        global _scheduler
        if _scheduler is None:
            _scheduler = _build_scheduler()

        _register_jobs(_scheduler)
        if not _scheduler.running:
            _scheduler.start()
            logger.info("Scheduler started")
        return True


def get_scheduler_job_ids() -> list[str]:
    with _scheduler_lock:
        if _scheduler is None:
            return []
        return sorted(job.id for job in _scheduler.get_jobs())


def is_scheduler_running() -> bool:
    with _scheduler_lock:
        return bool(_scheduler is not None and _scheduler.running)


def setup_scheduler():
    """Setup and start the scheduler."""
    sync_scheduler()


def shutdown_scheduler():
    """Shutdown the scheduler."""
    with _scheduler_lock:
        _clear_scheduler_locked()
