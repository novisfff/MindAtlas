from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()


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

