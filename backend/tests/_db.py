from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):  # noqa: ANN001
    return "JSON"


def _normalize_report_tables_for_sqlite() -> None:
    from app.report.models import MonthlyReport, WeeklyReport  # noqa: E402

    report_tables = (
        (WeeklyReport.__table__, {"ck_weekly_report_week_range"}),
        (
            MonthlyReport.__table__,
            {
                "ck_monthly_report_month_start_is_first_day",
                "ck_monthly_report_month_range",
            },
        ),
    )

    for table, ignored_check_names in report_tables:
        content_column = table.columns.get("content")
        if content_column is not None:
            content_column.type = JSON()

        for constraint in list(table.constraints):
            if (
                isinstance(constraint, CheckConstraint)
                and constraint.name in ignored_check_names
            ):
                table.constraints.discard(constraint)


def make_session() -> Session:
    """Create an isolated SQLite in-memory DB session with all models created."""
    reset_caches()

    # Import models to register tables on Base.metadata before create_all().
    from app.database import Base  # noqa: E402

    import app.ai_provider.models  # noqa: F401,E402
    import app.assistant.models  # noqa: F401,E402
    import app.assistant_config.models  # noqa: F401,E402
    import app.attachment.models  # noqa: F401,E402
    import app.entry.models  # noqa: F401,E402
    import app.entry_type.models  # noqa: F401,E402
    import app.relation.models  # noqa: F401,E402
    import app.tag.models  # noqa: F401,E402
    import app.lightrag.models  # noqa: F401,E402
    import app.report.models  # noqa: F401,E402

    _normalize_report_tables_for_sqlite()

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()
