from __future__ import annotations

import atexit
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, CheckConstraint, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()

# Modules that may cache `from app.database import SessionLocal`. Restored even
# if they are first imported while a temporary binding is active (see make_session).
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
    "app.lightrag.manager",
    "app.lightrag.documents",
    "app.assistant.service",
)

# Per-session restore tokens (do not rely on LIFO close order).
_ACTIVE_BINDINGS: dict[str, dict[str, Any]] = {}
_TEMP_DB_PATHS: list[Path] = []
_ORIGINAL_APP_DATABASE: dict[str, Any] | None = None


def _cleanup_temp_dbs() -> None:
    for path in list(_TEMP_DB_PATHS):
        try:
            path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except TypeError:
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


def _capture_module_sessionlocals() -> dict[str, Any]:
    captured: dict[str, Any] = {}
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "SessionLocal"):
            captured[module_name] = getattr(module, "SessionLocal")
    return captured


def _install_module_sessionlocals(factory: Any) -> None:
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "SessionLocal"):
            setattr(module, "SessionLocal", factory)


def _restore_module_sessionlocals(previous: dict[str, Any]) -> None:
    """Restore known importers.

    Modules first imported under a temporary binding are not in ``previous``;
    force them back to the process-global ``app.database.SessionLocal``.
    """
    import app.database as app_database  # noqa: E402

    global_factory = app_database.SessionLocal
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is None or not hasattr(module, "SessionLocal"):
            continue
        if module_name in previous:
            setattr(module, "SessionLocal", previous[module_name])
        else:
            # Late import under temporary binding — rebind to current global.
            setattr(module, "SessionLocal", global_factory)


def make_session() -> Session:
    """Create an isolated SQLite DB session with all models created.

    Uses a unique temporary file so multiple Sessions/connections share one DB.
    Process-global SessionLocal is rebound for capability/OpenClaw helpers and
    always restored when the returned session is closed (or via finalizer token).
    """
    global _ORIGINAL_APP_DATABASE
    reset_caches()

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

    # Remember the true process defaults once (before any test rebind).
    if _ORIGINAL_APP_DATABASE is None:
        _ORIGINAL_APP_DATABASE = {
            "engine": app_database.engine,
            "SessionLocal": app_database.SessionLocal,
        }

    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=True,
        future=True,
    )

    token = uuid.uuid4().hex
    previous_modules = _capture_module_sessionlocals()
    previous_app = {
        "engine": app_database.engine,
        "SessionLocal": app_database.SessionLocal,
    }
    _ACTIVE_BINDINGS[token] = {
        "app": previous_app,
        "modules": previous_modules,
        "tmp_path": tmp_path,
        "sqlalchemy_engine": engine,
    }

    app_database.engine = engine
    app_database.SessionLocal = test_session_factory
    _install_module_sessionlocals(test_session_factory)

    session = test_session_factory()
    restored = {"done": False}

    def _restore_bindings() -> None:
        if restored["done"]:
            return
        restored["done"] = True
        binding = _ACTIVE_BINDINGS.pop(token, None)
        if binding is None:
            return
        # Restore app.database to the state before this session.
        app_database.engine = binding["app"]["engine"]
        app_database.SessionLocal = binding["app"]["SessionLocal"]
        # If no other test sessions remain active, force absolute original defaults.
        if not _ACTIVE_BINDINGS and _ORIGINAL_APP_DATABASE is not None:
            app_database.engine = _ORIGINAL_APP_DATABASE["engine"]
            app_database.SessionLocal = _ORIGINAL_APP_DATABASE["SessionLocal"]
        _restore_module_sessionlocals(binding["modules"])
        # Re-assert module bindings against current global for any late imports.
        _install_module_sessionlocals(app_database.SessionLocal)
        eng = binding.get("sqlalchemy_engine")
        if eng is not None:
            try:
                eng.dispose()
            except Exception:
                pass
        path = binding.get("tmp_path")
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
    # Helpful for debugging / explicit finalizers.
    session._mindatlas_restore_bindings = _restore_bindings  # type: ignore[attr-defined]
    session._mindatlas_binding_token = token  # type: ignore[attr-defined]
    return session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed make_session that always restores global/module bindings."""
    session = make_session()
    try:
        yield session
    finally:
        session.close()
