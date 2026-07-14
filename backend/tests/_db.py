from __future__ import annotations

import atexit
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, CheckConstraint, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()

# Modules that may cache `from app.database import SessionLocal` and open their
# own Sessions (capability adapters / OpenClaw worker / runtime config).
_SESSIONLOCAL_IMPORTER_MODULES: tuple[str, ...] = (
    "app.system_settings.runtime_config_service",
    "app.assistant.capabilities.adapters.tool",
    "app.assistant.capabilities.adapters.workflow",
    "app.assistant.capabilities.adapters.agent",
    "app.openclaw_integration.runtime_worker",
    "app.openclaw_integration.service",
    "app.assistant_config.bootstrap",
    "app.scheduler",
    "app.attachment.worker",
    "app.lightrag.worker",
    "app.assistant.service",
)

# Stack of previous bindings for nested make_session calls.
_SESSION_BINDING_STACK: list[dict[str, Any]] = []
_TEMP_DB_PATHS: list[Path] = []


def _cleanup_temp_dbs() -> None:
    for path in list(_TEMP_DB_PATHS):
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except TypeError:
            # Python <3.8 style
            if path.exists():
                path.unlink()
        except OSError:
            pass
    _TEMP_DB_PATHS.clear()


atexit.register(_cleanup_temp_dbs)


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
    """Create an isolated SQLite DB session with all models created.

    Uses a unique temporary file (not ``:memory:`` + StaticPool) so multiple
    Sessions/connections — including capability adapters and OpenClaw workers
    that open their own SessionLocal — share one real database without the
    single-connection identity-map races that break ordinary unit tests.
    """
    reset_caches()

    # Import models to register tables on Base.metadata before create_all().
    from app.database import Base  # noqa: E402

    import app.ai_provider.models  # noqa: F401,E402
    import app.ai_registry.models  # noqa: F401,E402
    import app.assistant.models  # noqa: F401,E402
    import app.assistant.skills.models  # noqa: F401,E402
    import app.assistant_config.models  # noqa: F401,E402
    import app.attachment.models  # noqa: F401,E402
    import app.entry.models  # noqa: F401,E402
    import app.entry_type.models  # noqa: F401,E402
    import app.openclaw_integration.models  # noqa: F401,E402
    import app.relation.models  # noqa: F401,E402
    import app.tag.models  # noqa: F401,E402
    import app.lightrag.models  # noqa: F401,E402
    import app.report.models  # noqa: F401,E402
    import app.system_settings.models  # noqa: F401,E402

    _normalize_report_tables_for_sqlite()

    tmp = tempfile.NamedTemporaryFile(prefix="mindatlas-test-", suffix=".sqlite", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    _TEMP_DB_PATHS.append(tmp_path)

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    import app.database as app_database  # noqa: E402

    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=True,  # match production SessionLocal; quota/FK paths rely on autoflush
        future=True,
    )

    previous_module_bindings: dict[str, Any] = {}
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "SessionLocal"):
            previous_module_bindings[module_name] = getattr(module, "SessionLocal")

    _SESSION_BINDING_STACK.append(
        {
            "engine": app_database.engine,
            "SessionLocal": app_database.SessionLocal,
            "modules": previous_module_bindings,
            "tmp_path": tmp_path,
            "sqlalchemy_engine": engine,
        }
    )

    # Point process-global factory + known importers at this isolated DB so
    # capability/OpenClaw workers that open their own Session see the same data.
    # File-backed SQLite (not :memory:+StaticPool) allows concurrent Sessions.
    app_database.engine = engine
    app_database.SessionLocal = test_session_factory
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "SessionLocal"):
            setattr(module, "SessionLocal", test_session_factory)

    session = test_session_factory()
    restored = {"done": False}

    def _restore_bindings() -> None:
        if restored["done"]:
            return
        restored["done"] = True
        if not _SESSION_BINDING_STACK:
            return
        previous = _SESSION_BINDING_STACK.pop()
        if previous.get("engine") is not None:
            app_database.engine = previous["engine"]
        if previous.get("SessionLocal") is not None:
            app_database.SessionLocal = previous["SessionLocal"]
        for module_name, old_factory in previous["modules"].items():
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, "SessionLocal"):
                setattr(module, "SessionLocal", old_factory)
        eng = previous.get("sqlalchemy_engine")
        if eng is not None:
            try:
                eng.dispose()
            except Exception:
                pass
        path = previous.get("tmp_path")
        if isinstance(path, Path):
            try:
                path.unlink(missing_ok=True)  # type: ignore[call-arg]
            except TypeError:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
            try:
                _TEMP_DB_PATHS.remove(path)
            except ValueError:
                pass

    original_close = session.close

    def _close_and_restore(*args: Any, **kwargs: Any) -> None:
        try:
            return original_close(*args, **kwargs)
        finally:
            _restore_bindings()

    session.close = _close_and_restore  # type: ignore[method-assign]
    return session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed make_session that always restores global/module bindings."""
    session = make_session()
    try:
        yield session
    finally:
        session.close()
