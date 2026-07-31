"""Unit tests for the shared PostgreSQL destructive-reset guard."""

from __future__ import annotations

import importlib

import pytest

from tests.postgres_destructive_guard import (
    assert_disposable_postgres_target,
    reset_disposable_public_schema,
)


_SAFE_DATABASE = "mindatlas_test_plan08_guard"


class _NeverTouchedEngine:
    def __init__(self, url: str = "") -> None:
        self.url = url

    def begin(self):  # pragma: no cover - the test asserts this is never reached.
        raise AssertionError("guard must validate before opening the engine")


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


class _RecordingTransaction:
    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection

    def __enter__(self) -> _RecordingConnection:
        return self._connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _RecordingEngine:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connection = _RecordingConnection()

    def begin(self) -> _RecordingTransaction:
        return _RecordingTransaction(self.connection)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://localhost/{_SAFE_DATABASE}",
        f"postgresql://127.0.0.1/{_SAFE_DATABASE}",
        f"postgresql://[::1]/{_SAFE_DATABASE}",
        f"postgresql://postgres/{_SAFE_DATABASE}",
    ],
)
def test_guard_allows_exactly_the_supported_local_postgres_hosts(monkeypatch, url: str):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    assert_disposable_postgres_target(url)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://db.example.test/{_SAFE_DATABASE}",
        f"postgresql:///{_SAFE_DATABASE}",
        f"sqlite:///{_SAFE_DATABASE}",
        "postgresql://localhost/mindatlas_test_plan09_guard",
    ],
)
def test_guard_rejects_non_disposable_or_nonlocal_targets(monkeypatch, url: str):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    with pytest.raises(RuntimeError):
        assert_disposable_postgres_target(url)


def test_guard_requires_explicit_destructive_opt_in(monkeypatch):
    monkeypatch.delenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", raising=False)

    with pytest.raises(RuntimeError, match="MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1"):
        assert_disposable_postgres_target(f"postgresql://localhost/{_SAFE_DATABASE}")


def test_reset_validates_the_actual_engine_target_before_opening_a_transaction(
    monkeypatch,
):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    with pytest.raises(RuntimeError, match="allowlisted host"):
        reset_disposable_public_schema(
            _NeverTouchedEngine("postgresql+psycopg2://db.example.test/production"),
        )


_TRUNCATE_RESET_MODULES = (
    "tests.test_assistant_initialization_atomicity_postgres",
    "tests.test_system_initialization_concurrency_postgres",
)


@pytest.mark.parametrize("module_name", _TRUNCATE_RESET_MODULES)
@pytest.mark.parametrize(
    ("url", "destructive_opt_in", "message"),
    [
        (
            f"postgresql://db.example.test/{_SAFE_DATABASE}",
            "1",
            "allowlisted host",
        ),
        ("postgresql://localhost/mindatlas_not_a_test", "1", "database beginning"),
        (
            f"postgresql://localhost/{_SAFE_DATABASE}?host=db.example.test",
            "1",
            "query parameters",
        ),
        (
            f"postgresql://localhost/{_SAFE_DATABASE}",
            None,
            "MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1",
        ),
    ],
)
def test_truncate_resets_validate_before_opening_a_transaction(
    monkeypatch,
    module_name: str,
    url: str,
    destructive_opt_in: str | None,
    message: str,
):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_POSTGRES_URL", url)
    if destructive_opt_in is None:
        monkeypatch.delenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", raising=False)
    else:
        monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", destructive_opt_in)

    with pytest.raises(RuntimeError, match=message):
        module._truncate_owned(_NeverTouchedEngine(url))


@pytest.mark.parametrize("module_name", _TRUNCATE_RESET_MODULES)
def test_truncate_resets_validate_the_actual_engine_target(
    monkeypatch,
    module_name: str,
):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_POSTGRES_URL", f"postgresql://localhost/{_SAFE_DATABASE}")
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    with pytest.raises(RuntimeError, match="allowlisted host"):
        module._truncate_owned(
            _NeverTouchedEngine("postgresql+psycopg2://db.example.test/production"),
        )


def test_reset_rejects_wrong_host_before_opening_engine(monkeypatch):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    with pytest.raises(RuntimeError, match="allowlisted host"):
        reset_disposable_public_schema(
            _NeverTouchedEngine(f"postgresql://db.example.test/{_SAFE_DATABASE}"),
        )


@pytest.mark.parametrize(
    "query",
    [
        "host=db.example.test",
        "hostaddr=203.0.113.7",
        "dbname=production",
        "service=production",
        "application_name=postgres-destructive-guard-test",
    ],
)
def test_reset_rejects_query_overrides_before_opening_engine(monkeypatch, query: str):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")

    with pytest.raises(RuntimeError, match="query parameters are not permitted"):
        reset_disposable_public_schema(
            _NeverTouchedEngine(
                "postgresql+psycopg2://user:pw@localhost:5432/"
                f"{_SAFE_DATABASE}?{query}"
            ),
        )


def test_reset_recreates_and_grants_public_schema_after_validation(monkeypatch):
    monkeypatch.setenv("MINDATLAS_TEST_POSTGRES_DESTRUCTIVE", "1")
    engine = _RecordingEngine(f"postgresql://localhost/{_SAFE_DATABASE}")

    reset_disposable_public_schema(engine)

    assert engine.connection.statements == [
        "DROP SCHEMA IF EXISTS public " + "CASCADE",
        "CREATE SCHEMA public",
        "GRANT ALL ON SCHEMA public TO public",
    ]
