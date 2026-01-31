from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class WeeklyReport(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "weekly_report"

    week_start = Column(Date, nullable=False, unique=True, index=True)
    week_end = Column(Date, nullable=False)
    entry_count = Column(Integer, default=0)

    content = Column(JSONB, nullable=True)

    status = Column(String(32), default="pending")
    attempts = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "week_end = week_start + interval '6 days'",
            name="ck_weekly_report_week_range"
        ),
    )


class MonthlyReport(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monthly_report"

    month_start = Column(Date, nullable=False, unique=True, index=True)
    month_end = Column(Date, nullable=False)
    entry_count = Column(Integer, default=0)

    content = Column(JSONB, nullable=True)

    status = Column(String(32), default="pending")
    attempts = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "month_start = date_trunc('month', month_start)::date",
            name="ck_monthly_report_month_start_is_first_day",
        ),
        CheckConstraint(
            "month_end = (month_start + interval '1 month' - interval '1 day')::date",
            name="ck_monthly_report_month_range",
        ),
    )
