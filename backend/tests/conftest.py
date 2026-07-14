"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_session_bindings_after_test() -> None:
    """Always restore process-global SessionLocal after each test.

    ``make_session()`` rebinds ``app.database.SessionLocal`` for capability /
    OpenClaw / LightRAG helpers. Tests that forget ``session.close()`` would
    otherwise leak temporary factories into later tests (e.g. LightRAG
    ``app_setting`` missing). This finalizer is the safety net.
    """
    yield
    from tests._db import force_restore_all_session_bindings

    force_restore_all_session_bindings()
