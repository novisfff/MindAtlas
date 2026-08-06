"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

import os

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


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "schema_release_postgres: release-critical PostgreSQL schema verification",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    marker = pytest.mark.schema_release_postgres
    for item in items:
        if item.path.name.endswith("_postgres.py") and item.path.name.startswith("test_schema_"):
            item.add_marker(marker)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    if (
        os.getenv("MINDATLAS_REQUIRE_SCHEMA_POSTGRES") == "1"
        and report.when == "setup"
        and report.skipped
        and "schema_release_postgres" in item.keywords
    ):
        report.outcome = "failed"
        report.longrepr = "release-critical schema PostgreSQL test skipped"
