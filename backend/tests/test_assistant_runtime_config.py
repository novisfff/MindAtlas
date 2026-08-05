"""Configuration tests for removed runtime selector and emergency new-runs ceiling."""

from __future__ import annotations

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def test_default_configuration_has_no_runtime_selector(monkeypatch):
    monkeypatch.delenv("ASSISTANT_NEW_RUNS_ENABLED", raising=False)
    monkeypatch.delenv("ASSISTANT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("ASSISTANT_RUNTIME_ROLLOUT_REVISION", raising=False)
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.assistant_new_runs_enabled is True
    assert not hasattr(settings, "assistant_runtime_mode")
    assert not hasattr(settings, "assistant_runtime_rollout_revision")


@pytest.mark.parametrize(
    "name",
    ["ASSISTANT_RUNTIME_MODE", "ASSISTANT_RUNTIME_ROLLOUT_REVISION"],
)
def test_removed_runtime_selector_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "legacy")
    from app.config import Settings

    with pytest.raises(ValueError, match="removed runtime selector"):
        Settings(_env_file=None)
