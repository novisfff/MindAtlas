"""Regression: make_session binding lifecycle."""

from __future__ import annotations

import app.database as app_database
from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import force_restore_all_session_bindings, make_session


bootstrap_backend_imports()
reset_caches()


def test_make_session_rebinds_and_restores_module_level_importer() -> None:
    # Module-level `from app.database import SessionLocal` importer.
    import app.lightrag.worker as lightrag_worker

    original = app_database.SessionLocal
    session = make_session()
    temporary = app_database.SessionLocal
    assert temporary is not original
    assert lightrag_worker.SessionLocal is temporary

    session.close()
    assert app_database.SessionLocal is original
    # Without reload: already-imported module must be restored too.
    assert lightrag_worker.SessionLocal is original


def test_non_lifo_close_keeps_latest_active_binding() -> None:
    original = app_database.SessionLocal
    s1 = make_session()
    factory1 = app_database.SessionLocal
    s2 = make_session()
    factory2 = app_database.SessionLocal
    assert factory1 is not original
    assert factory2 is not original
    assert factory2 is not factory1

    # Close older first — globals must stay on still-alive s2, not jump to original.
    s1.close()
    assert app_database.SessionLocal is factory2

    s2.close()
    assert app_database.SessionLocal is original


def test_force_restore_cleans_unclosed_session() -> None:
    original = app_database.SessionLocal
    s1 = make_session()
    assert app_database.SessionLocal is not original
    # Forget to close.
    force_restore_all_session_bindings()
    assert app_database.SessionLocal is original
    # Closing after force restore must not crash.
    s1.close()
