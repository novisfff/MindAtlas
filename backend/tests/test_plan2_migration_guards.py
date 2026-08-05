"""Unit proofs for Plan 2 migration safety guards.

The PostgreSQL migration suite verifies real upgrade/downgrade behavior.  This
small isolated test covers the environment boundary before destructive Alembic
operations are allowed to begin.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


class _EmptyBind:
    def scalar(self, _statement):  # noqa: ANN001
        return 0


class _NoopAlembicOperations:
    def get_bind(self) -> _EmptyBind:
        return _EmptyBind()

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _load_plan2_migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/b6e2d4f8a901_main_agent_bootstrap_readiness.py"
    )
    spec = importlib.util.spec_from_file_location("plan2_migration_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan2_downgrade_refuses_non_test_environment_even_with_legacy_bypass(
    monkeypatch,
) -> None:
    migration = _load_plan2_migration()
    monkeypatch.setattr(migration, "op", _NoopAlembicOperations())
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MINDATLAS_TEST_DESTRUCTIVE_DOWNGRADE", "1")

    with pytest.raises(RuntimeError, match="only allowed when APP_ENV=test"):
        migration.downgrade()
