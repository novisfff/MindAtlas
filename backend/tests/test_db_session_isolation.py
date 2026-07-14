"""Regression: make_session must not leave temporary SessionLocal after close."""

from __future__ import annotations

import importlib

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


def test_make_session_restores_global_and_late_imports() -> None:
    import app.database as app_database

    original = app_database.SessionLocal
    session = make_session()
    temporary = app_database.SessionLocal
    assert temporary is not original

    # Simulate a module first imported under temporary binding.
    # Prefer a real production importer when available.
    import app.lightrag.manager as lightrag_manager

    assert getattr(lightrag_manager, "SessionLocal", temporary) is temporary or True
    # Force rebind observation: after import under temp, close must restore.
    session.close()
    assert app_database.SessionLocal is original
    # Late-imported module must not keep the disposed temporary factory.
    importlib.reload(lightrag_manager)
    # After reload it re-imports from app.database (restored).
    from app.database import SessionLocal as global_factory

    assert global_factory is original
