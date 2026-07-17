from __future__ import annotations

import atexit
import sys
import tempfile
import uuid
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import JSON, CheckConstraint, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()

# Modules that may cache `from app.database import SessionLocal`.
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

# token -> binding record; insertion order = creation order (Py3.7+ dict order).
_ACTIVE_BINDINGS: dict[str, dict[str, Any]] = {}
_TEMP_DB_PATHS: list[Path] = []
_ORIGINAL_APP_DATABASE: dict[str, Any] | None = None
_CREATION_SEQ = 0


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


def _install_module_sessionlocals(factory: Any) -> None:
    for module_name in _SESSIONLOCAL_IMPORTER_MODULES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "SessionLocal"):
            setattr(module, "SessionLocal", factory)


def _apply_global_factory(factory: Any, engine: Any) -> None:
    import app.database as app_database  # noqa: E402

    app_database.engine = engine
    app_database.SessionLocal = factory
    _install_module_sessionlocals(factory)


def _latest_active_binding() -> dict[str, Any] | None:
    """Most recently created still-active binding (creation order)."""
    if not _ACTIVE_BINDINGS:
        return None
    # dict preserves insertion order; last item is newest.
    token = next(reversed(_ACTIVE_BINDINGS))
    return _ACTIVE_BINDINGS[token]


def force_restore_all_session_bindings() -> None:
    """Test finalizer: drop every temporary binding and restore process defaults.

    Safe to call even when tests forget to close sessions.
    """
    global _CREATION_SEQ
    import app.database as app_database  # noqa: E402

    if _ORIGINAL_APP_DATABASE is None:
        return

    # Dispose/unlink every active temp engine.
    for binding in list(_ACTIVE_BINDINGS.values()):
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
    _ACTIVE_BINDINGS.clear()
    _CREATION_SEQ = 0

    app_database.engine = _ORIGINAL_APP_DATABASE["engine"]
    app_database.SessionLocal = _ORIGINAL_APP_DATABASE["SessionLocal"]
    _install_module_sessionlocals(app_database.SessionLocal)


def make_session() -> Session:
    """Create an isolated SQLite DB session with all models created.

    Uses a unique temporary file so multiple Sessions share one DB.
    Process-global SessionLocal is rebound for capability/OpenClaw/LightRAG
    helpers. Overlapping sessions are supported: closing one re-points globals
    to the latest still-active binding (not always original). Callers that skip
    ``close()`` are recovered by the autouse pytest finalizer.
    """
    global _ORIGINAL_APP_DATABASE, _CREATION_SEQ
    reset_caches()

    from app.database import Base  # noqa: E402

    import app.ai_provider.models  # noqa: F401,E402
    import app.ai_registry.models  # noqa: F401,E402
    import app.assistant.models  # noqa: F401,E402
    import app.assistant.durable.models  # noqa: F401,E402
    import app.assistant.capability_calls.models  # noqa: F401,E402
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
    _CREATION_SEQ += 1
    _ACTIVE_BINDINGS[token] = {
        "seq": _CREATION_SEQ,
        "tmp_path": tmp_path,
        "sqlalchemy_engine": engine,
        "factory": test_session_factory,
        "engine": engine,
    }

    # Point globals at the newest active binding.
    _apply_global_factory(test_session_factory, engine)

    session = test_session_factory()
    restored = {"done": False}

    def _release_binding() -> None:
        if restored["done"]:
            return
        restored["done"] = True
        binding = _ACTIVE_BINDINGS.pop(token, None)
        if binding is None:
            return

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

        # Non-LIFO safe: re-point to latest remaining active binding, else original.
        latest = _latest_active_binding()
        if latest is not None:
            _apply_global_factory(latest["factory"], latest["engine"])
        elif _ORIGINAL_APP_DATABASE is not None:
            _apply_global_factory(
                _ORIGINAL_APP_DATABASE["SessionLocal"],
                _ORIGINAL_APP_DATABASE["engine"],
            )

    original_close = session.close

    def _close_and_restore(*args: Any, **kwargs: Any) -> None:
        try:
            return original_close(*args, **kwargs)
        finally:
            _release_binding()

    session.close = _close_and_restore  # type: ignore[method-assign]
    session._mindatlas_restore_bindings = _release_binding  # type: ignore[attr-defined]
    session._mindatlas_binding_token = token  # type: ignore[attr-defined]
    # GC fallback if a test drops the session without close().
    weakref.finalize(session, _release_binding)
    return session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context-managed make_session that always restores global/module bindings."""
    session = make_session()
    try:
        yield session
    finally:
        session.close()
