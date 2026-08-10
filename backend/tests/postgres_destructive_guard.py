"""Fail-closed protection for PostgreSQL schema resets performed by tests."""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError


# Every destructive PostgreSQL test target must identify itself as a disposable
# database in the currently supported clean schema family.  Keep this prefix
# shared with CI and the temporary-database helpers so a typo cannot turn a
# release-critical reset into an immediate guard failure.
_DISPOSABLE_DATABASE_PREFIX = "mindatlas_test_pre_ga_v1_"
_DESTRUCTIVE_OPT_IN_ENV = "MINDATLAS_TEST_POSTGRES_DESTRUCTIVE"
_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres"})


def assert_disposable_postgres_target(url: str) -> None:
    """Raise unless *url* is the expressly permitted destructive test target."""
    try:
        parsed = make_url(url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise RuntimeError("destructive PostgreSQL reset requires a valid URL") from exc

    if parsed.query:
        raise RuntimeError(
            "destructive PostgreSQL reset URL query parameters are not permitted"
        )

    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("destructive reset requires a PostgreSQL backend")

    if parsed.host not in _ALLOWED_HOSTS:
        raise RuntimeError(
            "destructive PostgreSQL reset requires an allowlisted host "
            "(localhost, 127.0.0.1, ::1, or postgres)"
        )

    if not (parsed.database or "").startswith(_DISPOSABLE_DATABASE_PREFIX):
        raise RuntimeError(
            "destructive PostgreSQL reset requires a database beginning with "
            f"{_DISPOSABLE_DATABASE_PREFIX!r}"
        )

    if os.environ.get(_DESTRUCTIVE_OPT_IN_ENV, "").strip() != "1":
        raise RuntimeError(
            f"{_DESTRUCTIVE_OPT_IN_ENV}=1 is required for destructive PostgreSQL tests"
        )


def reset_disposable_public_schema(engine: Engine) -> None:
    """Drop and rebuild public only after validating the actual engine target."""
    assert_disposable_postgres_target(str(engine.url))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
